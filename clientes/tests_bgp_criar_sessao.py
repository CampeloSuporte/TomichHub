from django.test import SimpleTestCase

from clientes.backup_parser import parse_cisco, _extrair_prefix_lists_e_policies_cisco


CONFIG_CISCO_EXEMPLO = """
router bgp 268080
 bgp log-neighbor-changes
 neighbor 2804:3360:0:29::3D remote-as 262725
 neighbor 2804:3360:0:29::3D description UPSTREAM-CONECT-V6
 neighbor 172.16.8.1 remote-as 262725
 neighbor 172.16.8.1 description UPSTREAM-CONECT-V4
!
address-family ipv4
 neighbor 172.16.8.1 activate
 neighbor 172.16.8.1 send-community both
 neighbor 172.16.8.1 route-map RM-PEER-CONECT-V4-IN in
 neighbor 172.16.8.1 route-map RM-PEER-CONECT-V4-OUT out
!
address-family ipv6
 neighbor 2804:3360:0:29::3D activate
 neighbor 2804:3360:0:29::3D route-map RM-PEER-CONECT-V6-IN in
 neighbor 2804:3360:0:29::3D route-map RM-PEER-CONECT-V6-OUT out
!
ip prefix-list PL-ORIGIN-45.169.6.0_24 seq 5 permit 45.169.6.0/24
ip prefix-list PL-DEFAULT-ROUTE seq 5 permit 0.0.0.0/0
!
route-map RM-PEER-CONECT-V4-OUT permit 10
 match ip address prefix-list PL-ORIGIN-45.169.6.0_24
route-map RM-PEER-CONECT-V4-IN permit 10
 match ip address prefix-list PL-DEFAULT-ROUTE
"""


class ExtrairPrefixListsEPoliciesCiscoTest(SimpleTestCase):
    def test_extrai_prefix_lists_e_route_maps(self):
        prefix_lists, policies = _extrair_prefix_lists_e_policies_cisco(CONFIG_CISCO_EXEMPLO)

        self.assertEqual(
            prefix_lists['PL-ORIGIN-45.169.6.0_24'],
            [{'acao': 'permit', 'prefixo': '45.169.6.0/24', 'len_min': 24, 'len_max': 24, 'seq': 5}],
        )
        self.assertEqual(
            prefix_lists['PL-DEFAULT-ROUTE'],
            [{'acao': 'permit', 'prefixo': '0.0.0.0/0', 'len_min': 0, 'len_max': 0, 'seq': 5}],
        )
        self.assertEqual(policies['RM-PEER-CONECT-V4-OUT'][0]['prefix_lists'], ['PL-ORIGIN-45.169.6.0_24'])
        self.assertEqual(policies['RM-PEER-CONECT-V4-OUT'][0]['acao'], 'accept')
        self.assertEqual(policies['RM-PEER-CONECT-V4-IN'][0]['prefix_lists'], ['PL-DEFAULT-ROUTE'])

    def test_parse_cisco_continua_igual_depois_da_extracao(self):
        """Regressão: parse_cisco tem que devolver EXATAMENTE o mesmo
        prefix_lists/policies de antes do refactor (só muda ONDE o
        código mora, não o comportamento)."""
        resultado = parse_cisco(CONFIG_CISCO_EXEMPLO, nome_equip='teste')
        prefix_lists_direto, policies_direto = _extrair_prefix_lists_e_policies_cisco(CONFIG_CISCO_EXEMPLO)
        self.assertEqual(resultado['prefix_lists'], prefix_lists_direto)
        self.assertEqual(resultado['policies'], policies_direto)
