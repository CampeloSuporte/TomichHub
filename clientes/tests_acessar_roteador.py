"""Aba Ferramentas → "Acessar Roteador": IP + porta digitados, interface web
pelo proxy do cliente (views.roteador_web_sondar / proxy_web_roteador)."""
import json
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clientes.models import Cliente, ProxyServer
from clientes.proxy_engine import ProxyEngine
from usuario.models import TOTPDevice


class _Resp:
    def __init__(self, status=200, content=b'<html><a href="/login">x</a></html>', ct='text/html'):
        self.status_code = status
        self.content = content
        self.headers = {'Content-Type': ct}
        self.cookies_raw = []


class _Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('cli_rt', password='x')
        TOTPDevice.objects.create(usuario=self.user, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.cliente = Cliente.objects.create(
            nome_empresa='Cliente RT', cnpj='55.555.555/0001-55', endereco='Rua R', email='rt@example.com',
            usuario=self.user,
        )
        self.proxy = ProxyServer.objects.create(cliente=self.cliente, nome='PX-RT', host='10.9.9.9',
                                                usuario='u', senha='s')
        self.client.force_login(self.user)


class SondarTest(_Base):
    url = reverse('roteador_web_sondar')

    def _post(self, **corpo):
        corpo.setdefault('cliente_id', self.cliente.id)
        return self.client.post(self.url, json.dumps(corpo), content_type='application/json')

    def test_auto_cai_para_http_quando_https_nao_responde(self):
        chamadas = []

        def fake(self_eng, method, url, headers=None, body=None, timeout=None):
            chamadas.append(url)
            self_eng_tunel = self_eng.forcar_tunel and self_eng.proxy_server
            assert self_eng_tunel
            return None if url.startswith('https') else _Resp(401)

        with mock.patch.object(ProxyEngine, 'do_request', fake):
            r = self._post(ip='192.168.88.1', porta='8080', protocolo='auto')
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertEqual(chamadas, ['https://192.168.88.1:8080/', 'http://192.168.88.1:8080/'])
        self.assertEqual(j['scheme'], 'http')
        self.assertEqual(j['url'], f'/clientes/acessos/r{self.cliente.id}-192.168.88.1/web/8080/http/')

    def test_sem_porta_usa_443_e_80(self):
        chamadas = []
        with mock.patch.object(ProxyEngine, 'do_request',
                               lambda s, m, u, **k: chamadas.append(u)):
            r = self._post(ip='10.0.0.1', protocolo='auto')
        self.assertEqual(r.status_code, 502)
        self.assertEqual(chamadas, ['https://10.0.0.1:443/', 'http://10.0.0.1:80/'])

    def test_ip_invalido(self):
        with mock.patch.object(ProxyEngine, 'do_request') as dr:
            r = self._post(ip='10.0.0.1/../x', porta='80')
        self.assertEqual(r.status_code, 400)
        dr.assert_not_called()

    def test_sem_proxy_nem_vpn(self):
        self.proxy.ativo = False
        self.proxy.save()
        r = self._post(ip='10.0.0.1', porta='80')
        self.assertEqual(r.status_code, 400)

    def test_outro_cliente_negado(self):
        outro = User.objects.create_user('rt_outro', password='x')
        TOTPDevice.objects.create(usuario=outro, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(outro)
        self.assertEqual(self._post(ip='10.0.0.1').status_code, 403)


class ProxyRoteadorTest(_Base):
    def test_reescreve_para_o_prefixo_do_roteador(self):
        vistos = {}

        def fake(self_eng, method, url, headers=None, body=None, timeout=None):
            vistos['url'] = url
            vistos['tunel'] = self_eng.forcar_tunel
            return _Resp()

        base = f'/clientes/acessos/r{self.cliente.id}-200.1.2.3/web/443/https'
        with mock.patch.object(ProxyEngine, 'do_request', fake):
            r = self.client.get(base + '/webfig/?x=1')
        self.assertEqual(r.status_code, 200)
        # IP público também sai pelo proxy do cliente
        self.assertTrue(vistos['tunel'])
        self.assertEqual(vistos['url'], 'https://200.1.2.3:443/webfig/?x=1')
        self.assertIn(f'href="{base}/login"', r.content.decode())

    def test_redirect_fica_no_proxy(self):
        resp = _Resp(302)
        resp.headers['Location'] = 'https://10.0.0.1/login'
        with mock.patch.object(ProxyEngine, 'do_request', lambda *a, **k: resp):
            r = self.client.get(f'/clientes/acessos/r{self.cliente.id}-10.0.0.1/web/80/http/')
        self.assertEqual(r['Location'], f'/clientes/acessos/r{self.cliente.id}-10.0.0.1/web/443/https/login')

    def test_outro_cliente_negado(self):
        outro = User.objects.create_user('rt_outro2', password='x')
        TOTPDevice.objects.create(usuario=outro, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(outro)
        with mock.patch.object(ProxyEngine, 'do_request') as dr:
            r = self.client.get(f'/clientes/acessos/r{self.cliente.id}-10.0.0.1/web/80/http/')
        self.assertEqual(r.status_code, 403)
        dr.assert_not_called()

    def test_proxy_base_aceito_no_rewrite_de_url_absoluta(self):
        html = ProxyEngine._rewrite_urls_absolutas(
            'x http://10.0.0.1:8291/a', f'/clientes/acessos/r7-10.0.0.1/web/80/http', '10.0.0.1')
        self.assertEqual(html, 'x /clientes/acessos/r7-10.0.0.1/web/8291/http/a')


class MensagemTunelSemRedeTest(_Base):
    def test_tunel_ativo_sem_a_rede_explica(self):
        from clientes.models import VPNOpenVPN
        from clientes.views import caminho_roteador
        self.proxy.delete()
        VPNOpenVPN.objects.create(cliente=self.cliente, nome='VPN MikroTik', ativo=True, redes_privadas='10.0.0.0/24', vpn_ip='10.91.0.99')
        _, _, erro = caminho_roteador(self.cliente, '100.65.0.52')
        self.assertIn('nenhuma das redes', erro)
        self.assertIn('VPN MikroTik', erro)
