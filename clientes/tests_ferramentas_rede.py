"""Ferramentas de rede do dashboard do cliente (clientes/ferramentas_rede.py e
a view ferramenta_rede_cliente): validação do destino/portas — que viram
comando de shell no proxy —, leitura das saídas e o roteamento pelo proxy."""
import json
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from clientes import ferramentas_rede as fr
from clientes.models import Cliente, ProxyServer
from usuario.models import TOTPDevice

PING_OK = """PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=12.1 ms
64 bytes from 8.8.8.8: icmp_seq=2 ttl=117 time=14.3 ms
64 bytes from 8.8.8.8: icmp_seq=3 ttl=117 time=11.9 ms

--- 8.8.8.8 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 2003ms
rtt min/avg/max/mdev = 11.900/12.766/14.300/1.084 ms
"""

TRACE = """traceroute to 8.8.8.8 (8.8.8.8), 30 hops max, 60 byte packets
 1  192.168.0.1  0.512 ms
 2  *
 3  2001:db8::1  9.8 ms
"""

MTR = """Start: 2026-10-11T08:00:00-0300
HOST: proxy                      Loss%   Snt   Last   Avg  Best  Wrst StDev
  1.|-- 192.168.0.1                0.0%    10    0.4   0.5   0.3   0.9   0.1
  2.|-- ???                       100.0    10    0.0   0.0   0.0   0.0   0.0
  3.|-- 8.8.8.8                    0.0%    10   12.0  12.4  11.8  14.0   0.6
"""


class ValidacaoTest(SimpleTestCase):
    def test_destinos_validos(self):
        self.assertEqual(fr.validar_destino(' 8.8.8.8 '), '8.8.8.8')
        self.assertEqual(fr.validar_destino('2001:DB8::1'), '2001:db8::1')
        self.assertEqual(fr.validar_destino('Google.com.'), 'google.com')

    def test_injecao_recusada(self):
        for ruim in ['8.8.8.8; rm -rf /', '$(id)', '`id`', 'a b', '-c 1000 x', 'x|y',
                     "x'y", 'google.com&&id', '../etc', '', 'a' * 300]:
            with self.subTest(ruim=ruim), self.assertRaises(fr.ErroValidacao):
                fr.validar_destino(ruim)

    def test_portas(self):
        self.assertEqual(fr.validar_portas('22, 80;443 80'), [22, 80, 443])
        self.assertEqual(fr.validar_portas('8000-8002'), [8000, 8001, 8002])
        for ruim in ['', '0', '70000', '1-100', '22;id', '80-70', 'abc']:
            with self.subTest(ruim=ruim), self.assertRaises(fr.ErroValidacao):
                fr.validar_portas(ruim)

    def test_comando_cita_destino(self):
        cmd = fr.cmd_portas('google.com', [22, 443])
        self.assertIn("H=google.com;", cmd)
        self.assertIn('for P in 22 443;', cmd)
        self.assertIn('ping -6 ', fr.cmd_ping('2001:db8::1', 'v4', 3))   # IP literal manda


class ParsersTest(SimpleTestCase):
    def test_ping(self):
        r = fr.parse_ping(PING_OK)
        self.assertTrue(r['alcancavel'])
        self.assertEqual((r['enviados'], r['recebidos'], r['perda_pct']), (3, 3, 0))
        self.assertEqual(r['rtt_avg'], 12.766)
        self.assertEqual(r['tempos'], [12.1, 14.3, 11.9])

    def test_ping_sem_resposta(self):
        r = fr.parse_ping('5 packets transmitted, 0 received, 100% packet loss, time 4ms')
        self.assertFalse(r['alcancavel'])
        self.assertEqual(r['perda_pct'], 100)

    def test_qualidade(self):
        q = fr.avaliar_qualidade(fr.parse_ping(PING_OK))
        self.assertEqual(q['nota'], 'boa')
        self.assertEqual(q['jitter'], 2.3)   # |14.3-12.1| e |11.9-14.3| → média 2.3

    def test_traceroute(self):
        s = fr.parse_traceroute(TRACE)
        self.assertEqual([h['ip'] for h in s], ['192.168.0.1', None, '2001:db8::1'])
        self.assertEqual(s[0]['rtt'], 0.51)

    def test_mtr(self):
        s = fr.parse_mtr(MTR)
        self.assertEqual(len(s), 3)
        self.assertEqual(s[2]['host'], '8.8.8.8')
        self.assertEqual(s[1]['perda_pct'], 100.0)

    def test_portas(self):
        self.assertEqual(fr.parse_portas('PORTA 22 aberta\nPORTA 81 filtrada\n'),
                         [{'porta': 22, 'estado': 'aberta'}, {'porta': 81, 'estado': 'filtrada'}])

    def test_calculadora(self):
        r = fr.calcular_ip('192.168.10.77 255.255.255.0')
        self.assertEqual((r['cidr'], r['broadcast'], r['hosts_uteis']), ('192.168.10.0/24', '192.168.10.255', '254'))
        self.assertEqual(r['primeiro_host'], '192.168.10.1')
        r31 = fr.calcular_ip('10.0.0.0/31')
        self.assertEqual((r31['hosts_uteis'], r31['broadcast']), ('2', None))
        r6 = fr.calcular_ip('2001:db8::/48')
        self.assertEqual(r6['redes_64'], str(2 ** 16))
        with self.assertRaises(fr.ErroValidacao):
            fr.calcular_ip('300.1.1.1/24')


class ViewTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('cli_fr', password='x')
        TOTPDevice.objects.create(usuario=self.user, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.cliente = Cliente.objects.create(
            nome_empresa='Cliente FR', cnpj='44.444.444/0001-44', endereco='Rua Z', email='fr@example.com',
            usuario=self.user,
        )
        self.proxy = ProxyServer.objects.create(cliente=self.cliente, nome='PX-FR', host='10.9.9.9',
                                                usuario='u', senha='s')
        self.client.force_login(self.user)
        self.url = reverse('ferramenta_rede_cliente')

    def _post(self, **corpo):
        corpo.setdefault('cliente_id', self.cliente.id)
        return self.client.post(self.url, json.dumps(corpo), content_type='application/json')

    def test_ping_roda_no_proxy_do_cliente(self):
        with mock.patch('clientes.views._ssh_exec', return_value=(PING_OK, '', 0)) as ssh:
            r = self._post(ferramenta='ping', destino='8.8.8.8', pacotes=3)
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertEqual(j['proxy'], 'PX-FR')
        self.assertEqual(j['resultado']['recebidos'], 3)
        proxy, cmd, _ = ssh.call_args.args
        self.assertEqual(proxy.id, self.proxy.id)
        self.assertEqual(cmd, 'ping -4 -c 3 -W 2 8.8.8.8 2>&1')

    def test_destino_malicioso_nao_chega_ao_proxy(self):
        with mock.patch('clientes.views._ssh_exec') as ssh:
            r = self._post(ferramenta='traceroute', destino='8.8.8.8;reboot')
        self.assertEqual(r.status_code, 400)
        ssh.assert_not_called()

    def test_sem_proxy(self):
        self.proxy.ativo = False
        self.proxy.save()
        r = self._post(ferramenta='ping', destino='8.8.8.8')
        self.assertEqual(r.status_code, 400)
        self.assertIn('proxy', r.json()['error'])

    def test_calculadora_nao_precisa_de_proxy(self):
        self.proxy.delete()
        r = self._post(ferramenta='calculadora', entrada='10.0.0.0/30')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['resultado']['hosts_uteis'], '2')

    def test_um_teste_por_vez(self):
        cache.add(f'ferramenta_rede:{self.user.id}', 1, 60)
        with mock.patch('clientes.views._ssh_exec') as ssh:
            r = self._post(ferramenta='ping', destino='8.8.8.8')
        self.assertEqual(r.status_code, 429)
        ssh.assert_not_called()

    def test_usuario_sem_cliente(self):
        outro = User.objects.create_user('sem_cli', password='x')
        TOTPDevice.objects.create(usuario=outro, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(outro)
        self.assertEqual(self._post(ferramenta='ping', destino='8.8.8.8').status_code, 403)

    def test_cliente_de_outro_login_recusado(self):
        outro = Cliente.objects.create(nome_empresa='Outro', cnpj='66.666.666/0001-66', endereco='R', email='o@example.com')
        ProxyServer.objects.create(cliente=outro, nome='PX-OUTRO', host='10.9.9.7', usuario='u', senha='s')
        with mock.patch('clientes.views._ssh_exec') as ssh:
            r = self._post(ferramenta='ping', destino='8.8.8.8', cliente_id=outro.id)
        self.assertEqual(r.status_code, 403)
        ssh.assert_not_called()

    def test_modulo_desabilitado_responde_json(self):
        with mock.patch('usuario.perms.portal_pode_usar_ferramenta', return_value=False), \
             mock.patch('clientes.views._ssh_exec') as ssh:
            r = self._post(ferramenta='ping', destino='8.8.8.8')
        self.assertEqual(r.status_code, 403)
        self.assertIn('Ferramentas', r.json()['error'])
        ssh.assert_not_called()

    def test_dashboard_sem_ferramentas(self):
        html = self.client.get(reverse('cliente_dashboard')).content.decode()
        self.assertNotIn('Ferramentas de Rede', html)
        self.assertNotIn('tomich.com.br/ping', html)
        self.assertIn('Ir para Acessos', html)

    def test_aba_ferramentas_na_pagina_do_cliente(self):
        html = self.client.get(reverse('listar_clientes') + f'?id={self.cliente.id}').content.decode()
        self.assertIn('</i>Ferramentas\n', html)
        self.assertNotIn('🔬 Testes de Rede', html)
        for aba in ('ping', 'traceroute', 'mtr', 'portas', 'qualidade', 'calculadora', 'conectividade', 'dns'):
            self.assertIn(f"testeRedeSubAba(event, '{aba}')", html)
        self.assertIn('id="fr-painel"', html)
        self.assertIn(f'FR_CLIENTE_ID = {self.cliente.id};', html)
        self.assertIn('PX-FR', html)


class TesteRedeInternoTest(TestCase):
    """A aba interna "Testes de Rede" também monta shell com o destino digitado."""
    def test_destino_unico_malicioso_recusado(self):
        admin = User.objects.create_user('adm_fr', password='x', is_staff=True, is_superuser=True)
        TOTPDevice.objects.create(usuario=admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        cliente = Cliente.objects.create(nome_empresa='C2', cnpj='55.555.555/0001-55', endereco='R', email='c2@example.com')
        ProxyServer.objects.create(cliente=cliente, nome='PX2', host='10.9.9.8', usuario='u', senha='s')
        self.client.force_login(admin)
        with mock.patch('clientes.views._ssh_exec') as ssh:
            r = self.client.post(reverse('teste_rede_cliente', args=[cliente.id]),
                                 json.dumps({'destino_unico': '1.1.1.1;id'}), content_type='application/json')
        self.assertEqual(r.status_code, 400)
        ssh.assert_not_called()
