"""
Análise do inventário coletado → modelo AS-IS.

Funções puras sobre o dict de `coleta.coletar()`: nada aqui toca banco ou
disco, o que permite testar com fixtures e regenerar seções sob demanda.

Tudo o que é inferência (papel do equipamento, classificação de um peer eBGP,
finalidade de uma community) vem acompanhado da base usada para inferir,
porque o documento é revisado por uma pessoa antes de ir ao cliente.
"""
import ipaddress
import re
from collections import Counter, defaultdict
from datetime import datetime

from .extratores import eh_asn_privado

SEVERIDADES = ['Crítica', 'Alta', 'Média', 'Baixa', 'Informativa']

_CDN = re.compile(r'CDN|GGC|NETFLIX|\bOCA\b|META|FACEBOOK|FNA|AKAMAI|GOOGLE|GLOBO|CACHE|CLOUDFLARE|AMAZON|KWAI|TIKTOK', re.I)
_IX = re.compile(r'PTT|\bIX\b|IX[-_.]|IXBR|ROUTE[-_ ]?SERVER|\bRS\d', re.I)
_UP = re.compile(r'UPSTREA[MN]|TRANSITO|TR[AÂ]NSITO|IP[-_ ]?TRANSIT|OPERADORA|FULL[-_]?ROUTING[-_]?(IN|RECEB)', re.I)
_DOWN = re.compile(r'DOWNSTREA[MN]|CLIENTE|CUSTOMER', re.I)
_CGN = re.compile(r'CGN|CGNAT', re.I)
_ERROS_GRAFIA = {
    'UPSTREAN': 'UPSTREAM', 'DOWNSTREAN': 'DOWNSTREAM', 'TRANSPOTE': 'TRANSPORTE',
    'ENALCE': 'ENLACE', 'SUBSTACAO': 'SUBESTACAO', 'CUSTUMERS': 'CUSTOMERS',
    'LIBERACES': 'LIBERACOES', 'LIBERAOES': 'LIBERACOES', 'DONWSTREAM': 'DOWNSTREAM',
    'DOWSTREAM': 'DOWNSTREAM', 'SERVVER': 'SERVER',
}


# ═══════════════════════════════════════════════════════════════════════════
# Auxiliares
# ═══════════════════════════════════════════════════════════════════════════

def _ip(txt):
    try:
        return ipaddress.ip_address((txt or '').split('/')[0])
    except ValueError:
        return None


def _rede(txt):
    try:
        return ipaddress.ip_network(txt, strict=False)
    except ValueError:
        return None


def _eh_publico(prefixo):
    r = _rede(prefixo)
    return bool(r) and r.is_global


def _data(iso):
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def nome_equip(eq):
    return eq.get('hostname') or eq.get('nome') or eq.get('host')


def _ordenar_ip(txt):
    ip = _ip(txt)
    return (ip.version, int(ip)) if ip else (9, 0)


def _faixa_prefixo(e):
    """'186.250.16.0/21 até /24' a partir de uma entrada de prefix-list."""
    base = e['prefixo']
    tam = int(base.split('/')[1]) if '/' in base else 0
    if e['le'] > tam:
        return f'{base} até /{e["le"]}'
    return base


def nome_cliente_da_descricao(desc, asn=''):
    if not desc:
        return f'AS{asn}' if asn else '—'
    s = desc.upper()
    s = re.sub(r'\b(LINK|PEER|FULL|DEFAULT|DO?W?N?STREA[MN]|DONWSTREAM|UPSTREA[MN]|EBGP|BGP|SESSAO|TRANSPORTE|'
               r'CLIENTE|DEDICADO|TRANSITO|'
               r'IPV4|IPV6|V4|V6|ASN|AS\d+|\d{4,})\b', ' ', s)
    s = re.sub(r'[-_/]+', ' ', s)
    s = ' '.join(s.split())
    return s.replace(' ', '-') or desc


# ═══════════════════════════════════════════════════════════════════════════
# Policies
# ═══════════════════════════════════════════════════════════════════════════

def avaliar_policy(extr, nome, filtro=''):
    """Resumo de uma route-policy (ou prefix-list aplicada direto no peer)."""
    r = {
        'nome': nome or filtro, 'existe': False, 'nega_tudo': False, 'aceita_tudo': False,
        'prefixos': [], 'lp': [], 'communities': [], 'community_none': False,
        'prepend': 0, 'match_community': [], 'filtra_bogons': False,
    }
    listas = extr.get('prefix_lists', {})
    if filtro:
        entradas = listas.get(filtro)
        if entradas is not None:
            r['existe'] = True
            r['prefixos'] = [e for e in entradas if e['acao'] == 'permit']
            r['nega_tudo'] = not r['prefixos']
        if not nome:
            return r
    if not nome:
        return r
    if re.fullmatch(r'(?i)deny(-all)?|reject|bloqueio|nega', nome) and nome not in extr.get('route_policies', {}):
        r.update(existe=False, nega_tudo=True)
        return r
    nodes = extr.get('route_policies', {}).get(nome)
    if nodes is None:
        return r
    r['existe'] = True
    permits = [n for n in nodes if n['acao'] == 'permit']
    if not permits:
        r['nega_tudo'] = True
        return r
    for n in nodes:
        listas_ref = [v for t, v in n['if_match'] if t in ('ip-prefix', 'ipv6-prefix')]
        if n['acao'] == 'deny':
            if any(re.search(r'BOGON|MARTIAN', x, re.I) for x in listas_ref):
                r['filtra_bogons'] = True
            if not n['if_match']:
                break   # deny catch-all encerra a avaliação
            continue
        if not n['if_match']:
            r['aceita_tudo'] = True
        for nome_lista in listas_ref:
            for e in listas.get(nome_lista, []):
                if e['acao'] == 'permit':
                    r['prefixos'].append(e)
        r['match_community'] += [v for t, v in n['if_match'] if t == 'community-filter']
        if n['local_preference'] is not None and n['local_preference'] not in r['lp']:
            r['lp'].append(n['local_preference'])
        for c in n['communities']:
            if c not in r['communities']:
                r['communities'].append(c)
        r['community_none'] = r['community_none'] or n['community_none']
        r['prepend'] = max(r['prepend'], len(n['prepend']))
        if not n['if_match']:
            break   # permit catch-all: nós seguintes não são alcançados
    return r


def _eh_default(e):
    return e['prefixo'] in ('0.0.0.0/0', '::/0') and e['le'] == 0


def _eh_full(e):
    lim = 24 if e['familia'] == 'v4' else 48
    return e['prefixo'] in ('0.0.0.0/0', '::/0') and e['le'] >= lim - 16 and e['le'] > 0


def resumo_rotas(av, sentido):
    """'Default' | 'Full routing' | 'Nenhuma' | 'Prefixos específicos' | ..."""
    if av['nega_tudo']:
        return 'Nenhuma'
    if not av['existe']:
        return 'Sem filtro' if sentido == 'out' else 'Sem filtro'
    prefs = av['prefixos']
    if any(_eh_full(e) for e in prefs) or any(e['prefixo'].endswith('/0') and e['le'] >= 8 for e in prefs if not _eh_default(e)):
        return 'Full routing'
    if prefs and all(_eh_default(e) for e in prefs):
        return 'Default'
    if any(_eh_default(e) for e in prefs):
        return 'Default + prefixos'
    if prefs:
        return 'Prefixos específicos'
    if av['aceita_tudo'] and not av['match_community']:
        return 'Full routing' if sentido == 'in' else 'Sem filtro (tudo)'
    if av['match_community']:
        return 'Por community'
    return 'Parcial'


def classificar_peer(peer, av_in, av_out):
    """Classificação inferida de uma sessão eBGP + base da inferência."""
    desc = peer.get('descricao', '')
    asn = peer.get('asn', '')
    recebe = resumo_rotas(av_in, 'in')
    anuncia = resumo_rotas(av_out, 'out')
    base = [f'recebe: {recebe.lower()}', f'anuncia: {anuncia.lower()}']

    if eh_asn_privado(asn):
        if _CGN.search(desc):
            return 'Interno (CGNAT)', base + ['ASN privado']
        if _CDN.search(desc):
            return 'Parceiro de conteúdo / CDN', base + ['ASN privado', 'descrição indica CDN']
        return 'Interno / ASN privado', base + ['ASN privado']
    if _CDN.search(desc):
        return 'Parceiro de conteúdo / CDN', base + ['descrição indica CDN']
    if _IX.search(desc):
        return 'IX / PTT', base + ['descrição indica IX']

    anuncia_transito = anuncia in ('Full routing', 'Default', 'Default + prefixos', 'Sem filtro (tudo)')
    recebe_transito = recebe in ('Full routing', 'Default', 'Default + prefixos')
    if recebe_transito and not anuncia_transito:
        return 'Upstream (trânsito)', base
    if recebe == 'Prefixos específicos' and anuncia_transito:
        return 'ISP downstream', base
    if recebe == 'Nenhuma' and anuncia_transito:
        return 'ISP downstream', base + ['sessão só de anúncio']
    if recebe == 'Prefixos específicos' and (av_out['community_none'] or anuncia in ('Prefixos específicos', 'Por community')):
        return 'Peering / troca de tráfego', base
    if _UP.search(desc):
        return 'Upstream (trânsito)', base + ['pela descrição']
    if _DOWN.search(desc):
        return 'ISP downstream', base + ['pela descrição']
    return 'Não classificado', base


def _divergencia_descricao(classe, desc):
    """Descrição diz downstream/upstream e a política diz outra coisa.
    Só compara quando a classe veio da política (não da própria descrição)."""
    diz_down = bool(_DOWN.search(desc or '') or re.search(r'DONWSTREAM|DOWSTREAM', desc or '', re.I))
    diz_up = bool(_UP.search(desc or '')) and not diz_down
    if classe in ('Não classificado',) or classe.startswith('Interno') or classe.startswith('Cliente L3VPN'):
        return False
    if diz_down and classe != 'ISP downstream':
        return True
    if diz_up and not classe.startswith('Upstream'):
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# Montagem do modelo
# ═══════════════════════════════════════════════════════════════════════════

def _ips_do_equipamento(eq):
    ips = set()
    if _ip(eq.get('host')):
        ips.add(str(_ip(eq['host'])))
    x = eq.get('extr') or {}
    for k in ('lsr_id',):
        if x.get(k):
            ips.add(x[k])
    if x.get('bgp'):
        if isinstance(x['bgp'], dict) and x['bgp'].get('router_id'):
            ips.add(x['bgp']['router_id'])
    for o in x.get('ospf', []) if isinstance(x.get('ospf'), list) else []:
        if o.get('router_id'):
            ips.add(o['router_id'])
    for itf in x.get('interfaces', []):
        for p in itf.get('ips', []):
            ips.add(p.split('/')[0])
    for p in x.get('ips', []) or []:
        ip = p.get('ip', '') if isinstance(p, dict) else p
        if ip:
            ips.add(ip.split('/')[0])
    return ips


def _papeis(eq):
    x = eq.get('extr') or {}
    papeis = []
    funcao = (eq.get('funcao') or '').upper()
    if eq.get('vendor') == 'huawei' and x:
        peers = (x.get('bgp') or {}).get('peers', [])
        if any(p['reflect_client'] for p in peers):
            papeis.append('RR')
        tem_servico = bool(x['vrfs']) or x['mpls_l2vpn'] or bool(eq.get('l2vpn'))
        if x['mpls'] and tem_servico:
            papeis.append('PE')
        elif x['mpls']:
            papeis.append('P')
        b = x['bng']
        if [p for p in b['pools'] if p['vrf'] or p['secoes']] and (b['ve_l2_terminate'] or b['interfaces_bas'] or b['pools_v6']):
            papeis.append('BNG')
        if x['cgnat_servico']:
            papeis.append('CGNAT')
        if any(p['tipo'] == 'ebgp' and p['ativa'] and not eh_asn_privado(p['asn']) for p in peers):
            papeis.append('Borda eBGP')
    elif eq.get('vendor') == 'mikrotik' and x:
        if x['pppoe_servidores']:
            papeis.append('BNG')
        if x['nat']['netmap'] + x['nat']['src_nat'] >= 8 or 'CGNAT' in funcao:
            papeis.append('CGNAT')
        if x['mpls_ldp'] or x['vpls']:
            papeis.append('MPLS/VPLS')
        if x['bgp']['peers']:
            papeis.append('BGP')
    if not papeis:
        if 'BRAS' in funcao or 'BNG' in funcao:
            papeis.append('BNG')
        elif 'CGNAT' in funcao:
            papeis.append('CGNAT')
    return papeis


def _funcoes_observadas(eq, x):
    f = []
    papeis = eq['papeis']
    if 'RR' in papeis:
        f.append('RR')
    if (x.get('bgp') or {}).get('peers'):
        f.append('MP-BGP')
    if 'BNG' in papeis:
        f.append('BNG')
    if x.get('vrfs'):
        f.append('VRFs (' + ', '.join(v['nome'] for v in x['vrfs'][:4]) + ('…' if len(x['vrfs']) > 4 else '') + ')')
    if eq.get('l2vpn'):
        f.append(f'L2VPN ({len(eq["l2vpn"])})')
    if x.get('tuneis_te'):
        f.append(f'túneis TE ({len(x["tuneis_te"])})')
    if 'Borda eBGP' in papeis:
        f.append('eBGP externo')
    if 'CGNAT' in papeis:
        f.append('CGNAT')
    return ', '.join(f) or '—'


def _mtus_backbone(x):
    mtus, mpls_mtus = set(), set()
    por_nome = {i['nome']: i for i in x.get('interfaces', [])}
    for i in x.get('interfaces', []):
        if not (i['ospf'] or i['mpls']) or i['nome'].startswith(('LoopBack', 'NULL', 'Tunnel')):
            continue
        mtu = i['mtu']
        if mtu is None and '.' in i['nome']:
            mtu = (por_nome.get(i['nome'].split('.')[0]) or {}).get('mtu')
        mtus.add(mtu or 1500)
        if i['mpls_mtu']:
            mpls_mtus.add(i['mpls_mtu'])
    return sorted(mtus), sorted(mpls_mtus)


def montar_modelo(inv):
    eqs = inv['equipamentos']
    agora = _data(inv.get('gerado_em')) or datetime.now()

    for eq in eqs:
        eq['papeis'] = _papeis(eq)
        eq['nome_exibicao'] = nome_equip(eq)

    # ── mapa IP → equipamento ────────────────────────────────────────────
    por_ip = {}
    for eq in eqs:
        for ip in _ips_do_equipamento(eq):
            por_ip.setdefault(ip, eq['nome_exibicao'])

    huaweis = [e for e in eqs if e.get('vendor') == 'huawei' and e.get('extr')]
    mikrotiks = [e for e in eqs if e.get('vendor') == 'mikrotik' and e.get('extr')]
    com_backup = [e for e in eqs if e.get('extr') or e.get('generico')]

    # ── ASN principal ────────────────────────────────────────────────────
    asns = Counter()
    for e in huaweis:
        if e['extr'].get('bgp'):
            asns[e['extr']['bgp']['asn']] += 1
    for e in eqs:
        g = e.get('generico') or {}
        if g.get('as_local') and not eh_asn_privado(g['as_local']):
            asns[g['as_local']] += 1
    for b in inv.get('blocos_ip', []):
        a = (b.get('asn') or '').upper().replace('AS', '')
        if a.isdigit():
            asns[a] += 1
    asn = asns.most_common(1)[0][0] if asns else ''

    # ── datas-base ───────────────────────────────────────────────────────
    datas = [_data(e['backup']['confirmado_em']) for e in eqs if e.get('backup') and e['backup'].get('arquivo_disponivel')]
    datas = [d for d in datas if d]
    periodo = (min(datas), max(datas)) if datas else (None, None)

    # ── inventário ───────────────────────────────────────────────────────
    rrs, pes, demais = [], [], []
    for e in eqs:
        x = e.get('extr') or {}
        linha = {
            'nome': e['nome_exibicao'], 'cadastro': e['nome'], 'pop': e['pop'],
            'host': e['host'], 'funcao': e['funcao'], 'modelo': e['modelo'] or (x.get('modelo') or ''),
            'vendor': e.get('vendor') or '', 'versao': x.get('versao', ''),
            'lsr_id': x.get('lsr_id', ''), 'papeis': e['papeis'],
            'funcoes': _funcoes_observadas(e, x) if x else '—',
            'backup': e.get('backup'),
        }
        if 'RR' in e['papeis']:
            rrs.append(linha)
        if e.get('vendor') == 'huawei' and ('PE' in e['papeis'] or 'P' in e['papeis']):
            pes.append(linha)
        else:
            demais.append(linha)
    pes.sort(key=lambda l: (0 if 'RR' in l['papeis'] else 1, _ordenar_ip(l['lsr_id'])))

    # ── backbone ─────────────────────────────────────────────────────────
    backbone = []
    todas_mtus, todas_mpls_mtus = set(), set()
    ospf_ifs = ospf_ifs_bfd = 0
    for e in huaweis:
        x = e['extr']
        if not (x['mpls'] or x['ospf']):
            continue
        mtus, mpls_mtus = _mtus_backbone(x)
        todas_mtus.update(mtus)
        todas_mpls_mtus.update(mpls_mtus)
        o_ifs = [i for i in x['interfaces'] if i['ospf']]
        ospf_ifs += len(o_ifs)
        ospf_ifs_bfd += len([i for i in o_ifs if i['bfd']])
        principal = [o for o in x['ospf'] if not o['vrf']]
        backbone.append({
            'nome': e['nome_exibicao'], 'pop': e['pop'], 'lsr_id': x['lsr_id'],
            'ospf': ', '.join(f'{o["processo"]} (área {", ".join(o["areas"]) or "—"})' for o in principal) or '—',
            'ldp': x['ldp'], 'ldp_ifs': len([i for i in x['interfaces'] if i['ldp']]),
            'ldp_remotos': len(x['ldp_remote_peers']),
            'te': x['mpls_te'], 'rsvp': x['rsvp_te'],
            'rsvp_ifs': len([i for i in x['interfaces'] if i['rsvp']]),
            'tuneis': len(x['tuneis_te']),
            'bfd': x['bfd'], 'bfd_detalhe': _bfd_detalhe(x),
            'mtus': mtus, 'mpls_mtus': mpls_mtus,
        })
    backbone.sort(key=lambda b: _ordenar_ip(b['lsr_id']))

    # ── MP-BGP ───────────────────────────────────────────────────────────
    familias = Counter()
    sessoes_rr = []
    peers_ibgp_desconhecidos = []
    peers_ibgp_inativos = []
    ips_cadastro = {str(_ip(e['host'])) for e in eqs if _ip(e['host'])}
    for e in huaweis:
        bgp = e['extr'].get('bgp')
        if not bgp:
            continue
        clientes_rr = [p for p in bgp['peers'] if p['tipo'] == 'ibgp' and p['reflect_client']]
        for p in bgp['peers']:
            if p['tipo'] != 'ibgp':
                continue
            for af in p['afs']:
                familias[af] += 1
            if p['ip'] not in por_ip and p['ip'] not in ips_cadastro:
                peers_ibgp_desconhecidos.append({'equipamento': e['nome_exibicao'], 'peer': p['ip'],
                                                 'descricao': p['descricao']})
            elif not p['ativa']:
                peers_ibgp_inativos.append({'equipamento': e['nome_exibicao'], 'peer': p['ip'],
                                            'descricao': p['descricao']})
        if clientes_rr:
            afs = sorted({af for p in clientes_rr for af in p['reflect_client']})
            sessoes_rr.append({'rr': e['nome_exibicao'], 'pop': e['pop'], 'clientes': len(clientes_rr),
                               'familias': afs, 'router_id': bgp['router_id']})

    # ── eBGP ─────────────────────────────────────────────────────────────
    ebgp = []
    for e in huaweis:
        x = e['extr']
        bgp = x.get('bgp')
        if not bgp:
            continue
        for p in bgp['peers']:
            if p['tipo'] != 'ebgp':
                continue
            av_in = avaliar_policy(x, p['policy_in'], p['filtro_in'])
            av_out = avaliar_policy(x, p['policy_out'], p['filtro_out'])
            classe, base = classificar_peer(p, av_in, av_out)
            vrf_internet = eh_vrf_internet(p['vrf'])
            if not vrf_internet and not classe.startswith('Interno'):
                classe, base = f'Cliente L3VPN ({p["vrf"]})', base + ['sessão dentro de VRF de serviço']
            ebgp.append({
                'equipamento': e['nome_exibicao'], 'pop': e['pop'], 'vendor': 'huawei',
                'peer': p['ip'], 'asn': p['asn'], 'descricao': p['descricao'],
                'cliente': nome_cliente_da_descricao(p['descricao'], p['asn']),
                'vrf': p['vrf'] or 'global', 'familia': p['familia'],
                'ativa': p['ativa'], 'multihop': p['multihop'], 'bfd': p['bfd'],
                'policy_in': p['policy_in'] or p['filtro_in'], 'policy_out': p['policy_out'] or p['filtro_out'],
                'recebe': resumo_rotas(av_in, 'in'), 'anuncia': resumo_rotas(av_out, 'out'),
                'prefixos_aceitos': [_faixa_prefixo(pp) for pp in av_in['prefixos']
                                     if not _eh_default(pp) and not _eh_full(pp)][:8],
                'lp': av_in['lp'], 'communities_in': av_in['communities'],
                'prepend_out': av_out['prepend'], 'remove_communities_out': av_out['community_none'],
                'filtra_bogons': av_in['filtra_bogons'],
                'sem_policy_in': not (p['policy_in'] or p['filtro_in']),
                'sem_policy_out': not (p['policy_out'] or p['filtro_out']),
                'vrf_internet': vrf_internet,
                'classe': classe, 'base': base,
                'divergente': _divergencia_descricao(classe, p['descricao']),
            })
    for e in mikrotiks:
        x = e['extr']
        for p in x['bgp']['peers']:
            if not p['asn'] or p['asn'] == x['bgp']['asn']:
                continue
            if p['asn'] == asn:
                classe = 'Interno (sessão com PE próprio)'
            elif eh_asn_privado(p['asn']):
                classe = 'Interno / ASN privado'
            else:
                classe = 'Não classificado'
            ebgp.append({
                'equipamento': e['nome_exibicao'], 'pop': e['pop'], 'vendor': 'mikrotik',
                'peer': p['ip'], 'asn': p['asn'], 'descricao': p['descricao'] or p['nome'],
                'cliente': nome_cliente_da_descricao(p['descricao'] or p['nome'], p['asn']),
                'vrf': 'global', 'familia': 'v6' if ':' in p['ip'] else 'v4', 'ativa': p['ativa'],
                'multihop': '', 'bfd': False, 'policy_in': p['policy_in'], 'policy_out': p['policy_out'],
                'recebe': '—', 'anuncia': '—', 'prefixos_aceitos': [], 'lp': [], 'communities_in': [],
                'prepend_out': 0, 'remove_communities_out': False, 'filtra_bogons': False,
                'sem_policy_in': False, 'sem_policy_out': False, 'vrf_internet': True,
                'classe': classe, 'base': ['equipamento MikroTik: policy não avaliada'],
                'divergente': False,
            })

    downstreams = _agrupar_downstreams([s for s in ebgp if s['classe'] == 'ISP downstream'])
    upstreams = [s for s in ebgp if s['classe'].startswith('Upstream')]
    parceiros = [s for s in ebgp if s['classe'] in ('Parceiro de conteúdo / CDN', 'IX / PTT', 'Peering / troca de tráfego')]
    internos = [s for s in ebgp if s['classe'].startswith('Interno')]
    clientes_l3vpn = [s for s in ebgp if s['classe'].startswith('Cliente L3VPN')]
    nao_classificados = [s for s in ebgp if s['classe'] == 'Não classificado']

    # ── entregas estáticas (clientes sem BGP) ────────────────────────────
    estaticas = []
    for e in huaweis:
        for r in e['extr']['rotas_estaticas']:
            rede = _rede(r['prefixo'])
            if not rede or not rede.is_global or rede.prefixlen > 30 or rede.prefixlen < 16:
                continue
            if not _ip(r['proximo_salto']):
                continue
            estaticas.append({'equipamento': e['nome_exibicao'], 'pop': e['pop'], **r})

    # ── VRFs ─────────────────────────────────────────────────────────────
    vrfs = {}
    for e in huaweis:
        for v in e['extr']['vrfs']:
            ag = vrfs.setdefault(v['nome'], {'nome': v['nome'], 'rds': set(), 'rts': set(),
                                             'pes': [], 'peers_ebgp': 0, 'descricao': v['descricao']})
            if v['rd_v4'] or v['rd_v6']:
                ag['rds'].add(v['rd_v4'] or v['rd_v6'])
            ag['rts'].update(v['rt_import'] + v['rt_export'])
            ag['pes'].append(e['pop'])
    for s in ebgp:
        if s['vrf'] in vrfs:
            vrfs[s['vrf']]['peers_ebgp'] += 1
    vrfs_lista = []
    for v in sorted(vrfs.values(), key=lambda v: (-len(v['pes']), v['nome'])):
        v['rds'] = sorted(v['rds'])
        v['rts'] = sorted(v['rts'])
        v['pes'] = sorted(set(v['pes']))
        v['rd_divergente'] = len(v['rds']) > 1
        v['fora_padrao'] = [x for x in v['rds'] + v['rts']
                            if asn and x.split(':')[0] != asn and not _ip(x.split(':')[0])]
        v['rd_legado'] = bool(v['fora_padrao'])
        vrfs_lista.append(v)

    # ── L2VPN ────────────────────────────────────────────────────────────
    l2vpn = _agregar_l2vpn(eqs, por_ip)
    pppoe = _pppoe_transportado(huaweis, l2vpn)

    # ── BNG / CGNAT ──────────────────────────────────────────────────────
    bngs_centrais = []
    for e in huaweis:
        if 'BNG' not in e['papeis']:
            continue
        b = e['extr']['bng']
        bngs_centrais.append({
            'nome': e['nome_exibicao'], 'pop': e['pop'], 'modelo': e['modelo'],
            'pools': [p for p in b['pools'] if p['rede']], 'pools_v6': b['pools_v6'],
            'vrfs': sorted({p['vrf'] for p in b['pools'] if p['vrf']}),
            'pppoe_l2vpn': len(b['ve_l2_terminate']), 'interfaces_bas': b['interfaces_bas'],
            'dominios': len(b['dominios']), 'radius': b['radius_grupos'],
        })
    bngs_remotos = []
    for e in eqs:
        if 'BNG' not in e['papeis'] or e.get('vendor') == 'huawei':
            continue
        x = e.get('extr') or {}
        bngs_remotos.append({
            'nome': e['nome_exibicao'], 'pop': e['pop'], 'modelo': e['modelo'],
            'pppoe': len(x.get('pppoe_servidores', [])), 'radius': x.get('usa_radius') or x.get('radius_ppp'),
            'sem_backup': not x,
            'pools': ', '.join(p['faixas'] for p in x.get('pools', [])[:3]),
            'nat_embarcado': 'CGNAT' in e['papeis'],
        })
    cgnats = []
    for e in eqs:
        # CGNAT dedicado: cadastrado como CGNAT ou fazendo NAT sem ser BNG.
        # BNG remoto com NAT embarcado aparece na tabela de BNGs.
        dedicado = 'CGNAT' in (e.get('funcao') or '').upper() or 'BNG' not in e['papeis']
        if 'CGNAT' not in e['papeis'] or not dedicado or e.get('vendor') == 'huawei':
            continue
        x = e.get('extr') or {}
        if e.get('vendor') == 'mikrotik' and x:
            nat = x['nat']
            vlans = ', '.join(f'VLAN {v["vlan"]} ({v["comentario"] or v["nome"]})' for v in x['vlans'][:4])
            bonds = ', '.join(f'{b["nome"]} {b["modo"]}' for b in x['bondings'])
            cgnats.append({
                'nome': e['nome_exibicao'], 'pop': e['pop'], 'modelo': e['modelo'] or x.get('modelo', ''),
                'versao': x.get('versao', ''),
                'integracao': '; '.join(filter(None, [bonds, vlans])) or '—',
                'pools_privados': ', '.join(p['faixas'] for p in x['pools']) or ', '.join(nat['privados'][:3]),
                'publicos': ', '.join(nat['publicos']) or '—',
                'regras': nat['total'], 'netmap': nat['netmap'], 'src_nat': nat['src_nat'],
                'deterministico': nat['com_portas'] >= 4,
                'comentarios': ', '.join(nat['comentarios']),
                'bgp': f'AS{x["bgp"]["asn"]}' + (f' com {len(x["bgp"]["peers"])} sessão(ões)' if x['bgp']['peers'] else '')
                       if x['bgp']['asn'] else 'Sem BGP',
                'default': x['rota_default'],
            })
        else:
            cgnats.append({'nome': e['nome_exibicao'], 'pop': e['pop'], 'modelo': e['modelo'],
                           'versao': x.get('versao', '') if x else '', 'integracao': '—',
                           'pools_privados': '', 'publicos': '', 'regras': 0, 'netmap': 0, 'src_nat': 0,
                           'deterministico': False, 'comentarios': '', 'bgp': '', 'default': '',
                           'sem_backup': not x})

    # ── communities ──────────────────────────────────────────────────────
    communities = _agregar_communities(huaweis, asn)

    # ── segurança ────────────────────────────────────────────────────────
    seguranca = []
    for e in eqs:
        x = e.get('extr')
        if not x:
            continue
        s = x['seguranca']
        if e['vendor'] == 'huawei':
            telnet = s['telnet'] == 'habilitado' or 'telnet' in s['vty_protocolos'] or 'all' in s['vty_protocolos']
            seguranca.append({
                'nome': e['nome_exibicao'], 'vendor': 'Huawei',
                'telnet': 'Habilitado' if telnet else ('Desabilitado' if s['telnet'] == 'desabilitado' else 'Não explícito'),
                'ftp': 'Habilitado' if s['ftp'] else 'Desabilitado',
                'ssh': 'Sim' if s['stelnet'] else 'Não explícito',
                'snmp': s['snmp_versoes'] or ('—' if not s['snmp_communities'] else 'v1/v2c (padrão)'),
                'usuarios': s['usuarios_locais'], 'acl_vty': s['vty_acl'],
                'ntp': s['ntp_servidores'], 'syslog': s['syslog_hosts'],
                'risco_telnet': telnet, 'risco_ftp': s['ftp'],
                'risco_snmp': 'v2c' in (s['snmp_versoes'] or '') or 'v1' in (s['snmp_versoes'] or '') or 'all' in (s['snmp_versoes'] or ''),
            })
        elif e['vendor'] == 'mikrotik':
            desab = set(s['servicos_desabilitados'])
            seguranca.append({
                'nome': e['nome_exibicao'], 'vendor': 'MikroTik',
                'telnet': 'Desabilitado' if 'telnet' in desab else 'Ativo (padrão RouterOS)',
                'ftp': 'Desabilitado' if 'ftp' in desab else 'Ativo (padrão RouterOS)',
                'ssh': 'Desabilitado' if 'ssh' in desab else 'Sim',
                'snmp': ('Ativo' if s['snmp'] else 'Inativo') + (f' ({s["snmp_communities"]} community)' if s['snmp_communities'] else ''),
                'usuarios': s['usuarios_locais'], 'acl_vty': None, 'ntp': None, 'syslog': None,
                'risco_telnet': 'telnet' not in desab, 'risco_ftp': 'ftp' not in desab,
                'risco_snmp': s['snmp'],
            })

    grafias = _erros_grafia(huaweis)

    modelo = {
        'cliente': inv['cliente'], 'gerado_em': inv.get('gerado_em'),
        'asn': asn, 'periodo': periodo,
        'total_acessos': len(eqs), 'total_com_backup': len(com_backup),
        'vendors': Counter(e.get('vendor') or 'sem backup' for e in eqs),
        'sem_backup': [e for e in eqs if e['protocolo'] == 'SSH' and not (e.get('extr') or e.get('generico'))],
        'backups_antigos': [e for e in eqs if e.get('backup') and _data(e['backup']['confirmado_em'])
                            and (agora - _data(e['backup']['confirmado_em'])).days > 7],
        'equipamentos': eqs, 'rrs': rrs, 'pes': pes, 'demais': demais,
        'backbone': backbone, 'mtus': sorted(todas_mtus), 'mpls_mtus': sorted(todas_mpls_mtus),
        'ospf_ifs': ospf_ifs, 'ospf_ifs_bfd': ospf_ifs_bfd,
        'familias': familias, 'sessoes_rr': sessoes_rr,
        'peers_ibgp_desconhecidos': peers_ibgp_desconhecidos, 'peers_ibgp_inativos': peers_ibgp_inativos,
        'ebgp': ebgp, 'downstreams': downstreams, 'upstreams': upstreams, 'parceiros': parceiros,
        'internos': internos, 'clientes_l3vpn': clientes_l3vpn,
        'listas_nome_divergente': _listas_nome_divergente(huaweis),
        'nao_classificados': nao_classificados, 'estaticas': estaticas,
        'vrfs': vrfs_lista, 'l2vpn': l2vpn, 'pppoe': pppoe,
        'bngs_centrais': bngs_centrais, 'bngs_remotos': bngs_remotos, 'cgnats': cgnats,
        'communities': communities, 'seguranca': seguranca, 'grafias': grafias,
        'topologia': inv.get('topologia', {'mapas': [], 'enlaces': []}),
        'blocos_ip': inv.get('blocos_ip', []),
        'huaweis': huaweis, 'mikrotiks': mikrotiks,
    }
    modelo['achados'] = gerar_achados(modelo)
    modelo['riscos'] = gerar_riscos(modelo)
    return modelo


def _bfd_detalhe(x):
    partes = []
    if x['bfd']:
        partes.append('global')
    if x['te_bfd'] or x['rsvp_bfd']:
        partes.append('TE/RSVP')
    if any(o['bfd'] for o in x['ospf']) or any(i['bfd'] and i['ospf'] for i in x['interfaces']):
        partes.append('OSPF')
    if x['bfd_sessoes']:
        partes.append(f'{x["bfd_sessoes"]} sessão(ões) estática(s)')
    bgp = x.get('bgp') or {}
    n = len([p for p in bgp.get('peers', []) if p['bfd']])
    if n:
        partes.append(f'BGP ({n})')
    return ', '.join(partes) or 'Não'


def _agrupar_downstreams(sessoes):
    grupos = {}
    for s in sessoes:
        chave = (s['equipamento'], s['asn'])
        g = grupos.setdefault(chave, {
            'cliente': s['cliente'], 'pop': s['pop'], 'equipamento': s['equipamento'],
            'asn': s['asn'], 'entrega': {}, 'prefixos': [], 'lp': set(), 'communities': [],
            'vrfs': set(), 'ativa': False, 'bogons': True, 'descricoes': set(),
        })
        g['entrega'][s['familia']] = s['anuncia']
        for p in s['prefixos_aceitos']:
            if p not in g['prefixos']:
                g['prefixos'].append(p)
        g['lp'].update(s['lp'])
        for c in s['communities_in']:
            if c not in g['communities']:
                g['communities'].append(c)
        g['vrfs'].add(s['vrf'])
        g['ativa'] = g['ativa'] or s['ativa']
        g['bogons'] = g['bogons'] and s['filtra_bogons']
        if s['descricao']:
            g['descricoes'].add(s['descricao'])
        if len(g['cliente']) < 3 or g['cliente'].startswith('AS'):
            g['cliente'] = s['cliente']
    saida = []
    for g in grupos.values():
        v4, v6 = g['entrega'].get('v4'), g['entrega'].get('v6')
        if v4 and v6:
            g['entrega_txt'] = f'{v4} IPv4; {v6} IPv6' if v4 != v6 else f'{v4} v4/v6'
        else:
            g['entrega_txt'] = f'{v4 or v6} {"IPv4" if v4 else "IPv6"}'
        g['lp'] = sorted(g['lp'])
        g['vrfs'] = sorted(g['vrfs'])
        g['descricoes'] = sorted(g['descricoes'])
        saida.append(g)
    saida.sort(key=lambda g: (g['pop'], g['cliente']))
    # mesmo ASN com perfis diferentes em POPs diferentes
    por_asn = defaultdict(set)
    for g in saida:
        por_asn[g['asn']].add((g['pop'], g['entrega_txt']))
    for g in saida:
        perfis = por_asn[g['asn']]
        g['multi_perfil'] = len({p for _, p in perfis}) > 1 and len({pop for pop, _ in perfis}) > 1
    return saida


def _agregar_l2vpn(eqs, por_ip):
    servicos = {}
    for e in eqs:
        for s in e.get('l2vpn', []):
            ident = s.get('id') or ''
            if not ident:
                m = re.search(r'[:._-](\d{2,5})$', s.get('nome', ''))
                ident = m.group(1) if m else s.get('nome', '')
            chave = (s.get('tipo', ''), (s.get('sinalizacao') or '').upper(), ident)
            ag = servicos.setdefault(chave, {
                'id': ident, 'tipo': s.get('tipo', ''), 'tecnologias': set(), 'nomes': set(),
                'equipamentos': set(), 'peers': set(), 'peers_desconhecidos': set(),
                'vlans': set(), 'sinalizacao': set(), 'mtus': set(), 'descricoes': set(),
            })
            ag['tecnologias'].add(s.get('tecnologia', ''))
            if s.get('nome'):
                ag['nomes'].add(s['nome'])
            ag['equipamentos'].add(e['pop'] or e['nome_exibicao'])
            if s.get('sinalizacao'):
                ag['sinalizacao'].add(s['sinalizacao'].upper())
            if s.get('mtu'):
                ag['mtus'].add(str(s['mtu']))
            if s.get('vlan'):
                ag['vlans'].add(str(s['vlan']))
            for p in s.get('peers', []):
                nome = por_ip.get(p.get('ip', ''))
                if nome:
                    ag['peers'].add(nome)
                elif p.get('ip'):
                    ag['peers_desconhecidos'].add(p['ip'])
                if p.get('mtu'):
                    ag['mtus'].add(str(p['mtu']))
            for i in s.get('interfaces', []):
                if i.get('vlan'):
                    ag['vlans'].add(str(i['vlan']))
                if i.get('descricao'):
                    ag['descricoes'].add(i['descricao'])
            if s.get('descricao'):
                ag['descricoes'].add(s['descricao'])
    from .coleta import pop_do_nome
    saida = []
    for ag in servicos.values():
        pontas = set(ag['equipamentos']) | {pop_do_nome(p) for p in ag['peers']}
        nomes_txt = ' '.join(ag['nomes'] | ag['descricoes']).upper()
        ag['pppoe'] = 'PPPOE' in nomes_txt
        ag['pontas'] = sorted(pontas)
        ag['uma_ponta'] = len(pontas) < 2 and not ag['peers_desconhecidos'] and ag['sinalizacao'] != {'BGP'}
        for k in ('tecnologias', 'nomes', 'equipamentos', 'peers', 'peers_desconhecidos',
                  'vlans', 'sinalizacao', 'mtus', 'descricoes'):
            ag[k] = sorted(ag[k], key=lambda v: (len(v), v))
        saida.append(ag)
    saida.sort(key=lambda a: (int(a['id']) if str(a['id']).isdigit() else 10 ** 9, str(a['id'])))
    return saida


def _pppoe_transportado(huaweis, l2vpn):
    linhas = []
    for e in huaweis:
        for v in e['extr']['bng']['ve_l2_terminate']:
            m = re.search(r'(\d+)$', v['vsi'] or '')
            linhas.append({
                'bng': e['nome_exibicao'], 'pop_bng': e['pop'], 'vlan': v['vlan'],
                'id': m.group(1) if m else v['vlan'], 'servico': v['descricao'] or v['vsi'],
                'vsi': v['vsi'],
            })
    # VE l2-terminate também termina P2P de VRF; quando há serviços nomeados
    # como PPPoE, só eles entram (senão, mantém todos — nomenclatura livre).
    pppoe = [l for l in linhas if 'PPPOE' in f'{l["servico"]} {l["vsi"]}'.upper()]
    linhas = pppoe or linhas
    linhas.sort(key=lambda l: (l['bng'], int(l['id']) if str(l['id']).isdigit() else 0))
    return linhas


def _agregar_communities(huaweis, asn):
    valores = {}
    for e in huaweis:
        x = e['extr']
        for filtro, vals in x['community_filters'].items():
            for v in vals:
                ag = valores.setdefault(v, {'valor': v, 'filtros': set(), 'equipamentos': set(), 'aplicada': 0})
                ag['filtros'].add(filtro)
                ag['equipamentos'].add(e['pop'])
        for nodes in x['route_policies'].values():
            for n in nodes:
                for v in n['communities']:
                    if not re.match(r'^\d+:\d+$', v):
                        continue
                    ag = valores.setdefault(v, {'valor': v, 'filtros': set(), 'equipamentos': set(), 'aplicada': 0})
                    ag['aplicada'] += 1
                    ag['equipamentos'].add(e['pop'])

    # famílias de prepend: X:NN0..X:NN5 com nomes "0X-…", "1X-…"
    grupos = defaultdict(list)
    for v in valores.values():
        m = re.match(r'^(\d+):(\d+)$', v['valor'])
        if m and int(m.group(2)) >= 10 and int(m.group(2)) % 10 <= 5:
            grupos[(m.group(1), int(m.group(2)) // 10)].append(v)
    usados = set()
    linhas = []
    regra_prepend = False
    familias = {k: v for k, v in grupos.items() if len(v) >= 3}
    valores_do_filtro = defaultdict(set)
    for v in valores.values():
        for f in v['filtros']:
            valores_do_filtro[f].add(v['valor'])
    for (pref, dezena), membros in familias.items():
        da_familia = {mb['valor'] for mb in membros}
        # só nomeia a família por filtros exclusivos dela
        nomes = {f for mb in membros for f in mb['filtros'] if valores_do_filtro[f] <= da_familia}
        com_prepend = {n for n in nomes if _PADRAO_PREPEND.search(n)}
        if com_prepend:
            regra_prepend = True
        base = sorted({_PADRAO_PREPEND.sub('', n).strip('-_ ') for n in (com_prepend or nomes)} - {''})
        finais = sorted(int(mb['valor'].split(':')[1]) % 10 for mb in membros)
        rotulo = ', '.join(base) or 'Família de communities'
        linhas.append({
            'valor': f'{pref}:{dezena}{finais[0]} a {pref}:{dezena}{finais[-1]}',
            'finalidade': (f'{_finalidade(rotulo)} / prepend' if com_prepend else _finalidade(rotulo)),
            'filtros': sorted(nomes)[:6], 'equipamentos': sorted({q for m in membros for q in m['equipamentos']}),
            'aplicada': sum(m['aplicada'] for m in membros), 'faixa': True,
            'ordem': (int(pref), dezena * 10),
        })
        usados.update(m['valor'] for m in membros)
    for v in valores.values():
        if v['valor'] in usados:
            continue
        m = re.match(r'^(\d+):(\d+)$', v['valor'])
        linhas.append({
            'valor': v['valor'],
            'finalidade': _finalidade(', '.join(sorted(v['filtros']))) if v['filtros'] else 'Aplicada em policies (sem filtro nomeado)',
            'filtros': sorted(v['filtros'])[:6], 'equipamentos': sorted(v['equipamentos']),
            'aplicada': v['aplicada'], 'faixa': False,
            'ordem': (int(m.group(1)), int(m.group(2))) if m else (0, 0),
        })
    linhas.sort(key=lambda l: l['ordem'])
    prefixos = Counter(l['valor'].split(':')[0] for l in linhas)
    prefixo_dominante = prefixos.most_common(1)[0][0] if prefixos else ''
    return {
        'linhas': linhas, 'regra_prepend': regra_prepend,
        'prefixo': prefixo_dominante,
        'asn_4byte': bool(asn) and asn.isdigit() and int(asn) > 65535,
        'prefixo_difere_asn': bool(prefixo_dominante) and prefixo_dominante != asn,
    }


_PADRAO_PREPEND = re.compile(r'^\d\s*X[-_]?|[-_]?X\d$|[-_]?\dX$|x0?\d$|^Prepend', re.I)


def eh_vrf_internet(vrf):
    return not vrf or vrf == 'global' or bool(re.search(r'INTERNET|INET|GLOBAL|TRANSITO|IPT', vrf, re.I))


def _listas_nome_divergente(huaweis):
    saida = []
    for e in huaweis:
        for nome, entradas in e['extr']['prefix_lists'].items():
            if not re.search(r'DEFAULT', nome, re.I):
                continue
            extras = [x for x in entradas if x['acao'] == 'permit' and not _eh_default(x)]
            if extras:
                x = extras[0]
                saida.append({'equipamento': e['nome_exibicao'], 'lista': nome,
                              'conteudo': f'{x["prefixo"]} le {x["le"]}'})
    return saida


def _finalidade(nomes):
    if not nomes:
        return '—'
    s = nomes.upper()
    if 'BLACKHOLE' in s or 'RTBH' in s:
        return 'Blackhole'
    if 'CDN' in s:
        return 'CDN ' + nomes.replace('CDNS-', '').replace('CDN-', '')
    if 'INTERNA' in s:
        return 'Rotas internas'
    if 'PREFERENCIAL' in s:
        return 'Rota externa preferencial'
    if 'CUSTOMER' in s or 'CLIENT' in s or 'B2B' in s:
        return 'Rotas eBGP de clientes'
    if 'NO-EXPORT' in s or 'NAO-ANUNCIAR' in s or 'NO-ANNOUNCE' in s:
        return 'Não anunciar'
    return nomes.replace('_', ' ')


def _erros_grafia(huaweis):
    achados = Counter()
    for e in huaweis:
        x = e['extr']
        textos = [i['descricao'] for i in x['interfaces']]
        textos += [p['descricao'] for p in (x.get('bgp') or {}).get('peers', [])]
        textos += list(x['route_policies'].keys()) + [v['nome'] for v in x['vrfs']]
        for t in textos:
            for errado in _ERROS_GRAFIA:
                if errado in (t or '').upper():
                    achados[errado] += 1
    return [{'errado': k, 'correto': _ERROS_GRAFIA[k], 'ocorrencias': n} for k, n in achados.most_common()]


# ═══════════════════════════════════════════════════════════════════════════
# Achados e riscos
# ═══════════════════════════════════════════════════════════════════════════

def gerar_achados(m):
    a = []

    def add(sev, titulo, evidencia, impacto):
        a.append({'severidade': sev, 'titulo': titulo, 'evidencia': evidencia, 'impacto': impacto})

    if m['mtus'] and len(m['mtus']) > 1:
        sev = 'Crítica' if min(m['mtus']) <= 1500 and max(m['mtus']) > 1600 else 'Alta'
        extra = f' MPLS MTU configurada: {", ".join(map(str, m["mpls_mtus"]))}.' if m['mpls_mtus'] else ''
        pw = sorted({mt for s in m['l2vpn'] for mt in s['mtus']})
        extra += f' Pseudowires com MTU {", ".join(pw)}.' if pw else ''
        add(sev, 'MTU heterogênea no backbone e nos serviços',
            f'Interfaces de backbone (OSPF/MPLS) com MTU {", ".join(map(str, m["mtus"]))}.{extra}',
            'Rótulos MPLS reduzem o payload útil; exige validação de MTU por caminho antes de migrar serviços.')
    elif m['mtus'] == [1500]:
        add('Alta', 'Backbone MPLS operando com MTU 1500',
            'Todas as interfaces de backbone identificadas usam MTU padrão (1500).',
            'Cada rótulo MPLS consome 4 bytes; pseudowires e VPNs podem fragmentar ou descartar pacotes grandes.')

    total_mpls = len(m['backbone'])
    com_bfd = len([b for b in m['backbone'] if b['bfd_detalhe'] != 'Não'])
    if total_mpls and com_bfd < total_mpls:
        add('Alta', 'BFD parcial',
            f'BFD presente em {com_bfd} de {total_mpls} equipamentos do backbone; '
            f'{m["ospf_ifs_bfd"]} de {m["ospf_ifs"]} interfaces OSPF com BFD.',
            'Convergência desigual: falhas de enlace sem BFD dependem dos timers do protocolo.')

    rsvp = [b for b in m['backbone'] if b['rsvp']]
    tuneis = sum(b['tuneis'] for b in m['backbone'])
    if rsvp and tuneis < len(rsvp):
        add('Média', 'RSVP-TE habilitado com poucos consumidores',
            f'RSVP-TE ativo em {len(rsvp)} equipamento(s); {tuneis} túnel(is) TE explícito(s) identificado(s).',
            'Complexidade operacional sem uso correspondente; definir se TE permanece no TO-BE.')

    sinal = {x for s in m['l2vpn'] for x in s['sinalizacao']}
    if {'LDP', 'BGP'} <= sinal:
        n_ldp = len([s for s in m['l2vpn'] if 'LDP' in s['sinalizacao']])
        n_bgp = len([s for s in m['l2vpn'] if 'BGP' in s['sinalizacao']])
        add('Média', 'Sinalização L2VPN mista (LDP e BGP)',
            f'{n_ldp} serviço(s) com sinalização LDP e {n_bgp} com BGP.',
            'Dois modelos operacionais para o mesmo tipo de serviço; padronizar no TO-BE.')

    if m['peers_ibgp_desconhecidos']:
        lst = ', '.join(sorted({f'{p["peer"]}' + (f' ({p["descricao"]})' if p['descricao'] else '')
                                for p in m['peers_ibgp_desconhecidos']})[:8])
        add('Média', 'Peers iBGP históricos',
            f'{len(m["peers_ibgp_desconhecidos"])} sessão(ões) iBGP para endereços sem equipamento cadastrado: {lst}.',
            'Sessões presas em Idle poluem a operação e podem esconder dependências de equipamentos legados.')

    sem_pol = [s for s in m['ebgp'] if s['vendor'] == 'huawei' and s['ativa'] and s['vrf_internet']
               and not eh_asn_privado(s['asn']) and (s['sem_policy_in'] or s['sem_policy_out'])]
    if sem_pol:
        lst = ', '.join(sorted({f'{s["peer"]} AS{s["asn"]} ({s["equipamento"]})' for s in sem_pol})[:6])
        add('Crítica', 'Sessões eBGP sem política de importação ou exportação',
            f'{len(sem_pol)} sessão(ões): {lst}.',
            'Risco de vazamento de rotas (route leak) ou de aceitar prefixos indevidos.')

    inativas = [s for s in m['ebgp'] if not s['ativa']]
    if inativas:
        add('Baixa', 'Sessões eBGP desativadas mantidas na configuração',
            f'{len(inativas)} sessão(ões) com `ignore` ou sem família ativa.',
            'Configuração residual; confirmar se o serviço foi encerrado antes de remover.')

    if m['listas_nome_divergente']:
        lst = ', '.join(f'{x["lista"]} em {x["equipamento"]} ({x["conteudo"]})' for x in m['listas_nome_divergente'][:6])
        add('Alta', 'Prefix-list com nome divergente do conteúdo',
            f'Listas nomeadas como default que permitem mais que a rota default: {lst}.',
            'A sessão entrega mais rotas do que o nome indica (ex.: full routing IPv6 em vez de default).')

    div = [s for s in m['ebgp'] if s['divergente']]
    if div:
        lst = ', '.join(sorted({f'{s["descricao"]} → {s["classe"]}' for s in div})[:5])
        add('Média', 'Descrição divergente da função real',
            f'{len(div)} sessão(ões) cuja descrição não corresponde à política aplicada: {lst}.',
            'Classificação feita pela política, prefixos e peer; a descrição não é fonte confiável.')

    vrf_leg = [v for v in m['vrfs'] if v['rd_legado'] or v['rd_divergente']]
    if vrf_leg:
        lst = '; '.join(f'{v["nome"]} (' + ', '.join(sorted(set(v['fora_padrao'])) or v['rds']) + ')'
                        for v in vrf_leg[:6])
        add('Média', 'RD/RT fora do padrão do ASN',
            f'VRFs com RD/RT que não seguem o ASN {m["asn"]} ou divergem entre PEs: {lst}.',
            'Pode colidir com outro AS e dificulta a padronização de VPNs no TO-BE.')

    com = m['communities']
    if com['linhas'] and com['prefixo_difere_asn'] and com['asn_4byte']:
        add('Média', 'Communities com prefixo diferente do ASN',
            f'As communities padrão usam o prefixo {com["prefixo"]}; o ASN {m["asn"]} é de 4 bytes '
            f'e não cabe em community padrão (RFC 1997).',
            'Risco de colisão com o AS de 16 bits de mesmo número; avaliar large-communities (RFC 8092) no TO-BE.')

    telnet = [s['nome'] for s in m['seguranca'] if s['risco_telnet'] and s['vendor'] == 'Huawei']
    ftp = [s['nome'] for s in m['seguranca'] if s['risco_ftp'] and s['vendor'] == 'Huawei']
    mk = [s['nome'] for s in m['seguranca'] if s['vendor'] == 'MikroTik' and (s['risco_telnet'] or s['risco_ftp'])]
    if telnet or ftp:
        add('Alta', 'Protocolos de gerência sem criptografia habilitados',
            f'Telnet: {", ".join(telnet) or "nenhum"}. FTP: {", ".join(ftp) or "nenhum"}.',
            'Credenciais trafegam em texto claro.')
    if mk:
        add('Média', 'Serviços padrão ativos em MikroTik',
            f'Telnet/FTP não desabilitados no export de {len(mk)} equipamento(s): {", ".join(mk[:8])}.',
            'O RouterOS mantém esses serviços ativos por padrão; restringir ou desabilitar.')
    snmp = [s['nome'] for s in m['seguranca'] if s['risco_snmp'] and s['vendor'] == 'Huawei']
    if snmp:
        add('Média', 'SNMP v2c em uso',
            f'{len(snmp)} equipamento(s) Huawei com SNMP v1/v2c habilitado.',
            'Community em texto claro; priorizar SNMPv3 com autenticação e criptografia.')

    if m['sem_backup']:
        add('Alta', 'Cobertura de backup incompleta',
            f'{len(m["sem_backup"])} acesso(s) SSH sem backup de configuração utilizável: '
            + ', '.join(e['nome'] for e in m['sem_backup'][:10]) + ('…' if len(m['sem_backup']) > 10 else '') + '.',
            'Esses equipamentos ficam fora da análise e sem ponto de restauração.')
    if m['backups_antigos']:
        add('Média', 'Backups desatualizados',
            f'{len(m["backups_antigos"])} equipamento(s) com última coleta há mais de 7 dias.',
            'O AS-IS desses equipamentos pode não refletir o estado atual.')

    uma = [s for s in m['l2vpn'] if s['uma_ponta'] or s['peers_desconhecidos']]
    if uma:
        add('Média', 'L2VPNs com ponta não identificada',
            f'{len(uma)} serviço(s) L2 com apenas uma ponta conhecida ou peer sem equipamento cadastrado.',
            'Manter como serviço especial até identificar o contratante e o destino.')

    if m['grafias']:
        add('Baixa', 'Padronização de nomenclatura',
            'Erros de grafia recorrentes em descrições/policies: '
            + ', '.join(f'{g["errado"]} ({g["ocorrencias"]}×)' for g in m['grafias']) + '.',
            'Dificulta busca e automação; corrigir no padrão de nomenclatura do TO-BE.')

    sem_ntp = [s['nome'] for s in m['seguranca'] if s['vendor'] == 'Huawei' and not s['ntp']]
    if sem_ntp and len(sem_ntp) == len([s for s in m['seguranca'] if s['vendor'] == 'Huawei']):
        add('Baixa', 'NTP não identificado nos equipamentos Huawei',
            'Nenhum `ntp-service unicast-server` encontrado.',
            'Logs e eventos sem horário confiável dificultam a correlação de incidentes.')

    a.sort(key=lambda x: SEVERIDADES.index(x['severidade']))
    for i, x in enumerate(a, 1):
        x['id'] = f'AS-IS-{i:03d}'
    return a


def gerar_riscos(m):
    r = [('Serviços históricos desconhecidos',
          'Aceitar risco residual e atualizar o AS-IS por adendo quando houver descoberta.')]
    multi = {}
    for g in m['downstreams']:
        if g['multi_perfil']:
            multi.setdefault(g['asn'], g['cliente'])
    for asn, cliente in sorted(multi.items(), key=lambda x: x[1])[:6]:
        pops = sorted({g['pop'] for g in m['downstreams'] if g['asn'] == asn})
        r.append((f'{cliente} (AS{asn}) com perfis distintos em {" e ".join(pops)}',
                  'Validar sessão operacional e intended routing antes da wave do cliente.'))
    if len(m['mtus']) > 1 or m['mtus'] == [1500]:
        r.append(('MTU por caminho', 'Testar payload, labels e pseudowires antes de mover cada serviço.'))
    if any(s['divergente'] for s in m['ebgp']):
        r.append(('Descrição divergente da função real',
                  'Confiar em política, prefixos, peer e validação operacional, não apenas no campo description.'))
    if any(s['uma_ponta'] or s['peers_desconhecidos'] for s in m['l2vpn']):
        r.append(('L2VPNs sem contratante conhecido',
                  'Manter inventariadas como serviços especiais até identificação comercial.'))
    if m['bngs_remotos'] or m['peers_ibgp_desconhecidos']:
        r.append(('Equipamentos legados e BNGs remotos',
                  'Citar no inventário e validar ausência de dependência antes da desativação.'))
    if m['sem_backup']:
        r.append(('Equipamentos sem backup analisado',
                  'Regularizar o backup e revisar o AS-IS desses equipamentos antes do TO-BE.'))
    if m['nao_classificados']:
        r.append(('Sessões eBGP sem classificação automática',
                  'Validar com a operação o papel de cada sessão (upstream, downstream ou parceiro).'))
    return [{'risco': a, 'tratamento': b} for a, b in r]
