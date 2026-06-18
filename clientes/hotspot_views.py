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

import paramiko
from django.contrib.auth.decorators import login_required
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

    Estratégia de redirect em camadas (da mais compatível para a mais rica):
      1. <meta http-equiv="refresh"> — funciona sem JS (captive portal browsers
         do iOS/Android às vezes bloqueiam scripts na janela de detecção)
      2. window.location.replace() — JS padrão, passa todos os parâmetros
         URL-encoded para que o portal saiba o $(link-login) do MikroTik
      3. <a> visível como último recurso
    """
    # Força HTTP: clientes não têm HTTPS no walled-garden antes de autenticar
    http_portal = portal_url.replace('https://', 'http://', 1).rstrip('/')

    # Para o meta-refresh usamos o portal sem parâmetros — evita problemas com
    # caracteres especiais em $(link-login) que é uma URL completa. O portal
    # funciona sem esses params; o hotspot_portal_conectar usa h.gateway como
    # fallback quando link= está vazio.
    return (
        '<!DOCTYPE html>\n'
        '<html>\n'
        '<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta http-equiv="refresh" content="0;url=' + http_portal + '/">\n'
        '<script>\n'
        'var p="' + http_portal + '/";\n'
        'var q="?link="+encodeURIComponent("$(link-login)")\n'
        '     +"&mac="+encodeURIComponent("$(mac)")\n'
        '     +"&ip="+encodeURIComponent("$(ip)")\n'
        '     +"&orig="+encodeURIComponent("$(link-orig)");\n'
        'window.location.replace(p+q);\n'
        '</script>\n'
        '</head>\n'
        '<body>\n'
        '<p>Redirecionando para o portal...</p>\n'
        '<p><a href="' + http_portal + '/">Clique aqui se não for redirecionado automaticamente</a></p>\n'
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
            port_exists = _mt_count(client,
                f'/interface bridge port print count-only where bridge="{bridge_name}" interface="{iface}"')
            if port_exists == 0:
                bp_out, bp_err, bp_rc = _mt_exec(client,
                    f'/interface bridge port add bridge="{bridge_name}" interface="{iface}"')
                bp_ok = _mt_output_ok(bp_out, bp_err, bp_rc)
                log.append(f'{"✅" if bp_ok else "⚠️"} Bridge port {iface} → {bridge_name}: {(bp_out+bp_err).strip() or "ok"}')
            else:
                log.append(f'✅ Bridge port {iface} já está em {bridge_name}')
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
        ok, lbl, out = _set_or_add(
            f'/ip dhcp-server network print count-only where address="{hotspot.network}"',
            f'/ip dhcp-server network set [find address="{hotspot.network}"] '
            f'gateway={hotspot.gateway} dns-server={hotspot.dns_servidor}',
            f'/ip dhcp-server network add address={hotspot.network} '
            f'gateway={hotspot.gateway} dns-server={hotspot.dns_servidor}',
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
        # O hotspot proxy precisa resolver hostnames (walled-garden, pixel).
        # Se o roteador não tiver DNS configurado, as entradas hostname do
        # walled-garden não funcionam — configura 8.8.8.8 se servers estiver vazio.
        dns_out, _, _ = _mt_exec(client, '/ip dns print')
        if '8.8.8.8' not in dns_out:
            _mt_exec(client, '/ip dns set servers=8.8.8.8 allow-remote-requests=yes')
            log.append('✅ DNS configurado (8.8.8.8)')
        else:
            log.append('✅ DNS já configurado')

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
            _mt_exec(client,
                f'/ip hotspot walled-garden ip remove [find where dst-address="{crm_ip}/32"]')
            _mt_exec(client,
                f'/ip hotspot walled-garden ip add dst-address="{crm_ip}/32" '
                f'dst-port=80 protocol=tcp action=accept')
            _mt_exec(client,
                f'/ip hotspot walled-garden ip add dst-address="{crm_ip}/32" '
                f'dst-port=443 protocol=tcp action=accept')
            log.append(f'✅ Walled Garden IP liberado ({crm_ip}:80 e :443)')
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
            # literalmente chamada "flash", que o módulo de Hotspot nunca lê. O
            # html-directory do hotspot profile referencia "<dir>/login.html" relativo
            # à raiz do sistema de arquivos, que é exatamente o working directory do
            # SFTP do RouterOS.
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

        # Remove o arquivo órfão de uma versão anterior deste código, que gravava
        # incorretamente em /flash/<dir>/ (caminho absoluto — nunca lido pelo hotspot)
        _mt_exec(client, f'/file remove [find where name="flash/{dir_name}/login.html"]')

        # Aguarda RouterOS indexar o arquivo
        import time as _time
        _time.sleep(2)

        # Confirma arquivo no flash
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

@login_required
def hotspot_leads(request, cliente_id, hotspot_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    qs = h.leads.order_by('-criado_em')[:500]
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
        for lead in qs
    ]
    return JsonResponse({'ok': True, 'leads': data, 'total': h.leads.count()})


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

    cor_dark = cor

    # Banners
    slides = []
    for i, b in enumerate(hotspot.banners.filter(ativo=True).order_by('ordem', 'criado_em')):
        active = ' active' if i == 0 else ''
        img_url = f'{scheme}://{host}{b.imagem.url}'
        slides.append(f'<div class="slide{active}" style="background-image:url(\'{img_url}\')"></div>')
    has_slides = bool(slides)
    slides_str = '\n'.join(slides) if slides else ''

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
  background:#0a0a0f;
  display:flex;align-items:center;justify-content:center;
  position:relative;
}}

/* Fundo animado */
.bg{{
  position:fixed;inset:0;z-index:0;
  background:radial-gradient(ellipse at 20% 50%,{cor}22 0%,transparent 60%),
             radial-gradient(ellipse at 80% 20%,{cor}18 0%,transparent 55%),
             radial-gradient(ellipse at 50% 90%,#818cf822 0%,transparent 60%),
             #0a0a0f;
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

/* Banner slideshow */
.banner{{
  position:relative;height:200px;border-radius:24px 24px 0 0;overflow:hidden;
  background:linear-gradient(135deg,{cor}44,#818cf844);
}}
.slide{{
  position:absolute;inset:0;opacity:0;
  background-size:cover;background-position:center;
  transition:opacity .9s ease;
}}
.slide.active{{opacity:1}}
.banner-overlay{{
  position:absolute;inset:0;
  background:linear-gradient(to bottom,rgba(0,0,0,.1) 0%,rgba(0,0,0,.5) 100%);
  z-index:1;
}}
.banner-logo{{
  position:absolute;bottom:0;left:0;right:0;z-index:2;
  padding:16px 20px;display:flex;align-items:flex-end;gap:12px;
}}
.wifi-ring{{
  width:48px;height:48px;border-radius:14px;
  background:rgba(255,255,255,0.12);
  backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
  border:1px solid rgba(255,255,255,0.2);
  display:flex;align-items:center;justify-content:center;
  font-size:22px;flex-shrink:0;
}}
.empresa-logo{{
  display:block;max-height:56px;max-width:140px;
  object-fit:contain;
  filter:drop-shadow(0 2px 6px rgba(0,0,0,.5));
  border-radius:8px;
  margin-bottom:6px;
}}
.banner-text h2{{color:#fff;font-size:1.25rem;font-weight:700;line-height:1.2;text-shadow:0 1px 4px rgba(0,0,0,.4)}}
.banner-text p{{color:rgba(255,255,255,.75);font-size:.8rem;margin-top:2px;text-shadow:0 1px 3px rgba(0,0,0,.3)}}
.dots{{
  position:absolute;top:12px;right:14px;z-index:3;
  display:flex;gap:5px;
}}
.dot{{
  width:6px;height:6px;border-radius:50%;
  background:rgba(255,255,255,.35);transition:all .3s;cursor:pointer;
}}
.dot.active{{width:18px;border-radius:3px;background:#fff}}

/* Form area */
.form-area{{padding:24px 20px 20px}}

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
<div class="card">
  <!-- Banner -->
  <div class="banner" id="banner">
{slides_str}
    <div class="banner-overlay"></div>
    <div class="banner-logo">
      {'<img class="empresa-logo" src="' + logo_url + '" alt="Logo">' if logo_url else '<div class="wifi-ring">&#x1F4F6;</div>'}
      <div class="banner-text">
        <h2>{titulo}</h2>
        <p>{subtitulo}</p>
      </div>
    </div>
    {'<div class="dots" id="dots"></div>' if has_slides else ''}
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
    safe_link  = raw_link.replace('"', '%22').replace("'", '%27')
    safe_orig  = orig.replace('"', '%22').replace("'", '%27') if orig else ''
    guest_user = h.guest_usuario.replace('"', '')
    guest_pass = h.guest_senha.replace('"', '')

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
</style>
</head>
<body>
<div class="msg">
  <div class="spinner"></div>
  <p>Conectando ao WiFi...</p>
</div>
<form id="mt" method="post" action="{safe_link}" style="display:none">
  <input type="hidden" name="username" value="{guest_user}">
  <input type="hidden" name="password" value="{guest_pass}">
  <input type="hidden" name="dst"      value="{safe_orig}">
  <input type="hidden" name="popup"    value="true">
</form>
<script>
setTimeout(function(){{document.getElementById('mt').submit();}},1200);
</script>
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
