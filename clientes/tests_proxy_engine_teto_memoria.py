"""Teto de memória do corpo de resposta no proxy web (ProxyEngine).

13/11/2026: a VM travou por falta de memória. O proxy web lia a resposta do
equipamento inteira para a RAM (r.read() / r.content), sem teto, dentro do
Daphne (processo único). Agora acima de MAX_RESPOSTA_PROXY a resposta é
recusada (None → 502) sem crescer o processo.
"""
import gzip
import socket
import threading
import zlib
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from clientes import proxy_engine
from clientes.proxy_engine import (ProxyEngine, RespostaGrandeDemais, _TunnelConnPool,
                                   _descomprimir_limitado)

TETO = 64 * 1024  # teto reduzido no teste


class _Equipamento:
    """Servidor HTTP mínimo que devolve a resposta configurada."""

    def __init__(self, resposta: bytes):
        self.resposta = resposta
        self.srv = socket.socket()
        self.srv.bind(('127.0.0.1', 0))
        self.srv.listen(8)
        self.porta = self.srv.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            try:
                conn.recv(65536)
                conn.sendall(self.resposta)
            except OSError:
                pass
            finally:
                conn.close()

    def fechar(self):
        self.srv.close()


def _http(corpo: bytes, extra: bytes = b'', com_tamanho=True) -> bytes:
    cab = b'HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n' + extra
    if com_tamanho:
        cab += b'Content-Length: %d\r\n' % len(corpo)
    return cab + b'Connection: close\r\n\r\n' + corpo


@mock.patch.object(proxy_engine, 'MAX_RESPOSTA_PROXY', TETO)
class TetoRespostaTunelTest(SimpleTestCase):
    def _get(self, resposta):
        eq = _Equipamento(resposta)
        self.addCleanup(eq.fechar)
        engine = ProxyEngine(SimpleNamespace(id=9811))
        self.addCleanup(_TunnelConnPool.invalidate_proxy, 9811)
        with mock.patch('clientes.proxy_engine.TunnelPortCache.get_port', return_value=eq.porta), \
                mock.patch('clientes.proxy_engine.ProxyEngine._pmtu_manter'), \
                mock.patch.object(ProxyEngine, '_ssh_pool') as pool:
            resp = engine.do_request('GET', 'http://198.18.1.9:80/x', headers={}, timeout=(2, 2))
        return resp, pool

    def test_resposta_dentro_do_teto_passa(self):
        resp, _ = self._get(_http(b'a' * 1000))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.content), 1000)

    def test_content_length_acima_do_teto_recusa_sem_derrubar_pool_ssh(self):
        resp, pool = self._get(_http(b'a' * (TETO + 10)))
        self.assertIsNone(resp)
        pool.invalidate.assert_not_called()

    def test_corpo_sem_tamanho_acima_do_teto_recusa(self):
        resp, _ = self._get(_http(b'a' * (TETO * 3), com_tamanho=False))
        self.assertIsNone(resp)

    def test_gzip_bomba_recusa(self):
        bomba = gzip.compress(b'\0' * (TETO * 20))
        self.assertLess(len(bomba), TETO)
        resp, _ = self._get(_http(bomba, b'Content-Encoding: gzip\r\n'))
        self.assertIsNone(resp)

    def test_gzip_normal_descomprime(self):
        resp, _ = self._get(_http(gzip.compress(b'ok' * 100), b'Content-Encoding: gzip\r\n'))
        self.assertEqual(resp.content, b'ok' * 100)


@mock.patch.object(proxy_engine, 'MAX_RESPOSTA_PROXY', TETO)
class TetoRespostaDiretoTest(SimpleTestCase):
    def _get(self, resposta):
        eq = _Equipamento(resposta)
        self.addCleanup(eq.fechar)
        return ProxyEngine(None).do_request('GET', f'http://127.0.0.1:{eq.porta}/x',
                                            headers={}, timeout=(2, 2))

    def test_direto_dentro_do_teto_passa(self):
        resp = self._get(_http(b'b' * 500))
        self.assertEqual(resp.content, b'b' * 500)

    def test_direto_acima_do_teto_recusa(self):
        self.assertIsNone(self._get(_http(b'b' * (TETO * 2), com_tamanho=False)))


@mock.patch.object(proxy_engine, 'MAX_RESPOSTA_PROXY', TETO)
class DescomprimirLimitadoTest(SimpleTestCase):
    def test_deflate_cru_e_zlib(self):
        dado = b'xyz' * 50
        cru = zlib.compressobj(wbits=-15)
        self.assertEqual(_descomprimir_limitado(cru.compress(dado) + cru.flush(), 'deflate'), dado)
        self.assertEqual(_descomprimir_limitado(zlib.compress(dado), 'deflate'), dado)

    def test_conteudo_invalido_volta_como_veio(self):
        self.assertEqual(_descomprimir_limitado(b'nao-e-gzip', 'gzip'), b'nao-e-gzip')

    def test_bomba_levanta(self):
        with self.assertRaises(RespostaGrandeDemais):
            _descomprimir_limitado(zlib.compress(b'\0' * (TETO * 10)), 'deflate')
