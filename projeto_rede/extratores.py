"""
Extração do estado AS-IS a partir do texto de backup de um equipamento.

Complementa `clientes/backup_parser.py` (que foi escrito para a tela de BGP e
para o artigo do Agent NOC) com o que um documento de arquitetura precisa e o
parser antigo não guarda: contexto de address-family/VRF de cada peer BGP,
route-policies completas (LP, communities, prepend), MPLS/TE/BFD, BNG, MTU por
interface e postura de gerência (telnet/FTP/SNMP).

Regra de ouro deste módulo: nunca devolver segredo. Senhas, chaves, shared-keys
e nomes de community SNMP ficam de fora — só contagens e flags.
"""
import ipaddress
import re


# ═══════════════════════════════════════════════════════════════════════════
# Utilitários
# ═══════════════════════════════════════════════════════════════════════════

def _prefixo(ip, mascara_ou_len):
    """'10.0.0.0' + '255.255.255.0' | '24' → '10.0.0.0/24' (normalizado)."""
    try:
        if '.' in str(mascara_ou_len) and ':' not in ip:
            rede = ipaddress.ip_network(f'{ip}/{mascara_ou_len}', strict=False)
        else:
            rede = ipaddress.ip_network(f'{ip}/{int(mascara_ou_len)}', strict=False)
        return str(rede)
    except (ValueError, TypeError):
        return f'{ip}/{mascara_ou_len}'


def _ip_interface(ip, mascara_ou_len):
    """Endereço de interface com prefixo, preservando o host:
    '172.24.64.1' + '255.255.255.248' → '172.24.64.1/29'."""
    try:
        return str(ipaddress.ip_interface(f'{ip}/{mascara_ou_len}'))
    except ValueError:
        return f'{ip}/{mascara_ou_len}'


def eh_asn_privado(asn):
    try:
        n = int(asn)
    except (TypeError, ValueError):
        return False
    return 64512 <= n <= 65534 or 4200000000 <= n <= 4294967294


def _conteudo_config(conteudo):
    """Corta o cabeçalho do arquivo de backup do CRM (e outros comandos
    anteriores) quando a saída do `display current-configuration` está
    marcada — evita que texto de outro comando vire configuração."""
    marca = re.search(r'Comando:\s*display current-configuration[^\n]*\n=+\n', conteudo)
    if marca:
        return conteudo[marca.end():]
    return conteudo


def _blocos_vrp(linhas):
    """Divide a configuração VRP em blocos de primeiro nível:
    [(cabecalho, [linhas do corpo com indentação original])]."""
    blocos = []
    atual = None
    for linha in linhas:
        if not linha.strip():
            continue
        if linha[0] in ' \t':
            if atual is not None:
                atual[1].append(linha.rstrip())
            continue
        if linha.startswith(('#', '!', '<', 'return')):
            atual = None
            continue
        atual = (linha.strip(), [])
        blocos.append(atual)
    return blocos


# ═══════════════════════════════════════════════════════════════════════════
# Huawei VRP
# ═══════════════════════════════════════════════════════════════════════════

def _nova_interface(nome):
    return {
        'nome': nome, 'descricao': '', 'mtu': None, 'mpls_mtu': None,
        'ips': [], 'ipv6': [], 'vrf': '', 'dot1q': '',
        'ospf': '', 'mpls': False, 'ldp': False, 'te': False, 'rsvp': False,
        'bfd': False, 'l2_vsi': '', 'l2vc': [], 'shutdown': False,
        'trunk_pai': '', 'bas': False, 'l2_terminate': False,
        'tunel_te': False, 'tunel_destino': '', 'tunel_caminho': '',
        'ldp_sync': False, 'bfd_timers': '', 'ospf_custo': '', 'ospf_p2p': False,
        'ppp_mru': None, 'mss': None, 'jumbo': None,
    }


def _timers_bfd(linha):
    """'... min-tx-interval 100 min-rx-interval 100 detect-multiplier 4' → '100/100/4'."""
    p = linha.split()
    val = {}
    for chave in ('min-tx-interval', 'min-rx-interval', 'detect-multiplier'):
        if chave in p and p.index(chave) + 1 < len(p):
            val[chave] = p[p.index(chave) + 1]
    return '/'.join([val.get('min-tx-interval', '?'), val.get('min-rx-interval', '?'),
                     val.get('detect-multiplier', '3')])


def _parse_interface_vrp(cab, corpo):
    nome = cab.split(None, 1)[1].strip() if ' ' in cab else cab
    itf = _nova_interface(nome)
    em_bas = False
    for linha in corpo:
        s = linha.strip()
        # Subvisão `bas` do NE8000: linhas seguintes com 2+ espaços.
        if s == 'bas':
            itf['bas'] = True
            em_bas = True
            continue
        if em_bas and linha.startswith('  '):
            continue
        em_bas = False
        if s.startswith('description '):
            itf['descricao'] = s[12:].strip()
        elif re.match(r'^mtu \d+', s):
            itf['mtu'] = int(s.split()[1])
        elif re.match(r'^mpls mtu \d+', s):
            itf['mpls_mtu'] = int(s.split()[2])
        elif s.startswith('ip address ') and 'unnumbered' not in s:
            p = s.split()
            if len(p) >= 4:
                itf['ips'].append(_ip_interface(p[2], p[3]))
        elif s.startswith('ipv6 address ') and '/' in s:
            itf['ipv6'].append(s.split()[2])
        elif s.startswith('ip binding vpn-instance '):
            itf['vrf'] = s.split()[3]
        elif s.startswith('vlan-type dot1q '):
            itf['dot1q'] = s.split()[2]
        elif re.match(r'^ospf enable \d+ area ', s):
            p = s.split()
            itf['ospf'] = f'{p[2]}/{p[4]}'
        elif s == 'mpls':
            itf['mpls'] = True
        elif s == 'mpls ldp':
            itf['ldp'] = True
        elif s == 'mpls te':
            itf['te'] = True
        elif s == 'mpls rsvp-te':
            itf['rsvp'] = True
        elif s.startswith('jumboframe enable'):
            p = s.split()
            itf['jumbo'] = int(p[2]) if len(p) > 2 and p[2].isdigit() else 9216
        elif s.startswith('ppp mru '):
            itf['ppp_mru'] = int(s.split()[2]) if s.split()[2].isdigit() else None
        elif s.startswith('tcp adjust-mss '):
            itf['mss'] = int(s.split()[2]) if s.split()[2].isdigit() else None
        elif s == 'ospf ldp-sync':
            itf['ldp_sync'] = True
        elif s.startswith('ospf cost '):
            itf['ospf_custo'] = s.split()[2]
        elif s == 'ospf network-type p2p':
            itf['ospf_p2p'] = True
        elif re.match(r'^ospf bfd min-tx-interval', s):
            itf['bfd_timers'] = _timers_bfd(s)
            itf['bfd'] = True
        elif re.match(r'^(ospf|isis) bfd enable', s) or s.startswith('bfd '):
            itf['bfd'] = True
        elif s.startswith('l2 binding vsi '):
            itf['l2_vsi'] = s.split()[3]
        elif s.startswith('mpls l2vc '):
            p = s.split()
            if len(p) >= 4:
                itf['l2vc'].append({'peer': p[2], 'vc_id': p[3],
                                    'mtu': (p[p.index('mtu') + 1] if 'mtu' in p and p.index('mtu') + 1 < len(p) else '')})
        elif s == 'shutdown':
            itf['shutdown'] = True
        elif s.startswith('eth-trunk '):
            itf['trunk_pai'] = 'Eth-Trunk' + s.split()[1]
        elif 'l2-terminate' in s:
            itf['l2_terminate'] = True
        elif s == 'tunnel-protocol mpls te':
            itf['tunel_te'] = True
        elif s.startswith('destination '):
            itf['tunel_destino'] = s.split()[1]
        elif s.startswith('mpls te path explicit-path '):
            itf['tunel_caminho'] = s.split()[4]
    return itf


def _parse_route_policies(blocos):
    policies = {}
    for cab, corpo in blocos:
        m = re.match(r'^route-policy (\S+) (permit|deny) node (\d+)', cab)
        if not m:
            continue
        node = {
            'node': int(m.group(3)), 'acao': m.group(2),
            'if_match': [], 'local_preference': None, 'communities': [],
            'community_aditiva': False, 'community_none': False,
            'prepend': [], 'preferred_value': None, 'med': None,
        }
        for linha in corpo:
            s = linha.strip()
            if s.startswith('if-match '):
                p = s.split()
                if p[1] == 'ip-prefix':
                    node['if_match'].append(('ip-prefix', p[2]))
                elif p[1:4] == ['ipv6', 'address', 'prefix-list']:
                    node['if_match'].append(('ipv6-prefix', p[4]))
                elif p[1] == 'community-filter':
                    node['if_match'].append(('community-filter', ' '.join(p[2:])))
                elif p[1] == 'as-path-filter':
                    node['if_match'].append(('as-path-filter', ' '.join(p[2:])))
                else:
                    node['if_match'].append((p[1], ' '.join(p[2:])))
            elif s.startswith('apply local-preference '):
                node['local_preference'] = int(s.split()[2])
            elif s.startswith('apply community none'):
                node['community_none'] = True
            elif s.startswith('apply community '):
                vals = s.split()[2:]
                node['community_aditiva'] = 'additive' in vals
                node['communities'] += [v for v in vals if v != 'additive']
            elif s.startswith('apply as-path '):
                vals = s.split()[2:]
                if 'additive' in vals:
                    node['prepend'] = [v for v in vals if v.isdigit()]
            elif s.startswith('apply preferred-value '):
                node['preferred_value'] = int(s.split()[2])
            elif s.startswith('apply cost '):
                node['med'] = s.split()[2]
        policies.setdefault(m.group(1), []).append(node)
    for nodes in policies.values():
        nodes.sort(key=lambda n: n['node'])
    return policies


def _parse_prefix_lists(conteudo):
    listas = {}
    padrao = re.compile(
        r'^ip (ip-prefix|ipv6-prefix) (\S+) index (\d+) (permit|deny) (\S+) (\d+)'
        r'(?:\s+match-network)?(?:\s+greater-equal (\d+))?(?:\s+less-equal (\d+))?',
        re.MULTILINE)
    for m in padrao.finditer(conteudo):
        fam, nome, idx, acao, ip, tam = m.group(1, 2, 3, 4, 5, 6)
        tam = int(tam)
        ge = int(m.group(7)) if m.group(7) else tam
        le = int(m.group(8)) if m.group(8) else (ge if m.group(7) else tam)
        listas.setdefault(nome, []).append({
            'familia': 'v6' if fam == 'ipv6-prefix' else 'v4',
            'index': int(idx), 'acao': acao,
            'prefixo': _prefixo(ip, tam), 'ge': ge, 'le': le,
        })
    return listas


def _parse_bgp_vrp(cab, corpo):
    """Bloco `bgp <ASN>` com contexto de address-family.

    Peers globais são declarados no topo do bloco e habilitados por AF;
    peers de VRF são declarados dentro da própria `ipvX-family vpn-instance`.
    """
    bgp = {
        'asn': cab.split()[1], 'router_id': '', 'grupos': {},
        'peers': {}, 'networks': [], 'default_ipv4_unicast': True,
    }

    def peer(chave, ip):
        return bgp['peers'].setdefault(chave, {
            'ip': ip, 'asn': '', 'descricao': '', 'grupo': '', 'vrf': '',
            'familia': 'v6' if ':' in ip else 'v4',
            'afs': set(), 'afs_desabilitadas': set(), 'reflect_client': set(),
            'policy_in': '', 'policy_out': '', 'filtro_in': '', 'filtro_out': '',
            'ignorada': False, 'bfd': False, 'multihop': '', 'fake_as': '',
            'connect_interface': '', 'route_limit': '', 'public_as_only': False,
        })

    grupo_attr = {}   # (af, grupo) -> dict

    af = ''
    vrf = ''
    for linha in corpo:
        s = linha.strip()
        if not s or s == '#':
            continue
        nivel1 = linha.startswith(' ') and not linha.startswith('  ')
        if nivel1:
            m_af = re.match(r'^(ipv4-family|ipv6-family)(?:\s+(unicast|vpnv4|vpnv6|vpn-instance)\s*(\S*))?', s)
            if m_af:
                fam = 'v4' if m_af.group(1) == 'ipv4-family' else 'v6'
                tipo = m_af.group(2) or 'unicast'
                if tipo == 'vpn-instance':
                    vrf = m_af.group(3)
                    af = f'vrf-{fam}'
                else:
                    vrf = ''
                    af = {'unicast': f'unicast-{fam}', 'vpnv4': 'vpnv4', 'vpnv6': 'vpnv6'}[tipo]
                continue
            if s.startswith(('l2vpn-ad-family', 'vpls-family', 'evpn')):
                vrf = ''
                af = s.split()[0]
                continue
            af, vrf = '', ''

        # Linhas do escopo global do bloco (antes/fora de AF)
        if not af:
            if s.startswith('router-id '):
                bgp['router_id'] = s.split()[1]
            elif s == 'undo default ipv4-unicast':
                bgp['default_ipv4_unicast'] = False
        # `group` pode ser declarado no topo ou dentro da AF de uma VRF
        m_g = re.match(r'^group (\S+)(?:\s+(internal|external))?', s)
        if m_g:
            bgp['grupos'][m_g.group(1)] = {'tipo': m_g.group(2) or 'internal'}
            continue
        m_p = re.match(r'^peer (\S+) (.+)$', s)
        m_undo = re.match(r'^undo peer (\S+) enable$', s)
        if m_undo:
            alvo = m_undo.group(1)
            chave = (vrf, alvo)
            if alvo in bgp['grupos']:
                grupo_attr.setdefault((af, alvo), {})['desabilitado'] = True
            else:
                peer(chave, alvo)['afs_desabilitadas'].add(af)
            continue
        if not m_p:
            if af.startswith('vrf-') or af.startswith('unicast'):
                m_net = re.match(r'^network (\S+) (\S+)(?:\s+route-policy (\S+))?', s)
                if m_net:
                    bgp['networks'].append({
                        'prefixo': _prefixo(m_net.group(1), m_net.group(2)),
                        'vrf': vrf, 'route_policy': m_net.group(3) or '',
                    })
            continue
        alvo, resto = m_p.group(1), m_p.group(2)
        eh_grupo = alvo in bgp['grupos']
        if eh_grupo:
            g = grupo_attr.setdefault((af, alvo), {})
            if resto == 'enable':
                g['enable'] = True
            elif resto == 'reflect-client':
                g['reflect_client'] = True
            elif resto.startswith('route-policy '):
                p = resto.split()
                g['policy_in' if p[2] == 'import' else 'policy_out'] = p[1]
            elif re.match(r'^(ip-prefix|ipv6-prefix) ', resto):
                p = resto.split()
                g['filtro_in' if p[2] == 'import' else 'filtro_out'] = p[1]
            elif resto.startswith('ebgp-max-hop'):
                g['multihop'] = (resto.split() + ['255'])[1]
            elif resto.startswith('bfd enable'):
                g['bfd'] = True
            elif resto == 'ignore':
                g['ignorada'] = True
            continue

        p = peer((vrf, alvo), alvo)
        if vrf:
            p['vrf'] = vrf
        if resto.startswith('as-number '):
            p['asn'] = resto.split()[1]
            if af:
                p['afs'].add(af)
        elif resto.startswith('description '):
            p['descricao'] = resto[12:].strip()
        elif resto.startswith('group '):
            p['grupo'] = resto.split()[1]
            if af:
                p['afs'].add(af)
        elif resto == 'enable':
            p['afs'].add(af)
        elif resto == 'reflect-client':
            p['reflect_client'].add(af)
        elif resto == 'ignore':
            p['ignorada'] = True
        elif resto.startswith('bfd enable') or resto.startswith('bfd min'):
            p['bfd'] = p['bfd'] or resto.startswith('bfd enable')
        elif resto.startswith('ebgp-max-hop'):
            p['multihop'] = (resto.split() + ['255'])[1]
        elif resto.startswith('fake-as '):
            p['fake_as'] = resto.split()[1]
        elif resto.startswith('connect-interface '):
            p['connect_interface'] = resto.split()[1]
        elif resto.startswith('route-limit '):
            p['route_limit'] = resto.split()[1]
        elif resto.startswith('public-as-only'):
            p['public_as_only'] = True
        elif resto.startswith('route-policy '):
            partes = resto.split()
            p['policy_in' if partes[2] == 'import' else 'policy_out'] = partes[1]
        elif re.match(r'^(ip-prefix|ipv6-prefix) ', resto):
            partes = resto.split()
            p['filtro_in' if partes[2] == 'import' else 'filtro_out'] = partes[1]

    # Herança de grupo → membro, por AF
    for (vrf_p, ip), p in bgp['peers'].items():
        g_nome = p['grupo']
        if not g_nome:
            continue
        for (af_g, nome), attrs in grupo_attr.items():
            if nome != g_nome:
                continue
            if attrs.get('enable'):
                p['afs'].add(af_g)
            if attrs.get('desabilitado') and af_g not in p['afs']:
                p['afs_desabilitadas'].add(af_g)
            if attrs.get('reflect_client'):
                p['reflect_client'].add(af_g)
            for k in ('policy_in', 'policy_out', 'filtro_in', 'filtro_out', 'multihop'):
                if attrs.get(k) and not p[k]:
                    p[k] = attrs[k]
            if attrs.get('bfd'):
                p['bfd'] = True
            if attrs.get('ignorada'):
                p['ignorada'] = True
        if not p['asn'] and bgp['grupos'].get(g_nome, {}).get('tipo') == 'internal':
            p['asn'] = bgp['asn']

    peers = []
    for (vrf_p, ip), p in bgp['peers'].items():
        if not p['asn']:
            continue
        # IPv4 unicast implícito (sem `undo default ipv4-unicast`)
        if (not vrf_p and bgp['default_ipv4_unicast'] and p['familia'] == 'v4'
                and 'unicast-v4' not in p['afs_desabilitadas']):
            p['afs'].add('unicast-v4')
        p['afs'] -= p['afs_desabilitadas']
        p['afs'].discard('')
        p['tipo'] = 'ibgp' if p['asn'] == bgp['asn'] else 'ebgp'
        p['afs'] = sorted(p['afs'])
        p['afs_desabilitadas'] = sorted(a for a in p['afs_desabilitadas'] if a)
        p['reflect_client'] = sorted(a for a in p['reflect_client'] if a)
        p['ativa'] = not p['ignorada'] and bool(p['afs'])
        peers.append(p)
    bgp['peers'] = peers
    return bgp


def extrair_huawei(conteudo):
    cfg = _conteudo_config(conteudo)
    linhas = cfg.replace('\r\n', '\n').split('\n')
    blocos = _blocos_vrp(linhas)

    d = {
        'vendor': 'huawei', 'hostname': '', 'versao': '',
        'lsr_id': '', 'mpls': False, 'mpls_te': False, 'rsvp_te': False,
        'te_bfd': False, 'rsvp_bfd': False, 'ldp': False, 'ldp_remote_peers': [],
        'mpls_l2vpn': False, 'bfd': False, 'bfd_sessoes': 0,
        'ospf': [], 'isis': [], 'vrfs': [], 'bgp': None,
        'interfaces': [], 'tuneis_te': [], 'tunnel_policies': [], 'explicit_paths': [],
        'route_policies': {}, 'prefix_lists': {}, 'community_filters': {},
        'as_path_filters': [], 'rotas_estaticas': [],
        'bng': {'pools': [], 'pools_v6': [], 've_l2_terminate': [], 'interfaces_bas': 0,
                'dominios': [], 'radius_grupos': []},
        'cgnat_servico': False,
        'seguranca': {
            'telnet': 'nao_explicito', 'telnet_v6': 'nao_explicito',
            'ftp': False, 'sftp': False, 'stelnet': False,
            'snmp_versoes': '', 'snmp_communities': 0, 'snmp_usm': 0,
            'usuarios_locais': 0, 'vty_protocolos': [], 'vty_acl': False,
            'ntp_servidores': 0, 'syslog_hosts': 0, 'lldp': False,
        },
    }

    m = re.search(r'^!Software Version (\S+)', cfg, re.MULTILINE)
    if m:
        d['versao'] = m.group(1)
    else:
        m = re.search(r'VRP \(R\) software, Version ([^\n]+)', conteudo)
        if m:
            d['versao'] = m.group(1).strip()

    usuarios = set()
    for cab, corpo in blocos:
        c0 = cab.split()[0] if cab else ''
        if cab.startswith('sysname '):
            d['hostname'] = cab.split(None, 1)[1]
        elif cab.startswith('mpls lsr-id '):
            d['lsr_id'] = cab.split()[2]
        elif cab == 'mpls':
            d['mpls'] = True
            corpo_s = [x.strip() for x in corpo]
            d['mpls_te'] = 'mpls te' in corpo_s
            d['rsvp_te'] = 'mpls rsvp-te' in corpo_s
            d['te_bfd'] = 'mpls te bfd enable' in corpo_s
            d['rsvp_bfd'] = any(x.startswith('mpls rsvp-te bfd all-interfaces enable') for x in corpo_s)
        elif cab == 'mpls ldp':
            d['ldp'] = True
        elif cab.startswith('mpls ldp remote-peer '):
            ip = next((x.split()[1] for x in corpo if x.strip().startswith('remote-ip ')), '')
            d['ldp_remote_peers'].append(ip or cab.split()[3])
        elif cab == 'mpls l2vpn':
            d['mpls_l2vpn'] = True
        elif cab == 'bfd':
            d['bfd'] = True
        elif re.match(r'^bfd \S+ bind ', cab):
            d['bfd_sessoes'] += 1
        elif c0 == 'ospf':
            m_o = re.match(r'^ospf (\d+)(?:\s+router-id (\S+))?(?:\s+vpn-instance (\S+))?', cab)
            if m_o:
                areas = sorted({x.split()[1] for x in corpo if x.startswith(' area ')})
                d['ospf'].append({
                    'processo': m_o.group(1), 'router_id': m_o.group(2) or '',
                    'vrf': m_o.group(3) or '', 'areas': areas,
                    'bfd': any('bfd all-interfaces enable' in x for x in corpo),
                    'bfd_timers': next((_timers_bfd(x) for x in corpo
                                        if 'bfd all-interfaces min-tx-interval' in x), ''),
                    'import_route': sorted({x.split()[1] for x in corpo
                                            if x.strip().startswith('import-route ')}),
                    'te': any(x.strip() == 'opaque-capability enable' for x in corpo),
                })
        elif c0 == 'isis':
            d['isis'].append({'processo': (cab.split() + [''])[1]})
        elif cab.startswith('ip vpn-instance '):
            vrf = {'nome': cab.split()[2], 'descricao': '', 'rd_v4': '', 'rd_v6': '',
                   'rt_import': [], 'rt_export': [], 'familias': []}
            fam = ''
            for x in corpo:
                s = x.strip()
                if s in ('ipv4-family', 'ipv6-family'):
                    fam = 'v4' if s == 'ipv4-family' else 'v6'
                    vrf['familias'].append(fam)
                elif s.startswith('description '):
                    vrf['descricao'] = s[12:]
                elif s.startswith('route-distinguisher ') and fam:
                    vrf[f'rd_{fam}'] = s.split()[1]
                elif s.startswith('vpn-target '):
                    p = s.split()
                    alvo = vrf['rt_import'] if 'import-extcommunity' in p else vrf['rt_export']
                    for rt in p[1:]:
                        if ':' in rt and rt not in alvo:
                            alvo.append(rt)
                    if 'import-extcommunity' not in p and 'export-extcommunity' not in p:
                        for rt in p[1:]:
                            if ':' in rt and rt not in vrf['rt_import']:
                                vrf['rt_import'].append(rt)
            if not vrf['nome'].startswith('__'):
                d['vrfs'].append(vrf)
        elif c0 == 'bgp' and re.match(r'^bgp \d+$', cab):
            d['bgp'] = _parse_bgp_vrp(cab, corpo)
        elif c0 == 'interface':
            itf = _parse_interface_vrp(cab, corpo)
            d['interfaces'].append(itf)
            if itf['tunel_te']:
                d['tuneis_te'].append({'nome': itf['nome'], 'destino': itf['tunel_destino'],
                                       'caminho': itf['tunel_caminho'], 'descricao': itf['descricao']})
            if itf['bas']:
                d['bng']['interfaces_bas'] += 1
        elif cab.startswith('tunnel-policy '):
            destinos = [x.split()[3] for x in corpo if x.strip().startswith('tunnel binding destination ')]
            d['tunnel_policies'].append({'nome': cab.split()[1], 'destinos': destinos})
        elif cab.startswith('explicit-path '):
            d['explicit_paths'].append(cab.split()[1])
        elif re.match(r'^ip pool \S+ bas local', cab):
            pool = {'nome': cab.split()[2], 'vrf': '', 'gateway': '', 'rede': '', 'secoes': 0}
            for x in corpo:
                s = x.strip()
                if s.startswith('vpn-instance '):
                    pool['vrf'] = s.split()[1]
                elif s.startswith('gateway '):
                    p = s.split()
                    pool['gateway'] = p[1]
                    if len(p) > 2:
                        pool['rede'] = _prefixo(p[1], p[2])
                elif s.startswith('section '):
                    pool['secoes'] += 1
            d['bng']['pools'].append(pool)
        elif re.match(r'^ipv6 prefix \S+ delegation', cab):
            pref = {'nome': cab.split()[2], 'vrf': '', 'prefixo': '', 'tamanho_delegado': ''}
            for x in corpo:
                s = x.strip()
                if s.startswith('vpn-instance '):
                    pref['vrf'] = s.split()[1]
                elif s.startswith('prefix '):
                    p = s.split()
                    pref['prefixo'] = p[1]
                    if 'delegating-prefix-length' in p:
                        pref['tamanho_delegado'] = p[p.index('delegating-prefix-length') + 1]
            d['bng']['pools_v6'].append(pref)
        elif c0 == 'aaa':
            for x in corpo:
                s = x.strip()
                m_u = re.match(r'^local-user (\S+)', s)
                if m_u:
                    usuarios.add(m_u.group(1))
                elif x.startswith(' domain '):
                    d['bng']['dominios'].append(s.split()[1])
        elif cab.startswith('radius-server group '):
            d['bng']['radius_grupos'].append(cab.split()[2])
        elif cab.startswith(('service-instance-group', 'nat instance')):
            d['cgnat_servico'] = True
        elif cab.startswith('user-interface vty'):
            for x in corpo:
                s = x.strip()
                if s.startswith('protocol inbound '):
                    d['seguranca']['vty_protocolos'].append(s.split()[2])
                elif re.match(r'^acl (ipv6 )?\S+ inbound', s):
                    d['seguranca']['vty_acl'] = True
        elif cab == 'lldp enable':
            d['seguranca']['lldp'] = True

    d['route_policies'] = _parse_route_policies(blocos)
    d['prefix_lists'] = _parse_prefix_lists(cfg)
    for m_cf in re.finditer(
            r'^ip community-filter (?:basic |advanced )?(\S+) (?:index \d+ )?(permit|deny) (.+)$',
            cfg, re.MULTILINE):
        d['community_filters'].setdefault(m_cf.group(1), []).extend(
            v for v in m_cf.group(3).split() if re.match(r'^\d+:\d+$', v) or v in (
                'no-export', 'no-advertise', 'internet', 'no-export-subconfed'))
    d['as_path_filters'] = sorted(set(re.findall(r'^ip as-path-filter (\S+)', cfg, re.MULTILINE)))

    for m_rs in re.finditer(r'^ip route-static (.+)$', cfg, re.MULTILINE):
        p = m_rs.group(1).split()
        vrf = ''
        if p and p[0] == 'vpn-instance':
            vrf = p[1]
            p = p[2:]
        if len(p) < 3:
            continue
        desc = ''
        if 'description' in p:
            i = p.index('description')
            desc = ' '.join(p[i + 1:])
            p = p[:i]
        d['rotas_estaticas'].append({
            'prefixo': _prefixo(p[0], p[1]), 'vrf': vrf,
            'proximo_salto': p[2], 'descricao': desc,
        })

    seg = d['seguranca']
    if re.search(r'^undo telnet server enable', cfg, re.MULTILINE):
        seg['telnet'] = 'desabilitado'
    elif re.search(r'^telnet server enable', cfg, re.MULTILINE):
        seg['telnet'] = 'habilitado'
    if re.search(r'^undo telnet ipv6 server enable', cfg, re.MULTILINE):
        seg['telnet_v6'] = 'desabilitado'
    elif re.search(r'^telnet ipv6 server enable', cfg, re.MULTILINE):
        seg['telnet_v6'] = 'habilitado'
    seg['ftp'] = bool(re.search(r'^ftp (ipv6 )?server enable', cfg, re.MULTILINE))
    seg['sftp'] = bool(re.search(r'^sftp (ipv4 )?server enable', cfg, re.MULTILINE))
    seg['stelnet'] = bool(re.search(r'^stelnet (ipv4 )?server enable', cfg, re.MULTILINE))
    m_sv = re.search(r'^snmp-agent sys-info version (.+)$', cfg, re.MULTILINE)
    seg['snmp_versoes'] = m_sv.group(1).strip() if m_sv else ''
    seg['snmp_communities'] = len(re.findall(r'^snmp-agent community ', cfg, re.MULTILINE))
    seg['snmp_usm'] = len(re.findall(r'^snmp-agent usm-user ', cfg, re.MULTILINE))
    seg['usuarios_locais'] = len(usuarios)
    seg['ntp_servidores'] = len(re.findall(r'^ntp-service unicast-server ', cfg, re.MULTILINE))
    seg['syslog_hosts'] = len(re.findall(r'^info-center loghost ', cfg, re.MULTILINE))

    # VE l2-terminate: sub-interfaces que recebem PPPoE transportado por L2VPN
    pais_l2t = {i['nome'] for i in d['interfaces'] if i['l2_terminate']}
    for i in d['interfaces']:
        pai = i['nome'].split('.')[0]
        if '.' in i['nome'] and pai in pais_l2t and (i['l2_vsi'] or i['l2vc']):
            d['bng']['ve_l2_terminate'].append({
                'interface': i['nome'], 'vlan': i['dot1q'], 'descricao': i['descricao'],
                'vsi': i['l2_vsi'],
            })
    return d


# ═══════════════════════════════════════════════════════════════════════════
# MikroTik RouterOS
# ═══════════════════════════════════════════════════════════════════════════

def _kv(texto):
    """Parâmetros `chave=valor` de uma linha do /export (aspas respeitadas).

    O export do RouterOS 7 abrevia chaves com o mesmo prefixo da anterior:
    `remote.address=X .as=Y` significa `remote.as=Y`."""
    saida = {}
    prefixo = ''
    for m in re.finditer(r'([\w.\-]+)=("(?:[^"\\]|\\.)*"|\S+)', texto):
        chave = m.group(1)
        if chave.startswith('.') and prefixo:
            chave = prefixo + chave
        elif '.' in chave:
            prefixo = chave.split('.', 1)[0]
        saida[chave] = m.group(2).strip('"')
    return saida


def _linhas_mikrotik(conteudo):
    """Normaliza o export (terse ou não) em [(caminho, verbo, params)]."""
    from clientes.backup_parser import _juntar_continuacao_mikrotik
    texto = _juntar_continuacao_mikrotik(conteudo)
    saida = []
    caminho = ''
    for linha in texto.split('\n'):
        s = linha.strip()
        if not s or s.startswith('#'):
            continue
        if s.startswith('/'):
            m = re.match(r'^(/[\w\-/ ]+?)\s+(add|set|print|remove)\b(.*)$', s)
            if m:
                caminho = m.group(1).strip()
                saida.append((caminho, m.group(2), _kv(m.group(3))))
            else:
                caminho = s
            continue
        m = re.match(r'^(add|set)\b(.*)$', s)
        if m and caminho:
            saida.append((caminho, m.group(1), _kv(m.group(2))))
    return saida


def extrair_mikrotik(conteudo):
    d = {
        'vendor': 'mikrotik', 'hostname': '', 'versao': '', 'modelo': '',
        'bondings': [], 'vlans': [], 'ips': [], 'pools': [],
        'pppoe_servidores': [], 'radius_ppp': False, 'usa_radius': False,
        'nat': {'total': 0, 'netmap': 0, 'src_nat': 0, 'masquerade': 0, 'dst_nat': 0,
                'publicos': [], 'privados': [], 'com_portas': 0, 'comentarios': []},
        'bgp': {'asn': '', 'router_id': '', 'peers': []},
        'ospf': False, 'mpls_ldp': False, 'vpls': 0,
        'rota_default': '',
        'seguranca': {'servicos_desabilitados': [], 'servicos_ativos_explicitos': [],
                      'snmp': False, 'snmp_communities': 0, 'usuarios_locais': 0},
    }
    m = re.search(r'by RouterOS (\S+)', conteudo)
    if m:
        d['versao'] = m.group(1)
    m = re.search(r'^#\s*model\s*=\s*(\S+)', conteudo, re.MULTILINE)
    if m:
        d['modelo'] = m.group(1)

    publicos, privados = set(), set()
    for caminho, verbo, kv in _linhas_mikrotik(conteudo):
        if caminho == '/system identity' and 'name' in kv:
            d['hostname'] = kv['name']
        elif caminho == '/interface bonding' and verbo == 'add':
            d['bondings'].append({'nome': kv.get('name', ''), 'modo': kv.get('mode', ''),
                                  'membros': kv.get('slaves', '')})
        elif caminho == '/interface vlan' and verbo == 'add':
            d['vlans'].append({'nome': kv.get('name', ''), 'vlan': kv.get('vlan-id', ''),
                               'interface': kv.get('interface', ''), 'comentario': kv.get('comment', '')})
        elif caminho == '/ip address' and verbo == 'add' and kv.get('disabled') != 'yes':
            d['ips'].append({'ip': kv.get('address', ''), 'interface': kv.get('interface', '')})
        elif caminho == '/ip pool' and verbo == 'add':
            d['pools'].append({'nome': kv.get('name', ''), 'faixas': kv.get('ranges', ''),
                               'comentario': kv.get('comment', '')})
        elif caminho == '/interface pppoe-server server' and verbo == 'add':
            if kv.get('disabled') != 'yes':
                d['pppoe_servidores'].append({'interface': kv.get('interface', ''),
                                              'servico': kv.get('service-name', '')})
        elif caminho == '/ppp aaa' and kv.get('use-radius') == 'yes':
            d['usa_radius'] = True
        elif caminho == '/radius' and verbo == 'add' and 'ppp' in kv.get('service', ''):
            d['radius_ppp'] = True
        elif caminho == '/ip firewall nat' and verbo == 'add':
            if kv.get('disabled') == 'yes':
                continue
            nat = d['nat']
            nat['total'] += 1
            acao = kv.get('action', '')
            chave = {'netmap': 'netmap', 'src-nat': 'src_nat', 'masquerade': 'masquerade',
                     'dst-nat': 'dst_nat'}.get(acao)
            if chave:
                nat[chave] += 1
            if acao in ('netmap', 'src-nat'):
                if kv.get('to-addresses'):
                    publicos.add(kv['to-addresses'])
                if kv.get('src-address'):
                    privados.add(kv['src-address'])
                if kv.get('to-ports'):
                    nat['com_portas'] += 1
                com = kv.get('comment', '')
                if com and com not in nat['comentarios'] and len(nat['comentarios']) < 5:
                    nat['comentarios'].append(com)
        elif caminho in ('/routing bgp instance', '/routing bgp template') and 'as' in kv:
            d['bgp']['asn'] = d['bgp']['asn'] or kv['as']
            d['bgp']['router_id'] = d['bgp']['router_id'] or kv.get('router-id', '')
        elif caminho in ('/routing bgp peer', '/routing bgp connection') and verbo == 'add':
            ip = kv.get('remote-address') or kv.get('remote.address', '')
            d['bgp']['peers'].append({
                'nome': kv.get('name', ''),
                'ip': ip.split('/')[0],
                'asn': kv.get('remote-as') or kv.get('remote.as', ''),
                'policy_in': kv.get('in-filter') or kv.get('input.filter', ''),
                'policy_out': kv.get('out-filter') or kv.get('output.filter', ''),
                'ativa': kv.get('disabled') != 'yes',
                'descricao': kv.get('comment', ''),
            })
            if kv.get('as') and not d['bgp']['asn']:
                d['bgp']['asn'] = kv['as']
        elif caminho.startswith('/routing ospf') and verbo == 'add':
            d['ospf'] = True
        elif caminho == '/mpls ldp' and (kv.get('enabled') == 'yes' or verbo == 'add'):
            d['mpls_ldp'] = True
        elif caminho == '/interface vpls' and verbo == 'add':
            d['vpls'] += 1
        elif caminho == '/ip route' and verbo == 'add':
            if kv.get('dst-address', '0.0.0.0/0') in ('0.0.0.0/0',) and kv.get('disabled') != 'yes':
                d['rota_default'] = d['rota_default'] or kv.get('gateway', '')
        elif caminho == '/ip service' and verbo == 'set':
            nome = kv.get('numbers') or ''
            m_n = re.search(r'\[\s*find\s+name=(\S+?)\s*\]', ' '.join(f'{k}={v}' for k, v in kv.items()))
            if not nome:
                nome = m_n.group(1) if m_n else ''
            if kv.get('disabled') == 'yes':
                d['seguranca']['servicos_desabilitados'].append(nome)
        elif caminho == '/snmp' and verbo == 'set':
            d['seguranca']['snmp'] = kv.get('enabled') == 'yes'
        elif caminho == '/snmp community' and verbo == 'add':
            d['seguranca']['snmp_communities'] += 1
        elif caminho == '/user' and verbo == 'add':
            d['seguranca']['usuarios_locais'] += 1

    # `/ip service set telnet disabled=yes` — o nome vem como argumento posicional
    for m_s in re.finditer(r'^/ip service set (?:\[\s*find[^\]]*name=)?(\w+)\s*\]?\s+.*disabled=yes',
                           conteudo, re.MULTILINE):
        if m_s.group(1) not in d['seguranca']['servicos_desabilitados']:
            d['seguranca']['servicos_desabilitados'].append(m_s.group(1))
    d['seguranca']['servicos_desabilitados'] = sorted(
        s for s in set(d['seguranca']['servicos_desabilitados']) if s)

    d['nat']['publicos'] = sorted(publicos)[:20]
    d['nat']['privados'] = sorted(privados)[:20]
    return d


# ═══════════════════════════════════════════════════════════════════════════
# Dispatcher
# ═══════════════════════════════════════════════════════════════════════════

def extrair(conteudo, vendor):
    """Retorna o dict AS-IS do vendor, ou None quando não há extrator
    dedicado (o chamador cai no `backup_parser` genérico)."""
    if vendor == 'huawei':
        return extrair_huawei(conteudo)
    if vendor == 'mikrotik':
        return extrair_mikrotik(conteudo)
    return None
