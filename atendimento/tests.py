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

    # _save_media_file também é mockado: sem isso o reenvio grava um arquivo
    # órfão em MEDIA_ROOT a cada rodada da suíte (mesma pasta das mídias reais).
    @mock.patch('atendimento.services._save_media_file', return_value='/media/atendimento/media/fake.jpg')
    @mock.patch('atendimento.services._read_attachment_as_base64', return_value='ZmFrZQ==')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_envia_mensagem_de_midia_vencida(self, mock_client_cls, mock_read_b64, mock_save):
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


class AgendadorFluxoCompletoTest(TestCase):
    """Integração ponta a ponta: o atendente agenda pela API, o Celery roda e a
    mensagem sai. As outras classes testam cada peça isolada — esta garante que
    o que o endpoint grava é exatamente o que a task consegue enviar depois."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.conversation.status = 'open'
        self.conversation.save(update_fields=['status'])
        self.agent = _criar_agente_staff()
        self.client.force_login(self.agent)
        self.url = reverse('atendimento:api_schedule_message', args=[self.conversation.id])

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_texto_agendado_pela_api_e_enviado_pela_task(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'wamid123')

        # 1. agenda pra daqui a 2h
        resp = self.client.post(self.url, data=json.dumps({
            'message': 'Bom dia, seguimos com o chamado.',
            'scheduled_for': (timezone.now() + timedelta(hours=2)).isoformat(),
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 200)

        # 2. aparece na listagem de pendentes
        listagem = self.client.get(self.url).json()['scheduled']
        self.assertEqual(len(listagem), 1)
        self.assertEqual(listagem[0]['content'], 'Bom dia, seguimos com o chamado.')

        # 3. nada é enviado antes da hora
        enviar_mensagens_agendadas()
        self.assertFalse(Message.objects.filter(conversation=self.conversation).exists())

        # 4. chegada a hora, a task envia
        sm = ScheduledMessage.objects.get()
        sm.scheduled_for = timezone.now() - timedelta(minutes=1)
        sm.save(update_fields=['scheduled_for'])
        enviar_mensagens_agendadas()

        sm.refresh_from_db()
        self.assertEqual(sm.status, 'sent')
        msg = Message.objects.get(conversation=self.conversation)
        self.assertEqual(msg.content, 'Bom dia, seguimos com o chamado.')
        self.assertEqual(msg.sender, self.agent)

        # 5. some da listagem de pendentes
        self.assertEqual(self.client.get(self.url).json()['scheduled'], [])

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_cancelar_antes_da_hora_impede_o_envio(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'wamid123')
        self.client.post(self.url, data=json.dumps({
            'message': 'Nao deve sair',
            'scheduled_for': (timezone.now() + timedelta(hours=1)).isoformat(),
        }), content_type='application/json')

        sm = ScheduledMessage.objects.get()
        self.client.post(reverse('atendimento:api_cancel_scheduled_message', args=[sm.id]))

        sm.scheduled_for = timezone.now() - timedelta(minutes=1)
        sm.save(update_fields=['scheduled_for'])
        enviar_mensagens_agendadas()

        sm.refresh_from_db()
        self.assertEqual(sm.status, 'cancelled')
        self.assertFalse(Message.objects.filter(conversation=self.conversation).exists())

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_midia_sem_legenda_nao_ganha_rotulo_grudado_no_agendamento(self, mock_client_cls):
        """O ponto sutil da feature: agendar guarda a legenda CRUA (vazia), e o
        rótulo padrão ('Imagem') só é calculado na hora do envio. Se o endpoint
        gravasse 'Imagem' como legenda, a mídia sairia com esse texto colado."""
        mock_client_cls.return_value.send_media.return_value = True
        b64 = base64.b64encode(b'bytes-de-imagem-fake').decode()

        resp = self.client.post(self.url, data=json.dumps({
            'mediaBase64': b64, 'mediaType': 'image', 'fileName': 'print.jpg',
            'caption': '', 'scheduled_for': (timezone.now() + timedelta(hours=1)).isoformat(),
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 200)

        sm = ScheduledMessage.objects.get()
        self.assertEqual(sm.content, '')  # legenda crua, não 'Imagem'

        sm.scheduled_for = timezone.now() - timedelta(minutes=1)
        sm.save(update_fields=['scheduled_for'])
        try:
            enviar_mensagens_agendadas()

            sm.refresh_from_db()
            self.assertEqual(sm.status, 'sent')
            msg = Message.objects.get(conversation=self.conversation)
            self.assertEqual(msg.message_type, 'image')
            # rótulo aplicado só no envio, e a legenda que foi pro WhatsApp é vazia
            self.assertEqual(msg.content, 'Imagem')
            _, kwargs = mock_client_cls.return_value.send_media.call_args
            self.assertEqual(kwargs.get('caption'), '')
        finally:
            for url in ScheduledMessage.objects.values_list('attachment_url', flat=True):
                if url:
                    caminho = os.path.join(settings.MEDIA_ROOT, url.replace(settings.MEDIA_URL, '', 1))
                    if os.path.exists(caminho):
                        os.remove(caminho)
            for url in Message.objects.exclude(attachment_url=None).values_list('attachment_url', flat=True):
                caminho = os.path.join(settings.MEDIA_ROOT, url.replace(settings.MEDIA_URL, '', 1))
                if os.path.exists(caminho):
                    os.remove(caminho)


class AutoAtribuicaoAoResponderTest(TestCase):
    """Quem responde, assume — em qualquer caminho de envio.

    A regra vivia só na view de texto (api_send_message), então responder
    por mídia ou por mensagem agendada deixava o chamado sem responsável e
    ele nunca aparecia na aba "Assumidos" de quem respondeu.
    """

    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = _criar_agente_staff('ana')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_send_message_atribui_conversa_sem_dono(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'remote-1')

        ok, _ = ConversationService.send_message(self.conversation, 'olá', self.agent)

        self.assertTrue(ok)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.assigned_to, self.agent)
        self.assertTrue(
            self.conversation.activity.filter(action='assigned', actor=self.agent).exists()
        )

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_send_message_nao_rouba_conversa_de_outro_atendente(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'remote-1')
        dono = _criar_agente_staff('bruno')
        self.conversation.assigned_to = dono
        self.conversation.save(update_fields=['assigned_to'])

        ConversationService.send_message(self.conversation, 'olá', self.agent)

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.assigned_to, dono)
        self.assertFalse(self.conversation.activity.filter(action='assigned').exists())

    @mock.patch('atendimento.services._save_media_file', return_value='/media/atendimento/media/fake.jpg')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_send_media_tambem_atribui(self, mock_client_cls, _mock_save):
        mock_client_cls.return_value.send_media.return_value = True

        ok, _ = ConversationService.send_media(
            self.conversation, base64.b64encode(b'x').decode(), 'image', 'foto.jpg', '', self.agent,
        )

        self.assertTrue(ok)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.assigned_to, self.agent)

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_mensagem_agendada_atribui_a_quem_agendou(self, mock_client_cls):
        """Caso real que falhou em produção: a conversa TOMICH TEC - NOC
        continuou com assigned_to=None depois de uma mensagem agendada."""
        mock_client_cls.return_value.send_text.return_value = (True, 'remote-1')
        ScheduledMessage.objects.create(
            conversation=self.conversation, created_by=self.agent,
            content='teste de agendamento',
            scheduled_for=timezone.now() - timedelta(minutes=1),
        )

        enviar_mensagens_agendadas()

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.assigned_to, self.agent)

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_api_send_message_devolve_newly_assigned(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'remote-1')
        self.client.force_login(self.agent)

        url = reverse('atendimento:api_send_message', args=[self.conversation.id])
        resp = self.client.post(url, json.dumps({'message': 'olá'}), content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['newly_assigned'])

        # segundo envio: já é dono, não é "recém-atribuído"
        resp2 = self.client.post(url, json.dumps({'message': 'de novo'}), content_type='application/json')
        self.assertFalse(resp2.json()['newly_assigned'])


class NotaInternaTest(TestCase):
    """Nota interna (toggle "Comentário Interno" do chat) não podia vazar pro
    WhatsApp do cliente. `is_internal` chegava do front até `api_send_message`
    e era descartado ali — toda nota "interna" saía pro grupo igual a uma
    resposta normal, só que sem o cliente saber que era pra ser privada."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = _criar_agente_staff('ana')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_nota_interna_nao_envia_ao_whatsapp(self, mock_client_cls):
        ok, msg_id = ConversationService.send_message(
            self.conversation, 'cliente devendo, não fazer visita', self.agent, is_internal=True)

        self.assertTrue(ok)
        mock_client_cls.return_value.send_text.assert_not_called()
        msg = Message.objects.get(id=msg_id)
        self.assertEqual(msg.sender_type, 'internal')
        self.assertTrue(msg.is_internal)

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_nota_interna_nao_conta_como_primeira_resposta(self, mock_client_cls):
        ConversationService.send_message(
            self.conversation, 'nota qualquer', self.agent, is_internal=True)

        self.conversation.refresh_from_db()
        self.assertIsNone(self.conversation.first_response_at)

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_mensagem_normal_continua_enviando_ao_whatsapp(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'remote-1')

        ConversationService.send_message(self.conversation, 'olá cliente', self.agent)

        mock_client_cls.return_value.send_text.assert_called_once()
        self.conversation.refresh_from_db()
        self.assertIsNotNone(self.conversation.first_response_at)

    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_nota_interna_com_abrir_tarefa_dispara_task(self, mock_client_cls, mock_delay):
        ConversationService.send_message(
            self.conversation, 'abrir tarefa: verificar contrato do cliente', self.agent, is_internal=True)

        mock_delay.assert_called_once_with(
            str(self.conversation.id), 'abrir tarefa: verificar contrato do cliente', True)

    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_nota_interna_com_acento_errado_em_tarefa_ainda_dispara(self, mock_client_cls, mock_delay):
        # Caso real relatado: "Tomichinho, criar tarefá..." (acento errado)
        # digitado como nota interna não criava a tarefa.
        texto = 'Tomichinho, criar tarefá de configuração do radius do erp hubsoft.'
        ConversationService.send_message(self.conversation, texto, self.agent, is_internal=True)

        mock_delay.assert_called_once_with(str(self.conversation.id), texto, True)

    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_nota_interna_sem_abrir_tarefa_nao_dispara_nada(self, mock_client_cls, mock_delay):
        ConversationService.send_message(
            self.conversation, 'só um lembrete qualquer', self.agent, is_internal=True)

        mock_delay.assert_not_called()

    @mock.patch('atendimento.tasks.responder_tomichinho.delay')
    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_tomichinho_em_nota_interna_nao_dispara_ia(self, mock_client_cls, mock_tarefa, mock_tomichinho):
        # "tomichinho" numa nota interna não pode gerar resposta automática
        # pro WhatsApp — só "abrir tarefa" é tratado em nota interna.
        ConversationService.send_message(
            self.conversation, 'tomichinho, o que você acha disso?', self.agent, is_internal=True)

        mock_tomichinho.assert_not_called()
        mock_tarefa.assert_not_called()

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_api_send_message_com_is_internal_salva_como_nota(self, mock_client_cls):
        self.client.force_login(self.agent)
        url = reverse('atendimento:api_send_message', args=[self.conversation.id])

        resp = self.client.post(
            url, json.dumps({'message': 'nota via API', 'is_internal': True}),
            content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        mock_client_cls.return_value.send_text.assert_not_called()
        msg = Message.objects.get(content='nota via API')
        self.assertEqual(msg.sender_type, 'internal')


class AbrirTarefaIAInternaTest(TestCase):
    """abrir_tarefa_ia disparada por nota interna: confirmação fica só no
    CRM, nunca sai pro WhatsApp do cliente."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.group = self.conversation.group

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch(
        'atendimento.ai.call_ai',
        return_value='{"titulo": "Verificar contrato", "descricao": ""}',
    )
    def test_confirmacao_de_tarefa_interna_nao_vai_ao_whatsapp(self, mock_call_ai, mock_client_cls):
        from tarefas.models import Tarefa
        from atendimento.tasks import abrir_tarefa_ia

        cliente = _criar_cliente_teste()
        self.group.cliente = cliente
        self.group.save(update_fields=['cliente'])

        resultado = abrir_tarefa_ia(
            str(self.conversation.id), 'abrir tarefa: verificar contrato', True)

        self.assertTrue(resultado['ok'])
        tarefa = Tarefa.objects.get(id=resultado['tarefa_id'])
        self.assertEqual(tarefa.titulo, 'Verificar contrato')
        mock_client_cls.return_value.send_text.assert_not_called()
        confirmacao = Message.objects.get(content__startswith='✅ Tarefa aberta')
        self.assertEqual(confirmacao.sender_type, 'internal')
        self.assertTrue(confirmacao.is_internal)


class ChatRenderTest(TestCase):
    """Renderização dos balões na tela de conversa."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = _criar_agente_staff('ana')
        self.client.force_login(self.agent)

    def _html(self):
        url = reverse('atendimento:conversation_detail', args=[self.conversation.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_balao_nao_carrega_indentacao_do_template(self):
        """O balão usa white-space:pre-wrap, então a indentação do template
        virava linha em branco e recuo dentro da bolha. O conteúdo tem que
        começar colado na tag."""
        Message.objects.create(
            conversation=self.conversation, sender_type='customer',
            message_type='text', content='linha1\nlinha2',
            external_id='ext-1', created_at=timezone.now(),
        )

        html = self._html()

        self.assertIn('<div class="msg-bubble"><span class="msg-text">linha1\nlinha2</span>', html)
        # e a quebra não pode ter virado <br> (senão sai dobrada com o pre-wrap)
        self.assertNotIn('linha1<br>linha2', html)

    def test_hora_e_tick_ficam_dentro_do_balao(self):
        Message.objects.create(
            conversation=self.conversation, sender_type='agent', sender=self.agent,
            message_type='text', content='oi', external_id='ext-2', created_at=timezone.now(),
        )

        html = self._html()

        self.assertIn('msg-meta', html)
        self.assertIn('fa-check-double', html)
        # a hora não pode mais estar solta como irmã do balão
        self.assertNotIn('</div>\n                <div class="msg-time">', html)

    def test_mensagens_seguidas_do_mesmo_remetente_sao_agrupadas(self):
        agora = timezone.now()
        for i in range(2):
            Message.objects.create(
                conversation=self.conversation, sender_type='customer',
                sender_name='Joao', message_type='text', content=f'msg{i}',
                external_id=f'ext-g{i}', created_at=agora + timedelta(seconds=i),
            )

        html = self._html()

        # a segunda mensagem entra como "grouped" (sem repetir nome/rabicho);
        # a primeira, não. Conta só a classe do elemento, já que a palavra
        # "grouped" também aparece nas regras de CSS da página.
        self.assertEqual(html.count('class="msg customer grouped"'), 1)
        self.assertEqual(html.count('class="msg customer"'), 1)

    def test_lista_lateral_mostra_responsavel(self):
        """Sintoma relatado: respondi e a conversa não apareceu como assumida."""
        self.conversation.assigned_to = self.agent
        self.conversation.save(update_fields=['assigned_to'])

        html = self._html()

        self.assertIn('conv-assignee', html)
        self.assertIn('Assumido por você', html)

    def test_lista_lateral_usa_o_partial_compartilhado(self):
        """conversation_detail tinha uma cópia própria da lista, sem
        data-conv-id — o indicador de não lidas não achava o item."""
        html = self._html()
        self.assertIn('data-conv-id="%s"' % self.conversation.id, html)


    def test_pagina_nao_vaza_comentario_de_template(self):
        """{# ... #} do Django é comentário de UMA linha só; em várias linhas
        o texto vaza pra tela. Comentários multilinha usam {% comment %}."""
        Message.objects.create(
            conversation=self.conversation, sender_type='customer',
            message_type='text', content='oi', external_id='ext-c',
        )
        html = self._html()
        self.assertNotIn('{#', html)
        self.assertNotIn('de propósito', html)


    def test_balao_mostra_pilula_de_reacao(self):
        from atendimento.models import MessageReaction
        msg = Message.objects.create(
            conversation=self.conversation, sender_type='agent', sender=self.agent,
            message_type='text', content='ja verifiquei', external_id='ext-r',
        )
        MessageReaction.objects.create(message=msg, emoji='\U0001F44D', external_id='r1',
                                       sender_name='Agiliza')
        MessageReaction.objects.create(message=msg, emoji='', external_id='r2',
                                       sender_name='Humberto')

        html = self._html()

        self.assertIn('msg-reactions', html)
        self.assertIn('\U0001F44D', html)
        # reacao criptografada: sem emoji, mostra "reagiu"
        self.assertIn('msg-reaction unknown', html)
        # dentro de um balão — a string solta também aparece num comentário do CSS
        self.assertNotIn('<span class="msg-text">[sem conteúdo]</span>', html)

    def test_inbox_renderiza_com_responsavel(self):
        """inbox.html usa _inbox_conv_item.html, um partial diferente do da
        barra lateral — precisa renderizar e mostrar o responsável também."""
        self.conversation.status = 'open'
        self.conversation.assigned_to = self.agent
        self.conversation.save(update_fields=['status', 'assigned_to'])

        resp = self.client.get(reverse('atendimento:inbox'))

        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('conv-assignee', html)
        self.assertNotIn('{#', html)


class ReacaoWebhookTest(TestCase):
    """Reações não podem virar balão vazio.

    Payloads reais capturados da Evolution API. Reagir a uma mensagem de outro
    participante chega como `reactionMessage` (emoji em texto puro); reagir a
    uma mensagem que NÓS enviamos chega como `secretEncryptedMessage`, cifrada
    — os dois caíam no fallback '[sem conteúdo]'.
    """

    def setUp(self):
        self.conversation = _criar_conversa()
        self.conversation.status = 'open'
        self.conversation.save(update_fields=['status'])
        # process_webhook só trata grupos (@g.us) — o helper padrão cria um
        # JID de contato individual, que seria ignorado logo na entrada.
        self.group = self.conversation.group
        self.group.jid = '551930903600-1620661694@g.us'
        self.group.save(update_fields=['jid'])
        self.alvo = Message.objects.create(
            conversation=self.conversation, sender_type='agent',
            message_type='text', content='Bom dia, já verifiquei aqui',
            external_id='3EB07D031ED20B338740EA',
        )

    def _webhook(self, message, msg_id='REACAO1'):
        return ConversationService.process_webhook({
            'event': 'MESSAGES_UPSERT',
            'instance': self.group.connection.instance_name,
            'data': {
                'key': {'id': msg_id, 'fromMe': False,
                        'remoteJid': self.group.jid, 'participant': '55279@lid'},
                'pushName': 'Agiliza Telecom',
                'message': message,
            },
        })

    def test_reaction_message_vira_reacao_e_nao_mensagem(self):
        antes = Message.objects.count()

        self._webhook({'reactionMessage': {
            'key': {'id': self.alvo.external_id, 'fromMe': True, 'remoteJid': self.group.jid},
            'text': '👍🏻',
        }})

        self.assertEqual(Message.objects.count(), antes, 'não pode criar balão')
        self.assertEqual(self.alvo.reactions.count(), 1)
        self.assertEqual(self.alvo.reactions.first().emoji, '👍🏻')
        self.assertFalse(Message.objects.filter(content='[sem conteúdo]').exists())

    def test_secret_encrypted_vira_reacao_sem_emoji(self):
        """Caso relatado: cliente reage a uma mensagem enviada por nós."""
        antes = Message.objects.count()

        self._webhook({'secretEncryptedMessage': {
            'encIv': 'fBzR79JJofiA/eLw',
            'encPayload': 's1DEJh8LAjbVR8Yh+lGPCaanL456GSS3',
            'secretEncType': 2,
            'targetMessageKey': {'id': self.alvo.external_id, 'fromMe': True,
                                 'remoteJid': self.group.jid},
        }})

        self.assertEqual(Message.objects.count(), antes)
        self.assertEqual(self.alvo.reactions.count(), 1)
        self.assertEqual(self.alvo.reactions.first().emoji, '')
        self.assertFalse(Message.objects.filter(content='[sem conteúdo]').exists())

    def test_reagir_de_novo_troca_a_reacao_da_mesma_pessoa(self):
        self._webhook({'reactionMessage': {
            'key': {'id': self.alvo.external_id}, 'text': '👍'}}, msg_id='R1')
        self._webhook({'reactionMessage': {
            'key': {'id': self.alvo.external_id}, 'text': '❤️'}}, msg_id='R2')

        self.assertEqual(self.alvo.reactions.count(), 1)
        self.assertEqual(self.alvo.reactions.first().emoji, '❤️')

    def test_texto_vazio_remove_a_reacao(self):
        self._webhook({'reactionMessage': {
            'key': {'id': self.alvo.external_id}, 'text': '👍'}}, msg_id='R1')

        self._webhook({'reactionMessage': {
            'key': {'id': self.alvo.external_id}, 'text': ''}}, msg_id='R2')

        self.assertEqual(self.alvo.reactions.count(), 0)

    def test_reacao_a_mensagem_desconhecida_nao_cria_balao(self):
        antes = Message.objects.count()

        self._webhook({'reactionMessage': {
            'key': {'id': 'MENSAGEM-QUE-NAO-TEMOS'}, 'text': '👍'}})

        self.assertEqual(Message.objects.count(), antes)

    def test_album_header_nao_vira_balao(self):
        antes = Message.objects.count()

        self._webhook({'albumMessage': {'expectedImageCount': 1, 'expectedVideoCount': 1}})

        self.assertEqual(Message.objects.count(), antes)

    def test_mensagem_de_texto_normal_continua_funcionando(self):
        self._webhook({'conversation': 'esse foi o dado que não conseguiu add?'}, msg_id='TXT1')

        msg = Message.objects.get(external_id='TXT1')
        self.assertEqual(msg.content, 'esse foi o dado que não conseguiu add?')
        self.assertEqual(msg.sender_type, 'customer')


    def test_contato_compartilhado_mostra_o_nome(self):
        self._webhook({'contactMessage': {
            'displayName': 'Roberto Suporte',
            'vcard': 'BEGIN:VCARD\nFN:Roberto Suporte\nEND:VCARD',
        }}, msg_id='VCARD1')

        msg = Message.objects.get(external_id='VCARD1')
        self.assertEqual(msg.content, '\U0001F464 Roberto Suporte')

    def test_varios_contatos_compartilhados(self):
        self._webhook({'contactsArrayMessage': {'contacts': [
            {'displayName': 'Roberto'}, {'displayName': 'Maria'},
        ]}}, msg_id='VCARD2')

        msg = Message.objects.get(external_id='VCARD2')
        self.assertEqual(msg.content, '\U0001F464 Roberto, Maria')

    def test_pin_e_child_nao_viram_balao(self):
        antes = Message.objects.count()

        self._webhook({'pinInChatMessage': {'key': {'id': 'x'}}}, msg_id='PIN1')
        self._webhook({'associatedChildMessage': {}}, msg_id='CHILD1')

        self.assertEqual(Message.objects.count(), antes)


class AutoAtendimentoNaoPoluiGrupoTest(TestCase):
    """O robô respondia toda primeira mensagem com a saudação + a mensagem de
    conclusão do fluxo, direto no grupo do cliente. Como o chamado já abre na
    1ª mensagem sem depender de resposta do bot, isso só enchia a conversa do
    grupo de mensagens automáticas."""

    def setUp(self):
        from atendimento.models import ChatFlow
        self.conversation = _criar_conversa()
        self.group = self.conversation.group
        self.group.jid = '551930903601-1620661695@g.us'
        self.group.save(update_fields=['jid'])
        # Fluxo universal (group_ids vazio) e ativo — pega qualquer grupo
        self.flow = ChatFlow.objects.create(
            name='Primeiro atendimento', active=True,
            greeting_message='Olá! Seu chamado foi aberto.',
            completion_message='Em breve um atendente responde.',
        )
        # A conversa criada pelo helper ficaria como "chamado já aberto";
        # o caminho que disparava o bot é o de conversa nova.
        self.conversation.status = 'closed'
        self.conversation.save(update_fields=['status'])

    def _webhook(self, texto, msg_id):
        return ConversationService.process_webhook({
            'event': 'MESSAGES_UPSERT',
            'instance': self.group.connection.instance_name,
            'data': {
                'key': {'id': msg_id, 'fromMe': False,
                        'remoteJid': self.group.jid, 'participant': '55279@lid'},
                'pushName': 'Cliente',
                'message': {'conversation': texto},
            },
        })

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_chamado_novo_nao_manda_nada_no_grupo(self, mock_client_cls):
        resultado = self._webhook('bom dia, sem internet', 'MSGNOVA1')

        self.assertTrue(resultado['success'])
        mock_client_cls.return_value.send_text.assert_not_called()

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_chamado_novo_nao_cria_balao_do_robo(self, mock_client_cls):
        self._webhook('bom dia, sem internet', 'MSGNOVA2')

        conv = Conversation.objects.filter(
            group=self.group, status='open').order_by('-created_at').first()
        self.assertIsNotNone(conv)
        self.assertFalse(
            conv.messages.filter(sender_type='system').exists(),
            'auto atendimento não deve mais gravar balão "Auto Atendimento"',
        )
        self.assertEqual(conv.messages.filter(sender_type='customer').count(), 1)


class AtualizacaoAutomaticaDaListaTest(TestCase):
    """A lista lateral só se atualizava com F5.

    O WebSocket avisava da mensagem nova (som + toast), mas o refresh do painel
    buscava a URL da página atual. Dentro de um chamado essa URL é o
    conversation_detail, que herda a lista em vez de renderizá-la — a resposta
    vinha com o bloco vazio e nada era atualizado. A fonte correta é o Inbox.
    """

    def setUp(self):
        self.agent = _criar_agente_staff('bruno')
        self.client.force_login(self.agent)
        self.conversation = _criar_conversa()
        self.conversation.status = 'open'
        self.conversation.save(update_fields=['status'])

    def _ajax(self, url):
        return self.client.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_inbox_via_ajax_devolve_a_lista_no_bloco_conv_panel(self):
        html = self._ajax(reverse('atendimento:inbox')).content.decode()

        self.assertIn('data-target="conv-panel"', html)
        self.assertIn('data-conv-id="%s"' % self.conversation.id, html)

    def test_detalhe_da_conversa_via_ajax_nao_traz_a_lista(self):
        """Documenta por que o refresh precisa apontar para o Inbox: aqui o
        bloco conv_panel vem vazio de propósito (a lista já está na tela)."""
        url = reverse('atendimento:conversation_detail', args=[self.conversation.id])
        html = self._ajax(url).content.decode()

        self.assertIn('data-target="conv-panel"', html)
        self.assertNotIn('data-conv-id="%s"' % self.conversation.id, html)

    def test_chamado_novo_aparece_na_lista_buscada_pelo_refresh(self):
        nova = _criar_conversa()
        nova.status = 'open'
        nova.save(update_fields=['status'])

        html = self._ajax(reverse('atendimento:inbox') + '?tab=open').content.decode()

        self.assertIn('data-conv-id="%s"' % nova.id, html)


class ApiAgentsListTest(TestCase):
    """Modal "Transferir chamado" — quem entra e quem não entra na lista."""

    def setUp(self):
        self.eu = _criar_agente_staff('lucas')
        self.client.force_login(self.eu)

    def _nomes(self):
        resp = self.client.get(reverse('atendimento:api_agents_list'))
        self.assertEqual(resp.status_code, 200)
        return [a['name'] for a in json.loads(resp.content)['agents']]

    def test_conta_sem_identificacao_e_sem_historico_fica_de_fora(self):
        # Sobra de cadastro: is_staff, mas sem nome, sem e-mail, sem
        # AgentStatus e sem nenhum chamado. Era o "atendente que não existe"
        # que aparecia no modal.
        User.objects.create_user(username='adm_466dee', is_staff=True, is_active=True)
        self.assertNotIn('adm_466dee', self._nomes())

    def test_colega_sem_chamado_mas_com_email_continua_na_lista(self):
        # Nunca atendeu nada, mas é pessoa de verdade — não pode sumir, senão
        # ninguém consegue transferir pra ele na primeira vez.
        User.objects.create_user(
            username='josefh', email='josefh@example.com', is_staff=True, is_active=True,
        )
        self.assertIn('josefh', self._nomes())

    def test_colega_sem_email_mas_com_chamado_continua_na_lista(self):
        colega = User.objects.create_user(username='nailson', is_staff=True, is_active=True)
        conv = _criar_conversa()
        conv.assigned_to = colega
        conv.save()
        self.assertIn('nailson', self._nomes())

    def test_nao_lista_o_proprio_usuario_nem_inativo(self):
        User.objects.create_user(
            username='desligado', email='x@example.com', is_staff=True, is_active=False,
        )
        nomes = self._nomes()
        self.assertNotIn('lucas', nomes)
        self.assertNotIn('desligado', nomes)

    def test_nao_lista_quem_nao_acessa_o_modulo(self):
        # Consultor/Operador têm is_staff=False e o módulo inteiro é
        # staff_required: transferir pra eles sumiria com o chamado.
        from usuario.models import Instancia, PerfilUsuario
        inst = Instancia.objects.create(nome='Marinho')
        consultor = User.objects.create_user(
            username='mmarinho', email='m@example.com', is_staff=False, is_active=True,
        )
        PerfilUsuario.objects.create(
            usuario=consultor, role=PerfilUsuario.ROLE_CONSULTOR, instancia=inst,
        )
        self.assertNotIn('mmarinho', self._nomes())


def _criar_cliente_teste(nome='Cliente IA Teste'):
    from clientes.models import Cliente
    suffix = uuid.uuid4().hex[:8]
    return Cliente.objects.create(
        nome_empresa=nome, cnpj=f'00.000.000/{suffix[:4]}-00',
        endereco='Rua Teste, 123', email=f'{suffix}@example.com',
    )


class AgenteIACallTest(TestCase):
    """atendimento/ai.py: sem chave configurada não pode derrubar quem chama —
    é sempre None, nunca exceção, pros gatilhos do agente degradarem sozinhos."""

    def test_sem_nenhuma_chave_configurada_retorna_none(self):
        from atendimento.ai import call_ai
        self.assertIsNone(call_ai('system', 'user'))

    def test_provider_openai_sem_chave_retorna_none(self):
        from atendimento.models import SystemSetting
        from atendimento.ai import call_ai
        SystemSetting.set('ai_provider', 'openai')
        self.assertIsNone(call_ai('system', 'user'))


class GatilhoAgenteIATest(TestCase):
    """"tomichinho" e "abrir tarefa" no texto da mensagem disparam as tasks
    do agente IA — qualquer remetente aciona (inclusive o próprio cliente)."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.group = self.conversation.group
        self.group.jid = '551930903699-1620661699@g.us'
        self.group.save(update_fields=['jid'])

    def _webhook(self, texto, msg_id):
        return ConversationService.process_webhook({
            'event': 'MESSAGES_UPSERT',
            'instance': self.group.connection.instance_name,
            'data': {
                'key': {'id': msg_id, 'fromMe': False,
                        'remoteJid': self.group.jid, 'participant': '55279@lid'},
                'pushName': 'Cliente Teste',
                'message': {'conversation': texto},
            },
        })

    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.tasks.responder_tomichinho.delay')
    def test_tomichinho_no_texto_dispara_a_task(self, mock_tomichinho, mock_tarefa):
        self._webhook('Oi tomichinho, tudo bem?', 'M1')
        mock_tomichinho.assert_called_once_with(str(self.conversation.id))
        mock_tarefa.assert_not_called()

    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.tasks.responder_tomichinho.delay')
    def test_abrir_tarefa_no_texto_dispara_a_task(self, mock_tomichinho, mock_tarefa):
        self._webhook('abrir tarefa: trocar antena amanhã de manhã', 'M2')
        mock_tarefa.assert_called_once_with(
            str(self.conversation.id), 'abrir tarefa: trocar antena amanhã de manhã', False)
        mock_tomichinho.assert_not_called()

    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.tasks.responder_tomichinho.delay')
    def test_mensagem_normal_nao_dispara_nada(self, mock_tomichinho, mock_tarefa):
        self._webhook('Bom dia, o link caiu de novo', 'M3')
        mock_tomichinho.assert_not_called()
        mock_tarefa.assert_not_called()

    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.tasks.responder_tomichinho.delay')
    def test_as_duas_palavras_juntas_disparam_as_duas_tasks(self, mock_tomichinho, mock_tarefa):
        self._webhook('tomichinho, abrir tarefa pra isso aqui', 'M4')
        mock_tomichinho.assert_called_once()
        mock_tarefa.assert_called_once()

    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.tasks.responder_tomichinho.delay')
    def test_variacoes_do_pedido_de_tarefa_tambem_disparam(self, mock_tomichinho, mock_tarefa):
        # Caso real relatado: "Tomichinho, criar tarefa" não disparava nada
        # porque só o literal "abrir tarefa" era reconhecido.
        for i, texto in enumerate(['Tomichinho, criar tarefa', 'cria uma tarefa pra amanhã',
                                    'nova tarefa: verificar contrato'], start=1):
            mock_tarefa.reset_mock()
            self._webhook(texto, f'VAR{i}')
            mock_tarefa.assert_called_once_with(str(self.conversation.id), texto, False)

    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.tasks.responder_tomichinho.delay')
    def test_erro_de_acentuacao_em_tarefa_ainda_dispara(self, mock_tomichinho, mock_tarefa):
        # Caso real relatado: "criar tarefá" (acento errado) não disparava
        # nada porque a comparação era exata caractere a caractere.
        texto = 'Tomichinho, criar tarefá de configuração do radius do erp hubsoft.'
        self._webhook(texto, 'ACENTO1')
        mock_tarefa.assert_called_once_with(str(self.conversation.id), texto, False)

    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.tasks.responder_tomichinho.delay')
    def test_mencionar_uma_tarefa_sem_pedir_para_criar_nao_dispara(self, mock_tomichinho, mock_tarefa):
        self._webhook('essa tarefa já está atrasada, alguém viu?', 'M5')
        mock_tarefa.assert_not_called()


class ResponderTomichinhoTaskTest(TestCase):
    """Task que efetivamente lê o histórico e responde no grupo via IA."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.group = self.conversation.group
        Message.objects.create(
            conversation=self.conversation, sender_type='customer',
            content='tomichinho, qual o horário de atendimento de vocês?',
            external_id='msg-1',
        )

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch('atendimento.ai.call_ai', return_value='Atendemos de seg a sex, 8h às 18h.')
    def test_responde_e_salva_mensagem_tipo_ia(self, mock_call_ai, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'wamid-ia-1')
        from atendimento.tasks import responder_tomichinho

        resultado = responder_tomichinho(str(self.conversation.id))

        self.assertTrue(resultado['ok'])
        msg = Message.objects.get(sender_type='ai')
        self.assertEqual(msg.content, 'Atendemos de seg a sex, 8h às 18h.')
        self.assertEqual(msg.sender_name, 'Tomichinho')
        mock_client_cls.return_value.send_text.assert_called_once_with(
            self.group.jid, 'Atendemos de seg a sex, 8h às 18h.')

    @mock.patch('atendimento.ai.call_ai', return_value=None)
    def test_sem_ia_configurada_nao_cria_mensagem(self, mock_call_ai):
        from atendimento.tasks import responder_tomichinho

        antes = Message.objects.count()
        resultado = responder_tomichinho(str(self.conversation.id))

        self.assertFalse(resultado['ok'])
        self.assertEqual(Message.objects.count(), antes)


class AbrirTarefaIATaskTest(TestCase):
    """Task que interpreta "abrir tarefa" e cria a Tarefa vinculada ao
    cliente do grupo do WhatsApp."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.group = self.conversation.group

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch(
        'atendimento.ai.call_ai',
        return_value='{"titulo": "Trocar antena do cliente", "descricao": "Antena com sinal fraco, trocar amanhã de manhã"}',
    )
    def test_cria_tarefa_vinculada_ao_cliente_do_grupo(self, mock_call_ai, mock_client_cls):
        from tarefas.models import Tarefa
        from atendimento.tasks import abrir_tarefa_ia

        cliente = _criar_cliente_teste()
        self.group.cliente = cliente
        self.group.save(update_fields=['cliente'])

        resultado = abrir_tarefa_ia(str(self.conversation.id), 'abrir tarefa: antena fraca, trocar amanhã')

        self.assertTrue(resultado['ok'])
        tarefa = Tarefa.objects.get(id=resultado['tarefa_id'])
        self.assertEqual(tarefa.titulo, 'Trocar antena do cliente')
        self.assertEqual(tarefa.cliente, cliente)
        self.assertEqual(tarefa.responsaveis.count(), 0)
        mock_client_cls.return_value.send_text.assert_called_once()

    def test_grupo_sem_cliente_vinculado_nao_cria_tarefa(self):
        from tarefas.models import Tarefa
        from atendimento.tasks import abrir_tarefa_ia

        antes = Tarefa.objects.count()
        resultado = abrir_tarefa_ia(str(self.conversation.id), 'abrir tarefa: qualquer coisa')

        self.assertTrue(resultado['skipped'])
        self.assertEqual(Tarefa.objects.count(), antes)

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch('atendimento.ai.call_ai', return_value=None)
    def test_sem_ia_usa_o_proprio_texto_como_titulo(self, mock_call_ai, mock_client_cls):
        from tarefas.models import Tarefa
        from atendimento.tasks import abrir_tarefa_ia

        cliente = _criar_cliente_teste()
        self.group.cliente = cliente
        self.group.save(update_fields=['cliente'])

        resultado = abrir_tarefa_ia(str(self.conversation.id), 'abrir tarefa: sem IA configurada')

        tarefa = Tarefa.objects.get(id=resultado['tarefa_id'])
        self.assertEqual(tarefa.titulo, 'abrir tarefa: sem IA configurada')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch('atendimento.ai.call_ai', return_value='{"titulo": "Verificar MTU", "descricao": "Cliente relata que pacotes acima de 1442 bytes não passam"}')
    def test_comando_vazio_usa_historico_da_conversa_pra_entender_o_pedido(self, mock_call_ai, mock_client_cls):
        # Caso real: "Tomichinho, criar tarefa" não diz nada sozinho — o
        # pedido de verdade estava na mensagem do cliente logo antes.
        from tarefas.models import Tarefa
        from atendimento.tasks import abrir_tarefa_ia

        cliente = _criar_cliente_teste()
        self.group.cliente = cliente
        self.group.save(update_fields=['cliente'])
        Message.objects.create(
            conversation=self.conversation, sender_type='customer',
            content='Sobre o MTU: pacotes acima de 1442 não estão passando, pode verificar?',
            external_id='cli-1',
        )

        resultado = abrir_tarefa_ia(str(self.conversation.id), 'Tomichinho, criar tarefa', True)

        tarefa = Tarefa.objects.get(id=resultado['tarefa_id'])
        self.assertEqual(tarefa.titulo, 'Verificar MTU')
        self.assertIn('1442', tarefa.descricao)
        # O prompt mandado pra IA precisa carregar a mensagem do cliente,
        # não só o comando vazio.
        _args, kwargs = mock_call_ai.call_args
        self.assertIn('1442', kwargs['user_prompt'])

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch('atendimento.ai.call_ai', return_value=None)
    def test_sem_ia_e_sem_detalhe_no_comando_usa_ultima_mensagem_do_cliente(self, mock_call_ai, mock_client_cls):
        from tarefas.models import Tarefa
        from atendimento.tasks import abrir_tarefa_ia

        cliente = _criar_cliente_teste()
        self.group.cliente = cliente
        self.group.save(update_fields=['cliente'])
        Message.objects.create(
            conversation=self.conversation, sender_type='customer',
            content='O link caiu de novo aqui, pode verificar?',
            external_id='cli-2',
        )

        resultado = abrir_tarefa_ia(str(self.conversation.id), 'Tomichinho, criar tarefa', True)

        tarefa = Tarefa.objects.get(id=resultado['tarefa_id'])
        self.assertEqual(tarefa.titulo, 'O link caiu de novo aqui, pode verificar?')


class GatilhoFechamentoIATest(TestCase):
    """Pedido de fechamento no texto da mensagem dispara `fechar_chamado_ia`."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.group = self.conversation.group
        self.group.jid = '551930903699-1620661700@g.us'
        self.group.save(update_fields=['jid'])
        self.agent = _criar_agente_staff('bia')

    def _webhook(self, texto, msg_id):
        return ConversationService.process_webhook({
            'event': 'MESSAGES_UPSERT',
            'instance': self.group.connection.instance_name,
            'data': {
                'key': {'id': msg_id, 'fromMe': False,
                        'remoteJid': self.group.jid, 'participant': '55279@lid'},
                'pushName': 'Cliente Teste',
                'message': {'conversation': texto},
            },
        })

    @mock.patch('atendimento.tasks.fechar_chamado_ia.delay')
    def test_variacoes_do_pedido_de_fechamento_disparam(self, mock_fechar):
        for i, texto in enumerate(['pode fechar o chamado', 'Tomichinho, encerrar o atendimento',
                                   'finalizar chamado por favor', 'ticket resolvido, pode encerrar'],
                                  start=1):
            mock_fechar.reset_mock()
            self._webhook(texto, f'FEC{i}')
            mock_fechar.assert_called_once_with(str(self.conversation.id), texto, False)

    @mock.patch('atendimento.tasks.fechar_chamado_ia.delay')
    def test_pedido_negado_nao_dispara(self, mock_fechar):
        # "não pode fechar o chamado ainda" pede o oposto — fechar aqui
        # seria o pior erro possível do agente.
        for i, texto in enumerate(['não pode fechar o chamado ainda',
                                   'não vamos encerrar esse atendimento hoje'], start=1):
            self._webhook(texto, f'NEG{i}')
        mock_fechar.assert_not_called()

    @mock.patch('atendimento.tasks.fechar_chamado_ia.delay')
    def test_chamado_nao_resolvido_nao_dispara(self, mock_fechar):
        self._webhook('o chamado ainda não está resolvido', 'FEC-N4')
        mock_fechar.assert_not_called()

    @mock.patch('atendimento.tasks.fechar_chamado_ia.delay')
    def test_mensagem_normal_nao_fecha_chamado(self, mock_fechar):
        self._webhook('bom dia, o link caiu de novo', 'FEC-N1')
        mock_fechar.assert_not_called()

    @mock.patch('atendimento.tasks.fechar_chamado_ia.delay')
    def test_fechar_sem_dizer_chamado_nao_dispara(self, mock_fechar):
        # "pode fechar" solto (ex.: falando de uma porta, de um contrato) não
        # pode encerrar o atendimento de ninguém.
        self._webhook('pode fechar a porta do rack quando terminar', 'FEC-N2')
        mock_fechar.assert_not_called()

    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.tasks.fechar_chamado_ia.delay')
    def test_fechar_tarefa_nao_fecha_chamado(self, mock_fechar, mock_tarefa):
        self._webhook('fechar a tarefa da antena', 'FEC-N3')
        mock_fechar.assert_not_called()

    @mock.patch('atendimento.tasks.fechar_chamado_ia.delay')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_mensagem_normal_do_chat_tambem_dispara(self, mock_client_cls, mock_fechar):
        # Escrito na caixa normal do chat (não em nota interna): antes
        # `send_message` só olhava os gatilhos quando era comentário interno,
        # então "Tomichinho fechar atendimento" digitado no chat não fazia nada.
        ConversationService.send_message(
            self.conversation, 'Tomichinho fechar atendimento', self.agent)

        mock_fechar.assert_called_once_with(
            str(self.conversation.id), 'Tomichinho fechar atendimento', False)

    @mock.patch('atendimento.tasks.responder_tomichinho.delay')
    @mock.patch('atendimento.tasks.fechar_chamado_ia.delay')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_mensagem_normal_do_chat_nao_gera_resposta_conversacional(
            self, mock_client_cls, mock_fechar, mock_tomichinho):
        # "tomichinho" no que o atendente manda pela plataforma não pode virar
        # mais uma mensagem do bot pro cliente — só a ação pedida.
        ConversationService.send_message(
            self.conversation, 'Tomichinho fechar atendimento', self.agent)

        mock_tomichinho.assert_not_called()
        mock_fechar.assert_called_once()

    @mock.patch('atendimento.tasks.abrir_tarefa_ia.delay')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_mensagem_normal_do_chat_tambem_abre_tarefa(self, mock_client_cls, mock_tarefa):
        ConversationService.send_message(
            self.conversation, 'Tomichinho, criar tarefa pra trocar a antena', self.agent)

        mock_tarefa.assert_called_once_with(
            str(self.conversation.id), 'Tomichinho, criar tarefa pra trocar a antena', False)

    @mock.patch('atendimento.tasks.fechar_chamado_ia.delay')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_mensagem_de_encerramento_nao_redispara_o_fechamento(self, mock_client_cls, mock_fechar):
        # A "Mensagem de encerramento" das configurações sai por send_message
        # depois do chamado já estar resolvido e costuma citar o atendimento
        # ("Finalizamos seu atendimento") — não pode realimentar o gatilho.
        from atendimento.models import SystemSetting
        from atendimento.services import finalizar_conversa
        SystemSetting.set('msg_encerramento', 'Finalizamos seu atendimento, obrigado!')

        finalizar_conversa(self.conversation, resolution='ok', actor=self.agent)

        mock_fechar.assert_not_called()

    @mock.patch('atendimento.tasks.fechar_chamado_ia.delay')
    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_nota_interna_com_pedido_de_fechamento_dispara_como_interna(self, mock_client_cls, mock_fechar):
        ConversationService.send_message(
            self.conversation, 'pode encerrar o chamado, resolvido', self.agent, is_internal=True)

        mock_fechar.assert_called_once_with(
            str(self.conversation.id), 'pode encerrar o chamado, resolvido', True)


class FecharChamadoIATaskTest(TestCase):
    """Task que redige a resolução via IA e encerra o chamado."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.group = self.conversation.group
        self.conversation.status = 'open'
        self.conversation.save(update_fields=['status'])
        Message.objects.create(
            conversation=self.conversation, sender_type='customer',
            content='O link caiu de novo aqui', external_id='cli-f1')
        Message.objects.create(
            conversation=self.conversation, sender_type='agent',
            content='Troquei o SFP da porta 3 da OLT, link estabilizado.',
            external_id='ag-f1')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch(
        'atendimento.ai.call_ai',
        return_value='{"resolucao": "Cliente relatou queda do link; SFP da porta 3 da OLT trocado, link estabilizado."}',
    )
    def test_fecha_com_resolucao_da_ia_e_confirma_no_grupo(self, mock_call_ai, mock_client_cls):
        from atendimento.tasks import fechar_chamado_ia
        mock_client_cls.return_value.send_text.return_value = (True, 'wamid-f1')

        resultado = fechar_chamado_ia(str(self.conversation.id), 'pode fechar o chamado')

        self.assertTrue(resultado['ok'])
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, 'resolved')
        self.assertIsNotNone(self.conversation.closed_at)
        self.assertIn('SFP da porta 3', self.conversation.resolution)
        # A resposta do atendente precisa chegar à IA — é dela que sai a resolução.
        _args, kwargs = mock_call_ai.call_args
        self.assertIn('Troquei o SFP', kwargs['user_prompt'])
        confirmacao = Message.objects.get(sender_type='ai')
        self.assertIn('Resolução', confirmacao.content)
        mock_client_cls.return_value.send_text.assert_called_once()

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch('atendimento.ai.call_ai', return_value='{"resolucao": "Resolvido."}')
    def test_pedido_em_nota_interna_nao_vaza_pro_whatsapp(self, mock_call_ai, mock_client_cls):
        from atendimento.tasks import fechar_chamado_ia

        fechar_chamado_ia(str(self.conversation.id), 'pode encerrar o chamado', True)

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, 'resolved')
        mock_client_cls.return_value.send_text.assert_not_called()
        confirmacao = Message.objects.get(content__startswith='✅ Chamado #')
        self.assertEqual(confirmacao.sender_type, 'internal')
        self.assertTrue(confirmacao.is_internal)

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch('atendimento.ai.call_ai', return_value=None)
    def test_sem_ia_fecha_com_a_ultima_resposta_do_atendente(self, mock_call_ai, mock_client_cls):
        from atendimento.tasks import fechar_chamado_ia

        fechar_chamado_ia(str(self.conversation.id), 'pode fechar o chamado')

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, 'resolved')
        self.assertEqual(
            self.conversation.resolution,
            'Troquei o SFP da porta 3 da OLT, link estabilizado.')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch('atendimento.ai.call_ai')
    def test_chamado_ja_encerrado_nao_e_fechado_de_novo(self, mock_call_ai, mock_client_cls):
        from atendimento.tasks import fechar_chamado_ia
        self.conversation.status = 'resolved'
        self.conversation.resolution = 'Resolução original'
        self.conversation.save(update_fields=['status', 'resolution'])

        resultado = fechar_chamado_ia(str(self.conversation.id), 'fechar chamado')

        self.assertTrue(resultado['skipped'])
        mock_call_ai.assert_not_called()
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.resolution, 'Resolução original')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch('atendimento.ai.call_ai', return_value='{"resolucao": "Resolvido."}')
    def test_marco_de_conclusao_com_protocolo_fica_no_historico(self, mock_call_ai, mock_client_cls):
        from atendimento.tasks import fechar_chamado_ia

        fechar_chamado_ia(str(self.conversation.id), 'pode fechar o chamado')

        marco = Message.objects.get(sender_type='system')
        self.assertIn(f'#{self.conversation.conversation_id}', marco.content)


class ChamadosDoClienteAPITest(TestCase):
    """API que alimenta o botão "Listar Chamados" da aba Tarefas na página do
    cliente."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.group = self.conversation.group
        self.agent = _criar_agente_staff('carla')
        self.cliente = _criar_cliente_teste('Cliente Chamados')
        self.conversation.cliente = self.cliente
        self.conversation.status = 'resolved'
        self.conversation.resolution = 'Trocado o SFP da porta 3'
        self.conversation.assigned_to = self.agent
        self.conversation.save(update_fields=['cliente', 'status', 'resolution', 'assigned_to'])
        self.url = reverse('atendimento:api_cliente_conversations', args=[self.cliente.id])

    def test_lista_chamados_do_cliente(self):
        self.client.force_login(self.agent)

        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['total'], 1)
        chamado = data['chamados'][0]
        self.assertEqual(chamado['protocolo'], f'#{self.conversation.conversation_id}')
        self.assertEqual(chamado['status_label'], 'Resolvido')
        self.assertEqual(chamado['resolucao'], 'Trocado o SFP da porta 3')
        self.assertEqual(chamado['url'], f'/atendimento/conversation/{self.conversation.id}/')

    def test_chamado_vinculado_so_pelo_grupo_tambem_aparece(self):
        # Chamado antigo, aberto antes de o grupo ser vinculado ao cliente:
        # `Conversation.cliente` fica vazio e o vínculo existe só no grupo.
        self.conversation.cliente = None
        self.conversation.save(update_fields=['cliente'])
        self.group.cliente = self.cliente
        self.group.save(update_fields=['cliente'])
        self.client.force_login(self.agent)

        resp = self.client.get(self.url)

        self.assertEqual(resp.json()['total'], 1)

    def test_chamado_em_pre_abertura_nao_aparece(self):
        self.conversation.status = 'pre'
        self.conversation.save(update_fields=['status'])
        self.client.force_login(self.agent)

        self.assertEqual(self.client.get(self.url).json()['total'], 0)

    def test_nao_staff_nao_acessa(self):
        comum = User.objects.create_user(username='cliente_portal', password='x')
        self.client.force_login(comum)

        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 302)
