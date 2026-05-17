"""
WireGuard VPN Manager
Gerencia chaves, peers e status do servidor WireGuard.
"""
import subprocess
import ipaddress
import logging
import os

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
import base64

logger = logging.getLogger(__name__)

# ─── Configuração fixa do servidor ───────────────────────────────────────────
WG_INTERFACE   = 'wg0'
WG_PORT        = 51820
VPN_SUBNET     = '10.200.0.0/24'
SERVER_VPN_IP  = '10.200.0.1'


# ─── Geração de chaves (sem subprocess) ──────────────────────────────────────

def gerar_chave_privada():
    key = X25519PrivateKey.generate()
    return base64.b64encode(key.private_bytes_raw()).decode()

def derivar_chave_publica(private_key_b64):
    raw = base64.b64decode(private_key_b64)
    key = X25519PrivateKey.from_private_bytes(raw)
    return base64.b64encode(key.public_key().public_bytes_raw()).decode()

def gerar_par_chaves():
    priv = gerar_chave_privada()
    pub  = derivar_chave_publica(priv)
    return priv, pub

def gerar_preshared_key():
    try:
        return subprocess.check_output(['wg', 'genpsk']).decode().strip()
    except Exception:
        import secrets
        return base64.b64encode(secrets.token_bytes(32)).decode()


# ─── Alocação de IP ───────────────────────────────────────────────────────────

def alocar_proximo_ip():
    """Retorna o próximo IP livre na subnet VPN."""
    from .models import VPNWireGuard
    subnet = ipaddress.ip_network(VPN_SUBNET)
    usados = set(VPNWireGuard.objects.values_list('vpn_ip', flat=True))
    for host in subnet.hosts():
        ip = str(host)
        if ip == SERVER_VPN_IP:
            continue
        if ip not in usados:
            return ip
    raise Exception('Sem IPs disponíveis na subnet VPN')


# ─── Gerenciamento de peers no kernel ────────────────────────────────────────

def _wg(*args):
    return subprocess.run(['sudo', 'wg'] + list(args), capture_output=True, text=True)

def _ip(*args):
    return subprocess.run(['sudo', 'ip'] + list(args), capture_output=True, text=True)


def interface_existe():
    r = _ip('link', 'show', WG_INTERFACE)
    return r.returncode == 0


def criar_interface_servidor(server_private_key):
    """Cria a interface wg0 se não existir."""
    if interface_existe():
        return
    subprocess.run(['ip', 'link', 'add', WG_INTERFACE, 'type', 'wireguard'], check=True)
    subprocess.run(['ip', 'address', 'add', f'{SERVER_VPN_IP}/24', 'dev', WG_INTERFACE], check=True)
    _wg('set', WG_INTERFACE, 'private-key', '/dev/stdin',
        'listen-port', str(WG_PORT))
    # escreve a chave via stdin não é possível diretamente; usar arquivo temporário
    key_file = '/tmp/.wg_srv_key'
    with open(key_file, 'w') as f:
        f.write(server_private_key)
    os.chmod(key_file, 0o600)
    subprocess.run(['wg', 'set', WG_INTERFACE, 'listen-port', str(WG_PORT),
                    'private-key', key_file], check=True)
    os.unlink(key_file)
    subprocess.run(['ip', 'link', 'set', WG_INTERFACE, 'up'], check=True)
    # Persistir no wg-quick (opcional — depende do setup)
    logger.info(f'✅ Interface {WG_INTERFACE} criada')


def adicionar_peer(public_key, preshared_key, vpn_ip, redes_privadas=None):
    """Adiciona ou atualiza um peer no wg0 via wg addconf."""
    allowed = [f'{vpn_ip}/32']
    if redes_privadas:
        for rede in redes_privadas:
            rede = rede.strip()
            if rede:
                try:
                    ipaddress.ip_network(rede, strict=False)
                    allowed.append(rede)
                except ValueError:
                    pass

    allowed_str = ', '.join(allowed)

    # Grava config no formato WireGuard e usa wg addconf (sem arquivo PSK separado)
    conf_file = f'/tmp/wg_peer_{vpn_ip.replace(".", "_")}.conf'
    conf_content = (
        f'[Peer]\n'
        f'PublicKey = {public_key}\n'
        f'PresharedKey = {preshared_key}\n'
        f'AllowedIPs = {allowed_str}\n'
    )
    with open(conf_file, 'w') as f:
        f.write(conf_content)
    os.chmod(conf_file, 0o600)

    try:
        r = subprocess.run(
            ['sudo', '/usr/bin/wg', 'addconf', WG_INTERFACE, conf_file],
            capture_output=True, text=True,
            env={**os.environ, 'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'},
        )
        logger.info(f'wg addconf rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}')
        if r.returncode != 0:
            raise Exception(f'wg addconf peer falhou: {r.stderr.strip() or r.stdout.strip()}')
    finally:
        if os.path.exists(conf_file):
            os.unlink(conf_file)

    # Rotas para redes privadas do cliente
    if redes_privadas:
        for rede in redes_privadas:
            rede = rede.strip()
            if rede:
                _ip('route', 'add', rede, 'dev', WG_INTERFACE)

    logger.info(f'✅ Peer adicionado: {public_key[:16]}… → {allowed_str}')


def remover_peer(public_key, redes_privadas=None):
    """Remove um peer do wg0."""
    r = subprocess.run(['sudo', 'wg', 'set', WG_INTERFACE, 'peer', public_key, 'remove'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        logger.warning(f'wg remove peer: {r.stderr}')

    if redes_privadas:
        for rede in redes_privadas:
            rede = rede.strip()
            if rede:
                _ip('route', 'del', rede, 'dev', WG_INTERFACE)

    logger.info(f'✅ Peer removido: {public_key[:16]}…')


def salvar_config_persistente():
    """Salva o estado atual do wg0 em /etc/wireguard/wg0.conf."""
    os.makedirs('/etc/wireguard', exist_ok=True)
    r = subprocess.run(['sudo', 'wg', 'showconf', WG_INTERFACE], capture_output=True, text=True)
    if r.returncode == 0:
        conf = r.stdout
        # wg showconf não inclui Address — garantir que fique no [Interface]
        if '[Interface]' in conf and f'Address = {SERVER_VPN_IP}/24' not in conf:
            conf = conf.replace(
                '[Interface]\n',
                f'[Interface]\nAddress = {SERVER_VPN_IP}/24\n',
                1
            )
        proc = subprocess.run(
            ['sudo', 'tee', '/etc/wireguard/wg0.conf'],
            input=conf, capture_output=True, text=True
        )
        subprocess.run(['sudo', 'chmod', '600', '/etc/wireguard/wg0.conf'])


# ─── Status ──────────────────────────────────────────────────────────────────

def get_peers_status():
    """
    Retorna dict {public_key: {endpoint, latest_handshake, transfer_rx, transfer_tx, allowed_ips}}
    """
    result = {}
    if not interface_existe():
        return result

    r = _wg('show', WG_INTERFACE, 'dump')
    if r.returncode != 0:
        return result

    lines = r.stdout.strip().split('\n')
    for line in lines[1:]:   # primeira linha = interface
        parts = line.split('\t')
        if len(parts) < 8:
            continue
        pub_key       = parts[0]
        endpoint      = parts[2]
        allowed_ips   = parts[3]
        last_handshake = int(parts[4]) if parts[4].isdigit() else 0
        rx            = int(parts[5]) if parts[5].isdigit() else 0
        tx            = int(parts[6]) if parts[6].isdigit() else 0

        result[pub_key] = {
            'endpoint':        endpoint,
            'allowed_ips':     allowed_ips,
            'last_handshake':  last_handshake,
            'rx_bytes':        rx,
            'tx_bytes':        tx,
            'conectado':       last_handshake > 0 and (
                __import__('time').time() - last_handshake < 180
            ),
        }
    return result


def formatar_bytes(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} TB'


# ─── Geração de script MikroTik ──────────────────────────────────────────────

def gerar_script_mikrotik(vpn, server_config):
    """
    Gera script RouterOS completo para configurar o WireGuard na MikroTik.
    vpn: instância de VPNWireGuard
    server_config: instância de VPNServidorConfig
    """
    from datetime import datetime
    agora = datetime.now().strftime('%d/%m/%Y %H:%M')

    redes = [r.strip() for r in vpn.redes_privadas.splitlines() if r.strip()]
    redes_comentario = ', '.join(redes) if redes else 'nenhuma configurada'

    script = f"""# =============================================================
# VPN WireGuard — CRM Tomich
# Cliente : {vpn.cliente.nome_empresa}
# Gerado  : {agora}
# VPN IP  : {vpn.vpn_ip}/24
# Redes   : {redes_comentario}
# =============================================================
# INSTRUÇÕES:
#   1. Abra o Winbox ou SSH na MikroTik
#   2. New Terminal → cole este script → Enter
#   3. Aguarde ~30s e teste: /ping {SERVER_VPN_IP}
#   4. Se OK, o CRM já consegue acessar os hosts internos
# =============================================================

# --- 1. Remover configuração anterior (se existir) ---
:do {{ /interface wireguard remove [find name=wg-crm] }} on-error={{}}
:do {{ /ip address remove [find comment="CRM-VPN"] }} on-error={{}}
:do {{ /ip firewall nat remove [find comment="CRM-VPN-masq"] }} on-error={{}}

# --- 2. Interface WireGuard ---
/interface wireguard
add name=wg-crm \\
    listen-port=13231 \\
    private-key="{vpn.cliente_private_key}" \\
    comment="CRM Tomich VPN"

# --- 3. Peer: servidor CRM ---
/interface wireguard peers
add interface=wg-crm \\
    public-key="{server_config.servidor_public_key}" \\
    preshared-key="{vpn.preshared_key}" \\
    endpoint-address={server_config.servidor_endpoint} \\
    endpoint-port={server_config.servidor_porta} \\
    allowed-address=10.200.0.0/24 \\
    persistent-keepalive=25s \\
    comment="CRM Server"

# --- 4. IP na interface VPN ---
/ip address
add address={vpn.vpn_ip}/24 \\
    interface=wg-crm \\
    comment="CRM-VPN"

# --- 5. Masquerade: permite que o servidor acesse a rede interna ---
/ip firewall nat
add chain=srcnat \\
    src-address=10.200.0.0/24 \\
    action=masquerade \\
    comment="CRM-VPN-masq" \\
    place-before=0

# --- 6. Verificação ---
:log info "CRM VPN configurada — testando ping..."
:delay 5s
/ping {SERVER_VPN_IP} count=3
:log info "Se recebeu resposta acima, VPN esta ativa!"
"""
    return script


def gerar_script_remocao_mikrotik():
    """Script para remover a VPN da MikroTik."""
    return """:do { /interface wireguard remove [find name=wg-crm] } on-error={}
:do { /ip address remove [find comment="CRM-VPN"] } on-error={}
:do { /ip firewall nat remove [find comment="CRM-VPN-masq"] } on-error={}
:log info "CRM VPN removida"
"""
