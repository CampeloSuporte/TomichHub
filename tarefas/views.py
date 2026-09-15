from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from clientes.decorators import backoffice_required, cliente_can_view_cliente, modulo_habilitado_required
from clientes.models import Cliente
from usuario.perms import is_backoffice, pode_acessar_cliente, portal_pode_usar_ferramenta

from .models import Rotina, RotinaItem, Tarefa, TarefaChecklistItem
from .services import (
    adicionar_item_checklist, gerar_ocorrencias_rotinas, instancia_da_tarefa, marcar_item_checklist,
    remover_item_checklist, usuarios_atribuiveis,
)


def _next_url(request):
    return request.POST.get('next') or request.META.get('HTTP_REFERER') or 'quadro_geral'


def _parse_prazo(valor):
    """`<input type="datetime-local">` sempre manda a data sem timezone —
    sem tornar isso aware no fuso do settings, comparar com timezone.now()
    (aware, USE_TZ=True) quebra com TypeError na primeira leitura de
    `Tarefa.atrasada` (ex: exatamente ao criar/editar a tarefa)."""
    if not valor:
        return None
    dt = parse_datetime(valor)
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def _get_tarefa_no_escopo(request, tarefa_id):
    """404 tanto se não existe quanto se está fora do escopo do usuário —
    não vaza se a tarefa existe em outra instância."""
    return get_object_or_404(Tarefa.objects.visiveis_para(request.user), pk=tarefa_id)


def _aplicar_responsaveis(tarefa, user_ids, cliente_para_elegibilidade):
    """user_ids: lista de ids (vazia = desatribuir todos). Sincroniza a
    lista inteira de responsáveis pro que foi enviado — mesma semântica de
    "o form é a fonte de verdade" que título/descrição já têm. Retorna
    False se algum id não for elegível pro escopo da tarefa (não mexe em
    nada nesse caso). Requer `tarefa` já salva (M2M precisa de pk)."""
    elegiveis = _responsaveis_elegiveis(user_ids, cliente_para_elegibilidade)
    if elegiveis is None:
        return False
    tarefa.responsaveis.set(elegiveis)
    if elegiveis and tarefa.status == Tarefa.STATUS_PENDENTE:
        tarefa.status = Tarefa.STATUS_ANDAMENTO
    return True


def _responsaveis_elegiveis(user_ids, cliente):
    """Lista de Users para os ids enviados, ou None se algum não for
    elegível para `cliente` (ver `usuarios_atribuiveis`)."""
    user_ids = [uid for uid in user_ids if uid]
    if not user_ids:
        return []
    elegiveis = list(usuarios_atribuiveis(cliente).filter(pk__in=user_ids))
    if len(elegiveis) != len(set(user_ids)):
        return None
    return elegiveis


@login_required(login_url='login')
@backoffice_required
def tarefa_criar(request):
    if request.method != 'POST':
        return redirect('quadro_geral')

    titulo = (request.POST.get('titulo') or '').strip()
    if not titulo:
        messages.error(request, 'Informe um título para a tarefa.')
        return redirect(_next_url(request))

    cliente = None
    cliente_id = request.POST.get('cliente_id')
    if cliente_id:
        cliente = Cliente.objects.visiveis_para(request.user).filter(pk=cliente_id).first()
        if not cliente:
            messages.error(request, 'Cliente inválido.')
            return redirect(_next_url(request))

    prazo = _parse_prazo(request.POST.get('prazo'))
    prioridade = request.POST.get('prioridade') or Tarefa.PRIORIDADE_MEDIA

    tarefa = Tarefa.objects.create(
        titulo=titulo,
        descricao=(request.POST.get('descricao') or '').strip(),
        cliente=cliente,
        instancia=instancia_da_tarefa(request.user, cliente),
        prioridade=prioridade,
        prazo=prazo,
        criado_por=request.user,
    )
    # "+ Adicionar tarefa" dentro de "Minhas Tarefas" já cria atribuída a quem clicou.
    if request.POST.get('atribuir_a_mim'):
        tarefa.responsaveis.add(request.user)
    _criar_itens_checklist(tarefa, _itens_checklist_do_post(request))
    messages.success(request, 'Tarefa criada com sucesso.')
    return redirect(_next_url(request))


@login_required(login_url='login')
@backoffice_required
def tarefa_editar(request, tarefa_id):
    tarefa = _get_tarefa_no_escopo(request, tarefa_id)
    if request.method != 'POST':
        return redirect('quadro_geral')

    titulo = (request.POST.get('titulo') or '').strip()
    if not titulo:
        messages.error(request, 'Informe um título para a tarefa.')
        return redirect(_next_url(request))

    cliente_anterior = tarefa.cliente
    cliente = cliente_anterior
    cliente_id = request.POST.get('cliente_id')
    if cliente_id:
        cliente = Cliente.objects.visiveis_para(request.user).filter(pk=cliente_id).first()
    elif 'cliente_id' in request.POST:
        cliente = None

    tarefa.titulo = titulo
    tarefa.descricao = (request.POST.get('descricao') or '').strip()
    tarefa.cliente = cliente
    if cliente != cliente_anterior:
        tarefa.instancia = instancia_da_tarefa(request.user, cliente)
    tarefa.prioridade = request.POST.get('prioridade') or tarefa.prioridade
    tarefa.prazo = _parse_prazo(request.POST.get('prazo'))

    novo_status = request.POST.get('status')
    if novo_status in dict(Tarefa.STATUS_CHOICES):
        tarefa.status = novo_status
        tarefa.concluida_em = timezone.now() if novo_status == Tarefa.STATUS_CONCLUIDA else None

    if not _aplicar_responsaveis(tarefa, request.POST.getlist('responsaveis'), tarefa.cliente):
        messages.error(request, 'Um ou mais responsáveis selecionados são inválidos para esta tarefa.')
        return redirect(_next_url(request))

    tarefa.save()
    messages.success(request, 'Tarefa atualizada.')
    return redirect(_next_url(request))


@login_required(login_url='login')
@backoffice_required
def tarefa_assumir(request, tarefa_id):
    """Adiciona `request.user` como (mais um) responsável — não substitui
    quem já estava, pra permitir vários atendentes na mesma tarefa."""
    tarefa = _get_tarefa_no_escopo(request, tarefa_id)
    if request.method == 'POST':
        tarefa.responsaveis.add(request.user)
        if tarefa.status == Tarefa.STATUS_PENDENTE:
            tarefa.status = Tarefa.STATUS_ANDAMENTO
            tarefa.save(update_fields=['status'])
        messages.success(request, 'Você entrou como responsável pela tarefa.')
    return redirect(_next_url(request))


@login_required(login_url='login')
@backoffice_required
def tarefa_status(request, tarefa_id):
    tarefa = _get_tarefa_no_escopo(request, tarefa_id)
    if request.method == 'POST':
        novo_status = request.POST.get('status')
        if novo_status in dict(Tarefa.STATUS_CHOICES):
            tarefa.status = novo_status
            tarefa.concluida_em = timezone.now() if novo_status == Tarefa.STATUS_CONCLUIDA else None
            tarefa.save()
            messages.success(request, 'Status atualizado.')
    return redirect(_next_url(request))


@login_required(login_url='login')
@backoffice_required
@require_POST
def tarefa_excluir(request, tarefa_id):
    """Exclui uma tarefa a partir do painel do dashboard.

    Escopo por `_get_tarefa_no_escopo` (404 fora da instância, sem revelar
    que a tarefa existe). Mesma regra do kanban da página do cliente
    (`tarefa_kanban_excluir`): back-office exclui qualquer tarefa que
    enxerga. A diferença é que aqui a tarefa pode não ter cliente — a de
    plataforma, que o kanban nem lista —, então a checagem não pode passar
    por `pode_acessar_cliente`.
    """
    tarefa = _get_tarefa_no_escopo(request, tarefa_id)
    titulo = tarefa.titulo
    tarefa.delete()
    messages.success(request, f'Tarefa "{titulo}" excluída.')
    return redirect(_next_url(request))


@login_required(login_url='login')
@backoffice_required
def tarefa_usuarios_json(request, tarefa_id):
    """Lista de usuários elegíveis pro seletor 'Responsáveis' (múltiplos)
    do modal de edição — escopado ao cliente da própria tarefa. Leva junto
    o checklist, que o mesmo modal mostra para marcar."""
    tarefa = _get_tarefa_no_escopo(request, tarefa_id)
    usuarios = usuarios_atribuiveis(tarefa.cliente)
    responsaveis_ids = list(tarefa.responsaveis.values_list('id', flat=True))
    return JsonResponse({
        'results': [{'id': u.id, 'nome': u.get_full_name() or u.username} for u in usuarios],
        'responsaveis_ids': responsaveis_ids,
        'status': tarefa.status,
        'adicionar_url': reverse('tarefa_checklist_adicionar', args=[tarefa.id]),
        'checklist': [_checklist_item_dict(i) for i in tarefa.checklist.select_related('verificado_por')],
    })


# ─────────────────────────────────────────────────────────────────────────
# CHECKLIST — marcar item como verificado (painel do dashboard e Kanban)
# ─────────────────────────────────────────────────────────────────────────

def _itens_checklist_do_post(request):
    return [t.strip()[:255] for t in request.POST.getlist('itens') if t.strip()]


def _criar_itens_checklist(tarefa, textos):
    TarefaChecklistItem.objects.bulk_create([
        TarefaChecklistItem(tarefa=tarefa, texto=texto, ordem=i) for i, texto in enumerate(textos)
    ])


def _checklist_item_dict(item):
    return {
        'id': item.id,
        'texto': item.texto,
        'verificado': item.verificado,
        'marcar_url': reverse('tarefa_checklist_marcar', args=[item.id]),
        'remover_url': reverse('tarefa_checklist_remover', args=[item.id]),
        'verificado_por_nome': (
            (item.verificado_por.get_full_name() or item.verificado_por.username)
            if item.verificado_por_id else ''
        ),
        'verificado_em_fmt': (
            timezone.localtime(item.verificado_em).strftime('%d/%m %H:%M') if item.verificado_em else ''
        ),
    }


def _pode_mexer_na_tarefa(user, tarefa):
    """Back-office: escopo por instância (vale para tarefa sem cliente).
    Portal do cliente final: mesmo acesso que já tem no Kanban — cliente
    dele e módulo Tarefas liberado no login."""
    if Tarefa.objects.visiveis_para(user).filter(pk=tarefa.pk).exists():
        return True
    if is_backoffice(user) or not tarefa.cliente_id:
        return False
    return pode_acessar_cliente(user, tarefa.cliente) and portal_pode_usar_ferramenta(user, 'tarefas')


@login_required(login_url='login')
@require_POST
def checklist_item_marcar(request, item_id):
    item = get_object_or_404(TarefaChecklistItem.objects.select_related('tarefa__cliente'), pk=item_id)
    if not _pode_mexer_na_tarefa(request.user, item.tarefa):
        # 404 e não 403, como no resto do app: não revela o item de outra instância.
        return JsonResponse({'success': False, 'error': 'Item não encontrado.'}, status=404)

    verificado = request.POST.get('verificado') in ('1', 'true', 'on')
    tarefa = marcar_item_checklist(item, request.user, verificado)
    return _resposta_checklist(request, tarefa, item)


@login_required(login_url='login')
@require_POST
def checklist_item_adicionar(request, tarefa_id):
    tarefa = get_object_or_404(Tarefa.objects.select_related('cliente'), pk=tarefa_id)
    if not _pode_mexer_na_tarefa(request.user, tarefa):
        return JsonResponse({'success': False, 'error': 'Tarefa não encontrada.'}, status=404)
    texto = (request.POST.get('texto') or '').strip()
    if not texto:
        return JsonResponse({'success': False, 'error': 'Escreva o item.'}, status=400)
    tarefa, item = adicionar_item_checklist(tarefa, texto)
    return _resposta_checklist(request, tarefa, item)


@login_required(login_url='login')
@require_POST
def checklist_item_remover(request, item_id):
    item = get_object_or_404(TarefaChecklistItem.objects.select_related('tarefa__cliente'), pk=item_id)
    if not _pode_mexer_na_tarefa(request.user, item.tarefa):
        return JsonResponse({'success': False, 'error': 'Item não encontrado.'}, status=404)
    tarefa = remover_item_checklist(item)
    return _resposta_checklist(request, tarefa)


def _resposta_checklist(request, tarefa, item=None):
    """Resposta comum de marcar/adicionar/remover: contagem, status e a
    tarefa inteira no formato do Kanban (que já traz o checklist)."""
    tarefa = (
        Tarefa.objects.select_related('criado_por')
        .prefetch_related('responsaveis', 'checklist__verificado_por')
        .get(pk=tarefa.pk)
    )
    feitos, total = tarefa.checklist_progresso
    dados = {
        'success': True,
        'feitos': feitos,
        'total': total,
        'status': tarefa.status,
        'tarefa': _tarefa_kanban_dict(tarefa, request),
    }
    if item is not None:
        dados['item'] = next(_checklist_item_dict(i) for i in tarefa.checklist.all() if i.pk == item.pk)
    return JsonResponse(dados)


# ─────────────────────────────────────────────────────────────────────────
# ROTINAS MENSAIS — form POST + redirect, igual às views do painel acima.
# Cada mês a rotina vira uma Tarefa comum; ver services.gerar_ocorrencias_rotinas.
# ─────────────────────────────────────────────────────────────────────────

def _dados_rotina_do_post(request):
    """Valida o form de rotina. Devolve (dados, erro)."""
    titulo = (request.POST.get('titulo') or '').strip()
    if not titulo:
        return None, 'Informe um título para a rotina.'
    try:
        dia = int(request.POST.get('dia_do_mes') or 0)
    except ValueError:
        dia = 0
    if not 1 <= dia <= 31:
        return None, 'Escolha um dia do mês entre 1 e 31.'
    itens = _itens_checklist_do_post(request)
    if not itens:
        return None, 'Adicione pelo menos um item ao checklist da rotina.'
    prioridade = request.POST.get('prioridade')
    if prioridade not in dict(Tarefa.PRIORIDADE_CHOICES):
        prioridade = Tarefa.PRIORIDADE_MEDIA
    return {
        'titulo': titulo[:255],
        'descricao': (request.POST.get('descricao') or '').strip(),
        'dia_do_mes': dia,
        'prioridade': prioridade,
        'itens': itens,
    }, None


def _salvar_itens_rotina(rotina, itens):
    rotina.itens.all().delete()
    RotinaItem.objects.bulk_create([
        RotinaItem(rotina=rotina, texto=texto, ordem=i) for i, texto in enumerate(itens)
    ])


def _mensagem_proxima(rotina, geradas):
    if geradas:
        return 'A tarefa deste mês já foi criada e está na lista.'
    if not rotina.ativa:
        return 'A rotina está pausada.'
    return f'Próxima ocorrência em {rotina.proxima_data():%d/%m/%Y}.'


def _get_rotina_no_escopo(request, rotina_id):
    return get_object_or_404(Rotina.objects.visiveis_para(request.user), pk=rotina_id)


@login_required(login_url='login')
@backoffice_required
@require_POST
def rotina_criar(request):
    dados, erro = _dados_rotina_do_post(request)
    if erro:
        messages.error(request, erro)
        return redirect(_next_url(request))

    cliente = None
    cliente_id = request.POST.get('cliente_id')
    if cliente_id:
        cliente = Cliente.objects.visiveis_para(request.user).filter(pk=cliente_id).first()
        if not cliente:
            messages.error(request, 'Cliente inválido.')
            return redirect(_next_url(request))

    rotina, geradas = _criar_rotina(request, dados, cliente)
    messages.success(request, f'Rotina criada. {_mensagem_proxima(rotina, geradas)}')
    return redirect(_next_url(request))


def _criar_rotina(request, dados, cliente):
    with transaction.atomic():
        rotina = Rotina.objects.create(
            titulo=dados['titulo'],
            descricao=dados['descricao'],
            cliente=cliente,
            instancia=instancia_da_tarefa(request.user, cliente),
            dia_do_mes=dados['dia_do_mes'],
            prioridade=dados['prioridade'],
            criado_por=request.user,
        )
        _salvar_itens_rotina(rotina, dados['itens'])
        if request.POST.get('atribuir_a_mim'):
            rotina.responsaveis.add(request.user)
    geradas = gerar_ocorrencias_rotinas(rotinas=Rotina.objects.filter(pk=rotina.pk))
    return rotina, geradas


@login_required(login_url='login')
@backoffice_required
@cliente_can_view_cliente
@modulo_habilitado_required('tarefas')
@require_POST
def rotina_kanban_criar(request, cliente_id):
    """Mesma criação do painel, pelo modal do Kanban do cliente (JSON).
    Só back-office: o portal do cliente final não cria rotina."""
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    dados, erro = _dados_rotina_do_post(request)
    if erro:
        return JsonResponse({'success': False, 'error': erro}, status=400)
    rotina, geradas = _criar_rotina(request, dados, cliente)
    return JsonResponse({
        'success': True,
        'mensagem': f'Rotina criada. {_mensagem_proxima(rotina, geradas)}',
        'gerada': bool(geradas),
    })


@login_required(login_url='login')
@backoffice_required
@require_POST
def rotina_editar(request, rotina_id):
    """Altera o modelo. A tarefa já gerada no mês fica como está (o
    checklist dela é uma cópia); a mudança vale a partir da próxima."""
    rotina = _get_rotina_no_escopo(request, rotina_id)
    dados, erro = _dados_rotina_do_post(request)
    if erro:
        messages.error(request, erro)
        return redirect(_next_url(request))

    cliente = rotina.cliente
    cliente_id = request.POST.get('cliente_id')
    if cliente_id:
        cliente = Cliente.objects.visiveis_para(request.user).filter(pk=cliente_id).first()
        if not cliente:
            messages.error(request, 'Cliente inválido.')
            return redirect(_next_url(request))
    elif 'cliente_id' in request.POST:
        cliente = None

    responsaveis = _responsaveis_elegiveis(request.POST.getlist('responsaveis'), cliente)
    if responsaveis is None:
        messages.error(request, 'Um ou mais responsáveis selecionados são inválidos para esta rotina.')
        return redirect(_next_url(request))

    with transaction.atomic():
        if cliente != rotina.cliente:
            rotina.instancia = instancia_da_tarefa(request.user, cliente)
        rotina.cliente = cliente
        rotina.titulo = dados['titulo']
        rotina.descricao = dados['descricao']
        rotina.dia_do_mes = dados['dia_do_mes']
        rotina.prioridade = dados['prioridade']
        rotina.save()
        _salvar_itens_rotina(rotina, dados['itens'])
        rotina.responsaveis.set(responsaveis)

    geradas = gerar_ocorrencias_rotinas(rotinas=Rotina.objects.filter(pk=rotina.pk))
    messages.success(request, f'Rotina atualizada. {_mensagem_proxima(rotina, geradas)}')
    return redirect(_next_url(request))


@login_required(login_url='login')
@backoffice_required
@require_POST
def rotina_ativar(request, rotina_id):
    """Pausa/retoma. Retomar zera o `inicio` para hoje: a ocorrência de um
    dia que passou enquanto estava pausada não é criada já atrasada."""
    rotina = _get_rotina_no_escopo(request, rotina_id)
    rotina.ativa = not rotina.ativa
    if rotina.ativa:
        rotina.inicio = timezone.localdate()
    rotina.save(update_fields=['ativa', 'inicio', 'atualizado_em'])

    if rotina.ativa:
        geradas = gerar_ocorrencias_rotinas(rotinas=Rotina.objects.filter(pk=rotina.pk))
        messages.success(request, f'Rotina retomada. {_mensagem_proxima(rotina, geradas)}')
    else:
        messages.success(request, 'Rotina pausada. Nenhuma tarefa nova será criada até retomar.')
    return redirect(_next_url(request))


@login_required(login_url='login')
@backoffice_required
@require_POST
def rotina_excluir(request, rotina_id):
    """Apaga só o modelo; as tarefas já geradas continuam (rotina=NULL)."""
    rotina = _get_rotina_no_escopo(request, rotina_id)
    titulo = rotina.titulo
    rotina.delete()
    messages.success(request, f'Rotina "{titulo}" excluída. As tarefas já criadas foram mantidas.')
    return redirect(_next_url(request))


@login_required(login_url='login')
@backoffice_required
def rotina_usuarios_json(request, rotina_id):
    rotina = _get_rotina_no_escopo(request, rotina_id)
    return JsonResponse({
        'results': [{'id': u.id, 'nome': u.get_full_name() or u.username} for u in usuarios_atribuiveis(rotina.cliente)],
        'responsaveis_ids': list(rotina.responsaveis.values_list('id', flat=True)),
        'itens': list(rotina.itens.values_list('texto', flat=True)),
    })


# ─────────────────────────────────────────────────────────────────────────
# KANBAN — aba "Tarefas" na página do cliente (clientes/templates/listar.html)
#
# Diferente das views acima (dashboard, form POST + redirect de página
# inteira, só back-office), estas são JSON puro e também acessíveis pelo
# portal do cliente final — quem pode ver o cliente pode ver e criar
# tarefas dele; mover/editar/excluir seguem o mesmo escopo, checado aqui
# via `pode_acessar_cliente` (não dá pra reaproveitar `_get_tarefa_no_escopo`,
# que é baseado em `visiveis_para`/instância e sempre nega pro portal).
# ─────────────────────────────────────────────────────────────────────────

def _tarefa_kanban_dict(t, request):
    # O banco devolve o prazo em UTC: sem localtime o cartão mostrava 3h a mais.
    prazo = timezone.localtime(t.prazo) if t.prazo else None
    feitos, total = t.checklist_progresso
    return {
        'id': t.id,
        'titulo': t.titulo,
        'descricao': t.descricao,
        'status': t.status,
        'prioridade': t.prioridade,
        'prazo': prazo.strftime('%Y-%m-%dT%H:%M') if prazo else '',
        'prazo_fmt': prazo.strftime('%d/%m %H:%M') if prazo else '',
        'atrasada': t.atrasada,
        'rotina': bool(t.rotina_id),
        'checklist': [_checklist_item_dict(i) for i in t.checklist.all()],
        'checklist_feitos': feitos,
        'checklist_total': total,
        'responsaveis': [
            {'id': u.id, 'nome': u.get_full_name() or u.username} for u in t.responsaveis.all()
        ],
        'criado_por_nome': (t.criado_por.get_full_name() or t.criado_por.username) if t.criado_por_id else '',
        'pode_gerenciar': is_backoffice(request.user) or t.criado_por_id == request.user.id,
    }


@login_required(login_url='login')
@cliente_can_view_cliente
@modulo_habilitado_required('tarefas')
def tarefas_kanban_json(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    tarefas = Tarefa.objects.do_cliente(cliente).order_by('-prioridade', 'prazo', '-criado_em')

    responsaveis = []
    if is_backoffice(request.user):
        responsaveis = [
            {'id': u.id, 'nome': u.get_full_name() or u.username}
            for u in usuarios_atribuiveis(cliente)
        ]

    rotinas = []
    if is_backoffice(request.user):
        hoje = timezone.localdate()
        rotinas = [
            {
                'id': r.id,
                'titulo': r.titulo,
                'dia_do_mes': r.dia_do_mes,
                'ativa': r.ativa,
                'itens_total': len(r.itens.all()),
                'proxima_fmt': r.proxima_data(hoje).strftime('%d/%m'),
            }
            for r in Rotina.objects.filter(cliente=cliente).prefetch_related('itens')
        ]

    return JsonResponse({
        'tarefas': [_tarefa_kanban_dict(t, request) for t in tarefas],
        'is_backoffice': is_backoffice(request.user),
        'responsaveis': responsaveis,
        'rotinas': rotinas,
    })


@login_required(login_url='login')
@cliente_can_view_cliente
@modulo_habilitado_required('tarefas')
@require_POST
def tarefa_kanban_criar(request, cliente_id):
    cliente = get_object_or_404(Cliente, pk=cliente_id)

    titulo = (request.POST.get('titulo') or '').strip()
    if not titulo:
        return JsonResponse({'success': False, 'error': 'Informe um título.'}, status=400)

    prioridade = request.POST.get('prioridade') or Tarefa.PRIORIDADE_MEDIA
    if prioridade not in dict(Tarefa.PRIORIDADE_CHOICES):
        prioridade = Tarefa.PRIORIDADE_MEDIA
    prazo = _parse_prazo(request.POST.get('prazo'))

    tarefa = Tarefa.objects.create(
        titulo=titulo,
        descricao=(request.POST.get('descricao') or '').strip(),
        cliente=cliente,
        instancia=instancia_da_tarefa(request.user, cliente),
        prioridade=prioridade,
        prazo=prazo,
        criado_por=request.user,
    )
    _criar_itens_checklist(tarefa, _itens_checklist_do_post(request))
    return JsonResponse({'success': True, 'tarefa': _tarefa_kanban_dict(tarefa, request)})


def _tarefa_kanban_com_permissao(request, tarefa_id):
    """Busca a tarefa e confere se `request.user` pode mexer nela (mesmo
    escopo de `pode_acessar_cliente` sobre o cliente da tarefa). Retorna
    (tarefa, None) ou (None, JsonResponse de erro)."""
    tarefa = get_object_or_404(Tarefa, pk=tarefa_id)
    if not tarefa.cliente_id or not pode_acessar_cliente(request.user, tarefa.cliente):
        return None, JsonResponse({'success': False, 'error': 'Sem permissão.'}, status=403)
    return tarefa, None


@login_required(login_url='login')
@modulo_habilitado_required('tarefas')
@require_POST
def tarefa_kanban_mover(request, tarefa_id):
    """Endpoint do drag-and-drop: só muda o status (coluna de destino)."""
    tarefa, erro = _tarefa_kanban_com_permissao(request, tarefa_id)
    if erro:
        return erro

    novo_status = request.POST.get('status')
    if novo_status not in dict(Tarefa.STATUS_CHOICES):
        return JsonResponse({'success': False, 'error': 'Status inválido.'}, status=400)

    tarefa.status = novo_status
    tarefa.concluida_em = timezone.now() if novo_status == Tarefa.STATUS_CONCLUIDA else None
    tarefa.save(update_fields=['status', 'concluida_em', 'atualizado_em'])
    # Arrastar pra "Em Andamento" sem ninguém responsável ainda = quem arrastou assumiu.
    if novo_status == Tarefa.STATUS_ANDAMENTO and not tarefa.responsaveis.exists() and is_backoffice(request.user):
        tarefa.responsaveis.add(request.user)
    return JsonResponse({'success': True, 'tarefa': _tarefa_kanban_dict(tarefa, request)})


@login_required(login_url='login')
@modulo_habilitado_required('tarefas')
@require_POST
def tarefa_kanban_editar(request, tarefa_id):
    tarefa, erro = _tarefa_kanban_com_permissao(request, tarefa_id)
    if erro:
        return erro

    titulo = (request.POST.get('titulo') or '').strip()
    if not titulo:
        return JsonResponse({'success': False, 'error': 'Informe um título.'}, status=400)

    tarefa.titulo = titulo
    tarefa.descricao = (request.POST.get('descricao') or '').strip()
    prioridade = request.POST.get('prioridade')
    if prioridade in dict(Tarefa.PRIORIDADE_CHOICES):
        tarefa.prioridade = prioridade
    tarefa.prazo = _parse_prazo(request.POST.get('prazo'))

    # Só back-office designa responsáveis — o seletor nem aparece pro portal.
    # Marcador explícito porque um <select multiple> sem nada marcado não
    # manda a chave "responsaveis" no POST — sem isso não daria pra
    # distinguir "quero limpar todo mundo" de "campo nem veio nesse form".
    if is_backoffice(request.user) and 'responsaveis_form_present' in request.POST:
        if not _aplicar_responsaveis(tarefa, request.POST.getlist('responsaveis'), tarefa.cliente):
            return JsonResponse({'success': False, 'error': 'Um ou mais responsáveis são inválidos.'}, status=400)

    tarefa.save()
    return JsonResponse({'success': True, 'tarefa': _tarefa_kanban_dict(tarefa, request)})


@login_required(login_url='login')
@modulo_habilitado_required('tarefas')
@require_POST
def tarefa_kanban_excluir(request, tarefa_id):
    tarefa, erro = _tarefa_kanban_com_permissao(request, tarefa_id)
    if erro:
        return erro

    if not is_backoffice(request.user) and tarefa.criado_por_id != request.user.id:
        return JsonResponse({'success': False, 'error': 'Você só pode excluir tarefas que você criou.'}, status=403)

    tarefa.delete()
    return JsonResponse({'success': True})
