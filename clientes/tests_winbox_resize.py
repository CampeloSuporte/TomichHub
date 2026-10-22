import os
from unittest import mock

from django.test import SimpleTestCase

from clientes.winbox_vnc import WinboxVNCManager


class WinboxResizeTest(SimpleTestCase):
    """Xvfb nasce do tamanho do monitor; a área visível acompanha a janela do navegador."""

    def _manager(self, **kw):
        return WinboxVNCManager('10.0.0.1', 8291, 'u', 'p', **kw)

    def test_tela_do_monitor_e_teto_da_area_visivel(self):
        m = self._manager(width=1001, height=701, screen_width=2561, screen_height=1441)
        self.assertEqual((m.tela_w, m.tela_h), (2560, 1440))
        self.assertEqual((m.width, m.height), (1000, 700))
        self.assertEqual(m._dimensoes(5000, 3000), (2560, 1440))
        self.assertEqual(m._dimensoes(100, 100), (800, 600))

    def test_sem_tamanho_do_monitor_ainda_deixa_crescer_ate_full_hd(self):
        m = self._manager(width=1000, height=600)
        self.assertEqual((m.tela_w, m.tela_h), (1920, 1080))

    def test_margens_do_openbox_limitam_maximizacao_ao_recorte(self):
        m = self._manager(width=1000, height=600, screen_width=2560, screen_height=1440)
        try:
            m._escrever_openbox_rc()
            with open(m._openbox_rc) as f:
                xml = f.read()
        finally:
            os.unlink(m._openbox_rc)
        self.assertIn('<right>1560</right>', xml)
        self.assertIn('<bottom>840</bottom>', xml)
        self.assertIn('<maximized>true</maximized>', xml)

    def test_resize_igual_ao_atual_nao_dispara_nada(self):
        m = self._manager(width=1000, height=600)
        with mock.patch('clientes.winbox_vnc.threading.Thread') as thread:
            m.resize(1000, 600)
            thread.assert_not_called()
            m.resize(1400, 800)
            thread.assert_called_once()
