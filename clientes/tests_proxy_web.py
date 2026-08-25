"""Reescrita de HTML do proxy web (clientes/proxy_engine.py).

Cobre os dois pontos que quebravam Grafana/Zabbix abertos pelo proxy:
o sub-path do frontend do Grafana e URLs absolutas em porta não padrão.
"""
from django.test import SimpleTestCase

from clientes.proxy_engine import ProxyEngine

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
