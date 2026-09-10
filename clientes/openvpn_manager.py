"""
openvpn_manager.py — Criação automatizada de OpenVPN Server em MikroTik
Suporta RouterOS v6 e v7.
"""
import io
import ipaddress
import os
import re
import time
import secrets
import string
import logging
from collections import Counter

import paramiko

from django.conf import settings

logger = logging.getLogger(__name__)

# Nomes que a plataforma cria na RB — servem também para reconhecer o que é
# dela (e o que é do cliente) ao reconfigurar.
PROFILE_PLATAFORMA = 'OPEN_VPN'
CERT_SERVIDOR      = 'Servidor-OPEN'

# Serviços PPP que são VPN de acesso remoto (pppoe/any ficam de fora: num
# concentrador os profiles deles são os planos dos assinantes).
VPN_SERVICOS = ('l2tp', 'pptp', 'sstp', 'ovpn')


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def gerar_senha(tamanho=16):
    """Gera senha aleatória segura."""
    alfabeto = string.ascii_letters + string.digits + '-_'
    return ''.join(secrets.choice(alfabeto) for _ in range(tamanho))


def _pool_redes(pool_ranges):
    """Blocos CIDR exatos de um pool no formato do RouterOS: 'a-b', vários
    separados por vírgula, CIDR ou IP solto."""
    redes = []
    for parte in (pool_ranges or '').split(','):
        parte = parte.strip()
        if not parte:
            continue
        if '-' in parte:
            ini, fim = (ipaddress.ip_address(p.strip()) for p in parte.split('-', 1))
            redes.extend(ipaddress.summarize_address_range(ini, fim))
        else:
            redes.append(ipaddress.ip_network(parte, strict=False))
    return redes


def _cobertura(redes):
    """Menor prefixo único que contém todas as redes."""
    menor = min(r.network_address for r in redes)
    maior = max(r.broadcast_address for r in redes)
    prefixo = menor.max_prefixlen
    while True:
        rede = ipaddress.ip_network(f'{menor}/{prefixo}', strict=False)
        if maior in rede:
            return rede
        prefixo -= 1


def _pool_cidr(pool_ranges):
    """
    src-address da regra NAT_OpenVPN: o menor prefixo que cobre o pool
    inteiro. Antes era sempre "IP inicial + /25" (herança do script de
    referência), que só acerta o pool padrão .128-.254 — num pool
    '.2-.254' o NAT cobria só .0/25 e quem recebia IP de .128 pra cima
    ficava sem NAT.
    """
    return str(_cobertura(_pool_redes(pool_ranges)))


# ─────────────────────────────────────────────────────────────────────────────
# Geração de comandos MikroTik
# ─────────────────────────────────────────────────────────────────────────────

def _cmds_base(cfg):
    """
    Gera a lista de comandos compartilhados entre v6 e v7.
    Marcadores '__DELAY_N__' são tratados como time.sleep(N) no executor.

    `cfg['criar_pool']` False = o usuário entra num profile de VPN que a RB
    já tem (`cfg['ppp_profile']`, achado por `_detectar_profile_vpn`): nada
    de pool, profile ou NAT próprios — é a faixa desse profile que a rede do
    cliente já roteia e trata no NAT.
    """
    nome       = cfg['nome_vpn']
    ip_pub     = cfg['ip_publico']
    porta      = cfg['porta']
    pool       = cfg['vpn_pool']
    local_ip   = cfg['vpn_local_ip']
    username   = cfg['vpn_username']
    password   = cfg['vpn_password']
    passphrase = cfg['cert_passphrase']
    rate_limit = cfg['rate_limit']
    profile    = cfg['ppp_profile']

    cmds = [
        # ── Limpar arquivos exportados anteriores ─────────────────────────
        f'/file remove [find name="CA.crt"]',
        f'/file remove [find name="{nome}.crt"]',
        f'/file remove [find name="{nome}.key"]',
        '__DELAY_1__',

        # ── Limpar certificados anteriores (idempotente) ──────────────────
        f'/certificate remove [find name=CA]',
        f'/certificate remove [find name=Servidor-OPEN]',
        f'/certificate remove [find name={nome}]',
        '__DELAY_1__',

        # ── Criar e assinar CA ────────────────────────────────────────────
        f'/certificate add name=CA common-name=CA key-usage=crl-sign,key-cert-sign',
        f'/certificate sign CA ca-crl-host={ip_pub}',
        '__DELAY_5__',                      # aguarda assinatura CA
        f'/certificate set CA trusted=yes',

        # ── Criar e assinar certificado do servidor ───────────────────────
        f'/certificate add name=Servidor-OPEN common-name=Servidor-OPEN '
        f'key-usage=digital-signature,key-encipherment,tls-server',
        f'/certificate sign Servidor-OPEN ca=CA',
        '__DELAY_5__',                      # aguarda assinatura servidor
        f'/certificate set Servidor-OPEN trusted=yes',

        # ── Criar e assinar certificado do cliente ────────────────────────
        f'/certificate add name={nome} common-name={nome} key-usage=tls-client',
        f'/certificate sign {nome} ca=CA',
        '__DELAY_5__',                      # aguarda assinatura cliente

        # ── Exportar certificados ─────────────────────────────────────────
        # passphrase é necessária para MikroTik exportar a chave privada
        f'/certificate export-certificate CA type=pem file-name=CA',
        '__DELAY_3__',
        f'/certificate export-certificate {nome} type=pem file-name={nome} export-passphrase={passphrase}',
        '__DELAY_3__',                      # aguarda escrita dos arquivos
    ]

    if cfg['criar_pool']:
        cmds += [
            # ── NAT ───────────────────────────────────────────────────────
            f'/ip firewall nat remove [find comment="NAT_OpenVPN"]',
            f'/ip firewall nat add chain=srcnat action=src-nat '
            f'comment="NAT_OpenVPN" '
            f'src-address={_pool_cidr(pool)} to-addresses={ip_pub}',

            # ── Pool de IPs ───────────────────────────────────────────────
            f'/ip pool remove [find name=POOL_OpenVPN]',
            f'/ip pool add name=POOL_OpenVPN ranges={pool}',

            # ── Profile PPP ───────────────────────────────────────────────
            f'/ppp profile remove [find name={PROFILE_PLATAFORMA}]',
            f'/ppp profile add name={PROFILE_PLATAFORMA} local-address={local_ip} '
            f'remote-address=POOL_OpenVPN rate-limit={rate_limit} '
            f'change-tcp-mss=yes use-encryption=yes',
        ]

    return cmds + [
        # ── Usuário VPN ───────────────────────────────────────────────────
        f'/ppp secret remove [find name={username}]',
        f'/ppp secret add name={username} password={password} '
        f'service=ovpn profile="{profile}"',

        # ── Firewall ──────────────────────────────────────────────────────
        f'/ip firewall filter remove [find comment="OPENVPN SERVER"]',
        f'/ip firewall filter add chain=input action=accept protocol=tcp '
        f'dst-port={porta} comment="OPENVPN SERVER"',
    ]


def _gerar_mac():
    """Gera um MAC address aleatório localmente administrado."""
    import random
    mac = [0xFE,
           random.randint(0x00, 0xFF),
           random.randint(0x00, 0xFF),
           random.randint(0x00, 0xFF),
           random.randint(0x00, 0xFF),
           random.randint(0x00, 0xFF)]
    return ':'.join(f'{b:02X}' for b in mac)


def _cmd_ovpn_server_lista(cfg, remover=()):
    """`/interface ovpn-server server` como LISTA (`add`) — suporte a
    múltiplas instâncias, que só existe em builds recentes do v7 (não achamos
    um número de versão fixo confiável; ver `_ovpn_server_suporta_lista`,
    que detecta isso direto no equipamento em vez de assumir pela versão).

    Remove só as instâncias da própria plataforma (`remover`, vindas de
    `_checar_servidor_existente`) e a de mesmo nome — antes era
    `remove [find]`, que apagava junto qualquer servidor OpenVPN do cliente."""
    porta  = cfg['porta']
    nome   = cfg['nome_vpn']
    mac    = _gerar_mac()
    nomes  = list(dict.fromkeys([*remover, nome]))
    return [
        *(f'/interface ovpn-server server remove [find name="{n}"]' for n in nomes),
        f'/interface ovpn-server server add name={nome} port={porta} '
        f'auth=sha1 cipher=aes256-cbc disabled=no mac-address={mac} '
        f'certificate={CERT_SERVIDOR} default-profile="{cfg["ppp_profile"]}" '
        f'require-client-certificate=yes',
    ]


def _cmd_ovpn_server_singleton_v7(cfg):
    """`/interface ovpn-server server` como objeto único (`set`) — é o que a
    MAIORIA dos builds v7 ainda usa (o suporte a múltiplas instâncias é
    recente). Mesma sintaxe do v6, mas com o nome de cipher que o v7 exige:
    `aes256` puro dá "syntax error" a partir de um certo 7.x — mesmo
    problema já visto do lado cliente do túnel OpenVPN (RouterOS renomeou
    pra `aes256-cbc`/`aes256-gcm`)."""
    porta = cfg['porta']
    return [
        f'__PTY__/interface ovpn-server server set enabled=yes port={porta} '
        f'auth=sha1 cipher=aes256-cbc '
        f'certificate={CERT_SERVIDOR} default-profile="{cfg["ppp_profile"]}" '
        f'require-client-certificate=yes',
    ]


def comandos_ros7(cfg):
    """RouterOS v7 — base comum; o comando final (lista vs. objeto único) é
    decidido depois de conectar, em `executar_config_openvpn`, porque
    depende de sondar o equipamento (`_ovpn_server_suporta_lista`)."""
    return _cmds_base(cfg)


def comandos_ros6(cfg):
    """RouterOS v6 — /interface ovpn-server server set."""
    porta  = cfg['porta']
    cmds   = _cmds_base(cfg)
    cmds.append(
        f'__PTY__/interface ovpn-server server set enabled=yes port={porta} '
        f'auth=sha1 cipher=aes256 '
        f'certificate={CERT_SERVIDOR} default-profile="{cfg["ppp_profile"]}" '
        f'require-client-certificate=yes'
    )
    return cmds


# ─────────────────────────────────────────────────────────────────────────────
# Execução SSH
# ─────────────────────────────────────────────────────────────────────────────

def _exec(client, cmd, timeout=60):
    """Executa um comando via exec_command e retorna (stdout, stderr)."""
    try:
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=False)
        out = stdout.read().decode('utf-8', errors='replace').strip()
        err = stderr.read().decode('utf-8', errors='replace').strip()
        return out, err
    except Exception as e:
        return '', str(e)


# RouterOS devolve a recusa pela mesma saída normal do comando (não por um
# canal de erro separado) — por isso o loop de execução precisa varrer o
# texto, e não só confiar em `err`/exceção. Achado real: `/interface
# ovpn-server server add ...` foi gravado como sucesso mesmo respondendo
# "bad command name add" (a lista de instâncias só existe em builds
# recentes do v7 — a maioria ainda usa objeto único).
_ERRO_MIKROTIK_RE = re.compile(
    r'^\s*(?:script error|syntax error|bad command|expected end of command|'
    r'no such item|failure:|input does not match any value)',
    re.IGNORECASE)


def _erro_mikrotik(texto):
    """Primeira linha de erro reconhecida na saída, ou '' se o comando foi
    aceito (inclui um `remove [find ...]` que não achou nada — isso é
    no-op silencioso em menus de lista, não erro)."""
    if not texto:
        return ''
    for linha in texto.splitlines():
        if _ERRO_MIKROTIK_RE.match(linha):
            return linha.strip()
    return ''


def _ovpn_server_suporta_lista(client):
    """`/interface ovpn-server server` virou uma LISTA (suporta `add`,
    múltiplas instâncias) só em builds recentes do v7 — a maioria ainda
    trata como objeto único (só `set`), igual ao v6. Em vez de travar num
    número de versão (que pode mudar), sonda o próprio equipamento: `print
    count-only` só é um comando válido em menus de lista."""
    out, err = _exec(client, '/interface ovpn-server server print count-only', timeout=15)
    return not _erro_mikrotik(out) and not _erro_mikrotik(err)


# ─────────────────────────────────────────────────────────────────────────────
# Checagens prévias — só leitura, rodam antes de qualquer alteração na RB
# ─────────────────────────────────────────────────────────────────────────────

_KV_RE = re.compile(r'([\w-]+)=("[^"]*"|\S*)')


def _ler_ovpn_server(client, usa_lista):
    """Estado atual do(s) servidor(es) OpenVPN da RB:
    [{'nome', 'ativo', 'certificado', 'porta'}]."""
    if usa_lista:
        out, _ = _exec(client, '/interface ovpn-server server print terse', timeout=20)
        instancias = []
        for linha in out.splitlines():
            pares = list(_KV_RE.finditer(linha))
            kv = {m.group(1): m.group(2).strip('"') for m in pares}
            if 'name' not in kv:
                continue
            flags = linha[:pares[0].start()]
            instancias.append({
                'nome':        kv['name'],
                'ativo':       'X' not in flags and kv.get('disabled') != 'yes',
                'certificado': kv.get('certificate', ''),
                'porta':       kv.get('port', ''),
            })
        return instancias

    out, _ = _exec(client, '/interface ovpn-server server print', timeout=20)
    kv = {}
    for linha in out.splitlines():
        chave, sep, valor = linha.partition(':')
        if sep:
            kv[chave.strip()] = valor.strip()
    return [{
        'nome':        '',
        'ativo':       kv.get('enabled') == 'yes',
        'certificado': kv.get('certificate', ''),
        'porta':       kv.get('port', ''),
    }]


def _checar_servidor_existente(instancias, porta, usa_lista):
    """
    Retorna (erro, instâncias_da_plataforma_a_remover).

    No v6 (e na maioria dos v7) só existe UM ovpn-server: o `set` da
    plataforma trocava porta, certificado e profile do servidor que o cliente
    já usava. Achado real (CONECTONLINE, 45.180.36.1): servidor próprio na
    51194 com certificados `ovpn-server`/`ovpn-ca` virou o da plataforma na
    61194. Servidor ativo com certificado que não é o da plataforma = do
    cliente → não mexe. Em modo lista só barra se a porta colidir.
    """
    alheios = [i for i in instancias
               if i['ativo'] and i['certificado'] not in (CERT_SERVIDOR, 'none', '')]
    if not usa_lista:
        if alheios:
            i = alheios[0]
            return (
                f'A RB já tem um servidor OpenVPN próprio ativo (porta {i["porta"]}, '
                f'certificado "{i["certificado"]}"). Esta versão do RouterOS só tem um '
                f'ovpn-server: configurar por cima derrubaria a VPN que o cliente já usa. '
                f'Nada foi alterado na RB.'
            ), []
        return '', []

    for i in alheios:
        if str(i['porta']) == str(porta):
            return (
                f'A instância OpenVPN "{i["nome"]}" da RB (certificado '
                f'"{i["certificado"]}") já usa a porta {porta}. Escolha outra porta. '
                f'Nada foi alterado na RB.'
            ), []
    return '', [i['nome'] for i in instancias if i['certificado'] == CERT_SERVIDOR and i['nome']]


def _detectar_profile_vpn(client):
    """
    Profile PPP de VPN que a RB já usa — o dos secrets L2TP/PPTP/SSTP/OVPN do
    próprio cliente, o mais usado que tenha local-address e remote-address
    apontando para um pool. Retorna {'nome', 'secrets', 'local', 'pool'} ou
    None (aí o chamador cai no pool próprio, o comportamento antigo).

    Plano de assinante nunca serve, mesmo que algum secret de VPN aponte para
    ele — ficam de fora o default-profile dos pppoe-servers, profiles usados
    por secrets pppoe e profiles com rate-limit. Achado real (ALTA RADIO): um
    secret pptp usava o profile "pppoe" (IP público local, pool CGNAT_01), que
    é o default-profile dos 12 pppoe-servers da RB.

    Por que: o profile OPEN_VPN com pool fixo 192.168.250.x é uma faixa que
    a rede do cliente não conhece. Achado real (CONECTONLINE, 192.140.66.160):
    o NAT do L2TP isenta `LOOPBACKVPNS` (loopbacks dos sites Starlink ligados
    por L2TP) e a rede roteia 10.190.180.0/24 de volta; o usuário OpenVPN,
    em 192.168.250.x e com NAT_OpenVPN mascarando tudo para o IP público,
    chegava nos sites com origem pública e a resposta saía pela internet do
    site. Mesma RB, L2TP acessava e OpenVPN não. Caindo no profile do L2TP,
    o OpenVPN herda o roteamento e o NAT que já funcionam.
    """
    out, err = _exec(
        client,
        ':foreach i in=[/ppp secret find where service!=pppoe and service!=any] do={'
        ':put ([/ppp secret get $i service] . "|" . [/ppp secret get $i profile])}',
        timeout=60)
    if _erro_mikrotik(out) or _erro_mikrotik(err):
        return None
    uso = Counter()
    for linha in out.splitlines():
        servico, _, profile = linha.strip().partition('|')
        if servico in VPN_SERVICOS and profile and profile != PROFILE_PLATAFORMA:
            uso[profile] += 1
    if not uso:
        return None

    out, _ = _exec(
        client,
        ':foreach i in=[/ppp profile find] do={:put ([/ppp profile get $i name] . "|" . '
        '[/ppp profile get $i local-address] . "|" . [/ppp profile get $i remote-address] . "|" . '
        '[/ppp profile get $i rate-limit])}',
        timeout=30)
    perfis = {}
    for linha in out.splitlines():
        partes = linha.strip().split('|')
        if len(partes) == 4:
            perfis[partes[0]] = partes[1:]

    out, _ = _exec(client, ':foreach i in=[/ip pool find] do={:put [/ip pool get $i name]}',
                   timeout=30)
    pools = {l.strip() for l in out.splitlines() if l.strip()}

    out, _ = _exec(
        client,
        ':foreach i in=[/interface pppoe-server server find] do={'
        ':put [/interface pppoe-server server get $i default-profile]}',
        timeout=30)
    de_pppoe = {l.strip() for l in out.splitlines() if l.strip()}

    for nome, qtd in uso.most_common():
        local, remoto, rate_limit = perfis.get(nome, ('', '', ''))
        if not local or remoto not in pools or rate_limit or nome in de_pppoe:
            continue
        # Contagem feita no próprio RouterOS: num concentrador são milhares de secrets.
        out, _ = _exec(client, f'/ppp secret print count-only where profile="{nome}" and service=pppoe',
                       timeout=30)
        if out.strip() != '0':
            continue
        return {'nome': nome, 'secrets': qtd, 'local': local, 'pool': remoto}
    return None


def _rotas_no_pool(client, pool, local_ip):
    """
    Rotas da RB que se sobrepõem ao pool próprio (ou ao IP local) — sinal de
    que a faixa já é usada na rede do cliente. Retorna ['dst via gw', ...],
    [] se livre, ou None se não deu para conferir. Rotas desabilitadas e as
    dinâmicas de sessões OpenVPN (reexecução com usuários conectados) não
    contam — no PROMOFI, uma estática desabilitada 192.168.250.0/24 barrava
    a reexecução à toa.
    """
    try:
        redes = _pool_redes(pool) + [ipaddress.ip_network(f'{local_ip}/32')]
        cobertura = _cobertura(redes)
    except ValueError:
        return None
    out, err = _exec(client, f'/ip route print terse where dst-address in {cobertura}',
                     timeout=30)
    if _erro_mikrotik(out) or _erro_mikrotik(err):
        return None
    conflitos = []
    for linha in out.splitlines():
        pares = list(_KV_RE.finditer(linha))
        if not pares or 'X' in linha[:pares[0].start()]:
            continue
        kv = {m.group(1): m.group(2).strip('"') for m in pares}
        gateway = kv.get('gateway', '')
        if 'dst-address' not in kv or 'ovpn-' in gateway:
            continue
        try:
            dst = ipaddress.ip_network(kv['dst-address'], strict=False)
        except ValueError:
            continue
        if any(dst.overlaps(r) for r in redes):
            conflitos.append(f'{dst} via {gateway or "?"}')
    return conflitos


def _exec_pty(client, cmd, timeout=60):
    """
    Executa comando com PTY (modo terminal interativo).
    Necessário para comandos do /interface que o script engine rejeita.
    """
    import re
    try:
        chan = client.get_transport().open_session()
        chan.get_pty(term='vt100', width=200, height=24)
        chan.exec_command(cmd)
        buf = b''
        deadline = time.time() + timeout
        while time.time() < deadline:
            if chan.recv_ready():
                buf += chan.recv(4096)
            if chan.exit_status_ready():
                while chan.recv_ready():
                    buf += chan.recv(4096)
                break
            time.sleep(0.1)
        chan.close()
        # Remove códigos ANSI do terminal
        out = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', buf.decode('utf-8', errors='replace')).strip()
        return out, ''
    except Exception as e:
        return '', str(e)


def _aguardar_cert(client, nome, tentativas=15):
    """
    Aguarda até o certificado ser assinado (flag T=trusted aparece na listagem).
    Retorna True se confirmado, False se timeout.
    """
    for _ in range(tentativas):
        out, _ = _exec(client, f'/certificate print detail where name="{nome}"')
        if 'T' in out.split('\n')[0] or 'trusted' in out.lower() or 'fingerprint' in out.lower():
            return True
        time.sleep(1)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Download de certificados via SFTP
# ─────────────────────────────────────────────────────────────────────────────

def _baixar_sftp(client, nome_vpn):
    """
    Tenta baixar CA.crt, {nome}.crt e {nome}.key via SFTP.
    Retorna (ca_pem, cert_pem, key_pem) ou lança Exception.
    """
    sftp = client.open_sftp()
    try:
        def ler(path):
            with sftp.open(path) as f:
                return f.read().decode('utf-8', errors='replace')

        ca   = ler('CA.crt')
        cert = ler(f'{nome_vpn}.crt')
        key  = ler(f'{nome_vpn}.key')
        return ca, cert, key
    finally:
        sftp.close()


def _baixar_via_ssh(client, nome_vpn):
    """
    Fallback: lê conteúdo dos arquivos via comando MikroTik
    `:put [/file get [find name=X] contents]`.
    """
    def ler(filename):
        cmd = f':put [/file get [find name="{filename}"] contents]'
        out, _ = _exec(client, cmd, timeout=30)
        return out if out else None

    ca   = ler('CA.crt')
    cert = ler(f'{nome_vpn}.crt')
    key  = ler(f'{nome_vpn}.key')
    return ca, cert, key


def _listar_arquivos(client):
    """Retorna string com os arquivos disponíveis no MikroTik (para diagnóstico)."""
    out, _ = _exec(client, '/file print', timeout=15)
    return out or '(sem saída)'


def baixar_certificados(client, nome_vpn):
    """Tenta SFTP; se falhar, usa SSH command fallback."""
    # Log diagnóstico: lista arquivos disponíveis
    arquivos = _listar_arquivos(client)
    logger.info(f'Arquivos no MikroTik antes do download:\n{arquivos}')

    try:
        ca, cert, key = _baixar_sftp(client, nome_vpn)
        if all([ca, cert, key]):
            logger.info('Certificados baixados via SFTP')
            return ca, cert, key
    except Exception as e:
        logger.warning(f'SFTP falhou ({e}), tentando via comando SSH…')

    ca, cert, key = _baixar_via_ssh(client, nome_vpn)
    if all([ca, cert, key]):
        logger.info('Certificados baixados via comando SSH')
        return ca, cert, key

    raise RuntimeError(
        f'Não foi possível baixar os certificados do MikroTik '
        f'(SFTP e fallback SSH falharam). '
        f'Arquivos encontrados no router: {arquivos}'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Geração do arquivo .ovpn
# ─────────────────────────────────────────────────────────────────────────────

def _descriptografar_chave(key_pem, passphrase):
    """
    Descriptografa chave privada exportada pelo MikroTik (PKCS#8 com passphrase)
    e retorna como PEM não criptografado (PKCS#1 / TraditionalOpenSSL).
    """
    try:
        from cryptography.hazmat.primitives.serialization import (
            load_pem_private_key, Encoding, PrivateFormat, NoEncryption
        )
        key = load_pem_private_key(key_pem.encode(), password=passphrase.encode())
        return key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()).decode()
    except Exception:
        # Se falhar na descriptografia, retorna a chave como veio
        return key_pem


def gerar_ovpn(ip_pub, porta, ca_pem, cert_pem, key_pem, passphrase=''):
    """Monta o conteúdo completo do arquivo .ovpn."""
    return (
        f"client\n"
        f"dev tun\n"
        f"proto tcp\n"
        f"remote {ip_pub} {porta}\n"
        f"nobind\n"
        f"user nobody\n"
        f"group nogroup\n"
        f"persist-tun\n"
        f"persist-key\n"
        f"tls-client\n"
        f"remote-cert-tls server\n"
        f"verb 4\n"
        f"mute 10\n"
        f"cipher AES-256-CBC\n"
        f"auth SHA1\n"
        f"pull\n"
        f"auth-user-pass\n"
        f"connect-retry 1\n"
        f"reneg-sec 3600\n"
        f"redirect-gateway def1\n"
        f"\n"
        f"<ca>\n{ca_pem.strip()}\n</ca>\n"
        f"<cert>\n{cert_pem.strip()}\n</cert>\n"
        f"<key>\n{key_pem.strip()}\n</key>\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tarefa principal — chamada em thread separada
# ─────────────────────────────────────────────────────────────────────────────

def _falhar(config, logs, msg):
    config.logs     = '\n'.join(logs)
    config.status   = 'erro'
    config.erro_msg = msg
    config.save(update_fields=['logs', 'status', 'erro_msg'])
    logger.error(f'OpenVPN [{config.id}]: {msg}')


def executar_config_openvpn(config_id):
    """
    Conecta ao MikroTik, aplica a configuração de OpenVPN,
    baixa os certificados gerados e salva o arquivo .ovpn.
    Deve ser chamada em uma thread daemon separada.
    """
    from .models import OpenVPNConfig, ProxyServer
    from .views  import is_private_ip, vpn_cobre_ip, criar_ssh_tunnel

    ssh_tunnel = None
    client     = None

    try:
        config = OpenVPNConfig.objects.get(id=config_id)
        acesso = config.acesso

        config.status  = 'configurando'
        config.erro_msg = ''
        config.save(update_fields=['status', 'erro_msg'])

        cfg = {
            'nome_vpn':       config.nome_vpn,
            'ip_publico':     config.ip_publico,
            'porta':          config.porta,
            'vpn_pool':       config.vpn_pool,
            'vpn_local_ip':   config.vpn_local_ip,
            'vpn_username':   config.vpn_username,
            'vpn_password':   config.vpn_password,
            'cert_passphrase': config.cert_passphrase,
            'rate_limit':     config.rate_limit,
        }

        # Os comandos só são montados depois de conectar: profile PPP, pool
        # próprio e, no v7, `add` em lista vs `set` em objeto único dependem
        # de sondar o equipamento (checagens prévias, abaixo).

        # ── Conectar via SSH ──────────────────────────────────────────────
        host_conexao  = acesso.host
        porta_conexao = acesso.porta or 22

        if is_private_ip(acesso.host):
            if vpn_cobre_ip(acesso.cliente, acesso.host):
                pass  # Alcançável pelo túnel OpenVPN — conecta direto
            else:
                proxy = ProxyServer.objects.filter(
                    cliente=acesso.cliente, ativo=True
                ).first()
                if proxy:
                    ssh_tunnel = criar_ssh_tunnel(
                        {
                            'host': proxy.host, 'porta': proxy.porta,
                            'usuario': proxy.usuario, 'senha': proxy.senha,
                        },
                        acesso.host, porta_conexao,
                    )
                    host_conexao  = ssh_tunnel['local_host']
                    porta_conexao = ssh_tunnel['local_port']

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host_conexao,
            port=porta_conexao,
            username=acesso.usuario,
            password=acesso.senha,
            timeout=30,
            look_for_keys=False,
            allow_agent=False,
            banner_timeout=30,
        )
        client.get_transport().set_keepalive(10)
        logger.info(f'OpenVPN [{config_id}]: SSH conectado a {acesso.host}')

        usa_lista = config.ros_version == '7' and _ovpn_server_suporta_lista(client)
        if config.ros_version == '7':
            logger.info(
                f'OpenVPN [{config_id}]: /interface ovpn-server server '
                f'{"suporta lista (add)" if usa_lista else "é objeto único (set)"}'
            )

        # ── Checagens prévias (só leitura) ────────────────────────────────
        # Se alguma barrar, retorna antes do primeiro comando: a RB fica
        # exatamente como estava.
        logs = []
        erro, remover = _checar_servidor_existente(
            _ler_ovpn_server(client, usa_lista), config.porta, usa_lista)
        if erro:
            _falhar(config, logs, erro)
            return

        perfil = _detectar_profile_vpn(client) if config.usar_profile_existente else None
        if perfil:
            cfg['ppp_profile'], cfg['criar_pool'] = perfil['nome'], False
            logs.append(
                f'# Profile PPP "{perfil["nome"]}" (já usado por {perfil["secrets"]} '
                f'secret(s) de VPN desta RB; local {perfil["local"]}, pool {perfil["pool"]}).\n'
                f'# O usuário OpenVPN cai na mesma faixa do L2TP/demais VPNs, que a rede já\n'
                f'# roteia e trata no NAT. Pool, profile e NAT próprios (POOL_OpenVPN,\n'
                f'# {PROFILE_PLATAFORMA}, NAT_OpenVPN) não são criados; o rate-limit do cadastro não se aplica.\n'
            )
        else:
            cfg['ppp_profile'], cfg['criar_pool'] = PROFILE_PLATAFORMA, True
            motivo = ('pool próprio escolhido no cadastro' if not config.usar_profile_existente
                      else 'nenhum profile de VPN reaproveitável na RB')
            logs.append(f'# Profile PPP {PROFILE_PLATAFORMA} com pool {cfg["vpn_pool"]} ({motivo}).\n')
            conflitos = _rotas_no_pool(client, cfg['vpn_pool'], cfg['vpn_local_ip'])
            if conflitos:
                _falhar(config, logs,
                        f'O pool {cfg["vpn_pool"]} (IP local {cfg["vpn_local_ip"]}) se sobrepõe a '
                        f'rotas que já existem na RB: {", ".join(conflitos[:5])}. Essa faixa já é '
                        f'usada na rede do cliente — escolha outro pool nas Configurações '
                        f'avançadas. Nada foi alterado na RB.')
                return
            if conflitos is None:
                logs.append('# Aviso: não deu para conferir se o pool colide com rotas da RB.\n')

        config.ppp_profile = cfg['ppp_profile']
        config.save(update_fields=['ppp_profile'])

        cmds = comandos_ros7(cfg) if config.ros_version == '7' else comandos_ros6(cfg)
        if config.ros_version == '7':
            cmds += (_cmd_ovpn_server_lista(cfg, remover) if usa_lista
                     else _cmd_ovpn_server_singleton_v7(cfg))

        # ── Executar comandos ─────────────────────────────────────────────
        for cmd in cmds:
            if cmd.startswith('__DELAY_'):
                secs = int(cmd.split('_')[3])
                time.sleep(secs)
                continue

            if cmd.startswith('__PTY__'):
                real_cmd = cmd[7:]
                out, err = _exec_pty(client, real_cmd, timeout=90)
            else:
                real_cmd = cmd
                out, err = _exec(client, cmd, timeout=90)

            linha = f'$ {real_cmd}\n{out}\n{("ERR: " + err) if err else ""}\n'
            logs.append(linha)
            logger.info(f'OpenVPN [{config_id}]: {real_cmd[:80]} → {out[:80]}')

            # O MikroTik devolve recusa pela saída normal do comando, não por
            # um canal de erro separado — por isso checa `out` e `err`, não só
            # exceção. Achado real: `/interface ovpn-server server add` foi
            # gravado como "Concluído" mesmo respondendo "bad command name
            # add" (a lista de instâncias só existe em builds recentes do v7).
            erro = _erro_mikrotik(out) or _erro_mikrotik(err)
            if erro:
                config.logs     = '\n'.join(logs)
                config.status   = 'erro'
                config.erro_msg = f'Comando recusado pelo MikroTik: {erro}'
                config.save(update_fields=['logs', 'status', 'erro_msg'])
                logger.error(f'OpenVPN [{config_id}]: comando recusado — {erro}')
                return

        config.logs = '\n'.join(logs)
        config.save(update_fields=['logs'])

        # ── Aguardar e baixar certificados ────────────────────────────────
        time.sleep(2)
        ca_pem, cert_pem, key_pem = baixar_certificados(client, cfg['nome_vpn'])

        # ── Gerar .ovpn ───────────────────────────────────────────────────
        # Descriptografa a chave privada antes de embutir no .ovpn
        key_pem = _descriptografar_chave(key_pem, cfg['cert_passphrase'])
        ovpn_content = gerar_ovpn(
            config.ip_publico, config.porta,
            ca_pem, cert_pem, key_pem
        )

        # ── Salvar arquivo ────────────────────────────────────────────────
        ovpn_dir = os.path.join(
            settings.MEDIA_ROOT, 'openvpn', f'cliente_{config.cliente_id}'
        )
        os.makedirs(ovpn_dir, exist_ok=True)
        filename   = f'{config.nome_vpn}_{config.id}.ovpn'
        ovpn_path  = os.path.join(ovpn_dir, filename)
        with open(ovpn_path, 'w') as f:
            f.write(ovpn_content)

        config.ovpn_path = os.path.relpath(ovpn_path, settings.MEDIA_ROOT)
        config.status    = 'concluido'
        config.save(update_fields=['ovpn_path', 'status', 'logs'])
        logger.info(f'OpenVPN [{config_id}]: concluído → {filename}')

    except Exception as exc:
        logger.exception(f'OpenVPN [{config_id}]: ERRO: {exc}')
        try:
            OpenVPNConfig.objects.filter(id=config_id).update(
                status='erro', erro_msg=str(exc)
            )
        except Exception:
            pass

    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
        if ssh_tunnel:
            try:
                ssh_tunnel.get('ssh_client').close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Adicionar novo usuário a um OpenVPN já configurado
# ─────────────────────────────────────────────────────────────────────────────

def adicionar_usuario_openvpn(usuario_id):
    """
    Conecta ao MikroTik do config pai, cria certificado + usuário PPP para o novo
    usuário VPN e gera o arquivo .ovpn correspondente.
    Deve ser chamada em thread daemon separada.
    """
    from .models import OpenVPNUsuario, ProxyServer
    from .views  import is_private_ip, vpn_cobre_ip, criar_ssh_tunnel

    ssh_tunnel = None
    client     = None

    try:
        usuario = OpenVPNUsuario.objects.get(id=usuario_id)
        config  = usuario.config
        acesso  = config.acesso

        usuario.status   = 'configurando'
        usuario.erro_msg = ''
        usuario.save(update_fields=['status', 'erro_msg'])

        nome       = usuario.nome
        username   = usuario.username
        password   = usuario.password
        passphrase = config.cert_passphrase

        # ── Conectar via SSH ──────────────────────────────────────────────
        host_conexao  = acesso.host
        porta_conexao = acesso.porta or 22

        if is_private_ip(acesso.host):
            if vpn_cobre_ip(acesso.cliente, acesso.host):
                pass
            else:
                proxy = ProxyServer.objects.filter(
                    cliente=acesso.cliente, ativo=True
                ).first()
                if proxy:
                    ssh_tunnel = criar_ssh_tunnel(
                        {'host': proxy.host, 'porta': proxy.porta,
                         'usuario': proxy.usuario, 'senha': proxy.senha},
                        acesso.host, porta_conexao,
                    )
                    host_conexao  = ssh_tunnel['local_host']
                    porta_conexao = ssh_tunnel['local_port']

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host_conexao, port=porta_conexao,
            username=acesso.usuario, password=acesso.senha,
            timeout=30, look_for_keys=False, allow_agent=False, banner_timeout=30,
        )
        client.get_transport().set_keepalive(10)

        # ── Comandos: apenas cert + usuário (sem reconfigurar o servidor) ─
        cmds = [
            # Limpa arquivos anteriores deste usuário
            f'/file remove [find name="{nome}.crt"]',
            f'/file remove [find name="{nome}.key"]',
            '__DELAY_1__',
            # Limpa certificado anterior
            f'/certificate remove [find name={nome}]',
            '__DELAY_1__',
            # Cria e assina o certificado do cliente
            f'/certificate add name={nome} common-name={nome} key-usage=tls-client',
            f'/certificate sign {nome} ca=CA',
            '__DELAY_5__',
            # Exporta certificado com passphrase para incluir a chave privada
            f'/certificate export-certificate {nome} type=pem file-name={nome} export-passphrase={passphrase}',
            '__DELAY_3__',
            # Remove usuário PPP anterior e recria
            f'/ppp secret remove [find name={username}]',
            # Mesmo profile do usuário principal (o de VPN da RB ou OPEN_VPN)
            f'/ppp secret add name={username} password={password} service=ovpn '
            f'profile="{config.ppp_profile or PROFILE_PLATAFORMA}"',
        ]

        logs = []
        for cmd in cmds:
            if cmd.startswith('__DELAY_'):
                time.sleep(int(cmd.split('_')[3]))
                continue
            out, err = _exec(client, cmd, timeout=90)
            logs.append(f'$ {cmd}\n{out}\n{("ERR: " + err) if err else ""}\n')

        usuario.logs = '\n'.join(logs)
        usuario.save(update_fields=['logs'])

        # ── Baixar certificados e gerar .ovpn ─────────────────────────────
        time.sleep(2)
        arquivos = _listar_arquivos(client)
        logger.info(f'Arquivos no MikroTik (usuario {usuario_id}):\n{arquivos}')

        ca_pem, cert_pem, key_pem = baixar_certificados(client, nome)
        key_pem = _descriptografar_chave(key_pem, passphrase)

        ovpn_content = gerar_ovpn(config.ip_publico, config.porta, ca_pem, cert_pem, key_pem)

        ovpn_dir = os.path.join(settings.MEDIA_ROOT, 'openvpn', f'cliente_{config.cliente_id}')
        os.makedirs(ovpn_dir, exist_ok=True)
        filename  = f'{nome}_{usuario_id}.ovpn'
        ovpn_path = os.path.join(ovpn_dir, filename)
        with open(ovpn_path, 'w') as f:
            f.write(ovpn_content)

        usuario.ovpn_path = os.path.relpath(ovpn_path, settings.MEDIA_ROOT)
        usuario.status    = 'concluido'
        usuario.save(update_fields=['ovpn_path', 'status', 'logs'])

    except Exception as exc:
        logger.exception(f'OpenVPN usuario [{usuario_id}]: ERRO: {exc}')
        try:
            OpenVPNUsuario.objects.filter(id=usuario_id).update(
                status='erro', erro_msg=str(exc)
            )
        except Exception:
            pass
    finally:
        if client:
            try: client.close()
            except Exception: pass
        if ssh_tunnel:
            try: ssh_tunnel.get('ssh_client').close()
            except Exception: pass
