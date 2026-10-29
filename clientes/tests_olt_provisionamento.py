"""Provisionamento de ONU/ONT em OLT Huawei: autofind, sugestão pelo backup,
comandos dos dois modos e a execução por etapas (ID devolvido pela OLT)."""
import os
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from clientes import olt_provisionamento as op

# Saída real do MA5800-X7 da WTD (SENTO SE), 29/10/2026.
AUTOFIND = """
   ----------------------------------------------------------------------------
   Number              : 1
   F/S/P               : 0/1/0
   Ont SN              : 485754433B65169B (HWTC-3B65169B)
   Password            : 0x31323334353637383930(1234567890)
   Loid                : 1234567890
   Checkcode           :
   VendorID            : HWTC
   Ont Version         : V2.79.00P6T5S
   Ont SoftwareVersion : V3.1.0-160815
   Ont EquipmentID     : 7756ACG4
   Ont autofind time   : 2026-10-29 04:18:41-03:00
   ----------------------------------------------------------------------------
   Number              : 2
   F/S/P               : 0/1/0
   Ont SN              : 485754433B654F8B (HWTC-3B654F8B)
   VendorID            : HWTC
   Ont EquipmentID     : 7756ACG4
   ----------------------------------------------------------------------------
   The number of GPON autofind ONT is 2
"""

# Recorte no formato do backup real (recuos inclusos).
BACKUP = """
[MA5800V100R021: 6611]
 board add 0/1 H902GPSF
 vlan 300 to 330 smart
 vlan 1000 smart
 ont-srvprofile gpon profile-id 300 profile-name "VLANID 300 "
  ont-port eth 1
  port vlan eth 1 translation 300 user-vlan 300
  commit
 ont-srvprofile gpon profile-id 1001 profile-name "adaptive"
  ont-port pots adaptive 32 eth adaptive 8
  commit
 ont-lineprofile gpon profile-id 300 profile-name "VLANID 300 "
  omcc encrypt off
  tcont 4 dba-profile-id 10
  gem add 1 eth tcont 4 encrypt off
  gem mapping 1 0 vlan 300
  commit
  quit
 ont-lineprofile gpon profile-id 301 profile-name "VLANID 301 "
  gem add 2 eth tcont 4 encrypt off
  commit
  quit
#
interface gpon 0/1
 port 0 ont-auto-find enable
 port 1 ont-auto-find enable
 ont add 0 0 sn-auth "485754433B6E3ED9" omci ont-lineprofile-id 300 ont-srvprofile-id 1001 desc "fabio11"
 ont add 0 1 sn-auth "4D4B5047B470A1A8" omci ont-lineprofile-id 300 ont-srvprofile-id 300 desc "universal"
 ont add 0 2 sn-auth "4D4B5047B482BC5C" omci ont-lineprofile-id 300 ont-srvprofile-id 300 desc "durcilene"
 ont port native-vlan 0 0 eth 1 vlan 300 priority 0
 ont port native-vlan 0 1 eth 1 vlan 300 priority 0
 ont port native-vlan 0 2 eth 1 vlan 300 priority 0
#
service-port 3 vlan 300 gpon 0/1/0 ont 0 gemport 1 multi-service user-vlan 300 tag-transform translate
service-port 4 vlan 300 gpon 0/1/0 ont 1 gemport 1 multi-service user-vlan 300 tag-transform translate
"""

INVENTARIO = {'placas': [{'slot': '0/1', 'tipo': 'H902GPSF', 'portas_total': 16, 'portas': []}]}


def _item(**kw):
    base = {'sn': '485754433B654F8B', 'slot': '0/1', 'porta': 0, 'vlan': 300,
            'line': 300, 'srv': 1001, 'gemport': 1, 'modo': 'onu', 'desc': ''}
    base.update(kw)
    return base


class AutofindTest(SimpleTestCase):
    def test_parse_saida_real(self):
        onus = op.parse_autofind(AUTOFIND)
        self.assertEqual([o['sn'] for o in onus], ['485754433B65169B', '485754433B654F8B'])
        self.assertEqual(onus[0]['slot'], '0/1')
        self.assertEqual(onus[0]['porta'], 0)
        self.assertEqual(onus[0]['sn_legivel'], 'HWTC-3B65169B')
        self.assertEqual(onus[1]['modelo'], '7756ACG4')

    def test_sn_repetido_entra_uma_vez(self):
        self.assertEqual(len(op.parse_autofind(AUTOFIND + AUTOFIND)), 2)

    def test_vazio(self):
        self.assertEqual(op.parse_autofind('The number of GPON autofind ONT is 0'), [])

    def test_vendor_do_sn(self):
        self.assertEqual(op.vendor_do_sn('485754433B654F8B'), 'HWTC')
        self.assertEqual(op.vendor_do_sn('4D4B5047B470A1A8'), 'MKPG')


class BackupTest(SimpleTestCase):
    def setUp(self):
        self.bk = op.parse_backup(BACKUP)

    def test_perfis(self):
        self.assertEqual(self.bk['lines'][300]['gems'], [1])
        self.assertEqual(self.bk['lines'][300]['vlans'], [300])
        self.assertEqual(self.bk['lines'][301]['gems'], [2])
        self.assertEqual(self.bk['srvs'][1001]['nome'], 'adaptive')
        self.assertEqual(self.bk['srvs'][300]['vlans'], [300])

    def test_vlans_com_faixa(self):
        self.assertIn(315, self.bk['vlans'])
        self.assertIn(1000, self.bk['vlans'])
        self.assertNotIn(331, self.bk['vlans'])

    def test_onts_juntam_add_native_e_service_port(self):
        ont = self.bk['onts'][('0/1', 0)][1]
        self.assertEqual((ont['line'], ont['srv'], ont['native_vlan'], ont['vlan'], ont['gemport']),
                         (300, 300, 300, 300, 1))
        self.assertEqual(self.bk['sns']['485754433B6E3ED9'], '0/1/0 ONT 0')

    def test_sugestao_srv_pelo_vendor(self):
        # Huawei copia o srv das outras Huawei (1001); MKPG, o das MKPG (300).
        hw = op.sugerir(self.bk, '0/1', 0, '485754433B654F8B')
        mk = op.sugerir(self.bk, '0/1', 0, '4D4B5047B4000000')
        self.assertEqual((hw['vlan'], hw['line'], hw['srv'], hw['gemport'], hw['modo']),
                         (300, 300, 1001, 1, 'onu'))
        self.assertEqual(mk['srv'], 300)

    def test_sugestao_pon_vazia(self):
        s = op.sugerir(self.bk, '0/1', 1, '485754433B654F8B')
        self.assertIsNone(s['vlan'])
        self.assertIn('sem ONTs', s['origem'])


class ValidacaoTest(SimpleTestCase):
    def setUp(self):
        self.bk = op.parse_backup(BACKUP)

    def test_item_ok_sem_aviso(self):
        it, avisos = op.validar_item(_item(sn='485754433b654f8b'), INVENTARIO, self.bk)
        self.assertEqual(it['sn'], '485754433B654F8B')
        self.assertEqual(avisos, [])

    def test_recusas(self):
        for kw in ({'sn': 'HWTC-3B654F8B'}, {'slot': '0/9'}, {'porta': 16},
                   {'vlan': 0}, {'vlan': 5000}, {'line': 'x'}, {'modo': 'bridge'}):
            with self.subTest(kw=kw), self.assertRaises(op.ProvisionamentoInvalido):
                op.validar_item(_item(**kw), INVENTARIO, self.bk)

    def test_avisos_do_backup(self):
        _, avisos = op.validar_item(_item(line=999, srv=998, vlan=2000), INVENTARIO, self.bk)
        self.assertEqual(len(avisos), 3)
        _, avisos = op.validar_item(_item(gemport=5), INVENTARIO, self.bk)
        self.assertIn('gemport 5', avisos[0])
        _, avisos = op.validar_item(_item(sn='485754433B6E3ED9'), INVENTARIO, self.bk)
        self.assertIn('0/1/0 ONT 0', avisos[0])

    def test_desc_sem_aspas(self):
        it, _ = op.validar_item(_item(desc='joao "da" silva'), INVENTARIO, self.bk)
        self.assertEqual(it['desc'], 'joao da silva')


class ComandosTest(SimpleTestCase):
    def test_generico_onu_e_ont(self):
        self.assertEqual(op.comandos_onu(_item(), 44), [
            'interface gpon 0/1',
            'ont add 0 sn-auth 485754433B654F8B omci ont-lineprofile-id 300 ont-srvprofile-id 1001',
            'ont port native-vlan 0 44 eth 1 vlan 300 priority 0',
            'quit',
            'service-port vlan 300 gpon 0/1/0 ont 44 gemport 1 multi-service user-vlan 300 tag-transform translate',
        ])

    def test_so_ont_sem_native_vlan(self):
        cmds = op.comandos_onu(_item(modo='ont', desc='cliente x'))
        self.assertFalse(any('native-vlan' in c for c in cmds))
        self.assertTrue(cmds[1].endswith('desc "cliente x"'))
        self.assertIn('ont {ONT_ID} gemport', cmds[-1])


class ShellFalso:
    """Responde como o MA5800: `ont add` devolve o ONTID; o que está em
    `recusas` devolve Failure."""

    def __init__(self, autofind=AUTOFIND, ids=None, recusas=(), sem_id=()):
        self.enviados, self.autofind = [], autofind
        self.ids, self.recusas, self.sem_id = list(ids or [44, 45]), recusas, sem_id

    def enviar(self, cmd, max_wait=60):
        self.enviados.append(cmd)
        for trecho in self.recusas:
            if trecho in cmd:
                return '  Failure: SN already exists\nMA5800-X7(config-if-gpon-0/1)#'
        if cmd == 'display ont autofind all':
            return self.autofind
        if cmd.startswith('ont add'):
            if any(sn in cmd for sn in self.sem_id):
                return '  Number of ONTs that can be added: 1, success: 1\nMA5800-X7(config-if-gpon-0/1)#'
            return f'  Number of ONTs that can be added: 1, success: 1\n  PortID :0, ONTID :{self.ids.pop(0)}\n'
        if cmd.startswith('display ont info by-sn'):
            return '  F/S/P                   : 0/1/0\n  ONT-ID                  : 77\n'
        return 'MA5800-X7(config)#'

    def fechar(self):
        pass


def _rodar(itens, shell):
    with mock.patch('clientes.olt_pon.conectar', return_value=(object(), None)), \
            mock.patch('clientes.olt_pon.desconectar'), \
            mock.patch.object(op, 'ShellOlt', return_value=shell):
        return [(t, p) for t, p in op.provisionar(SimpleNamespace(host='10.69.14.2'), itens)]


class ProvisionarTest(SimpleTestCase):
    def test_lote_usa_id_devolvido_pela_olt(self):
        shell = ShellFalso()
        eventos = _rodar([_item(sn='485754433B65169B'), _item(modo='ont')], shell)
        onus = [p for t, p in eventos if t == 'onu']
        self.assertEqual([(o['status'], o['ont_id']) for o in onus], [('sucesso', 44), ('sucesso', 45)])
        self.assertIn('ont port native-vlan 0 44 eth 1 vlan 300 priority 0', shell.enviados)
        self.assertIn('service-port vlan 300 gpon 0/1/0 ont 45 gemport 1 multi-service '
                      'user-vlan 300 tag-transform translate', shell.enviados)
        self.assertNotIn('ont port native-vlan 0 45 eth 1 vlan 300 priority 0', shell.enviados)
        # O service-port sai fora da interface gpon.
        i = shell.enviados.index('ont port native-vlan 0 44 eth 1 vlan 300 priority 0')
        self.assertEqual(shell.enviados[i + 1], 'quit')

    def test_onu_que_saiu_do_autofind_e_ignorada(self):
        shell = ShellFalso()
        eventos = _rodar([_item(sn='4D4B5047B470A1A8')], shell)
        self.assertEqual([p['status'] for t, p in eventos if t == 'onu'], ['ignorada'])
        self.assertFalse(any(c.startswith('ont add') for c in shell.enviados))

    def test_onu_em_outra_pon_e_ignorada(self):
        eventos = _rodar([_item(porta=3)], ShellFalso())
        self.assertIn('0/1/0', [p for t, p in eventos if t == 'onu'][0]['msg'])

    def test_recusa_no_ont_add_nao_para_o_lote(self):
        shell = ShellFalso(recusas=('485754433B65169B',))
        eventos = _rodar([_item(sn='485754433B65169B'), _item()], shell)
        self.assertEqual([p['status'] for t, p in eventos if t == 'onu'], ['erro', 'sucesso'])
        self.assertFalse(any('service-port' in c and 'ont 44' not in c and '65169B' in c
                             for c in shell.enviados))

    def test_service_port_recusado_e_parcial(self):
        shell = ShellFalso(recusas=('service-port',))
        onu = [p for t, p in _rodar([_item()], shell) if t == 'onu'][0]
        self.assertEqual((onu['status'], onu['ont_id']), ('parcial', 44))

    def test_sem_ontid_na_resposta_consulta_por_sn(self):
        shell = ShellFalso(sem_id=('485754433B654F8B',))
        onu = [p for t, p in _rodar([_item()], shell) if t == 'onu'][0]
        self.assertEqual(onu['ont_id'], 77)
        self.assertIn('display ont info by-sn 485754433B654F8B', shell.enviados)

    def test_falha_de_conexao_vira_evento_de_erro(self):
        with mock.patch('clientes.olt_pon.conectar', side_effect=ConnectionError('sem proxy')), \
                mock.patch('clientes.olt_pon.desconectar'):
            eventos = list(op.provisionar(SimpleNamespace(host='10.0.0.1'), [_item()]))
        self.assertEqual(eventos[-1], ('erro', {'erro': 'sem proxy'}))


class PromptTest(SimpleTestCase):
    def test_reconhece_prompts_do_ma5800(self):
        for cauda in ('\nMA5800-X7(config)#', '\nMA5800-X7(config-if-gpon-0/1)# ', '\nMA5800-X7#'):
            self.assertTrue(op._PROMPT_FIM_RE.search(cauda), cauda)
        self.assertTrue(op._CONTINUA_RE.search('display ont autofind all\n{ <cr>||<K> }:'))


class CanalFalso:
    """Canal Paramiko: a cada `quit` desce um nível; no último, fecha."""

    def __init__(self, niveis=3, confirma=False):
        self.niveis, self.confirma, self.enviados, self.closed, self._buf = niveis, confirma, [], False, ''

    def send(self, txt):
        self.enviados.append(txt)
        if txt == 'quit\n':
            self.niveis -= 1
            if self.niveis <= 0:
                if self.confirma:
                    self._buf = 'Are you sure to log out? (y/n)[n]:'
                else:
                    self.closed = True
        elif txt == 'y\n':
            self.closed = True

    def exit_status_ready(self):
        return self.closed

    def recv_ready(self):
        return bool(self._buf)

    def recv(self, n):
        b, self._buf = self._buf, ''
        return b.encode()

    def close(self):
        self.closed = True


class FecharTest(SimpleTestCase):
    def _shell(self, canal):
        sh = op.ShellOlt.__new__(op.ShellOlt)
        sh.channel = canal
        return sh

    @mock.patch('clientes.olt_provisionamento.time.sleep')
    def test_quit_ate_deslogar(self, _):
        canal = CanalFalso(niveis=3)
        self._shell(canal).fechar()
        self.assertEqual(canal.enviados, ['quit\n'] * 3)

    @mock.patch('clientes.olt_provisionamento.time.sleep')
    def test_confirma_logout(self, _):
        canal = CanalFalso(niveis=2, confirma=True)
        self._shell(canal).fechar()
        self.assertEqual(canal.enviados[-1], 'y\n')

    def test_mensagem_vty_cheia(self):
        from paramiko import ChannelException
        self.assertIn('VTYs', op.mensagem_de_falha(ChannelException(4, 'Resource shortage')))
        self.assertEqual(op.mensagem_de_falha(ValueError('x')), 'x')


class FabricanteScriptTest(SimpleTestCase):
    def test_pelo_modelo_e_nao_pelo_nome(self):
        from clientes.views import _fabricante_script
        acesso = SimpleNamespace(tipo='OLT HU SENTO SE',
                                 modelo=SimpleNamespace(fabricante='Huawei', nome='OLT HUAWEI MA5800X7'))
        self.assertEqual(_fabricante_script(acesso), {'familia': 'huawei_olt', 'fabricante': 'huawei'})
        zte = SimpleNamespace(tipo='OLT X', modelo=SimpleNamespace(fabricante='ZTE', nome='C610'))
        self.assertEqual(_fabricante_script(zte)['fabricante'], 'zte')
        sem = SimpleNamespace(tipo='BRAS', modelo=None)
        self.assertEqual(_fabricante_script(sem), {'familia': '', 'fabricante': ''})


class ModoTerminalViewsTest(SimpleTestCase):
    """Com o terminal aberto, o servidor só interpreta e audita."""

    def _req(self, body):
        import json
        from django.test import RequestFactory
        r = RequestFactory().post('/', data=json.dumps(body), content_type='application/json')
        r.user = SimpleNamespace(is_authenticated=True, username='op')
        return r

    def _carregar(self):
        bk = op.parse_backup(BACKUP)
        log = SimpleNamespace(data_backup=__import__('django.utils.timezone', fromlist=['now']).now())
        inv = {'placas': [{'slot': '0/1', 'tipo': 'H902GPSF', 'portas_total': 16,
                           'portas': [{'porta': 0, 'onts': 3}]}]}
        return mock.patch('clientes.onu_prov_views._carregar',
                          return_value=(SimpleNamespace(id=9486), inv, bk, log, None))

    def test_autofind_com_saida_nao_conecta(self):
        import json
        from clientes import onu_prov_views as v
        with self._carregar(), mock.patch('clientes.onu_prov_views.conectar') as conectar, \
                mock.patch('clientes.decorators.ferramenta_instancia_required', lambda k: (lambda f: f)):
            resp = v.onu_prov_autofind.__wrapped__.__wrapped__.__wrapped__(self._req({'saida': AUTOFIND}), 9486)
        conectar.assert_not_called()
        onus = json.loads(resp.content)['onus']
        self.assertEqual([o['sn'] for o in onus], ['485754433B65169B', '485754433B654F8B'])
        self.assertEqual(onus[0]['sugestao']['srv'], 1001)

    def test_registrar_grava_comandos_com_id(self):
        from clientes import onu_prov_views as v
        with self._carregar(), mock.patch('clientes.onu_prov_views.AcaoOltPon') as acao:
            resp = v.onu_prov_registrar.__wrapped__.__wrapped__.__wrapped__(
                self._req({'item': _item(), 'ont_id': 76, 'status': 'sucesso', 'saida': 'ok'}), 9486)
        self.assertEqual(resp.status_code, 200)
        kw = acao.objects.create.call_args.kwargs
        self.assertIn('ont 76 gemport 1', kw['comandos'])
        self.assertEqual((kw['acao'], kw['status'], kw['onts_afetadas']), ('provisionar', 'sucesso', 3))

    def test_registrar_recusa_status_invalido(self):
        from clientes import onu_prov_views as v
        with self._carregar():
            resp = v.onu_prov_registrar.__wrapped__.__wrapped__.__wrapped__(
                self._req({'item': _item(), 'status': 'talvez'}), 9486)
        self.assertEqual(resp.status_code, 400)


BACKUP_TRANSLATE = BACKUP.replace(
    ' ont-lineprofile gpon profile-id 301',
    ' dba-profile add profile-id 10 profile-name "DBA_PROFILE" type4 max 1000000\n'
    ' ont-lineprofile gpon profile-id 20 profile-name "LINE-MIGRACAO"\n'
    '  tcont 1 dba-profile-id 10\n'
    '  gem add 101 eth tcont 1\n'
    '  gem add 102 eth tcont 1\n'
    '  gem mapping 101 1 vlan 101\n'
    '  gem mapping 102 1 vlan 102\n'
    '  commit\n'
    '  quit\n'
    ' ont-lineprofile gpon profile-id 301')


def _spec(**kw):
    spec = {'srv': {'criar': True, 'id': 8, 'nome': 'translate'},
            'line': {'criar': True, 'id': 21, 'nome': 'LINE-MIGRACAO', 'tcont': 1, 'dba': 10},
            'vlans': [101, 102, 103, 104, 105]}
    spec.update(kw)
    return spec


class PerfisTranslateTest(SimpleTestCase):
    def setUp(self):
        self.bk = op.parse_backup(BACKUP_TRANSLATE)

    def test_backup_traz_mapas_e_dba(self):
        self.assertEqual(self.bk['lines'][20]['mapas'], [[101, 101], [102, 102]])
        self.assertEqual(self.bk['dbas'], {10: 'DBA_PROFILE'})

    def test_comandos_no_formato_do_modelo(self):
        norm, avisos = op.validar_perfis(_spec(), self.bk)
        self.assertEqual(avisos, [])
        self.assertEqual(op.comandos_perfis(norm), [
            'ont-srvprofile gpon profile-id 8 profile-name "translate"',
            'ont-port eth adaptive 8', 'native-vlan unconcern', 'commit', 'quit',
            'ont-lineprofile gpon profile-id 21 profile-name "LINE-MIGRACAO"',
            'tcont 1 dba-profile-id 10',
            'gem add 101 eth tcont 1', 'gem add 102 eth tcont 1', 'gem add 103 eth tcont 1',
            'gem add 104 eth tcont 1', 'gem add 105 eth tcont 1',
            'gem mapping 101 1 vlan 101', 'gem mapping 102 1 vlan 102', 'gem mapping 103 1 vlan 103',
            'gem mapping 104 1 vlan 104', 'gem mapping 105 1 vlan 105',
            'commit', 'quit'])
        self.assertEqual(op.checagens_perfis(norm), [
            'display ont-srvprofile gpon profile-id 8', 'display ont-lineprofile gpon profile-id 21'])

    def test_uma_vlan_so(self):
        norm, _ = op.validar_perfis(_spec(vlans=[3304], srv={'criar': False}), self.bk)
        self.assertEqual(norm['line']['gems'], [(1, 3304)])
        self.assertIsNone(norm['srv'])

    def test_gem_acima_de_1023_pega_o_proximo_livre(self):
        norm, _ = op.validar_perfis(_spec(vlans=[1, 3304, 2]), self.bk)
        self.assertEqual(norm['line']['gems'], [(1, 1), (3, 3304), (2, 2)])

    def test_limite_de_gems(self):
        with self.assertRaises(op.ProvisionamentoInvalido):
            op.validar_perfis(_spec(vlans=list(range(100, 100 + op.MAX_GEMS_PERFIL + 1))), self.bk)
        norm, _ = op.validar_perfis(_spec(vlans=list(range(100, 100 + op.MAX_GEMS_PERFIL))), self.bk)
        self.assertEqual(len(norm['line']['gems']), op.MAX_GEMS_PERFIL)

    def test_recusa_id_existente_e_nome_com_aspas(self):
        for kw in ({'line': {'criar': True, 'id': 300, 'nome': 'x', 'dba': 10}},
                   {'srv': {'criar': True, 'id': 1001, 'nome': 'x'}},
                   {'srv': {'criar': True, 'id': 8, 'nome': 'a"b'}},
                   {'srv': {'criar': False}, 'line': {'criar': False}},
                   {'vlans': []}, {'vlans': [5000]}):
            with self.subTest(kw=kw), self.assertRaises(op.ProvisionamentoInvalido):
                op.validar_perfis(_spec(**kw), self.bk)

    def test_perfil_existe_pela_resposta_real(self):
        self.assertFalse(op.perfil_existe('  Failure: The line profile does not exist\n'))
        self.assertFalse(op.perfil_existe('  Failure: The service profile does not exist\n'))
        self.assertTrue(op.perfil_existe('  Profile-ID          :300\n  Profile-name :VLANID 300'))
        self.assertTrue(op.perfil_existe('  Profile-ID  : 1001\n'))
        self.assertIsNone(op.perfil_existe('% Unknown command'))

    def test_criar_para_se_ja_existe_ao_vivo(self):
        norm, _ = op.validar_perfis(_spec(), self.bk)
        shell = mock.Mock()
        shell.enviar.side_effect = lambda c, **k: ('  Profile-ID  : 8\n' if 'srvprofile' in c
                                                   else '  Failure: The line profile does not exist')
        status, _, msg = op.criar_perfis(shell, norm)
        self.assertEqual(status, 'erro')
        self.assertIn('já existe', msg)
        self.assertFalse(any(c.args[0].startswith('ont-') for c in shell.enviar.call_args_list))

    def test_criar_ok(self):
        norm, _ = op.validar_perfis(_spec(), self.bk)
        shell = mock.Mock()
        shell.enviar.side_effect = lambda c, **k: ('  Failure: The profile does not exist'
                                                   if c.startswith('display') else 'MA5800-X7(config)#')
        status, _, _ = op.criar_perfis(shell, norm)
        self.assertEqual(status, 'sucesso')
        self.assertEqual(shell.enviar.call_args_list[-1].args[0], 'quit')


class ProvisionarTranslateTest(SimpleTestCase):
    def setUp(self):
        self.bk = op.parse_backup(BACKUP_TRANSLATE)

    def test_service_port_por_gem_com_translate(self):
        it, avisos = op.validar_item(_item(modo='translate', line=20, srv=300, vlan='', servicos=[
            {'gem': 101, 'user_vlan': 101, 'vlan': 300}, {'gem': 102, 'user_vlan': 102, 'vlan': 315}]),
            INVENTARIO, self.bk)
        self.assertEqual(avisos, [])
        self.assertEqual(op.comandos_onu(it, 0), [
            'interface gpon 0/1',
            'ont add 0 sn-auth 485754433B654F8B omci ont-lineprofile-id 20 ont-srvprofile-id 300',
            'quit',
            'service-port vlan 300 gpon 0/1/0 ont 0 gemport 101 multi-service user-vlan 101 tag-transform translate',
            'service-port vlan 315 gpon 0/1/0 ont 0 gemport 102 multi-service user-vlan 102 tag-transform translate',
        ])

    def test_translate_exige_servico_e_avisa_gem_fora_do_perfil(self):
        with self.assertRaises(op.ProvisionamentoInvalido):
            op.validar_item(_item(modo='translate', line=20, servicos=[]), INVENTARIO, self.bk)
        _, avisos = op.validar_item(_item(modo='translate', line=20, servicos=[
            {'gem': 9, 'user_vlan': 101, 'vlan': 300}]), INVENTARIO, self.bk)
        self.assertIn('não tem o GEM 9', avisos[0])


class DbaNovoTest(SimpleTestCase):
    def setUp(self):
        self.bk = op.parse_backup(BACKUP_TRANSLATE)

    def test_cria_dba_antes_do_line_e_usa_o_id(self):
        norm, avisos = op.validar_perfis(_spec(
            line={'criar': True, 'id': 21, 'nome': 'L', 'tcont': 1, 'dba': 'novo'},
            dba={'criar': True, 'id': 11, 'nome': 'DBA-TRANSLATE', 'tipo': 'type4', 'max': 1024000}), self.bk)
        self.assertEqual(avisos, [])
        cmds = op.comandos_perfis(norm)
        self.assertEqual(cmds[0], 'dba-profile add profile-id 11 profile-name "DBA-TRANSLATE" type4 max 1024000')
        self.assertIn('tcont 1 dba-profile-id 11', cmds)
        self.assertEqual(op.checagens_perfis(norm)[0], 'display dba-profile profile-id 11')

    def test_type3_e_recusas(self):
        norm, _ = op.validar_perfis(_spec(dba={'criar': True, 'id': 12, 'nome': 'D', 'tipo': 'type3',
                                               'assure': 2048, 'max': 4096}), self.bk)
        self.assertTrue(op.comandos_perfis(norm)[0].endswith('type3 assure 2048 max 4096'))
        for dba in ({'criar': True, 'id': 10, 'nome': 'D', 'tipo': 'type4', 'max': 1024},   # já existe
                    {'criar': True, 'id': 5, 'nome': 'D', 'tipo': 'type4', 'max': 1024},    # padrão 0–9
                    {'criar': True, 'id': 12, 'nome': 'D', 'tipo': 'type9', 'max': 1024},
                    {'criar': True, 'id': 12, 'nome': 'D', 'tipo': 'type3', 'assure': 4096, 'max': 1024}):
            with self.subTest(dba=dba), self.assertRaises(op.ProvisionamentoInvalido):
                op.validar_perfis(_spec(dba=dba), self.bk)

    def test_dba_padrao_nao_avisa(self):
        _, avisos = op.validar_perfis(_spec(line={'criar': True, 'id': 21, 'nome': 'L', 'dba': 2}), self.bk)
        self.assertEqual(avisos, [])

    def test_dba_inexistente_na_olt(self):
        self.assertFalse(op.perfil_existe('  Failure: The DBA profile does not exist'))
        self.assertTrue(op.perfil_existe('  Profile-name :  DBA_PROFILE\n  Profile-ID:               10'))


# Config real da OLT da DKY: ONU com VLAN 1 setada, traduzida para 101 no service-port.
BACKUP_VLAN_FIXA = """
 board add 0/7 H901GPHF 
 ont-srvprofile gpon profile-id 100 profile-name "SRV_DKY_100"
  ont-port pots adaptive 32 eth adaptive 8 
  port vlan eth 1 translation 1 user-vlan 1
  commit
 ont-lineprofile gpon profile-id 100 profile-name "VLAN_1"
  omcc encrypt on
  tcont 4 dba-profile-id 100
  gem add 1 eth tcont 4 encrypt on
  gem mapping 1 0 vlan 1
  commit
  quit
#
 interface gpon 0/7
 ont add 0 0 sn-auth "4D4F4E55004C22F1" omci ont-lineprofile-id 100 ont-srvprofile-id 100 desc "TESTE_001" 
#
 service-port 1119 vlan 101 gpon 0/7/0 ont 0 gemport 1 multi-service user-vlan 1 tag-transform translate
"""


class VlanFixaTest(SimpleTestCase):
    def setUp(self):
        self.bk = op.parse_backup(BACKUP_TRANSLATE)

    def test_comandos_iguais_ao_modelo_dky(self):
        norm, avisos = op.validar_perfis({
            'modelo': 'vlan_fixa', 'vlan_fixa': 1,
            'srv': {'criar': True, 'id': 100, 'nome': 'SRV_DKY_100', 'eth': 8, 'pots': 32, 'eth_vlan': 4},
            'line': {'criar': True, 'id': 100, 'nome': 'VLAN_1', 'dba': 'novo'},
            'dba': {'criar': True, 'id': 100, 'nome': 'DBA_100', 'tipo': 'type4', 'max': 1024000}}, self.bk)
        self.assertEqual(avisos, [])
        self.assertEqual(op.comandos_perfis(norm), [
            'dba-profile add profile-id 100 profile-name "DBA_100" type4 max 1024000',
            'ont-srvprofile gpon profile-id 100 profile-name "SRV_DKY_100"',
            'ont-port pots adaptive 32 eth adaptive 8',
            'port vlan eth 1 translation 1 user-vlan 1', 'port vlan eth 2 translation 1 user-vlan 1',
            'port vlan eth 3 translation 1 user-vlan 1', 'port vlan eth 4 translation 1 user-vlan 1',
            'commit', 'quit',
            'ont-lineprofile gpon profile-id 100 profile-name "VLAN_1"',
            'omcc encrypt on', 'tcont 4 dba-profile-id 100',
            'gem add 1 eth tcont 4 encrypt on', 'gem mapping 1 0 vlan 1',
            'commit', 'quit'])
        self.assertEqual(norm['line']['gems'], [(1, 1)])

    def test_sem_pots_e_outra_vlan(self):
        norm, _ = op.validar_perfis({'modelo': 'vlan_fixa', 'vlan_fixa': 20,
                                     'srv': {'criar': True, 'id': 101, 'nome': 'S', 'pots': 0, 'eth_vlan': 1},
                                     'line': {'criar': False}}, self.bk)
        self.assertEqual(op.comandos_perfis(norm)[1:3],
                         ['ont-port eth adaptive 8', 'port vlan eth 1 translation 20 user-vlan 20'])

    def test_backup_dky_sugere_translate_e_provisiona_igual(self):
        bk = op.parse_backup(BACKUP_VLAN_FIXA)
        self.assertEqual(bk['lines'][100]['mapas'], [[1, 1]])
        sug = op.sugerir(bk, '0/7', 0, '4D4F4E55004C9999')
        self.assertEqual((sug['vlan'], sug['line'], sug['srv'], sug['modo']), (101, 100, 100, 'translate'))
        inv = {'placas': [{'slot': '0/7', 'tipo': 'H901GPHF', 'portas_total': 16, 'portas': []}]}
        it, _ = op.validar_item({'sn': '4D4F4E55004C22F1', 'slot': '0/7', 'porta': 0, 'line': 100, 'srv': 100,
                                 'modo': 'translate', 'desc': 'TESTE_001',
                                 'servicos': [{'gem': 1, 'user_vlan': 1, 'vlan': 101}]}, inv, bk)
        self.assertEqual(op.comandos_onu(it, 0), [
            'interface gpon 0/7',
            'ont add 0 sn-auth 4D4F4E55004C22F1 omci ont-lineprofile-id 100 ont-srvprofile-id 100 desc "TESTE_001"',
            'quit',
            'service-port vlan 101 gpon 0/7/0 ont 0 gemport 1 multi-service user-vlan 1 tag-transform translate'])

    def test_sento_se_continua_onu(self):
        bk = op.parse_backup(BACKUP)
        self.assertEqual(op.sugerir(bk, '0/1', 0, '485754433B654F8B')['modo'], 'onu')


BACKUP_UPLINK = BACKUP_TRANSLATE.replace(
    ' vlan 1000 smart\n',
    ' vlan 1000 smart\n'
    ' port vlan 300 to 330 0/8 0\n'
    ' port vlan 1000 0/8 0\n'
    ' port vlan 1000 0/8 1\n') + (
    ' ont-lineprofile gpon profile-id 3998 profile-name "FULLCAM"\n'
    '  tcont 4 dba-profile-id 101\n'
    '  gem add 1 eth tcont 4\n'
    '  gem mapping 1 1 vlan 3998\n'
    '  gem mapping 1 2 vlan 3084\n'
    '  commit\n'
    '  quit\n')


class UplinkTest(SimpleTestCase):
    def setUp(self):
        self.bk = op.parse_backup(BACKUP_UPLINK)

    def test_uplinks_do_backup(self):
        ups = op.uplinks_do_backup(self.bk)
        self.assertEqual(ups[0], {'porta': '0/8 0', 'vlans': 32, 'padrao': True})
        self.assertEqual(ups[1], {'porta': '0/8 1', 'vlans': 1, 'padrao': False})
        # O `port vlan eth 1 translation` do srv profile não conta como uplink.
        self.assertEqual(len(ups), 2)

    def test_so_o_que_falta(self):
        up, avisos = op.validar_uplink({'criar': True, 'portas': ['0/8 0', '0/8 1'], 'vlans': [300, 1553]}, self.bk)
        self.assertEqual(avisos, [])
        self.assertEqual(op.comandos_vlans_uplink(up, self.bk),
                         ['vlan 1553 smart', 'port vlan 300 0/8 1', 'port vlan 1553 0/8 0', 'port vlan 1553 0/8 1'])

    def test_recusas_e_aviso(self):
        self.assertEqual(op.validar_uplink({'criar': False}, self.bk), (None, []))
        for spec in ({'criar': True, 'portas': [], 'vlans': [10]},
                     {'criar': True, 'portas': ['0/8'], 'vlans': [10]},
                     {'criar': True, 'portas': ['0/8 0'], 'vlans': []}):
            with self.subTest(spec=spec), self.assertRaises(op.ProvisionamentoInvalido):
                op.validar_uplink(spec, self.bk)
        _, avisos = op.validar_uplink({'criar': True, 'portas': ['0/9 3'], 'vlans': [10]}, self.bk)
        self.assertIn('0/9 3', avisos[0])

    def test_ja_existe_nao_e_falha(self):
        self.assertEqual(op.falha_vlan_uplink('  Failure: The VLAN already exists'), '')
        self.assertTrue(op.falha_vlan_uplink('  % Unknown command, the error locates at \'^\''))


class MultiVlanTest(SimpleTestCase):
    def setUp(self):
        self.bk = op.parse_backup(BACKUP_UPLINK)

    def test_perfil_fullcam_igual_ao_modelo(self):
        norm, _ = op.validar_perfis({
            'modelo': 'multi_vlan', 'vlans': [3998, 3084],
            'srv': {'criar': True, 'id': 3998, 'nome': 'FULLCAM'},
            'line': {'criar': True, 'id': 3997, 'nome': 'FULLCAM', 'dba': 101},
            'uplink': {'criar': True, 'portas': ['0/8 0']}}, self.bk)
        self.assertEqual(op.comandos_perfis(norm), [
            'ont-srvprofile gpon profile-id 3998 profile-name "FULLCAM"',
            'ont-port pots adaptive eth adaptive',
            'port vlan eth 1 translation 3998 user-vlan 3998',
            'port vlan eth 1 translation 3084 user-vlan 3084',
            'commit', 'quit',
            'ont-lineprofile gpon profile-id 3997 profile-name "FULLCAM"',
            'tcont 4 dba-profile-id 101',
            'gem add 1 eth tcont 4',
            'gem mapping 1 1 vlan 3998', 'gem mapping 1 2 vlan 3084',
            'commit', 'quit'])
        # Uplink padrão = as VLANs do próprio perfil.
        self.assertEqual(op.comandos_vlans_uplink(norm['uplink'], self.bk),
                         ['vlan 3998 smart', 'vlan 3084 smart', 'port vlan 3998 0/8 0', 'port vlan 3084 0/8 0'])

    def test_limite_de_8_vlans_no_gem(self):
        with self.assertRaises(op.ProvisionamentoInvalido):
            op.validar_perfis({'modelo': 'multi_vlan', 'vlans': list(range(10, 19)),
                               'line': {'criar': True, 'id': 3990, 'nome': 'X', 'dba': 101}}, self.bk)
        norm, _ = op.validar_perfis({'modelo': 'multi_vlan', 'vlans': list(range(10, 18)),
                                     'line': {'criar': True, 'id': 3990, 'nome': 'X', 'dba': 101}}, self.bk)
        mapas = [c for c in op.comandos_perfis(norm) if c.startswith('gem mapping')]
        self.assertEqual(mapas[0], 'gem mapping 1 1 vlan 10')
        self.assertEqual(mapas[-1], 'gem mapping 1 0 vlan 17')

    def test_translate_exige_vlan_da_olt_para_uplink(self):
        with self.assertRaises(op.ProvisionamentoInvalido):
            op.validar_perfis(_spec(uplink={'criar': True, 'portas': ['0/8 0']}), self.bk)
        norm, _ = op.validar_perfis(_spec(uplink={'criar': True, 'portas': ['0/8 0'], 'vlans': [3304]}), self.bk)
        self.assertEqual(op.comandos_vlans_uplink(norm['uplink'], self.bk), ['vlan 3304 smart', 'port vlan 3304 0/8 0'])

    def test_provisionar_transparente_duas_vlans_no_gem_1(self):
        it, avisos = op.validar_item(_item(modo='transparente', line=3998, srv=300, vlan='', servicos=[
            {'gem': 1, 'user_vlan': 3998}, {'gem': 1, 'user_vlan': 3084}]), INVENTARIO, self.bk)
        self.assertEqual(op.comandos_onu(it, 5)[-2:], [
            'service-port vlan 3998 gpon 0/1/0 ont 5 gemport 1 multi-service user-vlan 3998 tag-transform transparent',
            'service-port vlan 3084 gpon 0/1/0 ont 5 gemport 1 multi-service user-vlan 3084 tag-transform transparent'])
        self.assertTrue(any('3998' in a for a in avisos))   # VLAN ainda não existe na OLT

    def test_translate_com_varias_vlans(self):
        it, _ = op.validar_item(_item(modo='translate', line=20, servicos=[
            {'gem': 101, 'user_vlan': 101, 'vlan': 3304}, {'gem': 102, 'user_vlan': 102, 'vlan': 3305}]),
            INVENTARIO, self.bk)
        self.assertEqual([c.split(' tag-transform ')[1] for c in op.comandos_onu(it, 1) if c.startswith('service-port')],
                         ['translate', 'translate'])

    def test_vlan_repetida_no_mesmo_gem(self):
        with self.assertRaises(op.ProvisionamentoInvalido):
            op.validar_item(_item(modo='transparente', line=3998, servicos=[
                {'gem': 1, 'user_vlan': 3998}, {'gem': 1, 'user_vlan': 3998}]), INVENTARIO, self.bk)

    def test_provisionar_roda_preparo_antes(self):
        shell = ShellFalso()
        eventos = _rodar_com_preparo([_item()], shell, ['vlan 1553 smart', 'port vlan 1553 0/8 0'])
        n = len(op.PREAMBULO)
        self.assertEqual(shell.enviados[n:n + 3], ['vlan 1553 smart', 'port vlan 1553 0/8 0', 'display ont autofind all'])
        self.assertEqual([p['status'] for t, p in eventos if t == 'onu'], ['sucesso'])

    def test_preparo_recusado_nao_inicia_lote(self):
        shell = ShellFalso(recusas=('port vlan',))
        shell.recusas = ()
        shell.enviar_orig = shell.enviar
        shell.enviar = lambda c, **k: ("  % Parameter error" if c.startswith('port vlan') else shell.enviar_orig(c))
        eventos = _rodar_com_preparo([_item()], shell, ['port vlan 1553 0/9 9'])
        self.assertEqual(eventos[-1][0], 'erro')
        self.assertFalse(any(t == 'onu' for t, _ in eventos))

    def test_sugestao_transparente(self):
        bk = op.parse_backup(BACKUP_VLAN_FIXA.replace('user-vlan 1 tag-transform translate',
                                                      'user-vlan 101 tag-transform transparent'))
        self.assertEqual(op.sugerir(bk, '0/7', 0)['modo'], 'transparente')


def _rodar_com_preparo(itens, shell, preparo):
    with mock.patch('clientes.olt_pon.conectar', return_value=(object(), None)), \
            mock.patch('clientes.olt_pon.desconectar'), \
            mock.patch.object(op, 'ShellOlt', return_value=shell):
        return list(op.provisionar(SimpleNamespace(host='x'), itens, preparo))


class BackupAposPerfisTest(SimpleTestCase):
    """Perfil novo só chega ao provisionamento por um backup novo."""

    def _acesso(self, **kw):
        base = dict(id=9486, cliente_id=96, tipo='OLT HU SENTO SE', host='10.69.14.2', porta=22,
                    modelo='OLT HUAWEI MA5800X7', ativo=True, backup_habilitado=True, backup_template_id=1,
                    backup_template=SimpleNamespace(nome='backup olt huawei'),
                    cliente=SimpleNamespace(nome_empresa='WTD'))
        base.update(kw)
        return SimpleNamespace(**base)

    def test_grava_captura_no_formato_do_backup(self):
        import tempfile
        from django.test import override_settings
        from clientes import onu_prov_views as v
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp), \
                mock.patch('clientes.models.BackupLog.objects.create') as criar:
            v._gravar_backup_capturado(self._acesso(), SimpleNamespace(username='op'), BACKUP + '\nreturn\n')
            kw = criar.call_args.kwargs
            self.assertTrue(kw['arquivo_path'].startswith('backups/cliente_96/acesso_9486/OLT_HU_SENTO_SE_'))
            self.assertEqual(kw['status'], 'SUCESSO')
            texto = open(os.path.join(tmp, kw['arquivo_path'])).read()
        self.assertIn('Comando: display current-configuration', texto)
        self.assertIn('Acesso: TERMINAL DO OPERADOR', texto)
        # O inventário lido do arquivo gravado enxerga os perfis.
        self.assertIn(300, op.parse_backup(texto)['lines'])

    def test_recusa_captura_incompleta(self):
        from clientes import onu_prov_views as v
        carregar = mock.patch('clientes.onu_prov_views._carregar', return_value=(
            self._acesso(), {'placas': []}, op.parse_backup(BACKUP), SimpleNamespace(), None))
        for saida in ('% Unknown command', BACKUP, 'interface gpon 0/1\nreturn'):   # BACKUP sem "return"
            with self.subTest(saida=saida[:20]), carregar, \
                    mock.patch('clientes.onu_prov_views._gravar_backup_capturado') as gravar:
                import json
                from django.test import RequestFactory
                r = RequestFactory().post('/', data=json.dumps({'saida': saida}), content_type='application/json')
                r.user = SimpleNamespace(is_authenticated=True)
                resp = v.onu_prov_backup.__wrapped__.__wrapped__.__wrapped__(r, 9486)
                self.assertEqual(resp.status_code, 422)
                gravar.assert_not_called()

    def test_agendar_so_com_backup_habilitado_e_sem_duplicar(self):
        from clientes import onu_prov_views as v
        from django.core.cache import cache
        cache.delete('onuprov:backup:9486')
        with mock.patch('clientes.onu_prov_views.threading.Thread') as thread:
            self.assertFalse(v._agendar_backup(self._acesso(backup_habilitado=False), None))
            self.assertTrue(v._agendar_backup(self._acesso(), None))
            self.assertTrue(v._agendar_backup(self._acesso(), None))   # já rodando: não dispara outro
        self.assertEqual(thread.call_count, 1)
        cache.delete('onuprov:backup:9486')
