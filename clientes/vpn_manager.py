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

# ─── Interfaces isoladas por cliente (wg5+) ──────────────────────────────────
# Cada cliente novo ganha sua própria interface (porta e /30 dedicados), nunca
# compartilhando rotas no kernel com outros clientes — elimina a classe de bug
# em que remover/criar a VPN de UM cliente afeta as rotas de OUTRO (ver
# incidente Conecta ISP, 2026-06-14: remoção de VPN do cliente 41 apagou
# rotas compartilhadas em wg0 das quais o Conecta ISP ainda dependia).
ISOLATED_BASE_PORT   = 51820  # wgN escuta em 51820+N
ISOLATED_SUBNET_BASE = 200    # wgN usa 10.{200+N}.0.0/30 (servidor=.1, cliente=.2)


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


def rota_dev_para(host):
    """
    Interface que o kernel REALMENTE usa para alcançar `host` ('wg2',
    'tun-crm-1', 'eth0'…), ou None se não der para determinar.

    Necessário porque declarar uma rede em `redes_privadas` não garante que a
    rota do kernel aponte para aquele túnel: quando dois clientes declaram a
    mesma faixa ampla (10.0.0.0/8 etc.), o kernel roteia por destino e só uma
    das rotas vale — a outra vira uma promessa falsa que joga o tráfego no
    túnel do cliente errado.
    """
    try:
        r = subprocess.run(['ip', 'route', 'get', str(host)],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        partes = r.stdout.split()
        if 'dev' in partes:
            return partes[partes.index('dev') + 1]
    except Exception as e:
        logger.debug(f'rota_dev_para({host}) falhou: {e}')
    return None


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


def _outro_peer_usa_rede(rede, excluir_public_key):
    """
    Verifica se outro VPNWireGuard ativo (qualquer cliente, exceto o próprio
    peer em questão) ainda declara essa MESMA rede em redes_privadas.

    Usado para nunca remover do kernel uma rota da qual outro cliente ainda
    depende — essa checagem ausente foi a causa do incidente em que remover
    a VPN do cliente 41 (Sartor Internet) derrubou o acesso interno do
    Conecta ISP em 2026-06-14 (ambos compartilhavam a rota 10.0.0.0/8 em wg0).
    """
    from .models import VPNWireGuard
    try:
        alvo = ipaddress.ip_network(rede, strict=False)
    except ValueError:
        return False
    outros = VPNWireGuard.objects.filter(ativo=True, peer_no_servidor=True).exclude(
        cliente_public_key=excluir_public_key
    )
    for v in outros:
        for r in v.redes_lista():
            try:
                if ipaddress.ip_network(r, strict=False) == alvo:
                    return True
            except ValueError:
                continue
    return False


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
    """Remove um peer do wg0. Preserva rotas que outros clientes ainda usam."""
    r = subprocess.run(['sudo', 'wg', 'set', WG_INTERFACE, 'peer', public_key, 'remove'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        logger.warning(f'wg remove peer: {r.stderr}')

    if redes_privadas:
        for rede in redes_privadas:
            rede = rede.strip()
            if not rede:
                continue
            if _outro_peer_usa_rede(rede, public_key):
                logger.info(f'🛡️ Rota {rede} preservada em {WG_INTERFACE} — ainda usada por outro cliente')
                continue
            _ip('route', 'del', rede, 'dev', WG_INTERFACE)

    logger.info(f'✅ Peer removido: {public_key[:16]}…')


def salvar_config_persistente(interface=WG_INTERFACE, address=None):
    """Salva o estado atual de uma interface WireGuard em /etc/wireguard/<iface>.conf."""
    if address is None and interface == WG_INTERFACE:
        address = f'{SERVER_VPN_IP}/24'
    os.makedirs('/etc/wireguard', exist_ok=True)
    r = subprocess.run(['sudo', 'wg', 'showconf', interface], capture_output=True, text=True)
    if r.returncode == 0:
        conf = r.stdout
        # wg showconf não inclui Address — garantir que fique no [Interface]
        if address and '[Interface]' in conf and f'Address = {address}' not in conf:
            conf = conf.replace(
                '[Interface]\n',
                f'[Interface]\nAddress = {address}\n',
                1
            )
        proc = subprocess.run(
            ['sudo', 'tee', f'/etc/wireguard/{interface}.conf'],
            input=conf, capture_output=True, text=True
        )
        subprocess.run(['sudo', 'chmod', '600', f'/etc/wireguard/{interface}.conf'])


# ─── Interfaces isoladas: ciclo de vida completo ─────────────────────────────

def alocar_proxima_interface():
    """
    Acha a próxima interface livre (wg5, wg6, ...) checando tanto o banco
    (VPNWireGuard.interface_nome) quanto o que já existe no kernel — nunca
    colide com wg0 (legado) nem com wg1-wg4 (isoladas já existentes,
    configuradas manualmente). Retorna (nome, porta, subnet_n).
    """
    from .models import VPNWireGuard
    usados_db = set(
        VPNWireGuard.objects.exclude(interface_nome='').values_list('interface_nome', flat=True)
    )
    n = 1
    while True:
        nome = f'wg{n}'
        existe_kernel = subprocess.run(['ip', 'link', 'show', nome], capture_output=True).returncode == 0
        if nome != WG_INTERFACE and nome not in usados_db and not existe_kernel:
            porta    = ISOLATED_BASE_PORT + n
            subnet_n = ISOLATED_SUBNET_BASE + n
            return nome, porta, subnet_n
        n += 1


def criar_interface_isolada(nome, porta, subnet_n, server_private_key):
    """
    Cria uma interface WireGuard isolada via wg-quick, com seu próprio /30
    ponto-a-ponto e porta dedicada — mesmo padrão já usado manualmente em
    wg1-wg4. O servidor reutiliza a mesma chave privada nas interfaces.
    """
    servidor_ip_local = f'10.{subnet_n}.0.1'

    if subprocess.run(['ip', 'link', 'show', nome], capture_output=True).returncode == 0:
        logger.info(f'Interface {nome} já existe — pulando criação')
        return servidor_ip_local

    conf_path = f'/etc/wireguard/{nome}.conf'
    conf_content = (
        f'[Interface]\n'
        f'Address = {servidor_ip_local}/30\n'
        f'ListenPort = {porta}\n'
        f'PrivateKey = {server_private_key}\n'
    )

    r = subprocess.run(['sudo', '/usr/bin/tee', conf_path], input=conf_content,
                        capture_output=True, text=True)
    if r.returncode != 0:
        raise Exception(f'Não foi possível gravar {conf_path}: {r.stderr.strip()}')
    subprocess.run(['sudo', 'chmod', '600', conf_path])

    r2 = subprocess.run(['sudo', '/usr/bin/wg-quick', 'up', nome], capture_output=True, text=True)
    if r2.returncode != 0:
        raise Exception(f'wg-quick up {nome} falhou: {r2.stderr.strip()}')

    # Best-effort: habilitar no systemd para sobreviver a reboot. www-data não
    # tem sudo para systemctl hoje — se falhar, a interface funciona mesmo
    # assim, só não volta automaticamente após um reboot (requer
    # `sudo systemctl enable wg-quick@{nome}` manual uma vez).
    r3 = subprocess.run(['sudo', 'systemctl', 'enable', f'wg-quick@{nome}'],
                         capture_output=True, text=True)
    if r3.returncode != 0:
        logger.warning(
            f'Não foi possível habilitar wg-quick@{nome} no boot (sudoers não libera systemctl). '
            f'Rode manualmente: sudo systemctl enable wg-quick@{nome}'
        )

    logger.info(f'✅ Interface isolada {nome} criada (porta {porta}, {servidor_ip_local}/30)')
    return servidor_ip_local


def adicionar_peer_isolado(interface, public_key, preshared_key, vpn_ip_cliente, redes_privadas=None):
    """
    Adiciona o peer do cliente na sua própria interface isolada. Como cada
    cliente tem uma interface dedicada, as redes podem ser tão amplas quanto
    necessário (10.0.0.0/8 etc.) sem qualquer risco de colidir com a rota de
    outro cliente — o problema do wg0 compartilhado não existe aqui.
    """
    allowed = [f'{vpn_ip_cliente}/32']
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

    conf_file = f'/tmp/wg_peer_{interface}.conf'
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
            ['sudo', '/usr/bin/wg', 'addconf', interface, conf_file],
            capture_output=True, text=True,
            env={**os.environ, 'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'},
        )
        if r.returncode != 0:
            raise Exception(f'wg addconf peer falhou ({interface}): {r.stderr.strip() or r.stdout.strip()}')
    finally:
        if os.path.exists(conf_file):
            os.unlink(conf_file)

    if redes_privadas:
        for rede in redes_privadas:
            rede = rede.strip()
            if not rede:
                continue
            res = _ip('route', 'add', rede, 'dev', interface)
            if res.returncode != 0 and 'File exists' in res.stderr:
                logger.warning(
                    f'⚠️ Rota {rede} já pertence a OUTRA interface (provavelmente outro '
                    f'cliente usa a mesma faixa ampla). Tráfego para {rede} continuará indo '
                    f'para a interface antiga, não para {interface}. Recomenda-se declarar '
                    f'sub-redes específicas do cliente em vez de faixas genéricas (10.0.0.0/8 etc).'
                )

    servidor_ip_local = f'10.{interface_subnet_n(interface)}.0.1'
    salvar_config_persistente(interface=interface, address=f'{servidor_ip_local}/30')

    logger.info(f'✅ Peer isolado adicionado em {interface}: {public_key[:16]}… → {allowed_str}')


def interface_subnet_n(interface):
    """Extrai o N de 'wgN' para reconstruir a subnet 10.{200+N}.0.0/30."""
    n = int(interface.replace('wg', ''))
    return ISOLATED_SUBNET_BASE + n


def remover_interface_isolada(interface):
    """Derruba e remove uma interface isolada por completo."""
    r = subprocess.run(['sudo', '/usr/bin/wg-quick', 'down', interface], capture_output=True, text=True)
    if r.returncode != 0:
        logger.warning(f'wg-quick down {interface}: {r.stderr.strip()}')

    conf_path = f'/etc/wireguard/{interface}.conf'
    try:
        os.remove(conf_path)
    except OSError as e:
        logger.warning(f'Não foi possível remover {conf_path} (limpeza manual pode ser necessária): {e}')

    r2 = subprocess.run(['sudo', 'systemctl', 'disable', f'wg-quick@{interface}'],
                         capture_output=True, text=True)
    if r2.returncode != 0:
        logger.warning(f'Não foi possível desabilitar wg-quick@{interface} no boot: {r2.stderr.strip()}')

    logger.info(f'🗑️ Interface isolada {interface} removida')


def vpn_e_isolada(vpn_ip):
    """True se o IP da VPN está fora da subnet legada do wg0 (10.200.0.0/24)."""
    try:
        return ipaddress.ip_address(vpn_ip) not in ipaddress.ip_network(VPN_SUBNET)
    except ValueError:
        return False


# ─── Status ──────────────────────────────────────────────────────────────────

def get_peers_status():
    """
    Retorna dict {public_key: {endpoint, latest_handshake, transfer_rx, transfer_tx, allowed_ips}}
    Varre wg0 (legado) e qualquer interface isolada wgN existente, já que cada
    cliente novo passa a ter sua própria interface.
    """
    result = {}
    r_all = subprocess.run(['sudo', 'wg', 'show', 'interfaces'], capture_output=True, text=True)
    interfaces = r_all.stdout.split() if r_all.returncode == 0 else [WG_INTERFACE]

    for iface in interfaces:
        r = _wg('show', iface, 'dump')
        if r.returncode != 0:
            continue
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

    Para VPNs em interface isolada (criadas a partir da correção de
    2026-06-16), usa a porta/sub-rede dedicada da própria interface em vez
    do wg0 compartilhado — cada cliente fala com sua própria porta no
    servidor, eliminando colisão de rotas entre clientes.
    """
    from datetime import datetime
    agora = datetime.now().strftime('%d/%m/%Y %H:%M')

    redes = [r.strip() for r in vpn.redes_privadas.splitlines() if r.strip()]
    redes_comentario = ', '.join(redes) if redes else 'nenhuma configurada'

    isolada = vpn_e_isolada(vpn.vpn_ip)
    if isolada:
        n              = interface_subnet_n(vpn.interface_nome) - ISOLATED_SUBNET_BASE
        porta_endpoint = ISOLATED_BASE_PORT + n
        servidor_ip    = vpn.servidor_ip_local or f'10.{ISOLATED_SUBNET_BASE + n}.0.1'
        subnet_cidr    = f'10.{ISOLATED_SUBNET_BASE + n}.0.0/30'
        vpn_ip_mask    = f'{vpn.vpn_ip}/30'
    else:
        porta_endpoint = server_config.servidor_porta
        servidor_ip    = SERVER_VPN_IP
        subnet_cidr    = VPN_SUBNET
        vpn_ip_mask    = f'{vpn.vpn_ip}/24'

    script = f"""# =============================================================
# VPN WireGuard — CRM Tomich
# Cliente : {vpn.cliente.nome_empresa}
# Gerado  : {agora}
# VPN IP  : {vpn_ip_mask}
# Redes   : {redes_comentario}
# =============================================================
# INSTRUÇÕES:
#   1. Abra o Winbox ou SSH na MikroTik
#   2. New Terminal → cole este script → Enter
#   3. Aguarde ~30s e teste: /ping {servidor_ip}
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
    endpoint-port={porta_endpoint} \\
    allowed-address={subnet_cidr} \\
    persistent-keepalive=25s \\
    comment="CRM Server"

# --- 4. IP na interface VPN ---
/ip address
add address={vpn_ip_mask} \\
    interface=wg-crm \\
    comment="CRM-VPN"

# --- 5. Masquerade: permite que o servidor acesse a rede interna ---
/ip firewall nat
add chain=srcnat \\
    src-address={subnet_cidr} \\
    action=masquerade \\
    comment="CRM-VPN-masq" \\
    place-before=0

# --- 6. Verificação ---
:log info "CRM VPN configurada — testando ping..."
:delay 5s
/ping {servidor_ip} count=3
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
