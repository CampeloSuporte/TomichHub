from django.contrib import admin
from django.utils.html import format_html
from .models import (
    WhatsAppConnection, ContactGroup, Conversation, Message,
    ConversationTag, ConversationActivity, AgentStatus, ChatbotConfig,
    SystemSettings
)


@admin.register(WhatsAppConnection)
class WhatsAppConnectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'instance_name', 'is_active', 'webhook_status', 'last_sync')
    list_filter = ('is_active', 'webhook_configured', 'created_at')
    search_fields = ('name', 'instance_name', 'business_phone')
    readonly_fields = ('id', 'created_at', 'updated_at', 'last_sync')
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('id', 'name', 'business_phone')
        }),
        ('Configuração Evolution', {
            'fields': ('evolution_url', 'api_key', 'instance_name')
        }),
        ('Status', {
            'fields': ('is_active', 'webhook_configured', 'last_sync')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def webhook_status(self, obj):
        color = 'green' if obj.webhook_configured else 'red'
        status = 'Configurado' if obj.webhook_configured else 'Não configurado'
        return format_html(f'<span style="color: {color};">●</span> {status}')
    webhook_status.short_description = 'Status Webhook'


@admin.register(ContactGroup)
class ContactGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'connection', 'cliente_link', 'members_count', 'status', 'synced_at')
    list_filter = ('connection', 'status', 'is_group', 'synced_at')
    search_fields = ('name', 'jid', 'cliente__nome_cliente')
    readonly_fields = ('jid', 'synced_at')
    fieldsets = (
        ('Informações do Grupo', {
            'fields': ('jid', 'connection', 'name', 'is_group')
        }),
        ('Detalhes', {
            'fields': ('profile_picture', 'description', 'members_count')
        }),
        ('Relacionamento', {
            'fields': ('cliente', 'status')
        }),
        ('Timestamps', {
            'fields': ('synced_at',),
            'classes': ('collapse',)
        }),
    )

    def cliente_link(self, obj):
        if obj.cliente:
            return format_html(
                '<a href="/admin/clientes/cliente/{}/change/">{}</a>',
                obj.cliente.id,
                obj.cliente.nome_cliente
            )
        return '—'
    cliente_link.short_description = 'Cliente'


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ('sender_type', 'sender', 'message_type', 'content', 'created_at')
    readonly_fields = ('created_at', 'external_id')
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('conversation_id', 'group', 'cliente_name', 'status_badge', 'assigned_to', 'priority', 'last_message_at')
    list_filter = ('status', 'priority', 'created_at', 'group__connection')
    search_fields = ('conversation_id', 'group__name', 'cliente__nome_cliente')
    readonly_fields = ('id', 'conversation_id', 'created_at', 'updated_at', 'closed_at')
    inlines = [MessageInline]
    fieldsets = (
        ('Informações do Ticket', {
            'fields': ('id', 'conversation_id', 'subject')
        }),
        ('Relacionamento', {
            'fields': ('group', 'cliente')
        }),
        ('Status e Prioridade', {
            'fields': ('status', 'priority', 'assigned_to')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'closed_at'),
            'classes': ('collapse',)
        }),
    )

    def cliente_name(self, obj):
        return obj.cliente.nome_cliente if obj.cliente else '—'
    cliente_name.short_description = 'Cliente'

    def status_badge(self, obj):
        colors = {
            'new': '#FFA500',
            'open': '#3B82F6',
            'pending': '#FFFF00',
            'resolved': '#00CC00',
            'closed': '#808080',
        }
        color = colors.get(obj.status, '#000000')
        return format_html(f'<span style="background: {color}; color: white; padding: 3px 8px; border-radius: 3px;">{obj.get_status_display()}</span>')
    status_badge.short_description = 'Status'


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'sender_type', 'sender', 'message_type', 'created_at', 'is_read')
    list_filter = ('message_type', 'sender_type', 'created_at')
    search_fields = ('conversation__conversation_id', 'content', 'external_id')
    readonly_fields = ('id', 'external_id', 'created_at', 'edited_at')

    def has_add_permission(self, request):
        return False  # Mensagens não devem ser adicionadas via admin


@admin.register(ConversationTag)
class ConversationTagAdmin(admin.ModelAdmin):
    list_display = ('name', 'color_preview', 'conversation_count')
    search_fields = ('name',)

    def color_preview(self, obj):
        return format_html(f'<span style="background: {obj.color}; padding: 5px 10px; border-radius: 3px; color: white;">●</span> {obj.color}')
    color_preview.short_description = 'Cor'

    def conversation_count(self, obj):
        return obj.conversations.count()
    conversation_count.short_description = 'Conversas'


@admin.register(ConversationActivity)
class ConversationActivityAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'action', 'actor', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('conversation__conversation_id',)
    readonly_fields = ('conversation', 'actor', 'action', 'created_at')

    def has_add_permission(self, request):
        return False


@admin.register(AgentStatus)
class AgentStatusAdmin(admin.ModelAdmin):
    list_display = ('user', 'status_badge', 'last_seen')
    list_filter = ('status', 'last_seen')

    def status_badge(self, obj):
        colors = {'online': 'green', 'busy': 'orange', 'away': 'yellow', 'offline': 'red'}
        color = colors.get(obj.status, 'gray')
        return format_html(f'<span style="color: {color};">●</span> {obj.get_status_display()}')
    status_badge.short_description = 'Status'


@admin.register(ChatbotConfig)
class ChatbotConfigAdmin(admin.ModelAdmin):
    list_display = ('connection', 'enabled', 'model', 'auto_respond')
    list_filter = ('enabled', 'auto_respond')
    fieldsets = (
        ('Configuração', {
            'fields': ('connection', 'enabled', 'model', 'auto_respond')
        }),
        ('Prompt do Sistema', {
            'fields': ('system_prompt',),
            'classes': ('wide',)
        }),
    )


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    list_display = ('key', 'value_preview', 'updated_at')
    search_fields = ('key',)

    def value_preview(self, obj):
        import json
        return json.dumps(obj.value)[:100] + '...'
    value_preview.short_description = 'Valor'
