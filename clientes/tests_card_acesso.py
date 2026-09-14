"""Card de acesso da página do cliente: blocos Ping/porta/último acesso,
linha de acessos e o endpoint de teste sob demanda (status_acesso)."""
import socket
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clientes import views
from clientes.models import Acesso, AcessoProtocolo, AcessoSessao, Cliente
from funcao_equipamento.models import Funcao_equipamento
from modelo_equipamento.models import Modelo_equipamento
from usuario.models import TOTPDevice


class _Base(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nome_empresa='Cliente Card', cnpj='33.333.333/0001-33',
            endereco='Rua Y, 2', email='card@example.com',
        )
        self.acesso = Acesso.objects.create(
            cliente=self.cliente, tipo='BA-TEI-A03-SW-F.VELHO-01', host='200.200.200.10',
            porta=22, protocolo='SSH', usuario='admin', senha='s3nha',
        )
        self.admin = User.objects.create_user('admin_card', password='x', is_staff=True, is_superuser=True)
        # Forcar2FAMiddleware devolve 302 para login sem TOTP confirmado
        TOTPDevice.objects.create(usuario=self.admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(self.admin)

    def _html(self):
        r = self.client.get(reverse('listar_clientes') + f'?id={self.cliente.id}')
        self.assertEqual(r.status_code, 200)
        return r.content.decode()


class CardAcessoTest(_Base):
    def test_sem_sessao_mostra_nunca_e_blocos_de_teste(self):
        html = self._html()
        self.assertIn('class="card h-100 ac-card"', html)
        self.assertIn(f'testarStatusAcesso(this, {self.acesso.id})', html)
        self.assertIn('ac-vazio">nunca', html)
        self.assertNotIn('Acessado por', html)
        # O ícone de ping (globo) saiu da barra: o bloco Ping do card substitui
        self.assertNotIn('realizarPing(', html)

    def test_cabecalho_com_fabricante_icone_e_rodape(self):
        self.acesso.funcao = Funcao_equipamento.objects.create(descricao='SWITCH L2')
        self.acesso.modelo = Modelo_equipamento.objects.create(nome='SW HUAWEI S6730', fabricante='Huawei')
        self.acesso.save()
        html = self._html()
        self.assertIn('data-fabricante="huawei"', html)
        self.assertIn('<span class="ac-fabricante">Huawei</span> SW S6730 · ', html)
        self.assertIn('fa-arrow-right-arrow-left', html)
        # Ações no rodapé; duplicar, adicionar protocolo e excluir no menu ⋯
        self.assertIn('<div class="ac-rodape">', html)
        self.assertIn('<details class="ac-menu">', html)
        self.assertIn(reverse('deletar_acesso', args=[self.acesso.id]), html)
        self.assertIn('toggleNovoProtocolo(this)"><i class="fas fa-plus"></i> Adicionar protocolo', html)
        self.assertNotIn('class="nav nav-tabs mb-3"', html)

    def test_sem_modelo_nem_funcao(self):
        html = self._html()
        self.assertIn('data-fabricante=""', html)
        self.assertIn('fa-hard-drive', html)
        self.assertNotIn('ac-fabricante', html.split('class="ac-sub"')[1].split('</div>')[0])

    def test_ultimo_acesso_vem_da_sessao_mais_recente(self):
        joao = User.objects.create_user('joao.noc')
        AcessoSessao.objects.create(acesso=self.acesso, usuario=self.admin, tipo='ssh')
        AcessoSessao.objects.create(acesso=self.acesso, usuario=joao, tipo='ssh')
        html = self._html()
        self.assertIn('Acessado por', html)
        self.assertIn('joao.noc · ', html)
        self.assertIn('class="ac-bloco-valor ac-relativo" data-ts="', html)

    def test_sessao_de_link_externo_sem_usuario(self):
        AcessoSessao.objects.create(acesso=self.acesso, usuario=None, tipo='ssh')
        self.assertIn('link externo · ', self._html())

    def test_linha_acessos_tem_padrao_extras_e_winbox(self):
        self.acesso.winbox = 8291
        self.acesso.save()
        AcessoProtocolo.objects.create(acesso=self.acesso, protocolo='HTTPS', porta=443)
        html = self._html()
        self.assertIn('<span class="acesso-chip">SSH 22</span>', html)
        self.assertIn('data-protocolo="HTTPS" data-porta="443"', html)
        self.assertIn('Winbox 8291</span>', html)
        self.assertIn('<span class="ac-qtd" hidden></span>', html)


class StatusAcessoViewTest(_Base):
    def _status(self):
        return self.client.get(reverse('status_acesso', args=[self.acesso.id]))

    @mock.patch('clientes.views._status_direto')
    def test_publico_testa_direto(self, status_direto):
        status_direto.return_value = ({'status': 'sucesso', 'tempos': {'avg': 4.2}}, True)
        r = self._status()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'responde': True, 'ping_ms': 4.2, 'porta': 22, 'porta_aberta': True})
        status_direto.assert_called_once_with('200.200.200.10', 22)

    @mock.patch('clientes.views._status_direto')
    def test_host_com_esquema_e_caminho(self, status_direto):
        status_direto.return_value = ({'status': 'timeout'}, False)
        self.acesso.host = 'https://200.200.200.10/zabbix'
        self.acesso.save()
        self.assertEqual(self._status().json()['responde'], False)
        status_direto.assert_called_once_with('200.200.200.10', 22)

    def test_privado_sem_proxy_nem_vpn(self):
        self.acesso.host = '10.105.200.102'
        self.acesso.save()
        r = self._status()
        self.assertEqual(r.status_code, 400)
        self.assertIn('proxy', r.json()['error'])

    def test_sem_login_nao_testa(self):
        self.client.logout()
        with mock.patch('clientes.views._status_direto') as status_direto:
            r = self._status()
        self.assertNotEqual(r.status_code, 200)
        status_direto.assert_not_called()


class StatusDiretoTest(TestCase):
    def _ping_ok(self):
        saida = ('3 packets transmitted, 3 received, 0% packet loss, time 1002ms\n'
                 'rtt min/avg/max/mdev = 3.1/4.0/5.2/0.8 ms\n')
        return mock.patch('subprocess.run', return_value=SimpleNamespace(stdout=saida, returncode=0))

    def test_porta_aberta_e_fechada(self):
        servidor = socket.socket()
        servidor.bind(('127.0.0.1', 0))
        servidor.listen(1)
        aberta = servidor.getsockname()[1]
        livre = socket.socket()
        livre.bind(('127.0.0.1', 0))
        fechada = livre.getsockname()[1]
        livre.close()
        try:
            with self._ping_ok():
                ping, ok = views._status_direto('127.0.0.1', aberta)
                _, nao = views._status_direto('127.0.0.1', fechada)
        finally:
            servidor.close()
        self.assertEqual(ping['tempos']['avg'], 4.0)
        self.assertIs(ok, True)
        self.assertIs(nao, False)

    def test_parser_le_latencia_do_linux_e_do_macos(self):
        linux = views.parsear_output_ping(
            '3 packets transmitted, 3 received, 0% packet loss\nrtt min/avg/max/mdev = 1.0/2.5/4.0/0.5 ms\n',
            'h', 0)
        macos = views.parsear_output_ping(
            '3 packets transmitted, 3 packets received, 0.0% packet loss\n'
            'round-trip min/avg/max/stddev = 1.0/3.5/4.0/0.5 ms\n', 'h', 0)
        self.assertEqual(linux['tempos']['avg'], 2.5)
        self.assertEqual(macos['tempos']['avg'], 3.5)

    def test_sem_porta_nao_testa_tcp(self):
        with self._ping_ok(), mock.patch('socket.create_connection') as conectar:
            _, porta_aberta = views._status_direto('127.0.0.1', None)
        self.assertIsNone(porta_aberta)
        conectar.assert_not_called()


class NomeSemFabricanteTest(TestCase):
    def test_tira_fabricante_repetido_do_nome(self):
        casos = [
            ('SW HUAWEI S6730', 'Huawei', 'SW S6730'),
            ('MIKROTIK CCR1036-8G-2S+', 'Mikrotik', 'CCR1036-8G-2S+'),
            ('MIMOSA - RADIO C5X', 'MIMOSA', 'RADIO C5X'),
            ('PROXMOX', 'PROXMOX', ''),
            ('RB3011', 'Mikrotik', 'RB3011'),
            ('TP-LINKX 10', 'TP-Link', 'TP-LINKX 10'),
            ('OLT X', '', 'OLT X'),
        ]
        for nome, fabricante, esperado in casos:
            with self.subTest(nome=nome):
                self.assertEqual(Modelo_equipamento(nome=nome, fabricante=fabricante).nome_sem_fabricante, esperado)
