"""L2VPN como transporte de empresa (Documentação de Rede → L2VPN / Empresas).

Os serviços L2VPN não ficam no banco — saem do backup a cada leitura — então
o vínculo com a empresa é pela identidade do serviço no switch (`chave`).
Aqui o backup é simulado direto em `_l2vpn_servicos_do_acesso`.
"""
import json
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clientes.models import Acesso, Cliente, L2vpnEmpresa, L2vpnTransporte
from usuario.models import TOTPDevice


def _servico(id_, nome, peer_ip):
    return {
        'tipo': 'vpls', 'tecnologia': 'VSI', 'nome': nome, 'id': id_, 'vlan': '300',
        'mtu': '9000', 'vendor': 'huawei',
        'peers': [{'ip': peer_ip}],
        'interfaces': [{'nome': 'GE0/0/1.300', 'vlan': '300', 'descricao': 'EMPRESA X'}],
    }


class _Log:
    """Basta o que as views usam do BackupLog (data e nome do arquivo)."""
    def __init__(self):
        from django.utils import timezone
        self.data_backup = timezone.now()
        self.arquivo_path = 'backups/sw.cfg'


class L2vpnEmpresasTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nome_empresa='Provedor L2', cnpj='66.666.666/0001-66',
            endereco='Rua L2, 1', email='l2@example.com',
        )
        self.sw_a = Acesso.objects.create(
            cliente=self.cliente, tipo='SW-A', host='10.0.0.1', porta=22,
            protocolo='SSH', usuario='u', senha='s',
        )
        self.sw_b = Acesso.objects.create(
            cliente=self.cliente, tipo='SW-B', host='10.0.0.2', porta=22,
            protocolo='SSH', usuario='u', senha='s',
        )
        self.servicos = {
            self.sw_a.id: [_servico('300', 'EMPRESA-X', '10.255.0.2'), _servico('301', 'OUTRA', '10.255.0.2')],
            self.sw_b.id: [_servico('300', 'EMPRESA-X', '10.255.0.1')],
        }
        self.admin = User.objects.create_user('admin_l2', password='x', is_staff=True, is_superuser=True)
        TOTPDevice.objects.create(usuario=self.admin, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(self.admin)

        p1 = mock.patch('clientes.views._l2vpn_servicos_do_acesso',
                        side_effect=lambda a: (self.servicos.get(a.id, []), _Log()))
        p2 = mock.patch('clientes.views._l2vpn_mapa_identidade', return_value={
            '10.255.0.1': {'acesso_id': self.sw_a.id, 'nome': 'SW-A', 'host': '10.0.0.1'},
            '10.255.0.2': {'acesso_id': self.sw_b.id, 'nome': 'SW-B', 'host': '10.0.0.2'},
        })
        p3 = mock.patch('clientes.views._l2vpn_ips_identidade', return_value={})
        p4 = mock.patch('clientes.views._l2vpn_ldp_do_acesso', return_value={})
        for p in (p1, p2, p3, p4):
            p.start()
            self.addCleanup(p.stop)

    def _post(self, url, corpo):
        return self.client.post(url, data=json.dumps(corpo), content_type='application/json')

    def _marcar(self, acesso, chave, **corpo):
        return self._post(reverse('l2vpn_transporte_marcar', args=[acesso.id]), dict(chave=chave, **corpo))

    def test_servicos_do_switch_vem_com_chave_e_sem_transporte(self):
        d = self.client.get(reverse('l2vpn_backup_acesso', args=[self.sw_a.id])).json()
        self.assertEqual(d['servicos'][0]['chave'], 'VSI|300|EMPRESA-X')
        self.assertIsNone(d['servicos'][0]['transporte'])

    def test_marcar_com_nome_novo_cria_empresa_e_aparece_no_switch(self):
        r = self._marcar(self.sw_a, 'VSI|300|EMPRESA-X', empresa_nome='Empresa X Ltda')
        self.assertEqual(r.status_code, 200)
        empresa = L2vpnEmpresa.objects.get(cliente=self.cliente, nome='Empresa X Ltda')
        t = L2vpnTransporte.objects.get(acesso=self.sw_a)
        self.assertEqual((t.empresa, t.servico_id, t.servico_nome, t.vlan), (empresa, '300', 'EMPRESA-X', '300'))

        d = self.client.get(reverse('l2vpn_backup_acesso', args=[self.sw_a.id])).json()
        self.assertEqual(d['servicos'][0]['transporte']['empresa_nome'], 'Empresa X Ltda')
        self.assertIsNone(d['servicos'][1]['transporte'])

        sw = self.client.get(reverse('l2vpn_switches_cliente', args=[self.cliente.id])).json()
        # Acessos de teste não têm "switch" no nome → listagem vazia, mas sem erro.
        self.assertIn('switches', sw)

    def test_nome_existente_reaproveita_empresa(self):
        L2vpnEmpresa.objects.create(cliente=self.cliente, nome='Empresa X')
        self._marcar(self.sw_a, 'VSI|300|EMPRESA-X', empresa_nome='empresa x')
        self.assertEqual(L2vpnEmpresa.objects.filter(cliente=self.cliente).count(), 1)

    def test_detalhe_da_empresa_lista_as_duas_pontas_com_peer(self):
        empresa = L2vpnEmpresa.objects.create(cliente=self.cliente, nome='Empresa X')
        self._marcar(self.sw_a, 'VSI|300|EMPRESA-X', empresa_id=empresa.id)
        self._marcar(self.sw_b, 'VSI|300|EMPRESA-X', empresa_id=empresa.id, observacao='ponta B')

        d = self.client.get(reverse('l2vpn_empresa_detalhe', args=[empresa.id])).json()
        self.assertEqual(d['empresa']['transportes'], 2)
        por_switch = {t['acesso']['nome']: t for t in d['transportes']}
        self.assertEqual(por_switch['SW-A']['peers'][0]['destino']['nome'], 'SW-B')
        self.assertEqual(por_switch['SW-B']['peers'][0]['destino']['nome'], 'SW-A')
        self.assertEqual(por_switch['SW-B']['observacao'], 'ponta B')
        self.assertTrue(all(t['encontrado'] for t in d['transportes']))

        lista = self.client.get(reverse('l2vpn_empresas_cliente', args=[self.cliente.id])).json()
        self.assertEqual(lista['empresas'][0]['transportes'], 2)

    def test_servico_que_sumiu_do_backup_continua_no_detalhe(self):
        empresa = L2vpnEmpresa.objects.create(cliente=self.cliente, nome='Empresa X')
        self._marcar(self.sw_a, 'VSI|301|OUTRA', empresa_id=empresa.id)
        self.servicos[self.sw_a.id] = self.servicos[self.sw_a.id][:1]

        t = self.client.get(reverse('l2vpn_empresa_detalhe', args=[empresa.id])).json()['transportes'][0]
        self.assertFalse(t['encontrado'])
        self.assertEqual((t['servico_id'], t['nome']), ('301', 'OUTRA'))

    def test_trocar_empresa_e_desmarcar(self):
        a = L2vpnEmpresa.objects.create(cliente=self.cliente, nome='A')
        b = L2vpnEmpresa.objects.create(cliente=self.cliente, nome='B')
        self._marcar(self.sw_a, 'VSI|300|EMPRESA-X', empresa_id=a.id)
        self._marcar(self.sw_a, 'VSI|300|EMPRESA-X', empresa_id=b.id)
        self.assertEqual(L2vpnTransporte.objects.get().empresa, b)

        self._marcar(self.sw_a, 'VSI|300|EMPRESA-X')
        self.assertFalse(L2vpnTransporte.objects.exists())

    def test_servico_inexistente_recusa(self):
        r = self._marcar(self.sw_a, 'VSI|999|NAO', empresa_nome='X')
        self.assertEqual(r.status_code, 404)
        self.assertFalse(L2vpnEmpresa.objects.exists())

    def test_empresa_de_outro_cliente_recusa(self):
        outro = Cliente.objects.create(nome_empresa='Outro', cnpj='77.777.777/0001-77',
                                       endereco='R', email='o@example.com')
        alheia = L2vpnEmpresa.objects.create(cliente=outro, nome='Alheia')
        r = self._marcar(self.sw_a, 'VSI|300|EMPRESA-X', empresa_id=alheia.id)
        self.assertEqual(r.status_code, 404)

    def test_salvar_duplicada_e_excluir_em_cascata(self):
        url = reverse('l2vpn_empresa_salvar', args=[self.cliente.id])
        self.assertEqual(self._post(url, {'nome': 'Empresa X', 'documento': '1'}).status_code, 200)
        self.assertEqual(self._post(url, {'nome': 'EMPRESA X'}).status_code, 400)
        empresa = L2vpnEmpresa.objects.get()
        self._marcar(self.sw_a, 'VSI|300|EMPRESA-X', empresa_id=empresa.id)

        r = self._post(reverse('l2vpn_empresa_deletar', args=[empresa.id]), {})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(L2vpnTransporte.objects.exists())

    def test_portal_do_cliente_so_consulta(self):
        empresa = L2vpnEmpresa.objects.create(cliente=self.cliente, nome='Empresa X')
        portal = User.objects.create_user('portal_l2', password='x')
        self.cliente.usuario = portal
        self.cliente.save()
        TOTPDevice.objects.create(usuario=portal, secret='JBSWY3DPEHPK3PXP', confirmado=True)
        self.client.force_login(portal)

        r = self._marcar(self.sw_a, 'VSI|300|EMPRESA-X', empresa_id=empresa.id)
        self.assertIn(r.status_code, (302, 403))
        self.assertFalse(L2vpnTransporte.objects.exists())
