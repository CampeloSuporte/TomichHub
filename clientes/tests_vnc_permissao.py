from unittest import mock

from django.test import SimpleTestCase

from clientes import consumers
from clientes.consumers import WinboxConsumer, WinboxVNCConsumer


class VncPermissaoTest(SimpleTestCase):
    """O WebSocket abre a sessão pelo id, com a senha salva: tem que checar
    permissão em todos os modos, não só a página."""

    def _conectar(self, mode, pode):
        c = WinboxVNCConsumer()
        c.scope = {'query_string': f'mode={mode}'.encode(), 'client': ['127.0.0.1', 1]}
        c._crm_user = mock.Mock()
        c.send_error = mock.Mock()
        c.close = mock.Mock()
        c.limpar_recursos = mock.Mock()
        c._usuario_pode_acessar = mock.Mock(return_value=pode)
        with mock.patch.object(consumers.Acesso.objects, 'get', return_value=mock.Mock()), \
                mock.patch.object(consumers.AcessoSessao.objects, 'create') as sessao:
            sessao.side_effect = RuntimeError('passou da checagem')
            c.conectar_vnc(1)
        return c, sessao

    def test_sem_permissao_recusa_em_todos_os_modos(self):
        for mode in ('winbox', 'browser', 'rdp', 'unm'):
            with self.subTest(mode=mode):
                c, sessao = self._conectar(mode, False)
                sessao.assert_not_called()
                c.send_error.assert_called_once_with('Sem permissão para este acesso')
                c.close.assert_called_once_with(code=4003)

    def test_com_permissao_segue_adiante(self):
        for mode in ('winbox', 'browser', 'rdp', 'unm'):
            with self.subTest(mode=mode):
                _, sessao = self._conectar(mode, True)
                sessao.assert_called_once()


class WinboxNativoPermissaoTest(SimpleTestCase):
    def test_sem_permissao_nao_abre_sessao(self):
        c = WinboxConsumer()
        c.send_error = mock.Mock()
        c._usuario_pode_acessar = mock.Mock(return_value=False)
        with mock.patch.object(consumers.Acesso.objects, 'get', return_value=mock.Mock()), \
                mock.patch.object(consumers.AcessoSessao.objects, 'create') as sessao:
            c.conectar_winbox(1)
        sessao.assert_not_called()
        c.send_error.assert_called_once_with('Sem permissão para este acesso')
