"""
clientes/bgp_caixas_unificadas.py
Automação de anúncios por community com DUAS (ou mais) caixas de borda do
mesmo AS, ligadas por iBGP — uma automação só para o conjunto.

O caso que motivou (GREEN TELECOM, 2026-10-23): BRAS CARMO e BRAS PATOS, AS
266296, mesmo catálogo de communities (65296:<grupo><ação>) e os slots já
divididos entre as duas — `c-01` é a operadora de CARMO, `c-02`/`c-03` são as
de PATOS. O operador quer dizer "170.79.95.0/24 → c-02 com 2 prepends" UMA vez
e ter efeito no circuito que está na outra caixa.

Por que carimbar só numa caixa não basta (é o que estava acontecendo):

- as duas caixas originam os MESMOS prefixos (`network … route-policy
  RT-BGP-LOCAL-*` nas duas). No VRP a rota originada localmente vence a
  aprendida via iBGP, então a policy de saída do `c-02` (em PATOS) olha a
  community que a policy local de PATOS carimbou — a de CARMO nunca chega lá;
- e mesmo quando só uma caixa origina, o VRP não envia community nenhuma pro
  peer (nem iBGP) sem `peer X advertise-community`.

Por isso a intenção unificada é SINCRONIZADA: a mesma lista de communities do
catálogo é escrita na policy local do prefixo em toda caixa que o origina (as
communities próprias de cada caixa, fora do catálogo, ficam como estavam). Seja
qual for a rota que vence em cada caixa, as policies de saída de todos os
circuitos enxergam a mesma intenção. Quando o circuito está numa caixa que NÃO
origina o prefixo, a intenção só chega pelo iBGP: a sessão da caixa de origem
para ela precisa de `advertise-community` (gerado junto, se faltar) e a policy
de saída do iBGP precisa deixar o prefixo passar (conferido na simulação).

Funções puras sobre os snapshots — quem lê o banco e executa é
`bgp_views.py` (`_caixas_do_grupo` / `bgp_executar_acao_unificada`).

    detectar_enlaces_ibgp(a, b)    → sessões iBGP de A para B (e de B para A)
    agrupar_caixas(caixas)         → componentes ligadas por iBGP
    montar_mapa_unificado(caixas)  → o mapa de `montar_mapa`, das caixas juntas
    comandos_anuncio_unificado(…)  → os comandos de cada caixa para UMA intenção
"""
import copy
import ipaddress

from .bgp_actions import AcaoBgpNaoSuportada
from .bgp_community_auto import (
    ACOES_POR_CHAVE,
    _RE_COMMUNITY,
    _asn_community_prevalente,
    _efeito_no_circuito,
    _ordem_prefixo,
    community_de,
    comandos_reescrever_communities,
)


# ─── Descoberta: quais caixas falam iBGP entre si ────────────────────────────

def _ip(valor):
    try:
        return ipaddress.ip_address((valor or '').split('/')[0].strip())
    except ValueError:
        return None


def _enderecos(dados):
    """IPs das interfaces da caixa (`dados['ips']` vem com máscara)."""
    return {ip for ip in (_ip(x.get('ip')) for x in dados.get('ips') or []) if ip}


def _as_local(dados):
    sessoes = dados.get('sessoes') or []
    return (sessoes[0].get('as_local', '') if sessoes else '') or dados.get('as_local', '')


def _sessoes_ibgp(dados):
    as_local = _as_local(dados)
    return [s for s in dados.get('sessoes') or []
            if as_local and s.get('peer_as') == as_local and _ip(s.get('peer_ip'))]


def detectar_enlaces_ibgp(dados_a, dados_b):
    """
    As sessões iBGP que ligam A e B, por família: `[{'familia', 'sessao_a',
    'sessao_b'}]` — `sessao_a` é a sessão configurada em A apontando pra B (e
    vice-versa). Lista vazia = não são caixas irmãs.

    IPv4 casa pelo endereço: o peer de A é um IP de interface de B. O parser
    só guarda os IPs v4 das interfaces, então o IPv6 casa pelo par: A fala com
    `::12`, B fala com `::11`, os dois na mesma /64 — cada um é o peer do outro
    (é como os enlaces /126 e /127 entre as bordas são numerados).

    Exige o mesmo AS local: iBGP é por definição dentro do AS.
    """
    as_a, as_b = _as_local(dados_a), _as_local(dados_b)
    if not as_a or as_a != as_b:
        return []
    ips_a, ips_b = _enderecos(dados_a), _enderecos(dados_b)
    ibgp_a, ibgp_b = _sessoes_ibgp(dados_a), _sessoes_ibgp(dados_b)
    enlaces = []

    for sa in ibgp_a:
        ip_a = _ip(sa['peer_ip'])
        if ip_a.version != 4 or ip_a not in ips_b:
            continue
        sb = next((s for s in ibgp_b if _ip(s['peer_ip']) in ips_a), None)
        enlaces.append({'familia': 'v4', 'sessao_a': sa, 'sessao_b': sb})

    for sa in ibgp_a:
        ip_a = _ip(sa['peer_ip'])
        if ip_a.version != 6:
            continue
        rede = ipaddress.ip_network(f'{ip_a}/64', strict=False)
        for sb in ibgp_b:
            ip_b = _ip(sb['peer_ip'])
            if ip_b.version == 6 and ip_b != ip_a and ip_b in rede:
                enlaces.append({'familia': 'v6', 'sessao_a': sa, 'sessao_b': sb})
                break

    # Só v4 sem o lado de volta (a caixa B não tem sessão de volta pra A no
    # snapshot) ainda é um enlace — mas pela metade, e quem usa avisa.
    return enlaces


def agrupar_caixas(caixas):
    """
    Componentes de caixas ligadas por iBGP, cada uma com os enlaces entre
    elas. `caixas` é `[{'id', 'nome', 'dados', …}]`; a ordem de entrada é
    preservada dentro de cada grupo. Caixa sem par não forma grupo.
    """
    vizinhos = {c['id']: set() for c in caixas}
    enlaces = []
    for i, a in enumerate(caixas):
        for b in caixas[i + 1:]:
            achados = detectar_enlaces_ibgp(a['dados'], b['dados'])
            if not achados:
                achados_inv = detectar_enlaces_ibgp(b['dados'], a['dados'])
                achados = [{'familia': e['familia'], 'sessao_a': e['sessao_b'],
                            'sessao_b': e['sessao_a']} for e in achados_inv]
            if achados:
                vizinhos[a['id']].add(b['id'])
                vizinhos[b['id']].add(a['id'])
                for e in achados:
                    enlaces.append({**e, 'a': a['id'], 'b': b['id']})

    grupos, vistos = [], set()
    for c in caixas:
        if c['id'] in vistos or not vizinhos[c['id']]:
            continue
        pilha, membros = [c['id']], set()
        while pilha:
            atual = pilha.pop()
            if atual in membros:
                continue
            membros.add(atual)
            pilha.extend(vizinhos[atual] - membros)
        vistos |= membros
        grupos.append({
            'caixas': [x for x in caixas if x['id'] in membros],
            'enlaces': [e for e in enlaces if e['a'] in membros],
        })
    return grupos


# ─── Propagação da intenção pelo iBGP ─────────────────────────────────────────

def _sessao_para(enlaces, origem, destino, familia):
    """A sessão configurada em `origem` que aponta para `destino` na família."""
    for e in enlaces:
        if e['familia'] != familia:
            continue
        if e['a'] == origem and e['b'] == destino:
            return e['sessao_a']
        if e['b'] == origem and e['a'] == destino:
            return e['sessao_b']
    return None


def _ibgp_exporta(dados_origem, sessao, prefixo):
    """True/False pelo que a simulação de anúncios diz da policy de saída do
    iBGP; None quando o prefixo não aparece na simulação (não dá pra afirmar)."""
    lista = (dados_origem.get('anuncios') or {}).get(sessao.get('nome') or sessao.get('peer_ip')) or []
    item = next((x for x in lista if x.get('prefixo') == prefixo), None)
    return None if item is None else bool(item.get('permitido'))


# ─── Mapa unificado ───────────────────────────────────────────────────────────

def _marcar_caixa(sessoes, caixa):
    return [{**s, 'caixa': caixa['id'], 'caixa_nome': caixa['nome']} for s in sessoes]


def _unificar_circuitos(caixas, avisos):
    """Um circuito por id. Quem responde por ele é a caixa onde ele tem
    sessão BGP; sem sessão em nenhuma, vale a caixa principal (a primeira)."""
    por_id = {}
    for cx in caixas:
        for cid, c in cx['mapa']['circuitos'].items():
            por_id.setdefault(cid, []).append((cx, c))

    circuitos = {}
    for cid, lista in por_id.items():
        com_sessao = [(cx, c) for cx, c in lista if c['sessoes']]
        base_cx, base = (com_sessao or lista)[0]
        u = copy.deepcopy(base)
        u['caixa'], u['caixa_nome'] = base_cx['id'], base_cx['nome']
        u['caixas'] = [cx['id'] for cx, _ in com_sessao]
        u['sessoes'] = [s for cx, c in com_sessao for s in _marcar_caixa(c['sessoes'], cx)]
        u['ipv4'] = any(c['ipv4'] for _, c in com_sessao)
        u['ipv6'] = any(c['ipv6'] for _, c in com_sessao)
        u['completo'] = not u['faltando'] and bool(u['sessoes'])
        if len(com_sessao) > 1:
            avisos.append(
                f'{cid} tem sessão BGP em mais de uma caixa '
                f'({", ".join(cx["nome"] for cx, _ in com_sessao)}) — a mesma community '
                f'mexe no anúncio das duas.'
            )
        catalogos = {(c['asn_community'], c['grupo']) for _, c in lista if c['grupo']}
        if len(catalogos) > 1:
            avisos.append(
                f'{cid}: as caixas não usam a mesma community para este circuito '
                f'({", ".join(sorted(f"{a}:{g}xx" for a, g in catalogos))}) — vale a de '
                f'{base_cx["nome"]}; confira antes de mexer.'
            )
        circuitos[cid] = u
    return circuitos


def _unificar_globais(caixas):
    globais = {}
    for cx in caixas:
        for gid, g in (cx['mapa'].get('globais') or {}).items():
            u = globais.get(gid)
            if u is None:
                u = copy.deepcopy(g)
                u['circuitos'], u['sessoes'] = [], []
                for a in u['acoes'].values():
                    a['circuitos'] = []
                globais[gid] = u
            for cid in g['circuitos']:
                if cid not in u['circuitos']:
                    u['circuitos'].append(cid)
            u['sessoes'].extend(_marcar_caixa(g['sessoes'], cx))
            u['ipv4'] = u['ipv4'] or g['ipv4']
            u['ipv6'] = u['ipv6'] or g['ipv6']
            for chave, a in g['acoes'].items():
                ua = u['acoes'].setdefault(chave, {**copy.deepcopy(a), 'circuitos': []})
                for cid in a['circuitos']:
                    if cid not in ua['circuitos']:
                        ua['circuitos'].append(cid)
    for g in globais.values():
        g['circuitos'].sort()
        g['alcance'] = len(g['circuitos'])
        for a in g['acoes'].values():
            a['circuitos'].sort()
            a['alcance'] = len(a['circuitos'])
    return globais


def _communities_da_linha(mapa, linha):
    """As communities do CATÁLOGO que a linha carimba hoje (as extras ficam
    de fora: não são intenção de anúncio deste padrão)."""
    valores = set()
    for cid, chave in linha['destinos'].items():
        acao = mapa['circuitos'].get(cid, {}).get('acoes', {}).get(chave)
        if acao:
            valores.add(acao['community'])
    for gid, chave in linha['globais'].items():
        acao = (mapa.get('globais') or {}).get(gid, {}).get('acoes', {}).get(chave)
        if acao:
            valores.add(acao['community'])
    return valores


def _filtros_de(mapa, communities):
    """Nomes dos community-filters DESTA caixa que casam as communities."""
    filtros = set()
    destinos = list(mapa['circuitos'].values()) + list((mapa.get('globais') or {}).values())
    for d in destinos:
        for a in d['acoes'].values():
            if a['community'] in communities:
                filtros.add(a['filtro'])
    return filtros


def _fonte_da_rota(grupo, por_caixa, caixa_id, prefixo, familia):
    """
    De onde vem a rota que `caixa_id` usa para `prefixo`: ela mesma, se
    origina; senão a primeira caixa que origina E entrega a community pelo
    iBGP. Devolve `(caixa_origem_id | None, motivo)`.
    """
    if caixa_id in por_caixa:
        return caixa_id, ''
    motivos = []
    for origem_id in por_caixa:
        sessao = _sessao_para(grupo['enlaces'], origem_id, caixa_id, familia)
        nome_origem = grupo['nomes'][origem_id]
        if not sessao:
            motivos.append(f'{nome_origem} não tem sessão iBGP {familia.upper()} para esta caixa')
            continue
        exporta = _ibgp_exporta(grupo['dados'][origem_id], sessao, prefixo)
        if exporta is False:
            motivos.append(
                f'a policy de saída do iBGP de {nome_origem} ({sessao.get("policy_out") or "—"}) '
                f'não deixa {prefixo} passar'
            )
            continue
        if not sessao.get('advertise_community'):
            motivos.append(
                f'{nome_origem} não envia community pelo iBGP (falta '
                f'`peer {sessao["peer_ip"]} advertise-community`)'
            )
            continue
        return origem_id, ''
    return None, '; '.join(motivos)


def montar_mapa_unificado(caixas, enlaces):
    """
    O mesmo formato de `montar_mapa`, para as caixas juntas — o painel de
    community renderiza um ou outro sem saber a diferença, e as chaves a mais
    (`caixa`, `caixas_origem`, `divergencias`, `unificado`) só enriquecem.

    `caixas` é `[{'id', 'nome', 'dados', 'mapa'}]` (o `mapa` de cada uma já
    montado), a primeira é a principal (a que o operador abriu).

    Cada prefixo mostra a intenção que VALE de verdade em cada circuito: a
    da caixa onde o circuito está, se ela origina o prefixo, senão a que
    chega pelo iBGP. Quando as caixas que originam carimbam intenções
    diferentes, a linha sai com `divergencias` — é exatamente o "mudei de um
    lado e não teve efeito do outro".
    """
    avisos = []
    nomes = {cx['id']: cx['nome'] for cx in caixas}
    circuitos = _unificar_circuitos(caixas, avisos)
    globais = _unificar_globais(caixas)
    grupo = {'enlaces': enlaces, 'nomes': nomes,
             'dados': {cx['id']: cx['dados'] for cx in caixas},
             'mapas': {cx['id']: cx['mapa'] for cx in caixas}}

    asns = {cx['id']: _asn_community_prevalente(cx['mapa']['circuitos']) for cx in caixas}
    if len(set(asns.values())) > 1:
        avisos.append(
            'As caixas não usam o mesmo ASN de community (' +
            ', '.join(f'{nomes[i]} {a or "—"}' for i, a in asns.items()) +
            ') — a mesma intenção vira communities diferentes em cada uma.'
        )

    for e in enlaces:
        for lado, outro, chave in (('a', 'b', 'sessao_a'), ('b', 'a', 'sessao_b')):
            s = e[chave]
            if s and not s.get('advertise_community'):
                avisos.append(
                    f'iBGP {e["familia"].upper()} {nomes[e[lado]]} → {nomes[e[outro]]} '
                    f'({s["peer_ip"]}) sem `advertise-community`: prefixo originado só em '
                    f'{nomes[e[lado]]} chega em {nomes[e[outro]]} sem a intenção. A automação '
                    f'acrescenta o comando quando precisar.'
                )

    # Linhas de cada caixa, por prefixo.
    por_prefixo = {}
    for cx in caixas:
        for linha in cx['mapa']['anuncios']:
            por_prefixo.setdefault(linha['prefixo'], {}).setdefault(cx['id'], linha)

    principal = caixas[0]['id']
    anuncios = []
    for prefixo, por_caixa in por_prefixo.items():
        base_id = principal if principal in por_caixa else next(iter(por_caixa))
        base = por_caixa[base_id]
        familia = base['familia']
        linha = copy.deepcopy(base)
        linha['avisos'] = [f'{nomes[i]}: {a}' for i, l in por_caixa.items() for a in l['avisos']]
        linha['caixas_origem'] = [
            {'id': i, 'nome': nomes[i], 'route_policy': l['route_policy'],
             'editavel': l['editavel']}
            for i, l in por_caixa.items()
        ]
        linha['editavel'] = all(l['editavel'] for l in por_caixa.values())

        # Intenção que vale em cada circuito = a da rota que a caixa dele usa.
        fontes = {}
        destinos, efetivo, sem_intencao = {}, {}, {}
        for cid, c in circuitos.items():
            dono = c['caixa']
            if dono not in fontes:
                fontes[dono] = _fonte_da_rota(grupo, por_caixa, dono, prefixo, familia)
            origem, motivo = fontes[dono]
            if origem is None:
                if c['sessoes']:
                    sem_intencao[cid] = motivo
                continue
            linha_origem = por_caixa[origem]
            chave = linha_origem['destinos'].get(cid)
            if chave:
                destinos[cid] = chave
            mapa_dono = grupo['mapas'][dono]
            circ_dono = mapa_dono['circuitos'].get(cid)
            if not circ_dono:
                continue
            marcados = _filtros_de(mapa_dono, _communities_da_linha(grupo['mapas'][origem], linha_origem))
            efeito = _efeito_no_circuito(circ_dono, familia, marcados) if marcados else None
            if efeito:
                efetivo[cid] = {**efeito, 'caixa': dono, 'caixa_nome': nomes[dono]}
        linha['destinos'] = destinos
        linha['efetivo'] = efetivo
        linha['sem_intencao'] = sem_intencao
        # Grupo global: a caixa principal (ou a primeira que origina) manda;
        # divergência entre as caixas vai pra `divergencias` logo abaixo.
        linha['globais'] = dict(base['globais'])

        # Divergência = intenção carimbada que NÃO vale: uma caixa que não é
        # dona do circuito marca o prefixo com uma ação diferente da que a
        # dona aplica (o "mudei em CARMO e PATOS não obedeceu"). Caixa que
        # simplesmente não marca nada pro circuito dos outros não é
        # divergência — só ainda não foi sincronizada. Nos grupos globais
        # qualquer diferença conta: cada caixa aplica a sua aos próprios
        # circuitos.
        divergencias = []
        if len(por_caixa) > 1:
            ids = sorted({d for l in por_caixa.values() for d in l['destinos']})
            for cid in ids:
                c = circuitos.get(cid)
                if not c or not c['sessoes']:
                    continue
                vale = destinos.get(cid, '')
                donos = set(c['caixas'] or [c['caixa']])
                ignoradas = [i for i, l in por_caixa.items()
                             if i not in donos and l['destinos'].get(cid) and l['destinos'][cid] != vale]
                if ignoradas:
                    divergencias.append({
                        'destino': cid, 'vale': vale, 'vale_em': c['caixa_nome'],
                        'por_caixa': {nomes[i]: l['destinos'].get(cid, '') for i, l in por_caixa.items()},
                    })
            ids = sorted({g for l in por_caixa.values() for g in l['globais']})
            for gid in ids:
                valores = {nomes[i]: l['globais'].get(gid, '') for i, l in por_caixa.items()}
                if len(set(valores.values())) > 1:
                    divergencias.append({'destino': gid, 'vale': '', 'vale_em': '',
                                         'por_caixa': valores})
        linha['divergencias'] = divergencias
        # Sincronizada = toda caixa que origina carimba exatamente a intenção
        # que vale. É o que o botão "sincronizar" do painel resolve.
        alvo_valores = sorted(_valores_da_intencao(
            {'circuitos': circuitos, 'globais': globais}, destinos, linha['globais']))
        linha['sincronizada'] = all(
            sorted(_communities_da_linha(grupo['mapas'][i], l)) == alvo_valores
            for i, l in por_caixa.items()
        )
        anuncios.append(linha)
    anuncios.sort(key=_ordem_prefixo)

    mapa_principal = caixas[0]['mapa']
    ids_com_sessao = {cid for cid, c in circuitos.items() if c['sessoes']}
    slots_vagos = [s for s in mapa_principal.get('slots_vagos') or []
                   if s['id'] not in ids_com_sessao and s['id'] not in circuitos]

    downstreams = {}
    for cx in caixas:
        for did, d in (cx['mapa'].get('downstreams') or {}).items():
            chave = did if did not in downstreams else f'{did}@{cx["id"]}'
            downstreams[chave] = {**d, 'id': chave, 'caixa': cx['id'], 'caixa_nome': cx['nome'],
                                  'sessoes': _marcar_caixa(d.get('sessoes') or [], cx)}

    mapa = {
        'circuitos': circuitos,
        'globais': globais,
        'anuncios': anuncios,
        'acoes': mapa_principal['acoes'],
        'tipos': mapa_principal['tipos'],
        'slots_vagos': slots_vagos,
        'downstreams': downstreams,
        'padroes': mapa_principal.get('padroes') or {},
        'avisos': avisos + [f'{cx["nome"]}: {a}' for cx in caixas for a in cx['mapa']['avisos']],
    }
    mapa['unificado'] = {
        'caixas': [{'id': cx['id'], 'nome': cx['nome'], 'host': cx.get('host', ''),
                    'principal': cx['id'] == principal} for cx in caixas],
        'enlaces': [
            {'familia': e['familia'], 'a': e['a'], 'b': e['b'],
             'a_nome': nomes[e['a']], 'b_nome': nomes[e['b']],
             'peer_em_a': (e['sessao_a'] or {}).get('peer_ip', ''),
             'peer_em_b': (e['sessao_b'] or {}).get('peer_ip', ''),
             'advertise_a': bool((e['sessao_a'] or {}).get('advertise_community')),
             'advertise_b': bool((e['sessao_b'] or {}).get('advertise_community'))}
            for e in enlaces
        ],
        'divergentes': sum(1 for l in anuncios if l['divergencias']),
    }
    return mapa


# ─── Geração: uma intenção, os comandos de cada caixa ─────────────────────────

def _valores_da_intencao(uni, destinos, globais_sel):
    valores = []
    for cid, chave in sorted(destinos.items()):
        c = uni['circuitos'][cid]
        valores.append(community_de(c['asn_community'], c['grupo'], chave))
    for gid, chave in sorted(globais_sel.items()):
        valores.append(uni['globais'][gid]['acoes'][chave]['community'])
    return valores


def _bloco_advertise(as_local, sessao, familia):
    af = 'ipv6-family unicast' if familia == 'v6' else 'ipv4-family unicast'
    return [f'bgp {as_local}', af, f'peer {sessao["peer_ip"]} advertise-community', 'quit', 'quit']


def comandos_anuncio_unificado(caixas, enlaces, uni, prefixo, destino_id, chave_acao):
    """
    Muda a intenção de `prefixo` para `destino_id` no conjunto de caixas.
    Devolve `[{'acesso_id', 'nome', 'comandos', 'valores', 'route_policy',
    'node', 'advertise': [peer_ip…]}]`, só com as caixas que mudam algo,
    na ordem das caixas (principal primeiro).

    As validações são as de `comandos_definir_anuncio`, só que sobre o mapa
    unificado: um circuito tem sessão se tiver em QUALQUER caixa.
    """
    linha = next((l for l in uni['anuncios'] if l['prefixo'] == prefixo), None)
    if linha is None:
        raise AcaoBgpNaoSuportada(
            f'O prefixo {prefixo} não é originado por nenhuma das caixas — use "novo prefixo".'
        )
    if chave_acao and chave_acao not in ACOES_POR_CHAVE:
        raise AcaoBgpNaoSuportada(f'Ação "{chave_acao}" não existe no catálogo.')
    if chave_acao == 'import-rr':
        raise AcaoBgpNaoSuportada(
            'A ação "import-rr" é aplicada pela policy de entrada da sessão, não pela '
            'policy local de um prefixo.'
        )
    nao_editaveis = [o for o in linha['caixas_origem'] if not o['editavel']]
    if nao_editaveis:
        motivo = next((a for a in linha['avisos']), 'sem route-policy local editável')
        raise AcaoBgpNaoSuportada(
            f'{prefixo}: {", ".join(o["nome"] for o in nao_editaveis)} origina este prefixo '
            f'mas a policy local não é editável ({motivo}) — sem ela as caixas não ficam '
            f'sincronizadas.'
        )

    familia = linha['familia']
    if not destino_id:
        # Só sincronizar: a intenção que já vale em cada circuito passa a ser
        # carimbada igual em todas as caixas que originam o prefixo.
        return _plano_por_caixa(caixas, linha, prefixo,
                                _valores_da_intencao(uni, linha['destinos'], linha['globais']), {})
    global_ = destino_id.startswith('glob-')
    destino = (uni['globais'] if global_ else uni['circuitos']).get(destino_id)
    if not destino:
        raise AcaoBgpNaoSuportada(f'"{destino_id}" não existe em nenhuma das caixas.')
    if chave_acao and chave_acao not in destino['acoes']:
        raise AcaoBgpNaoSuportada(
            f'{destino_id} não tem community-filter para a ação "{chave_acao}".'
        )

    if global_:
        alcance = destino['acoes'][chave_acao]['circuitos'] if chave_acao else destino['circuitos']
        if chave_acao and not alcance:
            raise AcaoBgpNaoSuportada(
                f'Nenhuma policy de saída das caixas referencia '
                f'"{destino["acoes"][chave_acao]["filtro"]}" — a community global não anunciaria nada.'
            )
        donos = {uni['circuitos'][cid]['caixa'] for cid in alcance
                 if cid in uni['circuitos'] and uni['circuitos'][cid]['sessoes']}
        if chave_acao and not any(
            any(s['familia'] == familia for s in uni['circuitos'][cid]['sessoes'])
            for cid in alcance if cid in uni['circuitos']
        ):
            raise AcaoBgpNaoSuportada(
                f'Nenhum circuito alcançado por {destino_id} tem sessão {familia.upper()} — '
                f'{prefixo} é {familia.upper()}.'
            )
    else:
        if chave_acao and not destino['sessoes']:
            raise AcaoBgpNaoSuportada(
                f'O circuito {destino_id} não tem sessão BGP em nenhuma das caixas.'
            )
        if chave_acao and not any(s['familia'] == familia for s in destino['sessoes']):
            raise AcaoBgpNaoSuportada(
                f'O circuito {destino_id} não tem sessão {familia.upper()} — {prefixo} é '
                f'{familia.upper()}.'
            )
        donos = set(destino['caixas'] or [destino['caixa']])

    destinos = dict(linha['destinos'])
    globais_sel = dict(linha['globais'])
    alvo = globais_sel if global_ else destinos
    if chave_acao:
        alvo[destino_id] = chave_acao
    else:
        alvo.pop(destino_id, None)
    valores = _valores_da_intencao(uni, destinos, globais_sel)

    por_id = {cx['id']: cx for cx in caixas}
    origens = [o['id'] for o in linha['caixas_origem']]
    advertise = {}   # caixa de origem → [sessões que precisam de advertise-community]
    if chave_acao:
        for dono in sorted(donos):
            if dono in origens:
                continue
            motivos = []
            entregue = False
            for origem in origens:
                sessao = _sessao_para(enlaces, origem, dono, familia)
                if not sessao:
                    motivos.append(f'{por_id[origem]["nome"]} não tem iBGP {familia.upper()} '
                                   f'para {por_id[dono]["nome"]}')
                    continue
                if _ibgp_exporta(por_id[origem]['dados'], sessao, prefixo) is False:
                    motivos.append(
                        f'a policy {sessao.get("policy_out") or "—"} (iBGP de '
                        f'{por_id[origem]["nome"]}) não deixa {prefixo} passar'
                    )
                    continue
                if not sessao.get('advertise_community'):
                    advertise.setdefault(origem, []).append(sessao)
                entregue = True
                break
            if not entregue:
                raise AcaoBgpNaoSuportada(
                    f'{prefixo} não é originado em {por_id[dono]["nome"]} (onde está '
                    f'{destino_id}) e não chega lá pelo iBGP: {"; ".join(motivos)}.'
                )

    return _plano_por_caixa(caixas, linha, prefixo, valores, advertise)


def _plano_por_caixa(caixas, linha, prefixo, valores, advertise):
    """Os comandos de cada caixa que origina o prefixo — só das que mudam
    alguma coisa (a lista do catálogo já igual e nada de advertise a
    acrescentar = caixa fora do plano)."""
    origens = [o['id'] for o in linha['caixas_origem']]
    resultado = []
    for cx in caixas:
        if cx['id'] not in origens:
            continue
        linha_cx = next(l for l in cx['mapa']['anuncios'] if l['prefixo'] == prefixo)
        atuais = sorted(_communities_da_linha(cx['mapa'], linha_cx))
        precisa_advertise = advertise.get(cx['id'], [])
        if atuais == sorted(valores) and not precisa_advertise:
            continue
        comandos = []
        if atuais != sorted(valores):
            comandos = comandos_reescrever_communities(linha_cx, valores)[:-1]   # sem o commit
        for sessao in precisa_advertise:
            comandos += _bloco_advertise(_as_local(cx['dados']), sessao, linha['familia'])
        comandos.append('commit')
        resultado.append({
            'acesso_id': cx['id'], 'nome': cx['nome'], 'comandos': comandos,
            'valores': valores, 'route_policy': linha_cx['route_policy'],
            'node': linha_cx['node'],
            'advertise': [s['peer_ip'] for s in precisa_advertise],
        })
    if not resultado:
        raise AcaoBgpNaoSuportada(f'{prefixo}: as caixas já estão com essa intenção.')
    return resultado


def aplicar_efeito_unificado(dados, item):
    """Atualização otimista do snapshot de UMA caixa depois que a parte dela
    rodou com sucesso: a linha `apply community` do node local vira a lista
    nova (as extras numéricas ficam no fim, como no comando) e as sessões que
    ganharam `advertise-community` passam a constar como tal."""
    nodes = dados.setdefault('community_nodes', {})
    rp, node = item.get('route_policy'), item.get('node')
    lista = nodes.setdefault(rp, []) if rp else []
    alvo = next((n for n in lista if n['node'] == node), None)
    if rp and alvo is None:
        alvo = {'policy': rp, 'node': node, 'acao': 'permit', 'community_filters': [],
                'apply_community': [], 'apply_community_extra': [], 'prefix_lists': [],
                'prepend_as': [], 'local_preference': None}
        lista.append(alvo)
    if alvo is not None:
        catalogo = set()
        for cx_filtros in (dados.get('community_filters') or {}).values():
            for e in cx_filtros:
                catalogo.update(e.get('valores') or [])
        extras = [v for v in alvo['apply_community']
                  if v not in catalogo and _RE_COMMUNITY.match(v)]
        alvo['apply_community'] = list(item['valores']) + extras
    for s in dados.get('sessoes') or []:
        if s.get('peer_ip') in (item.get('advertise') or []):
            s['advertise_community'] = True
