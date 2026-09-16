import inspect
import json
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from clientes import ipam_views
from clientes.models import Cliente, IPAMEndereco, IPAMPrefixo, IPAMSubRede

# Chama a view crua: os decorators (login/ferramenta) e a checagem de dono
# são cobertos em outro lugar — aqui interessa só o que a exclusão apaga.
prefixo_deletar = inspect.unwrap(ipam_views.ipam_prefixo_deletar)
subrede_deletar = inspect.unwrap(ipam_views.ipam_subrede_deletar)
subrede_salvar = inspect.unwrap(ipam_views.ipam_subrede_salvar)


@mock.patch('clientes.ipam_views._checar_obj_cliente', lambda r, o: None)
class IPAMExclusaoBlocoTest(TestCase):
    def setUp(self):
        self.c = Cliente.objects.create(
            nome_empresa='Cliente IPAM', cnpj='22.222.222/0001-22',
            endereco='Rua 1', email='ipam@example.com',
        )
        self.rf = RequestFactory()

    def _post(self, view, obj_id, body=None):
        req = self.rf.post('/', data=json.dumps(body or {}), content_type='application/json')
        req.user = AnonymousUser()
        return json.loads(view(req, obj_id).content)

    def _quebrar(self, prefixo, rede, pl, **kw):
        import ipaddress
        for n in ipaddress.ip_network(rede).subnets(new_prefix=pl):
            IPAMSubRede.objects.create(cliente=self.c, prefixo=prefixo, rede=str(n), **kw)

    def test_excluir_prefixo_leva_os_30_quebrados(self):
        p = IPAMPrefixo.objects.create(cliente=self.c, prefixo='45.161.248.0/24')
        self._quebrar(p, '45.161.248.0/24', 30)
        IPAMSubRede.objects.create(cliente=self.c, rede='45.161.249.0/30')  # fora do bloco

        previa = self._post(prefixo_deletar, p.id, {'previa': True})
        self.assertEqual(previa['subredes'], 64)
        self.assertTrue(IPAMPrefixo.objects.filter(id=p.id).exists())

        self.assertTrue(self._post(prefixo_deletar, p.id)['ok'])
        self.assertFalse(IPAMPrefixo.objects.filter(id=p.id).exists())
        self.assertEqual(list(IPAMSubRede.objects.filter(rede__startswith="45.").values_list("rede", flat=True)), ["45.161.249.0/30"])

    def test_excluir_prefixo_leva_orfas_e_ips(self):
        p = IPAMPrefixo.objects.create(cliente=self.c, prefixo='45.161.248.0/24')
        s = IPAMSubRede.objects.create(cliente=self.c, rede='45.161.248.4/30')  # sem FK
        IPAMEndereco.objects.create(cliente=self.c, subrede=s, ip='45.161.248.5')

        self.assertEqual(self._post(prefixo_deletar, p.id, {'previa': True})['ips'], 1)
        self._post(prefixo_deletar, p.id)
        self.assertFalse(IPAMSubRede.objects.filter(rede__startswith="45.").exists())
        self.assertFalse(IPAMEndereco.objects.exists())

    def test_prefixo_filho_e_suas_subredes_ficam_e_sobem_pro_avo(self):
        avo = IPAMPrefixo.objects.create(cliente=self.c, prefixo='45.160.0.0/15')
        p = IPAMPrefixo.objects.create(cliente=self.c, prefixo='45.161.248.0/22', pai=avo)
        filho = IPAMPrefixo.objects.create(cliente=self.c, prefixo='45.161.248.0/24', pai=p)
        self._quebrar(filho, '45.161.248.0/24', 26)
        IPAMSubRede.objects.create(cliente=self.c, rede='45.161.250.0/24', prefixo=p)

        self._post(prefixo_deletar, p.id)
        filho.refresh_from_db()
        self.assertEqual(filho.pai_id, avo.id)
        self.assertEqual(IPAMSubRede.objects.filter(rede__startswith="45.").count(), 4)
        self.assertFalse(IPAMSubRede.objects.filter(rede='45.161.250.0/24').exists())

    def test_excluir_subrede_leva_as_quebradas_dentro_dela(self):
        pai = IPAMPrefixo.objects.create(cliente=self.c, prefixo='45.160.0.0/15')
        s24 = IPAMSubRede.objects.create(cliente=self.c, prefixo=pai, rede='45.161.248.0/24')
        self._quebrar(pai, '45.161.248.0/24', 30)
        vizinha = IPAMSubRede.objects.create(cliente=self.c, prefixo=pai, rede='45.161.249.0/24')

        self.assertEqual(self._post(subrede_deletar, s24.id, {'previa': True})['subredes'], 65)
        self._post(subrede_deletar, s24.id)
        self.assertEqual(list(IPAMSubRede.objects.filter(rede__startswith="45.").values_list("id", flat=True)), [vizinha.id])

    def test_nao_cria_subrede_duplicada(self):
        IPAMSubRede.objects.create(cliente=self.c, rede='45.161.248.0/24')
        req = self.rf.post('/', data=json.dumps({'rede': '45.161.248.0/24'}), content_type='application/json')
        req.user = AnonymousUser()
        with mock.patch('clientes.ipam_views._cliente', return_value=self.c):
            resp = subrede_salvar(req, self.c.id)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(IPAMSubRede.objects.filter(rede__startswith="45.").count(), 1)
