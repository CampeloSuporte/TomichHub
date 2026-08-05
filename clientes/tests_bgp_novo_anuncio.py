from django.test import SimpleTestCase

from clientes.bgp_actions import AcaoBgpNaoSuportada, comandos_novo_anuncio


class ComandosNovoAnuncioCiscoTest(SimpleTestCase):
    def _dados(self):
        return {
            'sessoes': [
                {'nome': '172.16.8.1', 'policy_out': 'RM-OUT'},
            ],
            'prefix_lists': {
                'PL-DEFAULT-ROUTE': [{'acao': 'permit', 'prefixo': '0.0.0.0/0', 'len_min': 0, 'len_max': 0, 'seq': 5}],
            },
            'policies': {
                'RM-OUT': [
                    {'ordem': 10, 'prefix_lists': ['PL-DEFAULT-ROUTE'], 'acao': 'accept', 'prepend': 0, 'extra': {'seq': 10}},
                    {'ordem': 999, 'prefix_lists': [], 'acao': 'reject', 'prepend': 0, 'extra': {'seq': 999}},
                ],
            },
        }

    def test_anuncia_via_prefix_list_existente(self):
        comandos = comandos_novo_anuncio('cisco', self._dados(), '172.16.8.1', lista_escolhida='PL-DEFAULT-ROUTE')
        self.assertEqual(comandos, [
            'route-map RM-OUT permit 20',
            'match ip address prefix-list PL-DEFAULT-ROUTE',
        ])

    def test_cria_prefix_list_nova_e_anexa(self):
        comandos = comandos_novo_anuncio('cisco', self._dados(), '172.16.8.1', prefixo_novo='45.169.6.0/24')
        self.assertEqual(comandos, [
            'ip prefix-list PL-ORIGIN-45.169.6.0_24 seq 10 permit 45.169.6.0/24',
            'route-map RM-OUT permit 20',
            'match ip address prefix-list PL-ORIGIN-45.169.6.0_24',
        ])

    def test_cria_prefix_list_nova_ipv6(self):
        comandos = comandos_novo_anuncio('datacom', self._dados(), '172.16.8.1', prefixo_novo='2804:3360:6::/48')
        self.assertEqual(comandos, [
            'ipv6 prefix-list PL-ORIGIN-2804:3360:6::_48 seq 10 permit 2804:3360:6::/48',
            'route-map RM-OUT permit 20',
            'match ipv6 address prefix-list PL-ORIGIN-2804:3360:6::_48',
        ])

    def test_recusa_nome_de_prefix_list_nova_ja_em_uso(self):
        dados = self._dados()
        dados['prefix_lists']['PL-ORIGIN-45.169.6.0_24'] = [
            {'acao': 'permit', 'prefixo': '45.169.6.0/24', 'len_min': 24, 'len_max': 24, 'seq': 5},
        ]
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_novo_anuncio('cisco', dados, '172.16.8.1', prefixo_novo='45.169.6.0/24')

    def test_recusa_cidr_invalido(self):
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_novo_anuncio('cisco', self._dados(), '172.16.8.1', prefixo_novo='nao-e-um-cidr')

    def test_recusa_sem_lista_e_sem_prefixo(self):
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_novo_anuncio('cisco', self._dados(), '172.16.8.1')


class ComandosNovoAnuncioHuaweiTest(SimpleTestCase):
    def _dados(self):
        return {
            'sessoes': [
                {'nome': '10.0.0.1', 'policy_out': 'RP-OUT'},
            ],
            'prefix_lists': {
                'IPL1': [{'acao': 'permit', 'prefixo': '1.2.3.0/24', 'len_min': 24, 'len_max': 24, 'index': 10}],
            },
            'policies': {
                'RP-OUT': [
                    {'ordem': 10, 'prefix_lists': ['IPL1'], 'acao': 'accept', 'prepend': 0, 'extra': {'node': 10}},
                    {'ordem': 2000, 'prefix_lists': [], 'acao': 'reject', 'prepend': 0, 'extra': {'node': 2000}},
                ],
            },
        }

    def test_anuncia_via_prefix_list_existente(self):
        comandos = comandos_novo_anuncio('huawei', self._dados(), '10.0.0.1', lista_escolhida='IPL1')
        self.assertEqual(comandos, [
            'route-policy RP-OUT permit node 20',
            'if-match ip-prefix IPL1',
            'commit',
        ])

    def test_cria_prefix_list_nova_e_anexa(self):
        comandos = comandos_novo_anuncio('huawei', self._dados(), '10.0.0.1', prefixo_novo='9.9.9.0/24')
        self.assertEqual(comandos, [
            'ip ip-prefix PL-ORIGIN-9.9.9.0_24 index 10 permit 9.9.9.0 24',
            'route-policy RP-OUT permit node 20',
            'if-match ip-prefix PL-ORIGIN-9.9.9.0_24',
            'commit',
        ])

    def test_recusa_prefixo_novo_ipv6(self):
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_novo_anuncio('huawei', self._dados(), '10.0.0.1', prefixo_novo='2001:db8::/32')


class ComandosNovoAnuncioJuniperTest(SimpleTestCase):
    def _dados(self):
        return {
            'sessoes': [
                {'nome': '10.0.0.2', 'policy_out': 'PS-OUT'},
            ],
            'prefix_lists': {
                'JPL1': [{'acao': 'permit', 'prefixo': '1.2.3.0/24', 'len_min': 24, 'len_max': 24}],
            },
            'policies': {
                'PS-OUT': [
                    {'ordem': 0, 'prefix_lists': ['JPL1'], 'acao': 'accept', 'prepend': 0, 'extra': {'term': '10'}},
                    {'ordem': 1, 'prefix_lists': [], 'acao': 'reject', 'prepend': 0, 'extra': {'term': 'reject-all'}},
                ],
            },
        }

    def test_anuncia_via_prefix_list_existente(self):
        comandos = comandos_novo_anuncio('juniper', self._dados(), '10.0.0.2', lista_escolhida='JPL1')
        self.assertEqual(comandos, [
            'set policy-options policy-statement PS-OUT term 20 from prefix-list JPL1',
            'set policy-options policy-statement PS-OUT term 20 then accept',
            'insert policy-options policy-statement PS-OUT term 20 before term reject-all',
            'commit',
        ])

    def test_cria_prefix_list_nova_e_anexa(self):
        comandos = comandos_novo_anuncio('juniper', self._dados(), '10.0.0.2', prefixo_novo='10.10.0.0/16')
        self.assertEqual(comandos, [
            'set policy-options prefix-list PL-ORIGIN-10.10.0.0_16 10.10.0.0/16',
            'set policy-options policy-statement PS-OUT term 20 from prefix-list PL-ORIGIN-10.10.0.0_16',
            'set policy-options policy-statement PS-OUT term 20 then accept',
            'insert policy-options policy-statement PS-OUT term 20 before term reject-all',
            'commit',
        ])


class ComandosNovoAnuncioMikrotikTest(SimpleTestCase):
    def test_regra_nova_direto_na_chain(self):
        dados = {
            'sessoes': [{'nome': 'peer1', 'policy_out': 'export-peer1'}],
        }
        comandos = comandos_novo_anuncio('mikrotik', dados, 'peer1', prefixo_novo='1.2.3.0/24')
        self.assertEqual(comandos, [
            '/routing filter add action=accept chain="export-peer1" prefix="1.2.3.0/24" '
            'place-before=[find chain="export-peer1" action=discard]',
        ])
