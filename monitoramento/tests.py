"""Vínculo Acesso ↔ host Zabbix (monitoramento/vinculo_zabbix.py): casamento
por IP, sincronismo que respeita a escolha manual e status para a aba Acessos."""
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from clientes.models import Acesso, Cliente
from monitoramento import vinculo_zabbix as vz
from monitoramento.models import ZabbixConfig, ZabbixHostVinculo

HOSTS = [
    {'hostid': '101', 'host': 'bng-01', 'name': 'BNG 01', 'ips': ['10.0.0.1']},
    {'hostid': '102', 'host': 'olt-a', 'name': 'OLT A', 'ips': ['10.0.0.2', '172.16.0.2']},
    {'hostid': '103', 'host': 'sw-x', 'name': 'SW X', 'ips': ['10.0.0.9']},
    {'hostid': '104', 'host': 'sw-x-dup', 'name': 'SW X (dup)', 'ips': ['10.0.0.9']},
    {'hostid': '10084', 'host': 'Zabbix server', 'name': 'Zabbix server', 'ips': ['127.0.0.1']},
]


class IpDoAcessoTest(TestCase):
    def test_formatos(self):
        self.assertEqual(vz.ip_do_acesso('10.0.0.1'), '10.0.0.1')
        self.assertEqual(vz.ip_do_acesso('10.0.0.1:8080'), '10.0.0.1')
        self.assertEqual(vz.ip_do_acesso('https://198.18.1.13/zabbix'), '198.18.1.13')
        self.assertEqual(vz.ip_do_acesso('http://10.0.0.1:8443/x'), '10.0.0.1')
        self.assertEqual(vz.ip_do_acesso('2001:db8::1'), '2001:db8::1')
        self.assertEqual(vz.ip_do_acesso('[2001:db8::1]:443'), '2001:db8::1')
        self.assertEqual(vz.ip_do_acesso('zabbix.exemplo.com'), '')
        self.assertEqual(vz.ip_do_acesso(''), '')


# Cache local: o default é o Redis de produção, e cache.clear() lá apagaria
# sessões e caches do sistema em uso.
@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.cliente = Cliente.objects.create(
            nome_empresa='Cliente ZBX', cnpj='33.333.333/0001-33',
            endereco='Rua Z, 1', email='zbx@example.com',
        )
        ZabbixConfig.objects.create(cliente=self.cliente, url='http://10.0.0.50/zabbix')
        self.cfg = SimpleNamespace(id='x', url='http://127.0.0.1:1/zabbix')
        p1 = mock.patch('monitoramento.agent_zabbix.resolver_config', return_value=self.cfg)
        p2 = mock.patch('monitoramento.services.hosts_para_vinculo', return_value=HOSTS)
        p1.start(); p2.start()
        self.addCleanup(mock.patch.stopall)

    def acesso(self, tipo, host, **kw):
        return Acesso.objects.create(cliente=self.cliente, tipo=tipo, host=host,
                                     protocolo=kw.pop('protocolo', 'SSH'), porta=22,
                                     usuario='noc', senha='x', **kw)


class SincronizarTest(_Base):
    def test_casa_por_ip_inclusive_segunda_interface_e_varios_acessos(self):
        bng_ssh = self.acesso('BNG SSH', '10.0.0.1')
        bng_web = self.acesso('BNG WEB', 'https://10.0.0.1:8443', protocolo='HTTPS')
        olt = self.acesso('OLT', '172.16.0.2')
        resumo = vz.sincronizar(self.cliente.id)

        self.assertEqual(resumo['vinculados'], 3)
        self.assertEqual(bng_ssh.zabbix_vinculo.zabbix_hostid, '101')
        self.assertEqual(Acesso.objects.get(id=bng_web.id).zabbix_vinculo.zabbix_hostid, '101')
        self.assertEqual(Acesso.objects.get(id=olt.id).zabbix_vinculo.zabbix_nome, 'OLT A')

    def test_ip_ambiguo_nao_vincula(self):
        sw = self.acesso('SW', '10.0.0.9')
        resumo = vz.sincronizar(self.cliente.id)
        self.assertFalse(ZabbixHostVinculo.objects.filter(acesso=sw).exists())
        self.assertEqual([a['acesso_id'] for a in resumo['ambiguos']], [sw.id])
        self.assertEqual({h['hostid'] for h in resumo['ambiguos'][0]['hosts']}, {'103', '104'})

    def test_ip_ambiguo_desempata_por_nome_identico(self):
        sw = self.acesso('sw.x dup', '10.0.0.9')
        vz.sincronizar(self.cliente.id)
        self.assertEqual(ZabbixHostVinculo.objects.get(acesso=sw).zabbix_hostid, '104')

    def test_localhost_do_zabbix_nao_casa(self):
        a = self.acesso('LOCAL', '127.0.0.1')
        vz.sincronizar(self.cliente.id)
        self.assertFalse(ZabbixHostVinculo.objects.filter(acesso=a).exists())

    def test_manual_nao_e_tocado_e_auto_obsoleto_sai(self):
        manual = self.acesso('BNG', '10.0.0.1')
        ZabbixHostVinculo.objects.create(acesso=manual, zabbix_hostid='103', zabbix_nome='velho', origem='manual')
        nenhum = self.acesso('OLT', '10.0.0.2')
        ZabbixHostVinculo.objects.create(acesso=nenhum, zabbix_hostid='', origem='manual')
        sumiu = self.acesso('ANTIGO', '10.9.9.9')
        ZabbixHostVinculo.objects.create(acesso=sumiu, zabbix_hostid='999', origem='auto')

        vz.sincronizar(self.cliente.id)

        v = ZabbixHostVinculo.objects.get(acesso=manual)
        self.assertEqual((v.zabbix_hostid, v.zabbix_nome, v.origem), ('103', 'SW X', 'manual'))
        self.assertEqual(ZabbixHostVinculo.objects.get(acesso=nenhum).zabbix_hostid, '')
        self.assertFalse(ZabbixHostVinculo.objects.filter(acesso=sumiu).exists())

    def test_manual_apontando_para_host_removido_aparece_no_resumo(self):
        a = self.acesso('BNG', '10.0.0.1')
        ZabbixHostVinculo.objects.create(acesso=a, zabbix_hostid='555', origem='manual')
        resumo = vz.sincronizar(self.cliente.id)
        self.assertEqual(resumo['manuais_sem_host'], [{'acesso_id': a.id, 'tipo': 'BNG'}])


class StatusEDefinirTest(_Base):
    def test_status_sincroniza_na_primeira_vez_e_usa_cache(self):
        a = self.acesso('BNG', '10.0.0.1')
        with mock.patch('monitoramento.services.status_nodes', return_value={'101': 'problem'}) as st:
            r1 = vz.status_cliente(self.cliente.id)
            r2 = vz.status_cliente(self.cliente.id)
        self.assertEqual(r1[str(a.id)]['status'], 'problem')
        self.assertEqual(r1, r2)
        st.assert_called_once()

    def test_definir_manual_valida_host_e_voltar_automatico(self):
        a = self.acesso('BNG', '10.0.0.1')
        with self.assertRaises(ValueError):
            vz.definir_manual(a, '999')
        vz.definir_manual(a, '102')
        self.assertEqual(ZabbixHostVinculo.objects.get(acesso=a).origem, 'manual')
        vz.voltar_automatico(a)
        vz.sincronizar(self.cliente.id)
        v = ZabbixHostVinculo.objects.get(acesso=a)
        self.assertEqual((v.zabbix_hostid, v.origem), ('101', 'auto'))

    def test_zabbix_fora_fica_em_cache_negativo(self):
        from monitoramento.agent_zabbix import ZabbixIndisponivel
        with mock.patch('monitoramento.agent_zabbix.resolver_config',
                        side_effect=ZabbixIndisponivel('sem rota')) as rc:
            for _ in range(3):
                with self.assertRaises(ZabbixIndisponivel):
                    vz.hosts_do_cliente(self.cliente.id, usar_cache=False)
        rc.assert_called_once()


def _it(itemid, key, name, units='', lastvalue='0', value_type='3'):
    return {'itemid': itemid, 'key_': key, 'name': name, 'units': units,
            'lastvalue': lastvalue, 'value_type': value_type}


class MetricasClassificacaoTest(TestCase):
    """Casos tirados dos templates reais dos clientes (Huawei, Datacom,
    MikroTik/Cisco via HOST-RESOURCES, Linux, OLT)."""
    from monitoramento import zabbix_metricas as zm

    def test_cpu_linux_ignora_tempos_parciais(self):
        itens = [_it('1', 'system.cpu.util', 'Linux: CPU utilization', '%', '12'),
                 _it('2', 'system.cpu.util[,guest]', 'Linux: CPU guest time', '%', '0'),
                 _it('3', 'system.cpu.load[all,avg15]', 'Linux: Load average (15m avg)', '', '1.2'),
                 _it('4', 'sensor.temp.value[x]', 'CPU: Temperature', '°C', '50')]
        self.assertEqual([i['itemid'] for i in self.zm.escolher_cpu(itens)], ['1'])

    def test_cpu_datacom_e_hrprocessorload_e_sem_fan(self):
        dat = [_it('1', 'cpuLoadFiveMinutesActive[1.1.49]', 'CPU usage (last 5 minutes)', '%', '17'),
               _it('2', 'cpuLoadFiveSecondsActive[1.1.49]', 'CPU usage (last 5 seconds)', '%', '30')]
        self.assertEqual([i['itemid'] for i in self.zm.escolher_cpu(dat)], ['1'])
        mk = [_it('1', 'system.cpu.util[hrProcessorLoad.1]', '#1: CPU utilization', '%'),
              _it('2', 'system.cpu.util[hrProcessorLoad.2]', '#2: CPU utilization', '%')]
        self.assertEqual(len(self.zm.escolher_cpu(mk)), 2)
        olt = [_it('1', 'cpu.system', 'Cpu System', '%', '21'), _it('2', 'fan.duty[1]', 'Fan 1 Duty Cycle', '%', '54')]
        self.assertEqual([i['itemid'] for i in self.zm.escolher_cpu(olt)], ['1'])

    def test_uptime_prefere_item_com_valor(self):
        # Huawei VRP: hrSystemUptime vem 0, sysUpTime é o que vale
        itens = [_it('1', 'system.hw.uptime[hrSystemUptime.0]', 'Uptime (hardware)', 'uptime', '0'),
                 _it('2', 'system.net.uptime[sysUpTime.0]', 'Uptime (network)', 'uptime', '3274837'),
                 _it('3', 'mpls.ldp.uptime', 'MPLS LDP Uptime', 's', '999')]
        self.assertEqual(self.zm.escolher_uptime(itens)['itemid'], '2')
        self.assertIsNone(self.zm.escolher_uptime([_it('1', 'datacom.uptime', 'System Uptime', '', '123')]))

    def test_interfaces_pareia_entrada_e_saida(self):
        itens = [_it('1', 'net.if.in[ifHCInOctets.5]', 'Interface 100GE0/0/1(LINK): Bits received', 'bps'),
                 _it('2', 'net.if.out[ifHCOutOctets.5]', 'Interface 100GE0/0/1(LINK): Bits sent', 'bps'),
                 _it('3', 'net.if.in.errors[ifInErrors.5]', 'Interface 100GE0/0/1(LINK): Inbound packets with errors')]
        g = self.zm.interfaces(itens)
        self.assertEqual(len(g), 1)
        self.assertEqual((g[0]['nome'], g[0]['in']['itemid'], g[0]['out']['itemid']), ('100GE0/0/1(LINK)', '1', '2'))

    def test_opticos_multilane_usa_media_e_porta_vazia(self):
        itens = [_it('1', 'hwEntityOpticalLaneRxPowerMLLane0[100GE0/0/1]', 'Optical Module Interface 100GE0/0/1: RxPower Lane0', 'dBm', '-30'),
                 _it('2', 'calculado.multilane.avg.rx.[1]', 'Optical Module Interface 100GE0/0/1: RxPower Avg Lane1-4', 'dBm', '-31.4'),
                 _it('3', 'calculado.multilane.avg.tx.[1]', 'Optical Module Interface 100GE0/0/1: TxPower Avg Lane1-4', 'dBm', '5.4'),
                 _it('4', 'huawei.sfp.rx[GE0/0/9]', 'SFP GE0/0/9 RX Power', 'dBm', '0'),
                 _it('5', 'huawei.sfp.tx[GE0/0/9]', 'SFP GE0/0/9 TX Power', 'dBm', '0')]
        g = {x['interface']: x for x in self.zm.opticos(itens)}
        self.assertEqual({m['item']['itemid'] for m in g['100GE0/0/1']['itens']}, {'2', '3'})
        self.assertFalse(g['100GE0/0/1']['vazio'])
        self.assertTrue(g['GE0/0/9']['vazio'])

    def test_opticos_em_microwatt_e_sem_unidade(self):
        self.assertAlmostEqual(self.zm.para_dbm('1000', 'uW'), 0.0)
        self.assertAlmostEqual(self.zm.para_dbm('100', 'uW'), -10.0)
        dat = [_it('1', 'datacom.sfp.ge_1_1_5.rx', 'SFP GE1/1/5 RX Power', '', '-1.56')]
        self.assertEqual(self.zm.opticos(dat)[0]['interface'], 'GE1/1/5')


class GraficosTest(_Base):
    ITENS = [_it('11', 'net.if.in[x.1]', 'Interface ge1: Bits received', 'bps', '100'),
             _it('12', 'net.if.out[x.1]', 'Interface ge1: Bits sent', 'bps', '50'),
             _it('13', 'system.cpu.util', 'CPU utilization', '%', '7')]

    def setUp(self):
        super().setUp()
        self.a = self.acesso('BNG', '10.0.0.1')
        vz.sincronizar(self.cliente.id)
        p = mock.patch('monitoramento.services._rpc', return_value=self.ITENS)
        p.start()
        mock.patch('monitoramento.services._get_auth_token', return_value='t').start()

    def test_itens_e_historico_so_do_host_vinculado(self):
        d = vz.itens_graficos(self.a)
        self.assertEqual(d['interfaces'][0]['itens'], ['11', '12'])
        self.assertEqual(d['cpu'][0]['itemid'], '13')
        with mock.patch('monitoramento.services.historico_janela', return_value={'pontos': []}) as h:
            vz.historico_graficos(self.a, ['11', '999', '12'], '24h')
        self.assertEqual([c.args[1] for c in h.call_args_list], ['11', '12'])
        with self.assertRaises(ValueError):
            vz.historico_graficos(self.a, ['999'], '6h')

    def test_acesso_sem_vinculo_nao_tem_grafico(self):
        outro = self.acesso('SEM', '10.7.7.7')
        with self.assertRaises(ValueError):
            vz.itens_graficos(outro)

    def test_status_traz_cpu_e_uptime(self):
        itens = [dict(_it('21', 'system.cpu.util', 'CPU utilization', '%', '42.5'), hostid='101'),
                 dict(_it('22', 'system.uptime', 'System uptime', 'uptime', '86400'), hostid='101')]
        with mock.patch('monitoramento.services._rpc', return_value=itens), \
             mock.patch('monitoramento.services.status_nodes', return_value={'101': 'up'}):
            st = vz.status_cliente(self.cliente.id)[str(self.a.id)]
        self.assertEqual((st['cpu'], st['uptime']), (42.5, 86400))


class CadastroBackupTest(TestCase):
    from monitoramento import zabbix_cadastro as zc

    def test_routeros_terse_com_restricao_e_snmp_ligado(self):
        bkp = ('# model = CCR1009-7G-1C-1S+\n/snmp community set [ find default=yes ] addresses=172.31.10.2/32 name=green-ro\n'
               '/snmp community add addresses=0.0.0.0/0 name=outra\n/snmp community add disabled=yes name=velha\n'
               '/snmp set enabled=yes trap-version=2\n/system identity set name=X\n')
        r = self.zc.snmp_do_backup(bkp)
        self.assertEqual(r['communities'], ['green-ro', 'outra'])
        self.assertEqual(r['restricoes'], {'green-ro': '172.31.10.2/32'})
        self.assertFalse(r['desabilitado'])

    def test_routeros_bloco_e_snmp_desligado(self):
        bkp = '# model = RB750Gr3\n/snmp community\nset [ find default=yes ] name=publica\n/system identity\nset name=X\n'
        r = self.zc.snmp_do_backup(bkp)
        self.assertEqual(r['communities'], ['publica'])
        self.assertTrue(r['desabilitado'])

    def test_huawei_cifrada_e_cisco(self):
        r = self.zc.snmp_do_backup(' snmp-agent community read cipher %^%#abc%^%#\n')
        self.assertEqual((r['communities'], r['cifrada']), ([], True))
        r = self.zc.snmp_do_backup('snmp-server community LEITURA RO\n')
        self.assertEqual(r['communities'], ['LEITURA'])


def _ops(*nomes):
    return {'templates': [{'id': str(i), 'nome': n} for i, n in enumerate(nomes, 1)],
            'grupos': [{'id': '50', 'nome': 'Switches Huawei'}, {'id': '51', 'nome': 'MIKROTIK'}], 'proxies': []}


class CadastroSugestaoTest(TestCase):
    from monitoramento import zabbix_cadastro as zc

    OPS = _ops('Mikrotik by SNMP', 'MikroTik CCR1009-7G-1C-1S+ by SNMP', 'MikroTik CRS309-1G-8S+IN by SNMP',
               'Template Sinal SFP Mikrotik - BEE', 'Huawei VRP by SNMP', 'MW - HUAWEI - HEALTH - S57xx', 'Generic by SNMP')

    def viz(self, **kw):
        base = {'hostid': '1', 'nome': 'h', 'modelo_id': None, 'funcao_id': None, 'fabricante': '',
                'templates': [], 'grupos': [], 'snmp': None, 'proxy': ''}
        base.update(kw)
        return base

    def test_mesmo_modelo_copia_o_conjunto(self):
        a = SimpleNamespace(modelo_id=7, funcao_id=1)
        v = [self.viz(modelo_id=7, fabricante='mikrotik', templates=['3', '4'], grupos=['51'])]
        s = self.zc.sugerir(a, 'CRS309-1G-8S+IN', 'Mikrotik', v, self.OPS)
        self.assertEqual((s['templates'], s['grupos']), (['3', '4'], ['51']))

    def test_oficial_do_modelo_com_complementos_e_troca_de_modelo(self):
        a = SimpleNamespace(modelo_id=8, funcao_id=None)
        v = [self.viz(modelo_id=7, fabricante='mikrotik', templates=['3', '4']),
             self.viz(modelo_id=9, fabricante='mikrotik', templates=['1', '4'])]
        s = self.zc.sugerir(a, 'CCR1009-7G-1C-1S+', 'Mikrotik', v, self.OPS)
        self.assertEqual(s['templates'], ['2', '4'])   # oficial do modelo + SFP; nada de "by SNMP" alheio
        # Vizinho da mesma função com template oficial de OUTRO modelo: troca
        a2 = SimpleNamespace(modelo_id=8, funcao_id=3)
        v2 = [self.viz(modelo_id=7, funcao_id=3, fabricante='mikrotik', templates=['3', '4'])]
        s2 = self.zc.sugerir(a2, 'CCR1009-7G-1C-1S+', 'Mikrotik', v2, self.OPS)
        self.assertEqual(s2['templates'], ['2', '4'])

    def test_bras_huawei_sem_vizinho_da_funcao_usa_oficial_generico(self):
        a = SimpleNamespace(modelo_id=20, funcao_id=5)
        v = [self.viz(modelo_id=21, funcao_id=6, fabricante='huawei', templates=['6'], grupos=['50'])]
        s = self.zc.sugerir(a, 'NE8000', 'Huawei', v, self.OPS)
        self.assertEqual(s['templates'], ['5'])
        self.assertEqual(s['grupos'], ['50'])

    def test_fabricante_desconhecido_cai_no_generico(self):
        a = SimpleNamespace(modelo_id=None, funcao_id=None)
        s = self.zc.sugerir(a, '', 'Parks', [], self.OPS)
        self.assertEqual(s['templates'], ['7'])


class CadastroParametrosTest(TestCase):
    from monitoramento import zabbix_cadastro as zc

    D = {'acesso_id': 5, 'host': 'SW/CORE 01', 'nome': 'SW/CORE 01', 'ip': '10.0.0.1', 'porta': '161',
         'snmp_versao': '2', 'community': 'leitura', 'templates': ['2'], 'grupos': ['50'], 'proxy': '9'}

    def test_versoes_do_zabbix_e_proxy(self):
        p7 = self.zc.montar_parametros(self.D, (7, 0, 21))
        self.assertEqual((p7['monitored_by'], p7['proxyid']), (1, '9'))
        self.assertNotIn('proxy_hostid', p7)
        p5 = self.zc.montar_parametros(self.D, (5, 0, 37))
        self.assertEqual(p5['proxy_hostid'], '9')
        self.assertEqual(p7['host'], 'SW_CORE 01')
        self.assertEqual(p7['interfaces'][0]['details'], {'version': 2, 'bulk': 1, 'community': 'leitura'})
        self.assertEqual(p7['tags'], [{'tag': 'crm_acesso_id', 'value': '5'}])

    def test_validacoes(self):
        for campo, valor in (('ip', '999.1.1.1'), ('porta', '0'), ('grupos', []), ('community', '')):
            with self.assertRaises(ValueError):
                self.zc.montar_parametros(dict(self.D, **{campo: valor}), (7, 0, 0))
        with self.assertRaises(ValueError):
            self.zc.montar_parametros(dict(self.D, snmp_versao='3'), (7, 0, 0))


class CadastroCriarTest(_Base):
    def test_cria_e_vincula_como_manual(self):
        from django.contrib.auth.models import User
        from monitoramento import zabbix_cadastro as zc
        u = User.objects.create_user('noc_cad')
        a = self.acesso('NOVO', '10.5.5.5')
        chamadas = []

        def rpc(url, metodo, params, auth=None, config=None):
            chamadas.append(metodo)
            return [] if metodo == 'host.get' else {'hostids': ['777']}

        with mock.patch('monitoramento.services._rpc', side_effect=rpc), \
             mock.patch('monitoramento.services._get_auth_token', return_value='t'), \
             mock.patch('monitoramento.services._get_zabbix_version', return_value=(7, 0, 0)):
            r = zc.criar(a, dict(CadastroParametrosTest.D, proxy=''), u)
        self.assertEqual(r['hostid'], '777')
        self.assertEqual(chamadas, ['host.get', 'host.create'])
        v = ZabbixHostVinculo.objects.get(acesso=a)
        self.assertEqual((v.zabbix_hostid, v.origem, v.criado_no_zabbix_por), ('777', 'manual', u))

    def test_nome_repetido_e_sem_permissao(self):
        from monitoramento import zabbix_cadastro as zc
        a = self.acesso('NOVO', '10.5.5.5')
        with mock.patch('monitoramento.services._rpc', return_value=[{'hostid': '1'}]), \
             mock.patch('monitoramento.services._get_auth_token', return_value='t'), \
             mock.patch('monitoramento.services._get_zabbix_version', return_value=(7, 0, 0)):
            with self.assertRaisesRegex(ValueError, 'Já existe'):
                zc.criar(a, CadastroParametrosTest.D)

        def rpc(url, metodo, params, auth=None, config=None):
            if metodo == 'host.create':
                raise Exception('No permissions to referred object or it does not exist!')
            return []
        with mock.patch('monitoramento.services._rpc', side_effect=rpc), \
             mock.patch('monitoramento.services._get_auth_token', return_value='t'), \
             mock.patch('monitoramento.services._get_zabbix_version', return_value=(7, 0, 0)):
            with self.assertRaisesRegex(ValueError, 'permissão de escrita'):
                zc.criar(a, CadastroParametrosTest.D)
        self.assertFalse(ZabbixHostVinculo.objects.filter(acesso=a).exists())


SNMP_ANTES = ('            enabled: no\n            contact:\n'
              ' 0 * name=TOMICH addresses=172.31.10.2/32 security=none read-access=yes\n'
              ' 1   name=outra addresses=0.0.0.0/0 security=none read-access=yes\n')
SNMP_DEPOIS = ('            enabled: yes\n'
               ' 0 * name=green-ro addresses=10.9.9.9/32 security=none read-access=yes\n'
               ' 1   name=outra addresses=0.0.0.0/0 security=none read-access=yes\n')


class VoltagemTest(TestCase):
    from monitoramento import zabbix_metricas as zm

    def test_escolhe_voltagem_do_equipamento(self):
        itens = [_it('1', 'huawei.sfp.voltage[40GE0/0/1]', 'SFP 40GE0/0/1 Supply Voltage', 'V', '3.3'),
                 _it('2', '1.3.6.1.4.1.2011.6.128.1.1.2.23.1.2.[1]', 'PON GPON 0/1/0: Voltagem', 'V', '3.2'),
                 _it('3', 'datacom.psu.1.status', 'PSU 1 Status', '', '1'),
                 _it('4', 'sensor.voltage.value[psu1]', 'PSU 1: Voltage', 'V', '12.1')]
        self.assertEqual([i['itemid'] for i in self.zm.escolher_voltagem(itens)], ['4'])
        crm = _it('9', self.zm.CHAVE_VOLTAGEM_CRM, 'Voltagem', 'V', '24.1')
        self.assertEqual([i['itemid'] for i in self.zm.escolher_voltagem(itens + [crm])], ['9'])

    def test_valor_fora_de_escala_e_milivolt(self):
        self.assertIsNone(self.zm.volts('1195', 'V'))     # OLT Parks: centésimos em "V"
        self.assertAlmostEqual(self.zm.volts('12100', 'mV'), 12.1)
        self.assertIsNone(self.zm.volts('0', 'V'))


class SnmpRouterOSTest(TestCase):
    from monitoramento import zabbix_cadastro as zc

    def test_le_print_terse(self):
        r = self.zc.ler_snmp_routeros(SNMP_ANTES)
        self.assertFalse(r['enabled'])
        self.assertEqual(r['communities'][0], {'name': 'TOMICH', 'addresses': '172.31.10.2/32', 'default': True, 'disabled': False})

    def test_comandos_padrao_existente_e_v3(self):
        atual = self.zc.ler_snmp_routeros(SNMP_ANTES)
        c = self.zc.comandos_snmp_routeros(atual, '10.9.9.9', {'version': 2, 'community': 'green-ro'})
        self.assertEqual(c, ['/snmp community set [ find default=yes ] addresses=10.9.9.9/32 name="green-ro"',
                             '/snmp set enabled=yes'])
        c = self.zc.comandos_snmp_routeros(atual, '10.9.9.9', {'version': 2, 'community': 'outra'})
        self.assertEqual(c[0], '/snmp community set [ find name="outra" ] addresses=10.9.9.9/32')
        v3 = {'version': 3, 'securityname': 'wtdtelecom', 'securitylevel': 2, 'authprotocol': 1,
              'authpassphrase': 'procyon', 'privprotocol': 1, 'privpassphrase': 'procyon'}
        c = self.zc.comandos_snmp_routeros(atual, '10.9.9.9', v3)
        self.assertIn('name="wtdtelecom" security=private authentication-protocol=SHA1 '
                      'authentication-password="procyon" encryption-protocol=AES encryption-password="procyon"', c[0])
        with self.assertRaises(ValueError):
            self.zc.comandos_snmp_routeros(atual, '10.9.9.9', {'version': 2, 'community': 'a b;/system reboot'})

    def _acesso(self):
        return SimpleNamespace(id=1, tipo='RB', porta_ssh=lambda: 22)

    def test_configura_e_confere(self):
        saidas = iter([SNMP_ANTES, '', SNMP_DEPOIS])
        with mock.patch('clientes.consumers.platform_ssh_exec', side_effect=lambda *a, **k: next(saidas)) as ex:
            r = self.zc.configurar_snmp_mikrotik(self._acesso(), '10.9.9.9', {'version': 2, 'community': 'green-ro'})
        self.assertEqual(ex.call_args_list[1].args[0].porta, 22)
        self.assertIn('name="green-ro"', r['comandos'][0])

    def test_recusa_e_leitura_que_nao_confere(self):
        saidas = iter([SNMP_ANTES, 'failure: already have community with such name'])
        with mock.patch('clientes.consumers.platform_ssh_exec', side_effect=lambda *a, **k: next(saidas)):
            with self.assertRaisesRegex(ValueError, 'recusou'):
                self.zc.configurar_snmp_mikrotik(self._acesso(), '10.9.9.9', {'version': 2, 'community': 'green-ro'})
        saidas = iter([SNMP_ANTES, '', SNMP_ANTES])
        with mock.patch('clientes.consumers.platform_ssh_exec', side_effect=lambda *a, **k: next(saidas)):
            with self.assertRaisesRegex(ValueError, 'leitura de volta'):
                self.zc.configurar_snmp_mikrotik(self._acesso(), '10.9.9.9', {'version': 2, 'community': 'green-ro'})


class CadastroComSnmpTest(_Base):
    def _mikrotik(self):
        from modelo_equipamento.models import Modelo_equipamento
        m = Modelo_equipamento.objects.create(nome='CCR-TESTE', fabricante='Mikrotik')
        return self.acesso('RB NOVO', '10.5.5.5', modelo=m)

    def test_equipamento_primeiro_e_voltagem_depois(self):
        from monitoramento import zabbix_cadastro as zc
        a = self._mikrotik()
        ordem = []

        def rpc(url, metodo, params, auth=None, config=None):
            ordem.append(metodo)
            if metodo == 'host.create':
                return {'hostids': ['777']}
            if metodo == 'hostinterface.get':
                return [{'interfaceid': '55'}]
            if metodo == 'item.create':
                self.item = params
                return {'itemids': ['900']}
            return []

        def configurar(acesso, ip, snmp):
            ordem.append(f'ssh:{ip}:{snmp["community"]}')
            return {'comandos': ['x']}

        with mock.patch('monitoramento.services._rpc', side_effect=rpc), \
             mock.patch('monitoramento.services._get_auth_token', return_value='t'), \
             mock.patch('monitoramento.services._get_zabbix_version', return_value=(7, 0, 0)), \
             mock.patch('monitoramento.zabbix_cadastro.configurar_snmp_mikrotik', side_effect=configurar):
            r = zc.criar(a, dict(CadastroParametrosTest.D, proxy='', configurar_snmp=True, ip_zabbix='10.9.9.9'))
        self.assertEqual(ordem[:3], ['host.get', 'ssh:10.9.9.9:leitura', 'host.create'])
        self.assertEqual((self.item['type'], self.item['snmp_oid'], self.item['interfaceid']),
                         (20, '1.3.6.1.4.1.14988.1.1.3.8.0', '55'))
        self.assertEqual(self.item['preprocessing'][0]['params'], '0.1')
        self.assertEqual((r['avisos'], r['snmp_configurado']), ([], ['x']))

    def test_equipamento_recusou_nao_cria_host(self):
        from monitoramento import zabbix_cadastro as zc
        a = self._mikrotik()
        metodos = []
        with mock.patch('monitoramento.services._rpc', side_effect=lambda u, m, *x, **k: metodos.append(m) or []), \
             mock.patch('monitoramento.services._get_auth_token', return_value='t'), \
             mock.patch('monitoramento.services._get_zabbix_version', return_value=(7, 0, 0)), \
             mock.patch('monitoramento.zabbix_cadastro.configurar_snmp_mikrotik', side_effect=ValueError('recusou')):
            with self.assertRaises(ValueError):
                zc.criar(a, dict(CadastroParametrosTest.D, configurar_snmp=True, ip_zabbix='10.9.9.9'))
        self.assertNotIn('host.create', metodos)
        self.assertFalse(ZabbixHostVinculo.objects.filter(acesso=a).exists())

    def test_macro_resolvida_para_o_equipamento(self):
        from monitoramento import zabbix_cadastro as zc
        a = self._mikrotik()
        vistos = []

        def rpc(url, metodo, params, auth=None, config=None):
            if metodo == 'usermacro.get':
                return [{'macro': '{$SNMP_COMMUNITY}', 'value': 'TOMICH', 'type': '0'}]
            return {'hostids': ['1']} if metodo == 'host.create' else []

        with mock.patch('monitoramento.services._rpc', side_effect=rpc), \
             mock.patch('monitoramento.services._get_auth_token', return_value='t'), \
             mock.patch('monitoramento.services._get_zabbix_version', return_value=(7, 0, 0)), \
             mock.patch('monitoramento.zabbix_cadastro.configurar_snmp_mikrotik',
                        side_effect=lambda ac, ip, snmp: vistos.append(snmp['community']) or {'comandos': []}):
            zc.criar(a, dict(CadastroParametrosTest.D, community='{$SNMP_COMMUNITY}', proxy='',
                             configurar_snmp=True, ip_zabbix='10.9.9.9'))
        self.assertEqual(vistos, ['TOMICH'])

    def test_status_oferece_ativar_voltagem_em_mikrotik(self):
        a = self._mikrotik()
        a.host = '10.0.0.1'
        a.save()
        vz.sincronizar(self.cliente.id)
        with mock.patch('monitoramento.services._rpc', return_value=[]), \
             mock.patch('monitoramento.services._get_auth_token', return_value='t'), \
             mock.patch('monitoramento.services.status_nodes', return_value={'101': 'up'}):
            st = vz.status_cliente(self.cliente.id)[str(a.id)]
        self.assertTrue(st['volt_ativavel'])


class CadastroAvisoIpZabbixTest(_Base):
    def test_avisa_quando_ip_da_api_nao_esta_liberado_hoje(self):
        from modelo_equipamento.models import Modelo_equipamento
        from monitoramento import zabbix_cadastro as zc
        m = Modelo_equipamento.objects.create(nome='CCR-AVISO', fabricante='Mikrotik')
        a = self.acesso('RB AVISO', '10.6.6.6', modelo=m)
        bkp = ('# model = CCR-AVISO\n/snmp community set [ find default=yes ] addresses=172.31.10.2/32 name=green-ro\n'
               '/snmp set enabled=yes\n')
        ops = {'templates': [], 'grupos': [], 'proxies': [], 'macros': {}}
        with mock.patch('monitoramento.zabbix_cadastro._conteudo_backup', return_value=bkp), \
             mock.patch('monitoramento.zabbix_cadastro.opcoes', return_value=ops), \
             mock.patch('monitoramento.zabbix_cadastro.vizinhos', return_value=[]), \
             mock.patch('monitoramento.zabbix_cadastro.ip_do_zabbix', return_value=('170.79.92.46', 'API')):
            p = zc.previa(a)
        self.assertTrue(any('só aceita 172.31.10.2/32' in x for x in p['avisos']))
        self.assertEqual(p['community_padrao'], {'name': 'green-ro', 'addresses': '172.31.10.2/32'})


class LimparCacheTest(_Base):
    def test_config_nova_esquece_falha_e_sincronismo_antigos(self):
        from monitoramento.agent_zabbix import ZabbixIndisponivel
        with mock.patch('monitoramento.agent_zabbix.resolver_config', side_effect=ZabbixIndisponivel('fora')):
            with self.assertRaises(ZabbixIndisponivel):
                vz.hosts_do_cliente(self.cliente.id, usar_cache=False)
        vz.limpar_cache(self.cliente.id)
        # Com o cache limpo, volta a tentar (e agora o Zabbix responde)
        self.assertEqual(len(vz.hosts_do_cliente(self.cliente.id, usar_cache=False)), len(HOSTS))


class DisponibilidadeVersaoTest(TestCase):
    """Zabbix < 5.4 guarda a disponibilidade no host; 5.4+ na interface."""
    from monitoramento import services

    def test_status_nodes_zabbix_52(self):
        cfg = SimpleNamespace(id='c', url='http://z')
        hosts = [{'hostid': '1', 'available': '1', 'snmp_available': '0'},
                 {'hostid': '2', 'available': '0', 'snmp_available': '2'},
                 {'hostid': '3', 'available': '0', 'snmp_available': '0'}]
        with mock.patch.object(self.services, '_get_auth_token', return_value='t'), \
             mock.patch.object(self.services, '_get_zabbix_version', return_value=(5, 2, 7)), \
             mock.patch.object(self.services, '_rpc', side_effect=[hosts, []]) as rpc:
            st = self.services.status_nodes(cfg, ['1', '2', '3'])
        self.assertEqual(st, {'1': 'up', '2': 'down', '3': 'unknown'})
        params = rpc.call_args_list[0].args[2]
        self.assertNotIn('selectInterfaces', params)
        self.assertIn('snmp_available', params['output'])

    def test_status_nodes_zabbix_7(self):
        cfg = SimpleNamespace(id='c', url='http://z')
        hosts = [{'hostid': '1', 'interfaces': [{'available': '2'}, {'available': '1'}]}]
        with mock.patch.object(self.services, '_get_auth_token', return_value='t'), \
             mock.patch.object(self.services, '_get_zabbix_version', return_value=(7, 0, 0)), \
             mock.patch.object(self.services, '_rpc', side_effect=[hosts, []]):
            self.assertEqual(self.services.status_nodes(cfg, ['1']), {'1': 'up'})


class CadastroDuploTest(_Base):
    def test_segundo_cadastro_simultaneo_e_acesso_ja_vinculado_sao_recusados(self):
        from monitoramento import zabbix_cadastro as zc
        a = self.acesso('NOVO', '10.5.5.5')
        cache.add(f'zbx_cad_trava:{a.id}', 1, 60)
        with self.assertRaisesRegex(ValueError, 'em andamento'):
            zc.criar(a, CadastroParametrosTest.D)
        cache.delete(f'zbx_cad_trava:{a.id}')
        ZabbixHostVinculo.objects.create(acesso=a, zabbix_hostid='5', origem='manual')
        with self.assertRaisesRegex(ValueError, 'já está vinculado'):
            zc.criar(a, CadastroParametrosTest.D)
        self.assertIsNone(cache.get(f'zbx_cad_trava:{a.id}'))


class OpticoAgrupamentoTest(TestCase):
    from monitoramento import zabbix_metricas as zm

    def test_datacom_potencia_recebida_transmitida_por_ramo_numa_linha(self):
        base = 'na Interface hundred-gigabit-ethernet-1/1/1 - SW-HU-JUINA - Ramo'
        itens = [_it(str(i), f'k{i}', f'Potência {d} ({s}) {base} {r}', 'dBm', v)
                 for i, (d, s, r, v) in enumerate([('Recebida', 'RX', 1, '-28.5'), ('Recebida', 'RX', 2, '-27.4'),
                                                    ('Transmitida', 'TX', 1, '3.7'), ('Transmitida', 'TX', 2, '3.8')])]
        g = self.zm.opticos(itens)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]['interface'], 'hundred-gigabit-ethernet-1/1/1 - SW-HU-JUINA')
        self.assertEqual(sorted((m['sentido'], m['lane']) for m in g[0]['itens']),
                         [('rx', '1'), ('rx', '2'), ('tx', '1'), ('tx', '2')])

    def test_outros_formatos_pareiam(self):
        for rx, tx, iface in [('SFP GE1/1/5 RX Power', 'SFP GE1/1/5 TX Power', 'GE1/1/5'),
                              ('XGigabitEthernet0/0/1: Rx Power', 'XGigabitEthernet0/0/1: Tx Power', 'XGigabitEthernet0/0/1'),
                              ('SFP 100GE1/1/1 Ch1 RX Power', 'SFP 100GE1/1/1 Ch1 TX Power', '100GE1/1/1')]:
            g = self.zm.opticos([_it('1', 'a', rx, 'dBm', '-5'), _it('2', 'b', tx, 'dBm', '1')])
            self.assertEqual([x['interface'] for x in g], [iface], rx)
        self.assertEqual(self.zm._opt_sentido('10giga-ethernet0/0(OLT-TX_M.SERRAT): Sinal Base'), 'rx')


class OpticoDescricaoTest(TestCase):
    from monitoramento import zabbix_metricas as zm

    def test_descricao_vem_do_trafego_e_menos_001_e_sem_leitura(self):
        itens = [_it('1', 'net.if.in[x.1]', 'Interface XGigabitEthernet0/0/1(HUAWEI NE8000): Bits received', 'bps'),
                 _it('2', 'net.if.out[x.1]', 'Interface XGigabitEthernet0/0/1(HUAWEI NE8000): Bits sent', 'bps'),
                 _it('3', 'rx[1]', 'XGigabitEthernet0/0/1: Rx Power', '!dBm', '-1.34'),
                 _it('4', 'tx[1]', 'XGigabitEthernet0/0/1: Tx Power', '!dBm', '-3.6'),
                 _it('5', 'rx[2]', '40GE0/0/5: Rx Power', '!dBm', '-0.01'),
                 _it('6', 'tx[2]', '40GE0/0/5: Tx Power', '!dBm', '-0.01')]
        g = {x['interface']: x for x in self.zm.com_descricao(self.zm.opticos(itens), self.zm.interfaces(itens))}
        self.assertIn('XGigabitEthernet0/0/1(HUAWEI NE8000)', g)
        self.assertFalse(g['XGigabitEthernet0/0/1(HUAWEI NE8000)']['vazio'])
        self.assertTrue(g['40GE0/0/5']['vazio'])


class TemperaturaTest(TestCase):
    from monitoramento import zabbix_metricas as zm

    def test_temperatura_do_equipamento_e_nao_de_modulo(self):
        itens = [_it('1', 'hwEntityOpticalTemperature[x]', '100GE0/3/0: Temperatura', 'C', '31'),
                 _it('2', 'temperatureSensorCurrentTemperature[3]', 'Temperatura no Sensor TCV 100G 1/1/10', '°C', '60'),
                 _it('3', 'tempTholdLow[1]', 'Limite Inferior de Temperatura na Interface X', '°C', '0'),
                 _it('4', 'icmppingsec', 'ICMP - Tempo de Resposta', 's', '0.003'),
                 _it('5', 'pkSfpDiagTemperature_1', 'Temperature SFP Gpon 1/1', 'ºC', '40'),
                 _it('6', 'hwEntityTemperature[1]', 'Temperatura no Slot MPU Board 0', '°C', '39'),
                 _it('7', 'sensor.temp.value[mtxrHlProcessorTemperature.0]', 'CPU: Temperature', '°C', '47')]
        self.assertEqual(sorted(i['itemid'] for i in self.zm.escolher_temperatura(itens)), ['6', '7'])

    def test_leitura_implausivel(self):
        self.assertIsNone(self.zm.celsius('214748'))
        self.assertIsNone(self.zm.celsius('0'))
        self.assertEqual(self.zm.celsius('67.28'), 67.28)


class StatusPortaTest(TestCase):
    from monitoramento import zabbix_metricas as zm

    def test_formatos_de_status_operacional(self):
        itens = [_it('1', 'net.if.status[ifOperStatus.12]', 'Interface 25GE0/5/29(UPLINK): Operational status', '', '2'),
                 _it('2', 'ifOperStatus[XGigabitEthernet0/0/1]', 'Status da Interface XGigabitEthernet0/0/1 - P2P', '', '1'),
                 _it('3', 'datacom.if.status[1]', 'Interface GigabitEthernet1/1/1: Operational status', '', '7'),
                 _it('4', 'net.if.admin.status[x]', 'Interface ge9(): Admin status', '', '2'),
                 _it('5', 'statusShutdown[XGigabitEthernet0/0/9]', 'Status Shutdown XGigabitEthernet0/0/9', '', '1'),
                 _it('6', 'onu.status[1]', 'ONU Status 1.2.2', '', '1')]
        self.assertEqual(self.zm.status_portas(itens), {'25ge0/5/29': 'down', 'xgigabitethernet0/0/1': 'up',
                                                         'gigabitethernet1/1/1': 'down'})
        self.assertEqual(self.zm.porta_base('XGigabitEthernet0/0/1 - P2P-SW-HU-TAPURAH-P10'), 'xgigabitethernet0/0/1')
        self.assertEqual(self.zm.porta_base('XGigabitEthernet0/0/1(HUAWEI NE8000)'), 'xgigabitethernet0/0/1')

    def test_limiares_do_modulo_nao_sao_sinal(self):
        itens = [_it('1', 'a', '100GE0/3/0: Rx Power', 'dBm', '-7'), _it('2', 'b', '100GE0/3/0: Rx Power High Threshold', 'dBm', '4'),
                 _it('3', 'c', '100GE0/3/0: Rx Power Low Warning', 'dBm', '-14')]
        g = self.zm.opticos(itens)
        self.assertEqual([m['item']['itemid'] for m in g[0]['itens']], ['1'])


class StatusInferidoTest(_Base):
    def test_sem_item_de_status_e_sem_luz_vira_down(self):
        itens = [_it('1', 'rxpower[XGigabitEthernet0/0/2]', 'Potência Recebida (RX) na Interface XGigabitEthernet0/0/2 - P2P - Ramo 1', 'dBm', '-40'),
                 _it('2', 'txpower[XGigabitEthernet0/0/2]', 'Potência Transmitida (TX) na Interface XGigabitEthernet0/0/2 - P2P - Ramo 1', 'dBm', '-40'),
                 _it('3', 'rxpower[XGigabitEthernet0/0/5]', 'Potência Recebida (RX) na Interface XGigabitEthernet0/0/5 - Ramo 1', 'dBm', '-4.8'),
                 _it('4', 'ifOperStatus[XGigabitEthernet0/0/1]', 'Status da Interface XGigabitEthernet0/0/1 - X', '', '1'),
                 _it('5', 'rxpower[XGigabitEthernet0/0/1]', 'Potência Recebida (RX) na Interface XGigabitEthernet0/0/1 - X - Ramo 1', 'dBm', '-25')]
        a = self.acesso('SW', '10.0.0.1')
        vz.sincronizar(self.cliente.id)
        with mock.patch('monitoramento.vinculo_zabbix._itens_do_host', return_value=itens):
            g = {o['interface'].split(' ')[0]: o for o in vz.itens_graficos(a)['opticos']}
        self.assertEqual((g['XGigabitEthernet0/0/2']['status'], g['XGigabitEthernet0/0/2']['status_inferido']), ('down', True))
        self.assertEqual(g['XGigabitEthernet0/0/5']['status'], '')
        self.assertEqual((g['XGigabitEthernet0/0/1']['status'], g['XGigabitEthernet0/0/1']['status_inferido']), ('up', False))


class SerrilhadoContadorTest(TestCase):
    """Zigue-zague de contador (pico + vale que se compensam) não pode virar
    queda falsa no gráfico; queda real tem que continuar aparecendo."""

    @staticmethod
    def _serie(vals):
        from monitoramento.services import _corrigir_serrilhado
        return [round(p['v'], 2) for p in _corrigir_serrilhado(
            [{'t': i * 180, 'v': v} for i, v in enumerate(vals)])]

    def test_pico_e_vale_viram_media(self):
        # Dados reais do NE_8000_JUINA (Gbps), coleta de 3 em 3 min
        vals = [19.19, 19.3, 20.42, 22.74, 14.53, 23.28, 14.48, 17.02, 16.61, 16.56]
        out = self._serie(vals)
        for v in out[3:7]:
            self.assertAlmostEqual(v, 18.7, delta=0.6)
        # volume (média) preservado
        self.assertAlmostEqual(sum(out) / len(out), sum(vals) / len(vals), delta=0.3)

    def test_pico_uma_coleta_depois(self):
        # LINK AVATO AFL: pico 6.17 compensado pelo vale 4.10 duas coletas depois
        out = self._serie([4.91, 4.85, 6.17, 4.67, 4.10, 4.91, 4.85, 4.88])
        self.assertTrue(all(4.6 <= v <= 5.2 for v in out), out)

    def test_queda_real_preservada(self):
        vals = [18, 18, 18, 0, 18, 18, 18, 18, 0, 0, 0, 18, 18, 18]
        self.assertEqual(self._serie(vals), vals)

    def test_ruido_pequeno_so_suaviza(self):
        out = self._serie([10.0, 10.3, 9.8, 10.2, 9.9, 10.1])
        self.assertTrue(all(9.8 <= v <= 10.3 for v in out), out)
