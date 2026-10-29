"""💡 Comandos úteis do terminal: família pelo cadastro, catálogo só de leitura, endpoint."""
import inspect
import json
import re
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, SimpleTestCase

from clientes import comandos_uteis as cu
from clientes import views


def _acesso(fabricante='', nome='', tipo=''):
    modelo = SimpleNamespace(fabricante=fabricante, nome=nome) if (fabricante or nome) else None
    return SimpleNamespace(id=7, modelo=modelo, tipo=tipo)


class IdentificarFamiliaTest(SimpleTestCase):
    def test_modelos_do_cadastro(self):
        casos = {
            ('Huawei', 'SW HUAWEI S6730'): 'huawei_vrp',
            ('Huawei', 'HUAWEI NE8000 M8'): 'huawei_vrp',
            ('Huawei', 'OLT HUAWEI MA5800X7'): 'huawei_olt',
            ('Huawei', 'Huawei MA5800-X7'): 'huawei_olt',
            ('Datacom', 'SW DATACOM DM4370'): 'datacom',
            ('Datacom', 'OLT DATACOM DM4615'): 'datacom_olt',
            ('Datacom', 'OLT DATACOM DM4610 8GPON'): 'datacom_olt',
            ('Cisco', 'CISCO ASR1002'): 'cisco_ios',
            ('Cisco', 'SW CISCO NEXUS3000'): 'cisco_nxos',
            ('Juniper', 'MX204'): 'juniper',
            ('Mikrotik', 'RB4011'): 'mikrotik',
            ('ZTE', 'OLT ZTE C300'): 'zte_c300',
            ('ZTE', 'ZTE C610 TITAN'): 'zte_titan',
            ('ZTE', 'OLT-ZXA10-C650'): 'zte_titan',
            ('RAISECOM', 'SW RAISECOM RAX700'): 'raisecom',
            ('debian', 'debian 12'): 'linux',
            ('UBUNTU', 'UBUNTU 24.04'): 'linux',
            ('PROXMOX', 'PROXMOX'): 'proxmox',
            ('Fiberhome', 'Fiberhome AN5516-06'): 'fiberhome_5k',
            ('FIBERHOME', 'OLT FIBERHOME AN6000-17'): 'fiberhome_6k',
        }
        for (fab, nome), esperado in casos.items():
            self.assertEqual(cu.identificar_familia(_acesso(fab, nome)), esperado, nome)

    def test_sem_modelo_usa_o_tipo(self):
        self.assertEqual(cu.identificar_familia(_acesso(tipo='MIKROTIK BORDA')), 'mikrotik')
        self.assertEqual(cu.identificar_familia(_acesso(tipo='OLT HUAWEI CENTRO')), 'huawei_olt')

    def test_fabricante_sem_catalogo(self):
        for fab, nome in (('PARKS', 'Fiberlink 40016'),
                          ('Intelbras', 'OLT INTELBRAS 8820i'), ('', '')):
            self.assertIsNone(cu.identificar_familia(_acesso(fab, nome)), nome)


class CatalogoTest(SimpleTestCase):
    # Só leitura: show/display/print/diagnóstico Linux, mais os comandos que
    # apenas mudam de modo/diretório ou desligam a paginação. Configuração só
    # por _cfg() (altera=True, ⚠ no painel).
    LEITURA = re.compile(
        r'^(show |sh |display |list |cd |telnet slot |/.+ (print|monitor|monitor-traffic)\b|'
        r'(uptime|free -m|df -h|ip -br a|ip r|ip r get |ss -tulpn|ping -c 4 |pveversion|qm list|pct list|pvesm status)|'
        r'top -bn1|systemctl (--failed|status )|journalctl -p err)')
    MODO = {'enable', 'config', 'quit', 'lll', 'terminal length 0', 'terminal page-break disable'}

    def test_todo_comando_e_de_leitura(self):
        for familia in cu.FAMILIAS:
            for grupo in cu.catalogo(familia)['grupos']:
                for c in grupo['comandos']:
                    if c['altera']:
                        continue
                    for linha in c['cmd'].split('\n'):
                        self.assertTrue(
                            linha in self.MODO or linha.startswith('interface gpon ') or self.LEITURA.match(linha),
                            f'{familia}: {linha!r}')

    def test_campos_conhecidos_e_listados(self):
        for familia in cu.FAMILIAS:
            for grupo in cu.catalogo(familia)['grupos']:
                for c in grupo['comandos']:
                    usados = set(re.findall(r'\{(\w+)\}', c['cmd']))
                    self.assertTrue(usados <= set(cu.CAMPOS), f'{familia}: {c["cmd"]}')
                    self.assertEqual(set(c['campos']), usados)

    def test_raisecom_usa_o_formato_conferido(self):
        cmds = [c['cmd'] for g in cu.catalogo('raisecom')['grupos'] for c in g['comandos']]
        self.assertIn('show transceiver ddm {porta}', cmds)
        self.assertTrue(all(c['conferido'] for g in cu.catalogo('raisecom')['grupos'] for c in g['comandos']))


class ConfiguracaoTest(SimpleTestCase):
    def test_so_fiberhome_tem_comando_que_altera(self):
        for familia in cu.FAMILIAS:
            tem = any(c['altera'] for g in cu.catalogo(familia)['grupos'] for c in g['comandos'])
            self.assertEqual(tem, familia.startswith('fiberhome'), familia)

    def test_fiberhome_set_e_apply_sempre_marcados(self):
        for familia in ('fiberhome_5k', 'fiberhome_6k'):
            for g in cu.catalogo(familia)['grupos']:
                for c in g['comandos']:
                    if re.search(r'^(set|apply|upload|download|upgrade|user|config)\b', c['cmd'], re.M):
                        self.assertTrue(c['altera'], c['cmd'])

    def test_fiberhome_por_assunto_sem_titulo_repetido(self):
        for familia in ('fiberhome_5k', 'fiberhome_6k'):
            titulos = [g['titulo'] for g in cu.catalogo(familia)['grupos']]
            self.assertEqual(len(titulos), len(set(titulos)), familia)
            self.assertFalse(any(t.startswith('⚠') for t in titulos), familia)

    def test_distancia_da_pon_em_cada_versao(self):
        def cmds(f):
            g = next(g for g in cu.catalogo(f)['grupos'] if g['titulo'] == 'PON')
            return ' '.join(c['cmd'] for c in g['comandos'])
        self.assertIn('set pon_distance slot {slot} pon {pon}', cmds('fiberhome_6k'))
        self.assertIn('interface pon 1/{slot}/{pon}', cmds('fiberhome_5k'))

    def test_fiberhome_sem_senha_fixa(self):
        texto = repr(cu.FAMILIAS['fiberhome_5k']) + repr(cu.FAMILIAS['fiberhome_6k'])
        self.assertNotIn('infocloud', texto)


class VersoesTest(SimpleTestCase):
    def test_fiberhome_tem_sub_menu_5500_e_6k(self):
        for familia in ('fiberhome_5k', 'fiberhome_6k'):
            v = cu.catalogo(familia)['versoes']
            self.assertEqual([x['familia'] for x in v], ['fiberhome_5k', 'fiberhome_6k'])
        self.assertEqual(cu.catalogo('raisecom')['versoes'], [])

    def test_seletor_tipo_mostra_a_linha_uma_vez(self):
        opcoes = cu.lista_familias()
        fh = [o for o in opcoes if 'fiberhome_5k' in o['membros']]
        self.assertEqual(len(fh), 1)
        self.assertEqual(fh[0]['membros'], ['fiberhome_5k', 'fiberhome_6k'])
        self.assertFalse(any(o['familia'] == 'fiberhome_6k' for o in opcoes))
        # Toda família continua alcançável pelo seletor ou pelo sub-menu
        self.assertEqual({m for o in opcoes for m in o['membros']}, set(cu.FAMILIAS))


class EndpointTest(SimpleTestCase):
    def setUp(self):
        self.view = inspect.unwrap(views.comandos_uteis_acesso)
        self.rf = RequestFactory()
        self.acesso = _acesso('RAISECOM', 'SW RAISECOM RAX700', 'SW SAO DIOGO')
        self.enterContext(mock.patch.object(views, 'get_object_or_404', return_value=self.acesso))
        self.pode = self.enterContext(mock.patch.object(views._perms, 'pode_acessar_acesso', return_value=True))

    def _get(self, qs=''):
        req = self.rf.get('/clientes/acessos/7/comandos-uteis/' + qs)
        req.user = SimpleNamespace(is_authenticated=True)
        return self.view(req, 7)

    def test_identifica_e_devolve_o_catalogo(self):
        d = json.loads(self._get().content)
        self.assertEqual(d['detectada'], 'raisecom')
        self.assertEqual(d['catalogo']['familia'], 'raisecom')
        self.assertEqual(d['equipamento']['modelo'], 'SW RAISECOM RAX700')
        self.assertIn('porta', d['campos'])

    def test_familia_escolhida_pelo_operador(self):
        d = json.loads(self._get('?familia=huawei_vrp').content)
        self.assertEqual(d['detectada'], 'raisecom')
        self.assertEqual(d['catalogo']['familia'], 'huawei_vrp')

    def test_familia_invalida_volta_para_a_identificada(self):
        d = json.loads(self._get('?familia=nao-existe').content)
        self.assertEqual(d['catalogo']['familia'], 'raisecom')

    def test_sem_permissao(self):
        self.pode.return_value = False
        self.assertEqual(self._get().status_code, 403)


class DatacomInterfaceLinkTest(SimpleTestCase):
    def test_show_interface_link_primeiro_nas_interfaces_e_conferido(self):
        for familia in ('datacom', 'datacom_olt'):
            grupo = next(g for g in cu.catalogo(familia)['grupos'] if g['titulo'] == 'Interfaces')
            self.assertEqual(grupo['comandos'][0]['cmd'], 'show interface link', familia)
            self.assertTrue(grupo['comandos'][0]['conferido'], familia)
