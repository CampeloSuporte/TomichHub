"""Agent NOC × grafo de conhecimento (abordagem Graphify — home/agent_grafo.py).

Contexto de backup no formato real gerado por clientes/ipam_views.py.
"""
import asyncio
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from home import agent_engine as ae
from home import agent_grafo as ag

CTX_BRAS = """Fabricante: huawei | Hostname: BNG-SINOP

Protocolos configurados:

BGP (ASN local: 267409):
  Mapeamento de sessões (descrição → IP → comando de verificação):
  Peer 198.18.103.82 AS267409 — CGNAT-VROUTER
    → display bgp peer 198.18.103.82  [verificar: CGNAT-VROUTER]
  Peer 198.19.255.5 AS267409
    → display bgp peer 198.19.255.5  [verificar: 198.19.255.5]

OSPF (instância 1 | router-id: 198.19.255.2 | áreas: 0.0.0.0):
  → display ospf peer

Interfaces com endereço IP:
  100GE0/5/0.1001  IP=198.18.103.81/30  VLAN=1001  desc="P2P-CGNAT-VROUTER"
    → display interface 100GE0/5/0.1001
  l3 OSPF-SW-HU-JURUENA  IP=198.18.101.85/30
  LoopBack0  IP=198.19.255.2/32

Demais interfaces (sem IP): GigabitEthernet0/5/4, XGigabitEthernet0/0/2(SW-HU-ALMAS-P1), Eth-Trunk1(LINK (A,B)) ...+12 mais
"""

CTX_CGNAT = """Fabricante: hillstone

Interfaces com endereço IP:
  eth-trunk1.1001  IP=198.18.103.82/30  VLAN=1001  desc="DOWNLINK"
"""


def _acesso(id_, tipo, host, ctx='', funcao='', fab='huawei', notas=''):
    return SimpleNamespace(
        id=id_, tipo=tipo, host=host, porta=22, protocolo='SSH', contexto_backup=ctx, notas=notas,
        modelo=SimpleNamespace(nome='MOD', fabricante=fab),
        funcao=SimpleNamespace(descricao=funcao) if funcao else None,
    )


def _grafo():
    cliente = SimpleNamespace(id=1, nome_empresa='Teste')
    acessos = [
        _acesso(10, 'BRAS SINOP', '198.19.255.2', CTX_BRAS, 'BRAS'),
        _acesso(11, 'CGNAT SINOP', '45.0.0.1', CTX_CGNAT, 'CGNAT', fab='hillstone'),
        _acesso(12, 'OLT HU TANGARA', '10.0.0.9', '', 'OLT'),
    ]
    with mock.patch('clientes.models.TopologiaDiagrama.objects') as topo:
        topo.filter.return_value.only.return_value = [SimpleNamespace(
            nome='BACKBONE',
            dados_json='{"nodes":[{"id":"crm_10","acesso_id":10},{"id":"crm_12"}],'
                       '"links":[{"src":"crm_10","tgt":"crm_12","iface_a":"GE0/5/4","iface_b":"0/1"}]}',
        )]
        return ag.construir_grafo(cliente, acessos)


class ParseContextoTest(SimpleTestCase):
    def test_extrai_hostname_asn_peers_e_interfaces(self):
        r = ag.parse_contexto(CTX_BRAS)
        self.assertEqual(r['hostname'], 'BNG-SINOP')
        self.assertEqual(r['asn'], '267409')
        self.assertEqual([p['ip'] for p in r['peers']], ['198.18.103.82', '198.19.255.5'])
        self.assertEqual(r['peers'][0]['desc'], 'CGNAT-VROUTER')
        nomes = {i['nome']: i for i in r['ifaces']}
        self.assertEqual(nomes['100GE0/5/0.1001']['vlan'], '1001')
        self.assertEqual(nomes['100GE0/5/0.1001']['desc'], 'P2P-CGNAT-VROUTER')
        # nome de interface com espaço (Datacom "l3 ...")
        self.assertEqual(nomes['l3 OSPF-SW-HU-JURUENA']['ip'], '198.18.101.85/30')
        # "Demais interfaces": descrição com vírgula dentro do parêntese e "...+N mais"
        self.assertEqual(nomes['XGigabitEthernet0/0/2']['desc'], 'SW-HU-ALMAS-P1')
        self.assertEqual(nomes['Eth-Trunk1']['desc'], 'LINK (A,B)')
        self.assertFalse(any('mais' in n for n in nomes))

    def test_contexto_vazio_ou_lixo_nao_quebra(self):
        self.assertEqual(ag.parse_contexto('')['ifaces'], [])
        ag.parse_contexto('qualquer coisa\n  sem formato  IP=\n')


class GrafoTest(SimpleTestCase):
    def test_arestas_inferidas_e_extraidas(self):
        g = _grafo()
        rels = {(e['source'], e['target'], e['relation']) for e in g.arestas}
        # mesma /30 → enlace p2p entre as interfaces dos dois hosts
        self.assertIn(('if:10:100GE0/5/0.1001', 'if:11:eth-trunk1.1001', 'enlace_p2p'), rels)
        # peer BGP cujo IP é de outro host → e_o_host
        self.assertIn(('peer:10:198.18.103.82', 'host:11', 'e_o_host'), rels)
        # enlace desenhado na topologia (node sem acesso_id usa o id crm_<n>)
        self.assertIn(('host:10', 'host:12', 'enlace_topologia'), rels)

    def test_consulta_por_descricao_ip_e_nome(self):
        g = _grafo()
        r = g.consultar('cgnat vrouter')
        self.assertIn('BGP CGNAT-VROUTER = 198.18.103.82', r)
        self.assertIn('HOST ID 11: CGNAT SINOP', r)
        r = g.consultar('198.18.103.82')
        self.assertIn('eth-trunk1.1001', r)
        # termo com espaço casa descrição com hífen ("almas p1" → SW-HU-ALMAS-P1)
        self.assertIn('XGigabitEthernet0/0/2', g.consultar('almas p1'))
        # acento/caixa não importam
        self.assertIn('OLT HU TANGARA', g.consultar('olt tangará'))

    def test_consulta_respeita_orcamento(self):
        g = _grafo()
        r = g.consultar('sinop', orcamento_tokens=300)
        self.assertLessEqual(len(r), 300 * 3 + 200)

    def test_sem_resultado(self):
        self.assertIn('Nada no grafo', _grafo().consultar('zzzinexistente'))

    def test_detalhe_host_traz_backup_e_vizinhos(self):
        g = _grafo()
        r = g.detalhe_host(10, CTX_BRAS)
        self.assertIn('[Configuração conhecida do backup]', r)
        self.assertIn('enlace_p2p', r)
        self.assertIn('enlace_topologia', r)
        self.assertIn('fetch_host_config', g.detalhe_host(12, ''))

    def test_exporta_formato_graphify(self):
        d = _grafo().exportar_graphify_json()
        self.assertIn('nodes', d)
        self.assertIn('links', d)
        self.assertTrue(all({'id', 'label', 'file_type', 'source_file'} <= set(n) for n in d['nodes']))
        self.assertTrue(all({'source', 'target', 'relation', 'confidence'} <= set(e) for e in d['links']))


class LinhaCompactaTest(SimpleTestCase):
    def test_notas_vao_inteiras(self):
        a = _acesso(5, 'SW X', '1.1.1.1', 'ctx', 'SWITCH', notas='linha 1\nlinha 2')
        l = ag.linha_host_compacta(a)
        self.assertIn('ID 5 | SW X | 1.1.1.1:22 | SSH', l)
        self.assertIn('backup✓', l)
        self.assertIn('linha 2', l)


class EngineGrafoTest(SimpleTestCase):
    """Integração com o motor sem banco: sessão e config falsas."""

    def _engine(self, acessos, ligado=True):
        eng = ae.AgentNOCEngine(0, canal='whatsapp')
        cliente = SimpleNamespace(id=1, nome_empresa='Teste', cidade='', estado='', notas='')
        eng._sessao = SimpleNamespace(wa_grupo_id=None, wa_grupo=None, cliente=cliente, cliente_id=1)
        eng._config = SimpleNamespace(grafo_contexto_ativo=ligado)
        return eng

    def _prompt(self, eng, acessos):
        qs = mock.MagicMock()
        qs.filter.return_value.select_related.return_value.only.return_value = acessos
        kb = mock.MagicMock()
        kb.filter.return_value.filter.return_value.order_by.return_value = []
        with mock.patch('clientes.models.Acesso.objects', qs), \
             mock.patch('clientes.models.AgentKnowledge.objects', kb), \
             mock.patch('clientes.models.TopologiaDiagrama.objects') as topo:
            topo.filter.return_value.only.return_value = []
            topo.filter.return_value.aggregate.return_value = {'m': None, 'c': 0}
            ag._cache.clear()
            return asyncio.run(eng._build_system_prompt())

    def _muitos(self, n=40):
        return [_acesso(100 + i, f'SW-{i}', f'10.0.0.{i}', CTX_BRAS, 'SWITCH') for i in range(n)]

    def test_cliente_grande_recebe_prompt_compacto(self):
        acessos = self._muitos()
        eng = self._engine(acessos)
        p = self._prompt(eng, acessos)
        self.assertTrue(eng.modo_grafo)
        self.assertIn('## Grafo de conhecimento da rede', p)
        self.assertNotIn('[Configuração conhecida do backup]\n', p.split('## Como usar')[0])
        antes, depois = eng.prompt_hosts_chars
        self.assertLess(depois, antes / 2)

    def test_chave_desligada_mantem_prompt_completo(self):
        acessos = self._muitos()
        eng = self._engine(acessos, ligado=False)
        p = self._prompt(eng, acessos)
        self.assertFalse(eng.modo_grafo)
        self.assertNotIn('## Grafo de conhecimento da rede', p)
        self.assertIn('[Configuração conhecida do backup]', p)

    def test_cliente_pequeno_nao_muda(self):
        acessos = self._muitos(2)
        eng = self._engine(acessos)
        p = self._prompt(eng, acessos)
        self.assertFalse(eng.modo_grafo)
        self.assertIn('[Configuração conhecida do backup]', p)

    def test_falha_no_grafo_volta_ao_prompt_completo(self):
        acessos = self._muitos()
        eng = self._engine(acessos)
        with mock.patch('home.agent_grafo.grafo_do_cliente', side_effect=RuntimeError('boom')):
            p = self._prompt(eng, acessos)
        self.assertFalse(eng.modo_grafo)
        self.assertIn('[Configuração conhecida do backup]', p)

    def test_tools_registradas_nos_tres_provedores(self):
        nomes_claude = {t['name'] for t in ae.TOOLS_DEFINITION}
        nomes_openai = {t['function']['name'] for t in ae.TOOLS_OPENAI}
        for nome in ('consultar_grafo', 'detalhe_host'):
            self.assertIn(nome, nomes_claude)
            self.assertIn(nome, nomes_openai)
            self.assertIn(nome, ae._HERMES_PROTOCOLO_TOOLS)
        bloco = '```tool\n{"name": "consultar_grafo", "arguments": {"pergunta": "x"}}\n```'
        self.assertEqual(ae._hermes_extrair_tools(bloco), [('consultar_grafo', {'pergunta': 'x'})])

    def test_consultar_grafo_sessao_global_nao_quebra(self):
        eng = ae.AgentNOCEngine(0, canal='whatsapp')
        eng._sessao = SimpleNamespace(wa_grupo_id=1, wa_grupo=SimpleNamespace(acesso_global=True),
                                      cliente=None, cliente_id=None)
        r = asyncio.run(eng._dispatch_tool('consultar_grafo', {'pergunta': 'x'}))
        self.assertIn('list_hosts', r)
