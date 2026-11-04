"""Backup cortado no meio gravado como SUCESSO.

Caso real (Sartor, 16/08/2026): "Switch Huawei Servidores Core CPD Sinop"
(S6730) pausa alguns segundos depois da seção de VLANs enquanto monta as
interfaces. O coletor encerrava o comando após 3 s de silêncio, gravava
metade da config (7 KB de 13 KB) como SUCESSO, e a deduplicação — que
comparava com QUALQUER backup antigo — impedia que o completo voltasse a ser
o mais recente. Mesmo defeito em outros 6 Huawei e nas OLTs FiberHome.
"""
import tempfile
from unittest import mock

from django.test import TestCase, override_settings

from clientes.models import Acesso, BackupLog, BackupTemplate, Cliente
from clientes import views
from clientes.views import (
    MARCA_SAIDA_INCOMPLETA, _ler_ate_prompt, _motivo_backup_incompleto,
    _termina_em_prompt, realizar_backup,
)

CABECALHO = (
    "\n" + "=" * 60 + "\nComando: screen-length 0 temporary\n" + "=" * 60 + "\n"
    "Info: The configuration takes effect on the current user terminal interface only.\n"
    "\n" + "=" * 60 + "\nComando: display current-configuration\n" + "=" * 60 + "\n"
)
COMPLETA = CABECALHO + "#\nsysname SW\n#\nvlan 1001\n description storages\n#\ninterface GE0/0/1\n#\nreturn\n<SW>\n"
CORTADA = CABECALHO + "#\nsysname SW\n#\nvlan 1001\n description storages\n"


class PromptTest(TestCase):
    def test_reconhece_prompts(self):
        for txt in ('x\n<SW-SERVIDORES-CORE-CPD>', 'x\n[~SW]', 'x\nOLT.CCIT>',
                    'x\nMA5800-X7(config)#', 'x\nOLT-NBR# ', 'x\nOLT-NBR\\service# ', 'x\nZXAN#\n'):
            self.assertTrue(_termina_em_prompt(txt), txt)

    def test_linha_de_config_nao_e_prompt(self):
        for txt in ('vlan 1001\n description 1001-br-storages', 'x\n#', 'x\n!</MSAN>',
                    'x\n rule 15 deny', 'x\ninterface LoopBack100', ''):
            self.assertFalse(_termina_em_prompt(txt), txt)

    def test_pausa_no_meio_nao_encerra_leitura(self):
        canal = mock.Mock(closed=False)
        leituras = ['#\nvlan 1001\n description storages\n', '', '',
                    '#\ninterface GE0/0/1\n#\nreturn\n<SW>']
        with mock.patch.object(views, '_ler_ate_silencio', side_effect=leituras):
            texto, completo = _ler_ate_prompt(canal, silencio=3.0)
        self.assertTrue(completo)
        self.assertIn('interface GE0/0/1', texto)

    def test_sem_prompt_devolve_incompleto(self):
        canal = mock.Mock(closed=False)
        with mock.patch.object(views, '_ler_ate_silencio', side_effect=['vlan 1\n'] + [''] * 50), \
                mock.patch.object(views.time, 'time', side_effect=[float(i) for i in range(200)]):
            texto, completo = _ler_ate_prompt(canal, silencio=3.0, pausa_max=5)
        self.assertFalse(completo)


class MotivoIncompletoTest(TestCase):
    def test_huawei_sem_return(self):
        self.assertEqual(_motivo_backup_incompleto(COMPLETA, True), '')
        self.assertIn('return', _motivo_backup_incompleto(CORTADA, True))
        # Só vale para Huawei: Datacom/ZTE não terminam em "return".
        self.assertEqual(_motivo_backup_incompleto(CORTADA, False), '')

    def test_fiberhome_ocupada(self):
        saida = ("\n" + "=" * 60 + "\nComando: show startup-config\n" + "=" * 60 + "\n"
                 "\nsystem busy,please try later.\nCommand executes failed.\nOLT-X# \n")
        self.assertIn('system busy', _motivo_backup_incompleto(saida, False))

    def test_marca_de_prompt_ausente(self):
        saida = COMPLETA + f"{MARCA_SAIDA_INCOMPLETA}: o prompt não voltou depois de 'x'\n"
        self.assertIn('prompt', _motivo_backup_incompleto(saida, False))


class RealizarBackupIncompletoTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cliente = Cliente.objects.create(
            nome_empresa='Cliente Sinop', cnpj='66.666.666/0001-66',
            endereco='Rua Z, 1', email='sinop@example.com',
        )
        self.acesso = Acesso.objects.create(
            cliente=cliente, tipo='Switch Huawei Servidores', host='200.200.200.31',
            porta=22, protocolo='SSH', usuario='admin', senha='s3nha',
            backup_habilitado=True,
            backup_template=BackupTemplate.objects.create(
                nome='Backup Huawei', fabricante='HUAWEI',
                comandos='screen-length 0 temporary\ndisplay current-configuration',
            ),
        )

    def _rodar(self, saida):
        with override_settings(MEDIA_ROOT=self.tmp), \
                mock.patch('clientes.views.paramiko.SSHClient'), \
                mock.patch('clientes.views._executar_comandos_huawei', return_value=saida):
            return realizar_backup(self.acesso)

    def test_cortada_vira_erro(self):
        resultado = self._rodar(CORTADA)
        self.assertFalse(resultado['sucesso'])
        self.assertIn('incompleto', resultado['erro'])
        self.assertFalse(BackupLog.objects.filter(acesso=self.acesso, status='SUCESSO').exists())

    def test_volta_a_config_antiga_grava_novo_arquivo(self):
        """A → B → A: o terceiro tem o hash do primeiro, mas é mudança em
        relação ao último — precisa virar o backup mais recente."""
        outra = COMPLETA.replace('vlan 1001', 'vlan 2002')
        self.assertFalse(self._rodar(COMPLETA)['sem_mudancas'])
        self.assertFalse(self._rodar(outra)['sem_mudancas'])
        r = self._rodar(COMPLETA)
        self.assertTrue(r['sucesso'])
        self.assertFalse(r['sem_mudancas'])
        self.assertEqual(BackupLog.objects.filter(acesso=self.acesso, status='SUCESSO').count(), 3)
        # E repetir o último continua sendo "sem mudanças".
        self.assertTrue(self._rodar(COMPLETA)['sem_mudancas'])
