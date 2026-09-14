"""Caminho direto do ProxyEngine (IP público ou coberto por OpenVPN): sessão
HTTP compartilhada reaproveita a conexão com o equipamento, mas nunca guarda
cookie — a sessão é a mesma para todos os usuários e acessos."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from django.test import SimpleTestCase

from clientes.proxy_engine import ProxyEngine


class _Equipamento(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'   # keep-alive, como os servidores web de equipamento
    vistos = []                     # (porta do cliente, header Cookie)

    def do_GET(self):
        _Equipamento.vistos.append((self.client_address[1], self.headers.get('Cookie')))
        corpo = b'ok'
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(corpo)))
        self.send_header('Set-Cookie', 'SID=segredo-do-usuario-a; Path=/')
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *args):
        pass


class SessaoDiretaTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.servidor = ThreadingHTTPServer(('127.0.0.1', 0), _Equipamento)
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.servidor.server_address[1]}/'

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()
        super().tearDownClass()

    def setUp(self):
        _Equipamento.vistos = []

    def test_reaproveita_a_conexao_com_o_equipamento(self):
        for _ in range(3):
            r = ProxyEngine(None).do_request(method='GET', url=self.url, headers={})
            self.assertEqual(r.status_code, 200)
        portas = {porta for porta, _ in _Equipamento.vistos}
        self.assertEqual(len(portas), 1, 'cada requisição abriu uma conexão nova')

    def test_nao_guarda_cookie_do_equipamento_entre_requisicoes(self):
        r = ProxyEngine(None).do_request(method='GET', url=self.url, headers={})
        # o Set-Cookie continua chegando à view, que o repassa ao browser do usuário
        self.assertTrue(any('segredo-do-usuario-a' in c for c in r.cookies_raw))
        # outro usuário, sem cookie, na mesma sessão compartilhada
        ProxyEngine(None).do_request(method='GET', url=self.url, headers={})
        self.assertEqual([c for _, c in _Equipamento.vistos], [None, None])
        self.assertEqual(len(ProxyEngine._sessao_direta().cookies), 0)

    def test_cookie_do_browser_ainda_e_repassado(self):
        ProxyEngine(None).do_request(method='GET', url=self.url, headers={'Cookie': 'SID=do-browser'})
        self.assertEqual(_Equipamento.vistos[-1][1], 'SID=do-browser')
