import json
import os
import shutil
import tempfile

from django.test import TestCase, override_settings
from django.urls import reverse

from clientes.models import Acesso, BackupLog, TopologiaDiagrama

from . import analise, composicao_hld, composicao_tobe, exportacao, tobe
from . import convencao as cv
from .extratores import extrair_huawei, extrair_mikrotik
from .models import CenarioTopologia, DocumentoRede
from .tests import HUAWEI_PE, MIKROTIK_CGNAT, _BaseViews, _inventario

HUAWEI_PE2 = """\
sysname SW3-PE-JNA-CEN-01
#
mpls lsr-id 10.0.0.2
mpls
mpls ldp
#
ip vpn-instance INTERNET
 ipv4-family
  route-distinguisher 65000:1
  vpn-target 65000:1 export-extcommunity
  vpn-target 65000:1 import-extcommunity
#
interface Vlanif3202
 ip address 172.16.0.2 255.255.255.252
 ospf enable 1 area 0.0.0.1
 mpls
 mpls ldp
#
ospf 1 router-id 10.9.9.9
 import-route direct
 area 0.0.0.1
#
bgp 65000
 router-id 10.0.0.2
 peer 10.0.0.1 as-number 65000
 #
 ipv4-family vpnv4
  peer 10.0.0.1 enable
#
return
"""


def _modelo_dois_pes():
    inv = _inventario(extrair_huawei(HUAWEI_PE), extrair_mikrotik(MIKROTIK_CGNAT))
    pe2 = inv['equipamentos'][1]
    pe2.update(vendor='huawei', hostname='SW3-PE-JNA-CEN-01', extr=extrair_huawei(HUAWEI_PE2),
               backup={'id': 3, 'data': '2026-09-16T03:00:00-04:00', 'confirmado_em': '2026-09-16T03:00:00-04:00',
                       'hash': '', 'arquivo_disponivel': True, 'arquivo': 'backups/x/pe2.txt'})
    inv['topologia']['enlaces'] = [{
        'mapa': 'BACKBONE', 'a': 'RTR-PE-AFT-CEN-01', 'a_acesso': 1, 'b': 'SW3-PE-JNA-CEN-01', 'b_acesso': 2,
        'iface_a': 'Eth-Trunk1', 'iface_b': '100GE0/0/1', 'ip_a': '172.16.0.1/30', 'ip_b': '172.16.0.2/30',
        'vlan': '100', 'capacidade': '100g', 'rotulo': '',
    }]
    return analise.montar_modelo(inv)


class ConvencaoTest(TestCase):
    def test_large_community_para_asn_4_bytes(self):
        conv = cv.convencao_padrao(asn='272648', prefixo_v6='2804:8680::/32')
        self.assertEqual(cv.community(conv, 21000), '272648:21000:0')
        self.assertEqual(cv.faixa_community(conv, '20000-20090'), '272648:20000:0 a 272648:20090:0')
        self.assertEqual(cv.rt(conv, '11000'), '272648:11000')
        self.assertTrue(any('RFC 8092' in a for a in cv.validar(conv)))
        self.assertEqual(conv['bloco_v6_infra'], '2804:8680:ff00::/40')
        self.assertEqual(conv['ipv6'][1]['bloco'], '2804:8680:ff01::/48')

    def test_community_padrao_para_asn_16_bits(self):
        conv = cv.convencao_padrao(asn='65000')
        self.assertEqual(cv.community(conv, 666), '65000:666')
        self.assertFalse(any('RFC 8092' in a for a in cv.validar(conv)))

    def test_rt_acima_de_16_bits_com_asn_4_bytes(self):
        conv = cv.convencao_padrao(asn='272648')
        conv['servicos'][0]['rt'] = '70000'
        self.assertTrue(any('70000' in a for a in cv.validar(conv)))

    def test_hld_escapa_e_formata(self):
        conv = cv.convencao_padrao(asn='272648')
        conv['servicos'][0]['nome'] = '<script>x</script>'
        html = ''.join(f(conv) for _, _, f in composicao_hld.SECOES)
        self.assertNotIn('<script>', html)
        self.assertIn('272648:30700:0', html)
        self.assertIn('272648:19070', html)


class MotorTobeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.m = _modelo_dois_pes()
        cls.conv = cv.convencao_padrao(asn='65000', rr01='AFT', rr02='JNA')

    def _wave(self, plano, n):
        return plano['waves'][n]['itens']

    def test_sem_cenario_usa_sugestoes(self):
        plano = tobe.gerar_plano(self.m, self.conv)
        self.assertEqual(len(plano['waves']), 10)
        self.assertIn('RTR-PE-AFT-CEN-01', plano['rr01'])
        self.assertIn('RTR-PE-AFT-CEN-01', plano['bng01'])      # mesmo equipamento com dois papéis
        self.assertEqual(plano['mapeamentos']['vrfs']['CLIENTE-X'], tobe.L3VPN)
        self.assertTrue(any('MTU alvo' in p for p in plano['pendencias']))

    def test_underlay_detecta_desvios(self):
        itens = self._wave(tobe.gerar_plano(self.m, self.conv), 2)
        acoes = ' | '.join(f'{i["objeto"]}: {i["acao"]} ({i["atual"]} -> {i["alvo"]})' for i in itens)
        self.assertIn('SW3-PE-JNA-CEN-01: Migrar o OSPF de infraestrutura para o processo padrão (processo 1 -> processo 10)', acoes)
        self.assertIn('Consolidar as áreas OSPF', acoes)
        self.assertIn('Igualar o router-id OSPF ao MPLS LSR-ID (10.9.9.9 -> 10.0.0.2)', acoes)
        self.assertIn('Remover a redistribuição do IGP', acoes)
        self.assertIn('Habilitar LDP/IGP synchronization', acoes)

    def test_mtu_por_enlace_usa_interface_do_ip(self):
        itens = self._wave(tobe.gerar_plano(self.m, dict(self.conv, mtu_alvo='9000')), 1)
        self.assertEqual(len(itens), 1)
        self.assertIn('Eth-Trunk1.100', itens[0]['atual'])      # achado pelo IP, não pelo nome
        self.assertIn('Vlanif3202', itens[0]['atual'])
        self.assertEqual(itens[0]['acao'], 'Igualar a MTU das duas pontas e do meio de transporte')
        self.assertEqual(itens[0]['lote'], '1')

    def test_rr_e_vrf_e_borda(self):
        plano = tobe.gerar_plano(self.m, self.conv)
        w3 = ' | '.join(i['acao'] for i in self._wave(plano, 3))
        self.assertIn('Remover peer iBGP histórico 10.0.0.99', w3)
        w4 = self._wave(plano, 4)
        self.assertTrue(any(i['alvo'].startswith('RD 10.0.0.1:1000 · RT 65000:11000') for i in w4))
        w5 = self._wave(plano, 5)
        self.assertTrue(any('TR-CLIENTE-X-L3-001' in i['alvo'] for i in w5))
        w8 = ' | '.join(i['alvo'] for i in self._wave(plano, 8))
        self.assertIn('UPLINK01 · 65000:20000', w8)
        self.assertIn('CONTENT01', w8)
        self.assertIn('DOWNSTREAM', w8)

    def test_bng_pool_fora_do_bloco_e_cgnat(self):
        plano = tobe.gerar_plano(self.m, self.conv)
        w6 = self._wave(plano, 6)
        # 100.80.64.0/18 pertence ao bloco do BNG02, mas o AFT é o BNG01 → renumerar
        renum = [i for i in w6 if 'Renumerar' in i['acao']]
        self.assertEqual(renum[0]['atual'], '100.80.64.0/18')
        self.assertIn('100.64.0.0/12', renum[0]['alvo'])
        w7 = self._wave(plano, 7)
        self.assertTrue(any(i['objeto'] == 'RTR-CGN-AFT-CEN-01' for i in w7))
        w9 = ' | '.join(i['acao'] for i in self._wave(plano, 9))
        self.assertIn('Retirar o CGNAT externo', w9)

    def test_cenario_define_papeis_estados_e_lotes(self):
        cenario = {
            'nodes': [
                {'id': 'a', 'label': 'RTR-PE-AFT', 'acesso_id': 1, 'tobe': {'papeis': ['RR02'], 'estado': 'manter'}},
                {'id': 'b', 'label': 'SW-JNA', 'acesso_id': 2, 'tobe': {'papeis': ['RR01', 'BNG01'],
                                                                        'loopback': '198.18.248.1/32'}},
                {'id': 'c', 'label': 'PE-NOVO', 'tobe': {'papeis': ['PE'], 'estado': 'novo'}},
                {'id': 'd', 'label': 'CGN', 'acesso_id': 3, 'tobe': {'estado': 'remover'}},
            ],
            'links': [
                {'id': 'l1', 'src': 'a', 'tgt': 'b', 'iface_a': 'Eth-Trunk1', 'ip_local': '172.16.0.1/30',
                 'ip_remote': '172.16.0.2/30', 'vlan': '100', 'tobe': {'mtu_alvo': '9100', 'lote': '3'}},
                {'id': 'l2', 'src': 'b', 'tgt': 'c', 'tobe': {'estado': 'novo', 'custo': '10'}},
            ],
        }
        plano = tobe.gerar_plano(self.m, self.conv, cenario)
        self.assertIn('SW3-PE-JNA-CEN-01', plano['rr01'])
        self.assertIn('SW3-PE-JNA-CEN-01', plano['bng01'])
        w1 = self._wave(plano, 1)
        enlace = next(i for i in w1 if 'RTR-PE-AFT' in i['objeto'])
        self.assertEqual((enlace['alvo'], enlace['lote']), ('9100', '3'))
        self.assertTrue(any('novo' in i['acao'] for i in w1))
        w2 = ' | '.join(f'{i["acao"]} {i["alvo"]}' for i in self._wave(plano, 2))
        self.assertIn('Implantar a loopback aprovada e migrar LSR-ID/router-id 198.18.248.1/32', w2)
        self.assertIn('Ativar o enlace novo no underlay', w2)
        self.assertIn('custo 10', w2)
        w0 = ' | '.join(i['objeto'] for i in self._wave(plano, 0))
        self.assertIn('PE-NOVO', w0)
        w3 = ' | '.join(f'{i["objeto"]}: {i["acao"]}' for i in self._wave(plano, 3))
        self.assertNotIn('Retirar a função de route reflector', w3)   # AFT é o RR02 do cenário

    def test_mapeamentos_manuais_vencem_sugestoes(self):
        mapa = {'vrfs': {'CLIENTE-X': 'VRF-BUSINESS'}, 'papeis': {'1': ''}, 'criticos': 'NADA'}
        plano = tobe.gerar_plano(self.m, self.conv, None, mapa)
        self.assertEqual(plano['mapeamentos']['vrfs']['CLIENTE-X'], 'VRF-BUSINESS')
        self.assertEqual(plano['rr01'], '')
        self.assertTrue(any('RR01 não definido' in p for p in plano['pendencias']))

    def test_documento_renderiza_todas_as_waves(self):
        from .documentos import gerar_secoes
        ctx = {'plano': tobe.gerar_plano(self.m, self.conv), 'conv': self.conv,
               'refs': {'documentos': ['HLD v1'], 'backups': ['pe.txt'], 'fontes': ['x']}}
        secoes = gerar_secoes('change_plan', ctx)
        self.assertEqual([s['chave'] for s in secoes], [k for k, _, _ in composicao_tobe.SECOES])
        html = ''.join(s['html'] for s in secoes)
        self.assertIn('PRIORIDADE CRÍTICA.', html)
        self.assertIn('DECISÃO PENDENTE DO LLD.', html)
        self.assertIn('W1-001', html)


class DocxListaNumeradaTest(TestCase):
    def test_cada_lista_recomeca_em_1(self):
        import io
        import zipfile
        from clientes.models import Cliente
        cli = Cliente.objects.create(nome_empresa='X', cnpj='1', endereco='r', email='x@x.com')
        doc = DocumentoRede.objects.create(cliente=cli, tipo='hld', titulo='T', metadados={}, secoes=[
            {'id': 'a', 'titulo': 'A', 'html': '<ol><li>um</li><li>dois</li></ol><ol><li>outro</li></ol>'}])
        with zipfile.ZipFile(io.BytesIO(exportacao.gerar_docx(doc))) as z:
            xml = z.read('word/document.xml').decode()
        self.assertEqual(xml.count('>1.<') + xml.count('>1.\t<'), 2)
        self.assertNotIn('>3.', xml)


class FluxoTobeViewsTest(_BaseViews):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        TopologiaDiagrama.objects.create(cliente=self.cliente, nome='BACKBONE', dados_json=json.dumps({
            'nodes': [{'id': 'crm_1', 'label': 'PE', 'acesso_id': Acesso.objects.get().id, 'submap_id': 99}],
            'links': []}))

    def post(self, nome, *args, dados=None):
        with override_settings(MEDIA_ROOT=self.media):
            return self.post_json(reverse(f'projeto_rede:{nome}', args=args), dados or {})

    def test_hld_convencao_cenario_plano(self):
        r = self.post('gerar_hld', self.cliente.id, dados={'asn': '272648'})
        self.assertEqual(r.status_code, 200, r.content)
        hld = DocumentoRede.objects.get(tipo='hld')
        self.assertEqual(hld.dados['convencao']['asn'], '272648')
        self.assertIn('272648:21000:0', json.dumps(hld.secoes))

        r = self.client.get(reverse('projeto_rede:convencao', args=[hld.id]))
        self.assertContains(r, 'cv-dados')
        conv = hld.dados['convencao']
        conv['mtu_alvo'] = '9100'
        conv['servicos'].append({'id': '9000', 'nome': 'VRF-NOVA', 'camada': 'Teste', 'rt': '19999', 'lixo': 'x'})
        r = self.post('salvar_convencao', hld.id, dados={'convencao': conv})
        self.assertTrue(r.json()['ok'])
        hld.refresh_from_db()
        self.assertEqual(hld.dados['convencao']['servicos'][-1], {'id': '9000', 'nome': 'VRF-NOVA', 'camada': 'Teste', 'rt': '19999'})
        self.assertIn('9100', json.dumps(hld.secoes))

        r = self.post('cenario_criar', self.cliente.id)
        cen = CenarioTopologia.objects.get()
        self.assertNotIn('submap_id', cen.dados_json)
        r = self.client.get(reverse('projeto_rede:cenario_editor', args=[cen.id]))
        self.assertContains(r, 'window.TOPO_CENARIO')
        self.assertContains(r, reverse('projeto_rede:cenario_salvar', args=[cen.id]))
        dados = cen.dados()
        dados['nodes'][0]['tobe'] = {'papeis': ['RR01', 'BNG01'], 'estado': 'manter'}
        r = self.post('cenario_salvar', cen.id, dados={'nome': 'Alvo 2027', 'dados_json': json.dumps(dados)})
        self.assertTrue(r.json()['ok'])
        cen.refresh_from_db()
        self.assertEqual(cen.nome, 'Alvo 2027')
        self.assertEqual(TopologiaDiagrama.objects.get().nome, 'BACKBONE')     # mapa real intacto

        r = self.post('gerar_tobe', self.cliente.id, dados={'hld_id': hld.id, 'cenario_id': cen.id})
        self.assertEqual(r.status_code, 200, r.content)
        plano = DocumentoRede.objects.get(tipo='change_plan')
        self.assertEqual(plano.dados['hld_id'], hld.id)
        texto = json.dumps(plano.secoes)
        self.assertIn('RR01: RTR-PE-AFT-CEN-01', texto)
        self.assertIn('9100', texto)
        self.assertEqual(plano.metadados['principio_titulo'], 'PRIORIDADE CRÍTICA.')

        with override_settings(MEDIA_ROOT=self.media):
            r = self.client.get(reverse('projeto_rede:tobe_mapeamentos', args=[plano.id]))
        self.assertContains(r, 'CLIENTE-X')
        r = self.post('salvar_mapeamentos', plano.id, dados={
            'mapeamentos': {'vrfs': {'CLIENTE-X': 'VRF-BUSINESS'}, 'criticos': 'X'},
            'hld_id': hld.id, 'cenario_id': cen.id})
        self.assertTrue(r.json()['ok'], r.content)
        plano.refresh_from_db()
        self.assertEqual(plano.dados['mapeamentos']['vrfs']['CLIENTE-X'], 'VRF-BUSINESS')
        self.assertIn('CLIENTE-X → VRF-BUSINESS', json.dumps(plano.secoes, ensure_ascii=False))

        with override_settings(MEDIA_ROOT=self.media):
            r = self.post_json(reverse('projeto_rede:regenerar_secao', args=[plano.id]), {'chave': 'tobe_wave_4'})
        self.assertTrue(r.json()['ok'])
        r = self.post_json(reverse('projeto_rede:regenerar_secao', args=[plano.id]), {'chave': 'controle'})
        self.assertEqual(r.status_code, 400)          # chave do AS-IS não vale para o plano

        r = self.client.get(reverse('projeto_rede:docx', args=[plano.id]))
        self.assertEqual(r.status_code, 200)
        r = self.client.get(reverse('projeto_rede:visualizar', args=[hld.id]))
        self.assertContains(r, 'ESCOPO.')
        r = self.client.get(reverse('projeto_rede:lista', args=[self.cliente.id]))
        self.assertContains(r, 'Alvo 2027')
        self.assertContains(r, 'Novo Change Plan')

    def test_cenario_e_convencao_so_admin(self):
        cen = CenarioTopologia.objects.create(cliente=self.cliente)
        self.client.force_login(self.consultor)
        self.assertEqual(self.post('cenario_salvar', cen.id, dados={'nome': 'x'}).status_code, 403)
        self.assertEqual(self.post('gerar_tobe', self.cliente.id).status_code, 403)
        self.assertEqual(self.client.get(reverse('projeto_rede:cenario_editor', args=[cen.id])).status_code, 302)

    def test_plano_sem_hld_nem_cenario(self):
        r = self.post('gerar_tobe', self.cliente.id)
        self.assertEqual(r.status_code, 200, r.content)
        plano = DocumentoRede.objects.get(tipo='change_plan')
        self.assertIn('Nenhum HLD vinculado', json.dumps(plano.secoes, ensure_ascii=False))
