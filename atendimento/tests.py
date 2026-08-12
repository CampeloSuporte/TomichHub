from unittest import mock
import base64
import json
import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from atendimento.models import (
    WhatsAppConnection, ContactGroup, Conversation, Message, ScheduledMessage,
)
from atendimento.services import ConversationService, _save_media_file, _read_attachment_as_base64
from atendimento.tasks import enviar_mensagens_agendadas


def _criar_conversa():
    # Sufixo único: WhatsAppConnection.name é unique=True e alguns testes
    # (ex.: mesclagem de conversas) chamam este helper mais de uma vez no
    # mesmo teste, então um nome fixo colidiria (UniqueViolation).
    suffix = uuid.uuid4().hex[:8]
    connection = WhatsAppConnection.objects.create(
        name=f'Conexao Teste {suffix}', evolution_url='https://evolution.example.com',
        api_key='fake-key', instance_name='teste',
    )
    group = ContactGroup.objects.create(
        jid='5511999999999@s.whatsapp.net', connection=connection, name='Cliente Teste',
    )
    return Conversation.objects.create(group=group)


def _criar_agente_staff(username='ana'):
    # Forcar2FAMiddleware redireciona qualquer staff sem TOTPDevice confirmado
    # pra tela de configuração de 2FA; sem isso o POST cai em 302 antes da view.
    from usuario.models import TOTPDevice
    agent = User.objects.create_user(username=username, password='x', is_staff=True, is_active=True)
    TOTPDevice.objects.create(usuario=agent, secret='JBSWY3DPEHPK3PXP', confirmado=True)
    return agent


class ScheduledMessageModelTest(TestCase):
    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = User.objects.create_user(username='ana')

    def test_criacao_com_status_default_pending(self):
        sm = ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent,
            content='Mensagem de teste', scheduled_for=timezone.now() + timedelta(hours=1),
        )
        self.assertEqual(sm.status, 'pending')
        self.assertEqual(sm.message_type, 'text')
        self.assertEqual(sm.attempts, 0)
        self.assertIn('Pendente', str(sm))


class SendMediaServiceTest(TestCase):
    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = User.objects.create_user(username='ana', first_name='Ana')

    @mock.patch('atendimento.services._save_media_file', return_value='/media/atendimento/media/fake.jpg')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_send_media_cria_message_com_tipo_e_legenda(self, mock_client_cls, mock_save):
        mock_client_cls.return_value.send_media.return_value = True

        ok, result = ConversationService.send_media(
            self.conversation, 'ZmFrZQ==', 'image', 'foto.jpg', 'Segue a foto', self.agent
        )

        self.assertTrue(ok)
        msg = Message.objects.get(id=result)
        self.assertEqual(msg.message_type, 'image')
        self.assertEqual(msg.content, 'Segue a foto')
        self.assertEqual(msg.sender_type, 'agent')
        self.assertEqual(msg.attachment_url, '/media/atendimento/media/fake.jpg')

    @mock.patch('atendimento.services._save_media_file', return_value='/media/atendimento/media/fake.mp4')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_send_media_sem_legenda_usa_rotulo_do_tipo(self, mock_client_cls, mock_save):
        mock_client_cls.return_value.send_media.return_value = True

        ok, result = ConversationService.send_media(
            self.conversation, 'ZmFrZQ==', 'video', 'video.mp4', '', self.agent
        )

        msg = Message.objects.get(id=result)
        self.assertEqual(msg.content, 'Vídeo')

    @mock.patch('atendimento.services._save_media_file', return_value='/media/atendimento/media/audio.ogg')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_send_media_audio_chama_send_audio_nao_send_media(self, mock_client_cls, mock_save):
        mock_client_cls.return_value.send_audio.return_value = True

        ok, result = ConversationService.send_media(
            self.conversation, 'ZmFrZQ==', 'audio', 'audio.ogg', '', self.agent
        )
        self.assertTrue(ok)
        msg = Message.objects.get(id=result)
        self.assertEqual(msg.message_type, 'audio')


class ApiSendMediaTest(TestCase):
    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = _criar_agente_staff()
        self.client.force_login(self.agent)

    @mock.patch('atendimento.services._save_media_file', return_value='/media/atendimento/media/fake.jpg')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_envia_midia_com_legenda(self, mock_client_cls, mock_save):
        mock_client_cls.return_value.send_media.return_value = True
        url = reverse('atendimento:api_send_media', args=[self.conversation.id])
        resp = self.client.post(url, data=json.dumps({
            'mediaBase64': 'ZmFrZQ==', 'mediaType': 'image',
            'fileName': 'foto.jpg', 'caption': 'Segue a foto',
        }), content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['content'], 'Segue a foto')

    def test_sem_base64_retorna_400(self):
        url = reverse('atendimento:api_send_media', args=[self.conversation.id])
        resp = self.client.post(url, data=json.dumps({'mediaBase64': ''}), content_type='application/json')
        self.assertEqual(resp.status_code, 400)


class ReadAttachmentAsBase64Test(TestCase):
    def test_le_arquivo_salvo_e_devolve_base64_original(self):
        original_bytes = b'conteudo-fake-de-teste'
        b64_original = base64.b64encode(original_bytes).decode()
        saved_url = _save_media_file(b64_original, 'image/jpeg')

        try:
            result = _read_attachment_as_base64(saved_url)
            self.assertEqual(base64.b64decode(result), original_bytes)
        finally:
            relative = saved_url.replace(settings.MEDIA_URL, '', 1)
            os.remove(os.path.join(settings.MEDIA_ROOT, relative))


class EnviarMensagensAgendadasTest(TestCase):
    def setUp(self):
        self.conversation = _criar_conversa()
        self.conversation.status = 'open'
        self.conversation.save(update_fields=['status'])
        self.agent = User.objects.create_user(username='ana', first_name='Ana')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_envia_mensagem_de_texto_vencida(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'wamid123')
        sm = ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent,
            message_type='text', content='Oi, tudo certo?',
            scheduled_for=timezone.now() - timedelta(minutes=1),
        )

        enviar_mensagens_agendadas()

        sm.refresh_from_db()
        self.assertEqual(sm.status, 'sent')
        self.assertIsNotNone(sm.sent_at)
        self.assertTrue(Message.objects.filter(conversation=self.conversation, content='Oi, tudo certo?').exists())

    def test_ignora_mensagem_ainda_nao_vencida(self):
        sm = ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent,
            message_type='text', content='Ainda não',
            scheduled_for=timezone.now() + timedelta(hours=1),
        )

        enviar_mensagens_agendadas()

        sm.refresh_from_db()
        self.assertEqual(sm.status, 'pending')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_conversa_mesclada_envia_no_destino(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'wamid123')
        destino = _criar_conversa()
        destino.status = 'open'
        destino.save(update_fields=['status'])
        self.conversation.merged_into = destino
        self.conversation.save(update_fields=['merged_into'])

        sm = ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent,
            message_type='text', content='Mensagem pos-mesclagem',
            scheduled_for=timezone.now() - timedelta(minutes=1),
        )

        enviar_mensagens_agendadas()

        sm.refresh_from_db()
        self.assertEqual(sm.status, 'sent')
        self.assertTrue(Message.objects.filter(conversation=destino, content='Mensagem pos-mesclagem').exists())
        self.assertFalse(Message.objects.filter(conversation=self.conversation, content='Mensagem pos-mesclagem').exists())

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_reabre_conversa_fechada_antes_de_enviar(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'wamid123')
        self.conversation.status = 'closed'
        self.conversation.save(update_fields=['status'])

        sm = ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent,
            message_type='text', content='Reabrindo',
            scheduled_for=timezone.now() - timedelta(minutes=1),
        )

        enviar_mensagens_agendadas()

        self.conversation.refresh_from_db()
        sm.refresh_from_db()
        self.assertEqual(self.conversation.status, 'open')
        self.assertEqual(sm.status, 'sent')

    @mock.patch('atendimento.services.ConversationService.send_message')
    def test_falha_incrementa_tentativas_e_marca_failed_apos_limite(self, mock_send):
        mock_send.return_value = (False, 'erro simulado de rede')
        sm = ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent,
            message_type='text', content='Vai falhar',
            scheduled_for=timezone.now() - timedelta(minutes=1),
        )

        for _ in range(ScheduledMessage.MAX_ATTEMPTS):
            enviar_mensagens_agendadas()

        sm.refresh_from_db()
        self.assertEqual(sm.attempts, ScheduledMessage.MAX_ATTEMPTS)
        self.assertEqual(sm.status, 'failed')
        self.assertIn('erro simulado', sm.last_error)

    @mock.patch('atendimento.services._read_attachment_as_base64', return_value='ZmFrZQ==')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_envia_mensagem_de_midia_vencida(self, mock_client_cls, mock_read_b64):
        mock_client_cls.return_value.send_media.return_value = True
        sm = ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent,
            message_type='image', content='Legenda da foto',
            attachment_url='/media/atendimento/media/fake.jpg', file_name='foto.jpg',
            scheduled_for=timezone.now() - timedelta(minutes=1),
        )

        enviar_mensagens_agendadas()

        sm.refresh_from_db()
        self.assertEqual(sm.status, 'sent')
        msg = Message.objects.get(conversation=self.conversation, message_type='image')
        self.assertEqual(msg.content, 'Legenda da foto')


class EnviarMensagensAgendadasGuardTest(TestCase):
    """Guardas contra ciclo de mesclagem e contra corrida com o cancelamento."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.conversation.status = 'open'
        self.conversation.save(update_fields=['status'])
        self.agent = User.objects.create_user(username='ana', first_name='Ana')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_ciclo_de_mesclagem_nao_trava(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'wamid123')
        outra = _criar_conversa()
        outra.status = 'open'
        outra.save(update_fields=['status'])
        self.conversation.merged_into = outra
        self.conversation.save(update_fields=['merged_into'])
        outra.merged_into = self.conversation
        outra.save(update_fields=['merged_into'])

        sm = ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent,
            message_type='text', content='Em ciclo',
            scheduled_for=timezone.now() - timedelta(minutes=1),
        )

        enviar_mensagens_agendadas()

        sm.refresh_from_db()
        self.assertEqual(sm.status, 'sent')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_cancelada_no_meio_do_ciclo_nao_envia(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'wamid123')
        sm = ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent,
            message_type='text', content='Cancelada antes do envio',
            scheduled_for=timezone.now() - timedelta(minutes=1),
        )
        ScheduledMessage.objects.filter(id=sm.id).update(status='cancelled')

        enviar_mensagens_agendadas()

        sm.refresh_from_db()
        self.assertEqual(sm.status, 'cancelled')
        self.assertFalse(Message.objects.filter(content='Cancelada antes do envio').exists())


class ApiScheduleMessageTest(TestCase):
    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = _criar_agente_staff()
        self.client.force_login(self.agent)
        self.url = reverse('atendimento:api_schedule_message', args=[self.conversation.id])

    def test_post_com_data_no_passado_retorna_400(self):
        passado = (timezone.now() - timedelta(hours=1)).isoformat()
        resp = self.client.post(self.url, data=json.dumps({'message': 'Oi', 'scheduled_for': passado}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(ScheduledMessage.objects.exists())

    def test_post_texto_valido_cria_pendente(self):
        futuro = (timezone.now() + timedelta(hours=2)).isoformat()
        resp = self.client.post(self.url, data=json.dumps({'message': 'Oi, tudo bem?', 'scheduled_for': futuro}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        sm = ScheduledMessage.objects.get()
        self.assertEqual(sm.content, 'Oi, tudo bem?')
        self.assertEqual(sm.status, 'pending')
        self.assertEqual(sm.created_by, self.agent)

    def test_post_sem_mensagem_nem_midia_retorna_400(self):
        futuro = (timezone.now() + timedelta(hours=2)).isoformat()
        resp = self.client.post(self.url, data=json.dumps({'message': '', 'scheduled_for': futuro}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    @mock.patch('atendimento.services._save_media_file', return_value='/media/atendimento/media/fake.jpg')
    def test_post_com_midia_guarda_legenda_crua_sem_rotulo_padrao(self, mock_save):
        futuro = (timezone.now() + timedelta(hours=2)).isoformat()
        resp = self.client.post(self.url, data=json.dumps({
            'mediaBase64': 'ZmFrZQ==', 'mediaType': 'image', 'fileName': 'foto.jpg',
            'caption': '', 'scheduled_for': futuro,
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        sm = ScheduledMessage.objects.get()
        self.assertEqual(sm.content, '')  # legenda crua, não "Imagem"
        self.assertEqual(sm.file_name, 'foto.jpg')
        self.assertEqual(sm.attachment_url, '/media/atendimento/media/fake.jpg')

    def test_get_lista_apenas_pendentes(self):
        ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent, message_type='text',
            content='Pendente', scheduled_for=timezone.now() + timedelta(hours=1), status='pending',
        )
        ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent, message_type='text',
            content='Ja enviada', scheduled_for=timezone.now() - timedelta(hours=1), status='sent',
        )
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        items = resp.json()['scheduled']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['content'], 'Pendente')


class ApiCancelScheduledMessageTest(TestCase):
    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = _criar_agente_staff()
        self.client.force_login(self.agent)

    def test_cancela_mensagem_pendente(self):
        sm = ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent, message_type='text',
            content='Cancelar isso', scheduled_for=timezone.now() + timedelta(hours=1),
        )
        url = reverse('atendimento:api_cancel_scheduled_message', args=[sm.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200)
        sm.refresh_from_db()
        self.assertEqual(sm.status, 'cancelled')
        self.assertEqual(sm.cancelled_by, self.agent)

    def test_cancelar_ja_enviada_retorna_400(self):
        sm = ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent, message_type='text',
            content='Ja foi', scheduled_for=timezone.now() - timedelta(hours=1), status='sent',
        )
        url = reverse('atendimento:api_cancel_scheduled_message', args=[sm.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 400)
        sm.refresh_from_db()
        self.assertEqual(sm.status, 'sent')
