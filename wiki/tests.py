from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from usuario.models import (
    Instancia, InstanciaFerramenta, PerfilUsuario, TOTPDevice,
)
from wiki.models import ArtigoWiki, CategoriaWiki


def _com_2fa(user):
    # Forcar2FAMiddleware manda quem é back-office sem TOTP confirmado pra
    # tela de 2FA; sem isso a requisição vira 302 antes de chegar na view.
    TOTPDevice.objects.create(usuario=user, secret='JBSWY3DPEHPK3PXP', confirmado=True)
    return user


def _consultor(username, wiki_habilitada):
    instancia = Instancia.objects.create(nome=f'inst-{username}')
    user = User.objects.create_user(username=username, password='x', is_staff=False)
    PerfilUsuario.objects.create(
        usuario=user, role=PerfilUsuario.ROLE_CONSULTOR, instancia=instancia,
    )
    InstanciaFerramenta.objects.create(
        instancia=instancia, ferramenta='wiki', habilitado=wiki_habilitada,
    )
    return _com_2fa(user)


class WikiPermissaoConsultorTest(TestCase):
    """Consultor passou a criar/editar artigo (14/08/2026); excluir segue só
    com Administrador, porque a base da Wiki é global a todas as instâncias."""

    def setUp(self):
        self.categoria = CategoriaWiki.objects.create(nome='Redes', slug='redes')
        self.artigo = ArtigoWiki.objects.create(
            titulo='Artigo Base', categoria=self.categoria,
            fabricante='huawei', descricao_curta='x', conteudo='y',
        )

    def _payload(self, titulo):
        return {
            'titulo': titulo, 'categoria': self.categoria.id, 'fabricante': 'huawei',
            'descricao_curta': 'desc', 'conteudo': 'conteudo do artigo',
        }

    def test_consultor_com_wiki_liberada_cria_artigo(self):
        self.client.force_login(_consultor('c_ok', wiki_habilitada=True))
        resp = self.client.post(
            reverse('wiki:cadastrar_artigo'), self._payload('Criado pelo Consultor'),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ArtigoWiki.objects.filter(titulo='Criado pelo Consultor').exists())

    def test_consultor_com_wiki_liberada_edita_artigo(self):
        self.client.force_login(_consultor('c_edit', wiki_habilitada=True))
        resp = self.client.post(
            reverse('wiki:editar_artigo', args=[self.artigo.slug]),
            self._payload('Titulo Editado'),
        )
        self.assertEqual(resp.status_code, 302)
        self.artigo.refresh_from_db()
        self.assertEqual(self.artigo.titulo, 'Titulo Editado')

    def test_consultor_sem_wiki_liberada_nao_cria(self):
        self.client.force_login(_consultor('c_off', wiki_habilitada=False))
        resp = self.client.post(
            reverse('wiki:cadastrar_artigo'), self._payload('Nao Deveria Existir'),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ArtigoWiki.objects.filter(titulo='Nao Deveria Existir').exists())

    def test_consultor_nao_exclui_artigo(self):
        self.client.force_login(_consultor('c_del', wiki_habilitada=True))
        resp = self.client.post(reverse('wiki:deletar_artigo', args=[self.artigo.slug]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ArtigoWiki.objects.filter(pk=self.artigo.pk).exists())

    def test_operador_continua_so_com_leitura(self):
        instancia = Instancia.objects.create(nome='inst-op')
        InstanciaFerramenta.objects.create(
            instancia=instancia, ferramenta='wiki', habilitado=True,
        )
        operador = User.objects.create_user(username='op', password='x', is_staff=False)
        PerfilUsuario.objects.create(
            usuario=operador, role=PerfilUsuario.ROLE_OPERADOR, instancia=instancia,
        )
        self.client.force_login(_com_2fa(operador))

        self.assertEqual(
            self.client.get(reverse('wiki:visualizar_artigo', args=[self.artigo.slug])).status_code,
            200,
        )
        self.client.post(reverse('wiki:cadastrar_artigo'), self._payload('Do Operador'))
        self.assertFalse(ArtigoWiki.objects.filter(titulo='Do Operador').exists())

    def test_admin_continua_criando_e_excluindo(self):
        admin = User.objects.create_user(username='adm', password='x', is_staff=True)
        PerfilUsuario.objects.create(usuario=admin, role=PerfilUsuario.ROLE_ADMIN)
        self.client.force_login(_com_2fa(admin))

        self.client.post(reverse('wiki:cadastrar_artigo'), self._payload('Do Admin'))
        self.assertTrue(ArtigoWiki.objects.filter(titulo='Do Admin').exists())

        self.client.post(reverse('wiki:deletar_artigo', args=[self.artigo.slug]))
        self.assertFalse(ArtigoWiki.objects.filter(pk=self.artigo.pk).exists())


class ListarPorCategoriaFabricanteTest(TestCase):
    """Dentro de uma categoria, filtrar também por fabricante — só os
    fabricantes que realmente têm artigo nesta categoria aparecem como
    opção, com a contagem certa, e o filtro não vaza pra outras categorias."""

    def setUp(self):
        self.categoria = CategoriaWiki.objects.create(nome='BGP', slug='bgp')
        self.outra_categoria = CategoriaWiki.objects.create(nome='VPN', slug='vpn')

        self.mk1 = ArtigoWiki.objects.create(
            titulo='BGP no MikroTik', categoria=self.categoria,
            fabricante='MIKROTIK', descricao_curta='x', conteudo='y',
        )
        self.mk2 = ArtigoWiki.objects.create(
            titulo='BGP no MikroTik CHR', categoria=self.categoria,
            fabricante='MIKROTIK', descricao_curta='x', conteudo='y',
        )
        self.huawei = ArtigoWiki.objects.create(
            titulo='BGP na Huawei', categoria=self.categoria,
            fabricante='HUAWEI', descricao_curta='x', conteudo='y',
        )
        # Mesmo fabricante, mas em outra categoria — não pode contar aqui.
        ArtigoWiki.objects.create(
            titulo='VPN no MikroTik', categoria=self.outra_categoria,
            fabricante='MIKROTIK', descricao_curta='x', conteudo='y',
        )

        self.client.force_login(_consultor('consultor_wiki', wiki_habilitada=True))

    def test_sem_filtro_lista_todos_da_categoria(self):
        resp = self.client.get(reverse('wiki:categoria', args=['bgp']))
        self.assertEqual(resp.status_code, 200)
        titulos = {a.titulo for a in resp.context['artigos']}
        self.assertEqual(titulos, {'BGP no MikroTik', 'BGP no MikroTik CHR', 'BGP na Huawei'})

    def test_fabricantes_disponiveis_tem_contagem_correta_e_escopada_a_categoria(self):
        resp = self.client.get(reverse('wiki:categoria', args=['bgp']))
        por_valor = {f['valor']: f['total'] for f in resp.context['fabricantes_disponiveis']}
        self.assertEqual(por_valor, {'MIKROTIK': 2, 'HUAWEI': 1})
        self.assertEqual(resp.context['total_categoria'], 3)

    def test_filtro_por_fabricante_restringe_a_categoria_e_ao_fabricante(self):
        resp = self.client.get(reverse('wiki:categoria', args=['bgp']), {'fabricante': 'MIKROTIK'})
        titulos = {a.titulo for a in resp.context['artigos']}
        self.assertEqual(titulos, {'BGP no MikroTik', 'BGP no MikroTik CHR'})
        self.assertEqual(resp.context['fabricante_selecionado'], 'MIKROTIK')

    def test_filtro_por_fabricante_sem_artigo_na_categoria_fica_vazio(self):
        # DELL não tem artigo em BGP nenhum.
        resp = self.client.get(reverse('wiki:categoria', args=['bgp']), {'fabricante': 'DELL'})
        self.assertEqual(list(resp.context['artigos']), [])

    def test_categoria_sem_fabricantes_nao_quebra_a_pagina(self):
        vazia = CategoriaWiki.objects.create(nome='Sem Artigo', slug='sem-artigo')
        resp = self.client.get(reverse('wiki:categoria', args=['sem-artigo']))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context['fabricantes_disponiveis']), [])

    def test_pill_do_fabricante_aparece_no_html_com_contagem(self):
        resp = self.client.get(reverse('wiki:categoria', args=['bgp']))
        html = resp.content.decode()
        self.assertIn('MikroTik', html)
        self.assertIn('fab-pill', html)
