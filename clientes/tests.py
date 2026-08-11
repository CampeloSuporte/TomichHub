from django.test import SimpleTestCase

from clientes.tasks import _ampscan_status_para_porta


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
