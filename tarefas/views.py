from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from clientes.decorators import backoffice_required
from clientes.models import Cliente

from .models import Tarefa
from .services import instancia_da_tarefa, usuarios_atribuiveis


def _next_url(request):
    return request.POST.get('next') or request.META.get('HTTP_REFERER') or 'quadro_geral'


def _get_tarefa_no_escopo(request, tarefa_id):
    """404 tanto se não existe quanto se está fora do escopo do usuário —
    não vaza se a tarefa existe em outra instância."""
    return get_object_or_404(Tarefa.objects.visiveis_para(request.user), pk=tarefa_id)


def _aplicar_responsavel(tarefa, user_id, instancia_para_elegibilidade):
    """user_id vazio = desatribuir. Retorna False se user_id inválido pro
    escopo da tarefa (não mexe em nada nesse caso)."""
    if not user_id:
        tarefa.assigned_to = None
        return True
    elegivel = usuarios_atribuiveis(instancia_para_elegibilidade).filter(pk=user_id).first()
    if not elegivel:
        return False
    tarefa.assigned_to = elegivel
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

    prazo = parse_datetime(request.POST.get('prazo')) if request.POST.get('prazo') else None
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
    tarefa.prazo = parse_datetime(request.POST.get('prazo')) if request.POST.get('prazo') else None

    novo_status = request.POST.get('status')
    if novo_status in dict(Tarefa.STATUS_CHOICES):
        tarefa.status = novo_status
        tarefa.concluida_em = timezone.now() if novo_status == Tarefa.STATUS_CONCLUIDA else None

    if 'user_id' in request.POST:
        if not _aplicar_responsavel(tarefa, request.POST.get('user_id'), tarefa.instancia):
            messages.error(request, 'Usuário inválido para esta tarefa.')
            return redirect(_next_url(request))

    tarefa.save()
    messages.success(request, 'Tarefa atualizada.')
    return redirect(_next_url(request))


@login_required(login_url='login')
@backoffice_required
def tarefa_assumir(request, tarefa_id):
    tarefa = _get_tarefa_no_escopo(request, tarefa_id)
    if request.method == 'POST':
        tarefa.assigned_to = request.user
        if tarefa.status == Tarefa.STATUS_PENDENTE:
            tarefa.status = Tarefa.STATUS_ANDAMENTO
        tarefa.save()
        messages.success(request, 'Você assumiu a tarefa.')
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
def tarefa_usuarios_json(request, tarefa_id):
    """Lista de usuários elegíveis pro seletor 'Responsável' do modal de
    edição — escopado à instância da própria tarefa."""
    tarefa = _get_tarefa_no_escopo(request, tarefa_id)
    usuarios = usuarios_atribuiveis(tarefa.instancia)
    return JsonResponse({
        'results': [{'id': u.id, 'nome': u.get_full_name() or u.username} for u in usuarios]
    })
