"""Senha digitada no terminal não vai crua para a auditoria.

Os casos de comando/eco são do VRP (NE8000 da Sartor, 19/10/2026), com as
senhas trocadas por falsas.
"""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from clientes import auditoria_mascara as m
from clientes import consumers as c

NOVA = 'Xy&p7mF$t3%L$*V#'
VELHA = '9F(epX46#^q^'


class RegrasTests(SimpleTestCase):
    def test_prompt_de_senha(self):
        for saida in ('Please input the username: sartor\r\nEnter password: ',
                      '[~BGP-aaa]local-user x password\r\nPlease enter old password: ',
                      'Password:', 'Senha: ', 'Confirm Password:'):
            self.assertTrue(m.eh_prompt_senha(saida), saida)
        for saida in ('[~BGP-aaa]', '<BGP>', 'Error: Incorrect password.\r\n[~BGP-aaa]', ''):
            self.assertFalse(m.eh_prompt_senha(saida), saida)

    def test_inline_vrp_cisco_mikrotik(self):
        casos = {
            f'local-user sartor password irreversible-cipher {NOVA}':
                'local-user sartor password irreversible-cipher ******',
            f'local-user sartor password cipher {NOVA}': 'local-user sartor password cipher ******',
            'username adm privilege 15 secret 5 $1$abc$def': 'username adm privilege 15 secret 5 ******',
            '/user set admin password=Abc12345': '/user set admin password=******',
            '/ppp secret add name=a password="a b c"': '/ppp secret add name=a password=******',
            'snmp-agent community read cipher Comm1234': 'snmp-agent community read cipher ******',
            'snmp-agent community read s dstech-ro ': 'snmp-agent community read s ****** ',
            'snmp-server community parks rw': 'snmp-server community ****** rw',
            " local-user ixc password irreversible-cipher $1c$>B+BPCg\"07$6fQ7'5W5~r8Gf$":
                ' local-user ixc password irreversible-cipher ******',
            'ip ospf authentication-key simple 0 34121': 'ip ospf authentication-key simple 0 ******',
        }
        for antes, depois in casos.items():
            self.assertEqual(m.mascarar(antes), depois, antes)

    def test_nao_mexe_em_texto_normal(self):
        for t in (' user-password password-force-change disable',
                  'Info: The user sartor\'s password has been changed to be irreversible.',
                  'Error: Incorrect password.', 'display password-policy', '/ppp secret print',
                  'display current-configuration | include password',
                  'Warning: The initial password poses security risks.',
                  'The password needs to be changed. Change now? [Y/N]:',
                  ' system user password security-length 12',
                  'apply community 65109:50101 no-export additive',
                  '  community         SNMP community configuration.',
                  'snmp-agent community write ?', 'snmp-agent community complexity-check disable'):
            self.assertEqual(m.mascarar(t), t)

    def test_segredo_conhecido_some_em_qualquer_lugar(self):
        t = f'Enter password: \r\nstelnet x\r\n{NOVA}   lixo'
        self.assertEqual(m.mascarar(t, {NOVA}), 'Enter password: \r\nstelnet x\r\n******   lixo')

    def test_segredo_curto_nao_substitui_solto(self):
        self.assertEqual(m.mascarar('admin login ok', {'admin'}), 'admin login ok')

    def test_palavra_ip_e_community_nao_substituem_solto(self):
        t = 'authentication-scheme ixc\nipv6 address 2804:4d0c:400::1 64\napply community 65109:50101\nsnmp-agent target-host'
        self.assertEqual(m.mascarar(t, {'authentication', '2804:4d0c:400', '65109:50101', 'snmp-agent'}), t)

    def test_extrai_do_eco_expandido(self):
        eco = f'[~BDR-SINOP-aaa]local-user sartor password irreversible-cipher {NOVA}'
        self.assertEqual(m.extrair_segredos(eco), {NOVA})
        self.assertEqual(m.extrair_segredos('password has been changed'), set())

    def test_sem_eco_registros_antigos(self):
        transcript = ('<BGP>stelnet 198.18.235.2 4721\r\nPlease input the username: sartor\r\n'
                      'Enter password: \r\nThe user has been locked\r\n<BGP>save\r\n')
        cmds = ['stelnet 198.18.235.2 4721', 'sartor', NOVA, 'save', 'q', 'display']
        self.assertEqual(m.segredos_sem_eco(cmds, transcript), {NOVA})
        # Sem prompt de senha na sessão, nada é deduzido.
        self.assertEqual(m.segredos_sem_eco([NOVA], '<BGP>save\r\n'), set())


class ConsumerTests(SimpleTestCase):
    """_registrar_digitacao/_encerrar_sessao_auditoria gravam mascarado."""

    def _consumer(self, senhas_cadastradas=()):
        cons = c.SSHConsumer.__new__(c.SSHConsumer)
        cons._sessao_auditoria = SimpleNamespace(
            status='ativa', acesso=SimpleNamespace(cliente_id=41), save=mock.Mock())
        cons._transcript_buf = ''
        cons._segredos_auditoria = m.segredos_de_acessos([senhas_cadastradas])
        return cons

    def _digitar(self, cons, saida, teclas):
        cons._registrar_saida(saida)
        with mock.patch.object(c.AcessoComando.objects, 'create') as create:
            cons._registrar_digitacao(teclas)
        return [k.kwargs['comando'] for k in create.call_args_list]

    def test_resposta_a_prompt_vira_mascara(self):
        cons = self._consumer()
        self.assertEqual(self._digitar(cons, 'Enter password: ', VELHA + '\r'), ['******'])

    def test_tab_abreviado_mascarado_pelo_eco(self):
        cons = self._consumer()
        eco = f'[~BDR-SINOP-aaa]local-user sartor password irreversible-cipher {NOVA}'
        gravados = self._digitar(cons, eco, f'loca\tsartor pa\ti i\t{NOVA}\r')
        self.assertEqual(gravados, ['loca\tsartor pa\ti i\t******'])
        # E a mesma senha digitada depois, em outro contexto, também.
        self.assertEqual(self._digitar(cons, '\r\n[~BGP]', NOVA + '\r'), ['******'])

    def test_senha_cadastrada(self):
        cons = self._consumer(('Cadastr0!', ''))
        self.assertEqual(self._digitar(cons, '<BGP>', 'echo Cadastr0!\r'), ['echo ******'])

    def test_transcript_salvo_mascarado(self):
        cons = self._consumer()
        sessao = cons._sessao_auditoria
        cons._registrar_saida(f'[~BGP-aaa]local-user sartor password cipher {NOVA}\r\n'
                              f'Please enter old password: \r\n[*BGP-aaa]x {NOVA}\r\n')
        cons._encerrar_sessao_auditoria()
        self.assertNotIn(NOVA, sessao.transcript)
        self.assertIn('local-user sartor password cipher ******', sessao.transcript)
