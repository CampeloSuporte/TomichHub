"""Protocolos extras de um host (AcessoProtocolo): cadastro pela aba "+" do
card, escolha no "Acessar" e o SSH que o backup usa."""
import tempfile
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clientes.models import Acesso, AcessoProtocolo, BackupTemplate, Cliente
from clientes.views import realizar_backup
from usuario.models import TOTPDevice


class _Base(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nome_empresa='Cliente Proto', cnpj='22.222.222/0001-22',
            endereco='Rua X, 1', email='proto@example.com',
        )
        self.acesso = Acesso.objects.create(
            cliente=self.cliente, tipo='ROUTER PROTO', host='172.24.67.194',
            porta=8080, protocolo='HTTP', usuario='noc', senha='s3nha',
        )


class AcessoPortaSshTest(_Base):
    def test_principal_ssh_usa_porta_principal(self):
        self.acesso.protocolo, self.acesso.porta = 'SSH', 65111
        AcessoProtocolo.objects.create(acesso=self.acesso, protocolo='SSH', porta=22)
        self.assertEqual(self.acesso.porta_ssh(), 65111)

    def test_principal_http_usa_primeiro_ssh_extra(self):
        AcessoProtocolo.objects.create(acesso=self.acesso, protocolo='HTTPS', porta=443)
        AcessoProtocolo.objects.create(acesso=self.acesso, protocolo='SSH', porta=6522)
        AcessoProtocolo.objects.create(acesso=self.acesso, protocolo='SSH', porta=22)
        self.assertEqual(self.acesso.porta_ssh(), 6522)

    def test_sem_ssh_retorna_none(self):
        AcessoProtocolo.objects.create(acesso=self.acesso, protocolo='TELNET', porta=23)
        self.assertIsNone(self.acesso.porta_ssh())


class AplicarProtocoloExtraTest(_Base):
    def test_troca_porta_e_protocolo_so_em_memoria(self):
        extra = AcessoProtocolo.objects.create(acesso=self.acesso, protocolo='SSH', porta=6522)
        self.assertEqual(self.acesso.aplicar_protocolo_extra(str(extra.id)), extra)
        self.assertEqual((self.acesso.protocolo, self.acesso.porta), ('SSH', 6522))
        self.acesso.refresh_from_db()
        self.assertEqual((self.acesso.protocolo, self.acesso.porta), ('HTTP', 8080))

    def test_respeita_protocolos_permitidos(self):
        extra = AcessoProtocolo.objects.create(acesso=self.acesso, protocolo='RDP', porta=3389)
        self.assertIsNone(self.acesso.aplicar_protocolo_extra(extra.id, permitidos=('SSH', 'TELNET')))
        self.assertEqual(self.acesso.porta, 8080)

    def test_nao_aplica_protocolo_de_outro_host(self):
        outro = Acesso.objects.create(
            cliente=self.cliente, tipo='OUTRO', host='10.0.0.1', porta=22,
            protocolo='SSH', usuario='u', senha='s',
        )
        extra = AcessoProtocolo.objects.create(acesso=outro, protocolo='SSH', porta=2222)
        self.assertIsNone(self.acesso.aplicar_protocolo_extra(extra.id))

    def test_id_invalido(self):
        self.assertIsNone(self.acesso.aplicar_protocolo_extra('abc'))
        self.assertIsNone(self.acesso.aplicar_protocolo_extra(None))


class HostEhPrivadoTest(_Base):
    def test_ip_com_caminho_e_esquema(self):
        for host, esperado in [
            ('172.24.67.194', True),
            ('198.18.1.13/zabbix', True),
            ('http://10.1.1.1:8080/x', False),   # porta no host não é IP válido
            ('https://192.168.0.1/', True),
            ('45.228.195.1', False),
            ('router.exemplo.com', False),
        ]:
            self.acesso.host = host
            self.assertEqual(self.acesso.host_eh_privado, esperado, host)


class ProtocoloExtraViewsTest(_Base):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user('admin_proto', password='x', is_staff=True, is_superuser=True)
        # Forcar2FAMiddleware devolve 302 para login sem TOTP confirmado
        TOTPDevice.objects.create(usuario=self.admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(self.admin)

    def _adicionar(self, protocolo, porta):
        return self.client.post(
            reverse('adicionar_protocolo_acesso', args=[self.acesso.id]),
            {'protocolo': protocolo, 'porta': porta},
        )

    def test_adiciona_protocolo(self):
        r = self._adicionar('ssh', '6522')
        self.assertEqual(r.status_code, 200)
        dados = r.json()
        self.assertTrue(dados['success'])
        self.assertEqual(dados['protocolo']['protocolo'], 'SSH')
        self.assertEqual(dados['protocolo']['porta'], 6522)
        self.assertEqual(self.acesso.protocolos_extras.count(), 1)

    def test_rejeita_duplicado(self):
        self._adicionar('SSH', '22')
        r = self._adicionar('SSH', '22')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.acesso.protocolos_extras.count(), 1)

    def test_rejeita_igual_ao_padrao(self):
        r = self._adicionar('HTTP', '8080')
        self.assertEqual(r.status_code, 400)
        self.assertIn('padrão', r.json()['error'])

    def test_rejeita_protocolo_ou_porta_invalidos(self):
        for protocolo, porta in [('FTP', '21'), ('WINBOX', '8291'), ('SSH', '0'),
                                 ('SSH', '70000'), ('SSH', 'abc'), ('SSH', '')]:
            self.assertEqual(self._adicionar(protocolo, porta).status_code, 400, (protocolo, porta))
        self.assertFalse(self.acesso.protocolos_extras.exists())

    def test_get_nao_permitido(self):
        r = self.client.get(reverse('adicionar_protocolo_acesso', args=[self.acesso.id]))
        self.assertEqual(r.status_code, 405)

    def test_remove_protocolo(self):
        extra = AcessoProtocolo.objects.create(acesso=self.acesso, protocolo='SSH', porta=22)
        r = self.client.post(reverse('remover_protocolo_acesso', args=[extra.id]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(AcessoProtocolo.objects.filter(id=extra.id).exists())

    def test_portal_de_outro_cliente_nao_adiciona_nem_remove(self):
        outro_cliente = Cliente.objects.create(
            nome_empresa='Outro', cnpj='33.333.333/0001-33',
            endereco='Rua Y, 2', email='outro@example.com',
        )
        portal = User.objects.create_user('portal_outro', password='x')
        outro_cliente.usuario = portal
        outro_cliente.save()
        TOTPDevice.objects.create(usuario=portal, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        extra = AcessoProtocolo.objects.create(acesso=self.acesso, protocolo='SSH', porta=22)
        self.client.force_login(portal)

        r = self._adicionar('TELNET', '23')
        self.assertIn(r.status_code, (302, 403))
        r = self.client.post(reverse('remover_protocolo_acesso', args=[extra.id]))
        self.assertIn(r.status_code, (302, 403))
        self.assertEqual(list(self.acesso.protocolos_extras.values_list('protocolo', flat=True)), ['SSH'])

    def test_card_mostra_extras_e_escolha_no_acessar(self):
        AcessoProtocolo.objects.create(acesso=self.acesso, protocolo='SSH', porta=6522)
        r = self.client.get(reverse('listar_clientes') + f'?id={self.cliente.id}')
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('data-protocolo="SSH" data-porta="6522"', html)
        self.assertIn('class="novo-proto-form"', html)
        self.assertIn('acessarHost(this,', html)
        # host 172.24.x é privado: o extra HTTP/HTTPS iria pelo proxy web
        self.assertIn(', pid, true))', html)


class PaginaVncRotuloTest(_Base):
    """winbox.html serve Winbox Web, WebFig e RDP: o texto tem de seguir o modo."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user('admin_vnc', password='x', is_staff=True, is_superuser=True)
        TOTPDevice.objects.create(usuario=self.admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(self.admin)

    def test_rdp_nao_aparece_como_winbox(self):
        html = self.client.get(reverse('rdp_page', args=[self.acesso.id])).content.decode()
        self.assertIn('Preparando acesso RDP', html)
        self.assertIn(f'<title>RDP · {self.acesso.host}</title>', html)
        self.assertNotIn('Preparando WinBox', html)

    def test_winbox_continua_winbox(self):
        html = self.client.get(reverse('winbox_page', args=[self.acesso.id])).content.decode()
        self.assertIn('Preparando WinBox', html)
        self.assertNotIn('Preparando acesso RDP', html)


class ProxyWebFalhaTest(_Base):
    """Página de erro do próprio proxy leva X-CRM-Proxy-Falha: é o que o
    acesso web usa para, com IP privado, cair para a conexão direta."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user('admin_web', password='x', is_staff=True, is_superuser=True)
        TOTPDevice.objects.create(usuario=self.admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(self.admin)

    def _get(self):
        return self.client.get(f'/clientes/acessos/{self.acesso.id}/web/80/http/')

    def test_ip_privado_sem_proxy_nem_vpn_marca_falha(self):
        r = self._get()   # 172.24.67.194 e nenhum ProxyServer
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r['X-CRM-Proxy-Falha'], '1')

    @mock.patch('clientes.views.ProxyEngine.do_request', return_value=None)
    def test_sem_resposta_marca_falha(self, _):
        self.acesso.host = '45.228.195.1'
        self.acesso.save()
        r = self._get()
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r['X-CRM-Proxy-Falha'], '1')

    @mock.patch('clientes.views.ProxyEngine.do_request')
    def test_resposta_do_equipamento_nao_marca_falha(self, do_request):
        do_request.return_value = SimpleNamespace(
            status_code=401, headers={'Content-Type': 'text/plain'}, content=b'auth', cookies_raw=[],
        )
        self.acesso.host = '45.228.195.1'
        self.acesso.save()
        r = self._get()
        self.assertEqual(r.status_code, 401)
        self.assertNotIn('X-CRM-Proxy-Falha', r)


class BackupUsaSshExtraTest(_Base):
    def setUp(self):
        super().setUp()
        self.acesso.host = '45.228.195.1'   # público: conecta direto, sem túnel
        self.acesso.backup_template = BackupTemplate.objects.create(
            nome='Tpl', fabricante='mikrotik', comandos='/export',
        )
        self.acesso.save()

    def _porta_conectada(self):
        """Roda realizar_backup até o connect do paramiko e devolve a porta usada."""
        conexoes = []

        def _connect(**kwargs):
            conexoes.append(kwargs)
            raise Exception('parar aqui')

        with mock.patch('clientes.views.paramiko.SSHClient') as ssh_cls, \
                mock.patch('clientes.views.preparar_diretorio_backup', return_value=tempfile.mkdtemp()):
            ssh_cls.return_value.connect.side_effect = _connect
            resultado = realizar_backup(self.acesso)
        self.assertFalse(resultado['sucesso'])
        self.assertEqual(len(conexoes), 1)
        return conexoes[0]['port']

    def test_principal_http_faz_backup_pelo_ssh_extra(self):
        AcessoProtocolo.objects.create(acesso=self.acesso, protocolo='SSH', porta=6522)
        self.assertEqual(self._porta_conectada(), 6522)

    def test_sem_ssh_mantem_porta_principal(self):
        self.assertEqual(self._porta_conectada(), 8080)
