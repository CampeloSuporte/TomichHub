"""Backup Huawei de host sem "huawei" no nome nem no modelo.

Caso real (Sartor, 10/2026): switches cadastrados como "SW-HU-TUTILANDIA",
"Switch Lucas"… com modelo vazio e template "Backup Huawei". A detecção de
fabricante olhava só modelo + tipo, não reconhecia Huawei e rodava cada
comando num exec_command separado — o VRP encerra a sessão depois do
primeiro, e o "display current-configuration" voltava "SSH session not
active". Como o banner do "screen-length 0 temporary" passava na checagem de
conteúdo, o arquivo de ~1 KB ainda era gravado como SUCESSO, toda madrugada.
"""
import tempfile
from unittest import mock

from django.test import TestCase, override_settings

from clientes.models import Acesso, BackupLog, BackupTemplate, Cliente
from clientes.views import _backup_tem_conteudo, realizar_backup

SAIDA_FALHA = """
============================================================
Comando: screen-length 0 temporary
============================================================

Info: The max number of VTY users is 10, and the number
      of current VTY users on line is 1.
      The current login time is 2026-10-10 00:43:24-04:00.
Info: Lastest accessed IP: 45.235.72.18  Time: 2026-10-10 00:42:17-04:00  Password will expire in: -

<SW-HU-TUTILANDIA>screen-length 0 temporary
Info: The configuration takes effect on the current user terminal interface only.
<SW-HU-TUTILANDIA>

============================================================
Comando: display current-configuration
============================================================
ERRO: SSH session not active
"""

CONFIG = """
============================================================
Comando: screen-length 0 temporary
============================================================
Info: The configuration takes effect on the current user terminal interface only.

============================================================
Comando: display current-configuration
============================================================
!Software Version V200R021C00SPC100
#
sysname SW-HU-TUTILANDIA
#
router id 172.25.255.91
#
return
"""


class BackupHuaweiDeteccaoTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cliente = Cliente.objects.create(
            nome_empresa='Cliente HU', cnpj='55.555.555/0001-55',
            endereco='Rua Y, 1', email='hu@example.com',
        )
        self.acesso = Acesso.objects.create(
            cliente=cliente, tipo='SW-HU-TUTILANDIA', host='200.200.200.30',
            porta=22, protocolo='SSH', usuario='admin', senha='s3nha',
            backup_habilitado=True,
            backup_template=BackupTemplate.objects.create(
                nome='Backup Huawei', fabricante='HUAWEI',
                comandos='screen-length 0 temporary\ndisplay current-configuration',
            ),
        )

    def test_template_huawei_usa_shell_huawei_mesmo_sem_modelo(self):
        with override_settings(MEDIA_ROOT=self.tmp), \
                mock.patch('clientes.views.paramiko.SSHClient'), \
                mock.patch('clientes.views._executar_comandos_huawei', return_value=CONFIG) as hu, \
                mock.patch('clientes.views._executar_comandos_sem_pty') as sem_pty:
            resultado = realizar_backup(self.acesso)
        self.assertTrue(resultado['sucesso'], resultado.get('erro'))
        hu.assert_called_once()
        sem_pty.assert_not_called()

    def test_so_banner_da_paginacao_nao_e_sucesso(self):
        self.assertFalse(_backup_tem_conteudo(SAIDA_FALHA))
        self.assertTrue(_backup_tem_conteudo(CONFIG))

        with override_settings(MEDIA_ROOT=self.tmp), \
                mock.patch('clientes.views.paramiko.SSHClient'), \
                mock.patch('clientes.views._executar_comandos_huawei', return_value=SAIDA_FALHA):
            resultado = realizar_backup(self.acesso)
        self.assertFalse(resultado['sucesso'])
        self.assertFalse(BackupLog.objects.filter(acesso=self.acesso, status='SUCESSO').exists())
