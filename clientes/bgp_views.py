"""
clientes/bgp_views.py
Views da automação BGP: visualização do snapshot (sessões + anúncios
simulados) e execução de ações (ativar/desativar sessão, prepend, parar de
anunciar) num equipamento real. Restrito a staff/superuser — é engenharia
de rede em produção, não uma ferramenta de portal de cliente.
"""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .bgp_actions import (
    AcaoBgpNaoSuportada,
    comandos_aplicar_community,
    comandos_novo_anuncio,
    comandos_parar_anuncio,
    comandos_prepend,
    comandos_toggle_sessao,
    executar_acao_bgp,
)
from .bgp_matcher import escanear_prefix_lists
from .models import Acesso, AcaoBgp, BgpCommunity, BgpSnapshot

logger = logging.getLogger(__name__)


def _checar_staff(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Sem permissão'}, status=403)
    return None


@login_required(login_url='login')
def bgp_page(request, acesso_id):
    """GET /clientes/bgp/<acesso_id>/ — página da automação BGP do host."""
    if not (request.user.is_staff or request.user.is_superuser):
        return render(request, 'terminal_link_invalido.html',
                       {'motivo': 'Sem permissão para acessar esta tela.'}, status=403)
    acesso = get_object_or_404(Acesso, id=acesso_id)
    return render(request, 'bgp_automacao.html', {
        'acesso': acesso,
        'acesso_id': acesso.id,
    })


@login_required(login_url='login')
@require_http_methods(["GET"])
def bgp_dados(request, acesso_id):
    """GET /clientes/bgp/<acesso_id>/dados/ — snapshot atual em JSON."""
    erro = _checar_staff(request)
    if erro:
        return erro
    try:
        snap = BgpSnapshot.objects.select_related('acesso').get(acesso_id=acesso_id)
    except BgpSnapshot.DoesNotExist:
        return JsonResponse({'error': 'Sem snapshot BGP para este host ainda.'}, status=404)
    return JsonResponse({
        'vendor': snap.vendor,
        'gerado_em': timezone.localtime(snap.gerado_em).strftime('%d/%m/%Y %H:%M'),
        'erro': snap.erro,
        'dados': snap.dados,
    })


@login_required(login_url='login')
@require_http_methods(["POST"])
def bgp_atualizar_snapshot(request, acesso_id):
    """
    POST /clientes/bgp/<acesso_id>/atualizar/ — refaz a extração+simulação
    desse host agora, sem esperar a rotina noturna (02:45). Só lê o backup
    mais recente já salvo em disco e roda regex — não conecta em nada, não
    precisa de Celery, roda síncrono na própria request.
    """
    erro = _checar_staff(request)
    if erro:
        return erro
    acesso = get_object_or_404(Acesso, id=acesso_id)

    from .tasks import _atualizar_snapshot_bgp_de_acesso
    resultado, detalhe = _atualizar_snapshot_bgp_de_acesso(acesso)

    if resultado == 'ok':
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'status': resultado, 'error': detalhe or resultado}, status=422)


def _montar_comandos(tipo, vendor, dados, alvo, params):
    if tipo == 'ativar_sessao':
        return comandos_toggle_sessao(vendor, dados, alvo, ativar=True)
    if tipo == 'desativar_sessao':
        return comandos_toggle_sessao(vendor, dados, alvo, ativar=False)
    if tipo == 'prepend':
        nome_sessao = params.get('sessao', '')
        delta = int(params.get('delta', 1))
        return comandos_prepend(vendor, dados, nome_sessao, alvo, delta=delta)
    if tipo == 'parar_anuncio':
        nome_sessao = params.get('sessao', '')
        return comandos_parar_anuncio(vendor, dados, nome_sessao, alvo)
    if tipo == 'community':
        nome_sessao = params.get('sessao', '')
        valor = params.get('valor', '')
        label = params.get('label', '')
        return comandos_aplicar_community(vendor, dados, nome_sessao, alvo, valor, label=label)
    if tipo == 'novo_anuncio':
        nome_sessao = params.get('sessao', '')
        lista = params.get('lista') or None
        return comandos_novo_anuncio(vendor, dados, nome_sessao, alvo, lista_escolhida=lista)
    raise AcaoBgpNaoSuportada(f'Tipo de ação "{tipo}" desconhecido.')


@login_required(login_url='login')
@require_http_methods(["POST"])
def bgp_escanear_prefixo(request, acesso_id):
    """
    POST /clientes/bgp/<acesso_id>/escanear-prefixo/ — body {sessao, prefixo?}.
    Leitura pura sobre o snapshot já em memória (não toca em nada): varre as
    prefix-lists já usadas pelo export policy dessa sessão. `prefixo` é
    opcional — sem ele, devolve só as prefix-lists candidatas (pra UI
    mostrar de cara, antes do usuário escolher uma e digitar o prefixo);
    com ele, confere também se aquele prefixo novo já bate em alguma delas
    (nada a fazer) ou quais são candidatas pra adicionar uma entrada nova.
    """
    erro = _checar_staff(request)
    if erro:
        return erro
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    sessao_nome = (body.get('sessao') or '').strip()
    prefixo = (body.get('prefixo') or '').strip()
    if not sessao_nome:
        return JsonResponse({'error': 'Informe a sessão.'}, status=400)

    try:
        snap = BgpSnapshot.objects.get(acesso_id=acesso_id)
    except BgpSnapshot.DoesNotExist:
        return JsonResponse({'error': 'Sem snapshot BGP para este host.'}, status=404)

    sessao = next((s for s in snap.dados.get('sessoes', []) if s.get('nome') == sessao_nome), None)
    if not sessao:
        return JsonResponse({'error': f'Sessão "{sessao_nome}" não encontrada no snapshot.'}, status=404)
    policy_out = sessao.get('policy_out')
    if not policy_out:
        return JsonResponse({'error': 'Esta sessão não tem export policy identificada.'}, status=422)

    resultado = escanear_prefix_lists(
        snap.dados.get('prefix_lists', {}), snap.dados.get('policies', {}), policy_out, prefixo or None
    )
    resultado['vendor'] = snap.vendor
    return JsonResponse(resultado)


@login_required(login_url='login')
@require_http_methods(["GET"])
def bgp_communities_listar(request, acesso_id):
    """GET /clientes/bgp/<acesso_id>/communities/[?sessao=NOME] — communities
    cadastradas pro host. Sem `?sessao=`, devolve TODAS agrupadas por sessão
    de uma vez (`{"communities": {"<sessao_nome>": [...], ...}}`) — a UI
    carrega isso uma vez só no load da página em vez de um fetch por sessão."""
    erro = _checar_staff(request)
    if erro:
        return erro
    sessao_nome = request.GET.get('sessao')
    qs = BgpCommunity.objects.filter(acesso_id=acesso_id)
    if sessao_nome:
        qs = qs.filter(sessao_nome=sessao_nome)
    agrupado = {}
    for c in qs.order_by('sessao_nome', 'label'):
        agrupado.setdefault(c.sessao_nome, []).append({'id': c.id, 'label': c.label, 'valor': c.valor})
    return JsonResponse({'communities': agrupado})


@login_required(login_url='login')
@require_http_methods(["POST"])
def bgp_communities_criar(request, acesso_id):
    """POST /clientes/bgp/<acesso_id>/communities/ — body {sessao, label, valor}."""
    erro = _checar_staff(request)
    if erro:
        return erro
    acesso = get_object_or_404(Acesso, id=acesso_id)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    sessao_nome = (body.get('sessao') or '').strip()
    label       = (body.get('label') or '').strip()
    valor       = (body.get('valor') or '').strip()
    if not sessao_nome or not label or not valor:
        return JsonResponse({'error': 'Sessão, rótulo e valor são obrigatórios.'}, status=400)
    if len(label) > 100 or len(valor) > 255:
        return JsonResponse({'error': 'Rótulo ou valor longo demais.'}, status=400)

    try:
        community = BgpCommunity.objects.create(
            acesso=acesso, sessao_nome=sessao_nome, label=label, valor=valor,
            criado_por=request.user,
        )
    except Exception as e:
        # provável violação do unique_together (label repetido nessa sessão)
        return JsonResponse({'error': f'Não foi possível salvar: {e}'}, status=400)
    return JsonResponse({'id': community.id, 'label': community.label, 'valor': community.valor})


@login_required(login_url='login')
@require_http_methods(["POST"])
def bgp_communities_deletar(request, acesso_id, community_id):
    """POST /clientes/bgp/<acesso_id>/communities/<community_id>/deletar/"""
    erro = _checar_staff(request)
    if erro:
        return erro
    community = get_object_or_404(BgpCommunity, id=community_id, acesso_id=acesso_id)
    community.delete()
    return JsonResponse({'status': 'ok'})


@login_required(login_url='login')
@require_http_methods(["POST"])
def bgp_executar_acao(request, acesso_id):
    """
    POST /clientes/bgp/<acesso_id>/acao/
    body: {"tipo", "alvo", "params": {...}, "preview": bool, "comandos": [...]}

    `preview=true` só monta e devolve os comandos gerados automaticamente,
    sem tocar no equipamento — é o que a UI usa pra preencher o textarea
    editável do modal de confirmação. `preview=false` executa de verdade e
    grava AcaoBgp; se o body trouxer `comandos` (o texto do modal, possivelmente
    editado à mão — ex: trocar o ASN usado no prepend), usa exatamente esses
    comandos em vez de gerar de novo — dá pra revisar/ajustar antes de confirmar.
    """
    erro = _checar_staff(request)
    if erro:
        return erro

    acesso = get_object_or_404(Acesso, id=acesso_id)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    tipo = body.get('tipo', '')
    alvo = body.get('alvo', '')
    params = body.get('params') or {}
    preview = bool(body.get('preview', True))
    comandos_editados = body.get('comandos')

    try:
        snap = BgpSnapshot.objects.get(acesso_id=acesso_id)
    except BgpSnapshot.DoesNotExist:
        return JsonResponse({'error': 'Sem snapshot BGP para este host — aguarde a próxima atualização noturna.'}, status=404)

    if preview:
        # Preview sempre gera do zero — é o texto inicial que preenche o
        # textarea editável do modal, nunca deve refletir uma edição anterior.
        try:
            comandos = _montar_comandos(tipo, snap.vendor, snap.dados, alvo, params)
        except AcaoBgpNaoSuportada as e:
            return JsonResponse({'error': str(e)}, status=422)
        return JsonResponse({'comandos': comandos})

    if isinstance(comandos_editados, list) and comandos_editados and all(isinstance(c, str) for c in comandos_editados):
        if len(comandos_editados) > 30 or any(len(c) > 500 for c in comandos_editados):
            return JsonResponse({'error': 'Comando editado longo demais — revise antes de enviar.'}, status=400)
        comandos = [c.strip() for c in comandos_editados if c.strip()]
    else:
        try:
            comandos = _montar_comandos(tipo, snap.vendor, snap.dados, alvo, params)
        except AcaoBgpNaoSuportada as e:
            return JsonResponse({'error': str(e)}, status=422)

    output, status = executar_acao_bgp(acesso, snap.vendor, comandos)
    AcaoBgp.objects.create(
        acesso=acesso, usuario=request.user, tipo=tipo, alvo=alvo,
        comandos='\n'.join(comandos), output=output, status=status,
    )
    return JsonResponse({'status': status, 'output': output, 'comandos': comandos})
