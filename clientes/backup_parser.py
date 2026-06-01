"""
Parsers de backup de configuração por vendor.
Extrai: IPs, BGP peers, VLANs, interfaces WAN, hostname.
"""
import re
import ipaddress


def _is_public_ip(ip_str):
    try:
        return not ipaddress.ip_address(ip_str).is_private
    except ValueError:
        return False


def _mascara_para_prefix(mascara):
    """Converte 255.255.255.0 → 24."""
    try:
        return ipaddress.IPv4Network(f'0.0.0.0/{mascara}', strict=False).prefixlen
    except Exception:
        return None


def _detectar_vendor_backup(conteudo):
    """Detecta o vendor pelo cabeçalho e conteúdo do backup."""
    cabecalho = conteudo[:2000].lower()
    if 'template: backup mikrotik' in cabecalho or 'routeros' in cabecalho or '/ip address add' in cabecalho:
        return 'mikrotik'
    if 'template: backup huawei' in cabecalho or 'sysname' in cabecalho[:500] or 'peer.*as-number' in cabecalho:
        if 'ip address' in cabecalho and 'as-number' in cabecalho:
            return 'huawei'
    if 'zxan' in cabecalho or 'zte' in cabecalho or 'gpon-olt' in cabecalho:
        return 'zte'
    if 'routeros' in cabecalho or '/routing bgp' in cabecalho or '/ip route' in cabecalho:
        return 'mikrotik'
    if 'dmos' in cabecalho or 'datacom' in cabecalho or 'dm4000' in cabecalho or 'dm2500' in cabecalho:
        return 'datacom'
    if 'junos' in cabecalho or 'juniper' in cabecalho or 'set interfaces' in cabecalho:
        return 'juniper'
    if 'a10 thunder' in cabecalho or 'acos' in cabecalho or 'a10networks' in cabecalho:
        return 'a10'
    if 'hillstone' in cabecalho or 'stoneos' in cabecalho:
        return 'hillstone'
    if 'building configuration' in cabecalho or 'current configuration' in cabecalho:
        return 'cisco'
    if 'sysname' in cabecalho[:500] or 'display version' in cabecalho or 'vrp' in cabecalho:
        return 'huawei'
    return 'desconhecido'


# ─── MikroTik (RouterOS /export terse) ───────────────────────────────────────

def parse_mikrotik(conteudo, nome_equip=''):
    ips, bgp, vlans, hostname = [], [], [], nome_equip

    # Hostname (sysname do RouterOS aparece no comentário do export)
    m = re.search(r'#\s*model\s*=\s*(.+)', conteudo)
    modelo = m.group(1).strip() if m else ''

    # IPs: /ip address add address=X.X.X.X/YY interface=NAME [comment=...]
    for m in re.finditer(
        r'/ip address add .*?address=([\d./]+).*?interface=([^\s]+)', conteudo
    ):
        ip_prefix, iface = m.group(1), m.group(2).strip('"')
        ips.append({'ip': ip_prefix, 'interface': iface, 'equipamento': nome_equip})

    # BGP ROS7: /routing bgp connection add name=DESCRICAO remote.address=X/32 remote.as=Y
    for m in re.finditer(
        r'/routing bgp connection add[^\n]*?name=(?:"([^"]+)"|(\S+))[^\n]*?remote\.address=([\d.]+)/\d+[^\n]*?remote\.as=(\d+)',
        conteudo,
    ):
        desc   = (m.group(1) or m.group(2) or '').strip()
        bgp.append({'peer_ip': m.group(3), 'peer_as': m.group(4),
                    'equipamento': nome_equip, 'descricao': desc})
    # ROS7 formato multiline — ordem dos campos pode variar: pega pelo IP
    if not bgp:
        for m in re.finditer(
            r'/routing bgp connection add.*?remote\.address=([\d.]+)/\d+.*?(?:remote\.)?as=(\d+)',
            conteudo,
        ):
            # tenta pegar nome da mesma linha
            linha = m.group(0)
            mn = re.search(r'\bname=(?:"([^"]+)"|(\S+))', linha)
            desc = (mn.group(1) or mn.group(2) if mn else '').strip()
            bgp.append({'peer_ip': m.group(1), 'peer_as': m.group(2),
                        'equipamento': nome_equip, 'descricao': desc})

    # BGP ROS6: remote-address=X remote-as=Y [name=DESC | comment="DESC"]
    for m in re.finditer(
        r'/routing bgp peer add[^\n]*?remote-address=([\d.]+)[^\n]*?remote-as=(\d+)[^\n]*',
        conteudo,
    ):
        linha = m.group(0)
        # tenta name= primeiro, depois comment=
        mn = re.search(r'\bname=(?:"([^"]+)"|(\S+))', linha)
        mc = re.search(r'\bcomment="([^"]+)"', linha)
        desc = (mn.group(1) or mn.group(2) if mn else '') or (mc.group(1) if mc else '')
        bgp.append({'peer_ip': m.group(1), 'peer_as': m.group(2),
                    'equipamento': nome_equip, 'descricao': desc.strip()})

    # AS local: /routing bgp instance add as=XXXXX
    as_local = ''
    m = re.search(r'/routing bgp instance add as=(\d+)', conteudo)
    if not m:
        m = re.search(r'/routing bgp template set.*?as=(\d+)', conteudo)
    if m:
        as_local = m.group(1)
        for b in bgp:
            b['as_local'] = as_local

    # VLANs: /interface vlan add name=NAME vlan-id=ID
    seen_vlans = set()
    for m in re.finditer(
        r'/interface vlan add .*?name=(?:"([^"]+)"|(\S+)).*?vlan-id=(\d+)', conteudo
    ):
        nome = (m.group(1) or m.group(2) or '').strip()
        vid  = m.group(3)
        if vid not in seen_vlans:
            vlans.append({'id': vid, 'nome': nome, 'equipamento': nome_equip})
            seen_vlans.add(vid)

    return {'ips': ips, 'bgp': bgp, 'vlans': vlans,
            'modelo': modelo, 'as_local': as_local}


# ─── Cisco / IOS (Parks, Intelbras, genérico) ────────────────────────────────

def parse_cisco(conteudo, nome_equip=''):
    ips, bgp, vlans = [], [], []
    modelo, as_local = '', ''

    # hostname
    m = re.search(r'^hostname\s+(\S+)', conteudo, re.MULTILINE)
    if m:
        hostname_cfg = m.group(1)
    else:
        hostname_cfg = nome_equip

    # IPs: ip address X.X.X.X M.M.M.M (dentro de bloco interface)
    iface_atual = ''
    for linha in conteudo.splitlines():
        m_iface = re.match(r'^interface\s+(.+)', linha)
        if m_iface:
            iface_atual = m_iface.group(1).strip()
            continue
        m_ip = re.match(r'\s+ip address ([\d.]+) ([\d.]+)', linha)
        if m_ip and iface_atual:
            ip  = m_ip.group(1)
            msk = m_ip.group(2)
            pfx = _mascara_para_prefix(msk)
            ips.append({
                'ip': f'{ip}/{pfx}' if pfx else ip,
                'interface': iface_atual,
                'equipamento': nome_equip,
            })

    # BGP neighbors: neighbor X.X.X.X remote-as Y [description ...]
    bgp_as_m = re.search(r'^router bgp (\d+)', conteudo, re.MULTILINE)
    as_local  = bgp_as_m.group(1) if bgp_as_m else ''

    desc_map = {}
    for m in re.finditer(r'neighbor ([\d.]+) description (.+)', conteudo):
        desc_map[m.group(1)] = m.group(2).strip()
    for m in re.finditer(r'neighbor ([\d.]+) remote-as (\d+)', conteudo):
        peer_ip = m.group(1)
        bgp.append({
            'peer_ip': peer_ip, 'peer_as': m.group(2),
            'as_local': as_local, 'equipamento': nome_equip,
            'descricao': desc_map.get(peer_ip, ''),
        })

    # VLANs: vlan ID / name NAME
    vid_atual = None
    for linha in conteudo.splitlines():
        m_vid = re.match(r'^vlan (\d+)', linha)
        if m_vid:
            vid_atual = m_vid.group(1)
            vlans.append({'id': vid_atual, 'nome': '', 'equipamento': nome_equip})
            continue
        if vid_atual:
            m_vname = re.match(r'\s+name\s+(.+)', linha)
            if m_vname:
                vlans[-1]['nome'] = m_vname.group(1).strip()
            else:
                vid_atual = None

    return {'ips': ips, 'bgp': bgp, 'vlans': vlans,
            'modelo': modelo, 'as_local': as_local}


# ─── Huawei VRP ──────────────────────────────────────────────────────────────

def parse_huawei(conteudo, nome_equip=''):
    ips, bgp, vlans, ospf_list, vsi_list, l2vc_list = [], [], [], [], [], []
    modelo, as_local = '', ''

    m = re.search(r'^sysname\s+(\S+)', conteudo, re.MULTILINE)
    hostname_cfg = m.group(1) if m else nome_equip

    # ── IPs: ip address X.X.X.X M.M.M.M  (prefixlen ou máscara) ─────────────
    iface_atual = ''
    for linha in conteudo.splitlines():
        m_iface = re.match(r'^interface\s+(.+)', linha)
        if m_iface:
            iface_atual = m_iface.group(1).strip()
            continue
        m_ip = re.match(r'\s+ip address ([\d.]+) ([\d.]{7,15}|\d{1,2})$', linha)
        if m_ip and iface_atual:
            ip  = m_ip.group(1)
            msk = m_ip.group(2)
            if '.' in msk:
                pfx = _mascara_para_prefix(msk)
                ip_pfx = f'{ip}/{pfx}' if pfx else ip
            else:
                ip_pfx = f'{ip}/{msk}'
            ips.append({'ip': ip_pfx, 'interface': iface_atual, 'equipamento': nome_equip})

    # ── BGP ───────────────────────────────────────────────────────────────────
    bgp_as_m = re.search(r'^bgp (\d+)', conteudo, re.MULTILINE)
    as_local  = bgp_as_m.group(1) if bgp_as_m else ''
    desc_map  = {}
    for m in re.finditer(r'peer ([\d.]+) description (.+)', conteudo):
        desc_map[m.group(1)] = m.group(2).strip()
    for m in re.finditer(r'peer ([\d.:a-fA-F]+) as-number (\d+)', conteudo):
        peer_ip = m.group(1)
        if ':' in peer_ip:
            continue
        bgp.append({
            'peer_ip': peer_ip, 'peer_as': m.group(2),
            'as_local': as_local, 'equipamento': nome_equip,
            'descricao': desc_map.get(peer_ip, ''),
        })

    # ── VLANs: vlan batch / vlan ID + description ────────────────────────────
    seen = set()
    for m in re.finditer(r'^vlan (\d+)', conteudo, re.MULTILINE):
        vid = m.group(1)
        if vid not in seen:
            vlans.append({'id': vid, 'nome': '', 'equipamento': nome_equip})
            seen.add(vid)
    for m in re.finditer(r'vlan batch ([\d\s]+(?:to[\d\s]+)?)', conteudo, re.IGNORECASE):
        for part in m.group(1).split():
            if part.isdigit() and part not in seen:
                vlans.append({'id': part, 'nome': '', 'equipamento': nome_equip})
                seen.add(part)
    desc_atual = None
    for linha in conteudo.splitlines():
        m_vid = re.match(r'^vlan (\d+)', linha)
        if m_vid:
            desc_atual = m_vid.group(1)
        elif desc_atual:
            m_desc = re.match(r'\s+description\s+(.+)', linha)
            if m_desc:
                for v in vlans:
                    if v['id'] == desc_atual and not v['nome']:
                        v['nome'] = m_desc.group(1).strip()
                        break

    # ── OSPF ──────────────────────────────────────────────────────────────────
    # Formato VRP: 'ospf N router-id X.X.X.X' seguido de blocos 'area A.B.C.D'
    ospf_proc = None
    for linha in conteudo.splitlines():
        m_proc = re.match(r'^ospf (\d+)(?:\s+router-id\s+([\d.]+))?', linha)
        if m_proc:
            ospf_proc = {
                'process': m_proc.group(1),
                'router_id': m_proc.group(2) or '',
                'areas': [],
                'equipamento': nome_equip,
            }
            ospf_list.append(ospf_proc)
            continue
        if ospf_proc:
            m_area = re.match(r'\s+area\s+([\d.]+)', linha)
            if m_area:
                area = m_area.group(1)
                if area not in ospf_proc['areas']:
                    ospf_proc['areas'].append(area)
            # router-id pode aparecer como sub-comando também
            m_rid = re.match(r'\s+router-id\s+([\d.]+)', linha)
            if m_rid and not ospf_proc['router_id']:
                ospf_proc['router_id'] = m_rid.group(1)
            # sai do bloco ospf quando linha começa sem espaço e não é comentário
            if re.match(r'^[a-z#]', linha) and not re.match(r'^ospf', linha):
                ospf_proc = None

    # ── VSI (MPLS L2VPN Virtual Switch Instance) ──────────────────────────────
    # Formato: 'vsi NAME [static]' / '  vsi-id N' / '  peer X.X.X.X [vc-id N]'
    vsi_atual = None
    for linha in conteudo.splitlines():
        m_vsi = re.match(r'^vsi\s+(\S+)', linha)
        if m_vsi:
            vsi_nome = m_vsi.group(1)
            # Exclui blocos que não são VSI (ex: vsi-id sozinho no topo)
            if vsi_nome.lower() not in ('id',):
                vsi_atual = {'nome': vsi_nome, 'vsi_id': '', 'peers': [], 'equipamento': nome_equip}
                vsi_list.append(vsi_atual)
            continue
        if vsi_atual:
            m_vsid = re.match(r'\s+vsi-id\s+(\d+)', linha)
            if m_vsid:
                vsi_atual['vsi_id'] = m_vsid.group(1)
            m_peer = re.match(r'\s+peer\s+([\d.]+)', linha)
            if m_peer:
                peer = m_peer.group(1)
                if peer not in vsi_atual['peers']:
                    vsi_atual['peers'].append(peer)
            if re.match(r'^[a-z#]', linha) and not re.match(r'^vsi', linha):
                vsi_atual = None

    # ── L2VC / VPWS ───────────────────────────────────────────────────────────
    # Interface-level: 'mpls l2vc X.X.X.X N'
    for m in re.finditer(
        r'mpls l2vc\s+([\d.]+)\s+(\d+)',
        conteudo, re.MULTILINE
    ):
        l2vc_list.append({
            'peer': m.group(1), 'vc_id': m.group(2),
            'interface': '', 'tipo': 'l2vc', 'equipamento': nome_equip,
        })

    # Interface com binding VSI: 'l2 binding vsi NAME'
    iface_atual = ''
    for linha in conteudo.splitlines():
        m_iface = re.match(r'^interface\s+(.+)', linha)
        if m_iface:
            iface_atual = m_iface.group(1).strip()
        m_bind = re.match(r'\s+l2 binding vsi\s+(\S+)', linha)
        if m_bind and iface_atual:
            l2vc_list.append({
                'peer': '', 'vc_id': m_bind.group(1),
                'interface': iface_atual, 'tipo': 'vsi-binding', 'equipamento': nome_equip,
            })

    # VPWS pw no bloco l2vpn: 'connection NAME' / 'peer X vc-id N'
    conn_atual = ''
    for linha in conteudo.splitlines():
        m_conn = re.match(r'\s+connection\s+(\S+)', linha)
        if m_conn:
            conn_atual = m_conn.group(1)
        m_pw = re.match(r'\s+peer\s+([\d.]+)\s+vc-id\s+(\d+)', linha)
        if m_pw and conn_atual:
            l2vc_list.append({
                'peer': m_pw.group(1), 'vc_id': m_pw.group(2),
                'interface': conn_atual, 'tipo': 'vpws', 'equipamento': nome_equip,
            })

    return {
        'ips': ips, 'bgp': bgp, 'vlans': vlans,
        'ospf': ospf_list, 'vsi': vsi_list, 'l2vc': l2vc_list,
        'modelo': modelo, 'as_local': as_local,
    }


# ─── ZTE (CLI parecido com Cisco) ────────────────────────────────────────────

def parse_zte(conteudo, nome_equip=''):
    return parse_cisco(conteudo, nome_equip)


# ─── Datacom DmOS ─────────────────────────────────────────────────────────────

def parse_datacom(conteudo, nome_equip=''):
    ips, bgp, vlans, ospf_list, vsi_list, l2vc_list = [], [], [], [], [], []
    modelo, as_local = '', ''

    m = re.search(r'^hostname\s+(\S+)', conteudo, re.MULTILINE)
    hostname_cfg = m.group(1) if m else nome_equip

    m = re.search(r'running on\s+(\S+)', conteudo, re.IGNORECASE)
    if m:
        modelo = m.group(1).strip()

    # ── IPs ───────────────────────────────────────────────────────────────────
    iface_atual = ''
    for linha in conteudo.splitlines():
        m_iface = re.match(r'^interface\s+(.+)', linha)
        if m_iface:
            iface_atual = m_iface.group(1).strip()
            continue
        m_ip = re.match(r'\s+ip address ([\d.]+/\d+)', linha)
        if m_ip and iface_atual:
            ips.append({'ip': m_ip.group(1), 'interface': iface_atual, 'equipamento': nome_equip})
            continue
        m_ip2 = re.match(r'\s+ip address ([\d.]+) ([\d.]+)', linha)
        if m_ip2 and iface_atual:
            pfx = _mascara_para_prefix(m_ip2.group(2))
            ips.append({
                'ip': f'{m_ip2.group(1)}/{pfx}' if pfx else m_ip2.group(1),
                'interface': iface_atual, 'equipamento': nome_equip,
            })

    # ── BGP ───────────────────────────────────────────────────────────────────
    bgp_as_m = re.search(r'^router bgp (\d+)', conteudo, re.MULTILINE)
    as_local  = bgp_as_m.group(1) if bgp_as_m else ''
    desc_map  = {}
    for m in re.finditer(r'neighbor ([\d.]+) description (.+)', conteudo):
        desc_map[m.group(1)] = m.group(2).strip()
    for m in re.finditer(r'neighbor ([\d.]+) remote-as (\d+)', conteudo):
        peer_ip = m.group(1)
        bgp.append({
            'peer_ip': peer_ip, 'peer_as': m.group(2),
            'as_local': as_local, 'equipamento': nome_equip,
            'descricao': desc_map.get(peer_ip, ''),
        })

    # ── VLANs ─────────────────────────────────────────────────────────────────
    vid_atual = None
    for linha in conteudo.splitlines():
        m_vid = re.match(r'^vlan (\d+)', linha)
        if m_vid:
            vid_atual = m_vid.group(1)
            vlans.append({'id': vid_atual, 'nome': '', 'equipamento': nome_equip})
            continue
        if vid_atual:
            m_vname = re.match(r'\s+name\s+(.+)', linha)
            if m_vname:
                vlans[-1]['nome'] = m_vname.group(1).strip()
            else:
                vid_atual = None

    # ── OSPF ──────────────────────────────────────────────────────────────────
    # DmOS: 'router ospf N' / '  router-id X' / '  network X/prefix area A'
    # ou formato wildcard: '  network X M area A'
    ospf_proc = None
    for linha in conteudo.splitlines():
        m_proc = re.match(r'^router ospf (\d+)', linha)
        if m_proc:
            ospf_proc = {
                'process': m_proc.group(1),
                'router_id': '',
                'areas': [],
                'equipamento': nome_equip,
            }
            ospf_list.append(ospf_proc)
            continue
        if ospf_proc:
            m_rid = re.match(r'\s+(?:ospf\s+)?router-id\s+([\d.]+)', linha)
            if m_rid:
                ospf_proc['router_id'] = m_rid.group(1)
            m_net = re.match(r'\s+network\s+[\d./]+(?:\s+[\d.]+)?\s+area\s+([\d.]+)', linha)
            if m_net:
                area = m_net.group(1)
                if area not in ospf_proc['areas']:
                    ospf_proc['areas'].append(area)
            if re.match(r'^[a-z!]', linha):
                ospf_proc = None

    # ── VSI ───────────────────────────────────────────────────────────────────
    # DmOS: 'vsi NAME' / '  vsi-id N' / '  peer X.X.X.X'
    vsi_atual = None
    for linha in conteudo.splitlines():
        m_vsi = re.match(r'^vsi\s+(\S+)', linha)
        if m_vsi:
            vsi_nome = m_vsi.group(1)
            if vsi_nome.lower() not in ('id',):
                vsi_atual = {'nome': vsi_nome, 'vsi_id': '', 'peers': [], 'equipamento': nome_equip}
                vsi_list.append(vsi_atual)
            continue
        if vsi_atual:
            m_vsid = re.match(r'\s+vsi-id\s+(\d+)', linha)
            if m_vsid:
                vsi_atual['vsi_id'] = m_vsid.group(1)
            m_peer = re.match(r'\s+peer\s+([\d.]+)', linha)
            if m_peer:
                peer = m_peer.group(1)
                if peer not in vsi_atual['peers']:
                    vsi_atual['peers'].append(peer)
            if re.match(r'^[a-z!#]', linha) and not re.match(r'^vsi', linha):
                vsi_atual = None

    # ── L2VC / VPWS / xconnect ────────────────────────────────────────────────
    # Interface-level xconnect: 'xconnect X.X.X.X N encapsulation mpls'
    iface_atual = ''
    for linha in conteudo.splitlines():
        m_iface = re.match(r'^interface\s+(.+)', linha)
        if m_iface:
            iface_atual = m_iface.group(1).strip()
        m_xc = re.match(r'\s+xconnect\s+([\d.]+)\s+(\d+)', linha)
        if m_xc and iface_atual:
            l2vc_list.append({
                'peer': m_xc.group(1), 'vc_id': m_xc.group(2),
                'interface': iface_atual, 'tipo': 'xconnect', 'equipamento': nome_equip,
            })

    # Bloco l2vpn xconnect group: 'neighbor X pw-id N'
    conn_atual = ''
    for linha in conteudo.splitlines():
        m_p2p = re.match(r'\s+p2p\s+(\S+)', linha)
        if m_p2p:
            conn_atual = m_p2p.group(1)
        m_nb = re.match(r'\s+neighbor\s+([\d.]+)\s+pw-id\s+(\d+)', linha)
        if m_nb:
            l2vc_list.append({
                'peer': m_nb.group(1), 'vc_id': m_nb.group(2),
                'interface': conn_atual, 'tipo': 'vpws', 'equipamento': nome_equip,
            })

    return {
        'ips': ips, 'bgp': bgp, 'vlans': vlans,
        'ospf': ospf_list, 'vsi': vsi_list, 'l2vc': l2vc_list,
        'modelo': modelo, 'as_local': as_local,
    }


# ─── Juniper JunOS (set-style config) ─────────────────────────────────────────

def parse_juniper(conteudo, nome_equip=''):
    """
    Juniper JunOS — formato 'set' (show configuration | display set).
    - IPs: 'set interfaces ge-X/Y/Z unit N family inet address A.B.C.D/P'
    - BGP: 'set protocols bgp group G neighbor X.X.X.X peer-as Y'
    - AS:  'set routing-options autonomous-system N'
    - VLANs: 'set vlans NAME vlan-id ID'
    """
    ips, bgp, vlans = [], [], []
    modelo, as_local = '', ''

    m = re.search(r'^set system host-name\s+(\S+)', conteudo, re.MULTILINE)
    hostname_cfg = m.group(1) if m else nome_equip

    # Modelo
    m = re.search(r'^#\s*Model:\s*(\S+)', conteudo, re.MULTILINE)
    if not m:
        m = re.search(r'Juniper Networks\s+(\S+)', conteudo)
    if m:
        modelo = m.group(1).strip()

    # IPs: set interfaces <iface> unit <N> family inet address <IP/pfx>
    for m in re.finditer(
        r'^set interfaces (\S+) unit (\S+) family inet address ([\d.]+/\d+)',
        conteudo, re.MULTILINE
    ):
        iface = f'{m.group(1)}.{m.group(2)}'
        ips.append({'ip': m.group(3), 'interface': iface, 'equipamento': nome_equip})

    # AS local
    m = re.search(r'^set routing-options autonomous-system (\d+)', conteudo, re.MULTILINE)
    as_local = m.group(1) if m else ''

    # BGP neighbors — formato set-style
    # Captura descrições e peer-as por grupo E por neighbor (neighbor herda do grupo)
    group_desc  = {}   # group_name → description
    group_peras = {}   # group_name → peer-as
    for m in re.finditer(r'^set protocols bgp group (\S+) description (.+)', conteudo, re.MULTILINE):
        group_desc[m.group(1)] = m.group(2).strip().strip('"')
    for m in re.finditer(r'^set protocols bgp group (\S+) peer-as (\d+)', conteudo, re.MULTILINE):
        group_peras[m.group(1)] = m.group(2)

    # Descrição individual por neighbor (sobrepõe grupo)
    nb_desc  = {}   # ip → description
    nb_peras = {}   # ip → peer-as
    for m in re.finditer(
        r'^set protocols bgp group (\S+) neighbor ([\d.]+) description (.+)',
        conteudo, re.MULTILINE,
    ):
        nb_desc[m.group(2)] = m.group(3).strip().strip('"')
    for m in re.finditer(
        r'^set protocols bgp group (\S+) neighbor ([\d.]+) peer-as (\d+)',
        conteudo, re.MULTILINE,
    ):
        nb_peras[m.group(2)] = m.group(3)

    # Coletar todos os neighbors com seu grupo
    seen_nb = set()
    for m in re.finditer(
        r'^set protocols bgp group (\S+) neighbor ([\d.]+)',
        conteudo, re.MULTILINE,
    ):
        grp, ip = m.group(1), m.group(2)
        if ip in seen_nb:
            continue
        seen_nb.add(ip)
        peer_as = nb_peras.get(ip) or group_peras.get(grp) or ''
        desc    = nb_desc.get(ip) or group_desc.get(grp) or ''
        if not desc:
            desc = grp  # usa nome do grupo como fallback
        bgp.append({
            'peer_ip': ip, 'peer_as': peer_as,
            'as_local': as_local, 'equipamento': nome_equip,
            'descricao': desc,
        })

    # VLANs: set vlans <NAME> vlan-id <ID>
    seen = set()
    for m in re.finditer(r'^set vlans (\S+) vlan-id (\d+)', conteudo, re.MULTILINE):
        vid = m.group(2)
        if vid not in seen:
            vlans.append({'id': vid, 'nome': m.group(1), 'equipamento': nome_equip})
            seen.add(vid)

    return {'ips': ips, 'bgp': bgp, 'vlans': vlans, 'modelo': modelo, 'as_local': as_local}


# ─── A10 Networks Thunder (ACOS) ──────────────────────────────────────────────

def parse_a10(conteudo, nome_equip=''):
    """
    A10 Thunder ACOS — show running-config.
    - IPs: 'ip address X.X.X.X /prefix' (espaço antes da barra) ou 'X.X.X.X M.M.M.M'
    - BGP: 'neighbor X remote-as Y'
    - AS:  'router bgp N'
    - VLANs: 'vlan N' + 'name NAME'
    """
    ips, bgp, vlans = [], [], []
    modelo, as_local = '', ''

    m = re.search(r'^hostname\s+(\S+)', conteudo, re.MULTILINE)
    hostname_cfg = m.group(1) if m else nome_equip

    # Modelo: "A10 Thunder 1040" ou "Thunder 6435"
    m = re.search(r'Thunder\s+([\w\-]+)', conteudo, re.IGNORECASE)
    if m:
        modelo = f'Thunder {m.group(1).strip()}'

    # IPs: dentro de blocos interface
    iface_atual = ''
    for linha in conteudo.splitlines():
        m_iface = re.match(r'^interface\s+(.+)', linha)
        if m_iface:
            iface_atual = m_iface.group(1).strip()
            continue
        # 'ip address X.X.X.X /YY'  ou  'ip address X.X.X.X YY'
        m_ip = re.match(r'\s+ip address ([\d.]+)\s+/(\d+)', linha)
        if m_ip and iface_atual:
            ips.append({
                'ip': f'{m_ip.group(1)}/{m_ip.group(2)}',
                'interface': iface_atual, 'equipamento': nome_equip,
            })
            continue
        m_ip2 = re.match(r'\s+ip address ([\d.]+) ([\d.]+)', linha)
        if m_ip2 and iface_atual:
            pfx = _mascara_para_prefix(m_ip2.group(2))
            ips.append({
                'ip': f'{m_ip2.group(1)}/{pfx}' if pfx else m_ip2.group(1),
                'interface': iface_atual, 'equipamento': nome_equip,
            })

    # BGP
    bgp_as_m = re.search(r'^router bgp (\d+)', conteudo, re.MULTILINE)
    as_local  = bgp_as_m.group(1) if bgp_as_m else ''
    desc_map  = {}
    for m in re.finditer(r'neighbor ([\d.]+) description (.+)', conteudo):
        desc_map[m.group(1)] = m.group(2).strip()
    for m in re.finditer(r'neighbor ([\d.]+) remote-as (\d+)', conteudo):
        peer_ip = m.group(1)
        bgp.append({
            'peer_ip': peer_ip, 'peer_as': m.group(2),
            'as_local': as_local, 'equipamento': nome_equip,
            'descricao': desc_map.get(peer_ip, ''),
        })

    # VLANs
    vid_atual = None
    for linha in conteudo.splitlines():
        m_vid = re.match(r'^vlan (\d+)', linha)
        if m_vid:
            vid_atual = m_vid.group(1)
            vlans.append({'id': vid_atual, 'nome': '', 'equipamento': nome_equip})
            continue
        if vid_atual:
            m_vname = re.match(r'\s+name\s+(.+)', linha)
            if m_vname:
                vlans[-1]['nome'] = m_vname.group(1).strip()
            else:
                vid_atual = None

    return {'ips': ips, 'bgp': bgp, 'vlans': vlans, 'modelo': modelo, 'as_local': as_local}


# ─── Hillstone StoneOS ────────────────────────────────────────────────────────

def parse_hillstone(conteudo, nome_equip=''):
    """
    Hillstone StoneOS — show configuration.
    - IPs: 'ip address X.X.X.X M.M.M.M' dentro de blocos 'interface'
    - BGP: 'neighbor X as Y'  (StoneOS usa 'as' ao invés de 'remote-as')
    - AS:  'router bgp N'
    - Zones/VLANs: 'zone NAME' ou 'vlan N'
    """
    ips, bgp, vlans = [], [], []
    modelo, as_local = '', ''

    m = re.search(r'^hostname\s+(\S+)', conteudo, re.MULTILINE)
    hostname_cfg = m.group(1) if m else nome_equip

    # Modelo: "Hillstone SG-6000-G5150" etc.
    m = re.search(r'Hillstone\s+([\w\-]+)', conteudo, re.IGNORECASE)
    if m:
        modelo = m.group(1).strip()

    # IPs: blocos interface
    iface_atual = ''
    for linha in conteudo.splitlines():
        m_iface = re.match(r'^interface\s+(.+)', linha)
        if m_iface:
            iface_atual = m_iface.group(1).strip()
            continue
        m_ip = re.match(r'\s+ip address ([\d.]+) ([\d.]+)', linha)
        if m_ip and iface_atual:
            pfx = _mascara_para_prefix(m_ip.group(2))
            ips.append({
                'ip': f'{m_ip.group(1)}/{pfx}' if pfx else m_ip.group(1),
                'interface': iface_atual, 'equipamento': nome_equip,
            })
        # CIDR direto: ip address X.X.X.X/YY
        m_ip2 = re.match(r'\s+ip address ([\d.]+/\d+)', linha)
        if m_ip2 and iface_atual:
            ips.append({'ip': m_ip2.group(1), 'interface': iface_atual, 'equipamento': nome_equip})

    # BGP — StoneOS: 'neighbor X as Y'
    bgp_as_m = re.search(r'^router bgp (\d+)', conteudo, re.MULTILINE)
    as_local  = bgp_as_m.group(1) if bgp_as_m else ''
    desc_map  = {}
    for m in re.finditer(r'neighbor ([\d.]+) description (.+)', conteudo):
        desc_map[m.group(1)] = m.group(2).strip()
    # 'neighbor X as Y' (StoneOS) ou 'neighbor X remote-as Y' (modos compatíveis)
    for m in re.finditer(r'neighbor ([\d.]+)\s+(?:remote-as|as)\s+(\d+)', conteudo):
        peer_ip = m.group(1)
        bgp.append({
            'peer_ip': peer_ip, 'peer_as': m.group(2),
            'as_local': as_local, 'equipamento': nome_equip,
            'descricao': desc_map.get(peer_ip, ''),
        })

    # VLANs
    seen = set()
    for m in re.finditer(r'^vlan (\d+)', conteudo, re.MULTILINE):
        vid = m.group(1)
        if vid not in seen:
            vlans.append({'id': vid, 'nome': '', 'equipamento': nome_equip})
            seen.add(vid)
    # Zones (Hillstone usa zonas de segurança — registra como "VLAN" lógica)
    for m in re.finditer(r'^zone\s+"?(\w[\w\-]*)"?', conteudo, re.MULTILINE):
        nome_zona = m.group(1)
        if nome_zona.lower() not in ('trust', 'untrust', 'dmz', 'mgt'):
            vlans.append({'id': nome_zona, 'nome': nome_zona, 'equipamento': nome_equip})

    return {'ips': ips, 'bgp': bgp, 'vlans': vlans, 'modelo': modelo, 'as_local': as_local}


# ─── Dispatcher ──────────────────────────────────────────────────────────────

def parse_backup(conteudo, nome_equip='', vendor_hint=''):
    vendor = vendor_hint or _detectar_vendor_backup(conteudo)
    if vendor == 'mikrotik':
        resultado = parse_mikrotik(conteudo, nome_equip)
    elif vendor == 'huawei':
        resultado = parse_huawei(conteudo, nome_equip)
    elif vendor == 'datacom':
        resultado = parse_datacom(conteudo, nome_equip)
    elif vendor == 'juniper':
        resultado = parse_juniper(conteudo, nome_equip)
    elif vendor == 'a10':
        resultado = parse_a10(conteudo, nome_equip)
    elif vendor == 'hillstone':
        resultado = parse_hillstone(conteudo, nome_equip)
    elif vendor in ('zte', 'cisco', 'parks', 'intelbras'):
        resultado = parse_cisco(conteudo, nome_equip)
    else:
        # Tenta inferir pelo conteúdo
        if '/ip address add' in conteudo or '/routing bgp' in conteudo:
            resultado = parse_mikrotik(conteudo, nome_equip)
        elif 'sysname' in conteudo[:500] or 'as-number' in conteudo:
            resultado = parse_huawei(conteudo, nome_equip)
        elif 'set interfaces' in conteudo and 'family inet' in conteudo:
            resultado = parse_juniper(conteudo, nome_equip)
        elif 'dmos' in conteudo[:500].lower() or 'datacom' in conteudo[:500].lower():
            resultado = parse_datacom(conteudo, nome_equip)
        elif 'ip address' in conteudo and 'remote-as' in conteudo:
            resultado = parse_cisco(conteudo, nome_equip)
        else:
            resultado = {'ips': [], 'bgp': [], 'vlans': [], 'modelo': '', 'as_local': ''}

    resultado['vendor'] = vendor
    return resultado


# ─── Formatador de artigo Markdown ───────────────────────────────────────────

def formatar_artigo(nome_cliente, hosts_info, from_date):
    """
    Gera o conteúdo Markdown do artigo de infraestrutura do cliente.
    hosts_info: lista de dicts {'nome', 'host', 'porta', 'vendor', 'modelo',
                                'ips', 'bgp', 'vlans', 'ospf', 'vsi', 'l2vc', 'as_local'}
    """
    linhas = [
        f'# Infraestrutura — {nome_cliente}',
        f'> Snapshot automático gerado em {from_date}. '
        f'Atualizado a cada 4 dias a partir dos backups de configuração.',
        '',
    ]

    # ── Seção 1: Hosts ───────────────────────────────────────────────────────
    linhas.append(f'## Hosts ({len(hosts_info)} equipamentos SSH)')
    for h in hosts_info:
        modelo = f' | {h["modelo"]}' if h.get('modelo') else ''
        vendor = h.get('vendor', '').upper() or 'DESCONHECIDO'
        linhas.append(f'- **{h["nome"]}** | `{h["host"]}:{h["porta"]}` | {vendor}{modelo}')
    linhas.append('')

    # ── Seção 2: Endereços IP ────────────────────────────────────────────────
    all_ips = [ip for h in hosts_info for ip in h.get('ips', [])]
    if all_ips:
        linhas.append(f'## Endereços IP ({len(all_ips)} encontrados)')
        by_equip: dict = {}
        for ip in all_ips:
            by_equip.setdefault(ip.get('equipamento', '?'), []).append(ip)
        for equip, lista in by_equip.items():
            linhas.append(f'### {equip}')
            for ip in lista:
                publico = ' ⚠️ PÚBLICO' if _is_public_ip(ip['ip'].split('/')[0]) else ''
                linhas.append(f'- `{ip["ip"]}` → `{ip["interface"]}`{publico}')
        linhas.append('')

    # ── Seção 3: BGP Sessions ────────────────────────────────────────────────
    all_bgp = [b for h in hosts_info for b in h.get('bgp', [])]
    if all_bgp:
        linhas.append(f'## Sessões BGP ({len(all_bgp)} peers)')
        by_equip = {}
        for b in all_bgp:
            by_equip.setdefault(b.get('equipamento', '?'), []).append(b)
        for equip, lista in by_equip.items():
            as_local = lista[0].get('as_local', '?')
            linhas.append(f'### {equip} (AS{as_local})')
            for b in lista:
                desc = f' — {b["descricao"]}' if b.get('descricao') else ''
                linhas.append(f'- AS{b.get("as_local","?")} ↔ AS{b["peer_as"]} | peer: `{b["peer_ip"]}`{desc}')
        linhas.append('')

    # ── Seção 4: OSPF ────────────────────────────────────────────────────────
    all_ospf = [o for h in hosts_info for o in h.get('ospf', [])]
    if all_ospf:
        linhas.append(f'## OSPF ({len(all_ospf)} processos)')
        by_equip = {}
        for o in all_ospf:
            by_equip.setdefault(o.get('equipamento', '?'), []).append(o)
        for equip, lista in by_equip.items():
            linhas.append(f'### {equip}')
            for o in lista:
                rid   = f' | router-id `{o["router_id"]}`' if o.get('router_id') else ''
                areas = ', '.join(o['areas']) if o.get('areas') else '—'
                linhas.append(f'- Processo **{o["process"]}**{rid} | Áreas: {areas}')
        linhas.append('')

    # ── Seção 5: VSI (MPLS L2VPN) ────────────────────────────────────────────
    all_vsi = [v for h in hosts_info for v in h.get('vsi', [])]
    if all_vsi:
        linhas.append(f'## VSI — Virtual Switch Instances ({len(all_vsi)})')
        by_equip = {}
        for v in all_vsi:
            by_equip.setdefault(v.get('equipamento', '?'), []).append(v)
        for equip, lista in by_equip.items():
            linhas.append(f'### {equip}')
            for v in lista:
                vsid  = f' | vsi-id {v["vsi_id"]}' if v.get('vsi_id') else ''
                peers = ', '.join(f'`{p}`' for p in v['peers']) if v.get('peers') else '—'
                linhas.append(f'- **{v["nome"]}**{vsid} | peers: {peers}')
        linhas.append('')

    # ── Seção 6: L2VC / VPWS / xconnect ─────────────────────────────────────
    all_l2vc = [c for h in hosts_info for c in h.get('l2vc', [])]
    if all_l2vc:
        linhas.append(f'## L2VC / VPWS ({len(all_l2vc)} circuitos)')
        by_equip = {}
        for c in all_l2vc:
            by_equip.setdefault(c.get('equipamento', '?'), []).append(c)
        for equip, lista in by_equip.items():
            linhas.append(f'### {equip}')
            for c in lista:
                tipo  = c.get('tipo', 'l2vc').upper()
                iface = f'`{c["interface"]}`' if c.get('interface') else '—'
                peer  = f'`{c["peer"]}`'      if c.get('peer')      else '—'
                vcid  = c.get('vc_id', '?')
                linhas.append(f'- [{tipo}] iface: {iface} | peer: {peer} | vc-id: **{vcid}**')
        linhas.append('')

    # ── Seção 7: VLANs ───────────────────────────────────────────────────────
    all_vlans = [v for h in hosts_info for v in h.get('vlans', [])]
    vlans_com_nome = [v for v in all_vlans if v.get('nome')]
    vlans_sem_nome = [v for v in all_vlans if not v.get('nome')]
    vlans_exibir   = (vlans_com_nome + vlans_sem_nome)[:50]

    if vlans_exibir:
        total_vlans = len(all_vlans)
        exibindo    = len(vlans_exibir)
        nota = f' (exibindo {exibindo} de {total_vlans})' if total_vlans > exibindo else ''
        linhas.append(f'## VLANs{nota}')
        by_equip = {}
        for v in vlans_exibir:
            by_equip.setdefault(v.get('equipamento', '?'), []).append(v)
        for equip, lista in by_equip.items():
            linhas.append(f'### {equip}')
            lista_ord = sorted(lista, key=lambda x: int(x['id']) if str(x['id']).isdigit() else 9999)
            for v in lista_ord:
                nome = f' — {v["nome"]}' if v.get('nome') else ''
                linhas.append(f'- VLAN **{v["id"]}**{nome}')
        linhas.append('')

    # ── Resumo final ─────────────────────────────────────────────────────────
    linhas.append('## Resumo')
    linhas.append(f'- Equipamentos: **{len(hosts_info)}**')
    linhas.append(f'- Endereços IP encontrados: **{len(all_ips)}**')
    linhas.append(f'- Peers BGP: **{len(all_bgp)}**')
    linhas.append(f'- Processos OSPF: **{len(all_ospf)}**')
    linhas.append(f'- VSI: **{len(all_vsi)}**')
    linhas.append(f'- Circuitos L2VC/VPWS: **{len(all_l2vc)}**')
    linhas.append(f'- VLANs: **{len(all_vlans)}**')

    return '\n'.join(linhas)


# ─── Artigo BGP dedicado ──────────────────────────────────────────────────────

def formatar_artigo_bgp(nome_cliente, hosts_info, from_date):
    """
    Gera artigo Markdown dedicado ao BGP do cliente.
    Formato focado em lookup descrição→IP para o Agent NOC.

    hosts_info: lista com chaves 'nome', 'host', 'vendor', 'modelo',
                'bgp' (lista de peers), 'as_local'
    """
    # Coletar todos os peers de todos os equipamentos
    all_peers = []
    for h in hosts_info:
        for b in h.get('bgp', []):
            if b.get('peer_ip'):
                all_peers.append({
                    'descricao':  (b.get('descricao') or '').strip(),
                    'peer_ip':    b['peer_ip'],
                    'peer_as':    b.get('peer_as', ''),
                    'as_local':   b.get('as_local', '') or h.get('as_local', ''),
                    'equipamento': b.get('equipamento', '') or h.get('nome', ''),
                    'vendor':     h.get('vendor', ''),
                })

    if not all_peers:
        return ''

    linhas = [
        f'# BGP — {nome_cliente}',
        f'> Snapshot automático gerado em {from_date}. '
        f'Atualizado a cada 4 dias.',
        '',
        '## Como usar',
        'Quando solicitado a verificar uma sessão BGP pela **descrição**, '
        'localize o **IP do peer** nesta tabela e execute o comando adequado no equipamento.',
        '',
    ]

    # ── Tabela de lookup: Descrição → IP ────────────────────────────────────
    # Ordenar: peers com descrição primeiro, depois por descrição alfabética
    peers_com_desc  = sorted([p for p in all_peers if p['descricao']],
                             key=lambda x: x['descricao'].lower())
    peers_sem_desc  = [p for p in all_peers if not p['descricao']]

    linhas.append(f'## Tabela Descrição → IP ({len(all_peers)} peers)')
    linhas.append('')
    linhas.append('| Descrição | IP do Peer | AS Remoto | AS Local | Equipamento |')
    linhas.append('|-----------|-----------|-----------|----------|-------------|')

    for p in peers_com_desc + peers_sem_desc:
        desc = p['descricao'] or '*(sem descrição)*'
        as_rem  = f"AS{p['peer_as']}"  if p.get('peer_as')  else '—'
        as_loc  = f"AS{p['as_local']}" if p.get('as_local') else '—'
        linhas.append(
            f"| {desc} | `{p['peer_ip']}` | {as_rem} | {as_loc} | {p['equipamento']} |"
        )
    linhas.append('')

    # ── Comandos de verificação por vendor ──────────────────────────────────
    linhas.append('## Comandos de verificação por vendor')
    linhas.append('')
    linhas.append('**Huawei VRP:** `display bgp peer <IP> verbose`')
    linhas.append('**Cisco IOS/XE:** `show bgp neighbors <IP>`')
    linhas.append('**MikroTik ROS:** `/routing bgp session print where remote.address=<IP>/32`')
    linhas.append('**Juniper JunOS:** `show bgp neighbor <IP>`')
    linhas.append('**Datacom DmOS:** `show bgp neighbors <IP>`')
    linhas.append('')

    # ── Sessões por equipamento ──────────────────────────────────────────────
    linhas.append('## Sessões por Equipamento')
    by_equip = {}
    for p in all_peers:
        by_equip.setdefault(p['equipamento'], []).append(p)

    for equip, peers in by_equip.items():
        as_local = peers[0].get('as_local', '')
        vendor   = peers[0].get('vendor', '').upper() or ''
        header   = f"### {equip}"
        if as_local:
            header += f" (AS{as_local})"
        if vendor:
            header += f" — {vendor}"
        linhas.append(header)
        for p in sorted(peers, key=lambda x: x['descricao'].lower() if x['descricao'] else 'zzz'):
            desc    = f" — **{p['descricao']}**" if p.get('descricao') else ''
            as_rem  = f" AS{p['peer_as']}" if p.get('peer_as') else ''
            linhas.append(f"- `{p['peer_ip']}`{as_rem}{desc}")
        linhas.append('')

    return '\n'.join(linhas)
