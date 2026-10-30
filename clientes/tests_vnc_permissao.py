from unittest import mock

from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase

from clientes import consumers
from clientes.consumers import WebSocketProxyConsumer, WinboxConsumer, WinboxVNCConsumer
from home.agent_consumer import AgentNOCConsumer


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


class ProxyWsPermissaoTest(SimpleTestCase):
    def _conectar(self, user, pode=False):
        c = WebSocketProxyConsumer()
        c.scope = {'user': user, 'subprotocols': [], 'query_string': b'', 'cookies': {},
                   'url_route': {'kwargs': {'acesso_id': 1, 'porta': 8006, 'scheme': 'https', 'path': ''}}}
        c.accept = mock.Mock()
        c.close = mock.Mock()
        acesso = mock.Mock()
        acesso.host.strip.side_effect = RuntimeError('passou da checagem')
        with mock.patch.object(consumers.Acesso.objects, 'get', return_value=acesso), \
                mock.patch.object(consumers._perms, 'pode_acessar_acesso', return_value=pode):
            c.connect()
        return c, acesso

    def test_sem_login_nem_aceita(self):
        c, _ = self._conectar(AnonymousUser())
        c.accept.assert_not_called()
        c.close.assert_called_once_with(code=4001)

    def test_login_sem_permissao_nao_chega_ao_equipamento(self):
        c, acesso = self._conectar(mock.Mock(is_authenticated=True))
        acesso.host.strip.assert_not_called()
        c.close.assert_called_once_with(code=4003)

    def test_com_permissao_segue_adiante(self):
        _, acesso = self._conectar(mock.Mock(is_authenticated=True), pode=True)
        acesso.host.strip.assert_called_once()


class AgentPermissaoTest(SimpleTestCase):
    def _iniciar(self, qs, acesso_id=None, pode=False):
        from clientes.models import Acesso, AgentConfig, AgentSessao, Cliente
        from usuario import perms

        c = AgentNOCConsumer()
        c.acesso_id = acesso_id
        c.scope = {'query_string': qs}
        c._send_json = mock.AsyncMock()
        c.close = mock.AsyncMock()
        acesso = mock.Mock()
        with mock.patch.object(AgentConfig, 'get', return_value=mock.Mock(ativo=True)), \
                mock.patch.object(Acesso.objects, 'select_related') as sel, \
                mock.patch.object(Cliente.objects, 'get', return_value=mock.Mock()), \
                mock.patch.object(perms, 'pode_acessar_acesso', return_value=pode) as p_acesso, \
                mock.patch.object(perms, 'pode_acessar_cliente', return_value=pode) as p_cliente, \
                mock.patch.object(AgentSessao.objects, 'filter', side_effect=RuntimeError('passou')) as sessoes:
            sel.return_value.get.return_value = acesso
            async_to_sync(c._inicializar_sessao)(mock.Mock())
        return c, p_acesso, p_cliente, sessoes

    def test_acesso_sem_permissao_nao_cria_sessao(self):
        c, p_acesso, _, sessoes = self._iniciar(b'', acesso_id=1)
        p_acesso.assert_called_once()
        sessoes.assert_not_called()
        c.close.assert_awaited_once_with(code=4003)

    def test_cliente_por_parametro_sem_permissao_nao_cria_sessao(self):
        c, _, p_cliente, sessoes = self._iniciar(b'cliente=9')
        p_cliente.assert_called_once()
        sessoes.assert_not_called()
        c.close.assert_awaited_once_with(code=4003)

    def test_com_permissao_segue_adiante(self):
        _, _, _, sessoes = self._iniciar(b'', acesso_id=1, pode=True)
        sessoes.assert_called_once()
