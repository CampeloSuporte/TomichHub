# Segurança — proteção contra invasão

> Painel: **Sistema → Segurança** (`/seguranca/`)
> Código: `seguranca/` · Configuração: `crm/settings.py` (bloco "Segurança") · Servidor:
> `/etc/fail2ban/jail.d/crm.local`, `/etc/fail2ban/filter.d/crm-login.conf`,
> `/etc/sudoers.d/crm-fail2ban`

Três camadas independentes, todas visíveis no mesmo painel:

| Camada | O que segura | Onde vive |
|--------|--------------|-----------|
| **Bloqueio de login** | Força bruta de senha no CRM | Banco (`seguranca_bloqueiologin`) |
| **Fail2ban** | Força bruta no SSH e no login do CRM, no **firewall** | `fail2ban` (fonte da verdade) |
| **Filtro de injeção** | SQL injection, path traversal e XSS refletido | Middleware, antes da view |

---

## 1. Bloqueio por tentativa de login

**Regra padrão: 3 senhas erradas trancam a conta por 5 minutos.**

O contador vive em `seguranca.services` e é chamado pela view de login
(`usuario/views.py`). Dois contadores independentes:

| Chave | Limite | Bloqueio | Por quê |
|-------|--------|----------|---------|
| **Conta** (`username`) | 3 falhas | 5 min | O pedido literal do produto |
| **IP** | 10 falhas | 15 min | O robô que testa 500 usernames inventados nunca acumula 3 falhas no mesmo username |

Detalhes que importam:

- **A verificação vem ANTES do `authenticate()`.** Enquanto durar o bloqueio, nem a senha
  certa entra. Se a senha certa passasse, o bloqueio só atrasaria quem já errou — não
  seguraria um ataque de dicionário que acerta na tentativa seguinte.
- **Janela deslizante de 15 minutos** (`SEGURANCA_JANELA_MINUTOS`): falhas mais antigas não
  somam. Sem isso, duas senhas erradas em janeiro mais uma em março trancariam a conta.
- **Login certo zera o contador**, e o bloqueio expirado recomeça do zero (quem esperou os
  5 minutos não volta no 3º strike).
- **O 2FA usa o mesmo contador.** Código de 6 dígitos é adivinhável por força bruta; o
  contador da sessão que já existia (`2fa_tentativas`) só derruba aquela sessão, e trocar de
  aba zerava. Agora cada código errado conta para o bloqueio persistente da conta.
- **Usuário inexistente não cria linha de bloqueio de conta** — senão um robô com 500 nomes
  inventados encheria a tabela. Esse caso é do contador por IP (e do fail2ban).
- **Captcha reprovado não conta.** O widget do Cloudflare Turnstile falha sozinho de vez em
  quando (rede, extensão do navegador); trancar a conta por isso puniria quem nem chegou a
  errar a senha. A tentativa fica registrada, mas não incrementa nada.
- A mensagem na tela é genérica (`Usuário ou senha inválidos`) de propósito — não confirma
  ao atacante se o usuário existe. O motivo real (`usuario_inexistente`, `senha_invalida`,
  `usuario_inativo`, `2fa_invalido`) só aparece no painel interno. A partir da 2ª falha o
  usuário legítimo vê quantas tentativas restam antes do bloqueio.

### Configuração

Tudo em `crm/settings.py`, sobrescrevível por variável de ambiente:

```python
SEGURANCA_MAX_TENTATIVAS      = 3    # falhas antes de trancar a conta
SEGURANCA_BLOQUEIO_MINUTOS    = 5    # duração do bloqueio de conta
SEGURANCA_MAX_TENTATIVAS_IP   = 10   # falhas antes de trancar o IP
SEGURANCA_BLOQUEIO_IP_MINUTOS = 15   # duração do bloqueio de IP
SEGURANCA_JANELA_MINUTOS      = 15   # janela deslizante das falhas
SEGURANCA_RETENCAO_DIAS       = 90   # poda do log (task Celery diária, 03:40)
```

### IP real do cliente

`services.get_client_ip` lê, nesta ordem: `CF-Connecting-IP` → primeiro item de
`X-Forwarded-For` → `X-Real-IP` → `REMOTE_ADDR`. O primeiro item do XFF é o cliente porque o
nginx usa `$proxy_add_x_forwarded_for`, que **anexa** o peer no fim da lista.

---

## 2. Fail2ban — blacklist no firewall

O bloqueio do banco protege a aplicação; o fail2ban é o que **tira o atacante da porta**.

### Jails

| Jail | Fonte | Limite | Ban |
|------|-------|--------|-----|
| `sshd` | journal do sshd | 5 falhas / 10 min | 1h |
| `crm-login` | `/var/log/crm/auth.log` | 10 falhas / 15 min | 1h |

Reincidente fica banido progressivamente mais tempo (`bantime.increment`: 1h → 2h → 4h …,
até uma semana). Robô de varredura volta sempre; sem isso ele recomeçaria de hora em hora
para sempre.

### Duas armadilhas já resolvidas na configuração

1. **O SSH deste servidor está na porta 22002, não na 22.** Sem `port = 22002` na jail, o
   fail2ban criaria a regra de firewall para a porta errada e não bloquearia nada.
2. **No Ubuntu o backend padrão do fail2ban é `systemd`**, e com ele a jail ignora `logpath`
   em silêncio — o status mostra `Journal matches` em vez de `File list` e nada nunca é
   banido. Por isso `crm-login` tem `backend = auto` explícito.

### O log lido pela jail do CRM

`seguranca.services._log_fail2ban` (logger `seguranca.auth`) escreve uma linha por falha em
`/var/log/crm/auth.log`:

```
2026-08-23 17:04:11 LOGIN FAILED user=fulano ip=203.0.113.9 reason=senha_invalida
```

**Esse formato é um contrato** com `/etc/fail2ban/filter.d/crm-login.conf`. Mudar de um lado
exige mudar do outro. Para conferir o filtro depois de mexer:

```bash
fail2ban-regex /var/log/crm/auth.log /etc/fail2ban/filter.d/crm-login.conf
```

### Permissões

O gunicorn e o daphne rodam como `www-data`, e o `fail2ban-client` só responde a root. A
regra fica em `/etc/sudoers.d/crm-fail2ban` e libera **apenas** os verbos usados pelo painel:

```
ping · status · status <jail> · set <jail> banip <ip> · set <jail> unbanip <ip> · unban <ip>
```

Deliberadamente **não** é `NOPASSWD: /usr/bin/fail2ban-client` solto: esse binário aceita
`set <jail> action ...`, que executa comando de shell como root — seria transformar qualquer
falha na aplicação web em root no servidor.

O painel também lê `/var/log/fail2ban.log` para montar o histórico; o arquivo ficou `644`
(e o logrotate foi ajustado para recriá-lo assim) porque `www-data` não pertence ao grupo
`adm`. O log só contém IPs, nomes de jail e horários — nada sigiloso —, o que é mais barato
que colocar `www-data` no grupo `adm` e dar acesso a **todo** o `/var/log`.

### Nada é espelhado em tabela

A fonte da verdade dos banimentos é o `fail2ban-client`, porque é ele que fala com o
firewall. Um espelho em tabela mentiria sempre que alguém mexesse no fail2ban por fora — e
um IP "liberado" no CRM continuando banido no firewall é o pior tipo de bug de segurança: o
operador acha que resolveu.

### ⚠️ Antes de mexer, proteja seu próprio acesso

`ignoreip` já cobre `127.0.0.1/8`, `::1`, `45.235.72.10` e `10.91.0.0/16`. Se o escritório
tem IP fixo, acrescente-o em `/etc/fail2ban/jail.d/crm.local` — é o seguro contra se trancar
do lado de fora:

```
ignoreip = 127.0.0.1/8 ::1 45.235.72.10 10.91.0.0/16 200.x.x.x
```

Depois: `systemctl restart fail2ban`.

---

## 3. Filtro de injeção (SQL injection, path traversal, XSS)

`seguranca.middleware.ProtecaoInjecaoMiddleware`, registrado logo depois do
`SecurityMiddleware` do Django e **antes** de sessão/auth — payload malicioso é descartado
sem custo de banco.

### Por que existe, se o Django já é seguro por padrão

O ORM parametriza tudo, e uma auditoria do projeto confirmou que **não há SQL montado com
string** (os dois únicos `cursor.execute` — `clientes/apps.py` e a migration manual de
topologia em `clientes/views.py` — são literais fixos, sem interpolação de dados do
usuário). O middleware serve para outras duas coisas:

1. **Cinto de segurança contra regressão**: um `cursor.execute` com f-string introduzido no
   futuro, ou um `filter(**request.GET)` desavisado, reabriria o buraco.
2. **Visibilidade**: sem ele, uma varredura de `sqlmap` contra o CRM não deixa rastro
   nenhum — o Django devolve 404/200 e a tentativa some. Com ele, vira `EventoSeguranca` e
   aparece no painel.

### O que é inspecionado

| Superfície | Inspecionada? |
|------------|---------------|
| Query string | ✅ |
| POST `application/x-www-form-urlencoded` | ✅ |
| Caminho da URL | ✅ (só path traversal) |
| POST multipart / JSON | ❌ **de propósito** |

Multipart e JSON ficam de fora porque ler o corpo no middleware o **consome antes da view**:
para um upload de backup ou firmware isso significaria processar 100 MB no middleware e
impedir qualquer view de trocar os upload handlers. As rotas JSON do CRM são internas,
autenticadas e passam por ORM.

### Assinaturas

Específicas, nunca genéricas — a palavra "select" sozinha **não** dispara:

`union select` · `information_schema` · tabelas de sistema (`pg_catalog`, `mysql.user`,
`sqlite_master`…) · tautologia (`' OR '1'='1`) · stacked queries (`; DROP TABLE`) ·
time-based (`sleep(`, `pg_sleep(`, `benchmark(`, `waitfor delay`) · `load_file` /
`into outfile` · `xp_cmdshell` · comentário de versão MySQL (`/*!50000`) · `../../` ·
`/etc/passwd` · `<script`.

### Como não bloquear usuário legítimo

Falso positivo é o risco real aqui — o CRM manipula texto que **parece** ataque. Três
proteções:

1. **Prefixos isentos** (`SEGURANCA_INJECAO_ISENTAS`): `/clientes/scripts/`,
   `/clientes/terminal/`, `/wiki/`, `/atendimento/`, `/admin/`, `/monitoramento/webhook`,
   `/static/`, `/media/` — lugares onde texto de comando cru é o trabalho normal.
2. **Campos de conteúdo livre isentos em qualquer rota**
   (`SEGURANCA_INJECAO_CAMPOS_LIVRES`): `conteudo`, `descricao`, `observacao`, `mensagem`,
   `script`, `comandos`, `log`, `payload`, `drawio_xml`, `prompt`…
3. **Modo observação**: `SEGURANCA_INJECAO_BLOQUEAR=0` registra o evento e deixa passar.

**Se um usuário reclamar de "requisição bloqueada":** abra a aba *Injeção / SQLi* do painel,
que mostra a rota, o campo e o trecho exato que casou. A correção é acrescentar o campo em
`SEGURANCA_INJECAO_CAMPOS_LIVRES` (preferível) ou a rota em `SEGURANCA_INJECAO_ISENTAS`.

Para desligar tudo em emergência, sem deploy — no serviço, e reiniciar:

```bash
SEGURANCA_INJECAO_BLOQUEAR=0   # só registra
SEGURANCA_INJECAO_ATIVO=0      # desliga o filtro inteiro
```

---

## 4. O painel

**Sistema → Segurança** (`/seguranca/`). Cinco abas:

| Aba | Conteúdo | Quem vê |
|-----|----------|---------|
| **Bloqueios** | Contas/IPs bloqueados agora, com contagem regressiva e botão **Liberar** (e **Liberar todos**); histórico de falhas dos últimos 7 dias | Admin e Consultor |
| **Tentativas de login** | Toda tentativa (sucesso e falha) com usuário, IP, motivo e navegador; filtros por usuário, IP, resultado e período; ranking de IPs com mais falhas | Admin e Consultor |
| **SSH / Fail2ban** | Jails, blacklist com botão **Liberar**, banimento manual e histórico do `/var/log/fail2ban.log` | Só Admin |
| **Injeção / SQLi** | Requisições barradas, com rota, campo, assinatura e trecho | Só Admin |
| **Auditoria** | Quem liberou o quê, quando e de qual IP | Só Admin |

### Escopo por papel

- **Administrador** — tudo. É o dono do servidor.
- **Consultor** — só as contas que ele já gerencia (`perms.usuarios_gerenciaveis_por`), para
  destravar operador e cliente da própria instância sem depender do Administrador (mesma
  lógica de `docs/PERMISSOES_CONSULTOR.md`). **Bloqueio por IP, fail2ban e eventos de
  injeção são do servidor inteiro, não de uma instância** — ficam fora.
- **Operador e portal do cliente** — sem acesso.

### Auditoria dos desbloqueios

Todo desbloqueio e todo ban/unban manual gravam `AcaoSeguranca` com autor, alvo e IP de
origem. Desbloquear é exatamente a ação que um invasor com sessão roubada ia querer usar;
sem trilha, não sobraria rastro nenhum.

---

## 5. Endurecimento adicional aplicado

- `SECURE_PROXY_SSL_HEADER` — o nginx termina o TLS; sem isso o Django achava que toda
  requisição era `http` e `request.is_secure()` mentia (afetava, entre outras coisas, o flag
  `secure` do cookie de dispositivo confiável do 2FA).
- `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_REFERRER_POLICY='same-origin'`,
  `SESSION_COOKIE_HTTPONLY`, `SESSION_COOKIE_SAMESITE='Lax'`, `CSRF_COOKIE_SAMESITE='Lax'`.
- `CSRF_COOKIE_HTTPONLY` fica **False** de propósito: o JS do CRM lê o cookie `csrftoken`
  para montar o header `X-CSRFToken` nos `fetch()`.
- **Cookies só por HTTPS ficam desligados por padrão** (`SEGURANCA_COOKIES_HTTPS=1` para
  ligar): o servidor ainda atende em `http://` no IP bruto (45.235.72.10) e ligar isso
  derrubaria o login por lá.

### Bug corrigido de quebra

`usuario.views.redirect_user_by_role` chamava `messages.error(None, ...)`, que levanta
`TypeError` — um login de conta **sem Cliente vinculado** virava erro 500 em vez da
mensagem "sua conta não possui acesso ao sistema". A função agora recebe o `request` e
desloga a conta nesse caminho (sem o logout, a conta ficaria autenticada e o `GET` de
`/auth/login/` mandaria de volta para cá — laço infinito de redirect).

---

## 6. Operação

```bash
# Estado das jails
fail2ban-client status
fail2ban-client status sshd
fail2ban-client status crm-login

# Liberar um IP na mão (o painel faz o mesmo)
fail2ban-client unban 203.0.113.9

# Conferir o filtro do CRM depois de mexer no formato do log
fail2ban-regex /var/log/crm/auth.log /etc/fail2ban/filter.d/crm-login.conf

# Acompanhar as falhas de login em tempo real
tail -f /var/log/crm/auth.log
```

**Retenção:** a task Celery `seguranca.limpar_registros` roda diariamente às 03:40 e apaga
tentativas e eventos com mais de `SEGURANCA_RETENCAO_DIAS` (90). Sem ela a tabela só cresce:
um servidor exposto na internet leva milhares de tentativas de robô por dia, e é justamente
esse tráfego que não para.

---

## Testes

`seguranca/tests.py` — 21 testes:

- 3 senhas erradas bloqueiam a conta, com ~5 minutos restantes;
- **a senha certa é recusada durante o bloqueio** (o teste que impede a regressão mais
  perigosa);
- login volta a funcionar quando o bloqueio expira;
- login certo zera o contador;
- usuário inexistente não cria linha de bloqueio de conta;
- falhas fora da janela não somam;
- desbloqueio manual zera o contador (senão a próxima falha trancaria de novo);
- SQLi na query string e no POST urlencoded são bloqueados; path traversal também;
- **texto legítimo passa** (`O'Brien Telecom`, `select-fibra`, `update de contrato`);
- multipart não é inspecionado; modo observação não bloqueia;
- **escopo por papel**: Operador não entra; Consultor entra mas sem jails nem eventos de
  injeção, só vê tentativas da própria instância, desbloqueia conta da própria instância e leva
  403 ao tentar desbloquear conta de outra instância ou mexer no fail2ban.

```bash
cd /opt/crm && venv/bin/python manage.py test seguranca
```
