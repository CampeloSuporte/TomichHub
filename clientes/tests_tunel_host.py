"""Túnel de acesso por host: cliente com mais de um túnel (proxy SSH e/ou
OpenVPN, cada um numa rede) escolhe no host por qual chegar nele."""
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clientes import openvpn_tunnel_manager as ovpnm
from clientes import tunel_host, vpn_rota
from clientes.models import Acesso, Cliente, ProxyServer, VPNOpenVPN
from usuario.models import TOTPDevice


class _Base(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nome_empresa='Cliente 2 Redes', cnpj='55.555.555/0001-55',
                                              endereco='Rua A', email='duas@example.com')
        self.outro = Cliente.objects.create(nome_empresa='Outro', cnpj='66.666.666/0001-66',
                                            endereco='Rua B', email='outro@example.com')
        self.proxy = ProxyServer.objects.create(cliente=self.cliente, nome='Debian A', host='200.1.1.1',
                                                usuario='u', senha='s')
        self.vpn1 = self._vpn(1, '10.0.0.0/8\n192.168.0.0/16')
        self.vpn2 = self._vpn(2, '192.168.50.0/24')
        self.acesso = Acesso.objects.create(cliente=self.cliente, tipo='OLT B', host='192.168.50.10',
                                            porta=22, protocolo='SSH', usuario='a', senha='b')

    def _vpn(self, n, redes, cliente=None):
        return VPNOpenVPN.objects.create(
            cliente=cliente or self.cliente, nome=f'MK {n}', common_name=f'cn-th-{n}',
            redes_privadas=redes, vpn_ip=ovpnm._client_ip(n), porta=1195 + n,
            interface_nome=f'server-crm-{n}', subnet_n=n, cert_emitido=True,
        )

    @staticmethod
    def _rotas_ok():
        """Toda tabela isolada entrega o IP pela própria tun."""
        devs = {ovpnm.origem_tunel(v): ovpnm.dev_tun(v) for v in VPNOpenVPN.objects.all()}
        return mock.patch.object(ovpnm, 'rota_dev_para', side_effect=lambda h, origem: devs.get(origem))


class EscolhaTest(_Base):
    def test_sem_escolha_proxy_e_o_primeiro_ativo(self):
        self.assertEqual(tunel_host.proxy_para(self.cliente, self.acesso.host, self.acesso), self.proxy)

    def test_automatico_prefere_rede_mais_especifica(self):
        with self._rotas_ok():
            self.assertEqual(vpn_rota.tunel_para(self.cliente, '192.168.50.10'), self.vpn2)
            self.assertEqual(vpn_rota.tunel_para(self.cliente, '192.168.1.1'), self.vpn1)

    def test_host_fixado_em_vpn(self):
        self.acesso.tunel_vpn = self.vpn1
        self.acesso.save()
        self.assertIsNone(tunel_host.proxy_para(self.cliente, self.acesso.host, self.acesso))
        with self._rotas_ok():
            # mesmo com a vpn2 mais específica, vale a escolha do host
            self.assertEqual(vpn_rota.tunel_para(self.cliente, self.acesso.host, self.acesso), self.vpn1)
            # sem o objeto: acha a escolha pelo IP entre os hosts do cliente
            self.assertEqual(vpn_rota.tunel_para(self.cliente, '192.168.50.10'), self.vpn1)

    def test_vpn_fixada_fora_do_ar_nao_cai_em_outra_rede(self):
        self.acesso.tunel_vpn = self.vpn1
        self.acesso.save()
        with mock.patch.object(ovpnm, 'rota_dev_para', return_value='eth0'):
            self.assertEqual(vpn_rota.tunel_para(self.cliente, self.acesso.host, self.acesso), self.vpn1)

    def test_host_fixado_em_proxy(self):
        self.acesso.tunel_proxy = self.proxy
        self.acesso.save()
        with self._rotas_ok():
            self.assertIsNone(vpn_rota.tunel_para(self.cliente, self.acesso.host, self.acesso))
        self.assertEqual(tunel_host.proxy_para(self.cliente.id, '192.168.50.10'), self.proxy)

    def test_tunel_desativado_volta_ao_automatico(self):
        self.acesso.tunel_vpn = self.vpn1
        self.acesso.save()
        self.vpn1.ativo = False
        self.vpn1.save()
        self.assertEqual(tunel_host.escolha(self.cliente, acesso=self.acesso), (None, None))
        with self._rotas_ok():
            self.assertEqual(vpn_rota.tunel_para(self.cliente, self.acesso.host, self.acesso), self.vpn2)

    def test_aplicar_valor_recusa_tunel_de_outro_cliente(self):
        alheia = self._vpn(3, '172.16.0.0/12', cliente=self.outro)
        self.assertTrue(tunel_host.aplicar_valor(self.acesso, f'vpn:{alheia.id}'))
        self.assertIsNone(self.acesso.tunel_vpn)
        self.assertTrue(tunel_host.aplicar_valor(self.acesso, 'xpto:1'))
        self.assertEqual(tunel_host.aplicar_valor(self.acesso, f'proxy:{self.proxy.id}'), '')
        self.assertEqual(self.acesso.tunel_proxy, self.proxy)
        self.assertEqual(tunel_host.valor_do_acesso(self.acesso), f'proxy:{self.proxy.id}')

    def test_lista_de_tuneis_so_ativos(self):
        self.vpn2.ativo = False
        self.vpn2.save()
        valores = [v for v, _ in tunel_host.tuneis_do_cliente(self.cliente)]
        self.assertEqual(valores, [f'proxy:{self.proxy.id}', f'vpn:{self.vpn1.id}'])


class TelaTest(_Base):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user('admin_tunel', password='x', is_staff=True, is_superuser=True)
        TOTPDevice.objects.create(usuario=self.admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(self.admin)

    def _post_editar(self, **extra):
        dados = {'tipo': 'OLT B', 'hostname': self.acesso.host, 'protocolo': 'SSH', 'porta': '22',
                 'usuario': 'a', 'senha': 'b'}
        dados.update(extra)
        return self.client.post(reverse('editar_acesso', args=[self.acesso.id]), dados,
                                HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_editar_grava_e_buscar_devolve(self):
        r = self._post_editar(tunel=f'vpn:{self.vpn2.id}')
        self.assertTrue(r.json()['success'])
        self.acesso.refresh_from_db()
        self.assertEqual(self.acesso.tunel_vpn, self.vpn2)
        r = self.client.get(reverse('buscar_acesso', args=[self.acesso.id]))
        self.assertEqual(r.json()['tunel'], f'vpn:{self.vpn2.id}')

    def test_editar_sem_o_campo_preserva_escolha(self):
        self.acesso.tunel_proxy = self.proxy
        self.acesso.save()
        self.assertTrue(self._post_editar().json()['success'])
        self.acesso.refresh_from_db()
        self.assertEqual(self.acesso.tunel_proxy, self.proxy)

    def test_select_so_aparece_com_dois_tuneis(self):
        url = reverse('listar_clientes') + f'?id={self.cliente.id}'
        self.assertContains(self.client.get(url), 'id="dup1_tunel"')
        self.vpn1.delete()
        self.vpn2.delete()
        self.assertNotContains(self.client.get(url), 'id="dup1_tunel"')
