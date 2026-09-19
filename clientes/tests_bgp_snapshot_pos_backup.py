"""Snapshot BGP logo depois do backup.

O botão "Automação BGP" do card só aparece para host que já tem
`BgpSnapshot`, e o snapshot só era gerado pela rotina das 02:45
(`tasks.atualizar_snapshots_bgp`): host que ganhou sessões BGP durante o dia
ficava até a madrugada seguinte sem botão — e sem botão não há como chegar na
tela para clicar "Atualizar agora". Agora todo backup bem-sucedido reprocessa
o snapshot daquele host.
"""
import tempfile
from unittest import mock

from django.test import TestCase, override_settings

from clientes.models import Acesso, BackupTemplate, BgpSnapshot, Cliente
from clientes.views import realizar_backup
from modelo_equipamento.models import Modelo_equipamento

CONFIG_HUAWEI = """#
sysname BORDA-TESTE
#
interface GigabitEthernet0/0/1
 ip address 10.10.10.1 255.255.255.252
#
bgp 65000
 peer 10.10.10.2 as-number 65001
 peer 10.10.10.2 description UPSTREAM-TESTE
 #
 ipv4-family unicast
  undo synchronization
  network 203.0.113.0 255.255.255.0
  peer 10.10.10.2 enable
  peer 10.10.10.2 route-policy RP-OUT export
#
return
"""


class SnapshotBgpAposBackupTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cliente = Cliente.objects.create(
            nome_empresa='Cliente BGP', cnpj='44.444.444/0001-44',
            endereco='Rua Z, 3', email='bgp@example.com',
        )
        self.acesso = Acesso.objects.create(
            cliente=self.cliente, tipo='BORDA', host='200.200.200.20',
            porta=22, protocolo='SSH', usuario='admin', senha='s3nha',
            modelo=Modelo_equipamento.objects.create(nome='NE8000 M4', fabricante='Huawei'),
            backup_habilitado=True,
            backup_template=BackupTemplate.objects.create(
                nome='Backup Huawei', fabricante='huawei',
                comandos='display current-configuration',
            ),
        )

    def _rodar_backup(self, saida):
        with override_settings(MEDIA_ROOT=self.tmp), \
                mock.patch('clientes.views.paramiko.SSHClient'), \
                mock.patch('clientes.views._executar_comandos_huawei', return_value=saida):
            return realizar_backup(self.acesso)

    def test_backup_com_bgp_ja_deixa_o_snapshot_pronto(self):
        resultado = self._rodar_backup(CONFIG_HUAWEI)
        self.assertTrue(resultado['sucesso'], resultado.get('erro'))

        snap = BgpSnapshot.objects.get(acesso=self.acesso)
        self.assertEqual(snap.vendor, 'huawei')
        self.assertEqual(snap.erro, '')
        self.assertEqual([s['peer_ip'] for s in snap.dados['sessoes']], ['10.10.10.2'])

    def test_host_sem_bgp_continua_sem_snapshot(self):
        saida = CONFIG_HUAWEI.split('bgp 65000')[0] + 'ip route-static 0.0.0.0 0.0.0.0 10.10.10.2\n#\nreturn\n'
        resultado = self._rodar_backup(saida)
        self.assertTrue(resultado['sucesso'], resultado.get('erro'))
        self.assertFalse(BgpSnapshot.objects.filter(acesso=self.acesso).exists())

    def test_falha_no_snapshot_nao_derruba_o_backup(self):
        with mock.patch('clientes.tasks._atualizar_snapshot_bgp_de_acesso',
                        side_effect=RuntimeError('parser explodiu')):
            resultado = self._rodar_backup(CONFIG_HUAWEI)
        self.assertTrue(resultado['sucesso'], resultado.get('erro'))
        self.assertEqual(self.acesso.backups.filter(status='SUCESSO').count(), 1)
