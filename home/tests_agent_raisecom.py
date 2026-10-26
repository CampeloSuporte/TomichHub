"""Agent NOC × Raisecom ROS: listagem de interfaces e sinal óptico (DDM).

Saídas abaixo capturadas de um RAX711-C-R-AC/S (ROS 6.5.2) em 26/10/2026.
"""
import asyncio
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from home import agent_engine as ae

DESCRIPTION = """Interface           Admin     Operate   Description
------------------------------------------------------------
LO1                 UP        UP        --
NULL0               UP        UP        --
TGE1/1/1            UP        UP        P2P-SW-CRS-CARAPINA-P2
TGE1/1/3            UP        UP        CNX.OLT-17
GE1/2/2             UP        UP        CLIENTE - BOTICARIO - CENTRAL
FE1/0/1             UP        DOWN      --
VLAN24              UP        UP        OSPF-SW-RAI-PARREIRAL
TGE1/1/1.1804       UP        UP        descricao 1804-QINQ-CARAPINA-OLT-10"""

DDM = """Port: tengigabitethernet1/1/3
ParaName            Unit                Value               Status
-------------------------------------------------------------------
Temperature         Celsius              35.128             *
Optical Tx Power    dBm                 -0.008              *
Optical Rx Power    dBm                 -6.126              *"""


def _acesso(fabricante='RAISECOM', nome='SW RAISECOM RAX700'):
    return SimpleNamespace(
        modelo=SimpleNamespace(fabricante=fabricante, nome=nome),
        tipo='SW SAO DIOGO', host='10.255.255.4', protocolo='SSH',
    )


class NormalizarDDMTest(SimpleTestCase):
    def test_nome_abreviado_vira_completo(self):
        self.assertEqual(ae._raisecom_normalizar_ddm('show transceiver ddm TGE1/1/3'),
                         'show transceiver ddm tengigabitethernet 1/1/3')

    def test_ge_e_gigaethernet_nao_gigabitethernet(self):
        self.assertEqual(ae._raisecom_normalizar_ddm('show transceiver ddm GE1/2/2'),
                         'show transceiver ddm gigaethernet 1/2/2')

    def test_sintaxe_cisco_e_ddm_faltando(self):
        for errado in ('show interfaces tengigabitethernet 1/1/1 transceiver',
                       'show transceiver tengigabitethernet1/1/1',
                       'show transceiver tengigabitethernet 1/1/1 ddm'):
            self.assertEqual(ae._raisecom_normalizar_ddm(errado),
                             'show transceiver ddm tengigabitethernet 1/1/1', errado)

    def test_information_continua_information(self):
        self.assertEqual(ae._raisecom_normalizar_ddm('show transceiver information TGE1/1/1'),
                         'show transceiver information tengigabitethernet 1/1/1')

    def test_nao_mexe_no_que_nao_e_transceiver_de_porta(self):
        self.assertIsNone(ae._raisecom_normalizar_ddm('show interface description'))
        self.assertIsNone(ae._raisecom_normalizar_ddm('show transceiver ddm'))
        self.assertIsNone(ae._raisecom_normalizar_ddm('show transceiver ddm tengigabitethernet 1/1/1.1804'))


class AnotarInterfacesTest(SimpleTestCase):
    def test_expande_e_marca(self):
        texto, fisicas = ae._raisecom_anotar_interfaces(DESCRIPTION)
        self.assertEqual(fisicas, ['tengigabitethernet 1/1/1', 'tengigabitethernet 1/1/3',
                                   'gigaethernet 1/2/2', 'fastethernet 1/0/1'])
        self.assertIn('tengigabitethernet 1/1/3 | UP UP CNX.OLT-17 [FÍSICA]', texto)
        self.assertIn('tengigabitethernet 1/1/1.1804 | UP UP descricao 1804-QINQ-CARAPINA-OLT-10 [SUB-INTERFACE]', texto)
        self.assertIn('VLAN24 | UP UP OSPF-SW-RAI-PARREIRAL [LÓGICA]', texto)
        self.assertIn('LO1 | UP UP -- [LÓGICA]', texto)
        # Cabeçalho fica como veio
        self.assertTrue(texto.startswith('Interface           Admin'))


class FabricanteTest(SimpleTestCase):
    def test_raisecom_tem_lista_segura_propria(self):
        self.assertTrue(ae._is_safe_command('show transceiver ddm tengigabitethernet 1/1/1', 'RAISECOM'))
        self.assertTrue(ae._is_safe_command('show interface description', 'raisecom'))
        self.assertFalse(ae._is_safe_command('display interface brief', 'raisecom'))
        self.assertFalse(ae._is_safe_command('reboot', 'raisecom'))

    def test_detecta_pelo_modelo_ou_tipo(self):
        self.assertTrue(ae._eh_raisecom(_acesso()))
        self.assertTrue(ae._eh_raisecom(_acesso(fabricante='', nome='ISCOM2924 Raisecom')))
        self.assertFalse(ae._eh_raisecom(_acesso(fabricante='Huawei', nome='S6730')))


class ExecuteCommandTest(SimpleTestCase):
    """Fluxo pelo _tool_execute_command com o SSH simulado."""

    def setUp(self):
        self.enviados = []

        def _ssh(acesso, comando):
            self.enviados.append(comando)
            if comando.startswith('show interface description'):
                return DESCRIPTION
            if comando.startswith('show transceiver ddm'):
                return DDM
            return ''
        self.enterContext(mock.patch.object(ae, '_ssh_exec_sync', side_effect=_ssh))

        async def _nada(*a, **k):
            return None

        async def _get_acesso(_id):
            return _acesso()

        self.engine = object.__new__(ae.AgentNOCEngine)
        self.engine.canal = 'whatsapp'
        self.engine.notify_cb = _nada
        self.engine._registrar_log = _nada
        self.engine._get_acesso = _get_acesso

    def _executar(self, comando):
        return asyncio.run(self.engine._tool_execute_command(681, comando))

    def test_ddm_com_nome_abreviado_e_corrigido_antes_de_enviar(self):
        saida = self._executar('show transceiver ddm TGE1/1/3')
        self.assertEqual(self.enviados, ['show transceiver ddm tengigabitethernet 1/1/3'])
        self.assertIn('Optical Rx Power', saida)

    def test_listagem_expande_nomes_e_indica_o_ddm_no_topo(self):
        saida = self._executar('show interface description')
        self.assertIn('[Raisecom: nomes expandidos.', saida.splitlines()[2])
        self.assertIn('`show transceiver ddm tengigabitethernet 1/1/1`', saida)
        self.assertIn('tengigabitethernet 1/1/3 | UP UP CNX.OLT-17 [FÍSICA]', saida)

    def test_include_busca_e_ja_traz_o_ddm_da_porta_fisica(self):
        saida = self._executar('show interface description | include olt-17')
        self.assertEqual(self.enviados, ['show interface description',
                                         'show transceiver ddm tengigabitethernet 1/1/3'])
        self.assertIn('tengigabitethernet 1/1/3 | UP UP CNX.OLT-17 [FÍSICA]', saida)
        self.assertIn('Optical Rx Power', saida)
        # A sub-interface também casa com "olt-1"... mas aqui só a física tem DDM
        self.assertNotIn('1/1/1.1804 [FÍSICA]', saida)


class SemDDMTest(SimpleTestCase):
    LEGENDA = ("If device is externally calibrated, only calibrated values are printed.\n"
               "Tx: transmit, Rx: receive, mA: milliamperes, dBm: millidecibel.")

    def test_porta_sem_ddm_explica_o_motivo(self):
        # Capturado da TGE1/1/3 do RAX711: cabo DAC Finisar de 3 m
        saida = ae._raisecom_ddm_explicado('show transceiver ddm tengigabitethernet 1/1/3', self.LEGENDA)
        self.assertIn('Sem valores de DDM para tengigabitethernet 1/1/3', saida)
        self.assertIn('`transceiver ddm enable`', saida)
        self.assertIn('`show transceiver information tengigabitethernet 1/1/3`', saida)

    def test_information_vazio_explica(self):
        # RAX700 da DS TECH: eco do comando e o prompt, nada no meio
        saida = ae._raisecom_ddm_explicado('show transceiver information tengigabitethernet 1/1/1', '')
        self.assertIn('não devolveu dados do módulo de tengigabitethernet 1/1/1', saida)

    def test_porta_com_ddm_fica_como_veio(self):
        self.assertEqual(ae._raisecom_ddm_explicado('show transceiver ddm tengigabitethernet 1/1/3', DDM), DDM)
