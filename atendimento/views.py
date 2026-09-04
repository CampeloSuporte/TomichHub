from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseForbidden
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q, Count, F
from django.utils import timezone
from django.core.paginator import Paginator
import json
import logging
import re

def _is_staff(user):
    return user.is_active and user.is_staff

def staff_required(view_func):
    """Porta de entrada do módulo de Atendimento.

    O Atendimento é **exclusivo da instância principal** (a operação própria
    do Administrador): entram o Administrador e os Operadores dela. Consultor
    de revenda não entra — nem tela, nem API, nem WebSocket.

    Antes checava `request.user.is_staff` cru. Isso deixou de servir quando
    Consultor e Operador passaram a ser criados com `is_staff=True` (o que
    eles precisam para Scripts de Automação e para o WebSocket de firmware):
    o módulo inteiro ficou aberto para todas as revendas. Quem decide agora é
    `perms.pode_acessar_atendimento`.
    """
    from functools import wraps
    from django.conf import settings as _settings
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        from usuario.perms import pode_acessar_atendimento, is_backoffice, is_admin
        if not request.user.is_authenticated:
            login_url = getattr(_settings, 'LOGIN_URL', '/auth/login/')
            return redirect(f'{login_url}?next={request.path}')
        if not pode_acessar_atendimento(request.user):
            # Consultor/Operador de revenda: o dashboard deles é o da
            # instância. Mandar pro `quadro_geral` (que hoje é só do
            # Administrador) só empurraria o redirect adiante.
            if is_backoffice(request.user) and not is_admin(request.user):
                return redirect('quadro_instancia')
            return redirect('quadro_geral')
        return view_func(request, *args, **kwargs)
    return wrapper

# Views de configuração da plataforma (conexões WhatsApp, permissões,
# settings globais) — restritas ao Administrador. Checa o PAPEL e não
# `is_staff`, porque Consultor/Operador também são is_staff (é o que os
# libera no `staff_required` acima) e não podem configurar a plataforma
# nem ver as listas globais dessas telas.
def admin_required(view_func):
    def wrapper(request, *args, **kwargs):
        from usuario.perms import is_admin
        if not is_admin(request.user):
            return HttpResponseForbidden("Acesso restrito a administradores")
        return view_func(request, *args, **kwargs)
    return login_required(wrapper)

from .models import (
    WhatsAppConnection, ContactGroup, Conversation, Message,
    ConversationActivity, AgentStatus, ChatbotConfig,
    Task, TaskConversation, AttendantContact, ScheduledMessage,
)
from .services import EvolutionAPIClient, ConversationService
from .scope import (
    clientes_visiveis, conversations_visiveis, groups_visiveis,
    pode_ver_conversation, pode_ver_group,
)
from clientes.models import Cliente

logger = logging.getLogger(__name__)


def _marcar_mensagens_lidas(conversation):
    """Marca as mensagens do cliente como lidas e avisa outras abas/dispositivos
    via WebSocket para sumir o indicador de não lida em tempo real."""
    had_unread = Message.objects.filter(
        conversation=conversation, sender_type='customer', is_read=False
    ).update(is_read=True)
    if had_unread:
        from .services import _ws_send_inbox
        try:
            _ws_send_inbox({
                'type': 'messages_read',
                'conversation_id': str(conversation.id),
            })
        except Exception as _e:
            logger.warning(f"Falha ao notificar inbox (messages_read): {_e}")


def _base_ctx(request):
    """Contexto comum a todas as views do atendimento (sidebar + badges)."""
    active = conversations_visiveis(request.user).filter(
        status__in=['new', 'open', 'pending']
    ).select_related('group', 'cliente', 'assigned_to').prefetch_related('tags').annotate(
        unread_count=Count('messages', filter=Q(messages__sender_type='customer', messages__is_read=False))
    )

    open_q = active.filter(assigned_to__isnull=True, status__in=['new', 'open'])
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    from .services import build_ice_servers
    return {
        'base_tpl': 'atendimento/base_partial.html' if is_ajax else 'atendimento/base.html',
        'sidebar_conversations': open_q.order_by('-last_message_at')[:30],
        'unattended_count': open_q.count(),
        'open_count': open_q.count(),
        'mine_count': active.filter(assigned_to=request.user).count(),
        'ongoing_count': active.count(),
        'task_conv_count': active.filter(is_task_conv=True).count(),
        'ice_servers_json': json.dumps(build_ice_servers()),
    }


# ============ PÁGINAS PRINCIPAIS ============

@staff_required
def dashboard(request):
    """Dashboard principal do atendimento"""
    # Estatísticas — sempre sobre as conversas que o usuário pode ver
    # (Administrador: todas; Consultor/Operador: só a própria instância).
    convs = conversations_visiveis(request.user)
    stats = {
        'total_conversations': convs.count(),
        'open_conversations': convs.filter(status__in=['new', 'open']).count(),
        'pending_conversations': convs.filter(status='pending').count(),
        'resolved_conversations': convs.filter(status='resolved').count(),
        'total_messages': Message.objects.filter(conversation__in=convs).count(),
        'online_agents': AgentStatus.objects.filter(status='online').count(),
        'active_connections': WhatsAppConnection.objects.filter(is_active=True).count(),
    }

    # Conversas recentes
    recent_conversations = convs.select_related(
        'group', 'cliente', 'assigned_to'
    ).order_by('-last_message_at')[:10]

    # Conversas por status
    conversations_by_status = convs.values('status').annotate(count=Count('id'))

    context = {
        **_base_ctx(request),
        'stats': stats,
        'recent_conversations': recent_conversations,
        'conversations_by_status': list(conversations_by_status),
    }
    return render(request, 'atendimento/dashboard.html', context)


@staff_required
def inbox(request):
    """Caixa de entrada de conversas — carrega as 3 abas de uma vez para troca instantânea."""
    active_tab = request.GET.get('tab', 'open')
    search = request.GET.get('search', '')

    base_qs = conversations_visiveis(request.user).select_related('group', 'cliente', 'assigned_to').prefetch_related('tags').annotate(
        unread_count=Count('messages', filter=Q(messages__sender_type='customer', messages__is_read=False))
    )

    if search:
        base_qs = base_qs.filter(
            Q(group__name__icontains=search) |
            Q(cliente__nome_empresa__icontains=search) |
            Q(conversation_id__icontains=search)
        )

    mine_qs    = base_qs.filter(assigned_to=request.user, status__in=['new', 'open', 'pending']).order_by('-last_message_at')
    open_qs    = base_qs.filter(assigned_to__isnull=True, status__in=['new', 'open']).order_by('-last_message_at')
    ongoing_qs = base_qs.filter(status__in=['new', 'open', 'pending']).order_by('-last_message_at')
    task_qs    = base_qs.filter(is_task_conv=True, status__in=['new', 'open', 'pending']).order_by('-last_message_at')

    context = {
        **_base_ctx(request),
        'mine_conversations':    mine_qs,
        'open_conversations':    open_qs,
        'ongoing_conversations': ongoing_qs,
        'task_conversations':    task_qs,
        'active_tab': active_tab,
        'search': search,
    }
    resp = render(request, 'atendimento/inbox.html', context)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        resp['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp


@staff_required
def conversation_detail(request, conversation_id):
    """Detalhes de uma conversa"""
    conversation = get_object_or_404(Conversation, id=conversation_id)
    if not pode_ver_conversation(request.user, conversation):
        return HttpResponseForbidden('Conversa de outra instância.')

    # Atribui conversa ao agente se não estiver atribuída
    if not conversation.assigned_to and request.method == 'POST':
        if request.POST.get('action') == 'assign':
            old_assigned_to_id = conversation.assigned_to_id
            conversation.assigned_to = request.user
            conversation.status = 'open'
            conversation.save()
            ConversationActivity.objects.create(
                conversation=conversation,
                actor=request.user,
                action='assigned',
                new_value=request.user.get_full_name() or request.user.username
            )
            from .services import notify_reassignment
            notify_reassignment(conversation, old_assigned_to_id)

    # Mensagens
    messages = conversation.messages.select_related('sender').prefetch_related('reactions').order_by('created_at')

    # Atualiza status de leitura das mensagens do cliente e avisa outras abas/dispositivos
    _marcar_mensagens_lidas(conversation)

    # Determina em qual aba do sidebar esta conversa aparece
    if conversation.assigned_to == request.user:
        sidebar_active_tab = 'mine'
    elif conversation.assigned_to is None and conversation.status in ['new', 'open']:
        sidebar_active_tab = 'open'
    else:
        sidebar_active_tab = 'ongoing'

    # Filtra as conversas do sidebar de acordo com a aba ativa
    _qs = conversations_visiveis(request.user).select_related('group', 'cliente', 'assigned_to').prefetch_related('tags').annotate(
        unread_count=Count('messages', filter=Q(messages__sender_type='customer', messages__is_read=False))
    ).filter(
        status__in=['new', 'open', 'pending']
    )
    if sidebar_active_tab == 'mine':
        _sidebar_convs = _qs.filter(assigned_to=request.user)
    elif sidebar_active_tab == 'open':
        _sidebar_convs = _qs.filter(assigned_to__isnull=True, status__in=['new', 'open'])
    else:
        _sidebar_convs = _qs
    _sidebar_convs = _sidebar_convs.order_by('-last_message_at')[:30]

    # Tarefas vinculadas a esta conversa
    from django.contrib.auth.models import User as AuthUser
    conv_tasks = list(Task.objects.filter(
        task_conversations__conversation=conversation
    ).select_related('assigned_to').order_by('-created_at'))
    from usuario.perms import colegas_de_instancia
    agents_list = colegas_de_instancia(request.user).filter(is_staff=True).order_by('first_name', 'username')
    scheduled_count = conversation.scheduled_messages.filter(status='pending').count()

    context = {
        **_base_ctx(request),
        'conversation': conversation,
        'messages': messages,
        'group': conversation.group,
        'cliente': conversation.cliente,
        'activity': conversation.activity.all().order_by('-created_at')[:10],
        'active_conversation': conversation,
        'sidebar_active_tab': sidebar_active_tab,
        'sidebar_conversations': _sidebar_convs,
        'conv_tasks': conv_tasks,
        'agents_list': agents_list,
        'scheduled_count': scheduled_count,
    }

    return render(request, 'atendimento/conversation_detail.html', context)


# ============ CONFIGURAÇÕES ============

@admin_required
def settings_connections(request):
    """Gerenciar conexões WhatsApp"""
    connections = WhatsAppConnection.objects.all()

    context = {
        **_base_ctx(request),
        'connections': connections,
        'total_groups': groups_visiveis(request.user).count(),
    }
    return render(request, 'atendimento/settings_connections.html', context)


@admin_required
def settings_groups(request):
    """Gerenciar grupos sincronizados"""
    connection_id = request.GET.get('connection')
    search = request.GET.get('search', '')

    groups = groups_visiveis(request.user).select_related('connection', 'cliente')

    if connection_id:
        groups = groups.filter(connection_id=connection_id)

    if search:
        groups = groups.filter(
            Q(name__icontains=search) |
            Q(cliente__nome_empresa__icontains=search)
        )

    context = {
        **_base_ctx(request),
        'groups': groups,
        'connections': WhatsAppConnection.objects.all(),
        'clientes': clientes_visiveis(request.user).order_by('nome_empresa'),
    }
    return render(request, 'atendimento/settings_groups.html', context)


# ============ APIs ============

@admin_required
@require_http_methods(["POST"])
def api_create_connection(request):
    """Cria nova conexão WhatsApp"""
    try:
        data = json.loads(request.body)

        connection = WhatsAppConnection.objects.create(
            name=data.get('name'),
            evolution_url=data.get('evolution_url'),
            api_key=data.get('api_key'),
            instance_name=data.get('instance_name'),
            business_phone=data.get('business_phone', '')
        )

        return JsonResponse({
            'success': True,
            'connection_id': str(connection.id),
            'message': 'Conexão criada com sucesso'
        })
    except Exception as e:
        logger.error(f"Erro ao criar conexão: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@admin_required
@require_http_methods(["POST"])
def api_test_connection(request, connection_id):
    """Testa conexão WhatsApp"""
    try:
        connection = get_object_or_404(WhatsAppConnection, id=connection_id)
        client = EvolutionAPIClient(connection)

        success, message = client.test_connection()

        return JsonResponse({
            'success': success,
            'message': message
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@admin_required
@require_http_methods(["POST"])
def api_configure_webhook(request, connection_id):
    """Configura webhook na Evolution API para uma conexão"""
    try:
        connection = get_object_or_404(WhatsAppConnection, id=connection_id)
        data = json.loads(request.body) if request.body else {}
        webhook_url = data.get('webhookUrl', '').strip()

        if not webhook_url:
            # Monta URL padrão do webhook desta conexão
            host = request.build_absolute_uri('/')[:-1]
            webhook_url = f"{host}/atendimento/webhook/evolution/"

        client = EvolutionAPIClient(connection)
        success, message = client.configure_webhook(webhook_url)

        if success:
            connection.webhook_configured = True
            connection.save(update_fields=['webhook_configured'])

        return JsonResponse({'success': success, 'message': message, 'webhook_url': webhook_url})
    except Exception as e:
        logger.error(f"Erro ao configurar webhook: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@admin_required
@require_http_methods(["POST"])
def api_sync_groups(request, connection_id):
    """Sincroniza grupos de uma conexão específica."""
    try:
        connection = get_object_or_404(WhatsAppConnection, id=connection_id)
        result = ConversationService.sync_groups(connection)
        return JsonResponse(result)
    except Exception as e:
        logger.error(f"Erro ao sincronizar: {str(e)}")
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


@staff_required
@require_http_methods(["POST"])
def api_sync_all_connections(request):
    """Sincroniza grupos/contatos de todas as conexões ativas."""
    connections = WhatsAppConnection.objects.filter(is_active=True)
    if not connections.exists():
        return JsonResponse({'success': False, 'message': 'Nenhuma conexão ativa encontrada.'})

    total_new = total_updated = 0
    errors = []
    for conn in connections:
        try:
            result = ConversationService.sync_groups(conn)
            total_new     += result.get('created', 0)
            total_updated += result.get('updated', 0)
        except Exception as e:
            errors.append(f"{conn.name}: {str(e)}")
            logger.error(f"Erro ao sincronizar {conn.name}: {e}")

    return JsonResponse({
        'success': True,
        'message': f'{total_new} novos, {total_updated} atualizados' + (f' — {len(errors)} erro(s)' if errors else ''),
        'created': total_new,
        'updated': total_updated,
        'errors': errors,
    })


@staff_required
@require_http_methods(["POST"])
def api_send_message(request, conversation_id):
    """Envia mensagem em uma conversa"""
    try:
        conversation = get_object_or_404(Conversation, id=conversation_id)
        if not pode_ver_conversation(request.user, conversation):
            return JsonResponse({'success': False, 'error': 'Conversa de outra instância.'}, status=403)
        data = json.loads(request.body)
        message_text = data.get('message', '').strip()
        is_internal = bool(data.get('is_internal'))
        # Contatos marcados com "@" no chat: [{'nome','phone'}]
        mentions = data.get('mentions') or []

        if not message_text:
            return JsonResponse({'success': False, 'error': 'Mensagem vazia'}, status=400)

        # A auto-atribuição ("quem responde, assume") vive em
        # ConversationService.send_message, para valer também em mídia e
        # mensagem agendada. Aqui só detectamos se ela acabou de acontecer,
        # para o front trocar o cabeçalho sem recarregar.
        was_unassigned = conversation.assigned_to_id is None

        # Envia mensagem
        success, result = ConversationService.send_message(
            conversation,
            message_text,
            request.user,
            is_internal=is_internal,
            mentions=mentions,
        )

        if success:
            return JsonResponse({
                'success': True,
                'message_id': result,
                'newly_assigned': was_unassigned and conversation.assigned_to_id is not None,
                'assigned_to_name': (
                    conversation.assigned_to.get_full_name() or conversation.assigned_to.username
                ) if conversation.assigned_to else None,
            })
        else:
            return JsonResponse({'success': False, 'error': result}, status=400)

    except Exception as e:
        logger.error(f"Erro ao enviar mensagem: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@staff_required
@require_http_methods(["POST"])
def api_send_media(request, conversation_id):
    """Envia mídia (imagem, documento, áudio) em uma conversa"""
    try:
        conversation = get_object_or_404(Conversation, id=conversation_id)
        if not pode_ver_conversation(request.user, conversation):
            return JsonResponse({'success': False, 'error': 'Conversa de outra instância.'}, status=403)
        data = json.loads(request.body)

        media_base64 = data.get('mediaBase64', '').strip()
        media_type   = data.get('mediaType', 'document')   # image | audio | document | video
        file_name    = data.get('fileName', 'arquivo')
        caption      = data.get('caption', '').strip()

        if not media_base64:
            return JsonResponse({'success': False, 'error': 'Base64 vazio'}, status=400)

        was_unassigned = conversation.assigned_to_id is None

        success, result = ConversationService.send_media(
            conversation, media_base64, media_type, file_name, caption, request.user
        )
        if success:
            msg = Message.objects.get(id=result)
            return JsonResponse({
                'success': True,
                'message_id': result,
                'content': msg.content,
                'newly_assigned': was_unassigned and conversation.assigned_to_id is not None,
            })
        else:
            return JsonResponse({'success': False, 'error': result}, status=400)

    except Exception as e:
        logger.error(f"Erro ao enviar mídia: {e}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@staff_required
@require_http_methods(["GET", "POST"])
def api_schedule_message(request, conversation_id):
    """Cria (POST) ou lista pendentes (GET) mensagens agendadas de uma conversa."""
    conversation = get_object_or_404(Conversation, id=conversation_id)
    if not pode_ver_conversation(request.user, conversation):
        return JsonResponse({'success': False, 'error': 'Conversa de outra instância.'}, status=403)

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


@staff_required
@require_http_methods(["POST"])
def api_update_conversation(request, conversation_id):
    """Atualiza informações da conversa"""
    try:
        conversation = get_object_or_404(Conversation, id=conversation_id)
        if not pode_ver_conversation(request.user, conversation):
            return JsonResponse({'success': False, 'error': 'Conversa de outra instância.'}, status=403)
        data = json.loads(request.body)

        # Atualiza status
        if 'status' in data:
            novo_status = data['status']
            if novo_status in ['resolved', 'closed']:
                # Fechamento passa pelo serviço compartilhado — o mesmo
                # caminho usado pelo agente IA (fechar_chamado_ia).
                from .services import finalizar_conversa
                finalizar_conversa(
                    conversation,
                    resolution=(data.get('resolution') or '').strip() or None,
                    actor=request.user,
                    status=novo_status,
                )
            else:
                old_status = conversation.status
                conversation.status = novo_status
                if 'resolution' in data and data['resolution']:
                    conversation.resolution = data['resolution'].strip()
                conversation.save()
                ConversationActivity.objects.create(
                    conversation=conversation,
                    actor=request.user,
                    action='status_changed',
                    old_value=old_status,
                    new_value=novo_status
                )

        # Atualiza atribuição
        if 'assigned_to' in data:
            from django.contrib.auth.models import User
            old_assigned_to_id = conversation.assigned_to_id
            agent = User.objects.get(id=data['assigned_to']) if data['assigned_to'] else None
            conversation.assigned_to = agent
            conversation.save()

            ConversationActivity.objects.create(
                conversation=conversation,
                actor=request.user,
                action='assigned',
                new_value=(agent.get_full_name() or agent.username) if agent else 'Desatribuído'
            )

            from .services import notify_reassignment
            notify_reassignment(conversation, old_assigned_to_id)

        # Atualiza priority
        if 'priority' in data:
            conversation.priority = data['priority']
            # Recalcula o prazo de SLA para a nova prioridade — mantém o
            # tempo já decorrido como base (a partir da criação do chamado),
            # não reinicia a contagem do zero.
            from .services import aplicar_sla
            aplicar_sla(conversation, from_time=conversation.created_at)
            conversation.save()

        return JsonResponse({
            'success': True,
            'message': 'Conversa atualizada'
        })
    except Exception as e:
        logger.error(f"Erro ao atualizar conversa: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@staff_required
@require_http_methods(["GET"])
def api_conversation_participants(request, conversation_id):
    """Participantes do grupo do WhatsApp, para o autocomplete do "@" no chat.

    Vem da Evolution (o CRM não guarda a lista do grupo) e fica 5 min em
    cache: sem isso, cada "@" digitado viraria uma chamada HTTP externa no
    meio da conversa. `?refresh=1` força a releitura, para quando alguém
    acabou de entrar no grupo.
    """
    from django.core.cache import cache

    conversation = get_object_or_404(Conversation, id=conversation_id)
    if not pode_ver_conversation(request.user, conversation):
        return JsonResponse({'success': False, 'error': 'Conversa de outra instância.'}, status=403)
    group = conversation.group
    if not group or not group.connection or not group.jid:
        return JsonResponse({'success': True, 'participantes': []})

    from .services import completar_nomes_participantes

    chave = f'grp_participantes_{group.id}'
    refresh = bool(request.GET.get('refresh'))
    if refresh:
        # `?refresh=1` também descarta a agenda da instância: quem acabou de
        # entrar no grupo normalmente também é um contato novo.
        cache.delete(f'evo_contatos_{group.connection_id}')
    participantes = None if refresh else cache.get(chave)
    if participantes is None:
        participantes = EvolutionAPIClient(group.connection).get_group_participants_info(group.jid)
        # A Evolution devolve `name` nulo para quase todo participante; sem
        # este passo a lista do "@" fica só com telefone e não dá para saber
        # quem é quem.
        participantes = completar_nomes_participantes(group.connection, participantes)
        # Quem tem nome primeiro, em ordem alfabética; os números soltos vão
        # para o fim da lista, onde atrapalham menos.
        participantes.sort(key=lambda p: (not p.get('nome'), (p.get('nome') or '').lower()))
        # Guarda mesmo quando vem vazio (grupo sem retorno da Evolution),
        # senão toda tecla "@" tentaria de novo uma chamada que já falhou —
        # só que por bem menos tempo, para se recuperar sozinho.
        cache.set(chave, participantes, 300 if participantes else 60)

    return JsonResponse({'success': True, 'participantes': participantes})


@staff_required
@require_http_methods(["GET"])
def api_search_conversations(request):
    """Busca chamados abertos/aguardando por nome do grupo ou da empresa —
    usado para escolher o destino ao mesclar chamados duplicados."""
    q = request.GET.get('q', '').strip()
    exclude_id = request.GET.get('exclude_id', '')
    qs = conversations_visiveis(request.user).filter(
        status__in=['new', 'open', 'pending'], is_task_conv=False,
    ).select_related('group', 'cliente')
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    if q:
        qs = qs.filter(Q(group__name__icontains=q) | Q(cliente__nome_empresa__icontains=q) | Q(conversation_id__icontains=q))
    qs = qs.order_by('-last_message_at')[:15]
    return JsonResponse({'results': [
        {
            'id': str(c.id),
            'conversation_id': c.conversation_id,
            'group_name': c.group.name if c.group else '—',
            'cliente_nome': c.cliente.nome_empresa if c.cliente else '',
            'status': c.status,
        } for c in qs
    ]})


def _duracao_humana(delta):
    """timedelta → "2d 4h" / "3h 12m" / "45min". Vazio quando não há o que
    medir (nenhum chamado encerrado no filtro)."""
    if not delta:
        return ''
    total_min = int(delta.total_seconds() // 60)
    if total_min < 60:
        return f'{total_min}min'
    horas, minutos = divmod(total_min, 60)
    if horas < 24:
        return f'{horas}h {minutos}min' if minutos else f'{horas}h'
    dias, horas = divmod(horas, 24)
    return f'{dias}d {horas}h' if horas else f'{dias}d'


def _cliente_do_request(request, cliente_id):
    """Cliente da URL + checagem de acesso, para as APIs de chamado usadas
    FORA do módulo de Atendimento (aba Tarefas da página do cliente).

    Aqui não vale `staff_required`: o próprio cliente, logado no portal,
    acompanha e valida os chamados dele por essa tela. Quem manda é
    `pode_acessar_cliente` — admin, consultor/operador da instância do
    cliente, ou o usuário do portal vinculado a ele.

    Retorna (cliente, None) ou (None, JsonResponse de erro).
    """
    from usuario.perms import pode_acessar_cliente

    cliente = get_object_or_404(Cliente, pk=cliente_id)
    if not pode_acessar_cliente(request.user, cliente):
        return None, JsonResponse(
            {'success': False, 'error': 'Sem permissão para este cliente.'}, status=403)
    return cliente, None


def _conversas_do_cliente(cliente):
    """Chamados de um cliente. O vínculo pode estar na própria conversa
    (`Conversation.cliente`) ou só no grupo do WhatsApp (`group.cliente`) —
    chamados antigos, abertos antes de o grupo ser vinculado, ficaram sem
    `Conversation.cliente`. Buscar pelos dois é o que faz o histórico
    aparecer inteiro.

    'pre' é o buffer de pré-abertura (chamado que ainda não abriu) — não é
    histórico, não aparece nem na caixa de entrada.
    """
    return (Conversation.objects
            .filter(Q(cliente=cliente) | Q(group__cliente=cliente))
            .exclude(status='pre'))


@login_required
@require_http_methods(["GET"])
def api_cliente_conversations(request, cliente_id):
    """Histórico de chamados de um cliente — usado pelo botão "Listar
    Chamados" da aba Tarefas na página do cliente (`clientes/listar.html`).
    O chamado é aberto num modal ali mesmo, sem sair do CRM
    (`api_cliente_conversation_detail`).

    Filtros (todos opcionais, combináveis): `q` (protocolo, grupo, agente,
    categoria, assunto ou texto da resolução), `status`, `agente`,
    `categoria`, `date_from`/`date_to` e `date_field` — a data filtrada pode
    ser a de abertura, a da última mensagem ou a de encerramento, porque
    "chamados de julho" quer dizer coisas diferentes dependendo de quem
    pergunta (quem abriu vs. quem fechou no mês).

    Filtrar no servidor (e não na lista já carregada) é o que faz o filtro
    valer pro histórico inteiro do cliente, não só pelos primeiros 300
    chamados que couberam na tela.
    """
    from django.db.models import Avg, DurationField, ExpressionWrapper

    cliente, erro = _cliente_do_request(request, cliente_id)
    if erro:
        return erro

    base = _conversas_do_cliente(cliente).select_related('group', 'assigned_to', 'category')

    # Opções dos selects: só o que este cliente realmente tem — lista de
    # agentes/categorias do sistema inteiro aqui seria ruído.
    agentes_opts = sorted(
        {(c.assigned_to_id, c.assigned_to.get_full_name() or c.assigned_to.username)
         for c in base if c.assigned_to_id},
        key=lambda x: x[1].lower(),
    )
    categorias_opts = sorted(
        {(c.category_id, c.category.name) for c in base if c.category_id},
        key=lambda x: x[1].lower(),
    )

    qs = base
    q = (request.GET.get('q') or '').strip()
    if q:
        # "#123" e "T-123" são como o protocolo aparece na tela; no banco é só
        # o número — sem tirar o prefixo, buscar pelo que está escrito na
        # coluna Protocolo não acha nada.
        numero = q.lstrip('#').lstrip('tT').lstrip('-').strip()
        filtro = (Q(group__name__icontains=q) | Q(assigned_to__first_name__icontains=q)
                  | Q(assigned_to__username__icontains=q) | Q(category__name__icontains=q)
                  | Q(subject__icontains=q) | Q(resolution__icontains=q))
        if numero.isdigit():
            filtro |= Q(conversation_id=int(numero))
        qs = qs.filter(filtro)

    status = (request.GET.get('status') or '').strip()
    if status == 'abertos':
        qs = qs.filter(status__in=['new', 'open', 'pending'])
    elif status == 'encerrados':
        qs = qs.filter(status__in=['resolved', 'closed'])
    elif status:
        qs = qs.filter(status=status)

    agente = (request.GET.get('agente') or '').strip()
    if agente == 'sem':
        qs = qs.filter(assigned_to__isnull=True)
    elif agente.isdigit():
        qs = qs.filter(assigned_to_id=int(agente))

    categoria = (request.GET.get('categoria') or '').strip()
    if categoria == 'sem':
        qs = qs.filter(category__isnull=True)
    elif categoria.isdigit():
        qs = qs.filter(category_id=int(categoria))

    campo_data = {
        'criado': 'created_at', 'ultima': 'last_message_at', 'fechado': 'closed_at',
    }.get((request.GET.get('date_field') or 'criado').strip(), 'created_at')
    date_from = (request.GET.get('date_from') or '').strip()
    date_to = (request.GET.get('date_to') or '').strip()
    if date_from:
        qs = qs.filter(**{f'{campo_data}__date__gte': date_from})
    if date_to:
        qs = qs.filter(**{f'{campo_data}__date__lte': date_to})

    qs = qs.order_by(F('last_message_at').desc(nulls_last=True), '-created_at').distinct()

    # Resumo do que está filtrado (não do histórico todo): é o número que o
    # usuário está olhando na tela.
    total = qs.count()
    abertos = qs.filter(status__in=['new', 'open', 'pending']).count()
    encerrados = qs.filter(status__in=['resolved', 'closed']).count()
    media = (qs.filter(closed_at__isnull=False)
               .annotate(dur=ExpressionWrapper(F('closed_at') - F('created_at'),
                                               output_field=DurationField()))
               .aggregate(m=Avg('dur'))['m'])

    LIMITE = 300
    pagina = list(qs[:LIMITE])

    def _fmt(dt):
        return timezone.localtime(dt).strftime('%d/%m/%Y %H:%M') if dt else ''

    chamados = [{
        'id': str(c.id),
        'protocolo': f'T-{c.conversation_id}' if c.is_task_conv else f'#{c.conversation_id}',
        'grupo': c.group.name if c.group else '—',
        'status': c.status,
        'status_label': c.get_status_display(),
        'categoria': c.category.name if c.category else '',
        'agente': (c.assigned_to.get_full_name() or c.assigned_to.username) if c.assigned_to else '',
        'criado_em': _fmt(c.created_at),
        'ultima_msg': _fmt(c.last_message_at),
        'fechado_em': _fmt(c.closed_at),
        'resolucao': c.resolution or '',
        'url': f'/atendimento/conversation/{c.id}/',
    } for c in pagina]

    return JsonResponse({
        'success': True,
        'cliente_nome': cliente.nome_empresa,
        'total': total,
        'exibidos': len(chamados),
        'limite': LIMITE,
        'resumo': {
            'total': total,
            'abertos': abertos,
            'encerrados': encerrados,
            'tempo_medio': _duracao_humana(media),
        },
        'opcoes': {
            'status': [{'valor': v, 'label': l} for v, l in Conversation.STATUS_CHOICES if v != 'pre'],
            'agentes': [{'id': i, 'nome': n} for i, n in agentes_opts],
            'categorias': [{'id': i, 'nome': n} for i, n in categorias_opts],
        },
        'chamados': chamados,
    })


@login_required
@require_http_methods(["GET"])
def api_cliente_conversation_detail(request, cliente_id, conversation_id):
    """Um chamado do cliente (cabeçalho + mensagens) para o modal da aba
    Tarefas — o chamado abre dentro do CRM, sem mandar ninguém pro módulo de
    Atendimento.

    Somente leitura. A conversa precisa ser mesmo daquele cliente (senão
    qualquer id de chamado viraria uma porta de entrada pro histórico de
    outro cliente), e **nota interna não sai para quem não é staff**: é
    conversa da equipe sobre o chamado, não algo que o cliente deva ler.
    """
    cliente, erro = _cliente_do_request(request, cliente_id)
    if erro:
        return erro

    conv = get_object_or_404(
        _conversas_do_cliente(cliente).select_related('group', 'assigned_to', 'category'),
        id=conversation_id,
    )

    msgs = Message.objects.filter(conversation=conv).order_by('created_at').select_related('sender')
    if not request.user.is_staff:
        msgs = msgs.exclude(Q(is_internal=True) | Q(sender_type='internal'))

    def _fmt(dt):
        return timezone.localtime(dt).strftime('%d/%m/%Y %H:%M') if dt else ''

    return JsonResponse({
        'success': True,
        'chamado': {
            'id': str(conv.id),
            'protocolo': f'T-{conv.conversation_id}' if conv.is_task_conv else f'#{conv.conversation_id}',
            'grupo': conv.group.name if conv.group else '—',
            'assunto': conv.subject or conv.title or '',
            'status': conv.status,
            'status_label': conv.get_status_display(),
            'prioridade_label': conv.get_priority_display(),
            'categoria': conv.category.name if conv.category else '',
            'agente': (conv.assigned_to.get_full_name() or conv.assigned_to.username) if conv.assigned_to else '',
            'criado_em': _fmt(conv.created_at),
            'fechado_em': _fmt(conv.closed_at),
            'resolucao': conv.resolution or '',
        },
        'mensagens': [{
            'id': str(m.id),
            'sender_type': m.sender_type,
            'sender_name': m.sender_name or (m.sender.get_full_name() or m.sender.username if m.sender else ''),
            'content': m.content,
            'message_type': m.message_type,
            'attachment_url': m.attachment_url or '',
            'is_internal': m.is_internal or m.sender_type == 'internal',
            'data': timezone.localtime(m.created_at).strftime('%d/%m/%Y'),
            'hora': timezone.localtime(m.created_at).strftime('%H:%M'),
        } for m in msgs[:1000]],
    })


@staff_required
@require_http_methods(["POST"])
def api_merge_conversation(request, conversation_id):
    """Mescla esta conversa (origem, duplicada) em outra (destino): move
    todas as mensagens para o destino e fecha a origem apontando pra lá
    (merged_into) — o histórico não se perde, só some da caixa de entrada."""
    try:
        source = get_object_or_404(Conversation, id=conversation_id)
        if not pode_ver_conversation(request.user, source):
            return JsonResponse({'success': False, 'error': 'Conversa de outra instância.'}, status=403)
        data = json.loads(request.body)
        target_id = data.get('target_id')
        if not target_id:
            return JsonResponse({'success': False, 'error': 'target_id obrigatório'}, status=400)
        if str(target_id) == str(source.id):
            return JsonResponse({'success': False, 'error': 'Não é possível mesclar um chamado com ele mesmo'}, status=400)
        target = get_object_or_404(Conversation, id=target_id)
        if not pode_ver_conversation(request.user, target):
            return JsonResponse({'success': False, 'error': 'Conversa de outra instância.'}, status=403)

        moved = Message.objects.filter(conversation=source).update(conversation=target)

        now = timezone.now()
        source.merged_into = target
        source.status = 'closed'
        source.closed_at = now
        source.resolution = ((source.resolution or '') + f"\n\nMesclado com o chamado #{target.conversation_id}.").strip()
        source.save(update_fields=['merged_into', 'status', 'closed_at', 'resolution'])

        target.last_message_at = now
        target.save(update_fields=['last_message_at'])

        ConversationActivity.objects.create(
            conversation=source, actor=request.user, action='closed',
            description=f'Mesclado com o chamado #{target.conversation_id}',
        )
        ConversationActivity.objects.create(
            conversation=target, actor=request.user, action='note_added',
            description=f'Recebeu {moved} mensagem(ns) mescladas do chamado #{source.conversation_id}',
        )

        from .services import _ws_send_inbox
        _ws_send_inbox({
            'type': 'conversation_status',
            'conversation_id': str(source.id),
            'status': source.status,
            'assigned_to_id': source.assigned_to_id,
        })

        return JsonResponse({'success': True, 'moved': moved, 'target_id': str(target.id)})
    except Exception as e:
        logger.error(f"Erro ao mesclar conversa: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@staff_required
@require_http_methods(["POST"])
def api_link_group(request, group_id):
    """Vincula grupo a cliente"""
    try:
        # Vincular exige poder ver os DOIS lados: o grupo e o cliente. Sem
        # isso um Consultor puxava um grupo de outra instância pra si (ou
        # jogava o próprio grupo pra um cliente alheio).
        group = get_object_or_404(ContactGroup, id=group_id)
        if not pode_ver_group(request.user, group):
            return JsonResponse({'success': False, 'error': 'Grupo de outra instância.'}, status=403)
        data = json.loads(request.body)
        cliente_id = data.get('cliente_id')

        if cliente_id:
            cliente = clientes_visiveis(request.user).filter(id=cliente_id).first()
            if cliente is None:
                return JsonResponse({'success': False, 'error': 'Cliente inválido para o seu escopo.'}, status=403)
            group.cliente = cliente
            group.save()

            return JsonResponse({'success': True})
        else:
            group.cliente = None
            group.save()
            return JsonResponse({'success': True})
    except Exception as e:
        logger.error(f"Erro ao vincular grupo: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


# ============ WEBHOOK ============

@csrf_exempt
@require_http_methods(["POST"])
def webhook_evolution(request):
    """Webhook para receber mensagens da Evolution API"""
    try:
        data = json.loads(request.body)
        logger.info(f"Webhook recebido: {data}")

        result = ConversationService.process_webhook(data)

        return JsonResponse(result)
    except Exception as e:
        logger.error(f"Erro no webhook: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


# ============ NEW IMPORTS (appended) ============
from django.contrib.auth.models import User
from .models import (
    Company, Category, Tag, QuickMessage, ChatFlow,
    KanbanBoard, KanbanColumn, KanbanCard,
    UserGroupPermission, SystemSetting,
)


# ============ CONFIGURAÇÕES (ADMIN) ============

@admin_required
def configuracoes(request):
    """Página de configurações avançadas"""
    from atendimento.models import UserGroupPermission
    import json as _json

    groups = list(ContactGroup.objects.select_related('connection').order_by('name'))
    users  = list(User.objects.filter(is_active=True).order_by('first_name', 'username'))

    # Busca todas as permissões em UMA query e monta o mapa {user_id: [group_id, ...]}
    perm_rows = UserGroupPermission.objects.filter(
        user__in=users
    ).values_list('user_id', 'group_id')
    perm_map = {}
    for uid, gid in perm_rows:
        perm_map.setdefault(uid, set()).add(gid)
    # Serializa como JSON para o template (sets não são serializáveis diretamente)
    perm_map_json = _json.dumps({str(k): list(v) for k, v in perm_map.items()})

    # Coleta settings em uma única query
    setting_keys = ['ai_provider','ai_api_key','ai_model','ai_system_prompt',
                    'ai_openai_api_key','ai_openai_model',
                    'ai_last_error','ai_last_error_at',
                    'daily_alert_enabled','daily_alert_time','daily_alert_group',
                    'notif_abertos_enabled','notif_abertos_group_id','msg_encerramento',
                    'reminder_morning_time','reminder_noon_time',
                    'escalacao_enabled','escalacao_group_id','escalacao_reassign_user_id']
    settings_qs = {s.key: s.value for s in SystemSetting.objects.filter(key__in=setting_keys)}

    connections = list(WhatsAppConnection.objects.filter(is_active=True).order_by('name'))
    _ac_map = {ac.user_id: ac for ac in AttendantContact.objects.select_related('connection').all()}
    # Attach contact to each user object for easy template access (no leading underscore — Django blocks those)
    for u in users:
        u.attendant_contact = _ac_map.get(u.id)
    attendant_contacts = _ac_map

    context = {
        **_base_ctx(request),
        'tags': Tag.objects.all(),
        'categories': Category.objects.all(),
        'quick_messages': QuickMessage.objects.all(),
        'groups': groups,
        'users': users,
        'connections': connections,
        'attendant_contacts': attendant_contacts,
        'perm_map_json': perm_map_json,
        'ai_provider': settings_qs.get('ai_provider', 'claude'),
        'ai_key': settings_qs.get('ai_api_key', ''),
        'ai_model': settings_qs.get('ai_model', 'claude-sonnet-4-6'),
        'ai_openai_key': settings_qs.get('ai_openai_api_key', ''),
        'ai_openai_model': settings_qs.get('ai_openai_model', 'gpt-4o'),
        'ai_prompt': settings_qs.get('ai_system_prompt', ''),
        # Último erro da IA (gravado por atendimento/ai.py) — é o que avisa que
        # as automações estão saindo no fallback em vez de escritas pela IA.
        'ai_last_error': settings_qs.get('ai_last_error', ''),
        'ai_last_error_at': settings_qs.get('ai_last_error_at', ''),
        'daily_alert_enabled': settings_qs.get('daily_alert_enabled', 'false'),
        'daily_alert_time': settings_qs.get('daily_alert_time', '08:00'),
        'daily_alert_group': settings_qs.get('daily_alert_group', ''),
        'notif_abertos_enabled': settings_qs.get('notif_abertos_enabled', 'false'),
        'notif_abertos_group_id': settings_qs.get('notif_abertos_group_id', ''),
        'msg_encerramento': settings_qs.get('msg_encerramento', ''),
        'reminder_morning_time': settings_qs.get('reminder_morning_time', '08:00'),
        'reminder_noon_time': settings_qs.get('reminder_noon_time', '12:00'),
        'escalacao_enabled': settings_qs.get('escalacao_enabled', 'false'),
        'escalacao_group_id': settings_qs.get('escalacao_group_id', ''),
        'escalacao_reassign_user_id': settings_qs.get('escalacao_reassign_user_id', ''),
    }
    return render(request, 'atendimento/configuracoes.html', context)


# ─── Tags ───

@staff_required
@login_required
@require_http_methods(["POST", "DELETE"])
def api_conversation_tags(request, conversation_id, tag_id=None):
    """Adiciona (POST) ou remove (DELETE) uma tag de uma conversa."""
    conversation = get_object_or_404(Conversation, id=conversation_id)
    if not pode_ver_conversation(request.user, conversation):
        return JsonResponse({'success': False, 'error': 'Conversa de outra instância.'}, status=403)
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            tag_id = data.get('tag_id')
            if not tag_id:
                return JsonResponse({'error': 'tag_id obrigatorio'}, status=400)
            tag = get_object_or_404(Tag, id=tag_id)
            conversation.tags.add(tag)
            return JsonResponse({'success': True, 'tag': {'id': str(tag.id), 'name': tag.name, 'color': tag.color}})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    if request.method == 'DELETE':
        if not tag_id:
            return JsonResponse({'error': 'tag_id obrigatorio'}, status=400)
        tag = get_object_or_404(Tag, id=tag_id)
        conversation.tags.remove(tag)
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
def api_tags_list(request):
    if request.method == 'GET':
        tags = list(Tag.objects.values('id', 'name', 'color'))
        return JsonResponse({'tags': tags})
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            tag = Tag.objects.create(name=data['name'], color=data.get('color', '#3B82F6'))
            return JsonResponse({'id': str(tag.id), 'name': tag.name, 'color': tag.color})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
def api_tag_detail(request, tag_id):
    tag = get_object_or_404(Tag, id=tag_id)
    if request.method == 'PUT':
        try:
            data = json.loads(request.body)
            tag.name = data.get('name', tag.name)
            tag.color = data.get('color', tag.color)
            tag.save()
            return JsonResponse({'id': str(tag.id), 'name': tag.name, 'color': tag.color})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    if request.method == 'DELETE':
        tag.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


# ─── Categories ───

@staff_required
def api_categories_list(request):
    if request.method == 'GET':
        cats = list(Category.objects.values('id', 'name', 'color'))
        return JsonResponse({'categories': cats})
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            cat = Category.objects.create(name=data['name'], color=data.get('color', '#7c3aed'))
            return JsonResponse({'id': str(cat.id), 'name': cat.name, 'color': cat.color})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
def api_category_detail(request, category_id):
    cat = get_object_or_404(Category, id=category_id)
    if request.method == 'PUT':
        try:
            data = json.loads(request.body)
            cat.name = data.get('name', cat.name)
            cat.color = data.get('color', cat.color)
            cat.save()
            return JsonResponse({'id': str(cat.id), 'name': cat.name, 'color': cat.color})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    if request.method == 'DELETE':
        cat.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


# ─── Quick Messages ───

@staff_required
def api_quick_messages_list(request):
    if request.method == 'GET':
        msgs = list(QuickMessage.objects.values('id', 'title', 'body'))
        return JsonResponse({'quick_messages': msgs})
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            msg = QuickMessage.objects.create(title=data['title'], body=data['body'])
            return JsonResponse({'id': str(msg.id), 'title': msg.title, 'body': msg.body})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
def api_quick_message_detail(request, msg_id):
    msg = get_object_or_404(QuickMessage, id=msg_id)
    if request.method == 'PUT':
        try:
            data = json.loads(request.body)
            msg.title = data.get('title', msg.title)
            msg.body = data.get('body', msg.body)
            msg.save()
            return JsonResponse({'id': str(msg.id), 'title': msg.title, 'body': msg.body})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    if request.method == 'DELETE':
        msg.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


# ─── Settings ───

@admin_required
def api_settings(request):
    if request.method == 'GET':
        keys = ['ai_provider', 'ai_api_key', 'ai_model', 'ai_system_prompt',
                'ai_openai_api_key', 'ai_openai_model',
                'daily_alert_enabled', 'daily_alert_time', 'daily_alert_group']
        data = {}
        for k in keys:
            data[k] = SystemSetting.get(k, '')
        # Mask secrets
        if data.get('ai_api_key'):
            data['ai_api_key_masked'] = '•' * 20
        if data.get('ai_openai_api_key'):
            data['ai_openai_api_key_masked'] = '•' * 20
        return JsonResponse(data)
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            secret_keys = {'ai_api_key', 'ai_openai_api_key'}
            for k, v in data.items():
                if k.startswith('_'):
                    continue
                SystemSetting.set(k, v, is_secret=(k in secret_keys))
            # Trocou chave/provedor da IA: o erro antigo não vale mais e o
            # aviso na tela ficaria pendurado até a próxima falha.
            if any(k.startswith('ai_') for k in data):
                from atendimento.ai import AI_ERRO_KEY, AI_ERRO_AT_KEY
                SystemSetting.set(AI_ERRO_KEY, '')
                SystemSetting.set(AI_ERRO_AT_KEY, '')
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Method not allowed'}, status=405)


# ─── Permissions ───

@admin_required
def api_permissions(request):
    if request.method == 'GET':
        users = User.objects.filter(is_active=True).prefetch_related('group_permissions')
        groups = ContactGroup.objects.select_related('connection').order_by('name')
        result = []
        for u in users:
            permitted_ids = set(
                str(p.group_id) for p in u.group_permissions.all()
            )
            result.append({
                'user_id': u.id,
                'username': u.username,
                'full_name': u.get_full_name() or u.username,
                'is_staff': u.is_staff,
                'permitted_group_ids': list(permitted_ids),
            })
        groups_data = [
            {'id': g.id, 'name': g.name, 'connection': g.connection.name}
            for g in groups
        ]
        return JsonResponse({'users': result, 'groups': groups_data})
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            user = get_object_or_404(User, id=data['user_id'])
            group = get_object_or_404(ContactGroup, id=data['group_id'])
            perm, created = UserGroupPermission.objects.get_or_create(user=user, group=group)
            if not created:
                perm.delete()
                return JsonResponse({'action': 'removed'})
            return JsonResponse({'action': 'added'})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Method not allowed'}, status=405)


# ============ EMPRESAS ============

@staff_required
def empresas(request):
    from clientes.models import Cliente as CRMCliente
    search = request.GET.get('search', '').strip()

    qs = clientes_visiveis(request.user).annotate(
        group_count=Count('contactgroup', distinct=True),
        conv_count=Count('conversation', distinct=True),
    ).order_by('nome_empresa')

    if search:
        qs = qs.filter(
            Q(nome_empresa__icontains=search) |
            Q(cnpj__icontains=search) |
            Q(email__icontains=search)
        )

    context = {
        **_base_ctx(request),
        'clientes': qs,
        'total': qs.count(),
        'search': search,
    }
    return render(request, 'atendimento/empresas.html', context)


@staff_required
def api_empresas_list(request):
    if request.method == 'GET':
        companies = list(Company.objects.annotate(
            group_count=Count('groups')
        ).values('id', 'name', 'cnpj', 'email', 'phone', 'notes', 'group_count'))
        return JsonResponse({'companies': [
            {**c, 'id': str(c['id'])} for c in companies
        ]})
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            company = Company.objects.create(
                name=data['name'],
                cnpj=data.get('cnpj', ''),
                email=data.get('email', ''),
                phone=data.get('phone', ''),
                notes=data.get('notes', ''),
            )
            return JsonResponse({
                'id': str(company.id), 'name': company.name,
                'cnpj': company.cnpj, 'email': company.email,
                'phone': company.phone,
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
def api_empresa_detail(request, empresa_id):
    company = get_object_or_404(Company, id=empresa_id)
    if request.method == 'GET':
        return JsonResponse({
            'id': str(company.id), 'name': company.name,
            'cnpj': company.cnpj or '', 'email': company.email or '',
            'phone': company.phone or '', 'notes': company.notes or '',
        })
    if request.method == 'PUT':
        try:
            data = json.loads(request.body)
            company.name = data.get('name', company.name)
            company.cnpj = data.get('cnpj', company.cnpj)
            company.email = data.get('email', company.email)
            company.phone = data.get('phone', company.phone)
            company.notes = data.get('notes', company.notes)
            company.save()
            return JsonResponse({'success': True, 'name': company.name})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    if request.method == 'DELETE':
        company.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


# ============ KANBAN ============

@staff_required
@login_required
def kanban(request):
    from usuario.perms import colegas_de_instancia
    boards = KanbanBoard.objects.all()
    users = colegas_de_instancia(request.user)
    context = {
        **_base_ctx(request),
        'boards': boards,
        'users': users,
    }
    return render(request, 'atendimento/kanban.html', context)


@staff_required
@login_required
def api_kanban_boards(request):
    if request.method == 'GET':
        boards = []
        for b in KanbanBoard.objects.prefetch_related('columns__cards'):
            columns = []
            for col in b.columns.all():
                cards = []
                for card in col.cards.all():
                    cards.append({
                        'id': str(card.id),
                        'column_id': str(col.id),
                        'title': card.title,
                        'description': card.description or '',
                        'position': card.position,
                        'conversation_id': str(card.conversation_id) if card.conversation_id else None,
                        'assignee_id': card.assignee_id,
                        'assignee_name': card.assignee.get_full_name() if card.assignee else None,
                        'due_date': card.due_date.isoformat() if card.due_date else None,
                    })
                columns.append({
                    'id': str(col.id),
                    'name': col.name,
                    'position': col.position,
                    'color': col.color,
                    'cards': cards,
                })
            boards.append({'id': str(b.id), 'name': b.name, 'columns': columns})
        return JsonResponse({'boards': boards})
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            board = KanbanBoard.objects.create(name=data['name'])
            return JsonResponse({'id': str(board.id), 'name': board.name})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
@login_required
def api_kanban_board_detail(request, board_id):
    board = get_object_or_404(KanbanBoard, id=board_id)
    if request.method == 'PUT':
        try:
            data = json.loads(request.body)
            board.name = data.get('name', board.name)
            board.save()
            return JsonResponse({'success': True, 'name': board.name})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    if request.method == 'DELETE':
        board.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
@login_required
def api_kanban_columns(request, board_id):
    board = get_object_or_404(KanbanBoard, id=board_id)
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            last_pos = KanbanColumn.objects.filter(board=board).count()
            col = KanbanColumn.objects.create(
                board=board,
                name=data['name'],
                position=last_pos,
                color=data.get('color', '#7c3aed'),
            )
            return JsonResponse({'id': str(col.id), 'name': col.name, 'color': col.color, 'position': col.position})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
@login_required
def api_kanban_column_detail(request, board_id, column_id):
    col = get_object_or_404(KanbanColumn, id=column_id, board_id=board_id)
    if request.method == 'PUT':
        try:
            data = json.loads(request.body)
            col.name = data.get('name', col.name)
            col.color = data.get('color', col.color)
            col.save()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    if request.method == 'DELETE':
        col.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
@login_required
def api_kanban_cards(request, board_id, column_id):
    col = get_object_or_404(KanbanColumn, id=column_id, board_id=board_id)
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            last_pos = KanbanCard.objects.filter(column=col).count()
            assignee = None
            if data.get('assignee_id'):
                assignee = User.objects.filter(id=data['assignee_id']).first()
            due_date = None
            if data.get('due_date'):
                from django.utils.dateparse import parse_datetime, parse_date
                due_date = parse_datetime(data['due_date']) or parse_date(data['due_date'])
            card = KanbanCard.objects.create(
                column=col,
                title=data['title'],
                description=data.get('description', ''),
                position=last_pos,
                assignee=assignee,
                due_date=due_date,
            )
            return JsonResponse({'id': str(card.id), 'title': card.title})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
@login_required
def api_kanban_card_detail(request, board_id, column_id, card_id):
    card = get_object_or_404(KanbanCard, id=card_id, column_id=column_id, column__board_id=board_id)
    if request.method == 'PUT':
        try:
            data = json.loads(request.body)
            card.title = data.get('title', card.title)
            card.description = data.get('description', card.description)
            if 'assignee_id' in data:
                card.assignee = User.objects.filter(id=data['assignee_id']).first() if data['assignee_id'] else None
            if 'due_date' in data:
                card.due_date = data['due_date'] or None
            card.save()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    if request.method == 'DELETE':
        card.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
@login_required
@require_http_methods(["POST"])
def api_kanban_move_card(request, card_id):
    card = get_object_or_404(KanbanCard, id=card_id)
    try:
        data = json.loads(request.body)
        new_col = get_object_or_404(KanbanColumn, id=data['column_id'])
        card.column = new_col
        card.position = data.get('position', 0)
        card.save()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


# ============ AUTO ATENDIMENTO ============

@staff_required
def auto_atendimento(request):
    flows = ChatFlow.objects.all()
    groups = groups_visiveis(request.user).select_related('connection').order_by('name')

    # Quais grupos recebem auto atendimento de fato hoje (mesma lógica de
    # ConversationService._flow_do_grupo): um fluxo ativo com group_ids vazio
    # vale como universal e pega todo mundo que não estiver excluído.
    active_flows = [f for f in flows if f.active]
    explicit_group_ids = set()
    has_universal_flow = False
    for f in active_flows:
        if f.group_ids:
            explicit_group_ids.update(str(x) for x in f.group_ids)
        else:
            has_universal_flow = True

    groups_ativos, groups_removidos = [], []
    for g in groups:
        if g.auto_atendimento_excluido:
            groups_removidos.append(g)
        elif has_universal_flow or str(g.id) in explicit_group_ids:
            groups_ativos.append(g)

    context = {
        **_base_ctx(request),
        'flows': flows,
        'groups': groups,
        'groups_ativos': groups_ativos,
        'groups_removidos': groups_removidos,
        'has_universal_flow': has_universal_flow,
    }
    return render(request, 'atendimento/auto_atendimento.html', context)


@staff_required
def api_chat_flows_list(request):
    if request.method == 'GET':
        flows = []
        for f in ChatFlow.objects.all():
            flows.append({
                'id': str(f.id), 'name': f.name, 'active': f.active,
                'group_ids': f.group_ids or [],
                'greeting_message': f.greeting_message,
                'subject_question': f.subject_question,
                'category_question': f.category_question,
                'categories': f.categories or [],
                'completion_message': f.completion_message or '',
            })
        return JsonResponse({'flows': flows})
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            flow = ChatFlow.objects.create(
                name=data['name'],
                greeting_message=data.get('greeting_message', ''),
                subject_question=data.get('subject_question', 'Qual é o assunto?'),
                category_question=data.get('category_question', 'Qual categoria?'),
                categories=data.get('categories', []),
                group_ids=data.get('group_ids', []),
                completion_message=data.get('completion_message', ''),
                active=data.get('active', False),
            )
            return JsonResponse({'id': str(flow.id), 'name': flow.name})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
def api_chat_flow_detail(request, flow_id):
    flow = get_object_or_404(ChatFlow, id=flow_id)
    if request.method == 'GET':
        return JsonResponse({
            'id': str(flow.id), 'name': flow.name, 'active': flow.active,
            'group_ids': flow.group_ids or [],
            'greeting_message': flow.greeting_message,
            'subject_question': flow.subject_question,
            'category_question': flow.category_question,
            'categories': flow.categories or [],
            'completion_message': flow.completion_message or '',
        })
    if request.method == 'PUT':
        try:
            data = json.loads(request.body)
            flow.name = data.get('name', flow.name)
            flow.greeting_message = data.get('greeting_message', flow.greeting_message)
            flow.subject_question = data.get('subject_question', flow.subject_question)
            flow.category_question = data.get('category_question', flow.category_question)
            flow.categories = data.get('categories', flow.categories)
            flow.group_ids = data.get('group_ids', flow.group_ids)
            flow.completion_message = data.get('completion_message', flow.completion_message)
            flow.active = data.get('active', flow.active)
            flow.save()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    if request.method == 'DELETE':
        flow.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


# ============ RELATÓRIOS ============

@staff_required
def relatorio_pdf(request):
    """Gera PDF do relatório de atendimentos por empresa, mês e ano."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer, HRFlowable, KeepTogether)
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
    from django.http import HttpResponse
    from clientes.models import Cliente as CRMCliente
    import io, datetime as _dt

    cliente_id = request.GET.get('cliente_id', '').strip()
    mes        = request.GET.get('mes', '').strip()
    ano        = request.GET.get('ano', '').strip()

    if not cliente_id:
        return HttpResponse('Selecione uma empresa para gerar o relatório.', status=400)

    cliente = get_object_or_404(clientes_visiveis(request.user), id=cliente_id)

    qs = Conversation.objects.filter(
        Q(cliente=cliente) | Q(group__cliente=cliente),
        status__in=['resolved', 'closed'],
        closed_at__isnull=False,
    ).select_related('group', 'assigned_to', 'category').order_by('closed_at')


    if ano:
        qs = qs.filter(closed_at__year=int(ano))
    if mes:
        qs = qs.filter(closed_at__month=int(mes))

    MESES = ['','Janeiro','Fevereiro','Março','Abril','Maio','Junho',
             'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
    if mes and ano:
        periodo = f'{MESES[int(mes)]} de {ano}'
    elif ano:
        periodo = f'Ano {ano}'
    elif mes:
        periodo = MESES[int(mes)]
    else:
        periodo = 'Todos os períodos'

    def fmt_duracao(segundos):
        if segundos is None or segundos < 0:
            return '—'
        h = int(segundos // 3600)
        m = int((segundos % 3600) // 60)
        if h > 0:
            return f'{h}h {m:02d}min'
        return f'{m}min'

    # Pré-calcula duração de cada conversa
    conversas = []
    total_seg = 0
    for conv in qs:
        duracao_seg = None
        if conv.closed_at and conv.created_at:
            duracao_seg = (conv.closed_at - conv.created_at).total_seconds()
            total_seg += duracao_seg

        resolucao = conv.resolution or ''
        if not resolucao:
            act = conv.activity.filter(
                action='status_changed',
                new_value__in=['resolved', 'closed']
            ).order_by('-created_at').first()
            if act:
                resolucao = act.new_value  # ex: "resolved"
        resolucao = resolucao or '—'

        conversas.append({
            'conv': conv,
            'duracao_seg': duracao_seg,
            'resolucao': resolucao,
        })

    # ── Monta PDF ────────────────────────────────────────────────
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2.2*cm, bottomMargin=2*cm)

    C_DARK   = colors.HexColor('#04060e')
    C_CYAN   = colors.HexColor('#00c8d8')
    C_CYAN2  = colors.HexColor('#00f5ff')
    C_GREY   = colors.HexColor('#555555')
    C_LGREY  = colors.HexColor('#f5f5f5')
    C_WHITE  = colors.white
    C_BORDER = colors.HexColor('#d0d0d0')

    st = getSampleStyleSheet()
    def ps(name, **kw):
        return ParagraphStyle(name, parent=st['Normal'], **kw)

    title_s  = ps('tt', fontSize=17, textColor=C_DARK, fontName='Helvetica-Bold',
                  spaceAfter=2, alignment=TA_LEFT)
    sub_s    = ps('ss', fontSize=10, textColor=C_GREY, spaceAfter=2, alignment=TA_LEFT)
    head_s   = ps('hs', fontSize=8, fontName='Helvetica-Bold',
                  textColor=C_WHITE, leading=11)
    cell_s   = ps('cs', fontSize=8, textColor=C_DARK, leading=11)
    cell_sm  = ps('csm', fontSize=7, textColor=C_GREY, leading=10)
    right_s  = ps('rs', fontSize=9, textColor=C_DARK, alignment=TA_RIGHT)
    footer_s = ps('fs', fontSize=7, textColor=C_GREY, alignment=TA_CENTER)
    summ_s   = ps('su', fontSize=10, fontName='Helvetica-Bold', textColor=C_DARK)
    summ_v   = ps('sv', fontSize=14, fontName='Helvetica-Bold', textColor=C_CYAN)

    story = []

    # ── Cabeçalho do documento ───────────────────────────────────
    hdr_data = [[
        Paragraph('<b>TOMICH HUB</b>', ps('bh', fontSize=13, fontName='Helvetica-Bold',
                                          textColor=C_CYAN2)),
        Paragraph('Relatório de Atendimentos', ps('rh', fontSize=11, textColor=C_DARK,
                                                   alignment=TA_RIGHT)),
    ]]
    hdr_tbl = Table(hdr_data, colWidths=[9*cm, 8.27*cm])
    hdr_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(hdr_tbl)
    story.append(HRFlowable(width='100%', thickness=1.5, color=C_CYAN, spaceAfter=10))

    # ── Dados da empresa ─────────────────────────────────────────
    story.append(Paragraph(f'<b>{cliente.nome_empresa}</b>', title_s))
    story.append(Paragraph(f'CNPJ: {cliente.cnpj or "—"}   |   Período: {periodo}', sub_s))
    story.append(Spacer(1, 0.4*cm))

    # ── Cards de resumo ──────────────────────────────────────────
    total_chamados = len(conversas)
    total_horas    = fmt_duracao(total_seg)

    summ_data = [
        [
            [Paragraph('Total de Chamados', summ_s), Paragraph(str(total_chamados), summ_v)],
            [Paragraph('Horas Gastas', summ_s), Paragraph(total_horas, summ_v)],
            [Paragraph('Período', summ_s), Paragraph(periodo, ps('pv', fontSize=12,
                        fontName='Helvetica-Bold', textColor=C_CYAN))],
        ]
    ]
    summ_tbl = Table(summ_data, colWidths=[5.76*cm, 5.76*cm, 5.76*cm])
    summ_tbl.setStyle(TableStyle([
        ('BOX',          (0,0), (-1,-1), 0.5, C_CYAN),
        ('INNERGRID',    (0,0), (-1,-1), 0.5, C_BORDER),
        ('BACKGROUND',   (0,0), (-1,-1), colors.HexColor('#f0fffe')),
        ('VALIGN',       (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING',   (0,0), (-1,-1), 10),
        ('BOTTOMPADDING',(0,0), (-1,-1), 10),
        ('LEFTPADDING',  (0,0), (-1,-1), 12),
    ]))
    story.append(summ_tbl)
    story.append(Spacer(1, 0.5*cm))

    # ── Tabela de chamados ───────────────────────────────────────
    if not conversas:
        story.append(Paragraph(
            'Nenhum chamado resolvido/encerrado encontrado para o período.',
            ps('em', fontSize=10, textColor=C_GREY, alignment=TA_CENTER)
        ))
    else:
        story.append(Paragraph('Detalhamento dos Chamados',
                               ps('sec', fontSize=11, fontName='Helvetica-Bold',
                                  textColor=C_DARK, spaceAfter=8)))

        # Colunas: # | Grupo | Assunto | Categoria | Atendente | Abertura | Fechamento | Duração
        header_row = [
            Paragraph('#', head_s),
            Paragraph('Grupo', head_s),
            Paragraph('Assunto', head_s),
            Paragraph('Categoria', head_s),
            Paragraph('Atendente', head_s),
            Paragraph('Abertura', head_s),
            Paragraph('Fechamento', head_s),
            Paragraph('Duração', head_s),
        ]

        C_CAT = colors.HexColor('#7c3aed')

        rows = [header_row]
        alt = False
        for item in conversas:
            conv    = item['conv']
            alt     = not alt
            grupo   = conv.group.name or f'#{conv.conversation_id}'
            assunto = (conv.subject or conv.title or '—')[:80]
            cat_name = conv.category.name if conv.category else '—'
            cat_color = colors.HexColor(conv.category.color) if conv.category else C_GREY
            agente  = conv.assigned_to.get_full_name() if conv.assigned_to else '—'
            abertura = conv.created_at.strftime('%d/%m/%Y\n%H:%M') if conv.created_at else '—'
            fechado  = conv.closed_at.strftime('%d/%m/%Y\n%H:%M') if conv.closed_at else '—'
            dur      = fmt_duracao(item['duracao_seg'])

            bg = C_LGREY if alt else C_WHITE
            rows.append([
                Paragraph(f'#{conv.conversation_id}', cell_sm),
                Paragraph(grupo[:35], cell_s),
                Paragraph(assunto, ps('assunto', fontSize=7, textColor=C_DARK,
                                      leading=10, wordWrap='LTR')),
                Paragraph(cat_name[:22], ps('cat', fontSize=7, fontName='Helvetica-Bold',
                                            textColor=cat_color, leading=10)),
                Paragraph(agente[:20], cell_sm),
                Paragraph(abertura, cell_sm),
                Paragraph(fechado, cell_sm),
                Paragraph(f'<b>{dur}</b>', ps('dur', fontSize=8, fontName='Helvetica-Bold',
                                              textColor=C_CYAN)),
            ])

        # A4 útil = 17.27cm (21 - 2 - 2)
        col_widths = [1.3*cm, 3.3*cm, 4.0*cm, 2.2*cm, 2.4*cm, 1.9*cm, 1.9*cm, 1.77*cm]
        tbl = Table(rows, colWidths=col_widths, repeatRows=1)

        row_bgs = [('BACKGROUND', (0, i+1), (-1, i+1),
                    C_LGREY if (i % 2 == 0) else C_WHITE)
                   for i in range(len(conversas))]
        tbl.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,0), C_DARK),
            ('TEXTCOLOR',     (0,0), (-1,0), C_WHITE),
            ('GRID',          (0,0), (-1,-1), 0.3, C_BORDER),
            ('VALIGN',        (0,0), (-1,-1), 'TOP'),
            ('TOPPADDING',    (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING',   (0,0), (-1,-1), 4),
            ('RIGHTPADDING',  (0,0), (-1,-1), 4),
        ] + row_bgs))
        story.append(tbl)

        # Linha de total
        story.append(Spacer(1, 0.3*cm))
        total_row = Table([[
            Paragraph(f'Total: <b>{total_chamados} chamados</b>', right_s),
            Paragraph(f'Total horas: <b>{total_horas}</b>',
                      ps('th', fontSize=10, fontName='Helvetica-Bold', textColor=C_CYAN,
                         alignment=TA_RIGHT)),
        ]], colWidths=[9*cm, 8.27*cm])
        total_row.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
            ('TOPPADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(total_row)

    # ── Rodapé ───────────────────────────────────────────────────
    story.append(Spacer(1, 0.8*cm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=C_BORDER, spaceAfter=6))
    story.append(Paragraph(
        f'Documento gerado em {_dt.datetime.now().strftime("%d/%m/%Y às %H:%M")} '
        f'por {request.user.get_full_name() or request.user.username} — Tomich Hub',
        footer_s
    ))

    doc.build(story)
    buf.seek(0)

    safe_name = cliente.nome_empresa.replace(' ', '_').replace('/', '-')[:30]
    filename = f'relatorio_{safe_name}_{periodo.replace(" ","_")}.pdf'
    resp = HttpResponse(buf, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


@staff_required
def relatorios(request):
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    qs = conversations_visiveis(request.user)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    total = qs.count()
    open_count = qs.filter(status__in=['new', 'open']).count()
    pending = qs.filter(status='pending').count()
    resolved = qs.filter(status__in=['resolved', 'closed']).count()

    # Avg resolution time (hours)
    from django.db.models import Avg, ExpressionWrapper, DurationField
    resolved_qs = qs.filter(status__in=['resolved', 'closed'], closed_at__isnull=False)
    avg_duration = None
    for conv in resolved_qs.only('created_at', 'closed_at')[:500]:
        pass  # simple approach below

    # ── CSAT (pesquisa de satisfação) ───────────────────────────────
    csat_qs = qs.filter(csat_rating__isnull=False)
    csat_total = csat_qs.count()
    csat_avg = csat_qs.aggregate(avg=Avg('csat_rating'))['avg'] or 0
    csat_distribuicao = [
        {'nota': n, 'total': csat_qs.filter(csat_rating=n).count()} for n in range(5, 0, -1)
    ]

    # ── Desempenho por atendente ────────────────────────────────────
    resposta_dur = ExpressionWrapper(F('first_response_at') - F('created_at'), output_field=DurationField())
    resolucao_dur = ExpressionWrapper(F('closed_at') - F('created_at'), output_field=DurationField())

    desempenho_atendentes = []
    atendentes_ids = qs.filter(assigned_to__isnull=False).values_list('assigned_to', flat=True).distinct()
    for atendente in User.objects.filter(id__in=atendentes_ids):
        conv_atendente = qs.filter(assigned_to=atendente)
        total_at = conv_atendente.count()
        if not total_at:
            continue
        resolvidos_qs = conv_atendente.filter(status__in=['resolved', 'closed'], closed_at__isnull=False)
        media_resposta = conv_atendente.filter(first_response_at__isnull=False).annotate(
            _dur=resposta_dur
        ).aggregate(m=Avg('_dur'))['m']
        media_resolucao = resolvidos_qs.annotate(_dur=resolucao_dur).aggregate(m=Avg('_dur'))['m']

        desempenho_atendentes.append({
            'nome': atendente.get_full_name() or atendente.username,
            'total': total_at,
            'resolvidos': resolvidos_qs.count(),
            'abertos': conv_atendente.filter(status__in=['new', 'open', 'pending']).count(),
            'media_resposta_min': round(media_resposta.total_seconds() / 60, 1) if media_resposta else None,
            'media_resolucao_horas': round(media_resolucao.total_seconds() / 3600, 1) if media_resolucao else None,
        })
    desempenho_atendentes.sort(key=lambda d: -d['total'])

    # Category breakdown
    by_category = list(
        qs.values('category__name', 'category__color')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    total_for_pct = total or 1

    recent_resolved = conversations_visiveis(request.user).filter(
        status__in=['resolved', 'closed']
    ).select_related('group', 'cliente', 'assigned_to', 'category').order_by('-closed_at')[:20]

    from django.db.models.functions import ExtractYear
    import datetime as _dt

    anos_disponiveis = list(
        conversations_visiveis(request.user).filter(closed_at__isnull=False)
        .annotate(ano=ExtractYear('closed_at'))
        .values_list('ano', flat=True)
        .distinct()
        .order_by('-ano')
    ) or [_dt.date.today().year]

    # Todos os clientes cadastrados no CRM — para o seletor do PDF
    from clientes.models import Cliente as CRMCliente
    clientes_disponiveis = clientes_visiveis(request.user).order_by('nome_empresa')

    context = {
        **_base_ctx(request),
        'stats': {
            'total': total,
            'open': open_count,
            'pending': pending,
            'resolved': resolved,
        },
        'by_category': by_category,
        'total_for_pct': total_for_pct,
        'recent_resolved': recent_resolved,
        'date_from': date_from,
        'date_to': date_to,
        'anos_disponiveis': anos_disponiveis,
        'ano_atual': _dt.date.today().year,
        'clientes_disponiveis': clientes_disponiveis,
        'csat_total': csat_total,
        'csat_avg': round(csat_avg, 2),
        'csat_distribuicao': csat_distribuicao,
        'desempenho_atendentes': desempenho_atendentes,
    }
    return render(request, 'atendimento/relatorios.html', context)


# ============ HISTÓRICO ============

@staff_required
def historico(request):
    status_filter = request.GET.get('status', '')
    search = request.GET.get('search', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    qs = conversations_visiveis(request.user).select_related('group', 'cliente', 'assigned_to', 'category').order_by('-created_at')

    if status_filter:
        qs = qs.filter(status=status_filter)
    if search:
        qs = qs.filter(
            Q(group__name__icontains=search) |
            Q(cliente__nome_empresa__icontains=search) |
            Q(conversation_id__icontains=search)
        )
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    paginator = Paginator(qs, 50)
    page = request.GET.get('page', 1)
    conversations = paginator.get_page(page)

    context = {
        **_base_ctx(request),
        'conversations': conversations,
        'status_filter': status_filter,
        'search': search,
        'date_from': date_from,
        'date_to': date_to,
        'total': qs.count(),
        'status_choices': Conversation.STATUS_CHOICES,
    }
    return render(request, 'atendimento/historico.html', context)


# ============ GRUPOS (nova view) ============

@staff_required
def grupos(request):
    connection_id = request.GET.get('connection', '')
    search = request.GET.get('search', '')

    groups = groups_visiveis(request.user).select_related('connection', 'company', 'cliente').order_by('name')
    if connection_id:
        groups = groups.filter(connection_id=connection_id)
    if search:
        groups = groups.filter(Q(name__icontains=search) | Q(jid__icontains=search))

    from clientes.models import Cliente as CRMCliente
    context = {
        **_base_ctx(request),
        'groups': groups,
        'connections': WhatsAppConnection.objects.all(),
        'companies': Company.objects.all().order_by('name'),
        'clientes': clientes_visiveis(request.user).order_by('nome_empresa'),
        'connection_filter': connection_id,
        'search': search,
    }
    return render(request, 'atendimento/grupos.html', context)


@staff_required
@require_http_methods(["POST"])
def api_group_toggle_ai(request, group_id):
    group = get_object_or_404(ContactGroup, id=group_id)
    if not pode_ver_group(request.user, group):
        return JsonResponse({'success': False, 'error': 'Grupo de outra instância.'}, status=403)
    try:
        group.ai_enabled = not group.ai_enabled
        group.save()
        return JsonResponse({'success': True, 'ai_enabled': group.ai_enabled})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@staff_required
@require_http_methods(["POST"])
def api_group_toggle_auto_atendimento(request, group_id):
    """Remove/readiciona um grupo do auto atendimento — opt-out explícito que
    vale inclusive contra um fluxo universal (group_ids vazio) ativo."""
    group = get_object_or_404(ContactGroup, id=group_id)
    if not pode_ver_group(request.user, group):
        return JsonResponse({'success': False, 'error': 'Grupo de outra instância.'}, status=403)
    try:
        group.auto_atendimento_excluido = not group.auto_atendimento_excluido
        group.save(update_fields=['auto_atendimento_excluido'])
        return JsonResponse({'success': True, 'auto_atendimento_excluido': group.auto_atendimento_excluido})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@staff_required
@require_http_methods(["POST"])
def api_group_set_company(request, group_id):
    group = get_object_or_404(ContactGroup, id=group_id)
    if not pode_ver_group(request.user, group):
        return JsonResponse({'success': False, 'error': 'Grupo de outra instância.'}, status=403)
    try:
        data = json.loads(request.body)
        company_id = data.get('company_id')
        if company_id:
            group.company = get_object_or_404(Company, id=company_id)
        else:
            group.company = None
        group.save()
        return JsonResponse({'success': True, 'company_name': group.company.name if group.company else None})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@staff_required
@require_http_methods(["GET"])
def api_conversation_messages(request, conversation_id):
    """Retorna mensagens novas de uma conversa (polling fallback do WebSocket)."""
    conversation = get_object_or_404(Conversation, id=conversation_id)
    if not pode_ver_conversation(request.user, conversation):
        return JsonResponse({'error': 'Conversa de outra instância.'}, status=403)
    after_ts = request.GET.get('after_ts', '')
    try:
        import datetime as _dt
        after_dt = _dt.datetime.fromisoformat(after_ts) if after_ts else None
        if after_dt and after_dt.tzinfo is None:
            import pytz
            after_dt = pytz.utc.localize(after_dt)
    except (ValueError, ImportError):
        after_dt = None

    qs = Message.objects.filter(conversation=conversation).order_by('created_at').select_related('sender')
    if after_dt:
        # >= (não >): evita pular uma mensagem que compartilhe o microssegundo
        # exato da última já vista. A mensagem da fronteira é reenviada, mas o
        # front-end deduplica por id (_renderedIds), então não há duplicação.
        qs = qs.filter(created_at__gte=after_dt)
    # Limite alto (era 30): em rajadas de mensagens do cliente, 30 por ciclo de
    # polling fazia parecer que havia um "limite de mensagem". 300 garante que
    # todas apareçam mesmo após acúmulo.
    msgs = qs[:300]

    data = []
    for m in msgs:
        data.append({
            'id': m.id,
            'content': m.content,
            'sender_type': m.sender_type,
            'sender_name': m.sender_name or (m.sender.first_name if m.sender else 'Agente'),
            'created_at': timezone.localtime(m.created_at).strftime('%H:%M'),
            'created_at_iso': m.created_at.isoformat(),
            'message_type': m.message_type,
            'attachment_url': m.attachment_url or '',
        })

    # Usuário está vendo a conversa (mini-chat flutuante) → marca como lida
    _marcar_mensagens_lidas(conversation)

    return JsonResponse({'messages': data})


@staff_required
@require_http_methods(["POST"])
def api_auto_vincular(request):
    """
    Auto-vincula grupos a clientes com base em similaridade de nome.

    Algoritmo:
    1. Extrai a parte APÓS o separador " - " do nome do grupo como "hint" do cliente
       Ex: "TOMICH TEC - VILLAGGIONET" → hint = "VILLAGGIONET"
    2. Calcula score de match entre o hint e o nome de cada cliente:
       - 10 pts: nome da empresa está contido no hint (ou vice-versa) após normalização
       -  3 pts: interseção de palavras-chave
       -  1 pt:  substring parcial de palavras
    3. Víncula ao cliente com maior score (mínimo 1)

    Query param: force=1 → re-processa também grupos já vinculados
    """
    import re, unicodedata
    from clientes.models import Cliente as CRMCliente

    try:
        body = json.loads(request.body) if request.body else {}
    except Exception:
        body = {}
    force = body.get('force', False)

    STOPWORDS = {
        'LTDA','ME','EIRELI','SA','EPP','SRL','COMERCIO','EMPRESA','SOCIEDADE',
        'DE','DA','DO','DOS','DAS','E','EM','COM','AO','OS','AS',
        'NETWORKS','SOLUTIONS','SOLUTION','SERVICOS','TELECOMUNICACOES',
    }

    def normalize(s):
        """Remove acentos e caracteres especiais, retorna maiúsculas sem espaços."""
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        return re.sub(r'[^A-Z0-9]', '', s.upper())

    def keywords(name):
        parts = re.split(r'[\s\-\/\.\,&]+', name.upper())
        return {normalize(p) for p in parts if len(p) >= 3 and p.upper() not in STOPWORDS}

    def client_hint(group_name):
        """Parte do nome do grupo que identifica o cliente (após o último ' - ')."""
        parts = re.split(r'\s*[-–]\s*', group_name, maxsplit=10)
        if len(parts) > 1:
            return parts[-1].strip()   # Ex: "TOMICH TEC - VILLAGGIONET" → "VILLAGGIONET"
        return group_name

    def score(cliente_name, group_name):
        hint      = client_hint(group_name)
        hint_norm = normalize(hint)
        cli_norm  = normalize(cliente_name)

        # Nível 1: match direto (nome da empresa contido no hint ou vice-versa)
        if cli_norm and hint_norm:
            if cli_norm in hint_norm or hint_norm in cli_norm:
                # Bônus proporcional ao tamanho da palavra correspondida
                return 10 + min(len(cli_norm), len(hint_norm))

        # Nível 2: interseção de palavras-chave entre hint e nome do cliente
        kw_hint = keywords(hint)
        kw_cli  = keywords(cliente_name)
        exact   = kw_hint & kw_cli
        if exact:
            return 3 * len(exact)

        # Nível 3: substring de palavras-chave
        sub_score = 0
        for wa in kw_cli:
            for wb in kw_hint:
                if len(wa) >= 4 and len(wb) >= 4 and (wa in wb or wb in wa):
                    sub_score += 1
        return sub_score

    # Escopo: o auto-vínculo só pode enxergar os clientes da própria
    # instância e só pode mexer em grupo livre ou já dela — senão um
    # Consultor rodando "vincular automático" reatribuía os grupos das
    # outras instâncias para os clientes dele.
    clientes = list(clientes_visiveis(request.user))

    if force:
        groups = list(groups_visiveis(request.user).select_related('connection', 'cliente'))
    else:
        groups = list(groups_visiveis(request.user).filter(cliente__isnull=True).select_related('connection'))

    linked = []
    for grupo in groups:
        best_cliente = None
        best_score   = 0
        best_match   = []

        for cliente in clientes:
            s = score(cliente.nome_empresa, grupo.name)
            if s > best_score:
                best_score   = s
                best_cliente = cliente
                # Identifica palavras do match para log
                hint     = client_hint(grupo.name)
                kw_h     = keywords(hint)
                kw_c     = keywords(cliente.nome_empresa)
                best_match = sorted(kw_h & kw_c) or [normalize(hint)[:20]]

        if best_cliente and best_score >= 1:
            # Só salva se mudou
            if grupo.cliente_id != best_cliente.id:
                grupo.cliente = best_cliente
                grupo.save(update_fields=['cliente'])
                linked.append({
                    'group_id': grupo.id,
                    'group_name': grupo.name,
                    'cliente_id': best_cliente.id,
                    'cliente_name': best_cliente.nome_empresa,
                    'score': best_score,
                    'match_words': best_match,
                })

    return JsonResponse({
        'success': True,
        'count': len(linked),
        'linked': linked,
    })


@staff_required
def api_cliente_grupos(request, cliente_id):
    """Lista e atualiza grupos vinculados a um cliente do CRM"""
    cliente = get_object_or_404(clientes_visiveis(request.user), id=cliente_id)

    if request.method == 'GET':
        # `groups_visiveis` já corta os grupos de outras instâncias — sem
        # isso o campo `linked_to_name` entregava o nome do cliente alheio
        # dono de cada grupo.
        all_groups = groups_visiveis(request.user).select_related('connection', 'cliente').order_by('connection__name', 'name')
        data = []
        for g in all_groups:
            linked_to_this = (g.cliente_id == cliente_id)
            linked_to_other = (g.cliente_id is not None and g.cliente_id != cliente_id)
            data.append({
                'id': g.id,
                'name': g.name,
                'jid': g.jid,
                'connection': g.connection.name if g.connection else '—',
                'linked': linked_to_this,
                'linked_to_other': linked_to_other,
                'linked_to_name': g.cliente.nome_empresa if linked_to_other else None,
            })
        return JsonResponse({
            'cliente_name': cliente.nome_empresa,
            'groups': data,
        })

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            group_ids = [int(i) for i in data.get('group_ids', [])]

            # Remove vínculo dos grupos atualmente ligados a este cliente
            ContactGroup.objects.filter(cliente_id=cliente_id).update(cliente=None)

            # Vincula os grupos selecionados — restrito ao que o usuário
            # pode ver, senão os ids do POST alcançavam grupo de qualquer
            # instância.
            if group_ids:
                permitidos = groups_visiveis(request.user).filter(id__in=group_ids)
                group_ids = list(permitidos.values_list('id', flat=True))
                ContactGroup.objects.filter(id__in=group_ids).update(cliente=cliente)

            return JsonResponse({
                'success': True,
                'linked': len(group_ids),
                'message': f'{len(group_ids)} grupo(s) vinculado(s) a {cliente.nome_empresa}',
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_required
@require_http_methods(["GET"])
def api_conversation_hosts(request, conversation_id):
    """Retorna os hosts (Acesso) do cliente vinculado à conversa.

    Usa o cliente atual do grupo (vínculo vivo) como fonte primária, com
    fallback para conversation.cliente caso o grupo não tenha cliente.
    """
    from clientes.models import Acesso
    conversation = get_object_or_404(
        Conversation.objects.select_related('group__cliente', 'cliente'),
        id=conversation_id,
    )
    if not pode_ver_conversation(request.user, conversation):
        return JsonResponse({'hosts': [], 'cliente': None, 'error': 'Conversa de outra instância.'}, status=403)
    # Vínculo vivo: grupo → cliente (reflete edições recentes do usuário)
    cliente = (
        conversation.group.cliente
        if conversation.group and conversation.group.cliente
        else conversation.cliente
    )
    if not cliente:
        return JsonResponse({'hosts': [], 'cliente': None})

    acessos = Acesso.objects.filter(
        cliente=cliente
    ).select_related('funcao', 'modelo').order_by('funcao__descricao', 'tipo')

    hosts = []
    for a in acessos:
        proto = (a.protocolo or '').upper()
        if proto in ('HTTP', 'HTTPS'):
            porta_web = a.porta or (443 if proto == 'HTTPS' else 80)
            scheme = proto.lower()
            action_url = f'/clientes/acessos/{a.id}/web/{porta_web}/{scheme}/'
            action_label = 'Web'
            action_icon = 'fa-globe'
        elif proto == 'WINBOX' or getattr(a, 'winbox', None):
            action_url = f'/clientes/winbox/{a.id}/'
            action_label = 'Winbox'
            action_icon = 'fa-terminal'
        else:
            action_url = f'/clientes/terminal/?acesso={a.id}'
            action_label = 'Terminal'
            action_icon = 'fa-terminal'

        hosts.append({
            'id': a.id,
            'tipo': a.tipo,
            'host': a.host,
            'host_ipv6': a.host_ipv6 or '',
            'porta': a.porta,
            'protocolo': proto,
            'funcao': a.funcao.descricao if a.funcao else '',
            'modelo': a.modelo.nome if a.modelo else '',
            'action_url': action_url,
            'action_label': action_label,
            'action_icon': action_icon,
            'ping_url': f'/clientes/acessos/ping/{a.id}/',
        })

    return JsonResponse({
        'hosts': hosts,
        'cliente': {
            'id': cliente.id,
            'nome': cliente.nome_empresa,
        },
    })


@staff_required
@require_http_methods(["POST"])
def api_test_notif_abertos(request):
    """Envia uma notificação de teste de chamado aberto."""
    from .tasks import _get_notif_client_and_jid
    client, jid = _get_notif_client_and_jid()
    if not client:
        return JsonResponse({'success': False, 'error': 'Grupo de notificação não configurado.'})
    texto = (
        "🔔 *[TESTE] Novo chamado em aberto!*\n\n"
        "🏢 Empresa: *Empresa Exemplo*\n"
        "📱 Grupo: Grupo Teste\n\n"
        "Esta é uma mensagem de teste do sistema de notificações."
    )
    ok = client.send_text(jid, texto, everyone=True)
    return JsonResponse({'success': ok, 'error': '' if ok else 'Falha ao enviar. Verifique a conexão.'})


@staff_required
@require_http_methods(["POST"])
def api_test_alerta_diario(request):
    """Envia o alerta diário agora (ignora horário configurado)."""
    from .tasks import _run_alerta_diario_agora
    result = _run_alerta_diario_agora()
    return JsonResponse(result)


@staff_required
@require_http_methods(["GET"])
def api_agents_list(request):
    """Retorna atendentes ativos para o modal de transferência.

    A lista saía de `is_staff=True` cru, sem escopo nenhum: aparecia gente de
    outra instância e, pior, conta de serviço/sobra de cadastro que nunca
    atendeu nada (nome vazio, sem e-mail) — o operador via "atendentes que não
    existem" no modal. Agora vale o escopo de instância de `usuario.perms` e
    ficam de fora as contas sem identificação e sem histórico no módulo.
    """
    from django.db.models import Exists, OuterRef, Q
    from usuario.perms import colegas_de_instancia, get_role

    # Inclui o próprio usuário: o modal de transferência também é usado para
    # puxar pra si um chamado que está em andamento com outro atendente —
    # excluir quem está logado deixava essa opção impossível de aparecer.
    agents = colegas_de_instancia(request.user).filter(
        # Todo o módulo de atendimento passa por `staff_required` (is_staff).
        # Oferecer Consultor/Operador aqui mandaria o chamado pra quem não
        # consegue nem abrir a tela — o chamado sumiria da fila de todo mundo.
        is_staff=True,
    ).annotate(
        _tem_status=Exists(AgentStatus.objects.filter(user_id=OuterRef('pk'))),
        _ja_atendeu=Exists(Conversation.objects.filter(assigned_to_id=OuterRef('pk'))),
    ).filter(
        # Conta "de verdade": ou já usou o atendimento (tem AgentStatus ou já
        # ficou com algum chamado), ou pelo menos tem identificação (nome ou
        # e-mail). Só some quem não tem nada disso — que é exatamente o perfil
        # da conta criada por engano/serviço.
        Q(_tem_status=True) | Q(_ja_atendeu=True) |
        ~Q(email='') | ~Q(first_name='') | ~Q(last_name='')
    ).order_by('first_name', 'username')

    _ROLE_LABEL = {'admin': 'Supervisor', 'consultor': 'Consultor', 'operador': 'Agente'}
    data = []
    for u in agents:
        try:
            display = u.agent_status.get_display_name()
            status  = u.agent_status.status
        except Exception:
            display = u.get_full_name() or u.username
            status  = 'offline'
        data.append({
            'id':      u.id,
            'name':    display,
            # Rotulava todo mundo de "Supervisor" — o queryset já era só
            # is_staff, então o `else 'Agente'` nunca acontecia.
            'role':    _ROLE_LABEL.get(get_role(u), 'Agente'),
            'status':  status,
            'initials': (u.first_name[:1] + (u.last_name[:1] if u.last_name else '')).upper() or u.username[:2].upper(),
        })
    return JsonResponse({'agents': data})


@staff_required
@require_http_methods(["GET"])
def api_groups_json(request):
    """Lista grupos/contatos para o modal Iniciar Conversa."""
    q = request.GET.get('q', '').strip()
    qs = groups_visiveis(request.user).select_related('connection', 'cliente').order_by('name')
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(connection__name__icontains=q))
    qs = qs[:60]
    return JsonResponse({'groups': [{
        'id': g.id,
        'name': g.name,
        'connection': g.connection.name if g.connection else '—',
        'cliente': g.cliente.nome_empresa if g.cliente else None,
    } for g in qs]})


@staff_required
@require_http_methods(["POST"])
def api_start_conversation_by_group(request):
    """Abre ou cria uma conversa para um grupo."""
    try:
        data = json.loads(request.body)
        group_id = data.get('group_id')
        if not group_id:
            return JsonResponse({'success': False, 'error': 'group_id obrigatório'}, status=400)

        group = get_object_or_404(ContactGroup, id=group_id)
        if not pode_ver_group(request.user, group):
            return JsonResponse({'success': False, 'error': 'Grupo de outra instância.'}, status=403)

        from .services import _ws_send_inbox

        now = timezone.now()

        # Ao iniciar pela plataforma, queremos SEMPRE um chamado NOVO e limpo —
        # não reaproveitar a conversa anterior (que arrastava as mensagens
        # antigas). Encerra os chamados ativos anteriores deste grupo (o
        # histórico fica preservado na conversa resolvida) e abre um novo.
        anteriores = Conversation.objects.filter(
            group=group,
            status__in=['pre', 'new', 'open', 'pending'],
            is_task_conv=False,
        )
        for c in anteriores:
            old_status = c.status
            c.status = 'resolved'
            c.closed_at = now
            if not c.resolution:
                c.resolution = 'Encerrado automaticamente ao iniciar um novo atendimento.'
            c.save(update_fields=['status', 'closed_at', 'resolution'])
            ConversationActivity.objects.create(
                conversation=c, actor=request.user, action='status_changed',
                old_value=old_status, new_value='resolved',
            )
            # Sem este aviso o chamado encerrado aqui continuava na lista de
            # todo mundo (inclusive na do próprio atendente) até um F5 — um
            # item fantasma que, ao ser clicado, abria um chamado resolvido.
            try:
                _ws_send_inbox({
                    'type': 'conversation_status',
                    'conversation_id': str(c.id),
                    'status': c.status,
                    'assigned_to_id': c.assigned_to_id,
                })
            except Exception as _e:
                logger.warning(f"Falha ao notificar inbox (encerramento automático): {_e}")

        conv = Conversation.objects.create(
            group=group,
            status='open',
            assigned_to=request.user,
            cliente=group.cliente,
            last_message_at=now,   # torna este o chamado ativo do grupo
        )
        ConversationActivity.objects.create(
            conversation=conv,
            actor=request.user,
            action='status_changed',
            old_value='',
            new_value='open',
        )
        # Chamado criado pela plataforma não passa por webhook nem por
        # notify_reassignment (nunca teve outro dono), então nada avisava as
        # telas abertas: ele só aparecia em "Assumidos" depois de recarregar
        # a página. Evento próprio — quem assina refaz a lista, sem o som e o
        # toast de "transferido para você" (aqui foi o próprio atendente que
        # abriu o chamado).
        try:
            _ws_send_inbox({
                'type': 'conversation_created',
                'conversation_id': str(conv.id),
                'group_name': group.name,
                'assigned_to_id': conv.assigned_to_id,
            })
        except Exception as _e:
            logger.warning(f"Falha ao notificar inbox (chamado iniciado): {_e}")

        return JsonResponse({
            'success': True,
            'url': f'/atendimento/conversation/{conv.id}/',
            'created': True,
        })
    except Exception as e:
        logger.error(f"Erro ao iniciar conversa: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@staff_required
@require_http_methods(["GET", "POST"])
def api_display_name(request):
    """Retorna ou atualiza o nome de exibição do usuário logado"""
    profile, _ = AgentStatus.objects.get_or_create(
        user=request.user,
        defaults={'status': 'offline', 'display_name': ''}
    )
    if request.method == 'GET':
        return JsonResponse({
            'display_name': profile.display_name,
            'fallback': request.user.get_full_name() or request.user.username,
            'effective': profile.get_display_name(),
        })
    try:
        data = json.loads(request.body)
        name = data.get('display_name', '').strip()
        profile.display_name = name
        profile.save(update_fields=['display_name'])
        return JsonResponse({
            'success': True,
            'display_name': name,
            'effective': profile.get_display_name(),
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@staff_required
def sala_virtual(request):
    from .services import build_ice_servers
    display_name = request.user.get_full_name() or request.user.username
    return render(request, 'atendimento/sala_virtual.html', {
        'display_name': display_name,
        'ice_servers_json': json.dumps(build_ice_servers()),
    })


# ═══════════════════════════════════════════════════════
# TAREFAS
# ═══════════════════════════════════════════════════════

@staff_required
def tarefas(request):
    """Página principal de tarefas"""
    from django.contrib.auth.models import User as AuthUser
    from usuario.perms import colegas_de_instancia
    agents = colegas_de_instancia(request.user).filter(is_staff=True).order_by('first_name', 'username')
    context = {
        **_base_ctx(request),
        'agents': agents,
    }
    return render(request, 'atendimento/tarefas.html', context)


@staff_required
@require_http_methods(['GET', 'POST'])
def api_tasks_list(request):
    if request.method == 'GET':
        status_filter = request.GET.get('status', '')
        assigned_filter = request.GET.get('assigned_to', '')
        qs = Task.objects.select_related('assigned_to', 'created_by').prefetch_related('task_conversations__conversation__group')
        if status_filter:
            qs = qs.filter(status=status_filter)
        if assigned_filter == 'me':
            qs = qs.filter(assigned_to=request.user)
        elif assigned_filter:
            qs = qs.filter(assigned_to_id=assigned_filter)

        tasks = []
        for t in qs:
            tasks.append(_task_to_dict(t))
        return JsonResponse({'tasks': tasks})

    data = json.loads(request.body)
    title = data.get('title', '').strip()
    if not title:
        return JsonResponse({'error': 'title required'}, status=400)

    due_date = None
    if data.get('due_date'):
        from django.utils.dateparse import parse_datetime
        due_date = parse_datetime(data['due_date'])

    task = Task.objects.create(
        title=title,
        description=data.get('description', '').strip() or None,
        status=data.get('status', 'pending'),
        priority=data.get('priority', 'medium'),
        assigned_to_id=data.get('assigned_to') or None,
        created_by=request.user,
        due_date=due_date,
    )
    return JsonResponse(_task_to_dict(task), status=201)


@staff_required
@require_http_methods(['GET', 'PUT', 'DELETE'])
def api_task_detail(request, task_id):
    task = get_object_or_404(Task, id=task_id)

    if request.method == 'GET':
        return JsonResponse(_task_to_dict(task, full=True))

    if request.method == 'DELETE':
        task.delete()
        return JsonResponse({'success': True})

    data = json.loads(request.body)
    if 'title' in data:
        task.title = data['title'].strip()
    if 'description' in data:
        task.description = data['description'].strip() or None
    if 'status' in data:
        task.status = data['status']
    if 'priority' in data:
        task.priority = data['priority']
    if 'assigned_to' in data:
        task.assigned_to_id = data['assigned_to'] or None
    if 'due_date' in data:
        from django.utils.dateparse import parse_datetime
        task.due_date = parse_datetime(data['due_date']) if data['due_date'] else None
    task.save()
    return JsonResponse(_task_to_dict(task))


@staff_required
@require_http_methods(['POST', 'DELETE'])
def api_task_conversation(request, task_id, conversation_id):
    task = get_object_or_404(Task, id=task_id)
    conv = get_object_or_404(Conversation, id=conversation_id)
    if not pode_ver_conversation(request.user, conv):
        return JsonResponse({'success': False, 'error': 'Conversa de outra instância.'}, status=403)

    if request.method == 'POST':
        tc, created = TaskConversation.objects.get_or_create(
            task=task, conversation=conv,
            defaults={'added_by': request.user}
        )
        if created and not conv.is_task_conv:
            conv.is_task_conv = True
            conv.save(update_fields=['is_task_conv'])
        return JsonResponse({'success': True, 'created': created})

    # DELETE: remove vínculo e, se não há mais tarefas, desmarca
    TaskConversation.objects.filter(task=task, conversation=conv).delete()
    if not conv.task_conversations.exists():
        conv.is_task_conv = False
        conv.save(update_fields=['is_task_conv'])
    return JsonResponse({'success': True})


@staff_required
@require_http_methods(['POST'])
def api_task_add_conversation_by_conv(request, conversation_id):
    """Adiciona a conversa a uma tarefa existente ou cria nova — chamado pelo menu de contexto."""
    conv = get_object_or_404(Conversation, id=conversation_id)
    if not pode_ver_conversation(request.user, conv):
        return JsonResponse({'success': False, 'error': 'Conversa de outra instância.'}, status=403)
    data = json.loads(request.body)
    task_id = data.get('task_id')
    if task_id:
        task = get_object_or_404(Task, id=task_id)
    else:
        title = data.get('title', '').strip() or f"Tarefa #{conv.conversation_id}"
        due_date = None
        if data.get('due_date'):
            from django.utils.dateparse import parse_datetime
            from django.utils import timezone as _tz
            due_date = parse_datetime(data['due_date'])
            if due_date and due_date.tzinfo is None:
                due_date = _tz.make_aware(due_date)
        task = Task.objects.create(
            title=title,
            description=data.get('description', '').strip() or None,
            status='pending',
            priority=data.get('priority', 'medium'),
            assigned_to_id=data.get('assigned_to') or None,
            created_by=request.user,
            due_date=due_date,
        )
    TaskConversation.objects.get_or_create(task=task, conversation=conv, defaults={'added_by': request.user})
    return JsonResponse({'success': True, 'task': _task_to_dict(task)}, status=201)


def _task_to_dict(task, full=False):
    convs = []
    if full:
        for tc in task.task_conversations.select_related('conversation__group').all():
            c = tc.conversation
            convs.append({
                'id': str(c.id),
                'conversation_id': c.conversation_id,
                'group_name': c.group.name,
                'status': c.status,
                'added_at': tc.added_at.isoformat(),
            })

    d = {
        'id': str(task.id),
        'title': task.title,
        'description': task.description or '',
        'status': task.status,
        'priority': task.priority,
        'due_date': task.due_date.isoformat() if task.due_date else None,
        'is_overdue': task.is_overdue,
        'assigned_to': None,
        'created_by': None,
        'created_at': task.created_at.isoformat(),
        'conv_count': task.task_conversations.count(),
    }
    if task.assigned_to:
        d['assigned_to'] = {
            'id': task.assigned_to.id,
            'name': task.assigned_to.get_full_name() or task.assigned_to.username,
        }
    if task.created_by:
        d['created_by'] = {
            'id': task.created_by.id,
            'name': task.created_by.get_full_name() or task.created_by.username,
        }
    if full:
        d['conversations'] = convs
    return d


# ═══════════════════════════════════════════════════════
# CONTATOS DE ATENDENTES
# ═══════════════════════════════════════════════════════

@staff_required
@require_http_methods(['GET', 'POST'])
def api_attendant_contacts(request):
    if request.method == 'GET':
        contacts = AttendantContact.objects.select_related('user', 'connection').all()
        return JsonResponse({'contacts': [
            {
                'user_id': c.user_id,
                'user_name': c.user.get_full_name() or c.user.username,
                'phone': c.phone,
                'connection_id': str(c.connection_id) if c.connection_id else None,
                'reminders_enabled': c.reminders_enabled,
            }
            for c in contacts
        ]})

    data = json.loads(request.body)
    user_id = data.get('user_id')
    # Só dígitos — remove espaço/traço/parênteses/+ acidentais. Não mexe no
    # nono dígito (isso é decidido por quem digita); a detecção de "atendente
    # no pessoal" já tolera com/sem 9 via normalizar_telefone_br.
    phone = re.sub(r'\D', '', data.get('phone', ''))
    connection_id = data.get('connection_id') or None
    reminders_enabled = data.get('reminders_enabled', True)

    if not user_id or not phone:
        return JsonResponse({'error': 'user_id e phone são obrigatórios'}, status=400)

    contact, _ = AttendantContact.objects.update_or_create(
        user_id=user_id,
        defaults={
            'phone': phone,
            'connection_id': connection_id,
            'reminders_enabled': reminders_enabled,
        }
    )
    return JsonResponse({
        'success': True,
        'user_id': contact.user_id,
        'phone': contact.phone,
    })


@staff_required
@require_http_methods(['DELETE'])
def api_attendant_contact_delete(request, user_id):
    AttendantContact.objects.filter(user_id=user_id).delete()
    return JsonResponse({'success': True})


@staff_required
@require_http_methods(['POST'])
def api_attendant_contact_test(request, user_id):
    """Envia mensagem de teste para o contato WhatsApp do atendente."""
    contact = get_object_or_404(AttendantContact, user_id=user_id)

    if not contact.connection:
        return JsonResponse({'success': False, 'error': 'Nenhuma conexão WhatsApp configurada para este contato'})

    from django.utils import timezone as tz
    agora = tz.localtime(tz.now())
    user  = contact.user
    jid   = contact.get_jid()

    # Busca tarefas e chamados ativos deste atendente
    tarefas = Task.objects.filter(
        assigned_to=user,
        status__in=['pending', 'in_progress'],
    ).order_by('due_date')[:5]

    chamados = Conversation.objects.filter(
        assigned_to=user,
        status__in=['new', 'open', 'pending'],
    ).select_related('group').order_by('last_message_at')[:5]

    nome = user.get_full_name() or user.username

    if tarefas.exists() or chamados.exists():
        # Mensagem personalizada com as tarefas/chamados reais
        linhas = [
            f"🧪 *[TESTE] Lembrete pessoal — {nome}*",
            f"📅 {agora.strftime('%d/%m/%Y %H:%M')}",
            "",
        ]
        if chamados.exists():
            linhas.append(f"📞 *Chamados em aberto* ({chamados.count()}):")
            for c in chamados:
                label = {'new': 'Novo', 'open': 'Aberto', 'pending': 'Aguardando'}.get(c.status, c.status)
                linhas.append(f"  • {c.group.name} [{label}]")
            linhas.append("")
        if tarefas.exists():
            linhas.append(f"✅ *Tarefas pendentes* ({tarefas.count()}):")
            for t in tarefas:
                venc = ""
                if t.due_date:
                    venc = f" — prazo: {tz.localtime(t.due_date).strftime('%d/%m %H:%M')}"
                    if t.is_overdue:
                        venc += " ⚠️ ATRASADA"
                linhas.append(f"  • {t.title}{venc}")
            linhas.append("")
        linhas.append("_Esta é uma mensagem de teste. No alerta diário você receberá este resumo automaticamente._")
        detail = f"{tarefas.count()} tarefa(s) e {chamados.count()} chamado(s) encontrado(s)"
    else:
        # Mensagem padrão — sem tarefas/chamados
        linhas = [
            f"🧪 *[TESTE] Lembrete pessoal — {nome}*",
            f"📅 {agora.strftime('%d/%m/%Y %H:%M')}",
            "",
            "✅ Nenhuma tarefa pendente nem chamado em aberto no momento.",
            "",
            "_Esta é uma mensagem de teste do sistema de lembretes. Quando houver tarefas ou chamados atribuídos a você, este lembrete será enviado automaticamente no alerta diário._",
        ]
        detail = "sem tarefas/chamados ativos — mensagem padrão enviada"

    texto = "\n".join(linhas)

    try:
        client = EvolutionAPIClient(contact.connection)
        ok = client.send_text(jid, texto)
        if ok:
            return JsonResponse({'success': True, 'message': f'Mensagem de teste enviada para {contact.phone} ({detail})'})
        return JsonResponse({'success': False, 'error': f'Falha ao enviar via Evolution API para {jid}'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@staff_required
@require_http_methods(["GET"])
def api_my_conversations(request):
    """Retorna conversas ativas atribuídas ao usuário logado."""
    convs = Conversation.objects.filter(
        assigned_to=request.user,
        status__in=['new', 'open', 'pending'],
    ).select_related('group', 'cliente').annotate(
        unread_count=Count('messages', filter=Q(messages__sender_type='customer', messages__is_read=False))
    ).order_by('-last_message_at')[:30]

    data = []
    for c in convs:
        unread = c.unread_count
        last_msg = Message.objects.filter(conversation=c).order_by('-created_at').first()
        last_customer_msg = Message.objects.filter(
            conversation=c, sender_type='customer',
        ).order_by('-created_at').first()
        last_sender_name = (
            last_customer_msg.sender_name or ''
        ) if last_customer_msg else ''
        data.append({
            'id': str(c.id),
            'conversation_id': c.conversation_id,
            'group_name': c.group.name if c.group else '',
            'status': c.status,
            'unread_count': unread,
            'last_message': (last_msg.content or '')[:80] if last_msg else '',
            'last_message_type': last_msg.message_type if last_msg else 'text',
            'last_message_at': timezone.localtime(c.last_message_at).strftime('%H:%M') if c.last_message_at else '',
            'cliente': c.cliente.nome_empresa if c.cliente else '',
            'last_sender_name': last_sender_name,
        })

    return JsonResponse({'conversations': data, 'total': len(data)})
