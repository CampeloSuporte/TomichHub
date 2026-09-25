"""
Editor de topologia num Chrome headless real: sem botões Salvar/Importar
Hosts — host novo do CRM entra sozinho no mapa raiz e é gravado pelo
auto-save; host removido à mão não volta e fica na paleta para devolver.

Usa o driver CDP de `projeto_rede.tests_navegador` (o Chrome deste servidor
não abre socket).
"""
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


@unittest.skipUnless(os.path.exists(CHROME), 'Google Chrome não instalado')
class TopologiaHostsAutomaticosTest(StaticLiveServerTestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nome_empresa='Provedor Teste', cnpj='11.111.111/0001-11',
                                              endereco='Rua 1', email='prov@example.com')
        self.rtr = self.acesso('RTR-BORDA-01', '10.0.0.2')
        self.diagrama = TopologiaDiagrama.objects.create(cliente=self.cliente, dados_json=json.dumps({
            'nodes': [{'id': f'crm_{self.rtr.id}', 'type': 'router', 'label': 'RTR-BORDA-01',
                       'acesso_id': self.rtr.id, 'x': 500, 'y': 200, 'w': 64, 'h': 64, 'color': '#00d9ff'}],
            'links': [],
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

    def acesso(self, tipo, host):
        return Acesso.objects.create(cliente=self.cliente, tipo=tipo, host=host, porta=22,
                                     protocolo='SSH', usuario='u', senha='s')

    def salvo(self):
        self.b.esperar("document.getElementById('st-save').textContent.includes('Salvo') && !topo.dirty")
        self.diagrama.refresh_from_db()
        return json.loads(self.diagrama.dados_json)

    def test_host_novo_entra_sozinho_e_removido_nao_volta(self):
        b = self.b
        # Host cadastrado antes de abrir: entra na carga inicial.
        sw = self.acesso('SW-AGG-01', '10.0.0.1')
        b.ir(f'{self.live_server_url}/clientes/{self.cliente.id}/topologia/editor/')
        b.esperar("window.topo && topo.nodes.length >= 2")
        self.assertFalse(b.js("!!document.getElementById('btn-salvar')"))
        self.assertFalse(b.js("[...document.querySelectorAll('.tb-btn')].some(x => x.textContent.includes('Importar'))"))
        dados = self.salvo()
        self.assertIn(f'crm_{sw.id}', [n['id'] for n in dados['nodes']])

        # Host cadastrado com o editor aberto: entra na próxima sincronização.
        olt = self.acesso('OLT-CENTRO', '10.0.0.3')
        b.js("topo._sincronizarHosts()")
        b.esperar(f"!!topo.nodes.find(n => n.id === 'crm_{olt.id}')")
        dados = self.salvo()
        self.assertIn(f'crm_{olt.id}', [n['id'] for n in dados['nodes']])

        # Removido à mão: grava em hosts_removidos, não volta, aparece na paleta.
        b.js(f"topo._select('node', 'crm_{olt.id}'); topo._deleteSelected()")
        dados = self.salvo()
        self.assertEqual(dados['hosts_removidos'], [olt.id])
        b.js("topo._sincronizarHosts()")
        b.esperar("!topo._syncHostsEmVoo && !!document.querySelector('.pal-host')")
        self.assertFalse(b.js(f"!!topo.nodes.find(n => n.id === 'crm_{olt.id}')"))

        # Clique na paleta devolve ao mapa e tira da lista.
        b.js("document.querySelector('.pal-host').click()")
        dados = self.salvo()
        self.assertIn(f'crm_{olt.id}', [n['id'] for n in dados['nodes']])
        self.assertNotIn('hosts_removidos', dados)
        self.assertFalse(b.js("!!document.querySelector('.pal-host')"))

    def test_submapa_nao_importa_e_host_de_submapa_nao_duplica_no_raiz(self):
        b = self.b
        sw = self.acesso('SW-AGG-01', '10.0.0.1')
        sub = TopologiaDiagrama.objects.create(cliente=self.cliente, pai=self.diagrama, nome='POP', dados_json=json.dumps({
            'nodes': [{'id': f'crm_{sw.id}', 'type': 'switch_l3', 'label': 'SW-AGG-01', 'acesso_id': sw.id,
                       'x': 100, 'y': 100, 'w': 64, 'h': 64, 'color': '#58a6ff'}],
            'links': [],
        }))
        novo = self.acesso('RTR-NOVO', '10.0.0.9')
        # Sub-mapa: não recebe o host novo.
        b.ir(f'{self.live_server_url}/clientes/{self.cliente.id}/topologia/editor/?diagrama={sub.id}')
        b.esperar("window.topo && topo._ultimoSyncHosts && !topo._syncHostsEmVoo")
        self.assertEqual(b.js("topo.nodes.length"), 1)
        # Raiz: recebe o novo, mas não o que já está desenhado no sub-mapa.
        b.ir(f'{self.live_server_url}/clientes/{self.cliente.id}/topologia/editor/')
        b.esperar(f"window.topo && !!topo.nodes.find(n => n.id === 'crm_{novo.id}')")
        self.assertFalse(b.js(f"!!topo.nodes.find(n => n.id === 'crm_{sw.id}')"))
