"""
Provisionamento de ONU em OLT Datacom DmOS — endpoints da aba "ONU Datacom"
do painel ⚡ Scripts. A lógica está em `olt_datacom.py`.

    GET  acessos/<id>/onu-dm/             perfis/PONs do backup (sem conectar)
    POST acessos/<id>/onu-dm/discovered/  `show interface gpon discovered-onus` ao vivo
    POST acessos/<id>/onu-dm/executar/    preview (JSON) ou execução (SSE)
    POST acessos/<id>/onu-dm/perfis/      criar bandwidth/line/onu/service profile + VLAN
    POST acessos/<id>/onu-dm/remover/     remove a ONU recém-provisionada

Diferente do Huawei, tudo roda pela conexão própria do servidor: o DmOS
aceita várias sessões do mesmo usuário (o terminal do operador continua
aberto), e a sessão do servidor nunca herda um `config` pela metade do
operador — o commit dela só leva o que ela mesma digitou.
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
from . import olt_datacom as od
from .decorators import ferramenta_instancia_required
from .models import Acesso, AcaoOltPon
from .olt_pon import conectar, desconectar

logger = logging.getLogger(__name__)

# Sobe junto com qualquer mudança em `od.parse_backup`.
_CACHE_VERSAO = 1
_CACHE_TTL = 6 * 60 * 60
_LOCK_TTL = 15 * 60


def _pode(request, acesso):
    return (_perms.is_backoffice(request.user)
            and _perms.ferramenta_habilitada(request.user, 'scripts')
            and _perms.pode_acessar_acesso(request.user, acesso))


def _carregar(request, acesso_id):
    """(acesso, backup, log, resposta_de_erro)."""
    from .views import _backups_recentes, _ler_backup
    acesso = get_object_or_404(Acesso.objects.select_related('cliente'), id=acesso_id)
    if not _pode(request, acesso):
        return acesso, None, None, JsonResponse({'error': 'Sem permissão'}, status=403)
    recentes = _backups_recentes(acesso, limite=1)
    if not recentes:
        return acesso, None, None, JsonResponse({
            'error': 'Nenhum backup deste host — rode um backup antes de provisionar '
                     '(os perfis e as VLANs saem dele).'}, status=404)
    log, caminho = recentes[0]
    chave = f'onudm:bk:v{_CACHE_VERSAO}:{log.id}'
    backup = cache.get(chave)
    if backup is None:
        conteudo = _ler_backup(caminho)
        backup = od.parse_backup(conteudo) if od.eh_olt_datacom(conteudo) else {}
        cache.set(chave, backup, _CACHE_TTL)
    if not backup:
        return acesso, None, None, JsonResponse({
            'error': 'O backup deste host não é de uma OLT Datacom DmOS (DM4610/DM4615): '
                     'falta interface gpon / profile gpon no running-config.'}, status=422)
    return acesso, backup, log, None


def _data(log):
    return log.data_backup.astimezone(timezone.get_current_timezone()).strftime('%d/%m/%Y %H:%M')


def _json(request):
    try:
        body = json.loads(request.body or b'{}')
        return body if isinstance(body, dict) else {}
    except Exception:
        return None


@login_required(login_url='login')
@require_http_methods(['GET'])
@ferramenta_instancia_required('scripts')
def onu_dm_inventario(request, acesso_id):
    acesso, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    uso_line, uso_srv = {}, {}
    for p in backup['pons'].values():
        for o in p['onus'].values():
            uso_line[o['line']] = uso_line.get(o['line'], 0) + 1
            uso_srv[o['service']] = uso_srv.get(o['service'], 0) + 1
    return JsonResponse({
        'data_backup': _data(log),
        'hostname': backup['hostname'],
        'host': {'id': acesso.id, 'nome': acesso.tipo, 'ip': acesso.host, 'modelo': str(acesso.modelo or '')},
        'pons': od.resumo_inventario(backup),
        'max_onu_id': od.MAX_ONU_ID,
        'lines': [{'nome': n, 'modo': l['modo'], 'vlans': l['vlans'],
                   'gems': [g['gem'] for g in l['gems']], 'texto': l['texto'],
                   'onus': uso_line.get(n, 0)} for n, l in sorted(backup['lines'].items())],
        'services': [{'nome': n, 'onu_profile': s['onu_profile'], 'texto': s['texto'],
                      'onus': uso_srv.get(n, 0)} for n, s in sorted(backup['service_profiles'].items())],
        'onu_profiles': sorted(backup['onu_profiles']),
        'bandwidths': [{'nome': n, 'traffic': b['traffic']} for n, b in sorted(backup['bandwidths'].items())],
        'vlans': [{'id': v['id'], 'nome': v['nome'], 'interfaces': v['interfaces'],
                   'service': backup['service_vlans'].get(v['id'], '')}
                  for v in sorted(backup['vlans'].values(), key=lambda v: v['id'])],
        'uplinks': od.uplinks_do_backup(backup),
        'modos': [{'chave': k, 'label': v} for k, v in od.MODOS.items()],
        'max_lote': od.MAX_ONUS_LOTE,
    })


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def onu_dm_discovered(request, acesso_id):
    """Leitura pura: ONUs descobertas e ainda não provisionadas, com a
    sugestão do backup."""
    acesso, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    client = tunel = shell = None
    try:
        client, tunel = conectar(acesso)
        shell = od.ShellDatacom(client)
        saida = shell.enviar('show interface gpon discovered-onus', max_wait=60)
    except Exception as e:
        logger.warning(f'discovered-onus em {acesso}: {e}')
        return JsonResponse({'error': f'Falha ao consultar a OLT: {e}'}, status=502)
    finally:
        if shell:
            shell.fechar()
        desconectar(client, tunel)
    onus = []
    for o in od.parse_discovered(saida):
        if o['pon'] not in backup['pons']:
            o['erro'] = f'a PON {o["pon"]} não aparece no backup'
        o['sugestao'] = od.sugerir(backup, o['pon'], o['sn'])
        o['no_backup'] = backup['sns'].get(o['sn'], '')
        o['onus_na_pon'] = len(backup['pons'].get(o['pon'], {}).get('onus', {}))
        onus.append(o)
    return JsonResponse({'onus': onus, 'saida': saida, 'data_backup': _data(log)})


def _validar_lote(body, backup):
    itens = body.get('itens')
    if not isinstance(itens, list) or not itens:
        raise od.ProvisionamentoInvalido('Selecione pelo menos uma ONU.')
    if len(itens) > od.MAX_ONUS_LOTE:
        raise od.ProvisionamentoInvalido(
            f'{len(itens)} ONUs num lote só — o máximo é {od.MAX_ONUS_LOTE}. Divida em partes.')
    validos, avisos, vistos, ids_pedidos = [], [], set(), set()
    for item in itens:
        if not isinstance(item, dict):
            raise od.ProvisionamentoInvalido('Item inválido no lote.')
        it, av = od.validar_item(item, backup)
        if it['sn'] in vistos:
            raise od.ProvisionamentoInvalido(f'{it["sn"]} aparece duas vezes no lote.')
        if it['onu_id'] is not None:
            if (it['pon'], it['onu_id']) in ids_pedidos:
                raise od.ProvisionamentoInvalido(f'ONU {it["onu_id"]} de {it["pon"]} pedida duas vezes no lote.')
            ids_pedidos.add((it['pon'], it['onu_id']))
        vistos.add(it['sn'])
        validos.append(it)
        avisos.extend(av)
    od.alocar_ids(validos, backup)
    return validos, avisos


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def onu_dm_executar(request, acesso_id):
    """body: {"itens": [{sn, pon, modo, service, line, gem, vlan, vlan_olt, nome, onu_id}],
              "preview": bool}

    `preview=true` devolve os comandos (IDs do backup; na execução cada ID
    ainda é conferido ao vivo e pode andar). `preview=false` provisiona em
    SSE, uma ONU por evento, cada uma auditada em `AcaoOltPon`."""
    acesso, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    body = _json(request)
    if body is None:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    try:
        itens, avisos = _validar_lote(body, backup)
    except od.ProvisionamentoInvalido as e:
        return JsonResponse({'error': str(e)}, status=422)

    if bool(body.get('preview', True)):
        return JsonResponse({
            'avisos': avisos,
            'onus': [{'sn': it['sn'], 'onu_id': it['onu_id'], 'sp_id': it['sp_id'],
                      'checagens': od.checagens_onu(it),
                      'comandos': od.roteiro(od.comandos_onu(it))} for it in itens],
        })

    lock = f'onudm:lock:{acesso.id}'
    if not cache.add(lock, request.user.username, _LOCK_TTL):
        return JsonResponse({'error': f'{cache.get(lock) or "Outro usuário"} já está provisionando '
                                      'nesta OLT — aguarde terminar.'}, status=409)
    usuario = request.user
    por_sn = {it['sn']: it for it in itens}

    def _stream():
        def sse(tipo, payload):
            return f'data: {json.dumps({"tipo": tipo, **payload})}\n\n'

        contagem = {'sucesso': 0, 'erro': 0, 'ignorada': 0}
        try:
            yield sse('info', {'msg': f'{len(itens)} ONU(s) para provisionar em {acesso.tipo} ({acesso.host}).'})
            for tipo, payload in od.provisionar(acesso, itens, backup):
                if tipo == 'onu':
                    contagem[payload['status']] = contagem.get(payload['status'], 0) + 1
                    it = por_sn[payload['sn']]
                    if payload['status'] != 'ignorada':
                        AcaoOltPon.objects.create(
                            acesso=acesso, usuario=usuario, acao='provisionar',
                            slot=it['pon'], portas=str(it.get('onu_id') if it.get('onu_id') is not None else ''),
                            onts_afetadas=len(backup['pons'].get(it['pon'], {}).get('onus', {})),
                            escrita=True, comandos='\n'.join(od.roteiro(od.comandos_onu(it)))
                            if it.get('onu_id') is not None else '',
                            output=payload.get('comandos', '')[:200_000], status=payload['status'])
                        logger.warning(f'ONU Datacom {it["sn"]} em {acesso} {it["pon"]} por {usuario} — '
                                       f'{payload["status"]}: {payload["msg"]}')
                yield sse(tipo, payload)
            yield sse('fim', contagem)
        finally:
            cache.delete(lock)

    response = StreamingHttpResponse(_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def onu_dm_perfis(request, acesso_id):
    """Cria bandwidth/line/onu/service profile e a VLAN (dot1q + uplink +
    service vlan). body: {acao: 'preview'|'executar', spec: {...}}."""
    acesso, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    body = _json(request)
    if body is None:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    try:
        norm = od.validar_perfis(body.get('spec') or {}, backup)
    except od.ProvisionamentoInvalido as e:
        return JsonResponse({'error': str(e)}, status=422)
    comandos = od.roteiro(od.comandos_perfis(norm))
    if body.get('acao') != 'executar':
        return JsonResponse({'avisos': norm['avisos'], 'checagens': od.checagens_perfis(norm),
                             'comandos': comandos})
    lock = f'onudm:lock:{acesso.id}'
    if not cache.add(lock, request.user.username, _LOCK_TTL):
        return JsonResponse({'error': f'{cache.get(lock) or "Outro usuário"} já está mexendo nesta OLT.'},
                            status=409)
    try:
        status, saida, msg = od.criar_perfis(acesso, norm)
    except Exception as e:
        status, saida, msg = 'erro', '', f'Falha ao conectar: {e}'
    finally:
        cache.delete(lock)
    AcaoOltPon.objects.create(
        acesso=acesso, usuario=request.user, acao='perfil', slot='-', portas='',
        escrita=True, comandos='\n'.join(comandos), output=saida[:200_000], status=status)
    logger.warning(f'Perfis Datacom em {acesso} por {request.user} — {status}: {msg}')
    backup_agendado = False
    if status == 'sucesso':
        from .onu_prov_views import _agendar_backup
        backup_agendado = _agendar_backup(acesso, request.user)
    return JsonResponse({'status': status, 'saida': saida, 'msg': msg, 'comandos': comandos,
                         'backup': 'agendado' if backup_agendado else ''})


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def onu_dm_remover(request, acesso_id):
    """Desfaz a ONU recém-provisionada. body: {acao, sn, pon, onu_id, sp_ids}."""
    acesso, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    body = _json(request)
    if body is None:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    try:
        sn = str(body.get('sn') or '').strip().upper()
        pon = str(body.get('pon') or '').strip()
        if not od._SN_RE.match(sn) or pon not in backup['pons']:
            raise od.ProvisionamentoInvalido('SN ou PON inválidos.')
        onu_id = od._inteiro(body.get('onu_id'), 'ID da ONU', 0, od.MAX_ONU_ID)
        sp_ids = [od._inteiro(s, 'service-port', 1, od.MAX_SERVICE_PORT) for s in (body.get('sp_ids') or [])]
    except od.ProvisionamentoInvalido as e:
        return JsonResponse({'error': str(e)}, status=422)
    comandos = od.roteiro(od.comandos_remover(pon, onu_id, sp_ids))
    if body.get('acao') != 'executar':
        return JsonResponse({'comandos': comandos})
    try:
        status, saida, msg = od.remover_onu(acesso, sn, pon, onu_id, sp_ids)
    except Exception as e:
        status, saida, msg = 'erro', '', f'Falha ao conectar: {e}'
    AcaoOltPon.objects.create(
        acesso=acesso, usuario=request.user, acao='remover', slot=pon, portas=str(onu_id),
        escrita=True, comandos='\n'.join(comandos), output=saida[:200_000], status=status)
    logger.warning(f'Remover ONU Datacom {sn} em {acesso} {pon}/{onu_id} por {request.user} — {status}: {msg}')
    return JsonResponse({'status': status, 'saida': saida, 'msg': msg})
