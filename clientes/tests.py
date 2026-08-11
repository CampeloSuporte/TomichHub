import ipaddress

from django.test import SimpleTestCase

from clientes.tasks import _ampscan_status_para_porta, _rotaloop_ip_alvo


class AmpscanStatusParaPortaTest(SimpleTestCase):
    def test_porta_ssh_aberta_e_exposto_nao_vulneravel(self):
        self.assertEqual(_ampscan_status_para_porta('Open', 22, 'tcp'), 'exposto')

    def test_porta_rdp_aberta_e_exposto_nao_vulneravel(self):
        self.assertEqual(_ampscan_status_para_porta('Open', 3389, 'tcp'), 'exposto')

    def test_porta_mikrotik_aberta_continua_vulneravel(self):
        self.assertEqual(_ampscan_status_para_porta('Open', 4145, 'tcp'), 'vulneravel')

    def test_porta_snmp_aberta_continua_vulneravel(self):
        self.assertEqual(_ampscan_status_para_porta('Open', 161, 'udp'), 'vulneravel')

    def test_status_openprotected_e_sempre_protegido(self):
        self.assertEqual(_ampscan_status_para_porta('OpenProtected', 22, 'tcp'), 'protegido')
        self.assertEqual(_ampscan_status_para_porta('OpenProtected', 161, 'udp'), 'protegido')

    def test_status_desconhecido_retorna_none(self):
        self.assertIsNone(_ampscan_status_para_porta('Closed', 22, 'tcp'))
        self.assertIsNone(_ampscan_status_para_porta('Inconclusive', 22, 'tcp'))


class RotaloopIpAlvoTest(SimpleTestCase):
    def test_bloco_ipv4_normal_usa_primeiro_ip_util(self):
        net = ipaddress.ip_network('200.100.50.0/24')
        self.assertEqual(_rotaloop_ip_alvo(net), '200.100.50.1')

    def test_bloco_ipv4_barra_31_usa_network_address(self):
        net = ipaddress.ip_network('200.100.50.0/31')
        self.assertEqual(_rotaloop_ip_alvo(net), '200.100.50.0')

    def test_bloco_ipv4_barra_32_usa_network_address(self):
        net = ipaddress.ip_network('200.100.50.5/32')
        self.assertEqual(_rotaloop_ip_alvo(net), '200.100.50.5')

    def test_bloco_ipv6_normal_usa_primeiro_ip_util(self):
        net = ipaddress.ip_network('2801:80:1234::/48')
        self.assertEqual(_rotaloop_ip_alvo(net), '2801:80:1234::1')

    def test_bloco_ipv6_barra_127_usa_network_address(self):
        net = ipaddress.ip_network('2801:80:1234::/127')
        self.assertEqual(_rotaloop_ip_alvo(net), '2801:80:1234::')
