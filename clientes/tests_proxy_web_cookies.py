"""Cookies que o equipamento grava pelo servidor (Set-Cookie) e o proxy web
repassa ao browser: nome isolado por acesso e valor sem aspas (o Proxmox novo
respondia 401 com o ticket entre aspas)."""
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import SimpleTestCase, TestCase

from clientes.models import Acesso, Cliente
from clientes.views import _repassar_cookie_do_device
from usuario.models import TOTPDevice

TICKET_PBS = 'PBS:root@pam:66E5F0A1::aGVsbG8vd29ybGQ+Zm9v='


class RepassarCookieTest(SimpleTestCase):
    def _saida(self, set_cookie, secure=True):
        r = HttpResponse()
        _repassar_cookie_do_device(r, set_cookie, 'a1482_', secure)
        return r.cookies.output()

    def test_ticket_do_proxmox_sai_sem_aspas(self):
        saida = self._saida(f'__Host-PBSAuthCookie={TICKET_PBS}; Secure; SameSite=Lax; HttpOnly; Path=/;')
        self.assertIn(f'a1482___Host-PBSAuthCookie={TICKET_PBS};', saida)
        self.assertNotIn('"', saida)
        self.assertIn('HttpOnly', saida)
        self.assertIn('Path=/', saida)
        self.assertIn('Secure', saida)

    def test_expires_de_logout_e_mantido(self):
        saida = self._saida('__Host-PBSAuthCookie=; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; Path=/;')
        self.assertIn('a1482___Host-PBSAuthCookie=;', saida)
        self.assertIn('expires=Thu, 01 Jan 1970 00:00:00 GMT', saida)

    def test_max_age_mantido_domain_e_path_do_device_descartados(self):
        saida = self._saida('SID=abc123; Max-Age=3600; Domain=172.18.234.5; Path=/cgi-bin', secure=False)
        self.assertIn('a1482_SID=abc123;', saida)
        self.assertIn('Max-Age=3600', saida)
        self.assertNotIn('Domain', saida)
        self.assertNotIn('/cgi-bin', saida)
        self.assertNotIn('Secure', saida)
        self.assertNotIn('HttpOnly', saida)

    def test_set_cookie_invalido_e_ignorado(self):
        self.assertEqual(self._saida('sem-igual; Path=/'), '')
        self.assertEqual(self._saida('=valor'), '')
        # nome que o SimpleCookie recusa não derruba a resposta
        self.assertEqual(self._saida('nome com espaço=1'), '')

    def test_quebra_de_linha_nao_injeta_header(self):
        saida = self._saida('SID=abc\r\nX-Injetado: 1; Path=/')
        self.assertNotIn('\r\nX-Injetado', saida)


class ProxyWebRepassaCookieTest(TestCase):
    def setUp(self):
        cliente = Cliente.objects.create(
            nome_empresa='Cliente PBS', cnpj='44.444.444/0001-44',
            endereco='Rua Z, 3', email='pbs@example.com',
        )
        self.acesso = Acesso.objects.create(
            cliente=cliente, tipo='PROXMOX PBS', host='45.228.195.9',
            porta=8007, protocolo='HTTPS', usuario='root@pam', senha='s3nha',
        )
        admin = User.objects.create_user('admin_pbs', password='x', is_staff=True, is_superuser=True)
        TOTPDevice.objects.create(usuario=admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(admin)

    @mock.patch('clientes.views.ProxyEngine.do_request')
    def test_login_do_pbs_devolve_cookie_sem_aspas(self, do_request):
        do_request.return_value = SimpleNamespace(
            status_code=200, headers={'Content-Type': 'application/json'},
            content=b'{"data":{"ticket":"x"},"success":1}',
            cookies_raw=[f'__Host-PBSAuthCookie={TICKET_PBS}; Secure; SameSite=Lax; HttpOnly; Path=/;'],
        )
        r = self.client.post(f'/clientes/acessos/{self.acesso.id}/web/8007/https/api2/extjs/access/ticket',
                             data='username=root@pam&password=x', content_type='application/x-www-form-urlencoded')
        self.assertEqual(r.status_code, 200)
        morsel = r.cookies[f'a{self.acesso.id}___Host-PBSAuthCookie']
        self.assertEqual(morsel.coded_value, TICKET_PBS)
        self.assertTrue(morsel['httponly'])

    @mock.patch('clientes.views.ProxyEngine.do_request')
    def test_cookie_do_browser_volta_ao_device_sem_prefixo(self, do_request):
        do_request.return_value = SimpleNamespace(
            status_code=200, headers={'Content-Type': 'application/json'}, content=b'{}', cookies_raw=[],
        )
        # Header cru, como o browser manda: self.client.cookies usaria o
        # SimpleCookie, que põe aspas no valor (o mesmo problema do bug)
        sessao = self.client.cookies['sessionid'].value
        self.client.get(
            f'/clientes/acessos/{self.acesso.id}/web/8007/https/api2/json/version',
            HTTP_COOKIE=f'sessionid={sessao}; a{self.acesso.id}___Host-PBSAuthCookie={TICKET_PBS}; a99999_OUTRO=nao-vaza',
        )
        cookie = do_request.call_args.kwargs['headers']['Cookie']
        self.assertIn(f'__Host-PBSAuthCookie={TICKET_PBS}', cookie)
        self.assertNotIn('nao-vaza', cookie)
