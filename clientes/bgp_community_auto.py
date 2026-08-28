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
# `slots`/`base_grupo` são a faixa que o template reserva pra cada família
# (§6/§7/§8): 10 operadoras em 501-510, 10 IX em 601-610 e 5 CDNs em 611-615 —
# o grupo de community de um slot é `base_grupo + numero`, o que torna `c-02`
# → 502 uma conta, não um cadastro. `glob_community` é o sufixo da community
# "anunciar para todos" daquela família (§4).
TIPOS_CIRCUITO = [
    {'chave': 'upstream', 'prefixo': 'c',   'rotulo': 'Operadora / upstream',
     'plural': 'Operadoras / upstreams', 'glob_slug': 'all-upstream',
     'slots': 10, 'base_grupo': 500, 'glob_community': '60001'},
    {'chave': 'ix',       'prefixo': 'ix',  'rotulo': 'IX / PTT',
     'plural': 'IX / PTT',               'glob_slug': 'all-ptts-ixbr',
     'slots': 10, 'base_grupo': 600, 'glob_community': '60011'},
    {'chave': 'cdn',      'prefixo': 'cdn', 'rotulo': 'CDN',
     'plural': 'CDNs',                   'glob_slug': 'all-cdns',
     'slots': 5,  'base_grupo': 610, 'glob_community': '60021'},
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

# Local-preference sugerida ao subir uma sessão, por papel do circuito. Só o
# IX é fixado pelo template (§11, `apply local-preference 3000`); os demais
# valores mantêm a ordem que faz sentido operacionalmente — rota de cliente
# vence a mesma rota aprendida via IX, que vence a via upstream — e são
# editáveis no modal antes de aplicar.
LOCAL_PREFERENCE_PADRAO = {'upstream': '1000', 'ix': '3000', 'cdn': '2000',
                           'downstream': '4000'}

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


def _classificar_orfa(valor, por_resto):
    """
    Uma community da route-policy LOCAL de um prefixo que tem a cara deste
    catálogo mas não casa nenhum community-filter do equipamento — ou seja:
    está na config e não produz efeito nenhum.

    Dois casos aparecem em produção, e a diferença importa porque só um deles
    é derivável do backup:

    - `asn`: a parte numérica bate com um filtro que existe, só o ASN da
      community é outro (65100:50104 numa caixa que carimba 65101:50104). É o
      rastro de uma troca do ASN de community feita nos filtros sem reescrever
      as policies locais — dá pra dizer com certeza qual era a intenção.
    - `orfa`: decompõe no padrão (ação reconhecível), mas nem o valor nem a
      parte numérica existem na caixa (65146:65203 — grupo 652 num equipamento
      cujos grupos são 501-510/601-610/611-615). Aqui a intenção NÃO é
      dedutível: um dígito trocado em 50203 e um 65203 legítimo de convenção
      própria do cliente são indistinguíveis pelo backup. Vira aviso, nunca
      correção automática.

    Devolve None para o que não tem nada a ver com o catálogo (as convenções
    próprias do cliente, preservadas em silêncio como sempre).
    """
    m = _RE_COMMUNITY.match(valor or '')
    if not m:
        return None
    achado = por_resto.get(m.group(2))
    if achado:
        # Chegou aqui como "extra", então o valor exato não casou nenhum
        # filtro: se a parte numérica casa, o que difere é o ASN.
        destino_id, chave, community_viva = achado
        return {'valor': valor, 'tipo': 'asn', 'destino': destino_id,
                'acao': chave, 'sugestao': community_viva, 'grupo': ''}
    dec = _decompor_community(valor)
    if not dec:
        return None
    return {'valor': valor, 'tipo': 'orfa', 'destino': '', 'acao': dec[2],
            'sugestao': '', 'grupo': dec[1]}


def _indice_por_resto(circuitos, globais):
    """`{'50203': ('c-02', 'export-2p', '65146:50203')}` — as communities vivas
    da caixa indexadas pela parte DEPOIS do `:`, que é o que sobrevive a uma
    troca do ASN de community."""
    por_resto = {}
    for destino_id, d in list(circuitos.items()) + list((globais or {}).items()):
        for chave, a in d['acoes'].items():
            m = _RE_COMMUNITY.match(a['community'] or '')
            if m:
                por_resto.setdefault(m.group(2), (destino_id, chave, a['community']))
    return por_resto


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
            # `peer X fake-as N` faz o roteador se apresentar como N pra esse
            # peer — é N que ele vê no AS_PATH, e é N que o prepend precisa
            # repetir (senão o prepend não conta pra esse vizinho).
            'fake_as': sessao.get('fake_as', ''),
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


def _node_local_vazio(dados, rp):
    """Nó adotável de uma route-policy local que existe no equipamento mas
    está VAZIA (`route-policy X permit node 10` sem `apply` nem `if-match`).

    O parser guarda em `community_nodes` só os nós que mexem com community
    (ver `backup_parser.parse_huawei`), então uma policy local ainda "crua" —
    criada junto com o `network` e nunca preenchida — não aparece por lá e o
    prefixo caía como não editável, com o aviso errado de "não foi
    encontrada". Ela é o caso MAIS seguro de editar que existe: não há
    community pra preservar nem `if-match` que possa deixar de casar.

    Só adota quando a policy tem UM único node, `permit`, sem `if-match` e sem
    `apply as-path` — qualquer coisa além disso volta `None` pra automação não
    escrever community num node que talvez nem seja o que aquele prefixo
    percorre. Devolve um node no mesmo formato de `community_nodes`.
    """
    termos = (dados.get('policies') or {}).get(rp) or []
    if len(termos) != 1:
        return None
    termo = termos[0]
    if (termo.get('acao') != 'accept' or termo.get('prefix_lists')
            or termo.get('prepend') or termo.get('extra', {}).get('nao_suportado')):
        return None
    return {
        'policy': rp, 'node': termo.get('extra', {}).get('node', NODE_LOCAL),
        'acao': 'permit', 'community_filters': [], 'apply_community': [],
        'apply_community_extra': [], 'prefix_lists': [],
        'prepend_as': [], 'local_preference': None,
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
    por_resto = _indice_por_resto(circuitos, globais)

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
        elif rp and _node_local_vazio(dados, rp) is not None:
            # Policy local existente, porém ainda sem nenhuma community: node
            # editável, intenção vazia (o prefixo hoje não vai pra lugar nenhum).
            node_local = _node_local_vazio(dados, rp)
        elif rp and lista:
            avisos_linha.append(f'A policy local "{rp}" existe mas não aplica nenhuma community.')
        elif rp and rp in (dados.get('policies') or {}):
            avisos_linha.append(
                f'A policy local "{rp}" existe, mas não tem um node único e vazio onde a '
                f'automação possa carimbar community com segurança — ajuste manualmente.'
            )
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
            # Subconjunto das extras que tem a cara do catálogo mas não casa
            # filtro nenhum: continuam sendo preservadas na reescrita (mexer
            # nelas sozinho mudaria o que o prefixo faz), só deixam de passar
            # despercebidas — ver `_classificar_orfa`.
            'communities_orfas': [o for o in (_classificar_orfa(v, por_resto) for v in extras) if o],
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
    # Slots vagos e downstreams: o painel precisa mostrar tanto o que existe
    # quanto o que ainda falta subir, e as sessões de cliente não passam pelo
    # catálogo de communities (ver `mapear_downstreams`).
    mapa['slots_vagos'] = mapear_slots(mapa['circuitos'])
    mapa['downstreams'] = mapear_downstreams(dados, mapa['circuitos'], mapa['globais'])
    mapa['padroes'] = {
        'asn_community': _asn_community_prevalente(mapa['circuitos']),
        'local_preference': dict(LOCAL_PREFERENCE_PADRAO),
    }
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
        # Sessão com fake-as: o peer enxerga o ASN falso, então prepend com
        # qualquer outro ASN não alonga o caminho aos olhos DELE — as ações de
        # prepend deste circuito ficam sem efeito prático.
        fakes = {s['fake_as'] for s in c['sessoes'] if s.get('fake_as')}
        for fake in sorted(fakes):
            if c['prepend_as'] and c['prepend_as'] != fake:
                avisos.append(
                    f'{cid}: a sessão usa `fake-as {fake}` (é esse o ASN que o peer vê), '
                    f'mas a policy de saída prepende {c["prepend_as"]} — o prepend não '
                    f'alonga o caminho para este vizinho.'
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
    # Communities de prefixo que não casam filtro nenhum (`_classificar_orfa`).
    # Consolidadas por VALOR e não por prefixo: numa caixa que trocou o ASN de
    # community sem reescrever as policies locais, a mesma community órfã se
    # repete em dezenas de prefixos e um aviso por linha afogaria o painel.
    orfas = {}
    for linha in mapa.get('anuncios') or []:
        for o in linha.get('communities_orfas') or []:
            reg = orfas.setdefault(o['valor'], {'info': o, 'prefixos': []})
            reg['prefixos'].append(linha['prefixo'])

    def _onde(prefixos):
        return f'{len(prefixos)} prefixos' if len(prefixos) > 1 else prefixos[0]

    for valor in sorted(v for v, r in orfas.items() if r['info']['tipo'] == 'asn'):
        o, prefixos = orfas[valor]['info'], orfas[valor]['prefixos']
        rotulo = ACOES_POR_CHAVE.get(o['acao'], {}).get('rotulo', o['acao'])
        avisos.append(
            f'{valor} (em {_onde(prefixos)}) não casa nenhum community-filter: esta caixa '
            f'usa {o["sugestao"]} para "{rotulo}" em {o["destino"]}. Enquanto estiver assim, '
            f'{"esses prefixos não estão" if len(prefixos) > 1 else "esse prefixo não está"} '
            f'sendo anunciado por {o["destino"]} — provável troca do ASN de community feita '
            f'só nos filtros.'
        )
    # As órfãs sem destino dedutível vêm agrupadas por GRUPO: um bloco de
    # convenção antiga (581/582/583 de uma caixa pré-padrão) rende uma linha
    # por grupo em vez de uma por ação × prefixo.
    por_grupo = {}
    for valor, reg in orfas.items():
        if reg['info']['tipo'] == 'orfa':
            por_grupo.setdefault(reg['info']['grupo'], []).append((valor, reg['prefixos']))
    for grupo in sorted(por_grupo):
        itens = sorted(por_grupo[grupo])
        lista = ', '.join(f'{v} (em {_onde(p)})' for v, p in itens)
        avisos.append(
            f'{"As communities" if len(itens) > 1 else "A community"} {lista} '
            f'{"têm" if len(itens) > 1 else "tem"} a forma do padrão (grupo {grupo}) mas não '
            f'{"correspondem" if len(itens) > 1 else "corresponde"} a nenhum community-filter '
            f'deste equipamento — não {"produzem" if len(itens) > 1 else "produz"} efeito '
            f'nenhum. Ou é convenção própria fora do catálogo, ou é dígito trocado.'
        )

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

    comandos = [f'route-policy {linha["route_policy"]} permit node {linha["node"]}']
    # `undo apply community` só faz sentido quando há o que desfazer: num node
    # ainda vazio (policy local criada e nunca preenchida) o VRP recusa o undo
    # de um atributo inexistente e a sessão inteira aborta antes do `apply`.
    if linha['destinos'] or linha['globais'] or linha['communities_extras']:
        comandos.append('undo apply community')
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


def _blocos_policy_out(dados, circuito_id, nome_policy, prepend_as, filtro_global):
    """
    Linhas dos nodes que faltam na route-policy de SAÍDA de um circuito: um por
    ação do catálogo (`<circuito>-export`, `-export-1p`, …), o node da
    community global do tipo e o `deny node 999` final.

    Emitidos em ordem de NODE (e não na ordem do catálogo) pro preview sair
    igual ao template padrão — é o texto que o operador revisa antes de
    confirmar, e ler fora de ordem esconde erro. Nada existente é reemitido.
    """
    nodes = dados.get('community_nodes') or {}
    existentes = {f for n in nodes.get(nome_policy, []) for f in n['community_filters']}
    nodes_ocupados = {n['node'] for n in nodes.get(nome_policy, [])}
    nodes_ocupados |= {t['ordem'] for t in (dados.get('policies') or {}).get(nome_policy, [])}

    pendentes = [(f'{circuito_id}-{a["chave"]}', a) for a in ACOES_ANUNCIO_PADRAO]
    if filtro_global:
        pendentes.append((filtro_global, {
            'chave': filtro_global, 'policy': 'permit', 'prepend': 0,
            'node': NODE_GLOBAL,
        }))
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
    return [linha for _, linhas in sorted(blocos, key=lambda b: b[0]) for linha in linhas]


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
        comandos.extend(_blocos_policy_out(dados, circuito_id, nome_policy,
                                           prepend_as, filtro_global))

    if not comandos:
        raise AcaoBgpNaoSuportada(
            f'O circuito {circuito_id} já está completo para '
            f'{"/".join(f.upper() for f in familias)} — nada a gerar.'
        )
    return comandos + ['commit']


# ─── Slots do template: o que já existe e o que ainda está vago ──────────────

def slot_padrao(circuito_id):
    """
    `c-02` → o que o template reserva pra esse slot: tipo, número e grupo de
    community. O grupo é uma CONTA (`base_grupo + numero`), não um cadastro —
    c-02 → 502, ix-07 → 607, cdn-03 → 613 (§6/§7/§8) —, que é o que permite
    subir um circuito novo sem o operador ter que decorar a numeração.

    Devolve None se o identificador não for do padrão.
    """
    m = _RE_ID_CIRCUITO.match(str(circuito_id or ''))
    if not m:
        return None
    tipo = TIPOS_POR_PREFIXO[m.group(1)]
    numero = int(m.group(2))
    return {
        'id': f'{tipo["prefixo"]}-{numero:02d}',
        'tipo': tipo['chave'], 'numero': numero,
        'grupo': str(tipo['base_grupo'] + numero),
        'na_faixa': 1 <= numero <= tipo['slots'],
    }


def mapear_slots(circuitos):
    """
    Os slots do template que a caixa ainda NÃO tem configurados.

    O painel precisa mostrar o que falta e não só o que existe: é clicando num
    slot vago (`c-02`, `ix-04`) que o operador manda subir aquele circuito do
    zero. Slots já ocupados ficam de fora — quem responde por eles é
    `mapear_circuitos`.
    """
    vagos = []
    for tipo in TIPOS_CIRCUITO:
        for numero in range(1, tipo['slots'] + 1):
            cid = f'{tipo["prefixo"]}-{numero:02d}'
            if cid in circuitos:
                continue
            vagos.append({
                'id': cid, 'tipo': tipo['chave'], 'tipo_rotulo': tipo['rotulo'],
                'numero': numero, 'grupo': str(tipo['base_grupo'] + numero),
                'vago': True,
            })
    return vagos


def _asn_community_prevalente(circuitos):
    """ASN usado nas communities DESTA caixa. Não é necessariamente o ASN do
    `bgp <N>`: as caixas de referência marcam as rotas com 65100/65101 (ASN
    privado) mesmo anunciando como AS268080. Um circuito novo tem que nascer
    com o mesmo ASN dos que já existem, senão a community dele não casa filtro
    nenhum."""
    votos = {}
    for c in circuitos.values():
        if c.get('asn_community'):
            votos[c['asn_community']] = votos.get(c['asn_community'], 0) + 1
    return max(votos, key=lambda k: votos[k]) if votos else ASN_COMMUNITY_PADRAO


# ─── Downstreams: as sessões de cliente ──────────────────────────────────────

# `DOWNSTREAM-CIANET-V4-IN`, `AS268137-NET-SINI-IPv6_out`,
# `RP-DOWNSTREAM-GNET-IPV6-OUT` — as três formas aparecem nas caixas reais, e
# é o nome sem o sufixo de família que junta o IPv4 e o IPv6 do mesmo cliente.
_RE_POLICY_FAMILIA = re.compile(r'^(.*?)[-_]?(?:IP)?V([46])[-_](IN|OUT)$', re.IGNORECASE)


def _permite_tabela_cheia(dados, nome_policy):
    """
    A policy de saída libera a tabela cheia?

    É a assinatura de um cliente/downstream: em vez de casar communities de
    anúncio (que é como esta automação controla o que vai pra cada upstream),
    ela manda tudo — ou por um node `permit` sem nenhum `if-match`, ou por uma
    prefix-list de full routing (`0.0.0.0/0 less-equal 24`, `::/0 less-equal
    48`).
    """
    for termo in (dados.get('policies') or {}).get(nome_policy, []):
        if termo['acao'] != 'accept':
            continue
        if not termo['prefix_lists']:
            return True
        for nome_pl in termo['prefix_lists']:
            if _e_lista_full_routing((dados.get('prefix_lists') or {}).get(nome_pl, [])):
                return True
    return False


def _e_lista_full_routing(entradas):
    """
    Prefix-list que representa "a tabela cheia": permite a partir da rota
    default com desagregação (o `0.0.0.0/0 less-equal 24` do §1).

    O `len_min == 0` é o que separa isso de uma lista de BOGONS — a de uma das
    caixas em produção tem `permit 0.0.0.0/0 greater-equal 25 less-equal 32`
    (prefixo longo demais, que é bogon por tamanho) e casaria uma regra que
    olhasse só o prefixo e o teto.
    """
    for e in entradas:
        if e['acao'] != 'permit':
            continue
        if e['prefixo'] in ('0.0.0.0/0', '::/0') and e['len_min'] == 0 and e['len_max'] >= 24:
            return True
    return False


def mapear_downstreams(dados, circuitos, globais=None):
    """
    As sessões de CLIENTE (downstream) atendidas por este equipamento.

    Downstream não é circuito de community: pra um cliente não se escolhe
    "anunciar ou não o prefixo X" — manda-se a tabela cheia e recebe-se o
    bloco dele. O que identifica um na config, sem depender de nome, são duas
    coisas juntas: a sessão não pertence a nenhum circuito do catálogo (a
    policy de saída dela não casa filtro nenhum) e essa policy libera a tabela
    cheia (`_permite_tabela_cheia`).

    Sessões iBGP ficam de fora — a policy delas também manda tudo, mas cliente
    não é.

    IPv4 e IPv6 do mesmo cliente são agrupados pelo nome da policy sem o
    sufixo de família (`DOWNSTREAM-CIANET-V4-IN` → `DOWNSTREAM-CIANET`).

    O que o painel mostra de cada um vem daqui: as sessões, a prefix-list que
    define o que ele PODE mandar e — resolvendo as communities que a policy de
    entrada carimba — para onde os prefixos dele são reanunciados.
    """
    globais = globais or {}
    nodes = dados.get('community_nodes') or {}
    presos = {s['peer_ip'] for c in circuitos.values() for s in c['sessoes']}

    por_community = {}
    for cid, c in circuitos.items():
        for chave, a in c['acoes'].items():
            por_community.setdefault(a['community'], (cid, chave))
    for gid, g in globais.items():
        for chave, a in g['acoes'].items():
            por_community.setdefault(a['community'], (gid, chave))

    downs = {}
    for sessao in dados.get('sessoes') or []:
        policy_out = sessao.get('policy_out') or ''
        peer_ip = sessao.get('peer_ip', '')
        if not policy_out or peer_ip in presos:
            continue
        if sessao.get('peer_as') and sessao['peer_as'] == sessao.get('as_local'):
            continue
        if not _permite_tabela_cheia(dados, policy_out):
            continue

        m = _RE_POLICY_FAMILIA.match(policy_out)
        base = m.group(1) if m else policy_out
        familia = _familia_do_prefixo(peer_ip)
        d = downs.setdefault(base, {
            'id': base, 'tipo': 'downstream', 'tipo_rotulo': 'Downstream / cliente',
            'nome': re.sub(r'^(DOWNSTREAM|AS\d+)-', '', base, flags=re.IGNORECASE) or base,
            'rotulo': base, 'peer_as': '', 'sessoes': [],
            'policies': {}, 'prefixos_aceitos': {'v4': [], 'v6': []},
            'destinos': {}, 'ipv4': False, 'ipv6': False,
        })
        d['sessoes'].append({
            'nome': sessao.get('nome', ''), 'peer_ip': peer_ip,
            'peer_as': sessao.get('peer_as', ''), 'descricao': sessao.get('descricao', ''),
            'familia': familia, 'habilitada': sessao.get('habilitada', True),
            'policy_out': policy_out, 'policy_in': sessao.get('policy_in', ''),
        })
        d[('ipv6' if familia == 'v6' else 'ipv4')] = True
        d['peer_as'] = d['peer_as'] or sessao.get('peer_as', '')
        d['policies'][f'{familia}_out'] = policy_out
        if sessao.get('policy_in'):
            d['policies'][f'{familia}_in'] = sessao['policy_in']
            # O que o cliente pode mandar: as prefix-lists casadas nos nodes
            # `permit` da policy de entrada (as de bogon ficam nos `deny`).
            for termo in (dados.get('policies') or {}).get(sessao['policy_in'], []):
                if termo['acao'] != 'accept':
                    continue
                for nome_pl in termo['prefix_lists']:
                    entradas = [e['prefixo'] for e in (dados.get('prefix_lists') or {}).get(nome_pl, [])
                                if e['acao'] == 'permit']
                    if entradas and nome_pl not in [p['lista'] for p in d['prefixos_aceitos'][familia]]:
                        d['prefixos_aceitos'][familia].append({'lista': nome_pl, 'prefixos': entradas})
            # Para onde os prefixos dele são reanunciados: as communities que a
            # policy de entrada carimba, traduzidas em destino do catálogo.
            for n in nodes.get(sessao['policy_in'], []):
                for valor in n['apply_community']:
                    achado = por_community.get(valor)
                    if achado:
                        d['destinos'][achado[0]] = achado[1]
    return downs
# ─── Geração: subir um circuito (policies + sessão BGP) do zero ──────────────

# Bogons do §2 na forma que SERVE pra bloquear: `if-match ip-prefix X` só casa
# quando X **permite** a rota, então uma lista escrita toda em `deny` (como a
# BOGONS-V4 do template) nunca casa e o `deny node 5` que a usa é config
# morta. As caixas de referência resolveram isso com uma segunda lista, em
# `permit` — é essa que esta automação usa e cria quando não existe.
BOGONS = {
    'v4': [('0.0.0.0', 8), ('10.0.0.0', 8), ('100.64.0.0', 10), ('127.0.0.0', 8),
           ('169.254.0.0', 16), ('172.16.0.0', 12), ('192.0.0.0', 24), ('192.0.2.0', 24),
           ('192.88.99.0', 24), ('192.168.0.0', 16), ('198.18.0.0', 15),
           ('198.51.100.0', 24), ('203.0.113.0', 24), ('224.0.0.0', 4), ('240.0.0.0', 4)],
    'v6': [('::', 128), ('::1', 128), ('::ffff:0:0', 96), ('100::', 64), ('2001:2::', 48),
           ('2001:db8::', 32), ('2001:10::', 28), ('fc00::', 7), ('fe80::', 10), ('ff00::', 8)],
}
NOME_BOGONS = {'v4': 'BOGONS-V4-IN', 'v6': 'BOGONS-V6-IN'}
NOME_FULL_ROUTING = {'v4': 'FULL-ROUTING', 'v6': 'FULL-ROUTING-V6'}
# Maior prefixo aceito de um peer/cliente. O template §1 usa `less-equal 34`
# em IPv6, mas as caixas em produção usam 48 — que é o limite do IX.br e o que
# de fato circula; com 34 quase todo /48 legítimo seria descartado.
MAIOR_PREFIXO = {'v4': 24, 'v6': 48}
_CMD_PREFIX_LIST = {'v4': 'ip ip-prefix', 'v6': 'ip ipv6-prefix'}
_CMD_IF_MATCH = {'v4': 'if-match ip-prefix', 'v6': 'if-match ipv6 address prefix-list'}
_AF = {'v4': 'ipv4-family unicast', 'v6': 'ipv6-family unicast'}
_RE_NOME_LIMPO = re.compile(r'[^A-Z0-9-]')


def _familia_da_lista(entradas):
    return 'v6' if any(':' in e.get('prefixo', '') for e in entradas) else 'v4'


def _prefix_list_bogons(dados, familia):
    """(nome, comandos) da prefix-list de bogons usável num node `deny` —
    reaproveitando a da caixa quando ela já tem uma em `permit`, criando
    `BOGONS-V4-IN`/`BOGONS-V6-IN` quando não tem. Uma lista só de `deny` não
    serve (nunca casa) e por isso não é reaproveitada."""
    for nome, entradas in sorted((dados.get('prefix_lists') or {}).items()):
        if 'BOGON' not in nome.upper() or _familia_da_lista(entradas) != familia:
            continue
        # Uma lista que permite a partir do prefixo 0 casaria QUALQUER rota —
        # num node `deny` isso não filtra bogon, derruba a sessão inteira.
        if any(e['acao'] == 'permit' for e in entradas) and not any(
                e['acao'] == 'permit' and e['len_min'] == 0 for e in entradas):
            return nome, []
    nome = NOME_BOGONS[familia]
    if nome in (dados.get('prefix_lists') or {}):
        return nome, []
    teto = 32 if familia == 'v4' else 128
    comandos = []
    for i, (rede, tam) in enumerate(BOGONS[familia], start=1):
        linha = f'{_CMD_PREFIX_LIST[familia]} {nome} index {i * 10} permit {rede} {tam}'
        if tam < teto:
            linha += f' greater-equal {tam} less-equal {teto}'
        comandos.append(linha)
    return nome, comandos


def _prefix_list_full_routing(dados, familia):
    """(nome, comandos) da prefix-list de "tabela cheia" (§1) — a que a policy
    de entrada usa pra aceitar rotas do peer e a de saída de um downstream usa
    pra mandar a tabela inteira."""
    candidatas = sorted((dados.get('prefix_lists') or {}).items(),
                        key=lambda kv: ('FULL-ROUTING' not in kv[0].upper(), kv[0]))
    for nome, entradas in candidatas:
        if _familia_da_lista(entradas) == familia and _e_lista_full_routing(entradas):
            return nome, []
    nome = NOME_FULL_ROUTING[familia]
    rede = '0.0.0.0 0' if familia == 'v4' else ':: 0'
    return nome, [f'{_CMD_PREFIX_LIST[familia]} {nome} index 10 permit {rede} '
                  f'less-equal {MAIOR_PREFIXO[familia]}']


def _nome_limpo(valor, campo):
    nome = _RE_NOME_LIMPO.sub('-', (valor or '').strip().upper().replace(' ', '-')).strip('-')
    if not nome:
        raise AcaoBgpNaoSuportada(f'Informe {campo}.')
    return nome


def _validar_peer(dados, peer_ip, familia, ja_usados):
    """Peer válido, da família certa e ainda não configurado neste
    equipamento — configurar duas vezes o mesmo peer é erro de digitação, não
    intenção."""
    try:
        ip = ipaddress.ip_address(peer_ip)
    except ValueError:
        raise AcaoBgpNaoSuportada(f'IP de peer inválido: "{peer_ip}".')
    if (6 if ip.version == 6 else 4) != (6 if familia == 'v6' else 4):
        raise AcaoBgpNaoSuportada(
            f'O peer "{peer_ip}" não é {familia.upper()} — confira a família em que ele foi informado.'
        )
    if peer_ip in ja_usados:
        raise AcaoBgpNaoSuportada(f'Já existe uma sessão com o peer "{peer_ip}" neste equipamento.')
    ja_usados.add(peer_ip)
    return str(ip)


def _bloco_policy_in(nome_policy, familia, bogons, aceita, communities,
                     local_preference=''):
    """
    Policy de ENTRADA no layout do template (§9/§11): bogons fora no node 5,
    o que o peer pode mandar aceito no node 10 — carimbando a community de
    origem (§17) e, quando informada, a local-preference do tipo de circuito —,
    `deny node 999` no fim pra nada entrar por engano.

    `bogons` vazio pula o node 5. É o caso do downstream: lá o node 10 casa a
    prefix-list DO CLIENTE (os blocos dele, e só), então nenhum bogon teria como
    passar — o filtro seria 15 linhas de config que nunca casam. Num upstream/IX
    é o contrário: o node 10 aceita a tabela cheia, e é o node 5 que segura os
    bogons.
    """
    linhas = []
    if bogons:
        linhas += [f'route-policy {nome_policy} deny node 5',
                   f'{_CMD_IF_MATCH[familia]} {bogons}',
                   'quit']
    linhas += [f'route-policy {nome_policy} permit node {NODE_LOCAL}',
               f'{_CMD_IF_MATCH[familia]} {aceita}']
    if local_preference:
        linhas.append(f'apply local-preference {local_preference}')
    if communities:
        linhas.append(f'apply community {" ".join(communities)} additive')
    linhas += ['quit',
               f'route-policy {nome_policy} deny node {NODE_CATCHALL}',
               'quit']
    return linhas


def _bloco_sessao(as_local, familias, peers_por_familia, policies, grupos=None,
                  public_as_only=False, habilitar=True, fake_as=''):
    """
    O bloco `bgp <ASN>` de uma sessão nova, no formato das caixas em produção.

    Dois arranjos, conforme o tipo: peer individual (operadora, CDN, cliente)
    leva as route-policies nele mesmo; IX leva um `group EBGP-<NOME>-V4
    external` com as policies NO GRUPO e os route servers como membros — que é
    como o IX.br é configurado e o que o parser lê de volta.

    Detalhe de VRP que não pode faltar: um peer nasce habilitado na
    `ipv4-family unicast`, mesmo sendo IPv6. Por isso todo peer/grupo v6 leva
    um `undo peer … enable` na family v4 além do `enable` na v6 — sem isso o
    peer v6 fica pendurado na family errada.
    """
    grupos = grupos or {}
    comandos = [f'bgp {as_local}']
    for familia in familias:
        grupo = grupos.get(familia, '')
        if grupo:
            comandos.append(f'group {grupo} external')
        for peer in peers_por_familia[familia]:
            comandos.append(f'peer {peer["ip"]} as-number {peer["as"]}')
            if grupo:
                comandos.append(f'peer {peer["ip"]} group {grupo}')
            if peer.get('descricao'):
                comandos.append(f'peer {peer["ip"]} description {peer["descricao"]}')
            if fake_as:
                # Por peer, e não no grupo: é assim que o parser lê o fake-as
                # de volta (`peer <IP> fake-as N`), então é assim que o painel
                # continua sabendo qual ASN este vizinho enxerga.
                comandos.append(f'peer {peer["ip"]} fake-as {fake_as}')
            if not habilitar:
                # No VRP um peer nasce ATIVO — deixar de emitir `enable` não o
                # segura. Quem segura é `peer … ignore`, o mesmo comando que o
                # botão Ativar/Desativar do painel desfaz
                # (`bgp_actions.comandos_toggle_sessao`).
                comandos.append(f'peer {peer["ip"]} ignore')

    v6_na_family_v4 = ([grupos.get('v6')] if grupos.get('v6') else
                       [p['ip'] for p in peers_por_familia.get('v6', [])]) if 'v6' in familias else []
    if v6_na_family_v4 and 'v4' not in familias:
        # Sessão só-IPv6: ainda assim é preciso entrar na family v4 pra tirar
        # de lá o peer que nasceu habilitado nela.
        comandos.append(_AF['v4'])
        comandos += [f'undo peer {alvo} enable' for alvo in v6_na_family_v4]
        comandos.append('quit')

    for familia in familias:
        comandos.append(_AF[familia])
        if familia == 'v4':
            comandos += [f'undo peer {alvo} enable' for alvo in v6_na_family_v4]
        grupo = grupos.get(familia, '')
        for nome_alvo in ([grupo] if grupo else [p['ip'] for p in peers_por_familia[familia]]):
            comandos.append(f'peer {nome_alvo} enable')
            if public_as_only:
                comandos.append(f'peer {nome_alvo} public-as-only')
            comandos.append(f'peer {nome_alvo} route-policy {policies[f"{familia}_in"]} import')
            comandos.append(f'peer {nome_alvo} route-policy {policies[f"{familia}_out"]} export')
            comandos.append(f'peer {nome_alvo} advertise-community')
            comandos.append(f'peer {nome_alvo} advertise-ext-community')
        if grupo:
            # Membros do grupo: habilitar e prender ao grupo — as policies já
            # valem pra todos eles através dele.
            for peer in peers_por_familia[familia]:
                comandos.append(f'peer {peer["ip"]} enable')
                comandos.append(f'peer {peer["ip"]} group {grupo}')
        comandos.append('quit')
    comandos.append('quit')
    return comandos
def _peers_das_opcoes(dados, opcoes, ja_usados, sufixo_descricao, prefixo_rs=''):
    """`opcoes['v4']['peers']` → lista validada por família, com a descrição
    já montada. `prefixo_rs` liga a numeração de route server dos IX
    (`RS1.PTT-CE`, `RS2.PTT-CE`) — nos demais tipos a descrição é a do peer."""
    familias, peers = [], {}
    for familia in ('v4', 'v6'):
        entradas = ((opcoes.get(familia) or {}).get('peers')) or []
        lista = []
        for i, entrada in enumerate(entradas, start=1):
            ip = (entrada.get('ip') or '').strip()
            if not ip:
                continue
            ip = _validar_peer(dados, ip, familia, ja_usados)
            descricao = (entrada.get('descricao') or '').strip()
            if not descricao:
                if prefixo_rs:
                    descricao = f'RS{i}.{prefixo_rs}' + ('-V6' if familia == 'v6' else '')
                else:
                    descricao = f'{sufixo_descricao}-{familia.upper()}'
                    if len(entradas) > 1:
                        descricao += f'-{i}'
            lista.append({'ip': ip, 'as': (entrada.get('peer_as') or '').strip(),
                          'descricao': descricao})
        if lista:
            familias.append(familia)
            peers[familia] = lista
    return familias, peers


def _recusar_colisao_de_nomes(dados, circuito_id, policies, grupos_peer):
    """
    Recusa reaproveitar por acidente a route-policy ou o peer-group de OUTRO
    circuito.

    É o erro caro deste formulário: batizar o circuito novo com um nome que já
    existe (subir `ix-05` chamando de PTT-SP quando `AS26162-PTT-SP-V4-OUT` já
    é do `ix-01`) faria os nodes do circuito novo entrarem na policy do antigo
    — e, no caso do peer-group, trocaria as policies de uma sessão de IX que
    está no ar. Nada disso apareceria como erro no equipamento.
    """
    nodes = dados.get('community_nodes') or {}
    for chave, nome_policy in policies.items():
        dono = None
        for n in nodes.get(nome_policy, []):
            for f in n['community_filters']:
                m = _RE_FILTRO.match(f)
                if m:
                    dono = f'{m.group(1)}-{m.group(2)}'
                    break
            if dono:
                break
        if dono and dono != circuito_id:
            raise AcaoBgpNaoSuportada(
                f'A route-policy "{nome_policy}" já é do circuito {dono} — usar esse nome '
                f'misturaria os dois. Escolha outro nome para o circuito {circuito_id}.'
            )

    grupos_em_uso = {s.get('grupo') for s in (dados.get('sessoes') or []) if s.get('grupo')}
    for familia, grupo in (grupos_peer or {}).items():
        if grupo in grupos_em_uso:
            raise AcaoBgpNaoSuportada(
                f'O peer-group "{grupo}" já existe neste equipamento e tem sessões — '
                f'aplicar aqui trocaria as route-policies delas. Escolha outro nome.'
            )


def comandos_criar_circuito(dados, mapa, circuito_id, opcoes=None):
    """
    Sobe um circuito INTEIRO — o que o operador vê como "clicar num slot vago
    e preencher a sessão": os community-filters do slot, as route-policies de
    entrada e de saída no layout do template e a sessão BGP em si.

    Vale para as três famílias do catálogo, mudando só o que a convenção mudaria
    na mão:

    - operadora (`c-NN`) e CDN (`cdn-NN`): peer individual, policies no peer;
    - IX (`ix-NN`): `group EBGP-<NOME>-V4 external` com os route servers como
      membros, `public-as-only` e as policies NO GRUPO — é assim que o IX.br é
      configurado nas caixas em produção, e é dessa forma que o parser
      consegue ler a sessão de volta.

    O grupo de community não é perguntado: sai do próprio slot (`c-02` → 502,
    §6). Nada existente é sobrescrito — o que já estiver configurado é pulado,
    então a mesma ação serve pra subir do zero ou pra completar um circuito
    que ficou pela metade.
    """
    opcoes = opcoes or {}
    slot = slot_padrao(circuito_id)
    if not slot:
        raise AcaoBgpNaoSuportada(
            'Identificador de circuito inválido — esperado '
            f'{", ".join(t["prefixo"] + "-NN" for t in TIPOS_CIRCUITO)}.'
        )
    circuito_id = slot['id']
    tipo = TIPOS_POR_CHAVE[slot['tipo']]
    circuito = mapa['circuitos'].get(circuito_id)

    as_local = str(opcoes.get('as_local') or mapa.get('as_local') or '').strip()
    if not as_local:
        as_local = (dados.get('sessoes') or [{}])[0].get('as_local', '') or dados.get('as_local', '')
    if not as_local:
        raise AcaoBgpNaoSuportada('Não foi possível identificar o ASN local (`bgp <ASN>`) deste equipamento.')

    nome = _nome_limpo(opcoes.get('nome') or (circuito or {}).get('nome'),
                       'o nome do circuito (ex: BR-DIGITAL, PTT-CE, NETFLIX)')
    peer_as = str(opcoes.get('peer_as') or (circuito or {}).get('peer_as') or '').strip()
    if not peer_as.isdigit():
        raise AcaoBgpNaoSuportada('Informe o ASN do outro lado da sessão.')
    grupo = str(opcoes.get('grupo') or (circuito or {}).get('grupo') or slot['grupo'])
    asn_community = str(opcoes.get('asn_community') or (circuito or {}).get('asn_community')
                        or _asn_community_prevalente(mapa['circuitos']))
    fake_as = str(opcoes.get('fake_as') or '').strip()
    if fake_as and not fake_as.isdigit():
        raise AcaoBgpNaoSuportada('O fake-AS tem que ser um número.')
    # Com fake-as o peer enxerga o ASN falso — prepend com qualquer outro não
    # alonga o caminho pra ele. Por isso o fake-as manda no prepend, na frente
    # do que o circuito já usava.
    prepend_as = str(opcoes.get('prepend_as') or fake_as
                     or (circuito or {}).get('prepend_as') or as_local).strip()
    if fake_as and prepend_as != fake_as:
        raise AcaoBgpNaoSuportada(
            f'Com `fake-as {fake_as}` o prepend tem que repetir {fake_as} — é esse o ASN '
            f'que este peer enxerga; prepend de {prepend_as} não alongaria o caminho pra ele.'
        )
    local_preference = str(opcoes.get('local_preference') or '').strip()
    if local_preference and not local_preference.isdigit():
        raise AcaoBgpNaoSuportada('A local-preference tem que ser um número.')

    ja_usados = {s.get('peer_ip') for s in (dados.get('sessoes') or [])}
    familias, peers = _peers_das_opcoes(
        dados, opcoes, ja_usados, f'EBGP-AS{peer_as}-{nome}',
        prefixo_rs=nome if slot['tipo'] == 'ix' else '',
    )
    if not familias:
        raise AcaoBgpNaoSuportada('Informe pelo menos um peer IPv4 ou IPv6 da sessão.')
    for familia in familias:
        for peer in peers[familia]:
            peer['as'] = peer['as'] or peer_as

    # Nomes: os que a caixa já usa pra este circuito têm prioridade sobre a
    # convenção — completar um circuito não pode criar uma policy paralela.
    policies = {}
    for familia in familias:
        base = f'AS{peer_as}-{nome}-{familia.upper()}'
        atuais = (circuito or {}).get('policies', {})
        policies[f'{familia}_in'] = (opcoes.get(f'policy_{familia}_in')
                                     or atuais.get(f'{familia}_in') or f'{base}-IN')
        policies[f'{familia}_out'] = (opcoes.get(f'policy_{familia}_out')
                                      or atuais.get(f'{familia}_out')
                                      or atuais.get(f'{familia}_out_orfa') or f'{base}-OUT')
    grupos_peer = {}
    if slot['tipo'] == 'ix':
        for familia in familias:
            grupos_peer[familia] = (opcoes.get(f'grupo_peer_{familia}')
                                    or f'EBGP-{nome}-{familia.upper()}')

    filtros = dados.get('community_filters') or {}
    policies_existentes = set(dados.get('community_nodes') or {}) | set(dados.get('policies') or {})
    _recusar_colisao_de_nomes(dados, circuito_id, policies, grupos_peer)
    comandos = []

    # 1) prefix-lists de apoio (bogons e tabela cheia), só se faltarem
    listas = {}
    for familia in familias:
        bogons, cmd_bogons = _prefix_list_bogons(dados, familia)
        full, cmd_full = _prefix_list_full_routing(dados, familia)
        listas[familia] = {'bogons': bogons, 'full': full}
        comandos += cmd_bogons + cmd_full

    # 2) community-filters do circuito e o do grupo global do tipo
    for acao in ACOES_PROVISIONAVEIS:
        nome_filtro = f'{circuito_id}-{acao["chave"]}'
        if nome_filtro not in filtros:
            comandos.append(
                f'ip community-filter basic {nome_filtro} index 10 permit '
                f'{community_de(asn_community, grupo, acao["chave"])}'
            )
    glob_id = f'{PREFIXO_DESTINO_GLOBAL}{tipo["glob_slug"]}'
    if glob_id not in filtros:
        comandos.append(
            f'ip community-filter basic {glob_id} index 10 permit '
            f'{asn_community}:{tipo["glob_community"]}'
        )

    # 3) policies de entrada e saída
    for familia in familias:
        nome_in = policies[f'{familia}_in']
        if nome_in not in policies_existentes:
            comandos += _bloco_policy_in(
                nome_in, familia, listas[familia]['bogons'], listas[familia]['full'],
                [community_de(asn_community, grupo, 'import-rr')],
                local_preference=local_preference,
            )
        comandos += _blocos_policy_out(dados, circuito_id, policies[f'{familia}_out'],
                                       prepend_as, glob_id)

    # 4) a sessão em si
    comandos += _bloco_sessao(
        as_local, familias, peers, policies, grupos=grupos_peer,
        public_as_only=bool(opcoes.get('public_as_only', slot['tipo'] == 'ix')),
        habilitar=bool(opcoes.get('habilitar', True)), fake_as=fake_as,
    )
    return comandos + ['commit']


def comandos_criar_downstream(dados, mapa, opcoes=None):
    """
    Sobe uma sessão de CLIENTE (downstream) seguindo o mesmo script de uma
    operadora, com as duas diferenças que definem o papel:

    - na SAÍDA o cliente recebe a tabela cheia (`if-match ip-prefix
      FULL-ROUTING`), em vez dos nodes de community que filtram o que vai pra
      um upstream;
    - na ENTRADA só passam os prefixos DELE: a prefix-list `PL-DOWNSTREAM-
      <NOME>-V4/V6` é criada com os blocos informados e é ela que a policy de
      entrada casa. Fora dela, `deny node 999`.

    Os prefixos do cliente ainda precisam ser reanunciados, e é aí que o
    catálogo de communities entra: as communities dos destinos escolhidos são
    carimbadas na policy de ENTRADA, então as rotas dele já entram marcadas
    para os upstreams/IX/CDNs pedidos. Sem isso elas ficariam presas no
    equipamento — as policies de saída terminam em `deny node 999`, que só
    deixa passar o que tem community de anúncio.
    """
    opcoes = opcoes or {}
    nome = _nome_limpo(opcoes.get('nome'), 'o nome do cliente (ex: CIANET)')
    peer_as = str(opcoes.get('peer_as') or '').strip()
    if not peer_as.isdigit():
        raise AcaoBgpNaoSuportada('Informe o ASN do cliente.')
    as_local = str(opcoes.get('as_local') or mapa.get('as_local') or '').strip()
    if not as_local:
        as_local = (dados.get('sessoes') or [{}])[0].get('as_local', '') or dados.get('as_local', '')
    if not as_local:
        raise AcaoBgpNaoSuportada('Não foi possível identificar o ASN local (`bgp <ASN>`) deste equipamento.')
    local_preference = str(opcoes.get('local_preference') or '').strip()
    if local_preference and not local_preference.isdigit():
        raise AcaoBgpNaoSuportada('A local-preference tem que ser um número.')
    fake_as = str(opcoes.get('fake_as') or '').strip()
    if fake_as and not fake_as.isdigit():
        raise AcaoBgpNaoSuportada('O fake-AS tem que ser um número.')

    ja_usados = {s.get('peer_ip') for s in (dados.get('sessoes') or [])}
    familias, peers = _peers_das_opcoes(dados, opcoes, ja_usados, f'EBGP-DOWNSTREAM-{nome}')
    if not familias:
        raise AcaoBgpNaoSuportada('Informe pelo menos um peer IPv4 ou IPv6 do cliente.')
    for familia in familias:
        for peer in peers[familia]:
            peer['as'] = peer['as'] or peer_as

    # Communities de reanúncio: mesmas validações de um prefixo local (o
    # destino tem que existir, ter a ação e — se for global — alcançar
    # alguém), porque o efeito é exatamente o mesmo.
    communities = []
    destinos_escolhidos = {}
    for destino_id, chave in sorted((opcoes.get('destinos') or {}).items()):
        if not chave:
            continue
        destino = destino_de(mapa, str(destino_id))
        acao = destino['acoes'].get(chave)
        if not acao:
            raise AcaoBgpNaoSuportada(f'{destino_id} não tem community-filter para a ação "{chave}".')
        if _e_global(destino) and not acao.get('circuitos'):
            raise AcaoBgpNaoSuportada(
                f'Nenhuma policy de saída referencia "{acao["filtro"]}" — marcar as rotas do '
                f'cliente com essa community global não produziria anúncio nenhum.'
            )
        if not _e_global(destino) and not destino['sessoes']:
            raise AcaoBgpNaoSuportada(
                f'O circuito {destino_id} não tem sessão BGP neste equipamento — '
                f'não há para onde reanunciar os prefixos do cliente.'
            )
        communities.append(acao['community'])
        destinos_escolhidos[str(destino_id)] = chave

    prefixos_por_familia = {}
    for familia in familias:
        crus = ((opcoes.get(familia) or {}).get('prefixos')) or []
        redes = []
        for cru in crus:
            texto = str(cru or '').strip()
            if not texto:
                continue
            try:
                rede = ipaddress.ip_network(texto, strict=False)
            except ValueError as e:
                raise AcaoBgpNaoSuportada(f'Prefixo do cliente inválido ({texto}): {e}')
            if (6 if rede.version == 6 else 4) != (6 if familia == 'v6' else 4):
                raise AcaoBgpNaoSuportada(f'O prefixo {texto} não é {familia.upper()}.')
            redes.append(rede)
        if not redes:
            raise AcaoBgpNaoSuportada(
                f'Informe os prefixos {familia.upper()} que o cliente pode anunciar — é a '
                f'prefix-list que a policy de entrada vai casar.'
            )
        prefixos_por_familia[familia] = redes

    policies_existentes = set(dados.get('community_nodes') or {}) | set(dados.get('policies') or {})
    listas_existentes = dados.get('prefix_lists') or {}
    comandos, policies = [], {}

    for familia in familias:
        # Sem lista de bogons aqui: a entrada do cliente casa a prefix-list dele
        # (só os blocos informados), então bogon nenhum chegaria ao node.
        full, cmd_full = _prefix_list_full_routing(dados, familia)
        comandos += cmd_full

        nome_pl = opcoes.get(f'prefix_list_{familia}') or f'PL-DOWNSTREAM-{nome}-{familia.upper()}'
        if nome_pl in listas_existentes:
            raise AcaoBgpNaoSuportada(
                f'Já existe uma prefix-list chamada "{nome_pl}" — ela pode estar em uso por '
                f'outra sessão. Escolha outro nome de cliente ou informe a lista a usar.'
            )
        for i, rede in enumerate(prefixos_por_familia[familia], start=1):
            # `greater-equal/less-equal` deixa o cliente desagregar até o
            # maior prefixo aceito (o /24 e /48 que o resto da internet aceita)
            # sem precisar de uma entrada por bloco.
            teto = max(rede.prefixlen, MAIOR_PREFIXO[familia])
            linha = (f'{_CMD_PREFIX_LIST[familia]} {nome_pl} index {i * 10} permit '
                     f'{rede.network_address} {rede.prefixlen}')
            if rede.prefixlen < teto:
                linha += f' greater-equal {rede.prefixlen} less-equal {teto}'
            comandos.append(linha)

        base = f'DOWNSTREAM-{nome}-{familia.upper()}'
        policies[f'{familia}_in'] = opcoes.get(f'policy_{familia}_in') or f'{base}-IN'
        policies[f'{familia}_out'] = opcoes.get(f'policy_{familia}_out') or f'{base}-OUT'
        for chave in (f'{familia}_in', f'{familia}_out'):
            if policies[chave] in policies_existentes:
                raise AcaoBgpNaoSuportada(
                    f'Já existe uma route-policy chamada "{policies[chave]}" — '
                    f'escolha outro nome de cliente.'
                )

        comandos += _bloco_policy_in(policies[f'{familia}_in'], familia, '', nome_pl,
                                     communities, local_preference=local_preference)
        comandos += [f'route-policy {policies[f"{familia}_out"]} permit node {NODE_LOCAL}',
                     f'{_CMD_IF_MATCH[familia]} {full}',
                     'quit',
                     f'route-policy {policies[f"{familia}_out"]} deny node {NODE_CATCHALL}',
                     'quit']

    comandos += _bloco_sessao(as_local, familias, peers, policies,
                              habilitar=bool(opcoes.get('habilitar', True)), fake_as=fake_as)
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
            # Policy local que existia vazia (só em `policies`, sem nenhum
            # `apply community`): o node passa a existir em `community_nodes`
            # agora, senão o painel ficaria mostrando "não anunciado" até o
            # próximo backup mesmo com o comando já aplicado na caixa.
            if _node_local_vazio(dados, linha['route_policy']) is None:
                return
            alvo = _node_local_vazio(dados, linha['route_policy'])
            nodes.setdefault(linha['route_policy'], []).append(alvo)
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

    elif tipo in ('criar_circuito_community', 'criar_downstream_community'):
        # Diferente de `provisionar_circuito` (que só registra os filtros), aqui
        # dá pra refletir o circuito INTEIRO: os nodes e as sessões foram
        # gerados por este módulo agora há pouco, então são conhecidos linha a
        # linha. Sem isso o slot recém-criado continuaria aparecendo como vago
        # até o próximo backup — e o operador aplicaria tudo de novo.
        _registrar_criacao_local(dados, tipo, alvo, params)


def _registrar_criacao_local(dados, tipo, alvo, params):
    """Reflete no snapshot uma sessão recém-criada (circuito de community ou
    downstream). Aproximação otimista, como o resto de `aplicar_efeito_local`:
    o próximo backup é quem confirma."""
    opcoes = params.get('opcoes') or {}
    sessoes = dados.setdefault('sessoes', [])
    as_local = (sessoes[0].get('as_local') if sessoes else '') or dados.get('as_local', '')
    peer_as = str(opcoes.get('peer_as') or '')
    nome = _RE_NOME_LIMPO.sub('-', str(opcoes.get('nome') or '').upper()).strip('-')
    if not nome or not peer_as:
        return

    familias = [f for f in ('v4', 'v6') if ((opcoes.get(f) or {}).get('peers'))]
    if tipo == 'criar_circuito_community':
        cid = _destino_dos_params(params) or (alvo or '')
        slot = slot_padrao(cid)
        if not slot:
            return
        grupo = str(opcoes.get('grupo') or slot['grupo'])
        asn = str(opcoes.get('asn_community') or ASN_COMMUNITY_PADRAO)
        prepend_as = str(opcoes.get('prepend_as') or as_local or '')
        tipo_circuito = TIPOS_POR_CHAVE[slot['tipo']]
        glob_id = f'{PREFIXO_DESTINO_GLOBAL}{tipo_circuito["glob_slug"]}'

        filtros = dados.setdefault('community_filters', {})
        for acao in ACOES_PROVISIONAVEIS:
            filtros.setdefault(f'{cid}-{acao["chave"]}', [{
                'index': 10, 'acao': 'permit',
                'valores': [community_de(asn, grupo, acao['chave'])],
            }])
        filtros.setdefault(glob_id, [{
            'index': 10, 'acao': 'permit',
            'valores': [f'{asn}:{tipo_circuito["glob_community"]}'],
        }])

        nodes = dados.setdefault('community_nodes', {})
        for familia in familias:
            base = f'AS{peer_as}-{nome}-{familia.upper()}'
            nome_out = opcoes.get(f'policy_{familia}_out') or f'{base}-OUT'
            if nome_out in nodes:
                continue
            lista = []
            for acao in ACOES_ANUNCIO_PADRAO:
                lista.append({
                    'policy': nome_out, 'node': acao['node'], 'acao': acao['policy'],
                    'community_filters': [f'{cid}-{acao["chave"]}'],
                    'apply_community': [], 'apply_community_extra': (
                        ['no-export'] if acao.get('apply') else []),
                    'prefix_lists': [], 'prepend_as': [prepend_as] * acao['prepend'],
                    'local_preference': None,
                })
            lista.append({
                'policy': nome_out, 'node': NODE_GLOBAL, 'acao': 'permit',
                'community_filters': [glob_id], 'apply_community': [],
                'apply_community_extra': [], 'prefix_lists': [],
                'prepend_as': [], 'local_preference': None,
            })
            nodes[nome_out] = sorted(lista, key=lambda n: n['node'])
            nodes.setdefault(f'{base}-IN', [{
                'policy': f'{base}-IN', 'node': NODE_LOCAL, 'acao': 'permit',
                'community_filters': [], 'apply_community': [community_de(asn, grupo, 'import-rr')],
                'apply_community_extra': [], 'prefix_lists': [], 'prepend_as': [],
                'local_preference': None,
            }])
        prefixo_policy = f'AS{peer_as}-{nome}'
    else:
        prefixo_policy = f'DOWNSTREAM-{nome}'
        policies = dados.setdefault('policies', {})
        listas = dados.setdefault('prefix_lists', {})
        for familia in familias:
            nome_pl = opcoes.get(f'prefix_list_{familia}') or f'PL-DOWNSTREAM-{nome}-{familia.upper()}'
            entradas = []
            for cru in ((opcoes.get(familia) or {}).get('prefixos')) or []:
                try:
                    rede = ipaddress.ip_network(str(cru).strip(), strict=False)
                except ValueError:
                    continue
                entradas.append({'acao': 'permit', 'prefixo': str(rede), 'index': 10,
                                 'len_min': rede.prefixlen,
                                 'len_max': max(rede.prefixlen, MAIOR_PREFIXO[familia])})
            if entradas:
                listas.setdefault(nome_pl, entradas)
            base = f'{prefixo_policy}-{familia.upper()}'
            policies.setdefault(f'{base}-IN', [{
                'ordem': NODE_LOCAL, 'prefix_lists': [nome_pl], 'acao': 'accept',
                'prepend': 0, 'extra': {'policy': f'{base}-IN', 'node': NODE_LOCAL,
                                        'nao_suportado': False},
            }])
            # A policy de saída de um cliente é o que `mapear_downstreams`
            # procura: um node que libera a tabela cheia.
            full, _ = _prefix_list_full_routing(dados, familia)
            policies.setdefault(f'{base}-OUT', [{
                'ordem': NODE_LOCAL, 'prefix_lists': [full], 'acao': 'accept',
                'prepend': 0, 'extra': {'policy': f'{base}-OUT', 'node': NODE_LOCAL,
                                        'nao_suportado': False},
            }])
            communities = []
            for destino_id, chave in sorted((opcoes.get('destinos') or {}).items()):
                if not chave:
                    continue
                try:
                    destino = destino_de(montar_mapa(dados), str(destino_id))
                except AcaoBgpNaoSuportada:
                    continue
                acao = destino['acoes'].get(chave)
                if acao:
                    communities.append(acao['community'])
            dados.setdefault('community_nodes', {}).setdefault(f'{base}-IN', [{
                'policy': f'{base}-IN', 'node': NODE_LOCAL, 'acao': 'permit',
                'community_filters': [], 'apply_community': communities,
                'apply_community_extra': [], 'prefix_lists': [nome_pl],
                'prepend_as': [], 'local_preference': None,
            }])

    ips_existentes = {s.get('peer_ip') for s in sessoes}
    for familia in familias:
        base = f'{prefixo_policy}-{familia.upper()}'
        for peer in ((opcoes.get(familia) or {}).get('peers')) or []:
            ip = str(peer.get('ip') or '').strip()
            if not ip or ip in ips_existentes:
                continue
            sessoes.append({
                'peer_ip': ip, 'nome': ip, 'peer_as': peer.get('peer_as') or peer_as,
                'as_local': as_local, 'equipamento': '', 'grupo': '',
                'descricao': peer.get('descricao') or '',
                'habilitada': bool(opcoes.get('habilitar', True)),
                'policy_in': opcoes.get(f'policy_{familia}_in') or f'{base}-IN',
                'policy_out': opcoes.get(f'policy_{familia}_out') or f'{base}-OUT',
                'fake_as': str(opcoes.get('fake_as') or ''),
                'prepend_as': str(opcoes.get('fake_as') or '') or as_local,
            })
            ips_existentes.add(ip)

