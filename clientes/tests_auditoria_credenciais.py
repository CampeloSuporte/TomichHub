"""Registro de troca de usuário/senha no cadastro do Acesso (auditoria).

Caso real: o backup do Cgnat AFL passou a dar "Authentication failed" e não
havia como saber se alguém tinha trocado a senha no CRM — Acesso não tinha
histórico nenhum de edição.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clientes import auditoria_credenciais as aud
from clientes.models import Acesso, AcessoAlteracaoCredencial, Cliente
from usuario.models import TOTPDevice


class AuditoriaCredenciaisTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nome_empresa='Cliente Cred', cnpj='55.555.555/0001-55',
            endereco='Rua C, 1', email='cred@example.com',
        )
        self.acesso = Acesso.objects.create(
            cliente=self.cliente, tipo='Cgnat AFL', host='172.24.67.122',
            porta=65111, protocolo='SSH', usuario='startnetadm', senha='senhaantiga1',
        )
        self.admin = User.objects.create_user('admin_cred', password='x', is_staff=True, is_superuser=True)
        TOTPDevice.objects.create(usuario=self.admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(self.admin)

    def _post_editar(self, **extra):
        dados = {
            'tipo': self.acesso.tipo, 'hostname': self.acesso.host, 'hostname_ipv6': '',
            'protocolo': 'SSH', 'porta': '65111', 'usuario': 'startnetadm',
            'senha': 'senhaantiga1', 'senha_adm': '',
        }
        dados.update(extra)
        return self.client.post(
            reverse('editar_acesso', args=[self.acesso.id]), dados,
            HTTP_X_REQUESTED_WITH='XMLHttpRequest', HTTP_X_FORWARDED_FOR='189.1.2.3',
        )

    def test_troca_de_senha_registra_sem_guardar_senha(self):
        r = self._post_editar(senha='senhanova123')
        self.assertEqual(r.json(), {'success': True})
        reg = AcessoAlteracaoCredencial.objects.get()
        self.assertEqual(reg.campos_lista(), ['senha'])
        self.assertEqual(reg.usuario, self.admin)
        self.assertEqual(reg.ip_origem, '189.1.2.3')
        self.assertEqual(reg.origem, 'edicao')
        valores = ' '.join(str(v) for v in AcessoAlteracaoCredencial.objects.values().get().values())
        self.assertNotIn('senhaantiga1', valores)
        self.assertNotIn('senhanova123', valores)

    def test_edicao_sem_trocar_credencial_nao_registra(self):
        self._post_editar(tipo='Cgnat AFL novo nome')
        self.assertFalse(AcessoAlteracaoCredencial.objects.exists())

    def test_troca_de_login_guarda_antes_e_depois(self):
        self._post_editar(usuario='noc', senha_adm='adm12345')
        reg = AcessoAlteracaoCredencial.objects.get()
        self.assertEqual(reg.campos_lista(), ['usuario', 'senha_adm'])
        self.assertEqual((reg.login_antigo, reg.login_novo), ('startnetadm', 'noc'))

    def test_aparece_nas_telas_de_auditoria(self):
        self._post_editar(senha='senhanova123')
        r = self.client.get(reverse('listar_sessoes_auditoria', args=[self.acesso.id]), {'pagina': 1})
        alt = r.json()['alteracoes_credenciais']
        self.assertEqual(len(alt), 1)
        self.assertEqual(alt[0]['campos'], ['Senha'])
        r = self.client.get(reverse('auditoria_cliente_credenciais', args=[self.cliente.id]))
        self.assertEqual(r.json()['total'], 1)
        self.assertNotIn('senhanova123', r.content.decode())

    def test_registrar_direto_troca_massa(self):
        antes = aud.capturar(self.acesso.pk)
        Acesso.objects.filter(pk=self.acesso.pk).update(usuario='crm', senha='x' * 12)
        reg = aud.registrar(self.acesso, antes, usuario=self.admin, origem='troca_massa')
        self.assertEqual(reg.campos_lista(), ['usuario', 'senha'])
        self.assertEqual(reg.ip_origem, '')
