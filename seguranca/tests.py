"""Testes do módulo de segurança.

Cobrem o comportamento que o produto pediu literalmente (3 senhas erradas =
5 minutos de bloqueio) e as duas armadilhas fáceis de quebrar numa refatoração:
o bloqueio precisa recusar até a senha CERTA, e o filtro de injeção não pode
barrar texto legítimo.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from seguranca import services
from seguranca.models import BloqueioLogin, EventoSeguranca, TentativaLogin


@override_settings(SEGURANCA_MAX_TENTATIVAS=3, SEGURANCA_BLOQUEIO_MINUTOS=5)
class BloqueioLoginTests(TestCase):
    def setUp(self):
        self.senha = 'senha-super-secreta-123'
        self.user = User.objects.create_user(username='fulano', password=self.senha)
        self.client = Client()
        # O Turnstile é validado antes da senha; sem burlar aqui, nenhum teste
        # chegaria no authenticate().
        patcher = patch('usuario.views._verificar_turnstile', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _login(self, senha, username='fulano'):
        return self.client.post(reverse('login'), {'username': username, 'password': senha}, follow=True)

    def _autenticou(self):
        """O usuário do teste não tem Cliente vinculado, então
        `redirect_user_by_role` o desloga logo depois de autenticar — checar
        `response.context['user']` daria falso negativo. O registro de sucesso
        é a evidência direta de que o bloqueio deixou a senha certa passar."""
        return TentativaLogin.objects.filter(
            username='fulano', sucesso=True, motivo=TentativaLogin.MOTIVO_SUCESSO,
        ).exists()

    def test_tres_senhas_erradas_bloqueiam_a_conta(self):
        for _ in range(3):
            self._login('errada')

        bloqueio = BloqueioLogin.objects.get(tipo=BloqueioLogin.TIPO_CONTA, chave='fulano')
        self.assertTrue(bloqueio.ativo)
        self.assertEqual(bloqueio.falhas, 3)
        # ~5 minutos (margem pro tempo de execução do teste)
        self.assertGreater(bloqueio.segundos_restantes, 4 * 60)
        self.assertLessEqual(bloqueio.segundos_restantes, 5 * 60)

    def test_senha_certa_e_recusada_durante_o_bloqueio(self):
        """O ponto central: se a senha certa passasse, o bloqueio não seguraria
        um ataque de dicionário que acerta na tentativa seguinte."""
        for _ in range(3):
            self._login('errada')

        self._login(self.senha)
        self.assertFalse(self._autenticou())
        self.assertTrue(
            TentativaLogin.objects.filter(motivo=TentativaLogin.MOTIVO_BLOQUEADO).exists()
        )

    def test_login_volta_a_funcionar_quando_o_bloqueio_expira(self):
        for _ in range(3):
            self._login('errada')
        BloqueioLogin.objects.filter(chave='fulano').update(
            bloqueado_ate=timezone.now() - timezone.timedelta(seconds=1)
        )

        self._login(self.senha)
        self.assertTrue(self._autenticou())

    def test_login_certo_zera_o_contador(self):
        self._login('errada')
        self._login('errada')
        self._login(self.senha)

        bloqueio = BloqueioLogin.objects.get(tipo=BloqueioLogin.TIPO_CONTA, chave='fulano')
        self.assertEqual(bloqueio.falhas, 0)
        self.assertFalse(bloqueio.ativo)

    def test_usuario_inexistente_nao_cria_bloqueio_de_conta(self):
        """Robô testando 500 nomes inventados não pode encher a tabela — quem
        cobre esse caso é o contador por IP."""
        self._login('qualquer', username='nao-existe-'*3)
        self.assertFalse(
            BloqueioLogin.objects.filter(tipo=BloqueioLogin.TIPO_CONTA).exists()
        )
        self.assertTrue(
            TentativaLogin.objects.filter(motivo=TentativaLogin.MOTIVO_USUARIO_INEXISTENTE).exists()
        )

    def test_falhas_fora_da_janela_nao_somam(self):
        with override_settings(SEGURANCA_JANELA_MINUTOS=15):
            self._login('errada')
            self._login('errada')
            BloqueioLogin.objects.filter(chave='fulano').update(
                ultima_falha_em=timezone.now() - timezone.timedelta(minutes=30)
            )
            self._login('errada')

        bloqueio = BloqueioLogin.objects.get(chave='fulano', tipo=BloqueioLogin.TIPO_CONTA)
        self.assertEqual(bloqueio.falhas, 1)
        self.assertFalse(bloqueio.ativo)

    def test_desbloqueio_manual_zera_o_contador(self):
        for _ in range(3):
            self._login('errada')
        bloqueio = BloqueioLogin.objects.get(chave='fulano', tipo=BloqueioLogin.TIPO_CONTA)

        services.desbloquear(bloqueio, por_usuario=self.user)
        bloqueio.refresh_from_db()

        self.assertFalse(bloqueio.ativo)
        # Zerar o contador junto importa: senão a próxima falha trancaria de novo.
        self.assertEqual(bloqueio.falhas, 0)
        self._login(self.senha)
        self.assertTrue(self._autenticou())


class ProtecaoInjecaoTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_sql_injection_na_querystring_e_bloqueada(self):
        resp = self.client.get('/auth/login/', {'q': "1' OR '1'='1"})
        self.assertEqual(resp.status_code, 403)
        evento = EventoSeguranca.objects.get()
        self.assertEqual(evento.tipo, EventoSeguranca.TIPO_SQL_INJECTION)
        self.assertTrue(evento.bloqueado)

    def test_union_select_e_bloqueado(self):
        resp = self.client.get('/auth/login/', {'id': '5 UNION SELECT senha FROM auth_user'})
        self.assertEqual(resp.status_code, 403)

    def test_path_traversal_e_bloqueado(self):
        resp = self.client.get('/clientes/../../../../etc/passwd')
        self.assertEqual(resp.status_code, 403)

    def test_texto_legitimo_passa(self):
        """Falso positivo é o risco real deste filtro: o CRM tem busca livre e
        nomes de cliente com aspas e hífen."""
        for valor in ["O'Brien Telecom", 'select-fibra', 'Rua 1, n 1 - sala 2', 'update de contrato']:
            resp = self.client.get('/auth/login/', {'busca': valor})
            self.assertNotEqual(resp.status_code, 403, f'bloqueou indevidamente: {valor}')
        self.assertFalse(EventoSeguranca.objects.exists())

    def test_post_de_formulario_com_injecao_e_bloqueado(self):
        # urlencoded é o content-type de formulário HTML sem upload; o test
        # client usa multipart por padrão, que o middleware ignora de propósito.
        resp = self.client.post(
            '/auth/login/', "username=x'+UNION+SELECT+1,2+FROM+auth_user--",
            content_type='application/x-www-form-urlencoded',
        )
        self.assertEqual(resp.status_code, 403)

    def test_upload_multipart_nao_e_inspecionado(self):
        """Ler o corpo multipart no middleware processaria 100 MB antes da view
        e impediria qualquer troca de upload handler — por isso fica de fora."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        arquivo = SimpleUploadedFile('a.txt', b"1' OR '1'='1")
        resp = self.client.post('/auth/login/', {'arquivo': arquivo, 'x': 'ok'})
        self.assertNotEqual(resp.status_code, 403)

    @override_settings(SEGURANCA_INJECAO_BLOQUEAR=False)
    def test_modo_observacao_registra_mas_deixa_passar(self):
        # O middleware lê a configuração no __init__, então precisa ser
        # reconstruído pra o override valer.
        from seguranca.middleware import ProtecaoInjecaoMiddleware
        mw = ProtecaoInjecaoMiddleware(lambda r: None)
        self.assertFalse(mw.bloquear)
