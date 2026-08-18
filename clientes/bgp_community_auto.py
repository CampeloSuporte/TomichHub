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

O circuito pode ser de três tipos, identificados pelo prefixo do filtro:
`c-NN` (operadora/upstream), `ix-NN` (IX/PTT) e `cdn-NN` (CDN). Além dos
circuitos individuais existem os grupos GLOBAIS — `glob-all-upstream`,
`glob-all-ptts-ixbr`, `glob-all-cdns` —, uma community só que a policy de
saída de cada circuito daquele tipo também casa (node 12), ou seja: "anunciar
para todos os upstreams" sem listar um por um.

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
    mapear_globais(dados, …)  → descobre os grupos "anunciar para todos" e o
                                alcance real de cada um
    mapear_anuncios(dados, …) → matriz prefixo × destino → ação em vigor, mais
                                o efeito REAL de cada prefixo em cada circuito
    montar_mapa(dados)        → tudo acima + validações, numa chamada
    comandos_*(…)             → gera a config Huawei de uma mudança de intenção
"""
import ipaddress
import re

from .bgp_actions import AcaoBgpNaoSuportada

# ─── Catálogo de ações ────────────────────────────────────────────────────────
# `sufixo` são os 2 últimos dígitos da community (65100:<grupo><sufixo>);
# `node` é a posição da ação dentro da route-policy OUT do circuito (a ordem do
# template padrão — bloqueio primeiro, anúncio, global, prepends, no-export —
# ver docs/bgp_automacao.md); `prepend` é propriedade da AÇÃO, nunca cadastrada
# no anúncio (regra explícita da especificação). `grupo_ui` só agrupa as opções
# no seletor do painel.
ACOES = [
    {'chave': 'export',     'sufixo': '01', 'node': 11, 'prepend': 0,
     'rotulo': 'Anunciar (normal)',        'policy': 'permit', 'grupo_ui': 'anuncio'},
    {'chave': 'export-1p',  'sufixo': '02', 'node': 13, 'prepend': 1,
     'rotulo': 'Anunciar com 1 prepend',   'policy': 'permit', 'grupo_ui': 'prepend'},
    {'chave': 'export-2p',  'sufixo': '03', 'node': 14, 'prepend': 2,
     'rotulo': 'Anunciar com 2 prepends',  'policy': 'permit', 'grupo_ui': 'prepend'},
    {'chave': 'export-3p',  'sufixo': '04', 'node': 15, 'prepend': 3,
     'rotulo': 'Anunciar com 3 prepends',  'policy': 'permit', 'grupo_ui': 'prepend'},
    {'chave': 'export-4p',  'sufixo': '05', 'node': 16, 'prepend': 4,
     'rotulo': 'Anunciar com 4 prepends',  'policy': 'permit', 'grupo_ui': 'prepend'},
    # `export-ne` é PERMIT + `apply community no-export`: a rota é anunciada ao
    # peer e é ele que não a repassa adiante. Um `deny` aqui (como aparece em
    # caixas antigas, e como esta automação gerava até 18/08/2026) faz o
    # oposto do rótulo — não anuncia nada, e o `apply` do node nem roda.
    {'chave': 'export-ne',  'sufixo': '08', 'node': 17, 'prepend': 0,
     'rotulo': 'Anunciar com no-export',   'policy': 'permit', 'grupo_ui': 'especial',
     'apply': 'community no-export'},
    # Removida do template otimizado (§20, CORREÇÃO 4). Continua reconhecida
    # nas caixas que já a têm — só não é gerada em config nova.
    {'chave': 'export-df',  'sufixo': '09', 'node': 18, 'prepend': 0,
     'rotulo': 'Anunciar (default/específico)', 'policy': 'permit',
     'grupo_ui': 'especial', 'legado': True},
    {'chave': 'export-bh',  'sufixo': '66', 'node': 10, 'prepend': 0,
     'rotulo': 'Blackhole',                'policy': 'permit', 'grupo_ui': 'especial'},
    {'chave': 'export-bl',  'sufixo': '67', 'node': 9,  'prepend': 0,
     'rotulo': 'Bloquear anúncio',         'policy': 'deny', 'grupo_ui': 'especial'},
    {'chave': 'import-rr',  'sufixo': '00', 'node': None, 'prepend': 0,
     'rotulo': 'Origem (rotas recebidas)', 'policy': None, 'grupo_ui': 'entrada'},
]
ACOES_POR_CHAVE = {a['chave']: a for a in ACOES}
ACOES_POR_SUFIXO = {a['sufixo']: a for a in ACOES}
# Ações que o operador escolhe num anúncio (import-rr é carimbada pela policy
# IN da sessão, não pelo prefixo local — ver §12 da especificação).
ACOES_DE_ANUNCIO = [a for a in ACOES if a['chave'] != 'import-rr']
# Ações que entram em config NOVA: as legadas (export-df) continuam sendo
# reconhecidas e manipuláveis onde já existem, mas não são provisionadas.
ACOES_PROVISIONAVEIS = [a for a in ACOES if not a.get('legado')]
# Ações que um circuito no padrão atual precisa ter (é o que "falta: …" mede).
ACOES_ANUNCIO_PADRAO = [a for a in ACOES_DE_ANUNCIO if not a.get('legado')]

# ─── Tipos de circuito ────────────────────────────────────────────────────────
# O prefixo do community-filter identifica o TIPO do circuito, e cada tipo tem
# a sua community "anunciar para todos" (§4/§15 da especificação). As caixas em
# produção usam as três famílias: `c-NN` (operadora/upstream), `ix-NN` e
# `cdn-NN`. Nas caixas mais antigas tudo é `c-NN` — inclusive IX (c-81..c-83) —
# e aí o tipo real vem do glob-* que a policy de saída referencia, não do nome.
TIPOS_CIRCUITO = [
    {'chave': 'upstream', 'prefixo': 'c',   'rotulo': 'Operadora / upstream',
     'plural': 'Operadoras / upstreams', 'glob_slug': 'all-upstream'},
    {'chave': 'ix',       'prefixo': 'ix',  'rotulo': 'IX / PTT',
     'plural': 'IX / PTT',               'glob_slug': 'all-ptts-ixbr'},
    {'chave': 'cdn',      'prefixo': 'cdn', 'rotulo': 'CDN',
     'plural': 'CDNs',                   'glob_slug': 'all-cdns'},
]
TIPOS_POR_PREFIXO = {t['prefixo']: t for t in TIPOS_CIRCUITO}
TIPOS_POR_CHAVE = {t['chave']: t for t in TIPOS_CIRCUITO}

# Rótulos dos grupos globais conhecidos (§5). Slug desconhecido cai no nome
# cru do filtro — a descoberta não depende desta tabela.
ROTULOS_GLOBAIS = {
    'all-upstream': 'Todos os upstreams',
    'all-ptts-ixbr': 'Todos os IX (IX.br)',
    'all-ptts-priv': 'Todos os PTT privados',
    'all-cdns': 'Todas as CDNs',
    'all-pni': 'Todos os PNI',
    'all-peerings': 'Todos os peerings',
}

NODE_CATCHALL = 999          # `deny node 999` — bloqueio final da policy OUT
NODE_GLOBAL = 12             # node do `if-match community-filter glob-all-*`
NODE_LOCAL = 10              # node único das route-policies locais (RT-BGP-LOCAL-*)
ASN_COMMUNITY_PADRAO = '65100'
PREFIXO_POLICY_LOCAL = 'RT-BGP-LOCAL-'
PREFIXO_DESTINO_GLOBAL = 'glob-'

_PREFIXOS_CIRCUITO = '|'.join(
    re.escape(t['prefixo']) for t in sorted(TIPOS_CIRCUITO, key=lambda t: -len(t['prefixo']))
)
_CHAVES_ACAO = '|'.join(re.escape(a['chave']) for a in ACOES)
# `c-01-export-2p` / `ix-05-export` / `cdn-03-import-rr`
_RE_FILTRO = re.compile(rf'^({_PREFIXOS_CIRCUITO})-(\d+)-({_CHAVES_ACAO})$')
# `glob-all-upstream` / `glob-all-ptts-ixbr-2p`
_RE_FILTRO_GLOBAL = re.compile(r'^glob-(.+?)(?:-([1-4])p)?$')
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
    circuitos que a caixa já tem configurados, cada um com seu tipo, grupo de
    community, ações disponíveis, policies, wiring da policy de saída e sessões
    BGP vinculadas.

    O `id` do circuito é o próprio prefixo do community-filter (`c-01`,
    `ix-05`, `cdn-03`) — é ele que amarra filtro, ação e community, e é a
    chave usada em todo o resto do módulo.

    O vínculo circuito ↔ sessão é feito pela EXPORT POLICY da sessão (a policy
    que dá `if-match community-filter <circuito>-*` é, por definição, a policy
    de saída daquele circuito) — não pelo nome da policy nem pela community de
    origem. Foi a única regra que se sustentou nos equipamentos reais: nomes
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
        prefixo_nome, numero, chave_acao = m.group(1), m.group(2), m.group(3)
        cid = f'{prefixo_nome}-{numero}'
        valores = [v for e in entradas if e['acao'] == 'permit' for v in e['valores']]
        if not valores:
            continue
        c = circuitos.setdefault(cid, {
            'id': cid, 'numero': numero,
            'tipo': TIPOS_POR_PREFIXO[prefixo_nome]['chave'], 'tipo_origem': 'nome',
            'nome': '', 'peer_as': '', 'prepend_as': '',
            'grupo': '', 'asn_community': '', 'acoes': {}, 'policies': {},
            'policies_in_todas': [], 'policies_out_todas': {'v4': [], 'v6': []},
            'sessoes': [], 'ipv4': False, 'ipv6': False, 'completo': False,
            'globais': [], 'nodes_out': {'v4': [], 'v6': []},
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
            avisos.append(f'{cid}: nenhuma community reconhecível no padrão <asn>:<grupo><ação>.')
            continue
        base = c['acoes'].get('export') or c['acoes'].get('export-1p')
        if base and base.get('grupo_declarado'):
            c['asn_community'], c['grupo'] = base['asn_declarado'], base['grupo_declarado']
        else:
            c['asn_community'], c['grupo'] = max(votos, key=lambda k: len(votos[k]))
        for (asn, grupo), chaves in votos.items():
            if (asn, grupo) != (c['asn_community'], c['grupo']):
                avisos.append(
                    f'{cid}: {", ".join(sorted(chaves))} usa(m) community do grupo '
                    f'{asn}:{grupo}xx, mas o circuito é do grupo {c["asn_community"]}:{c["grupo"]}xx '
                    f'— provável cópia do bloco de outro circuito.'
                )
        for chave, a in c['acoes'].items():
            esperada = community_de(c['asn_community'], c['grupo'], chave)
            a['esperada'] = esperada
            a['conforme'] = (a['community'] == esperada)
            if a['acao_declarada'] and a['acao_declarada'] != chave:
                avisos.append(
                    f'{cid}: o filtro "{a["filtro"]}" aponta pra {a["community"]}, '
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
                    cid = f'{m.group(1)}-{m.group(2)}'
                    refs[cid] = refs.get(cid, 0) + 1
        if not refs:
            continue
        cid = max(refs, key=lambda k: refs[k])
        policy_do_circuito[nome_policy] = cid
        if len(refs) > 1:
            outros = ', '.join(k for k in sorted(refs) if k != cid)
            avisos.append(
                f'A route-policy "{nome_policy}" (circuito {cid}) também referencia '
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
                f'{c["id"]}: mais de uma route-policy de saída {familia.upper()} '
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

    # ── 5. Nome, ASNs, wiring da policy de saída e tipo ──────────────────────
    for c in circuitos.values():
        for chave in ('v4_out', 'v6_out', 'v4_out_orfa', 'v6_out_orfa', 'v4_in', 'v6_in'):
            m = _RE_NOME_POLICY.match(c['policies'].get(chave, ''))
            if m:
                if not c['nome']:
                    c['nome'] = m.group(2)
                if not c['peer_as']:
                    c['peer_as'] = m.group(1)

        # Wiring da policy de saída: qual node casa qual filtro. É isso que
        # decide o efeito REAL quando um prefixo carrega mais de uma community
        # deste circuito (ex: a individual `<circuito>-export-2p` no node 14 e
        # a global `glob-all-upstream` no node 12) — no VRP vale o PRIMEIRO
        # node que casa, então a global ganha do prepend individual.
        globais_ref = []
        for familia in ('v4', 'v6'):
            nome_policy = (c['policies'].get(f'{familia}_out')
                           or c['policies'].get(f'{familia}_out_orfa') or '')
            if not nome_policy:
                continue
            wiring = []
            for n in nodes.get(nome_policy, []):
                for f in n['community_filters']:
                    if not (_RE_FILTRO.match(f) or f.startswith(PREFIXO_DESTINO_GLOBAL)):
                        continue
                    wiring.append({
                        'policy': nome_policy, 'node': n['node'], 'filtro': f,
                        'acao': n['acao'], 'prepend': len(n['prepend_as']),
                        'prepend_as': list(n['prepend_as']),
                        'no_export': 'no-export' in n['apply_community_extra'],
                    })
                    if f.startswith(PREFIXO_DESTINO_GLOBAL):
                        gid = _grupo_global_do_filtro(f)
                        if gid and gid not in globais_ref:
                            globais_ref.append(gid)
            c['nodes_out'][familia] = sorted(wiring, key=lambda w: w['node'])
        c['globais'] = sorted(globais_ref)

        # Tipo do circuito: o nome do filtro é a fonte primária (`ix-05` é IX),
        # mas nas caixas antigas TUDO é `c-NN` — inclusive os IX (c-81..c-83).
        # Nesse caso o `glob-all-*` que a policy de saída referencia diz o tipo
        # real, e é uma informação da própria config, não um palpite.
        if c['tipo_origem'] == 'nome':
            slugs = {g[len(PREFIXO_DESTINO_GLOBAL):] for g in c['globais']}
            tipos_glob = {t['chave'] for t in TIPOS_CIRCUITO if t['glob_slug'] in slugs}
            if len(tipos_glob) == 1:
                tipo_glob = tipos_glob.pop()
                if tipo_glob != c['tipo']:
                    c['tipo'], c['tipo_origem'] = tipo_glob, 'glob'

        # ASN de prepend: o mais frequente entre os nodes de saída do
        # circuito. Caixa real (acesso 1216) tem policies do mesmo circuito
        # prependando ASNs diferentes — a maioria é o palpite menos ruim, e a
        # divergência vira aviso pro operador conferir.
        preps = {}
        for familia in ('v4', 'v6'):
            for w in c['nodes_out'][familia]:
                for asn in w['prepend_as']:
                    preps[asn] = preps.get(asn, 0) + 1
        c['prepend_as'] = max(preps, key=lambda k: preps[k]) if preps else ''
        if len(preps) > 1:
            avisos.append(
                f'{c["id"]}: prepend usa mais de um ASN nas policies de saída '
                f'({", ".join(sorted(preps))}) — o sistema assume {c["prepend_as"]}.'
            )
        # `export-df` saiu do padrão (§20) — não conta como "faltando".
        c['faltando'] = sorted(
            a['chave'] for a in ACOES_ANUNCIO_PADRAO if a['chave'] not in c['acoes']
        )
        c['completo'] = not c['faltando'] and bool(c['sessoes'])
        c['tipo_rotulo'] = TIPOS_POR_CHAVE[c['tipo']]['rotulo']
        c['rotulo'] = c['id'] + (f' — {c["nome"]}' if c['nome'] else '')

    # ── 6. Validação da policy IN (community de origem) ──────────────────────
    for c in circuitos.values():
        rr = c['acoes'].get('import-rr')
        if not rr:
            continue
        for nome_policy in c['policies_in_todas']:
            aplicadas = {v for n in nodes.get(nome_policy, []) for v in n['apply_community']}
            if rr['community'] not in aplicadas:
                avisos.append(
                    f'{c["id"]}: a policy de entrada "{nome_policy}" não carimba '
                    f'{rr["community"]} ({rr["filtro"]}) — rotas recebidas por este '
                    f'circuito ficam sem a community de origem.'
                )

    return {'circuitos': circuitos, 'avisos': avisos}


# ─── Descoberta: grupos globais ("anunciar para todos") ───────────────────────

def _grupo_global_do_filtro(nome_filtro):
    """`glob-all-upstream-2p` → `glob-all-upstream` (o grupo), ou None se o
    nome não for de um filtro global."""
    if not nome_filtro.startswith(PREFIXO_DESTINO_GLOBAL):
        return None
    m = _RE_FILTRO_GLOBAL.match(nome_filtro)
    return f'{PREFIXO_DESTINO_GLOBAL}{m.group(1)}' if m else None


def _acao_global_do_filtro(nome_filtro):
    """`glob-all-upstream-2p` → ('glob-all-upstream', 'export-2p')."""
    m = _RE_FILTRO_GLOBAL.match(nome_filtro or '')
    if not m or not nome_filtro.startswith(PREFIXO_DESTINO_GLOBAL):
        return None, None
    prep = m.group(2)
    return f'{PREFIXO_DESTINO_GLOBAL}{m.group(1)}', ('export' if not prep else f'export-{prep}p')


def mapear_globais(dados, circuitos):
    """
    Descobre os grupos "anunciar para todos" (§4/§15 da especificação): os
    community-filters `glob-<slug>[-Np]`, cada um com as variantes de prepend
    que existem e — o que importa de verdade — o ALCANCE real de cada variante:
    quais circuitos têm um node de saída referenciando aquele filtro.

    O alcance é a diferença entre uma community global que funciona e uma que é
    config morta: nas caixas antigas os `glob-*` existem mas nenhuma policy os
    referencia (alcance zero — marcar um prefixo com essa community não muda
    nada), enquanto nas caixas no padrão novo cada policy de saída traz
    `if-match community-filter glob-all-<tipo>` no node 12.

    Devolve `{'globais': {...}, 'avisos': [...]}`.
    """
    filtros = dados.get('community_filters') or {}
    globais, avisos = {}, []

    for nome_filtro, entradas in sorted(filtros.items()):
        gid, chave = _acao_global_do_filtro(nome_filtro)
        if not gid:
            continue
        valores = [v for e in entradas if e['acao'] == 'permit' for v in e['valores']]
        if not valores:
            continue
        slug = gid[len(PREFIXO_DESTINO_GLOBAL):]
        g = globais.setdefault(gid, {
            'id': gid, 'slug': slug, 'tipo': 'global',
            'tipo_rotulo': 'Grupo global',
            'rotulo': ROTULOS_GLOBAIS.get(slug, gid),
            'acoes': {}, 'circuitos': [], 'sessoes': [],
            'ipv4': False, 'ipv6': False, 'asn_community': '',
        })
        g['acoes'][chave] = {
            'filtro': nome_filtro, 'community': valores[0], 'multiplos': valores[1:],
            'circuitos': [], 'prepends': [], 'deny': False, 'no_export': False,
        }
        m_com = _RE_COMMUNITY.match(valores[0])
        if m_com and not g['asn_community']:
            g['asn_community'] = m_com.group(1)

    # Alcance: quem realmente casa cada filtro global.
    for cid in sorted(circuitos):
        c = circuitos[cid]
        for familia in ('v4', 'v6'):
            for w in c['nodes_out'][familia]:
                gid, chave = _acao_global_do_filtro(w['filtro'])
                if not gid:
                    continue
                g = globais.get(gid)
                acao = (g or {}).get('acoes', {}).get(chave)
                if acao is None:
                    # filtro referenciado que não existe no equipamento —
                    # `validar_mapa` já reporta isso separadamente.
                    continue
                if cid not in acao['circuitos']:
                    acao['circuitos'].append(cid)
                if cid not in g['circuitos']:
                    g['circuitos'].append(cid)
                    g['sessoes'].extend(c['sessoes'])
                if w['prepend'] not in acao['prepends']:
                    acao['prepends'].append(w['prepend'])
                acao['deny'] = acao['deny'] or w['acao'] != 'permit'
                acao['no_export'] = acao['no_export'] or w['no_export']
                g['ipv4'] = g['ipv4'] or c['ipv4']
                g['ipv6'] = g['ipv6'] or c['ipv6']

    for gid, g in globais.items():
        g['alcance'] = len(g['circuitos'])
        for chave, acao in g['acoes'].items():
            acao['alcance'] = len(acao['circuitos'])
            esperado = ACOES_POR_CHAVE[chave]['prepend']
            divergentes = [p for p in acao['prepends'] if p != esperado]
            if divergentes:
                avisos.append(
                    f'{gid}: o filtro "{acao["filtro"]}" é casado com '
                    f'{"/".join(str(p) for p in sorted(divergentes))} prepend(s) em '
                    f'{", ".join(acao["circuitos"])}, mas a ação define {esperado}.'
                )
            if acao['deny']:
                avisos.append(
                    f'{gid}: "{acao["filtro"]}" cai num node `deny` em pelo menos um '
                    f'circuito ({", ".join(acao["circuitos"])}) — nesse circuito a '
                    f'community global BLOQUEIA o anúncio em vez de liberar.'
                )
        # Tipo do circuito que este grupo representa, quando conhecido (§4).
        tipo = next((t for t in TIPOS_CIRCUITO if t['glob_slug'] == g['slug']), None)
        g['tipo_circuito'] = tipo['chave'] if tipo else ''

    return {'globais': globais, 'avisos': avisos}


# ─── Descoberta: o que cada prefixo local anuncia hoje ────────────────────────

def _efeito_no_circuito(circuito, familia, filtros_marcados):
    """
    Qual node da policy de saída de `circuito` realmente decide o destino de um
    prefixo que carrega `filtros_marcados` (nomes de community-filter).

    No VRP vale o PRIMEIRO node que casa — então um prefixo marcado ao mesmo
    tempo com a global (node 12) e com um prepend individual (node 14) é
    anunciado SEM prepend, e o node individual nunca roda. Os perdedores ficam
    em `ignorados` pro painel poder avisar em vez de mostrar uma intenção que o
    equipamento não cumpre.
    """
    candidatos = [w for w in circuito['nodes_out'].get(familia, [])
                  if w['filtro'] in filtros_marcados]
    if not candidatos:
        return None
    vencedor = candidatos[0]
    return {
        'filtro': vencedor['filtro'], 'node': vencedor['node'],
        'policy': vencedor['policy'],
        'anuncia': vencedor['acao'] == 'permit',
        'prepend': vencedor['prepend'],
        'no_export': vencedor['no_export'],
        'ignorados': [w['filtro'] for w in candidatos[1:]],
    }


def mapear_anuncios(dados, circuitos, globais=None):
    """
    Monta a matriz prefixo × destino → ação em vigor, a partir das communities
    aplicadas na route-policy local de cada `network`.

    Cada linha: `{prefixo, familia, route_policy, node, destinos: {cid: acao},
    globais: {gid: acao}, efetivo: {cid: {...}}, communities_extras: [...],
    avisos: [...]}`.

    - `destinos` são as intenções por circuito individual;
    - `globais` são as intenções "anunciar para todos" (§15);
    - `efetivo` é o que a policy de saída de cada circuito faz de fato com esse
      prefixo hoje, individual e global resolvidos juntos por ordem de node;
    - `communities_extras` são as communities que não pertencem a nenhum
      destino deste catálogo (ex: 65100:10091 — convenções próprias do
      cliente): são preservadas intactas em qualquer reescrita.
    """
    nodes = dados.get('community_nodes') or {}
    networks = dados.get('networks') or []
    globais = globais or {}

    por_community = {}
    for cid, c in circuitos.items():
        for chave, a in c['acoes'].items():
            por_community.setdefault(a['community'], (cid, chave))
    por_community_global = {}
    for gid, g in globais.items():
        for chave, a in g['acoes'].items():
            por_community_global.setdefault(a['community'], (gid, chave))

    linhas, vistos = [], set()
    for net in networks:
        prefixo = net.get('prefixo', '')
        rp = net.get('route_policy', '')
        chave_unica = (prefixo, rp)
        if not prefixo or chave_unica in vistos:
            continue
        vistos.add(chave_unica)

        destinos, destinos_globais, extras, avisos_linha = {}, {}, [], []
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
                            f'{prefixo}: duas ações conflitantes para o circuito {cid} '
                            f'({destinos[cid]} e {chave}).'
                        )
                    destinos[cid] = chave
                    continue
                achado_global = por_community_global.get(valor)
                if achado_global:
                    gid, chave = achado_global
                    destinos_globais[gid] = chave
                    continue
                extras.append(valor)
            extras.extend(node_local['apply_community_extra'])
        elif rp and lista:
            avisos_linha.append(f'A policy local "{rp}" existe mas não aplica nenhuma community.')
        elif rp:
            avisos_linha.append(f'A route-policy "{rp}" referenciada pelo network não foi encontrada.')

        familia = net.get('familia') or _familia_do_prefixo(prefixo)

        # Filtros que este prefixo "acende" — individuais e globais juntos.
        marcados = set()
        for cid, chave in destinos.items():
            acao = circuitos.get(cid, {}).get('acoes', {}).get(chave)
            if acao:
                marcados.add(acao['filtro'])
        for gid, chave in destinos_globais.items():
            acao = globais.get(gid, {}).get('acoes', {}).get(chave)
            if acao:
                marcados.add(acao['filtro'])
        efetivo = {}
        if marcados:
            for cid, c in circuitos.items():
                efeito = _efeito_no_circuito(c, familia, marcados)
                if efeito:
                    efetivo[cid] = efeito

        linhas.append({
            'prefixo': prefixo,
            'familia': familia,
            'ip': net.get('ip', ''), 'mascara': net.get('mascara', ''),
            'route_policy': rp,
            'node': node_local['node'] if node_local else None,
            'editavel': bool(rp) and node_local is not None,
            'destinos': destinos,
            'globais': destinos_globais,
            'efetivo': efetivo,
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
    """
    Descoberta completa, na ordem em que uma coisa depende da outra:
    circuitos → grupos globais (precisam do wiring dos circuitos pra saber o
    alcance) → matriz de anúncios (precisa dos dois pra resolver o efeito) →
    validações.

    Também devolve o catálogo (`acoes`, `tipos`) pro frontend montar os
    seletores sem duplicar as regras.
    """
    _vendor_ok(vendor)
    mapa = mapear_circuitos(dados)
    mapa_globais = mapear_globais(dados, mapa['circuitos'])
    mapa['globais'] = mapa_globais['globais']
    mapa['avisos'].extend(mapa_globais['avisos'])
    mapa['anuncios'] = mapear_anuncios(dados, mapa['circuitos'], mapa['globais'])
    mapa['acoes'] = [
        {'chave': a['chave'], 'rotulo': a['rotulo'], 'prepend': a['prepend'],
         'grupo_ui': a['grupo_ui'], 'legado': bool(a.get('legado'))}
        for a in ACOES_DE_ANUNCIO
    ]
    mapa['tipos'] = [
        {'chave': t['chave'], 'rotulo': t['rotulo'], 'plural': t['plural'],
         'prefixo': t['prefixo'], 'glob': f'{PREFIXO_DESTINO_GLOBAL}{t["glob_slug"]}'}
        for t in TIPOS_CIRCUITO
    ]
    mapa['avisos'].extend(validar_mapa(dados, mapa))
    return mapa


def destino_de(mapa, destino_id):
    """Resolve um identificador de destino — circuito (`c-01`, `ix-05`) ou grupo
    global (`glob-all-upstream`) — na sua estrutura do mapa. Levanta
    `AcaoBgpNaoSuportada` se não existir neste equipamento: as duas famílias
    compartilham o mesmo espaço de nomes na UI e nas ações gravadas."""
    if not destino_id:
        raise AcaoBgpNaoSuportada('Informe o destino do anúncio.')
    if destino_id.startswith(PREFIXO_DESTINO_GLOBAL):
        g = (mapa.get('globais') or {}).get(destino_id)
        if not g:
            raise AcaoBgpNaoSuportada(
                f'O grupo global "{destino_id}" não existe neste equipamento.'
            )
        return g
    c = mapa['circuitos'].get(destino_id)
    if not c:
        raise AcaoBgpNaoSuportada(f'Circuito "{destino_id}" não existe neste equipamento.')
    return c


def _e_global(destino):
    return destino.get('tipo') == 'global'


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
                # `import-rr` mora na policy de entrada; as legadas (export-df)
                # saíram do padrão e não vale poluir o painel com elas.
                if chave_acao == 'import-rr' or ACOES_POR_CHAVE[chave_acao].get('legado'):
                    continue
                n = por_filtro.get(a['filtro'])
                if n is None:
                    avisos.append(
                        f'{cid}: a ação "{chave_acao}" tem community-filter mas nenhum node '
                        f'em "{nome_policy}" — usar essa ação neste circuito não produz efeito.'
                    )
                    continue
                esperado = ACOES_POR_CHAVE[chave_acao]['prepend']
                if len(n['prepend_as']) != esperado:
                    avisos.append(
                        f'{cid}: "{nome_policy}" node {n["node"]} ({chave_acao}) aplica '
                        f'{len(n["prepend_as"])} prepend(s), mas a ação define {esperado}.'
                    )
                # "Anunciar com no-export" num node `deny` faz o OPOSTO do
                # rótulo: a rota não é anunciada e o `apply community
                # no-export` do node nem chega a rodar. Aparece em caixas
                # antigas — o template atual usa `permit` (§9).
                esperada_policy = ACOES_POR_CHAVE[chave_acao].get('policy')
                if esperada_policy and n['acao'] != esperada_policy:
                    avisos.append(
                        f'{cid}: "{nome_policy}" node {n["node"]} ({chave_acao}) é '
                        f'`{n["acao"]}`, mas a ação exige `{esperada_policy}` — '
                        f'o efeito no equipamento é o contrário do rótulo.'
                    )
        if c['sessoes'] and not c['prepend_as']:
            avisos.append(
                f'{cid}: nenhum ASN de prepend identificado nas policies de saída — '
                f'as ações de prepend não podem ser geradas para este circuito.'
            )
        for chave_policy in ('v4_in', 'v6_in', 'v4_out', 'v6_out'):
            nome_policy = c['policies'].get(chave_policy)
            if nome_policy and nome_policy not in policies_todas:
                avisos.append(
                    f'{cid}: a sessão referencia a route-policy "{nome_policy}", '
                    f'que não existe no equipamento.'
                )
        # Circuito com sessão mas sem o node da community global do seu tipo:
        # "anunciar para todos os <tipo>" não chega neste circuito (§15). Só
        # vale como achado se a caixa REALMENTE usa esse grupo global em algum
        # outro circuito — quando o bloco `glob-*` inteiro é config morta, o
        # aviso consolidado no fim já diz isso uma vez só.
        tipo = TIPOS_POR_CHAVE.get(c['tipo'])
        glob_esperado = f'{PREFIXO_DESTINO_GLOBAL}{tipo["glob_slug"]}' if tipo else ''
        if (c['sessoes'] and glob_esperado
                and (mapa.get('globais') or {}).get(glob_esperado, {}).get('alcance')
                and glob_esperado not in c['globais']):
            avisos.append(
                f'{cid}: a policy de saída não referencia "{glob_esperado}" — '
                f'anunciar para todos os {tipo["plural"]} não alcança este circuito.'
            )

    # Grupos globais que são config morta. Vem em UMA linha só: nas caixas
    # antigas o bloco `glob-*` inteiro (6-7 grupos) foi colado sem nunca ser
    # referenciado, e sete avisos idênticos afogariam os achados que importam.
    mortos = sorted(gid for gid, g in (mapa.get('globais') or {}).items() if not g['alcance'])
    if mortos:
        avisos.append(
            f'{"Os grupos globais" if len(mortos) > 1 else "O grupo global"} '
            f'{", ".join(mortos)} {"têm" if len(mortos) > 1 else "tem"} community-filter '
            f'mas nenhuma policy de saída {"os" if len(mortos) > 1 else "o"} referencia — '
            f'marcar um prefixo com {"essas communities" if len(mortos) > 1 else "essa community"} '
            f'não produz anúncio nenhum.'
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


def comandos_definir_anuncio(dados, mapa, prefixo, destino_id, chave_acao, route_policy=''):
    """
    Muda a intenção de anúncio de UM prefixo para UM destino — um circuito
    individual (`c-01`, `ix-05`, `cdn-02`) ou um grupo global (`glob-all-
    upstream`, que atinge de uma vez todos os circuitos cuja policy de saída
    referencia esse filtro — §15).

    `chave_acao` vazia remove o prefixo daquele destino (deixa de ser
    anunciado por lá — o `deny node 999` da policy OUT faz o resto).

    A mudança é sempre uma reescrita completa da linha `apply community` da
    route-policy LOCAL do prefixo: as communities dos OUTROS destinos e as
    que não pertencem a este catálogo (`communities_extras`) são reemitidas
    exatamente como estavam. O par `undo apply community` + `apply community …`
    é necessário porque no VRP um `apply community` repetido SOMA à lista já
    configurada no node — sem o `undo` não haveria como remover nada. Como o
    Huawei desta automação usa config candidata + `commit` (ver
    `bgp_actions._PRECISA_COMMIT`), as duas linhas viram uma alteração atômica:
    o prefixo nunca fica um instante sem communities na config em vigor.
    """
    linha = _achar_linha(mapa, prefixo, route_policy)
    destino = destino_de(mapa, destino_id)
    global_ = _e_global(destino)

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
    if chave_acao and chave_acao not in destino['acoes']:
        faltando = ('O grupo global' if global_ else 'O circuito')
        raise AcaoBgpNaoSuportada(
            f'{faltando} {destino_id} não tem community-filter para a ação "{chave_acao}" — '
            f'gere a config faltante antes de usar esta ação.'
        )

    if global_:
        alcance = destino['acoes'][chave_acao]['circuitos'] if chave_acao else destino['circuitos']
        if chave_acao and not alcance:
            raise AcaoBgpNaoSuportada(
                f'Nenhuma policy de saída deste equipamento referencia '
                f'"{destino["acoes"][chave_acao]["filtro"]}" — marcar o prefixo com essa '
                f'community global não produziria anúncio nenhum.'
            )
        if chave_acao and not any(
            any(s['familia'] == linha['familia'] for s in mapa['circuitos'][cid]['sessoes'])
            for cid in alcance if cid in mapa['circuitos']
        ):
            raise AcaoBgpNaoSuportada(
                f'Nenhum circuito alcançado por {destino_id} tem sessão '
                f'{linha["familia"].upper()} — {prefixo} é {linha["familia"].upper()}.'
            )
    else:
        if not destino['sessoes']:
            raise AcaoBgpNaoSuportada(
                f'O circuito {destino_id} não tem nenhuma sessão BGP vinculada neste '
                f'equipamento — não há para onde anunciar.'
            )
        if not any(s['familia'] == linha['familia'] for s in destino['sessoes']):
            raise AcaoBgpNaoSuportada(
                f'O circuito {destino_id} não tem sessão {linha["familia"].upper()} — '
                f'{prefixo} é {linha["familia"].upper()}.'
            )

    atual = (linha['globais'] if global_ else linha['destinos']).get(destino_id)
    if atual == chave_acao:
        raise AcaoBgpNaoSuportada(
            f'{prefixo} já está com a ação "{chave_acao}" para {destino_id}.'
        )

    destinos = dict(linha['destinos'])
    globais_sel = dict(linha['globais'])
    alvo = globais_sel if global_ else destinos
    if chave_acao:
        alvo[destino_id] = chave_acao
    else:
        alvo.pop(destino_id, None)

    return _comandos_reescrever_intencao(mapa, linha, destinos, globais_sel)


def _communities_da_intencao(mapa, destinos, globais_sel):
    """Lista de communities que representa a intenção completa de um prefixo:
    uma por circuito individual escolhido, mais uma por grupo global. Ordem
    determinística (circuitos primeiro, na ordem do id) pro comando gerado ser
    estável entre previews."""
    valores = []
    for cid, chave in sorted(destinos.items()):
        circuito = mapa['circuitos'][cid]
        valores.append(community_de(circuito['asn_community'], circuito['grupo'], chave))
    for gid, chave in sorted(globais_sel.items()):
        valores.append(mapa['globais'][gid]['acoes'][chave]['community'])
    return valores


def _comandos_reescrever_intencao(mapa, linha, destinos, globais_sel):
    """Reescreve a linha `apply community` do node local do prefixo com a
    intenção nova, preservando as communities que não são deste catálogo."""
    valores = _communities_da_intencao(mapa, destinos, globais_sel)
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

    `destinos` é `{destino_id: chave_acao}`, onde `destino_id` é um circuito
    (`c-01`, `ix-05`) ou um grupo global (`glob-all-upstream`).
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

    escolhidos, escolhidos_globais = {}, {}
    for destino_id, chave in sorted((destinos or {}).items()):
        destino = destino_de(mapa, destino_id)
        if chave not in destino['acoes']:
            raise AcaoBgpNaoSuportada(
                f'{destino_id} não tem community-filter para a ação "{chave}".'
            )
        if _e_global(destino):
            alcance = [cid for cid in destino['acoes'][chave]['circuitos']
                       if cid in mapa['circuitos']]
            if not alcance:
                raise AcaoBgpNaoSuportada(
                    f'Nenhuma policy de saída referencia '
                    f'"{destino["acoes"][chave]["filtro"]}" — essa community global não '
                    f'produziria anúncio nenhum.'
                )
            if not any(any(s['familia'] == familia for s in mapa['circuitos'][cid]['sessoes'])
                       for cid in alcance):
                raise AcaoBgpNaoSuportada(
                    f'Nenhum circuito alcançado por {destino_id} tem sessão '
                    f'{familia.upper()} — {prefixo} é {familia.upper()}.'
                )
            escolhidos_globais[destino_id] = chave
            continue
        if not any(s['familia'] == familia for s in destino['sessoes']):
            raise AcaoBgpNaoSuportada(
                f'O circuito {destino_id} não tem sessão {familia.upper()} — '
                f'{prefixo} é {familia.upper()}.'
            )
        escolhidos[destino_id] = chave
    valores = _communities_da_intencao(mapa, escolhidos, escolhidos_globais)

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
    peering CDN (`AS40027-NETFLIX-OCA-V4-OUT`) já usam nodes da faixa do
    catálogo pra outra coisa.

    A community GLOBAL é a exceção que importa de verdade: como ela precisa
    ganhar dos prepends individuais (é isso que "anunciar para todos" quer
    dizer), o node canônico dela (12) fica ANTES dos nodes de prepend
    (13-16) — quem estiver montando uma policy fora do padrão deve conferir
    essa ordem no preview antes de confirmar.
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


_RE_ID_CIRCUITO = re.compile(rf'^({_PREFIXOS_CIRCUITO})-(\d{{2,3}})$')


def comandos_provisionar_circuito(dados, mapa, circuito_id, opcoes=None):
    """
    Gera a config padrão que falta para um circuito: os `ip community-filter`
    das ações ausentes, os nodes correspondentes nas route-policies de saída
    (IPv4 e/ou IPv6), o node da community global do tipo do circuito e o
    `deny node 999` final quando não existir.

    Serve tanto pra COMPLETAR um circuito já existente (o caso comum: caixa
    com os filtros mas faltando um node, ou vice-versa) quanto pra CRIAR um
    circuito novo do zero (`opcoes` traz grupo/nome/asn/prepend/famílias).
    Só emite o que falta — nada existente é sobrescrito.

    A ordem dos nodes segue o template padrão (§9/§18): bloqueio (9), blackhole
    (10), anúncio (11), global (12), prepends (13-16), no-export (17). Quando o
    node canônico já está ocupado por outra coisa, `_node_livre` acha o
    próximo — a ordem entre as ações não é crítica, só bloqueio-antes-dos-
    permits e o `deny node 999` por último.
    """
    opcoes = opcoes or {}
    circuito = mapa['circuitos'].get(circuito_id)
    nodes = dados.get('community_nodes') or {}
    filtros = dados.get('community_filters') or {}
    globais = mapa.get('globais') or {}

    m_id = _RE_ID_CIRCUITO.match(str(circuito_id or ''))
    if not m_id:
        raise AcaoBgpNaoSuportada(
            'Identificador de circuito inválido — esperado '
            f'{", ".join(t["prefixo"] + "-NN" for t in TIPOS_CIRCUITO)}.'
        )
    tipo = TIPOS_POR_CHAVE[circuito['tipo']] if circuito else TIPOS_POR_PREFIXO[m_id.group(1)]

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

    familias = []
    if opcoes.get('ipv4', circuito['ipv4'] if circuito else False):
        familias.append('v4')
    if opcoes.get('ipv6', circuito['ipv6'] if circuito else False):
        familias.append('v6')
    if not familias:
        raise AcaoBgpNaoSuportada('Escolha pelo menos uma família (IPv4 ou IPv6).')

    comandos = []

    # 1) community-filters faltantes (as ações legadas ficam de fora — §20)
    for acao in ACOES_PROVISIONAVEIS:
        nome_filtro = f'{circuito_id}-{acao["chave"]}'
        if nome_filtro in filtros:
            continue
        comandos.append(
            f'ip community-filter basic {nome_filtro} index 10 permit '
            f'{community_de(asn_community, grupo, acao["chave"])}'
        )

    # 2) nodes faltantes nas policies de saída
    # O node da community global entra junto com as ações individuais: é ele
    # que faz "anunciar para todos os <tipo>" alcançar este circuito (§15).
    # Só é emitido quando o filtro global já existe na caixa — criar o bloco
    # `glob-*` inteiro é decisão de padronização do equipamento, não efeito
    # colateral de completar um circuito.
    glob_id = f'{PREFIXO_DESTINO_GLOBAL}{tipo["glob_slug"]}'
    filtro_global = glob_id if glob_id in filtros else ''
    if filtro_global and glob_id in globais and 'export' not in globais[glob_id]['acoes']:
        filtro_global = ''

    for familia in familias:
        # A escolha explícita do operador (campo do modal) tem prioridade: um
        # mesmo circuito pode ter mais de uma policy de saída na mesma família
        # (duas sessões pro mesmo upstream), e aí só ele sabe qual completar.
        nome_policy = (opcoes.get(f'policy_{familia}_out') or '').strip()
        if not nome_policy:
            candidatas = (circuito or {}).get('policies_out_todas', {}).get(familia, [])
            if len(candidatas) > 1:
                raise AcaoBgpNaoSuportada(
                    f'O circuito {circuito_id} tem mais de uma route-policy de saída '
                    f'{familia.upper()} ({", ".join(candidatas)}) — escolha em qual aplicar.'
                )
            nome_policy = policies.get(f'{familia}_out') or policies.get(f'{familia}_out_orfa') or ''
        if not nome_policy:
            # Não existe policy de saída pra esse circuito/família ainda — o
            # nome é montado na convenção AS{asn}-{NOME}-V4-OUT, e pra isso
            # os dois campos são obrigatórios (senão sairia "AS--V4-OUT").
            if not nome or not peer_as:
                raise AcaoBgpNaoSuportada(
                    f'O circuito {circuito_id} ainda não tem route-policy de saída '
                    f'{familia.upper()} — informe o nome do circuito e o ASN remoto '
                    f'para que ela possa ser criada.'
                )
            nome_policy = f'AS{peer_as}-{nome}-{familia.upper()}-OUT'
        existentes = {f for n in nodes.get(nome_policy, []) for f in n['community_filters']}
        nodes_ocupados = {n['node'] for n in nodes.get(nome_policy, [])}
        nodes_ocupados |= {t['ordem'] for t in (dados.get('policies') or {}).get(nome_policy, [])}

        pendentes = [(f'{circuito_id}-{a["chave"]}', a) for a in ACOES_ANUNCIO_PADRAO]
        if filtro_global:
            pendentes.append((filtro_global, {
                'chave': filtro_global, 'policy': 'permit', 'prepend': 0,
                'node': NODE_GLOBAL,
            }))
        # Os blocos são emitidos em ordem de NODE (e não na ordem do catálogo)
        # pro preview sair igual ao template padrão — é o texto que o operador
        # revisa antes de confirmar, e ler fora de ordem esconde erro.
        blocos = []
        for nome_filtro, acao in pendentes:
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
            linhas = [f'route-policy {nome_policy} {acao["policy"]} node {node}',
                      f'if-match community-filter {nome_filtro}']
            if acao['prepend']:
                linhas.append(
                    f'apply as-path {" ".join([prepend_as] * acao["prepend"])} additive'
                )
            if acao.get('apply'):
                linhas.append(f'apply {acao["apply"]}')
            linhas.append('quit')
            blocos.append((node, linhas))

        if NODE_CATCHALL not in nodes_ocupados:
            blocos.append((NODE_CATCHALL,
                           [f'route-policy {nome_policy} deny node {NODE_CATCHALL}', 'quit']))
        for _, linhas in sorted(blocos, key=lambda b: b[0]):
            comandos.extend(linhas)

    if not comandos:
        raise AcaoBgpNaoSuportada(
            f'O circuito {circuito_id} já está completo para '
            f'{"/".join(f.upper() for f in familias)} — nada a gerar.'
        )
    return comandos + ['commit']


# ─── Atualização otimista do painel ──────────────────────────────────────────

def _destino_dos_params(params):
    """`destino` é o campo novo (circuito ou grupo global); `circuito` é o nome
    antigo do mesmo parâmetro e continua aceito pra não quebrar ações gravadas
    antes desta versão."""
    return str(params.get('destino') or params.get('circuito') or '')


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
        destino_id = _destino_dos_params(params)
        chave = params.get('acao', '')
        mapa = montar_mapa(dados)
        linha = next((l for l in mapa['anuncios'] if l['prefixo'] == prefixo
                      and (not rp or l['route_policy'] == rp)), None)
        if not linha or linha['node'] is None:
            return
        try:
            destino = destino_de(mapa, destino_id)
        except AcaoBgpNaoSuportada:
            return
        alvo = next((n for n in nodes.get(linha['route_policy'], [])
                     if n['node'] == linha['node']), None)
        if alvo is None:
            return
        # Troca a community DESTE destino (todas as ações dele saem, a
        # escolhida entra) e não toca em mais nada da linha.
        antigas = {a['community'] for a in destino['acoes'].values()}
        if not _e_global(destino):
            antigas |= {community_de(destino['asn_community'], destino['grupo'], a['chave'])
                        for a in ACOES}
        alvo['apply_community'] = [v for v in alvo['apply_community'] if v not in antigas]
        if chave and chave in destino['acoes']:
            alvo['apply_community'].append(destino['acoes'][chave]['community'])

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
        for destino_id, chave in sorted(destinos.items()):
            try:
                destino = destino_de(mapa, str(destino_id))
            except AcaoBgpNaoSuportada:
                continue
            acao = destino['acoes'].get(chave) if chave else None
            if acao:
                valores.append(acao['community'])
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
        cid = _destino_dos_params(params) or (alvo or '')
        grupo = str(params.get('grupo', ''))
        asn = str(params.get('asn_community') or ASN_COMMUNITY_PADRAO)
        if not _RE_ID_CIRCUITO.match(cid) or not grupo:
            return
        filtros = dados.setdefault('community_filters', {})
        for acao in ACOES_PROVISIONAVEIS:
            nome_filtro = f'{cid}-{acao["chave"]}'
            filtros.setdefault(nome_filtro, [{
                'index': 10, 'acao': 'permit',
                'valores': [community_de(asn, grupo, acao['chave'])],
            }])
