import os
from unittest import mock

from django.test import SimpleTestCase
from django.urls import reverse

from clientes import unm_vnc
from clientes.models import Acesso, AcessoSessao
from clientes.unm_vnc import REDE_SH, UnmVNCManager
from clientes.winbox_vnc import WinboxVNCManager


class Proxy:
    nome, host, porta, usuario, senha = 'p', '192.0.2.1', 22, 'root', 'segredo-do-proxy'


class UnmRedeTest(SimpleTestCase):
    def _iniciar(self, proxy):
        m = UnmVNCManager('198.18.1.23', 'admin', 'senha-unm', proxy=proxy, width=1000, height=600)
        self.addCleanup(m.stop)
        env = {'DISPLAY': ':150'}
        with mock.patch.object(unm_vnc.os.path, 'exists', return_value=True), \
                mock.patch.object(unm_vnc.subprocess, 'Popen') as popen, \
                mock.patch.object(m, '_wait_for_port', return_value=True), \
                mock.patch.object(unm_vnc.threading, 'Thread'):
            popen.return_value.poll.return_value = None
            m._iniciar_app(env)
        return m, popen

    def test_ip_privado_roda_em_namespace_proprio_sem_root(self):
        _, popen = self._iniciar(Proxy())
        cmd = popen.call_args_list[-1].args[0]
        self.assertEqual(cmd[:3], ['unshare', '-Urn', '--pid'])
        self.assertIn('--kill-child', cmd)
        self.assertNotIn('sudo', cmd)
        self.assertEqual(cmd[-2], unm_vnc.UNM_APP)

    def test_senhas_fora_da_lista_de_processos(self):
        _, popen = self._iniciar(Proxy())
        for chamada in popen.call_args_list:
            argv = ' '.join(chamada.args[0])
            self.assertNotIn('segredo-do-proxy', argv)
            self.assertNotIn('senha-unm', argv)
        self.assertEqual(popen.call_args_list[0].kwargs['env']['SSHPASS'], 'segredo-do-proxy')

    def test_ip_publico_sai_direto_sem_namespace(self):
        _, popen = self._iniciar(None)
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(popen.call_args.args[0][0], unm_vnc.UNM_APP)

    def test_todo_tcp_vai_ao_redsocks(self):
        self.assertIn('-p tcp -j REDIRECT --to-ports 12345', REDE_SH)
        self.assertIn('-d 127.0.0.0/8 -j RETURN', REDE_SH)
        self.assertIn('ip route add default dev lo src', REDE_SH)

    def test_stop_apaga_a_pasta_da_sessao(self):
        m, _ = self._iniciar(None)
        home = m._home
        self.assertTrue(os.path.isdir(home))
        m.stop()
        self.assertFalse(os.path.exists(home))
        m.stop()  # idempotente: thread de leitura e disconnect chamam juntos

    def test_erro_claro_sem_o_cliente_instalado(self):
        m = UnmVNCManager('198.18.1.23', 'a', 'b')
        with mock.patch.object(unm_vnc.os.path, 'exists', return_value=False):
            with self.assertRaisesMessage(Exception, 'não está instalado'):
                m._iniciar_app({})


class UnmTelasTest(SimpleTestCase):
    def _login(self, user, senha):
        m = UnmVNCManager('198.18.1.23', user, senha)
        chamadas = []
        with mock.patch.object(m, '_teclar', side_effect=lambda env, *a, **k: chamadas.append((a, k))):
            m._preencher_login({}, '42')
        return chamadas

    def test_senha_digitada_pela_entrada_padrao(self):
        chamadas = self._login('admin', 'S@nha!#1')
        digitadas = [k.get('entrada') for _, k in chamadas if k.get('entrada')]
        self.assertEqual(digitadas, ['admin', 'S@nha!#1', '198.18.1.23'])
        self.assertTrue(all('S@nha!#1' not in a for a, _ in chamadas))
        self.assertEqual(chamadas[-1][0], ('key', 'alt+l'))

    def test_sem_senha_nao_clica_em_entrar(self):
        chamadas = self._login('admin', '')
        self.assertNotIn(('key', 'alt+l'), [a for a, _ in chamadas])


class UnmOpenboxTest(SimpleTestCase):
    def test_so_a_janela_principal_maximiza(self):
        m = UnmVNCManager('h', 'u', 'p', width=1000, height=600, screen_width=2560, screen_height=1440)
        try:
            m._escrever_openbox_rc()
            with open(m._openbox_rc) as f:
                xml = f.read()
        finally:
            os.unlink(m._openbox_rc)
        self.assertIn('title="UNM2000*" type="normal"', xml)
        self.assertNotIn('class="*"', xml)
        self.assertIn('<right>1560</right>', xml)

    def test_winbox_segue_com_a_propria_config(self):
        self.assertTrue(WinboxVNCManager.openbox_rc.endswith('openbox_rc.xml'))


class UnmCadastroTest(SimpleTestCase):
    def test_protocolo_e_tipo_de_sessao(self):
        self.assertIn('UNM', Acesso.ProtocoloChoices.values)
        self.assertIn('unm', dict(AcessoSessao.TIPOS))

    def test_rota_da_pagina(self):
        self.assertEqual(reverse('unm_page', args=[7]), '/clientes/unm/7/')
