"""platform_ssh_exec: RouterOS vai por exec sem PTY, o resto por shell interativo."""
import socket
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from clientes import consumers as c


def _acesso(fabricante=None, tipo='SW BORDA', host='200.200.200.1'):
    modelo = SimpleNamespace(fabricante=fabricante) if fabricante else None
    return SimpleNamespace(modelo=modelo, tipo=tipo, host=host, porta=22,
                           usuario='admin', senha='x', cliente=object())


class _Canal:
    """Canal de exec que entrega os blocos e fecha (ou estoura timeout)."""

    def __init__(self, blocos):
        self.blocos = list(blocos)
        self.comando = None

    def set_combine_stderr(self, _):
        pass

    def settimeout(self, _):
        pass

    def exec_command(self, comando):
        self.comando = comando

    def recv(self, _):
        bloco = self.blocos.pop(0) if self.blocos else b''
        if bloco is socket.timeout:
            raise socket.timeout()
        return bloco


class SondaRouterOSTest(SimpleTestCase):
    def test_reconhece_a_sonda_de_terminal(self):
        # Capturado de um RouterOS 7.16.1 logo após o invoke_shell.
        self.assertTrue(c._ROUTEROS_PROBE_RE.search(b'\r\x1b[9999B\r\x1b[9999B\x1bZ  \x1b[6n'))

    def test_ignora_prompt_comum(self):
        self.assertIsNone(c._ROUTEROS_PROBE_RE.search(b'\r\nInfo: last login...\r\n<NE8K>'))


class RoteamentoTest(SimpleTestCase):
    def setUp(self):
        self.routeros = self.enterContext(
            mock.patch.object(c, '_routeros_exec', return_value='saida-exec'))
        self.pexpect = self.enterContext(
            mock.patch.object(c, '_pexpect_exec', return_value='saida-shell'))
        self.via_proxy = self.enterContext(
            mock.patch.object(c, '_paramiko_proxy_exec', return_value='saida-shell-proxy'))

    def test_mikrotik_vai_por_exec(self):
        acesso = _acesso('Mikrotik')
        self.assertEqual(c.platform_ssh_exec(acesso, '/interface print'), 'saida-exec')
        self.routeros.assert_called_once_with(acesso, '/interface print', 25, proxy=None)
        self.pexpect.assert_not_called()

    def test_mikrotik_so_no_tipo(self):
        self.assertEqual(c.platform_ssh_exec(_acesso(tipo='MIKROTIK BORDA'), '/log print'),
                         'saida-exec')

    def test_outro_fabricante_segue_no_shell(self):
        self.assertEqual(c.platform_ssh_exec(_acesso('Huawei'), 'display clock'), 'saida-shell')
        self.routeros.assert_not_called()

    def test_mikrotik_privado_usa_o_proxy_do_cliente(self):
        acesso = _acesso('Mikrotik', host='172.24.65.18')
        proxy = object()
        with mock.patch.object(c, '_tunel_ovpn_cobre', return_value=None), \
                mock.patch('clientes.models.ProxyServer.objects') as objetos:
            objetos.filter.return_value.first.return_value = proxy
            c.platform_ssh_exec(acesso, '/interface print')
        self.routeros.assert_called_once_with(acesso, '/interface print', 25, proxy=proxy)
        self.via_proxy.assert_not_called()

    def test_sem_fabricante_cai_para_exec_quando_o_shell_detecta_routeros(self):
        self.pexpect.side_effect = c._RouterOSDetectado()
        acesso = _acesso()
        self.assertEqual(c.platform_ssh_exec(acesso, '/interface print'), 'saida-exec')
        self.routeros.assert_called_once_with(acesso, '/interface print', 25, proxy=None)


class RouterOSExecTest(SimpleTestCase):
    def _executar(self, blocos, comando='/system identity print', timeout=25):
        canal = _Canal(blocos)
        transport = mock.MagicMock()
        transport.open_session.return_value = canal
        with mock.patch.object(c.socket, 'create_connection'), \
                mock.patch.object(c.paramiko, 'Transport', return_value=transport):
            saida = c._routeros_exec(_acesso('Mikrotik'), comando, timeout)
        transport.close.assert_called_once()
        return saida, canal

    def test_devolve_a_saida_sem_cr_e_preserva_o_alinhamento(self):
        saida, _ = self._executar([b'  name: REG_', b'TUTLANDIA\r\n\r\n'])
        self.assertEqual(saida, '  name: REG_TUTLANDIA')

    def test_varias_linhas_vao_num_exec_so(self):
        _, canal = self._executar([b''], comando='/interface print\n\n  /log print  ')
        self.assertEqual(canal.comando, '/interface print\n/log print')

    def test_comando_sem_saida_nao_volta_vazio(self):
        saida, _ = self._executar([b'\r\n'])
        self.assertIn('não devolveu saída', saida)

    def test_comando_que_nao_termina_devolve_o_parcial_com_aviso(self):
        saida, _ = self._executar([b'seq=1\r\n', socket.timeout])
        self.assertTrue(saida.startswith('seq=1\n[saída interrompida'))
