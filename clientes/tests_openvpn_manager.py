"""
Testes do openvpn_manager (OpenVPN Server configurado no MikroTik do cliente).

Rodar: python manage.py test clientes.tests_openvpn_manager

As saídas de RouterOS abaixo são recortes reais das RBs da CONECTONLINE
(45.180.36.1 e 192.140.66.160), onde o OpenVPN gerado não alcançava rotas
que o L2TP da mesma RB alcançava.
"""
from unittest import mock

from django.test import SimpleTestCase

from clientes import openvpn_manager as om


def _fake_exec(respostas):
    """_exec falso: devolve a resposta do primeiro prefixo que casar."""
    def _exec(client, cmd, timeout=60):
        for prefixo, saida in respostas.items():
            if cmd.startswith(prefixo):
                return saida, ''
        return '', ''
    return _exec


SECRETS_CONECTONLINE = '\n'.join([
    'l2tp|default',
    'l2tp|VPN-Profile',
    'l2tp|VPN-Profile',
    'l2tp|VPN-Profile',
    'ovpn|VPN-Profile',
    'ovpn|OPEN_VPN',
    'ovpn|OPEN_VPN',
    'ovpn|OPEN_VPN',
    'ovpn|OPEN_VPN',
])
PROFILES_CONECTONLINE = '\n'.join([
    'default|||',
    'VPN-Profile|10.190.180.1|Pool-VPN|',
    'OPEN_VPN|192.168.250.1|POOL_OpenVPN|50M/50M',
    'default-encryption|||',
])
POOLS_CONECTONLINE = 'pool1\nPool-cgnat\nPool-VPN\nPOOL_OpenVPN'


def _respostas_profile(secrets, profiles, pools, pppoe_default='', pppoe_count='0'):
    return {
        ':foreach i in=[/ppp secret': secrets,
        ':foreach i in=[/ppp profile': profiles,
        ':foreach i in=[/ip pool': pools,
        ':foreach i in=[/interface pppoe-server': pppoe_default,
        '/ppp secret print count-only': pppoe_count,
    }


def _cfg(**extra):
    base = {
        'nome_vpn': 'paulo', 'ip_publico': '45.180.36.1', 'porta': 61194,
        'vpn_pool': '192.168.250.128-192.168.250.254', 'vpn_local_ip': '192.168.250.1',
        'vpn_username': 'paulo', 'vpn_password': 'x', 'cert_passphrase': 'y',
        'rate_limit': '50M/50M', 'ppp_profile': 'VPN-Profile', 'criar_pool': False,
    }
    base.update(extra)
    return base


class PoolCidrTests(SimpleTestCase):
    def test_pool_padrao(self):
        self.assertEqual(om._pool_cidr('192.168.250.128-192.168.250.254'), '192.168.250.128/25')

    def test_pool_que_passa_do_meio_do_bloco(self):
        # Antes: '192.168.250.2/25' → só .0-.127 tinham NAT.
        self.assertEqual(om._pool_cidr('192.168.250.2-192.168.250.254'), '192.168.250.0/24')

    def test_pool_em_cidr_e_multiplas_faixas(self):
        self.assertEqual(om._pool_cidr('100.64.96.0/22'), '100.64.96.0/22')
        self.assertEqual(om._pool_cidr('10.0.0.10-10.0.0.20,10.0.1.5-10.0.1.9'), '10.0.0.0/23')


class DetectarProfileTests(SimpleTestCase):
    def test_escolhe_o_profile_do_l2tp_e_ignora_o_da_plataforma(self):
        fake = _fake_exec(_respostas_profile(SECRETS_CONECTONLINE, PROFILES_CONECTONLINE,
                                             POOLS_CONECTONLINE))
        with mock.patch.object(om, '_exec', fake):
            perfil = om._detectar_profile_vpn(None)
        self.assertEqual(perfil['nome'], 'VPN-Profile')
        self.assertEqual(perfil['secrets'], 4)
        self.assertEqual(perfil['pool'], 'Pool-VPN')

    def test_profile_sem_pool_nao_serve(self):
        fake = _fake_exec(_respostas_profile('l2tp|default\nl2tp|default', 'default|10.0.0.1||',
                                             'algum'))
        with mock.patch.object(om, '_exec', fake):
            self.assertIsNone(om._detectar_profile_vpn(None))

    def test_plano_de_assinante_nunca_serve(self):
        # ALTA RADIO: pptp apontando para o default-profile dos pppoe-servers
        # e para um plano com rate-limit; sobra o profile de VPN de verdade.
        fake = _fake_exec(_respostas_profile(
            'pptp|pppoe\npptp|50 Megas\npptp|VPN',
            'pppoe|170.80.59.122|CGNAT_01|\n50 Megas|170.80.59.122|CGNAT_01|50M/50M\n'
            'VPN|10.8.0.1|pool-vpn|',
            'CGNAT_01\npool-vpn',
            pppoe_default='pppoe\npppoe'))
        with mock.patch.object(om, '_exec', fake):
            self.assertEqual(om._detectar_profile_vpn(None)['nome'], 'VPN')

    def test_profile_usado_por_secret_pppoe_nao_serve(self):
        fake = _fake_exec(_respostas_profile(SECRETS_CONECTONLINE, PROFILES_CONECTONLINE,
                                             POOLS_CONECTONLINE, pppoe_count='12'))
        with mock.patch.object(om, '_exec', fake):
            self.assertIsNone(om._detectar_profile_vpn(None))

    def test_rb_sem_vpn(self):
        with mock.patch.object(om, '_exec', _fake_exec({})):
            self.assertIsNone(om._detectar_profile_vpn(None))


class ServidorExistenteTests(SimpleTestCase):
    PRINT_V6 = (
        '                     enabled: yes\n'
        '                        port: 51194\n'
        '                 certificate: ovpn-server\n'
    )

    def test_v6_com_servidor_do_cliente_aborta(self):
        with mock.patch.object(om, '_exec', _fake_exec({'/interface ovpn-server server print': self.PRINT_V6})):
            instancias = om._ler_ovpn_server(None, usa_lista=False)
        erro, _ = om._checar_servidor_existente(instancias, 61194, usa_lista=False)
        self.assertIn('servidor OpenVPN próprio', erro)
        self.assertIn('51194', erro)

    def test_v6_com_servidor_da_plataforma_segue(self):
        saida = self.PRINT_V6.replace('ovpn-server', om.CERT_SERVIDOR)
        with mock.patch.object(om, '_exec', _fake_exec({'/interface ovpn-server server print': saida})):
            instancias = om._ler_ovpn_server(None, usa_lista=False)
        self.assertEqual(om._checar_servidor_existente(instancias, 61194, usa_lista=False), ('', []))

    def test_v6_desabilitado_segue(self):
        saida = self.PRINT_V6.replace('enabled: yes', 'enabled: no')
        with mock.patch.object(om, '_exec', _fake_exec({'/interface ovpn-server server print': saida})):
            instancias = om._ler_ovpn_server(None, usa_lista=False)
        self.assertEqual(om._checar_servidor_existente(instancias, 61194, usa_lista=False)[0], '')

    def test_lista_so_remove_as_da_plataforma(self):
        terse = (
            ' 0    name=cliente port=1194 certificate=cert-cliente\n'
            ' 1    name=paulo port=61194 certificate=Servidor-OPEN\n'
            ' 2 X  name=velho port=61194 certificate=outro\n'
        )
        with mock.patch.object(om, '_exec', _fake_exec({'/interface ovpn-server server print terse': terse})):
            instancias = om._ler_ovpn_server(None, usa_lista=True)
        erro, remover = om._checar_servidor_existente(instancias, 61194, usa_lista=True)
        self.assertEqual(erro, '')
        self.assertEqual(remover, ['paulo'])
        cmds = om._cmd_ovpn_server_lista(_cfg(nome_vpn='novo'), remover)
        self.assertNotIn('/interface ovpn-server server remove [find]', cmds)
        self.assertIn('/interface ovpn-server server remove [find name="paulo"]', cmds)

    def test_lista_porta_ocupada_por_instancia_do_cliente_aborta(self):
        instancias = [{'nome': 'cliente', 'ativo': True, 'certificado': 'cert-cliente', 'porta': '61194'}]
        erro, _ = om._checar_servidor_existente(instancias, 61194, usa_lista=True)
        self.assertIn('já usa a porta 61194', erro)


class ComandosTests(SimpleTestCase):
    def test_profile_existente_nao_cria_pool_profile_nem_nat(self):
        cmds = om.comandos_ros6(_cfg())
        texto = '\n'.join(cmds)
        self.assertNotIn('NAT_OpenVPN', texto)
        self.assertNotIn('POOL_OpenVPN', texto)
        self.assertNotIn('/ppp profile', texto)
        self.assertIn('service=ovpn profile="VPN-Profile"', texto)
        self.assertIn('default-profile="VPN-Profile"', cmds[-1])

    def test_pool_proprio_mantem_nat_com_cidr_certo(self):
        cmds = om.comandos_ros6(_cfg(ppp_profile='OPEN_VPN', criar_pool=True,
                                     vpn_pool='192.168.250.2-192.168.250.254'))
        texto = '\n'.join(cmds)
        self.assertIn('src-address=192.168.250.0/24 to-addresses=45.180.36.1', texto)
        self.assertIn('/ppp profile add name=OPEN_VPN', texto)


class RotasNoPoolTests(SimpleTestCase):
    def test_rotas_vizinhas_nao_colidem(self):
        # 192.140.66.160: OSPF tem .4/30 e .8/30 — fora de .1 e .128/25.
        saida = (
            ' 6 ADo  dst-address=192.168.250.4/30 gateway=172.18.100.33 distance=110\n'
            ' 7 ADo  dst-address=192.168.250.8/30 gateway=172.18.100.33 distance=110\n'
            ' 8 ADC  dst-address=192.168.250.254/32 pref-src=192.168.250.1 gateway=<ovpn-paulo>\n'
        )
        with mock.patch.object(om, '_exec', _fake_exec({'/ip route print terse': saida})):
            self.assertEqual(om._rotas_no_pool(None, '192.168.250.128-192.168.250.254', '192.168.250.1'), [])

    def test_rota_dentro_do_pool_colide(self):
        saida = ' 0 ADo  dst-address=192.168.250.128/26 gateway=10.0.0.1 distance=110\n'
        with mock.patch.object(om, '_exec', _fake_exec({'/ip route print terse': saida})):
            self.assertEqual(om._rotas_no_pool(None, '192.168.250.128-192.168.250.254', '192.168.250.1'),
                             ['192.168.250.128/26 via 10.0.0.1'])

    def test_rota_desabilitada_nao_conta(self):
        # PROMOFI (v7): estática desabilitada + sessão OpenVPN conectada.
        saida = (
            ' 0  Xs   dst-address=192.168.250.0/24 routing-table=main gateway=192.168.250.1\n'
            '    DAc   dst-address=192.168.250.10/32 routing-table=main gateway=<ovpn-vpn.promofi.10>\n'
        )
        with mock.patch.object(om, '_exec', _fake_exec({'/ip route print terse': saida})):
            self.assertEqual(om._rotas_no_pool(None, '192.168.250.128-192.168.250.254', '192.168.250.1'), [])

    def test_comando_recusado_vira_nao_verificado(self):
        with mock.patch.object(om, '_exec', _fake_exec({'/ip route print terse': 'syntax error (line 1 column 30)'})):
            self.assertIsNone(om._rotas_no_pool(None, '192.168.250.128-192.168.250.254', '192.168.250.1'))
