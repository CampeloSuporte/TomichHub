"""Provisionamento de ONU em OLT Datacom DmOS: backup hierárquico, discovered,
sugestão pela PON, IDs conferidos ao vivo e um commit por ONU."""
from unittest import mock

from django.test import SimpleTestCase

from clientes import olt_datacom as od

# Recorte no formato do running-config real (OLT03 / G5-DM04 do G5, 12/11/2026).
BACKUP = """
hostname OLT03
profile gpon bandwidth-profile PPPoE
 traffic type-4 max-bw 1106944
!
profile gpon line-profile BRIDGE_G5
 upstream-fec
 tcont 1 bandwidth-profile PPPoE
 gem 1
  tcont 1 priority 0
  map 107
   ethernet any vlan any cos any
  !
 !
!
profile gpon line-profile pppoe-router-2525
 upstream-fec
 tcont 4 bandwidth-profile PPPoE
 gem 4
  tcont 4 priority 0
  map pppoe
   veip 1 vlan any cos any
  !
 !
!
profile gpon line-profile PPPoE-Dados
 upstream-fec
 tcont 2 bandwidth-profile PPPoE
 gem 2
  tcont 2 priority 0
  map PPPoE-Dados
   ethernet any vlan 2603 cos any
  !
 !
!
profile gpon onu-profile ONU-BRIGDE
 ethernet adaptive
!
profile gpon service-profile ONU-BRIDGE
 onu-profile ONU-BRIGDE
!
profile gpon service-profile ONU_BRIDGE
 onu-profile ONU-BRIGDE
!
interface gpon 1/1/1
 downstream-fec
 no shutdown
 onu 0
  name LAZARO_CARDOSO
  serial-number DACM91EC965B
  service-profile ONU-BRIDGE line-profile PPPoE-Dados
  ethernet 1
   negotiation
   no shutdown
   native vlan vlan-id 2603
  !
 !
 onu 1
  serial-number VSOL0063CFE5
  service-profile ONU_BRIDGE line-profile BRIDGE_G5
  ethernet 1
   negotiation
   no shutdown
   native vlan vlan-id 2606
  !
 !
 onu 2
  serial-number DACM91D29854
  service-profile ONU-BRIDGE line-profile pppoe-router-2525
  veip 1
  !
 !
 onu 3
  serial-number DACM91D27B68
  service-profile ONU-BRIDGE line-profile PPPoE-Dados
  ethernet 1
   native vlan vlan-id 2603
  !
 !
!
interface gpon 1/1/2
 no shutdown
!
service vlan 2603
 type n:1
!
service-port 1
 gpon 1/1/1 onu 0 gem 2 match vlan vlan-id 2603 action vlan replace vlan-id 2603
!
service-port 2
 gpon 1/1/1 onu 1 gem 1 match vlan vlan-id 2606 action vlan replace vlan-id 2606
!
service-port 4
 gpon 1/1/1 onu 2 gem 4 match vlan vlan-id 1050 action vlan replace vlan-id 1050
!
service-port 5
 gpon 1/1/1 onu 3 gem 2 match vlan vlan-id 2603 action vlan replace vlan-id 2603
!
dot1q
 vlan 1050
  name VLAN-REDE-NEUTRA-MEGALINK
  interface ten-gigabit-ethernet-1/1/1
  !
 !
 vlan 2603
  name PPPOE
  interface ten-gigabit-ethernet-1/1/1
  !
  interface gigabit-ethernet-1/1/1
  !
 !
!
"""

# Saída real da OLT03, 12/11/2026.
DISCOVERED = """show interface gpon discovered-onus

 Chassis / Slot / Port  Serial Number
 ---------------------  -------------
       1/1/12           VSOL007B1566
       1/1/1            DACM91000001
OLT03# """

NAO_EXISTE = """----------------------------------------^
syntax error: element does not exist
OLT03# """


class BackupTest(SimpleTestCase):
    def setUp(self):
        self.b = od.parse_backup(BACKUP)

    def test_reconhece_olt_datacom(self):
        self.assertTrue(od.eh_olt_datacom(BACKUP))
        self.assertFalse(od.eh_olt_datacom('hostname SW\ninterface gigabit-ethernet 1/1/1\n!'))

    def test_onus_da_pon(self):
        onus = self.b['pons']['1/1/1']['onus']
        self.assertEqual(sorted(onus), [0, 1, 2, 3])
        self.assertEqual(onus[0]['nome'], 'LAZARO_CARDOSO')
        self.assertEqual(onus[0]['line'], 'PPPoE-Dados')
        self.assertEqual(onus[0]['service'], 'ONU-BRIDGE')
        self.assertEqual(onus[0]['native_vlan'], 2603)
        self.assertEqual(onus[2]['modo'], 'router')
        self.assertEqual(onus[0]['sps'][0]['gem'], 2)
        self.assertEqual(self.b['pons']['1/1/2']['onus'], {})
        self.assertEqual(self.b['sns']['VSOL0063CFE5'], '1/1/1 onu 1')

    def test_perfis_vlans_e_service_ports(self):
        self.assertEqual(self.b['hostname'], 'OLT03')
        self.assertEqual(self.b['lines']['PPPoE-Dados']['vlans'], [2603])
        self.assertEqual(self.b['lines']['pppoe-router-2525']['modo'], 'router')
        self.assertEqual(self.b['lines']['BRIDGE_G5']['vlans'], [])
        self.assertEqual(self.b['lines']['BRIDGE_G5']['tconts'], {1: 'PPPoE'})
        self.assertEqual(self.b['service_profiles']['ONU_BRIDGE']['onu_profile'], 'ONU-BRIGDE')
        self.assertEqual(self.b['service_vlans'], {2603: 'n:1'})
        self.assertEqual(sorted(self.b['service_ports']), [1, 2, 4, 5])
        self.assertEqual(self.b['vlans'][2603]['interfaces'],
                         ['ten-gigabit-ethernet-1/1/1', 'gigabit-ethernet-1/1/1'])
        self.assertEqual(od.uplinks_do_backup(self.b)[0], {'interface': 'ten-gigabit-ethernet-1/1/1', 'vlans': 2})


class DiscoveredTest(SimpleTestCase):
    def test_formato_real(self):
        self.assertEqual([(o['pon'], o['sn']) for o in od.parse_discovered(DISCOVERED)],
                         [('1/1/12', 'VSOL007B1566'), ('1/1/1', 'DACM91000001')])

    def test_nao_existe(self):
        self.assertTrue(od.nao_existe(NAO_EXISTE))
        self.assertFalse(od.nao_existe('interface gpon 1/1/1\n onu 0\n  name X\n!\nOLT03# '))
        self.assertIsNone(od.nao_existe('% timeout\nOLT03# '))


class SugestaoTest(SimpleTestCase):
    def setUp(self):
        self.b = od.parse_backup(BACKUP)

    def test_maioria_da_pon_mesmo_fabricante(self):
        s = od.sugerir(self.b, '1/1/1', 'DACM91000001')
        self.assertEqual((s['modo'], s['service'], s['line'], s['gem'], s['vlan'], s['vlan_olt']),
                         ('bridge', 'ONU-BRIDGE', 'PPPoE-Dados', 2, 2603, 2603))
        self.assertEqual(s['onu_id'], 4)

    def test_pon_vazia_usa_a_olt(self):
        s = od.sugerir(self.b, '1/1/2', 'VSOL00000001')
        self.assertEqual(s['line'], 'BRIDGE_G5')
        self.assertEqual(s['onu_id'], 0)


class ValidacaoTest(SimpleTestCase):
    def setUp(self):
        self.b = od.parse_backup(BACKUP)
        self.item = {'pon': '1/1/1', 'sn': 'dacm91000001', 'modo': 'bridge', 'service': 'ONU-BRIDGE',
                     'line': 'PPPoE-Dados', 'gem': 2, 'vlan': 2603, 'nome': 'Joao da Silva'}

    def test_normaliza(self):
        it, avisos = od.validar_item(self.item, self.b)
        self.assertEqual(it['sn'], 'DACM91000001')
        self.assertEqual(it['nome'], 'Joao_da_Silva')
        self.assertEqual(it['vlan_olt'], 2603)
        self.assertIsNone(it['onu_id'])
        self.assertEqual(avisos, [])

    def test_recusas(self):
        for campo, valor in (('pon', '1/1/9'), ('sn', 'XYZ'), ('vlan', 5000), ('line', 'a b'), ('modo', 'x')):
            with self.assertRaises(od.ProvisionamentoInvalido, msg=campo):
                od.validar_item({**self.item, campo: valor}, self.b)
        with self.assertRaises(od.ProvisionamentoInvalido):
            od.validar_item({**self.item, 'onu_id': 1}, self.b)    # já existe no backup

    def test_avisos(self):
        _, avisos = od.validar_item({**self.item, 'gem': 9, 'vlan': 3000, 'modo': 'router'}, self.b)
        self.assertEqual(len(avisos), 4)

    def test_alocar_ids_no_lote(self):
        a, _ = od.validar_item(self.item, self.b)
        b, _ = od.validar_item({**self.item, 'sn': 'DACM91000002'}, self.b)
        od.alocar_ids([a, b], self.b)
        self.assertEqual((a['onu_id'], a['sp_id']), (4, 3))
        self.assertEqual((b['onu_id'], b['sp_id']), (5, 6))

    def test_comandos_bridge_e_router(self):
        it, _ = od.validar_item(self.item, self.b)
        od.alocar_ids([it], self.b)
        self.assertEqual(od.roteiro(od.comandos_onu(it)), [
            'config', 'interface gpon 1/1/1', 'onu 4', 'name Joao_da_Silva',
            'serial-number DACM91000001', 'service-profile ONU-BRIDGE', 'line-profile PPPoE-Dados',
            'ethernet 1', 'negotiation', 'no shutdown', 'native vlan vlan-id 2603', 'top',
            'service-port 3 gpon 1/1/1 onu 4 gem 2 match vlan vlan-id 2603 action vlan replace vlan-id 2603',
            'commit', 'end'])
        it['modo'] = 'router'
        cmds = od.comandos_onu(it)
        self.assertIn('veip 1', cmds)
        self.assertNotIn('ethernet 1', cmds)


class ShellFalso:
    """Responde como o DmOS: IDs em `existentes` aparecem; o resto não existe."""

    def __init__(self, existentes=(), commit='Commit complete.', discovered=DISCOVERED):
        self.existentes, self.commit, self.discovered = set(existentes), commit, discovered
        self.enviados, self.modo = [], '#'

    def enviar(self, cmd, max_wait=60, responder='no'):
        self.enviados.append(cmd)
        if cmd.startswith('show interface gpon discovered'):
            return self.discovered
        if cmd.startswith('show running-config'):
            alvo = ' '.join(cmd.split()[2:])
            return f'{alvo}\n!\nOLT03# ' if alvo in self.existentes else NAO_EXISTE
        if cmd == 'config':
            self.modo = '(config)#'
            return 'Entering configuration mode terminal\nOLT03(config)# '
        if cmd == 'commit':
            return f'{self.commit}\nOLT03(config)# '
        if cmd in ('end', 'abort'):
            self.modo = '#'
            return 'OLT03# '
        if cmd.startswith('serial-number BAD'):
            return 'syntax error: unknown argument\nOLT03(config-gpon-onu)# '
        return f'OLT03{self.modo} '

    def prompt(self):
        return f'OLT03{self.modo}'

    def descartar(self):
        self.enviar('abort')

    def fechar(self):
        pass


def _rodar(shell, itens, backup):
    with mock.patch('clientes.olt_pon.conectar', return_value=(object(), None)), \
            mock.patch('clientes.olt_pon.desconectar'), \
            mock.patch.object(od, 'ShellDatacom', return_value=shell):
        return list(od.provisionar(object(), itens, backup))


class ProvisionarTest(SimpleTestCase):
    def setUp(self):
        self.b = od.parse_backup(BACKUP)
        self.it, _ = od.validar_item({'pon': '1/1/1', 'sn': 'DACM91000001', 'modo': 'bridge',
                                      'service': 'ONU-BRIDGE', 'line': 'PPPoE-Dados',
                                      'gem': 2, 'vlan': 2603}, self.b)

    def test_sucesso_um_commit(self):
        shell = ShellFalso()
        ev = [p for t, p in _rodar(shell, [self.it], self.b) if t == 'onu']
        self.assertEqual(ev[0]['status'], 'sucesso')
        self.assertEqual((ev[0]['onu_id'], ev[0]['sp_id']), (4, 3))
        self.assertEqual(shell.enviados.count('commit'), 1)
        self.assertIn('show running-config interface gpon 1/1/1 onu 4', shell.enviados)

    def test_id_ocupado_ao_vivo_anda_para_o_proximo(self):
        # Backup velho: a ONU 4 e o service-port 3 já existem na OLT.
        shell = ShellFalso(existentes={'interface gpon 1/1/1 onu 4', 'service-port 3'})
        ev = [p for t, p in _rodar(shell, [self.it], self.b) if t == 'onu']
        self.assertEqual(ev[0]['status'], 'sucesso')
        self.assertEqual((ev[0]['onu_id'], ev[0]['sp_id']), (5, 6))
        self.assertNotIn('onu 4', shell.enviados)

    def test_pon_cheia_ao_vivo_vira_erro_sem_quebrar_o_lote(self):
        # Visto na OLT03 em 12/11/2026: o único ID livre do backup (127) já
        # tinha sido usado depois do backup.
        existentes = {f'interface gpon 1/1/1 onu {i}' for i in range(4, 7)}
        shell = ShellFalso(existentes=existentes)
        with mock.patch.object(od, 'MAX_ONU_ID', 6):
            ev = [p for t, p in _rodar(shell, [self.it], self.b) if t == 'onu']
        self.assertEqual(ev[0]['status'], 'erro')
        self.assertIn('não tem ID de ONU livre', ev[0]['msg'])
        self.assertNotIn('config', shell.enviados)

    def test_recusa_descarta_sem_commit(self):
        self.it['sn'] = 'BAD000000000'
        shell = ShellFalso(discovered=DISCOVERED.replace('DACM91000001', 'BAD000000000'))
        ev = [p for t, p in _rodar(shell, [self.it], self.b) if t == 'onu']
        self.assertEqual(ev[0]['status'], 'erro')
        self.assertNotIn('commit', shell.enviados)
        self.assertIn('abort', shell.enviados)

    def test_commit_sem_confirmacao_e_erro(self):
        shell = ShellFalso(commit='Aborted: illegal reference')
        ev = [p for t, p in _rodar(shell, [self.it], self.b) if t == 'onu']
        self.assertEqual(ev[0]['status'], 'erro')
        self.assertIn('abort', shell.enviados)

    def test_nao_pendente_fica_ignorada(self):
        self.it['pon'] = '1/1/2'
        shell = ShellFalso()
        ev = [p for t, p in _rodar(shell, [self.it], self.b) if t == 'onu']
        self.assertEqual(ev[0]['status'], 'ignorada')
        self.assertNotIn('config', shell.enviados)

    def test_resposta_ambigua_nao_grava(self):
        shell = ShellFalso()
        shell.enviar = mock.Mock(side_effect=lambda c, **k: DISCOVERED if 'discovered' in c else '% timeout\nOLT03# ')
        ev = [p for t, p in _rodar(shell, [self.it], self.b) if t == 'onu']
        self.assertEqual(ev[0]['status'], 'erro')
        self.assertNotIn(mock.call('config'), shell.enviar.call_args_list)


class PerfisTest(SimpleTestCase):
    def setUp(self):
        self.b = od.parse_backup(BACKUP)
        self.spec = {'tipo': 'router', 'vlan': 3100, 'bandwidth': {'nome': 'PPPoE'},
                     'line': 'pppoe-router-3100', 'tcont': 1, 'gem': 1, 'map': 'pppoe',
                     'service': {'novo': True, 'nome': 'ONU-ROUTER',
                                 'onu_profile': {'novo': True, 'nome': 'ONU-ROUTER', 'pots': True}},
                     'uplinks': ['ten-gigabit-ethernet-1/1/1'], 'service_vlan': 'n:1'}

    def test_comandos_router_com_vlan_nova(self):
        n = od.validar_perfis(self.spec, self.b)
        self.assertEqual(od.comandos_perfis(n), [
            'profile gpon line-profile pppoe-router-3100', 'upstream-fec',
            'tcont 1 bandwidth-profile PPPoE', 'gem 1', 'tcont 1 priority 0', 'map pppoe',
            'veip 1 vlan 3100 cos any', 'top',
            'profile gpon onu-profile ONU-ROUTER', 'pots adaptive', 'veip 1', 'top',
            'profile gpon service-profile ONU-ROUTER', 'onu-profile ONU-ROUTER', 'top',
            'dot1q', 'vlan 3100', 'name VLAN-3100', 'interface ten-gigabit-ethernet-1/1/1', 'top',
            'service vlan 3100', 'type n:1', 'top'])
        self.assertEqual(len(od.checagens_perfis(n)), 3)

    def test_vlan_existente_so_o_que_falta(self):
        spec = {**self.spec, 'tipo': 'bridge', 'vlan': 2603, 'line': 'pppoe-bridge-2603',
                'service': {'nome': 'ONU-BRIDGE'},
                'bandwidth': {'novo': True, 'nome': 'BW-NOVO', 'tipo': 'type-4', 'kbps': 1024000},
                'uplinks': ['ten-gigabit-ethernet-1/1/1', 'gigabit-ethernet-1/1/2']}
        cmds = od.comandos_perfis(od.validar_perfis(spec, self.b))
        self.assertEqual(cmds[:3], ['profile gpon bandwidth-profile BW-NOVO', 'traffic type-4 max-bw 1024000', 'top'])
        self.assertIn('ethernet any vlan 2603 cos any', cmds)
        self.assertEqual(cmds[-4:], ['dot1q', 'vlan 2603', 'interface gigabit-ethernet-1/1/2', 'top'])
        self.assertNotIn('service vlan 2603', cmds)

    def test_nome_existente_recusado(self):
        with self.assertRaises(od.ProvisionamentoInvalido):
            od.validar_perfis({**self.spec, 'line': 'PPPoE-Dados'}, self.b)
        with self.assertRaises(od.ProvisionamentoInvalido):
            od.validar_perfis({**self.spec, 'service': {'novo': True, 'nome': 'ONU_BRIDGE',
                                                         'onu_profile': {'nome': 'ONU-BRIGDE'}}}, self.b)

    def test_criar_confere_nome_ao_vivo(self):
        n = od.validar_perfis(self.spec, self.b)
        shell = ShellFalso(existentes={'profile gpon line-profile pppoe-router-3100'})
        with mock.patch('clientes.olt_pon.conectar', return_value=(object(), None)), \
                mock.patch('clientes.olt_pon.desconectar'), \
                mock.patch.object(od, 'ShellDatacom', return_value=shell):
            status, _, msg = od.criar_perfis(object(), n)
        self.assertEqual(status, 'erro')
        self.assertNotIn('config', shell.enviados)


class RemoverTest(SimpleTestCase):
    def test_comandos_e_conferencia(self):
        self.assertEqual(od.comandos_remover('1/1/1', 4, [3]),
                         ['no service-port 3', 'interface gpon 1/1/1', 'no onu 4', 'top'])
        self.assertTrue(od.confere_onu('interface gpon 1/1/1\n onu 4\n  serial-number DACM91000001\n', 'DACM91000001'))
        self.assertFalse(od.confere_onu(NAO_EXISTE, 'DACM91000001'))
