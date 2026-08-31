from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from usuario.models import Instancia, PerfilUsuario, TOTPDevice, UsuarioAcesso
from usuario.views import _is_staff_para_role


def _criar_admin_staff(username='chefe'):
    # Forcar2FAMiddleware redireciona qualquer login sem TOTPDevice confirmado
    # pra tela de configuração de 2FA; sem isso o POST cai em 302 antes da view.
    admin = User.objects.create_user(username=username, password='x', is_staff=True, is_active=True)
    TOTPDevice.objects.create(usuario=admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
    return admin


class IsStaffParaRoleTest(TestCase):
    """Admin, Consultor e Operador são back-office e precisam de is_staff=True
    — é o que libera acesso a módulos internos como o atendimento
    (staff_required). Só 'cliente' fica de fora."""

    def test_admin_e_staff(self):
        self.assertTrue(_is_staff_para_role(PerfilUsuario.ROLE_ADMIN))

    def test_consultor_e_staff(self):
        self.assertTrue(_is_staff_para_role(PerfilUsuario.ROLE_CONSULTOR))

    def test_operador_e_staff(self):
        self.assertTrue(_is_staff_para_role(PerfilUsuario.ROLE_OPERADOR))

    def test_cliente_nao_e_staff(self):
        self.assertFalse(_is_staff_para_role('cliente'))


class CadastrarUsuarioIsStaffTest(TestCase):
    """Bug real: is_staff só era setado pra Admin — Consultor e Operador
    nasciam com is_staff=False e ficavam trancados fora do atendimento
    (staff_required) e invisíveis na lista de "atendentes" pra transferir
    chamado, mesmo tendo PerfilUsuario com role válida."""

    def setUp(self):
        self.admin = _criar_admin_staff()
        self.client.force_login(self.admin)
        self.instancia = Instancia.objects.create(nome='Instancia Teste')

    def test_operador_criado_ganha_is_staff(self):
        resp = self.client.post(reverse('cadastrar_usuario'), {
            'username': 'novo_operador', 'email': 'op@example.com', 'password': 'x123456',
            'role': PerfilUsuario.ROLE_OPERADOR, 'instancia': self.instancia.id,
        })
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username='novo_operador')
        self.assertTrue(user.is_staff)
        self.assertEqual(user.perfil.role, PerfilUsuario.ROLE_OPERADOR)

    def test_consultor_criado_ganha_is_staff(self):
        resp = self.client.post(reverse('cadastrar_usuario'), {
            'username': 'novo_consultor', 'email': 'cons@example.com', 'password': 'x123456',
            'role': PerfilUsuario.ROLE_CONSULTOR, 'instancia_nome': 'Consultoria Nova',
        })
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username='novo_consultor')
        self.assertTrue(user.is_staff)

    def test_cliente_criado_nao_ganha_is_staff(self):
        resp = self.client.post(reverse('cadastrar_usuario'), {
            'username': 'novo_cliente', 'email': 'cli@example.com', 'password': 'x123456',
            'role': 'cliente',
        })
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username='novo_cliente')
        self.assertFalse(user.is_staff)

    def test_editar_usuario_promove_operador_pra_is_staff(self):
        # Reproduz o caso real: login já existia (criado antes do fix, ou
        # editado depois) com role operador mas is_staff=False.
        operador = User.objects.create_user(username='op_antigo', is_staff=False, is_active=True)
        PerfilUsuario.objects.create(usuario=operador, role=PerfilUsuario.ROLE_OPERADOR, instancia=self.instancia)

        resp = self.client.post(reverse('editar_usuario'), {
            'id': operador.id, 'username': 'op_antigo', 'email': 'opantigo@example.com',
            'role': PerfilUsuario.ROLE_OPERADOR, 'instancia': self.instancia.id,
        })

        self.assertEqual(resp.status_code, 302)
        operador.refresh_from_db()
        self.assertTrue(operador.is_staff)


class HostsLiberadosPortalTest(TestCase):
    """Seleção de hosts por login do portal (`UsuarioAcesso`).

    Regra: sem registro nenhum o login vê **todos** os hosts do cliente
    (é como todo login existente continua); com registro, vê só os marcados.
    """

    def setUp(self):
        from clientes.models import Cliente, Acesso

        self.admin = _criar_admin_staff()
        self.client.force_login(self.admin)

        self.portal = User.objects.create_user(username='cliente_portal', password='x', is_active=True)
        # Mesmo motivo do _criar_admin_staff: sem 2FA confirmado o
        # Forcar2FAMiddleware devolve 302 antes de qualquer view.
        TOTPDevice.objects.create(usuario=self.portal, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.cliente = Cliente.objects.create(
            usuario=self.portal, nome_empresa='Empresa Teste',
            cnpj='00.000.000/0001-00', endereco='Rua 1', email='empresa@example.com',
        )
        self.hosts = [
            Acesso.objects.create(
                cliente=self.cliente, tipo=f'SW-{i}', host=f'10.0.0.{i}',
                protocolo='SSH', porta=22, usuario='adm', senha='x',
            )
            for i in range(1, 4)
        ]

    def _post_edicao(self, acessos, com_marcador=True):
        dados = {
            'id': self.portal.id, 'username': self.portal.username,
            'email': 'portal@example.com', 'role': 'cliente',
            'acessos': [str(a) for a in acessos],
        }
        if com_marcador:
            dados['acessos_form_present'] = '1'
        return self.client.post(reverse('editar_usuario'), dados)

    def test_sem_registro_ve_todos_os_hosts(self):
        from usuario import perms
        self.assertEqual(perms.filtrar_acessos_visiveis(self.portal, self.cliente.acessos.all()).count(), 3)
        self.assertTrue(perms.pode_acessar_acesso(self.portal, self.hosts[0]))

    def test_selecao_parcial_esconde_os_demais(self):
        from usuario import perms
        self._post_edicao([self.hosts[0].id])
        self.assertEqual(UsuarioAcesso.objects.filter(usuario=self.portal).count(), 1)
        self.assertEqual(perms.filtrar_acessos_visiveis(self.portal, self.cliente.acessos.all()).count(), 1)
        self.assertTrue(perms.pode_acessar_acesso(self.portal, self.hosts[0]))
        self.assertFalse(perms.pode_acessar_acesso(self.portal, self.hosts[1]))

    def test_marcar_todos_volta_a_ser_sem_restricao(self):
        from usuario import perms
        self._post_edicao([self.hosts[0].id])
        self._post_edicao([h.id for h in self.hosts])
        # Nenhum registro = sem restrição: host cadastrado depois já entra.
        self.assertEqual(UsuarioAcesso.objects.filter(usuario=self.portal).count(), 0)
        from clientes.models import Acesso
        novo = Acesso.objects.create(
            cliente=self.cliente, tipo='SW-novo', host='10.0.0.99',
            protocolo='SSH', porta=22, usuario='adm', senha='x',
        )
        self.assertTrue(perms.pode_acessar_acesso(self.portal, novo))

    def test_host_de_outro_cliente_no_post_e_descartado(self):
        from clientes.models import Cliente, Acesso
        outro = Cliente.objects.create(
            nome_empresa='Outra', cnpj='11.111.111/0001-11',
            endereco='Rua 2', email='outra@example.com',
        )
        alheio = Acesso.objects.create(
            cliente=outro, tipo='SW-alheio', host='10.9.9.9',
            protocolo='SSH', porta=22, usuario='adm', senha='x',
        )
        self._post_edicao([self.hosts[0].id, alheio.id])
        gravados = set(UsuarioAcesso.objects.filter(usuario=self.portal).values_list('acesso_id', flat=True))
        self.assertEqual(gravados, {self.hosts[0].id})

    def test_nada_marcado_mantem_a_selecao(self):
        # "Zero host" não é representável (seria igual a "sem restrição", que
        # libera tudo) — o form avisa e não mexe no que estava gravado.
        self._post_edicao([self.hosts[0].id])
        self._post_edicao([])
        self.assertEqual(UsuarioAcesso.objects.filter(usuario=self.portal).count(), 1)

    def test_post_sem_marcador_nao_mexe_na_selecao(self):
        self._post_edicao([self.hosts[0].id])
        self._post_edicao([], com_marcador=False)
        self.assertEqual(UsuarioAcesso.objects.filter(usuario=self.portal).count(), 1)

    def test_backoffice_nunca_e_filtrado_por_host(self):
        from usuario import perms
        self._post_edicao([self.hosts[0].id])
        self.assertEqual(perms.filtrar_acessos_visiveis(self.admin, self.cliente.acessos.all()).count(), 3)
        self.assertTrue(perms.pode_acessar_acesso(self.admin, self.hosts[2]))

    def test_painel_do_cliente_mostra_so_os_hosts_liberados(self):
        self._post_edicao([self.hosts[0].id])
        self.client.force_login(self.portal)
        html = self.client.get(
            reverse('listar_clientes') + f'?id={self.cliente.id}', follow=True
        ).content.decode()
        self.assertIn(self.hosts[0].host, html)
        self.assertNotIn(self.hosts[1].host, html)
        self.assertNotIn(self.hosts[2].host, html)

    def test_endpoint_de_host_bloqueado_responde_403(self):
        self._post_edicao([self.hosts[0].id])
        self.client.force_login(self.portal)
        self.assertEqual(self.client.get(f'/clientes/acessos/buscar/{self.hosts[0].id}/').status_code, 200)
        self.assertEqual(self.client.get(f'/clientes/acessos/buscar/{self.hosts[1].id}/').status_code, 403)
