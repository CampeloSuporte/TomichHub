"""Reescrita de HTML do proxy web (clientes/proxy_engine.py).

Cobre os dois pontos que quebravam Grafana/Zabbix abertos pelo proxy:
o sub-path do frontend do Grafana e URLs absolutas em porta não padrão.
"""
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from clientes.models import Acesso, Cliente
from clientes.proxy_engine import ProxyEngine
from usuario.models import TOTPDevice

BASE = '/clientes/acessos/1301/web/3000/http'


class RewriteGrafanaBootDataTest(SimpleTestCase):
    def _rewrite(self, html: str, base: str = BASE, host: str = '198.18.1.13',
                 porta: int = 3000) -> str:
        engine = ProxyEngine(None)
        return engine.rewrite_content(
            html.encode(), 'text/html', base, host, target_port=porta
        ).decode()

    def test_app_sub_url_vazio_recebe_o_caminho_do_proxy(self):
        # Grafana na raiz manda appSubUrl:"" — o router da SPA então não
        # reconhece o caminho do proxy e mostra o próprio "Page not found".
        html = '<html><head></head><body><script>window.grafanaBootData={settings:{"appSubUrl":"","appUrl":"http://localhost:3000/"}}</script></body></html>'
        out = self._rewrite(html)
        self.assertIn(f'"appSubUrl":"{BASE}"', out)

    def test_app_sub_url_ja_preenchido_e_substituido(self):
        # Grafana atrás de sub-path próprio (serve_from_sub_path) manda o
        # sub-path dele; quem vale dentro do proxy é o caminho do proxy.
        html = '<script>window.grafanaBootData={settings:{"appSubUrl":"/grafana"}}</script>'
        self.assertIn(f'"appSubUrl":"{BASE}"', self._rewrite(html))

    def test_pagina_sem_grafana_nao_e_tocada(self):
        html = '<html><head></head><body>{"appSubUrl":""}</body></html>'
        self.assertIn('{"appSubUrl":""}', self._rewrite(html))


class RewriteUrlAbsolutaComPortaTest(SimpleTestCase):
    def _rewrite(self, html: str, porta=None) -> str:
        engine = ProxyEngine(None)
        return engine.rewrite_content(
            html.encode(), 'text/html', BASE, '198.18.1.13', target_port=porta
        ).decode()

    def test_url_com_a_porta_real_vira_caminho_do_proxy(self):
        html = '<a href="http://198.18.1.13:3000/d/abc">painel</a>'
        self.assertIn(f'href="{BASE}/d/abc"', self._rewrite(html, porta=3000))

    def test_porta_deduzida_do_proxy_base_quando_nao_informada(self):
        # Sem target_port a porta sai do próprio proxy_base — antes dessa
        # dedução a porta ficava órfã no caminho (".../http:3000/d/abc").
        html = '<a href="http://198.18.1.13:3000/d/abc">painel</a>'
        out = self._rewrite(html)
        self.assertIn(f'href="{BASE}/d/abc"', out)
        self.assertNotIn('http:3000', out)

    def test_link_para_outra_porta_do_device_vai_pro_proxy_daquela_porta(self):
        # Zabbix linkando o Grafana do mesmo host, por exemplo: o link tem que
        # continuar dentro do proxy (o navegador do operador não alcança o IP
        # privado), só que na base da outra porta.
        html = '<a href="https://198.18.1.13:8006/console">proxmox</a>'
        self.assertIn('href="/clientes/acessos/1301/web/8006/https/console"',
                      self._rewrite(html, porta=3000))

    def test_url_sem_porta_explicita_fica_na_porta_ja_proxyada(self):
        # Firmware que imprime a própria URL canônica sem porta ("http://ip/")
        # mesmo servindo numa porta alta: seguir para a 80 quebraria o acesso,
        # então a URL sem porta continua caindo na porta que está funcionando.
        html = '<a href="http://198.18.1.13/c">c</a>'
        self.assertIn(f'href="{BASE}/c"', self._rewrite(html, porta=3000))

    def test_porta_explicita_diferente_vira_base_daquela_porta(self):
        html = ('<a href="http://198.18.1.13:80/a">a</a>'
                '<a href="https://198.18.1.13:443/b">b</a>')
        out = self._rewrite(html, porta=3000)
        self.assertIn('href="/clientes/acessos/1301/web/80/http/a"', out)
        self.assertIn('href="/clientes/acessos/1301/web/443/https/b"', out)


class RewriteReactRouterBasenameTest(SimpleTestCase):
    # Trecho real do bundle do TOMICH OBSERVER (acesso 1116): o componente
    # Router do react-router 6 minificado, e o BrowserRouter que o chama sem
    # basename.
    ROUTER = (b'function EG(e){let{basename:t="/",children:a=null,location:n,'
              b'navigationType:r=hl.Pop,navigator:o,static:i=!1,future:l}=e;')
    BROWSER_ROUTER = b'function DG(e){let{basename:t,children:a,future:n,window:r}=e,o=x.useRef();'

    def _js(self, js: bytes) -> bytes:
        return ProxyEngine(None).rewrite_content(js, 'application/javascript', BASE, '10.0.0.1')

    def test_default_do_basename_passa_pelo_helper_do_proxy(self):
        out = self._js(self.ROUTER)
        self.assertIn(
            b'basename:t=(window.__crmRouterBase?window.__crmRouterBase(e.location):"/"),children:a=null',
            out,
        )

    def test_basename_sem_default_nao_e_tocado(self):
        # BrowserRouter/HashRouter repassam o prop cru; o default que importa
        # é o do Router, que recebe undefined e aplica o dele.
        self.assertEqual(self._js(self.BROWSER_ROUTER), self.BROWSER_ROUTER)

    def test_js_sem_react_router_volta_identico(self):
        js = b'const a={basename:"x"};function f(){return "/"}'
        self.assertEqual(self._js(js), js)

    def test_html_injetado_define_o_helper(self):
        html = ProxyEngine(None).rewrite_content(
            b'<html><head></head><body></body></html>', 'text/html', BASE, '10.0.0.1'
        ).decode()
        self.assertIn('window.__crmRouterBase=function(l)', html)


class VersionarAssetsTest(SimpleTestCase):
    """Bundle antigo em cache (max-age de 30 dias do device) segurava o bug do
    basename mesmo depois do deploy — o F5 comum nem pedia o JS."""

    def _html(self, html: str) -> str:
        return ProxyEngine(None).rewrite_content(html.encode(), 'text/html', BASE, '10.0.0.1').decode()

    def test_script_de_modulo_e_modulepreload_ganham_versao_e_import_map(self):
        out = self._html(
            '<html><head>'
            '<script type="module" crossorigin src="/assets/index-A.js"></script>'
            '<link rel="modulepreload" crossorigin href="/assets/vendor-B.js">'
            '</head><body></body></html>'
        )
        v = ProxyEngine.VERSAO_ASSETS
        self.assertIn(f'src="{BASE}/assets/index-A.js?crmv={v}"', out)
        self.assertIn(f'href="{BASE}/assets/vendor-B.js?crmv={v}"', out)
        # chunk que importa "./index-A.js" cai na mesma URL versionada: uma
        # instância só do módulo
        self.assertIn(f'"{BASE}/assets/index-A.js": "{BASE}/assets/index-A.js?crmv={v}"', out)
        self.assertIn(f'"{BASE}/assets/vendor-B.js": "{BASE}/assets/vendor-B.js?crmv={v}"', out)
        # import map tem que vir antes do primeiro módulo
        self.assertLess(out.index('type="importmap"'), out.index('rel="modulepreload"'))
        self.assertLess(out.index('type="importmap"'), out.index('type="module"'))

    def test_script_classico_ganha_versao_sem_import_map(self):
        out = self._html('<head><script src="/static/js/main.1.js?x=1"></script></head>')
        self.assertIn(f'src="{BASE}/static/js/main.1.js?x=1&crmv={ProxyEngine.VERSAO_ASSETS}"', out)
        self.assertNotIn('importmap', out)

    def test_pagina_com_import_map_proprio_nao_e_tocada(self):
        out = self._html('<head><script type="importmap">{"imports":{}}</script>'
                         '<script type="module" src="/a.js"></script></head>')
        self.assertIn(f'src="{BASE}/a.js"', out)
        self.assertNotIn('crmv=', out)

    def test_script_externo_e_css_nao_sao_tocados(self):
        out = self._html('<head><script src="https://cdn.example.com/x.js"></script>'
                         '<link rel="stylesheet" href="/a.css"></head>')
        self.assertIn('src="https://cdn.example.com/x.js"', out)
        self.assertIn(f'href="{BASE}/a.css"', out)
        self.assertNotIn('crmv=', out)


class ProxyWebCacheJsTest(TestCase):

    def setUp(self):
        cliente = Cliente.objects.create(
            nome_empresa='Cliente Observer', cnpj='55.555.555/0001-55',
            endereco='Rua O, 1', email='observer@example.com',
        )
        self.acesso = Acesso.objects.create(
            cliente=cliente, tipo='OBSERVER', host='45.228.195.10',
            porta=80, protocolo='HTTP', usuario='u', senha='s',
        )
        admin = User.objects.create_user('admin_obs', password='x', is_staff=True, is_superuser=True)
        TOTPDevice.objects.create(usuario=admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(admin)
        self.url = f'/clientes/acessos/{self.acesso.id}/web/80/http/'

    def _resp(self, content_type, content, headers=None):
        return SimpleNamespace(
            status_code=200, content=content, cookies_raw=[],
            headers={'Content-Type': content_type, **(headers or {})},
        )

    @mock.patch('clientes.views.ProxyEngine.do_request')
    def test_html_do_proxy_sai_com_script_versionado(self, do_request):
        do_request.return_value = self._resp(
            'text/html', b'<html><head><script type="module" src="/assets/index-A.js"></script></head></html>')
        r = self.client.get(self.url)
        self.assertIn(f'/assets/index-A.js?crmv={ProxyEngine.VERSAO_ASSETS}', r.content.decode())

    @mock.patch('clientes.views.ProxyEngine.do_request')
    def test_js_reescrito_sai_sem_cache_longo(self, do_request):
        do_request.return_value = self._resp(
            'application/javascript', RewriteReactRouterBasenameTest.ROUTER,
            {'Cache-Control': 'public, max-age=2592000', 'ETag': '"abc"',
             'Last-Modified': 'Mon, 14 Sep 2026 23:59:06 GMT'},
        )
        r = self.client.get(self.url + 'assets/index-X.js')
        self.assertIn(b'window.__crmRouterBase', r.content)
        self.assertEqual(r['Cache-Control'], 'no-cache')
        self.assertNotIn('ETag', r)
        self.assertNotIn('Last-Modified', r)

    @mock.patch('clientes.views.ProxyEngine.do_request')
    def test_js_sem_react_router_mantem_cache_do_device(self, do_request):
        do_request.return_value = self._resp(
            'application/javascript', b'console.log(1)',
            {'Cache-Control': 'public, max-age=2592000', 'ETag': '"abc"'},
        )
        r = self.client.get(self.url + 'assets/outro.js')
        self.assertEqual(r['Cache-Control'], 'public, max-age=2592000')
        self.assertEqual(r['ETag'], '"abc"')
