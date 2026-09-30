"""Cost OSPF por interface lido do backup (editor de topologia)."""
from django.test import SimpleTestCase

from clientes.views import _extrair_interfaces_backup


def _custos(conteudo, fabricante):
    return {i['nome']: i['ospf_cost'] for i in _extrair_interfaces_backup(conteudo, fabricante)}


class OspfCostBackupTests(SimpleTestCase):
    def test_huawei_ospf_cost_no_bloco(self):
        cfg = ('interface Vlanif45\n description P2P\n ip address 10.0.0.1 255.255.255.252\n'
               ' ospf cost 5000\n#\ninterface Vlanif46\n ip address 10.0.0.5 255.255.255.252\n#\n')
        c = _custos(cfg, 'HUAWEI')
        self.assertEqual(c['Vlanif45'], '5000')
        self.assertEqual(c['Vlanif46'], '')  # sem cost setado = não sugere o default

    def test_cisco_ip_ospf_cost(self):
        cfg = 'interface Gi0/1\n ip address 10.0.0.1 255.255.255.252\n ip ospf cost 30\n!\n'
        self.assertEqual(_custos(cfg, 'CISCO')['Gi0/1'], '30')

    def test_datacom_router_ospf_casa_l3(self):
        cfg = ('interface l3 P2P-X\n ipv4 address 10.0.0.1/30\n!\n'
               'interface l3 P2P-Y\n ipv4 address 10.0.0.5/30\n!\n'
               'router ospf 1 vrf global\n area 0\n  interface l3-P2P-X\n   cost 10000\n'
               '   network-type point-to-point\n  !\n  interface l3-P2P-Y\n   network-type point-to-point\n'
               '  !\n !\n!\nrouter static\n cost 99\n')
        c = _custos(cfg, 'DATACOM')
        self.assertEqual(c['l3 P2P-X'], '10000')
        self.assertEqual(c['l3 P2P-Y'], '')
        self.assertNotIn('l3-P2P-X', c)

    def test_mikrotik_v6_e_v7(self):
        cfg = ('/interface ethernet set [ find default-name=ether1 ] name=ether1\n'
               '/routing ospf interface add cost=300 interface="ether1 - LINK" network-type=point-to-point\n'
               '/routing ospf interface-template add area=bb cost=10 interfaces=vlan197,vlan198 networks=1.1.1.0/30\n')
        c = _custos(cfg, 'MIKROTIK')
        self.assertEqual(c['ether1 - LINK'], '300')
        self.assertEqual(c['vlan197'], '10')
        self.assertEqual(c['vlan198'], '10')

    def test_juniper_metric(self):
        cfg = ('set interfaces ae0 unit 100 family inet address 10.0.0.1/30\n'
               'set protocols ospf area 0.0.0.0 interface ae0.100 metric 50\n')
        self.assertEqual(_custos(cfg, 'JUNIPER')['ae0.100'], '50')
