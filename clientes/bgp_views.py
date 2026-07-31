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
    comandos_parar_anuncio,
    comandos_prepend,
    comandos_toggle_sessao,
    executar_acao_bgp,
)
from .models import Acesso, AcaoBgp, BgpSnapshot

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
    raise AcaoBgpNaoSuportada(f'Tipo de ação "{tipo}" desconhecido.')


@login_required(login_url='login')
@require_http_methods(["POST"])
def bgp_executar_acao(request, acesso_id):
    """
    POST /clientes/bgp/<acesso_id>/acao/
    body: {"tipo", "alvo", "params": {...}, "preview": bool}

    `preview=true` só monta e devolve os comandos, sem tocar no equipamento
    — é o que a UI usa pro modal de confirmação mostrar antes do clique
    final. `preview=false` executa de verdade e grava AcaoBgp.
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

    try:
        snap = BgpSnapshot.objects.get(acesso_id=acesso_id)
    except BgpSnapshot.DoesNotExist:
        return JsonResponse({'error': 'Sem snapshot BGP para este host — aguarde a próxima atualização noturna.'}, status=404)

    try:
        comandos = _montar_comandos(tipo, snap.vendor, snap.dados, alvo, params)
    except AcaoBgpNaoSuportada as e:
        return JsonResponse({'error': str(e)}, status=422)

    if preview:
        return JsonResponse({'comandos': comandos})

    output, status = executar_acao_bgp(acesso, snap.vendor, comandos)
    AcaoBgp.objects.create(
        acesso=acesso, usuario=request.user, tipo=tipo, alvo=alvo,
        comandos='\n'.join(comandos), output=output, status=status,
    )
    return JsonResponse({'status': status, 'output': output, 'comandos': comandos})
