from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from clientes import winbox_arquivos as wa


class NomesTest(SimpleTestCase):
    def test_nome_tira_pasta_do_pc(self):
        self.assertEqual(wa.nome_seguro('C:\\Users\\x\\routeros-7.16.npk'), 'routeros-7.16.npk')
        self.assertEqual(wa.nome_seguro('/home/x/script.rsc'), 'script.rsc')

    def test_nome_invalido(self):
        for nome in ('', '..', '.', 'pasta/'):
            with self.subTest(nome=nome), self.assertRaises(ValueError):
                wa.nome_seguro(nome)

    def test_pasta(self):
        self.assertEqual(wa.pasta_segura(''), '')
        self.assertEqual(wa.pasta_segura('/flash/'), 'flash')
        self.assertEqual(wa.pasta_segura('flash//pub'), 'flash/pub')
        for ruim in ('..', 'flash/../x', 'a\x00b'):
            with self.subTest(pasta=ruim), self.assertRaises(ValueError):
                wa.pasta_segura(ruim)

    def test_caminho_remoto(self):
        self.assertEqual(wa.caminho_remoto('', 'a.rsc'), 'a.rsc')
        self.assertEqual(wa.caminho_remoto('flash', 'a.rsc'), 'flash/a.rsc')

    def test_mensagem_permissao(self):
        self.assertIn('ftp', wa.mensagem_erro(PermissionError(13, 'Permission denied')))


class EnvioTest(SimpleTestCase):
    def _acesso(self, porta_ssh=22):
        a = mock.Mock(id=7, host='10.0.0.1', porta='8291', protocolo='WINBOX')
        a.porta_ssh.return_value = porta_ssh
        return a

    def test_sem_ssh_recusa(self):
        arq = SimpleUploadedFile('a.rsc', b'x')
        with self.assertRaisesRegex(ValueError, 'SSH'):
            wa.iniciar_envio(self._acesso(None), arq, '', mock.Mock(id=1))

    def test_envia_por_sftp_e_marca_ok(self):
        estados = {}
        sftp = mock.Mock()
        recebido = {}

        def putfo(f, destino, file_size, callback, confirm):
            recebido['dados'] = f.read()
            recebido['destino'] = destino
            callback(file_size, file_size)
        sftp.putfo.side_effect = putfo
        client = mock.Mock()
        client.open_sftp.return_value = sftp

        class ThreadSincrona:
            def __init__(self, target, args, **kw):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        with mock.patch.object(wa.cache, 'get', side_effect=lambda k: estados.get(k)), \
                mock.patch.object(wa.cache, 'set', side_effect=lambda k, v, t: estados.__setitem__(k, v)), \
                mock.patch.object(wa.threading, 'Thread', ThreadSincrona), \
                mock.patch('clientes.tasks._conectar_ssh_acesso', return_value=(client, None)) as conn, \
                mock.patch('clientes.tasks._fechar_ssh'), \
                mock.patch.object(wa, '_auditar'):
            envio = wa.iniciar_envio(self._acesso(2222), SimpleUploadedFile('x.rsc', b'abc'),
                                     'flash', mock.Mock(id=1))

        self.assertEqual(conn.call_args[0][0].porta, 2222)
        self.assertEqual(recebido, {'dados': b'abc', 'destino': 'flash/x.rsc'})
        st = estados[wa._chave(envio)]
        self.assertEqual(st['status'], 'ok')
        self.assertEqual(st['enviado'], 3)

    def test_erro_vira_mensagem(self):
        estados = {}

        class ThreadSincrona:
            def __init__(self, target, args, **kw):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        with mock.patch.object(wa.cache, 'get', side_effect=lambda k: estados.get(k)), \
                mock.patch.object(wa.cache, 'set', side_effect=lambda k, v, t: estados.__setitem__(k, v)), \
                mock.patch.object(wa.threading, 'Thread', ThreadSincrona), \
                mock.patch('clientes.tasks._conectar_ssh_acesso', side_effect=OSError('timed out')):
            envio = wa.iniciar_envio(self._acesso(), SimpleUploadedFile('x.rsc', b'abc'), '', mock.Mock(id=1))
        st = estados[wa._chave(envio)]
        self.assertEqual(st['status'], 'erro')
        self.assertIn('Tempo esgotado', st['erro'])


class TemporarioNaoFicaTest(SimpleTestCase):
    """O arquivo enviado não pode ficar guardado no servidor."""

    def _tmps(self):
        import os, tempfile
        return {n for n in os.listdir(tempfile.gettempdir()) if n.startswith(wa.PREFIXO_TMP)}

    def test_apaga_se_falhar_antes_da_thread(self):
        antes = self._tmps()
        a = mock.Mock(id=7, host='10.0.0.1')
        a.porta_ssh.return_value = 22
        with mock.patch.object(wa, '_gravar_estado', side_effect=RuntimeError('redis fora')), \
                self.assertRaises(RuntimeError):
            wa.iniciar_envio(a, SimpleUploadedFile('x.rsc', b'abc'), '', mock.Mock(id=1))
        self.assertEqual(self._tmps(), antes)

    def test_apaga_depois_de_enviar(self):
        antes = self._tmps()
        estados = {}
        client = mock.Mock()

        class ThreadSincrona:
            def __init__(self, target, args, **kw):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)
        a = mock.Mock(id=7, host='10.0.0.1')
        a.porta_ssh.return_value = 22
        with mock.patch.object(wa.cache, 'get', side_effect=lambda k: estados.get(k)), \
                mock.patch.object(wa.cache, 'set', side_effect=lambda k, v, t: estados.__setitem__(k, v)), \
                mock.patch.object(wa.threading, 'Thread', ThreadSincrona), \
                mock.patch('clientes.tasks._conectar_ssh_acesso', return_value=(client, None)), \
                mock.patch('clientes.tasks._fechar_ssh'), mock.patch.object(wa, '_auditar'):
            wa.iniciar_envio(a, SimpleUploadedFile('x.rsc', b'abc'), '', mock.Mock(id=1))
        self.assertEqual(self._tmps(), antes)

    def test_limpa_sobra_antiga(self):
        import os, tempfile, time
        fd, velho = tempfile.mkstemp(prefix=wa.PREFIXO_TMP)
        os.close(fd)
        fd, novo = tempfile.mkstemp(prefix=wa.PREFIXO_TMP)
        os.close(fd)
        os.utime(velho, (time.time() - wa.SOBRA_SEGUNDOS - 10,) * 2)
        try:
            wa.limpar_sobras()
            self.assertFalse(os.path.exists(velho))
            self.assertTrue(os.path.exists(novo))
        finally:
            for p in (velho, novo):
                if os.path.exists(p):
                    os.unlink(p)
