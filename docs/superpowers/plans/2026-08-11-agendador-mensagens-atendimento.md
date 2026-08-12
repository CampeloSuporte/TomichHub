# Agendador de Mensagens no Atendimento — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que o atendente digite uma mensagem (ou anexe mídia) no chat de atendimento, clique em "Agendar" e escolha data/hora futura pra ela ser enviada automaticamente ao cliente, com um painel pra ver/cancelar agendamentos pendentes daquela conversa.

**Architecture:** Novo model `ScheduledMessage` guarda o que precisa ser enviado e quando. Uma task periódica do Celery (`timedelta(minutes=1)`, mesmo padrão já usado no projeto) varre pendentes vencidas e envia reaproveitando a mesma lógica de envio já usada pelos botões normais — para isso, a lógica de envio de mídia (hoje só existe inline dentro da view `api_send_media`) é extraída para `ConversationService.send_media`, no mesmo molde de `ConversationService.send_message` que já existe. UI: um terceiro botão "Agendar" ao lado das abas WhatsApp/Comentário Interno abre um modal de data/hora; um botão "Agendadas" no cabeçalho (mesmo padrão visual do botão "Tarefas" já existente) abre um painel lateral clonado do `.task-panel`.

**Tech Stack:** Django 5 (views/models/templates), Celery (`shared_task` + `beat_schedule`), JS vanilla (mesmo padrão dos outros módulos do chat — IIFEs com um namespace público em `window`).

**Spec:** `docs/superpowers/specs/2026-08-11-agendador-mensagens-atendimento-design.md`

---

## Nota de implementação (diverge ligeiramente do texto do spec, mesma intenção)

O spec descreve `ScheduledMessage.content` de forma genérica. Na implementação, pra mídia,
`content` guarda a **legenda crua digitada pelo atendente (pode ser vazia)** — não um rótulo
padrão tipo "Imagem" já computado. O rótulo padrão (usado quando não há legenda) é calculado só
na hora do envio, dentro de `ConversationService.send_media` — exatamente como já acontece hoje
no envio imediato. Se computássemos o rótulo padrão no momento de agendar e reenviássemos esse
valor como a "legenda" pro WhatsApp na hora do envio, uma mensagem agendada sem legenda sairia
com a legenda "Imagem" grudada nela, o que não é o comportamento do envio imediato.

---

### Task 1: Model `ScheduledMessage`

**Files:**
- Modify: `atendimento/models.py` (inserir depois da classe `Message`, que termina na linha 300 — logo antes do comentário `# Manter ConversationTag...` na linha 303)
- Create: `atendimento/migrations/0012_scheduledmessage.py` (gerada pelo Django)
- Test: `atendimento/tests.py`

- [ ] **Step 1: Escrever o teste (vai falhar — `ScheduledMessage` ainda não existe)**

Substitua o conteúdo de `atendimento/tests.py` (hoje só tem o stub padrão) por:

```python
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
```

Essa classe `_criar_conversa()` é reutilizada pelos testes das próximas tasks — todos ficam no
mesmo arquivo `atendimento/tests.py`, só adicionando classes novas no final. Os imports acima já
cobrem `_save_media_file`, `_read_attachment_as_base64`, `ConversationService`,
`enviar_mensagens_agendadas` e `reverse`/`json`/`mock` que as próximas tasks vão usar — não
precisa reescrever o bloco de import depois, só usar o que já está lá.

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `python manage.py test atendimento.tests.ScheduledMessageModelTest -v 2`
Expected: FAIL — `ImportError: cannot import name 'ScheduledMessage' from 'atendimento.models'`
(as outras importações do topo do arquivo, como `_read_attachment_as_base64` e
`enviar_mensagens_agendadas`, também vão falhar até as tasks seguintes — normal nesse ponto).

- [ ] **Step 3: Adicionar o model**

Em `atendimento/models.py`, logo depois do fim da classe `Message` (linha 300, `return f"Msg #{self.id}"`)
e antes do comentário `# Manter ConversationTag...`:

```python
class ScheduledMessage(models.Model):
    """Mensagem agendada para envio futuro numa conversa."""
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('sent', 'Enviada'),
        ('cancelled', 'Cancelada'),
        ('failed', 'Falhou'),
    ]
    MAX_ATTEMPTS = 5

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='scheduled_messages')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    message_type = models.CharField(max_length=20, choices=Message.MESSAGE_TYPE_CHOICES, default='text')
    content = models.TextField(blank=True)  # texto da mensagem, ou legenda da mídia (pode ser vazia)
    attachment_url = models.TextField(null=True, blank=True)  # já salvo em MEDIA_ROOT no momento do agendamento
    file_name = models.CharField(max_length=255, null=True, blank=True)  # nome original do arquivo, p/ reenviar
    scheduled_for = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['scheduled_for']
        indexes = [models.Index(fields=['status', 'scheduled_for'])]

    def __str__(self):
        return f"Agendada #{self.id} - {self.get_status_display()} - {self.scheduled_for:%d/%m %H:%M}"
```

- [ ] **Step 4: Gerar e aplicar a migração**

Run: `python manage.py makemigrations atendimento`
Expected: cria `atendimento/migrations/0012_scheduledmessage.py`

Run: `python manage.py migrate atendimento`
Expected: `Applying atendimento.0012_scheduledmessage... OK`

- [ ] **Step 5: Rodar o teste de novo (ainda vai falhar por causa dos outros imports que não existem)**

Run: `python manage.py test atendimento.tests.ScheduledMessageModelTest -v 2`
Expected: ainda FAIL, mas agora por `ImportError` de `_read_attachment_as_base64` ou
`enviar_mensagens_agendadas` (que só existem a partir da Task 4/5) — **não** mais por
`ScheduledMessage`. Se a única falha visível já não menciona mais `ScheduledMessage`, o Step 3
está correto; siga pra Task 2 antes de esperar esse teste passar de verdade.

- [ ] **Step 6: Commit**

```bash
git add atendimento/models.py atendimento/migrations/0012_scheduledmessage.py atendimento/tests.py
git commit -m "feat(atendimento): model ScheduledMessage para agendador de mensagens"
```

---

### Task 2: `ConversationService.send_media` (extrai lógica que hoje só vive na view)

**Files:**
- Modify: `atendimento/services.py:1163` (fim da classe `ConversationService`, depois de `send_message`)
- Test: `atendimento/tests.py`

- [ ] **Step 1: Escrever os testes (vão falhar — `send_media` ainda não existe no service)**

Adicionar ao final de `atendimento/tests.py`:

```python
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
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `python manage.py test atendimento.tests.SendMediaServiceTest -v 2`
Expected: FAIL — `AttributeError: type object 'ConversationService' has no attribute 'send_media'`

- [ ] **Step 3: Implementar `ConversationService.send_media`**

Em `atendimento/services.py`, logo depois do fim de `send_message` (linha 1163, `return False, str(e)`),
ainda dentro da classe `ConversationService` (mesma indentação de `send_message`):

```python

    @staticmethod
    def send_media(conversation: Conversation, media_base64: str, media_type: str,
                   file_name: str, caption: str, agent=None) -> Tuple[bool, str]:
        """Salva a Message de mídia imediatamente e envia ao WhatsApp em
        background. Mesma mecânica de send_message, mas para
        imagem/áudio/vídeo/documento. Igual ao envio imediato, sempre salva
        um arquivo novo em disco (mesmo se o chamador já tiver um
        attachment_url de antes, como no agendador) — simples e evita um
        segundo caminho de código só pra reaproveitar o arquivo."""
        import threading as _threading
        import mimetypes as _mt
        import time as _t

        try:
            detected_mime, _ = _mt.guess_type(file_name)
            if not detected_mime:
                detected_mime = {
                    'image': 'image/jpeg', 'audio': 'audio/ogg',
                    'video': 'video/mp4', 'document': 'application/octet-stream',
                }.get(media_type, 'application/octet-stream')

            attachment_url = None
            try:
                attachment_url = _save_media_file(media_base64, detected_mime)
            except Exception as _save_err:
                logger.warning("Salvar midia falhou: %s", _save_err)

            if caption:
                content = caption
            elif media_type == 'document':
                content = file_name
            else:
                type_labels = {'image': 'Imagem', 'audio': 'Áudio', 'document': 'Documento', 'video': 'Vídeo'}
                content = type_labels.get(media_type, media_type)

            display_name = ConversationService.get_agent_display_name(agent)
            now = timezone.now()
            msg = Message.objects.create(
                conversation=conversation, sender_type='agent', sender=agent,
                sender_name=display_name, message_type=media_type, content=content,
                external_id=f"local_media_{int(_t.time()*1000)}",
                attachment_url=attachment_url, created_at=now,
            )
            conversation.last_message_at = now
            if conversation.status == 'new':
                conversation.status = 'open'
            conversation.save(update_fields=['last_message_at', 'status'])

            group_connection = conversation.group.connection
            group_jid = conversation.group.jid
            msg_id = msg.id

            def _send_bg():
                try:
                    client = EvolutionAPIClient(group_connection)
                    if media_type == 'audio':
                        client.send_audio(group_jid, media_base64)
                    else:
                        client.send_media(group_jid, mediatype=media_type, media_b64=media_base64,
                                          filename=file_name, caption=caption)
                except Exception as _e:
                    logger.error(f"Erro bg envio mídia (msg {msg_id}): {_e}")

            _threading.Thread(target=_send_bg, daemon=True).start()

            return True, str(msg.id)

        except Exception as e:
            logger.error(f"Erro ao enviar mídia: {e}")
            return False, str(e)
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `python manage.py test atendimento.tests.SendMediaServiceTest -v 2`
Expected: `OK` (3 testes)

- [ ] **Step 5: Commit**

```bash
git add atendimento/services.py atendimento/tests.py
git commit -m "feat(atendimento): extrai ConversationService.send_media da view api_send_media"
```

---

### Task 3: `api_send_media` passa a chamar `ConversationService.send_media`

**Files:**
- Modify: `atendimento/views.py:448-527`
- Test: `atendimento/tests.py`

- [ ] **Step 1: Escrever o teste de regressão (vai passar já, mesmo antes da mudança — é o comportamento atual)**

Adicionar ao final de `atendimento/tests.py`:

```python
class ApiSendMediaTest(TestCase):
    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = User.objects.create_user(username='ana', password='x', is_staff=True, is_active=True)
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
```

- [ ] **Step 2: Rodar o teste ANTES da mudança pra confirmar que já passa (baseline do comportamento atual)**

Run: `python manage.py test atendimento.tests.ApiSendMediaTest -v 2`
Expected: `OK` (2 testes) — a view ainda tem a lógica antiga inline, mas o contrato JSON (`success`,
`content`) já é esse.

- [ ] **Step 3: Substituir a lógica inline da view pela chamada ao service**

Em `atendimento/views.py`, a função `api_send_media` inteira (linhas 448-534) vira:

```python
@staff_required
@require_http_methods(["POST"])
def api_send_media(request, conversation_id):
    """Envia mídia (imagem, documento, áudio) em uma conversa"""
    try:
        conversation = get_object_or_404(Conversation, id=conversation_id)
        data = json.loads(request.body)

        media_base64 = data.get('mediaBase64', '').strip()
        media_type   = data.get('mediaType', 'document')   # image | audio | document | video
        file_name    = data.get('fileName', 'arquivo')
        caption      = data.get('caption', '').strip()

        if not media_base64:
            return JsonResponse({'success': False, 'error': 'Base64 vazio'}, status=400)

        success, result = ConversationService.send_media(
            conversation, media_base64, media_type, file_name, caption, request.user
        )
        if success:
            msg = Message.objects.get(id=result)
            return JsonResponse({'success': True, 'message_id': result, 'content': msg.content})
        else:
            return JsonResponse({'success': False, 'error': result}, status=400)

    except Exception as e:
        logger.error(f"Erro ao enviar mídia: {e}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)
```

Os imports que só essa função usava (`mimetypes`, `time`, `threading`, `EvolutionAPIClient`,
`_save_media_file` dentro do corpo da função) somem — quem precisa disso agora é só
`ConversationService.send_media`, em `services.py`.

- [ ] **Step 4: Rodar o teste de novo e confirmar que ainda passa (mesmo contrato, lógica movida)**

Run: `python manage.py test atendimento.tests.ApiSendMediaTest -v 2`
Expected: `OK` (2 testes)

- [ ] **Step 5: Commit**

```bash
git add atendimento/views.py atendimento/tests.py
git commit -m "refactor(atendimento): api_send_media usa ConversationService.send_media"
```

---

### Task 4: Helper `_read_attachment_as_base64`

**Files:**
- Modify: `atendimento/services.py:221` (logo depois de `_save_media_file`)
- Test: `atendimento/tests.py`

- [ ] **Step 1: Escrever o teste (vai falhar — helper ainda não existe)**

Adicionar ao final de `atendimento/tests.py`:

```python
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
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `python manage.py test atendimento.tests.ReadAttachmentAsBase64Test -v 2`
Expected: FAIL — `ImportError: cannot import name '_read_attachment_as_base64'` (esse import já
está no topo do arquivo desde a Task 1, então o erro aparece na coleta dos testes, antes de rodar
qualquer um — normal até este ponto)

- [ ] **Step 3: Implementar o helper**

Em `atendimento/services.py`, logo depois de `_save_media_file` (linha 221, fecha com
`return f"{settings.MEDIA_URL}atendimento/media/{filename}"`):

```python

def _read_attachment_as_base64(attachment_url: str) -> str:
    """Lê de volta um arquivo salvo por _save_media_file e devolve em
    base64 — usado pra reenviar a mídia de uma mensagem agendada, que só
    guarda a URL (não o base64) enquanto espera a hora de enviar."""
    relative = attachment_url.replace(settings.MEDIA_URL, '', 1)
    abs_path = os.path.join(settings.MEDIA_ROOT, relative)
    with open(abs_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('ascii')
```

- [ ] **Step 4: Rodar todos os testes do arquivo e confirmar que passam agora**

Run: `python manage.py test atendimento.tests -v 2`
Expected: `OK` — todos os testes das Tasks 1-4 passam agora (os das Tasks 5+ ainda não existem).

- [ ] **Step 5: Commit**

```bash
git add atendimento/services.py atendimento/tests.py
git commit -m "feat(atendimento): helper _read_attachment_as_base64 p/ reenvio de midia agendada"
```

---

### Task 5: Task periódica `enviar_mensagens_agendadas`

**Files:**
- Modify: `atendimento/tasks.py` (adicionar no final do arquivo)
- Test: `atendimento/tests.py`

- [ ] **Step 1: Escrever os testes (vão falhar — task ainda não existe)**

Adicionar ao final de `atendimento/tests.py`:

```python
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
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `python manage.py test atendimento.tests.EnviarMensagensAgendadasTest -v 2`
Expected: FAIL — `ImportError: cannot import name 'enviar_mensagens_agendadas'` (mesmo caso da
Task 4: esse import já está no topo do arquivo desde a Task 1)

- [ ] **Step 3: Implementar a task**

Adicionar ao final de `atendimento/tasks.py`:

```python

@shared_task
def enviar_mensagens_agendadas():
    """A cada 1 min: envia ScheduledMessage pendentes cujo scheduled_for já
    passou. Segue merged_into se a conversa foi mesclada; reabre se estava
    Resolvida/Encerrada. Cada falha soma em `attempts`; depois de
    ScheduledMessage.MAX_ATTEMPTS tentativas (uma por ciclo, ~1/min) marca
    `failed` e para de tentar — fica sinalizado no painel de agendadas."""
    from .models import ScheduledMessage
    from .services import ConversationService

    due = ScheduledMessage.objects.select_related('conversation').filter(
        status='pending', scheduled_for__lte=timezone.now()
    )
    for sm in due:
        conversation = sm.conversation
        while conversation.merged_into_id:
            conversation = conversation.merged_into

        if conversation.status in ('resolved', 'closed'):
            conversation.status = 'open'
            conversation.save(update_fields=['status'])

        try:
            if sm.message_type == 'text':
                ok, result = ConversationService.send_message(conversation, sm.content, sm.created_by)
            else:
                from .services import _read_attachment_as_base64
                b64 = _read_attachment_as_base64(sm.attachment_url)
                ok, result = ConversationService.send_media(
                    conversation, b64, sm.message_type, sm.file_name or 'arquivo',
                    sm.content, sm.created_by
                )
            if not ok:
                raise Exception(result)

            sm.status = 'sent'
            sm.sent_at = timezone.now()
            sm.save(update_fields=['status', 'sent_at'])
        except Exception as e:
            sm.attempts += 1
            sm.last_error = str(e)[:500]
            if sm.attempts >= ScheduledMessage.MAX_ATTEMPTS:
                sm.status = 'failed'
            sm.save(update_fields=['attempts', 'last_error', 'status'])
            logger.error(f"Falha ao enviar mensagem agendada {sm.id}: {e}")
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `python manage.py test atendimento.tests.EnviarMensagensAgendadasTest -v 2`
Expected: `OK` (6 testes)

- [ ] **Step 5: Commit**

```bash
git add atendimento/tasks.py atendimento/tests.py
git commit -m "feat(atendimento): task enviar_mensagens_agendadas (envio automatico do agendador)"
```

---

### Task 6: Registrar a task periódica no Celery Beat

**Files:**
- Modify: `crm/celery.py`

- [ ] **Step 1: Adicionar entrada no `beat_schedule`**

Em `crm/celery.py`, dentro de `app.conf.beat_schedule = { ... }`, junto das outras entradas que
usam `timedelta` (linhas 73-88), adicionar:

```python
    'atendimento-enviar-mensagens-agendadas': {
        'task': 'atendimento.tasks.enviar_mensagens_agendadas',
        'schedule': timedelta(minutes=1),
    },
```

- [ ] **Step 2: Validar que o Celery reconhece a task sem erro de import**

Run: `python -c "import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','crm.settings'); django.setup(); from crm.celery import app; print(app.conf.beat_schedule['atendimento-enviar-mensagens-agendadas'])"`
Expected: imprime o dict `{'task': 'atendimento.tasks.enviar_mensagens_agendadas', 'schedule': datetime.timedelta(seconds=60)}` sem lançar exceção

- [ ] **Step 3: Commit**

```bash
git add crm/celery.py
git commit -m "feat(atendimento): agenda enviar_mensagens_agendadas no Celery Beat (1/min)"
```

- [ ] **Step 4: Reiniciar o celery beat em produção (fora do escopo do repositório — lembrete operacional)**

Depois do deploy, `systemctl restart celerybeat` (ou o nome do serviço equivalente) — celery beat
só relê `beat_schedule` na inicialização, então a task nova não roda até reiniciar o serviço.

---

### Task 7: Endpoint `api_schedule_message` (criar + listar)

**Files:**
- Modify: `atendimento/urls.py`
- Modify: `atendimento/views.py`
- Test: `atendimento/tests.py`

- [ ] **Step 1: Escrever os testes (vão falhar — endpoint ainda não existe)**

Adicionar ao final de `atendimento/tests.py`:

```python
class ApiScheduleMessageTest(TestCase):
    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = User.objects.create_user(username='ana', password='x', is_staff=True, is_active=True)
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
        self.assertEqual(sm.content, '')  # legenda crua, não "Imagem" — ver nota de implementação
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
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `python manage.py test atendimento.tests.ApiScheduleMessageTest -v 2`
Expected: FAIL — `NoReverseMatch: Reverse for 'api_schedule_message' not found`

- [ ] **Step 3: Adicionar a URL**

Em `atendimento/urls.py`, junto dos outros `api/conversation/...` (perto da linha 34):

```python
    path('api/conversation/<uuid:conversation_id>/schedule-message/', views.api_schedule_message, name='api_schedule_message'),
```

- [ ] **Step 4: Adicionar `ScheduledMessage` ao import de models na view**

Em `atendimento/views.py`, linha 39-43, o bloco de import fica:

```python
from .models import (
    WhatsAppConnection, ContactGroup, Conversation, Message,
    ConversationActivity, AgentStatus, ChatbotConfig,
    Task, TaskConversation, AttendantContact, ScheduledMessage,
)
```

- [ ] **Step 5: Implementar a view**

Em `atendimento/views.py`, logo depois de `api_send_media` (depois do fim da função, antes de
`api_update_conversation`):

```python
@staff_required
@require_http_methods(["GET", "POST"])
def api_schedule_message(request, conversation_id):
    """Cria (POST) ou lista pendentes (GET) mensagens agendadas de uma conversa."""
    conversation = get_object_or_404(Conversation, id=conversation_id)

    if request.method == 'GET':
        pending = conversation.scheduled_messages.filter(status='pending').order_by('scheduled_for')
        return JsonResponse({'scheduled': [
            {
                'id': str(sm.id),
                'content': sm.content,
                'file_name': sm.file_name,
                'message_type': sm.message_type,
                'scheduled_for': sm.scheduled_for.isoformat(),
                'scheduled_for_display': timezone.localtime(sm.scheduled_for).strftime('%d/%m %H:%M'),
            }
            for sm in pending
        ]})

    try:
        data = json.loads(request.body)
        scheduled_for_raw = data.get('scheduled_for')
        if not scheduled_for_raw:
            return JsonResponse({'success': False, 'error': 'Data/hora obrigatória'}, status=400)

        from django.utils.dateparse import parse_datetime
        scheduled_for = parse_datetime(scheduled_for_raw)
        if not scheduled_for:
            return JsonResponse({'success': False, 'error': 'Data/hora inválida'}, status=400)
        if scheduled_for.tzinfo is None:
            scheduled_for = timezone.make_aware(scheduled_for)
        if scheduled_for <= timezone.now():
            return JsonResponse({'success': False, 'error': 'A data/hora precisa ser no futuro'}, status=400)

        media_base64 = data.get('mediaBase64', '').strip()

        if media_base64:
            media_type = data.get('mediaType', 'document')
            file_name = data.get('fileName', 'arquivo')
            caption = data.get('caption', '').strip()

            import mimetypes as _mt
            from .services import _save_media_file
            detected_mime, _ = _mt.guess_type(file_name)
            if not detected_mime:
                detected_mime = {
                    'image': 'image/jpeg', 'audio': 'audio/ogg',
                    'video': 'video/mp4', 'document': 'application/octet-stream',
                }.get(media_type, 'application/octet-stream')
            attachment_url = _save_media_file(media_base64, detected_mime)

            sm = ScheduledMessage.objects.create(
                conversation=conversation, created_by=request.user,
                message_type=media_type, content=caption,
                attachment_url=attachment_url, file_name=file_name,
                scheduled_for=scheduled_for,
            )
        else:
            message_text = data.get('message', '').strip()
            if not message_text:
                return JsonResponse({'success': False, 'error': 'Mensagem vazia'}, status=400)
            sm = ScheduledMessage.objects.create(
                conversation=conversation, created_by=request.user,
                message_type='text', content=message_text,
                scheduled_for=scheduled_for,
            )

        return JsonResponse({'success': True, 'id': str(sm.id), 'scheduled_for': sm.scheduled_for.isoformat()})

    except Exception as e:
        logger.error(f"Erro ao agendar mensagem: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
```

Se `_save_media_file` falhar (disco cheio, permissão), a exceção sobe pro `try/except` externo e
volta 400 pro atendente — diferente do envio imediato, que degrada silenciosamente pra
`attachment_url=None`. Faz sentido aqui: sem arquivo salvo não tem o que reenviar depois, então
falhar na hora (o atendente vê o erro e tenta de novo) é melhor que criar um agendamento fadado a
falhar sozinho minutos/horas depois.

- [ ] **Step 6: Rodar os testes e confirmar que passam**

Run: `python manage.py test atendimento.tests.ApiScheduleMessageTest -v 2`
Expected: `OK` (5 testes)

- [ ] **Step 7: Rodar a suíte inteira do app pra garantir que nada quebrou**

Run: `python manage.py test atendimento -v 2`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add atendimento/urls.py atendimento/views.py atendimento/tests.py
git commit -m "feat(atendimento): endpoint api_schedule_message (criar/listar agendadas)"
```

---

### Task 8: Endpoint `api_cancel_scheduled_message`

**Files:**
- Modify: `atendimento/urls.py`
- Modify: `atendimento/views.py`
- Test: `atendimento/tests.py`

- [ ] **Step 1: Escrever os testes (vão falhar — endpoint ainda não existe)**

Adicionar ao final de `atendimento/tests.py`:

```python
class ApiCancelScheduledMessageTest(TestCase):
    def setUp(self):
        self.conversation = _criar_conversa()
        self.agent = User.objects.create_user(username='ana', password='x', is_staff=True, is_active=True)
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
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `python manage.py test atendimento.tests.ApiCancelScheduledMessageTest -v 2`
Expected: FAIL — `NoReverseMatch: Reverse for 'api_cancel_scheduled_message' not found`

- [ ] **Step 3: Adicionar a URL**

Em `atendimento/urls.py`, logo depois da URL de `api_schedule_message`:

```python
    path('api/scheduled-message/<uuid:scheduled_id>/cancel/', views.api_cancel_scheduled_message, name='api_cancel_scheduled_message'),
```

- [ ] **Step 4: Implementar a view**

Em `atendimento/views.py`, logo depois de `api_schedule_message`:

```python
@staff_required
@require_http_methods(["POST"])
def api_cancel_scheduled_message(request, scheduled_id):
    """Cancela uma mensagem agendada ainda pendente."""
    sm = get_object_or_404(ScheduledMessage, id=scheduled_id)
    if sm.status != 'pending':
        return JsonResponse({'success': False, 'error': 'Esta mensagem já foi enviada ou cancelada'}, status=400)
    sm.status = 'cancelled'
    sm.cancelled_by = request.user
    sm.save(update_fields=['status', 'cancelled_by'])
    return JsonResponse({'success': True})
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `python manage.py test atendimento.tests.ApiCancelScheduledMessageTest -v 2`
Expected: `OK` (2 testes)

- [ ] **Step 6: Rodar a suíte inteira do app**

Run: `python manage.py test atendimento -v 2`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add atendimento/urls.py atendimento/views.py atendimento/tests.py
git commit -m "feat(atendimento): endpoint api_cancel_scheduled_message"
```

---

### Task 9: `scheduled_count` no contexto de `conversation_detail`

**Files:**
- Modify: `atendimento/views.py:210-229`

Sem teste dedicado — é só uma contagem exposta pro template (coberta indiretamente pela Task 12,
que usa esse valor na UI).

- [ ] **Step 1: Adicionar a contagem e expor no contexto**

Em `atendimento/views.py`, logo depois do bloco que monta `conv_tasks`/`agents_list` (linhas
210-215):

```python
    scheduled_count = conversation.scheduled_messages.filter(status='pending').count()
```

E no `context = {...}` (linhas 217-229), adicionar a chave:

```python
        'conv_tasks': conv_tasks,
        'agents_list': agents_list,
        'scheduled_count': scheduled_count,
    }
```

- [ ] **Step 2: Verificar que a view ainda carrega sem erro**

Run: `python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 3: Commit**

```bash
git add atendimento/views.py
git commit -m "feat(atendimento): expoe scheduled_count no contexto de conversation_detail"
```

---

### Task 10: Botão "Agendar" ao lado das abas WhatsApp/Comentário Interno

**Files:**
- Modify: `atendimento/templates/atendimento/_chat_content.html:304-311` (HTML das tabs)
- Modify: `atendimento/templates/atendimento/_chat_content.html:1142-1149` (JS `setMode`)
- Modify: `atendimento/templates/atendimento/base.html:957-961` (CSS `.chat-mode-tab`)

Sem teste automatizado — é HTML/CSS/JS de template, sem cobertura de teste no projeto (mesmo
padrão dos outros módulos do chat). Verificação é manual, no final da Task 14.

- [ ] **Step 1: Adicionar o terceiro botão nas tabs**

Em `_chat_content.html`, o bloco `.chat-mode-tabs` (linhas 304-311) fica:

```html
        <div class="chat-mode-tabs">
            <button class="chat-mode-tab active" id="tabWa" onclick="chatApp.setMode('whatsapp')">
                <i class="fab fa-whatsapp"></i> WhatsApp
            </button>
            <button class="chat-mode-tab" id="tabInt" onclick="chatApp.setMode('internal')">
                <i class="fas fa-lock"></i> Comentário Interno
            </button>
            <button class="chat-mode-tab schedule-btn" id="tabSchedule" onclick="scheduledMsgs.openModal()" title="Agendar envio desta mensagem">
                <i class="fas fa-clock"></i> Agendar
            </button>
        </div>
```

- [ ] **Step 2: Desabilitar o botão visualmente no modo Comentário Interno**

Em `_chat_content.html`, a função `setMode` (linhas 1142-1149) fica:

```javascript
    function setMode(mode) {
        isInternal = (mode === 'internal');
        document.getElementById('tabWa').classList.toggle('active', !isInternal);
        document.getElementById('tabInt').classList.toggle('active', isInternal);
        document.getElementById('tabSchedule').classList.toggle('disabled', isInternal);
        msgInput.placeholder = isInternal ? 'Nota interna (não enviada ao cliente)...' : 'Mensagem...';
        var area = document.querySelector('.chat-input-area');
        if (area) area.classList.toggle('mode-internal', isInternal);
    }
```

- [ ] **Step 3: CSS do botão — visualmente diferente das duas abas de modo**

Em `base.html`, logo depois da regra `.chat-mode-tab.active` (linha 960, antes da regra
`.chat-mode-tabs + .chat-input-area.mode-internal ...` na linha 961):

```css
.chat-mode-tab.schedule-btn { border-style: dashed; margin-left: auto; }
.chat-mode-tab.schedule-btn.disabled { opacity: 0.35; pointer-events: none; }
```

`margin-left:auto` empurra o botão pro fim da linha (separado visualmente das duas abas de modo,
que ficam juntas à esquerda) — reforça que é uma ação, não um terceiro modo.

- [ ] **Step 4: Commit**

```bash
git add atendimento/templates/atendimento/_chat_content.html atendimento/templates/atendimento/base.html
git commit -m "feat(atendimento): botao Agendar ao lado das abas WhatsApp/Comentario Interno"
```

---

### Task 11: Modal de data/hora (`#scheduleModal`)

**Files:**
- Modify: `atendimento/templates/atendimento/_chat_content.html:255` (logo depois do fim de `#resolucaoModal`)

- [ ] **Step 1: Adicionar o modal**

Em `_chat_content.html`, logo depois do `</div>` que fecha `#resolucaoModal` (linha 255) e antes
do comentário `<!-- ── Área de Input ── -->` (linha 257):

```html

    <!-- ── Modal: Agendar mensagem ── -->
    <div id="scheduleModal" style="
        display:none; position:fixed; inset:0; z-index:3000;
        background:rgba(0,0,0,0.82); backdrop-filter:blur(4px);
        align-items:center; justify-content:center;
    " onclick="if(event.target===this)scheduledMsgs.closeModal()">
        <div style="
            background:#161b22; border:1px solid rgba(6,207,156,0.22);
            border-radius:12px; padding:0; width:380px; max-width:94vw;
            box-shadow:0 20px 60px rgba(0,0,0,0.8);
        ">
            <div style="padding:18px 22px 14px; border-bottom:1px solid rgba(6,207,156,0.1); display:flex; align-items:center; justify-content:space-between;">
                <div>
                    <h3 style="font-size:14px;font-weight:700;color:var(--text);margin:0;">Agendar Mensagem</h3>
                    <p style="font-size:11px;color:var(--muted);margin:3px 0 0;">Escolha quando esta mensagem deve ser enviada</p>
                </div>
                <button onclick="scheduledMsgs.closeModal()" style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:17px;padding:4px;">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div style="padding:20px 22px;">
                <label style="display:block;font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">
                    Data e hora do envio *
                </label>
                <input type="datetime-local" id="scheduleDateTime" style="
                    width:100%; padding:9px 12px; box-sizing:border-box;
                    background:rgba(255,255,255,0.04);
                    border:1px solid rgba(6,207,156,0.18);
                    border-radius:8px; color:var(--text);
                    font-size:13px; font-family:'Inter',sans-serif;
                    outline:none;
                ">
                <div style="display:flex;gap:8px;margin-top:16px;">
                    <button onclick="scheduledMsgs.closeModal()" style="
                        flex:1; padding:10px; background:rgba(9,14,26,0.8);
                        border:1px solid rgba(88,166,255,0.15); border-radius:8px;
                        color:var(--muted); font-size:12px; cursor:pointer; font-family:'Inter',sans-serif;
                    ">Cancelar</button>
                    <button onclick="scheduledMsgs.confirm()" style="
                        flex:2; padding:10px;
                        background:rgba(6,207,156,0.1);
                        border:1px solid rgba(6,207,156,0.35); border-radius:8px;
                        color:#06cf9c; font-size:12px; font-weight:700;
                        cursor:pointer; font-family:'Inter',sans-serif;
                    ">
                        <i class="fas fa-clock" style="margin-right:6px;"></i>
                        Agendar Envio
                    </button>
                </div>
            </div>
        </div>
    </div>
```

- [ ] **Step 2: Commit**

```bash
git add atendimento/templates/atendimento/_chat_content.html
git commit -m "feat(atendimento): modal de data/hora do agendador de mensagens"
```

---

### Task 12: Botão "Agendadas" no cabeçalho + badge de contagem

**Files:**
- Modify: `atendimento/templates/atendimento/_chat_content.html:76-90`

- [ ] **Step 1: Adicionar o botão no `.chat-header-actions`, antes do botão Tarefas**

Em `_chat_content.html`, logo depois do bloco `{% endwith %}` do botão Hosts (linha 82) e antes
do comentário `<!-- Botão Tarefas -->` (linha 83):

```html
            <button class="btn-cyber secondary" onclick="scheduledMsgs.togglePanel()" id="btnSchedulePanel" title="Mensagens agendadas desta conversa">
                <i class="fas fa-clock"></i>
                {% if scheduled_count %}<span id="scheduleCountBadge" style="background:rgba(6,207,156,.2);color:#06cf9c;border-radius:10px;padding:1px 5px;font-size:9px;margin-left:2px">{{ scheduled_count }}</span>{% endif %}
                Agendadas
            </button>
```

- [ ] **Step 2: Commit**

```bash
git add atendimento/templates/atendimento/_chat_content.html
git commit -m "feat(atendimento): botao Agendadas no cabecalho da conversa"
```

---

### Task 13: Painel lateral "Mensagens Agendadas"

**Files:**
- Modify: `atendimento/templates/atendimento/_chat_content.html:481` (HTML, logo depois do fim de `.task-panel`)
- Modify: `atendimento/templates/atendimento/base.html:841` (CSS, logo depois do fim de `.task-panel`)

- [ ] **Step 1: HTML do painel**

Em `_chat_content.html`, logo depois do `</div>` que fecha `.task-panel` (`#taskPanelEl`, linha
481) e antes do `</div>` de fechamento do `.chat-main` (linha 483):

```html

    <!-- ══ Painel de Mensagens Agendadas ══ -->
    <div class="schedule-panel" id="schedulePanelEl">
        <div class="schedule-panel-header">
            <i class="fas fa-clock" style="color:#06cf9c;font-size:12px"></i>
            <span style="flex:1;font-size:13px;font-weight:700;color:var(--text)">Mensagens Agendadas</span>
            <button style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:14px" onclick="scheduledMsgs.togglePanel()"><i class="fas fa-times"></i></button>
        </div>
        <div class="schedule-panel-body" id="schedulePanelList">
            <div style="padding:20px;text-align:center;color:var(--muted);font-size:12px">
                <i class="fas fa-clock" style="font-size:24px;opacity:.2;display:block;margin-bottom:8px"></i>
                Nenhuma mensagem agendada
            </div>
        </div>
    </div>
```

- [ ] **Step 2: CSS do painel (clone de `.task-panel`)**

Em `base.html`, logo depois do fim do bloco `.btn-cyber.task-active` (linha 841) e antes do
comentário `/* ── Painel de Terminal ── */` (linha 842):

```css
/* ── Painel de Agendadas ── */
.schedule-panel {
    width: 0;
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    background: var(--dark-900);
    border-left: 0px solid rgba(6,207,156,0.2);
    overflow: hidden;
    transition: width 0.22s ease, border-left-width 0.22s ease;
    min-height: 0;
}
.schedule-panel.open {
    width: 340px;
    border-left-width: 1px;
}
.schedule-panel-header {
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
    background: var(--dark-800);
}
.schedule-panel-body {
    flex: 1;
    overflow-y: auto;
    padding: 6px;
}
.sp-item {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 10px 8px;
    border-bottom: 1px solid var(--border);
}
.sp-item:hover { background: rgba(255,255,255,.02); }
.sp-cancel-btn {
    background: none; border: none; cursor: pointer;
    color: var(--muted); padding: 3px 5px; border-radius: 4px;
    flex-shrink: 0; transition: color .15s;
}
.sp-cancel-btn:hover { color: #ef4444; }
```

- [ ] **Step 3: Commit**

```bash
git add atendimento/templates/atendimento/_chat_content.html atendimento/templates/atendimento/base.html
git commit -m "feat(atendimento): painel lateral de mensagens agendadas"
```

---

### Task 14: JS — módulo `scheduledMsgs` + `chatApp.getComposerState`

**Files:**
- Modify: `atendimento/templates/atendimento/_chat_content.html:2007-2019` (`window.chatApp`)
- Modify: `atendimento/templates/atendimento/_chat_content.html:2368` (logo depois do fim do bloco TASK PANEL, antes de `</script>`)

- [ ] **Step 1: Expor o estado do composer em `window.chatApp`**

Em `_chat_content.html`, o objeto `window.chatApp = {...}` (linhas 2007-2019) ganha um método
novo, antes de `destroy`:

```javascript
    window.chatApp = {
        setMode:setMode, toggleAttachMenu:toggleAttachMenu, closeAttachMenu:closeAttachMenu,
        handleFileSelect:handleFileSelect, clearMedia:clearMedia, sendMedia:sendMedia,
        startRecording:startRecording, stopRecording:stopRecording, cancelRecording:cancelRecording,
        toggleStatusMenu:toggleStatusMenu, updateStatus:updateStatus,
        closeResolucao:closeResolucao, confirmarResolucao:confirmarResolucao,
        getComposerState: function() {
            var captionEl = document.getElementById('mediaCaption');
            return {
                text: msgInput.value.trim(),
                mediaBase64: mediaBase64,
                mediaType: mediaType,
                mediaFileName: mediaFileName,
                mediaCaption: captionEl ? captionEl.value.trim() : ''
            };
        },
        destroy: function() {
            if(ws){ws.onclose=null;ws.close();ws=null;}
            _pollDestroyed = true;
            clearTimeout(wsTimer); clearInterval(recTimer); clearTimeout(pollTimer);
            document.removeEventListener('click', docClick);
        }
    };
```

`getComposerState` lê `mediaCaption` do DOM (não do `msgInput`) porque, quando há mídia anexada,
a legenda vem desse campo separado — igual ao que `sendMedia()` já faz hoje.

- [ ] **Step 2: Novo módulo `scheduledMsgs`**

Em `_chat_content.html`, logo depois do `})();` que fecha o bloco `TASK PANEL` (linha 2368) e
antes de `</script>` (linha 2370):

```javascript

// ══════════════════════════════════════════════════════════
// AGENDADOR DE MENSAGENS
// ══════════════════════════════════════════════════════════
(function(){
var CSRF    = '{{ csrf_token }}';
var CONV_ID = '{{ conversation.id }}';
var URL_SCHEDULE = '/atendimento/api/conversation/' + CONV_ID + '/schedule-message/';
var URL_CANCEL = function(id){ return '/atendimento/api/scheduled-message/' + id + '/cancel/'; };

function _esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function _nowLocalISO() {
    var d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0,16);
}

window.scheduledMsgs = {
    openModal: function() {
        if (document.getElementById('tabInt').classList.contains('active')) return;
        var state = window.chatApp.getComposerState();
        if (!state.text && !state.mediaBase64) {
            alert('Digite uma mensagem ou anexe uma mídia antes de agendar.');
            return;
        }
        var dt = document.getElementById('scheduleDateTime');
        dt.min = _nowLocalISO();
        dt.value = '';
        document.getElementById('scheduleModal').style.display = 'flex';
    },
    closeModal: function() {
        document.getElementById('scheduleModal').style.display = 'none';
    },
    confirm: function() {
        var scheduledFor = document.getElementById('scheduleDateTime').value;
        if (!scheduledFor) { alert('Escolha data e hora.'); return; }

        var state = window.chatApp.getComposerState();
        var body = { scheduled_for: scheduledFor };
        if (state.mediaBase64) {
            body.mediaBase64 = state.mediaBase64;
            body.mediaType = state.mediaType;
            body.fileName = state.mediaFileName;
            body.caption = state.mediaCaption;
        } else {
            if (!state.text) { alert('Digite uma mensagem.'); return; }
            body.message = state.text;
        }

        fetch(URL_SCHEDULE, {
            method: 'POST',
            headers: {'Content-Type':'application/json','X-CSRFToken':CSRF},
            body: JSON.stringify(body)
        }).then(function(r){ return r.json(); }).then(function(d){
            if (d.success) {
                scheduledMsgs.closeModal();
                var msgInput = document.getElementById('msgInput');
                msgInput.value = '';
                msgInput.style.height = 'auto';
                window.chatApp.clearMedia();
                scheduledMsgs._refreshBadge();
            } else {
                alert('Erro: ' + (d.error || 'Falha ao agendar'));
            }
        }).catch(function(){ alert('Erro de conexão'); });
    },
    togglePanel: function() {
        var el = document.getElementById('schedulePanelEl');
        var opening = !el.classList.contains('open');
        el.classList.toggle('open');
        if (opening) scheduledMsgs.loadList();
    },
    loadList: function() {
        fetch(URL_SCHEDULE, {headers:{'X-CSRFToken':CSRF}})
            .then(function(r){ return r.json(); })
            .then(function(d){ scheduledMsgs._render(d.scheduled || []); });
    },
    _render: function(items) {
        var body = document.getElementById('schedulePanelList');
        if (!items.length) {
            body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--muted);font-size:12px"><i class="fas fa-clock" style="font-size:24px;opacity:.2;display:block;margin-bottom:8px"></i>Nenhuma mensagem agendada</div>';
            return;
        }
        body.innerHTML = items.map(function(it){
            var preview = it.file_name ? ('📎 ' + _esc(it.file_name)) : _esc((it.content||'').slice(0,80));
            return '<div class="sp-item" id="sp-item-'+it.id+'">'
                + '<div style="flex:1;min-width:0">'
                + '<div style="font-size:12px;color:var(--text);margin-bottom:3px">'+preview+'</div>'
                + '<div style="font-size:11px;color:var(--muted)"><i class="fas fa-clock"></i> '+_esc(it.scheduled_for_display)+'</div>'
                + '</div>'
                + '<button class="sp-cancel-btn" onclick="scheduledMsgs.cancel(\''+it.id+'\')" title="Cancelar"><i class="fas fa-times"></i></button>'
                + '</div>';
        }).join('');
    },
    cancel: function(id) {
        if (!confirm('Cancelar esta mensagem agendada?')) return;
        fetch(URL_CANCEL(id), {method:'POST', headers:{'X-CSRFToken':CSRF}})
            .then(function(r){ return r.json(); })
            .then(function(d){
                if (d.success) {
                    var el = document.getElementById('sp-item-'+id);
                    if (el) el.remove();
                    scheduledMsgs._refreshBadge();
                } else {
                    alert('Erro: ' + (d.error || 'Falha ao cancelar'));
                }
            });
    },
    _refreshBadge: function() {
        fetch(URL_SCHEDULE, {headers:{'X-CSRFToken':CSRF}})
            .then(function(r){ return r.json(); })
            .then(function(d){
                var btn = document.getElementById('btnSchedulePanel');
                if (!btn) return;
                var count = (d.scheduled || []).length;
                var badge = document.getElementById('scheduleCountBadge');
                if (count > 0) {
                    if (!badge) {
                        badge = document.createElement('span');
                        badge.id = 'scheduleCountBadge';
                        badge.style.cssText = 'background:rgba(6,207,156,.2);color:#06cf9c;border-radius:10px;padding:1px 5px;font-size:9px;margin-left:2px';
                        btn.insertBefore(badge, btn.lastChild);
                    }
                    badge.textContent = count;
                } else if (badge) {
                    badge.remove();
                }
                // Se o painel estiver aberto, atualiza a lista também
                if (document.getElementById('schedulePanelEl').classList.contains('open')) {
                    scheduledMsgs._render(d.scheduled || []);
                }
            });
    },
};
})();
```

- [ ] **Step 3: Commit**

```bash
git add atendimento/templates/atendimento/_chat_content.html
git commit -m "feat(atendimento): modulo JS scheduledMsgs (agendar/listar/cancelar)"
```

---

### Task 15: Verificação manual end-to-end

**Files:** nenhum (só verificação)

Não há navegador/browser disponível neste ambiente de implementação para screenshot automático —
esta task é uma checklist manual pra quem executar o plano rodar com o servidor de dev.

- [ ] **Step 1: Rodar a suíte de testes completa do app**

Run: `python manage.py test atendimento -v 2`
Expected: `OK` — todos os testes das Tasks 1, 2, 3, 4, 5, 7, 8 passam

- [ ] **Step 2: Checar migrações e configuração do Django**

Run: `python manage.py check`
Expected: `System check identified no issues (0 silenced).`

Run: `python manage.py makemigrations --check --dry-run`
Expected: `No changes detected` (garante que a migração da Task 1 já cobre o model)

- [ ] **Step 3: Smoke test manual no navegador (dev server)**

Run: `python manage.py runserver 0.0.0.0:8000`

Depois, logado como atendente, numa conversa qualquer do módulo de Atendimento:

1. Digitar uma mensagem no campo de texto (modo WhatsApp) → clicar em "Agendar" → modal abre.
2. Tentar confirmar sem escolher data → alerta "Escolha data e hora."
3. Escolher uma data/hora poucos minutos no futuro → confirmar → modal fecha, campo de texto
   limpa, badge "Agendadas" no cabeçalho mostra "1".
4. Abrir o painel "Agendadas" → item aparece com preview do texto e horário.
5. Cancelar o item → some da lista, badge some.
6. Repetir o agendamento (mensagem de texto, ~2 min no futuro) e esperar o Celery Beat rodar
   (task a cada 1 min) → mensagem some da lista de agendadas e aparece no histórico do chat como
   enviada pelo agente.
7. Trocar pra aba "Comentário Interno" → botão "Agendar" fica visualmente apagado e não clicável.
8. Anexar uma imagem (sem digitar legenda) → clicar em "Agendar" → confirmar → esperar o ciclo do
   Celery → checar que a imagem chega no chat sem legenda "Imagem" grudada (valida a nota de
   implementação sobre legenda crua vs. rótulo padrão).

- [ ] **Step 4: Confirmar visualmente em light e dark mode (se o CRM suportar troca de tema)**

Não é obrigatório correção pixel-perfect, só confirmar que o modal e o painel não ficam
ilegíveis/quebrados no tema oposto ao testado no Step 3.

---

## Self-Review

**1. Cobertura do spec:**
- Feature 1 (modelo) → Task 1. ✅
- Feature 2 (endpoints) → Tasks 7, 8, 9. ✅ (listagem via GET na mesma URL de criar, como o spec
  já previa)
- Feature 3 (Celery + refactor de `send_media`) → Tasks 2, 3, 4, 5, 6. ✅
- Feature 4 (UI: botão, modal, painel, JS) → Tasks 10, 11, 12, 13, 14. ✅
- "Fora de escopo" do spec (edição de agendada, recorrência, limite de quantidade, notificação
  ativa em falha) → nenhuma task implementa isso. ✅

**2. Placeholder scan:** nenhum "TBD"/"adicionar validação apropriada" — todo código é completo e
literal em cada step.

**3. Consistência de tipos/nomes:** `ScheduledMessage` (Task 1) usado com os mesmos campos em
todas as tasks seguintes (`content`, `attachment_url`, `file_name`, `scheduled_for`, `status`,
`attempts`, `last_error`, `MAX_ATTEMPTS`). `ConversationService.send_media(conversation,
media_base64, media_type, file_name, caption, agent)` (Task 2) chamado com essa mesma ordem de
argumentos na Task 3 (view) e na Task 5 (task periódica). `_read_attachment_as_base64` (Task 4)
importado e chamado do mesmo jeito na Task 5. Nomes de URL (`api_schedule_message`,
`api_cancel_scheduled_message`) iguais entre `urls.py`, os testes (`reverse(...)`) e o JS
(caminhos hardcoded batendo com o `path()` registrado).
