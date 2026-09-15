"""Buraco negro de PMTU no servidor do proxy SSH (ProxyEngine._pmtu_*).

ProxyIsp da DS TECH (acesso 1455) com MTU 4096 num caminho de 1500: o HTML
pequeno passava, CSS/JS travavam sem nenhum byte e viravam 502.
"""
import socket
import threading
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from clientes.proxy_engine import ProxyEngine, _TunnelConnPool


class _Canal:
    def __init__(self, saida: bytes):
        self._saida = [saida]
        self.comando = None

    def settimeout(self, _):
        pass

    def exec_command(self, cmd):
        self.comando = cmd

    def recv(self, _):
        return self._saida.pop() if self._saida else b''

    def close(self):
        pass


class PmtuSondarTest(SimpleTestCase):
    def _engine(self, saida: bytes):
        engine = ProxyEngine(SimpleNamespace(id=35))
        canal = _Canal(saida)
        transport = mock.Mock()
        transport.open_session.return_value = canal
        return engine, canal, transport

    def test_rota_com_mtu_aprendido(self):
        engine, canal, transport = self._engine(
            b'10.0.100.200 via 186.235.160.100 dev ens18 src 186.235.160.41 uid 1000 \n'
            b'    cache expires 596sec mtu 1500 \n')
        with mock.patch.object(ProxyEngine._ssh_pool, 'get_transport', return_value=transport):
            self.assertTrue(engine._pmtu_sondar('10.0.100.200'))
        self.assertIn('ping -c1 -W1 -M do -s 1473 10.0.100.200', canal.comando)

    def test_rota_sem_mtu_nao_conta(self):
        engine, _, transport = self._engine(
            b'10.0.100.200 via 186.235.160.100 dev ens18 src 186.235.160.41 uid 1000 \n    cache \n')
        with mock.patch.object(ProxyEngine._ssh_pool, 'get_transport', return_value=transport):
            self.assertFalse(engine._pmtu_sondar('10.0.100.200'))

    def test_proxy_sem_exec_nao_derruba(self):
        engine = ProxyEngine(SimpleNamespace(id=35))
        with mock.patch.object(ProxyEngine._ssh_pool, 'get_transport', side_effect=OSError('sem shell')):
            self.assertFalse(engine._pmtu_sondar('10.0.100.200'))

    def test_ipv6_nao_sonda(self):
        engine = ProxyEngine(SimpleNamespace(id=35))
        with mock.patch.object(ProxyEngine._ssh_pool, 'get_transport') as get:
            self.assertFalse(engine._pmtu_sondar('fd00::1'))
        get.assert_not_called()


class _ServidorQueTrava:
    """1ª conexão lê o pedido e nunca responde (segmento grande sumindo no
    caminho); as seguintes respondem 200 — como depois do priming."""

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(('127.0.0.1', 0))
        self.srv.listen(8)
        self.porta = self.srv.getsockname()[1]
        self.conexoes = 0
        self._presas = []
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            self.conexoes += 1
            conn.recv(65536)
            if self.conexoes == 1:
                self._presas.append(conn)
                continue
            conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
            conn.close()

    def fechar(self):
        for c in self._presas:
            c.close()
        self.srv.close()


class ViaTunnelRefazAposPmtuTest(SimpleTestCase):
    def setUp(self):
        self.servidor = _ServidorQueTrava()
        self.addCleanup(self.servidor.fechar)
        self.engine = ProxyEngine(SimpleNamespace(id=9035))
        self.addCleanup(_TunnelConnPool.invalidate_proxy, 9035)
        self.addCleanup(ProxyEngine._pmtu_primado.clear)
        patch = mock.patch('clientes.proxy_engine.TunnelPortCache.get_port',
                           return_value=self.servidor.porta)
        patch.start()
        self.addCleanup(patch.stop)

    def _get(self):
        return self.engine.do_request('GET', 'http://10.9.9.9:80/assets/index.css', timeout=(1, 1))

    def test_timeout_com_mtu_aprendido_refaz_em_conexao_nova(self):
        with mock.patch.object(ProxyEngine, '_pmtu_sondar', return_value=True) as sondar:
            resp = self._get()
        sondar.assert_called_once_with('10.9.9.9')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'ok')
        self.assertEqual(self.servidor.conexoes, 2)
        self.assertIn((9035, '10.9.9.9'), ProxyEngine._pmtu_primado)

    def test_timeout_sem_mtu_aprendido_nao_refaz(self):
        with mock.patch.object(ProxyEngine, '_pmtu_sondar', return_value=False):
            self.assertIsNone(self._get())
        self.assertEqual(self.servidor.conexoes, 1)
        self.assertNotIn((9035, '10.9.9.9'), ProxyEngine._pmtu_primado)

    def test_par_marcado_renova_o_priming_antes_de_expirar(self):
        ProxyEngine._pmtu_primado[(9035, '10.9.9.9')] = 0  # último priming há muito tempo
        with mock.patch.object(ProxyEngine, '_pmtu_sondar', return_value=True) as sondar:
            self.servidor.conexoes = 1  # pula a conexão que trava
            resp = self._get()
        sondar.assert_called_once_with('10.9.9.9')
        self.assertEqual(resp.status_code, 200)
