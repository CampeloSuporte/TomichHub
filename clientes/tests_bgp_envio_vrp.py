"""
Testes do envio de configuração BGP no VRP (Huawei) —
`clientes/bgp_actions.py::_enviar_config_vrp`/`executar_acao_bgp`.

Cobre o bug real de 24/08/2026: subir o circuito ix-03 (PTT-RS) morria com
"Pattern not detected: '(?:VS\\-BGP.*$|#.*$)' in output." porque o VRP
pergunta "Continue? [Y/N]:" no `undo peer <grupo> enable` da address-family
v4 e o `send_config_set` do Netmiko só sabe esperar o prompt.

O `FakeVRP` imita a mecânica do canal do Netmiko (write_channel escreve, o
equipamento responde no buffer, read_until_pattern lê até casar), então o
que está sendo testado é o protocolo de leitura de verdade — não uma
simulação do resultado.
"""
import re
from unittest import mock

from django.test import SimpleTestCase

from clientes.bgp_actions import ErroEnvioBgp, _enviar_config_vrp, executar_acao_bgp


class TimeoutFake(Exception):
    """Equivalente ao netmiko.exceptions.ReadTimeout."""


class FakeVRP:
    RETURN = '\n'

    def __init__(self, perguntas=(), travar=None, prompt='[~VS-BGP]'):
        self.perguntas = set(perguntas)   # comandos que pedem confirmação
        self.travar = travar              # comando que nunca volta ao prompt
        self.prompt = prompt
        self.enviados = []
        self.buffer = ''
        self.entrou_em_config = False

    # ── API do Netmiko usada por _enviar_config_vrp ──
    def config_mode(self):
        self.entrou_em_config = True
        return self.prompt

    def normalize_cmd(self, comando):
        return comando.rstrip() + self.RETURN

    def write_channel(self, texto):
        comando = texto.strip()
        self.enviados.append(comando)
        if comando == 'Y':
            self.buffer += f'Y\n{self.prompt}'
        elif comando == self.travar:
            # Equipamento respondeu algo e ficou esperando — nunca volta ao prompt.
            self.buffer += f'{comando}\nWarning: aguardando algo que não vai chegar\n'
        elif comando in self.perguntas:
            self.buffer += (f'{comando}\nWarning: The operation will delete the configurations '
                            f'of the peer/peer group in the address family. Continue? [Y/N]:')
        else:
            self.buffer += f'{comando}\n{self.prompt}'

    def read_until_pattern(self, pattern, read_timeout=60, re_flags=0):
        m = re.search(pattern, self.buffer, flags=re_flags)
        if not m:
            raise TimeoutFake(f'Pattern not detected: {pattern!r} in output.')
        saida, self.buffer = self.buffer[:m.end()], self.buffer[m.end():]
        return saida


COMANDOS_IX = [
    'bgp 268080',
    'group EBGP-PTT-RS-V6 external',
    'peer 2001:12f8:0:6::a253 as-number 26162',
    'ipv4-family unicast',
    'undo peer EBGP-PTT-RS-V6 enable',
    'quit',
]


class EnviarConfigVrpTest(SimpleTestCase):

    def test_confirmacao_e_respondida_e_envio_continua(self):
        conn = FakeVRP(perguntas={'undo peer EBGP-PTT-RS-V6 enable'})
        saida = _enviar_config_vrp(conn, COMANDOS_IX)

        self.assertTrue(conn.entrou_em_config)
        # Todos os comandos foram enviados, com o "Y" entre o que pergunta e o seguinte.
        self.assertEqual(conn.enviados, [
            'bgp 268080',
            'group EBGP-PTT-RS-V6 external',
            'peer 2001:12f8:0:6::a253 as-number 26162',
            'ipv4-family unicast',
            'undo peer EBGP-PTT-RS-V6 enable',
            'Y',
            'quit',
        ])
        # O transcript guarda a pergunta e a resposta (é o que vai pra auditoria).
        self.assertIn('Continue? [Y/N]:', saida)
        self.assertIn('quit', saida)

    def test_sem_pergunta_nenhum_Y_e_enviado(self):
        conn = FakeVRP()
        _enviar_config_vrp(conn, COMANDOS_IX)
        self.assertEqual(conn.enviados, COMANDOS_IX)

    def test_varias_perguntas_seguidas(self):
        conn = FakeVRP(perguntas={'undo peer EBGP-PTT-RS-V6 enable', 'bgp 268080'})
        _enviar_config_vrp(conn, COMANDOS_IX)
        self.assertEqual(conn.enviados.count('Y'), 2)

    def test_comando_que_trava_diz_qual_foi_e_devolve_o_parcial(self):
        conn = FakeVRP(travar='ipv4-family unicast')
        with self.assertRaises(ErroEnvioBgp) as ctx:
            _enviar_config_vrp(conn, COMANDOS_IX)

        erro = ctx.exception
        self.assertEqual(erro.comando, 'ipv4-family unicast')
        self.assertIn('ipv4-family unicast', str(erro))
        # O que já tinha entrado antes de travar não se perde.
        self.assertIn('bgp 268080', erro.parcial)
        self.assertIn('peer 2001:12f8:0:6::a253 as-number 26162', erro.parcial)
        # E o que vinha depois não chegou a ser enviado.
        self.assertNotIn('quit', conn.enviados)

    def test_pergunta_repetida_nao_vira_laco_infinito(self):
        class SempreP(FakeVRP):
            def write_channel(self, texto):
                comando = texto.strip()
                self.enviados.append(comando)
                self.buffer += f'{comando}\nError: Continue? [Y/N]:'

        conn = SempreP()
        with self.assertRaises(ErroEnvioBgp):
            _enviar_config_vrp(conn, ['bgp 268080'])
        # 1 comando + no máximo 5 confirmações, e para.
        self.assertLessEqual(conn.enviados.count('Y'), 5)

    def test_prompt_pendente_e_subview_tambem_terminam_a_leitura(self):
        for prompt in ('[*VS-BGP-bgp-af-ipv6]', '<VS-BGP>', '[~VS-BGP-bgp]'):
            with self.subTest(prompt=prompt):
                conn = FakeVRP(prompt=prompt)
                _enviar_config_vrp(conn, ['bgp 268080'])
                self.assertEqual(conn.enviados, ['bgp 268080'])


class ExecutarAcaoBgpHuaweiTest(SimpleTestCase):
    """Fiação de `executar_acao_bgp`: Huawei tem que passar pelo envio
    comando a comando (e não pelo `send_config_set`, que é o que quebrava)."""

    def _conn(self, **kwargs):
        conn = FakeVRP(**kwargs)
        conn.commit = lambda: setattr(conn, 'commitou', True) or 'Info: commit ok'
        conn.commitou = False
        conn.disconnect = lambda: None
        return conn

    def _executar(self, conn, **kwargs):
        with mock.patch('clientes.bgp_actions._conectar_script', return_value=(conn, None)):
            return executar_acao_bgp('acesso-fake', 'huawei', COMANDOS_IX + ['commit'], **kwargs)

    def test_trial_usa_commit_trial_e_responde_a_confirmacao(self):
        conn = self._conn(perguntas={'undo peer EBGP-PTT-RS-V6 enable'})
        output, status = self._executar(conn, trial=True, trial_segundos=60)

        self.assertEqual(status, 'sucesso')
        self.assertIn('MODO TRIAL', output)
        self.assertIn('Y', conn.enviados)
        self.assertIn('commit trial 60', conn.enviados)
        # 'commit' literal continua fora do envio de config (viria duplicado).
        self.assertNotIn('commit', conn.enviados)
        self.assertFalse(conn.commitou)

    def test_sem_trial_fecha_com_commit_de_verdade(self):
        conn = self._conn(perguntas={'undo peer EBGP-PTT-RS-V6 enable'})
        output, status = self._executar(conn)

        self.assertEqual(status, 'sucesso')
        self.assertTrue(conn.commitou)
        self.assertNotIn('commit trial 60', conn.enviados)

    def test_erro_no_meio_devolve_o_parcial_e_o_comando(self):
        conn = self._conn(travar='ipv4-family unicast')
        output, status = self._executar(conn, trial=True, trial_segundos=60)

        self.assertEqual(status, 'erro')
        self.assertIn('bgp 268080', output)                    # o que entrou
        self.assertIn('ipv4-family unicast', output)           # onde parou
        self.assertNotIn('commit trial 60', conn.enviados)     # nada foi aplicado
