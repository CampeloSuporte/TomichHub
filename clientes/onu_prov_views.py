"""
Provisionamento de ONU/ONT em OLT Huawei — endpoints da aba "ONU" do painel
⚡ Scripts do terminal. A lógica está em `olt_provisionamento.py`.

    GET  acessos/<id>/onu-prov/            perfis/placas do backup (sem conectar)
    POST acessos/<id>/onu-prov/autofind/   `display ont autofind all` ao vivo
    POST acessos/<id>/onu-prov/executar/   preview (JSON) ou execução (SSE)
    POST acessos/<id>/onu-prov/registrar/  auditoria de ONU provisionada pelo terminal
    POST acessos/<id>/onu-prov/perfis/     criar srv/line profile de VLAN translate
    POST acessos/<id>/onu-prov/backup/     backup logo depois de criar perfis
    POST acessos/<id>/onu-prov/mac/        MAC aprendido nos service-ports da ONU provisionada
    POST acessos/<id>/onu-prov/remover/    remove a ONU recém-provisionada

Duas formas de falar com a OLT. Com o terminal do operador ABERTO nela, tudo
vai por aquela sessão (o JS do painel envia e lê pelo WebSocket): a OLT da WTD
não aceita o mesmo login duas vezes ao mesmo tempo, então a conexão própria do
servidor seria recusada. Sem terminal aberto, o servidor conecta sozinho.
"""
import hashlib
import json
import logging
import os
import threading
from datetime import datetime

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
_CACHE_VERSAO = 4
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
        'lines': [{'id': p['id'], 'nome': p['nome'], 'gems': p['gems'], 'vlans': p['vlans'],
                   'mapas': p.get('mapas', [])}
                  for p in sorted(backup['lines'].values(), key=lambda p: p['id'])],
        'dbas': [{'id': k, 'nome': v} for k, v in sorted(backup.get('dbas', {}).items())],
        'proximo_dba': op.proximo_id_livre(set(backup.get('dbas', {})), op.DBA_PRIMEIRO_LIVRE, 1024),
        'proximo_line': op.proximo_id_livre(set(backup['lines'])),
        'proximo_srv': op.proximo_id_livre(set(backup['srvs'])),
        'max_gems': op.MAX_GEMS_PERFIL,
        'max_vlans_gem': op.MAX_VLANS_GEM,
        'uplinks': op.uplinks_do_backup(backup),
        'vlans_olt': sorted(backup['vlans']),
        'srvs': [{'id': p['id'], 'nome': p['nome'], 'vlans': p['vlans']}
                 for p in sorted(backup['srvs'].values(), key=lambda p: p['id'])],
        'modos': [{'chave': k, 'label': v['label']} for k, v in op.MODOS.items()],
        'max_lote': op.MAX_ONUS_LOTE,
    })


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def onu_prov_autofind(request, acesso_id):
    """Leitura pura: lista as ONUs pendentes, já com a sugestão do backup.

    body opcional `{"saida": "..."}`: a saída do `display ont autofind all`
    já rodado no terminal aberto — aí só interpreta, sem conectar.
    """
    acesso, inventario, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    try:
        body = json.loads(request.body or b'{}')
    except Exception:
        body = {}
    saida = body.get('saida') if isinstance(body, dict) else None
    if isinstance(saida, str):
        return _resposta_autofind(saida[:500_000], backup, log)

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
    return _resposta_autofind(saida, backup, log)


def _resposta_autofind(saida, backup, log):
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
        # VLANs dos service-ports do lote: criadas e passadas na uplink antes
        # do primeiro `ont add` quando o operador marca a opção.
        vlans_lote = list(dict.fromkeys(
            sv['vlan'] for it in itens for sv in (it['servicos'] or [{'vlan': it['vlan']}])))
        uplink, av = op.validar_uplink(body.get('uplink'), backup, vlans_lote)
        avisos += av
    except op.ProvisionamentoInvalido as e:
        return JsonResponse({'error': str(e)}, status=422)
    preparo = op.comandos_vlans_uplink(uplink, backup)

    if bool(body.get('preview', True)):
        return JsonResponse({
            'avisos': avisos,
            'preparo': preparo,
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
            for tipo, payload in op.provisionar(acesso, itens, preparo):
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


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def onu_prov_registrar(request, acesso_id):
    """Auditoria de uma ONU provisionada pelo terminal aberto (o JS executa e
    só reporta). body: {item: {...}, ont_id, status, saida, msg}."""
    acesso, inventario, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    try:
        body = json.loads(request.body)
        it, _ = op.validar_item(body.get('item') or {}, inventario, backup)
    except op.ProvisionamentoInvalido as e:
        return JsonResponse({'error': str(e)}, status=422)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    status = body.get('status')
    if status not in ('sucesso', 'parcial', 'erro'):
        return JsonResponse({'error': 'status inválido'}, status=400)
    ont_id = body.get('ont_id')
    ont_id = int(ont_id) if str(ont_id).isdigit() else op.ONT_ID
    placa = next(p for p in inventario['placas'] if p['slot'] == it['slot'])
    onts = next((q['onts'] for q in placa['portas'] if q['porta'] == it['porta']), 0)
    AcaoOltPon.objects.create(
        acesso=acesso, usuario=request.user, acao='provisionar',
        slot=it['slot'], portas=str(it['porta']), onts_afetadas=onts, escrita=True,
        comandos='\n'.join(op.comandos_onu(it, ont_id)),
        output=str(body.get('saida') or '')[:200_000], status=status)
    logger.warning(f'ONU {it["sn"]} em {acesso} {it["slot"]}/{it["porta"]} pelo terminal '
                   f'de {request.user} — {status}: {str(body.get("msg") or "")[:200]}')
    # O terminal mandou a saída do `display service-port ... ont N`: devolve
    # os índices para o botão "Validar MAC" do cartão.
    sps = op.service_ports_com_mac(op.parse_service_ports(str(body.get('saida_service_ports') or '')))
    return JsonResponse({'ok': True, 'service_ports': sps})


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def onu_prov_perfis(request, acesso_id):
    """Cria srv e/ou line profile para VLAN translate.

    body: {"spec": {srv: {criar, id, nome, eth}, line: {criar, id, nome, tcont, dba},
                    vlans: [101, 102, ...], gem_igual_vlan: bool},
           "acao": "preview" | "executar" | "registrar", ...}

    - preview: comandos, checagens e avisos (sem conectar);
    - executar: o servidor conecta, confere que os IDs não existem e cria
      (só usado sem terminal aberto: a OLT recusa um segundo login);
    - registrar: o terminal aberto já executou; só grava a auditoria.
    """
    acesso, inventario, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    try:
        body = json.loads(request.body)
        norm, avisos = op.validar_perfis(body.get('spec'), backup)
    except op.ProvisionamentoInvalido as e:
        return JsonResponse({'error': str(e)}, status=422)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    comandos_uplink = op.comandos_vlans_uplink(norm.get('uplink'), backup)
    comandos = op.comandos_perfis(norm) + comandos_uplink
    acao = body.get('acao') or 'preview'
    if acao == 'preview':
        return JsonResponse({
            # `comandos` são os perfis (recusa = erro); `uplink` aceita "já existe".
            'comandos': op.comandos_perfis(norm), 'uplink': comandos_uplink,
            'checagens': op.checagens_perfis(norm), 'avisos': avisos,
            'gems': (norm['line'] or {}).get('gems', []),
            'dba': norm['dba'],
        })

    def _auditar(status, saida, msg):
        alvo = ' + '.join(f'{k} {norm[k]["id"]}' for k in ('dba', 'srv', 'line') if norm.get(k))
        AcaoOltPon.objects.create(
            acesso=acesso, usuario=request.user, acao='perfil', slot='-', portas='',
            onts_afetadas=0, escrita=True, comandos='\n'.join(comandos),
            output=str(saida or '')[:200_000], status=status)
        logger.warning(f'Perfis {alvo} em {acesso} por {request.user} — {status}: {str(msg)[:200]}')

    if acao == 'registrar':
        status = body.get('status')
        if status not in ('sucesso', 'erro'):
            return JsonResponse({'error': 'status inválido'}, status=400)
        _auditar(status, body.get('saida'), body.get('msg') or '')
        return JsonResponse({'ok': True})
    if acao != 'executar':
        return JsonResponse({'error': 'ação inválida'}, status=400)

    lock = f'onuprov:lock:{acesso.id}'
    if not cache.add(lock, request.user.username, _LOCK_TTL):
        return JsonResponse({'error': f'{cache.get(lock) or "Outro usuário"} está mexendo nesta OLT — aguarde.'},
                            status=409)
    client = tunel = shell = None
    try:
        client, tunel = conectar(acesso)
        shell = op.ShellOlt(client)
        for cmd in PREAMBULO:
            shell.enviar(cmd)
        status, saida, msg = op.criar_perfis(shell, norm, backup)
    except Exception as e:
        cache.delete(lock)
        logger.warning(f'Perfis em {acesso}: {e}')
        return JsonResponse({'error': f'Falha ao falar com a OLT: {op.mensagem_de_falha(e)}'}, status=502)
    finally:
        if shell:
            shell.fechar()
        desconectar(client, tunel)
    cache.delete(lock)
    _auditar(status, saida, msg)
    # Perfil novo só aparece no provisionamento depois de um backup novo (o
    # inventário é lido do backup). A sessão acima já foi fechada, então o
    # backup pode logar de novo.
    backup_agendado = status == 'sucesso' and _agendar_backup(acesso, request.user)
    return JsonResponse({'status': status, 'saida': saida, 'msg': msg, 'comandos': comandos,
                         'backup': 'agendado' if backup_agendado else ''})


# ─── Backup depois de criar perfis ────────────────────────────────────────────

def _agendar_backup(acesso, usuario):
    """Roda o backup normal (`views.realizar_backup`) numa thread, sem segurar
    a resposta: um MA5800 com 3 mil ONTs leva perto de 1 min. Thread, e não
    Celery, porque o worker tem concurrency=1 e o backup ficaria na fila atrás
    da rotina que estiver rodando. Devolve False se não há como fazer backup."""
    if not (acesso.ativo and acesso.backup_habilitado and acesso.backup_template_id):
        return False
    chave = f'onuprov:backup:{acesso.id}'
    if not cache.add(chave, 1, 10 * 60):
        return True     # já tem um rodando; o inventário pega o dele

    def _rodar():
        from django.db import connection
        from .views import realizar_backup
        try:
            resultado = realizar_backup(acesso, usuario)
            logger.warning(f'Backup após perfis em {acesso}: {resultado.get("sucesso")} '
                           f'{resultado.get("erro") or ""}')
        except Exception as e:
            logger.warning(f'Backup após perfis em {acesso} falhou: {e}')
        finally:
            cache.delete(chave)
            connection.close()

    threading.Thread(target=_rodar, daemon=True, name=f'onuprov-backup-{acesso.id}').start()
    return True


def _gravar_backup_capturado(acesso, usuario, saida):
    """Grava como backup a configuração lida pelo terminal aberto, no mesmo
    lugar e com o mesmo cabeçalho do `realizar_backup` (é o que
    `_backups_recentes` e o inventário leem). Devolve o BackupLog."""
    from .models import BackupLog
    from .views import preparar_diretorio_backup
    import re as _re

    corpo = ('\n' + '=' * 60 + '\nComando: display current-configuration\n' + '=' * 60 + '\n' + saida)
    norm = _re.sub(r'\x1b\[[0-9;]*[mGKHF]', '', corpo).strip()
    hash_novo = hashlib.sha256(norm.encode('utf-8', errors='replace')).hexdigest()
    pasta = preparar_diretorio_backup(acesso.cliente_id, acesso.id)
    tipo_seguro = _re.sub(r'[^A-Za-z0-9_-]+', '_', acesso.tipo).strip('_') or 'backup'
    caminho = os.path.join(pasta, f'{tipo_seguro}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt')
    with open(caminho, 'w', encoding='utf-8') as f:
        f.write('=' * 80 + '\nBACKUP DE CONFIGURAÇÃO\n' + '=' * 80 + '\n')
        f.write(f'Cliente: {acesso.cliente.nome_empresa}\nEquipamento: {acesso.tipo}\n'
                f'Host: {acesso.host}:{acesso.porta}\nAcesso: TERMINAL DO OPERADOR\n'
                f'Modelo: {acesso.modelo}\n'
                f'Template: {acesso.backup_template.nome if acesso.backup_template else "-"}\n'
                f'Data: {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}\n'
                f'Executado por: {usuario.username}\n' + '=' * 80 + '\n\n')
        f.write(corpo)
    from django.conf import settings
    return BackupLog.objects.create(
        acesso=acesso, cliente=acesso.cliente, template=acesso.backup_template,
        arquivo_path=os.path.relpath(caminho, settings.MEDIA_ROOT),
        tamanho_bytes=os.path.getsize(caminho), hash_conteudo=hash_novo, status='SUCESSO',
        mensagem='Backup capturado pelo terminal aberto, depois de criar srv/line profile',
        executado_por=usuario, duracao_segundos=0)


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def onu_prov_backup(request, acesso_id):
    """Backup depois de criar perfis.

    - body `{"saida": "..."}`: o `display current-configuration` rodado no
      terminal aberto (a OLT não aceita segundo login com ele aberto) —
      grava como backup e responde já com o inventário novo valendo;
    - body vazio: dispara o backup normal em segundo plano; a tela acompanha
      pelo `data_backup` do inventário.
    """
    acesso, inventario, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    try:
        body = json.loads(request.body or b'{}')
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    saida = body.get('saida') if isinstance(body, dict) else None
    if isinstance(saida, str):
        from .olt_pon import parse_pon
        # Só grava o que é configuração de OLT Huawei de verdade: um backup
        # truncado (sessão caiu no meio) viraria o "mais recente" e esconderia
        # placas e perfis do inventário.
        # O `display current-configuration` do VRP termina em `return`.
        completa = 'return' in saida[-3000:]
        if 'ont-lineprofile gpon' not in saida or not completa or not parse_pon(saida).get('suportado'):
            return JsonResponse({'error': 'A saída capturada não parece uma configuração completa '
                                          'da OLT — backup não gravado.'}, status=422)
        novo = _gravar_backup_capturado(acesso, request.user, saida)
        return JsonResponse({'ok': True, 'backup_id': novo.id, 'data_backup': _data(novo)})
    if not _agendar_backup(acesso, request.user):
        return JsonResponse({'error': 'Backup não habilitado neste host (ou sem template).'}, status=422)
    return JsonResponse({'ok': True, 'agendado': True})


def _service_ports_do_pedido(body):
    brutos = body.get('service_ports')
    if not isinstance(brutos, list) or not brutos or len(brutos) > op.MAX_GEMS_PERFIL:
        raise op.ProvisionamentoInvalido('Nenhum service-port para validar.')
    sps = []
    for sp in brutos:
        if not isinstance(sp, dict):
            raise op.ProvisionamentoInvalido('Service-port inválido.')
        sps.append({'index': op._inteiro(sp.get('index'), 'índice do service-port', 0, 65535),
                    'vlan': op._inteiro(sp.get('vlan'), 'VLAN do service-port', 1, 4094),
                    'gem': sp.get('gem')})
    return op.service_ports_com_mac(sps)


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def onu_prov_mac(request, acesso_id):
    """Confere se a ONU recém-provisionada já trouxe MAC: `display
    mac-address vlan V | include <índice do service-port>` para cada
    service-port criado. Leitura pura.

    body: {"service_ports": [{index, vlan, gem}], "saidas": {"<index>": "..."}}
    Com `saidas` (rodado no terminal aberto) só interpreta; sem, conecta.
    """
    acesso, inventario, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    try:
        body = json.loads(request.body)
        sps = _service_ports_do_pedido(body)
    except op.ProvisionamentoInvalido as e:
        return JsonResponse({'error': str(e)}, status=422)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    saidas = body.get('saidas')
    if not isinstance(saidas, dict):
        saidas = {}
        client = tunel = shell = None
        try:
            client, tunel = conectar(acesso)
            shell = op.ShellOlt(client)
            for cmd in PREAMBULO:
                shell.enviar(cmd)
            for sp in sps:
                saidas[str(sp['index'])] = shell.enviar(sp['cmd_mac'])
        except Exception as e:
            return JsonResponse({'error': f'Falha ao consultar a OLT: {op.mensagem_de_falha(e)}'}, status=502)
        finally:
            if shell:
                shell.fechar()
            desconectar(client, tunel)

    resultados = []
    for sp in sps:
        saida = str(saidas.get(str(sp['index'])) or '')[:100_000]
        resultados.append({**sp, 'macs': op.parse_macs(saida, sp['index']), 'saida': saida})
    return JsonResponse({'resultados': resultados})


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def onu_prov_remover(request, acesso_id):
    """Remove a ONU que acabou de ser provisionada: `undo service-port` dos
    service-ports criados e `ont delete`. Antes confere pela SN que a ONT
    ainda é aquela (mesma PON, mesmo ID).

    body: {sn, slot, porta, ont_id, service_ports: [{index, vlan}],
           acao: "preview" | "executar" | "registrar", status?, saida?, msg?}
    """
    acesso, inventario, backup, log, erro = _carregar(request, acesso_id)
    if erro:
        return erro
    try:
        body = json.loads(request.body)
        sn = str(body.get('sn') or '').strip().upper()
        if not op._SN_RE.match(sn):
            raise op.ProvisionamentoInvalido(f'SN inválido: "{sn}".')
        slot = str(body.get('slot') or '').strip()
        if not any(p['slot'] == slot for p in inventario['placas']):
            raise op.ProvisionamentoInvalido(f'Placa inválida: "{slot}".')
        porta = op._inteiro(body.get('porta'), 'PON', 0, 63)
        ont_id = op._inteiro(body.get('ont_id'), 'ONT-ID', 0, 1023)
        sps = _service_ports_do_pedido(body) if body.get('service_ports') else []
    except op.ProvisionamentoInvalido as e:
        return JsonResponse({'error': str(e)}, status=422)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    comandos = op.comandos_remover(slot, porta, ont_id, sps)
    acao = body.get('acao') or 'preview'
    if acao == 'preview':
        return JsonResponse({'checagem': f'display ont info by-sn {sn}', 'comandos': comandos})

    def _auditar(status, saida, msg):
        AcaoOltPon.objects.create(
            acesso=acesso, usuario=request.user, acao='remover', slot=slot, portas=str(porta),
            onts_afetadas=1, escrita=True, comandos='\n'.join(comandos),
            output=str(saida or '')[:200_000], status=status)
        logger.warning(f'Remover ONU {sn} ({slot}/{porta} ONT {ont_id}) em {acesso} por {request.user} '
                       f'— {status}: {str(msg)[:200]}')

    if acao == 'registrar':
        status = body.get('status')
        if status not in ('sucesso', 'erro'):
            return JsonResponse({'error': 'status inválido'}, status=400)
        _auditar(status, body.get('saida'), body.get('msg') or '')
        return JsonResponse({'ok': True})
    if acao != 'executar':
        return JsonResponse({'error': 'ação inválida'}, status=400)

    lock = f'onuprov:lock:{acesso.id}'
    if not cache.add(lock, request.user.username, _LOCK_TTL):
        return JsonResponse({'error': f'{cache.get(lock) or "Outro usuário"} está mexendo nesta OLT — aguarde.'},
                            status=409)
    client = tunel = shell = None
    try:
        client, tunel = conectar(acesso)
        shell = op.ShellOlt(client)
        for cmd in PREAMBULO:
            shell.enviar(cmd)
        status, saida, msg = op.remover_onu(shell, sn, slot, porta, ont_id, sps)
    except Exception as e:
        cache.delete(lock)
        return JsonResponse({'error': f'Falha ao falar com a OLT: {op.mensagem_de_falha(e)}'}, status=502)
    finally:
        if shell:
            shell.fechar()
        desconectar(client, tunel)
    cache.delete(lock)
    _auditar(status, saida, msg)
    return JsonResponse({'status': status, 'saida': saida, 'msg': msg})
