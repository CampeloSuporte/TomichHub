"""
Aba Monitoramento num Chrome headless real (driver CDP de projeto_rede):
busca de interface no modal, Upload preenchido sozinho ao escolher o
Download e o gráfico carregando Download + Upload numa requisição só.
O Zabbix é simulado (services._rpc) no próprio processo do live server.
"""
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.http import HttpResponse
from django.template import engines
from django.test import Client, override_settings
from django.urls import include, path

from clientes.models import Cliente
from monitoramento.models import MonitorDashConfig, ZabbixConfig
from projeto_rede.tests_navegador import CDP, CHROME
from usuario.models import PerfilUsuario, TOTPDevice

_CHART_JS = next((p for p in (
    os.path.join(str(settings.STATIC_ROOT), 'js', 'chart.umd.min.js'),
    '/opt/crm/static/js/chart.umd.min.js',
) if os.path.isfile(p)), None)


def _pagina(request, cliente_id):
    chart = ''
    if _CHART_JS:
        with open(_CHART_JS, encoding='utf-8') as fh:
            chart = fh.read()
    tpl = engines['django'].from_string(
        "<!doctype html><html><head><meta charset='utf-8'><script>" + chart.replace('</script', '<\\/script') +
        "</script><script>window.uiAlert = m => { window._alerta = m; };</script></head><body>"
        "<div class='main-content'>{% include 'monitoramento/tab_monitoramento.html' %}</div>"
        "<script>window.GRAPH.init();</script></body></html>")
    return HttpResponse(tpl.render({'cliente': Cliente.objects.get(id=cliente_id)}, request))


urlpatterns = [
    path('__teste_monitoramento/<int:cliente_id>/', _pagina),
    path('', include(settings.ROOT_URLCONF)),
]

ITENS = [
    {'itemid': '1', 'name': 'Interface 100GE0/0/1(UPLINK-OPERADORA): Bits received', 'key_': 'net.if.in[ifHCInOctets.5]',
     'units': 'bps', 'lastvalue': '1500000000', 'value_type': '3'},
    {'itemid': '2', 'name': 'Interface 100GE0/0/2(CDN): Bits received', 'key_': 'net.if.in[ifHCInOctets.6]',
     'units': 'bps', 'lastvalue': '800000000', 'value_type': '3'},
    {'itemid': '3', 'name': 'Interface 100GE0/0/2(CDN): Bits sent', 'key_': 'net.if.out[ifHCOutOctets.6]',
     'units': 'bps', 'lastvalue': '90000000', 'value_type': '3'},
    {'itemid': '4', 'name': 'Interface 100GE0/0/1(UPLINK-OPERADORA): Bits sent', 'key_': 'net.if.out[ifHCOutOctets.5]',
     'units': 'bps', 'lastvalue': '300000000', 'value_type': '3'},
    {'itemid': '9', 'name': 'CPU utilization', 'key_': 'system.cpu.util', 'units': '%', 'lastvalue': '7', 'value_type': '0'},
]


@unittest.skipUnless(os.path.exists(CHROME), 'Google Chrome não instalado')
@override_settings(ROOT_URLCONF='monitoramento.tests_navegador')
class MonitoramentoNavegadorTest(StaticLiveServerTestCase):
    def setUp(self):
        cache.clear()
        self.cliente = Cliente.objects.create(nome_empresa='Provedor Graf', cnpj='44.444.444/0001-44',
                                              endereco='Rua 4', email='graf@example.com')
        ZabbixConfig.objects.create(cliente=self.cliente, url='http://10.9.9.9/zabbix', ativo=True)
        admin = User.objects.create_user('admin_graf', password='x', is_staff=True)
        PerfilUsuario.objects.create(usuario=admin, role=PerfilUsuario.ROLE_ADMIN)
        TOTPDevice.objects.create(usuario=admin, secret='A' * 32, confirmado=True)
        c = Client()
        c.force_login(admin)

        self.historicos = []

        def rpc(url, method, params, auth=None, config=None):
            if method == 'item.get' and 'hostids' in params:
                return [dict(i) for i in ITENS]
            if method == 'item.get':
                return [{'itemid': i, 'value_type': '3', 'units': 'bps'} for i in params['itemids']]
            if method == 'history.get':
                self.historicos.append(list(params['itemids']))
                agora = params['time_till']
                return [{'itemid': iid, 'clock': str(agora - k * 60), 'value': str(int(iid) * 1e8 + k)}
                        for iid in params['itemids'] for k in range(30)]
            return []

        cfg = SimpleNamespace(id='nav', url='http://zbx.teste', api_token='t', usuario='', senha='', ativo=True)
        for alvo, kw in (('monitoramento.services._rpc', {'side_effect': rpc}),
                         ('monitoramento.views._get_config_com_tunel', {'return_value': (cfg, None)})):
            p = mock.patch(alvo, **kw)
            p.start()
            self.addCleanup(p.stop)

        self.b = CDP()
        self.addCleanup(self.b.fechar)
        self.b.servir(self.live_server_url)
        self.b.cookie_fixo = f'{settings.SESSION_COOKIE_NAME}={c.cookies[settings.SESSION_COOKIE_NAME].value}'

    def _abrir_modal_com_host(self):
        b = self.b
        b.ir(f'{self.live_server_url}/__teste_monitoramento/{self.cliente.id}/')
        b.esperar('!!(window.GRAPH && document.getElementById("grph-grid"))')
        b.js('GRAPH.abrirModalAdd(); GRAPH.selecionarHost("500", "BORDA-01")')
        b.esperar('document.querySelectorAll("#grph-m-iface-list .grph-ri[data-idx]").length === 2')

    def test_busca_de_interface_preenche_download_e_upload(self):
        b = self._abrir_modal_com_host()
        b = self.b
        # nenhuma interface escolhida sozinha: o host tem duas
        self.assertEqual(b.js('document.getElementById("grph-m-item-in-id").value'), '')
        # busca sem acento/caixa e com palavras fora de ordem
        b.js('const i = document.getElementById("grph-m-iface-search"); i.value = "operadora 0/0/1"; grphFiltrarIfaces(i.value)')
        linhas = b.js('[...document.querySelectorAll("#grph-m-iface-list .grph-ri[data-idx]")].map(e => e.innerText)')
        self.assertEqual(len(linhas), 1, linhas)
        self.assertIn('100GE0/0/1(UPLINK-OPERADORA)', linhas[0])
        self.assertIn('1.50 Gbps', linhas[0])
        # Enter escolhe a interface realçada
        b.js('grphTeclaLista(new KeyboardEvent("keydown", {key: "Enter"}), "iface")')
        self.assertEqual(b.js('[document.getElementById("grph-m-item-in-id").value, document.getElementById("grph-m-item-out-id").value]'),
                         ['1', '4'])

    def test_escolher_bits_received_preenche_bits_sent(self):
        self._abrir_modal_com_host()
        b = self.b
        b.js('const i = document.getElementById("grph-m-item-in-search"); i.value = "cdn received"; grphFiltrarItens("in", i.value)')
        b.esperar('document.querySelectorAll("#grph-m-item-in-list .grph-ri[data-idx]").length >= 1')
        primeiro = b.js('document.querySelector("#grph-m-item-in-list .grph-ri[data-idx]").innerText')
        self.assertIn('100GE0/0/2(CDN): Bits received', primeiro)
        b.js('document.querySelector("#grph-m-item-in-list .grph-ri[data-idx]").click()')
        self.assertEqual(b.js('[document.getElementById("grph-m-item-in-id").value, document.getElementById("grph-m-item-out-id").value]'),
                         ['2', '3'])
        self.assertIn('Bits sent', b.js('document.getElementById("grph-m-item-out-badge").textContent'))
        # e o contrário: escolher o sent preenche o received
        b.js('GRAPH.selecionarHost("500", "BORDA-01")')
        b.esperar('document.querySelectorAll("#grph-m-iface-list .grph-ri[data-idx]").length === 2')
        b.js('const o = document.getElementById("grph-m-item-out-search"); o.value = "uplink"; grphFiltrarItens("out", o.value)')
        b.js('document.querySelector("#grph-m-item-out-list .grph-ri[data-idx]").click()')
        self.assertEqual(b.js('[document.getElementById("grph-m-item-in-id").value, document.getElementById("grph-m-item-out-id").value]'),
                         ['1', '4'])

    @unittest.skipUnless(_CHART_JS, 'chart.umd.min.js não encontrado')
    def test_grafico_busca_os_dois_sentidos_numa_requisicao(self):
        MonitorDashConfig.objects.create(cliente=self.cliente, dados={'tabs': [{'id': 1, 'nome': 'Geral', 'charts': [
            {'id': 1, 'hostid': '500', 'hostname': 'BORDA-01', 'titulo': '', 'ifaceName': 'x',
             'inId': '1', 'outId': '4', 'hours': 24}]}]})
        b = self.b
        b.ir(f'{self.live_server_url}/__teste_monitoramento/{self.cliente.id}/')
        b.esperar('(document.getElementById("grph-ts-1")||{}).textContent?.startsWith("Atualizado")')
        self.assertEqual(self.historicos, [['1', '4']])
        self.assertTrue(b.js('document.getElementById("grph-in-1").textContent.includes("Mbps")'))
        # reabrir a página: desenha na hora com o que ficou na sessão do navegador
        b.ir(f'{self.live_server_url}/__teste_monitoramento/{self.cliente.id}/')
        b.esperar('!!(window.GRAPH && document.getElementById("grph-in-1"))')
        self.assertNotEqual(b.js('document.getElementById("grph-in-1").textContent'), '—')
        self.assertEqual(b.erros, [])
