"""
Testes da automação de anúncios por community (Huawei/VRP) —
`clientes/bgp_community_auto.py`.

O snapshot usado aqui é uma redução fiel do que `parse_huawei` extrai das
caixas reais no padrão novo: circuitos das três famílias (`c-NN` operadora,
`ix-NN`, `cdn-NN`), o grupo global `glob-all-ptts-ixbr` casado no node 12 das
policies de IX e um circuito IX "à moda antiga" (`c-81`), cujo tipo só dá pra
saber pelo glob que a policy de saída referencia.
"""
from django.test import SimpleTestCase

from clientes.bgp_actions import AcaoBgpNaoSuportada
from clientes.bgp_community_auto import (
    aplicar_efeito_local,
    comandos_definir_anuncio,
    comandos_novo_prefixo,
    comandos_provisionar_circuito,
    montar_mapa,
)

ASN = '65100'


def _filtros(base, grupo, sufixos=('01', '02', '03', '04', '05', '08', '66', '67', '00')):
    """Bloco de community-filters de um circuito, no padrão do template."""
    nomes = {'01': 'export', '02': 'export-1p', '03': 'export-2p', '04': 'export-3p',
             '05': 'export-4p', '08': 'export-ne', '66': 'export-bh', '67': 'export-bl',
             '00': 'import-rr', '09': 'export-df'}
    return {
        f'{base}-{nomes[s]}': [{'index': 10, 'acao': 'permit', 'valores': [f'{ASN}:{grupo}{s}']}]
        for s in sufixos
    }


def _node(policy, node, filtro, acao='permit', prepend=0, extra=(), communities=()):
    return {
        'policy': policy, 'node': node, 'acao': acao,
        'community_filters': [filtro] if filtro else [],
        'apply_community': list(communities), 'apply_community_extra': list(extra),
        'prefix_lists': [], 'prepend_as': ['268080'] * prepend, 'local_preference': None,
    }


def _policy_out(nome, base, glob=''):
    """Policy de saída no layout do template: bl 9, bh 10, export 11,
    global 12, prepends 13-16, no-export 17."""
    nodes = [
        _node(nome, 9, f'{base}-export-bl', acao='deny'),
        _node(nome, 10, f'{base}-export-bh'),
        _node(nome, 11, f'{base}-export'),
    ]
    if glob:
        nodes.append(_node(nome, 12, glob))
    nodes += [
        _node(nome, 13, f'{base}-export-1p', prepend=1),
        _node(nome, 14, f'{base}-export-2p', prepend=2),
        _node(nome, 15, f'{base}-export-3p', prepend=3),
        _node(nome, 16, f'{base}-export-4p', prepend=4),
        _node(nome, 17, f'{base}-export-ne', extra=['no-export']),
    ]
    return nodes


def dados_base():
    filtros = {}
    filtros.update(_filtros('c-01', '501'))
    filtros.update(_filtros('ix-01', '601'))
    filtros.update(_filtros('cdn-01', '611'))
    filtros.update(_filtros('c-81', '581'))
    filtros.update({
        'glob-all-ptts-ixbr': [{'index': 10, 'acao': 'permit', 'valores': [f'{ASN}:60011']}],
        'glob-all-ptts-ixbr-2p': [{'index': 10, 'acao': 'permit', 'valores': [f'{ASN}:60013']}],
        'glob-all-cdns': [{'index': 10, 'acao': 'permit', 'valores': [f'{ASN}:60021']}],
    })
    nodes = {
        'AS14840-BRD-V4-OUT': _policy_out('AS14840-BRD-V4-OUT', 'c-01'),
        'AS26162-PTT-SP-V4-OUT': _policy_out('AS26162-PTT-SP-V4-OUT', 'ix-01', 'glob-all-ptts-ixbr'),
        'AS26162-PTT-RJ-V4-OUT': _policy_out('AS26162-PTT-RJ-V4-OUT', 'c-81', 'glob-all-ptts-ixbr'),
        'AS32934-FACE-V4-OUT': _policy_out('AS32934-FACE-V4-OUT', 'cdn-01'),
        'AS14840-BRD-V4-IN': [_node('AS14840-BRD-V4-IN', 10, '', communities=[f'{ASN}:50100'])],
        # policy local de cada prefixo — é aqui que mora a intenção
        'RT-BGP-LOCAL-04-22': [_node('RT-BGP-LOCAL-04-22', 10, '', communities=[f'{ASN}:50101'])],
        'RT-BGP-LOCAL-06-24': [_node('RT-BGP-LOCAL-06-24', 10, '',
                                     communities=[f'{ASN}:60011', f'{ASN}:60103'])],
        'RT-BGP-LOCAL-08-24': [_node('RT-BGP-LOCAL-08-24', 10, '',
                                     communities=[f'{ASN}:10091'])],
    }
    sessoes = [
        {'nome': '10.0.0.1', 'peer_ip': '10.0.0.1', 'peer_as': '14840', 'as_local': '268080',
         'policy_in': 'AS14840-BRD-V4-IN', 'policy_out': 'AS14840-BRD-V4-OUT',
         'habilitada': True, 'descricao': ''},
        {'nome': '187.16.216.253', 'peer_ip': '187.16.216.253', 'peer_as': '26162',
         'as_local': '268080', 'policy_in': '', 'policy_out': 'AS26162-PTT-SP-V4-OUT',
         'habilitada': True, 'descricao': 'RS1.PTT-SP'},
        {'nome': '45.68.80.253', 'peer_ip': '45.68.80.253', 'peer_as': '26162',
         'as_local': '268080', 'policy_in': '', 'policy_out': 'AS26162-PTT-RJ-V4-OUT',
         'habilitada': True, 'descricao': 'RS1.PTT-RJ'},
    ]
    networks = [
        {'prefixo': '45.169.4.0/22', 'route_policy': 'RT-BGP-LOCAL-04-22', 'familia': 'v4',
         'ip': '45.169.4.0', 'mascara': '255.255.252.0'},
        {'prefixo': '45.169.6.0/24', 'route_policy': 'RT-BGP-LOCAL-06-24', 'familia': 'v4',
         'ip': '45.169.6.0', 'mascara': '255.255.255.0'},
        {'prefixo': '45.169.8.0/24', 'route_policy': 'RT-BGP-LOCAL-08-24', 'familia': 'v4',
         'ip': '45.169.8.0', 'mascara': '255.255.255.0'},
    ]
    # O `deny node 999` não tem community nenhuma, então o parser o guarda em
    # `policies` (a simulação) e não em `community_nodes` — é de lá que
    # `validar_mapa` confere que o bloqueio final existe.
    policies = {
        nome: [{'ordem': 999, 'acao': 'reject', 'prefix_lists': [], 'prepend': 0,
                'extra': {'policy': nome, 'node': 999, 'nao_suportado': False}}]
        for nome in nodes if nome.endswith('-OUT')
    }
    return {'as_local': '268080', 'sessoes': sessoes, 'community_filters': filtros,
            'community_nodes': nodes, 'networks': networks, 'policies': policies}


class DescobertaTest(SimpleTestCase):
    def setUp(self):
        self.dados = dados_base()
        self.mapa = montar_mapa(self.dados)

    def test_reconhece_as_tres_familias_de_circuito(self):
        tipos = {cid: c['tipo'] for cid, c in self.mapa['circuitos'].items()}
        self.assertEqual(tipos['c-01'], 'upstream')
        self.assertEqual(tipos['ix-01'], 'ix')
        self.assertEqual(tipos['cdn-01'], 'cdn')

    def test_circuito_c_NN_vira_ix_pelo_glob_da_policy(self):
        """Caixa antiga: o IX também se chama `c-NN`. O tipo real vem do
        `glob-all-ptts-ixbr` que a policy de saída referencia."""
        c81 = self.mapa['circuitos']['c-81']
        self.assertEqual(c81['tipo'], 'ix')
        self.assertEqual(c81['tipo_origem'], 'glob')

    def test_grupo_global_conhece_o_alcance_real(self):
        g = self.mapa['globais']['glob-all-ptts-ixbr']
        self.assertEqual(sorted(g['circuitos']), ['c-81', 'ix-01'])
        self.assertEqual(g['alcance'], 2)
        # A variante -2p existe como filtro mas nenhuma policy a casa.
        self.assertEqual(g['acoes']['export-2p']['circuitos'], [])
        # E o grupo que ninguém referencia vira aviso, não destino utilizável.
        self.assertEqual(self.mapa['globais']['glob-all-cdns']['alcance'], 0)
        self.assertTrue(any('glob-all-cdns' in a for a in self.mapa['avisos']))

    def test_intencao_do_prefixo_separa_individual_de_global(self):
        linha = next(l for l in self.mapa['anuncios'] if l['prefixo'] == '45.169.6.0/24')
        self.assertEqual(linha['globais'], {'glob-all-ptts-ixbr': 'export'})
        self.assertEqual(linha['destinos'], {'ix-01': 'export-2p'})
        self.assertEqual(linha['communities_extras'], [])

    def test_community_fora_do_catalogo_fica_em_extras(self):
        linha = next(l for l in self.mapa['anuncios'] if l['prefixo'] == '45.169.8.0/24')
        self.assertEqual(linha['communities_extras'], [f'{ASN}:10091'])

    def test_efeito_resolve_global_x_prepend_pela_ordem_do_node(self):
        """O prefixo carrega a global (node 12) e o prepend individual (node
        14): no VRP vale o primeiro node que casa, então sai SEM prepend."""
        linha = next(l for l in self.mapa['anuncios'] if l['prefixo'] == '45.169.6.0/24')
        efeito = linha['efetivo']['ix-01']
        self.assertEqual(efeito['filtro'], 'glob-all-ptts-ixbr')
        self.assertEqual(efeito['node'], 12)
        self.assertTrue(efeito['anuncia'])
        self.assertEqual(efeito['prepend'], 0)
        self.assertEqual(efeito['ignorados'], ['ix-01-export-2p'])
        # A global também alcança o circuito IX antigo, que não tem intenção
        # individual nenhuma.
        self.assertEqual(linha['efetivo']['c-81']['filtro'], 'glob-all-ptts-ixbr')
        self.assertNotIn('c-01', linha['efetivo'])


class ComandosAnuncioTest(SimpleTestCase):
    def setUp(self):
        self.dados = dados_base()
        self.mapa = montar_mapa(self.dados)

    def test_mudar_acao_num_circuito_preserva_as_outras_intencoes(self):
        comandos = comandos_definir_anuncio(
            self.dados, self.mapa, '45.169.6.0/24', 'ix-01', 'export-3p')
        self.assertEqual(comandos, [
            'route-policy RT-BGP-LOCAL-06-24 permit node 10',
            'undo apply community',
            f'apply community {ASN}:60104 {ASN}:60011 additive',
            'quit',
            'commit',
        ])

    def test_definir_grupo_global(self):
        comandos = comandos_definir_anuncio(
            self.dados, self.mapa, '45.169.4.0/22', 'glob-all-ptts-ixbr', 'export')
        self.assertEqual(comandos[2], f'apply community {ASN}:50101 {ASN}:60011 additive')

    def test_remover_do_destino(self):
        comandos = comandos_definir_anuncio(
            self.dados, self.mapa, '45.169.6.0/24', 'glob-all-ptts-ixbr', '')
        self.assertEqual(comandos[2], f'apply community {ASN}:60103 additive')

    def test_community_fora_do_catalogo_e_reemitida(self):
        comandos = comandos_definir_anuncio(
            self.dados, self.mapa, '45.169.8.0/24', 'c-01', 'export')
        self.assertEqual(comandos[2], f'apply community {ASN}:50101 {ASN}:10091 additive')

    def test_recusa_global_sem_alcance(self):
        with self.assertRaises(AcaoBgpNaoSuportada) as e:
            comandos_definir_anuncio(
                self.dados, self.mapa, '45.169.4.0/22', 'glob-all-cdns', 'export')
        self.assertIn('não produziria anúncio', str(e.exception))

    def test_recusa_variante_global_que_ninguem_casa(self):
        with self.assertRaises(AcaoBgpNaoSuportada) as e:
            comandos_definir_anuncio(
                self.dados, self.mapa, '45.169.4.0/22', 'glob-all-ptts-ixbr', 'export-2p')
        self.assertIn('Nenhuma policy de saída', str(e.exception))

    def test_recusa_circuito_sem_sessao(self):
        with self.assertRaises(AcaoBgpNaoSuportada) as e:
            comandos_definir_anuncio(
                self.dados, self.mapa, '45.169.4.0/22', 'cdn-01', 'export')
        self.assertIn('não tem nenhuma sessão BGP', str(e.exception))

    def test_recusa_acao_repetida(self):
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_definir_anuncio(
                self.dados, self.mapa, '45.169.6.0/24', 'ix-01', 'export-2p')

    def test_novo_prefixo_aceita_circuito_e_global(self):
        comandos = comandos_novo_prefixo(
            self.dados, self.mapa, '45.169.12.0/22',
            {'c-01': 'export-1p', 'glob-all-ptts-ixbr': 'export'})
        self.assertEqual(comandos[0], 'route-policy RT-BGP-LOCAL-12-22 permit node 10')
        self.assertEqual(comandos[1], f'apply community {ASN}:50102 {ASN}:60011 additive')
        self.assertIn('network 45.169.12.0 255.255.252.0 route-policy RT-BGP-LOCAL-12-22',
                      comandos)


class ProvisionarCircuitoTest(SimpleTestCase):
    def setUp(self):
        self.dados = dados_base()
        self.mapa = montar_mapa(self.dados)

    def test_circuito_ix_novo_sai_no_layout_do_template(self):
        comandos = comandos_provisionar_circuito(self.dados, self.mapa, 'ix-02', {
            'grupo': '602', 'nome': 'PTT-CE', 'peer_as': '26162',
            'prepend_as': '268080', 'ipv4': True, 'ipv6': False,
        })
        filtros = [c for c in comandos if c.startswith('ip community-filter')]
        self.assertEqual(len(filtros), 9)
        self.assertIn(f'ip community-filter basic ix-02-export index 10 permit {ASN}:60201', filtros)
        # `export-df` saiu do padrão e não é mais gerada.
        self.assertFalse(any('export-df' in c for c in comandos))
        nodes = [c for c in comandos if c.startswith('route-policy')]
        self.assertEqual(nodes, [
            'route-policy AS26162-PTT-CE-V4-OUT deny node 9',
            'route-policy AS26162-PTT-CE-V4-OUT permit node 10',
            'route-policy AS26162-PTT-CE-V4-OUT permit node 11',
            'route-policy AS26162-PTT-CE-V4-OUT permit node 12',
            'route-policy AS26162-PTT-CE-V4-OUT permit node 13',
            'route-policy AS26162-PTT-CE-V4-OUT permit node 14',
            'route-policy AS26162-PTT-CE-V4-OUT permit node 15',
            'route-policy AS26162-PTT-CE-V4-OUT permit node 16',
            'route-policy AS26162-PTT-CE-V4-OUT permit node 17',
            'route-policy AS26162-PTT-CE-V4-OUT deny node 999',
        ])
        # o node 12 é a community global do tipo do circuito
        self.assertEqual(comandos[comandos.index(
            'route-policy AS26162-PTT-CE-V4-OUT permit node 12') + 1],
            'if-match community-filter glob-all-ptts-ixbr')
        # e o no-export é permit + apply (um deny aqui não anunciaria nada)
        i_ne = comandos.index('route-policy AS26162-PTT-CE-V4-OUT permit node 17')
        self.assertEqual(comandos[i_ne + 1], 'if-match community-filter ix-02-export-ne')
        self.assertEqual(comandos[i_ne + 2], 'apply community no-export')

    def test_upstream_novo_nao_leva_node_de_glob_que_a_caixa_nao_tem(self):
        comandos = comandos_provisionar_circuito(self.dados, self.mapa, 'c-02', {
            'grupo': '502', 'nome': 'WIRELINK', 'peer_as': '61832',
            'prepend_as': '268080', 'ipv4': True, 'ipv6': False,
        })
        self.assertFalse(any('glob-' in c for c in comandos))

    def test_recusa_id_fora_do_padrao(self):
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_provisionar_circuito(self.dados, self.mapa, 'upstream-1', {'grupo': '502'})

    def test_circuito_completo_nao_gera_nada(self):
        with self.assertRaises(AcaoBgpNaoSuportada) as e:
            comandos_provisionar_circuito(self.dados, self.mapa, 'ix-01', {'ipv4': True})
        self.assertIn('já está completo', str(e.exception))


class EfeitoOtimistaTest(SimpleTestCase):
    def test_troca_a_community_do_destino_no_snapshot(self):
        dados = dados_base()
        aplicar_efeito_local(dados, 'anuncio_community', '45.169.6.0/24',
                             {'destino': 'ix-01', 'acao': 'export-4p',
                              'route_policy': 'RT-BGP-LOCAL-06-24'})
        aplicadas = dados['community_nodes']['RT-BGP-LOCAL-06-24'][0]['apply_community']
        self.assertIn(f'{ASN}:60105', aplicadas)
        self.assertNotIn(f'{ASN}:60103', aplicadas)
        self.assertIn(f'{ASN}:60011', aplicadas)   # a global não é tocada

    def test_remover_grupo_global(self):
        dados = dados_base()
        aplicar_efeito_local(dados, 'anuncio_community', '45.169.6.0/24',
                             {'destino': 'glob-all-ptts-ixbr', 'acao': '',
                              'route_policy': 'RT-BGP-LOCAL-06-24'})
        aplicadas = dados['community_nodes']['RT-BGP-LOCAL-06-24'][0]['apply_community']
        self.assertEqual(aplicadas, [f'{ASN}:60103'])

    def test_provisionar_registra_os_filtros_novos(self):
        dados = dados_base()
        aplicar_efeito_local(dados, 'provisionar_circuito', 'ix-02',
                             {'destino': 'ix-02', 'grupo': '602', 'asn_community': ASN})
        self.assertIn('ix-02-export', dados['community_filters'])
        self.assertEqual(dados['community_filters']['ix-02-export'][0]['valores'],
                         [f'{ASN}:60201'])
