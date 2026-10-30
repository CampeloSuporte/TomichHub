"""Renomear o acesso chega aos mapas do cliente, sem pisar em rótulo editado à mão."""
import json

from django.test import TestCase

from clientes.models import Acesso, Cliente, TopologiaDiagrama


class RenomearHostNaTopologiaTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nome_empresa='Provedor Teste', cnpj='11.111.111/0001-11',
                                              endereco='Rua 1', email='prov@example.com')
        self.acesso = self.criar_acesso('SW-ANTIGO')
        self.outro = self.criar_acesso('SW-ANTIGO')

    def criar_acesso(self, tipo, cliente=None):
        return Acesso.objects.create(cliente=cliente or self.cliente, tipo=tipo, host='10.0.0.1', porta=22,
                                     protocolo='SSH', usuario='u', senha='s')

    def mapa(self, nodes, **kwargs):
        return TopologiaDiagrama.objects.create(cliente=kwargs.pop('cliente', self.cliente),
                                                dados_json=json.dumps({'nodes': nodes, 'links': []}), **kwargs)

    def node(self, acesso, label):
        return {'id': f'crm_{acesso.id}', 'acesso_id': acesso.id, 'label': label}

    def labels(self, mapa):
        mapa.refresh_from_db()
        return [n['label'] for n in json.loads(mapa.dados_json)['nodes']]

    def renomear(self, nome='SW-NOVO'):
        self.acesso.tipo = nome
        self.acesso.save()

    def test_renomeia_no_mapa_e_no_submapa(self):
        raiz = self.mapa([self.node(self.acesso, 'SW-ANTIGO')])
        sub = self.mapa([self.node(self.acesso, 'SW-ANTIGO')], pai=raiz)
        self.renomear()
        self.assertEqual(self.labels(raiz), ['SW-NOVO'])
        self.assertEqual(self.labels(sub), ['SW-NOVO'])

    def test_renomeia_membro_de_grupo(self):
        raiz = self.mapa([{'id': 'g1', 'grupo': True, 'label': 'POP',
                           'grupo_membros': [self.node(self.acesso, 'SW-ANTIGO')]}])
        self.renomear()
        raiz.refresh_from_db()
        grupo = json.loads(raiz.dados_json)['nodes'][0]
        self.assertEqual(grupo['grupo_membros'][0]['label'], 'SW-NOVO')

    def test_rotulo_editado_a_mao_e_outro_host_de_mesmo_nome_ficam(self):
        raiz = self.mapa([self.node(self.acesso, 'Nome do mapa'), self.node(self.outro, 'SW-ANTIGO')])
        self.renomear()
        self.assertEqual(self.labels(raiz), ['Nome do mapa', 'SW-ANTIGO'])

    def test_mapa_de_outro_cliente_nao_e_tocado(self):
        outro_cliente = Cliente.objects.create(nome_empresa='Outro', cnpj='22.222.222/0001-22',
                                               endereco='Rua 2', email='o@example.com')
        mapa = self.mapa([{'id': f'crm_{self.acesso.id}', 'acesso_id': self.acesso.id, 'label': 'SW-ANTIGO'}],
                         cliente=outro_cliente)
        self.renomear()
        self.assertEqual(self.labels(mapa), ['SW-ANTIGO'])

    def test_salvar_sem_renomear_nao_grava_o_mapa(self):
        raiz = self.mapa([self.node(self.acesso, 'SW-ANTIGO')])
        antes = TopologiaDiagrama.objects.get(pk=raiz.pk).atualizado_em
        self.acesso.porta = 2222
        self.acesso.save()
        self.assertEqual(TopologiaDiagrama.objects.get(pk=raiz.pk).atualizado_em, antes)

    def test_mapa_com_json_invalido_nao_impede_salvar_o_acesso(self):
        TopologiaDiagrama.objects.create(cliente=self.cliente, dados_json='{quebrado')
        bom = self.mapa([self.node(self.acesso, 'SW-ANTIGO')])
        with self.assertLogs('clientes.models', level='ERROR'):
            self.renomear()
        self.acesso.refresh_from_db()
        self.assertEqual(self.acesso.tipo, 'SW-NOVO')
        self.assertEqual(self.labels(bom), ['SW-NOVO'])
