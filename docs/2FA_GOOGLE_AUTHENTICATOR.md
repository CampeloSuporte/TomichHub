# Autenticação em Duas Etapas (2FA) — Google Authenticator

**Arquivos principais:**
- `usuario/models.py` — `TOTPDevice`, `TOTPBackupCode`, `DispositivoConfiavel`
- `usuario/totp.py` — geração/verificação TOTP, QR code, códigos de backup, dispositivo confiável
- `usuario/views.py` — `login`, `verificar_2fa`, `configurar_2fa`, `resetar_2fa_admin`
- `usuario/middleware.py` — `Forcar2FAMiddleware`, `ProtegerAdminMiddleware`
- `usuario/urls.py` — `/auth/2fa/`, `/auth/2fa/verificar/`, `/auth/2fa/resetar/`
- `templates/login.html`, `templates/verificar_2fa.html`

**Atualizado em:** 18/08/2026

---

## Visão Geral

2FA baseado em **TOTP** (RFC 6238) compatível com o app **Google Authenticator** (também funciona
com Authy, Microsoft Authenticator etc. — é um protocolo aberto, não depende de conta Google nem de
API externa). Vale pra qualquer login do sistema: Administrador, Consultor, Operador e portal do
cliente final.

Desde a Sessão 32, o 2FA é **obrigatório**: todo usuário autenticado sem `TOTPDevice` confirmado é
travado na tela de configuração até ativar (ver [Obrigatoriedade](#obrigatoriedade--forcar2famiddleware)).

## Modelos

```python
class TOTPDevice(models.Model):
    usuario = models.OneToOneField(User, related_name='totp_device')
    secret = models.CharField(max_length=32)   # base32, texto puro — o servidor precisa validar a cada login
    confirmado = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    confirmado_em = models.DateTimeField(null=True, blank=True)

class TOTPBackupCode(models.Model):
    device = models.ForeignKey(TOTPDevice, related_name='backup_codes')
    codigo_hash = models.CharField(max_length=128)   # hash via make_password, nunca texto puro
    usado_em = models.DateTimeField(null=True, blank=True)
```

`confirmado=False` existe pra não travar a conta caso o usuário abandone o setup antes de escanear
o QR — só passa a valer no login (e no middleware obrigatório) depois de confirmar com um código
válido.

## Fluxo de login

1. `login` (`usuario/views.py`) autentica usuário/senha normalmente.
2. Se `user.totp_device.confirmado` é `True`: em vez de logar direto, guarda
   `request.session['2fa_user_id']` (sessão comum, usuário **ainda não autenticado**) e redireciona
   pra `verificar_2fa`.
3. `verificar_2fa` pede o código de 6 dígitos do Authenticator **ou** um código de backup. Só chama
   `auth_login()` depois de um dos dois bater. 5 tentativas erradas limpam a sessão e mandam de
   volta pro login (mitiga força bruta contra o código de 6 dígitos — essa etapa não passa pelo
   Cloudflare Turnstile do login).
4. Se o usuário **não** tem `TOTPDevice` confirmado, `auth_login()` acontece direto — mas o
   middleware obrigatório (abaixo) intercepta a próxima requisição e força a configuração.

## Configuração (auto-atendimento) — `/auth/2fa/`

Tela de `configurar_2fa`, acessível a qualquer usuário logado, só pra própria conta:

- **Sem `TOTPDevice` (ou não confirmado):** mostra QR code (gerado com `qrcode`, PNG em base64,
  sem salvar em disco) + secret manual + campo pra digitar o código de confirmação. Um modal
  (`data-bs-backdrop="static"`, sem botão de fechar por fora) avisa que a configuração é
  obrigatória.
- **Ativar:** valida o código digitado contra o secret pendente; se bater, marca `confirmado=True`
  e gera 10 **códigos de backup**, mostrados uma única vez na tela.
- **Já confirmado:** mostra status + botão "Gerar Novos Códigos de Backup" (pede senha) + botão
  "Desativar 2FA" (pede senha).

## Códigos de backup

10 códigos formato `xxxx-xxxx`, gerados com `secrets.token_hex`, hasheados com
`django.contrib.auth.hashers.make_password` (mesmo hasher da senha) — só existem em texto puro no
retorno de `gerar_backup_codes()`, exibido uma única vez. Cada um funciona pra um único login
(`usado_em` marcado após o uso); regenerar apaga os antigos.

## Obrigatoriedade — `Forcar2FAMiddleware`

Registrado em `crm/settings.py` (`MIDDLEWARE`), logo depois de `AuthenticationMiddleware`. Em toda
requisição:

```python
def _precisa_configurar_2fa(self, request):
    if not request.user.is_authenticated:
        return False
    if request.path.startswith(('/static/', '/media/')):
        return False
    if resolve(request.path_info).url_name in {'configurar_2fa', 'logout'}:
        return False
    device = getattr(request.user, 'totp_device', None)
    return not (device and device.confirmado)
```

Se `True`, redireciona pra `configurar_2fa` — não importa qual página o usuário tentou acessar.
Únicas rotas livres: a própria tela de configuração, `logout` e estático/mídia. Vale pra **todos os
perfis**, inclusive portal do cliente final.

Conexões WebSocket (Channels) não passam por esse middleware — o `AuthMiddlewareStack` delas é
independente do `MIDDLEWARE` do Django (ver `crm/asgi.py`), então terminal SSH/monitoramento em
tempo real não são afetados por esse gate (a página que os embute já teria sido bloqueada antes,
pelo HTTP normal).

## Django admin — bypass do 2FA e `ProtegerAdminMiddleware`

O `/admin/` nativo do Django tem form de login próprio (`AdminAuthenticationForm`) que chama
`auth_login()` direto, sem passar pela view `usuario.views.login` nem por `verificar_2fa` — ou seja,
até 03/08/2026, um usuário anônimo digitando `crm.tomich.com.br/admin/` conseguia logar por ali e
**pular o 2FA obrigatório** por completo, mesmo com `Forcar2FAMiddleware` ativo (que só age depois
que a sessão já está autenticada).

Corrigido com `ProtegerAdminMiddleware` (`usuario/middleware.py`), registrado em `MIDDLEWARE` logo
depois de `AuthenticationMiddleware` (antes de `Forcar2FAMiddleware`):

```python
class ProtegerAdminMiddleware:
    def __call__(self, request):
        if request.path.startswith('/admin/'):
            if not request.user.is_authenticated:
                return redirect(f"{settings.LOGIN_URL}?next={request.path}")
            if not perms.is_admin(request.user):
                return HttpResponseForbidden(...)
        return self.get_response(request)
```

- **Anônimo** tentando qualquer rota `/admin/*` (inclusive `/admin/login/`) → redirecionado pro login
  do próprio CRM (`/auth/login/`). Só chega em `/admin/` depois de já ter passado pelo fluxo normal
  (senha + `verificar_2fa`, quando aplicável) — o form nativo do Django nunca é exibido a quem não
  está logado.
- **Autenticado mas não Administrador** (`perms.is_admin` — cobre `PerfilUsuario.role == 'admin'` e o
  legado `is_staff=True` sem perfil) → `403 Forbidden`. Consultor e Operador não acessam, mesmo
  logados.
- Botão de acesso em `templates/base.html`, dropdown "Sistema" → "Django Admin", visível só quando
  `is_admin_bo` (mesma flag do context processor `usuario.context_processors.perfil_context`) — não
  existe mais link direto pra quem não é admin, e a rota continua bloqueada mesmo se alguém adivinhar
  a URL.

## Redirect pós-login (`next`) — corrigido em 18/08/2026

**Sintoma:** um usuário navegando dentro do proxy web de acessos (ex: tela de login do Grafana,
`/clientes/acessos/<id>/web/<porta>/http/login`) caía de volta no **dashboard do CRM** em vez de
continuar de onde estava, sempre que a sessão expirava no meio da navegação.

**Causa:** a rota do proxy é protegida por `@login_required(login_url='login')` — quando a sessão
expira, o Django redireciona pra `/auth/login/?next=<url original>` (comportamento nativo do
decorator). O `next` chegava íntegro até a view (`crm/urls.py` repassa a querystring no redirect de
`/login/` pra `/auth/login/`), mas `usuario.views.login()` e `verificar_2fa()` **nunca liam esse
parâmetro** — sempre chamavam `redirect_user_by_role(user)` incondicionalmente, mandando pro
dashboard fixo do papel do usuário.

**Correção** (`usuario/views.py`):

```python
def _next_seguro(request, valor=None):
    next_url = valor if valor is not None else (request.POST.get('next') or request.GET.get('next'))
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return None

def _redirect_pos_login(request, user, next_url=None):
    destino = _next_seguro(request, next_url)
    return redirect(destino) if destino else redirect_user_by_role(user)
```

- `url_has_allowed_host_and_scheme` (mesma validação que o `LoginView` nativo do Django usa) evita
  open redirect — um `next` apontando pra fora do próprio domínio é ignorado, cai no fallback.
- `login.html` ganhou um `<input type="hidden" name="next">`, preenchido a partir de
  `request.GET.get('next')` (view passa isso no contexto do GET) — sobrevive ao POST do formulário.
- Quando o usuário tem 2FA confirmado, o fluxo passa por duas telas (`login` → `verificar_2fa`);
  `next` não sobrevive nesse redirect (é uma página própria, sem querystring), então fica guardado
  em `request.session['2fa_next']` até `verificar_2fa` concluir e chamar `_redirect_pos_login`
  de novo com esse valor.

## Navegador confiável ("lembrar este dispositivo") — novo em 18/08/2026

Pedido do usuário: parar de pedir o código do Authenticator toda vez no mesmo navegador — **sem**
abrir mão de exigir usuário+senha sempre. Modelo novo:

```python
class DispositivoConfiavel(models.Model):
    usuario = models.ForeignKey(User, related_name='dispositivos_confiaveis')
    token_hash = models.CharField(max_length=128)   # hash via make_password, nunca texto puro
    descricao = models.CharField(max_length=255, blank=True, default='')  # User-Agent, informativo
    criado_em = models.DateTimeField(auto_now_add=True)
    expira_em = models.DateTimeField()               # 30 dias a partir da criação
    ultimo_uso_em = models.DateTimeField(null=True, blank=True)
```

Fluxo (`usuario/totp.py`):

- **`verificar_2fa`**: checkbox "Confiar neste navegador por 30 dias" (marcado por padrão) no
  form. Se marcado e o código bater, `criar_dispositivo_confiavel(user, descricao=user_agent)`
  gera um token aleatório (`secrets.token_urlsafe(32)`), grava só o **hash** no banco, e devolve
  `f"{user.id}:{token}"` — vira um cookie `dispositivo_confiavel` (`HttpOnly`, `Secure` se HTTPS,
  `SameSite=Lax`, `expires=expira_em`).
- **`login`**: antes de forçar a segunda etapa, se o usuário tem 2FA confirmado, checa o cookie:
  `verificar_dispositivo_confiavel(cookie_valor)` procura um `DispositivoConfiavel` não expirado
  cujo hash bata com o token do cookie **e** cujo `usuario_id` (embutido no próprio valor do
  cookie, antes do `:`) seja o mesmo do login em andamento — evita que um cookie de outro usuário
  no mesmo navegador (conta compartilhada) sirva de bypass pra essa conta. Batendo, pula direto
  pra `auth_login()` + `_redirect_pos_login`, sem passar por `verificar_2fa`. `ultimo_uso_em` é
  atualizado a cada uso.
- **Revogação:** não existe UI pra listar/revogar dispositivos confiáveis ainda — fica registrado
  como próximo passo natural (o registro já existe no banco, `admin.py` do app `usuario` dá pra
  gerenciar via Django Admin enquanto isso).

## Duração da sessão — aumentada em 18/08/2026

`crm/settings.py`: `SESSION_COOKIE_AGE` subiu de **1 hora** pra **7 dias**
(`SESSION_EXPIRE_AT_BROWSER_CLOSE=False`, `SESSION_SAVE_EVERY_REQUEST=True` continuam iguais —
sliding window, o timer reinicia a cada request). O valor de 1h era curto demais pra qualquer
navegação mais longa (ex: dentro do proxy web de acessos), forçando login/2FA repetido no meio do
uso — motivo direto do bug do `next` acima.

## Reset por Administrador/Consultor — `/auth/2fa/resetar/`

`resetar_2fa_admin`, protegida por `perms.pode_gerenciar_usuarios_required` (mesmo decorator de
`cadastrar_usuario`). Apaga o `TOTPDevice` de um usuário gerenciável
(`perms.usuarios_gerenciaveis_por`) — cobre o caso de perda de celular **e** dos códigos de backup,
onde só um Administrador/Consultor destrava a conta. Botão correspondente aparece na coluna Ações
de `cadastrar_usuario.html`, só quando `usuarios.tem_2fa` é `True`.

## Como configurar (usuário final)

1. Instalar o app Google Authenticator (ou Authy/Microsoft Authenticator).
2. No próximo login sem 2FA, o sistema já força a tela `/auth/2fa/` — ou, voluntariamente, ícone de
   usuário → "Autenticação em Duas Etapas".
3. Escanear o QR code (ou digitar o secret manualmente).
4. Digitar o código de 6 dígitos gerado pelo app e confirmar.
5. Guardar os 10 códigos de backup mostrados na sequência — só aparecem essa vez.

## Segurança — decisões tomadas

- **Secret em texto puro no banco:** necessário pra validar TOTP a cada login; sem infra de KMS no
  projeto pra justificar criptografia adicional.
- **Códigos de backup hasheados**, nunca reversíveis — só existem em texto puro no momento da
  geração.
- **Rate limit na verificação** (5 tentativas por sessão pendente) — código de 6 dígitos tem só
  1.000.000 de combinações e essa etapa não passa pelo Turnstile do login.
- **Sem exceção por papel:** decisão explícita (`AskUserQuestion` com o usuário) de aplicar a
  obrigatoriedade a todos os logins, inclusive portal do cliente final — não só back-office.

---

**Última atualização:** 18/08/2026
