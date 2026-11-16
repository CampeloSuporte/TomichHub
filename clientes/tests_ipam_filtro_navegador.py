"""Filtro por faixa na aba Prefixos & Sub-redes, num Chrome headless real."""
import os
import unittest

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client

from clientes.models import IPAMPrefixo, IPAMSubRede
from projeto_rede.tests import _BaseViews
from projeto_rede.tests_navegador import CDP, CHROME


@unittest.skipUnless(os.path.exists(CHROME), 'sem Chrome')
class FiltroPrefixoNavegadorTest(StaticLiveServerTestCase):
    def setUp(self):
        _BaseViews.setUp(self)
        c = Client()
        c.force_login(self.admin)
        self.b = CDP()
        self.addCleanup(self.b.fechar)
        self.b.servir(self.live_server_url)
        self.b.cookie_fixo = f'{settings.SESSION_COOKIE_NAME}={c.cookies[settings.SESSION_COOKIE_NAME].value}'

    def linhas(self):
        return self.b.js("[...document.querySelectorAll('#ipam-prefixos-tbody .ipam-row-prefixo')]"
                         ".filter(r => r.style.display !== 'none').map(r => r.querySelector('.ipam-pfx-addr').textContent)")

    def test_filtro(self):
        b = self.b
        # A página semeia os prefixos RFC padrão (10/8, 100.64/10, 172.16/12,
        # 192.168/16, 198.18/15, fc00::/7) na 1ª visita; os filhos entram depois.
        b.ir(f'{self.live_server_url}/clientes/listar/?id={self.cliente.id}')
        cl = self.cliente
        self.p10 = IPAMPrefixo.objects.get(cliente=cl, prefixo='10.0.0.0/8')
        self.p10b = IPAMPrefixo.objects.create(cliente=cl, prefixo='10.1.0.0/16', pai=self.p10)
        # dentro da faixa pelo CIDR, mas sem pai gravado
        IPAMPrefixo.objects.create(cliente=cl, prefixo='10.2.0.0/16')
        IPAMSubRede.objects.create(cliente=cl, prefixo=self.p10, rede='10.0.0.0/24')
        total = IPAMPrefixo.objects.filter(cliente=cl).count()
        b.js("[...document.querySelectorAll('a.nav-link')].find(a => a.textContent.includes('Documentação de Rede')).click()")
        b.js("IPAM.subredes = []; ipamCarregarPrefixos()")
        b.esperar(f"document.querySelectorAll('#ipam-prefixos-tbody .ipam-row-prefixo').length === {total}")
        # painel: "Todas" + um item por prefixo; busca pelo teclado e Enter filtra
        b.js("ipamPfAlternar()")
        self.assertEqual(b.js("document.querySelectorAll('#ipam-pf-lista .ipam-pf-item').length"), total + 1)
        self.assertEqual(b.js("document.activeElement.id"), 'ipam-pf-busca')
        b.js("(i => { i.value = '10.0.0.0'; i.dispatchEvent(new Event('input')); })(document.getElementById('ipam-pf-busca'))")
        self.assertEqual(b.js("[...document.querySelectorAll('#ipam-pf-lista .ipam-pf-item')].map(e => e.dataset.id)"),
                         [str(self.p10.id)])
        b.js("ipamPfTecla(new KeyboardEvent('keydown', {key: 'Enter'}))")
        self.assertTrue(b.js("document.getElementById('ipam-pf-pop').hidden"))
        b.esperar("document.querySelectorAll('#ipam-prefixos-tbody .ipam-row-prefixo').length === 3")
        self.assertEqual(sorted(self.linhas()), ['10.0.0.0/8', '10.1.0.0/16', '10.2.0.0/16'])
        self.assertIn('10.0.0.0/8', b.js("document.getElementById('ipam-pf-label').textContent"))
        self.assertFalse(b.js("document.getElementById('ipam-pf-limpar').hidden"))
        # pasta da faixa escolhida já abre
        self.assertEqual(b.js("document.querySelector('.ipam-row-subrede').style.display"), '')

        # filho escolhido sozinho aparece mesmo com o pai fora da tela
        b.js(f"ipamFiltrarPorPrefixo('{self.p10b.id}')")
        b.esperar("document.querySelectorAll('#ipam-prefixos-tbody .ipam-row-prefixo').length === 1")
        self.assertEqual(self.linhas(), ['10.1.0.0/16'])

        # lembra a escolha ao recarregar
        b.ir(f'{self.live_server_url}/clientes/listar/?id={self.cliente.id}')
        b.js("ipamCarregarPrefixos()")
        b.esperar("document.querySelectorAll('#ipam-prefixos-tbody .ipam-row-prefixo').length === 1")
        self.assertIn('10.1.0.0/16', b.js("document.getElementById('ipam-pf-label').textContent"))

        # × limpa o filtro
        b.js("document.getElementById('ipam-pf-limpar').click()")
        b.esperar(f"document.querySelectorAll('#ipam-prefixos-tbody .ipam-row-prefixo').length === {total}")
        self.assertEqual(b.js("document.getElementById('ipam-pf-label').textContent"), 'Todas as faixas')
        self.assertEqual(b.erros, [])

    def visivel(self, row_id):
        return self.b.js(f"(r => !!r && r.style.display !== 'none' && r.classList.contains('ipam-row-recente'))"
                         f"(document.getElementById('{row_id}'))")

    def test_abre_o_mais_recente(self):
        from datetime import timedelta
        from django.utils import timezone
        b = self.b
        b.ir(f'{self.live_server_url}/clientes/listar/?id={self.cliente.id}')
        cl = self.cliente
        p10 = IPAMPrefixo.objects.get(cliente=cl, prefixo='10.0.0.0/8')
        p10b = IPAMPrefixo.objects.create(cliente=cl, prefixo='10.1.0.0/16', pai=p10)
        sr24 = IPAMSubRede.objects.create(cliente=cl, prefixo=p10, rede='10.0.0.0/24')
        sr26 = IPAMSubRede.objects.create(cliente=cl, prefixo=p10, rede='10.0.0.64/26')
        agora = timezone.now()
        # fora da faixa e mais novo que tudo: não pode ser o escolhido
        IPAMPrefixo.objects.filter(cliente=cl, prefixo='172.16.0.0/12').update(atualizado_em=agora + timedelta(hours=1))
        IPAMPrefixo.objects.filter(cliente=cl).exclude(prefixo='172.16.0.0/12').update(atualizado_em=agora - timedelta(days=2))
        IPAMSubRede.objects.filter(id=sr24.id).update(atualizado_em=agora - timedelta(days=1))
        IPAMSubRede.objects.filter(id=sr26.id).update(atualizado_em=agora)
        b.js("IPAM.subredes = []; IPAM._srOpen = {}; IPAM._bdOpen = {}; ipamCarregarPrefixos()")
        b.esperar("document.querySelectorAll('#ipam-prefixos-tbody .ipam-row-prefixo').length > 1")

        # 1) sub-rede /26 dentro da /24 fechada: o caminho abre e ela fica destacada
        b.js(f"ipamFiltrarPorPrefixo('{p10.id}')")
        b.esperar(f"!!document.querySelector('#sr-row-{sr26.id}.ipam-row-recente')")
        self.assertTrue(self.visivel(f'sr-row-{sr26.id}'))

        # 2) agora o prefixo filho é o mais novo
        IPAMPrefixo.objects.filter(id=p10b.id).update(atualizado_em=agora + timedelta(minutes=5))
        b.js("IPAM._srOpen = {}; IPAM._bdOpen = {}; ipamFiltrarPorPrefixo('')")
        b.esperar("document.getElementById('ipam-pf-limpar').hidden")
        b.js(f"ipamFiltrarPorPrefixo('{p10.id}')")
        b.esperar(f"!!document.querySelector('#pfx-row-{p10b.id}.ipam-row-recente')")
        self.assertTrue(self.visivel(f'pfx-row-{p10b.id}'))
        self.assertFalse(b.js(f"!!document.querySelector('#sr-row-{sr26.id}.ipam-row-recente')"))
        self.assertEqual(b.erros, [])
