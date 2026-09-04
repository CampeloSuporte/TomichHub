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
    SystemSetting,
)
from atendimento.services import (
    ConversationService, EvolutionAPIClient, _save_media_file, _read_attachment_as_base64,
)
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


def _criar_usuario_portal(username='cliente_portal'):
    """Usuário do portal do cliente (não staff). Precisa de TOTP confirmado
    como qualquer outro: o Forcar2FAMiddleware manda todo mundo pro
    /configurar-2fa/ antes da view, e o teste veria só um 302."""
    from usuario.models import TOTPDevice
    user = User.objects.create_user(username=username, password='x', is_active=True)
    TOTPDevice.objects.create(usuario=user, secret='JBSWY3DPEHPK3PXP', confirmado=True)
    return user


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
        o texto vaza pra tela — junto com o literal "{#". Comentários
        multilinha usam {% comment %}, que some do HTML por completo.

        A checagem por texto usa a frase EXATA do bloco {% comment %} do
        template. A versão anterior procurava só "de propósito", que é prosa
        comum: um comentário legítimo de CSS com essas duas palavras quebrava
        o teste sem que nada tivesse vazado (aconteceu em 04/09/2026, com o
        comentário do lápis de editar).
        """
        Message.objects.create(
            conversation=self.conversation, sender_type='customer',
            message_type='text', content='oi', external_id='ext-c',
        )
        html = self._html()
        # O sinal estrutural: um {# multilinha deixa o próprio literal na tela.
        self.assertNotIn('{#', html)
        self.assertNotIn('{%', html)
        # E a frase do {% comment %} do _chat_content.html, que é o bloco que
        # de fato vazou na época.
        self.assertNotIn('o conteúdo é emitido colado nas tags', html)


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
    @mock.patch('atendimento.ai.call_ai', return_value=None)
    def test_sem_ia_nao_usa_o_proprio_pedido_de_fechamento_como_resolucao(
            self, mock_call_ai, mock_client_cls):
        """O comando já está gravado como mensagem quando a task roda —
        pegá-lo de volta era o que enchia o campo Resolução de "pode
        finalizar o chamado"."""
        from atendimento.tasks import fechar_chamado_ia
        Message.objects.create(
            conversation=self.conversation, sender_type='internal',
            content='pode finalizar o chamado', external_id='nota-f1')

        fechar_chamado_ia(str(self.conversation.id), 'pode finalizar o chamado', True)

        self.conversation.refresh_from_db()
        self.assertEqual(
            self.conversation.resolution,
            'Troquei o SFP da porta 3 da OLT, link estabilizado.')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch('atendimento.ai.call_ai', return_value=None)
    def test_sem_ia_avisa_no_chat_que_a_resolucao_nao_veio_da_ia(
            self, mock_call_ai, mock_client_cls):
        from atendimento.ai import AI_ERRO_KEY
        from atendimento.tasks import fechar_chamado_ia
        SystemSetting.set(AI_ERRO_KEY, 'ChatGPT: conta sem crédito/quota')

        fechar_chamado_ia(str(self.conversation.id), 'pode fechar o chamado')

        confirmacao = Message.objects.get(content__startswith='✅ Chamado #')
        self.assertIn('conta sem crédito', confirmacao.content)
        self.assertIn('⚠️', confirmacao.content)

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch('atendimento.ai.call_ai', return_value=None)
    def test_sem_ia_e_sem_fala_do_atendente_descreve_o_relato_do_cliente(
            self, mock_call_ai, mock_client_cls):
        from atendimento.tasks import fechar_chamado_ia
        Message.objects.filter(sender_type='agent').delete()

        fechar_chamado_ia(str(self.conversation.id), 'pode fechar o chamado')

        self.conversation.refresh_from_db()
        self.assertIn('O link caiu de novo aqui', self.conversation.resolution)
        self.assertNotIn('pode fechar', self.conversation.resolution)

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch('atendimento.ai.call_ai',
                return_value='{"resolucao": "pode finalizar o chamado"}')
    def test_ia_devolvendo_o_comando_cai_no_fallback(self, mock_call_ai, mock_client_cls):
        from atendimento.tasks import fechar_chamado_ia

        fechar_chamado_ia(str(self.conversation.id), 'pode finalizar o chamado')

        self.conversation.refresh_from_db()
        self.assertEqual(
            self.conversation.resolution,
            'Troquei o SFP da porta 3 da OLT, link estabilizado.')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    @mock.patch('atendimento.ai.call_ai',
                return_value='{"resolucao": "Resolvido: link estabilizado após a troca do SFP."}')
    def test_relato_original_do_cliente_vai_pro_prompt_mesmo_em_conversa_longa(
            self, mock_call_ai, mock_client_cls):
        """Sem prender o início do chamado no contexto, as últimas 30 linhas
        de um grupo movimentado são só o desfecho — e a resolução tem que
        dizer o que o cliente relatou."""
        from atendimento.tasks import fechar_chamado_ia
        for i in range(40):
            Message.objects.create(
                conversation=self.conversation, sender_type='customer',
                content=f'mensagem de enchimento {i}', external_id=f'ench-{i}')

        fechar_chamado_ia(str(self.conversation.id), 'pode fechar o chamado')

        _args, kwargs = mock_call_ai.call_args
        self.assertIn('O link caiu de novo aqui', kwargs['user_prompt'])
        self.assertIn('(…)', kwargs['user_prompt'])

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

    def test_usuario_do_portal_ve_os_chamados_do_proprio_cliente(self):
        # O cliente valida os próprios chamados por esta tela — não é
        # staff-only.
        portal = _criar_usuario_portal()
        self.cliente.usuario = portal
        self.cliente.save(update_fields=['usuario'])
        self.client.force_login(portal)

        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['total'], 1)

    def test_usuario_sem_vinculo_com_o_cliente_recebe_403(self):
        estranho = _criar_usuario_portal('de_fora')
        self.client.force_login(estranho)

        self.assertEqual(self.client.get(self.url).status_code, 403)


class ChamadoDetalheDoClienteAPITest(TestCase):
    """Conversa do chamado aberta no modal da página do cliente (sem sair do
    CRM)."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = _criar_agente_staff('davi')
        self.cliente = _criar_cliente_teste('Cliente Detalhe')
        self.conversation.cliente = self.cliente
        self.conversation.resolution = 'SFP trocado'
        self.conversation.save(update_fields=['cliente', 'resolution'])
        Message.objects.create(
            conversation=self.conversation, sender_type='customer',
            content='o link caiu', external_id='det-1')
        Message.objects.create(
            conversation=self.conversation, sender_type='internal',
            content='cliente devendo, não fazer visita', is_internal=True,
            external_id='det-2')
        self.url = reverse('atendimento:api_cliente_conversation_detail',
                           args=[self.cliente.id, self.conversation.id])

    def test_staff_ve_a_conversa_inteira_com_nota_interna(self):
        self.client.force_login(self.agent)

        data = self.client.get(self.url).json()

        self.assertEqual(data['chamado']['resolucao'], 'SFP trocado')
        conteudos = [m['content'] for m in data['mensagens']]
        self.assertIn('o link caiu', conteudos)
        self.assertIn('cliente devendo, não fazer visita', conteudos)

    def test_cliente_do_portal_nao_ve_nota_interna(self):
        portal = _criar_usuario_portal('portal_det')
        self.cliente.usuario = portal
        self.cliente.save(update_fields=['usuario'])
        self.client.force_login(portal)

        data = self.client.get(self.url).json()

        conteudos = [m['content'] for m in data['mensagens']]
        self.assertIn('o link caiu', conteudos)
        self.assertNotIn('cliente devendo, não fazer visita', conteudos)

    def test_chamado_de_outro_cliente_nao_abre_por_esta_rota(self):
        outro = _criar_cliente_teste('Outro Cliente')
        self.client.force_login(self.agent)
        url = reverse('atendimento:api_cliente_conversation_detail',
                      args=[outro.id, self.conversation.id])

        self.assertEqual(self.client.get(url).status_code, 404)


class ChamadosDoClienteFiltrosTest(TestCase):
    """Filtros do modal "Listar Chamados" — rodam no servidor para valer pro
    histórico inteiro, não só pelos chamados já carregados na tela."""

    def setUp(self):
        from datetime import timedelta as _td
        self.agent = _criar_agente_staff('elis')
        self.outro = _criar_agente_staff('fabio')
        self.cliente = _criar_cliente_teste('Cliente Filtros')

        self.antigo = _criar_conversa()          # aberto há 40 dias, encerrado
        self.antigo.cliente = self.cliente
        self.antigo.status = 'resolved'
        self.antigo.assigned_to = self.agent
        self.antigo.resolution = 'Trocado o cabo de fibra'
        self.antigo.closed_at = timezone.now() - _td(days=39)
        self.antigo.save()
        Conversation.objects.filter(pk=self.antigo.pk).update(
            created_at=timezone.now() - _td(days=40))

        self.recente = _criar_conversa()         # aberto hoje, em andamento
        self.recente.cliente = self.cliente
        self.recente.status = 'open'
        self.recente.assigned_to = self.outro
        self.recente.save()

        self.url = reverse('atendimento:api_cliente_conversations', args=[self.cliente.id])
        self.client.force_login(self.agent)

    def _protocolos(self, **params):
        data = self.client.get(self.url, params).json()
        return [c['protocolo'] for c in data['chamados']], data

    def test_periodo_por_data_de_abertura(self):
        hoje = timezone.localdate().isoformat()

        protocolos, data = self._protocolos(date_from=hoje, date_to=hoje)

        self.assertEqual(protocolos, [f'#{self.recente.conversation_id}'])
        self.assertEqual(data['total'], 1)

    def test_periodo_por_data_de_encerramento_pega_outro_conjunto(self):
        # Mesma janela, campo de data diferente: o chamado aberto hoje não foi
        # encerrado, então some — é o motivo de `date_field` existir.
        hoje = timezone.localdate().isoformat()

        protocolos, _ = self._protocolos(date_field='fechado', date_from=hoje, date_to=hoje)

        self.assertEqual(protocolos, [])

    def test_filtro_por_status_agrupado(self):
        abertos, _ = self._protocolos(status='abertos')
        encerrados, _ = self._protocolos(status='encerrados')

        self.assertEqual(abertos, [f'#{self.recente.conversation_id}'])
        self.assertEqual(encerrados, [f'#{self.antigo.conversation_id}'])

    def test_filtro_por_responsavel(self):
        protocolos, _ = self._protocolos(agente=str(self.agent.id))

        self.assertEqual(protocolos, [f'#{self.antigo.conversation_id}'])

    def test_busca_por_protocolo_com_prefixo(self):
        # Na tela o protocolo aparece como "#123" — buscar exatamente o que
        # está escrito precisa funcionar.
        protocolos, _ = self._protocolos(q=f'#{self.antigo.conversation_id}')

        self.assertEqual(protocolos, [f'#{self.antigo.conversation_id}'])

    def test_busca_pelo_texto_da_resolucao(self):
        protocolos, _ = self._protocolos(q='cabo de fibra')

        self.assertEqual(protocolos, [f'#{self.antigo.conversation_id}'])

    def test_resumo_acompanha_o_filtro(self):
        _, tudo = self._protocolos()
        _, so_abertos = self._protocolos(status='abertos')

        self.assertEqual(tudo['resumo']['total'], 2)
        self.assertEqual(tudo['resumo']['abertos'], 1)
        self.assertEqual(tudo['resumo']['encerrados'], 1)
        self.assertTrue(tudo['resumo']['tempo_medio'])   # 1 dia entre abrir e fechar
        self.assertEqual(so_abertos['resumo']['total'], 1)
        self.assertEqual(so_abertos['resumo']['tempo_medio'], '')

    def test_opcoes_trazem_somente_os_responsaveis_do_cliente(self):
        _, data = self._protocolos()

        nomes = {a['nome'] for a in data['opcoes']['agentes']}
        self.assertEqual(nomes, {self.agent.username, self.outro.username})


class MencaoNoChatTest(TestCase):
    """Marcar participante do grupo com "@" no chat: o CRM guarda o nome, o
    WhatsApp recebe o número (é o que destaca a menção e notifica)."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = _criar_agente_staff('gil')

    def test_aplicar_mencoes_troca_nome_pelo_numero(self):
        from atendimento.services import aplicar_mencoes

        texto, numeros = aplicar_mencoes(
            '@João Silva, confere aí?', [{'nome': 'João Silva', 'phone': '5511999998888'}])

        self.assertEqual(texto, '@5511999998888, confere aí?')
        self.assertEqual(numeros, ['5511999998888'])

    def test_nome_mais_longo_primeiro(self):
        # Com "João" e "João Silva" no mesmo grupo, trocar o curto antes
        # deixaria "@5511... Silva" no meio da frase.
        from atendimento.services import aplicar_mencoes

        texto, numeros = aplicar_mencoes(
            'oi @João Silva e @João',
            [{'nome': 'João', 'phone': '5511111111111'},
             {'nome': 'João Silva', 'phone': '5522222222222'}])

        self.assertEqual(texto, 'oi @5522222222222 e @5511111111111')
        self.assertEqual(set(numeros), {'5511111111111', '5522222222222'})

    def test_sem_mencoes_texto_intacto(self):
        from atendimento.services import aplicar_mencoes

        texto, numeros = aplicar_mencoes('mensagem normal', [])

        self.assertEqual(texto, 'mensagem normal')
        self.assertEqual(numeros, [])

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_envio_manda_numero_ao_whatsapp_e_guarda_nome_no_crm(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'wamid-men-1')

        ok, msg_id = ConversationService.send_message(
            self.conversation, '@João Silva pode confirmar?', self.agent,
            mentions=[{'nome': 'João Silva', 'phone': '5511999998888'}])

        self.assertTrue(ok)
        # No CRM a mensagem continua legível, com o nome.
        self.assertEqual(Message.objects.get(id=msg_id).content, '@João Silva pode confirmar?')
        args, kwargs = mock_client_cls.return_value.send_text.call_args
        self.assertIn('@5511999998888', args[1])
        self.assertEqual(kwargs['mentions'], ['5511999998888'])

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_nota_interna_com_mencao_nao_vai_ao_whatsapp(self, mock_client_cls):
        ConversationService.send_message(
            self.conversation, '@João Silva olha isso', self.agent, is_internal=True,
            mentions=[{'nome': 'João Silva', 'phone': '5511999998888'}])

        mock_client_cls.return_value.send_text.assert_not_called()

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_api_send_message_repassa_as_mencoes(self, mock_client_cls):
        mock_client_cls.return_value.send_text.return_value = (True, 'wamid-men-2')
        self.client.force_login(self.agent)
        url = reverse('atendimento:api_send_message', args=[self.conversation.id])

        resp = self.client.post(url, json.dumps({
            'message': 'bom dia @Maria',
            'mentions': [{'nome': 'Maria', 'phone': '5511777776666'}],
        }), content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        _args, kwargs = mock_client_cls.return_value.send_text.call_args
        self.assertEqual(kwargs['mentions'], ['5511777776666'])

    # A view importa EvolutionAPIClient direto no módulo (`from .services
    # import ...`), então o mock precisa apontar pra `atendimento.views`.
    @mock.patch('atendimento.views.EvolutionAPIClient')
    def test_api_participantes_lista_o_grupo(self, mock_client_cls):
        from django.core.cache import cache
        cache.clear()
        mock_client_cls.return_value.get_group_participants_info.return_value = [
            {'phone': '5511999998888', 'nome': 'João Silva', 'admin': True},
        ]
        self.client.force_login(self.agent)
        url = reverse('atendimento:api_conversation_participants', args=[self.conversation.id])

        data = self.client.get(url).json()

        self.assertEqual(data['participantes'][0]['nome'], 'João Silva')
        # Segunda chamada sai do cache — não bate de novo na Evolution.
        self.client.get(url)
        self.assertEqual(mock_client_cls.return_value.get_group_participants_info.call_count, 1)


class NomeDosParticipantesTest(TestCase):
    """A Evolution devolve `name` nulo para quase todo participante de grupo,
    e a lista do "@" virava um monte de número solto. Aqui garantimos que as
    três fontes de nome (contatos da instância, nomes aprendidos das
    mensagens e a própria equipe) entram no lugar certo."""

    def setUp(self):
        self.conversation = _criar_conversa()
        self.connection = self.conversation.group.connection

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_contato_da_instancia_preenche_o_nome(self, mock_client_cls):
        from django.core.cache import cache
        from atendimento.services import completar_nomes_participantes
        cache.clear()
        mock_client_cls.return_value.get_contacts_map.return_value = {
            '44809544802320@lid': 'Sara Campelo',
        }

        [p] = completar_nomes_participantes(self.connection, [
            {'phone': '557499255512', 'lid': '44809544802320@lid', 'nome': '', 'admin': False},
        ])

        self.assertEqual(p['nome'], 'Sara Campelo')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_nome_aprendido_das_mensagens_do_grupo(self, mock_client_cls):
        from django.core.cache import cache
        from atendimento.models import GroupMemberName
        from atendimento.services import completar_nomes_participantes
        cache.clear()
        mock_client_cls.return_value.get_contacts_map.return_value = {}
        GroupMemberName.objects.create(
            connection=self.connection, jid='13486750957720@lid', name='Humberto Gusmão')

        [p] = completar_nomes_participantes(self.connection, [
            {'phone': '5527988887777', 'lid': '13486750957720@lid', 'nome': '', 'admin': False},
        ])

        self.assertEqual(p['nome'], 'Humberto Gusmão')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_atendente_da_equipe_vira_nome_do_usuario(self, mock_client_cls):
        from django.core.cache import cache
        from atendimento.models import AttendantContact
        from atendimento.services import completar_nomes_participantes
        cache.clear()
        mock_client_cls.return_value.get_contacts_map.return_value = {}
        agente = _criar_agente_staff('lucas')
        agente.first_name, agente.last_name = 'Lucas', 'Campelo'
        agente.save()
        AttendantContact.objects.create(user=agente, phone='557488737970')

        [p] = completar_nomes_participantes(self.connection, [
            {'phone': '557488737970', 'lid': '', 'nome': '', 'admin': False},
        ])

        self.assertEqual(p['nome'], 'Lucas Campelo')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_sem_nome_em_lugar_nenhum_fica_vazio(self, mock_client_cls):
        # Vazio de propósito: a tela mostra o número formatado. Repetir o
        # telefone no lugar do nome era justamente o que confundia.
        from django.core.cache import cache
        from atendimento.services import completar_nomes_participantes
        cache.clear()
        mock_client_cls.return_value.get_contacts_map.return_value = {}

        [p] = completar_nomes_participantes(self.connection, [
            {'phone': '553291594943', 'lid': '99@lid', 'nome': '', 'admin': False},
        ])

        self.assertEqual(p['nome'], '')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_nome_que_ja_veio_da_evolution_nao_e_sobrescrito(self, mock_client_cls):
        from django.core.cache import cache
        from atendimento.services import completar_nomes_participantes
        cache.clear()
        mock_client_cls.return_value.get_contacts_map.return_value = {'1@lid': 'Nome Errado'}

        [p] = completar_nomes_participantes(self.connection, [
            {'phone': '5511999998888', 'lid': '1@lid', 'nome': 'Nome Certo', 'admin': False},
        ])

        self.assertEqual(p['nome'], 'Nome Certo')
        # Nem chega a consultar a agenda quando todo mundo já tem nome.
        mock_client_cls.return_value.get_contacts_map.assert_not_called()

    def test_webhook_aprende_o_nome_de_quem_escreve(self):
        from django.core.cache import cache
        from atendimento.models import GroupMemberName
        from atendimento.services import aprender_nome_participante
        cache.clear()

        aprender_nome_participante(self.connection, '44809544802320@lid', 'Sara Campelo')
        aprender_nome_participante(self.connection, '44809544802320@lid', 'Sara Campelo')

        self.assertEqual(
            GroupMemberName.objects.filter(connection=self.connection).count(), 1)
        self.assertEqual(
            GroupMemberName.objects.get(jid='44809544802320@lid').name, 'Sara Campelo')

    def test_webhook_atualiza_nome_quando_a_pessoa_troca(self):
        from django.core.cache import cache
        from atendimento.models import GroupMemberName
        from atendimento.services import aprender_nome_participante
        cache.clear()

        aprender_nome_participante(self.connection, '77@lid', 'Zé')
        aprender_nome_participante(self.connection, '77@lid', 'José da Silva')

        self.assertEqual(GroupMemberName.objects.get(jid='77@lid').name, 'José da Silva')

    def test_sem_push_name_nao_grava_nada(self):
        from atendimento.models import GroupMemberName
        from atendimento.services import aprender_nome_participante

        aprender_nome_participante(self.connection, '77@lid', '')

        self.assertFalse(GroupMemberName.objects.exists())


class EditarMensagemTest(TestCase):
    """Editar mensagem já enviada precisa mudar os dois lados: o balão do CRM
    e a mensagem no WhatsApp. Se um dos dois não mudar, a tela passa a mentir
    sobre o que o cliente leu."""

    def setUp(self):
        from clientes.models import Cliente
        from usuario.models import Instancia, PerfilUsuario
        self.conversation = _criar_conversa()
        self.agent = _criar_agente_staff('bia')
        self.outro = _criar_agente_staff('caio')
        # Sem PerfilUsuario, `perms.get_role` trata qualquer is_staff como
        # "admin legado" — e o teste de "mensagem de outro atendente" passaria
        # à toa. Estes dois são operadores da instância principal, como um
        # atendente de verdade (o Atendimento é exclusivo dela).
        principal = Instancia.objects.create(nome='Principal', principal=True)
        for u in (self.agent, self.outro):
            PerfilUsuario.objects.create(
                usuario=u, role=PerfilUsuario.ROLE_OPERADOR, instancia=principal)
        # Operador só enxerga conversa ligada a um Cliente da instância dele
        # (atendimento/scope.py); sem isso a API responderia 403 por escopo e
        # o teste não chegaria a exercitar a edição.
        cliente = Cliente.objects.create(
            nome_empresa='Cliente Teste Edicao', cnpj='11.222.333/0001-99',
            endereco='Rua X', email='edicao@example.com', instancia=principal,
        )
        self.conversation.group.cliente = cliente
        self.conversation.group.save(update_fields=['cliente'])

    def _msg(self, **kw):
        campos = dict(
            conversation=self.conversation, sender_type='agent', sender=self.agent,
            sender_name='Bia', message_type='text', content='texto original',
            external_id='wamid.ORIGINAL', is_internal=False,
        )
        campos.update(kw)
        return Message.objects.create(**campos)

    # ── Quem pode editar o quê ──────────────────────────────────────────

    def test_mensagem_do_cliente_nao_pode_ser_editada(self):
        msg = self._msg(sender_type='customer', sender=None)

        pode, motivo = ConversationService.pode_editar(msg, self.agent)

        self.assertFalse(pode)
        self.assertIn('cliente', motivo)

    def test_mensagem_de_outro_atendente_nao_pode_ser_editada(self):
        msg = self._msg()

        pode, motivo = ConversationService.pode_editar(msg, self.outro)

        self.assertFalse(pode)
        self.assertIn('outro atendente', motivo)

    def test_admin_edita_mensagem_de_outro(self):
        from unittest import mock as _mock
        msg = self._msg()

        with _mock.patch('usuario.perms.is_admin', return_value=True):
            pode, _motivo = ConversationService.pode_editar(msg, self.outro)

        self.assertTrue(pode)

    def test_mensagem_automatica_sem_autor_nao_pode_ser_editada(self):
        # IA e fluxo escrevem sem `sender`; editar o texto no CRM só criaria
        # divergência com o que de fato saiu.
        msg = self._msg(sender=None, sender_type='internal', is_internal=True)

        pode, motivo = ConversationService.pode_editar(msg, self.agent)

        self.assertFalse(pode)
        self.assertIn('automática', motivo)

    def test_audio_nao_pode_ser_editado(self):
        msg = self._msg(message_type='audio')

        pode, motivo = ConversationService.pode_editar(msg, self.agent)

        self.assertFalse(pode)
        self.assertIn('texto', motivo)

    def test_fora_da_janela_de_15_min_nao_pode(self):
        msg = self._msg()
        Message.objects.filter(id=msg.id).update(
            created_at=timezone.now() - timedelta(minutes=16))
        msg.refresh_from_db()

        pode, motivo = ConversationService.pode_editar(msg, self.agent)

        self.assertFalse(pode)
        self.assertIn('15 minutos', motivo)

    def test_nota_interna_nao_tem_prazo(self):
        # Nunca saiu do CRM: não há mensagem no WhatsApp para o prazo valer.
        msg = self._msg(sender_type='internal', is_internal=True, external_id='local_nota_1')
        Message.objects.filter(id=msg.id).update(
            created_at=timezone.now() - timedelta(days=3))
        msg.refresh_from_db()

        pode, _motivo = ConversationService.pode_editar(msg, self.agent)

        self.assertTrue(pode)

    def test_sem_id_do_whatsapp_ainda_nao_pode(self):
        # Ainda no ar: o envio em background não confirmou o wamid.
        msg = self._msg(external_id='sending_123_abc')

        pode, motivo = ConversationService.pode_editar(msg, self.agent)

        self.assertFalse(pode)
        self.assertIn('confirmada', motivo)

    # ── O que chega ao WhatsApp ─────────────────────────────────────────

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_edicao_vai_ao_whatsapp_com_a_assinatura_original(self, mock_client_cls):
        # O grupo vê "*Bia*" em todas as mensagens; a editada não pode
        # aparecer sem a assinatura nem com o nome de quem editou.
        mock_client_cls.return_value.edit_text.return_value = (True, '')
        msg = self._msg()

        ok, _r = ConversationService.edit_message(msg, 'texto corrigido', self.agent)

        self.assertTrue(ok)
        args, kwargs = mock_client_cls.return_value.edit_text.call_args
        self.assertEqual(args[1], 'wamid.ORIGINAL')
        self.assertEqual(args[2], '*Bia*\n\ntexto corrigido')
        msg.refresh_from_db()
        self.assertEqual(msg.content, 'texto corrigido')
        self.assertIsNotNone(msg.edited_at)

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_mencao_na_edicao_vira_numero_no_texto(self, mock_client_cls):
        # O corpo que vai pro grupo troca o nome pelo número, igual ao envio,
        # para a mensagem editada ficar igual às outras. O que NÃO vai é o
        # `mentioned`: `updateMessage` não aceita esse campo (o controller da
        # Evolution ignora), então editar não notifica ninguém.
        mock_client_cls.return_value.edit_text.return_value = (True, '')
        msg = self._msg()

        ConversationService.edit_message(
            msg, '@João Silva confere', self.agent,
            mentions=[{'nome': 'João Silva', 'phone': '5511999998888'}])

        args, kwargs = mock_client_cls.return_value.edit_text.call_args
        self.assertIn('@5511999998888', args[2])
        self.assertNotIn('mentions', kwargs)
        # No CRM a mensagem continua legível, com o nome.
        msg.refresh_from_db()
        self.assertEqual(msg.content, '@João Silva confere')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_whatsapp_recusando_nao_muda_o_crm(self, mock_client_cls):
        # O ponto do recurso: se a edição não pegou lá, o balão daqui NÃO pode
        # mostrar um texto que o cliente nunca viu.
        mock_client_cls.return_value.edit_text.return_value = (False, 'Message not compatible')
        msg = self._msg()

        ok, erro = ConversationService.edit_message(msg, 'texto corrigido', self.agent)

        self.assertFalse(ok)
        self.assertIn('Message not compatible', erro)
        msg.refresh_from_db()
        self.assertEqual(msg.content, 'texto original')
        self.assertIsNone(msg.edited_at)

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_nota_interna_edita_sem_falar_com_o_whatsapp(self, mock_client_cls):
        msg = self._msg(sender_type='internal', is_internal=True, external_id='local_nota_2')

        ok, _r = ConversationService.edit_message(msg, 'nota corrigida', self.agent)

        self.assertTrue(ok)
        mock_client_cls.return_value.edit_text.assert_not_called()
        msg.refresh_from_db()
        self.assertEqual(msg.content, 'nota corrigida')

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_texto_igual_nao_chama_o_whatsapp(self, mock_client_cls):
        msg = self._msg()

        ok, _r = ConversationService.edit_message(msg, 'texto original', self.agent)

        self.assertTrue(ok)
        mock_client_cls.return_value.edit_text.assert_not_called()

    def test_lapis_continua_depois_do_prazo(self):
        # A tela mostra o lápis mesmo fora da janela (`ignorar_prazo=True`) e
        # o clique explica o motivo. Some o botão e o atendente fica sem saber
        # se o recurso existe, se quebrou ou se ele fez algo errado.
        msg = self._msg()
        Message.objects.filter(id=msg.id).update(
            created_at=timezone.now() - timedelta(minutes=40))
        msg.refresh_from_db()

        pode_agora, motivo = ConversationService.pode_editar(msg, self.agent)
        mostra_lapis, _ = ConversationService.pode_editar(msg, self.agent, ignorar_prazo=True)

        self.assertFalse(pode_agora)
        self.assertIn('15 minutos', motivo)
        self.assertTrue(mostra_lapis)

    def test_ignorar_prazo_nao_afrouxa_as_outras_regras(self):
        # `ignorar_prazo` é só sobre tempo: mensagem de outro atendente, mídia
        # e mensagem do cliente continuam fora, senão a tela ofereceria um
        # lápis que a API vai recusar.
        de_outro = self._msg(external_id='wamid.A')
        audio = self._msg(external_id='wamid.B', message_type='audio')
        do_cliente = self._msg(external_id='wamid.C', sender_type='customer', sender=None)

        self.assertFalse(ConversationService.pode_editar(de_outro, self.outro, ignorar_prazo=True)[0])
        self.assertFalse(ConversationService.pode_editar(audio, self.agent, ignorar_prazo=True)[0])
        self.assertFalse(ConversationService.pode_editar(do_cliente, self.agent, ignorar_prazo=True)[0])

    def test_api_continua_barrando_fora_do_prazo(self):
        # O lápis aparecer não pode virar edição de verdade: quem manda é a API.
        msg = self._msg()
        Message.objects.filter(id=msg.id).update(
            created_at=timezone.now() - timedelta(minutes=40))
        self.client.force_login(self.agent)
        url = reverse('atendimento:api_edit_message', args=[msg.id])

        resp = self.client.post(url, json.dumps({'content': 'tarde demais'}),
                                content_type='application/json')

        self.assertEqual(resp.status_code, 403)
        self.assertIn('15 minutos', resp.json()['error'])
        msg.refresh_from_db()
        self.assertEqual(msg.content, 'texto original')

    # ── Edição vinda do WhatsApp (cliente editou no celular) ────────────

    def _webhook_edicao(self, alvo_id, texto):
        return {
            'event': 'MESSAGES_UPSERT',
            'instance': self.conversation.group.connection.instance_name,
            'data': {
                'key': {'remoteJid': '120363000000000000@g.us', 'fromMe': False, 'id': 'wamid.EDIT'},
                'pushName': 'Cliente',
                'message': {
                    'protocolMessage': {
                        'key': {'id': alvo_id},
                        'type': 'MESSAGE_EDIT',
                        'editedMessage': {'conversation': texto},
                    }
                },
            },
        }

    def test_cliente_editando_no_celular_atualiza_o_balao(self):
        grupo = self.conversation.group
        grupo.jid = '120363000000000000@g.us'
        grupo.save(update_fields=['jid'])
        msg = self._msg(sender_type='customer', sender=None, sender_name='Cliente',
                        external_id='wamid.DOCLIENTE', content='mensagem com erro')

        ConversationService.process_webhook(self._webhook_edicao('wamid.DOCLIENTE', 'mensagem certa'))

        msg.refresh_from_db()
        self.assertEqual(msg.content, 'mensagem certa')
        self.assertIsNotNone(msg.edited_at)

    def test_edicao_recebida_nao_cria_balao_novo(self):
        # Era o estrago antigo das reações: evento sem texto reconhecível
        # virava um balão "[sem conteúdo]" no meio da conversa.
        grupo = self.conversation.group
        grupo.jid = '120363000000000000@g.us'
        grupo.save(update_fields=['jid'])
        self._msg(sender_type='customer', sender=None, external_id='wamid.DOCLIENTE',
                  content='mensagem com erro')
        antes = Message.objects.filter(conversation=self.conversation).count()

        ConversationService.process_webhook(self._webhook_edicao('wamid.DOCLIENTE', 'mensagem certa'))

        self.assertEqual(Message.objects.filter(conversation=self.conversation).count(), antes)

    def test_edicao_de_mensagem_desconhecida_e_ignorada(self):
        grupo = self.conversation.group
        grupo.jid = '120363000000000000@g.us'
        grupo.save(update_fields=['jid'])
        antes = Message.objects.count()

        r = ConversationService.process_webhook(self._webhook_edicao('wamid.NUNCAVISTA', 'oi'))

        self.assertTrue(r['success'])
        self.assertEqual(Message.objects.count(), antes)

    # ── API ─────────────────────────────────────────────────────────────

    @mock.patch('atendimento.services.EvolutionAPIClient')
    def test_api_edita_e_devolve_o_texto_novo(self, mock_client_cls):
        mock_client_cls.return_value.edit_text.return_value = (True, '')
        msg = self._msg()
        self.client.force_login(self.agent)
        url = reverse('atendimento:api_edit_message', args=[msg.id])

        resp = self.client.post(url, json.dumps({'content': 'agora sim'}),
                                content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['content'], 'agora sim')
        msg.refresh_from_db()
        self.assertEqual(msg.content, 'agora sim')

    def test_api_recusa_mensagem_de_outro_atendente(self):
        msg = self._msg()
        self.client.force_login(self.outro)
        url = reverse('atendimento:api_edit_message', args=[msg.id])

        resp = self.client.post(url, json.dumps({'content': 'não é minha'}),
                                content_type='application/json')

        self.assertEqual(resp.status_code, 403)
        msg.refresh_from_db()
        self.assertEqual(msg.content, 'texto original')

    def test_api_recusa_texto_vazio(self):
        msg = self._msg()
        self.client.force_login(self.agent)
        url = reverse('atendimento:api_edit_message', args=[msg.id])

        resp = self.client.post(url, json.dumps({'content': '   '}),
                                content_type='application/json')

        self.assertEqual(resp.status_code, 400)
        msg.refresh_from_db()
        self.assertEqual(msg.content, 'texto original')


def _dar_2fa(user):
    """`Forcar2FAMiddleware` redireciona quem não tem TOTP confirmado — sem
    isso todo request do teste vira 302 e as asserções passariam à toa
    (corpo vazio "não contém" o dado alheio)."""
    from usuario.models import TOTPDevice
    TOTPDevice.objects.create(usuario=user, secret='A' * 32, confirmado=True)
    return user


class AtendimentoExclusivoDaPrincipalTest(TestCase):
    """O módulo de Atendimento é exclusivo da instância principal (a operação
    própria do Administrador). Consultor/Operador de revenda não entram — nem
    tela, nem API, nem WebSocket.

    Regressão de duas falhas: (1) `staff_required` checava `is_staff`, que
    passou a ser True também para Consultor/Operador, abrindo o módulo para
    todas as revendas; (2) as APIs de kanban e `api_tags_list` não tinham
    nem o gate do módulo — `api_tags_list` não tinha decorator nenhum.
    """

    def setUp(self):
        from usuario.models import Instancia, PerfilUsuario

        self.principal = Instancia.objects.create(nome='Principal', principal=True)
        self.revenda = Instancia.objects.create(nome='Revenda X')

        def cria(username, role, instancia):
            u = User.objects.create_user(
                username=username, email=f'{username}@example.com', password='x',
                is_staff=True, is_active=True,
            )
            PerfilUsuario.objects.create(usuario=u, role=role, instancia=instancia)
            _dar_2fa(u)
            return u

        self.consultor = cria('consultor_revenda', PerfilUsuario.ROLE_CONSULTOR, self.revenda)
        self.operador_revenda = cria('operador_revenda', PerfilUsuario.ROLE_OPERADOR, self.revenda)
        self.operador_principal = cria('operador_principal', PerfilUsuario.ROLE_OPERADOR, self.principal)
        self.admin = _dar_2fa(User.objects.create_user(
            username='admin_plataforma', email='ap@example.com', password='x',
            is_staff=True, is_superuser=True, is_active=True,
        ))
        self.portal = _dar_2fa(User.objects.create_user(
            username='login_portal', email='lp@example.com', password='x',
            is_staff=False, is_active=True,
        ))

    ROTAS = [
        'atendimento:dashboard', 'atendimento:inbox', 'atendimento:grupos',
        'atendimento:empresas', 'atendimento:historico', 'atendimento:relatorios',
        'atendimento:kanban', 'atendimento:tarefas', 'atendimento:auto_atendimento',
        'atendimento:sala_virtual',
    ]

    def test_consultor_de_revenda_nao_abre_nenhuma_tela(self):
        self.client.force_login(self.consultor)
        for nome in self.ROTAS:
            with self.subTest(rota=nome):
                r = self.client.get(reverse(nome))
                self.assertEqual(r.status_code, 302)
                self.assertIn('instancia', r['Location'])

    def test_operador_de_revenda_tambem_nao_entra(self):
        self.client.force_login(self.operador_revenda)
        r = self.client.get(reverse('atendimento:inbox'))
        self.assertEqual(r.status_code, 302)

    def test_operador_da_principal_entra(self):
        self.client.force_login(self.operador_principal)
        for nome in self.ROTAS:
            with self.subTest(rota=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

    def test_administrador_entra(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('atendimento:dashboard')).status_code, 200)

    def test_apis_de_kanban_e_tags_exigem_o_modulo(self):
        # Eram `@login_required` (kanban) e sem decorator nenhum (tags).
        self.client.force_login(self.consultor)
        self.assertEqual(self.client.get(reverse('atendimento:api_kanban_boards')).status_code, 302)
        self.assertEqual(self.client.get(reverse('atendimento:api_tags_list')).status_code, 302)

        self.client.force_login(self.operador_principal)
        self.assertEqual(self.client.get(reverse('atendimento:api_kanban_boards')).status_code, 200)
        self.assertEqual(self.client.get(reverse('atendimento:api_tags_list')).status_code, 200)

    def test_login_de_portal_nao_entra(self):
        self.client.force_login(self.portal)
        self.assertNotEqual(self.client.get(reverse('atendimento:inbox')).status_code, 200)

    def test_websocket_do_inbox_usa_a_mesma_regra_da_porta_http(self):
        # Os consumers chamam `perms.pode_acessar_atendimento` (envelopado em
        # `_pode_atendimento` só para sair do contexto async). Antes checavam
        # apenas `is_authenticated`: qualquer conta logada assinava
        # `atendimento_inbox` e recebia toda mensagem em tempo real.
        from atendimento import consumers
        from usuario.perms import pode_acessar_atendimento

        self.assertTrue(hasattr(consumers, '_pode_atendimento'))
        self.assertTrue(pode_acessar_atendimento(self.admin))
        self.assertTrue(pode_acessar_atendimento(self.operador_principal))
        self.assertFalse(pode_acessar_atendimento(self.consultor))
        self.assertFalse(pode_acessar_atendimento(self.operador_revenda))
        self.assertFalse(pode_acessar_atendimento(self.portal))

    def test_sem_instancia_principal_cadastrada_so_o_administrador_entra(self):
        # Banco sem `principal=True` (instalação nova): o módulo não pode
        # cair aberto pra revenda por falta de configuração.
        self.principal.principal = False
        self.principal.save(update_fields=['principal'])

        self.client.force_login(self.operador_principal)
        self.assertEqual(self.client.get(reverse('atendimento:inbox')).status_code, 302)

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('atendimento:inbox')).status_code, 200)


class EscopoDeDadosNoAtendimentoTest(TestCase):
    """Mesmo dentro do módulo (instância principal), o queryset é escopado:
    a instância principal não vê os dados de uma revenda.

    Antes desta correção o módulo listava `Cliente.objects.all()` e buscava
    conversa/grupo por id sem checar dono — o que virava vazamento assim que
    qualquer outra instância existisse.
    """

    def setUp(self):
        from usuario.models import Instancia, PerfilUsuario

        self.principal = Instancia.objects.create(nome='Principal', principal=True)
        self.revenda = Instancia.objects.create(nome='Revenda X')

        self.operador = User.objects.create_user(
            username='op_principal', email='op@example.com', password='x',
            is_staff=True, is_active=True,
        )
        PerfilUsuario.objects.create(
            usuario=self.operador, role=PerfilUsuario.ROLE_OPERADOR, instancia=self.principal,
        )
        _dar_2fa(self.operador)

        self.admin = _dar_2fa(User.objects.create_user(
            username='admin_plataforma', email='ap@example.com', password='x',
            is_staff=True, is_superuser=True, is_active=True,
        ))

        self.cliente_principal = _criar_cliente_teste('CLIENTE DA PRINCIPAL')
        self.cliente_principal.instancia = self.principal
        self.cliente_principal.save(update_fields=['instancia'])

        self.cliente_revenda = _criar_cliente_teste('CLIENTE DA REVENDA')
        self.cliente_revenda.instancia = self.revenda
        self.cliente_revenda.save(update_fields=['instancia'])

        self.conv_revenda = _criar_conversa()
        self.conv_revenda.cliente = self.cliente_revenda
        self.conv_revenda.save(update_fields=['cliente'])
        self.grupo_revenda = self.conv_revenda.group
        self.grupo_revenda.cliente = self.cliente_revenda
        self.grupo_revenda.save(update_fields=['cliente'])

    def test_grupos_nao_mostra_cliente_de_outra_instancia(self):
        self.client.force_login(self.operador)
        html = self.client.get(reverse('atendimento:grupos')).content.decode()
        self.assertNotIn('CLIENTE DA REVENDA', html)
        self.assertIn('CLIENTE DA PRINCIPAL', html)

    def test_empresas_nao_mostra_cliente_de_outra_instancia(self):
        self.client.force_login(self.operador)
        html = self.client.get(reverse('atendimento:empresas')).content.decode()
        self.assertNotIn('CLIENTE DA REVENDA', html)

    def test_historico_nao_mostra_conversa_de_outra_instancia(self):
        self.client.force_login(self.operador)
        html = self.client.get(reverse('atendimento:historico')).content.decode()
        self.assertNotIn('CLIENTE DA REVENDA', html)

    def test_abrir_conversa_de_outra_instancia_da_403(self):
        self.client.force_login(self.operador)
        r = self.client.get(
            reverse('atendimento:conversation_detail', args=[self.conv_revenda.id]))
        self.assertEqual(r.status_code, 403)

    def test_hosts_da_conversa_de_outra_instancia_da_403(self):
        self.client.force_login(self.operador)
        r = self.client.get(
            reverse('atendimento:api_conversation_hosts', args=[self.conv_revenda.id]))
        self.assertEqual(r.status_code, 403)

    def test_vincular_grupo_de_outra_instancia_da_403(self):
        self.client.force_login(self.operador)
        r = self.client.post(
            reverse('atendimento:api_link_group', args=[self.grupo_revenda.id]),
            data=json.dumps({'cliente_id': self.cliente_principal.id}),
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 403)
        self.grupo_revenda.refresh_from_db()
        self.assertEqual(self.grupo_revenda.cliente_id, self.cliente_revenda.id)

    def test_configuracoes_da_plataforma_sao_so_do_administrador(self):
        self.client.force_login(self.operador)
        self.assertEqual(
            self.client.get(reverse('atendimento:settings_connections')).status_code, 403)

        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse('atendimento:settings_connections')).status_code, 200)

    def test_administrador_continua_vendo_tudo(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse('atendimento:empresas')).content.decode()
        self.assertIn('CLIENTE DA PRINCIPAL', html)
        self.assertIn('CLIENTE DA REVENDA', html)


class IniciarConversaTempoRealTest(TestCase):
    """Chamado aberto pela plataforma ("Iniciar conversa") só aparecia em
    "Assumidos" depois de recarregar a página.

    Ele nasce já assumido por quem clicou, então não passa por
    `notify_reassignment` (nunca teve outro dono) nem pelo webhook — nada
    avisava as telas abertas. E o chamado anterior do grupo, que essa mesma
    view encerra automaticamente, continuava nas listas como item fantasma.
    """

    def setUp(self):
        self.agent = _criar_agente_staff('joana')
        self.client.force_login(self.agent)
        self.anterior = _criar_conversa()
        self.anterior.status = 'open'
        self.anterior.save(update_fields=['status'])
        self.group = self.anterior.group

    def _iniciar(self):
        return self.client.post(
            reverse('atendimento:api_start_conversation'),
            data=json.dumps({'group_id': self.group.id}),
            content_type='application/json',
        )

    @mock.patch('atendimento.services._ws_send_inbox')
    def test_avisa_a_caixa_de_entrada_do_chamado_criado(self, mock_ws):
        resp = self._iniciar()

        self.assertEqual(resp.status_code, 200)
        nova = Conversation.objects.exclude(id=self.anterior.id).get()
        eventos = [c.args[0] for c in mock_ws.call_args_list]
        criados = [e for e in eventos if e['type'] == 'conversation_created']
        self.assertEqual(len(criados), 1)
        self.assertEqual(criados[0]['conversation_id'], str(nova.id))
        self.assertEqual(criados[0]['assigned_to_id'], self.agent.id)
        self.assertEqual(criados[0]['group_name'], self.group.name)

    @mock.patch('atendimento.services._ws_send_inbox')
    def test_avisa_o_encerramento_automatico_do_chamado_anterior(self, mock_ws):
        self._iniciar()

        eventos = [c.args[0] for c in mock_ws.call_args_list]
        encerrados = [e for e in eventos
                      if e['type'] == 'conversation_status'
                      and e['conversation_id'] == str(self.anterior.id)]
        self.assertEqual(len(encerrados), 1)
        self.assertEqual(encerrados[0]['status'], 'resolved')

    @mock.patch('atendimento.services._ws_send_inbox', side_effect=RuntimeError('sem channel layer'))
    def test_falha_no_websocket_nao_derruba_a_criacao(self, _mock_ws):
        """O chamado é o que importa: WebSocket fora do ar não pode virar
        erro 400 na tela de quem clicou em "Iniciar"."""
        resp = self._iniciar()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        self.assertTrue(
            Conversation.objects.filter(
                assigned_to=self.agent, status='open').exclude(id=self.anterior.id).exists())

    def test_chamado_iniciado_entra_na_aba_assumidos(self):
        """O refresh do painel busca a lista no Inbox — o chamado precisa
        estar em `tab=mine` já na primeira consulta depois de criado."""
        self._iniciar()
        nova = Conversation.objects.exclude(id=self.anterior.id).get()

        html = self.client.get(
            reverse('atendimento:inbox') + '?tab=mine',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        ).content.decode()

        self.assertIn('data-conv-id="%s"' % nova.id, html)
        self.assertNotIn('data-conv-id="%s"' % self.anterior.id, html)


class ContatoTelefoneVisibilidadeTest(TestCase):
    """Contato de telefone (1:1) e a restrição de visibilidade por atendente.

    A regra vive na AUSÊNCIA de linhas em `UserGroupPermission`: contato sem
    ninguém marcado é aberto (cai no atendimento geral, como sempre foi);
    marcando alguém, só essas pessoas — e os administradores — veem os
    chamados dele, em qualquer tela. Estes testes existem porque a restrição
    tem que valer em TODOS os caminhos: listagem, detalhe, WebSocket e os
    avisos que saem para um grupo do WhatsApp.
    """

    def setUp(self):
        from usuario.models import Instancia, PerfilUsuario
        from clientes.models import Cliente

        self.principal = Instancia.objects.create(nome='Principal', principal=True)

        def cria(username, role):
            u = User.objects.create_user(
                username=username, email=f'{username}@example.com', password='x',
                is_staff=True, is_active=True,
            )
            PerfilUsuario.objects.create(usuario=u, role=role, instancia=self.principal)
            return _dar_2fa(u)

        self.ana = cria('ana_op', PerfilUsuario.ROLE_OPERADOR)
        self.bruno = cria('bruno_op', PerfilUsuario.ROLE_OPERADOR)
        self.admin = _dar_2fa(User.objects.create_user(
            username='admin_visib', email='av@example.com', password='x',
            is_staff=True, is_superuser=True, is_active=True,
        ))

        self.cliente = Cliente.objects.create(nome_empresa='Prefeitura', instancia=self.principal)
        self.connection = WhatsAppConnection.objects.create(
            name='Conexao Visib', evolution_url='https://evolution.example.com',
            api_key='k', instance_name='visib',
        )

    def _contato(self, jid='5534999998888@s.whatsapp.net', nome='João'):
        return ContactGroup.objects.create(
            jid=jid, connection=self.connection, name=nome,
            is_group=False, cliente=self.cliente,
        )

    def _restringir(self, group, *users):
        from atendimento.models import UserGroupPermission
        for u in users:
            UserGroupPermission.objects.create(group=group, user=u)

    # ── Modelo ────────────────────────────────────────────────────────────

    def test_telefone_para_jid_aceita_o_que_a_pessoa_digita(self):
        from atendimento.models import telefone_para_jid
        self.assertEqual(telefone_para_jid('(34) 99999-8888'), '5534999998888@s.whatsapp.net')
        self.assertEqual(telefone_para_jid('+55 34 99999-8888'), '5534999998888@s.whatsapp.net')
        self.assertEqual(telefone_para_jid('34999998888'), '5534999998888@s.whatsapp.net')
        # O "+" desliga o palpite do DDI 55 — sem isso um número dos EUA
        # virava um JID brasileiro inexistente.
        self.assertEqual(telefone_para_jid('+1 415 555 2671'), '14155552671@s.whatsapp.net')
        self.assertEqual(telefone_para_jid('123'), '')
        self.assertEqual(telefone_para_jid(''), '')

    def test_contato_sem_ninguem_marcado_e_aberto(self):
        contato = self._contato()
        self.assertFalse(contato.restrito)
        self.assertEqual(contato.atendentes_ids(), set())

    def test_marcar_alguem_torna_o_contato_restrito(self):
        contato = self._contato()
        self._restringir(contato, self.ana)
        self.assertTrue(contato.restrito)
        self.assertEqual(contato.atendentes_ids(), {self.ana.id})

    # ── Escopo ────────────────────────────────────────────────────────────

    def test_chamado_de_contato_aberto_aparece_para_toda_a_equipe(self):
        """Contato sem ninguém marcado não é afetado por nada disso."""
        from atendimento.scope import conversations_visiveis
        conv = Conversation.objects.create(group=self._contato(), cliente=self.cliente)
        for user in (self.ana, self.bruno, self.admin):
            with self.subTest(user=user.username):
                self.assertIn(conv, conversations_visiveis(user))

    def test_chamado_restrito_some_para_quem_nao_foi_marcado(self):
        from atendimento.scope import conversations_visiveis, pode_ver_conversation
        contato = self._contato()
        self._restringir(contato, self.ana)
        conv = Conversation.objects.create(group=contato, cliente=self.cliente)

        self.assertIn(conv, conversations_visiveis(self.ana))
        self.assertTrue(pode_ver_conversation(self.ana, conv))

        self.assertNotIn(conv, conversations_visiveis(self.bruno))
        self.assertFalse(pode_ver_conversation(self.bruno, conv))

    def test_administrador_nao_marcado_tambem_perde_o_chamado(self):
        """A restrição vale para TODO MUNDO. No primeiro dia o admin passava
        direto, e na prática isso não restringia nada: `perms.get_role` trata
        todo `is_staff` sem `PerfilUsuario` como "admin legado", e a maioria da
        equipe caía nisso sem ninguém ter decidido — um contato marcado para
        uma pessoa seguia visível para o escritório inteiro."""
        from atendimento.scope import conversations_visiveis, pode_ver_conversation
        contato = self._contato()
        self._restringir(contato, self.ana)
        conv = Conversation.objects.create(group=contato, cliente=self.cliente)

        self.assertNotIn(conv, conversations_visiveis(self.admin))
        self.assertFalse(pode_ver_conversation(self.admin, conv))

    def test_admin_marcado_ve_o_chamado(self):
        from atendimento.scope import conversations_visiveis, pode_ver_conversation
        contato = self._contato()
        self._restringir(contato, self.admin)
        conv = Conversation.objects.create(group=contato, cliente=self.cliente)

        self.assertIn(conv, conversations_visiveis(self.admin))
        self.assertTrue(pode_ver_conversation(self.admin, conv))
        self.assertNotIn(conv, conversations_visiveis(self.ana))

    def test_admin_ainda_enxerga_o_contato_para_poder_desfazer(self):
        """Válvula de escape: o admin perde os CHAMADOS do contato restrito,
        mas não o CONTATO em si — é pela tela de Grupos/Contatos que ele muda a
        lista. Sem isso, contato marcado para quem sai da empresa ficaria sem
        volta: ninguém veria o chamado e ninguém acharia o contato."""
        from atendimento.scope import groups_visiveis, pode_ver_group
        contato = self._contato()
        self._restringir(contato, self.ana)
        conv = Conversation.objects.create(group=contato, cliente=self.cliente)

        self.assertIn(contato, groups_visiveis(self.admin))
        self.assertTrue(pode_ver_group(self.admin, contato))

        from atendimento.scope import conversations_visiveis
        self.assertNotIn(conv, conversations_visiveis(self.admin))

        # E o caminho de volta funciona de verdade: esvaziar a lista devolve
        # o chamado para todo mundo, admin incluído.
        self.client.force_login(self.admin)
        self.client.post(
            reverse('atendimento:api_group_atendentes', args=[contato.id]),
            data=json.dumps({'atendentes': []}), content_type='application/json',
        )
        self.assertIn(conv, conversations_visiveis(self.admin))

    def test_restricao_nao_derruba_os_chamados_abertos_dos_outros_contatos(self):
        """O `Exists` negado tem que valer por contato, não por consulta: um
        contato restrito no banco não pode sumir com os chamados dos contatos
        abertos, que são a maioria."""
        from atendimento.scope import conversations_visiveis
        restrito = self._contato(jid='5534111112222@s.whatsapp.net', nome='Sigiloso')
        self._restringir(restrito, self.ana)
        Conversation.objects.create(group=restrito, cliente=self.cliente)

        aberto = self._contato(jid='5534333334444@s.whatsapp.net', nome='Comum')
        conv_aberta = Conversation.objects.create(group=aberto, cliente=self.cliente)

        visiveis = conversations_visiveis(self.bruno)
        self.assertIn(conv_aberta, visiveis)
        self.assertEqual(visiveis.count(), 1)

    def test_um_chamado_nao_duplica_com_varios_atendentes_marcados(self):
        """Join simples multiplicaria a linha do chamado por atendente
        autorizado — dois marcados fariam o chamado aparecer duas vezes."""
        from atendimento.scope import conversations_visiveis
        contato = self._contato()
        self._restringir(contato, self.ana, self.bruno)
        Conversation.objects.create(group=contato, cliente=self.cliente)

        self.assertEqual(conversations_visiveis(self.ana).count(), 1)

    def test_contato_restrito_some_da_lista_de_grupos(self):
        from atendimento.scope import groups_visiveis, pode_ver_group
        contato = self._contato()
        self._restringir(contato, self.ana)

        self.assertIn(contato, groups_visiveis(self.ana))
        self.assertTrue(pode_ver_group(self.ana, contato))
        self.assertNotIn(contato, groups_visiveis(self.bruno))
        self.assertFalse(pode_ver_group(self.bruno, contato))
        # Admin continua vendo o CONTATO (administração) — ver o teste da
        # válvula de escape acima.
        self.assertIn(contato, groups_visiveis(self.admin))

    # ── Telas ─────────────────────────────────────────────────────────────

    def test_inbox_nao_mostra_o_chamado_restrito(self):
        contato = self._contato()
        self._restringir(contato, self.ana)
        conv = Conversation.objects.create(group=contato, cliente=self.cliente, status='open')

        self.client.force_login(self.bruno)
        html = self.client.get(reverse('atendimento:inbox') + '?tab=open').content.decode()
        self.assertNotIn(str(conv.id), html)
        self.assertNotIn('João', html)

        self.client.force_login(self.ana)
        html = self.client.get(reverse('atendimento:inbox') + '?tab=open').content.decode()
        self.assertIn(str(conv.id), html)

    def test_detalhe_do_chamado_restrito_e_403_para_quem_nao_pode(self):
        contato = self._contato()
        self._restringir(contato, self.ana)
        conv = Conversation.objects.create(group=contato, cliente=self.cliente)

        self.client.force_login(self.bruno)
        r = self.client.get(reverse('atendimento:conversation_detail', args=[conv.id]))
        self.assertEqual(r.status_code, 403)

        self.client.force_login(self.ana)
        r = self.client.get(reverse('atendimento:conversation_detail', args=[conv.id]))
        self.assertEqual(r.status_code, 200)

    # ── API de criação ────────────────────────────────────────────────────

    def _criar_via_api(self, **campos):
        payload = {
            'nome': 'João da Silva',
            'telefone': '34 99999-8888',
            'connection_id': str(self.connection.id),
        }
        payload.update(campos)
        return self.client.post(
            reverse('atendimento:api_criar_contato'),
            data=json.dumps(payload), content_type='application/json',
        )

    def test_api_cria_contato_aberto_por_padrao(self):
        self.client.force_login(self.admin)
        r = self._criar_via_api()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['success'])

        contato = ContactGroup.objects.get(jid='5534999998888@s.whatsapp.net')
        self.assertFalse(contato.is_group)
        self.assertFalse(contato.restrito)
        self.assertEqual(contato.telefone, '5534999998888')

    def test_api_cria_contato_ja_restrito(self):
        self.client.force_login(self.admin)
        r = self._criar_via_api(atendentes=[self.ana.id])
        self.assertEqual(r.status_code, 200)

        contato = ContactGroup.objects.get(jid='5534999998888@s.whatsapp.net')
        self.assertEqual(contato.atendentes_ids(), {self.ana.id})

    def test_api_recusa_telefone_invalido_e_duplicado(self):
        self.client.force_login(self.admin)
        r = self._criar_via_api(telefone='123')
        self.assertEqual(r.status_code, 400)
        self.assertIn('Telefone', r.json()['error'])

        self._criar_via_api()
        r = self._criar_via_api()
        self.assertEqual(r.status_code, 400)
        self.assertIn('Já existe', r.json()['error'])

    def test_lista_vazia_devolve_o_contato_para_o_atendimento_geral(self):
        contato = self._contato()
        self._restringir(contato, self.ana)

        self.client.force_login(self.admin)
        r = self.client.post(
            reverse('atendimento:api_group_atendentes', args=[contato.id]),
            data=json.dumps({'atendentes': []}), content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['restrito'])
        self.assertFalse(contato.restrito)

    def test_operador_nao_altera_quem_atende(self):
        """É regra de visibilidade: um operador podendo se auto-remover (ou
        remover os outros) esvaziaria a restrição."""
        contato = self._contato()
        self._restringir(contato, self.ana)

        self.client.force_login(self.ana)
        r = self.client.post(
            reverse('atendimento:api_group_atendentes', args=[contato.id]),
            data=json.dumps({'atendentes': []}), content_type='application/json',
        )
        self.assertEqual(r.status_code, 403)
        self.assertTrue(contato.restrito)

    def test_id_de_nao_atendente_e_ignorado(self):
        """Um id de login de portal viraria uma restrição que ninguém
        satisfaz — o chamado sumiria para a equipe inteira."""
        portal = User.objects.create_user(username='portal_x', password='x', is_active=True)
        contato = self._contato()

        self.client.force_login(self.admin)
        self.client.post(
            reverse('atendimento:api_group_atendentes', args=[contato.id]),
            data=json.dumps({'atendentes': [portal.id]}), content_type='application/json',
        )
        self.assertFalse(contato.restrito)

    # ── Vazamentos ────────────────────────────────────────────────────────

    def test_websocket_do_inbox_carrega_a_lista_de_quem_pode_ver(self):
        """Todo mundo assina o mesmo grupo de canal, então o pacote precisa
        dizer para quem vale — senão o chamado restrito aparece na caixa de
        entrada de quem não pode vê-lo."""
        from atendimento.scope import atendentes_do_chamado
        contato = self._contato()
        conv = Conversation.objects.create(group=contato, cliente=self.cliente)
        self.assertIsNone(atendentes_do_chamado(conv))

        self._restringir(contato, self.ana)
        conv.refresh_from_db()
        self.assertEqual(atendentes_do_chamado(conv), [self.ana.id])

    def test_aviso_de_chamado_sem_atendimento_pula_contato_restrito(self):
        """A mensagem vai para um grupo do WhatsApp que a equipe inteira lê e
        carrega o NOME do contato — avisar ali vazaria o que a restrição
        existe para proteger."""
        from atendimento.tasks import notificar_chamados_abertos

        contato = self._contato(nome='Assunto Sigiloso')
        self._restringir(contato, self.ana)
        antigo = timezone.now() - timedelta(minutes=30)
        Conversation.objects.create(
            group=contato, cliente=self.cliente, status='open', last_message_at=antigo)

        SystemSetting.set('notif_abertos_enabled', 'true')
        grupo_notif = ContactGroup.objects.create(
            jid='120363000@g.us', connection=self.connection, name='NOC')
        SystemSetting.set('notif_abertos_group_id', str(grupo_notif.id))

        with mock.patch('atendimento.tasks._get_notif_client_and_jid') as m:
            cliente_fake = mock.Mock()
            cliente_fake.send_text.return_value = (True, 'id')
            m.return_value = (cliente_fake, grupo_notif.jid)
            resultado = notificar_chamados_abertos()

        self.assertEqual(resultado.get('notified', 0), 0)
        cliente_fake.send_text.assert_not_called()

    def test_webhook_ignora_privada_de_numero_nao_cadastrado(self):
        """Sem isso, qualquer pessoa que mandasse mensagem para o WhatsApp da
        empresa abriria um chamado."""
        antes = Conversation.objects.count()
        r = ConversationService.process_webhook({
            'event': 'MESSAGES_UPSERT', 'instance': 'visib',
            'data': {
                'key': {'remoteJid': '5534777776666@s.whatsapp.net', 'fromMe': False, 'id': 'M1'},
                'pushName': 'Desconhecido',
                'message': {'conversation': 'oi'},
            },
        })
        self.assertTrue(r['success'])
        self.assertEqual(Conversation.objects.count(), antes)

    def test_webhook_abre_chamado_para_contato_cadastrado(self):
        contato = self._contato()
        r = ConversationService.process_webhook({
            'event': 'MESSAGES_UPSERT', 'instance': 'visib',
            'data': {
                'key': {'remoteJid': contato.jid, 'fromMe': False, 'id': 'M2'},
                'pushName': 'João',
                'message': {'conversation': 'preciso de ajuda'},
            },
        })
        self.assertTrue(r['success'])
        conv = Conversation.objects.get(group=contato)
        self.assertEqual(conv.status, 'open')
        msg = Message.objects.get(conversation=conv)
        self.assertEqual(msg.sender_type, 'customer')
        self.assertEqual(msg.content, 'preciso de ajuda')

    def test_webhook_marca_contato_1_a_1_como_nao_grupo(self):
        """`is_group` não ia no `defaults` do get_or_create: todo contato 1:1
        nascia com o default do campo (True) e se passava por grupo."""
        ConversationService.process_webhook({
            'event': 'MESSAGES_UPSERT', 'instance': 'visib',
            'data': {
                'key': {'remoteJid': self._contato().jid, 'fromMe': False, 'id': 'M3'},
                'message': {'conversation': 'oi'},
            },
        })
        self.assertFalse(ContactGroup.objects.get(jid='5534999998888@s.whatsapp.net').is_group)


class ApagarMensagemTest(TestCase):
    """Apagar mensagem tem que valer dos dois lados: sai do WhatsApp do cliente
    e vira "Mensagem apagada" no CRM. Se só um lado mudar, ou a tela mente
    para o atendente, ou o registro do chamado mente para o supervisor.
    """

    def setUp(self):
        from clientes.models import Cliente
        from usuario.models import Instancia, PerfilUsuario
        self.conversation = _criar_conversa()
        self.agent = _criar_agente_staff('dora')
        self.outro = _criar_agente_staff('elias')
        # Sem PerfilUsuario todo is_staff vira "admin legado" e os testes de
        # "mensagem de outro atendente" passariam à toa.
        principal = Instancia.objects.create(nome='Principal', principal=True)
        for u in (self.agent, self.outro):
            PerfilUsuario.objects.create(
                usuario=u, role=PerfilUsuario.ROLE_OPERADOR, instancia=principal)
        cliente = Cliente.objects.create(
            nome_empresa='Cliente Teste Exclusao', cnpj='22.333.444/0001-88',
            endereco='Rua Y', email='exclusao@example.com', instancia=principal,
        )
        self.conversation.group.cliente = cliente
        self.conversation.group.save(update_fields=['cliente'])

    def _msg(self, **kw):
        campos = dict(
            conversation=self.conversation, sender_type='agent', sender=self.agent,
            sender_name='Dora', message_type='text', content='texto a apagar',
            external_id='wamid.APAGAR', is_internal=False,
        )
        campos.update(kw)
        return Message.objects.create(**campos)

    # ── Quem pode apagar o quê ──────────────────────────────────────────

    def test_mensagem_do_cliente_nao_pode_ser_apagada(self):
        msg = self._msg(sender_type='customer', sender=None)

        pode, motivo = ConversationService.pode_excluir(msg, self.agent)

        self.assertFalse(pode)
        self.assertIn('cliente', motivo)

    def test_mensagem_de_outro_atendente_nao_pode_ser_apagada(self):
        msg = self._msg()

        pode, motivo = ConversationService.pode_excluir(msg, self.outro)

        self.assertFalse(pode)
        self.assertIn('outro atendente', motivo)

    def test_admin_apaga_mensagem_de_outro(self):
        msg = self._msg()

        with mock.patch('usuario.perms.is_admin', return_value=True):
            pode, _motivo = ConversationService.pode_excluir(msg, self.outro)

        self.assertTrue(pode)

    def test_midia_pode_ser_apagada_mesmo_nao_podendo_ser_editada(self):
        """Diferença deliberada em relação à edição: o WhatsApp não reescreve
        mídia, mas apaga — e mandar o arquivo errado é o caso em que apagar
        mais importa."""
        msg = self._msg(message_type='document', attachment_url='/media/x.pdf')

        pode_editar, _ = ConversationService.pode_editar(msg, self.agent)
        pode_apagar, _ = ConversationService.pode_excluir(msg, self.agent)

        self.assertFalse(pode_editar)
        self.assertTrue(pode_apagar)

    def test_mensagem_automatica_so_admin_apaga(self):
        msg = self._msg(sender=None, sender_name='Tomichinho')

        pode, motivo = ConversationService.pode_excluir(msg, self.agent)
        self.assertFalse(pode)
        self.assertIn('automática', motivo)

        with mock.patch('usuario.perms.is_admin', return_value=True):
            pode_admin, _ = ConversationService.pode_excluir(msg, self.agent)
        self.assertTrue(pode_admin)

    def test_mensagem_sem_confirmacao_do_whatsapp_nao_pode_ser_apagada(self):
        msg = self._msg(external_id='sending_12345_abc')

        pode, motivo = ConversationService.pode_excluir(msg, self.agent)

        self.assertFalse(pode)
        self.assertIn('confirmada', motivo)

    def test_mensagem_ja_apagada_nao_apaga_de_novo(self):
        msg = self._msg(deleted_at=timezone.now())

        pode, motivo = ConversationService.pode_excluir(msg, self.agent)

        self.assertFalse(pode)
        self.assertIn('já foi apagada', motivo)

    # ── O que acontece ao apagar ────────────────────────────────────────

    @mock.patch.object(EvolutionAPIClient, 'delete_message', return_value=(True, ''))
    def test_apagar_chama_o_whatsapp_e_marca_no_crm(self, mock_del):
        msg = self._msg()

        ok, _ = ConversationService.delete_message(msg, self.agent)

        self.assertTrue(ok)
        msg.refresh_from_db()
        self.assertIsNotNone(msg.deleted_at)
        self.assertEqual(msg.deleted_by, self.agent)
        self.assertTrue(msg.excluida)
        mock_del.assert_called_once()
        # A key enviada é a do WhatsApp, não o id interno do CRM.
        self.assertEqual(mock_del.call_args[0][1], 'wamid.APAGAR')

    @mock.patch.object(EvolutionAPIClient, 'delete_message',
                       return_value=(False, 'Message not compatible'))
    def test_whatsapp_recusando_nao_marca_nada_no_crm(self, _mock_del):
        """O ponto todo de ser síncrono: marcar como apagada aqui enquanto o
        cliente segue com a mensagem no celular é a pior das duas telas."""
        msg = self._msg()

        ok, erro = ConversationService.delete_message(msg, self.agent)

        self.assertFalse(ok)
        self.assertIn('Message not compatible', erro)
        msg.refresh_from_db()
        self.assertIsNone(msg.deleted_at)
        self.assertFalse(msg.excluida)

    def test_nota_interna_apaga_sem_falar_com_o_whatsapp(self):
        msg = self._msg(sender_type='internal', is_internal=True,
                        external_id='local_nota_1')

        with mock.patch.object(EvolutionAPIClient, 'delete_message') as m:
            ok, _ = ConversationService.delete_message(msg, self.agent)

        self.assertTrue(ok)
        m.assert_not_called()
        msg.refresh_from_db()
        self.assertIsNotNone(msg.deleted_at)

    @mock.patch.object(EvolutionAPIClient, 'delete_message', return_value=(True, ''))
    def test_a_linha_fica_no_banco(self, _mock_del):
        """Soft delete: apagar a linha destruiria o histórico do chamado e
        liberaria o `external_id`, que é unique e é a chave lá no WhatsApp."""
        msg = self._msg()

        ConversationService.delete_message(msg, self.agent)

        self.assertTrue(Message.objects.filter(id=msg.id).exists())
        self.assertEqual(Message.objects.get(id=msg.id).content, 'texto a apagar')

    @mock.patch.object(EvolutionAPIClient, 'delete_message', return_value=(True, ''))
    def test_arquivo_de_midia_sai_do_disco(self, _mock_del):
        """Sem isso a exclusão seria de fachada: quem tivesse a URL continuaria
        baixando o documento enviado por engano."""
        import os as _os
        from django.conf import settings as _settings

        pasta = _os.path.join(_settings.MEDIA_ROOT, 'atendimento', 'media')
        _os.makedirs(pasta, exist_ok=True)
        caminho = _os.path.join(pasta, 'teste_exclusao.pdf')
        with open(caminho, 'wb') as f:
            f.write(b'conteudo')
        url = f"{_settings.MEDIA_URL}atendimento/media/teste_exclusao.pdf"

        msg = self._msg(message_type='document', attachment_url=url)
        try:
            ConversationService.delete_message(msg, self.agent)
            self.assertFalse(_os.path.exists(caminho))
        finally:
            if _os.path.exists(caminho):
                _os.remove(caminho)

    @mock.patch.object(EvolutionAPIClient, 'delete_message', return_value=(True, ''))
    def test_attachment_url_com_path_traversal_nao_apaga_fora_do_media_root(self, _mock_del):
        from django.conf import settings as _settings
        msg = self._msg(message_type='document',
                        attachment_url=f"{_settings.MEDIA_URL}../../etc/passwd")

        ok, _ = ConversationService.delete_message(msg, self.agent)

        self.assertTrue(ok)                      # a exclusão em si não falha
        self.assertTrue(os.path.exists('/etc/passwd'))

    @mock.patch.object(EvolutionAPIClient, 'delete_message', return_value=(True, ''))
    def test_atividade_registra_quem_apagou(self, _mock_del):
        from atendimento.models import ConversationActivity
        msg = self._msg()

        ConversationService.delete_message(msg, self.agent)

        act = ConversationActivity.objects.get(
            conversation=self.conversation, action='message_deleted')
        self.assertEqual(act.actor, self.agent)

    # ── API ─────────────────────────────────────────────────────────────

    @mock.patch.object(EvolutionAPIClient, 'delete_message', return_value=(True, ''))
    def test_api_apaga(self, _mock_del):
        msg = self._msg()
        self.client.force_login(self.agent)

        r = self.client.post(reverse('atendimento:api_delete_message', args=[msg.id]))

        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['success'])
        msg.refresh_from_db()
        self.assertIsNotNone(msg.deleted_at)

    def test_api_recusa_mensagem_de_outro_atendente(self):
        msg = self._msg()
        self.client.force_login(self.outro)

        r = self.client.post(reverse('atendimento:api_delete_message', args=[msg.id]))

        self.assertEqual(r.status_code, 403)
        msg.refresh_from_db()
        self.assertIsNone(msg.deleted_at)

    @mock.patch.object(EvolutionAPIClient, 'delete_message',
                       return_value=(False, 'Message not compatible'))
    def test_api_devolve_o_motivo_do_whatsapp(self, _mock_del):
        """O motivo da recusa é justamente o que o atendente precisa ler."""
        msg = self._msg()
        self.client.force_login(self.agent)

        r = self.client.post(reverse('atendimento:api_delete_message', args=[msg.id]))

        self.assertEqual(r.status_code, 400)
        self.assertIn('Message not compatible', r.json()['error'])

    # ── O apagado não pode voltar por outro caminho ─────────────────────

    @mock.patch.object(EvolutionAPIClient, 'delete_message', return_value=(True, ''))
    def test_polling_devolve_a_mensagem_sem_conteudo(self, _mock_del):
        """Fallback de quem está sem WebSocket: devolver o texto aqui
        ressuscitaria na tela o que acabou de ser apagado dos dois lados."""
        msg = self._msg(message_type='document', attachment_url='/media/x.pdf')
        ConversationService.delete_message(msg, self.agent)
        self.client.force_login(self.agent)

        r = self.client.get(
            reverse('atendimento:api_conversation_messages', args=[self.conversation.id]))

        linha = [m for m in r.json()['messages'] if m['id'] == str(msg.id)][0]
        self.assertEqual(linha['content'], '')
        self.assertEqual(linha['attachment_url'], '')
        self.assertTrue(linha['deleted'])

    @mock.patch.object(EvolutionAPIClient, 'delete_message', return_value=(True, ''))
    def test_contexto_da_ia_ignora_mensagem_apagada(self, _mock_del):
        """A IA reescreveria o conteúdo numa resolução ou num resumo,
        trazendo de volta por escrito o que se quis tirar."""
        from atendimento.tasks import _contexto_conversa
        msg = self._msg(content='segredo que foi apagado')
        Message.objects.create(
            conversation=self.conversation, sender_type='customer',
            content='mensagem que fica', external_id='wamid.FICA')
        ConversationService.delete_message(msg, self.agent)

        texto, historico = _contexto_conversa(self.conversation)

        self.assertNotIn('segredo que foi apagado', texto)
        self.assertIn('mensagem que fica', texto)
        self.assertNotIn(msg.id, [m.id for m in historico])

    @mock.patch.object(EvolutionAPIClient, 'delete_message', return_value=(True, ''))
    def test_balao_vira_o_rastro_na_tela(self, _mock_del):
        msg = self._msg(content='some daqui')
        ConversationService.delete_message(msg, self.agent)
        self.client.force_login(self.agent)

        html = self.client.get(
            reverse('atendimento:conversation_detail', args=[self.conversation.id])
        ).content.decode()

        self.assertIn('Mensagem apagada', html)
        self.assertNotIn('some daqui', html)
