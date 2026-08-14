"""
clientes/bgp_community_auto.py
Automação de anúncios BGP por community (Huawei/VRP).

O operador declara a INTENÇÃO — "anunciar o prefixo X para o circuito C02 com
2 prepends" — e o sistema resolve o resto:

    Prefixo → Circuito → Ação → Community → Route-Policy local → BGP

A convenção implementada é a que já está em produção em vários roteadores de
borda deste CRM (8 equipamentos Huawei confirmados nos backups em disco):

    ip community-filter basic c-02-export-2p index 10 permit 65100:50203
                              └──┬─┘ └──┬───┘                └─┬─┘└┬┘└┬┘
                            circuito   ação                   asn grupo
                                                                    sufixo da ação

    route-policy AS263941-TOGONET-V4-OUT permit node 12
     if-match community-filter c-02-export-2p
     apply as-path 268080 268080 additive

    route-policy RT-BGP-LOCAL-52DC-38 permit node 10
     apply community 65100:50203 additive        ← a "intenção" do prefixo

    network 2804:52DC:3000:: 38 route-policy RT-BGP-LOCAL-52DC-38

Ou seja: a policy LOCAL do prefixo carimba a community de intenção; a policy
OUT do circuito traduz essa community em comportamento (permitir, prepend,
no-export, bloquear). Mudar "para quem" e "como" um prefixo é anunciado é,
portanto, mudar UMA linha `apply community` — nunca mexer na route-policy de
uma sessão nem numa prefix-list compartilhada (mesmo princípio já seguido pelo
resto de `bgp_actions.py`).

Este módulo é composto por funções puras sobre o dict `BgpSnapshot.dados`
(nada de DB, nada de SSH — quem executa é `bgp_actions.executar_acao_bgp`):

    mapear_circuitos(dados)   → descobre os circuitos já configurados na caixa
    mapear_anuncios(dados, …) → matriz prefixo × circuito → ação em vigor
    comandos_*(…)             → gera a config Huawei de uma mudança de intenção
"""
import ipaddress
import re

from .bgp_actions import AcaoBgpNaoSuportada

# ─── Catálogo de ações ────────────────────────────────────────────────────────
# `sufixo` são os 2 últimos dígitos da community (65100:<grupo><sufixo>);
# `node` é a posição da ação dentro da route-policy OUT do circuito (o padrão
# em produção — ver docs/bgp_automacao.md); `prepend` é propriedade da AÇÃO,
# nunca cadastrada no anúncio (regra explícita da especificação).
ACOES = [
    {'chave': 'export',     'sufixo': '01', 'node': 10, 'prepend': 0,
     'rotulo': 'Anunciar (normal)',        'policy': 'permit'},
    {'chave': 'export-1p',  'sufixo': '02', 'node': 11, 'prepend': 1,
     'rotulo': 'Anunciar com 1 prepend',   'policy': 'permit'},
    {'chave': 'export-2p',  'sufixo': '03', 'node': 12, 'prepend': 2,
     'rotulo': 'Anunciar com 2 prepends',  'policy': 'permit'},
    {'chave': 'export-3p',  'sufixo': '04', 'node': 13, 'prepend': 3,
     'rotulo': 'Anunciar com 3 prepends',  'policy': 'permit'},
    {'chave': 'export-4p',  'sufixo': '05', 'node': 14, 'prepend': 4,
     'rotulo': 'Anunciar com 4 prepends',  'policy': 'permit'},
    {'chave': 'export-ne',  'sufixo': '08', 'node': 15, 'prepend': 0,
     'rotulo': 'Anunciar com no-export',   'policy': 'deny', 'apply': 'community no-export'},
    {'chave': 'export-df',  'sufixo': '09', 'node': 16, 'prepend': 0,
     'rotulo': 'Anunciar (default/específico)', 'policy': 'permit'},
    {'chave': 'export-bh',  'sufixo': '66', 'node': 17, 'prepend': 0,
     'rotulo': 'Blackhole',                'policy': 'permit'},
    {'chave': 'export-bl',  'sufixo': '67', 'node': 9,  'prepend': 0,
     'rotulo': 'Bloquear anúncio',         'policy': 'deny'},
    {'chave': 'import-rr',  'sufixo': '00', 'node': None, 'prepend': 0,
     'rotulo': 'Origem (rotas recebidas)', 'policy': None},
]
ACOES_POR_CHAVE = {a['chave']: a for a in ACOES}
ACOES_POR_SUFIXO = {a['sufixo']: a for a in ACOES}
# Ações que o operador escolhe num anúncio (import-rr é carimbada pela policy
# IN da sessão, não pelo prefixo local — ver §12 da especificação).
ACOES_DE_ANUNCIO = [a for a in ACOES if a['chave'] != 'import-rr']

NODE_CATCHALL = 999          # `deny node 999` — bloqueio final da policy OUT
NODE_LOCAL = 10              # node único das route-policies locais (RT-BGP-LOCAL-*)
ASN_COMMUNITY_PADRAO = '65100'
PREFIXO_POLICY_LOCAL = 'RT-BGP-LOCAL-'

_RE_FILTRO = re.compile(r'^c-(\d+)-(' + '|'.join(re.escape(a['chave']) for a in ACOES) + r')$')
_RE_COMMUNITY = re.compile(r'^(\d+):(\d+)$')
_RE_NOME_POLICY = re.compile(r'^AS(\d+)-(.+?)-V([46])-(IN|OUT)$', re.IGNORECASE)


def _vendor_ok(vendor):
    if vendor != 'huawei':
        raise AcaoBgpNaoSuportada(
            'A automação de anúncios por community é específica de Huawei/VRP — '
            f'este host é "{vendor}".'
        )


def community_de(asn, grupo, chave_acao):
    """'65100' + '502' + 'export-2p' → '65100:50203'."""
    acao = ACOES_POR_CHAVE.get(chave_acao)
    if not acao:
        raise AcaoBgpNaoSuportada(f'Ação "{chave_acao}" não existe no catálogo.')
    return f'{asn}:{grupo}{acao["sufixo"]}'


def _decompor_community(valor):
    """'65100:50203' → ('65100', '502', 'export-2p'). Devolve None se o valor
    não seguir o padrão <asn>:<grupo><sufixo> deste catálogo."""
    m = _RE_COMMUNITY.match(valor or '')
    if not m:
        return None
    asn, resto = m.group(1), m.group(2)
    if len(resto) < 3:
        return None
    grupo, sufixo = resto[:-2], resto[-2:]
    acao = ACOES_POR_SUFIXO.get(sufixo)
    if not acao:
        return None
    return asn, grupo, acao['chave']


def _familia_do_prefixo(prefixo):
    return 'v6' if ':' in (prefixo or '') else 'v4'


# ─── Descoberta: quais circuitos a caixa já tem configurados ──────────────────

def mapear_circuitos(dados):
    """
    Lê `community_filters`/`community_nodes`/`sessoes` do snapshot e devolve os
    circuitos que a caixa já tem configurados, cada um com seu grupo de
    community, ações disponíveis, policies e sessões BGP vinculadas.

    O vínculo circuito ↔ sessão é feito pela EXPORT POLICY da sessão (a policy
    que dá `if-match community-filter c-NN-*` é, por definição, a policy de
    saída daquele circuito) — não pelo nome da policy nem pela community de
    origem. Foi a única regra que se sustentou nos 8 equipamentos reais: nomes
    seguem a convenção `AS<asn>-<NOME>-V4-OUT` na maioria, mas há exceções
    (`RP-IX-SP-V4-OUT`), e há caixas onde a policy IN de um circuito carimba a
    community de origem de OUTRO (copy/paste real, reportado em `avisos`).

    Devolve `{'circuitos': {...}, 'avisos': [...]}`.
    """
    filtros = dados.get('community_filters') or {}
    nodes = dados.get('community_nodes') or {}
    sessoes = dados.get('sessoes') or []
    avisos = []

    # ── 1. Circuitos a partir dos community-filters ───────────────────────────
    circuitos = {}
    for nome_filtro, entradas in filtros.items():
        m = _RE_FILTRO.match(nome_filtro)
        if not m:
            continue
        cid, chave_acao = m.group(1), m.group(2)
        valores = [v for e in entradas if e['acao'] == 'permit' for v in e['valores']]
        if not valores:
            continue
        c = circuitos.setdefault(cid, {
            'id': cid, 'nome': '', 'peer_as': '', 'prepend_as': '',
            'grupo': '', 'asn_community': '', 'acoes': {}, 'policies': {},
            'policies_in_todas': [], 'policies_out_todas': {'v4': [], 'v6': []},
            'sessoes': [], 'ipv4': False, 'ipv6': False, 'completo': False,
        })
        c['acoes'][chave_acao] = {
            'filtro': nome_filtro, 'community': valores[0],
            'multiplos': valores[1:],
        }

    # ── 2. Grupo/ASN do circuito ─────────────────────────────────────────────
    # Derivado da ação `export` (a mais confiável — é a base do padrão). As
    # demais ações só confirmam: divergência vira aviso em vez de mudar o
    # grupo, porque em produção existem caixas onde `c-04-export-bh` aponta
    # pra community do grupo 503 (copy/paste ao clonar o bloco do c-03) — o
    # circuito continua sendo o 504, só aquele filtro está errado.
    for cid, c in circuitos.items():
        votos = {}
        for chave, a in c['acoes'].items():
            dec = _decompor_community(a['community'])
            a['grupo_declarado'] = dec[1] if dec else ''
            a['asn_declarado'] = dec[0] if dec else ''
            a['acao_declarada'] = dec[2] if dec else ''
            if dec:
                votos.setdefault((dec[0], dec[1]), []).append(chave)
        if not votos:
            avisos.append(f'c-{cid}: nenhuma community reconhecível no padrão <asn>:<grupo><ação>.')
            continue
        base = c['acoes'].get('export') or c['acoes'].get('export-1p')
        if base and base.get('grupo_declarado'):
            c['asn_community'], c['grupo'] = base['asn_declarado'], base['grupo_declarado']
        else:
            c['asn_community'], c['grupo'] = max(votos, key=lambda k: len(votos[k]))
        for (asn, grupo), chaves in votos.items():
            if (asn, grupo) != (c['asn_community'], c['grupo']):
                avisos.append(
                    f'c-{cid}: {", ".join(sorted(chaves))} usa(m) community do grupo '
                    f'{asn}:{grupo}xx, mas o circuito é do grupo {c["asn_community"]}:{c["grupo"]}xx '
                    f'— provável cópia do bloco de outro circuito.'
                )
        for chave, a in c['acoes'].items():
            esperada = community_de(c['asn_community'], c['grupo'], chave)
            a['esperada'] = esperada
            a['conforme'] = (a['community'] == esperada)
            if a['acao_declarada'] and a['acao_declarada'] != chave:
                avisos.append(
                    f'c-{cid}: o filtro "{a["filtro"]}" aponta pra {a["community"]}, '
                    f'que no padrão significa "{a["acao_declarada"]}" (esperado {esperada}).'
                )

    # ── 3. Policies OUT: quem referencia os filtros de cada circuito ─────────
    policy_do_circuito = {}
    for nome_policy, lista_nodes in nodes.items():
        refs = {}
        for n in lista_nodes:
            for f in n['community_filters']:
                m = _RE_FILTRO.match(f)
                if m:
                    refs.setdefault(m.group(1), 0)
                    refs[m.group(1)] += 1
        if not refs:
            continue
        cid = max(refs, key=lambda k: refs[k])
        policy_do_circuito[nome_policy] = cid
        if len(refs) > 1:
            outros = ', '.join(f'c-{k}' for k in sorted(refs) if k != cid)
            avisos.append(
                f'A route-policy "{nome_policy}" (circuito c-{cid}) também referencia '
                f'filtros de {outros} — nós dessa policy respondem por mais de um circuito.'
            )

    # ── 4. Sessões: o vínculo real circuito ↔ peer ───────────────────────────
    for sessao in sessoes:
        policy_out = sessao.get('policy_out') or ''
        cid = policy_do_circuito.get(policy_out)
        if not cid or cid not in circuitos:
            continue
        c = circuitos[cid]
        familia = _familia_do_prefixo(sessao.get('peer_ip', ''))
        c['sessoes'].append({
            'nome': sessao.get('nome', ''), 'peer_ip': sessao.get('peer_ip', ''),
            'peer_as': sessao.get('peer_as', ''), 'descricao': sessao.get('descricao', ''),
            'familia': familia, 'habilitada': sessao.get('habilitada', True),
            'policy_out': policy_out, 'policy_in': sessao.get('policy_in', ''),
        })
        c[('ipv6' if familia == 'v6' else 'ipv4')] = True
        anterior = c['policies'].get(f'{familia}_out')
        if policy_out not in c['policies_out_todas'][familia]:
            c['policies_out_todas'][familia].append(policy_out)
        if anterior and anterior != policy_out:
            avisos.append(
                f'c-{c["id"]}: mais de uma route-policy de saída {familia.upper()} '
                f'("{anterior}" e "{policy_out}") — sessões diferentes do mesmo circuito. '
                f'Ao gerar config, escolha explicitamente em qual delas aplicar.'
            )
        else:
            c['policies'][f'{familia}_out'] = policy_out
        if sessao.get('policy_in'):
            c['policies'].setdefault(f'{familia}_in', sessao['policy_in'])
            # Todas as policies de entrada do circuito (mais de uma quando o
            # circuito tem várias sessões) — a validação da community de
            # origem confere cada uma, não só a primeira.
            if sessao['policy_in'] not in c['policies_in_todas']:
                c['policies_in_todas'].append(sessao['policy_in'])
        if sessao.get('peer_as') and not c['peer_as']:
            c['peer_as'] = sessao['peer_as']

    # Policies OUT que existem mas não estão presas a nenhuma sessão — o
    # circuito está configurado "pela metade" (config pronta, peer ainda não).
    for nome_policy, cid in policy_do_circuito.items():
        if cid not in circuitos:
            continue
        c = circuitos[cid]
        if nome_policy in c['policies'].values():
            continue
        familia = 'v6' if re.search(r'-V6-|IPV6|IPv6', nome_policy) else 'v4'
        direcao = 'in' if nome_policy.upper().endswith('-IN') else 'out'
        c['policies'].setdefault(f'{familia}_{direcao}_orfa', nome_policy)

    # ── 5. Nome e ASNs a partir das policies ─────────────────────────────────
    for c in circuitos.values():
        for chave in ('v4_out', 'v6_out', 'v4_out_orfa', 'v6_out_orfa', 'v4_in', 'v6_in'):
            m = _RE_NOME_POLICY.match(c['policies'].get(chave, ''))
            if m:
                if not c['nome']:
                    c['nome'] = m.group(2)
                if not c['peer_as']:
                    c['peer_as'] = m.group(1)
        # ASN de prepend: o mais frequente entre os nodes de saída do
        # circuito. Caixa real (acesso 1216) tem policies do mesmo circuito
        # prependando ASNs diferentes — a maioria é o palpite menos ruim, e a
        # divergência vira aviso pro operador conferir.
        preps = {}
        for chave in ('v4_out', 'v6_out', 'v4_out_orfa', 'v6_out_orfa'):
            for n in nodes.get(c['policies'].get(chave, ''), []):
                for asn in n['prepend_as']:
                    preps[asn] = preps.get(asn, 0) + 1
        c['prepend_as'] = max(preps, key=lambda k: preps[k]) if preps else ''
        if len(preps) > 1:
            avisos.append(
                f'c-{c["id"]}: prepend usa mais de um ASN nas policies de saída '
                f'({", ".join(sorted(preps))}) — o sistema assume {c["prepend_as"]}.'
            )
        c['faltando'] = sorted(
            a['chave'] for a in ACOES_DE_ANUNCIO if a['chave'] not in c['acoes']
        )
        c['completo'] = not c['faltando'] and bool(c['sessoes'])
        c['rotulo'] = f'c-{c["id"]}' + (f' — {c["nome"]}' if c['nome'] else '')

    # ── 6. Validação da policy IN (community de origem) ──────────────────────
    for c in circuitos.values():
        rr = c['acoes'].get('import-rr')
        if not rr:
            continue
        for nome_policy in c['policies_in_todas']:
            aplicadas = {v for n in nodes.get(nome_policy, []) for v in n['apply_community']}
            if rr['community'] not in aplicadas:
                avisos.append(
                    f'c-{c["id"]}: a policy de entrada "{nome_policy}" não carimba '
                    f'{rr["community"]} ({rr["filtro"]}) — rotas recebidas por este '
                    f'circuito ficam sem a community de origem.'
                )

    return {'circuitos': circuitos, 'avisos': avisos}


# ─── Descoberta: o que cada prefixo local anuncia hoje ────────────────────────

def mapear_anuncios(dados, circuitos):
    """
    Monta a matriz prefixo × circuito → ação em vigor, a partir das
    communities aplicadas na route-policy local de cada `network`.

    Cada linha: `{prefixo, familia, route_policy, node, destinos: {cid: acao},
    communities_extras: [...], avisos: [...]}`. `communities_extras` são as
    communities que a policy local aplica e NÃO pertencem a nenhum circuito
    deste catálogo (ex: 65100:10091 — convenções próprias do cliente): são
    preservadas intactas em qualquer reescrita.
    """
    nodes = dados.get('community_nodes') or {}
    networks = dados.get('networks') or []

    por_community = {}
    for cid, c in circuitos.items():
        for chave, a in c['acoes'].items():
            por_community.setdefault(a['community'], (cid, chave))

    linhas, vistos = [], set()
    for net in networks:
        prefixo = net.get('prefixo', '')
        rp = net.get('route_policy', '')
        chave_unica = (prefixo, rp)
        if not prefixo or chave_unica in vistos:
            continue
        vistos.add(chave_unica)

        destinos, extras, avisos_linha = {}, [], []
        node_local = None
        lista = nodes.get(rp, []) if rp else []
        candidatos = [n for n in lista if n['apply_community'] or n['apply_community_extra']]
        if len(candidatos) > 1:
            avisos_linha.append(
                f'A policy local "{rp}" tem mais de um node aplicando community '
                f'(nodes {", ".join(str(n["node"]) for n in candidatos)}) — '
                f'edição automática desativada para este prefixo.'
            )
        elif candidatos:
            node_local = candidatos[0]
            for valor in node_local['apply_community']:
                achado = por_community.get(valor)
                if achado:
                    cid, chave = achado
                    if cid in destinos and destinos[cid] != chave:
                        avisos_linha.append(
                            f'{prefixo}: duas ações conflitantes para o circuito c-{cid} '
                            f'({destinos[cid]} e {chave}).'
                        )
                    destinos[cid] = chave
                else:
                    extras.append(valor)
            extras.extend(node_local['apply_community_extra'])
        elif rp and lista:
            avisos_linha.append(f'A policy local "{rp}" existe mas não aplica nenhuma community.')
        elif rp:
            avisos_linha.append(f'A route-policy "{rp}" referenciada pelo network não foi encontrada.')

        linhas.append({
            'prefixo': prefixo,
            'familia': net.get('familia') or _familia_do_prefixo(prefixo),
            'ip': net.get('ip', ''), 'mascara': net.get('mascara', ''),
            'route_policy': rp,
            'node': node_local['node'] if node_local else None,
            'editavel': bool(rp) and node_local is not None,
            'destinos': destinos,
            'communities_extras': extras,
            'avisos': avisos_linha,
        })

    linhas.sort(key=_ordem_prefixo)
    return linhas


def _ordem_prefixo(linha):
    """v4 antes de v6, depois ordem numérica de rede/máscara (e não a
    alfabética, que embaralharia 45.166.104.0/22 com 45.166.9.0/24)."""
    try:
        rede = ipaddress.ip_network(linha['prefixo'], strict=False)
        return (rede.version, rede.network_address.packed, rede.prefixlen)
    except ValueError:
        return (9, b'', linha['prefixo'])


def montar_mapa(dados, vendor='huawei'):
    """Descoberta completa: circuitos + matriz de anúncios + validações."""
    _vendor_ok(vendor)
    mapa = mapear_circuitos(dados)
    mapa['anuncios'] = mapear_anuncios(dados, mapa['circuitos'])
    mapa['acoes'] = [
        {'chave': a['chave'], 'rotulo': a['rotulo'], 'prepend': a['prepend']}
        for a in ACOES_DE_ANUNCIO
    ]
    mapa['avisos'].extend(validar_mapa(dados, mapa))
    return mapa


# ─── Validações (§16 da especificação) ────────────────────────────────────────

def validar_mapa(dados, mapa):
    """
    Confere o que dá pra conferir estaticamente antes de qualquer geração de
    config: nó 999 presente, filtros referenciados que não existem, ações sem
    node na policy OUT, prepend incompatível com a ação e circuito sem ASN de
    prepend. Devolve uma lista de avisos (texto) — não levanta exceção: são
    achados sobre a config JÁ EXISTENTE do equipamento, não erros do operador.
    """
    avisos = []
    nodes = dados.get('community_nodes') or {}
    filtros = dados.get('community_filters') or {}
    policies_todas = set(nodes) | set(dados.get('policies') or {})

    for nome_policy, lista in nodes.items():
        for n in lista:
            for f in n['community_filters']:
                if f not in filtros:
                    avisos.append(
                        f'"{nome_policy}" node {n["node"]} referencia o community-filter '
                        f'"{f}", que não existe no equipamento.'
                    )

    for cid, c in mapa['circuitos'].items():
        for chave_policy in ('v4_out', 'v6_out'):
            nome_policy = c['policies'].get(chave_policy)
            if not nome_policy:
                continue
            todos_nodes = [t['ordem'] for t in (dados.get('policies') or {}).get(nome_policy, [])]
            todos_nodes += [n['node'] for n in nodes.get(nome_policy, [])]
            if NODE_CATCHALL not in todos_nodes:
                avisos.append(
                    f'"{nome_policy}" não tem o `deny node {NODE_CATCHALL}` final — '
                    f'rotas sem community de anúncio podem vazar pra este circuito.'
                )
            por_filtro = {f: n for n in nodes.get(nome_policy, []) for f in n['community_filters']}
            for chave_acao, a in c['acoes'].items():
                if chave_acao == 'import-rr':
                    continue
                n = por_filtro.get(a['filtro'])
                if n is None:
                    avisos.append(
                        f'c-{cid}: a ação "{chave_acao}" tem community-filter mas nenhum node '
                        f'em "{nome_policy}" — usar essa ação neste circuito não produz efeito.'
                    )
                    continue
                esperado = ACOES_POR_CHAVE[chave_acao]['prepend']
                if len(n['prepend_as']) != esperado:
                    avisos.append(
                        f'c-{cid}: "{nome_policy}" node {n["node"]} ({chave_acao}) aplica '
                        f'{len(n["prepend_as"])} prepend(s), mas a ação define {esperado}.'
                    )
        if c['sessoes'] and not c['prepend_as']:
            avisos.append(
                f'c-{cid}: nenhum ASN de prepend identificado nas policies de saída — '
                f'as ações de prepend não podem ser geradas para este circuito.'
            )
        for chave_policy in ('v4_in', 'v6_in', 'v4_out', 'v6_out'):
            nome_policy = c['policies'].get(chave_policy)
            if nome_policy and nome_policy not in policies_todas:
                avisos.append(
                    f'c-{cid}: a sessão referencia a route-policy "{nome_policy}", '
                    f'que não existe no equipamento.'
                )
    return avisos


# ─── Geração de config: mudar a intenção de um prefixo ────────────────────────

def _linha_apply_community(valores, extras):
    partes = list(valores) + list(extras)
    return f'apply community {" ".join(partes)} additive'


def _achar_linha(mapa, prefixo, route_policy=''):
    candidatos = [l for l in mapa['anuncios'] if l['prefixo'] == prefixo
                  and (not route_policy or l['route_policy'] == route_policy)]
    if not candidatos:
        raise AcaoBgpNaoSuportada(
            f'O prefixo {prefixo} não é originado por nenhum `network` neste equipamento — '
            f'use "novo prefixo" para criá-lo.'
        )
    if len(candidatos) > 1:
        raise AcaoBgpNaoSuportada(
            f'O prefixo {prefixo} aparece em mais de um `network` com policies locais '
            f'diferentes ({", ".join(sorted(c["route_policy"] or "—" for c in candidatos))}) — '
            f'edite manualmente.'
        )
    return candidatos[0]


def comandos_definir_anuncio(dados, mapa, prefixo, circuito_id, chave_acao, route_policy=''):
    """
    Muda a intenção de anúncio de UM prefixo para UM circuito.

    `chave_acao` vazia remove o prefixo daquele circuito (deixa de ser
    anunciado por lá — o `deny node 999` da policy OUT faz o resto).

    A mudança é sempre uma reescrita completa da linha `apply community` da
    route-policy LOCAL do prefixo: as communities dos OUTROS circuitos e as
    que não pertencem a este catálogo (`communities_extras`) são reemitidas
    exatamente como estavam. O par `undo apply community` + `apply community …`
    é necessário porque no VRP um `apply community` repetido SOMA à lista já
    configurada no node — sem o `undo` não haveria como remover nada. Como o
    Huawei desta automação usa config candidata + `commit` (ver
    `bgp_actions._PRECISA_COMMIT`), as duas linhas viram uma alteração atômica:
    o prefixo nunca fica um instante sem communities na config em vigor.
    """
    linha = _achar_linha(mapa, prefixo, route_policy)
    circuito = mapa['circuitos'].get(circuito_id)
    if not circuito:
        raise AcaoBgpNaoSuportada(f'Circuito "c-{circuito_id}" não existe neste equipamento.')
    if chave_acao and chave_acao not in ACOES_POR_CHAVE:
        raise AcaoBgpNaoSuportada(f'Ação "{chave_acao}" não existe no catálogo.')
    if chave_acao == 'import-rr':
        raise AcaoBgpNaoSuportada(
            'A ação "import-rr" identifica rotas RECEBIDAS do circuito — é aplicada pela '
            'policy de entrada da sessão, não pela policy local de um prefixo.'
        )
    if not linha['editavel']:
        motivo = linha['avisos'][0] if linha['avisos'] else 'sem route-policy local editável.'
        raise AcaoBgpNaoSuportada(f'{prefixo}: {motivo}')
    if chave_acao and chave_acao not in circuito['acoes']:
        raise AcaoBgpNaoSuportada(
            f'O circuito c-{circuito_id} não tem community-filter para a ação "{chave_acao}" — '
            f'gere a config faltante do circuito antes de usar esta ação.'
        )
    if not circuito['sessoes']:
        raise AcaoBgpNaoSuportada(
            f'O circuito c-{circuito_id} não tem nenhuma sessão BGP vinculada neste '
            f'equipamento — não há para onde anunciar.'
        )
    familia_ok = any(s['familia'] == linha['familia'] for s in circuito['sessoes'])
    if not familia_ok:
        raise AcaoBgpNaoSuportada(
            f'O circuito c-{circuito_id} não tem sessão {linha["familia"].upper()} — '
            f'{prefixo} é {linha["familia"].upper()}.'
        )
    if linha['destinos'].get(circuito_id) == chave_acao:
        raise AcaoBgpNaoSuportada(
            f'{prefixo} já está com a ação "{chave_acao}" para o circuito c-{circuito_id}.'
        )

    destinos = dict(linha['destinos'])
    if chave_acao:
        destinos[circuito_id] = chave_acao
    else:
        destinos.pop(circuito_id, None)

    valores = [community_de(mapa['circuitos'][cid]['asn_community'],
                            mapa['circuitos'][cid]['grupo'], chave)
               for cid, chave in sorted(destinos.items())]
    extras = [v for v in linha['communities_extras'] if _RE_COMMUNITY.match(v)]
    palavras = [v for v in linha['communities_extras'] if not _RE_COMMUNITY.match(v)]

    comandos = [f'route-policy {linha["route_policy"]} permit node {linha["node"]}',
                'undo apply community']
    if valores or extras or palavras:
        comandos.append(_linha_apply_community(valores + extras, palavras))
    comandos += ['quit', 'commit']
    return comandos


def comandos_novo_prefixo(dados, mapa, prefixo, destinos, nome_policy=''):
    """
    Passa a originar um prefixo NOVO já com a intenção de anúncio definida:
    cria a route-policy local, aplica as communities dos circuitos escolhidos e
    prende o `network` a ela dentro da address-family certa.

    `destinos` é `{circuito_id: chave_acao}`.
    """
    try:
        rede = ipaddress.ip_network(prefixo, strict=False)
    except ValueError as e:
        raise AcaoBgpNaoSuportada(f'Prefixo inválido: {e}')
    if str(rede) != prefixo:
        prefixo = str(rede)

    familia = 'v6' if rede.version == 6 else 'v4'
    as_local = (dados.get('sessoes') or [{}])[0].get('as_local', '') or dados.get('as_local', '')
    if not as_local:
        raise AcaoBgpNaoSuportada('Não foi possível identificar o ASN local (`bgp <ASN>`) deste equipamento.')

    for net in dados.get('networks') or []:
        if net.get('prefixo') == prefixo:
            raise AcaoBgpNaoSuportada(
                f'{prefixo} já é originado por este equipamento '
                f'(route-policy {net.get("route_policy") or "—"}) — edite o anúncio existente.'
            )

    nome_policy = (nome_policy or _nome_policy_local(rede)).strip()
    if not nome_policy.startswith(PREFIXO_POLICY_LOCAL):
        raise AcaoBgpNaoSuportada(
            f'O nome da policy local deve começar com "{PREFIXO_POLICY_LOCAL}" '
            f'(convenção deste equipamento).'
        )
    if nome_policy in (dados.get('community_nodes') or {}) or nome_policy in (dados.get('policies') or {}):
        raise AcaoBgpNaoSuportada(
            f'Já existe uma route-policy chamada "{nome_policy}" — escolha outro nome.'
        )

    valores = []
    for cid, chave in sorted((destinos or {}).items()):
        circuito = mapa['circuitos'].get(cid)
        if not circuito:
            raise AcaoBgpNaoSuportada(f'Circuito "c-{cid}" não existe neste equipamento.')
        if chave not in circuito['acoes']:
            raise AcaoBgpNaoSuportada(
                f'O circuito c-{cid} não tem community-filter para a ação "{chave}".'
            )
        if not any(s['familia'] == familia for s in circuito['sessoes']):
            raise AcaoBgpNaoSuportada(
                f'O circuito c-{cid} não tem sessão {familia.upper()} — '
                f'{prefixo} é {familia.upper()}.'
            )
        valores.append(community_de(circuito['asn_community'], circuito['grupo'], chave))

    if familia == 'v4':
        rede_arg = f'{rede.network_address} {rede.netmask}'
        af = 'ipv4-family unicast'
    else:
        rede_arg = f'{rede.network_address} {rede.prefixlen}'
        af = 'ipv6-family unicast'

    comandos = [f'route-policy {nome_policy} permit node {NODE_LOCAL}']
    if valores:
        comandos.append(_linha_apply_community(valores, []))
    comandos += [
        'quit',
        f'bgp {as_local}',
        af,
        f'network {rede_arg} route-policy {nome_policy}',
        'quit',
        'quit',
        'commit',
    ]
    return comandos


def _nome_policy_local(rede):
    """Nome no padrão já usado nas caixas: RT-BGP-LOCAL-<token>-<tamanho>,
    onde `token` é o octeto/hexteto que distingue esse bloco dos vizinhos
    (45.166.104.0/22 → 104; 2804:52DC:3400::/38 → 3400; 2804:8570::/32 → 8570)."""
    if rede.version == 4:
        # 2 dígitos como nas caixas reais (45.169.4.0/22 → RT-BGP-LOCAL-04-22)
        token = f'{int(str(rede.network_address).split(".")[2]):02d}'
    else:
        grupos = rede.exploded.split('/')[0].split(':')
        # 3º hexteto quando ele distingue o bloco (ex: :3400::/38), senão o 2º
        token = grupos[2].lstrip('0') or ''
        if not token or int(grupos[2], 16) == 0:
            token = grupos[1].lstrip('0') or grupos[1]
        token = token.upper()
    return f'{PREFIXO_POLICY_LOCAL}{token}-{rede.prefixlen}'


# ─── Geração de config: completar/criar o bloco de um circuito ────────────────

def _node_livre(ocupados, acao):
    """
    Node onde encaixar a ação na route-policy de saída: o canônico do catálogo
    quando estiver livre, senão o próximo livre.

    A ordem entre os nodes de ação não é crítica — cada um casa um
    community-filter diferente e um prefixo carrega no máximo UMA community
    por circuito, então nunca há dois nodes disputando a mesma rota. As duas
    exceções, que o encaixe respeita: o `deny` de bloqueio (`export-bl`) tem
    que vir ANTES dos permits (uma rota marcada por engano com bloqueio +
    anúncio precisa cair no bloqueio primeiro), e o `deny node 999` final tem
    que continuar sendo o último. Caso real que motivou o fallback: policies de
    peering CDN (`AS40027-NETFLIX-OCA-V4-OUT`) já usam o node 15 pra outra
    coisa, e o catálogo reservava esse número pro `export-ne`.
    """
    preferido = acao['node']
    if preferido not in ocupados:
        return preferido
    if acao['chave'] == 'export-bl':
        for n in range(preferido - 1, 0, -1):
            if n not in ocupados:
                return n
    for n in range(preferido + 1, NODE_CATCHALL):
        if n not in ocupados:
            return n
    return None


def comandos_provisionar_circuito(dados, mapa, circuito_id, opcoes=None):
    """
    Gera a config padrão que falta para um circuito: os `ip community-filter`
    das ações ausentes e os nodes correspondentes nas route-policies de saída
    (IPv4 e/ou IPv6), incluindo o `deny node 999` final quando não existir.

    Serve tanto pra COMPLETAR um circuito já existente (o caso comum: caixa
    com os filtros mas faltando um node, ou vice-versa) quanto pra CRIAR um
    circuito novo do zero (`opcoes` traz grupo/nome/asn/prepend/famílias).
    Só emite o que falta — nada existente é sobrescrito.
    """
    opcoes = opcoes or {}
    circuito = mapa['circuitos'].get(circuito_id)
    nodes = dados.get('community_nodes') or {}
    filtros = dados.get('community_filters') or {}

    if circuito:
        grupo = circuito['grupo']
        asn_community = circuito['asn_community'] or ASN_COMMUNITY_PADRAO
        prepend_as = opcoes.get('prepend_as') or circuito['prepend_as']
        nome = opcoes.get('nome') or circuito['nome']
        peer_as = opcoes.get('peer_as') or circuito['peer_as']
        policies = dict(circuito['policies'])
    else:
        grupo = str(opcoes.get('grupo') or '').strip()
        if not re.match(r'^\d{3}$', grupo):
            raise AcaoBgpNaoSuportada('Informe um grupo de community de 3 dígitos (ex: 502).')
        asn_community = str(opcoes.get('asn_community') or ASN_COMMUNITY_PADRAO)
        prepend_as = str(opcoes.get('prepend_as') or '').strip()
        nome = (opcoes.get('nome') or '').strip().upper()
        peer_as = str(opcoes.get('peer_as') or '').strip()
        policies = {}
        if not nome or not peer_as:
            raise AcaoBgpNaoSuportada('Circuito novo precisa de nome e ASN remoto.')
        if not re.match(r'^\d{2,3}$', str(circuito_id)):
            raise AcaoBgpNaoSuportada('Identificador de circuito inválido (esperado c-NN).')

    familias = []
    if opcoes.get('ipv4', circuito['ipv4'] if circuito else False):
        familias.append('v4')
    if opcoes.get('ipv6', circuito['ipv6'] if circuito else False):
        familias.append('v6')
    if not familias:
        raise AcaoBgpNaoSuportada('Escolha pelo menos uma família (IPv4 ou IPv6).')

    comandos = []

    # 1) community-filters faltantes
    for acao in ACOES:
        nome_filtro = f'c-{circuito_id}-{acao["chave"]}'
        if nome_filtro in filtros:
            continue
        comandos.append(
            f'ip community-filter basic {nome_filtro} index 10 permit '
            f'{community_de(asn_community, grupo, acao["chave"])}'
        )

    # 2) nodes faltantes nas policies de saída
    for familia in familias:
        # A escolha explícita do operador (campo do modal) tem prioridade: um
        # mesmo circuito pode ter mais de uma policy de saída na mesma família
        # (duas sessões pro mesmo upstream), e aí só ele sabe qual completar.
        nome_policy = (opcoes.get(f'policy_{familia}_out') or '').strip()
        if not nome_policy:
            candidatas = (circuito or {}).get('policies_out_todas', {}).get(familia, [])
            if len(candidatas) > 1:
                raise AcaoBgpNaoSuportada(
                    f'O circuito c-{circuito_id} tem mais de uma route-policy de saída '
                    f'{familia.upper()} ({", ".join(candidatas)}) — escolha em qual aplicar.'
                )
            nome_policy = policies.get(f'{familia}_out') or policies.get(f'{familia}_out_orfa') or ''
        if not nome_policy:
            # Não existe policy de saída pra esse circuito/família ainda — o
            # nome é montado na convenção AS{asn}-{NOME}-V4-OUT, e pra isso
            # os dois campos são obrigatórios (senão sairia "AS--V4-OUT").
            if not nome or not peer_as:
                raise AcaoBgpNaoSuportada(
                    f'O circuito c-{circuito_id} ainda não tem route-policy de saída '
                    f'{familia.upper()} — informe o nome do circuito e o ASN remoto '
                    f'para que ela possa ser criada.'
                )
            nome_policy = f'AS{peer_as}-{nome}-{familia.upper()}-OUT'
        existentes = {f: n for n in nodes.get(nome_policy, []) for f in n['community_filters']}
        nodes_ocupados = {n['node'] for n in nodes.get(nome_policy, [])}
        nodes_ocupados |= {t['ordem'] for t in (dados.get('policies') or {}).get(nome_policy, [])}

        for acao in ACOES_DE_ANUNCIO:
            nome_filtro = f'c-{circuito_id}-{acao["chave"]}'
            if nome_filtro in existentes:
                continue
            node = _node_livre(nodes_ocupados, acao)
            if node is None:
                raise AcaoBgpNaoSuportada(
                    f'Não sobrou node livre em "{nome_policy}" para a ação "{acao["chave"]}" — '
                    f'renumere a policy manualmente.'
                )
            nodes_ocupados.add(node)
            if acao['prepend'] and not prepend_as:
                raise AcaoBgpNaoSuportada(
                    f'A ação "{acao["chave"]}" precisa do ASN de prepend do circuito, '
                    f'que não foi informado nem identificado na config atual.'
                )
            comandos.append(f'route-policy {nome_policy} {acao["policy"]} node {node}')
            comandos.append(f'if-match community-filter {nome_filtro}')
            if acao['prepend']:
                comandos.append(
                    f'apply as-path {" ".join([prepend_as] * acao["prepend"])} additive'
                )
            if acao.get('apply'):
                comandos.append(f'apply {acao["apply"]}')
            comandos.append('quit')

        if NODE_CATCHALL not in nodes_ocupados:
            comandos.append(f'route-policy {nome_policy} deny node {NODE_CATCHALL}')
            comandos.append('quit')

    if not comandos:
        raise AcaoBgpNaoSuportada(
            f'O circuito c-{circuito_id} já está completo para '
            f'{"/".join(f.upper() for f in familias)} — nada a gerar.'
        )
    return comandos + ['commit']


# ─── Atualização otimista do painel ──────────────────────────────────────────

def aplicar_efeito_local(dados, tipo, alvo, params):
    """
    Reflete no snapshot o efeito de uma ação de community já executada com
    sucesso, pro painel não continuar mostrando o estado anterior até o
    próximo backup — mesma ideia (e mesmas ressalvas) de
    `bgp_actions.aplicar_efeito_localmente`, que é quem chama esta função.

    `alvo` é o prefixo (ações de anúncio) ou o identificador do circuito.
    """
    nodes = dados.setdefault('community_nodes', {})

    if tipo == 'anuncio_community':
        prefixo = alvo or params.get('prefixo', '')
        rp = params.get('route_policy', '')
        cid = str(params.get('circuito', ''))
        chave = params.get('acao', '')
        mapa = montar_mapa(dados)
        linha = next((l for l in mapa['anuncios'] if l['prefixo'] == prefixo
                      and (not rp or l['route_policy'] == rp)), None)
        if not linha or linha['node'] is None:
            return
        circuito = mapa['circuitos'].get(cid)
        if not circuito:
            return
        alvo = next((n for n in nodes.get(linha['route_policy'], [])
                     if n['node'] == linha['node']), None)
        if alvo is None:
            return
        antigas = {community_de(circuito['asn_community'], circuito['grupo'], a['chave'])
                   for a in ACOES}
        alvo['apply_community'] = [v for v in alvo['apply_community'] if v not in antigas]
        if chave:
            alvo['apply_community'].append(
                community_de(circuito['asn_community'], circuito['grupo'], chave)
            )

    elif tipo == 'novo_prefixo_community':
        prefixo = alvo or params.get('prefixo', '')
        destinos = params.get('destinos') or {}
        if not prefixo:
            return
        try:
            rede_alvo = ipaddress.ip_network(prefixo, strict=False)
        except ValueError:
            return
        # Nome vazio = o operador deixou o sistema escolher; recalcula com a
        # mesma regra usada na geração dos comandos.
        rp = params.get('route_policy', '') or _nome_policy_local(rede_alvo)
        mapa = montar_mapa(dados)
        valores = []
        for cid, chave in sorted(destinos.items()):
            circuito = mapa['circuitos'].get(str(cid))
            if circuito and chave:
                valores.append(community_de(circuito['asn_community'], circuito['grupo'], chave))
        nodes[rp] = [{
            'policy': rp, 'node': NODE_LOCAL, 'acao': 'permit',
            'community_filters': [], 'apply_community': valores,
            'apply_community_extra': [], 'prefix_lists': [],
            'prepend_as': [], 'local_preference': None,
        }]
        dados.setdefault('networks', []).append({
            'prefixo': str(rede_alvo), 'habilitada': True, 'origem': 'network',
            'route_policy': rp, 'ip': str(rede_alvo.network_address),
            'mascara': str(rede_alvo.netmask) if rede_alvo.version == 4 else str(rede_alvo.prefixlen),
            'familia': 'v6' if rede_alvo.version == 6 else 'v4',
        })

    elif tipo == 'provisionar_circuito':
        # Os filtros/nodes novos são recarregados no próximo backup; aqui só
        # dá pra registrar os community-filters (o que basta pro painel já
        # oferecer as ações novas do circuito). Os nodes das policies de
        # saída dependem de nome/ordem que a resposta do equipamento não
        # devolve, então não são forjados — ver a nota de "aproximação
        # otimista" em docs/bgp_automacao.md.
        cid = str(params.get('circuito', '') or (alvo or '').removeprefix('c-'))
        grupo = str(params.get('grupo', ''))
        asn = str(params.get('asn_community') or ASN_COMMUNITY_PADRAO)
        if not cid or not grupo:
            return
        filtros = dados.setdefault('community_filters', {})
        for acao in ACOES:
            nome_filtro = f'c-{cid}-{acao["chave"]}'
            filtros.setdefault(nome_filtro, [{
                'index': 10, 'acao': 'permit',
                'valores': [community_de(asn, grupo, acao['chave'])],
            }])
