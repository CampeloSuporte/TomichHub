"""
Backup da OLT VSOL: login interno + enable com senha + paginação "--More--".
Roda sem banco: `python -m unittest clientes.tests_backup_vsol` (com DJANGO_SETTINGS_MODULE).
"""
from unittest import TestCase, mock

from clientes import views


class FakeOltVsol:
    """Canal paramiko de mentira que responde como a CLI da OLT_MARI."""

    def __init__(self, senha='s3nha', senha_enable='en4ble', paginas=2):
        self.senha, self.senha_enable, self.paginas = senha, senha_enable, paginas
        self.buffer = b'Login: '
        self.estado = 'login'
        self.enviados = []
        self.closed = False

    def settimeout(self, _):
        pass

    def recv_ready(self):
        return bool(self.buffer)

    def recv(self, _n):
        dados, self.buffer = self.buffer, b''
        return dados

    def close(self):
        self.closed = True

    def _sai(self, texto):
        self.buffer += texto.encode()

    def send(self, dado):
        self.enviados.append(dado)
        linha = dado.rstrip('\n')
        if self.estado == 'login':
            self.estado = 'senha'
            return self._sai(linha + '\r\nPassword: ')
        if self.estado == 'senha':
            self.estado = 'user' if linha == self.senha else 'login'
            return self._sai('\r\nOLT_MARI> ' if linha == self.senha else '\r\nLogin failed\r\nLogin: ')
        if self.estado == 'user' and linha == 'enable':
            self.estado = 'enable_senha'
            return self._sai('enable\r\nPassword: ')
        if self.estado == 'enable_senha':
            self.estado = 'priv' if linha == self.senha_enable else 'user'
            return self._sai('\r\nOLT_MARI# ' if linha == self.senha_enable else '\r\n% Bad passwords\r\nOLT_MARI> ')
        if self.estado == 'priv' and linha == 'terminal length 0':
            return self._sai('terminal length 0\r\nOLT_MARI# ')
        if self.estado == 'priv' and linha == 'show running-config':
            self.restantes = self.paginas
            return self._sai('show running-config\r\n!\r\nhostname OLT_MARI\r\ninterface gpon 0/1\r\n --More-- ')
        if dado == ' ' and getattr(self, 'restantes', 0):
            self.restantes -= 1
            if self.restantes:
                return self._sai('\x08' * 10 + ' onu 1 type GPON sn VSOL0001\r\n --More-- ')
            return self._sai('\x08' * 10 + ' onu 2 type GPON sn VSOL0002\r\nend\r\nOLT_MARI# ')
        if linha == 'exit':
            return
        return self._sai(linha + '\r\n% Unknown command.\r\nOLT_MARI# ')


class BackupVsolTests(TestCase):
    def _rodar(self, canal, comandos):
        client = mock.Mock()
        client.invoke_shell.return_value = canal
        with mock.patch.object(views.time, 'sleep'), \
             mock.patch.object(views, '_ler_ate_silencio', side_effect=lambda ch, **kw: ch.recv(0).decode() if ch.recv_ready() else ''):
            return views._executar_comandos_vsol(client, comandos, 'admin', 's3nha', 'en4ble')

    def test_login_enable_e_paginacao(self):
        canal = FakeOltVsol()
        output = self._rodar(canal, ['enable', 'terminal length 0', 'show running-config'])
        self.assertIn('hostname OLT_MARI', output)
        self.assertIn('onu 2 type GPON sn VSOL0002', output)
        self.assertNotIn('More', output)
        # "enable" do template não é reenviado (viraria a "senha" do próximo)
        self.assertEqual(canal.enviados.count('enable\n'), 1)
        self.assertTrue(views._backup_tem_conteudo(output))

    def test_senha_de_enable_errada_falha_legivel(self):
        canal = FakeOltVsol(senha_enable='outra')
        with self.assertRaisesRegex(Exception, 'enable incorreta'):
            self._rodar(canal, ['show running-config'])
