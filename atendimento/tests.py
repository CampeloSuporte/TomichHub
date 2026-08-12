from unittest import mock
import base64
import json
import os
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
    connection = WhatsAppConnection.objects.create(
        name='Conexao Teste', evolution_url='https://evolution.example.com',
        api_key='fake-key', instance_name='teste',
    )
    group = ContactGroup.objects.create(
        jid='5511999999999@s.whatsapp.net', connection=connection, name='Cliente Teste',
    )
    return Conversation.objects.create(group=group)


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
        from usuario.models import TOTPDevice
        self.conversation = _criar_conversa()
        self.agent = User.objects.create_user(username='ana', password='x', is_staff=True, is_active=True)
        # Forcar2FAMiddleware redireciona qualquer staff sem TOTPDevice confirmado
        # pra tela de configuração de 2FA; sem isso o POST cai em 302 antes da view.
        TOTPDevice.objects.create(usuario=self.agent, secret='JBSWY3DPEHPK3PXP', confirmado=True)
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
