"""Diagrama de transportes da empresa (Documentação de Rede → Empresas)."""
from io import BytesIO
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from PIL import Image

from clientes.models import Acesso, Cliente, L2vpnEmpresa, L2vpnTransporte
from clientes.transportes_diagrama import agrupar_blocos, gerar_pdf, gerar_png, montar_ligacoes
from usuario.models import TOTPDevice


def _item(origem, destino, servico_id, tipo='vpls', tecnologia='VSI', nome='TR', encontrado=True):
    peers = [{'ip': '10.0.0.9', 'destino': {'acesso_id': destino, 'nome': f'H{destino}'} if destino else None}]
    return {
        'acesso': {'id': origem, 'nome': f'H{origem}', 'host': ''},
        'tipo': tipo, 'tecnologia': tecnologia, 'servico_id': servico_id, 'nome': nome,
        'encontrado': encontrado, 'peers': peers if encontrado else [],
    }


def _enlace(a, b, link_id='L1'):
    return {
        'link_id': link_id, 'label': 'BB-01', 'iface': '', 'circuito': 'C-9', 'observacao': '',
        'a': {'label': f'H{a}', 'acesso_id': a, 'ip_gerencia': ''},
        'b': {'label': f'H{b}', 'acesso_id': b, 'ip_gerencia': ''},
    }


class LigacoesTest(SimpleTestCase):
    def test_mesmo_servico_nas_duas_pontas_vira_uma_linha(self):
        ligacoes, _ = montar_ligacoes([_item(1, 2, '10'), _item(2, 1, '10', nome='OUTRO NOME')], [])
        self.assertEqual(len(ligacoes), 1)
        self.assertEqual([l['rotulo'] for l in ligacoes[0]['linhas']], ['VSI 10 - TR'])

    def test_dois_servicos_do_mesmo_par_ficam_em_ordem_numerica(self):
        ligacoes, _ = montar_ligacoes([_item(1, 2, '100'), _item(1, 2, '9', tipo='vpws', tecnologia='L2L')], [])
        self.assertEqual([l['rotulo'] for l in ligacoes[0]['linhas']], ['L2L 9 - TR', 'VSI 100 - TR'])
        self.assertEqual(ligacoes[0]['linhas'][0]['estilo'], 'p2p')

    def test_peer_nao_identificado_aparece_com_o_ip(self):
        ligacoes, _ = montar_ligacoes([_item(1, None, '10')], [])
        self.assertIn('10.0.0.9 (não identificado)', ligacoes[0]['nos'].values())

    def test_sem_a_outra_ponta_nao_some_do_arquivo(self):
        sem_peer = dict(_item(1, 2, '11'), peers=[])
        ligacoes, sem_ponta = montar_ligacoes([_item(1, 2, '10', encontrado=False), sem_peer], [])
        self.assertEqual(ligacoes, [])
        self.assertEqual([m for _n, _r, m in sem_ponta], ['não está mais no backup atual', 'sem peer'])

    def test_enlace_da_topologia_entra_com_o_circuito(self):
        ligacoes, _ = montar_ligacoes([], [_enlace(1, 2)])
        self.assertEqual(ligacoes[0]['linhas'][0]['rotulo'], 'Enlace BB-01 - C-9')

    def test_site_com_quatro_destinos_vira_estrela_e_o_resto_fica_em_pares(self):
        itens = [_item(1, d, str(d)) for d in (2, 3, 4, 5)] + [_item(5, 6, '20'), _item(6, 7, '21')]
        blocos = agrupar_blocos(montar_ligacoes(itens, [])[0])
        self.assertEqual([len(b['ligacoes']) for b in blocos], [4, 1, 1])
        self.assertEqual(blocos[0]['centro'], 'a:1')

    def test_tres_destinos_continuam_em_pares(self):
        blocos = agrupar_blocos(montar_ligacoes([_item(1, d, str(d)) for d in (2, 3, 4)], [])[0])
        self.assertEqual([len(b['ligacoes']) for b in blocos], [1, 1, 1])

    def test_arquivos_saem_validos_e_sem_transporte_tambem(self):
        itens = [_item(1, d, str(d)) for d in (2, 3, 4, 5)] + [_item(1, 2, '30', encontrado=False)]
        for transportes in (itens, []):
            img = Image.open(BytesIO(gerar_png('Transportes: Ação x Coração', transportes, [_enlace(8, 9)] if transportes else [])))
            self.assertEqual(img.format, 'PNG')
            self.assertTrue(gerar_pdf('Transportes: Ação x Coração', transportes, []).startswith(b'%PDF'))


class DiagramaViewTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nome_empresa='Provedor', cnpj='66.666.666/0001-66', endereco='Rua', email='p@example.com')
        self.acesso = Acesso.objects.create(
            cliente=self.cliente, tipo='SW-A', host='10.0.0.1', porta=22, protocolo='SSH', usuario='u', senha='s')
        self.empresa = L2vpnEmpresa.objects.create(cliente=self.cliente, nome='Claro Nortão')
        L2vpnTransporte.objects.create(empresa=self.empresa, acesso=self.acesso, chave='VSI|10|X', tipo='vpls',
                                       tecnologia='VSI', servico_id='10', servico_nome='X')
        self.admin = User.objects.create_user('admin_diag', password='x', is_staff=True, is_superuser=True)
        TOTPDevice.objects.create(usuario=self.admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(self.admin)
        for alvo, retorno in (('_l2vpn_servicos_do_acesso', ([], None)), ('_l2vpn_mapa_identidade', {})):
            p = mock.patch(f'clientes.views.{alvo}', return_value=retorno)
            p.start()
            self.addCleanup(p.stop)

    def test_baixa_png_e_pdf(self):
        for formato, tipo in (('png', 'image/png'), ('pdf', 'application/pdf')):
            r = self.client.get(f'/clientes/ipam/l2vpn/empresas/{self.empresa.id}/diagrama.{formato}')
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r['Content-Type'], tipo)
            self.assertIn(f'transportes-claro-nortao.{formato}', r['Content-Disposition'])

    def test_formato_desconhecido_nao_existe(self):
        r = self.client.get(f'/clientes/ipam/l2vpn/empresas/{self.empresa.id}/diagrama.exe')
        self.assertEqual(r.status_code, 404)

    def test_quem_nao_acessa_o_cliente_recebe_403(self):
        with mock.patch('clientes.views._perms.pode_acessar_cliente', return_value=False):
            r = self.client.get(f'/clientes/ipam/l2vpn/empresas/{self.empresa.id}/diagrama.png')
        self.assertEqual(r.status_code, 403)
