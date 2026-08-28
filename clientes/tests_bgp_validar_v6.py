"""
Consulta ao vivo de anúncios em sessão IPv6 — os comandos e o parser de
prefixos precisam mudar de address-family quando o peer é v6, senão o
equipamento responde a tabela errada (ou recusa o comando) e a consulta
volta vazia (bug real: sessão RS1.PTT-CE-V6 do acesso 20, Huawei, que
anuncia 4 prefixos e aparecia com "Anunciados (0)").
"""
from django.test import SimpleTestCase

from clientes.bgp_actions import (
    _contar_prepends,
    _extrair_anuncios,
    _extrair_prefixos,
    comando_contar_recebidos,
    comandos_validar_anuncios,
)

SESSAO_V6 = {'nome': '2001:12F8:0:9::253', 'peer_ip': '2001:12F8:0:9::253'}
SESSAO_V4 = {'nome': '45.68.79.253', 'peer_ip': '45.68.79.253'}


class ComandosValidarV6Test(SimpleTestCase):
    def test_huawei_usa_arvore_ipv6(self):
        cmds = comandos_validar_anuncios('huawei', {}, SESSAO_V6)
        self.assertEqual(
            cmds['anunciados'],
            'display bgp ipv6 routing-table peer 2001:12F8:0:9::253 advertised-routes',
        )
        self.assertEqual(
            cmds['recebidos'],
            'display bgp ipv6 routing-table peer 2001:12F8:0:9::253 received-routes',
        )
        cmd_contagem, _ = comando_contar_recebidos('huawei', {}, SESSAO_V6)
        self.assertEqual(cmd_contagem, 'display bgp ipv6 peer 2001:12F8:0:9::253 verbose')

    def test_huawei_v4_nao_muda(self):
        cmds = comandos_validar_anuncios('huawei', {}, SESSAO_V4)
        self.assertEqual(
            cmds['anunciados'],
            'display bgp routing-table peer 45.68.79.253 advertised-routes',
        )
        cmd_contagem, _ = comando_contar_recebidos('huawei', {}, SESSAO_V4)
        self.assertEqual(cmd_contagem, 'display bgp peer 45.68.79.253 verbose')

    def test_cisco_usa_address_family_explicita(self):
        cmds = comandos_validar_anuncios('cisco', {}, SESSAO_V6)
        base = 'show bgp ipv6 unicast neighbors 2001:12F8:0:9::253'
        self.assertEqual(cmds['anunciados'], f'{base} advertised-routes')
        self.assertEqual(cmds['recebidos'], f'{base} received-routes')
        self.assertEqual(cmds['recebidos_fallback'], f'{base} routes')
        cmd_contagem, _ = comando_contar_recebidos('cisco', {}, SESSAO_V6)
        self.assertEqual(cmd_contagem, f'{base} | include Prefixes Current')

    def test_cisco_v4_nao_muda(self):
        cmds = comandos_validar_anuncios('cisco', {}, SESSAO_V4)
        self.assertEqual(cmds['anunciados'], 'show ip bgp neighbors 45.68.79.253 advertised-routes')
        self.assertEqual(cmds['recebidos_fallback'], 'show ip bgp neighbors 45.68.79.253 routes')

    def test_mikrotik_recebidos_v6_vem_de_ipv6_route(self):
        cmds_v6 = comandos_validar_anuncios('mikrotik', {'versao_routeros': 6}, SESSAO_V6)
        self.assertEqual(
            cmds_v6['recebidos'],
            '/ipv6 route print where received-from="2001:12F8:0:9::253"',
        )
        cmds_v7 = comandos_validar_anuncios('mikrotik', {'versao_routeros': 7}, SESSAO_V6)
        self.assertEqual(
            cmds_v7['recebidos'],
            '/ipv6 route print where gateway=2001:12F8:0:9::253 bgp=yes',
        )
        cmds_v4 = comandos_validar_anuncios('mikrotik', {'versao_routeros': 6}, SESSAO_V4)
        self.assertEqual(cmds_v4['recebidos'], '/ip route print where received-from="45.68.79.253"')


class ExtrairPrefixosV6Test(SimpleTestCase):
    def test_huawei_network_prefixlen(self):
        # Saída real do acesso 20 (BDR-DNO), sessão RS1.PTT-CE-V6.
        saida = """
 Total Number of Routes: 2
 *>     Network  : 2804:57B0::                              PrefixLen : 34
        NextHop  : 2001:12F8:0:9::146:25                    LocPrf    :
        MED      : 0                                        PrefVal   : 0
 *>     Network  : 2804:57B0:4000::                         PrefixLen : 34
        NextHop  : 2001:12F8:0:9::146:25                    LocPrf    :
"""
        self.assertEqual(
            _extrair_prefixos(saida),
            ['2804:57B0::/34', '2804:57B0:4000::/34'],
        )

    def test_cidr_v6_e_default_route_v6(self):
        saida = '*>i 2804:57B0:EFF0::/44   2804:3360::1\n*> ::/0   2804:3360::1\n'
        self.assertEqual(_extrair_prefixos(saida), ['2804:57B0:EFF0::/44', '::/0'])

    def test_mikrotik_dst_address(self):
        saida = ' 0 ADb dst-address=2804:7240::/32 gateway=2804:4bc:99:d::1\n'
        self.assertEqual(_extrair_prefixos(saida), ['2804:7240::/32'])

    def test_v4_continua_funcionando(self):
        saida = ' *>  45.169.4.0/24   0.0.0.0   0   100   0   i\n'
        self.assertEqual(_extrair_prefixos(saida), ['45.169.4.0/24'])

    def test_ignora_token_que_nao_e_ipv6(self):
        # "Path/Ogn : 268080i" e afins não podem virar prefixo.
        self.assertEqual(_extrair_prefixos(' Path/Ogn : 268080i\n Label : 3/4\n'), [])


class AsPathDoAnuncioTest(SimpleTestCase):
    """AS-path de cada prefixo ANUNCIADO — o que o peer recebe de verdade. O
    `apply as-path X X additive` da policy de saída não aparece na RIB local,
    então só a consulta ao vivo mostra quantos prepends estão saindo.

    As saídas abaixo são capturas reais, tiradas dos transcripts das sessões de
    terminal (acessos 324/A2+ e 923/DS TECH)."""

    CABECALHO = (
        ' Total Number of Routes: 1\n'
        '        Network            NextHop                       MED        LocPrf'
        '    PrefVal Path/Ogn\n\n'
    )

    def test_v4_conta_os_prepends_do_path(self):
        saida = self.CABECALHO + (
            ' *>     45.187.123.0/24    172.30.252.34                  0'
            '                     0      268546 268546 268546i\n')
        self.assertEqual(_extrair_anuncios(saida), [{
            'prefixo': '45.187.123.0/24', 'as_path': '268546 268546 268546',
            'asns': 3, 'prepends': 2}])

    def test_v4_um_prepend_e_nenhum(self):
        um = self.CABECALHO + (
            ' *>     45.187.123.0/24    172.30.252.22                  0'
            '                     0      268546 268546i\n')
        nenhum = self.CABECALHO + (
            ' *>     45.187.123.0/24    45.68.76.102                   0'
            '                     0      268546i\n')
        self.assertEqual(_extrair_anuncios(um)[0]['prepends'], 1)
        self.assertEqual(_extrair_anuncios(um)[0]['asns'], 2)
        self.assertEqual(_extrair_anuncios(nenhum)[0]['prepends'], 0)
        self.assertEqual(_extrair_anuncios(nenhum)[0]['as_path'], '268546')

    def test_colunas_numericas_nao_viram_as_path(self):
        """MED/LocPrf/PrefVal também são dígitos e podem vir vazias — uma regex
        de "cauda de dígitos" comeria o final do next-hop e as colunas junto."""
        saida = self.CABECALHO + ' *>     45.169.4.0/24   0.0.0.0   0   100   0   i\n'
        anuncio = _extrair_anuncios(saida)[0]
        self.assertEqual(anuncio['as_path'], '')
        self.assertEqual(anuncio['asns'], 0)
        self.assertEqual(anuncio['prepends'], 0)

    def test_v6_le_o_path_do_bloco_e_admite_nao_saber(self):
        """IPv6 no VRP não é tabela, é bloco por prefixo. O último bloco está
        cortado pelo `---- More ----` da paginação: sem Path/Ogn o campo vem
        `None` — "não consegui ler" não pode virar "zero prepend"."""
        saida = (
            ' *>     Network  : 2804:57B0::                              PrefixLen : 34\n'
            '        NextHop  : 2001:12F8:0:2::54:172                    LocPrf    :\n'
            '        MED      : 0                                        PrefVal   : 0\n'
            '        Label    :\n'
            '        Path/Ogn : 268080 268080i\n'
            ' *>     Network  : 2804:57B0:8000::                         PrefixLen : 34\n'
            '        NextHop  : 2001:12F8:0:2::54:172                    LocPrf    :\n'
            '  ---- More ----\n'
        )
        self.assertEqual(_extrair_anuncios(saida), [
            {'prefixo': '2804:57B0::/34', 'as_path': '268080 268080',
             'asns': 2, 'prepends': 1},
            {'prefixo': '2804:57B0:8000::/34', 'as_path': None,
             'asns': None, 'prepends': None},
        ])

    def test_asn_diferente_no_path_nao_e_prepend(self):
        """Dois ASNs não significam um prepend: só a repetição do primeiro é."""
        self.assertEqual(_contar_prepends('268546 26162'), 0)
        self.assertEqual(_contar_prepends('268546 268546 26162'), 1)
        self.assertEqual(_contar_prepends(''), 0)

    def test_saida_de_fabricante_nao_mapeado_nao_inventa_numero(self):
        """Mikrotik: prefixo é lido, AS-path não — melhor "—" que um número
        errado na tela de conferência."""
        saida = ' 0 ADb dst-address=2804:7240::/32 gateway=2804:4bc:99:d::1\n'
        self.assertEqual(_extrair_anuncios(saida), [
            {'prefixo': '2804:7240::/32', 'as_path': None,
             'asns': None, 'prepends': None}])
