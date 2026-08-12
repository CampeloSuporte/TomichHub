from django.db import models
from django.contrib.auth.models import User
from clientes.models import Cliente
from django.utils import timezone
import re
import uuid


def normalizar_telefone_br(numero: str) -> str:
    """
    Normaliza um telefone BR para fins de COMPARAÇÃO (nunca para exibição/
    envio — o nono dígito é obrigatório num JID de WhatsApp real). Mantém só
    dígitos e, se for celular no formato 55+DDD+9+8dígitos (13 dígitos),
    remove o nono dígito -> 55+DDD+8dígitos.
    Torna a detecção de "atendente respondendo pelo pessoal" tolerante a
    números cadastrados com ou sem o 9 — sem isso, um único dígito de
    diferença faz a comparação exata nunca bater.
    """
    digits = re.sub(r'\D', '', numero or '')
    if len(digits) == 13 and digits.startswith('55') and digits[4] == '9':
        return digits[:4] + digits[5:]
    return digits


class WhatsAppConnection(models.Model):
    """Configuração de instância Evolution API"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=255, unique=True)
    evolution_url = models.URLField()
    api_key = models.CharField(max_length=500)
    instance_name = models.CharField(max_length=255)
    business_phone = models.CharField(max_length=20, null=True, blank=True)
    color = models.CharField(max_length=7, default='#7c3aed')
    is_active = models.BooleanField(default=True)
    webhook_configured = models.BooleanField(default=False)
    last_sync = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Conexão WhatsApp"
        verbose_name_plural = "Conexões WhatsApp"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.instance_name})"


class Company(models.Model):
    """Empresa vinculada a grupos de atendimento"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=255)
    cnpj = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=30, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"
        ordering = ['name']

    def __str__(self):
        return self.name


class ContactGroup(models.Model):
    """Grupo/Contato do WhatsApp"""
    jid = models.CharField(max_length=255)
    connection = models.ForeignKey(WhatsAppConnection, on_delete=models.CASCADE, related_name='groups')
    name = models.CharField(max_length=255)
    is_group = models.BooleanField(default=True)
    profile_picture = models.URLField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    members_count = models.IntegerField(default=0)
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True)
    company = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True, blank=True, related_name='groups')
    ai_enabled = models.BooleanField(default=False)
    blocked = models.BooleanField(default=False)
    auto_atendimento_excluido = models.BooleanField(
        default=False,
        help_text='Se marcado, este grupo nunca recebe auto atendimento — nem por um '
                   'fluxo específico, nem pelo fluxo universal (group_ids vazio).'
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ('active', 'Ativo'),
            ('archived', 'Arquivado'),
            ('deleted', 'Deletado'),
        ],
        default='active'
    )
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('jid', 'connection')
        indexes = [
            models.Index(fields=['connection', 'status']),
            models.Index(fields=['cliente']),
        ]

    def __str__(self):
        return self.name


class UserGroupPermission(models.Model):
    """Permissão de atendente para visualizar um grupo"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='group_permissions')
    group = models.ForeignKey(ContactGroup, on_delete=models.CASCADE, related_name='user_permissions')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'group')
        verbose_name = "Permissão de Grupo"
        verbose_name_plural = "Permissões de Grupos"

    def __str__(self):
        return f"{self.user.get_full_name()} → {self.group.name}"


class Category(models.Model):
    """Categoria de chamado/conversa"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(max_length=7, default='#7c3aed')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Categoria"
        verbose_name_plural = "Categorias"
        ordering = ['name']

    def __str__(self):
        return self.name


class Tag(models.Model):
    """Tag para organizar conversas"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(max_length=7, default='#3B82F6')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Conversation(models.Model):
    """Conversa/Ticket de atendimento"""
    STATUS_CHOICES = [
        ('pre', 'Pré-abertura'),   # oculto: aguardando 2ª mensagem do cliente p/ abrir o chamado
        ('new', 'Novo'),
        ('open', 'Aberto'),
        ('pending', 'Aguardando'),
        ('resolved', 'Resolvido'),
        ('closed', 'Fechado'),
    ]

    PRIORITY_CHOICES = [
        ('low', 'Baixa'),
        ('medium', 'Média'),
        ('high', 'Alta'),
        ('urgent', 'Urgente'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    conversation_id = models.IntegerField(unique=True)
    group = models.ForeignKey(ContactGroup, on_delete=models.CASCADE, related_name='conversations')
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='conversations')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_conversations')
    subject = models.CharField(max_length=500, null=True, blank=True)
    title = models.CharField(max_length=500, null=True, blank=True)
    resolution = models.TextField(null=True, blank=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name='conversations')
    is_task_conv = models.BooleanField(
        default=False,
        help_text='Conversa vinculada a uma tarefa — novas mensagens abrem novo chamado'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    # Guard: marca que a notificação de "chamado sem atendimento" já foi
    # enviada para este chamado — garante que seja avisada uma única vez,
    # em vez de repetir a cada execução da task.
    notif_aberto_enviada = models.BooleanField(default=False)

    # ── SLA (tempo de resposta/resolução) ──────────────────────────────
    sla_response_due_at = models.DateTimeField(null=True, blank=True)
    sla_resolution_due_at = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    # Guard: marca que a escalação (alerta/reatribuição por SLA estourado)
    # já foi disparada para este chamado — evita repetir a cada ciclo.
    escalated = models.BooleanField(default=False)

    # ── CSAT (pesquisa de satisfação pós-atendimento) ──────────────────
    csat_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    csat_requested_at = models.DateTimeField(null=True, blank=True)
    csat_comment = models.TextField(blank=True, default='')

    # ── Mesclagem de chamados duplicados ───────────────────────────────
    merged_into = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='merged_children',
        help_text='Se preenchido, este chamado foi mesclado para outro (o destino).'
    )

    # Guard: marca que já avisamos o grupo do NOC que um atendente está
    # respondendo este chamado pelo WhatsApp pessoal — evita repetir o
    # alerta a cada mensagem enviada por esse número.
    personal_wa_alert_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ['-last_message_at', '-created_at']
        indexes = [
            models.Index(fields=['status', '-last_message_at']),
            models.Index(fields=['assigned_to', 'status']),
            models.Index(fields=['group', 'status']),
        ]

    def __str__(self):
        return f"#{self.conversation_id} - {self.group.name}"

    def save(self, *args, **kwargs):
        if not self.conversation_id:
            last = Conversation.objects.order_by('-conversation_id').first()
            self.conversation_id = (last.conversation_id + 1) if last else 1000
        super().save(*args, **kwargs)

    @property
    def sla_response_overdue(self):
        """SLA de 1ª resposta estourado — ainda sem resposta do atendente e já passou do prazo."""
        return bool(
            self.sla_response_due_at and not self.first_response_at
            and timezone.now() > self.sla_response_due_at
            and self.status not in ('resolved', 'closed')
        )

    @property
    def sla_resolution_overdue(self):
        """SLA de resolução estourado — chamado ainda aberto e já passou do prazo de resolução."""
        return bool(
            self.sla_resolution_due_at and timezone.now() > self.sla_resolution_due_at
            and self.status not in ('resolved', 'closed')
        )


class Message(models.Model):
    """Mensagem em uma conversa"""
    MESSAGE_TYPE_CHOICES = [
        ('text', 'Texto'),
        ('image', 'Imagem'),
        ('document', 'Documento'),
        ('audio', 'Áudio'),
        ('video', 'Vídeo'),
        ('location', 'Localização'),
        ('system', 'Sistema'),
    ]

    SENDER_TYPE_CHOICES = [
        ('customer', 'Cliente'),
        ('agent', 'Atendente'),
        ('ai', 'IA'),
        ('system', 'Sistema'),
        ('internal', 'Interno'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender_type = models.CharField(max_length=20, choices=SENDER_TYPE_CHOICES)
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    sender_name = models.CharField(max_length=255, null=True, blank=True)
    message_type = models.CharField(max_length=20, choices=MESSAGE_TYPE_CHOICES, default='text')
    content = models.TextField()
    external_id = models.CharField(max_length=255, unique=True, db_index=True)
    attachment_url = models.TextField(null=True, blank=True)
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['conversation', 'created_at']),
            models.Index(fields=['external_id']),
        ]

    def __str__(self):
        return f"Msg #{self.id}"


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


# Manter ConversationTag para compatibilidade (será removido gradualmente)
class ConversationTag(models.Model):
    """Tag legada — use Tag + Conversation.tags"""
    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(max_length=7, default="#3B82F6")
    conversations = models.ManyToManyField(Conversation, related_name='legacy_tags', blank=True)

    def __str__(self):
        return self.name


class ConversationActivity(models.Model):
    """Log de atividades na conversa"""
    ACTION_CHOICES = [
        ('opened', 'Aberto'),
        ('assigned', 'Atribuído'),
        ('transferred', 'Transferido'),
        ('status_changed', 'Status alterado'),
        ('note_added', 'Nota adicionada'),
        ('message_sent', 'Mensagem enviada'),
        ('closed', 'Fechado'),
        ('resolved', 'Resolvido'),
        ('reopened', 'Reaberto'),
    ]

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='activity')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    description = models.TextField(null=True, blank=True)
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['conversation', '-created_at']),
        ]

    def __str__(self):
        return f"{self.action} - {self.conversation}"


class AgentStatus(models.Model):
    """Status online dos atendentes"""
    STATUS_CHOICES = [
        ('online', 'Online'),
        ('busy', 'Ocupado'),
        ('away', 'Ausente'),
        ('offline', 'Offline'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='agent_status')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='offline')
    display_name = models.CharField(max_length=100, blank=True, default='',
                                    help_text='Nome exibido nas mensagens enviadas pelo WhatsApp')
    last_seen = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.status}"

    def get_display_name(self):
        """Retorna o nome de exibição ou o nome completo como fallback"""
        return self.display_name.strip() or self.user.get_full_name() or self.user.username


class QuickMessage(models.Model):
    """Mensagem rápida / resposta pré-definida"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    title = models.CharField(max_length=255)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title']
        verbose_name = "Mensagem Rápida"
        verbose_name_plural = "Mensagens Rápidas"

    def __str__(self):
        return self.title


class ChatFlow(models.Model):
    """Fluxo de auto atendimento"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=255)
    greeting_message = models.TextField()
    subject_question = models.TextField(default='Qual é o assunto do seu chamado?')
    category_question = models.TextField(default='Qual categoria melhor descreve o problema?')
    categories = models.JSONField(default=list)
    group_ids = models.JSONField(default=list)
    completion_message = models.TextField(blank=True, null=True)
    active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Fluxo de Auto Atendimento"
        verbose_name_plural = "Fluxos de Auto Atendimento"

    def __str__(self):
        return self.name


class ChatFlowSession(models.Model):
    """Sessão ativa de um fluxo de auto atendimento"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    flow = models.ForeignKey(ChatFlow, on_delete=models.CASCADE, related_name='sessions')
    # Conversa (chamado) já criada à qual este fluxo está vinculado — o auto
    # atendimento agora roda sobre uma conversa existente para que todas as
    # mensagens fiquem salvas e visíveis no chat.
    conversation = models.ForeignKey('Conversation', on_delete=models.CASCADE,
                                     null=True, blank=True, related_name='flow_sessions')
    group_jid = models.CharField(max_length=255, db_index=True)
    step = models.CharField(max_length=50, default='subject')
    subject = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=255, null=True, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['group_jid'])]

    def is_expired(self):
        return timezone.now() > self.expires_at


class KanbanBoard(models.Model):
    """Quadro Kanban"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = "Quadro Kanban"
        verbose_name_plural = "Quadros Kanban"

    def __str__(self):
        return self.name


class KanbanColumn(models.Model):
    """Coluna de um quadro Kanban"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    board = models.ForeignKey(KanbanBoard, on_delete=models.CASCADE, related_name='columns')
    name = models.CharField(max_length=255)
    position = models.IntegerField(default=0)
    color = models.CharField(max_length=7, default='#7c3aed')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position']
        indexes = [models.Index(fields=['board', 'position'])]

    def __str__(self):
        return f"{self.board.name} → {self.name}"


class KanbanCard(models.Model):
    """Card de um quadro Kanban"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    column = models.ForeignKey(KanbanColumn, on_delete=models.CASCADE, related_name='cards')
    title = models.CharField(max_length=500)
    description = models.TextField(null=True, blank=True)
    position = models.IntegerField(default=0)
    conversation = models.ForeignKey(Conversation, on_delete=models.SET_NULL, null=True, blank=True, related_name='kanban_cards')
    assignee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='kanban_cards')
    due_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['position']
        indexes = [models.Index(fields=['column', 'position'])]

    def __str__(self):
        return self.title


class SystemSetting(models.Model):
    """Configurações globais do sistema (chave-valor)"""
    key = models.CharField(max_length=100, unique=True, primary_key=True)
    value = models.TextField(blank=True, default='')
    is_secret = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuração do Sistema"
        verbose_name_plural = "Configurações do Sistema"

    def __str__(self):
        return self.key

    @classmethod
    def get(cls, key, default=''):
        try:
            return cls.objects.get(key=key).value
        except cls.DoesNotExist:
            return default

    @classmethod
    def set(cls, key, value, is_secret=False):
        obj, _ = cls.objects.update_or_create(
            key=key,
            defaults={'value': str(value), 'is_secret': is_secret}
        )
        return obj


class Task(models.Model):
    """Tarefa de atendimento — pode vincular uma ou mais conversas"""
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('in_progress', 'Em Andamento'),
        ('done', 'Concluída'),
        ('cancelled', 'Cancelada'),
    ]
    PRIORITY_CHOICES = [
        ('low', 'Baixa'),
        ('medium', 'Média'),
        ('high', 'Alta'),
        ('urgent', 'Urgente'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    title = models.CharField(max_length=500)
    description = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium')
    assigned_to = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_tasks'
    )
    due_date = models.DateTimeField(null=True, blank=True)
    conversations = models.ManyToManyField(
        Conversation, blank=True, through='TaskConversation', related_name='tasks'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Tarefa"
        verbose_name_plural = "Tarefas"
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['assigned_to', 'status']),
        ]

    def __str__(self):
        return self.title

    @property
    def is_overdue(self):
        return self.due_date and self.status not in ('done', 'cancelled') and self.due_date < timezone.now()


class TaskConversation(models.Model):
    """Vínculo entre tarefa e conversa"""
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='task_conversations')
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='task_conversations')
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('task', 'conversation')
        verbose_name = "Conversa da Tarefa"

    def __str__(self):
        return f"{self.task.title} ← {self.conversation}"


class AttendantContact(models.Model):
    """Número de WhatsApp do atendente para receber lembretes pessoais"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='attendant_contact')
    phone = models.CharField(
        max_length=50,
        help_text='Número no formato internacional, ex: 5511999999999'
    )
    connection = models.ForeignKey(
        WhatsAppConnection, on_delete=models.SET_NULL, null=True, blank=True,
        help_text='Conexão WhatsApp usada para enviar os lembretes'
    )
    reminders_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Contato do Atendente"
        verbose_name_plural = "Contatos dos Atendentes"

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} → {self.phone}"

    def get_jid(self):
        """Retorna o JID WhatsApp (adiciona @s.whatsapp.net se necessário)"""
        phone = self.phone.strip().replace('+', '').replace(' ', '').replace('-', '')
        if '@' not in phone:
            phone = f"{phone}@s.whatsapp.net"
        return phone


# Manter ChatbotConfig para compatibilidade
class ChatbotConfig(models.Model):
    """Configuração de chatbot/IA por conexão"""
    connection = models.OneToOneField(WhatsAppConnection, on_delete=models.CASCADE, related_name='chatbot_config')
    enabled = models.BooleanField(default=False)
    model = models.CharField(max_length=100, default='claude-sonnet-4-6')
    system_prompt = models.TextField()
    auto_respond = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Chatbot - {self.connection.name}"


# Manter SystemSettings (antigo) para não quebrar migrations
class SystemSettings(models.Model):
    """Configurações globais legadas"""
    key = models.CharField(max_length=100, unique=True, primary_key=True)
    value = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "System Settings (Legacy)"

    def __str__(self):
        return self.key
