"""
Testes da automação unificada de duas caixas ligadas por iBGP —
`clientes/bgp_caixas_unificadas.py`.

O par usado aqui é a redução do caso real que motivou a feature (GREEN
TELECOM, BRAS CARMO + BRAS PATOS): mesmo AS, mesmo catálogo de communities,
`c-01` com sessão só em CARMO e `c-02` só em PATOS, as duas originando o
mesmo prefixo com policies locais que divergem.
"""
from django.test import SimpleTestCase

from clientes.backup_parser import parse_huawei
from clientes.bgp_actions import AcaoBgpNaoSuportada
from clientes.bgp_caixas_unificadas import (
    agrupar_caixas,
    aplicar_efeito_unificado,
    comandos_anuncio_unificado,
    detectar_enlaces_ibgp,
    montar_mapa_unificado,
)
from clientes.bgp_community_auto import montar_mapa
from clientes.tests_bgp_community_auto import _filtros, _node, _policy_out

ASN = '65100'
AS_LOCAL = '266296'


def _caixa(nome, ip_ibgp, peer_ibgp, ip_v6, peer_v6, circuito, peer_up, networks,
           locais, advertise=False, ibgp_exporta=True):
    """Snapshot mínimo de uma borda: as duas faixas de filtros (template), a
    policy de saída só do circuito que tem sessão nesta caixa, iBGP v4/v6 pra
    irmã e os prefixos que ela origina com a policy local de cada um."""
    filtros = {}
    filtros.update(_filtros('c-01', '501'))
    filtros.update(_filtros('c-02', '502'))
    nome_out = f'AS1000{circuito[-1]}-UP-V4-OUT'
    nodes = {nome_out: _policy_out(nome_out, circuito)}
    for rp, communities in locais.items():
        nodes[rp] = [_node(rp, 10, '', communities=communities)]
    sessoes = [
        {'nome': peer_up, 'peer_ip': peer_up, 'peer_as': f'1000{circuito[-1]}', 'as_local': AS_LOCAL,
         'policy_in': '', 'policy_out': nome_out, 'habilitada': True, 'descricao': ''},
        {'nome': peer_ibgp, 'peer_ip': peer_ibgp, 'peer_as': AS_LOCAL, 'as_local': AS_LOCAL,
         'policy_in': 'IBGP-IN', 'policy_out': 'IBGP-OUT', 'habilitada': True,
         'descricao': 'IBGP', 'advertise_community': advertise},
        {'nome': peer_v6, 'peer_ip': peer_v6, 'peer_as': AS_LOCAL, 'as_local': AS_LOCAL,
         'policy_in': 'IBGP-V6-IN', 'policy_out': 'IBGP-V6-OUT', 'habilitada': True,
         'descricao': 'IBGP-V6', 'advertise_community': advertise},
    ]
    nets = [{'prefixo': p, 'route_policy': rp, 'familia': 'v4'} for p, rp in networks]
    return {
        'as_local': AS_LOCAL, 'sessoes': sessoes, 'community_filters': filtros,
        'community_nodes': nodes, 'networks': nets, 'policies': {},
        'ips': [{'ip': f'{ip_ibgp}/30', 'interface': 'Eth-Trunk10.3005', 'equipamento': nome}],
        'anuncios': {peer_ibgp: [{'prefixo': p, 'prepend': 0, 'permitido': ibgp_exporta}
                                 for p, _ in networks]},
    }


def _par(locais_carmo=None, locais_patos=None, nets_carmo=None, nets_patos=None, **kw):
    carmo = _caixa('BRAS CARMO', '172.18.18.109', '172.18.18.110',
                   '2804:359c:2400::11', '2804:359c:2400::12', 'c-01', '172.18.18.9',
                   nets_carmo if nets_carmo is not None else [('170.79.95.0/24', 'RT-BGP-LOCAL-95-24')],
                   locais_carmo if locais_carmo is not None else
                   {'RT-BGP-LOCAL-95-24': [f'{ASN}:50103', f'{ASN}:50203']}, **kw)
    patos = _caixa('BRAS PATOS', '172.18.18.110', '172.18.18.109',
                   '2804:359c:2400::12', '2804:359c:2400::11', 'c-02', '198.18.92.109',
                   nets_patos if nets_patos is not None else [('170.79.95.0/24', 'RT-BGP-LOCAL-95-24')],
                   locais_patos if locais_patos is not None else
                   {'RT-BGP-LOCAL-95-24': [f'{ASN}:50201']}, **kw)
    caixas = [{'id': 667, 'nome': 'BRAS CARMO', 'dados': carmo},
              {'id': 673, 'nome': 'BRAS PATOS', 'dados': patos}]
    for cx in caixas:
        cx['mapa'] = montar_mapa(cx['dados'])
    grupo = agrupar_caixas(caixas)[0]
    uni = montar_mapa_unificado(grupo['caixas'], grupo['enlaces'])
    return grupo, uni


def _linha(uni, prefixo='170.79.95.0/24'):
    return next(l for l in uni['anuncios'] if l['prefixo'] == prefixo)


class DeteccaoTest(SimpleTestCase):
    def test_par_ibgp_v4_pelo_ip_e_v6_pelo_par_na_mesma_64(self):
        grupo, _ = _par()
        self.assertEqual([c['nome'] for c in grupo['caixas']], ['BRAS CARMO', 'BRAS PATOS'])
        familias = sorted(e['familia'] for e in grupo['enlaces'])
        self.assertEqual(familias, ['v4', 'v6'])
        v4 = next(e for e in grupo['enlaces'] if e['familia'] == 'v4')
        self.assertEqual(v4['sessao_a']['peer_ip'], '172.18.18.110')
        self.assertEqual(v4['sessao_b']['peer_ip'], '172.18.18.109')

    def test_as_diferente_nao_e_irma(self):
        grupo, _ = _par()
        a, b = (c['dados'] for c in grupo['caixas'])
        b = {**b, 'as_local': '1', 'sessoes': [{**s, 'as_local': '1'} for s in b['sessoes']]}
        self.assertEqual(detectar_enlaces_ibgp(a, b), [])

    def test_caixa_sem_par_nao_forma_grupo(self):
        grupo, _ = _par()
        sozinha = {**grupo['caixas'][0], 'id': 1}
        self.assertEqual(agrupar_caixas([sozinha]), [])

    def test_parser_le_advertise_community(self):
        dados = parse_huawei(
            'bgp 266296\n'
            ' peer 172.18.18.110 as-number 266296\n'
            ' peer 172.18.18.9 as-number 267221\n'
            ' ipv4-family unicast\n'
            '  peer 172.18.18.110 enable\n'
            '  peer 172.18.18.110 advertise-community\n'
            '  peer 172.18.18.9 enable\n'
            '#\n', 'BRAS CARMO')
        por_ip = {s['peer_ip']: s for s in dados['bgp']}
        self.assertTrue(por_ip['172.18.18.110']['advertise_community'])
        self.assertFalse(por_ip['172.18.18.9']['advertise_community'])


class MapaUnificadoTest(SimpleTestCase):
    def test_circuitos_das_duas_caixas_com_a_dona_de_cada(self):
        _, uni = _par()
        self.assertEqual(uni['circuitos']['c-01']['caixa_nome'], 'BRAS CARMO')
        self.assertEqual(uni['circuitos']['c-02']['caixa_nome'], 'BRAS PATOS')
        self.assertEqual([s['caixa_nome'] for s in uni['circuitos']['c-02']['sessoes']], ['BRAS PATOS'])

    def test_intencao_que_vale_e_a_da_caixa_dona(self):
        """CARMO carimba c-02 com 2 prepends, mas o c-02 está em PATOS e PATOS
        origina o próprio prefixo carimbando c-02 normal: vale o de PATOS."""
        _, uni = _par()
        linha = _linha(uni)
        self.assertEqual(linha['destinos'], {'c-01': 'export-2p', 'c-02': 'export'})
        self.assertEqual(linha['efetivo']['c-02']['prepend'], 0)
        self.assertEqual(linha['efetivo']['c-02']['caixa_nome'], 'BRAS PATOS')
        self.assertEqual(linha['efetivo']['c-01']['prepend'], 2)
        self.assertEqual(len(linha['divergencias']), 1)
        div = linha['divergencias'][0]
        self.assertEqual((div['destino'], div['vale'], div['vale_em']), ('c-02', 'export', 'BRAS PATOS'))
        self.assertFalse(linha['sincronizada'])
        self.assertEqual(uni['unificado']['divergentes'], 1)

    def test_caixa_sem_marca_pro_circuito_alheio_nao_e_divergencia(self):
        _, uni = _par(locais_carmo={'RT-BGP-LOCAL-95-24': [f'{ASN}:50101']})
        linha = _linha(uni)
        self.assertEqual(linha['divergencias'], [])
        self.assertFalse(linha['sincronizada'])   # listas diferentes, mesmo sem efeito

    def test_slot_com_sessao_na_irma_nao_aparece_como_vago(self):
        _, uni = _par()
        self.assertNotIn('c-02', {s['id'] for s in uni['slots_vagos']})


class ComandosUnificadosTest(SimpleTestCase):
    def test_caixa_que_ja_tem_a_intencao_fica_fora(self):
        """CARMO já carimba c-02 2p (o pedido do operador); só PATOS muda."""
        grupo, uni = _par()
        plano = comandos_anuncio_unificado(grupo['caixas'], grupo['enlaces'], uni,
                                           '170.79.95.0/24', 'c-02', 'export-2p')
        self.assertEqual([p['nome'] for p in plano], ['BRAS PATOS'])
        self.assertIn(f'apply community {ASN}:50103 {ASN}:50203 additive', plano[0]['comandos'])

    def test_mesma_intencao_escrita_nas_duas_caixas(self):
        grupo, uni = _par()
        plano = comandos_anuncio_unificado(grupo['caixas'], grupo['enlaces'], uni,
                                           '170.79.95.0/24', 'c-02', 'export-1p')
        self.assertEqual([p['nome'] for p in plano], ['BRAS CARMO', 'BRAS PATOS'])
        esperado = f'apply community {ASN}:50103 {ASN}:50202 additive'
        for p in plano:
            self.assertIn(esperado, p['comandos'])
            self.assertEqual(p['comandos'][-1], 'commit')

    def test_caixa_ja_igual_fica_fora_do_plano(self):
        grupo, uni = _par(locais_carmo={'RT-BGP-LOCAL-95-24': [f'{ASN}:50103', f'{ASN}:50201']})
        plano = comandos_anuncio_unificado(grupo['caixas'], grupo['enlaces'], uni,
                                           '170.79.95.0/24', 'c-02', 'export-1p')
        self.assertEqual(len(plano), 2)
        plano = comandos_anuncio_unificado(grupo['caixas'], grupo['enlaces'], uni,
                                           '170.79.95.0/24', '', '')   # só sincronizar
        self.assertEqual([p['nome'] for p in plano], ['BRAS PATOS'])
        self.assertIn(f'apply community {ASN}:50103 {ASN}:50201 additive', plano[0]['comandos'])

    def test_tudo_igual_recusa(self):
        grupo, uni = _par(locais_carmo={'RT-BGP-LOCAL-95-24': [f'{ASN}:50201']},
                          locais_patos={'RT-BGP-LOCAL-95-24': [f'{ASN}:50201']})
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_anuncio_unificado(grupo['caixas'], grupo['enlaces'], uni,
                                       '170.79.95.0/24', 'c-02', 'export')

    def test_prefixo_so_de_um_lado_liga_advertise_community_no_ibgp(self):
        """Só CARMO origina; o c-02 está em PATOS. A intenção só chega pelo
        iBGP — e sem `advertise-community` o VRP não manda community."""
        grupo, uni = _par(nets_patos=[], locais_patos={})
        linha = _linha(uni)
        self.assertIn('c-02', linha['sem_intencao'])
        plano = comandos_anuncio_unificado(grupo['caixas'], grupo['enlaces'], uni,
                                           '170.79.95.0/24', 'c-02', 'export')
        self.assertEqual([p['nome'] for p in plano], ['BRAS CARMO'])
        cmds = plano[0]['comandos']
        self.assertIn('peer 172.18.18.110 advertise-community', cmds)
        self.assertLess(cmds.index(f'bgp {AS_LOCAL}'), cmds.index('commit'))
        self.assertEqual(plano[0]['advertise'], ['172.18.18.110'])

    def test_advertise_ja_configurado_nao_repete(self):
        grupo, uni = _par(nets_patos=[], locais_patos={}, advertise=True)
        linha = _linha(uni)
        self.assertEqual(linha['efetivo']['c-02']['caixa_nome'], 'BRAS PATOS')
        plano = comandos_anuncio_unificado(grupo['caixas'], grupo['enlaces'], uni,
                                           '170.79.95.0/24', 'c-02', 'export-1p')
        self.assertFalse(any('advertise-community' in c for c in plano[0]['comandos']))

    def test_ibgp_que_nao_exporta_o_prefixo_recusa(self):
        grupo, uni = _par(nets_patos=[], locais_patos={}, ibgp_exporta=False)
        with self.assertRaisesMessage(AcaoBgpNaoSuportada, 'não deixa 170.79.95.0/24 passar'):
            comandos_anuncio_unificado(grupo['caixas'], grupo['enlaces'], uni,
                                       '170.79.95.0/24', 'c-02', 'export')

    def test_circuito_sem_sessao_em_nenhuma_caixa_recusa(self):
        grupo, uni = _par()
        dados = grupo['caixas'][0]['dados']
        dados['community_filters'].update(_filtros('c-03', '503'))
        for cx in grupo['caixas']:
            cx['mapa'] = montar_mapa(cx['dados'])
        uni = montar_mapa_unificado(grupo['caixas'], grupo['enlaces'])
        with self.assertRaisesMessage(AcaoBgpNaoSuportada, 'nenhuma das caixas'):
            comandos_anuncio_unificado(grupo['caixas'], grupo['enlaces'], uni,
                                       '170.79.95.0/24', 'c-03', 'export')

    def test_efeito_otimista_troca_a_lista_e_marca_advertise(self):
        grupo, uni = _par(nets_patos=[], locais_patos={},
                          locais_carmo={'RT-BGP-LOCAL-95-24': [f'{ASN}:50101', f'{ASN}:10091']})
        plano = comandos_anuncio_unificado(grupo['caixas'], grupo['enlaces'], uni,
                                           '170.79.95.0/24', 'c-02', 'export')
        dados = grupo['caixas'][0]['dados']
        aplicar_efeito_unificado(dados, plano[0])
        node = dados['community_nodes']['RT-BGP-LOCAL-95-24'][0]
        self.assertEqual(node['apply_community'], [f'{ASN}:50101', f'{ASN}:50201', f'{ASN}:10091'])
        sessao = next(s for s in dados['sessoes'] if s['peer_ip'] == '172.18.18.110')
        self.assertTrue(sessao['advertise_community'])
