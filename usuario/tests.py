from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from usuario.models import Instancia, PerfilUsuario, TOTPDevice
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
