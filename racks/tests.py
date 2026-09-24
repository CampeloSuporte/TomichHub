import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clientes.models import Acesso, Cliente, TopologiaDiagrama
from usuario.models import Instancia, InstanciaFerramenta, PerfilUsuario, TOTPDevice

from . import services
from .models import ConexaoFisica, LinkSemCabo, Rack, RackEquipamento
from .services import ErroRack


def _cliente(nome='Provedor', instancia=None):
    n = Cliente.objects.count() + 11
    return Cliente.objects.create(nome_empresa=nome, cnpj=f'{n}.111.111/0001-11', endereco='Rua 1',
                                  email=f'p{n}@example.com', instancia=instancia)


def _acesso(cliente, tipo, host):
    return Acesso.objects.create(cliente=cliente, tipo=tipo, host=host, porta=22, protocolo='SSH',
                                 usuario='u', senha='s')


class EspacoNoRackTest(TestCase):
    def setUp(self):
        self.cliente = _cliente()
        self.rack = services.criar_rack(self.cliente, {'nome': 'RACK-01', 'altura_u': 10})

    def montar(self, **dados):
        return services.montar_equipamento(self.rack, dados)

    def test_mesmo_u_recusado_com_nome_de_quem_ocupa(self):
        self.montar(tipo='switch', nome='SW-CORE', u_inicial=5)
        with self.assertRaisesMessage(ErroRack, 'SW-CORE'):
            self.montar(tipo='router', nome='RTR', u_inicial=5)

    def test_equipamento_de_2u_colide_com_o_u_de_cima(self):
        self.montar(tipo='switch', nome='SW', u_inicial=6)
        with self.assertRaises(ErroRack):
            self.montar(tipo='olt', nome='OLT', u_inicial=5)  # ocupa U5–U6

    def test_nao_passa_do_topo(self):
        with self.assertRaisesMessage(ErroRack, 'topo do rack'):
            self.montar(tipo='olt', nome='OLT', u_inicial=10)

    def test_passivo_raso_divide_o_u_com_a_face_oposta(self):
        self.montar(tipo='patch_panel', nome='PP', u_inicial=3)              # frente, raso
        pdu = self.montar(tipo='pdu', nome='PDU', u_inicial=3)               # traseira, rasa
        self.assertEqual(pdu.face, 'traseira')
        with self.assertRaises(ErroRack):                                    # ativo ocupa as duas faces
            self.montar(tipo='switch', nome='SW', u_inicial=3)

    def test_sem_u_informado_monta_no_mais_alto_livre(self):
        self.assertEqual(self.montar(tipo='switch', nome='A').u_inicial, 10)
        self.assertEqual(self.montar(tipo='olt', nome='B').u_inicial, 8)

    def test_mover_para_si_mesmo_nao_conflita(self):
        eq = self.montar(tipo='olt', nome='OLT', u_inicial=4)
        services.atualizar_equipamento(eq, {'u_inicial': 5})
        eq.refresh_from_db()
        self.assertEqual((eq.u_inicial, eq.u_final), (5, 6))

    def test_mover_para_rack_de_outro_cliente_recusado(self):
        eq = self.montar(tipo='switch', nome='SW', u_inicial=1)
        alheio = services.criar_rack(_cliente('Outro'), {'nome': 'X'})
        with self.assertRaisesMessage(ErroRack, 'destino'):
            services.atualizar_equipamento(eq, {'rack_id': alheio.id})

    def test_reduzir_altura_nao_corta_equipamento(self):
        self.montar(tipo='switch', nome='SW-TOPO', u_inicial=9)
        with self.assertRaisesMessage(ErroRack, 'SW-TOPO'):
            services.atualizar_rack(self.rack, {'altura_u': 8})

    def test_host_do_crm_montado_uma_vez_so(self):
        acesso = _acesso(self.cliente, 'SW-CORE', '10.0.0.1')
        self.montar(tipo='switch', acesso_id=acesso.id, u_inicial=1)
        with self.assertRaisesMessage(ErroRack, 'já está montado'):
            self.montar(tipo='switch', acesso_id=acesso.id, u_inicial=2)

    def test_host_de_outro_cliente_recusado(self):
        alheio = _acesso(_cliente('Outro'), 'SW', '10.9.9.9')
        with self.assertRaisesMessage(ErroRack, 'não encontrado'):
            self.montar(tipo='switch', acesso_id=alheio.id, u_inicial=1)


class TopologiaParaRackTest(TestCase):
    def setUp(self):
        self.cliente = _cliente()
        self.sw = _acesso(self.cliente, 'SW-CORE', '10.0.0.1')
        self.rtr = _acesso(self.cliente, 'RTR-BORDA', '10.0.0.2')
        dados = {
            'nodes': [
                {'id': f'crm_{self.sw.id}', 'type': 'switch_l3', 'label': 'SW-CORE', 'acesso_id': self.sw.id},
                {'id': f'crm_{self.rtr.id}', 'type': 'router', 'label': 'RTR-BORDA', 'acesso_id': self.rtr.id},
                {'id': 'nolt', 'type': 'olt', 'label': 'OLT desenhada'},
                {'id': 'ninet', 'type': 'internet', 'label': 'Internet'},
            ],
            'links': [
                {'id': 'L1', 'src': f'crm_{self.sw.id}', 'tgt': f'crm_{self.rtr.id}', 'iface': '10g',
                 'iface_a': 'XGE0/0/1', 'iface_b': 'sfp-sfpplus1', 'label': 'P2P-CORE-BORDA'},
                {'id': 'L2', 'src': f'crm_{self.sw.id}', 'tgt': 'nolt', 'iface': '1g',
                 'iface_a': 'GE0/0/5', 'iface_b': 'eth0'},
                {'id': 'L3', 'src': f'crm_{self.rtr.id}', 'tgt': 'ninet', 'iface': '100g'},
            ],
        }
        self.diagrama = TopologiaDiagrama.objects.create(cliente=self.cliente, dados_json=json.dumps(dados))
        self.rack = services.criar_rack(self.cliente, {'nome': 'RACK-01'})

    def links(self):
        return {l['link_id']: l for l in services.links_topologia(self.cliente)}

    def test_enlace_logico_nao_vira_cabo(self):
        self.assertNotIn('L3', self.links())

    def test_situacao_evolui_de_pendente_para_criada(self):
        self.assertEqual(self.links()['L1']['status'], 'pendente')
        self.assertEqual(set(self.links()['L1']['faltando']), {'SW-CORE', 'RTR-BORDA'})
        services.montar_equipamento(self.rack, {'tipo': 'switch', 'acesso_id': self.sw.id, 'u_inicial': 40})
        services.montar_equipamento(self.rack, {'tipo': 'router', 'acesso_id': self.rtr.id, 'u_inicial': 38})
        self.assertEqual(self.links()['L1']['status'], 'pronta')

        c = services.criar_conexao_do_link(self.cliente, 'L1')
        self.assertEqual((c.porta_a, c.porta_b), ('XGE0/0/1', 'sfp-sfpplus1'))
        self.assertEqual((c.meio, c.conector, c.identificacao), ('fibra_sm', 'LC', 'P2P-CORE-BORDA'))
        self.assertEqual(c.diagrama, self.diagrama)
        self.assertEqual(self.links()['L1']['status'], 'criada')
        with self.assertRaisesMessage(ErroRack, 'já tem conexão'):
            services.criar_conexao_do_link(self.cliente, 'L1')

    def test_node_desenhado_a_mao_casa_pelo_id(self):
        services.montar_equipamento(self.rack, {'tipo': 'switch', 'acesso_id': self.sw.id, 'u_inicial': 40})
        services.montar_equipamento(self.rack, {'tipo': 'olt', 'topologia_node_id': 'nolt', 'u_inicial': 30})
        c = services.criar_conexao_do_link(self.cliente, 'L2', {'porta_b': 'uplink1'})
        self.assertEqual((c.meio, c.porta_b), ('utp', 'uplink1'))

    def test_porta_ja_cabeada_recusada(self):
        sw = services.montar_equipamento(self.rack, {'tipo': 'switch', 'acesso_id': self.sw.id, 'u_inicial': 40})
        rtr = services.montar_equipamento(self.rack, {'tipo': 'router', 'acesso_id': self.rtr.id, 'u_inicial': 38})
        services.criar_conexao(self.cliente, {'ponta_a_id': sw.id, 'porta_a': 'xge0/0/1 ',
                                              'ponta_b_id': rtr.id, 'porta_b': 'ether9'})
        with self.assertRaisesMessage(ErroRack, 'já tem cabo'):
            services.criar_conexao_do_link(self.cliente, 'L1')

    def test_dispositivos_junta_topologia_e_crm_sem_repetir(self):
        _acesso(self.cliente, 'OLT-NOVA', '10.0.0.9')  # host do CRM fora do desenho
        chaves = [d['chave'] for d in services.dispositivos(self.cliente)]
        self.assertEqual(len(chaves), len(set(chaves)))
        self.assertIn('nnolt', chaves)
        self.assertNotIn('nninet', chaves)
        self.assertEqual(len(chaves), 4)  # SW, RTR, OLT desenhada, OLT-NOVA

    def test_posicoes_por_acesso_e_por_node(self):
        services.montar_equipamento(self.rack, {'tipo': 'switch', 'acesso_id': self.sw.id, 'u_inicial': 40})
        services.montar_equipamento(self.rack, {'tipo': 'olt', 'topologia_node_id': 'nolt', 'u_inicial': 30})
        p = services.posicoes(self.cliente)
        self.assertEqual(p['por_acesso'][str(self.sw.id)]['u'], 40)
        self.assertEqual((p['por_node']['nolt']['u'], p['por_node']['nolt']['u_final']), (30, 31))
        self.assertEqual(p['por_node']['nolt']['rack'], 'RACK-01')
        self.assertNotIn(str(self.rtr.id), p['por_acesso'])

    def test_excluir_equipamento_leva_os_cabos(self):
        sw = services.montar_equipamento(self.rack, {'tipo': 'switch', 'acesso_id': self.sw.id, 'u_inicial': 40})
        services.montar_equipamento(self.rack, {'tipo': 'router', 'acesso_id': self.rtr.id, 'u_inicial': 38})
        services.criar_conexao_do_link(self.cliente, 'L1')
        sw.delete()
        self.assertFalse(ConexaoFisica.objects.exists())
        self.assertEqual(self.links()['L1']['status'], 'pendente')


class SincronizacaoTest(TestCase):
    """Cabos seguindo os enlaces do mapa (services.sincronizar_cabos + signal)."""

    def setUp(self):
        self.cliente = _cliente()
        self.sw = _acesso(self.cliente, 'SW-AGG', '10.0.0.1')
        self.rtr = _acesso(self.cliente, 'RTR', '10.0.0.2')
        self.dados = {
            'nodes': [{'id': f'crm_{self.sw.id}', 'type': 'switch_l2', 'label': 'SW-AGG', 'acesso_id': self.sw.id},
                      {'id': f'crm_{self.rtr.id}', 'type': 'router', 'label': 'RTR', 'acesso_id': self.rtr.id}],
            'links': [{'id': 'L1', 'src': f'crm_{self.sw.id}', 'tgt': f'crm_{self.rtr.id}', 'iface': '10g',
                       'iface_a': 'XGE0/0/1', 'iface_b': 'sfp1', 'label': 'P2P'}],
        }
        self.diagrama = TopologiaDiagrama.objects.create(cliente=self.cliente, dados_json=json.dumps(self.dados))
        self.rack = services.criar_rack(self.cliente, {'nome': 'R1'})

    def montar_os_dois(self):
        a = services.montar_equipamento(self.rack, {'tipo': 'switch', 'acesso_id': self.sw.id})
        b = services.montar_equipamento(self.rack, {'tipo': 'router', 'acesso_id': self.rtr.id})
        return a, b

    def salvar_mapa(self):
        self.diagrama.dados_json = json.dumps(self.dados)
        self.diagrama.save()  # dispara o signal

    def cabo(self):
        return ConexaoFisica.objects.get(topologia_link_id='L1')

    def test_cria_cabo_quando_as_duas_pontas_estao_montadas(self):
        services.montar_equipamento(self.rack, {'tipo': 'switch', 'acesso_id': self.sw.id})
        self.assertEqual(services.sincronizar_cabos(self.cliente)['criados'], 0)
        services.montar_equipamento(self.rack, {'tipo': 'router', 'acesso_id': self.rtr.id})
        self.assertEqual(services.sincronizar_cabos(self.cliente)['criados'], 1)
        c = self.cabo()
        self.assertTrue(c.sincronizado)
        self.assertEqual((c.porta_a, c.porta_b, c.meio, c.identificacao), ('XGE0/0/1', 'sfp1', 'fibra_sm', 'P2P'))
        self.assertEqual(services.sincronizar_cabos(self.cliente),
                         {'criados': 0, 'atualizados': 0, 'removidos': 0, 'bloqueados': 0})

    def test_salvar_topologia_cria_e_acompanha_o_enlace(self):
        self.montar_os_dois()
        self.salvar_mapa()
        self.assertEqual(self.cabo().porta_a, 'XGE0/0/1')
        self.dados['links'][0].update(iface_a='XGE0/0/2', iface='1g')
        self.salvar_mapa()
        c = self.cabo()
        self.assertEqual((c.porta_a, c.meio, c.conector), ('XGE0/0/2', 'utp', 'RJ45'))

    def test_enlace_apagado_do_mapa_leva_o_cabo_sincronizado(self):
        self.montar_os_dois()
        self.salvar_mapa()
        self.dados['links'] = []
        self.salvar_mapa()
        self.assertFalse(ConexaoFisica.objects.exists())

    def test_editado_a_mao_para_de_seguir_o_mapa_e_fica_orfao(self):
        self.montar_os_dois()
        self.salvar_mapa()
        services.atualizar_conexao(self.cabo(), {'comprimento_m': '3'})
        self.assertFalse(self.cabo().sincronizado)
        self.dados['links'][0]['iface_a'] = 'XGE0/0/9'
        self.salvar_mapa()
        self.assertEqual(self.cabo().porta_a, 'XGE0/0/1')
        self.dados['links'] = []
        self.salvar_mapa()
        self.assertTrue(services.estado(self.cliente)['conexoes'][0]['orfao'])

    def test_cabo_excluido_nao_volta_ate_religar(self):
        self.montar_os_dois()
        self.salvar_mapa()
        services.excluir_conexao(self.cabo())
        self.salvar_mapa()
        self.assertFalse(ConexaoFisica.objects.exists())
        self.assertEqual(services.links_topologia(self.cliente)[0]['status'], 'ignorada')
        services.religar_link(self.cliente, 'L1')
        services.sincronizar_cabos(self.cliente)
        self.assertTrue(self.cabo().sincronizado)
        self.assertFalse(LinkSemCabo.objects.exists())

    def test_porta_ja_cabeada_bloqueia_e_explica(self):
        a, b = self.montar_os_dois()
        services.criar_conexao(self.cliente, {'ponta_a_id': a.id, 'porta_a': 'xge0/0/1', 'ponta_b_id': b.id, 'porta_b': 'x'})
        self.assertEqual(services.sincronizar_cabos(self.cliente)['bloqueados'], 1)
        link = services.links_topologia(self.cliente)[0]
        self.assertEqual(link['status'], 'pronta')
        self.assertIn('já tem cabo', link['bloqueio'])

    def test_desmontar_ponta_remove_cabo_e_remontar_devolve(self):
        a, _ = self.montar_os_dois()
        services.sincronizar_cabos(self.cliente)
        a.delete()
        self.assertFalse(ConexaoFisica.objects.exists())
        services.montar_equipamento(self.rack, {'tipo': 'switch', 'acesso_id': self.sw.id})
        services.sincronizar_cabos(self.cliente)
        self.assertEqual(self.cabo().porta_a, 'XGE0/0/1')


class ApiTest(TestCase):
    def setUp(self):
        self.inst = Instancia.objects.create(nome='Revenda')
        InstanciaFerramenta.objects.create(instancia=self.inst, ferramenta='topologia', habilitado=True)
        self.cliente = _cliente(instancia=self.inst)
        self.admin = self._usuario('admin', PerfilUsuario.ROLE_ADMIN)
        self.consultor = self._usuario('consultor', PerfilUsuario.ROLE_CONSULTOR, self.inst)
        outra = Instancia.objects.create(nome='Outra')
        InstanciaFerramenta.objects.create(instancia=outra, ferramenta='topologia', habilitado=True)
        self.intruso = self._usuario('intruso', PerfilUsuario.ROLE_CONSULTOR, outra)

    def _usuario(self, nome, role, instancia=None):
        u = User.objects.create_user(nome, password='x', is_staff=True)
        PerfilUsuario.objects.create(usuario=u, role=role, instancia=instancia)
        TOTPDevice.objects.create(usuario=u, secret='A' * 32, confirmado=True)
        return u

    def post(self, nome, args, dados):
        return self.client.post(reverse(f'racks:{nome}', args=args), data=json.dumps(dados),
                                content_type='application/json')

    def test_anonimo_recebe_json_401(self):
        r = self.post('rack_criar', [self.cliente.id], {'nome': 'R'})
        self.assertEqual(r.status_code, 401)
        self.assertFalse(r.json()['ok'])

    def test_consultor_de_outra_instancia_bloqueado(self):
        self.client.force_login(self.intruso)
        r = self.post('rack_criar', [self.cliente.id], {'nome': 'R'})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Rack.objects.exists())

    def test_fluxo_consultor_rack_equipamento_e_erro_legivel(self):
        self.client.force_login(self.consultor)
        r = self.post('rack_criar', [self.cliente.id], {'nome': 'RACK-POP', 'altura_u': 12})
        self.assertEqual(r.status_code, 200, r.content)
        rack_id = r.json()['rack_id']
        r = self.post('equipamento_criar', [rack_id], {'tipo': 'olt', 'nome': 'OLT-1', 'u_inicial': 1})
        self.assertEqual(r.json()['estado']['racks'][0]['equipamentos'][0]['altura_u'], 2)
        r = self.post('equipamento_criar', [rack_id], {'tipo': 'switch', 'nome': 'SW', 'u_inicial': 2})
        self.assertEqual(r.status_code, 400)
        self.assertIn('OLT-1', r.json()['erro'])
        self.assertEqual(RackEquipamento.objects.count(), 1)

    def test_montar_pela_api_sincroniza_e_relata(self):
        a = _acesso(self.cliente, 'SW', '10.0.0.1')
        b = _acesso(self.cliente, 'RTR', '10.0.0.2')
        TopologiaDiagrama.objects.create(cliente=self.cliente, dados_json=json.dumps({
            'nodes': [{'id': f'crm_{a.id}', 'type': 'switch_l2', 'acesso_id': a.id},
                      {'id': f'crm_{b.id}', 'type': 'router', 'acesso_id': b.id}],
            'links': [{'id': 'L9', 'src': f'crm_{a.id}', 'tgt': f'crm_{b.id}', 'iface': '1g'}]}))
        self.client.force_login(self.consultor)
        rack_id = self.post('rack_criar', [self.cliente.id], {'nome': 'R'}).json()['rack_id']
        self.post('equipamento_criar', [rack_id], {'tipo': 'switch', 'acesso_id': a.id})
        r = self.post('equipamento_criar', [rack_id], {'tipo': 'router', 'acesso_id': b.id})
        self.assertEqual(r.json()['sync']['criados'], 1)
        cid = r.json()['estado']['conexoes'][0]['id']
        r = self.post('conexao_excluir', [cid], {})
        self.assertEqual(r.json()['estado']['links'][0]['status'], 'ignorada')
        r = self.post('link_religar', [self.cliente.id], {'link_id': 'L9'})
        self.assertEqual(r.json()['estado']['links'][0]['status'], 'criada')

    def test_tela_abre_para_admin(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('racks:tela', args=[self.cliente.id]))
        self.assertContains(r, 'rack_builder.js')

    def test_posicoes_bloqueado_para_outra_instancia(self):
        self.client.force_login(self.intruso)
        r = self.client.get(reverse('racks:posicoes', args=[self.cliente.id]))
        self.assertEqual(r.status_code, 403)

    def test_link_endpoint_sem_enlace(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('racks:link', args=[self.cliente.id]), {'link': 'nao-existe'})
        self.assertEqual(r.json(), {'ok': True, 'link': None, 'conexao': None})

    def test_topologia_hosts_mantem_mapeamento(self):
        from funcao_equipamento.models import Funcao_equipamento
        _acesso(self.cliente, 'BNG-01', '10.0.0.1')
        a = _acesso(self.cliente, 'caixa', '10.0.0.2')
        a.funcao = Funcao_equipamento.objects.create(descricao='OLT GPON')
        a.save()
        self.client.force_login(self.admin)
        r = self.client.get(reverse('topologia_hosts', args=[self.cliente.id]))
        tipos = {h['label']: h['tipo'] for h in r.json()['hosts']}
        self.assertEqual(tipos, {'BNG-01': 'router', 'caixa': 'olt'})
