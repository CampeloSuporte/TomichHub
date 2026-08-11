# Agendador de mensagens no chat de atendimento

**Data:** 2026-08-11
**Status:** Aprovado para planejamento

## Objetivo

No módulo de atendimento (`atendimento/templates/atendimento/_chat_content.html`), permitir que o
atendente digite uma mensagem (ou anexe mídia, como já faz hoje) e agende o envio pro cliente numa
data/hora futura, em vez de enviar na hora. Inclui um painel pra ver e cancelar mensagens já
agendadas de uma conversa.

## Fora de escopo

- Agendar **Comentário Interno** — agendamento só se aplica ao modo WhatsApp (mensagem pro
  cliente); o botão fica desabilitado nesse modo.
- Edição de uma mensagem já agendada — só cancelar (e digitar de novo se quiser reagendar).
- Recorrência ("enviar toda segunda às 9h") — é agendamento pontual, uma vez só.
- Limite de quantidade de agendamentos por conversa ou de quão longe no futuro — sem indicação de
  que isso precise ser restrito.
- Notificação ativa (push/e-mail) quando um agendamento falha definitivamente — fica sinalizado
  passivamente na lista de agendadas (ver Feature 3).

---

## Feature 1 — Modelo de dados

`atendimento/models.py`, logo após a classe `Message` (linha ~301):

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
    content = models.TextField(blank=True)          # texto ou legenda da mídia
    attachment_url = models.TextField(null=True, blank=True)  # já salvo em MEDIA_ROOT no momento do agendamento
    file_name = models.CharField(max_length=255, null=True, blank=True)  # nome original, p/ reenviar documento
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

Migração padrão (`python manage.py makemigrations atendimento`).

---

## Feature 2 — Endpoints

`atendimento/urls.py`, junto dos outros `api/conversation/...`:

```python
path('api/conversation/<uuid:conversation_id>/schedule-message/', views.api_schedule_message, name='api_schedule_message'),
path('api/scheduled-message/<uuid:scheduled_id>/cancel/', views.api_cancel_scheduled_message, name='api_cancel_scheduled_message'),
```

`atendimento/views.py`, ao lado de `api_send_message`/`api_send_media` (linha ~446):

- **`api_schedule_message(request, conversation_id)`** (POST) — mesmo shape de payload de
  `api_send_message`/`api_send_media` (`message` OU `mediaBase64`/`mediaType`/`fileName`/`caption`),
  mais `scheduled_for` (ISO datetime). Valida:
  - `scheduled_for` no futuro (`> timezone.now()`) — senão 400.
  - conteúdo não vazio (texto ou mídia) — senão 400, igual às validações existentes.
  - Se houver mídia, salva no disco imediatamente via `_save_media_file` (mesma função usada em
    `api_send_media`), grava `attachment_url` — **não** guarda o base64 no banco.
  - Cria o `ScheduledMessage` com `status='pending'`, `created_by=request.user`.
  - Retorna `{'success': True, 'id': ..., 'scheduled_for': ...}`.
- **`api_cancel_scheduled_message(request, scheduled_id)`** (POST) — busca o `ScheduledMessage`,
  se `status != 'pending'` retorna 400 ("já foi enviada/cancelada"), senão marca
  `status='cancelled'`, `cancelled_by=request.user`.
- **Listagem**: em vez de endpoint novo, reaproveita-se um `GET` na própria
  `api_schedule_message` (mesma URL, método diferente) filtrando
  `conversation.scheduled_messages.filter(status='pending').order_by('scheduled_for')` — evita uma
  terceira rota pra algo tão simples, seguindo o padrão já usado em `api_conversation_tags` (POST
  cria, DELETE remove, mesma URL base).

Ambos decorados com `@staff_required` — mesma regra de acesso dos outros endpoints do módulo (não
há checagem adicional de "dono da conversa"; qualquer atendente com acesso ao chat pode agendar ou
cancelar, consistente com o resto do módulo).

---

## Feature 3 — Envio automático (Celery)

### Refactor necessário

A lógica de envio de mídia hoje está inline em `api_send_media` (`atendimento/views.py:448-527`),
acoplada a `request`/`request.user`. Pra reaproveitar do Celery (sem `request`), extrai-se pro
`ConversationService` (`atendimento/services.py`), ao lado de `send_message` (linha 1086):

```python
@staticmethod
def send_media(conversation, media_base64, media_type, file_name, caption, agent=None) -> Tuple[bool, str]:
    """Mesma lógica hoje em api_send_media: salva Message, atualiza conversa,
    dispara Evolution API em background. Retorna (success, message_id_or_error)."""
```

`api_send_media` passa a chamar `ConversationService.send_media(...)` em vez de ter a lógica
inline — remove duplicação, não muda comportamento.

### Task periódica

`atendimento/tasks.py`, seguindo o padrão já usado (RotaLoop, SLA, etc — `shared_task` +
`beat_schedule` com `timedelta`, sem agendamento avulso por item):

```python
@shared_task
def enviar_mensagens_agendadas():
    """Varre ScheduledMessage pendentes com scheduled_for <= agora e envia."""
    from .models import ScheduledMessage
    from .services import ConversationService

    due = ScheduledMessage.objects.select_related('conversation').filter(
        status='pending', scheduled_for__lte=timezone.now()
    )
    for sm in due:
        conversation = sm.conversation
        # segue mesclagem até a conversa final
        while conversation.merged_into_id:
            conversation = conversation.merged_into

        if conversation.status in ('resolved', 'closed'):
            conversation.status = 'open'
            conversation.save(update_fields=['status'])

        try:
            if sm.message_type == 'text':
                ok, result = ConversationService.send_message(conversation, sm.content, sm.created_by)
            else:
                b64 = _read_attachment_as_base64(sm.attachment_url)
                ok, result = ConversationService.send_media(
                    conversation, b64, sm.message_type, sm.file_name or 'arquivo',
                    sm.content, sm.created_by
                )
            if ok:
                sm.status = 'sent'
                sm.sent_at = timezone.now()
                sm.save(update_fields=['status', 'sent_at'])
            else:
                raise Exception(result)
        except Exception as e:
            sm.attempts += 1
            sm.last_error = str(e)[:500]
            if sm.attempts >= ScheduledMessage.MAX_ATTEMPTS:
                sm.status = 'failed'
            sm.save(update_fields=['attempts', 'last_error', 'status'])
```

`_read_attachment_as_base64`: helper novo em `atendimento/services.py`, ao lado de
`_save_media_file` (linha 213), que resolve `attachment_url` (`MEDIA_URL` + caminho) de volta pro
caminho absoluto em `MEDIA_ROOT` e devolve o conteúdo em base64 — mesma convenção de
`_save_media_file`, só no sentido inverso.

`crm/celery.py`, `beat_schedule` (junto dos outros `timedelta`, linha ~73-88):

```python
'atendimento-enviar-mensagens-agendadas': {
    'task': 'atendimento.tasks.enviar_mensagens_agendadas',
    'schedule': timedelta(minutes=1),
},
```

Falha esgotando `MAX_ATTEMPTS=5` (5 tentativas, uma por minuto) → fica `status='failed'`, visível
no painel (Feature 4) com o `last_error`, sem notificação ativa — o atendente só sabe se conferir o
painel.

---

## Feature 4 — UI

### Botão "Agendar Mensagem"

`atendimento/templates/atendimento/_chat_content.html`, na `.chat-mode-tabs` (linha ~304-311), um
terceiro botão depois de `tabInt`:

```html
<button class="chat-mode-tab schedule-btn" id="tabSchedule" onclick="scheduledMsgs.openModal()" title="Agendar envio desta mensagem">
    <i class="fas fa-clock"></i> Agendar
</button>
```

CSS (`base.html`, junto de `.chat-mode-tab`, linha ~957-961): variante `.schedule-btn` com borda
tracejada (visualmente diferente das duas abas de modo, já que é uma ação, não um toggle) —
reaproveita cores neutras já usadas em `.btn-add-tag`. Fica com `opacity:.4; pointer-events:none`
quando `isInternal === true` (JS toggla uma classe `disabled` no `setMode`).

Modal de data/hora: reaproveita o padrão visual do modal de Resolução já existente
(`#resolucaoModal`, linhas 194-255) — mesmo estilo inline, novo `#scheduleModal` com um único
`<input type="datetime-local" id="scheduleDateTime">` (`min` setado via JS pro instante atual) e
botões Cancelar/Confirmar.

### Painel "Agendadas"

No `.chat-header-actions` (linha ~84-90), ao lado do botão Tarefas, mesmo padrão visual (badge de
contagem):

```html
<button class="btn-cyber secondary" onclick="scheduledMsgs.togglePanel()" id="btnSchedulePanel" title="Mensagens agendadas desta conversa">
    <i class="fas fa-clock"></i>
    {% if scheduled_count %}<span id="scheduleCountBadge" ...>{{ scheduled_count }}</span>{% endif %}
    Agendadas
</button>
```

`scheduled_count` é calculado onde `conv_tasks` já é hoje (view que renderiza `_chat_content.html`,
mesmo local de `conversation_detail`): `conversation.scheduled_messages.filter(status='pending').count()`.

Painel lateral novo `.schedule-panel` — HTML/CSS clonados de `.task-panel` (`base.html:739-841`,
`_chat_content.html:410-481`): mesma mecânica de abrir/fechar (`width: 0` → `340px`), cada item
mostra preview do conteúdo (texto truncado ou "📎 nome_do_arquivo"), `scheduled_for` formatado, e
um botão cancelar que chama `scheduledMsgs.cancel(id)`.

### JS

Novo módulo `window.scheduledMsgs`, mesmo padrão de `window.taskPanel`
(`_chat_content.html:2228`): `openModal`, `confirm` (lê o texto do `msgInput` ou o
`mediaBase64`/`mediaType` já staged, mesma lógica de `doSend`/`sendMedia`, mas chamando
`api_schedule_message` em vez de `api_send_message`/`api_send_media`), `togglePanel`, `cancel`,
`_fb` (feedback, igual ao `taskPanel._fb`). Ao confirmar com sucesso, limpa o textarea/preview de
mídia igual a um envio normal e atualiza o badge de contagem sem precisar recarregar a conversa.

---

## Testes

- `atendimento/tests.py`: criar `ScheduledMessage` com `scheduled_for` no passado, rodar
  `enviar_mensagens_agendadas()`, checar que virou `Message` + `status='sent'`.
- Caso conversa mesclada: agenda numa conversa A, mescla A→B antes da task rodar, confere que a
  `Message` foi criada em B.
- Caso conversa fechada: agenda, marca `closed`, roda a task, confere `status` volta pra `open` e a
  mensagem sai.
- Caso falha (mock do `EvolutionAPIClient` levantando exceção): confere `attempts` incrementa e,
  após `MAX_ATTEMPTS`, `status='failed'`.
- Validação: `api_schedule_message` com `scheduled_for` no passado retorna 400.
