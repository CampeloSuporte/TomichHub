"""
Tela do rack e a API JSON dela.

Mesma permissão da topologia: quem abre o editor de topologia do cliente
(`pode_acessar_cliente` + ferramenta 'topologia') vê os racks; login do
portal em modo somente leitura (`acessos_somente_leitura`) só olha.

Rotas AJAX respondem JSON 401/403 — o `@login_required` redirecionaria
para o login e o `response.json()` da tela estouraria num erro genérico.
Toda alteração devolve `estado` inteiro (ver `services.estado`).
"""
import json
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from clientes.decorators import modulo_habilitado_required
from clientes.models import Cliente, TopologiaDiagrama
from usuario import perms

from . import catalogo, services
from .models import ConexaoFisica, Rack, RackEquipamento
from .services import ErroRack

FERRAMENTA = 'topologia'


def _ferramenta_liberada(user):
    if perms.is_admin(user):
        return True
    if perms.is_consultor(user) or perms.is_operador(user):
        return perms.ferramenta_habilitada(user, FERRAMENTA)
    return perms.portal_pode_usar_ferramenta(user, FERRAMENTA)


def _api(escrita=False):
    """Autenticação + ferramenta + cliente, em JSON. A view recebe o
    `cliente` já resolvido pela função `resolver(request, **kwargs)`."""
    def decorator(resolver):
        def envolver(view):
            @wraps(view)
            def wrapper(request, **kwargs):
                if not request.user.is_authenticated:
                    return JsonResponse({'ok': False, 'erro': 'Sessão expirada. Entre novamente.'}, status=401)
                if not _ferramenta_liberada(request.user):
                    return JsonResponse({'ok': False, 'erro': 'Topologia não habilitada para você.'}, status=403)
                alvo = resolver(**kwargs)
                cliente = alvo if isinstance(alvo, Cliente) else alvo.cliente
                if not perms.pode_acessar_cliente(request.user, cliente):
                    return JsonResponse({'ok': False, 'erro': 'Sem permissão.'}, status=403)
                if escrita and perms.acessos_somente_leitura(request.user):
                    return JsonResponse({'ok': False, 'erro': 'Seu acesso é somente leitura.'}, status=403)
                try:
                    return view(request, alvo)
                except ErroRack as e:
                    return JsonResponse({'ok': False, 'erro': str(e)}, status=400)
            return wrapper
        return envolver
    return decorator


def _cliente(cliente_id):
    return get_object_or_404(Cliente, id=cliente_id)


def _rack(rack_id):
    return get_object_or_404(Rack.objects.select_related('cliente'), id=rack_id)


def _equipamento(equipamento_id):
    eq = get_object_or_404(RackEquipamento.objects.select_related('rack__cliente'), id=equipamento_id)
    eq.cliente = eq.rack.cliente
    return eq


def _conexao(conexao_id):
    return get_object_or_404(ConexaoFisica.objects.select_related('cliente'), id=conexao_id)


def _corpo(request):
    try:
        dados = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        raise ErroRack('Requisição inválida.')
    if not isinstance(dados, dict):
        raise ErroRack('Requisição inválida.')
    return dados


def _ok(cliente, **extra):
    return JsonResponse({'ok': True, 'estado': services.estado(cliente), **extra})


# ── Tela ─────────────────────────────────────────────────────────────────────

@login_required(login_url='login')
@modulo_habilitado_required(FERRAMENTA)
def tela(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    if not perms.pode_acessar_cliente(request.user, cliente):
        return JsonResponse({'error': 'Sem permissao'}, status=403)
    diagrama_id = request.GET.get('diagrama') or ''
    if diagrama_id and not TopologiaDiagrama.objects.filter(id=diagrama_id, cliente=cliente).exists():
        diagrama_id = ''
    return render(request, 'racks/rack_builder.html', {
        'cliente': cliente,
        'diagrama_id': diagrama_id,
        'somente_leitura': perms.acessos_somente_leitura(request.user),
        'catalogo_json': json.dumps(catalogo.catalogo_para_tela(), ensure_ascii=False),
    })


# ── API ──────────────────────────────────────────────────────────────────────

@require_GET
@_api()(_cliente)
def api_estado(request, cliente):
    return JsonResponse({'ok': True, 'estado': services.estado(cliente)})


@require_GET
@_api()(_cliente)
def api_posicoes(request, cliente):
    """Posição no rack de cada node — botão de rack nos hosts da topologia."""
    return JsonResponse({'ok': True, **services.posicoes(cliente)})


@require_GET
@_api()(_cliente)
def api_link(request, cliente):
    """Situação de um enlace da topologia — painel do link no editor."""
    link_id = (request.GET.get('link') or '').strip()
    achados = services.links_topologia(cliente, link_id=link_id) if link_id else []
    link = achados[0] if achados else None
    conexao = None
    if link and link['conexao_id']:
        conexao = services.conexao_dict(ConexaoFisica.objects.get(id=link['conexao_id']))
    return JsonResponse({'ok': True, 'link': link, 'conexao': conexao})


@require_POST
@_api(escrita=True)(_cliente)
def api_rack_criar(request, cliente):
    rack = services.criar_rack(cliente, _corpo(request), request.user)
    return _ok(cliente, rack_id=rack.id)


@require_POST
@_api(escrita=True)(_rack)
def api_rack_editar(request, rack):
    services.atualizar_rack(rack, _corpo(request))
    return _ok(rack.cliente)


@require_POST
@_api(escrita=True)(_rack)
def api_rack_excluir(request, rack):
    cliente = rack.cliente
    rack.delete()
    return _ok(cliente)


@require_POST
@_api(escrita=True)(_rack)
def api_equipamento_criar(request, rack):
    eq = services.montar_equipamento(rack, _corpo(request))
    return _ok(rack.cliente, equipamento_id=eq.id)


@require_POST
@_api(escrita=True)(_equipamento)
def api_equipamento_editar(request, eq):
    services.atualizar_equipamento(eq, _corpo(request))
    return _ok(eq.cliente)


@require_POST
@_api(escrita=True)(_equipamento)
def api_equipamento_excluir(request, eq):
    eq.delete()
    return _ok(eq.cliente)


@require_POST
@_api(escrita=True)(_cliente)
def api_conexao_criar(request, cliente):
    conexao = services.criar_conexao(cliente, _corpo(request), request.user)
    return _ok(cliente, conexao_id=conexao.id)


@require_POST
@_api(escrita=True)(_cliente)
def api_conexao_do_link(request, cliente):
    dados = _corpo(request)
    conexao = services.criar_conexao_do_link(cliente, dados.get('link_id'), dados, request.user)
    return _ok(cliente, conexao_id=conexao.id)


@require_POST
@_api(escrita=True)(_conexao)
def api_conexao_editar(request, conexao):
    services.atualizar_conexao(conexao, _corpo(request))
    return _ok(conexao.cliente)


@require_POST
@_api(escrita=True)(_conexao)
def api_conexao_excluir(request, conexao):
    cliente = conexao.cliente
    conexao.delete()
    return _ok(cliente)
