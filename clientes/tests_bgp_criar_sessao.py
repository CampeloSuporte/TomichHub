from unittest import mock

from django.test import SimpleTestCase

from clientes.backup_parser import parse_cisco, _extrair_prefix_lists_e_policies_cisco


CONFIG_CISCO_EXEMPLO = """
router bgp 268080
 bgp log-neighbor-changes
 neighbor 2804:3360:0:29::3D remote-as 262725
 neighbor 2804:3360:0:29::3D description UPSTREAM-CONECT-V6
 neighbor 172.16.8.1 remote-as 262725
 neighbor 172.16.8.1 description UPSTREAM-CONECT-V4
!
address-family ipv4
 neighbor 172.16.8.1 activate
 neighbor 172.16.8.1 send-community both
 neighbor 172.16.8.1 route-map RM-PEER-CONECT-V4-IN in
 neighbor 172.16.8.1 route-map RM-PEER-CONECT-V4-OUT out
!
address-family ipv6
 neighbor 2804:3360:0:29::3D activate
 neighbor 2804:3360:0:29::3D route-map RM-PEER-CONECT-V6-IN in
 neighbor 2804:3360:0:29::3D route-map RM-PEER-CONECT-V6-OUT out
!
ip prefix-list PL-ORIGIN-45.169.6.0_24 seq 5 permit 45.169.6.0/24
ip prefix-list PL-DEFAULT-ROUTE seq 5 permit 0.0.0.0/0
!
route-map RM-PEER-CONECT-V4-OUT permit 10
 match ip address prefix-list PL-ORIGIN-45.169.6.0_24
route-map RM-PEER-CONECT-V4-IN permit 10
 match ip address prefix-list PL-DEFAULT-ROUTE
"""


class ExtrairPrefixListsEPoliciesCiscoTest(SimpleTestCase):
    def test_extrai_prefix_lists_e_route_maps(self):
        prefix_lists, policies = _extrair_prefix_lists_e_policies_cisco(CONFIG_CISCO_EXEMPLO)

        self.assertEqual(
            prefix_lists['PL-ORIGIN-45.169.6.0_24'],
            [{'acao': 'permit', 'prefixo': '45.169.6.0/24', 'len_min': 24, 'len_max': 24, 'seq': 5}],
        )
        self.assertEqual(
            prefix_lists['PL-DEFAULT-ROUTE'],
            [{'acao': 'permit', 'prefixo': '0.0.0.0/0', 'len_min': 0, 'len_max': 0, 'seq': 5}],
        )
        self.assertEqual(policies['RM-PEER-CONECT-V4-OUT'][0]['prefix_lists'], ['PL-ORIGIN-45.169.6.0_24'])
        self.assertEqual(policies['RM-PEER-CONECT-V4-OUT'][0]['acao'], 'accept')
        self.assertEqual(policies['RM-PEER-CONECT-V4-IN'][0]['prefix_lists'], ['PL-DEFAULT-ROUTE'])

    def test_parse_cisco_continua_igual_depois_da_extracao(self):
        """Regressão: parse_cisco tem que devolver EXATAMENTE o mesmo
        prefix_lists/policies de antes do refactor (só muda ONDE o
        código mora, não o comportamento)."""
        resultado = parse_cisco(CONFIG_CISCO_EXEMPLO, nome_equip='teste')
        prefix_lists_direto, policies_direto = _extrair_prefix_lists_e_policies_cisco(CONFIG_CISCO_EXEMPLO)
        self.assertEqual(resultado['prefix_lists'], prefix_lists_direto)
        self.assertEqual(resultado['policies'], policies_direto)


from clientes.bgp_actions import AcaoBgpNaoSuportada, comandos_criar_sessao


DADOS_SNAPSHOT_BASE = {
    'sessoes': [
        {'peer_ip': '10.0.0.1', 'peer_as': '65000', 'as_local': '268080',
         'nome': '10.0.0.1', 'descricao': 'EXISTENTE', 'habilitada': True,
         'policy_in': 'RM-OUTRO-IN', 'policy_out': 'RM-OUTRO-OUT'},
    ],
    'prefix_lists': {
        'PL-DEFAULT-ROUTE': [{'acao': 'permit', 'prefixo': '0.0.0.0/0', 'len_min': 0, 'len_max': 0, 'seq': 5}],
    },
    'policies': {
        'RM-OUTRO-OUT': [{'ordem': 10, 'prefix_lists': ['PL-DEFAULT-ROUTE'], 'acao': 'accept', 'prepend': 0, 'extra': {}}],
    },
}


class ComandosCriarSessaoTest(SimpleTestCase):
    def test_gera_comandos_v4_e_v6_reproduzindo_exemplo_real(self):
        """Regressão do exemplo real fornecido pelo usuário: upstream
        CONECT, IPv4 com prefix-list NOVA (PL-ORIGIN-...) no OUT e
        reaproveitamento de PL-DEFAULT-ROUTE no IN; IPv6 com prefix-list
        nova em ambas direções (o exemplo original não mostra os
        route-maps V6 — omitidos por brevidade no texto original — mas
        uma sessão nova de verdade precisa defini-los)."""
        params = {
            'tipo_peer': 'upstream',
            'sufixo': 'CONECT',
            'afs': [
                {
                    'af': 'ipv4', 'peer_ip': '172.16.8.1', 'remote_as': '262725',
                    'pl_in': {'modo': 'existente', 'nome': 'PL-DEFAULT-ROUTE'},
                    'pl_out': {'modo': 'nova', 'cidr': '45.169.6.0/24'},
                },
                {
                    'af': 'ipv6', 'peer_ip': '2804:3360:0:29::3d', 'remote_as': '262725',
                    'pl_in': {'modo': 'existente', 'nome': 'PL-DEFAULT-ROUTE'},
                    'pl_out': {'modo': 'nova', 'cidr': '2804:3360:6::/48'},
                },
            ],
        }
        comandos = comandos_criar_sessao('cisco', DADOS_SNAPSHOT_BASE, params)

        self.assertEqual(comandos[0], 'router bgp 268080')
        self.assertIn('neighbor 172.16.8.1 remote-as 262725', comandos)
        self.assertIn('neighbor 172.16.8.1 description UPSTREAM-CONECT-V4', comandos)
        self.assertIn('neighbor 172.16.8.1 shutdown', comandos)
        self.assertIn('neighbor 2804:3360:0:29::3d remote-as 262725', comandos)
        self.assertIn('neighbor 2804:3360:0:29::3d description UPSTREAM-CONECT-V6', comandos)
        self.assertIn('neighbor 2804:3360:0:29::3d shutdown', comandos)

        # prefix-list/route-map novos (só o OUT de cada AF, já que o IN reaproveita PL-DEFAULT-ROUTE)
        self.assertIn('ip prefix-list PL-ORIGIN-45.169.6.0_24 seq 10 permit 45.169.6.0/24', comandos)
        self.assertIn('route-map RM-PEER-CONECT-V4-OUT permit 10', comandos)
        self.assertIn(' match ip address prefix-list PL-ORIGIN-45.169.6.0_24', comandos)
        self.assertIn('ipv6 prefix-list PL-ORIGIN-2804:3360:6::_48 seq 10 permit 2804:3360:6::/48', comandos)
        self.assertIn('route-map RM-PEER-CONECT-V6-OUT permit 10', comandos)
        self.assertIn(' match ipv6 address prefix-list PL-ORIGIN-2804:3360:6::_48', comandos)

        # IN reaproveitado nunca gera prefix-list nova nem toca em PL-DEFAULT-ROUTE
        self.assertNotIn('ip prefix-list PL-DEFAULT-ROUTE seq 10 permit 0.0.0.0/0', comandos)
        self.assertIn('route-map RM-PEER-CONECT-V4-IN permit 10', comandos)
        self.assertIn(' match ip address prefix-list PL-DEFAULT-ROUTE', comandos)

        # address-family blocks
        self.assertIn('address-family ipv4', comandos)
        self.assertIn(' neighbor 172.16.8.1 activate', comandos)
        self.assertIn(' neighbor 172.16.8.1 send-community both', comandos)
        self.assertIn(' neighbor 172.16.8.1 route-map RM-PEER-CONECT-V4-IN in', comandos)
        self.assertIn(' neighbor 172.16.8.1 route-map RM-PEER-CONECT-V4-OUT out', comandos)
        self.assertIn('address-family ipv6', comandos)
        self.assertIn(' neighbor 2804:3360:0:29::3d send-community both', comandos)

        # ordem: peer defs -> prefix-list/route-map novos -> address-family blocks
        idx_neighbor = comandos.index('neighbor 172.16.8.1 remote-as 262725')
        idx_pl = comandos.index('ip prefix-list PL-ORIGIN-45.169.6.0_24 seq 10 permit 45.169.6.0/24')
        idx_af = comandos.index('address-family ipv4')
        self.assertLess(idx_neighbor, idx_pl)
        self.assertLess(idx_pl, idx_af)

    def test_downstream_usa_convencao_pl_cliente_no_in(self):
        params = {
            'tipo_peer': 'downstream',
            'sufixo': 'CLIENTEX',
            'afs': [{
                'af': 'ipv4', 'peer_ip': '192.0.2.10', 'remote_as': '65010',
                'pl_in': {'modo': 'nova', 'cidr': '203.0.113.0/24'},
                'pl_out': {'modo': 'existente', 'nome': 'PL-DEFAULT-ROUTE'},
            }],
        }
        comandos = comandos_criar_sessao('cisco', DADOS_SNAPSHOT_BASE, params)
        self.assertIn('ip prefix-list PL-CLIENTE-CLIENTEX-203.0.113.0_24 seq 10 permit 203.0.113.0/24', comandos)
        self.assertIn('neighbor 192.0.2.10 description DOWNSTREAM-CLIENTEX-V4', comandos)
        self.assertIn('neighbor 192.0.2.10 shutdown', comandos)

    def test_recusa_vendor_nao_cisco(self):
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_criar_sessao('huawei', DADOS_SNAPSHOT_BASE, {'tipo_peer': 'upstream', 'sufixo': 'X', 'afs': []})

    def test_recusa_peer_ip_duplicado(self):
        params = {
            'tipo_peer': 'upstream', 'sufixo': 'X',
            'afs': [{
                'af': 'ipv4', 'peer_ip': '10.0.0.1', 'remote_as': '65000',
                'pl_in': {'modo': 'existente', 'nome': 'PL-DEFAULT-ROUTE'},
                'pl_out': {'modo': 'existente', 'nome': 'PL-DEFAULT-ROUTE'},
            }],
        }
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_criar_sessao('cisco', DADOS_SNAPSHOT_BASE, params)

    def test_recusa_nome_route_map_colidente(self):
        dados = {**DADOS_SNAPSHOT_BASE, 'policies': {
            **DADOS_SNAPSHOT_BASE['policies'],
            'RM-PEER-CONECT-V4-IN': [],
        }}
        params = {
            'tipo_peer': 'upstream', 'sufixo': 'CONECT',
            'afs': [{
                'af': 'ipv4', 'peer_ip': '172.16.8.1', 'remote_as': '262725',
                'pl_in': {'modo': 'existente', 'nome': 'PL-DEFAULT-ROUTE'},
                'pl_out': {'modo': 'existente', 'nome': 'PL-DEFAULT-ROUTE'},
            }],
        }
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_criar_sessao('cisco', dados, params)

    def test_recusa_cidr_de_familia_errada(self):
        params = {
            'tipo_peer': 'upstream', 'sufixo': 'X',
            'afs': [{
                'af': 'ipv4', 'peer_ip': '172.16.8.1', 'remote_as': '262725',
                'pl_in': {'modo': 'existente', 'nome': 'PL-DEFAULT-ROUTE'},
                'pl_out': {'modo': 'nova', 'cidr': '2001:db8::/32'},
            }],
        }
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_criar_sessao('cisco', DADOS_SNAPSHOT_BASE, params)

    def test_recusa_nenhuma_af_marcada(self):
        with self.assertRaises(AcaoBgpNaoSuportada):
            comandos_criar_sessao('cisco', DADOS_SNAPSHOT_BASE, {'tipo_peer': 'upstream', 'sufixo': 'X', 'afs': []})


from clientes.bgp_actions import buscar_prefix_lists_ao_vivo


SAIDA_SHOW_RUN_PREFIX_LIST = """
ip prefix-list PL-DEFAULT-ROUTE seq 5 permit 0.0.0.0/0
ip prefix-list PL-ORIGIN-45.169.6.0_24 seq 5 permit 45.169.6.0/24
"""

SAIDA_SHOW_RUN_ROUTE_MAP = """
route-map RM-PEER-CONECT-V4-OUT permit 10
 match ip address prefix-list PL-ORIGIN-45.169.6.0_24
route-map RM-PEER-CONECT-V4-IN permit 10
 match ip address prefix-list PL-DEFAULT-ROUTE
"""


class BuscarPrefixListsAoVivoTest(SimpleTestCase):
    @mock.patch('clientes.bgp_actions._fechar_tunel')
    @mock.patch('clientes.bgp_actions._conectar_script')
    def test_conecta_roda_show_running_e_reaproveita_parser_do_backup(self, mock_conectar, mock_fechar):
        conn_falsa = mock.Mock()
        conn_falsa.send_command.side_effect = [SAIDA_SHOW_RUN_PREFIX_LIST, SAIDA_SHOW_RUN_ROUTE_MAP]
        mock_conectar.return_value = (conn_falsa, None)
        acesso_falso = mock.Mock()

        resultado = buscar_prefix_lists_ao_vivo(acesso_falso)

        mock_conectar.assert_called_once_with(acesso_falso, 'cisco')
        self.assertEqual(conn_falsa.send_command.call_count, 2)
        self.assertIn('PL-DEFAULT-ROUTE', resultado['prefix_lists'])
        self.assertIn('PL-ORIGIN-45.169.6.0_24', resultado['prefix_lists'])
        self.assertEqual(resultado['policies']['RM-PEER-CONECT-V4-OUT'][0]['prefix_lists'], ['PL-ORIGIN-45.169.6.0_24'])
        conn_falsa.disconnect.assert_called_once()

    @mock.patch('clientes.bgp_actions._fechar_tunel')
    @mock.patch('clientes.bgp_actions._conectar_script')
    def test_fecha_conexao_mesmo_se_o_comando_falhar(self, mock_conectar, mock_fechar):
        conn_falsa = mock.Mock()
        conn_falsa.send_command.side_effect = Exception('timeout')
        mock_conectar.return_value = (conn_falsa, None)

        with self.assertRaises(Exception):
            buscar_prefix_lists_ao_vivo(mock.Mock())
        conn_falsa.disconnect.assert_called_once()


import copy

from clientes.bgp_actions import aplicar_efeito_localmente


class AplicarEfeitoLocalmenteCriarSessaoTest(SimpleTestCase):
    def test_insere_sessao_nova_e_prefix_lists_novas_em_dados(self):
        dados = copy.deepcopy(DADOS_SNAPSHOT_BASE)
        params = {
            'tipo_peer': 'upstream',
            'sufixo': 'CONECT',
            'afs': [{
                'af': 'ipv4', 'peer_ip': '172.16.8.1', 'remote_as': '262725',
                'pl_in': {'modo': 'existente', 'nome': 'PL-DEFAULT-ROUTE'},
                'pl_out': {'modo': 'nova', 'cidr': '45.169.6.0/24'},
            }],
        }
        aplicar_efeito_localmente('cisco', dados, 'criar_sessao', '', '172.16.8.1', params)

        nova_sessao = next(s for s in dados['sessoes'] if s['peer_ip'] == '172.16.8.1')
        self.assertEqual(nova_sessao['descricao'], 'UPSTREAM-CONECT-V4')
        # criada em shutdown por padrão (ver comandos_criar_sessao) — o
        # painel não pode mostrar como ativa antes do operador confirmar.
        self.assertFalse(nova_sessao['habilitada'])
        self.assertEqual(nova_sessao['policy_out'], 'RM-PEER-CONECT-V4-OUT')
        self.assertEqual(nova_sessao['policy_in'], 'RM-PEER-CONECT-V4-IN')

        self.assertIn('PL-ORIGIN-45.169.6.0_24', dados['prefix_lists'])
        self.assertEqual(dados['policies']['RM-PEER-CONECT-V4-OUT'][0]['prefix_lists'], ['PL-ORIGIN-45.169.6.0_24'])
        self.assertEqual(dados['policies']['RM-PEER-CONECT-V4-IN'][0]['prefix_lists'], ['PL-DEFAULT-ROUTE'])

        # simular_anuncios já deve ter rodado pra essa sessão nova
        self.assertIn('172.16.8.1', dados['anuncios'])
        anunciados = [a for a in dados['anuncios']['172.16.8.1'] if a['permitido']]
        self.assertTrue(any(a['prefixo'] == '45.169.6.0/24' for a in anunciados))

    def test_nunca_levanta_excecao(self):
        dados = {}   # dados vazio/malformado de propósito
        try:
            aplicar_efeito_localmente('cisco', dados, 'criar_sessao', '', '1.2.3.4', {'afs': []})
        except Exception as e:
            self.fail(f'aplicar_efeito_localmente não deveria levantar exceção, levantou: {e}')


import json

from django.contrib.auth.models import User
from django.test import TestCase, Client

from clientes.models import Acesso, BgpSnapshot, Cliente


def _criar_cliente():
    return Cliente.objects.create(
        nome_empresa='Cliente Teste', cnpj='00.000.000/0001-00',
        endereco='Rua Teste, 1', email='cliente-teste-bgp@example.com',
    )


def _criar_acesso_cisco(cliente):
    return Acesso.objects.create(
        cliente=cliente, tipo='Router Borda', host='198.51.100.1',
        protocolo=Acesso.ProtocoloChoices.SSH, usuario='admin', senha='segredo',
    )


def _criar_staff():
    from usuario.models import TOTPDevice
    user = User.objects.create_user('staff-bgp-teste', password='x', is_staff=True, is_superuser=True)
    # Forcar2FAMiddleware redireciona qualquer usuário autenticado sem
    # TOTPDevice confirmado pra tela de configuração de 2FA, em toda
    # requisição — sem isso o client de teste nunca chega nas views BGP.
    TOTPDevice.objects.create(usuario=user, secret='JBSWY3DPEHPK3PXP', confirmado=True)
    return user


class BgpCriarSessaoViewTest(TestCase):
    def setUp(self):
        self.cliente = _criar_cliente()
        self.acesso = _criar_acesso_cisco(self.cliente)
        self.snapshot = BgpSnapshot.objects.create(
            acesso=self.acesso, vendor='cisco', dados=DADOS_SNAPSHOT_BASE,
        )
        self.user = _criar_staff()
        self.client = Client()
        self.client.force_login(self.user)

    def test_preview_criar_sessao_devolve_comandos(self):
        params = {
            'tipo_peer': 'upstream', 'sufixo': 'CONECT',
            'afs': [{
                'af': 'ipv4', 'peer_ip': '172.16.8.1', 'remote_as': '262725',
                'pl_in': {'modo': 'existente', 'nome': 'PL-DEFAULT-ROUTE'},
                'pl_out': {'modo': 'nova', 'cidr': '45.169.6.0/24'},
            }],
        }
        r = self.client.post(
            f'/clientes/bgp/{self.acesso.id}/acao/',
            data=json.dumps({'tipo': 'criar_sessao', 'alvo': '172.16.8.1', 'params': params, 'preview': True}),
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn('router bgp 268080', data['comandos'])
        self.assertIn('neighbor 172.16.8.1 remote-as 262725', data['comandos'])

    def test_escanear_prefixo_com_sessao_inexistente_nao_da_404(self):
        r = self.client.post(
            f'/clientes/bgp/{self.acesso.id}/escanear-prefixo/',
            data=json.dumps({'sessao': '__nova_sessao__'}),
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)
        nomes = [c['nome'] for c in r.json()['candidatas']]
        self.assertIn('PL-DEFAULT-ROUTE', nomes)
        # sessão não existe -> nada pode estar marcado como já anunciando
        self.assertTrue(all(not c['ja_anunciando'] for c in r.json()['candidatas']))

    def test_escanear_prefixo_ao_vivo_usa_leitura_ssh(self):
        with mock.patch('clientes.bgp_views.buscar_prefix_lists_ao_vivo') as mock_buscar:
            mock_buscar.return_value = {
                'prefix_lists': {'PL-AO-VIVO': [{'acao': 'permit', 'prefixo': '203.0.113.0/24', 'len_min': 24, 'len_max': 24, 'seq': 5}]},
                'policies': {},
            }
            r = self.client.post(
                f'/clientes/bgp/{self.acesso.id}/escanear-prefixo/',
                data=json.dumps({'sessao': '__nova_sessao__', 'ao_vivo': True}),
                content_type='application/json',
            )
        self.assertEqual(r.status_code, 200)
        nomes = [c['nome'] for c in r.json()['candidatas']]
        self.assertEqual(nomes, ['PL-AO-VIVO'])
        mock_buscar.assert_called_once_with(self.acesso)

    def test_escanear_prefixo_ao_vivo_com_erro_de_conexao_devolve_422(self):
        with mock.patch('clientes.bgp_views.buscar_prefix_lists_ao_vivo', side_effect=Exception('timeout')):
            r = self.client.post(
                f'/clientes/bgp/{self.acesso.id}/escanear-prefixo/',
                data=json.dumps({'sessao': '__nova_sessao__', 'ao_vivo': True}),
                content_type='application/json',
            )
        self.assertEqual(r.status_code, 422)

    def test_escanear_prefixo_com_sessao_real_continua_funcionando(self):
        """Regressão: o fluxo existente de 'Anunciar prefixo novo' (sessão
        real, sem ao_vivo) não pode quebrar."""
        r = self.client.post(
            f'/clientes/bgp/{self.acesso.id}/escanear-prefixo/',
            data=json.dumps({'sessao': '10.0.0.1'}),
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)
        candidatas = {c['nome']: c for c in r.json()['candidatas']}
        self.assertTrue(candidatas['PL-DEFAULT-ROUTE']['ja_anunciando'])
