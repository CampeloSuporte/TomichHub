"""
hotspot_views.py — Gerenciamento de Hotspot MikroTik por cliente

Fluxo:
  1. Operador cria HotspotConfig no CRM associando um Acesso MikroTik
  2. Faz upload dos banners (slides) do portal de login
  3. Clica em "Aplicar no MikroTik" → SSH aplica a config + envia login.html
  4. Usuários do hotspot veem o portal, preenchem dados → leads capturados via pixel
"""
import base64
import html as _html
import json
import logging
import os
from datetime import datetime

import paramiko
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models.functions import TruncDate
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Acesso, Cliente, HotspotBanner, HotspotConfig, HotspotLead

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cliente(request, cliente_id):
    return get_object_or_404(Cliente, id=cliente_id)


def _json(request):
    try:
        return json.loads(request.body)
    except Exception:
        return {}


def _mt_exec(client, cmd, timeout=30):
    """Run a single RouterOS command via SSH exec_command."""
    try:
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode('utf-8', errors='replace').strip()
        err = stderr.read().decode('utf-8', errors='replace').strip()
        rc = stdout.channel.recv_exit_status()
        return out, err, rc
    except Exception as exc:
        return '', str(exc), -1


def _get_crm_host(request):
    """Return the CRM's public hostname/IP (used for walled-garden and pixel URL)."""
    host = request.get_host().split(':')[0]  # strip port
    return host


def _pixel_url(request, hotspot):
    scheme = 'https' if request.is_secure() else 'http'
    host = request.get_host()
    return f'{scheme}://{host}/clientes/hotspot/pixel/{hotspot.uuid}/'


def _portal_url(request, hotspot):
    scheme = 'https' if request.is_secure() else 'http'
    host = request.get_host()
    return f'{scheme}://{host}/clientes/hotspot/portal/{hotspot.uuid}/'


# ─────────────────────────────────────────────────────────────────────────────
# HTML generation
# ─────────────────────────────────────────────────────────────────────────────

def _gerar_login_html(hotspot, portal_url):
    """
    Gera um login.html mínimo para o MikroTik que redireciona o usuário para
    o portal hospedado no CRM. Usa HTTP para que o walled-garden permita o
    acesso antes da autenticação.

    IMPORTANTE (sem <script>, sem $(link-login)/$(link-orig)): captive portal
    browsers embutidos (iOS Captive Network Assistant, Android) são muito mais
    restritos que um Safari/Chrome normal — historicamente falham silenciosamente
    (tela em branco) em páginas que dependem de JS para redirecionar, e o
    RouterOS precisa reescrever o arquivo em tempo real para substituir cada
    $(...) pelo valor real, o que pode inflar bastante o tamanho da resposta
    quando a variável é uma URL longa (ex: $(link-login), $(link-orig) — cada
    uma pode ter 100+ caracteres com querystring própria). Um <meta refresh>
    puro, sem JS e só com variáveis curtas ($(mac)/$(ip)), é a combinação mais
    compatível. O hotspot_portal_conectar já tem fallback para gateway quando
    "link"/"orig" não chegam (ver uso de h.gateway abaixo).

    IMPORTANTE 2 (IP em vez de hostname): usar o nome de domínio aqui exige que
    o cliente resolva DNS antes de conseguir navegar. Em Android isso costuma
    ir por "DNS Privado" (DoH do Google/Cloudflare, ignorando totalmente o DNS
    do roteador) — o domínio tem registro AAAA (IPv6), o aparelho tenta por
    IPv6 mesmo sem rota de verdade, e o navegador cativo restrito não faz o
    fallback pra IPv4 que um browser normal faria (net::ERR_CONNECTION_ABORTED).
    Resolvendo pra IP aqui (uma vez, no servidor) e usando o IP puro na URL,
    a navegação nunca depende de DNS do cliente — nginx tem crm.tomich.com.br
    e esse IP como server_name válidos, então a rota até o Django funciona
    igual em ambos os casos.
    """
    # Força HTTP: clientes não têm HTTPS no walled-garden antes de autenticar
    http_portal = portal_url.replace('https://', 'http://', 1).rstrip('/')
    try:
        from urllib.parse import urlsplit, urlunsplit
        import socket as _sock3
        _parts = urlsplit(http_portal)
        _ip_host = _sock3.gethostbyname(_parts.hostname)  # IPv4-only, evita AAAA
        http_portal = urlunsplit(_parts._replace(netloc=_ip_host))
    except Exception:
        pass  # se a resolução falhar, mantém o hostname (comportamento anterior)
    destino = http_portal + '/?mac=$(mac)&ip=$(ip)'

    return (
        '<!DOCTYPE html>\n'
        '<html>\n'
        '<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta http-equiv="refresh" content="1;url=' + destino + '">\n'
        '</head>\n'
        '<body>\n'
        '<p>Redirecionando para o portal...</p>\n'
        '<p><a href="' + destino + '">Clique aqui se não for redirecionado automaticamente</a></p>\n'
        '</body>\n'
        '</html>\n'
    )


# ─────────────────────────────────────────────────────────────────────────────
# SSH — apply config to MikroTik
# ─────────────────────────────────────────────────────────────────────────────

def _mt_output_ok(out, err, rc):
    """RouterOS sends errors to stdout; check all outputs for failure indicators."""
    combined = (out + err).lower()
    if rc != 0:
        return False
    bad = ('failure', 'bad command', 'invalid', 'no such item', 'syntax error',
           'expected', 'required', 'already have')
    return not any(k in combined for k in bad)


def _mt_count(client, cmd):
    """Return integer count from a RouterOS 'print count-only' command."""
    out, _, _ = _mt_exec(client, cmd)
    try:
        return int(out.strip())
    except ValueError:
        return 0


def _aplicar_mikrotik(hotspot, pixel_url):
    """
    Aplica configuração completa de hotspot no MikroTik via SSH:
      1. IP Address na interface
      2. IP Pool
      3. DHCP Server
      4. DHCP Network
      5. Hotspot Profile
      6. Hotspot Server
      7. Usuário guest
      8. Walled Garden
      9. Upload login.html via SFTP

    Usa padrão check→set/add (idempotente): RouterOS não permite remover itens
    que estão referenciados por outros (ex: pool em uso por servidor).
    """
    import ipaddress as _ipmod
    from urllib.parse import urlparse

    acesso = hotspot.acesso
    log = []

    if not acesso:
        return False, ['Nenhum acesso MikroTik selecionado.']

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=acesso.host,
            port=int(acesso.porta) if acesso.porta else 22,
            username=acesso.usuario,
            password=acesso.senha,
            timeout=20,
            look_for_keys=False,
            allow_agent=False,
            banner_timeout=20,
            disabled_algorithms={'kex': ['diffie-hellman-group16-sha512',
                                         'diffie-hellman-group18-sha512']},
        )
        log.append('✅ SSH conectado ao MikroTik')
    except Exception as exc:
        return False, [f'❌ Falha SSH: {exc}']

    pool_name    = 'hs-pool-crm'
    dhcp_name    = 'hs-dhcp-crm'
    profile_name = 'hs-prof-crm'
    server_name  = 'hs-crm'
    # Usar o diretório padrão do MikroTik — sempre existe no flash,
    # evita qualquer problema de path com diretório customizado
    dir_name     = 'hotspot'

    # Calcular prefixo a partir do campo network (ex: 192.168.88.0/24 → /24)
    try:
        prefix = _ipmod.IPv4Network(hotspot.network, strict=False).prefixlen
    except ValueError:
        prefix = 24
    gw_cidr = f'{hotspot.gateway}/{prefix}'

    def _set_or_add(check_cmd, set_cmd, add_cmd, label_item):
        """Check existence, then set or add. Returns (ok, label, output)."""
        n = _mt_count(client, check_cmd)
        if n > 0:
            o, e, rc = _mt_exec(client, set_cmd)
            detail = (o + ' ' + e).strip() or 'ok'
            return _mt_output_ok(o, e, rc), 'atualizado', f'rc={rc} {detail}'
        else:
            o, e, rc = _mt_exec(client, add_cmd)
            detail = (o + ' ' + e).strip() or 'ok'
            return _mt_output_ok(o, e, rc), 'criado', f'rc={rc} {detail}'

    try:
        # ── 0. Bridge interface ───────────────────────────────────────────────
        bridge_comment = f'hotspot-{hotspot.nome}'
        # Nome base para a nova bridge (sanitizado, ex: "hotspot-tomich")
        safe_nome = hotspot.nome.lower().replace(' ', '-')
        bridge_name = f'hs-{safe_nome}'

        if _mt_count(client, f'/interface bridge print count-only where name="{bridge_name}"') == 0:
            out, err, rc = _mt_exec(client,
                f'/interface bridge add name="{bridge_name}" comment="{bridge_comment}"')
            ok = _mt_output_ok(out, err, rc)
            log.append(f'{"✅" if ok else "⚠️"} Bridge criada ({bridge_name}): {out or err or "ok"}')
        else:
            # Reutiliza a bridge existente — NÃO cria nova para não perder as interfaces já configuradas
            _mt_exec(client, f'/interface bridge set [find name="{bridge_name}"] comment="{bridge_comment}"')
            log.append(f'✅ Bridge reutilizada ({bridge_name}) — interfaces existentes preservadas')

        # Adicionar interface física ao bridge como bridge port (se configurada)
        if hotspot.interface_fisica:
            iface = hotspot.interface_fisica.strip()
            port_in_hs = _mt_count(client,
                f'/interface bridge port print count-only where bridge="{bridge_name}" interface="{iface}"')
            if port_in_hs > 0:
                log.append(f'✅ Bridge port {iface} já está em {bridge_name}')
            else:
                # Verifica se a interface está em OUTRA bridge.
                # RouterOS não permite que uma interface seja membro de duas bridges;
                # o "add" falharia silenciosamente e o hotspot não interceptaria tráfego.
                other_count = _mt_count(client,
                    f'/interface bridge port print count-only where interface="{iface}"')
                if other_count > 0:
                    _mt_exec(client,
                        f'/interface bridge port remove [find interface="{iface}"]')
                    log.append(f'   Removida {iface} de bridge anterior')
                bp_out, bp_err, bp_rc = _mt_exec(client,
                    f'/interface bridge port add bridge="{bridge_name}" interface="{iface}"')
                bp_ok = _mt_output_ok(bp_out, bp_err, bp_rc)
                log.append(f'{"✅" if bp_ok else "⚠️"} Bridge port {iface} → {bridge_name}: {(bp_out+bp_err).strip() or "ok"}')
        else:
            # Listar interfaces disponíveis para ajudar o usuário a configurar
            ifaces_out, _, _ = _mt_exec(client,
                '/interface print where type="wlan" or type="ether" or type="vlan"')
            ifaces_lines = [l.strip() for l in ifaces_out.strip().splitlines() if l.strip()]
            ifaces_str = ' | '.join(ifaces_lines[:8]) if ifaces_lines else '(nenhuma listada)'
            log.append(
                f'   ⚠️ Interface física NÃO configurada — o hotspot não vai interceptar clientes!\n'
                f'   Acesse Configurações do Hotspot e preencha o campo "Interface Física".\n'
                f'   Interfaces disponíveis no roteador:\n'
                + '\n'.join(f'      {l}' for l in ifaces_lines[:10])
            )

        # ── 0b. NAT src-nat para a rede do hotspot ───────────────────────────
        # Usa src-nat com to-addresses=IP-do-acesso em vez de masquerade.
        # Masquerade usa o IP da interface de saída (pode ser um IP interno de carrier
        # como 198.18.x.x), que o upstream ISP não roteia de volta para a internet.
        # Com src-nat to-addresses=acesso.host, o tráfego dos clientes aparece como
        # originado do IP público do roteador — o mesmo que o CRM usa para SSH.
        nat_public_ip = acesso.host
        # Remove regras antigas (masquerade ou src-nat) para evitar duplicatas
        _mt_exec(client,
            f'/ip firewall nat remove [find chain=srcnat src-address="{hotspot.network}" comment="hs-crm-masq"]')
        nat_count = _mt_count(client,
            f'/ip firewall nat print count-only where chain=srcnat '
            f'src-address="{hotspot.network}" action=src-nat to-addresses="{nat_public_ip}"')
        if nat_count == 0:
            _mt_exec(client,
                f'/ip firewall nat add chain=srcnat action=src-nat '
                f'to-addresses="{nat_public_ip}" '
                f'src-address="{hotspot.network}" comment="hs-crm-masq"')
            log.append(f'✅ NAT src-nat adicionado ({hotspot.network} → {nat_public_ip})')
        else:
            log.append(f'✅ NAT src-nat já existe ({hotspot.network} → {nat_public_ip})')

        # ── 1. IP Address na interface ────────────────────────────────────────
        ip_flt = f'interface="{bridge_name}" address~"{hotspot.gateway}"'
        ok, lbl, out = _set_or_add(
            f'/ip address print count-only where {ip_flt}',
            f'/ip address set [find {ip_flt}] address={gw_cidr}',
            f'/ip address add address={gw_cidr} interface={bridge_name}',
            'IP Address',
        )
        log.append(f'{"✅" if ok else "⚠️"} IP Address {lbl} ({gw_cidr} → {bridge_name}): {out}')

        # ── 2. IP Pool ────────────────────────────────────────────────────────
        ranges = f'{hotspot.pool_start}-{hotspot.pool_end}'
        ok, lbl, out = _set_or_add(
            f'/ip pool print count-only where name="{pool_name}"',
            f'/ip pool set [find name="{pool_name}"] ranges={ranges}',
            f'/ip pool add name="{pool_name}" ranges={ranges}',
            'IP Pool',
        )
        log.append(f'{"✅" if ok else "⚠️"} IP Pool {lbl} ({ranges}): {out}')

        # ── 3. DHCP Server ────────────────────────────────────────────────────
        ok, lbl, out = _set_or_add(
            f'/ip dhcp-server print count-only where name="{dhcp_name}"',
            f'/ip dhcp-server set [find name="{dhcp_name}"] '
            f'interface={bridge_name} address-pool="{pool_name}" disabled=no',
            f'/ip dhcp-server add name="{dhcp_name}" '
            f'interface={bridge_name} address-pool="{pool_name}" disabled=no',
            'DHCP Server',
        )
        log.append(f'{"✅" if ok else "⚠️"} DHCP Server {lbl}: {out}')

        # ── 3b. DHCP Lease Script (controle de banda via Queue Simple) ────────
        if hotspot.dhcp_controle_banda:
            limit = hotspot.dhcp_banda_limit or '10M/10M'
            # Script simples: cria/remove queue por IP do lease
            # \" dentro do valor RouterOS → aspas literais no script
            lease_script = (
                ':if ($leaseBound = 1) do={'
                '/queue simple remove [find where comment=$leaseActMAC]; '
                f'/queue simple add target=$leaseActIP max-limit={limit} comment=$leaseActMAC'
                '} else={'
                '/queue simple remove [find where comment=$leaseActMAC]'
                '}'
            )
            ls_out, ls_err, ls_rc = _mt_exec(
                client,
                f'/ip dhcp-server set [find name="{dhcp_name}"] lease-script="{lease_script}"',
            )
            ls_ok = _mt_output_ok(ls_out, ls_err, ls_rc)
            log.append(f'{"✅" if ls_ok else "⚠️"} DHCP Lease Script (banda {limit}): {ls_out or ls_err or "ok"}')
        else:
            # Garantir que não há script de banda ativo
            _mt_exec(client, f'/ip dhcp-server set [find name="{dhcp_name}"] lease-script=""')
            log.append('ℹ️ Controle de banda desativado — lease-script limpo')

        # ── 4. DHCP Network ───────────────────────────────────────────────────
        # IMPORTANTE: o cliente do hotspot deve receber o GATEWAY (o próprio
        # roteador) como DNS — NÃO um DNS externo (8.8.8.8). Antes do login o
        # hotspot bloqueia o acesso externo; se o cliente tentar falar com 8.8.8.8
        # a resolução falha (DNS_PROBE_FINISHED_NO_INTERNET) e o portal nem abre.
        # Usando o gateway, o hotspot intercepta o DNS e redireciona para o login.
        ok, lbl, out = _set_or_add(
            f'/ip dhcp-server network print count-only where address="{hotspot.network}"',
            f'/ip dhcp-server network set [find address="{hotspot.network}"] '
            f'gateway={hotspot.gateway} dns-server={hotspot.gateway}',
            f'/ip dhcp-server network add address={hotspot.network} '
            f'gateway={hotspot.gateway} dns-server={hotspot.gateway}',
            'DHCP Network',
        )
        log.append(f'{"✅" if ok else "⚠️"} DHCP Network {lbl} ({hotspot.network}): {out}')

        # ── 5. Hotspot Profile ────────────────────────────────────────────────
        # RouterOS aceita inteiros em segundos para timeout (0 = ilimitado)
        session_secs = hotspot.session_timeout * 60  # minutos → segundos
        idle_secs    = hotspot.idle_timeout    * 60
        rate         = f'{hotspot.rate_limit_down}/{hotspot.rate_limit_up}'
        # Criar/atualizar perfil com parâmetros mínimos primeiro.
        # Aspas ao redor dos valores evitam "expected end of command" no RouterOS
        # quando o comando ultrapassa certos limites de parsing via SSH exec_command.
        prof_base = (
            f'hotspot-address="{hotspot.gateway}" '
            f'login-by=http-pap '
            f'html-directory="{dir_name}"'
        )
        prof_exists = _mt_count(client, f'/ip hotspot profile print count-only where name="{profile_name}"')
        if prof_exists > 0:
            p_out, p_err, p_rc = _mt_exec(client,
                f'/ip hotspot profile set [find name="{profile_name}"] {prof_base}')
            p_lbl = 'atualizado'
        else:
            p_out, p_err, p_rc = _mt_exec(client,
                f'/ip hotspot profile add name="{profile_name}" {prof_base}')
            p_lbl = 'criado'
        p_ok = _mt_output_ok(p_out, p_err, p_rc)
        log.append(f'{"✅" if p_ok else "⚠️"} Hotspot Profile {p_lbl}: rc={p_rc} {(p_out+p_err).strip() or "ok"}')

        # Aplicar rate-limit e timeouts separadamente (evita erro de sintaxe no add)
        if p_ok or prof_exists > 0:
            _mt_exec(client,
                f'/ip hotspot profile set [find name="{profile_name}"] '
                f'session-timeout={session_secs} idle-timeout={idle_secs} rate-limit="{rate}"')
            log.append(f'   Rate-limit/timeout: {rate}, session={session_secs}s, idle={idle_secs}s')

        # ── 6. Hotspot Server ─────────────────────────────────────────────────
        srv_params = (
            f'interface="{bridge_name}" '
            f'address-pool="{pool_name}" '
            f'profile="{profile_name}" '
            f'disabled=no'
        )
        srv_exists = _mt_count(client, f'/ip hotspot print count-only where name="{server_name}"')
        if srv_exists > 0:
            cmd_srv = f'/ip hotspot set [find name="{server_name}"] {srv_params}'
            srv_out, srv_err, srv_rc = _mt_exec(client, cmd_srv)
            srv_lbl = 'atualizado'
        else:
            cmd_srv = f'/ip hotspot add name="{server_name}" {srv_params}'
            srv_out, srv_err, srv_rc = _mt_exec(client, cmd_srv)
            srv_lbl = 'criado'
        srv_ok = _mt_output_ok(srv_out, srv_err, srv_rc)
        srv_detail = (srv_out + ' ' + srv_err).strip() or 'ok'
        log.append(f'{"✅" if srv_ok else "⚠️"} Hotspot Server {srv_lbl}: {srv_detail}')

        # ── 7. Usuário guest ──────────────────────────────────────────────────
        g_flt = f'name="{hotspot.guest_usuario}" server="{server_name}"'
        ok, lbl, out = _set_or_add(
            f'/ip hotspot user print count-only where {g_flt}',
            f'/ip hotspot user set [find {g_flt}] '
            f'password="{hotspot.guest_senha}" profile=default',
            f'/ip hotspot user add name="{hotspot.guest_usuario}" '
            f'password="{hotspot.guest_senha}" server="{server_name}" profile=default',
            'Usuário guest',
        )
        log.append(f'{"✅" if ok else "⚠️"} Usuário guest {lbl} ({hotspot.guest_usuario}): {out}')

        # Perfil default: permite múltiplos dispositivos simultâneos com a conta guest
        _mt_exec(client, '/ip hotspot user profile set [find name="default"] shared-users=unlimited')

        # ── 7b. DNS no MikroTik ───────────────────────────────────────────────
        # O roteador precisa RESPONDER DNS aos clientes do hotspot (eles recebem
        # o gateway como DNS via DHCP) e ter um upstream para resolver. Sem
        # allow-remote-requests o cliente não recebe resposta de DNS → o portal
        # nem carrega (DNS_PROBE_FINISHED_NO_INTERNET).
        dns_out, _, _ = _mt_exec(client, '/ip dns print')
        if 'allow-remote-requests: yes' not in dns_out:
            _mt_exec(client, '/ip dns set allow-remote-requests=yes')
            log.append('✅ DNS: allow-remote-requests habilitado')
        # Garante um upstream (sem sobrescrever um já existente do cliente)
        _srv = ''
        for _l in dns_out.splitlines():
            if _l.strip().startswith('servers:'):
                _srv = _l.split(':', 1)[1].strip()
                break
        if not _srv:
            _up = hotspot.dns_servidor or '8.8.8.8'
            _mt_exec(client, f'/ip dns set servers={_up}')
            log.append(f'✅ DNS upstream configurado ({_up})')
        else:
            log.append(f'✅ DNS do roteador OK (upstream {_srv})')

        # ── 8. Walled Garden ──────────────────────────────────────────────────
        crm_host = urlparse(pixel_url).hostname or pixel_url.split('/')[2].split(':')[0]

        # Remove TODAS as entradas existentes para este host antes de re-adicionar
        # (evita duplicatas causadas por múltiplos applies ou referências por ID *N)
        _mt_exec(client,
            f'/ip hotspot walled-garden remove [find where dst-host="{crm_host}"]')
        # Adiciona uma entrada para o servidor específico e uma global (sem server)
        _mt_exec(client,
            f'/ip hotspot walled-garden add dst-host="{crm_host}" server="{server_name}"')
        _mt_exec(client,
            f'/ip hotspot walled-garden add dst-host="{crm_host}"')
        log.append(f'✅ Walled Garden HTTP configurado ({crm_host})')

        import socket as _sock
        try:
            crm_ip = _sock.gethostbyname(crm_host)
            # Garante walled-garden IP para HTTP (80) e HTTPS (443) — sem eles o
            # mini-browser não alcança o portal antes da autenticação.
            # Limpa TODAS as entradas existentes para este IP (evita duplicatas).
            # RouterOS normaliza dst-address de host único sem o sufixo /32 ao
            # armazenar — um find com "/32" nunca casa com o valor salvo e a
            # entrada nunca é removida, duplicando a cada aplicação. Usar o
            # IP puro (sem /32) tanto para o find quanto para o add.
            _mt_exec(client,
                f'/ip hotspot walled-garden ip remove [find where dst-address="{crm_ip}"]')
            _mt_exec(client,
                f'/ip hotspot walled-garden ip add dst-address="{crm_ip}" '
                f'dst-port=80 protocol=tcp action=accept')
            _mt_exec(client,
                f'/ip hotspot walled-garden ip add dst-address="{crm_ip}" '
                f'dst-port=443 protocol=tcp action=accept')
            log.append(f'✅ Walled Garden IP liberado ({crm_ip}:80 e :443)')

            # DNS estático IPv4-only para o domínio do CRM: sem isso, um
            # cliente cujo dispositivo prefira IPv6 (comum no Android) recebe
            # o registro AAAA do CRM, tenta conectar por IPv6 e trava com
            # net::ERR_CONNECTION_ABORTED — o walled-garden acima só cobre o
            # IPv4. Uma entrada estática força o resolvedor do próprio
            # roteador (usado pelo cliente via DHCP dns-server=gateway) a
            # nunca oferecer AAAA para esse nome.
            _mt_exec(client,
                f'/ip dns static remove [find where name="{crm_host}"]')
            _mt_exec(client,
                f'/ip dns static add name="{crm_host}" address="{crm_ip}" '
                f'comment="CRM-hotspot-force-ipv4"')
            log.append(f'✅ DNS estático IPv4-only configurado para {crm_host}')
        except Exception as _e:
            log.append(f'⚠️ Walled Garden IP: não resolveu IP de {crm_host} — {_e}')

        # ── 9. Upload login.html via SFTP ────────────────────────────────────
        # Usa SFTP (mesma conexão SSH já aberta) em vez de /tool fetch HTTP.
        # Isso evita dependência de conectividade HTTP do MikroTik para o CRM
        # (DNS, firewall, timeout) — o arquivo é empurrado diretamente pelo CRM.
        import io as _io

        # Garante que html-directory aponte para o diretório padrão do hotspot
        _mt_exec(client,
            f'/ip hotspot profile set [find name="{profile_name}"] html-directory="{dir_name}"')

        # Deriva portal URL a partir do pixel_url (mesmo host/scheme)
        portal_url_for_html = pixel_url.replace(
            f'/clientes/hotspot/pixel/{hotspot.uuid}/',
            f'/clientes/hotspot/portal/{hotspot.uuid}/'
        )
        html_content = _gerar_login_html(hotspot, portal_url_for_html)
        html_bytes   = html_content.encode('utf-8')

        sftp_ok = False
        try:
            sftp = client.open_sftp()
            # IMPORTANTE: caminho relativo (sem "/flash/" na frente). O protocolo SFTP
            # não traduz "flash/" como alias da raiz (isso só acontece no parser do CLI
            # do RouterOS) — um path absoluto "/flash/<dir>/login.html" cria uma pasta
            # "flash" DIFERENTE e vazia, separada de "<dir>" (confirmado ao vivo em
            # 2026-07-10: `/file print` mostra "hotspot" e "flash/hotspot" como duas
            # árvores distintas — a segunda vazia). O html-directory do hotspot profile
            # referencia "<dir>/login.html" relativo à raiz do filesystem.
            remote_path = f'{dir_name}/login.html'
            sftp.putfo(_io.BytesIO(html_bytes), remote_path)
            sftp.close()
            sftp_ok = True
            log.append(f'✅ login.html enviado via SFTP → {remote_path} ({len(html_bytes)} bytes)')
        except Exception as _sftp_err:
            log.append(f'⚠️ SFTP falhou: {_sftp_err} — tentando /tool fetch como fallback')
            # Fallback: /tool fetch via IP direto
            _crm_host = urlparse(pixel_url).hostname
            try:
                import socket as _sock2
                _crm_ip = _sock2.gethostbyname(_crm_host)
            except Exception:
                _crm_ip = _crm_host
            login_html_url = f'http://{_crm_ip}/clientes/hotspot/login-html/{hotspot.uuid}/'
            fetch_out, fetch_err, fetch_rc = _mt_exec(client,
                f'/tool fetch url="{login_html_url}" '
                f'dst-path="{dir_name}/login.html" mode=http',
                timeout=30)
            fetch_combined = (fetch_out + fetch_err).strip()
            sftp_ok = fetch_rc == 0 and not any(
                k in fetch_combined.lower() for k in ('error', 'failure', 'failed', 'timed out'))
            if sftp_ok:
                log.append(f'✅ login.html baixado via fetch: {login_html_url}')
            else:
                log.append(f'⚠️ /tool fetch também falhou (rc={fetch_rc}): {fetch_combined or "sem detalhe"}')

        # Remove o arquivo órfão que uma versão anterior deste código gravava no
        # caminho ERRADO: "flash/<dir>/login.html" — uma árvore separada e vazia
        # que o hotspot nunca lê. O correto é "<dir>/login.html" (enviado acima).
        _mt_exec(client, f'/file remove [find where name="flash/{dir_name}/login.html"]')

        # Aguarda RouterOS indexar o arquivo
        import time as _time
        _time.sleep(2)

        # Confirma arquivo no diretório servido pelo hotspot
        fcheck, _, _ = _mt_exec(client,
            f'/file print where name~"{dir_name}/login.html"')
        data_lines = [l for l in fcheck.strip().splitlines() if 'login' in l and '.html' in l]
        if data_lines:
            log.append(f'   Arquivo confirmado: {data_lines[-1].strip()}')
        else:
            log.append(f'   ⚠️ login.html não encontrado no flash após upload')

        # Reiniciar hotspot para carregar novo login.html
        _mt_exec(client, f'/ip hotspot set [find name="{server_name}"] disabled=yes')
        _mt_exec(client, f'/ip hotspot set [find name="{server_name}"] disabled=no')
        log.append('✅ Hotspot reiniciado — novo portal ativo')

        hotspot.configurado_em = timezone.now()
        hotspot.save(update_fields=['configurado_em'])
        log.append('✅ Configuração concluída!')

        client.close()
        return True, log

    except Exception as exc:
        log.append(f'❌ Erro inesperado: {exc}')
        logger.exception('hotspot _aplicar_mikrotik error')
        try:
            client.close()
        except Exception:
            pass
        return False, log


# ─────────────────────────────────────────────────────────────────────────────
# CRUD — HotspotConfig
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def hotspot_listar(request, cliente_id):
    c = _cliente(request, cliente_id)
    configs = HotspotConfig.objects.filter(cliente=c).select_related('acesso')
    data = []
    for h in configs:
        data.append({
            'id': h.id,
            'nome': h.nome,
            'interface': h.interface,
            'gateway': h.gateway,
            'rate_limit_down': h.rate_limit_down,
            'rate_limit_up': h.rate_limit_up,
            'portal_titulo': h.portal_titulo,
            'cor_primaria': h.cor_primaria,
            'ativo': h.ativo,
            'acesso_id': h.acesso_id,
            'acesso_host': h.acesso.host if h.acesso else '',
            'banners_count': h.banners.filter(ativo=True).count(),
            'leads_count': h.leads.count(),
            'configurado_em': h.configurado_em.strftime('%d/%m/%Y %H:%M') if h.configurado_em else None,
            'uuid': str(h.uuid),
        })
    return JsonResponse({'ok': True, 'hotspots': data})


@login_required
def hotspot_detalhe(request, cliente_id, hotspot_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    return JsonResponse({'ok': True, 'hotspot': {
        'id': h.id,
        'nome': h.nome,
        'acesso_id': h.acesso_id,
        'interface': h.interface,
        'interface_fisica': h.interface_fisica,
        'network': h.network,
        'gateway': h.gateway,
        'pool_start': h.pool_start,
        'pool_end': h.pool_end,
        'dns_servidor': h.dns_servidor,
        'session_timeout': h.session_timeout,
        'idle_timeout': h.idle_timeout,
        'rate_limit_down': h.rate_limit_down,
        'rate_limit_up': h.rate_limit_up,
        'guest_usuario': h.guest_usuario,
        'guest_senha': h.guest_senha,
        'portal_titulo': h.portal_titulo,
        'portal_subtitulo': h.portal_subtitulo,
        'cor_primaria': h.cor_primaria,
        'cor_secundaria': h.cor_secundaria,
        'estilo_fundo': h.estilo_fundo,
        'cor_fundo': h.cor_fundo,
        'imagem_fundo_url': h.imagem_fundo.url if h.imagem_fundo else None,
        'ativo': h.ativo,
        'uuid': str(h.uuid),
        'configurado_em': h.configurado_em.strftime('%d/%m/%Y %H:%M') if h.configurado_em else None,
        'logo_url': h.logo.url if h.logo else None,
        'dhcp_controle_banda': h.dhcp_controle_banda,
        'dhcp_banda_limit': h.dhcp_banda_limit,
    }})


@login_required
@require_http_methods(['POST'])
def hotspot_salvar(request, cliente_id):
    c = _cliente(request, cliente_id)
    body = _json(request)
    hid = body.get('id')

    acesso = None
    if body.get('acesso_id'):
        acesso = get_object_or_404(Acesso, id=body['acesso_id'], cliente=c)

    defaults = {
        'acesso': acesso,
        'nome': body.get('nome', 'Hotspot').strip(),
        'interface': body.get('interface', 'bridge').strip(),
        'interface_fisica': body.get('interface_fisica', '').strip(),
        'network': body.get('network', '192.168.88.0/24').strip(),
        'gateway': body.get('gateway', '192.168.88.1').strip(),
        'pool_start': body.get('pool_start', '192.168.88.10').strip(),
        'pool_end': body.get('pool_end', '192.168.88.254').strip(),
        'dns_servidor': body.get('dns_servidor', '8.8.8.8').strip(),
        'session_timeout': int(body.get('session_timeout', 0)),
        'idle_timeout': int(body.get('idle_timeout', 30)),
        'rate_limit_down': body.get('rate_limit_down', '10M').strip(),
        'rate_limit_up': body.get('rate_limit_up', '5M').strip(),
        'guest_usuario': body.get('guest_usuario', 'guest').strip(),
        'guest_senha': body.get('guest_senha', 'wifi123').strip(),
        'portal_titulo': body.get('portal_titulo', 'WiFi Grátis').strip(),
        'portal_subtitulo': body.get('portal_subtitulo', '').strip(),
        'cor_primaria': body.get('cor_primaria', '#1a73e8').strip(),
        'cor_secundaria': body.get('cor_secundaria', '').strip(),
        'estilo_fundo': body.get('estilo_fundo', 'gradiente').strip() or 'gradiente',
        'cor_fundo': body.get('cor_fundo', '#0a0a0f').strip() or '#0a0a0f',
        'ativo': bool(body.get('ativo', True)),
        'dhcp_controle_banda': bool(body.get('dhcp_controle_banda', False)),
        'dhcp_banda_limit': body.get('dhcp_banda_limit', '10M/10M').strip() or '10M/10M',
    }

    if hid:
        h = get_object_or_404(HotspotConfig, id=hid, cliente=c)
        for k, v in defaults.items():
            setattr(h, k, v)
        h.save()
        created = False
    else:
        h = HotspotConfig.objects.create(cliente=c, **defaults)
        created = True

    return JsonResponse({'ok': True, 'id': h.id, 'created': created})


@login_required
@require_http_methods(['POST'])
def hotspot_deletar(request, cliente_id, hotspot_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    h.delete()
    return JsonResponse({'ok': True})


# ─────────────────────────────────────────────────────────────────────────────
# Apply to MikroTik
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def hotspot_aplicar(request, cliente_id, hotspot_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    portal = _portal_url(request, h)
    ok, log = _aplicar_mikrotik(h, portal)
    return JsonResponse({'ok': ok, 'log': log})


@login_required
def hotspot_preview_html(request, cliente_id, hotspot_id):
    """Retorna o login.html de redirecionamento para inspeção."""
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    portal = _portal_url(request, h)
    html = _gerar_login_html(h, portal)
    return HttpResponse(html, content_type='text/html; charset=utf-8')


# ─────────────────────────────────────────────────────────────────────────────
# Banners
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def hotspot_logo_upload(request, cliente_id, hotspot_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    img = request.FILES.get('logo')
    if not img:
        return JsonResponse({'ok': False, 'error': 'Nenhuma imagem enviada.'}, status=400)
    ext = img.name.rsplit('.', 1)[-1].lower()
    if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'):
        return JsonResponse({'ok': False, 'error': 'Formato inválido. Use JPG, PNG, WebP ou SVG.'}, status=400)
    # Remove logo anterior
    try:
        if h.logo and os.path.isfile(h.logo.path):
            os.remove(h.logo.path)
    except Exception:
        pass
    h.logo = img
    h.save(update_fields=['logo'])
    return JsonResponse({'ok': True, 'url': h.logo.url})


@login_required
@require_http_methods(['POST'])
def hotspot_logo_deletar(request, cliente_id, hotspot_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    try:
        if h.logo and os.path.isfile(h.logo.path):
            os.remove(h.logo.path)
    except Exception:
        pass
    h.logo = None
    h.save(update_fields=['logo'])
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['POST'])
def hotspot_fundo_upload(request, cliente_id, hotspot_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    img = request.FILES.get('imagem_fundo')
    if not img:
        return JsonResponse({'ok': False, 'error': 'Nenhuma imagem enviada.'}, status=400)
    ext = img.name.rsplit('.', 1)[-1].lower()
    if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
        return JsonResponse({'ok': False, 'error': 'Formato inválido. Use JPG, PNG, GIF ou WebP.'}, status=400)
    try:
        if h.imagem_fundo and os.path.isfile(h.imagem_fundo.path):
            os.remove(h.imagem_fundo.path)
    except Exception:
        pass
    h.imagem_fundo = img
    h.save(update_fields=['imagem_fundo'])
    return JsonResponse({'ok': True, 'url': h.imagem_fundo.url})


@login_required
@require_http_methods(['POST'])
def hotspot_fundo_deletar(request, cliente_id, hotspot_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    try:
        if h.imagem_fundo and os.path.isfile(h.imagem_fundo.path):
            os.remove(h.imagem_fundo.path)
    except Exception:
        pass
    h.imagem_fundo = None
    h.save(update_fields=['imagem_fundo'])
    return JsonResponse({'ok': True})


@login_required
def hotspot_banners(request, cliente_id, hotspot_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    data = [
        {
            'id': b.id,
            'url': b.imagem.url,
            'titulo': b.titulo,
            'subtitulo': b.subtitulo,
            'ordem': b.ordem,
            'ativo': b.ativo,
        }
        for b in h.banners.all()
    ]
    return JsonResponse({'ok': True, 'banners': data})


@login_required
@require_http_methods(['POST'])
def hotspot_banner_upload(request, cliente_id, hotspot_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    img = request.FILES.get('imagem')
    if not img:
        return JsonResponse({'ok': False, 'error': 'Nenhuma imagem enviada.'}, status=400)
    # Validate extension
    ext = img.name.rsplit('.', 1)[-1].lower()
    if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
        return JsonResponse({'ok': False, 'error': 'Formato inválido. Use JPG, PNG, GIF ou WebP.'}, status=400)
    ordem = h.banners.count()
    titulo = request.POST.get('titulo', '').strip()
    subtitulo = request.POST.get('subtitulo', '').strip()
    b = HotspotBanner.objects.create(hotspot=h, imagem=img, titulo=titulo, subtitulo=subtitulo, ordem=ordem)
    return JsonResponse({'ok': True, 'id': b.id, 'url': b.imagem.url})


@login_required
@require_http_methods(['POST'])
def hotspot_banner_deletar(request, cliente_id, hotspot_id, banner_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    b = get_object_or_404(HotspotBanner, id=banner_id, hotspot=h)
    # Remove physical file
    try:
        if b.imagem and os.path.isfile(b.imagem.path):
            os.remove(b.imagem.path)
    except Exception:
        pass
    b.delete()
    return JsonResponse({'ok': True})


# ─────────────────────────────────────────────────────────────────────────────
# Leads
# ─────────────────────────────────────────────────────────────────────────────

LEADS_POR_PAGINA = 50


@login_required
def hotspot_leads(request, cliente_id, hotspot_id):
    """
    Lista os leads de UM dia por vez (padrão: hoje), paginado — antes trazia
    tudo numa lista só, cortada em 500 registros sem paginação nem filtro;
    com ~80 leads/dia num hotspot ativo isso estourava o corte em poucos
    dias e escondia leads antigos silenciosamente (`total` mostrava a
    contagem real, mas a tabela nunca passava de 500 linhas).
    """
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)

    data_str = (request.GET.get('data') or '').strip()
    if data_str:
        try:
            dia = datetime.strptime(data_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'ok': False, 'erro': 'Data inválida.'}, status=400)
    else:
        dia = timezone.localdate()

    qs = h.leads.filter(criado_em__date=dia).order_by('-criado_em')

    try:
        pagina = max(1, int(request.GET.get('pagina', 1)))
    except (TypeError, ValueError):
        pagina = 1

    paginator = Paginator(qs, LEADS_POR_PAGINA)
    page_obj = paginator.get_page(pagina)

    data = [
        {
            'id': lead.id,
            'nome': lead.nome,
            'telefone': lead.telefone,
            'data_nascimento': lead.data_nascimento.strftime('%d/%m/%Y') if lead.data_nascimento else '',
            'mac': lead.mac,
            'ip_cliente': lead.ip_cliente,
            'criado_em': lead.criado_em.strftime('%d/%m/%Y %H:%M'),
        }
        for lead in page_obj.object_list
    ]

    # Dias com pelo menos 1 lead — alimenta o seletor de data no front
    # (mostra só dias com dado, sem precisar adivinhar/rolar calendário à toa).
    dias_disponiveis = list(
        h.leads.annotate(_dia=TruncDate('criado_em'))
        .values_list('_dia', flat=True).distinct().order_by('-_dia')
    )

    return JsonResponse({
        'ok': True,
        'leads': data,
        'data': dia.isoformat(),
        'total_dia': paginator.count,
        'pagina': page_obj.number,
        'total_paginas': paginator.num_pages,
        'tem_anterior': page_obj.has_previous(),
        'tem_proxima': page_obj.has_next(),
        'dias_disponiveis': [d.isoformat() for d in dias_disponiveis],
        'total_geral': h.leads.count(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Public portal — página de login hospedada no CRM, acessível via walled-garden
# ─────────────────────────────────────────────────────────────────────────────

def _portal_page_html(hotspot, link, mac, ip, orig, request):
    """Gera a página do portal de captação de lead hospedada no CRM."""
    scheme = 'https' if request.is_secure() else 'http'
    host   = request.get_host()
    submit_url = f'{scheme}://{host}/clientes/hotspot/portal/{hotspot.uuid}/conectar/'

    cor       = hotspot.cor_primaria or '#6366f1'
    titulo    = hotspot.portal_titulo or 'WiFi Grátis'
    subtitulo = hotspot.portal_subtitulo or 'Preencha seus dados para se conectar'

    # Logo — usa o mesmo scheme da requisição
    logo_url = f'{scheme}://{host}{hotspot.logo.url}' if hotspot.logo else None

    cor_dark = hotspot.cor_secundaria or cor

    # Fundo da página de login — 3 estilos: gradiente (padrão, comportamento
    # histórico), solido, imagem. cor_fundo default reproduz o valor
    # hardcoded anterior (#0a0a0f), então configs existentes ficam idênticas.
    cor_fundo    = hotspot.cor_fundo or '#0a0a0f'
    estilo_fundo = hotspot.estilo_fundo or 'gradiente'
    imagem_fundo_url = f'{scheme}://{host}{hotspot.imagem_fundo.url}' if hotspot.imagem_fundo else None

    if estilo_fundo == 'imagem' and imagem_fundo_url:
        bg_css = (
            f"background:url('{imagem_fundo_url}') center/cover no-repeat, {cor_fundo};"
        )
        bg_overlay_css = (
            f"background:linear-gradient(180deg,{cor_fundo}99,{cor_fundo}cc);"
        )
    elif estilo_fundo == 'solido':
        bg_css = f"background:{cor_fundo};"
        bg_overlay_css = "background:none;"
    else:
        bg_css = f"background:{cor_fundo};"
        bg_overlay_css = (
            f"background:radial-gradient(ellipse at 20% 50%,{cor}22 0%,transparent 60%),"
            f"radial-gradient(ellipse at 80% 20%,{cor}18 0%,transparent 55%),"
            f"radial-gradient(ellipse at 50% 90%,{cor_dark}22 0%,transparent 60%);"
        )

    # Banners → slideshow do splash (mostrado ANTES da página de login)
    slides = []
    for i, b in enumerate(hotspot.banners.filter(ativo=True).order_by('ordem', 'criado_em')):
        active = ' active' if i == 0 else ''
        img_url = f'{scheme}://{host}{b.imagem.url}'
        slides.append(f'<div class="slide{active}" style="background-image:url(\'{img_url}\')"></div>')
    has_slides = bool(slides)
    slides_str = '\n'.join(slides) if slides else ''

    # Logo centralizada no topo do card (ou ícone de WiFi como fallback)
    logo_html = (f'<img class="empresa-logo" src="{logo_url}" alt="Logo">'
                 if logo_url else '<div class="wifi-ring">&#x1F4F6;</div>')

    # Splash de banners (só existe se houver banner ativo). Card de login começa
    # oculto enquanto o splash é exibido.
    if has_slides:
        splash_html = (
            '<div class="splash" id="splash">\n'
            '  <div class="splash-slides">\n'
            f'{slides_str}\n'
            '    <div class="splash-grad"></div>\n'
            '  </div>\n'
            '  <div class="splash-dots" id="dots"></div>\n'
            '  <button class="splash-btn" type="button" id="splashBtn">'
            'Continuar para o WiFi &#x2192;</button>\n'
            '</div>'
        )
        card_style = ' style="display:none"'
    else:
        splash_html = ''
        card_style = ''

    # Valores dos campos ocultos devem ser HTML-escaped: URLs contêm & que
    # seria interpretado como início de entidade HTML e quebraria o value=""
    link_h = _html.escape(link, quote=True)
    mac_h  = _html.escape(mac,  quote=True)
    ip_h   = _html.escape(ip,   quote=True)
    orig_h = _html.escape(orig, quote=True)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>{titulo}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}}
:root{{--c:{cor};--cr:color-mix(in srgb,{cor} 80%,#000)}}
html,body{{height:100%;overflow:hidden}}
body{{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  {bg_css}
  display:flex;align-items:center;justify-content:center;
  position:relative;
}}

/* Fundo animado */
.bg{{
  position:fixed;inset:0;z-index:0;
  {bg_overlay_css}
  animation:bgpulse 8s ease-in-out infinite alternate;
}}
@keyframes bgpulse{{
  0%{{background-position:0% 50%}}
  100%{{background-position:100% 50%}}
}}

/* Card principal */
.card{{
  position:relative;z-index:1;
  width:min(420px,100vw);
  max-height:100dvh;
  overflow-y:auto;
  -webkit-overflow-scrolling:touch;
  border-radius:24px;
  background:rgba(15,15,25,0.85);
  backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);
  border:1px solid rgba(255,255,255,0.08);
  box-shadow:0 32px 80px rgba(0,0,0,0.7),0 0 0 1px rgba(255,255,255,0.04) inset;
  animation:cardIn .5s cubic-bezier(.22,1,.36,1) both;
}}
@keyframes cardIn{{from{{opacity:0;transform:translateY(24px)}}to{{opacity:1;transform:none}}}}

/* ── Splash de banners (mostrado ANTES do login) ───────────────── */
.splash{{
  position:fixed;inset:0;z-index:10;
  display:flex;flex-direction:column;
  background:#0a0a0f;
  animation:splashIn .4s ease both;
}}
.splash.hide{{animation:splashOut .5s ease forwards}}
@keyframes splashIn{{from{{opacity:0}}to{{opacity:1}}}}
@keyframes splashOut{{to{{opacity:0;visibility:hidden}}}}
.splash-slides{{position:relative;flex:1;overflow:hidden}}
.slide{{
  position:absolute;inset:0;opacity:0;
  background-size:contain;background-repeat:no-repeat;background-position:center;
  transition:opacity .9s ease;
}}
.slide.active{{opacity:1}}
.splash-grad{{
  position:absolute;inset:0;z-index:1;pointer-events:none;
  background:linear-gradient(to bottom,transparent 60%,rgba(10,10,15,.9) 100%);
}}
.splash-dots{{
  position:absolute;bottom:96px;left:0;right:0;z-index:2;
  display:flex;justify-content:center;gap:6px;
}}
.dot{{
  width:7px;height:7px;border-radius:50%;
  background:rgba(255,255,255,.35);transition:all .3s;cursor:pointer;
}}
.dot.active{{width:20px;border-radius:4px;background:#fff}}
.splash-btn{{
  position:absolute;left:20px;right:20px;bottom:28px;z-index:3;
  height:54px;border:none;border-radius:14px;
  background:linear-gradient(135deg,{cor},{cor_dark});
  color:#fff;font-size:1rem;font-weight:700;letter-spacing:.02em;cursor:pointer;
  box-shadow:0 6px 24px {cor}55;
}}
.splash-btn:active{{transform:translateY(1px)}}

/* ── Cabeçalho do card de login (logo centralizada no topo) ─────── */
.slide{{background-color:#0a0a0f}}
.login-header{{text-align:center;padding:34px 24px 6px}}
.empresa-logo{{
  display:block;margin:0 auto 18px;
  max-height:100px;max-width:78%;
  object-fit:contain;
  filter:drop-shadow(0 4px 14px rgba(0,0,0,.55));
}}
.wifi-ring{{
  width:76px;height:76px;border-radius:22px;margin:0 auto 18px;
  background:rgba(255,255,255,0.08);
  border:1px solid rgba(255,255,255,0.16);
  display:flex;align-items:center;justify-content:center;
  font-size:36px;
}}
.login-header h2{{color:#fff;font-size:1.5rem;font-weight:700;line-height:1.2}}
.login-header p{{color:rgba(255,255,255,.6);font-size:.9rem;margin-top:6px}}

/* Form area */
.form-area{{padding:20px 20px 20px}}

/* Inputs com ícone flutuante */
.field{{position:relative;margin-bottom:14px}}
.field-icon{{
  position:absolute;left:14px;top:50%;transform:translateY(-50%);
  font-size:16px;opacity:.45;pointer-events:none;
}}
.field input{{
  width:100%;height:52px;
  padding:0 14px 0 42px;
  background:rgba(255,255,255,0.05);
  border:1.5px solid rgba(255,255,255,0.1);
  border-radius:14px;
  color:#f1f5f9;font-size:.95rem;
  outline:none;transition:border-color .2s,background .2s;
  -webkit-appearance:none;appearance:none;
}}
.field input::placeholder{{color:rgba(255,255,255,.3)}}
.field input:focus{{
  border-color:{cor};
  background:rgba(255,255,255,0.08);
  box-shadow:0 0 0 3px {cor}22;
}}
.field input[type=date]{{color-scheme:dark}}
.field-label{{
  position:absolute;left:42px;top:50%;transform:translateY(-50%);
  font-size:.85rem;color:rgba(255,255,255,.3);
  pointer-events:none;transition:all .2s;
}}
.field input:focus ~ .field-label,
.field input:not(:placeholder-shown) ~ .field-label{{
  top:8px;font-size:.68rem;color:{cor};letter-spacing:.03em;transform:none;
}}

/* Botão */
.btn-wrap{{margin-top:6px}}
.btn{{
  width:100%;height:54px;
  background:linear-gradient(135deg,{cor},{cor_dark});
  border:none;border-radius:14px;
  color:#fff;font-size:1rem;font-weight:700;letter-spacing:.02em;
  cursor:pointer;position:relative;overflow:hidden;
  display:flex;align-items:center;justify-content:center;gap:10px;
  transition:transform .15s,box-shadow .2s;
  box-shadow:0 6px 24px {cor}55;
}}
.btn:hover{{transform:translateY(-1px);box-shadow:0 10px 32px {cor}66}}
.btn:active{{transform:translateY(0);box-shadow:0 4px 16px {cor}44}}
.btn:disabled{{opacity:.5;cursor:not-allowed;transform:none}}
.btn::after{{
  content:"";position:absolute;inset:0;
  background:linear-gradient(135deg,rgba(255,255,255,.15),transparent);
  border-radius:inherit;
}}
.spin{{
  width:20px;height:20px;border:2.5px solid rgba(255,255,255,.25);
  border-top-color:#fff;border-radius:50%;
  animation:spin .7s linear infinite;display:none;
}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}

/* Rodapé */
.footer{{
  text-align:center;margin-top:14px;
  font-size:.72rem;color:rgba(255,255,255,.2);line-height:1.5;
}}
.footer a{{color:rgba(255,255,255,.35);text-decoration:none}}
</style>
</head>
<body>
<div class="bg"></div>

<!-- Banners exibidos ANTES da página de login (slideshow se >1) -->
{splash_html}

<div class="card" id="card"{card_style}>
  <!-- Cabeçalho: logo centralizada no topo -->
  <div class="login-header">
    {logo_html}
    <h2>{titulo}</h2>
    <p>{subtitulo}</p>
  </div>

  <!-- Formulário -->
  <div class="form-area">
    <form id="hf" method="post" action="{submit_url}" onsubmit="return onSubmit()">
      <input type="hidden" name="link"  value="{link_h}">
      <input type="hidden" name="mac"   value="{mac_h}">
      <input type="hidden" name="ip"    value="{ip_h}">
      <input type="hidden" name="orig"  value="{orig_h}">

      <div class="field">
        <span class="field-icon">&#x1F464;</span>
        <input type="text" name="nome" id="f_nome"
               placeholder=" " required autocomplete="name">
        <label class="field-label" for="f_nome">Nome completo</label>
      </div>

      <div class="field">
        <span class="field-icon">&#x1F4F1;</span>
        <input type="tel" name="telefone" id="f_tel"
               placeholder=" " required inputmode="tel" autocomplete="tel">
        <label class="field-label" for="f_tel">WhatsApp / Telefone</label>
      </div>

      <div class="field">
        <span class="field-icon">&#x1F382;</span>
        <input type="date" name="data_nascimento" id="f_nasc"
               placeholder=" ">
        <label class="field-label" for="f_nasc">Data de nascimento</label>
      </div>

      <div class="btn-wrap">
        <button class="btn" type="submit" id="btn">
          <span id="btn-txt">Conectar ao WiFi</span>
          <span class="spin" id="spin"></span>
        </button>
      </div>
    </form>

    <div class="footer">
      Ao conectar você concorda com os termos de uso desta rede.<br>
      Seus dados são protegidos e não serão compartilhados.
    </div>
  </div>
</div>

<script>
(function(){{
  // Limite data de nascimento
  var dn=document.getElementById('f_nasc');
  if(dn)dn.max=new Date().toISOString().split('T')[0];

  // Slideshow
  var slides=document.querySelectorAll('.slide');
  var dotsEl=document.getElementById('dots');
  var cur=0,timer;
  if(slides.length>1 && dotsEl){{
    slides.forEach(function(_,i){{
      var d=document.createElement('span');
      d.className='dot'+(i===0?' active':'');
      d.addEventListener('click',function(){{goTo(i);restart();}});
      dotsEl.appendChild(d);
    }});
    timer=setInterval(function(){{goTo((cur+1)%slides.length);}},4500);
  }}
  function goTo(n){{
    slides[cur].classList.remove('active');
    var ds=dotsEl?dotsEl.querySelectorAll('.dot'):[];
    if(ds[cur])ds[cur].classList.remove('active');
    cur=n;
    slides[cur].classList.add('active');
    if(ds[cur])ds[cur].classList.add('active');
  }}
  function restart(){{clearInterval(timer);timer=setInterval(function(){{goTo((cur+1)%slides.length);}},4500);}}

  // Splash → revela o card de login ao continuar
  var splash=document.getElementById('splash');
  var card=document.getElementById('card');
  var sb=document.getElementById('splashBtn');
  if(sb&&splash&&card){{
    sb.addEventListener('click',function(){{
      clearInterval(timer);
      splash.classList.add('hide');
      card.style.display='';
      setTimeout(function(){{splash.style.display='none';}},500);
    }});
  }}

  // Máscara telefone
  document.getElementById('f_tel').addEventListener('input',function(){{
    var v=this.value.replace(/[^0-9]/g,'');
    if(v.length>11)v=v.slice(0,11);
    if(v.length>7)v='('+v.slice(0,2)+') '+v.slice(2,7)+'-'+v.slice(7);
    else if(v.length>2)v='('+v.slice(0,2)+') '+v.slice(2);
    this.value=v;
  }});
}})();

function onSubmit(){{
  var nome=document.getElementById('f_nome').value.trim();
  var tel=document.getElementById('f_tel').value.trim();
  if(!nome||!tel)return false;
  var btn=document.getElementById('btn');
  btn.disabled=true;
  document.getElementById('btn-txt').textContent='Conectando...';
  document.getElementById('spin').style.display='block';
  return true;
}}
</script>
</body>
</html>"""


@csrf_exempt
def hotspot_login_html_publico(request, hotspot_uuid):
    """
    Endpoint público: retorna o login.html de redirect para o MikroTik baixar
    via /tool fetch. Não requer autenticação.
    """
    try:
        h = HotspotConfig.objects.get(uuid=hotspot_uuid, ativo=True)
    except HotspotConfig.DoesNotExist:
        return HttpResponse('', status=404)
    scheme = 'https' if request.is_secure() else 'http'
    portal = f'{scheme}://{request.get_host()}/clientes/hotspot/portal/{h.uuid}/'
    html = _gerar_login_html(h, portal)
    resp = HttpResponse(html, content_type='text/html; charset=utf-8')
    resp['Cache-Control'] = 'no-store'
    return resp


@csrf_exempt
def hotspot_portal(request, hotspot_uuid):
    """Portal de login do hotspot — acessível sem autenticação (walled-garden)."""
    try:
        h = HotspotConfig.objects.get(uuid=hotspot_uuid, ativo=True)
    except HotspotConfig.DoesNotExist:
        return HttpResponse('Portal não encontrado.', status=404)

    link = request.GET.get('link', '')
    mac  = request.GET.get('mac', '')
    ip   = request.GET.get('ip', '')
    orig = request.GET.get('orig', '')

    html = _portal_page_html(h, link, mac, ip, orig, request)
    return HttpResponse(html, content_type='text/html; charset=utf-8')


@csrf_exempt
@require_http_methods(['POST'])
def hotspot_portal_conectar(request, hotspot_uuid):
    """
    Recebe o formulário do portal, salva o lead e retorna uma página
    que auto-submete as credenciais guest para o MikroTik autenticar o usuário.
    """
    try:
        h = HotspotConfig.objects.get(uuid=hotspot_uuid, ativo=True)
    except HotspotConfig.DoesNotExist:
        return HttpResponse('Hotspot não encontrado.', status=404)

    link  = request.POST.get('link', '').strip()
    orig  = request.POST.get('orig', '').strip()
    mac   = request.POST.get('mac', '').strip()
    ip_c  = request.POST.get('ip', '').strip()
    nome  = request.POST.get('nome', '').strip()
    tel   = request.POST.get('telefone', '').strip()
    nasc  = request.POST.get('data_nascimento', '').strip() or None

    if nome or tel:
        try:
            from datetime import date
            nasc_date = date.fromisoformat(nasc) if nasc else None
        except ValueError:
            nasc_date = None
        try:
            HotspotLead.objects.create(
                hotspot=h,
                nome=nome[:100],
                telefone=tel[:20],
                data_nascimento=nasc_date,
                mac=mac[:17],
                ip_cliente=ip_c[:15],
            )
        except Exception as exc:
            logger.debug('hotspot_portal_conectar lead error: %s', exc)

    # Página que auto-submete ao MikroTik para autenticar
    # link = $(link-login) — inclui dst= para redirect pós-auth.
    # Se vazio (captive portal browser não executou JS no login.html e o
    # meta-refresh redirecionou sem parâmetros), cai para a URL padrão do
    # gateway, que é sempre acessível pelo cliente hotspot.
    raw_link = link if link else f'http://{h.gateway}/login'

    # Autentica no MikroTik por NAVEGAÇÃO (GET), não por form-submit.
    # Motivo: no desktop o portal do CRM é carregado por HTTPS (crm.tomich.com.br
    # sobe pra HTTPS). Enviar um FORMULÁRIO de uma página HTTPS para um destino
    # HTTP (o login do MikroTik, http://gateway/login, que não tem TLS) dispara o
    # aviso "As informações que você está prestes a enviar não estão protegidas".
    # Uma navegação top-level HTTPS→HTTP (window.location) é permitida sem aviso —
    # e o hotspot (login-by=http-pap) aceita username/password via query string.
    from urllib.parse import urlsplit as _urlsplit, urlencode as _urlencode
    _sp = _urlsplit(raw_link)
    if _sp.netloc:
        login_base = f'{_sp.scheme or "http"}://{_sp.netloc}{_sp.path or "/login"}'
    else:
        login_base = f'http://{h.gateway}/login'
    login_full = login_base + '?' + _urlencode({
        'username': h.guest_usuario,
        'password': h.guest_senha,
        'dst': orig or '',
        'popup': 'true',
    })
    login_js   = login_full.replace('\\', '\\\\').replace("'", "\\'")
    login_href = _html.escape(login_full, quote=True)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;
     min-height:100vh;background:#0f172a;color:#fff;margin:0}}
.msg{{text-align:center}}
.spinner{{width:40px;height:40px;border:3px solid rgba(255,255,255,.2);
         border-top-color:{h.cor_primaria or '#1a73e8'};border-radius:50%;
         animation:spin .7s linear infinite;margin:0 auto 16px}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
a{{color:#93c5fd}}
</style>
</head>
<body>
<div class="msg">
  <div class="spinner"></div>
  <p>Conectando ao WiFi...</p>
  <p><a id="mlink" href="{login_href}" style="display:none">Clique aqui se não conectar</a></p>
</div>
<script>
setTimeout(function(){{ window.location.replace('{login_js}'); }}, 800);
setTimeout(function(){{ var a=document.getElementById('mlink'); if(a) a.style.display='inline'; }}, 3500);
</script>
<noscript><a href="{login_href}">Clique aqui para conectar</a></noscript>
</body>
</html>"""
    return HttpResponse(html, content_type='text/html; charset=utf-8')


# ─────────────────────────────────────────────────────────────────────────────
# Public endpoint — lead capture pixel (called from MikroTik's login.html)
# No login required; returns 1×1 transparent GIF
# ─────────────────────────────────────────────────────────────────────────────

_TRANSPARENT_GIF = (
    b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00'
    b'\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00'
    b'\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
)


@csrf_exempt
def hotspot_lead_pixel(request, hotspot_uuid):
    """
    Public pixel endpoint — accessed from within the hotspot network
    (unauthenticated users; added to MikroTik's walled-garden automatically).
    """
    try:
        h = HotspotConfig.objects.get(uuid=hotspot_uuid, ativo=True)
        nome      = request.GET.get('nome', '').strip()
        telefone  = request.GET.get('telefone', '').strip()
        cpf       = request.GET.get('cpf', '').strip()
        mac       = request.GET.get('mac', '').strip()
        ip_cli    = request.GET.get('ip', '').strip()

        if nome or telefone:
            HotspotLead.objects.create(
                hotspot=h,
                nome=nome[:100],
                telefone=telefone[:20],
                cpf=cpf[:14],
                mac=mac[:17],
                ip_cliente=ip_cli[:15],
            )
    except Exception as exc:
        logger.debug('hotspot_lead_pixel error: %s', exc)

    resp = HttpResponse(_TRANSPARENT_GIF, content_type='image/gif')
    resp['Cache-Control'] = 'no-store'
    resp['Access-Control-Allow-Origin'] = '*'
    return resp
