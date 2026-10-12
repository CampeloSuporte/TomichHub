"""Túneis OpenVPN com tabela de rotas isolada por cliente: a instância não
instala rede de cliente na tabela main, clientes diferentes podem declarar a
mesma faixa, e a conexão a equipamento sai com a origem do túnel."""
import socket
import threading
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase

from clientes import openvpn_tunnel_manager as ovpnm
from clientes import vpn_rota
from clientes.models import Cliente, VPNOpenVPN
from clientes.proxy_engine import ProxyEngine, _AdaptadorOrigem


def _vpn_fake(n=3, redes='10.0.0.0/8\n198.18.1.5/24'):
    return SimpleNamespace(
        interface_nome=f'server-crm-{n}', porta=1195 + n, subnet_n=n,
        common_name='c1-teste', redes_lista=lambda: [r for r in redes.split('\n') if r],
    )


class ConfInstanciaTest(SimpleTestCase):
    def test_conf_nao_instala_rotas_na_tabela_main(self):
        conf = ovpnm._gerar_conteudo_conf(_vpn_fake())
        self.assertNotRegex(conf, r'(?m)^route ')
        self.assertIn(f'up {ovpnm.SCRIPT_ROTAS}', conf)
        self.assertIn('script-security 2', conf)

    def test_arquivo_rotas_normaliza_cidr(self):
        self.assertEqual(ovpnm._gerar_conteudo_rotas(_vpn_fake()),
                         '10.0.0.0/8\n198.18.1.0/24\n')

    def test_origem_e_tabela(self):
        vpn = _vpn_fake(n=3)
        self.assertEqual(ovpnm.origem_tunel(vpn), '10.91.0.25')
        self.assertEqual(ovpnm.tabela_tunel(vpn), 1003)


class _ComClientes(TestCase):
    def setUp(self):
        self.a = Cliente.objects.create(nome_empresa='Cliente A', cnpj='11.111.111/0001-11',
                                        endereco='Rua A', email='a@example.com')
        self.b = Cliente.objects.create(nome_empresa='Cliente B', cnpj='22.222.222/0001-22',
                                        endereco='Rua B', email='b@example.com')

    def _tunel(self, cliente, n, redes):
        return VPNOpenVPN.objects.create(
            cliente=cliente, common_name=f'c{cliente.id}-{n}', redes_privadas=redes,
            vpn_ip=ovpnm._client_ip(n), porta=1195 + n, interface_nome=f'server-crm-{n}',
            subnet_n=n, cert_emitido=True,
        )


class ConflitoRedesTest(_ComClientes):
    def test_clientes_diferentes_podem_declarar_a_mesma_faixa(self):
        self._tunel(self.a, 1, '10.0.0.0/8')
        self.assertEqual(ovpnm.redes_em_conflito(['10.0.0.0/8'], cliente=self.b), [])

    def test_mesma_faixa_em_dois_tuneis_do_mesmo_cliente_conflita(self):
        self._tunel(self.a, 1, '10.0.0.0/8')
        conflitos = ovpnm.redes_em_conflito(['10.0.0.0/8'], cliente=self.a)
        self.assertEqual(len(conflitos), 1)


class TunelParaTest(_ComClientes):
    def test_consulta_a_rota_com_a_origem_do_tunel(self):
        t = self._tunel(self.a, 3, '198.18.1.0/24')
        with mock.patch.object(ovpnm, 'rota_dev_para', return_value='tun-crm-3') as rota:
            self.assertEqual(vpn_rota.tunel_para(self.a, '198.18.1.2'), t)
        rota.assert_called_once()
        self.assertEqual(rota.call_args.kwargs['origem'], '10.91.0.25')

    def test_cada_cliente_usa_o_proprio_tunel_na_mesma_faixa(self):
        ta = self._tunel(self.a, 1, '10.0.0.0/8')
        tb = self._tunel(self.b, 2, '10.0.0.0/8')
        devs = {'10.91.0.9': 'tun-crm-1', '10.91.0.17': 'tun-crm-2'}
        with mock.patch.object(ovpnm, 'rota_dev_para', side_effect=lambda h, origem: devs[origem]):
            self.assertEqual(vpn_rota.tunel_para(self.a, '10.1.1.1'), ta)
            self.assertEqual(vpn_rota.tunel_para(self.b, '10.1.1.1'), tb)

    def test_tunel_desconectado_nao_cobre(self):
        self._tunel(self.a, 3, '198.18.1.0/24')
        with mock.patch.object(ovpnm, 'rota_dev_para', return_value=None):
            self.assertIsNone(vpn_rota.tunel_para(self.a, '198.18.1.2'))

    def test_ip_fora_das_redes_do_tunel(self):
        self._tunel(self.a, 3, '198.18.1.0/24')
        with mock.patch.object(ovpnm, 'rota_dev_para') as rota:
            self.assertIsNone(vpn_rota.tunel_para(self.a, '45.235.72.113'))
        rota.assert_not_called()


class RelayTest(SimpleTestCase):
    def test_relay_entrega_com_a_origem_do_tunel(self):
        servidor = socket.socket()
        servidor.bind(('127.0.0.1', 0))
        servidor.listen(1)
        porta = servidor.getsockname()[1]
        visto = {}

        def _eco():
            conn, addr = servidor.accept()
            visto['origem'] = addr[0]
            conn.sendall(conn.recv(100).upper())
            conn.close()

        threading.Thread(target=_eco, daemon=True).start()
        tunel = SimpleNamespace(subnet_n=0)
        with mock.patch.object(vpn_rota, 'origem', return_value='127.0.0.2'):
            host, porta_local = vpn_rota.endpoint_local(tunel, '127.0.0.1', porta)
            self.assertEqual(vpn_rota.endpoint_local(tunel, '127.0.0.1', porta), (host, porta_local))
        with socket.create_connection((host, porta_local), timeout=5) as s:
            s.sendall(b'oi')
            self.assertEqual(s.recv(100), b'OI')
        self.assertEqual(visto['origem'], '127.0.0.2')
        servidor.close()


class ProxyEngineOrigemTest(SimpleTestCase):
    def test_sessao_com_origem_e_separada_e_faz_bind(self):
        sessao = ProxyEngine._sessao_direta('10.91.0.25')
        self.assertIsNot(sessao, ProxyEngine._sessao_direta())
        adaptador = sessao.get_adapter('http://198.18.1.2/')
        self.assertIsInstance(adaptador, _AdaptadorOrigem)
        self.assertEqual(adaptador.poolmanager.connection_pool_kw['source_address'], ('10.91.0.25', 0))
