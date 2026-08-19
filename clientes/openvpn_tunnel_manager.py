"""
OpenVPN Tunnel Manager
Gerencia a PKI própria da CRM (CA + certificados por cliente), uma instância
de servidor OpenVPN DEDICADA por túnel (porta/interface/sub-rede próprias),
e a geração do script/one-liner de bootstrap para o MikroTik.

É o único tipo de VPN da CRM desde 14/08/2026, quando o WireGuard foi
removido por completo (código, modelos e interfaces do servidor).

Arquitetura: cada túnel roda em sua PRÓPRIA instância systemd
(openvpn-server@server-crm-N), com sua própria porta TCP e seu próprio /29.
Isolamento é por processo, não por certificado compartilhado num daemon
único — cada instância só aceita a UMA conexão daquele cliente (via
client-config-dir + ccd-exclusive, com um único arquivo CCD).

IMPORTANTE — o que isso resolve e o que NÃO resolve:
Isso elimina a classe de bug em que apagar/editar o túnel de um cliente
afeta outro. NÃO elimina o problema de dois clientes DIFERENTES terem, ao
mesmo tempo, a MESMA rede "alcançável" declarada (ex: ambos com
172.16.0.0/12 no CGNAT padrão) — isso é uma limitação de roteamento IP por
destino (o kernel só pode mandar um pacote destinado a um IP específico
para UM lugar), não uma limitação de arquitetura de túnel. Daí a checagem
em `redes_em_conflito` e a conferência do `dev` real em `vpn_cobre_ip`.
"""
import ipaddress
import logging
import os
import secrets
import subprocess
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

logger = logging.getLogger(__name__)

# ─── Configuração fixa ────────────────────────────────────────────────────────
OVPN_BASE_PORT      = 1195               # instância N escuta em 1195+N (1194 = legado, descomissionado)
OVPN_SUBNET_POOL    = ipaddress.ip_network('10.91.0.0/16')  # instância N usa o N-ésimo /29 daqui
OVPN_ENDPOINT_HOST  = 'crm.tomich.com.br'

PKI_DIR      = '/etc/openvpn/pki-crm'
SERVER_DIR   = '/etc/openvpn/server'
CCD_BASE_DIR = '/etc/openvpn/ccd-instancias'

CA_KEY   = f'{PKI_DIR}/ca.key'
CA_CRT   = f'{PKI_DIR}/ca.crt'
SRV_KEY  = f'{PKI_DIR}/server.key'
SRV_CRT  = f'{PKI_DIR}/server.crt'
CRL_PATH = f'{PKI_DIR}/crl.pem'

# Redes padrão sugeridas ao criar um túnel — cobrem CGNAT + todo o espaço
# RFC1918 + benchmarking (RFC 2544). Cada instância declara essas rotas
# isoladamente, só pra si mesma.
REDES_PADRAO = [
    '100.64.0.0/10',
    '172.16.0.0/12',
    '10.0.0.0/8',
    '192.168.0.0/16',
    '198.18.0.0/15',
]


# ─── Helpers de rede ──────────────────────────────────────────────────────────

def _cidr_to_route(cidr):
    """'10.0.0.0/8' → ('10.0.0.0', '255.0.0.0') para a diretiva `route`."""
    net = ipaddress.ip_network(cidr, strict=False)
    return str(net.network_address), str(net.netmask)


def rota_dev_para(host):
    """
    Interface que o kernel REALMENTE usa para alcançar `host` ('tun-crm-1',
    'eth0'…), ou None se não der para determinar.

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


def tunel_conectado(vpn):
    """O MikroTik daquele túnel está de fato conectado agora? Confere se o IP
    do cliente no /29 responde."""
    if not vpn.vpn_ip:
        return False
    try:
        r = subprocess.run(['ping', '-c', '1', '-W', '2', str(vpn.vpn_ip)],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception as e:
        logger.debug(f'tunel_conectado({vpn.vpn_ip}) falhou: {e}')
        return False


def dev_tun(vpn):
    """Nome da interface tun da instância deste túnel ('server-crm-3' →
    'tun-crm-3'). É o `dev` que aparece no `ip route get` quando a rota
    realmente aponta para ESTE cliente."""
    return (vpn.interface_nome or '').replace('server-crm-', 'tun-crm-')


def _rotas_kernel():
    """{prefixo: [devs]} das rotas IPv4 já instaladas (fora a default). O mesmo
    prefixo pode aparecer em mais de uma interface — é exatamente o estado que
    esta checagem existe para evitar."""
    rotas = {}
    try:
        r = subprocess.run(['ip', '-4', 'route', 'show'],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return rotas
        for linha in r.stdout.splitlines():
            partes = linha.split()
            if not partes or partes[0] == 'default' or 'dev' not in partes:
                continue
            prefixo = partes[0] if '/' in partes[0] else f'{partes[0]}/32'
            try:
                ipaddress.ip_network(prefixo, strict=False)
            except ValueError:
                continue
            dev = partes[partes.index('dev') + 1]
            devs = rotas.setdefault(prefixo, [])
            if dev not in devs:
                devs.append(dev)
    except Exception as e:
        logger.debug(f'_rotas_kernel falhou: {e}')
    return rotas


def redes_em_conflito(redes, excluir_vpn_id=None):
    """
    Retorna [(rede_pedida, rede_do_outro, rótulo_do_outro), ...] para toda
    rede IDÊNTICA a uma já declarada por OUTRO túnel OpenVPN ativo, ou já
    presente na tabela de rotas do kernel por outra interface.

    Por que isso é obrigatório: a rota vive no kernel, que é único e roteia por
    DESTINO. Duas instâncias declarando 10.0.0.0/8 instalam duas rotas para o
    mesmo prefixo — só uma é usada, e o tráfego destinado ao equipamento do
    cliente A entra no roteador do cliente B (que ainda mascara a origem e
    tenta entregar em um IP igual na rede dele). Foi exatamente o que
    aconteceu em produção: os dois túneis ativos declaravam as 5 faixas
    padrão, o 198.18.10.2 da TOPNET saía pelo tun da INFORTECLINE e nenhum
    dos dois funcionava.

    Prefixos de tamanhos diferentes (um /24 dentro do /8 de outro cliente) NÃO
    entram aqui: o kernel casa o mais específico primeiro, então o resultado é
    determinístico e é justamente o que se quer ao declarar a rede exata do
    cliente. O que sobra dessa sobreposição — o outro cliente perder aquela
    fatia — é a limitação de espaço de endereço já documentada no topo do
    arquivo, e é coberta em tempo de conexão pelo `dev` real da rota
    (`vpn_cobre_ip`, views.py).
    """
    from .models import VPNOpenVPN

    alvos = []
    for rede in redes:
        try:
            alvos.append((rede, ipaddress.ip_network(rede, strict=False)))
        except ValueError:
            continue

    existentes = []
    ovpn_qs = VPNOpenVPN.objects.filter(ativo=True).exclude(id=excluir_vpn_id)
    for outro in ovpn_qs.select_related('cliente'):
        rotulo = f'túnel OpenVPN "{outro.nome}" ({outro.cliente.nome_empresa})'
        for r in outro.redes_lista():
            existentes.append((r, rotulo))
    # Rotas que já estão no kernel mas não saem de nenhum registro do banco —
    # rota posta na mão, resto de configuração antiga etc. Foi assim que
    # 198.18.1.0/24 (Conecta ISP) passou batido pela checagem só-de-banco.
    dev_proprio = ''
    if excluir_vpn_id:
        proprio = VPNOpenVPN.objects.filter(id=excluir_vpn_id).first()
        dev_proprio = dev_tun(proprio) if proprio else ''
    for prefixo, devs in _rotas_kernel().items():
        for dev in devs:
            if dev and dev != dev_proprio:
                existentes.append((prefixo, f'rota já existente no kernel via {dev}'))

    conflitos = []
    for rede_str, alvo in alvos:
        for outra_str, rotulo in existentes:
            try:
                outra = ipaddress.ip_network(outra_str, strict=False)
            except ValueError:
                continue
            if alvo == outra:
                conflitos.append((rede_str, outra_str, rotulo))
                break
    return conflitos


# ─── PKI (pura em Python — sem depender de easy-rsa/openssl CLI) ────────────

def _nome(cn, org='CRM Tunnel CA'):
    return x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
    ])


def gerar_ca():
    """Gera a CA raiz (uma vez só, na instalação). Retorna (key_pem, crt_pem)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = _nome('CRM Tunnel Root CA')
    agora = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - timedelta(days=1))
        .not_valid_after(agora + timedelta(days=365 * 20))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    crt_pem = cert.public_bytes(serialization.Encoding.PEM)
    return key_pem, crt_pem


def _carregar_ca():
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    with open(CA_KEY, 'rb') as f:
        ca_key = load_pem_private_key(f.read(), password=None)
    with open(CA_CRT, 'rb') as f:
        ca_cert = x509.load_pem_x509_certificate(f.read())
    return ca_key, ca_cert


def _emitir_certificado(cn, eku, dias=3650):
    """Emite um certificado (server ou client) assinado pela CA local."""
    ca_key, ca_cert = _carregar_ca()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    agora = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_nome(cn))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - timedelta(days=1))
        .not_valid_after(agora + timedelta(days=dias))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    crt_pem = cert.public_bytes(serialization.Encoding.PEM)
    return key_pem, crt_pem, cert.serial_number


def emitir_certificado_servidor():
    return _emitir_certificado(OVPN_ENDPOINT_HOST, ExtendedKeyUsageOID.SERVER_AUTH, dias=3650)


def emitir_certificado_cliente(common_name):
    """Emite (e grava em disco) o certificado de um túnel de cliente."""
    key_pem, crt_pem, serial = _emitir_certificado(common_name, ExtendedKeyUsageOID.CLIENT_AUTH, dias=3650)
    os.makedirs(f'{PKI_DIR}/clients', exist_ok=True)
    key_path = f'{PKI_DIR}/clients/{common_name}.key'
    crt_path = f'{PKI_DIR}/clients/{common_name}.crt'
    with open(key_path, 'wb') as f:
        f.write(key_pem)
    os.chmod(key_path, 0o600)
    with open(crt_path, 'wb') as f:
        f.write(crt_pem)
    _registrar_emitido(common_name, serial)
    return key_pem.decode(), crt_pem.decode()


def ler_certificado_cliente(common_name):
    key_path = f'{PKI_DIR}/clients/{common_name}.key'
    crt_path = f'{PKI_DIR}/clients/{common_name}.crt'
    if not (os.path.exists(key_path) and os.path.exists(crt_path)):
        return None, None
    with open(key_path) as f:
        key_pem = f.read()
    with open(crt_path) as f:
        crt_pem = f.read()
    return key_pem, crt_pem


# ─── Registro de seriais emitidos (para poder revogar/gerar CRL) ────────────

def _registrar_emitido(common_name, serial):
    os.makedirs(PKI_DIR, exist_ok=True)
    with open(f'{PKI_DIR}/emitidos.tsv', 'a') as f:
        f.write(f'{common_name}\t{serial}\t{datetime.now(timezone.utc).isoformat()}\n')


def gerar_crl_vazia():
    """CRL inicial sem nenhuma revogação — necessária para crl-verify funcionar
    desde o primeiro boot do daemon."""
    ca_key, ca_cert = _carregar_ca()
    agora = datetime.now(timezone.utc)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(agora)
        .next_update(agora + timedelta(days=3650))
    )
    crl = builder.sign(private_key=ca_key, algorithm=hashes.SHA256())
    with open(CRL_PATH, 'wb') as f:
        f.write(crl.public_bytes(serialization.Encoding.PEM))


def revogar_certificado(common_name):
    """Revoga o certificado de um cliente (recria a CRL com o serial incluído).
    Defesa em profundidade — a instância dedicada já sendo removida basta
    pra bloquear o acesso, mas revogar impede reuso do mesmo cert alhures."""
    serial = None
    tsv = f'{PKI_DIR}/emitidos.tsv'
    if os.path.exists(tsv):
        with open(tsv) as f:
            for line in f:
                parts = line.strip().split('\t')
                if len(parts) >= 2 and parts[0] == common_name:
                    serial = int(parts[1])
    if serial is None:
        logger.warning(f'revogar_certificado: serial não encontrado para {common_name}')
        return

    revogados_path = f'{PKI_DIR}/revogados.txt'
    revogados = set()
    if os.path.exists(revogados_path):
        with open(revogados_path) as f:
            revogados = {int(l.strip()) for l in f if l.strip()}
    revogados.add(serial)
    with open(revogados_path, 'w') as f:
        f.write('\n'.join(str(s) for s in revogados) + '\n')

    ca_key, ca_cert = _carregar_ca()
    agora = datetime.now(timezone.utc)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(agora)
        .next_update(agora + timedelta(days=3650))
    )
    for s in revogados:
        rev = (
            x509.RevokedCertificateBuilder()
            .serial_number(s)
            .revocation_date(agora)
            .build()
        )
        builder = builder.add_revoked_certificate(rev)
    crl = builder.sign(private_key=ca_key, algorithm=hashes.SHA256())
    with open(CRL_PATH, 'wb') as f:
        f.write(crl.public_bytes(serialization.Encoding.PEM))

    for ext in ('key', 'crt'):
        path = f'{PKI_DIR}/clients/{common_name}.{ext}'
        if os.path.exists(path):
            os.remove(path)

    logger.info(f'🗑️ Certificado revogado: {common_name} (serial {serial})')


def sugerir_redes(cliente):
    """
    Sugere as sub-redes /24 dos equipamentos privados JÁ cadastrados nos
    Acessos do cliente. É o que o modal de criação pré-preenche no lugar das
    faixas amplas: faixa ampla é cômoda mas colide com o outro cliente que
    também declarou 10.0.0.0/8, e aí nenhum dos dois funciona (ver
    `redes_em_conflito`). O operador pode ajustar/incluir outras redes.
    """
    from .models import Acesso
    redes = []
    for host in Acesso.objects.filter(cliente=cliente).values_list('host', flat=True):
        ip_raw = (host or '').strip().split(':')[0]
        try:
            ip = ipaddress.ip_address(ip_raw)
        except ValueError:
            continue
        if ip.version != 4 or not ip.is_private:
            continue
        rede = str(ipaddress.ip_network(f'{ip}/24', strict=False))
        if rede not in redes:
            redes.append(rede)
    return sorted(redes)


def gerar_common_name(cliente):
    import re
    slug = re.sub(r'[^a-z0-9]+', '-', (cliente.nome_empresa or 'cliente').lower()).strip('-')[:30]
    sufixo = secrets.token_hex(3)
    return f'c{cliente.id}-{slug}-{sufixo}'


# ─── Alocação de instância dedicada (porta + interface + /30) ───────────────

def alocar_proxima_instancia():
    """
    Acha o próximo N livre (checando o banco e o que sobrou em disco) e
    retorna (interface_nome, porta, subnet_n).
    """
    from .models import VPNOpenVPN
    usados_db = set(
        VPNOpenVPN.objects.exclude(subnet_n=None).values_list('subnet_n', flat=True)
    )
    n = 1
    while True:
        interface_nome = f'server-crm-{n}'
        # Além do banco, respeita sobra em disco: se uma remoção anterior
        # falhou pela metade, o .conf/ccd do N antigo ainda está lá e reusar
        # esse N faria o novo túnel herdar a config (e o CN) do túnel morto.
        if n not in usados_db and not os.path.exists(_conf_path(interface_nome)) \
                and not os.path.isdir(_ccd_dir(interface_nome)):
            return interface_nome, OVPN_BASE_PORT + n, n
        n += 1


def _subnet_for(subnet_n):
    """N-ésimo bloco /29 dentro do pool — RouterOS/OpenVPN exigem pelo menos
    /29 para `server` com `dev tun` + `topology subnet` (um /30 é rejeitado:
    'must define a subnet of 255.255.255.248 (/29) or lower')."""
    subnets = list(OVPN_SUBNET_POOL.subnets(new_prefix=29))
    return subnets[subnet_n]


def _server_ip(subnet_n):
    return str(_subnet_for(subnet_n).network_address + 1)


def _client_ip(subnet_n):
    return str(_subnet_for(subnet_n).network_address + 2)


def _conf_path(interface_nome):
    return f'{SERVER_DIR}/{interface_nome}.conf'


def _ccd_dir(interface_nome):
    return f'{CCD_BASE_DIR}/{interface_nome}'


def _gerar_conteudo_conf(vpn):
    """Gera o conteúdo do arquivo de config da instância dedicada deste túnel."""
    linhas_rotas = []
    for rede in vpn.redes_lista():
        try:
            net, mask = _cidr_to_route(rede)
        except ValueError:
            continue
        linhas_rotas.append(f'route {net} {mask}')
    rotas_str = '\n'.join(linhas_rotas)

    tun_dev = vpn.interface_nome.replace('server-crm-', 'tun-crm-')
    return f"""port {vpn.porta}
proto tcp-server
dev {tun_dev}
dev-type tun

ca {CA_CRT}
cert {SRV_CRT}
key {SRV_KEY}
dh none
crl-verify {CRL_PATH}

client-config-dir {_ccd_dir(vpn.interface_nome)}
ccd-exclusive

topology subnet
server {_subnet_for(vpn.subnet_n).network_address} 255.255.255.248

{rotas_str}

keepalive 10 60
persist-key
persist-tun

cipher AES-256-CBC
data-ciphers AES-256-GCM:AES-256-CBC
data-ciphers-fallback AES-256-CBC
auth SHA1

user nobody
group nogroup

verb 3
"""


def _gerar_conteudo_ccd(vpn):
    """
    Arquivo client-config-dir do cliente deste túnel.

    O `iroute` é OBRIGATÓRIO: em modo `--server` o OpenVPN mantém uma tabela
    de roteamento INTERNA própria. A diretiva `route` do .conf só faz o kernel
    entregar o pacote na tun; sem um `iroute` casando com o destino, o próprio
    OpenVPN descarta o pacote em silêncio (não existe rota interna para
    nenhum cliente conectado). Sem essas linhas o túnel sobe, faz handshake e
    responde ping no IP do /29 — mas NADA da rede interna do cliente é
    alcançável, que era o sintoma em produção.
    """
    linhas = [
        '# instância dedicada — iroute obrigatório para o OpenVPN saber que',
        '# estas redes ficam ATRÁS deste cliente (a `route` do .conf só cuida',
        '# do lado kernel; sem iroute o pacote é descartado internamente).',
    ]
    for rede in vpn.redes_lista():
        try:
            net, mask = _cidr_to_route(rede)
        except ValueError:
            continue
        linhas.append(f'iroute {net} {mask}')
    return '\n'.join(linhas) + '\n'


def _escrever_arquivos_instancia(vpn):
    """Grava .conf + client-config-dir da instância (usado ao criar e ao editar)."""
    os.makedirs(SERVER_DIR, exist_ok=True)
    ccd_dir = _ccd_dir(vpn.interface_nome)
    os.makedirs(ccd_dir, exist_ok=True)
    # ccd-exclusive: só quem tem arquivo aqui pode conectar — como só existe
    # UM arquivo (o do próprio cliente do túnel), a instância vira de-facto
    # single-tenant mesmo usando uma CA compartilhada entre todos os túneis.
    # Arquivos de CNs antigos (túnel reemitido) são removidos para o
    # ccd-exclusive continuar valendo de fato.
    for antigo in os.listdir(ccd_dir):
        if antigo != vpn.common_name:
            try:
                os.remove(os.path.join(ccd_dir, antigo))
            except OSError:
                pass
    with open(f'{ccd_dir}/{vpn.common_name}', 'w') as f:
        f.write(_gerar_conteudo_ccd(vpn))

    with open(_conf_path(vpn.interface_nome), 'w') as f:
        f.write(_gerar_conteudo_conf(vpn))


def criar_instancia_servidor(vpn):
    """Cria e sobe a instância dedicada deste túnel (config + ccd + systemd)."""
    _escrever_arquivos_instancia(vpn)

    r = subprocess.run(
        ['sudo', '/usr/bin/systemctl', 'enable', '--now', f'openvpn-server@{vpn.interface_nome}'],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        # Sem esta limpeza a unit fica habilitada e em Restart=on-failure para
        # sempre: foi assim que openvpn-server@server-crm-999 acumulou 558 mil
        # reinícios no servidor, tentando abrir um .conf que nunca existiu.
        _desabilitar_unit(vpn.interface_nome)
        raise Exception(f'Falha ao subir openvpn-server@{vpn.interface_nome}: {r.stderr.strip()}')
    logger.info(f'✅ Instância {vpn.interface_nome} criada (porta {vpn.porta}, {_subnet_for(vpn.subnet_n)})')


def atualizar_redes_instancia(vpn):
    """Reescreve as rotas da instância (kernel via `route` E interna via
    `iroute` no ccd) e reinicia SÓ ela (reconexão breve apenas para este
    cliente — zero impacto nos demais túneis, que rodam em instâncias/
    processos totalmente separados)."""
    _escrever_arquivos_instancia(vpn)
    r = subprocess.run(
        ['sudo', '/usr/bin/systemctl', 'restart', f'openvpn-server@{vpn.interface_nome}'],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise Exception(f'Falha ao reiniciar openvpn-server@{vpn.interface_nome}: {r.stderr.strip()}')
    logger.info(f'🔄 Instância {vpn.interface_nome} atualizada e reiniciada')


def _desabilitar_unit(interface_nome):
    """Para, desabilita e zera o contador de falhas da unit — o reset-failed é
    o que impede a unit de continuar em loop de Restart depois que o .conf
    dela deixa de existir."""
    for args in (['disable', '--now'], ['reset-failed']):
        subprocess.run(
            ['sudo', '/usr/bin/systemctl', *args, f'openvpn-server@{interface_nome}'],
            capture_output=True, text=True,
        )


def remover_instancia_servidor(vpn):
    """Para e remove por completo a instância dedicada deste túnel."""
    _desabilitar_unit(vpn.interface_nome)
    conf_path = _conf_path(vpn.interface_nome)
    try:
        os.remove(conf_path)
    except OSError:
        pass
    ccd_dir = _ccd_dir(vpn.interface_nome)
    try:
        for f in os.listdir(ccd_dir):
            os.remove(os.path.join(ccd_dir, f))
        os.rmdir(ccd_dir)
    except OSError:
        pass
    logger.info(f'🗑️ Instância {vpn.interface_nome} removida')


# ─── Bootstrap Mikrotik ───────────────────────────────────────────────────────

def gerar_oneliner_bootstrap(token, request=None):
    scheme = 'https'
    host = OVPN_ENDPOINT_HOST
    url = f'{scheme}://{host}/clientes/tunel-ovpn/setup/{token}/get_setup.rsc'
    return (
        ':local ver [/system resource get version] ; :local version '
        '[:pick $ver 0 [:find $ver " "]] ; /tool fetch '
        f'url="{url}\\?v=$version" dst-path="/CRM-ovpn-setup.rsc" ; '
        '/import "/CRM-ovpn-setup.rsc"'
    )


def _parse_versao_ros(ros_version):
    """'7.21.4' → (7, 21); '6.49.10 (long-term)' → (6, 49). Sem versão
    (fetch antigo, sem ?v=) assume 7.6+, que é o RouterOS instalado hoje na
    esmagadora maioria dos clientes."""
    import re
    m = re.match(r'\s*(\d+)(?:\.(\d+))?', ros_version or '')
    if not m:
        return 7, 6
    return int(m.group(1)), int(m.group(2) or 0)


def gerar_setup_rsc(vpn, ros_version=''):
    """Script RouterOS completo, servido no endpoint público /get_setup.rsc.
    Busca os 3 arquivos de certificado (endpoints-irmãos, mesmo token),
    importa, sobe a interface ovpn-client e configura NAT — tudo em um
    import só, sem intervenção manual no roteador.

    IMPORTANTE: cada comando fica em UMA linha só (continuação via "\\" não é
    confiável dentro de /import). "user=" é obrigatório no RouterOS mesmo
    com autenticação só por certificado (senão dá "missing value(s) of
    argument(s) user"). "protocol=tcp" só existe no RouterOS 7+ — no ROS6 é
    implícito e o parâmetro nem existe (por isso lido de ?v=$version).
    "auth=sha1" é a combinação comprovada compatível com ROS6 (validada ao
    vivo — ROS6 não aceita auth=sha256, nem tls-crypt).

    O nome do cipher MUDOU no RouterOS 7.6, quando a MikroTik acrescentou GCM
    ao ovpn-client: até lá era "aes256", de 7.6 em diante é "aes256-cbc"
    (aes256 puro deixou de existir). Mandar o nome errado nem chega a tentar
    conectar — o /import morre com "syntax error" na coluna do cipher, que foi
    o que aconteceu ao configurar o túnel da Conecta ISP num RouterOS 7.21.4.
    O servidor aceita os dois lados (`cipher AES-256-CBC` +
    `data-ciphers AES-256-GCM:AES-256-CBC`), então basta escolher o nome que
    aquela versão entende.

    NAT (in-interface=ovpn-crm → masquerade): o CRM alcança a rede interna
    do cliente através do túnel, mas o roteador do CLIENTE geralmente não
    tem (nem deveria precisar ter) uma rota de volta para a sub-rede do
    túnel — isso exigiria mexer no roteamento dinâmico (OSPF/BGP) ao vivo
    do cliente, arriscado e fora do nosso controle. Mascarando a origem
    como o próprio roteador do cliente (masquerade, não um IP fixo), a
    resposta do equipamento interno volta para um IP que a rede do cliente
    já sabe rotear (o próprio roteador), sem tocar em nada além deste
    roteador."""
    ros_major, ros_minor = _parse_versao_ros(ros_version)
    protocolo_param = ' protocol=tcp' if ros_major >= 7 else ''
    cipher = 'aes256-cbc' if (ros_major, ros_minor) >= (7, 6) else 'aes256'

    base = f'https://{OVPN_ENDPOINT_HOST}/clientes/tunel-ovpn/setup/{vpn.token}'
    return f"""# =============================================================
# Tunel OpenVPN — CRM Tomich
# Cliente : {vpn.cliente.nome_empresa}
# Common Name : {vpn.common_name}
# RouterOS detectado : {ros_version or '(desconhecido, assumindo v7+)'}
# =============================================================

:do {{ /interface ovpn-client remove [find name=ovpn-crm] }} on-error={{}}
:do {{ /certificate remove [find name~"crm-ovpn-"] }} on-error={{}}
:do {{ /ip firewall nat remove [find comment="CRM-OVPN-masq"] }} on-error={{}}

/tool fetch url="{base}/ca.crt" dst-path="crm-ovpn-ca.crt" mode=https
/tool fetch url="{base}/client.crt" dst-path="crm-ovpn-client.crt" mode=https
/tool fetch url="{base}/client.key" dst-path="crm-ovpn-client.key" mode=https

/certificate import file-name=crm-ovpn-ca.crt passphrase="" name=crm-ovpn-ca
/certificate import file-name=crm-ovpn-client.crt passphrase="" name=crm-ovpn-client
/certificate import file-name=crm-ovpn-client.key passphrase="" name=crm-ovpn-client

/interface ovpn-client add name=ovpn-crm connect-to={OVPN_ENDPOINT_HOST} port={vpn.porta}{protocolo_param} mode=ip cipher={cipher} auth=sha1 user={vpn.common_name} certificate=[/certificate find where name~"crm-ovpn-client" and private-key=yes] verify-server-certificate=no add-default-route=no comment="CRM Tomich VPN"

/ip firewall nat add chain=srcnat src-address={_subnet_for(vpn.subnet_n)} action=masquerade comment="CRM-OVPN-masq"

:put "Import concluido. Aguardando o tunel subir..."
:delay 3s
:local tentativas 0
:local conectou false
:while ($tentativas < 5) do={{
    :if ([/interface ovpn-client get [find name=ovpn-crm] running] = true) do={{
        :set conectou true
        :set tentativas 5
    }} else={{
        :set tentativas ($tentativas + 1)
        :delay 2s
    }}
}}
:if ($conectou = true) do={{
    :put "OK - Interface ovpn-crm: RUNNING - handshake ok, tunel estabelecido."
    :log info "Tunel OpenVPN CRM: RUNNING"
    :put "Testando comunicacao com o servidor..."
    /ping {_server_ip(vpn.subnet_n)} count=3
}} else={{
    :put "FALHOU - Interface ovpn-crm NAO subiu (running=no) apos 10s de espera."
    :put "Verifique: /interface ovpn-client print detail  e  /log print where topics~\\"ovpn\\""
    :log warning "Tunel OpenVPN CRM: interface nao ficou running apos import"
}}
"""
