import socket
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from clientes.consumers import _ProxyPool
from clientes.winbox_vnc import WinboxVNCManager

TEMPLATE = Path(__file__).resolve().parent / 'templates' / 'winbox.html'


class WinboxLatenciaTest(SimpleTestCase):
    """Ajustes medidos de fluidez do WinBox Web (ver docs/winbox_vnc.md)."""

    def test_x11vnc_sobe_com_polling_e_envio_de_5ms(self):
        m = WinboxVNCManager('10.0.0.1', 8291, 'u', 'p')
        with mock.patch('clientes.winbox_vnc.subprocess.Popen') as popen, \
             mock.patch.object(m, '_wait_for_socket', return_value=True), \
             mock.patch.object(m, '_wait_for_port', return_value=True), \
             mock.patch.object(m, '_escrever_openbox_rc'):
            m.start()
        vnc = next(c.args[0] for c in popen.call_args_list if c.args[0][0] == 'x11vnc')
        self.assertIn('-threads', vnc)
        self.assertEqual(vnc[vnc.index('-wait') + 1], '5')
        self.assertEqual(vnc[vnc.index('-defer') + 1], '5')
        self.assertNotIn('-ncache', vnc)

    def test_pool_do_proxy_desliga_nagle(self):
        transport = mock.Mock()
        client = mock.Mock(get_transport=mock.Mock(return_value=transport))
        proxy = mock.Mock(usuario='u', host='h', porta=22)
        _ProxyPool().put(proxy, client)
        transport.sock.setsockopt.assert_called_with(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def test_noVNC_do_winbox_nao_pede_jpeg(self):
        html = TEMPLATE.read_text()
        self.assertIn("if (vncMode === 'winbox')", html)
        self.assertIn('e < -32 || e > -23', html)
