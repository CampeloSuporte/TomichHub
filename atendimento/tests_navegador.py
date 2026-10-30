"""
"Chamar atenção" da Sala Virtual num Chrome headless real, nas duas telas.

O WebSocket é trocado por um falso (o consumer tem os testes dele em
tests.py): aqui o que se confere é o que a pessoa vê — menu, alerta, tremida,
som e o que o botão Escutar faz. Pulado quando não há Chrome instalado.
"""
import os
import time
import unittest

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client

from projeto_rede.tests_navegador import CDP, CHROME
from usuario.models import TOTPDevice

WS_FALSO = """
window.__enviadas = []; window.__osciladores = 0;
class WsFalso {
  static OPEN = 1; static CLOSED = 3; static CLOSING = 2; static CONNECTING = 0;
  constructor(url) {
    this.url = url; this.readyState = 1;
    if (url.includes('/sala/')) window.__sala = this;
    setTimeout(() => this.onopen && this.onopen({}), 0);
  }
  send(t) { if (this.url.includes('/sala/')) window.__enviadas.push(JSON.parse(t)); }
  close() { this.readyState = 3; }
}
window.WebSocket = WsFalso;
const CtxReal = window.AudioContext;
window.AudioContext = class extends CtxReal {
  createOscillator() { window.__osciladores++; return super.createOscillator(); }
};
window.__notifs = [];
window.Notification = class {
  static permission = 'granted';
  static requestPermission() { return Promise.resolve('granted'); }
  constructor(t, o) { this.fechada = false; window.__notifs.push(this); this.corpo = o.body; }
  close() { this.fechada = true; }
};
window.__msg = m => window.__sala.onmessage({data: JSON.stringify(m)});
"""


@unittest.skipUnless(os.path.exists(CHROME), 'Google Chrome não instalado')
class ChamarAtencaoNavegadorTest(StaticLiveServerTestCase):
    def setUp(self):
        admin = User.objects.create_superuser('chefe', password='x')
        TOTPDevice.objects.create(usuario=admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        c = Client()
        c.force_login(admin)
        self.b = CDP()
        self.addCleanup(self.b.fechar)
        self.b.servir(self.live_server_url)
        self.b.cookie_fixo = f'{settings.SESSION_COOKIE_NAME}={c.cookies[settings.SESSION_COOKIE_NAME].value}'
        self.b.cmd('Page.addScriptToEvaluateOnNewDocument', source=WS_FALSO)

    def _entrar_com_colega(self):
        b = self.b
        b.esperar('!!window.__sala && !!window.__sala.onmessage')
        b.js("""__msg({type: 'room_joined', my_id: 'eu', can_kick: false, peers: [
            {channel_name: 'bia', display_name: 'Bia <b>', audio: false, screen: false, private_with: null}]})""")

    def _conferir_alerta(self):
        b = self.b
        b.js("__msg({type: 'nudge', from: 'bia', display_name: 'Bia <b>'})")
        self.assertTrue(b.js("document.getElementById('chamada-alerta').classList.contains('open')"))
        # Nome vem do colega: entra como texto, nunca como HTML.
        self.assertEqual(b.js("document.querySelector('#chamada-alerta strong').textContent"), 'Bia <b>')
        self.assertTrue(b.js("document.documentElement.classList.contains('chamada-tremendo')"))
        self.assertEqual(b.js("getComputedStyle(document.body).animationName"), 'chamada-tremer')
        self.assertEqual(b.js('window.__osciladores'), 1)
        b.esperar("document.title.includes('chamando')", timeout=3)
        b.esperar("!document.documentElement.classList.contains('chamada-tremendo')", timeout=3)
        # Notifica mesmo com a aba visível: o navegador pode estar atrás de outro programa.
        self.assertEqual(b.js('__notifs.length'), 1)
        self.assertIn('Bia', b.js('__notifs[0].corpo'))
        # Continua tocando até a pessoa responder.
        b.esperar('window.__osciladores >= 2', timeout=5)

    def _escutar(self):
        self.b.js("document.querySelector('#chamada-alerta .ca-escutar').click()")
        self.assertFalse(self.b.js("document.getElementById('chamada-alerta').classList.contains('open')"))
        self.assertNotIn('chamando', self.b.js('document.title'))
        self.assertTrue(self.b.js('__notifs[0].fechada'))
        toques = self.b.js('window.__osciladores')
        time.sleep(3.3)
        self.assertEqual(self.b.js('window.__osciladores'), toques)

    def _enviadas(self, tipo):
        return self.b.js(f"__enviadas.filter(m => m.type === '{tipo}')")

    def test_pagina_cheia(self):
        b = self.b
        b.ir(self.live_server_url + '/atendimento/sala/')
        b.js("localStorage.setItem('sala_deafen', '1')")
        b.ir(self.live_server_url + '/atendimento/sala/')
        b.js("document.getElementById('btn-skip-mic').click()")
        self._entrar_com_colega()

        b.esperar("!!document.querySelector('.peer-card.clicavel')", timeout=5)
        b.js("document.querySelector('.peer-card.clicavel').click()")
        b.js("document.querySelector('#peer-menu [data-acao=nudge]').click()")
        self.assertEqual(self._enviadas('nudge'), [{'type': 'nudge', 'target': 'bia'}])

        self._conferir_alerta()
        self.assertIn('silenciada', b.js("document.querySelector('#chamada-alerta .ca-dica').textContent"))
        self._escutar()
        self.assertEqual(b.js("localStorage.getItem('sala_deafen')"), '0')

    def test_painel_do_menu_lateral(self):
        b = self.b
        b.ir(self.live_server_url + '/atendimento/')
        b.js('salaTogglePanel()')
        self._entrar_com_colega()
        b.js('salaPanelClose()')  # alerta tem de aparecer com o painel fechado

        b.esperar("!!document.getElementById('sala-peer-bia')", timeout=5)
        b.js("document.getElementById('sala-peer-bia').click()")
        b.js("document.querySelector('#sala-peer-menu [data-acao=nudge]').click()")
        self.assertEqual(self._enviadas('nudge'), [{'type': 'nudge', 'target': 'bia'}])

        # Em privado com outra pessoa: Escutar desfaz o par para poder ouvir.
        b.js("""__msg({type: 'peer_joined', peer: {channel_name: 'caio', display_name: 'Caio', audio: false, screen: false, private_with: null}});
                __msg({type: 'private_state', a: 'eu', b: 'caio', active: true, by: 'Caio'})""")
        self._conferir_alerta()
        self.assertIn('privada', b.js("document.querySelector('#chamada-alerta .ca-dica').textContent"))
        self._escutar()
        self.assertEqual(self._enviadas('private_end'), [{'type': 'private_end'}])
        self.assertTrue(b.js("document.getElementById('sala-panel-body').classList.contains('open')"))

    def test_chamada_aparece_nas_outras_abas(self):
        b = self.b
        b.ir(self.live_server_url + '/atendimento/')  # esta aba nem entrou na sala
        b.js("window.__outra = new BroadcastChannel('sala-chamada')")
        b.js("__outra.postMessage({acao: 'chamada', display_name: 'Bia', from: 'bia'})")
        b.esperar("document.getElementById('chamada-alerta')?.classList.contains('open')", timeout=3)
        self.assertEqual(b.js('__notifs.length'), 0)  # quem notifica é a aba da sala
        b.js("__outra.postMessage({acao: 'fechar'})")
        b.esperar("!document.getElementById('chamada-alerta').classList.contains('open')", timeout=3)

        # Escutar sem estar na sala: entra por aqui e avisa as outras abas.
        b.js("__outra.postMessage({acao: 'chamada', display_name: 'Bia', from: 'bia'})")
        b.esperar("document.getElementById('chamada-alerta').classList.contains('open')", timeout=3)
        b.js("window.__fechou = false; __outra.onmessage = e => { if (e.data.acao === 'fechar') window.__fechou = true; }")
        b.js("document.querySelector('#chamada-alerta .ca-escutar').click()")
        b.esperar('!!window.__sala', timeout=3)
        b.esperar('window.__fechou', timeout=3)
