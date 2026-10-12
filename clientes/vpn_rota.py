"""
Saída pelo túnel OpenVPN de um cliente.

As redes de cada cliente ficam numa tabela de rotas isolada do túnel, usada
só pelo tráfego cuja origem é o IP do servidor naquele túnel (ver
openvpn_tunnel_manager). Por isso, conectar "direto" no IP do equipamento
não basta mais: sem a origem, o kernel usa a tabela main e o pacote sai pela
internet. Toda conexão a equipamento de cliente via VPN passa por aqui:

  - `tunel_para(cliente, host)`: túnel do cliente que entrega o host (a rota
    da tabela isolada sai mesmo pela tun dele), ou None;
  - `conectar(tunel, host, porta)`: socket TCP já com a origem do túnel;
  - `endpoint_local(tunel, host, porta)`: ('127.0.0.1', porta) de um relay
    local que conecta com a origem do túnel — para libs que não aceitam
    socket nem bind de origem (netmiko, telnetlib, requests, navegador…),
    no mesmo formato de `criar_ssh_tunnel`.
"""
import ipaddress
import logging
import socket
import threading

logger = logging.getLogger(__name__)


def _host_ip(host):
    try:
        return ipaddress.ip_address(str(host).strip())
    except ValueError:
        return None


def tunel_para(cliente, host):
    """Túnel OpenVPN ativo do cliente que declara uma rede contendo `host` e
    cuja tabela isolada realmente entrega o host pela tun dele."""
    from .models import VPNOpenVPN
    from . import openvpn_tunnel_manager as ovpnm

    ip = _host_ip(host)
    if ip is None or ip.version != 4:
        return None
    for tunel in VPNOpenVPN.objects.filter(cliente=cliente, ativo=True, cert_emitido=True):
        declara = False
        for rede in tunel.redes_lista():
            try:
                if ip in ipaddress.ip_network(rede, strict=False):
                    declara = True
                    break
            except ValueError:
                continue
        if not declara:
            continue
        origem = ovpnm.origem_tunel(tunel)
        dev = ovpnm.dev_tun(tunel)
        if not origem or not dev:
            continue
        dev_real = ovpnm.rota_dev_para(ip, origem=origem)
        if dev_real == dev:
            return tunel
        logger.warning(
            f'⚠️ Túnel "{tunel.nome}" de {cliente.nome_empresa} declara {ip}, mas a tabela '
            f'isolada dele sai por "{dev_real}" (esperado {dev}) — túnel desconectado ou '
            f'rede fora do .rotas.'
        )
    return None


def origem(tunel):
    from . import openvpn_tunnel_manager as ovpnm
    return ovpnm.origem_tunel(tunel)


def conectar(tunel, host, porta, timeout=10):
    """Socket TCP para host:porta saindo com a origem do túnel."""
    return socket.create_connection((str(host), int(porta)), timeout=timeout,
                                    source_address=(origem(tunel), 0))


# ─── Relay local ──────────────────────────────────────────────────────────────
# Um listener por (origem, host, porta), vivo enquanto o processo viver — o
# mesmo modelo do TunnelPortCache do proxy SSH. Aceita várias conexões (o
# proxy web abre dezenas por página).
_relays = {}
_relays_lock = threading.Lock()


def _bombear(src, dst):
    try:
        while True:
            dados = src.recv(65536)
            if not dados:
                break
            dst.sendall(dados)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


def _atender(cliente_sock, origem_ip, host, porta):
    try:
        remoto = socket.create_connection((host, porta), timeout=15,
                                          source_address=(origem_ip, 0))
        remoto.settimeout(None)
    except OSError as e:
        logger.warning(f'[VPN RELAY] {origem_ip} → {host}:{porta} falhou: {e}')
        try:
            cliente_sock.close()
        except OSError:
            pass
        return
    for s in (cliente_sock, remoto):
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
    threading.Thread(target=_bombear, args=(remoto, cliente_sock), daemon=True).start()
    _bombear(cliente_sock, remoto)


def _aceitar(servidor, origem_ip, host, porta):
    while True:
        try:
            conn, _ = servidor.accept()
        except OSError as e:
            logger.error(f'[VPN RELAY] listener {origem_ip} → {host}:{porta} parou: {e}')
            with _relays_lock:
                _relays.pop((origem_ip, host, porta), None)
            return
        threading.Thread(target=_atender, args=(conn, origem_ip, host, porta), daemon=True).start()


def endpoint_local(tunel, host, porta):
    """('127.0.0.1', porta_local) de um relay que entrega em host:porta pela
    origem do túnel."""
    origem_ip = origem(tunel)
    chave = (origem_ip, str(host), int(porta))
    with _relays_lock:
        porta_local = _relays.get(chave)
        if porta_local:
            return '127.0.0.1', porta_local
        servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        servidor.bind(('127.0.0.1', 0))
        servidor.listen(64)
        porta_local = servidor.getsockname()[1]
        _relays[chave] = porta_local
    threading.Thread(target=_aceitar, args=(servidor, *chave), daemon=True).start()
    logger.info(f'[VPN RELAY] 127.0.0.1:{porta_local} → {host}:{porta} (origem {origem_ip})')
    return '127.0.0.1', porta_local
