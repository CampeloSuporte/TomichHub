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
        b.js("IPAM.subredes = []; ipamCarregarPrefixos()")
        b.esperar(f"document.querySelectorAll('#ipam-prefixos-tbody .ipam-row-prefixo').length === {total}")
        self.assertEqual(b.js("document.getElementById('ipam-pfx-filtro').options.length"), total + 1)

        b.js(f"(s => {{ s.value = '{self.p10.id}'; s.dispatchEvent(new Event('change')); }})(document.getElementById('ipam-pfx-filtro'))")
        b.esperar("document.querySelectorAll('#ipam-prefixos-tbody .ipam-row-prefixo').length === 3")
        self.assertEqual(sorted(self.linhas()), ['10.0.0.0/8', '10.1.0.0/16', '10.2.0.0/16'])
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
        self.assertEqual(b.js("document.getElementById('ipam-pfx-filtro').value"), str(self.p10b.id))

        b.js("ipamFiltrarPorPrefixo('')")
        b.esperar(f"document.querySelectorAll('#ipam-prefixos-tbody .ipam-row-prefixo').length === {total}")
        self.assertEqual(b.erros, [])
