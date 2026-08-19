# Autenticação em Duas Etapas (2FA) — Google Authenticator

**Arquivos principais:**
- `usuario/models.py` — `TOTPDevice`, `TOTPBackupCode`
- `usuario/totp.py` — geração/verificação TOTP, QR code, códigos de backup
- `usuario/views.py` — `login`, `verificar_2fa`, `configurar_2fa`, `resetar_2fa_admin`
- `usuario/middleware.py` — `Forcar2FAMiddleware`, `ProtegerAdminMiddleware`
- `usuario/urls.py` — `/auth/2fa/`, `/auth/2fa/verificar/`, `/auth/2fa/resetar/`
- `usuario/templates/configurar_2fa.html`, `templates/verificar_2fa.html`

**Atualizado em:** 03/08/2026

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

**Última atualização:** 02/08/2026

---

## Tela de configuração travada no primeiro login (corrigido em 2026-08-19)

### O sintoma

No primeiro login o usuário caía na tela de configuração do 2FA com um modal por cima. Ao
clicar em "Dashboard" para sair dali, a tela ficava escurecida e **nada respondia** — nem os
botões da barra superior, nem o menu com "Deslogar".

### Por que travava

Três coisas somadas:

1. O aviso era um **modal Bootstrap com `data-bs-backdrop="static"` e `data-bs-keyboard="false"`**
   — clique fora não fecha, ESC não fecha.
2. O backdrop desse modal tem `z-index: 1040 !important` (`static/css/style.css`), enquanto a
   `.top-bar` do sistema tem `z-index: 1000`. Ou seja, **o backdrop cobre a barra inteira**:
   Dashboard, menu do usuário e o próprio "Deslogar" ficavam atrás dele. Todo clique ali era
   engolido pelo backdrop, que com `static` não faz nada.
3. O script reabria o modal a **cada carregamento** da página, e o `Forcar2FAMiddleware` devolve
   o usuário para essa mesma página a cada tentativa de navegar. O único elemento clicável da
   tela era o botão do próprio modal — quem clicasse em qualquer outro lugar via só a tela
   escura, em loop.

### A correção

O modal virou um **aviso inline** no topo do card (`usuario/templates/configurar_2fa.html`): a
mesma informação, sem overlay, sem backdrop e sem script de auto-abertura. A barra superior
continua acessível, o formulário fica utilizável de imediato e o aviso traz um link direto para
`logout`, para quem preferir configurar depois com o celular em mãos.

A obrigatoriedade **não mudou**: o `Forcar2FAMiddleware` continua devolvendo qualquer rota para
`/auth/2fa/` enquanto o dispositivo não estiver confirmado (§ "Obrigatoriedade"). O que mudou é
que agora dá para ver a tela, entender o motivo e sair pelo logout, em vez de ficar preso atrás
de um overlay.

Conferido com um login recém-criado (criado e revertido em transação): `/homegeral` responde
`302 → /auth/2fa/`, a página renderiza sem `modalObrigatorio2fa`, com o aviso, o QR code, o campo
de código e o link de logout.
