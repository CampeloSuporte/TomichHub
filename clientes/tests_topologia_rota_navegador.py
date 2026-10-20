"""
Editor de topologia num Chrome headless real: traçar uma conexão ponto a
ponto (soltar no vazio, clicar dobras, clicar no host de destino) e marcar o
enlace como transporte + botão "Transportes" da barra.

Usa o driver CDP de `projeto_rede.tests_navegador` (o Chrome deste servidor
não abre socket). Com TOPO_SCREENSHOTS=<pasta> salva as telas em PNG.
"""
import base64
import json
import os
import unittest

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client

from clientes.models import Cliente, TopologiaDiagrama
from projeto_rede.tests_navegador import CDP, CHROME
from usuario.models import PerfilUsuario, TOTPDevice


@unittest.skipUnless(os.path.exists(CHROME), 'Google Chrome não instalado')
class TopologiaRotaTransporteTest(StaticLiveServerTestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nome_empresa='Provedor Teste', cnpj='11.111.111/0001-11',
                                              endereco='Rua 1', email='prov@example.com')
        no = lambda i, tipo, x, y: {'id': i, 'type': tipo, 'label': i.upper(), 'x': x, 'y': y,
                                    'w': 64, 'h': 64, 'color': '#58a6ff'}
        self.diagrama = TopologiaDiagrama.objects.create(cliente=self.cliente, dados_json=json.dumps({
            'nodes': [no('ra', 'router', 200, 200), no('rb', 'router', 600, 400), no('sw', 'switch_l2', 200, 450)],
            'links': [{'id': 'L1', 'src': 'ra', 'tgt': 'sw', 'iface': '1g', 'style': 'solid',
                       'shape': 'straight', 'waypoints': []}],
        }))
        admin = User.objects.create_user('admin', password='x', is_staff=True)
        PerfilUsuario.objects.create(usuario=admin, role=PerfilUsuario.ROLE_ADMIN)
        TOTPDevice.objects.create(usuario=admin, secret='A' * 32, confirmado=True)
        c = Client()
        c.force_login(admin)
        self.b = CDP()
        self.addCleanup(self.b.fechar)
        self.b.servir(self.live_server_url)
        self.b.cookie_fixo = f'{settings.SESSION_COOKIE_NAME}={c.cookies[settings.SESSION_COOKIE_NAME].value}'

    def foto(self, nome):
        pasta = os.environ.get('TOPO_SCREENSHOTS')
        if pasta:
            png = self.b.cmd('Page.captureScreenshot', format='png')['data']
            with open(os.path.join(pasta, f'{nome}.png'), 'wb') as fh:
                fh.write(base64.b64decode(png))

    def tela(self, x, y):
        """Coordenada do mapa (SVG) → coordenada da janela."""
        return self.b.js(f"""(() => {{ const r = topo._svgRect();
            return [r.left + topo.panX + {x} * topo.zoom, r.top + topo.panY + {y} * topo.zoom]; }})()""")

    def mouse(self, tipo, x, y, apertado=False):
        extra = {'button': 'left', 'clickCount': 1} if tipo != 'mouseMoved' else \
            {'button': 'left' if apertado else 'none', 'buttons': 1 if apertado else 0}
        self.b.cmd('Input.dispatchMouseEvent', type=tipo, x=x, y=y, **extra)

    def clicar(self, x, y):
        self.mouse('mouseMoved', x, y)
        self.mouse('mousePressed', x, y)
        self.mouse('mouseReleased', x, y)

    def test_rota_ponto_a_ponto_e_transporte(self):
        b = self.b
        b.ir(f'{self.live_server_url}/clientes/{self.cliente.id}/topologia/editor/')
        b.esperar("window.topo && topo.links.length === 1")
        b.js("topo.snap = false")

        # Puxa da âncora leste de RA e solta no vazio: começa a traçar (antes descartava).
        ax, ay = self.tela(232, 200)
        vx, vy = self.tela(400, 200)
        self.mouse('mouseMoved', ax, ay)
        self.mouse('mousePressed', ax, ay)
        for i in range(1, 6):
            self.mouse('mouseMoved', ax + (vx - ax) * i / 5, ay + (vy - ay) * i / 5, apertado=True)
        self.mouse('mouseReleased', vx, vy)
        self.assertTrue(b.js("!!topo.rota && topo.rota.pts.length === 1"))
        self.assertIn('Traçando conexão', b.js("document.getElementById('st-mode').textContent"))

        # Duas dobras com clique; Backspace desfaz a última; mais uma dobra.
        self.clicar(*self.tela(400, 320))
        self.clicar(*self.tela(480, 320))
        self.assertEqual(b.js("topo.rota.pts.length"), 3)
        b.cmd('Input.dispatchKeyEvent', type='keyDown', key='Backspace', code='Backspace', windowsVirtualKeyCode=8)
        self.assertEqual(b.js("topo.rota.pts.length"), 2)
        self.clicar(*self.tela(400, 400))

        # Arrastar no vazio durante o traçado é pan, não dobra.
        px, py = self.tela(300, 600)
        self.mouse('mouseMoved', px, py)
        self.mouse('mousePressed', px, py)
        for i in range(1, 5):
            self.mouse('mouseMoved', px + 10 * i, py, apertado=True)
        self.mouse('mouseReleased', px + 40, py)
        self.assertEqual(b.js("topo.rota.pts.length"), 3)
        self.mouse('mouseMoved', *self.tela(560, 400))
        self.foto('1_tracando')

        # Clique no host RB conclui o enlace com o caminho traçado.
        self.clicar(*self.tela(600, 400))
        self.assertFalse(b.js("!!topo.rota"))
        self.assertEqual(b.js("topo.links.length"), 2)
        link = b.js("JSON.stringify(topo.links[1])")
        link = json.loads(link)
        self.assertEqual((link['src'], link['tgt']), ('ra', 'rb'))
        esperado = [(400, 200), (400, 320), (400, 400)]   # sub-pixel do clique não importa
        self.assertEqual(len(link['waypoints']), 3)
        for p, (x, y) in zip(link['waypoints'], esperado):
            self.assertLess(abs(p['x'] - x) + abs(p['y'] - y), 2)

        # Esc cancela um traçado sem criar nada.
        sx, sy = self.tela(200, 482)   # âncora sul de SW
        self.clicar(sx, sy)
        self.assertTrue(b.js("!!topo.rota"))
        b.cmd('Input.dispatchKeyEvent', type='keyDown', key='Escape', code='Escape', windowsVirtualKeyCode=27)
        self.assertFalse(b.js("!!topo.rota"))
        self.assertEqual(b.js("topo.links.length"), 2)

        # Sem transporte marcado, o botão só avisa.
        b.js("document.getElementById('btn-transportes').click()")
        self.assertFalse(b.js("topo.verTransportes"))

        # Marca o novo enlace como transporte pelo painel.
        lid = link['id']
        b.js(f"topo._select('link', '{lid}')")
        b.js("document.getElementById('pl-transp').checked = true")
        b.js(f"topo._applyLinkProps('{lid}')")
        self.assertTrue(b.js(f"topo.links.find(l => l.id === '{lid}').transporte"))
        self.assertEqual(b.js(f"document.querySelector('g[data-link=\"{lid}\"] .link').getAttribute('stroke')"),
                         '#ff2e97')

        # Mostrar transportes: só RA, RB e o enlace transporte ficam acesos.
        b.js("topo._deselect()")
        b.js("document.getElementById('btn-transportes').click()")
        self.assertTrue(b.js("topo.verTransportes"))
        op = lambda sel: float(b.js(f"getComputedStyle(document.querySelector('{sel}')).opacity"))
        self.assertEqual(op('.node[data-id="ra"]'), 1)
        self.assertEqual(op('.node[data-id="rb"]'), 1)
        self.assertLess(op('.node[data-id="sw"]'), 0.5)
        self.assertLess(op('g[data-link="L1"]'), 0.5)
        self.assertEqual(op(f'g[data-link="{lid}"]'), 1)
        self.foto('2_transportes')

        # Desmarcar o último transporte devolve o mapa normal.
        b.js(f"topo._select('link', '{lid}')")
        b.js("document.getElementById('pl-transp').checked = false")
        b.js(f"topo._applyLinkProps('{lid}')")
        self.assertFalse(b.js("topo.verTransportes"))
        self.assertEqual(op('.node[data-id="sw"]'), 1)

        # O campo vai para o dados_json salvo.
        b.js(f"topo.links.find(l => l.id === '{lid}').transporte = true; topo.save()")
        b.esperar("!topo.dirty")
        self.diagrama.refresh_from_db()
        salvo = {l['id']: l for l in json.loads(self.diagrama.dados_json)['links']}
        self.assertTrue(salvo[lid]['transporte'])
