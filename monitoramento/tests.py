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
