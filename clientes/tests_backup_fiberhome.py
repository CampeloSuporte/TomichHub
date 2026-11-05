"""Template FiberHome sempre vai pelo shell interativo.

Caso real (Sartor, 05/11/2026): OLTs FiberHome da série 6k (RP1300–RP1600)
fazem backup com "config" / "terminal length 0" / "show startup-config" —
sem "cd service", então o template não ativava o shell persistente e cada
comando ia num exec_command separado. Cinco delas estão cadastradas com
modelo "OLT HUAWEI MA5800X7" e iam para o executor Huawei.
"""
import tempfile
from unittest import mock

from django.test import TestCase, override_settings

from clientes.models import Acesso, BackupTemplate, Cliente
from modelo_equipamento.models import Modelo_equipamento
from clientes.views import realizar_backup

CONFIG = ("\n" + "=" * 60 + "\nComando: show startup-config\n" + "=" * 60 + "\n"
          "!module config:CardAuth.\nset card_auth slot 9 type hswa\nset card_auth slot 11 type gcob\n"
          "!end  of config -----------------------------------------------\nOLT-TAPURAH(config)# \n")


class BackupFiberhomeShellTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cliente = Cliente.objects.create(
            nome_empresa='Cliente FB', cnpj='44.444.444/0001-44',
            endereco='Rua F, 1', email='fb@example.com',
        )

    def _acesso(self, comandos, nome_tpl='Backup fiberhome 6k', modelo=None):
        return Acesso.objects.create(
            cliente=self.cliente, tipo='OLT FB TAPURAH', host='200.200.200.40',
            porta=22, protocolo='SSH', usuario='admin', senha='s3nha',
            backup_habilitado=True, modelo=modelo,
            backup_template=BackupTemplate.objects.create(
                nome=nome_tpl, fabricante='GENERICO', comandos=comandos),
        )

    def _rodar(self, acesso):
        with override_settings(MEDIA_ROOT=self.tmp), \
                mock.patch('clientes.views.paramiko.SSHClient'), \
                mock.patch('clientes.views._executar_comandos_shell_generico', return_value=CONFIG) as gen, \
                mock.patch('clientes.views._executar_comandos_huawei') as hu, \
                mock.patch('clientes.views._executar_comandos_sem_pty') as sem_pty:
            resultado = realizar_backup(acesso)
        return resultado, gen, hu, sem_pty

    def test_6k_sem_cd_usa_shell(self):
        resultado, gen, hu, sem_pty = self._rodar(
            self._acesso('config\nterminal length 0\nshow startup-config'))
        self.assertTrue(resultado['sucesso'], resultado)
        gen.assert_called_once()
        sem_pty.assert_not_called()

    def test_modelo_huawei_errado_nao_vai_pro_executor_huawei(self):
        modelo = Modelo_equipamento.objects.create(nome='OLT HUAWEI MA5800X7', fabricante='HUAWEI')
        resultado, gen, hu, sem_pty = self._rodar(
            self._acesso('config\nterminal length 0\nshow startup-config', modelo=modelo))
        self.assertTrue(resultado['sucesso'], resultado)
        gen.assert_called_once()
        hu.assert_not_called()
