# Autenticação em Duas Etapas (2FA) — Google Authenticator

**Arquivos principais:**
- `usuario/models.py` — `TOTPDevice`, `TOTPBackupCode`
- `usuario/totp.py` — geração/verificação TOTP, QR code, códigos de backup
- `usuario/views.py` — `login`, `verificar_2fa`, `configurar_2fa`, `resetar_2fa_admin`
- `usuario/middleware.py` — `Forcar2FAMiddleware`
- `usuario/urls.py` — `/auth/2fa/`, `/auth/2fa/verificar/`, `/auth/2fa/resetar/`
- `usuario/templates/configurar_2fa.html`, `templates/verificar_2fa.html`

**Atualizado em:** 02/08/2026

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
