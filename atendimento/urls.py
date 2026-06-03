from django.urls import path
from . import views

app_name = 'atendimento'

urlpatterns = [
    # Páginas principais
    path('', views.dashboard, name='dashboard'),
    path('inbox/', views.inbox, name='inbox'),
    path('conversation/<uuid:conversation_id>/', views.conversation_detail, name='conversation_detail'),

    # Configurações (settings* para que 'settings' in url_name funcione)
    path('settings/connections/', views.settings_connections, name='settings_connections'),
    path('settings/groups/', views.settings_groups, name='settings_groups'),
    path('settings/configuracoes/', views.configuracoes, name='settings_configuracoes'),

    # Páginas novas
    path('empresas/', views.empresas, name='empresas'),
    path('kanban/', views.kanban, name='kanban'),
    path('auto-atendimento/', views.auto_atendimento, name='auto_atendimento'),
    path('relatorios/', views.relatorios, name='relatorios'),
    path('relatorios/pdf/', views.relatorio_pdf, name='relatorio_pdf'),
    path('historico/', views.historico, name='historico'),
    path('grupos/', views.grupos, name='grupos'),

    # APIs existentes
    path('api/connection/create/', views.api_create_connection, name='api_create_connection'),
    path('api/connection/<uuid:connection_id>/test/', views.api_test_connection, name='api_test_connection'),
    path('api/connection/<uuid:connection_id>/sync/', views.api_sync_groups, name='api_sync_groups'),
    path('api/connection/<uuid:connection_id>/webhook/', views.api_configure_webhook, name='api_configure_webhook'),
    path('api/conversation/<uuid:conversation_id>/messages/', views.api_conversation_messages, name='api_conversation_messages'),
    path('api/conversation/<uuid:conversation_id>/send-message/', views.api_send_message, name='api_send_message'),
    path('api/conversation/<uuid:conversation_id>/send-media/', views.api_send_media, name='api_send_media'),
    path('api/conversation/<uuid:conversation_id>/update/', views.api_update_conversation, name='api_update_conversation'),
    path('api/group/<int:group_id>/link/', views.api_link_group, name='api_link_group'),

    # APIs novas — Tags
    path('api/conversations/<uuid:conversation_id>/tags/', views.api_conversation_tags, name='api_conversation_tags'),
    path('api/conversations/<uuid:conversation_id>/tags/<uuid:tag_id>/', views.api_conversation_tags, name='api_conversation_tag_remove'),
    path('api/tags/', views.api_tags_list, name='api_tags_list'),
    path('api/tags/<uuid:tag_id>/', views.api_tag_detail, name='api_tag_detail'),

    # APIs novas — Categorias
    path('api/categories/', views.api_categories_list, name='api_categories_list'),
    path('api/categories/<uuid:category_id>/', views.api_category_detail, name='api_category_detail'),

    # APIs novas — Mensagens Rápidas
    path('api/quick-messages/', views.api_quick_messages_list, name='api_quick_messages_list'),
    path('api/quick-messages/<uuid:msg_id>/', views.api_quick_message_detail, name='api_quick_message_detail'),

    # APIs novas — Configurações do sistema
    path('api/settings/', views.api_settings, name='api_settings'),
    path('api/permissions/', views.api_permissions, name='api_permissions'),

    # APIs novas — Empresas
    path('api/empresas/', views.api_empresas_list, name='api_empresas_list'),
    path('api/empresas/<uuid:empresa_id>/', views.api_empresa_detail, name='api_empresa_detail'),

    # APIs novas — Kanban
    path('api/kanban/boards/', views.api_kanban_boards, name='api_kanban_boards'),
    path('api/kanban/boards/<uuid:board_id>/', views.api_kanban_board_detail, name='api_kanban_board_detail'),
    path('api/kanban/boards/<uuid:board_id>/columns/', views.api_kanban_columns, name='api_kanban_columns'),
    path('api/kanban/boards/<uuid:board_id>/columns/<uuid:column_id>/', views.api_kanban_column_detail, name='api_kanban_column_detail'),
    path('api/kanban/boards/<uuid:board_id>/columns/<uuid:column_id>/cards/', views.api_kanban_cards, name='api_kanban_cards'),
    path('api/kanban/boards/<uuid:board_id>/columns/<uuid:column_id>/cards/<uuid:card_id>/', views.api_kanban_card_detail, name='api_kanban_card_detail'),
    path('api/kanban/cards/<uuid:card_id>/move/', views.api_kanban_move_card, name='api_kanban_move_card'),

    # APIs novas — Chat Flows
    path('api/chat-flows/', views.api_chat_flows_list, name='api_chat_flows_list'),
    path('api/chat-flows/<uuid:flow_id>/', views.api_chat_flow_detail, name='api_chat_flow_detail'),

    # APIs novas — Grupos
    path('api/groups/<int:group_id>/toggle-ai/', views.api_group_toggle_ai, name='api_group_toggle_ai'),
    path('api/groups/<int:group_id>/set-company/', views.api_group_set_company, name='api_group_set_company'),

    # API Clientes (empresas) → grupos
    path('api/empresas/auto-vincular/', views.api_auto_vincular, name='api_auto_vincular'),
    path('api/clientes/<int:cliente_id>/grupos/', views.api_cliente_grupos, name='api_cliente_grupos'),

    # Testes de notificação
    path('api/test-notif/', views.api_test_notif_abertos, name='api_test_notif_abertos'),
    path('api/test-alerta/', views.api_test_alerta_diario, name='api_test_alerta_diario'),

    # Lista de agentes para transferência
    path('api/agents/', views.api_agents_list, name='api_agents_list'),
    # Sincronização de todos os grupos/contatos
    path('api/sync-all/', views.api_sync_all_connections, name='api_sync_all'),

    # Hosts do cliente da conversa
    path('api/conversation/<uuid:conversation_id>/hosts/', views.api_conversation_hosts, name='api_conversation_hosts'),

    # Iniciar conversa
    path('api/groups/search/', views.api_groups_json, name='api_groups_json'),
    path('api/conversation/start/', views.api_start_conversation_by_group, name='api_start_conversation'),

    # Perfil do agente
    path('api/display-name/', views.api_display_name, name='api_display_name'),

    # Webhook
    path('webhook/evolution/', views.webhook_evolution, name='webhook_evolution'),
]
