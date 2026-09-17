"""
Composição do HLD (Arquitetura de Rede ISP — Convenção Oficial) a partir da
convenção estruturada. Mesmo contrato de `composicao.py`: seções HTML sem
numeração, tudo com escape; communities formatadas conforme o ASN.
"""
from django.utils.html import escape

from . import convencao as cv
from .composicao import callout, e, h3, p, tabela, ul


def _c(conv, valor):
    return e(cv.faixa_community(conv, valor))


def s_sumario(conv):
    linhas = [[f'<strong>{e(s.get("camada"))}</strong>', e(s.get('nome'))] for s in conv.get('servicos', [])]
    linhas.append(['<strong>Transportes</strong>', e('L2VPN e L3VPN dedicadas')])
    partes = [p('Arquitetura orientada a serviços e independente de fornecedores.'),
              tabela(['Camada', 'Serviço'], linhas)]
    if conv.get('regra_de_ouro'):
        partes.append(callout('REGRA DE OURO.', e(conv['regra_de_ouro'])))
    avisos = cv.validar(conv)
    if avisos:
        partes.append(h3('Validações técnicas da convenção'))
        partes.append(callout('Atenção.', 'pontos que precisam de decisão antes da implantação:', 'risco'))
        partes.append(ul([escape(a) for a in avisos]))
    return ''.join(partes)


def s_nomenclatura(conv):
    return tabela(['Objeto', 'Padrão', 'Exemplo'],
                  [[e(n.get('objeto')), f'<code>{e(n.get("padrao"))}</code>', e(n.get('exemplo'))]
                   for n in conv.get('nomenclatura', [])])


def s_vrfs(conv):
    linhas = [[f'<strong>{e(s.get("id"))}</strong>', e(s.get('nome')), e(cv.rt(conv, s.get('rt', '')))]
              for s in conv.get('servicos', [])]
    for t in conv.get('transportes', []):
        ini, _, fim = str(t.get('rts', '')).partition('-')
        linhas.append([f'<strong>{e(t.get("ids"))}</strong>', e(t.get('tipo')),
                       e(f'{cv.rt(conv, ini)}-{fim}' if fim else cv.rt(conv, ini))])
    return tabela(['ID', 'Serviço', 'RT privado'], linhas) + callout(
        'RD.', f'RD = <code>{e(conv.get("rd_padrao"))}</code>. A mesma VRF usa RD distinto por PE e RT privado '
        'comum em todos os PEs.')


def s_rts(conv):
    return tabela([f'RT {conv.get("asn", "")}:', 'Nome / direção'],
                  [[f'<strong>{e(r.get("valor"))}</strong>', e(r.get('nome'))]
                   for r in conv.get('rts_compartilhados', [])]) + callout(
        'DEFAULT DENY.', 'Todo leak exige RT, prefix-list, route-policy, caminho de retorno e inventário. '
        'Os RTs de IX-ROUTES e IX-EXPORT são direcionais e não se substituem.', 'risco')


def s_communities_internas(conv):
    formato = cv.formato_community(conv.get('asn'))
    nota = (p(f'Formato: large community <code>{e(conv.get("asn"))}:&lt;valor&gt;:0</code> (RFC 8092), porque o '
              'ASN tem 4 bytes e não cabe em community padrão.') if formato == cv.FORMATO_LARGE else
            p(f'Formato: community padrão <code>{e(conv.get("asn"))}:&lt;valor&gt;</code> (RFC 1997).'))
    return nota + tabela(['Faixa / community', 'Função'],
                         [[_c(conv, x.get('faixa')), e(x.get('funcao'))] for x in conv.get('communities_internas', [])]) + \
        h3('Classes de Local Preference') + \
        tabela(['LP', 'Classe', 'Community'],
               [[f'<strong>{e(x.get("lp"))}</strong>', e(x.get('classe')), _c(conv, x.get('community', ''))]
                for x in conv.get('lp_classes', [])])


def s_communities_publicas(conv):
    return tabela(['Bloco', 'Ação'], [[_c(conv, x.get('faixa')), e(x.get('acao'))]
                                     for x in conv.get('communities_publicas', [])]) + \
        ul([e(r) for r in conv.get('regras_communities', [])])


def s_ix(conv):
    serv = cv.servico_por_nome(conv, 'VRF-IX') or {}
    rts = {r.get('nome', '').split(':')[0]: r.get('valor') for r in conv.get('rts_compartilhados', [])}
    lp_ix = cv.lp_da_classe(conv, 'IX')
    com_lp = next((x.get('community') for x in conv.get('lp_classes', []) if x.get('classe') == 'IX'), '')
    decisoes = [
        ['VRF', e(serv.get('nome', 'VRF-IX'))],
        ['Service ID', e(serv.get('id'))],
        ['RD', e(f'<MPLS-LSR-ID>:{serv.get("id", "")}')],
        ['RT privado', e(cv.rt(conv, serv.get('rt', '')))],
        ['Rotas IX para consumidores', e(cv.rt(conv, rts.get('IX-ROUTES', '')))],
        ['Prefixos de consumidores para IX', e(cv.rt(conv, rts.get('IX-EXPORT', '')))],
        ['Classificação', e(f'{cv.community(conv, 21004)} IX')],
        ['LP', e(f'{lp_ix} / {cv.community(conv, com_lp)}' if lp_ix else '—')],
    ]
    comunidades = [
        [_c(conv, '20200') + ' … ' + _c(conv, '20290'), e('IX01 … IX10')],
        [_c(conv, '22500') + ' … ' + _c(conv, '22590'), e('EXPORT-IX01 … ALL-IX')],
        [_c(conv, '30700') + ' … ' + _c(conv, '30790'), e('ANNOUNCE-IX01 … ALL-IX')],
        [_c(conv, '30800') + ' … ' + _c(conv, '30890'), e('NO-IX01 … ALL-IX')],
        [_c(conv, '30900-30905'), e('IX01 PREPEND0-5')],
        [_c(conv, '30910-30915'), e('IX02 PREPEND0-5')],
    ]
    return ''.join([
        p('Tabela isolada e produto entregável separadamente.'),
        tabela(['Item', 'Decisão'], decisoes),
        h3('Communities do IX'), tabela(['Community', 'Ação'], comunidades),
        h3('Produtos e isolamento'),
        tabela(['Produto', 'Terminação', 'Entrega'],
               [[e(x.get('produto')), e(x.get('terminacao')), e(x.get('entrega'))] for x in conv.get('ix_produtos', [])]),
        callout('ISOLAMENTO.', e(conv.get('ix_isolamento', '')), 'risco'),
    ])


def s_seguranca(conv):
    linhas = []
    for x in conv.get('seguranca', []):
        padrao = x.get('padrao', '')
        if x.get('item', '').upper() == 'BLACKHOLE':
            padrao = f'{cv.community(conv, 666)} interno; {padrao}'
        linhas.append([f'<strong>{e(x.get("item"))}</strong>', e(padrao)])
    return tabela(['Item', 'Padrão'], linhas)


def s_uplinks(conv):
    return ul([e(x) for x in conv.get('uplinks', [])])


def s_bng(conv):
    partes = [tabela(['Estado', 'Comportamento'], [[f'<strong>{e(x.get("estado"))}</strong>', e(x.get('comportamento'))]
                                                  for x in conv.get('bng_estados', [])])]
    partes.append(callout('ACESSO.', e(conv.get('acesso_regra', ''))))
    locais = [['BNG01', e(conv.get('bng01'))], ['BNG02', e(conv.get('bng02'))], ['CGNAT', e(conv.get('cgnat_modo'))]]
    partes.append(tabela(['Função', 'Local / modo'], locais))
    partes.append(h3('Blocos CGNAT'))
    partes.append(tabela(['Bloco', 'Uso'], [[f'<code>{e(x.get("bloco"))}</code>', e(x.get('uso'))]
                                           for x in conv.get('pools_cgnat', [])]))
    return ''.join(partes)


def s_ipv4(conv):
    return tabela(['Bloco', 'Uso'], [[f'<code>{e(x.get("bloco"))}</code>', e(x.get('uso'))] for x in conv.get('ipv4', [])]) + \
        h3('Loopbacks de POP correlacionadas') + \
        tabela(['Loopback POP', 'Rede interna', 'CGNAT MikroTik'],
               [[e(x.get('loopback')), e(x.get('rede')), e(x.get('cgnat'))] for x in conv.get('loopbacks_pop', [])])


def s_ipv6(conv):
    return tabela(['Bloco', 'Uso'], [[f'<code>{e(x.get("bloco"))}</code>', e(x.get('uso'))] for x in conv.get('ipv6', [])]) + \
        tabela(['Tipo', 'Prefixo'], [[e(x.get('tipo')), e(x.get('prefixo'))] for x in conv.get('ipv6_tamanhos', [])])


def s_rr(conv):
    return ul([
        f'RR01 no {e(conv.get("rr01") or "(a definir)")}; RR02 no {e(conv.get("rr02") or "(a definir)")}.',
        'Cada PE/BNG fecha com os dois RRs; PEs não precisam de MP-BGP direto entre si.',
        'RR01 e RR02 fecham iBGP normal, usam o mesmo cluster ID e router IDs individuais.',
        f'Famílias: {e(conv.get("familias_mpbgp"))}; EVPN {e(str(conv.get("evpn", "")).lower())}.',
    ])


def s_underlay(conv):
    linhas = [
        ('OSPF', f'Processo {conv.get("ospf_processo")}, área {conv.get("ospf_area")}'),
        ('Router ID', conv.get('router_id')),
        ('P2P', f'IPv4 /{conv.get("p2p_v4")}, IPv6 /{conv.get("p2p_v6")}'),
        ('Labels', conv.get('labels')),
        ('LDP/IGP sync', conv.get('ldp_sync')),
        ('BFD', f'{conv.get("bfd_tx")}/{conv.get("bfd_rx")} ms, multiplier {conv.get("bfd_mult")}'),
        ('ECMP', conv.get('ecmp')),
        ('RSVP-TE', conv.get('rsvp')),
        ('IGP', conv.get('igp_escopo')),
        ('MTU do backbone', conv.get('mtu_alvo') or 'Definida no LLD (Ethernet, VLAN/QinQ, labels, L2VPN e PPPoE)'),
        ('MPLS MTU', conv.get('mpls_mtu_alvo') or 'Calculada no LLD pela pilha máxima de labels'),
    ]
    return tabela(['Item', 'Padrão'], [[f'<strong>{e(a)}</strong>', e(b)] for a, b in linhas])


def s_transportes(conv):
    linhas = []
    for t in conv.get('transportes', []):
        linhas.append([f'<strong>{e(t.get("tipo"))}</strong>',
                       e(f'{t.get("nome")}, IDs {t.get("ids")}, RT {cv.rt(conv, t.get("rts", ""))}, '
                         f'endpoint {t.get("endpoint")}')])
    linhas.append(['<strong>RD</strong>', e(conv.get('rd_padrao'))])
    return tabela(['Serviço', 'Padrão'], linhas) + callout(
        'PAPÉIS.', 'CE é o equipamento do cliente e não precisa de MPLS. PE termina o serviço. P transporta labels.')


def s_governanca(conv):
    return ul([e(x.get('regra')) for x in conv.get('governanca', [])])


def s_catalogo(conv):
    linhas = [[f'<strong>{e(s.get("id"))}</strong>', e(f'{s.get("nome")} | {cv.rt(conv, s.get("rt", ""))}')]
              for s in conv.get('servicos', [])]
    for t in conv.get('transportes', []):
        linhas.append([f'<strong>{e(t.get("ids"))}</strong>', e(f'{t.get("tipo")} | {cv.rt(conv, t.get("rts", ""))}')])
    loop_core = next((x.get('bloco') for x in conv.get('ipv4', []) if 'core' in x.get('uso', '').lower()), '')
    loop_pop = next((x.get('bloco') for x in conv.get('ipv4', []) if 'pop' in x.get('uso', '').lower()), '')
    linhas += [
        ['<strong>RD</strong>', e(conv.get('rd_padrao'))],
        ['<strong>Communities internas</strong>', _c(conv, '20000-29999')],
        ['<strong>Communities públicas</strong>', _c(conv, '30000-39999')],
        ['<strong>BLACKHOLE</strong>', e(cv.community(conv, 666))],
        ['<strong>Core loopbacks</strong>', e(loop_core)],
        ['<strong>POP loopbacks</strong>', e(loop_pop)],
        ['<strong>P2P</strong>', e(f'IPv4 /{conv.get("p2p_v4")} | IPv6 /{conv.get("p2p_v6")}')],
    ]
    partes = [tabela(['Domínio', 'Convenção'], linhas)]
    if conv.get('frase'):
        partes.append(callout('ARQUITETURA EM UMA FRASE.', e(conv['frase']), 'status'))
    return ''.join(partes)


SECOES = [
    ('hld_sumario', 'Sumário executivo', s_sumario),
    ('hld_nomenclatura', 'Nomenclatura', s_nomenclatura),
    ('hld_vrfs', 'VRFs, Service IDs, RD e RT', s_vrfs),
    ('hld_rts', 'RTs compartilhados', s_rts),
    ('hld_comm_int', 'Communities internas', s_communities_internas),
    ('hld_comm_pub', 'Communities públicas', s_communities_publicas),
    ('hld_ix', 'Internet Exchange (IX)', s_ix),
    ('hld_seguranca', 'Blackhole, fullbogons e segurança', s_seguranca),
    ('hld_uplinks', 'Uplinks e Content', s_uplinks),
    ('hld_bng', 'BNG, acesso e CGNAT', s_bng),
    ('hld_ipv4', 'Endereçamento IPv4', s_ipv4),
    ('hld_ipv6', 'Endereçamento IPv6', s_ipv6),
    ('hld_rr', 'Route Reflectors e MP-BGP', s_rr),
    ('hld_underlay', 'Underlay OSPF/MPLS', s_underlay),
    ('hld_transportes', 'Transportes L2VPN e L3VPN', s_transportes),
    ('hld_governanca', 'Governança e implantação', s_governanca),
    ('hld_catalogo', 'Catálogo executivo', s_catalogo),
]

CAMPOS_METADADOS = [
    ('documento', 'Documento'), ('versao', 'Versão'), ('status', 'Status'), ('asn', 'ASN'),
    ('responsavel', 'Responsável'), ('classificacao', 'Classificação'),
]


def metadados_padrao(conv, empresa, responsavel=''):
    return {
        'empresa': empresa,
        'titulo': 'Arquitetura de Rede ISP',
        'subtitulo': f'High Level Design — Convenção Oficial · AS{conv.get("asn", "")} – {empresa}'.strip(),
        'documento': f'HLD - Arquitetura ISP {empresa}',
        'versao': '1.0',
        'status': 'Rascunho',
        'asn': conv.get('asn', ''),
        'responsavel': responsavel,
        'classificacao': 'Uso interno',
        'principio_titulo': 'ESCOPO.',
        'principio': 'O HLD contém apenas decisões aprovadas. Comandos por release, execução, testes e rollback '
                     'pertencem ao LLD e ao plano de mudança.',
    }
