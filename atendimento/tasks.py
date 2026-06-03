from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone
from datetime import timedelta

logger = get_task_logger(__name__)


def _get_notif_client_and_jid():
    """Retorna (EvolutionAPIClient, jid) para notificações de chamados, ou (None, None)."""
    from .models import SystemSetting, ContactGroup
    from .services import EvolutionAPIClient

    group_id = SystemSetting.get('notif_abertos_group_id', '').strip()
    if not group_id:
        return None, None

    group = ContactGroup.objects.filter(id=group_id).select_related('connection').first()
    if not group or not group.connection or not group.jid:
        return None, None

    return EvolutionAPIClient(group.connection), group.jid


def _get_alert_client_and_jid():
    """Retorna (EvolutionAPIClient, jid) para o alerta diário, ou (None, None)."""
    from .models import SystemSetting, ContactGroup
    from .services import EvolutionAPIClient

    group_id = SystemSetting.get('daily_alert_group', '').strip()
    if not group_id:
        return None, None

    group = ContactGroup.objects.filter(id=group_id).select_related('connection').first()
    if not group or not group.connection or not group.jid:
        return None, None

    return EvolutionAPIClient(group.connection), group.jid


@shared_task
def notificar_chamados_abertos():
    """
    Roda a cada 10 min. Para cada conversa aberta sem atendente e sem
    interação nos últimos 10 min, envia notificação (com @everyone) no grupo configurado.
    """
    from .models import Conversation, SystemSetting

    if SystemSetting.get('notif_abertos_enabled', 'false') != 'true':
        return {'skipped': True, 'reason': 'disabled'}

    client, notif_jid = _get_notif_client_and_jid()
    if not client:
        return {'skipped': True, 'reason': 'no group configured'}

    threshold = timezone.now() - timedelta(minutes=10)
    convs = Conversation.objects.filter(
        status__in=['new', 'open'],
        assigned_to__isnull=True,
        last_message_at__lt=threshold,
    ).select_related('cliente', 'group')

    if not convs.exists():
        return {'notified': 0}

    notified = 0
    for conv in convs:
        texto = (
            f"⚠️ *Chamado sem atendimento!*\n\n"
            f"📱 Grupo: *{conv.group.name}*\n"
            f"⏰ Sem resposta há mais de 10 minutos\n\n"
            f"Acesse o sistema para assumir o chamado."
        )
        ok = client.send_text(notif_jid, texto, everyone=True)
        if ok:
            notified += 1
        else:
            logger.warning(f"Falha ao notificar chamado {conv.conversation_id} ({conv.group.name})")

    return {'notified': notified}


@shared_task
def enviar_alerta_diario():
    """
    Roda a cada hora e dispara no horário configurado.
    Envia resumo dos atendimentos abertos e assumidos no grupo configurado.
    """
    from .models import Conversation, SystemSetting
    import datetime as _dt

    if SystemSetting.get('daily_alert_enabled', 'false') != 'true':
        return {'skipped': True, 'reason': 'disabled'}

    # Verifica se a hora atual bate com a configurada
    alert_time_str = SystemSetting.get('daily_alert_time', '08:00')
    try:
        hora_cfg, min_cfg = [int(x) for x in alert_time_str.split(':')]
    except Exception:
        hora_cfg, min_cfg = 8, 0

    agora = timezone.localtime(timezone.now())
    if agora.hour != hora_cfg or agora.minute not in range(min_cfg, min_cfg + 5):
        return {'skipped': True, 'reason': 'not alert time'}

    client, jid = _get_alert_client_and_jid()
    if not client:
        return {'skipped': True, 'reason': 'no group configured'}

    # Busca conversas ativas
    abertos = Conversation.objects.filter(
        status__in=['new', 'open'],
        assigned_to__isnull=True,
    ).select_related('cliente', 'group').order_by('last_message_at')

    assumidos = Conversation.objects.filter(
        status__in=['new', 'open', 'pending'],
        assigned_to__isnull=False,
    ).select_related('cliente', 'group', 'assigned_to').order_by('assigned_to__first_name')

    resolvidos_hoje = Conversation.objects.filter(
        status__in=['resolved', 'closed'],
        closed_at__date=agora.date(),
    ).count()

    # Monta mensagem
    data_str = agora.strftime('%d/%m/%Y %H:%M')
    linhas = [
        f"📊 *RESUMO DIÁRIO DE ATENDIMENTOS*",
        f"📅 {data_str}",
        "",
    ]

    # Abertos
    linhas.append(f"📬 *EM ABERTO* (sem atendente): {abertos.count()}")
    if abertos:
        for conv in abertos[:15]:
            linhas.append(f"  • {conv.group.name}")
    else:
        linhas.append("  ✅ Nenhum chamado em aberto")

    linhas.append("")

    # Assumidos — agrupa por atendente
    linhas.append(f"👥 *ASSUMIDOS*: {assumidos.count()}")
    if assumidos:
        by_agent: dict = {}
        for conv in assumidos[:30]:
            agent_name = conv.assigned_to.get_full_name() or conv.assigned_to.username
            grupo = conv.group.name
            by_agent.setdefault(agent_name, []).append(grupo)
        for agent, empresas in by_agent.items():
            linhas.append(f"  👤 *{agent}*:")
            for emp in empresas:
                linhas.append(f"    • {emp}")
    else:
        linhas.append("  — Nenhum chamado assumido")

    linhas.append("")
    linhas.append(f"✅ *Resolvidos hoje*: {resolvidos_hoje}")

    texto = "\n".join(linhas)

    try:
        client.send_text(jid, texto)
        logger.info(f"Alerta diário enviado para {jid}")
        return {'success': True, 'abertos': abertos.count(), 'assumidos': assumidos.count()}
    except Exception as e:
        logger.error(f"Erro ao enviar alerta diário: {e}")
        return {'success': False, 'error': str(e)}


def _run_alerta_diario_agora():
    """Executa o alerta diário ignorando a verificação de horário (para testes)."""
    from .models import Conversation, SystemSetting
    import datetime as _dt

    client, jid = _get_alert_client_and_jid()
    if not client:
        return {'success': False, 'error': 'Grupo não configurado'}

    agora = timezone.localtime(timezone.now())

    abertos = Conversation.objects.filter(
        status__in=['new', 'open'], assigned_to__isnull=True,
    ).select_related('cliente', 'group').order_by('last_message_at')

    assumidos = Conversation.objects.filter(
        status__in=['new', 'open', 'pending'], assigned_to__isnull=False,
    ).select_related('cliente', 'group', 'assigned_to').order_by('assigned_to__first_name')

    resolvidos_hoje = Conversation.objects.filter(
        status__in=['resolved', 'closed'], closed_at__date=agora.date(),
    ).count()

    data_str = agora.strftime('%d/%m/%Y %H:%M')
    linhas = [
        f"📊 *RESUMO DIÁRIO DE ATENDIMENTOS* _(teste)_",
        f"📅 {data_str}", "",
    ]

    linhas.append(f"📬 *EM ABERTO* (sem atendente): {abertos.count()}")
    for conv in abertos[:15]:
        linhas.append(f"  • {conv.group.name}")
    if not abertos:
        linhas.append("  ✅ Nenhum chamado em aberto")

    linhas.append("")
    linhas.append(f"👥 *ASSUMIDOS*: {assumidos.count()}")
    if assumidos:
        by_agent: dict = {}
        for conv in assumidos[:30]:
            agent_name = conv.assigned_to.get_full_name() or conv.assigned_to.username
            grupo = conv.group.name
            by_agent.setdefault(agent_name, []).append(grupo)
        for agent, empresas in by_agent.items():
            linhas.append(f"  👤 *{agent}*:")
            for emp in empresas:
                linhas.append(f"    • {emp}")
    else:
        linhas.append("  — Nenhum chamado assumido")

    linhas += ["", f"✅ *Resolvidos hoje*: {resolvidos_hoje}"]

    try:
        client.send_text(jid, "\n".join(linhas))
        return {'success': True, 'abertos': abertos.count(), 'assumidos': assumidos.count()}
    except Exception as e:
        return {'success': False, 'error': str(e)}
