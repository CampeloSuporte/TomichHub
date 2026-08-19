"""
monitoramento/views.py
Views da aba de Monitoramento de Rede.
"""
import json
import logging
import socket
import threading
import ipaddress
import paramiko
from urllib.parse import urlparse

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from .models import MonitorDashConfig, MonitorLink, MonitorNode, MonitorTopology, ZabbixConfig
from . import services

# Import do ProxyServer do app clientes
from clientes.models import ProxyServer
from clientes.decorators import ferramenta_instancia_required
from usuario.perms import pode_acessar_cliente as _perms_pode_acessar_cliente

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# HELPERS DE PERMISSÃO
# ──────────────────────────────────────────────────────────────

def _pode_acessar_cliente(request, cliente_id: str) -> bool:
    try:
        from clientes.models import Cliente
        cliente = Cliente.objects.get(id=cliente_id)
    except Exception:
        return False
    return _perms_pode_acessar_cliente(request.user, cliente)


# ──────────────────────────────────────────────────────────────
# HELPERS DE TÚNEL SSH PARA ZABBIX COM IP PRIVADO
# ──────────────────────────────────────────────────────────────

def _is_private_ip(host: str) -> bool:
    """Verifica se o host é um IP privado (RFC-1918)."""
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        # hostname DNS — assume público
        return False


def _criar_tunel_zabbix(proxy, zbx_host, zbx_port, timeout=10):
    """
    Cria túnel SSH para acessar Zabbix em IP privado.
    Retorna (local_port, ssh_client, server_sock) ou None se falhar.
    """
    try:
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(
            hostname=proxy.host,
            port=int(proxy.porta),
            username=proxy.usuario,
            password=proxy.senha,
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
            banner_timeout=timeout,
        )

        # Encontrar porta local livre
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('127.0.0.1', 0))
        local_port = s.getsockname()[1]
        s.close()

        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(('127.0.0.1', local_port))
        server_sock.listen(5)
        server_sock.settimeout(1)

        transport = ssh_client.get_transport()

        def _forward(client_socket):
            try:
                channel = transport.open_channel(
                    'direct-tcpip',
                    (zbx_host, int(zbx_port)),
                    ('127.0.0.1', local_port),
                )

                def _pipe(src, dst):
                    try:
                        while True:
                            data = src.recv(65536)
                            if not data:
                                break
                            dst.sendall(data)
                    except Exception:
                        pass
                    finally:
                        try: src.close()
                        except: pass
                        try: dst.close()
                        except: pass

                t1 = threading.Thread(target=_pipe, args=(client_socket, channel), daemon=True)
                t2 = threading.Thread(target=_pipe, args=(channel, client_socket), daemon=True)
                t1.start()
                t2.start()
            except Exception as e:
                logger.error(f"[ZBX TUNNEL] forward error: {e}")
                try: client_socket.close()
                except: pass

        def _accept_loop():
            while True:
                try:
                    client_sock, _ = server_sock.accept()
                    threading.Thread(target=_forward, args=(client_sock,), daemon=True).start()
                except socket.timeout:
                    continue
                except Exception:
                    break

        threading.Thread(target=_accept_loop, daemon=True).start()
        logger.info(f"[ZBX TUNNEL] túnel criado: localhost:{local_port} → {zbx_host}:{zbx_port}")
        return local_port, ssh_client, server_sock

    except Exception as e:
        logger.error(f"[ZBX TUNNEL] erro ao criar túnel: {e}")
        return None


def _fechar_tunel(tunel):
    """No-op intencional: túneis Zabbix agora vivem em _tunnel_pool e são
    reaproveitados entre requisições/pollings (ver _pool_get_or_create_tunnel).
    Antes, fechar aqui matava o túnel logo após cada request — cada poll de
    15s do gráfico refazia handshake SSH + login Zabbix do zero. Mantido
    como no-op (em vez de removido) para não obrigar a mexer no `finally:
    _fechar_tunel(tunel)` de cada view que já usa esse helper."""
    pass


def _fechar_tunel_real(tunel):
    """Fecha de fato um túnel — usado só internamente pelo pool, quando o
    túnel está morto/ocioso demais e precisa ser substituído."""
    if not tunel:
        return
    _, ssh_client, server_sock = tunel
    try: server_sock.close()
    except: pass
    try: ssh_client.close()
    except: pass


_tunnel_pool = {}
_tunnel_pool_lock = threading.Lock()
_TUNNEL_IDLE_TIMEOUT = 300  # 5 min sem uso -> túnel é descartado no próximo acesso


def _pool_get_or_create_tunnel(cliente_id, proxy, zbx_host, zbx_port):
    """Reaproveita o túnel SSH já aberto para este cliente (se ainda vivo,
    mesmo destino e não ocioso há mais de _TUNNEL_IDLE_TIMEOUT); senão cria
    um novo. Isso evita abrir uma conexão SSH nova a cada requisição de
    histórico/status — o maior custo da abertura da aba de Monitoramento
    quando o Zabbix está atrás de um proxy (IP privado)."""
    import time as _time
    with _tunnel_pool_lock:
        entry = _tunnel_pool.get(cliente_id)
        if entry:
            transport = entry['ssh_client'].get_transport()
            idle = _time.time() - entry['last_used']
            ainda_valido = (
                transport is not None and transport.is_active()
                and entry['zbx_host'] == zbx_host and entry['zbx_port'] == zbx_port
                and idle < _TUNNEL_IDLE_TIMEOUT
            )
            if ainda_valido:
                entry['last_used'] = _time.time()
                return entry['local_port'], entry['ssh_client'], entry['server_sock']
            _fechar_tunel_real((entry['local_port'], entry['ssh_client'], entry['server_sock']))
            _tunnel_pool.pop(cliente_id, None)

        tunel = _criar_tunel_zabbix(proxy, zbx_host, zbx_port)
        if not tunel:
            return None
        local_port, ssh_client, server_sock = tunel
        _tunnel_pool[cliente_id] = {
            'local_port': local_port, 'ssh_client': ssh_client, 'server_sock': server_sock,
            'last_used': _time.time(), 'zbx_host': zbx_host, 'zbx_port': zbx_port,
        }
        return local_port, ssh_client, server_sock


def _get_config_com_tunel(config, cliente_id):
    """
    Se a URL do Zabbix tiver IP privado, reaproveita/cria túnel SSH via
    _pool_get_or_create_tunnel e retorna (config_modificado, tunel) onde
    config_modificado aponta para localhost:porta_local.
    Caso contrário retorna (config_original, None).
    """
    parsed   = urlparse(config.url)
    zbx_host = parsed.hostname
    zbx_port = parsed.port or (443 if parsed.scheme == 'https' else 80)

    if not _is_private_ip(zbx_host):
        return config, None

    # IP privado — precisa de túnel
    proxy = ProxyServer.objects.filter(
        cliente_id=cliente_id, ativo=True
    ).first()

    if not proxy:
        # Verificar se VPN WireGuard cobre este IP (fallback sem túnel SSH)
        try:
            from clientes.views import vpn_cobre_ip
            from clientes.models import Cliente as _Cliente
            _cli = _Cliente.objects.get(id=cliente_id)
            if vpn_cobre_ip(_cli, zbx_host):
                logger.info(f"[ZBX] WireGuard VPN cobre {zbx_host} — conectando diretamente")
                return config, None
        except Exception as _e:
            logger.debug(f"[ZBX] vpn_cobre_ip check falhou: {_e}")
        raise Exception(
            f"Zabbix tem IP privado ({zbx_host}) mas não há proxy SSH ativo "
            f"para este cliente. Configure na aba 'Túneis SSH'."
        )

    tunel = _pool_get_or_create_tunnel(cliente_id, proxy, zbx_host, zbx_port)
    if not tunel:
        raise Exception(f"Falha ao criar túnel SSH para {zbx_host}:{zbx_port}")

    local_port, _, _ = tunel

    # Montar URL local mantendo o path original
    nova_url = f"{parsed.scheme}://127.0.0.1:{local_port}"
    if parsed.path:
        nova_url += parsed.path

    # Criar config temporário com URL substituída
    class _ConfigProxy:
        pass

    cfg           = _ConfigProxy()
    cfg.id        = config.id  # mantém estável p/ cache de token/metadados mesmo com a porta local do túnel mudando
    cfg.url       = nova_url
    cfg.usuario   = config.usuario
    cfg.senha     = config.senha
    cfg.api_token = getattr(config, 'api_token', None)
    cfg.ativo     = config.ativo

    logger.info(f"[ZBX TUNNEL] usando túnel: {config.url} → {nova_url}")
    return cfg, tunel


# ──────────────────────────────────────────────────────────────
# CONFIGURAÇÃO ZABBIX
# ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
@require_http_methods(["POST"])
@ferramenta_instancia_required('monitoramento')
def salvar_zabbix_config(request):
    cliente_id = request.POST.get('cliente')
    url        = request.POST.get('url', '').strip().rstrip('/')
    usuario    = request.POST.get('usuario', '').strip()
    senha      = request.POST.get('senha', '').strip()
    api_token  = request.POST.get('api_token', '').strip()

    if not cliente_id or not url:
        return JsonResponse({'error': 'URL e cliente são obrigatórios'}, status=400)

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    config, _ = ZabbixConfig.objects.update_or_create(
        cliente_id=cliente_id,
        defaults={
            'url':       url,
            'usuario':   usuario,
            'senha':     senha,
            'api_token': api_token or None,
            'ativo':     True,
        },
    )

    tunel = None
    try:
        config, tunel = _get_config_com_tunel(config, cliente_id)
        version = services.testar_conexao(config)
        return JsonResponse({'success': True, 'message': f'Conectado! Zabbix v{version}'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'Configurado, mas teste falhou: {e}'})
    finally:
        _fechar_tunel(tunel)


@login_required(login_url='login')
@ferramenta_instancia_required('monitoramento')
def buscar_zabbix_config(request):
    cliente_id = request.GET.get('id')
    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    try:
        config = ZabbixConfig.objects.get(cliente_id=cliente_id)
        return JsonResponse({
            'existe':    True,
            'url':       config.url,
            'usuario':   config.usuario,
            'tem_token': bool(config.api_token),
            'ativo':     config.ativo,
        })
    except ZabbixConfig.DoesNotExist:
        return JsonResponse({'existe': False})


@login_required(login_url='login')
@ferramenta_instancia_required('monitoramento')
def autoconfig_zabbix(request):
    """
    Busca automaticamente a configuração do Zabbix a partir de um Acesso
    do cliente cujo tipo contenha 'zabbix' (case-insensitive).
    Retorna URL, usuário e senha para pré-preencher o modal.
    """
    cliente_id = request.GET.get('id')
    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    from clientes.models import Acesso
    acesso = Acesso.objects.filter(
        cliente_id=cliente_id,
        tipo__icontains='zabbix'
    ).first()

    if not acesso:
        return JsonResponse({'encontrado': False, 'message': 'Nenhum host com nome "zabbix" encontrado nos acessos deste cliente.'})

    protocolo = (acesso.protocolo or 'HTTPS').upper()
    if protocolo in ('HTTP',):
        scheme = 'http'
    else:
        scheme = 'https'

    porta = acesso.porta or (443 if scheme == 'https' else 80)
    url = f"{scheme}://{acesso.host}:{porta}"

    return JsonResponse({
        'encontrado': True,
        'url': url,
        'usuario': acesso.usuario or '',
        'senha': acesso.senha or '',
        'tipo': acesso.tipo,
        'host': acesso.host,
    })


@login_required(login_url='login')
@ferramenta_instancia_required('monitoramento')
def testar_zabbix_conexao(request):
    cliente_id = request.GET.get('id')
    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    tunel = None
    try:
        config  = ZabbixConfig.objects.get(cliente_id=cliente_id, ativo=True)
        config, tunel = _get_config_com_tunel(config, cliente_id)
        version = services.testar_conexao(config)
        return JsonResponse({'success': True, 'message': f'Zabbix v{version} — conexão OK'})
    except ZabbixConfig.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Zabbix não configurado para este cliente'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})
    finally:
        _fechar_tunel(tunel)


# ──────────────────────────────────────────────────────────────
# HOSTS E INTERFACES ZABBIX
# ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
@ferramenta_instancia_required('monitoramento')
def listar_hosts_zabbix(request):
    cliente_id = request.GET.get('id')
    busca      = request.GET.get('q', '')

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    tunel = None
    try:
        config = ZabbixConfig.objects.get(cliente_id=cliente_id, ativo=True)
        config, tunel = _get_config_com_tunel(config, cliente_id)
        hosts  = services.listar_hosts(config, busca)
        return JsonResponse({'hosts': hosts})
    except ZabbixConfig.DoesNotExist:
        return JsonResponse({'error': 'Zabbix não configurado para este cliente'}, status=400)
    except Exception as e:
        logger.exception("Erro ao listar hosts Zabbix")
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        _fechar_tunel(tunel)


@login_required(login_url='login')
@ferramenta_instancia_required('monitoramento')
def listar_interfaces_zabbix(request):
    cliente_id = request.GET.get('cliente_id')
    host_id    = request.GET.get('host_id')

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    tunel = None
    try:
        config     = ZabbixConfig.objects.get(cliente_id=cliente_id, ativo=True)
        config, tunel = _get_config_com_tunel(config, cliente_id)
        interfaces = services.listar_interfaces(config, host_id)
        return JsonResponse({'interfaces': interfaces})
    except ZabbixConfig.DoesNotExist:
        return JsonResponse({'error': 'Zabbix não configurado'}, status=400)
    except Exception as e:
        logger.exception("Erro ao listar interfaces")
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        _fechar_tunel(tunel)


@login_required(login_url='login')
@ferramenta_instancia_required('monitoramento')
def historico_item_zabbix(request):
    cliente_id = request.GET.get('cliente_id')
    item_id    = request.GET.get('item_id')
    hours      = int(request.GET.get('hours', 1))

    # Garante valor válido
    if hours not in (1, 3, 6, 12, 24):
        hours = 1

    # O frontend já manda o limit real que o gráfico usa (ex: 60 pontos) —
    # antes essa view ignorava e sempre buscava até 1000 pontos brutos do
    # Zabbix a cada poll de 15s. Teto de segurança em 1000.
    try:
        limit = int(request.GET.get('limit', 300))
    except (TypeError, ValueError):
        limit = 300
    limit = max(1, min(limit, 1000))

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    tunel = None
    try:
        config  = ZabbixConfig.objects.get(cliente_id=cliente_id, ativo=True)
        config, tunel = _get_config_com_tunel(config, cliente_id)
        history = services.historico_item(config, item_id, hours=hours, limit=limit)
        return JsonResponse({'history': history})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        _fechar_tunel(tunel)


# ──────────────────────────────────────────────────────────────
# CRUD DE TOPOLOGIAS
# ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
@ferramenta_instancia_required('monitoramento')
def listar_topologias(request):
    cliente_id = request.GET.get('id')
    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    topos = MonitorTopology.objects.filter(
        cliente_id=cliente_id
    ).prefetch_related('nodes', 'links').order_by('-data_atualizacao')

    return JsonResponse({
        'topologias': [{
            'id':               t.id,
            'nome':             t.nome,
            'descricao':        t.descricao,
            'data_atualizacao': t.data_atualizacao.strftime('%d/%m/%Y %H:%M'),
            'total_nodes':      t.nodes.count(),
            'total_links':      t.links.count(),
        } for t in topos]
    })


@login_required(login_url='login')
@require_http_methods(["POST"])
@ferramenta_instancia_required('monitoramento')
def criar_topologia(request):
    try:
        data       = json.loads(request.body)
        cliente_id = data.get('cliente_id')
        nome       = data.get('nome', '').strip()

        if not cliente_id or not nome:
            return JsonResponse({'error': 'cliente_id e nome são obrigatórios'}, status=400)

        if not _pode_acessar_cliente(request, cliente_id):
            return JsonResponse({'error': 'Sem permissão'}, status=403)

        topo = MonitorTopology.objects.create(
            cliente_id=cliente_id,
            nome=nome,
            descricao=data.get('descricao', ''),
        )
        return JsonResponse({'id': topo.id, 'nome': topo.nome})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url='login')
@require_http_methods(["POST"])
@ferramenta_instancia_required('monitoramento')
def salvar_topologia(request):
    try:
        data       = json.loads(request.body)
        topo_id    = data.get('topo_id')
        cliente_id = data.get('cliente_id')

        if not _pode_acessar_cliente(request, cliente_id):
            return JsonResponse({'error': 'Sem permissão'}, status=403)

        topo = get_object_or_404(MonitorTopology, id=topo_id, cliente_id=cliente_id)

        topo.nodes.all().delete()
        topo.links.all().delete()

        node_map: dict = {}
        for nd in data.get('nodes', []):
            node = MonitorNode.objects.create(
                topologia       = topo,
                tipo            = nd.get('tipo', 'switch'),
                label           = nd.get('label', 'Node'),
                zabbix_hostid   = nd.get('zabbix_hostid')   or None,
                zabbix_hostname = nd.get('zabbix_hostname') or None,
                pos_x           = nd.get('x', 200),
                pos_y           = nd.get('y', 200),
            )
            node_map[str(nd['id'])] = node

        for lk in data.get('links', []):
            src = node_map.get(str(lk['source']))
            dst = node_map.get(str(lk['target']))
            if src and dst:
                MonitorLink.objects.create(
                    topologia            = topo,
                    node_origem          = src,
                    node_destino         = dst,
                    label                = lk.get('label', ''),
                    zabbix_itemid_in     = lk.get('itemid_in')     or None,
                    zabbix_itemid_out    = lk.get('itemid_out')    or None,
                    zabbix_itemid_status = lk.get('itemid_status') or None,
                )

        topo.save()
        return JsonResponse({'success': True})

    except Exception as e:
        logger.exception("Erro ao salvar topologia")
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url='login')
@ferramenta_instancia_required('monitoramento')
def carregar_topologia(request):
    topo_id    = request.GET.get('topo_id')
    cliente_id = request.GET.get('cliente_id')

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    topo  = get_object_or_404(MonitorTopology, id=topo_id, cliente_id=cliente_id)
    nodes = MonitorNode.objects.filter(topologia=topo)
    links = MonitorLink.objects.filter(topologia=topo)

    return JsonResponse({
        'id':    topo.id,
        'nome':  topo.nome,
        'nodes': [{
            'id':              n.id,
            'tipo':            n.tipo,
            'label':           n.label,
            'zabbix_hostid':   n.zabbix_hostid,
            'zabbix_hostname': n.zabbix_hostname,
            'x':               n.pos_x,
            'y':               n.pos_y,
        } for n in nodes],
        'links': [{
            'id':            lk.id,
            'source':        lk.node_origem_id,
            'target':        lk.node_destino_id,
            'label':         lk.label,
            'itemid_in':     lk.zabbix_itemid_in,
            'itemid_out':    lk.zabbix_itemid_out,
            'itemid_status': lk.zabbix_itemid_status,
        } for lk in links],
    })


@login_required(login_url='login')
@require_http_methods(["POST"])
@ferramenta_instancia_required('monitoramento')
def deletar_topologia(request, topo_id):
    topo       = get_object_or_404(MonitorTopology, id=topo_id)
    cliente_id = topo.cliente_id

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    topo.delete()
    return JsonResponse({'success': True, 'cliente_id': cliente_id})


# ──────────────────────────────────────────────────────────────
# STATUS EM TEMPO REAL
# ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
@ferramenta_instancia_required('monitoramento')
def status_topologia(request):
    topo_id    = request.GET.get('topo_id')
    cliente_id = request.GET.get('cliente_id')

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    tunel = None
    try:
        config    = ZabbixConfig.objects.get(cliente_id=cliente_id, ativo=True)
        config, tunel = _get_config_com_tunel(config, cliente_id)
        topologia = get_object_or_404(MonitorTopology, id=topo_id, cliente_id=cliente_id)

        nodes = list(MonitorNode.objects.filter(topologia=topologia))
        links = list(MonitorLink.objects.filter(topologia=topologia))

        host_ids = list({n.zabbix_hostid for n in nodes if n.zabbix_hostid})
        item_ids = []
        for lk in links:
            for fid in [lk.zabbix_itemid_in, lk.zabbix_itemid_out, lk.zabbix_itemid_status]:
                if fid:
                    item_ids.append(fid)

        host_status = services.status_nodes(config, host_ids)
        item_values = services.status_items(config, item_ids)

        # ── Nodes ─────────────────────────────────────────
        nodes_resp = {}
        for n in nodes:
            nodes_resp[n.id] = {
                'status': host_status.get(n.zabbix_hostid, 'unknown')
                          if n.zabbix_hostid else 'unconfigured',
                'hostid': n.zabbix_hostid,
            }

        # ── Links ─────────────────────────────────────────
        links_resp = {}
        for lk in links:
            val_in     = item_values.get(lk.zabbix_itemid_in,     {}) if lk.zabbix_itemid_in     else {}
            val_out    = item_values.get(lk.zabbix_itemid_out,    {}) if lk.zabbix_itemid_out    else {}
            val_status = item_values.get(lk.zabbix_itemid_status, {}) if lk.zabbix_itemid_status else {}

            traffic_in  = val_in.get('value')  if val_in  else None
            traffic_out = val_out.get('value')  if val_out else None

            raw_status = val_status.get('value') if val_status else None
            if raw_status is not None:
                link_up = str(raw_status).strip() == '1'
            elif traffic_in is not None or traffic_out is not None:
                total   = float(traffic_in or 0) + float(traffic_out or 0)
                link_up = total > 0
            else:
                link_up = None

            links_resp[lk.id] = {
                'status':      'up'   if link_up is True
                          else 'down' if link_up is False
                          else 'unknown',
                'traffic_in':  services.format_bps(traffic_in)  if traffic_in  is not None else None,
                'traffic_out': services.format_bps(traffic_out) if traffic_out is not None else None,
                'raw_in':      traffic_in,
                'raw_out':     traffic_out,
            }

        return JsonResponse({'success': True, 'nodes': nodes_resp, 'links': links_resp})

    except ZabbixConfig.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Zabbix não configurado'}, status=400)
    except Exception as e:
        logger.exception("Erro ao buscar status da topologia")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
    finally:
        _fechar_tunel(tunel)



@login_required(login_url='login')
@ferramenta_instancia_required('monitoramento')
def listar_itens_zabbix(request):
    cliente_id = request.GET.get('cliente_id')
    host_id    = request.GET.get('host_id')
    busca      = request.GET.get('q', '')

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    tunel = None
    try:
        config = ZabbixConfig.objects.get(cliente_id=cliente_id, ativo=True)
        config, tunel = _get_config_com_tunel(config, cliente_id)
        itens  = services.listar_itens(config, host_id, busca)
        return JsonResponse({'itens': itens})
    except ZabbixConfig.DoesNotExist:
        return JsonResponse({'error': 'Zabbix não configurado'}, status=400)
    except Exception as e:
        logger.exception("Erro ao listar itens")
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        _fechar_tunel(tunel)

# ──────────────────────────────────────────────────────────────
# DASHBOARD DE GRÁFICOS — persistência no banco por cliente
# ──────────────────────────────────────────────────────────────

@login_required
@require_http_methods(['GET'])
@ferramenta_instancia_required('monitoramento')
def carregar_dash_config(request):
    cliente_id = request.GET.get('id') or request.GET.get('cliente_id')
    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)
    try:
        cfg = MonitorDashConfig.objects.get(cliente_id=cliente_id)
        dados = cfg.dados
        # Formato antigo: lista plana de charts → envolve em aba padrão
        if isinstance(dados, list):
            return JsonResponse({'tabs': [{'id': 1, 'nome': 'Geral', 'charts': dados}]})
        # Formato novo: dict com chave 'tabs'
        return JsonResponse(dados)
    except MonitorDashConfig.DoesNotExist:
        return JsonResponse({'tabs': []})


@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('monitoramento')
def salvar_dash_config(request):
    try:
        body = json.loads(request.body)
        cliente_id = body.get('cliente_id')
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)
    tabs   = body.get('tabs')
    charts = body.get('charts', [])
    dados  = {'tabs': tabs} if tabs is not None else charts
    MonitorDashConfig.objects.update_or_create(
        cliente_id=cliente_id,
        defaults={'dados': dados},
    )
    return JsonResponse({'success': True})
