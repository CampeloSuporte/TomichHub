"""
Ponta a ponta da tela de racks num Chrome headless real: abrir pela
topologia, criar rack, arrastar equipamento com o mouse até um U, ligar o
cabo a partir do enlace e ver o resultado no painel do link da topologia.

Usa o driver CDP de `projeto_rede.tests_navegador` (o Chrome deste servidor
não abre socket). Com RACK_SCREENSHOTS=<pasta> salva as telas em PNG.
"""
import base64
import json
import os
import unittest

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client

from clientes.models import Acesso, Cliente, TopologiaDiagrama
from projeto_rede.tests_navegador import CDP, CHROME
from usuario.models import PerfilUsuario, TOTPDevice

from .models import ConexaoFisica, Rack, RackEquipamento


@unittest.skipUnless(os.path.exists(CHROME), 'Google Chrome não instalado')
class RackNavegadorTest(StaticLiveServerTestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nome_empresa='Provedor Teste', cnpj='11.111.111/0001-11',
                                              endereco='Rua 1', email='prov@example.com')
        mk = lambda tipo, host: Acesso.objects.create(cliente=self.cliente, tipo=tipo, host=host, porta=22,
                                                      protocolo='SSH', usuario='u', senha='s')
        self.sw, self.rtr = mk('SW-AGG-01', '10.0.0.1'), mk('RTR-BORDA-01', '10.0.0.2')
        self.diagrama = TopologiaDiagrama.objects.create(cliente=self.cliente, dados_json=json.dumps({
            'nodes': [
                {'id': f'crm_{self.sw.id}', 'type': 'switch_l3', 'label': 'SW-AGG-01', 'acesso_id': self.sw.id,
                 'x': 200, 'y': 200, 'w': 64, 'h': 64, 'color': '#58a6ff'},
                {'id': f'crm_{self.rtr.id}', 'type': 'router', 'label': 'RTR-BORDA-01', 'acesso_id': self.rtr.id,
                 'x': 500, 'y': 200, 'w': 64, 'h': 64, 'color': '#00d9ff'},
            ],
            'links': [{'id': 'L1', 'src': f'crm_{self.sw.id}', 'tgt': f'crm_{self.rtr.id}', 'iface': '10g',
                       'iface_a': 'XGE0/0/1', 'iface_b': 'sfp-sfpplus1', 'label': 'P2P-CORE-BORDA',
                       'style': 'solid', 'shape': 'straight', 'waypoints': []}],
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
        pasta = os.environ.get('RACK_SCREENSHOTS')
        if pasta:
            png = self.b.cmd('Page.captureScreenshot', format='png')['data']
            with open(os.path.join(pasta, f'{nome}.png'), 'wb') as fh:
                fh.write(base64.b64decode(png))

    def centro(self, seletor):
        return self.b.js(f"""(() => {{ const r = document.querySelector({json.dumps(seletor)}).getBoundingClientRect();
            return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")

    def mouse(self, tipo, x, y):
        extra = {'button': 'left', 'clickCount': 1} if tipo != 'mouseMoved' else {'button': 'left', 'buttons': 1}
        self.b.cmd('Input.dispatchMouseEvent', type=tipo, x=x, y=y, **extra)

    def arrastar_para_u(self, seletor, u):
        """Arrasta com o mouse de verdade até a linha do U (topo do item nela)."""
        x0, y0 = self.centro(seletor)
        alvo = self.b.js(f"""(() => {{ const b = document.getElementById('baias').getBoundingClientRect();
            const n = rb._rack().altura_u; return [b.left + b.width / 2, b.top + (n - {u}) * rb.U + rb.U / 2]; }})()""")
        self.mouse('mousePressed', x0, y0)
        for i in range(1, 9):
            self.mouse('mouseMoved', x0 + (alvo[0] - x0) * i / 8, y0 + (alvo[1] - y0) * i / 8)
        self.assertEqual(self.b.js("getComputedStyle(document.getElementById('previa')).display"), 'flex')
        self.mouse('mouseReleased', *alvo)

    def test_rack_da_topologia_ate_o_cabo(self):
        b = self.b
        # Topologia -> botão Racks
        b.ir(f'{self.live_server_url}/clientes/{self.cliente.id}/topologia/editor/')
        b.esperar("window.topo && topo.links.length === 1")
        # O refresh de tipos reclassifica SW-AGG-01 (switch_l3 -> switch_l2) e
        # deixa o mapa "não salvo": o botão pergunta e salva antes de sair.
        # confirm() nativo travaria o Runtime.evaluate — responde "OK" aqui.
        b.esperar("topo.dirty")
        b.js("window.confirm = () => true")
        b.js("document.getElementById('btn-racks').click()")
        b.esperar("location.pathname.startsWith('/racks/') && window.rb && !!document.querySelector('.vazio')")
        self.assertIn(f'diagrama={self.diagrama.id}', b.js("document.getElementById('btn-voltar').href"))
        self.diagrama.refresh_from_db()
        self.assertIn('switch_l2', self.diagrama.dados_json)  # salvou antes de sair

        # Criar rack de 24U pelo diálogo
        b.js("rb.dialogoRack()")
        b.js("document.getElementById('rk-nome').value = 'RACK-POP-01'; document.getElementById('rk-alt').value = 24")
        b.js("document.querySelector('#dlg .prop-btn.primary').click()")
        b.esperar("!!document.getElementById('baias')")
        rack = Rack.objects.get()
        self.assertEqual((rack.nome, rack.altura_u), ('RACK-POP-01', 24))

        # Aba "Da topologia": arrastar o switch até o U20; clicar no roteador monta no mais alto livre
        b.js("rb.abaPaleta('dispositivos')")
        self.arrastar_para_u(f'.pal-item[data-disp="a{self.sw.id}"]', 20)
        b.esperar("rb.estado.racks[0].equipamentos.length === 1")
        sw = RackEquipamento.objects.get(acesso=self.sw)
        self.assertEqual((sw.u_inicial, sw.tipo), (20, 'switch'))
        b.js(f"document.querySelector('.pal-item[data-disp=\"a{self.rtr.id}\"]').dispatchEvent(new PointerEvent('pointerdown', {{button: 0, bubbles: true, clientX: 5, clientY: 5}}))")
        b.js("window.dispatchEvent(new PointerEvent('pointerup', {clientX: 5, clientY: 5}))")
        b.esperar("rb.estado.racks[0].equipamentos.length === 2")
        self.assertEqual(RackEquipamento.objects.get(acesso=self.rtr).u_inicial, 24)

        # Patch panel do catálogo arrastado para U10, e prévia vermelha em cima do switch
        b.js("rb.abaPaleta('catalogo')")
        self.arrastar_para_u('.pal-item[data-tipo="patch_panel"]', 10)
        b.esperar("rb.estado.racks[0].equipamentos.length === 3")
        x0, y0 = self.centro('.pal-item[data-tipo="switch"]')
        self.mouse('mousePressed', x0, y0)
        alvo = b.js("""(() => { const b = document.getElementById('baias').getBoundingClientRect();
            return [b.left + 100, b.top + (rb._rack().altura_u - 20) * rb.U + 5]; })()""")
        self.mouse('mouseMoved', x0 + 10, y0)
        self.mouse('mouseMoved', *alvo)
        self.assertIn('SW-AGG-01', b.js("document.getElementById('previa').textContent"))
        self.assertEqual(b.js("document.getElementById('previa').className"), 'erro')
        self.foto('1_rack_prevista_conflito')
        self.mouse('mouseReleased', *alvo)
        self.assertEqual(RackEquipamento.objects.count(), 3)

        # Conexões: o enlace está pronto; criar o cabo pelo diálogo pré-preenchido
        b.js("rb.abaLateral('conexoes')")
        self.assertEqual(b.js("document.querySelector('[data-link=\"L1\"] .pill').textContent"), 'pronta')
        b.js("document.querySelector('[data-link=\"L1\"] .prop-btn').click()")
        self.assertEqual(b.js("document.getElementById('cx-pa').value"), 'XGE0/0/1')
        self.assertEqual(b.js("document.getElementById('cx-meio').value"), 'fibra_sm')
        self.foto('2_dialogo_cabo')
        b.js("document.getElementById('cx-ok').click()")
        b.esperar("rb.estado.conexoes.length === 1")
        c = ConexaoFisica.objects.get()
        self.assertEqual((c.porta_a, c.porta_b, c.topologia_link_id, c.identificacao),
                         ('XGE0/0/1', 'sfp-sfpplus1', 'L1', 'P2P-CORE-BORDA'))
        b.js(f"rb.selecionar({sw.id})")
        self.foto('3_rack_com_cabo')

        # Voltar para a topologia: o painel do link mostra o cabo
        b.js("document.getElementById('btn-voltar').click()")
        b.esperar("window.topo && topo.links.length === 1")
        b.js("topo._select('link', 'L1')")
        b.esperar("!!document.querySelector('#pl-fisica .fisica-ok')")
        self.assertIn('XGE0/0/1', b.js("document.querySelector('#pl-fisica').textContent"))
        self.foto('4_topologia_painel_link')

        # Selo de rack nos hosts montados: clicar leva direto ao equipamento no rack
        b.esperar(f"!!document.querySelector('.node-rack-badge[data-node=\"crm_{self.sw.id}\"]')")
        self.assertEqual(b.js("document.querySelectorAll('.node-rack-badge').length"), 2)
        self.assertIn('RACK-POP-01 · U20', b.js(f"document.querySelector('.node-rack-badge[data-node=\"crm_{self.sw.id}\"] title').textContent"))
        b.js(f"topo._select('node', 'crm_{self.sw.id}')")
        self.assertIn('No rack: RACK-POP-01 · U20', b.js("document.getElementById('btn-rack').textContent"))
        self.foto('5_topologia_selo_rack')
        b.js("window.confirm = () => true")
        x, y = self.centro(f'.node-rack-badge[data-node="crm_{self.sw.id}"]')
        self.mouse('mousePressed', x, y)
        self.mouse('mouseReleased', x, y)
        b.esperar(f"location.pathname.startsWith('/racks/') && window.rb && rb.sel === {sw.id}")
        self.assertIn(f'equip={sw.id}', b.js("location.search"))
        self.assertTrue(b.js(f"document.querySelector('.eq[data-id=\"{sw.id}\"]').classList.contains('sel')"))
        self.foto('6_rack_vindo_do_selo')
        self.assertEqual(b.erros, [])
