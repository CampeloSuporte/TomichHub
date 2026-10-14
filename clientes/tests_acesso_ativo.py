"""Botão Desativar/Reativar host do Editar Acesso (Acesso.ativo).

Desmarcar "Habilitar Backup" não bastava para parar as tentativas: a rotina
diária `habilitar_backups_automaticos` religa o backup de todo host SSH com
modelo. Host desativado fica fora de todo backup — manual, agendado e da
habilitação automática.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clientes import tasks
from clientes.models import Acesso, BackupTemplate, Cliente
from modelo_equipamento.models import Modelo_equipamento
from usuario.models import TOTPDevice


class _Base(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nome_empresa='Cliente Ativo', cnpj='44.444.444/0001-44',
            endereco='Rua Z, 1', email='ativo@example.com',
        )
        self.template = BackupTemplate.objects.create(
            nome='Backup Cisco', fabricante='CISCO', comandos='show running-config',
        )
        self.acesso = Acesso.objects.create(
            cliente=self.cliente, tipo='SW ATIVO', host='200.200.200.40',
            porta=22, protocolo='SSH', usuario='admin', senha='s3nha',
            backup_habilitado=True, backup_automatico=True, backup_template=self.template,
        )


class AlternarAtivoViewTest(_Base):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user('admin_ativo', password='x', is_staff=True, is_superuser=True)
        TOTPDevice.objects.create(usuario=self.admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(self.admin)
        self.url = reverse('alternar_ativo_acesso', args=[self.acesso.id])

    def test_desativa_e_reativa(self):
        r = self.client.post(self.url)
        self.assertEqual(r.json(), {'success': True, 'ativo': False})
        self.acesso.refresh_from_db()
        self.assertFalse(self.acesso.ativo)
        # Configuração de backup é preservada para quando reativar
        self.assertTrue(self.acesso.backup_habilitado)

        r = self.client.post(self.url)
        self.assertEqual(r.json(), {'success': True, 'ativo': True})

    def test_get_nao_altera(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 405)
        self.acesso.refresh_from_db()
        self.assertTrue(self.acesso.ativo)

    def test_portal_de_outro_cliente_nao_altera(self):
        outro = Cliente.objects.create(
            nome_empresa='Outro', cnpj='55.555.555/0001-55',
            endereco='Rua W, 2', email='outro@example.com',
        )
        portal = User.objects.create_user('portal_ativo', password='x')
        outro.usuario = portal
        outro.save()
        TOTPDevice.objects.create(usuario=portal, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(portal)

        r = self.client.post(self.url)
        self.assertIn(r.status_code, (302, 403))
        self.acesso.refresh_from_db()
        self.assertTrue(self.acesso.ativo)

    def test_buscar_acesso_devolve_ativo(self):
        r = self.client.get(reverse('buscar_acesso', args=[self.acesso.id]))
        self.assertIs(r.json()['ativo'], True)

    def test_backup_manual_recusado(self):
        Acesso.objects.filter(pk=self.acesso.pk).update(ativo=False)
        with mock.patch('clientes.views.realizar_backup') as rb:
            r = self.client.post(reverse('executar_backup', args=[self.acesso.id]))
        self.assertEqual(r.status_code, 400)
        rb.assert_not_called()


class RotinasIgnoramDesativadoTest(_Base):
    def test_agendador_pula_desativado(self):
        Acesso.objects.filter(pk=self.acesso.pk).update(ativo=False)
        with mock.patch('clientes.tasks.realizar_backup') as rb:
            tasks.agendar_backups_pendentes_SEM_CELERY()
        rb.assert_not_called()

    def test_task_individual_pula_desativado(self):
        Acesso.objects.filter(pk=self.acesso.pk).update(ativo=False)
        with mock.patch('clientes.tasks.realizar_backup') as rb:
            r = tasks.executar_backup_agendado.apply(args=[self.acesso.id]).get()
        self.assertEqual(r['status'], 'ignorado')
        rb.assert_not_called()

    def test_habilitacao_automatica_nao_religa(self):
        modelo = Modelo_equipamento.objects.create(nome='C9300', fabricante='CISCO')
        Acesso.objects.filter(pk=self.acesso.pk).update(
            ativo=False, modelo=modelo, backup_habilitado=False, backup_automatico=False,
        )
        tasks.habilitar_backups_automaticos.apply().get()
        self.acesso.refresh_from_db()
        self.assertFalse(self.acesso.backup_habilitado)

        # Controle: o mesmo host ativo é religado
        Acesso.objects.filter(pk=self.acesso.pk).update(ativo=True)
        tasks.habilitar_backups_automaticos.apply().get()
        self.acesso.refresh_from_db()
        self.assertTrue(self.acesso.backup_habilitado)
