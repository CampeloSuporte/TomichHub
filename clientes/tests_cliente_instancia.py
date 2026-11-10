"""Instância do cliente no cadastro/edição (caso SICONECT, 10/11/2026).

O cadastro do Administrador lia `instancia` do POST, mas a tela não tinha o
campo: todo cliente criado por ele nascia sem instância e sumia para os
Operadores/Consultores.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clientes.models import Cliente
from usuario.models import Instancia, PerfilUsuario, TOTPDevice


DADOS = {
    'nome_empresa': 'Nova Empresa', 'email': 'nova@example.com', 'cnpj': '11.111.111/0001-11',
    'telefone': '1', 'endereco': 'Rua', 'cidade': 'C', 'estado': 'E', 'cep': '0',
}


class InstanciaClienteTest(TestCase):
    def setUp(self):
        self.principal = Instancia.objects.create(nome='Principal', principal=True)
        self.outra = Instancia.objects.create(nome='Revenda')
        self.admin = User.objects.create_user('adm_inst', password='x', is_staff=True, is_superuser=True)
        TOTPDevice.objects.create(usuario=self.admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(self.admin)

    def _operador(self, instancia):
        op = User.objects.create_user('op_inst', password='x', is_staff=True)
        PerfilUsuario.objects.create(usuario=op, role=PerfilUsuario.ROLE_OPERADOR, instancia=instancia)
        TOTPDevice.objects.create(usuario=op, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        return op

    def test_tela_do_admin_traz_seletor_com_principal_marcada(self):
        html = self.client.get(reverse('cadastrar_cliente')).content.decode()
        self.assertIn('name="instancia"', html)
        self.assertIn(f'<option value="{self.principal.id}" selected>', html)

    def test_operador_nao_ve_seletor(self):
        self.client.force_login(self._operador(self.principal))
        html = self.client.get(reverse('cadastrar_cliente')).content.decode()
        self.assertNotIn('name="instancia"', html)

    def test_admin_cadastra_na_instancia_escolhida(self):
        self.client.post(reverse('cadastrar_cliente'), {**DADOS, 'instancia': self.principal.id})
        self.assertEqual(Cliente.objects.get(email=DADOS['email']).instancia_id, self.principal.id)

    def test_admin_cadastra_sem_instancia_quando_escolhe_vazio(self):
        self.client.post(reverse('cadastrar_cliente'), {**DADOS, 'instancia': ''})
        self.assertIsNone(Cliente.objects.get(email=DADOS['email']).instancia_id)

    def test_instancia_inexistente_e_recusada(self):
        self.client.post(reverse('cadastrar_cliente'), {**DADOS, 'instancia': '99999'})
        self.assertFalse(Cliente.objects.filter(email=DADOS['email']).exists())

    def _cliente(self, instancia=None):
        return Cliente.objects.create(nome_empresa='X', email='x@example.com', cnpj='1', instancia=instancia)

    def _editar(self, cliente, **extra):
        dados = {**DADOS, 'id': cliente.id, 'email': cliente.email, **extra}
        self.client.post(reverse('editar_cliente'), dados)
        cliente.refresh_from_db()

    def test_admin_troca_instancia_na_edicao(self):
        c = self._cliente()
        self._editar(c, instancia=self.principal.id)
        self.assertEqual(c.instancia_id, self.principal.id)
        self._editar(c, instancia='')
        self.assertIsNone(c.instancia_id)

    def test_edicao_sem_campo_mantem_instancia(self):
        c = self._cliente(self.principal)
        self._editar(c)
        self.assertEqual(c.instancia_id, self.principal.id)

    def test_operador_nao_troca_instancia(self):
        c = self._cliente(self.principal)
        self.client.force_login(self._operador(self.principal))
        self._editar(c, instancia=self.outra.id)
        self.assertEqual(c.instancia_id, self.principal.id)
