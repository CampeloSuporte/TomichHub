"""
clientes/script_views.py
Views para o sistema de Scripts de Automação.
"""
import json
import logging
import socket
import threading
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_http_methods

from .decorators import ferramenta_instancia_required
from .models import Acesso, ScriptCRM, ScriptExecucaoLog
from usuario.perms import pode_acessar_cliente

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# TEMPLATE ENGINE
# ──────────────────────────────────────────────────────────────

def _subst(text: str, params: dict) -> str:
    for k, v in params.items():
        text = text.replace('{' + str(k).upper() + '}', str(v))
        text = text.replace('{' + str(k).lower() + '}', str(v))
        text = text.replace('{' + str(k) + '}', str(v))
    return text


def _expandir_template(template_text: str, params: dict) -> list:
    """
    Expande o template de comandos substituindo parâmetros e processando loops.

    Sintaxe suportada:
    - {PARAM}              → substituído pelo valor do parâmetro
    - # comentário         → linha ignorada
    - #FOR I FROM X TO Y   → loop de X até Y inclusive
    - #ENDFOR              → fim do loop
    """
    import re as _re

    linhas = template_text.splitlines()
    resultado = []
    i = 0

    while i < len(linhas):
        linha = linhas[i].rstrip()
        stripped = linha.strip()

        # Comentário (exceto diretivas #FOR / #ENDFOR)
        if stripped.startswith('#') and \
                not stripped.upper().startswith('#FOR') and \
                not stripped.upper().startswith('#ENDFOR'):
            i += 1
            continue

        # Loop #FOR
        if stripped.upper().startswith('#FOR '):
            m = _re.match(r'#FOR\s+(\w+)\s+FROM\s+(\S+)\s+TO\s+(\S+)', stripped, _re.IGNORECASE)
            if m:
                var_name = m.group(1).upper()
                try:
                    start = int(_subst(m.group(2), params))
                    end   = int(_subst(m.group(3), params))
                except (ValueError, TypeError):
                    i += 1
                    continue

                # Coletar corpo do loop
                corpo = []
                i += 1
                while i < len(linhas) and not linhas[i].strip().upper().startswith('#ENDFOR'):
                    corpo.append(linhas[i])
                    i += 1

                # Expandir loop
                for j in range(start, end + 1):
                    loop_params = {**params, var_name: str(j)}
                    for corpo_linha in corpo:
                        expandido = _subst(corpo_linha.rstrip(), loop_params)
                        if expandido.strip() and not expandido.strip().startswith('#'):
                            resultado.append(expandido)
                i += 1  # pula #ENDFOR
                continue

        # Linha normal
        if stripped and not stripped.startswith('#'):
            expandido = _subst(linha, params)
            resultado.append(expandido)

        i += 1

    return resultado


# ──────────────────────────────────────────────────────────────
# CONEXÃO SSH PARA SCRIPTS
# ──────────────────────────────────────────────────────────────

DEVICE_TYPES = {
    'zte':      'zte_zxros',
    'huawei':   'huawei_vrpv8',
    'cisco':    'cisco_ios',
    'mikrotik': 'mikrotik_routeros',
    'datacom':  'cisco_ios',
    'parks':    'cisco_ios',
    'juniper':  'juniper_junos',
    'generico': 'autodetect',
}


def _conectar_script(acesso, fabricante: str):
    """
    Cria conexão netmiko para execução de script.
    Lida com IP privado via ProxyServer ou túnel OpenVPN.
    Retorna (ConnectHandler, tunel_ou_None).
    """
    import ipaddress as _ipa
    from netmiko import ConnectHandler
    from .models import ProxyServer
    from clientes.views import vpn_cobre_ip

    host = acesso.host
    porta = int(acesso.porta or 22)
    usuario = acesso.usuario
    senha = acesso.senha

    device_type = DEVICE_TYPES.get(fabricante, 'autodetect')

    # Verifica se IP é privado
    try:
        eh_privado = _ipa.ip_address(host).is_private
    except ValueError:
        eh_privado = False

    host_conexao = host
    porta_conexao = porta
    tunel_ssh = None

    if eh_privado:
        proxy = ProxyServer.objects.filter(cliente=acesso.cliente, ativo=True).first()

        if proxy:
            # Cria túnel SSH via proxy
            import paramiko
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=proxy.host,
                port=int(proxy.porta),
                username=proxy.usuario,
                password=proxy.senha,
                timeout=15,
                look_for_keys=False,
                allow_agent=False,
            )

            # Porta local livre
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('127.0.0.1', 0))
            local_port = s.getsockname()[1]
            s.close()

            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind(('127.0.0.1', local_port))
            server_sock.listen(1)
            server_sock.settimeout(30)

            transport = ssh.get_transport()

            def _aceitar():
                try:
                    conn_sock, _ = server_sock.accept()
                    channel = transport.open_channel(
                        'direct-tcpip', (host, porta), ('127.0.0.1', local_port)
                    )

                    def _pipe(src, dst):
                        try:
                            while True:
                                d = src.recv(65536)
                                if not d:
                                    break
                                dst.sendall(d)
                        except Exception:
                            pass

                    threading.Thread(target=_pipe, args=(conn_sock, channel), daemon=True).start()
                    threading.Thread(target=_pipe, args=(channel, conn_sock), daemon=True).start()
                except Exception as e:
                    logger.error(f"[SCRIPT TUNNEL] aceitar: {e}")

            threading.Thread(target=_aceitar, daemon=True).start()

            host_conexao = '127.0.0.1'
            porta_conexao = local_port
            tunel_ssh = (ssh, server_sock)

        elif vpn_cobre_ip(acesso.cliente, host):
            pass  # conecta diretamente pelo túnel OpenVPN
        else:
            raise Exception(
                f"IP privado ({host}) sem proxy SSH ou túnel OpenVPN configurado para este cliente."
            )

    device = {
        'device_type':      device_type,
        'host':             host_conexao,
        'port':             porta_conexao,
        'username':         usuario,
        'password':         senha,
        'timeout':          30,
        'banner_timeout':   30,
        'conn_timeout':     20,
        'session_timeout':  120,
        'global_delay_factor': 2,
    }

    conn = ConnectHandler(**device)
    return conn, tunel_ssh


def _fechar_tunel(tunel):
    if not tunel:
        return
    ssh, server_sock = tunel
    try:
        server_sock.close()
    except Exception:
        pass
    try:
        ssh.close()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
# VIEWS — LISTAGEM / CRUD
# ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
@ferramenta_instancia_required('scripts')
def listar_scripts(request):
    """GET /clientes/scripts/ — retorna JSON com scripts ativos"""
    fabricante = request.GET.get('fabricante', '')
    qs = ScriptCRM.objects.filter(ativo=True)
    if fabricante:
        qs = qs.filter(fabricante=fabricante)

    dados = []
    for s in qs:
        dados.append({
            'id':               s.id,
            'nome':             s.nome,
            'descricao':        s.descricao,
            'fabricante':       s.fabricante,
            'fabricante_display': s.get_fabricante_display(),
            'modo':             s.modo_execucao,
            'n_params':         len(s.parametros),
            'n_cmds':           len([l for l in s.comandos.splitlines()
                                     if l.strip() and not l.strip().startswith('#')]),
        })

    return JsonResponse({'scripts': dados})


@login_required(login_url='login')
@ferramenta_instancia_required('scripts')
def detalhe_script(request, script_id):
    """GET /clientes/scripts/<id>/ — retorna detalhe completo"""
    script = get_object_or_404(ScriptCRM, id=script_id, ativo=True)
    return JsonResponse({
        'id':         script.id,
        'nome':       script.nome,
        'descricao':  script.descricao,
        'fabricante': script.fabricante,
        'modo':       script.modo_execucao,
        'comandos':   script.comandos,
        'parametros': script.parametros,
    })


@login_required(login_url='login')
@ferramenta_instancia_required('scripts')
def gerenciar_scripts(request):
    """GET /clientes/scripts/gerenciar/ — painel de gestão (staff)"""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('login')
    scripts = ScriptCRM.objects.all().order_by('fabricante', 'nome')
    total_execucoes = ScriptExecucaoLog.objects.count()
    return render(request, 'scripts/gerenciar.html', {
        'scripts': scripts,
        'total_execucoes': total_execucoes,
    })


@login_required(login_url='login')
@require_http_methods(["POST"])
@ferramenta_instancia_required('scripts')
def salvar_script(request, script_id=None):
    """POST /clientes/scripts/salvar/ ou /clientes/scripts/salvar/<id>/"""
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    try:
        dados = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    if script_id:
        script = get_object_or_404(ScriptCRM, id=script_id)
    else:
        script = ScriptCRM(criado_por=request.user)

    script.nome          = dados.get('nome', '').strip()
    script.descricao     = dados.get('descricao', '').strip()
    script.fabricante    = dados.get('fabricante', 'generico')
    script.modo_execucao = dados.get('modo_execucao', 'operacional')
    script.comandos      = dados.get('comandos', '').strip()
    script.ativo         = bool(dados.get('ativo', True))

    parametros = dados.get('parametros', [])
    if isinstance(parametros, str):
        try:
            parametros = json.loads(parametros)
        except Exception:
            parametros = []
    script.parametros = parametros

    if not script.nome:
        return JsonResponse({'error': 'Nome obrigatório'}, status=400)
    if not script.comandos:
        return JsonResponse({'error': 'Comandos obrigatórios'}, status=400)

    script.save()
    return JsonResponse({'success': True, 'id': script.id})


@login_required(login_url='login')
@require_http_methods(["POST"])
@ferramenta_instancia_required('scripts')
def deletar_script(request, script_id):
    """POST /clientes/scripts/deletar/<id>/"""
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Sem permissão'}, status=403)
    script = get_object_or_404(ScriptCRM, id=script_id)
    script.delete()
    return JsonResponse({'success': True})


@login_required(login_url='login')
@ferramenta_instancia_required('scripts')
def historico_execucoes(request, acesso_id):
    """GET /clientes/scripts/historico/<acesso_id>/ — últimas execuções"""
    acesso = get_object_or_404(Acesso, id=acesso_id)
    if not pode_acessar_cliente(request.user, acesso.cliente):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    logs = ScriptExecucaoLog.objects.filter(
        acesso_id=acesso_id
    ).select_related('script', 'usuario')[:20]

    dados = []
    for log in logs:
        dados.append({
            'id':            log.id,
            'script':        log.script.nome if log.script else '—',
            'usuario':       log.usuario.username if log.usuario else '—',
            'status':        log.status,
            'iniciado_em':   log.iniciado_em.strftime('%d/%m/%Y %H:%M:%S'),
            'finalizado_em': log.finalizado_em.strftime('%d/%m/%Y %H:%M:%S') if log.finalizado_em else None,
            'params':        log.parametros_usados,
        })
    return JsonResponse({'historico': dados})


# ──────────────────────────────────────────────────────────────
# ZTE AUTO-PROVISIONAMENTO
# ──────────────────────────────────────────────────────────────

def _zte_auto_provisionar(conn, params: dict):
    """
    Lógica de auto-provisionamento em massa de ONUs ZTE.
    Traduz o algoritmo do script original Oltzte.py para uso integrado.
    Yield: tuplas (tipo, payload_dict)
    """
    import re as _re

    pon         = params.get('PON', '1/3/1')
    vlan        = params.get('VLAN', '1655')
    tipo_onu    = params.get('TIPO_ONU', 'F601')
    perfil      = params.get('TCONT_PROFILE', '1g')
    nome_prefix = params.get('NOME_PREFIX', 'CLIENTE')

    serial_re = _re.compile(r'([A-Z0-9]{12})')

    # ── Passo 1: contar ONUs já autorizadas na PON ──
    # Usa o mesmo comando e filtro do script original Oltzte.py
    cmd_state = 'show gpon onu state'
    yield ('info',    {'msg': f'[1/3] Consultando ONUs autorizadas: {cmd_state}'})
    out1 = conn.send_command(cmd_state, read_timeout=60)
    yield ('output',  {'output': out1, 'cmd': cmd_state})

    # Extrai linhas da PON alvo e obtém o maior ID de ONU em uso
    onu_idx_re  = _re.compile(_re.escape(pon) + r':(\d+)')
    linhas_auth = [l for l in out1.splitlines() if pon in l]
    ids_usados  = set()
    for l in linhas_auth:
        m = onu_idx_re.search(l)
        if m:
            ids_usados.add(int(m.group(1)))
    x = max(ids_usados) if ids_usados else 0   # próximo ID = max + 1
    yield ('info',    {'msg': f'ONUs já autorizadas na PON {pon}: {len(linhas_auth)} (max ID: {x})'})

    # ── Passo 2: listar ONUs não autorizadas ──
    cmd_un = 'show pon onu un sn'
    yield ('info',    {'msg': f'[2/3] Consultando ONUs aguardando autorização: {cmd_un}'})
    out2 = conn.send_command(cmd_un, read_timeout=60)
    yield ('output',  {'output': out2, 'cmd': cmd_un})

    # Filtra apenas as ONUs da PON alvo (idêntico ao original: "gpon_olt-1/3/1" in line)
    linhas_nao_auth = [l for l in out2.splitlines() if f'gpon_olt-{pon}' in l]
    total_novas     = len(linhas_nao_auth)
    yield ('info',    {'msg': f'ONUs aguardando autorização na PON {pon}: {total_novas}'})

    if not linhas_nao_auth:
        yield ('info', {'msg': 'Nenhuma ONU nova para provisionar. Processo encerrado.'})
        return

    # ── Passo 3: provisionar cada ONU nova ──
    yield ('info', {'msg': f'[3/3] Provisionando {total_novas} ONU(s)...'})
    provisionadas = 0
    erros         = 0

    for i, linha in enumerate(linhas_nao_auth):
        m = serial_re.search(linha)
        if not m:
            yield ('info', {'msg': f'  ⚠ Serial não encontrado na linha: {linha.strip()}'})
            erros += 1
            continue

        serial  = m.group(1)
        onu_id  = x + i + 1
        nome_on = f'{nome_prefix}-{onu_id}'

        yield ('cmd', {'cmd': f'Provisionando ONU {onu_id} (serial {serial})', 'idx': i + 1, 'total': total_novas})

        cmds = [
            f'interface gpon_olt-{pon}',
            f'onu {onu_id} type {tipo_onu} sn {serial}',
            'exit',
            f'interface gpon_onu-{pon}:{onu_id}',
            f'name {nome_on}',
            'sn-bind enable sn',
            f'tcont 1 profile {perfil}',
            'gemport 1 tcont 1',
            'exit',
            f'interface vport-{pon}.{onu_id}:1',
            f'service-port 1 user-vlan {vlan} vlan {vlan}',
            'exit',
            f'pon-onu-mng gpon_onu-{pon}:{onu_id}',
            f'service 1 gemport 1 vlan {vlan}',
            f'vlan port eth_0/1 mode tag vlan {vlan}',
        ]

        try:
            out = conn.send_config_set(cmds, read_timeout=90)
            yield ('output', {'output': out, 'cmd': f'ONU {onu_id} provisionada'})
            provisionadas += 1
        except Exception as e:
            yield ('erro_cmd', {'cmd': f'ONU {onu_id}', 'erro': str(e), 'idx': i + 1})
            erros += 1

    yield ('info', {
        'msg': (f'Processo concluído. {provisionadas}/{total_novas} ONU(s) provisionada(s)'
                + (f', {erros} erro(s)' if erros else '') + '.')
    })


# ──────────────────────────────────────────────────────────────
# EXECUÇÃO COM SSE (Server-Sent Events)
# ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
@require_http_methods(["POST"])
@ferramenta_instancia_required('scripts')
def executar_script(request):
    """
    POST /clientes/scripts/executar/
    Body JSON: { acesso_id, script_id, parametros: {} }
    Retorna StreamingHttpResponse com Server-Sent Events.
    """
    try:
        dados = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    acesso_id = dados.get('acesso_id')
    script_id = dados.get('script_id')
    params    = dados.get('parametros', {})

    try:
        acesso = Acesso.objects.select_related('cliente').get(id=acesso_id)
        script = ScriptCRM.objects.get(id=script_id, ativo=True)
    except Acesso.DoesNotExist:
        return JsonResponse({'error': 'Acesso não encontrado'}, status=404)
    except ScriptCRM.DoesNotExist:
        return JsonResponse({'error': 'Script não encontrado'}, status=404)

    if not pode_acessar_cliente(request.user, acesso.cliente):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    # Normaliza chaves: remove {} caso usuário tenha digitado "{PON}" em vez de "PON"
    params_norm = {k.strip('{}').upper(): v for k, v in params.items()}

    # Captura user id para uso na thread (request não é thread-safe)
    user_id = request.user.id

    def _stream():
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            usuario_obj = User.objects.get(pk=user_id)
        except Exception:
            usuario_obj = None

        log = ScriptExecucaoLog.objects.create(
            script=script,
            acesso=acesso,
            usuario=usuario_obj,
            parametros_usados=params,
            status='executando',
        )

        output_total = []
        conn = None
        tunel = None
        status_final = 'sucesso'

        def sse(tipo, payload):
            return f"data: {json.dumps({'tipo': tipo, **payload})}\n\n"

        try:
            # Expandir template
            comandos = _expandir_template(script.comandos, params_norm)
            yield sse('info', {'msg': f'Script: {script.nome}', 'total_cmds': len(comandos)})
            yield sse('info', {'msg': f'Conectando a {acesso.host}:{acesso.porta}...'})

            conn, tunel = _conectar_script(acesso, script.fabricante)
            yield sse('info', {'msg': 'Conectado com sucesso.'})

            if script.modo_execucao == 'zte_auto_prov':
                # ── Auto-provisionamento em massa ZTE ──
                for tipo, payload in _zte_auto_provisionar(conn, params_norm):
                    chunk = sse(tipo, payload)
                    output_total.append(payload.get('output') or payload.get('msg') or payload.get('erro') or '')
                    yield chunk
                yield sse('fim', {'status': status_final, 'total': 0})

            elif script.modo_execucao == 'configuracao':
                # Envia todos como config set
                yield sse('cmd', {'cmd': '(modo configuração)', 'idx': 0, 'total': len(comandos)})
                output = conn.send_config_set(comandos, read_timeout=60)
                output_total.append(output)
                yield sse('output', {'output': output, 'idx': 0})
                yield sse('fim', {'status': status_final, 'total': len(comandos)})

            else:
                # Operacional — envia um por um
                for idx, cmd in enumerate(comandos):
                    yield sse('cmd', {'cmd': cmd, 'idx': idx + 1, 'total': len(comandos)})
                    try:
                        output = conn.send_command(cmd, read_timeout=30)
                        output_total.append(f"# {cmd}\n{output}")
                        yield sse('output', {'output': output, 'cmd': cmd, 'idx': idx + 1})
                    except Exception as e:
                        err_msg = f"Erro no comando '{cmd}': {e}"
                        output_total.append(err_msg)
                        yield sse('erro_cmd', {'cmd': cmd, 'erro': str(e), 'idx': idx + 1})
                        status_final = 'parcial'
                yield sse('fim', {'status': status_final, 'total': len(comandos)})

        except Exception as e:
            logger.exception("[SCRIPT] Erro de execução")
            status_final = 'erro'
            output_total.append(f"ERRO: {e}")
            yield sse('erro', {'erro': str(e)})

        finally:
            if conn:
                try:
                    conn.disconnect()
                except Exception:
                    pass
            _fechar_tunel(tunel)

            log.output = '\n'.join(output_total)
            log.status = status_final
            log.finalizado_em = datetime.now()
            log.save(update_fields=['output', 'status', 'finalizado_em'])

    response = StreamingHttpResponse(_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response
