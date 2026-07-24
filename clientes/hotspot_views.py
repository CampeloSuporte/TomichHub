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
from io import BytesIO

import paramiko
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db.models import Q
from django.db.models.functions import TruncDate
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from PIL import Image, ImageChops

from .models import (
    Acesso, Cliente, ClienteIntegracaoDisparo, DISPARO_VARIAVEIS_EXEMPLO,
    HotspotBanner, HotspotConfig, HotspotInterface, HotspotLead,
)

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


def _autocrop_logo(img_file, ext):
    """Recorta a margem vazia (transparente ou de cor sólida) ao redor do
    conteúdo real do logo antes de salvar.

    O cabeçalho do portal exibe o logo com `object-fit:contain` dentro de
    uma caixa de altura fixa (~100px) — se o arquivo enviado tiver uma
    "moldura" grande de espaço em branco/transparente em volta do símbolo
    (comum em exports de ferramentas de design), esse espaço vazio também
    é encaixado na caixa, sobrando pouquíssima altura pro logo em si, que
    aparece minúsculo mesmo com a caixa de tamanho normal.

    Retorna um `ContentFile` PNG já recortado, ou `None` se não for
    possível/necessário recortar (SVG, GIF animado, erro ao processar, ou
    o conteúdo já ocupa quase toda a imagem).
    """
    if ext not in ('png', 'jpg', 'jpeg', 'webp'):
        return None
    try:
        img_file.seek(0)
        im = Image.open(img_file)
        im.load()

        if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
            im = im.convert('RGBA')
            bbox = im.split()[-1].getbbox()  # bbox do canal alpha (área visível)
        else:
            im = im.convert('RGB')
            fundo = Image.new('RGB', im.size, im.getpixel((0, 0)))
            bbox = ImageChops.difference(im, fundo).getbbox()

        if not bbox:
            return None

        largura, altura = im.size
        bx0, by0, bx1, by1 = bbox
        # Recorte já ocupa quase tudo — não vale o esforço/risco de reprocessar.
        if (bx1 - bx0) >= largura * 0.97 and (by1 - by0) >= altura * 0.97:
            return None

        # Margem de respiro em volta do conteúdo recortado.
        margem = int(max(bx1 - bx0, by1 - by0) * 0.06)
        bx0 = max(0, bx0 - margem)
        by0 = max(0, by0 - margem)
        bx1 = min(largura, bx1 + margem)
        by1 = min(altura, by1 + margem)

        recortada = im.crop((bx0, by0, bx1, by1))
        buf = BytesIO()
        recortada.save(buf, format='PNG')
        buf.seek(0)
        return ContentFile(buf.read(), name='logo_recortado.png')
    except Exception:
        logger.debug('Falha ao recortar logo automaticamente — mantendo arquivo original', exc_info=True)
        return None


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

    # Nomes por hotspot (não globais!) — dois hotspots no MESMO equipamento (ex:
    # interfaces diferentes) usavam antes os mesmos nomes fixos "hs-pool-crm",
    # "hs-dhcp-crm" etc. Aplicar o segundo hotspot reencontrava esses objetos já
    # criados pelo primeiro e os reconfigurava (set) para a nova interface —
    # na prática "roubando" DHCP server/pool/profile/hotspot server do primeiro
    # hotspot, que ficava sem DHCP funcionando na sua própria interface mesmo com
    # a bridge/IP dele intactos. Sufixo por `safe_nome` isola cada hotspot.
    safe_nome    = hotspot.nome.lower().replace(' ', '-')
    pool_name    = f'hs-pool-{safe_nome}'
    dhcp_name    = f'hs-dhcp-{safe_nome}'
    profile_name = f'hs-prof-{safe_nome}'
    server_name  = f'hs-srv-{safe_nome}'
    # Diretório do login.html também precisa ser por hotspot — com um único
    # diretório "hotspot" compartilhado, o login.html do segundo hotspot
    # sobrescrevia o do primeiro no mesmo equipamento (mesmo bug do parágrafo
    # acima). Criado via SFTP mkdir logo abaixo, no passo 9.
    dir_name     = f'hotspot-{safe_nome}'

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
        # ── -1. Migração: nomes globais antigos → por hotspot ────────────────
        # Antes de 22/07/2026, pool/dhcp-server/profile/hotspot server usavam
        # nomes fixos ("hs-pool-crm", "hs-dhcp-crm", "hs-prof-crm", "hs-crm")
        # compartilhados por TODOS os hotspots do mesmo equipamento. Se este
        # hotspot ainda não tem seus objetos com o nome novo mas o objeto com
        # o nome antigo existe, renomeia em vez de criar do zero — evita
        # duplicar (dois hotspot servers) ou deixar o objeto antigo órfão.
        for _menu, _old, _new in (
            ('/ip pool', 'hs-pool-crm', pool_name),
            ('/ip dhcp-server', 'hs-dhcp-crm', dhcp_name),
            ('/ip hotspot profile', 'hs-prof-crm', profile_name),
            ('/ip hotspot', 'hs-crm', server_name),
        ):
            if (_new != _old
                    and _mt_count(client, f'{_menu} print count-only where name="{_new}"') == 0
                    and _mt_count(client, f'{_menu} print count-only where name="{_old}"') > 0):
                _mt_exec(client, f'{_menu} set [find name="{_old}"] name="{_new}"')
                log.append(f'   Migrado {_old} → {_new}')

        # `hotspot.interface` decide o modo da interface PRINCIPAL: vazio/"bridge"
        # (default do model) usa a bridge de sempre; qualquer outro valor (ex:
        # "ether5") é o nome literal de uma interface física, e o hotspot é
        # configurado direto nela, sem criar bridge nenhuma. Interfaces adicionais
        # (HotspotInterface) sempre usam bridge própria — modo direto só faz
        # sentido para uma única interface.
        modo_interface = (hotspot.interface or 'bridge').strip() or 'bridge'
        usar_bridge_principal = modo_interface.lower() == 'bridge'
        bridge_principal = f'hs-{safe_nome}' if usar_bridge_principal else modo_interface

        # Interfaces a aplicar: a principal (campos do próprio HotspotConfig, nomes
        # já calculados acima) + as adicionais em HotspotInterface (nomes sufixados
        # por id para não colidir entre si). Todas usam o mesmo Acesso/roteador e
        # compartilham profile/portal — cada uma ganha sua própria pool/dhcp-server/
        # hotspot-server (e bridge própria, exceto a principal em modo direto).
        interfaces_aplicar = [{
            'suffix': '',
            'usar_bridge': usar_bridge_principal,
            'bridge_name': bridge_principal,
            'pool_name': pool_name,
            'dhcp_name': dhcp_name,
            'server_name': server_name,
            'interface_fisica': hotspot.interface_fisica,
            'network': hotspot.network,
            'gateway': hotspot.gateway,
            'pool_start': hotspot.pool_start,
            'pool_end': hotspot.pool_end,
        }]
        for extra in hotspot.interfaces.filter(ativo=True).order_by('id'):
            suf = f'-{extra.id}'
            modo_extra = (extra.interface or 'bridge').strip() or 'bridge'
            usar_bridge_extra = modo_extra.lower() == 'bridge'
            bridge_extra = f'hs-{safe_nome}{suf}' if usar_bridge_extra else modo_extra
            interfaces_aplicar.append({
                'suffix': suf,
                'usar_bridge': usar_bridge_extra,
                'bridge_name': bridge_extra,
                'pool_name': f'{pool_name}{suf}',
                'dhcp_name': f'{dhcp_name}{suf}',
                'server_name': f'{server_name}{suf}',
                'interface_fisica': extra.interface_fisica,
                'network': extra.network,
                'gateway': extra.gateway,
                'pool_start': extra.pool_start,
                'pool_end': extra.pool_end,
            })

        for cfg in interfaces_aplicar:
            iface_label = f' [{cfg["interface_fisica"] or cfg["bridge_name"]}]' if cfg['suffix'] else ''
            bridge_name = cfg['bridge_name']

            # ── 0. Interface: bridge (padrão) ou interface física direta ───────
            if cfg['usar_bridge']:
                bridge_comment = f'hotspot-{hotspot.nome}{cfg["suffix"]}'

                if _mt_count(client, f'/interface bridge print count-only where name="{bridge_name}"') == 0:
                    out, err, rc = _mt_exec(client,
                        f'/interface bridge add name="{bridge_name}" comment="{bridge_comment}"')
                    ok = _mt_output_ok(out, err, rc)
                    log.append(f'{"✅" if ok else "⚠️"} Bridge criada ({bridge_name}){iface_label}: {out or err or "ok"}')
                else:
                    # Reutiliza a bridge existente — NÃO cria nova para não perder as interfaces já configuradas
                    _mt_exec(client, f'/interface bridge set [find name="{bridge_name}"] comment="{bridge_comment}"')
                    log.append(f'✅ Bridge reutilizada ({bridge_name}){iface_label} — interfaces existentes preservadas')

                # Adicionar interface física ao bridge como bridge port (se configurada)
                if cfg['interface_fisica']:
                    iface = cfg['interface_fisica'].strip()
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
                    log.append(
                        f'   ⚠️ Interface física NÃO configurada{iface_label} — este hotspot não vai interceptar clientes!\n'
                        f'   Interfaces disponíveis no roteador:\n'
                        + '\n'.join(f'      {l}' for l in ifaces_lines[:10])
                    )
            else:
                # Modo direto: sem bridge — IP/DHCP/Hotspot Server vão direto na
                # interface física informada no campo "Interface" (ex: "ether5").
                log.append(f'ℹ️ Modo direto — hotspot configurado direto em {bridge_name}, sem bridge')

            # ── 0b. NAT src-nat para a rede desta interface ─────────────────────
            # Usa src-nat com to-addresses=IP-do-acesso em vez de masquerade.
            # Masquerade usa o IP da interface de saída (pode ser um IP interno de carrier
            # como 198.18.x.x), que o upstream ISP não roteia de volta para a internet.
            # Com src-nat to-addresses=acesso.host, o tráfego dos clientes aparece como
            # originado do IP público do roteador — o mesmo que o CRM usa para SSH.
            nat_public_ip = acesso.host
            nat_comment = f'hs-nat-{safe_nome}{cfg["suffix"]}'
            # Remove regras antigas (nome antigo global ou por hotspot) para evitar duplicatas
            _mt_exec(client,
                f'/ip firewall nat remove [find chain=srcnat src-address="{cfg["network"]}" comment="hs-crm-masq"]')
            _mt_exec(client,
                f'/ip firewall nat remove [find chain=srcnat src-address="{cfg["network"]}" comment="{nat_comment}"]')
            nat_count = _mt_count(client,
                f'/ip firewall nat print count-only where chain=srcnat '
                f'src-address="{cfg["network"]}" action=src-nat to-addresses="{nat_public_ip}"')
            if nat_count == 0:
                _mt_exec(client,
                    f'/ip firewall nat add chain=srcnat action=src-nat '
                    f'to-addresses="{nat_public_ip}" '
                    f'src-address="{cfg["network"]}" comment="{nat_comment}"')
                log.append(f'✅ NAT src-nat adicionado ({cfg["network"]} → {nat_public_ip})')
            else:
                log.append(f'✅ NAT src-nat já existe ({cfg["network"]} → {nat_public_ip})')

            # ── 1. IP Address na interface ──────────────────────────────────────
            try:
                prefix = _ipmod.IPv4Network(cfg['network'], strict=False).prefixlen
            except ValueError:
                prefix = 24
            gw_cidr = f'{cfg["gateway"]}/{prefix}'

            ip_flt = f'interface="{bridge_name}" address~"{cfg["gateway"]}"'
            ok, lbl, out = _set_or_add(
                f'/ip address print count-only where {ip_flt}',
                f'/ip address set [find {ip_flt}] address={gw_cidr}',
                f'/ip address add address={gw_cidr} interface={bridge_name}',
                'IP Address',
            )
            log.append(f'{"✅" if ok else "⚠️"} IP Address {lbl} ({gw_cidr} → {bridge_name}): {out}')

            # ── 2. IP Pool ───────────────────────────────────────────────────────
            ranges = f'{cfg["pool_start"]}-{cfg["pool_end"]}'
            ok, lbl, out = _set_or_add(
                f'/ip pool print count-only where name="{cfg["pool_name"]}"',
                f'/ip pool set [find name="{cfg["pool_name"]}"] ranges={ranges}',
                f'/ip pool add name="{cfg["pool_name"]}" ranges={ranges}',
                'IP Pool',
            )
            log.append(f'{"✅" if ok else "⚠️"} IP Pool {lbl} ({ranges}): {out}')

            # ── 3. DHCP Server ───────────────────────────────────────────────────
            ok, lbl, out = _set_or_add(
                f'/ip dhcp-server print count-only where name="{cfg["dhcp_name"]}"',
                f'/ip dhcp-server set [find name="{cfg["dhcp_name"]}"] '
                f'interface={bridge_name} address-pool="{cfg["pool_name"]}" disabled=no',
                f'/ip dhcp-server add name="{cfg["dhcp_name"]}" '
                f'interface={bridge_name} address-pool="{cfg["pool_name"]}" disabled=no',
                'DHCP Server',
            )
            log.append(f'{"✅" if ok else "⚠️"} DHCP Server {lbl}: {out}')

            # ── 3b. DHCP Lease Script (controle de banda via Queue Simple) ──────
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
                    f'/ip dhcp-server set [find name="{cfg["dhcp_name"]}"] lease-script="{lease_script}"',
                )
                ls_ok = _mt_output_ok(ls_out, ls_err, ls_rc)
                log.append(f'{"✅" if ls_ok else "⚠️"} DHCP Lease Script (banda {limit}): {ls_out or ls_err or "ok"}')
            else:
                # Garantir que não há script de banda ativo
                _mt_exec(client, f'/ip dhcp-server set [find name="{cfg["dhcp_name"]}"] lease-script=""')
                log.append('ℹ️ Controle de banda desativado — lease-script limpo')

            # ── 4. DHCP Network ──────────────────────────────────────────────────
            # IMPORTANTE: o cliente do hotspot deve receber o GATEWAY (o próprio
            # roteador) como DNS — NÃO um DNS externo (8.8.8.8). Antes do login o
            # hotspot bloqueia o acesso externo; se o cliente tentar falar com 8.8.8.8
            # a resolução falha (DNS_PROBE_FINISHED_NO_INTERNET) e o portal nem abre.
            # Usando o gateway, o hotspot intercepta o DNS e redireciona para o login.
            ok, lbl, out = _set_or_add(
                f'/ip dhcp-server network print count-only where address="{cfg["network"]}"',
                f'/ip dhcp-server network set [find address="{cfg["network"]}"] '
                f'gateway={cfg["gateway"]} dns-server={cfg["gateway"]}',
                f'/ip dhcp-server network add address={cfg["network"]} '
                f'gateway={cfg["gateway"]} dns-server={cfg["gateway"]}',
                'DHCP Network',
            )
            log.append(f'{"✅" if ok else "⚠️"} DHCP Network {lbl} ({cfg["network"]}): {out}')

        # ── 5. Hotspot Profile (compartilhado por todas as interfaces) ─────────
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

        # ── 6. Hotspot Server (um por interface, todos usando o profile acima) ─
        for cfg in interfaces_aplicar:
            srv_params = (
                f'interface="{cfg["bridge_name"]}" '
                f'address-pool="{cfg["pool_name"]}" '
                f'profile="{profile_name}" '
                f'disabled=no'
            )
            srv_exists = _mt_count(client, f'/ip hotspot print count-only where name="{cfg["server_name"]}"')
            if srv_exists > 0:
                cmd_srv = f'/ip hotspot set [find name="{cfg["server_name"]}"] {srv_params}'
                srv_out, srv_err, srv_rc = _mt_exec(client, cmd_srv)
                srv_lbl = 'atualizado'
            else:
                cmd_srv = f'/ip hotspot add name="{cfg["server_name"]}" {srv_params}'
                srv_out, srv_err, srv_rc = _mt_exec(client, cmd_srv)
                srv_lbl = 'criado'
            srv_ok = _mt_output_ok(srv_out, srv_err, srv_rc)
            srv_detail = (srv_out + ' ' + srv_err).strip() or 'ok'
            log.append(f'{"✅" if srv_ok else "⚠️"} Hotspot Server {cfg["server_name"]} {srv_lbl}: {srv_detail}')

        # ── 7. Usuário guest (server=all → funciona em todos os servers deste hotspot) ─
        g_flt = f'name="{hotspot.guest_usuario}"'
        ok, lbl, out = _set_or_add(
            f'/ip hotspot user print count-only where {g_flt}',
            f'/ip hotspot user set [find {g_flt}] '
            f'password="{hotspot.guest_senha}" profile=default server=all',
            f'/ip hotspot user add name="{hotspot.guest_usuario}" '
            f'password="{hotspot.guest_senha}" server=all profile=default',
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
        # Entrada sem 'server' → libera em TODOS os hotspot servers deste hotspot,
        # cobrindo a interface principal e as adicionais
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

        # IMPORTANTE (confirmado ao vivo em 2026-07-18 na WTD): o html-directory do
        # profile do hotspot pode normalizar/resolver para "flash/<dir>" mesmo
        # quando setado como "<dir>" (RouterOS não é consistente entre profiles —
        # o profile "default" mantém "<dir>" como digitado, mas um profile
        # criado/recriado via SSH normaliza para "flash/<dir>"). O arquivo real
        # que o hotspot serve para o cliente é o que estiver em QUALQUER que seja
        # a resolução efetiva — então gravamos nos dois caminhos possíveis para
        # cobrir ambos os comportamentos, em vez de adivinhar qual vale para
        # cada roteador.
        remote_paths = [f'{dir_name}/login.html', f'flash/{dir_name}/login.html']
        sftp_ok = False
        try:
            sftp = client.open_sftp()
            # dir_name agora é único por hotspot (ver comentário no início da
            # função) — ao contrário do antigo "hotspot" fixo, esse diretório
            # não existe de fábrica no roteador, então precisa ser criado.
            # IOError = já existe (ou caminho "flash/<dir>" é só um alias do
            # mesmo diretório, mesma incerteza do comentário acima) — ignora.
            for _dir in (dir_name, f'flash/{dir_name}'):
                try:
                    sftp.mkdir(_dir)
                except IOError:
                    pass
            for remote_path in remote_paths:
                sftp.putfo(_io.BytesIO(html_bytes), remote_path)
            sftp.close()
            sftp_ok = True
            log.append(f'✅ login.html enviado via SFTP → {", ".join(remote_paths)} ({len(html_bytes)} bytes)')
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
            fetch_all_ok = True
            for remote_path in remote_paths:
                fetch_out, fetch_err, fetch_rc = _mt_exec(client,
                    f'/tool fetch url="{login_html_url}" '
                    f'dst-path="{remote_path}" mode=http',
                    timeout=30)
                fetch_combined = (fetch_out + fetch_err).strip()
                ok = fetch_rc == 0 and not any(
                    k in fetch_combined.lower() for k in ('error', 'failure', 'failed', 'timed out'))
                fetch_all_ok = fetch_all_ok and ok
                if ok:
                    log.append(f'✅ login.html baixado via fetch: {login_html_url} → {remote_path}')
                else:
                    log.append(f'⚠️ /tool fetch falhou para {remote_path} (rc={fetch_rc}): {fetch_combined or "sem detalhe"}')
            sftp_ok = fetch_all_ok

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

        # Reiniciar todos os hotspot servers (principal + adicionais) para carregar novo login.html
        for cfg in interfaces_aplicar:
            _mt_exec(client, f'/ip hotspot set [find name="{cfg["server_name"]}"] disabled=yes')
            _mt_exec(client, f'/ip hotspot set [find name="{cfg["server_name"]}"] disabled=no')
        log.append(f'✅ Hotspot(s) reiniciado(s) ({len(interfaces_aplicar)}) — novo portal ativo')

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
            'interfaces_count': h.interfaces.filter(ativo=True).count(),
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
        'cor_painel': h.cor_painel,
        'cor_texto': h.cor_texto,
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
        'cor_painel': body.get('cor_painel', '#0f0f19').strip() or '#0f0f19',
        'cor_texto': body.get('cor_texto', '#ffffff').strip() or '#ffffff',
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
# Interfaces adicionais — mais de uma pool/DHCP no mesmo Hotspot
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def hotspot_interfaces(request, cliente_id, hotspot_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    data = [
        {
            'id': i.id,
            'nome': i.nome,
            'interface': i.interface,
            'interface_fisica': i.interface_fisica,
            'network': i.network,
            'gateway': i.gateway,
            'pool_start': i.pool_start,
            'pool_end': i.pool_end,
            'dns_servidor': i.dns_servidor,
            'ativo': i.ativo,
        }
        for i in h.interfaces.all()
    ]
    return JsonResponse({'ok': True, 'interfaces': data})


@login_required
@require_http_methods(['POST'])
def hotspot_interface_salvar(request, cliente_id, hotspot_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    body = _json(request)
    iid = body.get('id')

    interface = body.get('interface', 'bridge').strip() or 'bridge'
    interface_fisica = body.get('interface_fisica', '').strip()
    if interface.lower() == 'bridge' and not interface_fisica:
        return JsonResponse({'ok': False, 'error': 'Informe a interface física (bridge port) ou o nome da interface para modo direto.'}, status=400)

    defaults = {
        'nome': body.get('nome', '').strip(),
        'interface': interface,
        'interface_fisica': interface_fisica,
        'network': body.get('network', '192.168.89.0/24').strip(),
        'gateway': body.get('gateway', '192.168.89.1').strip(),
        'pool_start': body.get('pool_start', '192.168.89.10').strip(),
        'pool_end': body.get('pool_end', '192.168.89.254').strip(),
        'dns_servidor': body.get('dns_servidor', '8.8.8.8').strip(),
        'ativo': bool(body.get('ativo', True)),
    }

    if iid:
        i = get_object_or_404(HotspotInterface, id=iid, hotspot=h)
        for k, v in defaults.items():
            setattr(i, k, v)
        i.save()
        created = False
    else:
        i = HotspotInterface.objects.create(hotspot=h, **defaults)
        created = True

    return JsonResponse({'ok': True, 'id': i.id, 'created': created})


@login_required
@require_http_methods(['POST'])
def hotspot_interface_deletar(request, cliente_id, hotspot_id, interface_id):
    c = _cliente(request, cliente_id)
    h = get_object_or_404(HotspotConfig, id=hotspot_id, cliente=c)
    i = get_object_or_404(HotspotInterface, id=interface_id, hotspot=h)
    i.delete()
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

    recortada = _autocrop_logo(img, ext)
    if recortada:
        img = recortada

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
            'termos_aceitos': lead.termos_aceitos,
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
# Integração Disparo — envio automático de WhatsApp (HSM) para novos leads
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def hotspot_disparo_config(request, cliente_id):
    """Config de disparo por empresa de integração (Chatmix, Opa Suite, ...)
    para o cliente — compartilhada entre todos os hotspots dele."""
    c = _cliente(request, cliente_id)
    salvos = {cfg.provider: cfg for cfg in ClienteIntegracaoDisparo.objects.filter(cliente=c)}

    providers = []
    for key, nome in ClienteIntegracaoDisparo.PROVIDER_CHOICES:
        cfg = salvos.get(key)
        providers.append({
            'key': key,
            'nome': nome,
            'disponivel': True,
            'habilitado': cfg.habilitado if cfg else False,
            'api_key': cfg.api_key if cfg else '',
            'api_token': cfg.api_token if cfg else '',
            'api_dominio': cfg.api_dominio if cfg else '',
            'canal_id': cfg.canal_id if cfg else '',
            'template_id': cfg.template_id if cfg else '',
            'variaveis': cfg.variaveis_modelo if cfg else list(DISPARO_VARIAVEIS_EXEMPLO),
        })

    return JsonResponse({'ok': True, 'providers': providers})


@login_required
@require_http_methods(['POST'])
def hotspot_disparo_salvar(request, cliente_id):
    c = _cliente(request, cliente_id)
    body = _json(request)
    provider = (body.get('provider') or '').strip()

    if provider not in dict(ClienteIntegracaoDisparo.PROVIDER_CHOICES):
        return JsonResponse({'ok': False, 'error': 'Empresa de integração inválida.'}, status=400)

    variaveis_raw = body.get('variaveis')
    if not isinstance(variaveis_raw, list):
        return JsonResponse({'ok': False, 'error': 'Lista de variáveis inválida.'}, status=400)
    variaveis = [str(v).strip()[:255] for v in variaveis_raw if str(v).strip()]
    if not variaveis:
        return JsonResponse({'ok': False, 'error': 'Adicione ao menos uma variável (ex: {nome}).'}, status=400)

    cfg, _created = ClienteIntegracaoDisparo.objects.get_or_create(cliente=c, provider=provider)
    cfg.api_key = (body.get('api_key') or '').strip()[:255]
    cfg.api_token = (body.get('api_token') or '').strip()[:255]
    cfg.api_dominio = (body.get('api_dominio') or '').strip()[:255]
    cfg.canal_id = (body.get('canal_id') or '').strip()[:64]
    cfg.template_id = (body.get('template_id') or '').strip()[:64]
    cfg.variaveis_modelo = variaveis
    cfg.save()

    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['POST'])
def hotspot_disparo_toggle(request, cliente_id):
    c = _cliente(request, cliente_id)
    body = _json(request)
    provider = (body.get('provider') or '').strip()

    if provider not in dict(ClienteIntegracaoDisparo.PROVIDER_CHOICES):
        return JsonResponse({'ok': False, 'error': 'Empresa de integração inválida.'}, status=400)

    cfg, _created = ClienteIntegracaoDisparo.objects.get_or_create(cliente=c, provider=provider)
    cfg.habilitado = not cfg.habilitado
    cfg.save(update_fields=['habilitado', 'atualizado_em'])

    return JsonResponse({'ok': True, 'provider': provider, 'habilitado': cfg.habilitado})


@login_required
@require_http_methods(['POST'])
def hotspot_disparo_testar(request, cliente_id):
    """Envia um disparo de teste com os dados salvos, sem depender de um
    lead real — útil para validar as credenciais/template antes de habilitar."""
    c = _cliente(request, cliente_id)
    body = _json(request)
    provider = (body.get('provider') or '').strip()
    numero = (body.get('numero') or '').strip()

    if provider not in dict(ClienteIntegracaoDisparo.PROVIDER_CHOICES):
        return JsonResponse({'ok': False, 'error': 'Empresa de integração inválida.'}, status=400)
    if not numero:
        return JsonResponse({'ok': False, 'error': 'Informe um número para o teste.'}, status=400)

    try:
        cfg = ClienteIntegracaoDisparo.objects.get(cliente=c, provider=provider)
    except ClienteIntegracaoDisparo.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Configure e salve a integração antes de testar.'}, status=400)

    from .services import (
        ChatmixClient, OpaSuiteClient, montar_variaveis_mensagem, normalizar_numero_whatsapp,
    )

    lead_fake = HotspotLead(nome='Teste', telefone=numero)
    variaveis = montar_variaveis_mensagem(cfg.variaveis_modelo, lead_fake)
    numero_fmt = normalizar_numero_whatsapp(numero)

    if provider == 'chatmix':
        if not cfg.api_key or not cfg.api_token or not cfg.template_id:
            return JsonResponse({'ok': False, 'error': 'Preencha key, token e ID do template antes de testar.'}, status=400)
        client = ChatmixClient(cfg.api_key, cfg.api_token)
        ok, detalhe = client.enviar_hsm(numero_fmt, variaveis, cfg.template_id)
    else:  # opa_suit
        if not cfg.api_dominio or not cfg.api_token or not cfg.canal_id or not cfg.template_id:
            return JsonResponse({'ok': False, 'error': 'Preencha domínio, token, canal e ID do template antes de testar.'}, status=400)
        client = OpaSuiteClient(cfg.api_dominio, cfg.api_token)
        ok, detalhe = client.enviar_template(numero_fmt, cfg.canal_id, cfg.template_id, variaveis)

    return JsonResponse({'ok': ok, 'detalhe': detalhe})


# ─────────────────────────────────────────────────────────────────────────────
# Termos de Uso / Política de Privacidade (LGPD) — conteúdo estático exibido
# no modal de aceite da tela de login do hotspot.
# ─────────────────────────────────────────────────────────────────────────────

_TERMOS_CONTEUDO_HTML = """
<div class="m-tabcontent active" id="mc-resumo">
  <p class="m-lead">Este documento define como coletamos, usamos e protegemos seus dados quando você usa nosso serviço de WiFi grátis, e as regras para uso do serviço.</p>
  <ul>
    <li>Coletamos seu <strong>nome</strong> e <strong>telefone</strong> para liberar seu acesso;</li>
    <li>Registramos dados técnicos de conexão por <strong>12 meses</strong> (exigência legal do Marco Civil da Internet);</li>
    <li>Você pode solicitar a exclusão dos seus dados, mas alguns registros serão mantidos pelo período legal;</li>
    <li>Você é responsável por usar o serviço de forma legal e adequada;</li>
    <li>Não compartilhamos seus dados com terceiros, exceto quando exigido por lei.</li>
  </ul>
  <p>Para detalhes completos, consulte as abas "Privacidade" e "Termos" acima.</p>
</div>

<div class="m-tabcontent" id="mc-privacidade">
  <p>Esta Política de Privacidade descreve como coletamos, usamos, armazenamos, compartilhamos e protegemos seus dados pessoais ao utilizar este serviço de WiFi gratuito ("Serviço"), em conformidade com a Lei Geral de Proteção de Dados (Lei nº 13.709/2018) e o Marco Civil da Internet (Lei nº 12.965/2014).</p>

  <h4>1. Dados que coletamos</h4>
  <ul>
    <li><strong>Dados cadastrais</strong>: nome, sobrenome e telefone para autenticação;</li>
    <li><strong>Dados de conexão</strong>: data e hora de início e término, duração, IP utilizado, endereço MAC do dispositivo e registros de acesso;</li>
    <li><strong>Dados de navegação</strong>: volume de dados trafegados (sem conteúdo das comunicações).</li>
  </ul>

  <h4>2. Finalidades do tratamento</h4>
  <ul>
    <li>Autenticar seu acesso ao Serviço;</li>
    <li>Cumprir obrigações legais e regulatórias;</li>
    <li>Proteger a segurança da rede e identificar usos inadequados ou ilícitos;</li>
    <li>Melhorar a qualidade do Serviço.</li>
  </ul>

  <h4>3. Bases legais</h4>
  <p>Execução de contrato, consentimento, cumprimento de obrigação legal (Marco Civil da Internet) e legítimo interesse na segurança da rede.</p>

  <h4>4. Compartilhamento de dados</h4>
  <ul>
    <li>Com autoridades públicas, mediante ordem judicial ou requisição legal;</li>
    <li>Com prestadores de serviço que nos auxiliam na operação do Serviço, sob contrato de confidencialidade;</li>
    <li>Em caso de reorganização societária, fusão ou aquisição.</li>
  </ul>

  <h4>5. Período de retenção</h4>
  <ul>
    <li><strong>Registros de conexão</strong>: 12 meses, conforme exigido pelo Marco Civil da Internet;</li>
    <li><strong>Dados cadastrais</strong>: enquanto você for usuário do Serviço e pelo período necessário ao cumprimento de obrigações legais.</li>
  </ul>

  <h4>6. Seus direitos (LGPD)</h4>
  <p>Você pode confirmar a existência de tratamento, acessar, corrigir, solicitar anonimização/bloqueio/eliminação de dados desnecessários, solicitar portabilidade e revogar o consentimento a qualquer momento.</p>
  <p class="m-important">Em caso de solicitação de exclusão, alguns dados poderão ser mantidos pelo período legal (registros de conexão) ou para cumprimento de outras obrigações legais.</p>

  <h4>7. Medidas de segurança</h4>
  <p>Adotamos criptografia, controle de acesso, firewalls e políticas internas de proteção de dados.</p>

  <h4>8. Alterações</h4>
  <p>Esta Política pode ser atualizada periodicamente; a versão mais recente estará sempre disponível nesta tela de login.</p>
</div>

<div class="m-tabcontent" id="mc-termos">
  <p>Estes Termos de Uso estabelecem as condições para utilização deste serviço de WiFi gratuito ("Serviço"). Ao utilizar o Serviço, você concorda com estes Termos.</p>

  <h4>1. Descrição do Serviço</h4>
  <p>O Serviço consiste no fornecimento de acesso gratuito à internet via WiFi. É oferecido "como está", sem garantias de velocidade ou disponibilidade contínua.</p>

  <h4>2. Condições de uso</h4>
  <ul>
    <li>Utilizar o Serviço apenas para finalidades lícitas;</li>
    <li>Não prejudicar o funcionamento do Serviço ou da rede;</li>
    <li>Não enviar spam, vírus ou outros códigos maliciosos;</li>
    <li>Não acessar ou distribuir conteúdos ilegais;</li>
    <li>Não violar direitos de propriedade intelectual;</li>
    <li>Não utilizar ferramentas para burlar limitações do Serviço ou ocultar sua identidade.</li>
  </ul>

  <h4>3. Limitações do Serviço</h4>
  <p>Limite de velocidade e de tempo de uso, bloqueio de determinados conteúdos, interrupções para manutenção e priorização de tráfego, conforme definido pelo provedor do Serviço.</p>

  <h4>4. Responsabilidades</h4>
  <p>Você é o único responsável pelas atividades realizadas durante sua sessão e pelo conteúdo que acessar. Não nos responsabilizamos por perda de dados, danos a dispositivos ou interrupções do Serviço.</p>

  <h4>5. Monitoramento e suspensão</h4>
  <p>Reservamo-nos o direito de monitorar o uso do Serviço para fins de segurança, suspender o acesso em caso de violação destes Termos e reportar atividades ilegais às autoridades competentes.</p>

  <h4>6. Legislação aplicável</h4>
  <p>Estes Termos são regidos pelas leis da República Federativa do Brasil.</p>
</div>
"""


def _alpha(hex_color, opacity):
    """Anexa um sufixo alpha de 2 dígitos hex a uma cor #RRGGBB (ex: #fff, .6 -> #fff99)."""
    return f'{hex_color}{round(opacity * 255):02x}'


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
    cor_painel = hotspot.cor_painel or '#0f0f19'
    cor_texto = hotspot.cor_texto or '#ffffff'

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
  background:{cor_painel}d9;
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
  max-height:100px;max-width:90%;
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
.login-header h2{{color:{cor_texto};font-size:1.5rem;font-weight:700;line-height:1.2}}
.login-header p{{color:{_alpha(cor_texto,.6)};font-size:.9rem;margin-top:6px}}

/* Form area */
.form-area{{padding:22px 22px 22px}}

/* Linha com dois campos lado a lado (Nome / Sobrenome) */
.field-row{{display:flex;gap:10px}}
.field-row .field{{flex:1 1 0;min-width:0}}

/* Inputs com ícone flutuante */
.field{{position:relative;margin-bottom:16px}}
.field-icon{{
  position:absolute;left:16px;top:50%;transform:translateY(-50%);
  font-size:17px;opacity:.5;pointer-events:none;
}}
.field input{{
  width:100%;height:54px;
  padding:0 14px 0 44px;
  background:rgba(255,255,255,0.06);
  border:1.5px solid rgba(255,255,255,0.14);
  border-radius:14px;
  color:{cor_texto};font-size:.95rem;
  outline:none;transition:border-color .2s,background .2s,box-shadow .2s;
  -webkit-appearance:none;appearance:none;
}}
.field.no-icon input{{padding-left:16px}}
.field input::placeholder{{color:{_alpha(cor_texto,.3)}}}
.field input:hover{{border-color:rgba(255,255,255,.22)}}
.field input:focus{{
  border-color:{cor};
  background:rgba(255,255,255,0.09);
  box-shadow:0 0 0 3px {cor}2e;
}}
.field.error input{{border-color:#f87171;background:rgba(248,113,113,.08)}}
.field-hint{{font-size:.72rem;color:{_alpha(cor_texto,.38)};margin:-10px 2px 14px;padding-left:4px;transition:color .2s}}
.field-hint.error{{color:#f87171}}
.field-label{{
  position:absolute;left:44px;top:50%;transform:translateY(-50%);
  font-size:.85rem;color:{_alpha(cor_texto,.38)};
  pointer-events:none;transition:all .2s;
}}
.field.no-icon .field-label{{left:16px}}
.field input:focus ~ .field-label,
.field input:not(:placeholder-shown) ~ .field-label{{
  top:9px;font-size:.68rem;color:{cor};letter-spacing:.03em;transform:none;
}}

/* Aceite de termos */
.terms-check{{
  display:flex;align-items:flex-start;gap:10px;
  margin:4px 2px 18px;cursor:pointer;user-select:none;
}}
.terms-check input{{
  margin-top:2px;width:18px;height:18px;flex:none;
  accent-color:{cor};cursor:pointer;
}}
.terms-check span{{font-size:.8rem;line-height:1.45;color:{_alpha(cor_texto,.55)}}}
.terms-check a{{color:{cor};text-decoration:underline;font-weight:600}}
.terms-check.error span{{color:#f87171}}

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
  font-size:.72rem;color:{_alpha(cor_texto,.2)};line-height:1.5;
}}
.footer a{{color:{_alpha(cor_texto,.35)};text-decoration:none}}

/* ── Modal de Termos de Uso / Política de Privacidade ────────────── */
.modal-overlay{{
  position:fixed;inset:0;z-index:20;display:none;
  background:rgba(5,5,10,.72);backdrop-filter:blur(4px);
  align-items:center;justify-content:center;padding:16px;
}}
.modal-overlay.open{{display:flex}}
.modal{{
  width:min(480px,100%);max-height:88vh;
  display:flex;flex-direction:column;
  background:#12121c;border-radius:20px;
  border:1px solid rgba(255,255,255,0.1);
  box-shadow:0 32px 80px rgba(0,0,0,.6);
  animation:cardIn .3s cubic-bezier(.22,1,.36,1) both;
}}
.modal-head{{
  display:flex;align-items:center;justify-content:space-between;
  padding:18px 20px;border-bottom:1px solid rgba(255,255,255,.08);
}}
.modal-head h3{{color:#fff;font-size:1.02rem;font-weight:700}}
.modal-close{{
  background:rgba(255,255,255,.08);border:none;color:#fff;
  width:30px;height:30px;border-radius:50%;font-size:18px;line-height:1;
  cursor:pointer;flex:none;
}}
.modal-tabs{{display:flex;gap:6px;padding:12px 20px 0}}
.m-tab{{
  padding:8px 14px;border-radius:10px;font-size:.78rem;font-weight:600;
  color:rgba(255,255,255,.5);background:rgba(255,255,255,.05);cursor:pointer;
}}
.m-tab.active{{color:#fff;background:{cor}}}
.modal-body{{
  padding:16px 20px 4px;overflow-y:auto;-webkit-overflow-scrolling:touch;
  color:rgba(255,255,255,.72);font-size:.82rem;line-height:1.6;
}}
.m-tabcontent{{display:none}}
.m-tabcontent.active{{display:block}}
.m-tabcontent h4{{color:#fff;font-size:.86rem;margin:16px 0 6px}}
.m-tabcontent h4:first-child{{margin-top:0}}
.m-tabcontent p{{margin-bottom:8px}}
.m-tabcontent ul{{margin:0 0 10px 18px}}
.m-tabcontent li{{margin-bottom:4px}}
.m-lead{{color:rgba(255,255,255,.85)}}
.m-important{{color:#fbbf24;font-weight:600}}
.modal-foot{{padding:16px 20px 20px}}
.modal-foot .btn{{height:48px;font-size:.9rem}}
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

      <div class="field-row">
        <div class="field">
          <span class="field-icon">&#x1F464;</span>
          <input type="text" name="nome" id="f_nome"
                 placeholder=" " required autocomplete="given-name">
          <label class="field-label" for="f_nome">Nome</label>
        </div>
        <div class="field no-icon">
          <input type="text" name="sobrenome" id="f_sobrenome"
                 placeholder=" " required autocomplete="family-name">
          <label class="field-label" for="f_sobrenome">Sobrenome</label>
        </div>
      </div>

      <div class="field">
        <span class="field-icon">&#x1F4F1;</span>
        <input type="tel" name="telefone" id="f_tel"
               placeholder=" " required inputmode="tel" autocomplete="tel">
        <label class="field-label" for="f_tel">WhatsApp / Telefone</label>
      </div>
      <div class="field-hint" id="f_tel_hint">Com o 9: (DD) 9XXXX-XXXX</div>

      <label class="terms-check" id="termsCheck">
        <input type="checkbox" name="termos" id="f_termos" required>
        <span>Li e aceito os <a href="#" id="termsLink">Termos de Uso e a Política de Privacidade</a></span>
      </label>

      <div class="btn-wrap">
        <button class="btn" type="submit" id="btn">
          <span id="btn-txt">Conectar ao WiFi</span>
          <span class="spin" id="spin"></span>
        </button>
      </div>
    </form>

    <div class="footer">
      Seus dados são protegidos e não serão compartilhados com terceiros.
    </div>
  </div>
</div>

<!-- Modal: Termos de Uso / Política de Privacidade -->
<div class="modal-overlay" id="termsOverlay">
  <div class="modal">
    <div class="modal-head">
      <h3>Termos e Privacidade</h3>
      <button type="button" class="modal-close" id="termsClose">&times;</button>
    </div>
    <div class="modal-tabs">
      <div class="m-tab active" data-tab="resumo">Resumo</div>
      <div class="m-tab" data-tab="privacidade">Privacidade</div>
      <div class="m-tab" data-tab="termos">Termos</div>
    </div>
    <div class="modal-body">
      {_TERMOS_CONTEUDO_HTML}
    </div>
    <div class="modal-foot">
      <button type="button" class="btn" id="termsAccept">Li e aceito</button>
    </div>
  </div>
</div>

<script>
(function(){{
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

  // Modal de Termos de Uso / Política de Privacidade
  var overlay=document.getElementById('termsOverlay');
  var link=document.getElementById('termsLink');
  var closeBtn=document.getElementById('termsClose');
  var acceptBtn=document.getElementById('termsAccept');
  var termsChk=document.getElementById('f_termos');
  var termsRow=document.getElementById('termsCheck');
  function openModal(e){{if(e)e.preventDefault();overlay.classList.add('open');}}
  function closeModal(){{overlay.classList.remove('open');}}
  if(link)link.addEventListener('click',openModal);
  if(closeBtn)closeBtn.addEventListener('click',closeModal);
  if(overlay)overlay.addEventListener('click',function(e){{if(e.target===overlay)closeModal();}});
  if(acceptBtn)acceptBtn.addEventListener('click',function(){{
    termsChk.checked=true;
    if(termsRow)termsRow.classList.remove('error');
    closeModal();
  }});
  var mtabs=document.querySelectorAll('.m-tab');
  mtabs.forEach(function(t){{
    t.addEventListener('click',function(){{
      mtabs.forEach(function(x){{x.classList.remove('active');}});
      document.querySelectorAll('.m-tabcontent').forEach(function(c){{c.classList.remove('active');}});
      t.classList.add('active');
      document.getElementById('mc-'+t.dataset.tab).classList.add('active');
    }});
  }});
}})();

function onSubmit(){{
  var nome=document.getElementById('f_nome');
  var sobrenome=document.getElementById('f_sobrenome');
  var tel=document.getElementById('f_tel');
  var termos=document.getElementById('f_termos');
  var ok=true;
  [nome,sobrenome].forEach(function(inp){{
    var field=inp.closest('.field');
    if(!inp.value.trim()){{field.classList.add('error');ok=false;}}
    else field.classList.remove('error');
  }});
  // Exige DDD + 9º dígito + 8 números (11 dígitos) — sem o 9, o número não
  // bate no formato do WhatsApp e a mensagem de disparo não é entregue.
  var telField=tel.closest('.field');
  var telHint=document.getElementById('f_tel_hint');
  var telDigits=tel.value.replace(/[^0-9]/g,'');
  if(telDigits.length!==11){{
    telField.classList.add('error');
    if(telHint)telHint.classList.add('error');
    ok=false;
  }}else{{
    telField.classList.remove('error');
    if(telHint)telHint.classList.remove('error');
  }}
  var termsRow=document.getElementById('termsCheck');
  if(!termos.checked){{termsRow.classList.add('error');ok=false;}}
  else termsRow.classList.remove('error');
  if(!ok)return false;
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

    link      = request.POST.get('link', '').strip()
    orig      = request.POST.get('orig', '').strip()
    mac       = request.POST.get('mac', '').strip()
    ip_c      = request.POST.get('ip', '').strip()
    nome      = request.POST.get('nome', '').strip()
    sobrenome = request.POST.get('sobrenome', '').strip()
    tel       = request.POST.get('telefone', '').strip()
    termos    = request.POST.get('termos', '').strip().lower() in ('on', 'true', '1')

    nome_completo = f'{nome} {sobrenome}'.strip()

    if nome_completo or tel:
        # Evita salvar lead duplicado: mesmo hotspot + mesmo telefone ou mesmo
        # nome completo (case-insensitive) já cadastrado não gera novo registro.
        dup_filter = Q()
        if tel:
            dup_filter |= Q(telefone=tel)
        if nome_completo:
            dup_filter |= Q(nome__iexact=nome_completo)

        ja_existe = bool(dup_filter) and HotspotLead.objects.filter(hotspot=h).filter(dup_filter).exists()

        if not ja_existe:
            try:
                HotspotLead.objects.create(
                    hotspot=h,
                    nome=nome_completo[:100],
                    telefone=tel[:20],
                    mac=mac[:17],
                    ip_cliente=ip_c[:15],
                    termos_aceitos=termos,
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

    # dst vazio faz o MikroTik mostrar a tela de status ("Hi, guest!") em vez de
    # liberar a navegação — orig quase nunca chega (login.html não captura
    # $(link-orig), ver _gerar_login_html). Sem destino real, mandamos o
    # cliente para a própria URL de detecção de captive portal do SO: ela
    # responde com o "sucesso" esperado e o SO fecha o mini-browser sozinho,
    # liberando o usuário direto pra internet sem tela nenhuma.
    _ua = request.META.get('HTTP_USER_AGENT', '').lower()
    if 'android' in _ua:
        _default_dst = 'http://connectivitycheck.gstatic.com/generate_204'
    elif any(k in _ua for k in ('iphone', 'ipad', 'ipod', 'macintosh', 'cfnetwork')):
        _default_dst = 'http://captive.apple.com/hotspot-detect.html'
    elif 'windows' in _ua:
        _default_dst = 'http://www.msftconnecttest.com/redirect'
    else:
        _default_dst = 'http://connectivitycheck.gstatic.com/generate_204'

    login_full = login_base + '?' + _urlencode({
        'username': h.guest_usuario,
        'password': h.guest_senha,
        'dst': orig or _default_dst,
        'popup': 'true',
    })
    login_js   = login_full.replace('\\', '\\\\').replace("'", "\\'")
    login_href = _html.escape(login_full, quote=True)

    html = _sucesso_page_html(h, nome, login_js, login_href)
    return HttpResponse(html, content_type='text/html; charset=utf-8')


def _sucesso_page_html(hotspot, nome, login_js, login_href):
    """Gera a tela de sucesso exibida entre o envio do formulário e o redirect que autentica no MikroTik."""
    cor        = hotspot.cor_primaria or '#6366f1'
    cor_dark   = hotspot.cor_secundaria or cor
    cor_painel = hotspot.cor_painel or '#0f0f19'
    cor_texto  = hotspot.cor_texto or '#ffffff'
    titulo     = _html.escape(hotspot.portal_titulo or 'WiFi Grátis', quote=True)
    logo_url   = hotspot.logo.url if hotspot.logo else None
    logo_html  = (f'<img class="logo" src="{logo_url}" alt="Logo">' if logo_url else '')

    primeiro_nome = _html.escape(nome, quote=True) if nome else ''
    saudacao = f'Tudo certo, <b>{primeiro_nome}</b>!' if primeiro_nome else 'Tudo certo!'

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Conectado!</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}}
html,body{{height:100%;overflow:hidden}}
body{{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  background:#0a0a0f;
  display:flex;align-items:center;justify-content:center;
  position:relative;color:{cor_texto};
}}
.bg{{
  position:fixed;inset:0;z-index:0;
  background:radial-gradient(ellipse at 20% 25%,{cor}2e 0%,transparent 60%),
             radial-gradient(ellipse at 80% 75%,{cor_dark}2e 0%,transparent 60%);
  animation:bgpulse 7s ease-in-out infinite alternate;
}}
@keyframes bgpulse{{0%{{background-position:0% 50%}}100%{{background-position:100% 50%}}}}
.card{{
  position:relative;z-index:1;width:min(380px,90vw);
  border-radius:24px;background:{cor_painel}d9;
  backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);
  border:1px solid rgba(255,255,255,0.08);
  box-shadow:0 32px 80px rgba(0,0,0,0.7),0 0 0 1px rgba(255,255,255,0.04) inset;
  padding:38px 30px 30px;text-align:center;
  animation:cardIn .5s cubic-bezier(.22,1,.36,1) both;
}}
@keyframes cardIn{{from{{opacity:0;transform:translateY(24px) scale(.96)}}to{{opacity:1;transform:none}}}}
.logo{{display:block;margin:0 auto 20px;max-height:64px;max-width:80%;object-fit:contain;filter:drop-shadow(0 4px 14px rgba(0,0,0,.5))}}
.check-wrap{{width:80px;height:80px;margin:0 auto 22px}}
.check-ring{{
  width:100%;height:100%;border-radius:50%;
  background:linear-gradient(135deg,{cor},{cor_dark});
  display:flex;align-items:center;justify-content:center;
  box-shadow:0 10px 30px {cor}55;
  animation:popIn .5s cubic-bezier(.22,1.4,.36,1) .15s both;
}}
@keyframes popIn{{from{{transform:scale(0);opacity:0}}to{{transform:scale(1);opacity:1}}}}
.check-ring svg{{width:38px;height:38px}}
.check-ring path{{
  stroke:#fff;stroke-width:3;fill:none;stroke-linecap:round;stroke-linejoin:round;
  stroke-dasharray:40;stroke-dashoffset:40;
  animation:draw .45s ease .5s forwards;
}}
@keyframes draw{{to{{stroke-dashoffset:0}}}}
h1{{font-size:1.32rem;font-weight:700;margin-bottom:8px;letter-spacing:-.01em}}
.sub{{color:{_alpha(cor_texto,.6)};font-size:.9rem;line-height:1.55;margin-bottom:26px}}
.sub b{{color:{cor_texto}}}
.progress{{height:4px;border-radius:4px;background:rgba(255,255,255,.1);overflow:hidden;margin-bottom:14px}}
.progress-bar{{height:100%;border-radius:4px;background:linear-gradient(135deg,{cor},{cor_dark});
  width:0;animation:fill 2.1s ease forwards}}
@keyframes fill{{to{{width:100%}}}}
.status{{font-size:.76rem;color:{_alpha(cor_texto,.35)}}}
.status a{{color:{cor};text-decoration:none;font-weight:600}}
</style>
</head>
<body>
<div class="bg"></div>
<div class="card">
  {logo_html}
  <div class="check-wrap">
    <div class="check-ring">
      <svg viewBox="0 0 24 24"><path d="M4 12.5l5 5L20 6"></path></svg>
    </div>
  </div>
  <h1>{saudacao}</h1>
  <p class="sub">Sua conexão foi liberada.<br>Aproveite o <b>{titulo}</b>!</p>
  <div class="progress"><div class="progress-bar"></div></div>
  <p class="status" id="statusMsg">Finalizando conexão&hellip;</p>
</div>
<script>
setTimeout(function(){{ window.location.replace('{login_js}'); }}, 2200);
setTimeout(function(){{
  var s=document.getElementById('statusMsg');
  if(s) s.innerHTML='Demorando? <a href="{login_href}">Clique aqui</a>';
}}, 5000);
</script>
<noscript><p style="text-align:center;margin-top:16px;position:relative;z-index:1"><a href="{login_href}" style="color:{cor}">Clique aqui para navegar</a></p></noscript>
</body>
</html>"""


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
            # Mesma checagem de duplicidade do portal: mesmo hotspot + mesmo
            # telefone ou mesmo nome (case-insensitive) já cadastrado não gera
            # um novo lead.
            dup_filter = Q()
            if telefone:
                dup_filter |= Q(telefone=telefone)
            if nome:
                dup_filter |= Q(nome__iexact=nome)

            ja_existe = bool(dup_filter) and HotspotLead.objects.filter(hotspot=h).filter(dup_filter).exists()

            if not ja_existe:
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
