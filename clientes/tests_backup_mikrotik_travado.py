"""Backup MikroTik com /export que para de responder no meio.

Caso real (INFORTECLINE, 10/2026): "BGP - CENTRAL" e "BORDA - CGNAT"
(CCR1036, RouterOS 6.49.18) passaram de ~2 s para ~182 s por backup e
gravavam "Backup vazio". O /export mandava 26 KB em 1 s e emudecia em
/tool graphing — o subsistema travou no equipamento e não responde nem a
"/tool graphing print". O stdout.read() com timeout de 120 s descartava o
que já tinha chegado. Agora o que chegou fica, o resto vem seção a seção e
só a parte travada sai, com aviso.
"""
import socket
from unittest import mock

from django.test import SimpleTestCase

from clientes import views
from clientes.views import (
    _backup_tem_conteudo, _completar_export_travado, _executar_comandos_mikrotik,
    _routeros_blocos,
)

CABECALHO = "# oct/26/2026 11:29:44 by RouterOS 6.49.18\r\n# software id = 3KZH-RJPF\r\n"

EXPORT_PARCIAL = CABECALHO + (
    "/interface bridge add name=bridge-server\r\n"
    "/user group add name=null\r\n"
    "/ip address add address=10.0.0.1/30 interface=ether1\r\n"
    "/system identity set name=BGP\r\n"
    "/tool e-mail set from=\"\"\r\n"
)

SECOES = {
    '/interface export terse': CABECALHO + "/interface bridge add name=bridge-server\r\n",
    '/ip export terse': CABECALHO + "/ip address add address=10.0.0.1/30 interface=ether1\r\n",
    '/system export terse': CABECALHO + "/system identity set name=BGP\r\n",
    '/user export terse': CABECALHO + (
        "/user group add name=null\r\n"
        "/user add group=full name=tomich\r\n"
        "/user aaa set default-group=null use-radius=yes\r\n"
    ),
    '/tool e-mail export terse': CABECALHO + "/tool e-mail set from=\"\"\r\n",
    '/tool romon export terse': CABECALHO + "/tool romon set enabled=yes\r\n",
}
TRAVADOS = {'/export terse', '/tool export terse', '/tool graphing export terse'}


class _CanalFalso:
    """Canal paramiko: manda a saída e fecha (EOF), ou manda e emudece."""

    def __init__(self, comandos):
        self._comandos = comandos
        self._pedacos = []
        self._trava = False

    def settimeout(self, _):
        pass

    def exec_command(self, comando):
        self._comandos.append(comando)
        if comando == '/export show-sensitive terse':
            saida = "expected end of command (line 1 column 9)\n"
        elif comando == '/export terse':
            saida = EXPORT_PARCIAL
        else:
            saida = SECOES.get(comando, CABECALHO if comando in TRAVADOS
                               else "bad command name x (line 1 column 2)\n")
        self._pedacos = [saida.encode()]
        self._trava = comando in TRAVADOS

    def recv(self, _):
        if self._pedacos:
            return self._pedacos.pop(0)
        if self._trava:
            raise socket.timeout()
        return b''

    def close(self):
        pass


class _ClienteFalso:
    def __init__(self):
        self.comandos = []

    def get_transport(self):
        cliente = self

        class _T:
            def open_session(self_inner):
                return _CanalFalso(cliente.comandos)
        return _T()


class _Relogio:
    """time.time() que avança 1 s a cada chamada — o silêncio estoura sem esperar."""

    def __init__(self):
        self.agora = 0.0

    def __call__(self):
        self.agora += 1.0
        return self.agora


class ExportTravadoTests(SimpleTestCase):
    def _rodar(self, comandos):
        cliente = _ClienteFalso()
        with mock.patch.object(views.time, 'time', _Relogio()):
            saida = _executar_comandos_mikrotik(cliente, comandos)
        return saida, cliente.comandos

    def test_aproveita_o_que_chegou_e_completa_por_secao(self):
        saida, comandos = self._rodar(['/export show-sensitive terse'])

        self.assertTrue(_backup_tem_conteudo(saida))
        self.assertIn('# Comando: /export terse', saida)
        for linha in ('/ip address add address=10.0.0.1/30',
                      '/tool e-mail set from=""',
                      '/tool romon set enabled=yes',
                      '/user aaa set default-group=null'):
            self.assertIn(linha, saida)
        self.assertIn('# AVISO CRM: sem resposta do RouterOS em /tool graphing', saida)

    def test_nao_duplica_menus_ja_exportados(self):
        saida, _ = self._rodar(['/export terse'])
        self.assertEqual(saida.count('/user group add name=null'), 1)
        self.assertEqual(saida.count('/ip address add'), 1)
        self.assertEqual(saida.count('/tool e-mail set'), 1)

    def test_nao_inclui_contas_do_user_export(self):
        saida, _ = self._rodar(['/export terse'])
        self.assertNotIn('/user add', saida)

    def test_secao_travada_vai_direto_as_subsecoes(self):
        _, comandos = self._rodar(['/export terse'])
        self.assertNotIn('/tool export terse', comandos)
        self.assertIn('/tool romon export terse', comandos)

    def test_export_que_termina_nao_dispara_recuperacao(self):
        cliente = _ClienteFalso()
        SECOES['/export completo'] = CABECALHO + "/system identity set name=BGP\r\n" * 5
        try:
            with mock.patch.object(views.time, 'time', _Relogio()):
                saida = _executar_comandos_mikrotik(cliente, ['/export completo'])
        finally:
            del SECOES['/export completo']
        self.assertEqual(cliente.comandos, ['/export completo'])
        self.assertNotIn('AVISO CRM', saida)

    def test_completar_sem_subsecao_conhecida_avisa_a_secao(self):
        cliente = _ClienteFalso()
        TRAVADOS.add('/ip export terse')
        try:
            with mock.patch.object(views.time, 'time', _Relogio()):
                _, avisos = _completar_export_travado(cliente, '/export terse', EXPORT_PARCIAL)
        finally:
            TRAVADOS.discard('/ip export terse')
        self.assertIn('/ip', avisos)


class BlocosTests(SimpleTestCase):
    def test_caminho_terse_e_export_normal(self):
        terse = "/ip firewall filter add action=accept chain=input\r\n/tool romon set enabled=yes\r\n"
        self.assertEqual([c for c, _ in _routeros_blocos(terse)],
                         [('ip', 'firewall', 'filter'), ('tool', 'romon')])
        normal = "/ip address\nadd address=10.0.0.1/30 interface=ether1\nadd address=10.0.0.5/30 \\\n    interface=ether2\n"
        blocos = _routeros_blocos(normal)
        self.assertEqual(len(blocos), 1)
        self.assertEqual(blocos[0][0], ('ip', 'address'))
        self.assertEqual(len(blocos[0][1]), 4)
