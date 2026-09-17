"""
Convenção de rede (HLD) estruturada e reutilizável.

O HLD da Startnet virou o modelo padrão: todas as identidades são derivadas
do ASN e dos prefixos de cada cliente, então o mesmo catálogo serve para
qualquer provedor. A convenção fica em `DocumentoRede.dados['convencao']`
(documento tipo `hld`) e é a régua que o Change Plan TO-BE usa para comparar
o AS-IS.

Communities: com ASN de 4 bytes (> 65535) a community padrão `ASN:valor`
(RFC 1997) não existe — o formato passa a ser large community (RFC 8092)
`ASN:valor:0`. Route targets continuam `ASN:valor` (extended community
AS4 com valor de 16 bits), válidos enquanto o valor couber em 65535.
"""
import copy
import ipaddress
import re

FORMATO_STANDARD = 'standard'
FORMATO_LARGE = 'large'

# Seções editáveis no formulário: (chave, título, colunas[(campo, rótulo)])
TABELAS = [
    ('servicos', 'VRFs e Service IDs', [('id', 'Service ID'), ('nome', 'Serviço / VRF'), ('camada', 'Camada'), ('rt', 'RT (valor)')]),
    ('transportes', 'Transportes L2VPN/L3VPN', [('tipo', 'Tipo'), ('ids', 'Faixa de IDs'), ('rts', 'Faixa de RT'), ('nome', 'Nomenclatura'), ('endpoint', 'Endpoint')]),
    ('rts_compartilhados', 'RTs compartilhados', [('valor', 'RT (valor)'), ('nome', 'Nome / direção')]),
    ('communities_internas', 'Communities internas', [('faixa', 'Faixa / valor'), ('funcao', 'Função')]),
    ('lp_classes', 'Classes de Local Preference', [('lp', 'LP'), ('classe', 'Classe'), ('community', 'Community (valor)')]),
    ('communities_publicas', 'Communities públicas', [('faixa', 'Bloco'), ('acao', 'Ação')]),
    ('seguranca', 'Blackhole, fullbogons e segurança', [('item', 'Item'), ('padrao', 'Padrão')]),
    ('nomenclatura', 'Nomenclatura', [('objeto', 'Objeto'), ('padrao', 'Padrão'), ('exemplo', 'Exemplo')]),
    ('bng_estados', 'Estados de assinante', [('estado', 'Estado'), ('comportamento', 'Comportamento')]),
    ('pools_cgnat', 'Blocos CGNAT (RFC 6598)', [('bloco', 'Bloco'), ('uso', 'Uso')]),
    ('ipv4', 'Endereçamento IPv4 de infraestrutura', [('bloco', 'Bloco'), ('uso', 'Uso')]),
    ('loopbacks_pop', 'Loopbacks de POP correlacionadas', [('loopback', 'Loopback'), ('rede', 'Rede interna'), ('cgnat', 'CGNAT MikroTik')]),
    ('ipv6', 'Endereçamento IPv6 de infraestrutura', [('bloco', 'Bloco'), ('uso', 'Uso')]),
    ('ipv6_tamanhos', 'Tamanhos IPv6', [('tipo', 'Tipo'), ('prefixo', 'Prefixo')]),
    ('ix_produtos', 'Produtos de IX', [('produto', 'Produto'), ('terminacao', 'Terminação'), ('entrega', 'Entrega')]),
    ('governanca', 'Governança', [('regra', 'Regra')]),
]

# Campos simples: (chave, rótulo, dica)
CAMPOS = [
    ('asn', 'ASN', 'ex.: 272648'),
    ('prefixo_v6', 'Prefixo IPv6 do provedor', 'ex.: 2804:8680::/32'),
    ('bloco_v6_infra', 'Bloco IPv6 de infraestrutura (/40)', 'ex.: 2804:8680:ff00::/40'),
    ('rr01', 'RR01 (local)', 'ex.: NE8000 de Juína'),
    ('rr02', 'RR02 (local)', 'ex.: NE8000 de Alta Floresta'),
    ('familias_mpbgp', 'Famílias MP-BGP', 'VPNv4, VPNv6, L2VPN'),
    ('evpn', 'EVPN', 'Reservada'),
    ('bng01', 'BNG01 (local)', ''),
    ('bng02', 'BNG02 (local)', ''),
    ('cgnat_modo', 'CGNAT', 'ex.: card de serviço nos BNGs'),
    ('ospf_processo', 'OSPF — processo', '10'),
    ('ospf_area', 'OSPF — área', '0.0.0.0'),
    ('router_id', 'Router ID', 'Igual ao MPLS LSR-ID'),
    ('p2p_v4', 'P2P IPv4 (máscara)', '30'),
    ('p2p_v6', 'P2P IPv6 (máscara)', '127'),
    ('labels', 'Distribuição de labels', 'LDP'),
    ('ldp_sync', 'LDP/IGP sync', 'Obrigatório'),
    ('bfd_tx', 'BFD min-tx (ms)', '300'),
    ('bfd_rx', 'BFD min-rx (ms)', '300'),
    ('bfd_mult', 'BFD multiplicador', '3'),
    ('ecmp', 'ECMP', 'Entre caminhos equivalentes'),
    ('rsvp', 'RSVP-TE', 'Legado controlado'),
    ('igp_escopo', 'Escopo do IGP', 'Somente infraestrutura'),
    ('mtu_alvo', 'MTU alvo do backbone', 'vazio = decisão pendente do LLD'),
    ('mpls_mtu_alvo', 'MPLS MTU alvo', 'vazio = calcular no LLD'),
    ('lp_rtbh', 'LP do RTBH', '2000'),
    ('lp_fullbogons', 'LP de fullbogons', '1900'),
    ('rd_padrao', 'Padrão de RD', '<MPLS-LSR-ID>:<SERVICE-ID>'),
    ('regra_de_ouro', 'Regra de ouro', ''),
    ('frase', 'Arquitetura em uma frase', ''),
]


def formato_community(asn):
    try:
        return FORMATO_LARGE if int(asn) > 65535 else FORMATO_STANDARD
    except (TypeError, ValueError):
        return FORMATO_STANDARD


def community(conv, valor):
    """Valor da convenção → community no formato certo para o ASN."""
    asn = conv.get('asn', '')
    valor = str(valor).strip()
    if not valor:
        return ''
    if formato_community(asn) == FORMATO_LARGE:
        return f'{asn}:{valor}:0'
    return f'{asn}:{valor}'


def faixa_community(conv, faixa):
    """'20000-20090' → '272648:20000:0 – 272648:20090:0'."""
    partes = [p.strip() for p in re.split(r'\s*[-–]\s*', str(faixa)) if p.strip()]
    if len(partes) == 2 and all(p.isdigit() for p in partes):
        return f'{community(conv, partes[0])} a {community(conv, partes[1])}'
    return community(conv, faixa) if str(faixa).strip().isdigit() else str(faixa)


def rt(conv, valor):
    asn = conv.get('asn', '')
    return f'{asn}:{valor}' if valor != '' else ''


def rt_valido(conv, valor):
    try:
        v = int(str(valor).split('-')[-1])
        asn = int(conv.get('asn') or 0)
    except ValueError:
        return True
    # tipo 0x02 (AS4:16 bits) quando ASN > 65535; tipo 0x00 (AS2:32 bits) senão
    return v <= 65535 if asn > 65535 else v <= 4294967295


def _v6_infra(prefixo_v6):
    """Primeiro /40 do fim do bloco do provedor (ff00::/40 num /32)."""
    try:
        rede = ipaddress.ip_network(prefixo_v6, strict=False)
    except ValueError:
        return None
    if rede.version != 6 or rede.prefixlen > 40:
        return None
    base = int(rede.network_address) | (0xff00 << (128 - 48))
    return ipaddress.ip_network((base, 40), strict=False) if rede.prefixlen <= 32 else None


def convencao_padrao(asn='', prefixo_v6='', rr01='', rr02='', bng01='', bng02=''):
    """Modelo ISP padrão (derivado do HLD Startnet v1.0), parametrizado."""
    infra6 = _v6_infra(prefixo_v6) if prefixo_v6 else None
    v6 = []
    if infra6:
        base = int(infra6.network_address)
        usos = ['Interconexões externas', 'Backbone MPLS', 'Serviços', 'Loopbacks', 'Gerência',
                'CGNAT técnico', 'RPKI e automação', 'BNG técnico']
        for i, uso in enumerate(usos):
            v6.append({'bloco': str(ipaddress.ip_network((base + (i << 80), 48))), 'uso': uso})
        v6.append({'bloco': f'Demais /48 do {infra6}', 'uso': 'Reserva'})
    else:
        v6 = [{'bloco': '<prefixo>:ff00::/48 … ff07::/48', 'uso': 'Definir a partir do /32 do provedor'}]

    return {
        'versao_modelo': 1,
        'asn': str(asn or ''),
        'prefixo_v6': prefixo_v6,
        'bloco_v6_infra': str(infra6) if infra6 else '',
        'rr01': rr01, 'rr02': rr02,
        'familias_mpbgp': 'VPNv4, VPNv6, L2VPN',
        'evpn': 'Reservada',
        'bng01': bng01, 'bng02': bng02,
        'cgnat_modo': 'Card de serviço nos BNGs centrais; elementos externos saem da arquitetura permanente',
        'ospf_processo': '10', 'ospf_area': '0.0.0.0', 'router_id': 'Igual ao MPLS LSR-ID',
        'p2p_v4': '30', 'p2p_v6': '127', 'labels': 'LDP', 'ldp_sync': 'Obrigatório',
        'bfd_tx': '300', 'bfd_rx': '300', 'bfd_mult': '3',
        'ecmp': 'Entre caminhos equivalentes', 'rsvp': 'Legado controlado',
        'igp_escopo': 'Somente infraestrutura, sem redistribuição genérica',
        'mtu_alvo': '', 'mpls_mtu_alvo': '',
        'lp_rtbh': '2000', 'lp_fullbogons': '1900',
        'rd_padrao': '<MPLS-LSR-ID>:<SERVICE-ID>',
        'regra_de_ouro': 'VRF representa serviço. RD identifica PE e serviço. RT representa participação ou '
                         'compartilhamento. Community representa origem, estado ou intenção de política.',
        'frase': 'O backbone transporta; as VRFs isolam; os RTs compartilham somente o necessário; as communities '
                 'expressam origem e intenção; e o inventário mantém toda identidade técnica rastreável.',
        'servicos': [
            {'id': '1000', 'nome': 'VRF-INTERNET', 'camada': 'Borda', 'rt': '11000'},
            {'id': '1100', 'nome': 'VRF-ACCESS', 'camada': 'Acesso', 'rt': '11100'},
            {'id': '1200', 'nome': 'VRF-TRANSIT', 'camada': 'Trânsito', 'rt': '11200'},
            {'id': '1300', 'nome': 'VRF-BUSINESS', 'camada': 'Corporativo', 'rt': '11300'},
            {'id': '1400', 'nome': 'VRF-IX', 'camada': 'Internet Exchange', 'rt': '11400'},
            {'id': '2000', 'nome': 'VRF-CONTENT', 'camada': 'Conteúdo', 'rt': '12000'},
            {'id': '3000', 'nome': 'VRF-INFRA', 'camada': 'Infraestrutura', 'rt': '13000'},
            {'id': '4000', 'nome': 'VRF-CGNAT', 'camada': 'CGNAT', 'rt': '14000'},
            {'id': '5000', 'nome': 'VRF-SERVICES', 'camada': 'Serviços', 'rt': '15000'},
        ],
        'transportes': [
            {'tipo': 'L3VPN', 'ids': '6001-6999', 'rts': '16001-16999', 'nome': 'TR-<CONTRATANTE>-L3-<SEQ>',
             'endpoint': '...-<POP>-CE<NN>'},
            {'tipo': 'L2VPN', 'ids': '7001-7999', 'rts': '17001-17999', 'nome': 'TR-<CONTRATANTE>-L2-<SEQ>',
             'endpoint': '...-<POP>-AC<NN>'},
        ],
        'rts_compartilhados': [
            {'valor': v, 'nome': n} for v, n in [
                ('19000', 'DNS'), ('19001', 'NTP'), ('19002', 'RADIUS'), ('19010', 'CONTENT'),
                ('19011', 'INTERNET-V4'), ('19012', 'INTERNET-V6'), ('19020', 'CGNAT-INSIDE'),
                ('19021', 'CGNAT-OUTSIDE'), ('19030', 'MGMT'), ('19040', 'PORTAL'),
                ('19050', 'BUSINESS-INTERNET'), ('19060', 'TRANSIT-INTERNET'),
                ('19070', 'IX-ROUTES: IX para consumidores'), ('19071', 'IX-EXPORT: consumidores para IX'),
            ]
        ],
        'communities_internas': [
            {'faixa': f, 'funcao': n} for f, n in [
                ('20000-20090', 'UPLINK01-10'), ('20100-20190', 'CONTENT01-10'), ('20200-20290', 'IX01-10'),
                ('21000', 'INTERNAL'), ('21001', 'CUSTOMER'), ('21002', 'DOWNSTREAM'), ('21003', 'UPSTREAM'),
                ('21004', 'IX'), ('21110', 'FULLBOGONS'), ('22000-22090', 'Classes de Local Preference'),
                ('22500-22590', 'EXPORT-IX01-10 / EXPORT-ALL-IX'),
            ]
        ],
        'lp_classes': [
            {'lp': lp, 'classe': c, 'community': str(22000 + i * 10)} for i, (lp, c) in enumerate([
                ('1000', 'LOCAL'), ('900', 'CUSTOMER/DOWNSTREAM'), ('800', 'CONTENT'), ('700', 'IX'),
                ('600', 'PRIVATE-PEERING'), ('500', 'UPSTREAM'), ('400', 'DE-PREFER'), ('300', 'BACKUP'),
                ('200', 'FALLBACK'), ('100', 'LAST-RESORT'),
            ])
        ],
        'communities_publicas': [
            {'faixa': f, 'acao': a} for f, a in [
                ('30000-30099', 'ANNOUNCE-UPLINK'), ('30100-30199', 'NO-UPLINK'),
                ('30200-30299', 'PREPEND por UPLINK'), ('30300-30399', 'PREPEND por CONTENT'),
                ('30400-30499', 'NO-EXPORT por UPLINK'), ('30500-30599', 'ANNOUNCE-CONTENT'),
                ('30600-30699', 'NO-CONTENT'), ('30700-30799', 'ANNOUNCE-IX'), ('30800-30899', 'NO-IX'),
                ('30900-30999', 'PREPEND por IX'), ('31000-39999', 'Reserva'),
            ]
        ],
        'regras_communities': [
            'NO específico vence ANNOUNCE específico.',
            'ANNOUNCE autoriza o destino; PREPEND e NO-EXPORT apenas modificam anúncio autorizado.',
            'O último dígito representa prepend 0-5.',
            'Communities desconhecidas são removidas; deny final é obrigatório.',
        ],
        'seguranca': [
            {'item': i, 'padrao': p} for i, p in [
                ('BLACKHOLE', '666 interno e externo; para operadoras, 65535:666 (RFC 7999)'),
                ('LP RTBH', '2000'),
                ('Fullbogons', 'community 21110, LP 1900, receive-only'),
                ('RPKI', 'INVALID rejeitado'),
                ('IPv4 normal', 'até /24'), ('IPv6 normal', 'até /48'),
                ('RTBH', 'IPv4 até /32; IPv6 até /128'),
                ('Max-prefix', 'Obrigatório'),
                ('Export', 'Somente prefixos próprios, clientes diretos e RTBH autorizado'),
            ]
        ],
        'nomenclatura': [
            {'objeto': o, 'padrao': p, 'exemplo': e} for o, p, e in [
                ('VRF', 'VRF-<SERVICE>', 'VRF-IX'),
                ('Route-policy', 'RP-AS<ASN>-<NAME>-V4/V6-IN/OUT', 'RP-AS52900-AVATO-V4-IN'),
                ('Prefix-list', 'PL-<NAME>-V4/V6', 'PL-BOGONS-V6'),
                ('Community-filter', 'CF-<VALUE>-<NAME>', 'CF-20200-IX01'),
                ('L3VPN', 'TR-<CONTRATANTE>-L3-<SEQ>', 'TR-CLIENTE-L3-001'),
                ('L2VPN', 'TR-<CONTRATANTE>-L2-<SEQ>', 'TR-CLIENTE-L2-001'),
                ('Endpoint L3', '...-<POP>-CE<NN>', 'TR-CLIENTE-L3-001-POP-CE01'),
                ('Endpoint L2', '...-<POP>-AC<NN>', 'TR-CLIENTE-L2-001-POP-AC01'),
            ]
        ],
        'bng_estados': [
            {'estado': 'ACTIVE', 'comportamento': 'Normal'},
            {'estado': 'NOTICE', 'comportamento': 'Normal com aviso'},
            {'estado': 'RESTRICTED', 'comportamento': 'Walled garden IPv4/IPv6'},
            {'estado': 'PENDING', 'comportamento': 'Ativação e portal'},
        ],
        'acesso_regra': 'IPoE e PPPoE, B2C ou B2B, terminam diretamente na VRF-ACCESS. Não existem Virtual System, '
                        'VRF-BLOCK ou VRF-PORTAL.',
        'pools_cgnat': [
            {'bloco': '100.64.0.0/12', 'uso': 'BNG01'},
            {'bloco': '100.80.0.0/12', 'uso': 'BNG02'},
            {'bloco': '100.96.0.0/12', 'uso': 'BNG03'},
            {'bloco': '100.112.0.0/13', 'uso': 'Reserva'},
            {'bloco': '100.120.0.0/14', 'uso': 'Reserva'},
            {'bloco': '100.124.0.0/14', 'uso': 'BRAS MikroTik dos POPs pequenos'},
        ],
        'ipv4': [
            {'bloco': '198.18.0.0/20', 'uso': 'Interconexões externas /30'},
            {'bloco': '198.18.16.0/20', 'uso': 'Backbone OSPF/MPLS /30'},
            {'bloco': '198.18.32.0/20', 'uso': 'Serviços técnicos'},
            {'bloco': '198.18.48.0-198.18.247.255', 'uso': 'Reserva'},
            {'bloco': '198.18.248.0/22', 'uso': 'Loopbacks core, /32 sequencial, .0 e .255 utilizáveis'},
            {'bloco': '198.18.252.0/22', 'uso': 'Loopbacks de POP correlacionadas'},
        ],
        'loopbacks_pop': [
            {'loopback': f'198.18.{252 + i}.ID/32', 'rede': f'172.{28 + i}.ID.0/24', 'cgnat': f'100.{124 + i}.ID.0/24'}
            for i in range(4)
        ],
        'ipv6': v6,
        'ipv6_tamanhos': [
            {'tipo': 'P2P', 'prefixo': '/127'}, {'tipo': 'Loopback', 'prefixo': '/128'},
            {'tipo': 'LAN', 'prefixo': '/64'}, {'tipo': 'PD padrão', 'prefixo': '/56'},
            {'tipo': 'PD avançado', 'prefixo': '/48'},
        ],
        'ix_produtos': [
            {'produto': 'Internet + IX', 'terminacao': 'VRF-TRANSIT', 'entrega': 'Rotas Internet e IX previstas no contrato.'},
            {'produto': 'IX dedicado', 'terminacao': 'VRF-IX', 'entrega': 'Somente rotas classificadas como IX.'},
            {'produto': 'Peering do provedor', 'terminacao': 'VRF-IX', 'entrega': 'Route servers e bilaterais autorizados.'},
        ],
        'ix_isolamento': 'Trânsito lateral entre downstreams é negado. Upstreams, conteúdo direto, fullbogons, '
                         'infraestrutura, CGNAT, ACCESS e VPNs privadas não são exportados ao IX.',
        'uplinks': [
            'UPLINK01-10 são identidades lógicas estáveis, independentes de operadora, ASN, porta e PE.',
            'Todos os upstreams usam LP 500 por padrão; ajustes são seletivos.',
            'CONTENT01-10 representa o peer ou grupo BGP real.',
            'Troca de fornecedor no mesmo papel lógico preserva o ID; novo circuito independente recebe novo ID.',
        ],
        'governanca': [
            {'regra': r} for r in [
                'IPAM/inventário é a fonte única de verdade para IDs, endereços, peers, contratos e policies.',
                'Criar objetos novos em paralelo; migrar um serviço ou equipamento por vez.',
                'Validar RIB, FIB, labels, MP-BGP, BNG, CGNAT, AAA, IPv6 e retorno.',
                'Toda exceção exige motivo, responsável, data, impacto e critério de remoção.',
            ]
        ],
    }


def sugerir_parametros(modelo):
    """Parâmetros do modelo a partir do AS-IS analisado (quando houver)."""
    params = {'asn': modelo.get('asn', '')}
    v6 = [n for e in modelo.get('huaweis', []) for n in (e['extr'].get('bgp') or {}).get('networks', [])
          if ':' in n['prefixo'] and n['prefixo'].endswith('/32')]
    if v6:
        params['prefixo_v6'] = v6[0]['prefixo']
    else:
        for b in modelo.get('blocos_ip', []):
            if ':' in (b.get('bloco') or ''):
                params['prefixo_v6'] = b['bloco']
                break
    from .tobe import rrs_e_bngs_sugeridos
    nomes_rr, nomes_bng = rrs_e_bngs_sugeridos(modelo)
    por_nome = {e['nome_exibicao']: e for e in modelo.get('equipamentos', [])}

    def rotulo(nome):
        e = por_nome[nome]
        return f'{e.get("modelo") or "Roteador"} de {e.get("pop") or nome}'
    rrs = [rotulo(n) for n in nomes_rr]
    centrais = [rotulo(n) for n in nomes_bng]
    for i, chave in enumerate(('rr01', 'rr02')):
        if len(rrs) > i:
            params[chave] = rrs[i]
    for i, chave in enumerate(('bng01', 'bng02')):
        if len(centrais) > i:
            params[chave] = centrais[i]
    return params


def normalizar(dados):
    """Completa uma convenção salva com chaves que o modelo ganhou depois."""
    base = convencao_padrao(dados.get('asn', ''), dados.get('prefixo_v6', ''))
    saida = copy.deepcopy(base)
    for k, v in (dados or {}).items():
        saida[k] = v
    return saida


def servico_por_nome(conv, nome):
    for s in conv.get('servicos', []):
        if s.get('nome') == nome:
            return s
    return None


def lp_da_classe(conv, classe):
    for c in conv.get('lp_classes', []):
        if c.get('classe', '').upper().startswith(classe.upper()):
            return c.get('lp')
    return ''


def validar(conv):
    """Problemas técnicos da convenção (aparecem no HLD e no TO-BE)."""
    avisos = []
    asn = conv.get('asn', '')
    if not str(asn).isdigit():
        avisos.append('ASN não informado — communities e RTs não podem ser montados.')
        return avisos
    if formato_community(asn) == FORMATO_LARGE:
        avisos.append(
            f'O ASN {asn} tem 4 bytes: communities padrão (RFC 1997) não o comportam. As communities desta '
            f'convenção usam large communities (RFC 8092) no formato {asn}:<valor>:0; confirmar suporte em '
            'todos os equipamentos (no Huawei, apply large-community) antes da Wave 4.')
    for s in conv.get('servicos', []):
        if not rt_valido(conv, s.get('rt', '')):
            avisos.append(f'RT {s.get("rt")} de {s.get("nome")} excede 65535 para ASN de 4 bytes.')
    for linha in conv.get('ipv4', []):
        if str(linha.get('bloco', '')).startswith('198.18.'):
            avisos.append('A infraestrutura usa 198.18.0.0/15 (RFC 2544, faixa de benchmark listada em bogons): '
                          'os filtros de bogon internos precisam de exceção documentada.')
            break
    return avisos
