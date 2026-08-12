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
