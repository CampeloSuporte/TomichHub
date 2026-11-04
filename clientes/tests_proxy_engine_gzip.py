"""Túnel pede gzip ao equipamento (ProxyEngine._via_tunnel).

iDRAC9 (acesso 1798, Startnet): o Apache só guarda os JS/CSS do /restgui em
.gz e responde 404 quando o pedido não traz Accept-Encoding: gzip. O túnel
descartava o cabeçalho do navegador e não mandava nenhum — o start.html abria
e a tela de login ficava em branco.
"""
import gzip
import socket
import threading
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from clientes.proxy_engine import ProxyEngine, _TunnelConnPool


class _IdracFalso:
    """Responde como o Apache do iDRAC9: 200 gzip se pedirem gzip, senão 404."""

    CORPO = b'var loginApp = angular.module("loginApp", []);'

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(('127.0.0.1', 0))
        self.srv.listen(8)
        self.porta = self.srv.getsockname()[1]
        self.pedidos = []
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            pedido = conn.recv(65536).decode('latin-1')
            self.pedidos.append(pedido)
            if 'accept-encoding: gzip' in pedido.lower():
                corpo = gzip.compress(self.CORPO)
                cab = (b'HTTP/1.1 200 OK\r\nContent-Type: text/javascript\r\n'
                       b'Content-Encoding: gzip\r\n')
            else:
                corpo = b'<h1>Not Found</h1>'
                cab = b'HTTP/1.1 404 Not Found\r\nContent-Type: text/html\r\n'
            conn.sendall(cab + b'Content-Length: %d\r\nConnection: close\r\n\r\n' % len(corpo) + corpo)
            conn.close()

    def fechar(self):
        self.srv.close()


class ViaTunnelPedeGzipTest(SimpleTestCase):
    def setUp(self):
        self.idrac = _IdracFalso()
        self.addCleanup(self.idrac.fechar)
        self.engine = ProxyEngine(SimpleNamespace(id=9798))
        self.addCleanup(_TunnelConnPool.invalidate_proxy, 9798)
        for alvo, kw in (('clientes.proxy_engine.TunnelPortCache.get_port', {'return_value': self.idrac.porta}),
                         ('clientes.proxy_engine.ProxyEngine._pmtu_manter', {})):
            p = mock.patch(alvo, **kw)
            p.start()
            self.addCleanup(p.stop)

    def _get(self, headers):
        return self.engine.do_request('GET', 'http://198.18.1.3:80/restgui/js/loginapp.js',
                                      headers=headers, timeout=(2, 2))

    def test_asset_gzip_chega_descomprimido(self):
        resp = self._get({})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, _IdracFalso.CORPO)

    def test_accept_encoding_do_navegador_vira_o_que_sabemos_abrir(self):
        resp = self._get({'Accept-Encoding': 'gzip, deflate, br, zstd'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('accept-encoding: gzip, deflate\r\n', self.idrac.pedidos[-1].lower())
        self.assertNotIn('br', self.idrac.pedidos[-1].lower().split('accept-encoding:')[1].split('\r\n')[0])
