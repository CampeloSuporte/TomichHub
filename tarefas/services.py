import logging
from datetime import datetime, time

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from usuario.perms import is_admin, get_instancia, is_backoffice

logger = logging.getLogger(__name__)


def instancia_da_tarefa(user, cliente):
    """Instância a gravar na tarefa: a do cliente (se houver), senão a de
    quem está criando (Consultor/Operador); Administrador sem cliente cria
    tarefa de plataforma (instancia=None, só ele vê)."""
    if cliente is not None:
        return cliente.instancia
    return get_instancia(user)


def usuarios_atribuiveis(cliente):
    """Quem pode aparecer no seletor de responsáveis (múltiplos) de uma
    tarefa vinculada a `cliente` (ou None, tarefa de plataforma):
    - Administradores reais do sistema — sempre, independente do cliente.
      Exclui contas sem e-mail: sobra de cadastro/instância de teste que
      nunca correspondeu a uma pessoa de verdade (mesmo filtro que
      `atendimento.views.api_agents_list` já usa pra não vazar "conta
      fantasma" no seletor de transferir chamado).
    - Atendentes (Consultor/Operador) da instância do cliente, se houver.
    - Usuários de portal vinculados a esse cliente (principal +
      adicionais) — pra que o próprio cliente possa participar do vínculo.
    """
    admins = User.objects.filter(is_active=True).filter(
        Q(perfil__role='admin') | Q(is_staff=True, perfil__isnull=True)
    ).exclude(email='')

    instancia = cliente.instancia if cliente is not None else None
    atendentes = (
        User.objects.filter(is_active=True, perfil__instancia=instancia)
        if instancia is not None else User.objects.none()
    )

    portal = (
        User.objects.filter(is_active=True).filter(Q(cliente=cliente) | Q(clientes_adicionais=cliente))
        if cliente is not None else User.objects.none()
    )

    return (admins | atendentes | portal).distinct().order_by('first_name', 'username')


# ─────────────────────────────────────────────────────────────────────────
# ROTINAS MENSAIS
# ─────────────────────────────────────────────────────────────────────────

def gerar_ocorrencias_rotinas(hoje=None, rotinas=None):
    """Cria a Tarefa do mês de cada rotina ativa cujo dia já chegou.

    Idempotente: roda de hora em hora pelo beat e também logo depois de
    criar/editar/retomar uma rotina. `Rotina.ultima_competencia` é o que
    garante uma por mês — não a existência da tarefa, senão excluir a
    tarefa do mês faria ela voltar na hora seguinte. Não recupera meses
    passados: se o worker ficou parado o mês inteiro, aquele mês fica sem
    ocorrência, em vez de despejar tarefas velhas de uma vez.
    """
    from .models import Rotina, Tarefa, TarefaChecklistItem

    hoje = hoje or timezone.localdate()
    competencia = hoje.replace(day=1)
    qs = rotinas if rotinas is not None else Rotina.objects.all()
    candidatas = qs.filter(ativa=True).filter(
        Q(ultima_competencia__isnull=True) | Q(ultima_competencia__lt=competencia)
    ).values_list('pk', flat=True)

    criadas = []
    for rotina_id in list(candidatas):
        with transaction.atomic():
            rotina = Rotina.objects.select_for_update().filter(pk=rotina_id).first()
            if rotina is None or not rotina.ativa:
                continue
            if rotina.ultima_competencia and rotina.ultima_competencia >= competencia:
                continue
            data = rotina.data_no_mes(competencia)
            if data > hoje or data < rotina.inicio:
                continue

            tarefa = Tarefa.objects.create(
                titulo=rotina.titulo,
                descricao=rotina.descricao,
                cliente_id=rotina.cliente_id,
                instancia_id=rotina.instancia_id,
                prioridade=rotina.prioridade,
                prazo=timezone.make_aware(datetime.combine(data, time(23, 59))),
                criado_por_id=rotina.criado_por_id,
                rotina=rotina,
                competencia=competencia,
            )
            tarefa.responsaveis.set(rotina.responsaveis.all())
            TarefaChecklistItem.objects.bulk_create([
                TarefaChecklistItem(tarefa=tarefa, texto=item.texto, ordem=item.ordem)
                for item in rotina.itens.all()
            ])
            rotina.ultima_competencia = competencia
            rotina.save(update_fields=['ultima_competencia'])
            criadas.append(tarefa)

    if criadas:
        logger.info('Rotinas: %d tarefa(s) gerada(s) para %s', len(criadas), competencia.strftime('%m/%Y'))
    return criadas


def marcar_item_checklist(item, user, verificado):
    """Marca/desmarca um item e ajusta a tarefa junto:
    - primeiro item marcado numa pendente → Em Andamento (e quem marcou
      assume, se ninguém tinha assumido — mesma regra do arrastar no Kanban);
    - todos marcados → Concluída;
    - desmarcar um item de uma Concluída → volta pra Em Andamento.
    Cancelada não muda de status. Devolve a tarefa atualizada."""
    from .models import Tarefa

    with transaction.atomic():
        tarefa = Tarefa.objects.select_for_update().get(pk=item.tarefa_id)
        item.verificado = verificado
        item.verificado_por = user if verificado else None
        item.verificado_em = timezone.now() if verificado else None
        item.save(update_fields=['verificado', 'verificado_por', 'verificado_em'])

        ajustar_status_pelo_checklist(tarefa)

        if verificado and is_backoffice(user) and not tarefa.responsaveis.exists():
            tarefa.responsaveis.add(user)
    return tarefa


def ajustar_status_pelo_checklist(tarefa):
    """Status que o checklist implica: todos verificados → Concluída; algum
    faltando numa Concluída → Em Andamento; algum verificado numa Pendente →
    Em Andamento. Tarefa sem itens ou Cancelada fica como está (remover o
    último item não reabre uma tarefa concluída)."""
    from .models import Tarefa

    total = tarefa.checklist.count()
    if not total or tarefa.status == Tarefa.STATUS_CANCELADA:
        return tarefa
    feitos = tarefa.checklist.filter(verificado=True).count()
    status_antes = tarefa.status
    if feitos == total:
        if tarefa.status != Tarefa.STATUS_CONCLUIDA:
            tarefa.status = Tarefa.STATUS_CONCLUIDA
            tarefa.concluida_em = timezone.now()
    elif tarefa.status == Tarefa.STATUS_CONCLUIDA:
        tarefa.status = Tarefa.STATUS_ANDAMENTO
        tarefa.concluida_em = None
    elif feitos and tarefa.status == Tarefa.STATUS_PENDENTE:
        tarefa.status = Tarefa.STATUS_ANDAMENTO
    if tarefa.status != status_antes:
        tarefa.save(update_fields=['status', 'concluida_em', 'atualizado_em'])
    return tarefa


def adicionar_item_checklist(tarefa, texto):
    """Item novo vai para o fim. Numa tarefa concluída, reabre (agora falta um)."""
    from django.db.models import Max
    from .models import Tarefa, TarefaChecklistItem

    with transaction.atomic():
        tarefa = Tarefa.objects.select_for_update().get(pk=tarefa.pk)
        ultima = tarefa.checklist.aggregate(m=Max('ordem'))['m']
        item = TarefaChecklistItem.objects.create(
            tarefa=tarefa, texto=texto[:255], ordem=0 if ultima is None else ultima + 1,
        )
        ajustar_status_pelo_checklist(tarefa)
    return tarefa, item


def remover_item_checklist(item):
    """Remover o único item que faltava conclui a tarefa."""
    from .models import Tarefa

    with transaction.atomic():
        tarefa = Tarefa.objects.select_for_update().get(pk=item.tarefa_id)
        item.delete()
        ajustar_status_pelo_checklist(tarefa)
    return tarefa
