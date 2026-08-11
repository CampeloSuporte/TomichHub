import ipaddress
import json
import subprocess
from unittest import mock

from django.test import SimpleTestCase, TestCase

from clientes.models import BlocoIP, Cliente, RotaLoopExecucaoLog, RotaLoopResultado
from clientes.tasks import (
    _ampscan_status_para_porta,
    _rotaloop_detectar_loop,
    _rotaloop_executar_para_bloco,
    _rotaloop_executar_para_cliente,
    _rotaloop_ip_alvo,
    _rotaloop_mtr_json,
)


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


class RotaloopDetectarLoopTest(SimpleTestCase):
    def test_sem_repeticao_e_normal(self):
        hops = [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': '200.1.1.1'},
            {'hop': 3, 'ip': '200.1.1.2'},
        ]
        status, ip_em_loop = _rotaloop_detectar_loop(hops)
        self.assertEqual(status, 'normal')
        self.assertIsNone(ip_em_loop)

    def test_ip_repetido_consecutivo_e_loop(self):
        hops = [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': '200.1.1.1'},
            {'hop': 3, 'ip': '200.1.1.1'},
        ]
        status, ip_em_loop = _rotaloop_detectar_loop(hops)
        self.assertEqual(status, 'loop_detectado')
        self.assertEqual(ip_em_loop, '200.1.1.1')

    def test_ip_repetido_nao_consecutivo_tambem_e_loop(self):
        hops = [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': '200.1.1.1'},
            {'hop': 3, 'ip': '200.1.1.2'},
            {'hop': 4, 'ip': '200.1.1.1'},
        ]
        status, ip_em_loop = _rotaloop_detectar_loop(hops)
        self.assertEqual(status, 'loop_detectado')
        self.assertEqual(ip_em_loop, '200.1.1.1')

    def test_hops_sem_resposta_nao_contam_como_repeticao(self):
        hops = [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': None},
            {'hop': 3, 'ip': None},
            {'hop': 4, 'ip': '200.1.1.2'},
        ]
        status, ip_em_loop = _rotaloop_detectar_loop(hops)
        self.assertEqual(status, 'normal')
        self.assertIsNone(ip_em_loop)

    def test_lista_vazia_e_normal(self):
        status, ip_em_loop = _rotaloop_detectar_loop([])
        self.assertEqual(status, 'normal')
        self.assertIsNone(ip_em_loop)


MTR_JSON_EXEMPLO = json.dumps({
    "report": {
        "mtr": {"src": "servidor", "dst": "200.1.1.1"},
        "hubs": [
            {"count": 1, "host": "10.0.0.1", "Loss%": 0.0},
            {"count": 2, "host": "200.1.1.1", "Loss%": 0.0},
        ],
    }
})


class RotaloopMtrJsonTest(SimpleTestCase):
    @mock.patch('clientes.tasks.shutil.which', return_value='/usr/bin/mtr')
    @mock.patch('clientes.tasks.subprocess.run')
    def test_parseia_hops_do_json_do_mtr(self, mock_run, mock_which):
        mock_run.return_value = subprocess.CompletedProcess(
            args=['mtr'], returncode=0, stdout=MTR_JSON_EXEMPLO, stderr='',
        )
        hops = _rotaloop_mtr_json('200.1.1.1')
        self.assertEqual(hops, [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': '200.1.1.1'},
        ])

    @mock.patch('clientes.tasks.shutil.which', return_value=None)
    def test_levanta_erro_se_mtr_nao_instalado(self, mock_which):
        with self.assertRaises(RuntimeError):
            _rotaloop_mtr_json('200.1.1.1')

    @mock.patch('clientes.tasks.shutil.which', return_value='/usr/bin/mtr')
    @mock.patch('clientes.tasks.subprocess.run')
    def test_levanta_erro_se_json_invalido(self, mock_run, mock_which):
        mock_run.return_value = subprocess.CompletedProcess(
            args=['mtr'], returncode=0, stdout='não é json', stderr='',
        )
        with self.assertRaises(RuntimeError):
            _rotaloop_mtr_json('200.1.1.1')

    @mock.patch('clientes.tasks.shutil.which', return_value='/usr/bin/mtr')
    @mock.patch('clientes.tasks.subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='mtr', timeout=30))
    def test_levanta_erro_se_timeout(self, mock_run, mock_which):
        with self.assertRaises(RuntimeError):
            _rotaloop_mtr_json('200.1.1.1')

    @mock.patch('clientes.tasks.shutil.which', return_value='/usr/bin/mtr')
    @mock.patch('clientes.tasks.subprocess.run')
    def test_hop_sem_resposta_vira_ip_none(self, mock_run, mock_which):
        saida = json.dumps({"report": {"hubs": [
            {"count": 1, "host": "???", "Loss%": 100.0},
            {"count": 2, "host": "200.1.1.1", "Loss%": 0.0},
        ]}})
        mock_run.return_value = subprocess.CompletedProcess(
            args=['mtr'], returncode=0, stdout=saida, stderr='',
        )
        hops = _rotaloop_mtr_json('200.1.1.1')
        self.assertEqual(hops, [
            {'hop': 1, 'ip': None},
            {'hop': 2, 'ip': '200.1.1.1'},
        ])

    @mock.patch('clientes.tasks.shutil.which', return_value='/usr/bin/mtr')
    @mock.patch('clientes.tasks.subprocess.run')
    def test_hubs_null_levanta_runtimeerror_e_nao_typeerror_cru(self, mock_run, mock_which):
        saida = json.dumps({"report": {"hubs": None}})
        mock_run.return_value = subprocess.CompletedProcess(
            args=['mtr'], returncode=1, stdout=saida, stderr='mtr: unable to get raw sockets',
        )
        with self.assertRaises(RuntimeError) as ctx:
            _rotaloop_mtr_json('200.1.1.1')
        self.assertIn('unable to get raw sockets', str(ctx.exception))


class RotaloopExecutarParaBlocoTest(SimpleTestCase):
    @mock.patch('clientes.tasks._rotaloop_mtr_json')
    def test_bloco_com_loop(self, mock_mtr):
        mock_mtr.return_value = [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': '10.0.0.1'},
        ]
        bloco = mock.Mock(bloco='200.1.1.0/24')
        resultado = _rotaloop_executar_para_bloco(bloco)
        self.assertEqual(resultado['status'], 'loop_detectado')
        self.assertEqual(resultado['ip_alvo'], '200.1.1.1')
        self.assertEqual(resultado['ip_em_loop'], '10.0.0.1')
        self.assertEqual(resultado['ferramenta'], 'mtr')
        self.assertEqual(len(resultado['hops']), 2)

    @mock.patch('clientes.tasks._rotaloop_mtr_json')
    def test_bloco_sem_loop(self, mock_mtr):
        mock_mtr.return_value = [{'hop': 1, 'ip': '10.0.0.1'}]
        bloco = mock.Mock(bloco='200.1.1.0/24')
        resultado = _rotaloop_executar_para_bloco(bloco)
        self.assertEqual(resultado['status'], 'normal')
        self.assertIsNone(resultado['ip_em_loop'])

    @mock.patch('clientes.tasks._rotaloop_mtr_json', side_effect=RuntimeError('mtr ausente'))
    def test_bloco_com_erro_e_inconclusivo(self, mock_mtr):
        bloco = mock.Mock(bloco='200.1.1.0/24')
        resultado = _rotaloop_executar_para_bloco(bloco)
        self.assertEqual(resultado['status'], 'inconclusivo')
        self.assertEqual(resultado['erro'], 'mtr ausente')

    def test_bloco_ip_invalido_e_inconclusivo(self):
        bloco = mock.Mock(bloco='não-é-um-cidr')
        resultado = _rotaloop_executar_para_bloco(bloco)
        self.assertEqual(resultado['status'], 'inconclusivo')


class RotaloopExecutarParaClienteTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nome_empresa='Cliente Teste', cnpj='11.111.111/0001-11',
            endereco='Rua Teste, 1', email='cliente.teste@example.com',
        )
        self.bloco = BlocoIP.objects.create(cliente=self.cliente, tipo='IPV4', bloco='200.1.1.0/24')

    @mock.patch('clientes.tasks._rotaloop_mtr_json')
    def test_loop_detectado_persiste_resultado(self, mock_mtr):
        mock_mtr.return_value = [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': '10.0.0.1'},
        ]
        execucao = _rotaloop_executar_para_cliente(self.cliente)

        self.assertTrue(execucao.sucesso)
        self.assertEqual(execucao.total_blocos_testados, 1)
        self.assertEqual(execucao.total_loops_detectados, 1)

        resultado = RotaLoopResultado.objects.get(bloco_ip=self.bloco)
        self.assertEqual(resultado.status, 'loop_detectado')
        self.assertEqual(resultado.ip_em_loop, '10.0.0.1')
        self.assertFalse(resultado.resolvido)

    @mock.patch('clientes.tasks._rotaloop_mtr_json')
    def test_loop_que_some_marca_resolvido(self, mock_mtr):
        mock_mtr.return_value = [{'hop': 1, 'ip': '10.0.0.1'}, {'hop': 2, 'ip': '10.0.0.1'}]
        _rotaloop_executar_para_cliente(self.cliente)
        self.assertFalse(RotaLoopResultado.objects.get(bloco_ip=self.bloco).resolvido)

        mock_mtr.return_value = [{'hop': 1, 'ip': '10.0.0.1'}, {'hop': 2, 'ip': '200.1.1.1'}]
        _rotaloop_executar_para_cliente(self.cliente)

        resultado = RotaLoopResultado.objects.get(bloco_ip=self.bloco)
        self.assertTrue(resultado.resolvido)
        self.assertIsNotNone(resultado.resolvido_em)

    @mock.patch('clientes.tasks._rotaloop_mtr_json')
    def test_sem_loop_nao_persiste_linha(self, mock_mtr):
        mock_mtr.return_value = [{'hop': 1, 'ip': '10.0.0.1'}, {'hop': 2, 'ip': '200.1.1.1'}]
        execucao = _rotaloop_executar_para_cliente(self.cliente)

        self.assertEqual(execucao.total_loops_detectados, 0)
        self.assertFalse(RotaLoopResultado.objects.filter(bloco_ip=self.bloco).exists())

    def test_cliente_sem_blocos_finaliza_sem_erro(self):
        cliente_vazio = Cliente.objects.create(
            nome_empresa='Sem Blocos', cnpj='22.222.222/0001-22',
            endereco='Rua Teste, 2', email='sem.blocos@example.com',
        )
        execucao = _rotaloop_executar_para_cliente(cliente_vazio)
        self.assertTrue(execucao.sucesso)
        self.assertEqual(execucao.total_blocos_testados, 0)
