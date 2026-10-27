"""Gerador de CGNAT MikroTik (clientes/cgnat.py e clientes/cgnat_views.py).

O gabarito é a saída real do gerador da Redes Brasil para
131.100.240.192/29 + 100.64.0.0/23, baixada em 27/10/2026 e guardada em
clientes/fixtures_cgnat/ (.rsc e .csv)."""
import json
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from clientes import cgnat
from clientes.models import Acesso, Cliente, Documento
from usuario.models import TOTPDevice

FIX = Path(__file__).parent / 'fixtures_cgnat'
REF_RSC = FIX / 'redesbrasil_131.100.240.192_29__100.64.0.0_23.rsc'
REF_CSV = FIX / 'redesbrasil_131.100.240.192_29__100.64.0.0_23.csv'
PUB, PRIV = '131.100.240.192/29', '100.64.0.0/23'


def _regras(texto):
    return [l for l in texto.splitlines() if l.startswith('/ip')]


class PlanoTest(SimpleTestCase):
    def setUp(self):
        self.plano = cgnat.planejar(PUB, PRIV)

    def test_resumo_igual_redes_brasil(self):
        # "512 IPs Privados / 8 IPs Públicos / 64 Clientes por IP / 1008 Portas por cliente"
        r = self.plano.resumo()
        self.assertEqual((r['ips_privados'], r['ips_publicos'], r['clientes_por_ip'],
                          r['portas_por_cliente']), (512, 8, 64, 1008))
        self.assertEqual(r['portas_sobrando'], 0)
        self.assertEqual(self.plano.fatias[-1].portas, '64528-65535')

    def test_script_v6_identico_ao_da_redes_brasil(self):
        meu = cgnat.gerar_script(self.plano, 'v6', icmp=False, fallback=True)
        self.assertEqual(_regras(meu), _regras(REF_RSC.read_text()))
        self.assertEqual(len(_regras(meu)), 2 + 64 * 3 + 1)

    def test_icmp_antes_do_fallback(self):
        regras = _regras(cgnat.gerar_script(self.plano, 'v6'))
        self.assertIn('protocol=icmp action=netmap src-address=100.64.0.0/23 '
                      'to-addresses=131.100.240.192/29', regras[-2])
        self.assertNotIn('protocol=', regras[-1])
        self.assertNotIn('to-ports', regras[-1])

    def test_v7_mesmas_regras_com_sintaxe_v7(self):
        v6 = _regras(cgnat.gerar_script(self.plano, 'v6'))
        v7 = _regras(cgnat.gerar_script(self.plano, 'v7'))
        self.assertEqual(len(v6), len(v7))
        self.assertTrue(all(l.startswith('/ip/firewall/') for l in v7))
        self.assertIn('jump-target=cgnat-100.64.0.8-29 src-address=100.64.0.8/29', v7[3 * 1 + 2])
        self.assertIn('chain=cgnat-100.64.1.248-29 ', v7[-3])
        self.assertIn('to-ports=64528-65535', v7[-3])
        # sem "/" em nome de chain no v7
        for l in v7:
            for tok in l.split():
                if tok.startswith(('chain=', 'jump-target=')):
                    self.assertNotIn('/', tok, l)

    def test_mapeamento_igual_ao_csv_da_redes_brasil(self):
        ref = {}
        for linha in REF_CSV.read_text().splitlines()[1:]:
            portas, pub, priv = linha.split(';')
            a, b = (int(x) for x in portas.split(' - '))
            ref[priv] = (pub, a, b)
        meu = {priv: (pub, a, b) for priv, pub, a, b in self.plano.mapeamento()}
        self.assertEqual(len(meu), 512)
        self.assertEqual(meu, ref)

    def test_csv_gerado(self):
        linhas = cgnat.gerar_csv(self.plano).lstrip('﻿').splitlines()
        self.assertEqual(linhas[0], 'IP Privado;IP Público;Porta inicial;Porta final;Portas')
        self.assertEqual(linhas[4], '100.64.0.3;131.100.240.195;1024;2031;1024-2031')
        self.assertEqual(len(linhas), 513)

    def test_localizar_ip_publico_porta(self):
        self.assertEqual(self.plano.localizar('131.100.240.199', 65000), '100.64.1.255')
        self.assertEqual(self.plano.localizar('131.100.240.195', 1500), '100.64.0.3')
        self.assertEqual(self.plano.localizar('131.100.240.192', 2032), '100.64.0.8')
        self.assertIsNone(self.plano.localizar('131.100.240.192', 80))
        self.assertIsNone(self.plano.localizar('8.8.8.8', 2000))

    def test_portas_por_cliente_e_faixa(self):
        p = cgnat.planejar(PUB, PRIV, porta_inicial=2000, porta_final=60000, portas_por_cliente=900)
        self.assertEqual(p.fatias[0].portas, '2000-2899')
        self.assertEqual(p.fatias[63].portas, '58700-59599')
        self.assertEqual(p.resumo()['portas_sobrando'], (60000 - 2000 + 1) - 64 * 900)
        self.assertEqual(len(p.avisos), 1)  # 900 < 1000 portas
        self.assertIn('máximo 906 por cliente', ValidacaoTest().erro(
            PUB, PRIV, porta_inicial=2000, porta_final=60000, portas_por_cliente=1000))

    def test_aviso_poucas_portas(self):
        p = cgnat.planejar('200.200.200.0/30', '100.64.0.0/22')  # 256 clientes/IP
        self.assertEqual(p.portas_por_cliente, 252)
        self.assertTrue(any('252 portas' in a for a in p.avisos))

    def test_ip_publico_unico(self):
        p = cgnat.planejar('200.200.200.1/32', '100.64.0.0/26')
        self.assertEqual(len(p.fatias), 64)
        self.assertEqual(p.mapeamento()[5], ('100.64.0.5', '200.200.200.1', 1024 + 5 * 1008, 1024 + 6 * 1008 - 1))


class ValidacaoTest(SimpleTestCase):
    def erro(self, *a, **kw):
        with self.assertRaises(cgnat.CGNATErro) as cm:
            cgnat.planejar(*a, **kw)
        return str(cm.exception)

    def test_cidr_invalido(self):
        self.assertIn('não é um CIDR válido', self.erro('131.100.240.300/29', PRIV))
        self.assertIn('sem máscara', self.erro('131.100.240.192', PRIV))
        self.assertIn('o certo é 131.100.240.192/29', self.erro('131.100.240.193/29', PRIV))
        self.assertIn('IPv4', self.erro('2001:db8::/64', PRIV))
        self.assertIn('Informe o bloco privado', self.erro(PUB, ''))

    def test_blocos_trocados_ou_errados(self):
        self.assertIn('é privado/CGNAT', self.erro('100.64.0.0/29', '100.64.4.0/23'))
        self.assertIn('é privado/CGNAT', self.erro('10.0.0.0/29', PRIV))
        self.assertIn('não é RFC 1918', self.erro(PUB, '8.8.0.0/23'))
        self.assertIn('menor que o público', self.erro('131.100.240.0/24', '100.64.0.0/25'))
        self.assertIn('grande demais', self.erro('131.100.240.0/24', '100.64.0.0/15'))

    def test_portas_insuficientes(self):
        self.assertIn('Portas insuficientes', self.erro(PUB, PRIV, portas_por_cliente=2000))
        self.assertIn('Portas insuficientes', self.erro(PUB, PRIV, porta_inicial=65500))
        self.assertIn('menor que a final', self.erro(PUB, PRIV, porta_inicial=5000, porta_final=4000))
        self.assertIn('de 1 a 65535', self.erro(PUB, PRIV, porta_final=70000))
        self.assertIn('número inteiro', self.erro(PUB, PRIV, portas_por_cliente='mil'))

    def test_versao_invalida(self):
        with self.assertRaises(cgnat.CGNATErro):
            cgnat.gerar_script(cgnat.planejar(PUB, PRIV), 'v8')


class ViewTest(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        ov = override_settings(MEDIA_ROOT=self.media)
        ov.enable()
        self.addCleanup(ov.disable)
        self.user = User.objects.create_user('cgnat_adm', password='x', is_staff=True, is_superuser=True)
        TOTPDevice.objects.create(usuario=self.user, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.cliente = Cliente.objects.create(
            nome_empresa='Cliente CGNAT', cnpj='55.555.555/0001-55', endereco='Rua C', email='cg@example.com')
        self.acesso = Acesso.objects.create(cliente=self.cliente, tipo='MikroTik', host='10.0.0.1',
                                            porta=22, usuario='admin', senha='x')
        self.client.force_login(self.user)

    def _post(self, **corpo):
        corpo.setdefault('publico', PUB)
        corpo.setdefault('privado', PRIV)
        return self.client.post(reverse('gerar_cgnat'), json.dumps(corpo), content_type='application/json')

    def test_gera_sem_salvar(self):
        r = self._post(versao='v7')
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertEqual(j['resumo']['portas_por_cliente'], 1008)
        self.assertIn('/ip/firewall/nat/add', j['script'])
        self.assertEqual(len(j['mapeamento']), 512)
        self.assertEqual(j['documentos'], [])
        self.assertFalse(Documento.objects.exists())

    def test_erro_de_validacao(self):
        r = self._post(privado='100.64.0.0/33')
        self.assertEqual(r.status_code, 400)
        self.assertIn('CIDR', r.json()['erro'])

    def test_salva_nos_documentos_pelo_acesso_do_terminal(self):
        r = self._post(salvar=True, acesso_id=self.acesso.id)
        j = r.json()
        self.assertEqual(j['cliente']['id'], self.cliente.id)
        docs = Documento.objects.filter(cliente=self.cliente).order_by('nome')
        self.assertEqual(docs.count(), 2)
        nomes = [d.nome for d in docs]
        self.assertTrue(nomes[0].startswith('CGNAT_131.100.240.192_29__100.64.0.0_23_v6_'))
        self.assertTrue(nomes[0].endswith('.csv') and nomes[1].endswith('.rsc'))
        with docs[0].arquivo.open('rb') as f:
            self.assertIn(b'100.64.1.255;131.100.240.199;64528;65535', f.read())
        with docs[1].arquivo.open('rb') as f:
            self.assertEqual(_regras(f.read().decode()), _regras(j['script']))

    def test_salva_pelo_cliente_id(self):
        self._post(salvar=True, cliente_id=self.cliente.id, versao='v7')
        self.assertEqual(Documento.objects.filter(cliente=self.cliente).count(), 2)

    def test_sem_cliente_nao_salva_e_avisa(self):
        j = self._post(salvar=True).json()
        self.assertTrue(any('Não salvo' in a for a in j['avisos']))
        self.assertFalse(Documento.objects.exists())

    def test_download_e_tela(self):
        r = self.client.get(reverse('baixar_cgnat'), {'formato': 'csv', 'publico': PUB, 'privado': PRIV})
        self.assertEqual(r.status_code, 200)
        self.assertIn('attachment; filename="CGNAT_131.100.240.192_29__100.64.0.0_23_v6.csv"',
                      r['Content-Disposition'])
        r = self.client.get(reverse('baixar_cgnat'), {'publico': PUB, 'privado': PRIV, 'versao': 'v7'})
        self.assertIn(b'/ip/firewall/nat/add', r.content)
        r = self.client.get(reverse('gerador_cgnat'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Cliente CGNAT')
