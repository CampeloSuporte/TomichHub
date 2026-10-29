"""
Provisionamento de ONU/ONT em OLT Huawei — endpoints da aba "ONU" do painel
⚡ Scripts do terminal. A lógica está em `olt_provisionamento.py`.

    GET  acessos/<id>/onu-prov/            perfis/placas do backup (sem conectar)
    POST acessos/<id>/onu-prov/autofind/   `display ont autofind all` ao vivo
    POST acessos/<id>/onu-prov/executar/   preview (JSON) ou execução (SSE)
"""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from usuario import perms as _perms
from . import olt_provisionamento as op
from .decorators import ferramenta_instancia_required
from .models import Acesso, AcaoOltPon
from .olt_pon import PREAMBULO, conectar, desconectar

logger = logging.getLogger(__name__)

# Sobe junto com qualquer mudança em `op.parse_backup` (mesma regra do
# `_PON_CACHE_VERSAO`).
_CACHE_VERSAO = 1
_CACHE_TTL = 6 * 60 * 60
# Um lote por OLT de cada vez: duas abas provisionando a mesma ONU dariam um
# `ont add` duplicado (o segundo falha) no meio do lote do outro.
_LOCK_TTL = 15 * 60


def _pode(request, acesso):
    """Mesma régua do painel ⚡ Scripts (back-office com a ferramenta
    'scripts'), mais o recorte de cliente/host."""
    return (_perms.is_backoffice(request.user)
            and _perms.ferramenta_habilitada(request.user, 'scripts')
            and _perms.pode_acessar_acesso(request.user, acesso))


def _dados_backup(acesso):
    """(inventário PON, backup parseado, BackupLog) do backup mais recente."""
    from .views import _backups_recentes, _ler_backup, _pon_inventario_do_acesso
    inventario, log = _pon_inventario_do_acesso(acesso)
    if log is None:
        return None, None, None
    chave = f'onuprov:bk:v{_CACHE_VERSAO}:{log.id}'
    backup = cache.get(chave)
    if backup is None:
        _, caminho = _backups_recentes(acesso, limite=1)[0]
        backup = op.parse_backup(_ler_backup(caminho))
        cache.set(chave, backup, _CACHE_TTL)
    return inventario, backup, log


def _carregar(request, acesso_id):
    """(acesso, inventário, backup, log, resposta_de_erro)."""
    acesso = get_object_or_404(Acesso.objects.select_related('cliente'), id=acesso_id)
    if not _pode(request, acesso):
        return acesso, None, None, None, JsonResponse({'error': 'Sem permissão'}, status=403)
    inventario, backup, log = _dados_backup(acesso)
    if log is None:
        return acesso, None, None, None, JsonResponse({
            'error': 'Nenhum backup deste host — rode um backup antes de provisionar '
                     '(os perfis e as VLANs saem dele).'}, status=404)
    if not inventario.get('suportado'):
        return acesso, None, None, None, JsonResponse({
            'error': 'O backup deste host não é de uma OLT Huawei (MA5600T/MA5800). '
                     'Esta automação cobre só a CLI Huawei.'}, status=422)
    return acesso, inventario, backup, log, None


def _data(log):
    return log.data_backup.astimezone(timezone.get_current_timezone()).strftime('%d/%m/%Y %H:%M')


@login_required(login_url='login')
@require_http_methods(['GET'])
@ferramenta_instancia_required('scripts')
def onu_prov_inventario(request, acesso_id):
    acesso, inventario, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    return JsonResponse({
        'modelo': inventario['modelo'],
        'data_backup': _data(log),
        'host': {'id': acesso.id, 'nome': acesso.tipo, 'ip': acesso.host},
        'placas': [{'slot': p['slot'], 'tipo': p['tipo'], 'portas_total': p['portas_total']}
                   for p in inventario['placas']],
        'lines': [{'id': p['id'], 'nome': p['nome'], 'gems': p['gems'], 'vlans': p['vlans']}
                  for p in sorted(backup['lines'].values(), key=lambda p: p['id'])],
        'srvs': [{'id': p['id'], 'nome': p['nome'], 'vlans': p['vlans']}
                 for p in sorted(backup['srvs'].values(), key=lambda p: p['id'])],
        'modos': [{'chave': k, 'label': v['label']} for k, v in op.MODOS.items()],
        'max_lote': op.MAX_ONUS_LOTE,
    })


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def onu_prov_autofind(request, acesso_id):
    """Leitura pura: lista as ONUs pendentes, já com a sugestão do backup."""
    acesso, inventario, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro

    client = tunel = shell = None
    try:
        client, tunel = conectar(acesso)
        shell = op.ShellOlt(client)
        for cmd in PREAMBULO:
            shell.enviar(cmd)
        saida = shell.enviar('display ont autofind all', max_wait=90)
    except Exception as e:
        logger.warning(f'Autofind em {acesso}: {e}')
        return JsonResponse({'error': f'Falha ao consultar a OLT: {op.mensagem_de_falha(e)}'}, status=502)
    finally:
        if shell:
            shell.fechar()
        desconectar(client, tunel)

    onus = []
    for o in op.parse_autofind(saida):
        o['sugestao'] = op.sugerir(backup, o['slot'], o['porta'], o['sn'])
        o['no_backup'] = backup['sns'].get(o['sn'], '')
        onus.append(o)
    return JsonResponse({'onus': onus, 'saida': saida, 'data_backup': _data(log)})


def _validar_lote(body, inventario, backup):
    itens = body.get('itens')
    if not isinstance(itens, list) or not itens:
        raise op.ProvisionamentoInvalido('Selecione pelo menos uma ONU.')
    if len(itens) > op.MAX_ONUS_LOTE:
        raise op.ProvisionamentoInvalido(
            f'{len(itens)} ONUs num lote só — o máximo é {op.MAX_ONUS_LOTE}. Divida em partes.')
    validos, avisos, vistos = [], [], set()
    for item in itens:
        if not isinstance(item, dict):
            raise op.ProvisionamentoInvalido('Item inválido no lote.')
        it, av = op.validar_item(item, inventario, backup)
        if it['sn'] in vistos:
            raise op.ProvisionamentoInvalido(f'{it["sn"]} aparece duas vezes no lote.')
        vistos.add(it['sn'])
        validos.append(it)
        avisos.extend(av)
    return validos, avisos


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def onu_prov_executar(request, acesso_id):
    """body: {"itens": [{sn, slot, porta, vlan, line, srv, gemport, modo, desc}],
              "preview": bool}

    `preview=true` devolve os comandos de cada ONU (com `{ONT_ID}` no lugar
    do ID que a OLT vai escolher) e os avisos do backup, sem conectar.
    `preview=false` provisiona e responde em Server-Sent Events, um evento
    por ONU, gravando cada uma em `AcaoOltPon` (acao='provisionar').
    """
    acesso, inventario, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    try:
        itens, avisos = _validar_lote(body, inventario, backup)
    except op.ProvisionamentoInvalido as e:
        return JsonResponse({'error': str(e)}, status=422)

    if bool(body.get('preview', True)):
        return JsonResponse({
            'avisos': avisos,
            'onus': [{'sn': it['sn'], 'comandos': op.comandos_onu(it)} for it in itens],
        })

    lock = f'onuprov:lock:{acesso.id}'
    if not cache.add(lock, request.user.username, _LOCK_TTL):
        return JsonResponse({'error': f'{cache.get(lock) or "Outro usuário"} já está provisionando '
                                      'nesta OLT — aguarde terminar.'}, status=409)

    usuario = request.user
    onts_por_porta = {(p['slot'], q['porta']): q['onts']
                      for p in inventario['placas'] for q in p['portas']}
    por_sn = {it['sn']: it for it in itens}

    def _stream():
        def sse(tipo, payload):
            return f'data: {json.dumps({"tipo": tipo, **payload})}\n\n'

        contagem = {'sucesso': 0, 'parcial': 0, 'erro': 0, 'ignorada': 0}
        try:
            yield sse('info', {'msg': f'{len(itens)} ONU(s) para provisionar em {acesso.tipo} ({acesso.host}).'})
            for tipo, payload in op.provisionar(acesso, itens):
                if tipo == 'onu':
                    contagem[payload['status']] = contagem.get(payload['status'], 0) + 1
                    it = por_sn[payload['sn']]
                    if payload['status'] != 'ignorada':
                        AcaoOltPon.objects.create(
                            acesso=acesso, usuario=usuario, acao='provisionar',
                            slot=it['slot'], portas=str(it['porta']),
                            onts_afetadas=onts_por_porta.get((it['slot'], it['porta']), 0),
                            escrita=True, comandos='\n'.join(op.comandos_onu(it, payload.get('ont_id', op.ONT_ID))),
                            output=payload.get('comandos', ''), status=payload['status'])
                        logger.warning(f'ONU {it["sn"]} em {acesso} {it["slot"]}/{it["porta"]} '
                                       f'por {usuario} — {payload["status"]}: {payload["msg"]}')
                yield sse(tipo, payload)
            yield sse('fim', contagem)
        finally:
            cache.delete(lock)

    response = StreamingHttpResponse(_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response
