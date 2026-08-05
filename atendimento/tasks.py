from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone
from datetime import timedelta

logger = get_task_logger(__name__)


def _get_notif_client_and_jid():
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
    from .models import SystemSetting, ContactGroup
    from .services import EvolutionAPIClient
    group_id = SystemSetting.get('daily_alert_group', '').strip()
    if not group_id:
        return None, None
    group = ContactGroup.objects.filter(id=group_id).select_related('connection').first()
    if not group or not group.connection or not group.jid:
        return None, None
    return EvolutionAPIClient(group.connection), group.jid


def _parse_time(time_str, default_hour=8, default_min=0):
    """Converte 'HH:MM' em (hour, minute). Retorna defaults em caso de erro."""
    try:
        h, m = [int(x) for x in str(time_str).strip().split(':')]
        return h, m
    except Exception:
        return default_hour, default_min


def _already_sent_today(key):
    """Verifica se já foi enviado hoje usando SystemSetting como guard."""
    from .models import SystemSetting
    today = timezone.localtime(timezone.now()).strftime('%Y-%m-%d')
    return SystemSetting.get(key, '') == today


def _mark_sent_today(key):
    from .models import SystemSetting
    today = timezone.localtime(timezone.now()).strftime('%Y-%m-%d')
    SystemSetting.set(key, today)


def _is_time_now(hour_cfg, min_cfg, window=5):
    """Retorna True se o horário atual (local) está dentro da janela de `window` minutos."""
    agora = timezone.localtime(timezone.now())
    if agora.hour != hour_cfg:
        return False
    return min_cfg <= agora.minute < min_cfg + window


@shared_task
def notificar_chamados_abertos():
    """A cada 10 min: avisa, UMA ÚNICA VEZ por chamado, sobre chamados sem
    atendente há mais de 10 min. Envia uma só mensagem consolidada ao grupo
    configurado, marcando todos — sem repetir os mesmos chamados a cada ciclo."""
    from .models import Conversation, SystemSetting

    if SystemSetting.get('notif_abertos_enabled', 'false') != 'true':
        return {'skipped': True, 'reason': 'disabled'}

    # Respeita snooze: "Reagendar lembrete" adia notificações ao próximo dia
    snooze_until = SystemSetting.get('notif_abertos_snooze_until', '').strip()
    if snooze_until:
        today = timezone.localtime(timezone.now()).strftime('%Y-%m-%d')
        if today < snooze_until:
            return {'skipped': True, 'reason': f'snoozed until {snooze_until}'}

    client, notif_jid = _get_notif_client_and_jid()
    if not client:
        return {'skipped': True, 'reason': 'no group configured'}

    threshold = timezone.now() - timedelta(minutes=10)
    # Apenas chamados AINDA NÃO notificados (notif_aberto_enviada=False) →
    # cada chamado é avisado uma única vez.
    convs = list(Conversation.objects.filter(
        status__in=['new', 'open'],
        assigned_to__isnull=True,
        last_message_at__lt=threshold,
        notif_aberto_enviada=False,
    ).select_related('cliente', 'group'))

    if not convs:
        return {'notified': 0}

    # Monta UMA mensagem consolidada com todos os chamados pendentes
    linhas = [f"⚠️ *{len(convs)} chamado(s) sem atendimento!*", ""]
    for conv in convs:
        nome = conv.group.name if conv.group else 'Sem grupo'
        linhas.append(f"• *{nome}* — sem resposta há +10 min")
    linhas.append("")
    linhas.append("Acessem o sistema para assumir os chamados.")
    texto = "\n".join(linhas)

    # send_text retorna (bool, msg_id) — checar a tupla inteira com `if not ok`
    # nunca seria verdadeiro (tupla de 2 nunca é vazia), então uma falha real
    # de envio marcaria os chamados como notificados sem nunca ter avisado
    # ninguém — perdendo o alerta silenciosamente em vez de tentar de novo
    # no próximo ciclo.
    ok, _msg_id = client.send_text(notif_jid, texto, everyone=True)
    if not ok:
        logger.warning("Falha ao enviar notificação consolidada de chamados abertos")
        return {'notified': 0, 'error': 'send_failed'}

    # Marca todos como notificados para não repetir nos próximos ciclos
    ids = [c.id for c in convs]
    Conversation.objects.filter(id__in=ids).update(notif_aberto_enviada=True)
    return {'notified': len(ids)}


@shared_task
def escalar_chamados_sla():
    """A cada 10 min: verifica chamados com SLA de 1ª resposta estourado e
    ainda não escalados. Alerta o grupo de escalação configurado e, se
    houver um atendente de fallback configurado, reatribui automaticamente
    chamados sem atendente. Cada chamado escala uma única vez (campo
    `escalated`), assim como o mecanismo de `notificar_chamados_abertos`."""
    from .models import Conversation, SystemSetting, ContactGroup
    from django.contrib.auth.models import User

    if SystemSetting.get('escalacao_enabled', 'false') != 'true':
        return {'skipped': True, 'reason': 'disabled'}

    now = timezone.now()
    candidatos = list(Conversation.objects.filter(
        status__in=['new', 'open', 'pending'],
        sla_response_due_at__lt=now,
        first_response_at__isnull=True,
        escalated=False,
    ).select_related('cliente', 'group', 'assigned_to'))

    if not candidatos:
        return {'escalated': 0}

    # Reatribuição automática (opcional) — só para chamados sem atendente
    fallback_user_id = SystemSetting.get('escalacao_reassign_user_id', '').strip()
    fallback_user = User.objects.filter(id=fallback_user_id).first() if fallback_user_id else None

    reassigned = 0
    for conv in candidatos:
        if fallback_user and not conv.assigned_to:
            old_assigned_to_id = conv.assigned_to_id
            conv.assigned_to = fallback_user
            conv.save(update_fields=['assigned_to'])
            from .models import ConversationActivity
            ConversationActivity.objects.create(
                conversation=conv, actor=None, action='assigned',
                description='Reatribuído automaticamente por estouro de SLA',
                new_value=fallback_user.get_full_name() or fallback_user.username,
            )
            from .services import notify_reassignment
            notify_reassignment(conv, old_assigned_to_id)
            reassigned += 1

    # Alerta consolidado ao grupo de escalação (reaproveita o grupo de
    # "chamados abertos" se nenhum grupo específico de escalação foi definido)
    group_id = SystemSetting.get('escalacao_group_id', '').strip() or SystemSetting.get('notif_abertos_group_id', '').strip()
    if group_id:
        group = ContactGroup.objects.filter(id=group_id).select_related('connection').first()
        if group and group.connection and group.jid:
            from .services import EvolutionAPIClient
            linhas = [f"⏰ *{len(candidatos)} chamado(s) com SLA de resposta estourado!*", ""]
            for conv in candidatos:
                nome = conv.group.name if conv.group else 'Sem grupo'
                linhas.append(f"• *{nome}* — prioridade {conv.get_priority_display()}")
            linhas.append("")
            linhas.append("Acessem o sistema para assumir os chamados.")
            try:
                EvolutionAPIClient(group.connection).send_text(group.jid, "\n".join(linhas), everyone=True)
            except Exception as e:
                logger.warning(f"Falha ao enviar alerta de escalação: {e}")

    ids = [c.id for c in candidatos]
    Conversation.objects.filter(id__in=ids).update(escalated=True)

    # Avisa a tela em tempo real (toque de som distinto de SLA estourado)
    from .services import _ws_send_inbox
    for conv in candidatos:
        try:
            _ws_send_inbox({
                'type': 'sla_breach',
                'conversation_id': str(conv.id),
                'group_name': conv.group.name if conv.group else 'Sem grupo',
                'assigned_to_id': conv.assigned_to_id,
            })
        except Exception as e:
            logger.warning(f"Falha ao notificar SLA estourado via WS: {e}")

    return {'escalated': len(ids), 'reassigned': reassigned}


@shared_task
def enviar_alerta_diario():
    """
    Roda a cada 5 min. No horário configurado, envia resumo geral de atendimentos
    para o grupo configurado e marca todos os chamados com seus atendentes.
    Guard anti-duplo-envio: só dispara uma vez por dia.
    """
    from .models import Conversation, SystemSetting

    if SystemSetting.get('daily_alert_enabled', 'false') != 'true':
        return {'skipped': True, 'reason': 'disabled'}

    hora_cfg, min_cfg = _parse_time(SystemSetting.get('daily_alert_time', '08:00'))

    if not _is_time_now(hora_cfg, min_cfg):
        return {'skipped': True, 'reason': 'not alert time'}

    # Guard: evita envio duplo no mesmo dia
    if _already_sent_today('daily_alert_sent_date'):
        return {'skipped': True, 'reason': 'already sent today'}

    client, jid = _get_alert_client_and_jid()
    if not client:
        return {'skipped': True, 'reason': 'no group configured'}

    agora = timezone.localtime(timezone.now())

    abertos = Conversation.objects.filter(
        status__in=['new', 'open'],
        assigned_to__isnull=True,
    ).select_related('cliente', 'group').order_by('last_message_at')

    assumidos = Conversation.objects.filter(
        status__in=['new', 'open', 'pending'],
        assigned_to__isnull=False,
    ).select_related('cliente', 'group', 'assigned_to').order_by('assigned_to__first_name')

    em_tarefa = Conversation.objects.filter(
        status__in=['new', 'open', 'pending'],
        is_task_conv=True,
    ).select_related('group').count()

    resolvidos_hoje = Conversation.objects.filter(
        status__in=['resolved', 'closed'],
        closed_at__date=agora.date(),
    ).count()

    data_str = agora.strftime('%d/%m/%Y %H:%M')
    linhas = [
        f"📊 *RESUMO DIÁRIO DE ATENDIMENTOS*",
        f"📅 {data_str}",
        "",
    ]

    # Em aberto (sem atendente)
    linhas.append(f"📬 *EM ABERTO* (sem atendente): {abertos.count()}")
    if abertos.exists():
        for conv in abertos[:15]:
            linhas.append(f"  • {conv.group.name}")
        if abertos.count() > 15:
            linhas.append(f"  ...e mais {abertos.count() - 15}")
    else:
        linhas.append("  ✅ Nenhum chamado em aberto")
    linhas.append("")

    # Assumidos — agrupa por atendente
    linhas.append(f"👥 *ASSUMIDOS*: {assumidos.count()}")
    if assumidos.exists():
        by_agent: dict = {}
        for conv in assumidos:
            name = conv.assigned_to.get_full_name() or conv.assigned_to.username
            by_agent.setdefault(name, []).append(conv.group.name)
        for agent, grupos in by_agent.items():
            linhas.append(f"  👤 *{agent}* ({len(grupos)}):")
            for g in grupos[:10]:
                linhas.append(f"    • {g}")
            if len(grupos) > 10:
                linhas.append(f"    ...e mais {len(grupos) - 10}")
    else:
        linhas.append("  — Nenhum chamado assumido")
    linhas.append("")

    if em_tarefa:
        linhas.append(f"📌 *Em Tarefa*: {em_tarefa}")
        linhas.append("")

    linhas.append(f"✅ *Resolvidos hoje*: {resolvidos_hoje}")

    try:
        client.send_text(jid, "\n".join(linhas))
        _mark_sent_today('daily_alert_sent_date')
        logger.info(f"Alerta diário enviado para {jid}")
    except Exception as e:
        logger.error(f"Erro ao enviar alerta diário: {e}")
        return {'success': False, 'error': str(e)}

    return {
        'success': True,
        'abertos': abertos.count(),
        'assumidos': assumidos.count(),
        'em_tarefa': em_tarefa,
        'resolvidos': resolvidos_hoje,
    }


@shared_task
def enviar_lembretes_pessoais():
    """
    Roda a cada 5 min. Nos horários configurados (manhã e meio-dia),
    envia lembrete pessoal via WhatsApp para cada atendente cadastrado
    com seus chamados em aberto e tarefas pendentes.
    Guards anti-duplo-envio separados para cada turno.
    """
    from .models import SystemSetting

    if SystemSetting.get('daily_alert_enabled', 'false') != 'true':
        return {'skipped': True, 'reason': 'disabled'}

    agora = timezone.localtime(timezone.now())
    enviados = {}

    # Turno manhã — usa o mesmo horário do alerta diário como referência
    hora_manha, min_manha = _parse_time(
        SystemSetting.get('reminder_morning_time', SystemSetting.get('daily_alert_time', '08:00'))
    )
    # Turno meio-dia
    hora_meio, min_meio = _parse_time(
        SystemSetting.get('reminder_noon_time', '12:00')
    )

    if _is_time_now(hora_manha, min_manha) and not _already_sent_today('reminder_morning_sent_date'):
        n = _enviar_lembretes_atendentes(turno='manhã')
        _mark_sent_today('reminder_morning_sent_date')
        enviados['manha'] = n
        logger.info(f"Lembretes pessoais (manhã) enviados: {n}")

    if _is_time_now(hora_meio, min_meio) and not _already_sent_today('reminder_noon_sent_date'):
        n = _enviar_lembretes_atendentes(turno='meio-dia')
        _mark_sent_today('reminder_noon_sent_date')
        enviados['meio_dia'] = n
        logger.info(f"Lembretes pessoais (meio-dia) enviados: {n}")

    if not enviados:
        return {'skipped': True, 'reason': 'not reminder time'}

    return {'success': True, 'enviados': enviados}


def _enviar_lembretes_atendentes(test_mode=False, turno=''):
    """
    Envia lembrete WhatsApp individual para cada atendente com contato cadastrado.
    Inclui chamados assumidos e tarefas pendentes.
    """
    from .models import Conversation, AttendantContact, Task
    from .services import EvolutionAPIClient

    agora  = timezone.localtime(timezone.now())
    notificados = 0

    contacts = AttendantContact.objects.filter(
        reminders_enabled=True
    ).select_related('user', 'connection')

    for contact in contacts:
        if not contact.connection or not contact.phone:
            continue

        user = contact.user
        jid  = contact.get_jid()

        chamados = Conversation.objects.filter(
            status__in=['new', 'open', 'pending'],
            assigned_to=user,
        ).select_related('group').order_by('last_message_at')

        tarefas = Task.objects.filter(
            assigned_to=user,
            status__in=['pending', 'in_progress'],
        ).order_by('due_date')

        if not chamados.exists() and not tarefas.exists():
            continue

        turno_str = f" — {turno}" if turno else ""
        teste_str = " _(teste)_" if test_mode else ""
        nome      = user.get_full_name() or user.username

        linhas = [
            f"👋 *Olá, {nome}!*{teste_str}",
            f"📅 {agora.strftime('%d/%m/%Y %H:%M')}{turno_str}",
            "",
        ]

        if chamados.exists():
            linhas.append(f"📞 *Chamados em aberto* ({chamados.count()}):")
            for c in chamados[:10]:
                label = {'new': 'Novo', 'open': 'Aberto', 'pending': 'Aguardando'}.get(c.status, c.status)
                task_flag = " 📌" if c.is_task_conv else ""
                linhas.append(f"  • {c.group.name} [{label}]{task_flag}")
            if chamados.count() > 10:
                linhas.append(f"  ...e mais {chamados.count() - 10}")
            linhas.append("")

        if tarefas.exists():
            linhas.append(f"✅ *Tarefas pendentes* ({tarefas.count()}):")
            for t in tarefas[:10]:
                venc = ""
                if t.due_date:
                    venc = f" — prazo: {timezone.localtime(t.due_date).strftime('%d/%m %H:%M')}"
                    if t.is_overdue:
                        venc += " ⚠️ ATRASADA"
                linhas.append(f"  • {t.title}{venc}")
            if tarefas.count() > 10:
                linhas.append(f"  ...e mais {tarefas.count() - 10}")
            linhas.append("")

        linhas.append("Acesse o sistema para atualizar o status.")

        try:
            client = EvolutionAPIClient(contact.connection)
            ok = client.send_text(jid, "\n".join(linhas))
            if ok:
                notificados += 1
            else:
                logger.warning(f"Falha ao enviar lembrete para {user.username} ({jid})")
        except Exception as e:
            logger.error(f"Erro ao enviar lembrete para {user.username}: {e}")

    return notificados


def _run_alerta_diario_agora():
    """Executa o alerta diário + lembretes ignorando horário e guard (para testes)."""
    from .models import Conversation, SystemSetting

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

    em_tarefa = Conversation.objects.filter(
        status__in=['new', 'open', 'pending'], is_task_conv=True,
    ).select_related('group').count()

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
    if assumidos.exists():
        by_agent: dict = {}
        for conv in assumidos:
            name = conv.assigned_to.get_full_name() or conv.assigned_to.username
            by_agent.setdefault(name, []).append(conv.group.name)
        for agent, grupos in by_agent.items():
            linhas.append(f"  👤 *{agent}* ({len(grupos)}):")
            for g in grupos[:10]:
                linhas.append(f"    • {g}")
    else:
        linhas.append("  — Nenhum chamado assumido")
    linhas.append("")

    if em_tarefa:
        linhas.append(f"📌 *Em Tarefa*: {em_tarefa}")
        linhas.append("")

    linhas.append(f"✅ *Resolvidos hoje*: {resolvidos_hoje}")

    try:
        client.send_text(jid, "\n".join(linhas))
        n = _enviar_lembretes_atendentes(test_mode=True)
        return {'success': True, 'abertos': abertos.count(), 'assumidos': assumidos.count(), 'lembretes': n}
    except Exception as e:
        return {'success': False, 'error': str(e)}
