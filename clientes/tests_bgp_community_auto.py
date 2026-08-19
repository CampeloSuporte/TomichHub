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
    comandos_criar_circuito,
    comandos_criar_downstream,
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

    def test_avisa_quando_o_prepend_ignora_o_fake_as_da_sessao(self):
        dados = dados_base()
        dados['sessoes'][0]['fake_as'] = '52995'   # c-01 prepende 268080
        mapa = montar_mapa(dados)
        self.assertTrue(any('fake-as 52995' in a and 'não alonga o caminho' in a
                            for a in mapa['avisos']), mapa['avisos'])

    def test_fake_as_da_sessao_fica_visivel_no_circuito(self):
        dados = dados_base()
        dados['sessoes'][0]['fake_as'] = '52995'
        mapa = montar_mapa(dados)
        self.assertEqual(mapa['circuitos']['c-01']['sessoes'][0]['fake_as'], '52995')


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

def _dados_com_cliente():
    """`dados_base` + uma sessão de cliente no desenho real: policy de saída
    que libera a tabela cheia e policy de entrada casando a prefix-list dele."""
    dados = dados_base()
    dados['prefix_lists'] = {
        'FULL-ROUTING': [{'acao': 'permit', 'prefixo': '0.0.0.0/0', 'len_min': 0,
                          'len_max': 24, 'index': 10}],
        'BOGONS-V4': [{'acao': 'deny', 'prefixo': '10.0.0.0/8', 'len_min': 8,
                       'len_max': 8, 'index': 10}],
        'BOGONS-V4-IN': [{'acao': 'permit', 'prefixo': '10.0.0.0/8', 'len_min': 8,
                          'len_max': 32, 'index': 10}],
        'PL-DOWNSTREAM-CIANET-V4': [{'acao': 'permit', 'prefixo': '170.83.156.0/22',
                                     'len_min': 22, 'len_max': 24, 'index': 10}],
    }
    dados['policies']['DOWNSTREAM-CIANET-V4-OUT'] = [
        {'ordem': 10, 'acao': 'accept', 'prefix_lists': ['FULL-ROUTING'], 'prepend': 0,
         'extra': {'policy': 'DOWNSTREAM-CIANET-V4-OUT', 'node': 10, 'nao_suportado': False}}]
    dados['policies']['DOWNSTREAM-CIANET-V4-IN'] = [
        {'ordem': 10, 'acao': 'accept', 'prefix_lists': ['PL-DOWNSTREAM-CIANET-V4'], 'prepend': 0,
         'extra': {'policy': 'DOWNSTREAM-CIANET-V4-IN', 'node': 10, 'nao_suportado': False}}]
    dados['community_nodes']['DOWNSTREAM-CIANET-V4-IN'] = [
        _node('DOWNSTREAM-CIANET-V4-IN', 10, '', communities=[f'{ASN}:60011'])]
    dados['sessoes'].append(
        {'nome': '172.18.100.138', 'peer_ip': '172.18.100.138', 'peer_as': '266483',
         'as_local': '268080', 'policy_in': 'DOWNSTREAM-CIANET-V4-IN',
         'policy_out': 'DOWNSTREAM-CIANET-V4-OUT', 'habilitada': True, 'descricao': ''})
    # iBGP: a policy também manda tudo, mas cliente não é
    dados['policies']['RP-IBGP-OUT'] = [
        {'ordem': 10, 'acao': 'accept', 'prefix_lists': [], 'prepend': 0,
         'extra': {'policy': 'RP-IBGP-OUT', 'node': 10, 'nao_suportado': False}}]
    dados['sessoes'].append(
        {'nome': '10.64.65.2', 'peer_ip': '10.64.65.2', 'peer_as': '268080',
         'as_local': '268080', 'policy_in': '', 'policy_out': 'RP-IBGP-OUT',
         'habilitada': True, 'descricao': ''})
    return dados


class SlotsTest(SimpleTestCase):
    def setUp(self):
        self.mapa = montar_mapa(dados_base())

    def test_slot_vago_traz_o_grupo_de_community_do_template(self):
        vagos = {v['id']: v for v in self.mapa['slots_vagos']}
        self.assertEqual(vagos['c-02']['grupo'], '502')      # §6: 501-510
        self.assertEqual(vagos['ix-07']['grupo'], '607')     # §7: 601-610
        self.assertEqual(vagos['cdn-05']['grupo'], '615')    # §8: 611-615

    def test_slot_ocupado_nao_aparece_como_vago(self):
        ids = {v['id'] for v in self.mapa['slots_vagos']}
        self.assertNotIn('c-01', ids)
        self.assertIn('c-02', ids)

    def test_faixa_do_template(self):
        por_tipo = {}
        for v in self.mapa['slots_vagos']:
            por_tipo[v['tipo']] = por_tipo.get(v['tipo'], 0) + 1
        self.assertEqual(por_tipo, {'upstream': 9, 'ix': 9, 'cdn': 4})


class CriarCircuitoTest(SimpleTestCase):
    def setUp(self):
        self.dados = dados_base()
        self.mapa = montar_mapa(self.dados)

    def _criar(self, cid, **opcoes):
        base = {'nome': 'TESTE', 'peer_as': '65000',
                'v4': {'peers': [{'ip': '192.0.2.1'}]}}
        base.update(opcoes)
        return comandos_criar_circuito(self.dados, self.mapa, cid, base)

    def test_upstream_novo_sai_com_policy_in_out_e_sessao(self):
        cmds = self._criar('c-02', nome='BR-DIGITAL', peer_as='14840', local_preference='1000')
        # community-filters do slot, com o grupo calculado (c-02 → 502)
        self.assertIn(f'ip community-filter basic c-02-export index 10 permit {ASN}:50201', cmds)
        # entrada: bogons fora, tabela cheia dentro, community de origem
        i = cmds.index('route-policy AS14840-BR-DIGITAL-V4-IN deny node 5')
        self.assertEqual(cmds[i + 1], 'if-match ip-prefix BOGONS-V4-IN')
        self.assertIn('apply local-preference 1000', cmds)
        self.assertIn(f'apply community {ASN}:50200 additive', cmds)
        self.assertIn('route-policy AS14840-BR-DIGITAL-V4-IN deny node 999', cmds)
        # saída no layout do template
        self.assertIn('route-policy AS14840-BR-DIGITAL-V4-OUT permit node 11', cmds)
        self.assertIn('if-match community-filter c-02-export', cmds)
        # e a sessão
        self.assertIn('peer 192.0.2.1 as-number 14840', cmds)
        self.assertIn('peer 192.0.2.1 description EBGP-AS14840-BR-DIGITAL-V4', cmds)
        self.assertIn('peer 192.0.2.1 route-policy AS14840-BR-DIGITAL-V4-IN import', cmds)
        self.assertIn('peer 192.0.2.1 route-policy AS14840-BR-DIGITAL-V4-OUT export', cmds)
        self.assertIn('peer 192.0.2.1 advertise-community', cmds)
        self.assertEqual(cmds[-1], 'commit')

    def test_ix_novo_usa_peer_group_com_as_policies_no_grupo(self):
        cmds = self._criar('ix-02', nome='PTT-CE', peer_as='26162',
                           v4={'peers': [{'ip': '45.68.79.253'}, {'ip': '45.68.79.254'}]})
        self.assertIn('group EBGP-PTT-CE-V4 external', cmds)
        self.assertIn('peer 45.68.79.253 group EBGP-PTT-CE-V4', cmds)
        self.assertIn('peer 45.68.79.253 description RS1.PTT-CE', cmds)
        self.assertIn('peer 45.68.79.254 description RS2.PTT-CE', cmds)
        # as policies vão no GRUPO, não no peer — é o que o parser lê de volta
        self.assertIn('peer EBGP-PTT-CE-V4 route-policy AS26162-PTT-CE-V4-IN import', cmds)
        self.assertIn('peer EBGP-PTT-CE-V4 public-as-only', cmds)
        self.assertNotIn('peer 45.68.79.253 route-policy AS26162-PTT-CE-V4-IN import', cmds)

    def test_peer_v6_e_tirado_da_family_v4(self):
        cmds = self._criar('c-03', v6={'peers': [{'ip': '2001:db8::1'}]})
        self.assertIn('undo peer 2001:db8::1 enable', cmds)
        self.assertLess(cmds.index('ipv4-family unicast'), cmds.index('undo peer 2001:db8::1 enable'))
        self.assertIn('ipv6-family unicast', cmds)
        self.assertIn('peer 2001:db8::1 enable', cmds)

    def test_node_da_community_global_entra_no_circuito_novo(self):
        cmds = self._criar('ix-02', nome='PTT-CE', peer_as='26162')
        self.assertIn('if-match community-filter glob-all-ptts-ixbr', cmds)

    def test_cria_o_filtro_global_do_tipo_quando_a_caixa_nao_tem(self):
        cmds = self._criar('c-02')   # a caixa de teste não tem glob-all-upstream
        self.assertIn(f'ip community-filter basic glob-all-upstream index 10 permit {ASN}:60001', cmds)

    def test_fake_as_entra_na_sessao_e_manda_no_prepend(self):
        cmds = self._criar('c-04', nome='TEN', peer_as='65020', fake_as='52995')
        self.assertIn('peer 192.0.2.1 fake-as 52995', cmds)
        # o prepend repete o ASN que o peer ENXERGA, não o do `bgp <N>`
        self.assertIn('apply as-path 52995 52995 additive', cmds)
        self.assertNotIn('apply as-path 268080 268080 additive', cmds)

    def test_recusa_prepend_que_nao_repete_o_fake_as(self):
        with self.assertRaises(AcaoBgpNaoSuportada) as ctx:
            self._criar('c-04', fake_as='52995', prepend_as='268080')
        self.assertIn('fake-as 52995', str(ctx.exception))

    def test_subir_desabilitada_usa_ignore_e_nao_a_ausencia_de_enable(self):
        # No VRP o peer nasce ativo: sem `ignore` ele subiria assim que a
        # config entrasse. `undo peer … ignore` é o que o botão Ativar faz.
        cmds = self._criar('c-04', habilitar=False)
        self.assertIn('peer 192.0.2.1 ignore', cmds)
        self.assertIn('peer 192.0.2.1 enable', cmds)

    def test_recusa_peer_ja_configurado(self):
        with self.assertRaises(AcaoBgpNaoSuportada) as ctx:
            self._criar('c-02', v4={'peers': [{'ip': '10.0.0.1'}]})
        self.assertIn('Já existe uma sessão', str(ctx.exception))

    def test_recusa_ip_na_familia_errada(self):
        with self.assertRaises(AcaoBgpNaoSuportada):
            self._criar('c-02', v6={'peers': [{'ip': '192.0.2.9'}]})

    def test_recusa_sem_peer(self):
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_criar_circuito(self.dados, self.mapa, 'c-02',
                                    {'nome': 'X', 'peer_as': '65000'})

    def test_recusa_nome_que_pega_a_policy_de_outro_circuito(self):
        # "BRD" faria o ix-02 escrever nos nodes do c-01 (AS14840-BRD-V4-OUT)
        with self.assertRaises(AcaoBgpNaoSuportada) as ctx:
            self._criar('ix-02', nome='BRD', peer_as='14840')
        self.assertIn('já é do circuito c-01', str(ctx.exception))

    def test_completar_circuito_existente_nao_recria_o_que_ja_ha(self):
        cmds = comandos_criar_circuito(self.dados, self.mapa, 'c-01', {
            'nome': 'BRD', 'peer_as': '14840', 'v4': {'peers': [{'ip': '192.0.2.5'}]},
            'policy_v4_out': 'AS14840-BRD-V4-OUT', 'policy_v4_in': 'AS14840-BRD-V4-IN',
        })
        self.assertFalse([c for c in cmds if c.startswith('ip community-filter basic c-01-')])
        self.assertFalse([c for c in cmds if c.startswith('route-policy AS14840-BRD-V4-IN')])
        self.assertIn('peer 192.0.2.5 as-number 14840', cmds)


class CriarDownstreamTest(SimpleTestCase):
    def setUp(self):
        self.dados = _dados_com_cliente()
        self.mapa = montar_mapa(self.dados)

    def _criar(self, **opcoes):
        base = {'nome': 'ACME', 'peer_as': '268999',
                'v4': {'peers': [{'ip': '192.0.2.20'}], 'prefixos': ['170.84.0.0/22']}}
        base.update(opcoes)
        return comandos_criar_downstream(self.dados, self.mapa, base)

    def test_prefixos_do_cliente_viram_a_prefix_list_casada_na_entrada(self):
        cmds = self._criar()
        self.assertIn('ip ip-prefix PL-DOWNSTREAM-ACME-V4 index 10 permit 170.84.0.0 22 '
                      'greater-equal 22 less-equal 24', cmds)
        i = cmds.index('route-policy DOWNSTREAM-ACME-V4-IN permit node 10')
        self.assertEqual(cmds[i + 1], 'if-match ip-prefix PL-DOWNSTREAM-ACME-V4')
        self.assertIn('route-policy DOWNSTREAM-ACME-V4-IN deny node 999', cmds)

    def test_saida_manda_a_tabela_cheia(self):
        cmds = self._criar()
        i = cmds.index('route-policy DOWNSTREAM-ACME-V4-OUT permit node 10')
        self.assertEqual(cmds[i + 1], 'if-match ip-prefix FULL-ROUTING')
        self.assertIn('route-policy DOWNSTREAM-ACME-V4-OUT deny node 999', cmds)

    def test_communities_de_reanuncio_ficam_na_policy_de_entrada(self):
        cmds = self._criar(destinos={'c-01': 'export', 'glob-all-ptts-ixbr': 'export'})
        self.assertIn(f'apply community {ASN}:50101 {ASN}:60011 additive', cmds)

    def test_upstream_continua_com_o_filtro_de_bogons(self):
        # No upstream o node 10 aceita a tabela cheia — é o node 5 que segura
        # os bogons. Só o downstream dispensa a lista.
        cmds = comandos_criar_circuito(self.dados, self.mapa, 'c-02', {
            'nome': 'X', 'peer_as': '65000', 'v4': {'peers': [{'ip': '192.0.2.40'}]}})
        self.assertIn('if-match ip-prefix BOGONS-V4-IN', cmds)

    def test_recusa_destino_global_sem_alcance(self):
        with self.assertRaises(AcaoBgpNaoSuportada):
            self._criar(destinos={'glob-all-cdns': 'export'})

    def test_recusa_sem_prefixo_do_cliente(self):
        with self.assertRaises(AcaoBgpNaoSuportada) as ctx:
            self._criar(v4={'peers': [{'ip': '192.0.2.21'}], 'prefixos': []})
        self.assertIn('prefixos', str(ctx.exception).lower())

    def test_recusa_nome_que_colide_com_config_existente(self):
        with self.assertRaises(AcaoBgpNaoSuportada) as ctx:
            self._criar(nome='CIANET', v4={'peers': [{'ip': '192.0.2.22'}],
                                           'prefixos': ['170.85.0.0/22']})
        self.assertIn('já existe', str(ctx.exception).lower())

    def test_nao_gera_lista_de_bogons(self):
        # A entrada do cliente casa a prefix-list DELE — bogon nenhum chegaria
        # a esse node, então a lista seria config que nunca casa.
        cmds = self._criar()
        self.assertFalse([c for c in cmds if 'BOGON' in c.upper()], cmds)
        i = cmds.index('route-policy DOWNSTREAM-ACME-V4-IN permit node 10')
        self.assertEqual(cmds[i + 1], 'if-match ip-prefix PL-DOWNSTREAM-ACME-V4')


class DownstreamDescobertaTest(SimpleTestCase):
    def setUp(self):
        self.mapa = montar_mapa(_dados_com_cliente())

    def test_encontra_o_cliente_pela_policy_que_libera_tudo(self):
        self.assertIn('DOWNSTREAM-CIANET', self.mapa['downstreams'])

    def test_ibgp_nao_vira_downstream(self):
        self.assertEqual(len(self.mapa['downstreams']), 1)

    def test_mostra_prefixos_aceitos_e_para_onde_sao_reanunciados(self):
        d = self.mapa['downstreams']['DOWNSTREAM-CIANET']
        self.assertEqual([p['lista'] for p in d['prefixos_aceitos']['v4']],
                         ['PL-DOWNSTREAM-CIANET-V4'])
        self.assertEqual(d['destinos'], {'glob-all-ptts-ixbr': 'export'})


class CriacaoOtimistaTest(SimpleTestCase):
    def test_circuito_novo_aparece_completo_no_painel(self):
        dados = dados_base()
        aplicar_efeito_local(dados, 'criar_circuito_community', 'ix-02', {
            'destino': 'ix-02', 'opcoes': {
                'nome': 'PTT-CE', 'peer_as': '26162', 'grupo': '602',
                'asn_community': ASN, 'prepend_as': '268080',
                'v4': {'peers': [{'ip': '45.68.79.253'}]}}})
        mapa = montar_mapa(dados)
        c = mapa['circuitos']['ix-02']
        self.assertEqual(len(c['sessoes']), 1)
        self.assertEqual(c['faltando'], [])
        self.assertNotIn('ix-02', {v['id'] for v in mapa['slots_vagos']})

    def test_downstream_novo_aparece_com_os_destinos(self):
        dados = _dados_com_cliente()
        aplicar_efeito_local(dados, 'criar_downstream_community', 'ACME', {
            'opcoes': {'nome': 'ACME', 'peer_as': '268999',
                       'destinos': {'glob-all-ptts-ixbr': 'export'},
                       'v4': {'peers': [{'ip': '192.0.2.30'}],
                              'prefixos': ['170.84.0.0/22']}}})
        mapa = montar_mapa(dados)
        d = mapa['downstreams'].get('DOWNSTREAM-ACME')
        self.assertIsNotNone(d)
        self.assertEqual(d['destinos'], {'glob-all-ptts-ixbr': 'export'})
