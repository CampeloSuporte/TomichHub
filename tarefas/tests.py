import uuid

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clientes.models import Cliente
from tarefas.models import Tarefa
from usuario.models import Instancia, PerfilUsuario, TOTPDevice


def _dar_2fa(user):
    """`Forcar2FAMiddleware` redireciona quem não confirmou TOTP — sem isso
    todo request do teste vira 302 e a asserção passaria à toa."""
    TOTPDevice.objects.create(usuario=user, secret='A' * 32, confirmado=True)
    return user


def _cliente(nome, instancia):
    suf = uuid.uuid4().hex[:8]
    return Cliente.objects.create(
        nome_empresa=nome, cnpj=f'00.000.000/{suf[:4]}-00',
        endereco='Rua Teste, 123', email=f'{suf}@example.com',
        instancia=instancia,
    )


class ExcluirTarefaTest(TestCase):
    """Exclusão de tarefa pelo painel do dashboard (`tarefa_excluir`).

    Antes só dava pra excluir pelo kanban da página do cliente, que nem
    lista tarefa sem cliente — a de plataforma não tinha como sair.
    """

    def setUp(self):
        self.inst_a = Instancia.objects.create(nome='Instância A')
        self.inst_b = Instancia.objects.create(nome='Instância B')

        self.consultor = _dar_2fa(User.objects.create_user(
            username='consultor_a', email='ca@example.com', password='x',
            is_staff=True, is_active=True,
        ))
        PerfilUsuario.objects.create(
            usuario=self.consultor, role=PerfilUsuario.ROLE_CONSULTOR, instancia=self.inst_a,
        )
        self.admin = _dar_2fa(User.objects.create_user(
            username='admin_plataforma', email='ap@example.com', password='x',
            is_staff=True, is_superuser=True, is_active=True,
        ))
        self.portal = _dar_2fa(User.objects.create_user(
            username='login_portal', email='lp@example.com', password='x',
            is_staff=False, is_active=True,
        ))

        self.cliente_a = _cliente('CLIENTE A', self.inst_a)
        self.tarefa_a = Tarefa.objects.create(
            titulo='Tarefa da instância A', cliente=self.cliente_a,
            instancia=self.inst_a, criado_por=self.consultor,
        )
        self.tarefa_b = Tarefa.objects.create(
            titulo='Tarefa da instância B', cliente=_cliente('CLIENTE B', self.inst_b),
            instancia=self.inst_b, criado_por=self.admin,
        )
        # Tarefa de plataforma: sem cliente. O kanban da página do cliente
        # nem lista este caso, então era impossível excluí-la.
        self.tarefa_plataforma = Tarefa.objects.create(
            titulo='Tarefa sem cliente', instancia=self.inst_a, criado_por=self.consultor,
        )

    def _excluir(self, tarefa):
        return self.client.post(reverse('tarefa_excluir', args=[tarefa.id]), {'next': '/homeinstancia'})

    def test_consultor_exclui_tarefa_da_propria_instancia(self):
        self.client.force_login(self.consultor)
        r = self._excluir(self.tarefa_a)
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Tarefa.objects.filter(id=self.tarefa_a.id).exists())

    def test_exclui_tarefa_sem_cliente(self):
        self.client.force_login(self.consultor)
        self._excluir(self.tarefa_plataforma)
        self.assertFalse(Tarefa.objects.filter(id=self.tarefa_plataforma.id).exists())

    def test_nao_exclui_tarefa_de_outra_instancia(self):
        self.client.force_login(self.consultor)
        r = self._excluir(self.tarefa_b)
        # 404 e não 403: não revela que a tarefa existe em outra instância.
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Tarefa.objects.filter(id=self.tarefa_b.id).exists())

    def test_administrador_exclui_de_qualquer_instancia(self):
        self.client.force_login(self.admin)
        self._excluir(self.tarefa_b)
        self.assertFalse(Tarefa.objects.filter(id=self.tarefa_b.id).exists())

    def test_login_de_portal_nao_exclui(self):
        self.client.force_login(self.portal)
        r = self._excluir(self.tarefa_a)
        self.assertNotEqual(r.status_code, 200)
        self.assertTrue(Tarefa.objects.filter(id=self.tarefa_a.id).exists())

    def test_get_nao_exclui(self):
        """Só POST — link/prefetch do navegador não pode apagar tarefa."""
        self.client.force_login(self.consultor)
        r = self.client.get(reverse('tarefa_excluir', args=[self.tarefa_a.id]))
        self.assertEqual(r.status_code, 405)
        self.assertTrue(Tarefa.objects.filter(id=self.tarefa_a.id).exists())

    def test_botao_aparece_no_painel(self):
        self.client.force_login(self.consultor)
        html = self.client.get('/homeinstancia').content.decode()
        self.assertIn(reverse('tarefa_excluir', args=[self.tarefa_a.id]), html)
