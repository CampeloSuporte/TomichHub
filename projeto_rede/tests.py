import io
import json
import os
import shutil
import tempfile
import zipfile
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from clientes.models import Acesso, BackupLog, Cliente
from usuario.models import PerfilUsuario, TOTPDevice

from . import analise, composicao, exportacao
from .extratores import extrair_huawei, extrair_mikrotik
from .models import DocumentoRede, DocumentoRedeRevisao
from .sanitizar import limpar_html

HUAWEI_PE = """\
============================================================
Comando: display current-configuration
============================================================
!Software Version V800R023C00SPC500
#
sysname RTR-PE-AFT-CEN-01
#
undo telnet server enable
ftp server enable
#
ip vpn-instance INTERNET
 ipv4-family
  route-distinguisher 65000:1
  vpn-target 65000:1 export-extcommunity
  vpn-target 65000:1 import-extcommunity
#
ip vpn-instance CLIENTE-X
 ipv4-family
  route-distinguisher 27248:27063
  vpn-target 27248:27063 export-extcommunity
  vpn-target 27248:27063 import-extcommunity
#
mpls lsr-id 10.0.0.1
#
mpls
 mpls te
 mpls rsvp-te
#
mpls ldp
#
bfd
#
ip pool cgnat bas local
 vpn-instance INTERNET
 gateway 100.80.64.1 255.255.192.0
 section 0 100.80.64.2 100.80.127.254
#
ipv6 prefix pd delegation
 vpn-instance INTERNET
 prefix 2001:db8:400:: 41 delegating-prefix-length 56
#
aaa
 local-user admin password irreversible-cipher SEGREDO-NAO-PODE-VAZAR
 local-user noc password irreversible-cipher OUTRO
 domain clientes
#
interface Eth-Trunk1.100
 mtu 9000
 ip address 172.16.0.1 255.255.255.252
 ospf enable 10 area 0.0.0.0
 mpls
 mpls ldp
#
interface Eth-Trunk2.200
 ip address 172.16.0.5 255.255.255.252
 ospf enable 10 area 0.0.0.0
 mpls
#
interface Virtual-Ethernet0/1/100
 ve-group 100 l2-terminate
#
interface Virtual-Ethernet0/1/100.700
 vlan-type dot1q 700
 description PPPOE-CNZ
 l2 binding vsi L2L-CNZ-PPPOE:700
#
interface LoopBack0
 ip address 10.0.0.1 255.255.255.255
#
bgp 65000
 router-id 10.0.0.1
 undo default ipv4-unicast
 group RR-CLIENTES internal
 peer RR-CLIENTES connect-interface LoopBack0
 peer 10.0.0.2 as-number 65000
 peer 10.0.0.2 group RR-CLIENTES
 peer 10.0.0.2 description SW3-PE-JNA
 peer 10.0.0.99 as-number 65000
 peer 10.0.0.99 description RTR-LEGADO
 #
 ipv4-family vpnv4
  policy vpn-target
  peer RR-CLIENTES enable
  peer RR-CLIENTES reflect-client
  peer 10.0.0.2 enable
  peer 10.0.0.2 group RR-CLIENTES
  peer 10.0.0.99 enable
 #
 ipv4-family vpn-instance INTERNET
  network 200.0.0.0 255.255.255.0 route-policy ORIGEM
  peer 172.24.70.18 as-number 262803
  peer 172.24.70.18 description LINK-NET-ULTRA
  peer 172.24.70.18 route-policy IN-NETULTRA import
  peer 172.24.70.18 route-policy FULL-V4 export
  group CLIENTES-FULL external
  peer 172.24.70.10 as-number 267561
  peer 172.24.70.10 group CLIENTES-FULL
  peer CLIENTES-FULL route-policy DENY import
  peer CLIENTES-FULL route-policy FULL-V4 export
  peer 10.80.1.254 as-number 266284
  peer 10.80.1.254 description UPSTREAN-OPERADORA
  peer 10.80.1.254 route-policy FULL-IN import
  peer 10.80.1.254 route-policy ORIGEM export
  peer 201.0.0.2 as-number 65020
  peer 201.0.0.2 description PEER-CDN-GLOBO
  peer 201.0.0.2 route-policy IN-CDN import
  peer 201.0.0.2 route-policy ORIGEM export
 #
 ipv4-family vpn-instance CLIENTE-X
  peer 198.18.100.17 as-number 270563
  peer 198.18.100.17 description LINK-SICREDI
 #
 ipv6-family vpn-instance INTERNET
  peer 2001:db8::2 as-number 270282
  peer 2001:db8::2 description LINK-MUNDO-NET
  peer 2001:db8::2 ipv6-prefix DEFAULT-V6 export
  peer 2001:db8::2 route-policy IN-MUNDO-V6 import
#
route-policy IN-NETULTRA permit node 10
 if-match ip-prefix AS262803
 apply local-preference 1000
 apply community 27648:450 27648:1010
#
route-policy IN-NETULTRA deny node 500
#
route-policy FULL-V4 permit node 10
 if-match ip-prefix FULL-V4
#
route-policy FULL-IN deny node 5
 if-match ip-prefix BOGONS
#
route-policy FULL-IN permit node 10
 apply local-preference 200
#
route-policy ORIGEM permit node 10
 if-match ip-prefix PROPRIOS
#
route-policy IN-CDN permit node 10
 if-match ip-prefix CDN
#
route-policy IN-MUNDO-V6 permit node 10
 if-match ipv6 address prefix-list MUNDO-V6
 apply local-preference 1000
#
ip ip-prefix AS262803 index 10 permit 186.250.16.0 21 greater-equal 21 less-equal 24
ip ip-prefix FULL-V4 index 10 permit 0.0.0.0 0 greater-equal 8 less-equal 24
ip ip-prefix BOGONS index 10 permit 10.0.0.0 8 greater-equal 8 less-equal 32
ip ip-prefix PROPRIOS index 10 permit 200.0.0.0 24
ip ip-prefix CDN index 10 permit 45.235.72.0 22 less-equal 24
ip ipv6-prefix MUNDO-V6 index 10 permit 2804:83C4:A00:: 40 greater-equal 40 less-equal 48
ip ipv6-prefix DEFAULT-V6 index 10 permit :: 0 less-equal 48
#
ip community-filter advanced 0X-AVTO index 10 permit 27648:6010
ip community-filter advanced 1X-AVTO index 10 permit 27648:6011
ip community-filter advanced 2X-AVTO index 10 permit 27648:6012
ip community-filter advanced 3X-AVTO index 10 permit 27648:6013
ip community-filter advanced Blackhole index 10 permit 27648:666
#
ip route-static vpn-instance INTERNET 201.218.163.32 255.255.255.224 172.24.70.22 description EVOLUTION
ip route-static vpn-instance INTERNET 5.161.228.222 255.255.255.255 NULL0 description bloqueio
#
snmp-agent community read cipher SEGREDO-SNMP
snmp-agent sys-info version v2c v3
#
stelnet server enable
#
return
"""

MIKROTIK_CGNAT = """\
# 2026-09-16 03:00:09 by RouterOS 7.17.2
# model = CCR1036-8G-2S+
/interface bonding add mode=802.3ad name=bond0 slaves=sfp-sfpplus1,sfp-sfpplus2
/interface vlan add comment=PE-INSIDE interface=bond0 name=bond0.10 vlan-id=10
/ip pool add comment="CGNat IXCSoft" name=poolCGNat ranges=100.80.64.0/19
/routing bgp template set default as=65532 router-id=200.220.165.0
/snmp community add addresses=::/0 name=SEGREDO-COMMUNITY
/ip address add address=172.24.67.122/29 interface=bond0.10 network=172.24.67.120
/ip firewall nat add action=netmap chain=srcnat comment="{CGNat IXCSoft}" src-address=100.80.64.0/25 to-addresses=201.218.163.128/32 to-ports=1024-65535
/ip firewall nat add action=netmap chain=srcnat src-address=100.80.64.128/25 to-addresses=201.218.163.129/32 to-ports=1024-65535
/ip firewall nat add action=netmap chain=srcnat src-address=100.80.65.0/25 to-addresses=201.218.163.130/32 to-ports=1024-65535
/ip firewall nat add action=netmap chain=srcnat src-address=100.80.65.128/25 to-addresses=201.218.163.131/32 to-ports=1024-65535
/ip firewall nat add action=netmap chain=srcnat disabled=yes src-address=100.80.66.0/25 to-addresses=201.218.163.132/32
/ip route add gateway=172.24.67.121
/ip service set telnet disabled=yes
/routing bgp connection add address-families=ip as=65532 local.address=172.24.67.122 .role=ebgp name=RTR-PE-AFT remote.address=172.24.67.121/32 .as=65000 templates=default
/system identity set name=RTR-CGN-AFT-CEN-01
"""


class ExtratorHuaweiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d = extrair_huawei(HUAWEI_PE)

    def _peer(self, ip, vrf=''):
        return next(p for p in self.d['bgp']['peers'] if p['ip'] == ip and p['vrf'] == vrf)

    def test_identidade_e_mpls(self):
        d = self.d
        self.assertEqual(d['hostname'], 'RTR-PE-AFT-CEN-01')
        self.assertEqual(d['versao'], 'V800R023C00SPC500')
        self.assertEqual(d['lsr_id'], '10.0.0.1')
        self.assertTrue(d['mpls'] and d['mpls_te'] and d['rsvp_te'] and d['ldp'] and d['bfd'])

    def test_bgp_por_address_family_e_vrf(self):
        rr = self._peer('10.0.0.2')
        self.assertEqual(rr['tipo'], 'ibgp')
        self.assertIn('vpnv4', rr['afs'])
        self.assertIn('vpnv4', rr['reflect_client'])       # herdado do grupo
        self.assertNotIn('unicast-v4', rr['afs'])           # undo default ipv4-unicast

        up = self._peer('172.24.70.18', 'INTERNET')
        self.assertEqual((up['asn'], up['tipo'], up['policy_in']), ('262803', 'ebgp', 'IN-NETULTRA'))

        # grupo declarado dentro da AF da VRF, policy herdada
        membro = self._peer('172.24.70.10', 'INTERNET')
        self.assertEqual((membro['policy_in'], membro['policy_out']), ('DENY', 'FULL-V4'))

        v6 = self._peer('2001:db8::2', 'INTERNET')
        self.assertEqual((v6['familia'], v6['filtro_out']), ('v6', 'DEFAULT-V6'))

    def test_policies_prefixos_e_communities(self):
        nodes = self.d['route_policies']['IN-NETULTRA']
        self.assertEqual(nodes[0]['local_preference'], 1000)
        self.assertEqual(nodes[0]['communities'], ['27648:450', '27648:1010'])
        v6 = self.d['prefix_lists']['DEFAULT-V6'][0]
        self.assertEqual((v6['prefixo'], v6['le'], v6['familia']), ('::/0', 48, 'v6'))
        self.assertEqual(self.d['community_filters']['Blackhole'], ['27648:666'])

    def test_bng_e_seguranca_sem_segredos(self):
        d = self.d
        self.assertEqual(d['bng']['pools'][0]['rede'], '100.80.64.0/18')
        self.assertEqual(d['bng']['pools_v6'][0]['tamanho_delegado'], '56')
        self.assertEqual(d['bng']['ve_l2_terminate'][0]['vlan'], '700')
        seg = d['seguranca']
        self.assertEqual((seg['telnet'], seg['ftp'], seg['usuarios_locais']), ('desabilitado', True, 2))
        self.assertEqual(seg['snmp_communities'], 1)
        self.assertNotIn('SEGREDO', json.dumps(d))

    def test_mtu_e_rotas_estaticas(self):
        itf = {i['nome']: i for i in self.d['interfaces']}
        self.assertEqual(itf['Eth-Trunk1.100']['mtu'], 9000)
        self.assertIsNone(itf['Eth-Trunk2.200']['mtu'])
        rota = self.d['rotas_estaticas'][0]
        self.assertEqual((rota['prefixo'], rota['proximo_salto'], rota['descricao']),
                         ('201.218.163.32/27', '172.24.70.22', 'EVOLUTION'))


class ExtratorMikrotikTest(TestCase):
    def test_cgnat(self):
        d = extrair_mikrotik(MIKROTIK_CGNAT)
        self.assertEqual((d['hostname'], d['versao'], d['modelo']), ('RTR-CGN-AFT-CEN-01', '7.17.2', 'CCR1036-8G-2S+'))
        self.assertEqual(d['nat']['netmap'], 4)          # a regra desabilitada não conta
        self.assertEqual(d['nat']['com_portas'], 4)
        self.assertEqual(d['bgp']['asn'], '65532')
        # `.as=` abreviado herda o prefixo `remote.`
        self.assertEqual((d['bgp']['peers'][0]['ip'], d['bgp']['peers'][0]['asn']), ('172.24.67.121', '65000'))
        self.assertEqual(d['rota_default'], '172.24.67.121')
        self.assertIn('telnet', d['seguranca']['servicos_desabilitados'])
        self.assertNotIn('SEGREDO', json.dumps(d))


def _inventario(extr_huawei, extr_mk=None):
    eqs = [{
        'acesso_id': 1, 'nome': 'NE8K-AFT', 'host': '10.0.0.1', 'protocolo': 'SSH', 'funcao': 'ROTEADOR PE',
        'modelo': 'NE8000', 'fabricante': 'HUAWEI', 'backup_habilitado': True,
        'backup': {'id': 1, 'data': '2026-09-16T03:00:00-04:00', 'confirmado_em': '2026-09-16T03:00:00-04:00',
                   'hash': '', 'arquivo_disponivel': True},
        'vendor': 'huawei', 'hostname': extr_huawei['hostname'], 'pop': 'AFT', 'extr': extr_huawei,
        'generico': None,
        'l2vpn': [{'tipo': 'vpls', 'tecnologia': 'VSI', 'nome': 'L2L-CNZ-PPPOE:700', 'id': '', 'sinalizacao': 'bgp',
                   'mtu': '', 'vlan': '', 'peers': [], 'interfaces': [], 'descricao': ''}],
    }, {
        'acesso_id': 2, 'nome': 'SW-PE-JNA', 'host': '10.0.0.2', 'protocolo': 'SSH', 'funcao': 'SWITCH L3',
        'modelo': '', 'fabricante': '', 'backup_habilitado': True, 'backup': None, 'vendor': '',
        'hostname': '', 'pop': 'JNA', 'extr': None, 'generico': None, 'l2vpn': [],
    }]
    if extr_mk:
        eqs.append({
            'acesso_id': 3, 'nome': 'Cgnat AFT', 'host': '172.24.67.122', 'protocolo': 'SSH', 'funcao': 'CGNAT',
            'modelo': 'CCR1036', 'fabricante': 'MIKROTIK', 'backup_habilitado': True,
            'backup': {'id': 2, 'data': '2026-09-16T03:00:00-04:00', 'confirmado_em': '2026-09-16T03:00:00-04:00',
                       'hash': '', 'arquivo_disponivel': True},
            'vendor': 'mikrotik', 'hostname': extr_mk['hostname'], 'pop': 'AFT', 'extr': extr_mk,
            'generico': None, 'l2vpn': [],
        })
    return {'cliente': {'id': 1, 'nome': 'Provedor Teste', 'cidade': '', 'estado': ''},
            'gerado_em': '2026-09-16T10:00:00-04:00', 'equipamentos': eqs,
            'topologia': {'mapas': [], 'enlaces': []}, 'blocos_ip': []}


class AnaliseTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.m = analise.montar_modelo(_inventario(extrair_huawei(HUAWEI_PE), extrair_mikrotik(MIKROTIK_CGNAT)))

    def _sessao(self, peer):
        return next(s for s in self.m['ebgp'] if s['peer'] == peer)

    def test_classificacao_pela_politica(self):
        self.assertEqual(self._sessao('172.24.70.18')['classe'], 'ISP downstream')
        self.assertEqual(self._sessao('172.24.70.18')['anuncia'], 'Full routing')
        self.assertEqual(self._sessao('172.24.70.10')['recebe'], 'Nenhuma')
        self.assertEqual(self._sessao('172.24.70.10')['classe'], 'ISP downstream')
        self.assertEqual(self._sessao('10.80.1.254')['classe'], 'Upstream (trânsito)')
        self.assertTrue(self._sessao('10.80.1.254')['filtra_bogons'])
        self.assertEqual(self._sessao('201.0.0.2')['classe'], 'Parceiro de conteúdo / CDN')
        self.assertEqual(self._sessao('198.18.100.17')['classe'], 'Cliente L3VPN (CLIENTE-X)')

    def test_downstream_agrupado_com_lp_e_communities(self):
        g = next(g for g in self.m['downstreams'] if g['asn'] == '262803')
        self.assertEqual(g['lp'], [1000])
        self.assertEqual(g['communities'], ['27648:450', '27648:1010'])
        self.assertEqual(g['prefixos'], ['186.250.16.0/21 até /24'])

    def test_lista_default_que_entrega_full(self):
        mundo = self._sessao('2001:db8::2')
        self.assertEqual(mundo['anuncia'], 'Full routing')
        self.assertTrue(any(x['lista'] == 'DEFAULT-V6' for x in self.m['listas_nome_divergente']))

    def test_papeis_rr_e_bng_e_cgnat(self):
        eq = self.m['equipamentos'][0]
        self.assertIn('RR', eq['papeis'])
        self.assertIn('PE', eq['papeis'])
        self.assertIn('BNG', eq['papeis'])
        self.assertEqual(self.m['cgnats'][0]['nome'], 'RTR-CGN-AFT-CEN-01')
        self.assertTrue(self.m['cgnats'][0]['deterministico'])
        self.assertEqual(self.m['pppoe'][0]['id'], '700')

    def test_achados(self):
        titulos = {a['titulo'] for a in self.m['achados']}
        self.assertIn('MTU heterogênea no backbone e nos serviços', titulos)
        self.assertIn('Peers iBGP históricos', titulos)                   # 10.0.0.99
        self.assertIn('Prefix-list com nome divergente do conteúdo', titulos)
        self.assertIn('RD/RT fora do padrão do ASN', titulos)             # 27248:27063
        self.assertIn('Cobertura de backup incompleta', titulos)          # SW-PE-JNA
        ids = [a['id'] for a in self.m['achados']]
        self.assertEqual(ids[0], 'AS-IS-001')
        sevs = [analise.SEVERIDADES.index(a['severidade']) for a in self.m['achados']]
        self.assertEqual(sevs, sorted(sevs))

    def test_communities_familia_de_prepend(self):
        linhas = {l['valor']: l for l in self.m['communities']['linhas']}
        self.assertIn('27648:6010 a 27648:6013', linhas)
        self.assertTrue(self.m['communities']['regra_prepend'])
        self.assertEqual(linhas['27648:666']['finalidade'], 'Blackhole')

    def test_secoes_escapam_texto_da_configuracao(self):
        inv = _inventario(extrair_huawei(HUAWEI_PE.replace('LINK-NET-ULTRA', '<script>x</script>')))
        m = analise.montar_modelo(inv)
        html = ''.join(s['html'] for s in composicao.gerar_secoes(m))
        self.assertNotIn('<script', html.lower())
        self.assertIn('&lt;script&gt;', html.lower())


class SanitizarTest(TestCase):
    def test_lista_de_permissoes(self):
        sujo = ('<h1 style="color:red" onclick="x()">Título</h1><script>alert(1)</script>'
                '<div class="callout evil"><b>ok</b></div><div>linha</div>'
                '<a href="javascript:alert(1)">x</a><a href="https://a.b">y</a>'
                '<span class="sev-alta" style="x">Alta</span><img src=x onerror=alert(1)>'
                '<table><tr><td colspan="2" onmouseover="x">c</td></tr></table>')
        limpo = limpar_html(sujo)
        self.assertNotIn('script', limpo)
        self.assertNotIn('onclick', limpo)
        self.assertNotIn('onerror', limpo)
        self.assertNotIn('javascript', limpo)
        self.assertNotIn('style', limpo)
        self.assertNotIn('<img', limpo)
        self.assertIn('<h3>Título</h3>', limpo)
        self.assertIn('<div class="callout"><strong>ok</strong></div>', limpo)
        self.assertIn('<p>linha</p>', limpo)
        self.assertIn('href="https://a.b"', limpo)
        self.assertIn('<span class="sev-alta">Alta</span>', limpo)
        self.assertIn('<td colspan="2">c</td>', limpo)

    def test_tags_nao_fechadas(self):
        self.assertEqual(limpar_html('<p><strong>a'), '<p><strong>a</strong></p>')


class ExportacaoTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nome_empresa='Provedor Teste', cnpj='11.111.111/0001-11',
                                              endereco='Rua 1', email='prov@example.com')
        m = analise.montar_modelo(_inventario(extrair_huawei(HUAWEI_PE)))
        secoes = composicao.gerar_secoes(m)
        for i, s in enumerate(secoes):
            s['id'] = f's{i}'
        self.doc = DocumentoRede.objects.create(
            cliente=self.cliente, titulo='AS-IS Teste', metadados=composicao.metadados_padrao(m, 'Fulano'),
            secoes=secoes)

    def test_numeracao_ignora_anexo(self):
        numeradas = exportacao.secoes_numeradas(self.doc.secoes)
        self.assertEqual(numeradas[0]['numero'], '1')
        self.assertIn('<span class="num">1.1</span>', numeradas[0]['html'])
        self.assertEqual(numeradas[-1]['numero'], '')

    def test_docx_valido(self):
        conteudo = exportacao.gerar_docx(self.doc)
        with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
            xml = z.read('word/document.xml').decode('utf-8')
        self.assertIn('AS-IS DA INFRAESTRUTURA DE REDE', xml)
        self.assertIn('Achados estruturais do AS-IS', xml)
        self.assertIn('AS-IS-001', xml)

    def test_html_de_impressao_escapa_cabecalho(self):
        self.doc.metadados['empresa'] = 'Evil"; } body { display:none } </style><script>'
        html = exportacao.html_documento(self.doc, para_pdf=True)
        self.assertNotIn('</style><script>', html)
        self.assertNotIn('display:none } </style>', html)


class _BaseViews(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.cliente = Cliente.objects.create(nome_empresa='Provedor Teste', cnpj='11.111.111/0001-11',
                                              endereco='Rua 1', email='prov@example.com')
        acesso = Acesso.objects.create(cliente=self.cliente, tipo='NE8K-AFT', host='10.0.0.1', porta=22,
                                       protocolo='SSH', usuario='u', senha='s', backup_habilitado=True)
        rel = 'backups/cliente_x/pe.txt'
        os.makedirs(os.path.join(self.media, 'backups/cliente_x'))
        with open(os.path.join(self.media, rel), 'w') as fh:
            fh.write(HUAWEI_PE)
        BackupLog.objects.create(acesso=acesso, cliente=self.cliente, arquivo_path=rel, status='SUCESSO')

        self.admin = User.objects.create_user('admin', password='x', is_staff=True)
        PerfilUsuario.objects.create(usuario=self.admin, role=PerfilUsuario.ROLE_ADMIN)
        TOTPDevice.objects.create(usuario=self.admin, secret='A' * 32, confirmado=True)
        self.consultor = User.objects.create_user('consultor', password='x', is_staff=True)
        PerfilUsuario.objects.create(usuario=self.consultor, role=PerfilUsuario.ROLE_CONSULTOR)
        TOTPDevice.objects.create(usuario=self.consultor, secret='A' * 32, confirmado=True)

    def post_json(self, url, dados=None):
        return self.client.post(url, data=json.dumps(dados or {}), content_type='application/json')


class ViewsTest(_BaseViews):
    def gerar(self):
        with override_settings(MEDIA_ROOT=self.media):
            r = self.post_json(reverse('projeto_rede:gerar_asis', args=[self.cliente.id]))
        self.assertEqual(r.status_code, 200, r.content)
        return DocumentoRede.objects.get(id=r.json()['id'])

    def test_somente_administrador(self):
        self.client.force_login(self.consultor)
        r = self.post_json(reverse('projeto_rede:gerar_asis', args=[self.cliente.id]))
        self.assertEqual(r.status_code, 403)
        r = self.client.get(reverse('projeto_rede:lista', args=[self.cliente.id]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(DocumentoRede.objects.exists())

    def test_anonimo_recebe_json_401(self):
        r = self.post_json(reverse('projeto_rede:gerar_asis', args=[self.cliente.id]))
        self.assertEqual(r.status_code, 401)
        self.assertFalse(r.json()['ok'])

    def test_admin_ve_tela_de_documentos(self):
        self.client.force_login(self.admin)
        with override_settings(MEDIA_ROOT=self.media):
            r = self.client.get(reverse('projeto_rede:lista', args=[self.cliente.id]))
        self.assertContains(r, 'Gerar AS-IS')

    def test_gerar_editar_revisar_exportar(self):
        self.client.force_login(self.admin)
        doc = self.gerar()
        self.assertEqual(doc.criado_por, self.admin)
        self.assertEqual(doc.revisoes.count(), 1)
        self.assertEqual(doc.coleta['total_com_backup'], 1)
        self.assertIn('RTR-PE-AFT-CEN-01', json.dumps(doc.secoes))

        r = self.client.get(reverse('projeto_rede:editor', args=[doc.id]))
        self.assertContains(r, 'doc-dados')

        secoes = doc.secoes
        secoes[0]['html'] = '<p>Texto <b>revisado</b><script>alert(1)</script></p>'
        secoes.append({'id': 'nova', 'chave': 'invalida', 'titulo': 'Premissas', 'html': '<p>ok</p>'})
        r = self.post_json(reverse('projeto_rede:salvar', args=[doc.id]), {
            'base': doc.atualizado_em.isoformat(), 'status': 'revisao',
            'metadados': {'versao': '1.1', 'responsavel': 'Marcio', 'hack': 'x'}, 'secoes': secoes,
        })
        self.assertEqual(r.status_code, 200, r.content)
        doc.refresh_from_db()
        self.assertEqual((doc.status, doc.versao), ('revisao', '1.1'))
        self.assertEqual(doc.secoes[0]['html'], '<p>Texto <strong>revisado</strong></p>')
        self.assertEqual(doc.secoes[-1]['chave'], '')
        self.assertNotIn('hack', doc.metadados)

        # conflito: base antiga
        r = self.post_json(reverse('projeto_rede:salvar', args=[doc.id]),
                           {'base': '2000-01-01T00:00:00+00:00', 'secoes': doc.secoes})
        self.assertEqual(r.status_code, 409)

        r = self.post_json(reverse('projeto_rede:registrar_revisao', args=[doc.id]), {'motivo': 'Revisão NOC'})
        self.assertTrue(r.json()['ok'])
        self.assertEqual(doc.revisoes.count(), 2)

        with override_settings(MEDIA_ROOT=self.media):
            r = self.post_json(reverse('projeto_rede:regenerar_secao', args=[doc.id]), {'chave': 'controle'})
        self.assertTrue(r.json()['ok'])
        self.assertIn('Objetivo', r.json()['html'])

        r = self.client.get(reverse('projeto_rede:docx', args=[doc.id]))
        self.assertEqual(r.status_code, 200)
        self.assertIn('attachment', r['Content-Disposition'])

        with mock.patch.object(exportacao, 'gerar_pdf', return_value=b'%PDF-1.4'):
            r = self.client.get(reverse('projeto_rede:pdf', args=[doc.id]))
        self.assertEqual((r.status_code, r['Content-Type']), (200, 'application/pdf'))

        r = self.client.get(reverse('projeto_rede:visualizar', args=[doc.id]))
        self.assertContains(r, 'Texto <strong>revisado</strong>', html=False)

    def test_atualizar_mantem_secoes_manuais_e_restaurar(self):
        self.client.force_login(self.admin)
        doc = self.gerar()
        secoes = doc.secoes + [{'id': 'm1', 'chave': '', 'titulo': 'Premissas do cliente', 'html': '<p>manual</p>'}]
        secoes[0]['html'] = '<p>editado</p>'
        self.post_json(reverse('projeto_rede:salvar', args=[doc.id]), {'secoes': secoes})
        with override_settings(MEDIA_ROOT=self.media):
            r = self.post_json(reverse('projeto_rede:regenerar_tudo', args=[doc.id]))
        self.assertTrue(r.json()['ok'])
        doc.refresh_from_db()
        self.assertEqual(doc.secoes[-1]['html'], '<p>manual</p>')
        self.assertNotEqual(doc.secoes[0]['html'], '<p>editado</p>')

        rev = doc.revisoes.filter(motivo__startswith='Antes de atualizar').get()
        r = self.post_json(reverse('projeto_rede:restaurar_revisao', args=[doc.id, rev.id]))
        self.assertTrue(r.json()['ok'])
        doc.refresh_from_db()
        self.assertEqual(doc.secoes[0]['html'], '<p>editado</p>')

    def test_sem_backup_nao_gera(self):
        self.client.force_login(self.admin)
        vazio = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vazio, ignore_errors=True)
        with override_settings(MEDIA_ROOT=vazio):
            r = self.post_json(reverse('projeto_rede:gerar_asis', args=[self.cliente.id]))
        self.assertEqual(r.status_code, 400)
        # arquivo ausente não apaga o registro do backup
        self.assertEqual(BackupLog.objects.count(), 1)

    def test_excluir(self):
        self.client.force_login(self.admin)
        doc = self.gerar()
        r = self.post_json(reverse('projeto_rede:excluir', args=[doc.id]))
        self.assertTrue(r.json()['ok'])
        self.assertFalse(DocumentoRede.objects.exists())
        self.assertFalse(DocumentoRedeRevisao.objects.exists())
