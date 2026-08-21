from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from clientes.decorators import backoffice_required, cliente_can_view_cliente, modulo_habilitado_required
from clientes.models import Cliente
from usuario.perms import is_backoffice, pode_acessar_cliente

from .models import Tarefa
from .services import instancia_da_tarefa, usuarios_atribuiveis


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
    user_ids = [uid for uid in user_ids if uid]
    if not user_ids:
        tarefa.responsaveis.set([])
        return True
    elegiveis = list(usuarios_atribuiveis(cliente_para_elegibilidade).filter(pk__in=user_ids))
    if len(elegiveis) != len(set(user_ids)):
        return False
    tarefa.responsaveis.set(elegiveis)
    if tarefa.status == Tarefa.STATUS_PENDENTE:
        tarefa.status = Tarefa.STATUS_ANDAMENTO
    return True


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

    Tarefa.objects.create(
        titulo=titulo,
        descricao=(request.POST.get('descricao') or '').strip(),
        cliente=cliente,
        instancia=instancia_da_tarefa(request.user, cliente),
        prioridade=prioridade,
        prazo=prazo,
        criado_por=request.user,
    )
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
    do modal de edição — escopado ao cliente da própria tarefa."""
    tarefa = _get_tarefa_no_escopo(request, tarefa_id)
    usuarios = usuarios_atribuiveis(tarefa.cliente)
    responsaveis_ids = list(tarefa.responsaveis.values_list('id', flat=True))
    return JsonResponse({
        'results': [{'id': u.id, 'nome': u.get_full_name() or u.username} for u in usuarios],
        'responsaveis_ids': responsaveis_ids,
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
    return {
        'id': t.id,
        'titulo': t.titulo,
        'descricao': t.descricao,
        'status': t.status,
        'prioridade': t.prioridade,
        'prazo': t.prazo.strftime('%Y-%m-%dT%H:%M') if t.prazo else '',
        'prazo_fmt': t.prazo.strftime('%d/%m %H:%M') if t.prazo else '',
        'atrasada': t.atrasada,
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

    return JsonResponse({
        'tarefas': [_tarefa_kanban_dict(t, request) for t in tarefas],
        'is_backoffice': is_backoffice(request.user),
        'responsaveis': responsaveis,
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
