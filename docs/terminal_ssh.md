# Terminal SSH — Documentação Técnica

**Arquivo:** `clientes/consumers.py`  
**Classe principal:** `SSHConsumer` (Django Channels WebSocket Consumer)  
**Atualizado em:** 2026-08-05

---

## Visão Geral

O Terminal SSH é implementado como um WebSocket Consumer do Django Channels.  
Cada sessão de terminal no browser abre uma conexão WebSocket que o `SSHConsumer` mantém,
gerenciando o processo SSH (via `pexpect`) ou a sessão Paramiko diretamente com o equipamento
de rede.

---

## Arquitetura

```
Browser (xterm.js)
    │  WebSocket (JSON + binary frames)
    ▼
SSHConsumer (channels)
    ├── conexão direta SSH/Telnet  →  equipamento
    └── via ProxyServer             →  proxy SSH  →  equipamento
```

### Pool de Conexões com Proxies (`_ProxyPool`)

Classe auxiliar que mantém conexões Paramiko ativas com servidores proxy em cache.  
Evita o custo de re-handshake SSH a cada novo terminal aberto para o mesmo proxy.

- **`get(proxy)`** — retorna conexão ativa existente ou `None`
- **`put(proxy, client)`** — armazena cliente SSHClient no pool
- **`remove(proxy)`** — remove do pool (chamado em caso de falha)

A instância global `_proxy_pool` é compartilhada entre todos os consumers do processo.

---

## Ciclo de Vida da Sessão

1. Browser abre WebSocket → `SSHConsumer.connect()` aceita e limpa estado anterior
2. Browser envia `{"action": "connect", "acesso_id": N}` → consumer lê o `Acesso` do banco
3. `SSHConsumer` decide protocolo (SSH / Telnet) e inicia conexão
4. Thread de leitura (`read_thread`) fica em loop enviando output para o browser
5. Browser envia frames binários (teclas) → `receive()` repassa ao processo/canal SSH
6. Ao fechar: `disconnect()` → `limpar_recursos()` encerra threads e fecha canais

### Limpeza de Recursos (`limpar_recursos`)

- Fecha `ssh_process`, `telnet_client`, `tunnel_process`, `_paramiko_shell`,
  `_paramiko_dest_transport`, `_tunnel_server`
- **Não fecha** `_paramiko_client` (é o cliente do pool compartilhado — fechá-lo derrubaria
  outros terminais ativos no mesmo proxy)

---

## Configuração SSH — KexAlgorithms

### Situação anterior (problema)

A lista de algoritmos de troca de chaves tinha `diffie-hellman-group16-sha512` (DH 4096-bit)
em posição alta. Equipamentos ZTE com CPU lenta levavam vários segundos para completar o
handshake, causando timeout da sessão.

### Correção aplicada em 2026-05-26

A ordem foi reestruturada para priorizar algoritmos leves:

```
KexAlgorithms=
  diffie-hellman-group14-sha256,   ← prioridade 1 (2048-bit, rápido)
  diffie-hellman-group14-sha1,     ← prioridade 2 (compatibilidade legada)
  curve25519-sha256,               ← curva elíptica (modernos)
  curve25519-sha256@libssh.org,
  ecdh-sha2-nistp256,
  ecdh-sha2-nistp384,
  ecdh-sha2-nistp521,
  diffie-hellman-group-exchange-sha256,
  diffie-hellman-group-exchange-sha1,
  diffie-hellman-group16-sha512,   ← 4096-bit movido para o final
  diffie-hellman-group18-sha512,
  diffie-hellman-group1-sha1
```

**Motivo:** O cliente SSH negocia o primeiro algoritmo que o servidor também suporte.
Ao colocar `group14-sha256` (DH 2048-bit) antes de `group16-sha512` (DH 4096-bit), o
handshake com ZTEs e outros equipamentos de CPU limitada passa a ser concluído em ~1 s
em vez de provocar timeout.

### Extraído para constante compartilhada — 2026-07-20

A tupla de KEX preferencial foi extraída para o módulo-nível `_ZTE_PREFERRED_KEX` em
`clientes/consumers.py` e passou a ser reutilizada também em `_paramiko_proxy_exec` (execução de
comando via proxy). Antes, só a conexão direta (`paramiko.Transport` do terminal interativo) tinha
o fix — conexões de proxy para equipamentos ZTE podiam sofrer o mesmo timeout de KEX sem estar
cobertas. `clientes/views.py::realizar_backup` também ganhou uma proteção equivalente via
`disabled_algorithms={'kex': [...]}` no `SSHClient.connect()` do backup manual/automático (ver
[backup_automatico.md](backup_automatico.md)).

### Gap encontrado em `connect_ssh_via_proxy` — corrigido em 2026-08-05

**Sintoma:** terminal via proxy falhava intermitentemente com `No existing session` alguns
segundos após abrir o canal — visto ao vivo num switch Huawei S5735 (banner SSH sem "client
version", indicando stack SSH embarcada mínima). O log mostrava o canal aberto e o banner
recebido normalmente, mas a autenticação nunca acontecia.

**Causa:** `connect_ssh_via_proxy()` (o caminho usado pela maioria dos acessos a IP privado sem
VPN dedicada) criava o `paramiko.Transport` do canal `direct-tcpip` **sem** aplicar
`_ZTE_PREFERRED_KEX` — diferente de `_connect_ssh_paramiko_direct` e do `FirmwareDownloadConsumer`,
que já tinham o ajuste. Sem ele, o paramiko tenta os grupos pesados primeiro; em equipamentos
lentos a negociação passa dos 10s de `start_client(timeout=10)`. E aqui há uma armadilha do
próprio paramiko: se a thread de negociação ainda estiver viva (só lenta, não morta),
`start_client()` **não levanta exceção** ao estourar o timeout — só devolve o controle sem KEX
completo. O código seguia para `auth_password()`, que aí sim falha com `SSHException("No existing
session")`, porque `initial_kex_done` ainda era `False`.

**Correção:** adicionado `dest_transport._preferred_kex = _ZTE_PREFERRED_KEX` logo após criar o
`Transport`, mesma linha já usada nos outros dois pontos — fecha o último caminho de conexão via
proxy que ainda usava a ordem padrão do paramiko.

---

## Outras Opções SSH Relevantes

| Opção                         | Valor / Justificativa                              |
|-------------------------------|----------------------------------------------------|
| `StrictHostKeyChecking`       | `no` — ambiente interno controlado                 |
| `ConnectTimeout`              | `10` segundos                                      |
| `ServerAliveInterval`         | `60` s — mantém sessão viva em links instáveis     |
| `ServerAliveCountMax`         | `3` tentativas antes de desconectar                |
| `HostKeyAlgorithms`           | `+ssh-rsa,ssh-dss` — suporte a equipamentos legados|
| `Ciphers`                     | inclui `aes128-cbc`, `aes256-cbc`, `3des-cbc`      |
| `PreferredAuthentications`    | `password,keyboard-interactive`                    |

---

## Suporte a Huawei (modo especial)

Equipamentos Huawei requerem tratamento de prompt diferenciado.  
O flag `self.is_huawei` é ativado na detecção do tipo de equipamento e altera o comportamento
de parsing de output e envio de comandos.

---

## Protocolos Suportados

| Protocolo | Implementação              |
|-----------|---------------------------|
| SSH       | `pexpect` + processo `ssh` |
| Telnet    | `telnetlib`                |
| SSH via Proxy | Paramiko + tunnel      |

---

## Autenticação e Auditoria — Adicionado em 2026-07-20

`connect()` agora exige um usuário autenticado no `scope` (`self.close(code=4001)` caso contrário)
e toda sessão passa a ser registrada — quem conectou, quando, de qual IP, comandos digitados e
transcript completo da tela. Detalhes completos em [AUDITORIA_ACESSOS.md](AUDITORIA_ACESSOS.md).

---

## Terminal Compartilhado (opt-in) — Adicionado em 2026-07-31

**Caso de uso:** dois usuários do CRM autorizados sobre o mesmo `Acesso` precisam ver e digitar no
**mesmo** terminal em tempo real — ex: um sênior acompanhando/ajudando um técnico numa mesma sessão
SSH, em vez de dois logins independentes no equipamento.

### Decisão de arquitetura: opt-in, não automático

Duas abas abrindo o mesmo `Acesso` continuam, por padrão, abrindo **duas conexões SSH/Telnet
físicas independentes** ao equipamento — exatamente como antes. O compartilhamento só existe
quando alguém explicitamente ativa (`action: "share_start"`); sem isso, nada muda.

### Registro em memória — `_SharedTerminalSession` / `_TerminalSessionRegistry`

Mesmo padrão já usado por `_ProxyPool` neste arquivo: um registro em memória de processo (não
Redis/`channel_layer`) mapeando `acesso_id → _SharedTerminalSession`. Só é seguro porque o
`daphne.service` roda com **um único worker** (mesma premissa documentada em `VirtualRoomConsumer`,
`atendimento/consumers.py`) — se o daphne algum dia escalar para múltiplos processos, este registro
precisa migrar para Redis.

`_SharedTerminalSession` guarda:

- `physical` — o `SSHConsumer` que fisicamente abriu a conexão SSH/Telnet (shell paramiko/pexpect,
  thread de leitura). **Nunca muda** durante a vida da sessão.
- `viewers` — dict ordenado `consumer → rótulo de exibição`, todos os WebSockets assistindo agora
  (inclui o próprio `physical`).
- `recent_output` — buffer dos últimos ~20.000 caracteres de output, para quem entra depois ver
  contexto em vez de tela em branco.

### Fluxo

1. Usuário A já está conectado normalmente (`is_reading=True`, shell físico aberto). Manda
   `{"action": "share_start"}` → `_iniciar_compartilhamento()` cria a `_SharedTerminalSession` e
   registra A como `physical`.
2. Usuário B abre o terminal do mesmo `Acesso`. `receive()`, na ação `connect`, primeiro verifica
   `_terminal_sessions.get(acesso_id)`: se existir sessão compartilhada, chama
   `_entrar_em_sessao_compartilhada()` em vez de `conectar_acesso()` — **B nunca abre uma conexão
   SSH/Telnet própria**, só se anexa como espectador.
3. **Output:** `send_output()` (chamado pelos mesmos 5 loops de leitura de sempre —
   `_read_paramiko_shell`, `read_ssh_output`, `read_telnet_output`, `_read_pexpect_shell` — sem
   nenhuma mudança neles) passou a checar `self._shared_session`; se existir, propaga o texto para
   **todos** os `viewers` (cada um grava no próprio `_registrar_saida`/transcript de auditoria) em
   vez de só para `self`. Esse é o único ponto de fan-out.
4. **Input:** `enviar_comando()` passou a resolver `alvo = session.physical if session else self` —
   quem digita pode ser um mero espectador (sem shell próprio); o byte é escrito no shell de quem
   detém a conexão física, mas a auditoria (`_registrar_digitacao`) continua atribuída a quem
   digitou, não ao dono da conexão. `_resize_pty()` segue a mesma regra (redimensiona o PTY de
   `session.physical`; último a redimensionar "vence").

### Dono sai, sessão continua

Se quem compartilhou (`physical`) fecha a aba enquanto ainda há espectadores, a conexão real com o
equipamento **não é encerrada** — `limpar_recursos()` detecta (`_sair_de_sessao_compartilhada()`
retorna `True`) que ainda há espectadores e **não** toca em `_paramiko_shell`/`ssh_process`/
`telnet_client`/`is_reading`/`read_thread`: a thread de leitura, já rodando como daemon thread,
continua sozinha, sem nenhum consumer "dono" vivo — só referenciada pela própria thread e pelo
objeto `_SharedTerminalSession`. Só quando o **último** espectador sai é que a conexão física é
efetivamente fechada (`_fechar_recursos_fisicos()`, chamado sobre `session.physical` mesmo que ele
já tenha desconectado há tempos — evita ficar com shell/thread órfãos abertos indefinidamente).

### Correção de segurança pré-existente

`conectar_acesso()` nunca validava se o usuário autenticado tinha permissão sobre o `acesso_id`
recebido do frontend — qualquer usuário autenticado podia abrir o terminal de qualquer host
cadastrado, de qualquer cliente, bastando adivinhar/descobrir o ID. Adicionado
`_usuario_pode_acessar()` (mesma regra de `listar_acessos_terminal` em `views.py`: staff/superuser
vê tudo, usuário comum só acessa `Acesso`s do(s) `Cliente`(s) a que está vinculado via
`Cliente.objects.filter_by_usuario_vinculado()`), chamado tanto em `conectar_acesso()` quanto em
`_entrar_em_sessao_compartilhada()`.

---

## Link Externo — Compartilhar Terminal Sem Login (Adicionado em 2026-07-31)

**Caso de uso:** compartilhar o terminal com alguém **de fora do CRM** (sem usuário/senha) por um
tempo limitado — ex: suporte de fabricante numa chamada. A pessoa acessa por um link, vê e digita
comandos como um espectador comum, e o acesso expira sozinho.

### Modelo `TerminalLinkExterno` (`clientes/models.py`)

| Campo | Descrição |
|---|---|
| `id` | `UUIDField` primary key (`uuid4`) — **é** o token; a autorização inteira é ele, imprevisível |
| `acesso` | FK → `Acesso` |
| `criado_por` | FK → `User`, nullable (`SET_NULL`) |
| `criado_em` / `expira_em` | datetime |
| `revogado` | bool |
| `validar()` | retorna `(bool, motivo)` — checa `revogado` e `expira_em` |

`AcessoSessao` ganhou FK `link_externo` (nullable) para rastrear, na auditoria, que uma sessão veio
de um visitante externo e quem autorizou (`link.criado_por`) — ver
[AUDITORIA_ACESSOS.md](AUDITORIA_ACESSOS.md).

### Geração do link — sempre a partir de uma sessão compartilhada

`{"action": "criar_link_externo", "minutos": N}` (`_criar_link_externo`, `N` entre 5 e 240,
default 30): se a sessão ainda não estiver compartilhada, chama `_iniciar_compartilhamento()`
primeiro — um link sem sessão compartilhada não teria a quem o visitante se anexar. Qualquer
participante já autenticado da sessão (dono ou espectador interno) pode gerar/revogar links; um
visitante externo **nunca** pode (`TerminalLinkExternoConsumer._usuario_pode_acessar` sempre libera
o *join*, mas a ação `criar_link_externo` nem existe no `receive()` dessa subclasse).

### `TerminalLinkExternoConsumer` (`ws/ssh-link/`, `clientes/consumers.py`)

Subclasse de `SSHConsumer` para visitantes **sem login Django** (`self._crm_user = None` sempre):

- `connect()` aceita qualquer WebSocket, autenticado ou não — a autorização é 100% o token,
  validado só quando chega `{"action": "connect_link", "token": "..."}`.
- `_conectar_via_link()`: busca `TerminalLinkExterno` pelo token, chama `link.validar()`, confirma
  que existe `_SharedTerminalSession` ativa para aquele `acesso_id` (`_terminal_sessions.get()`) —
  se o dono nunca ativou o compartilhamento (ou já encerrou), o link fica "válido" no banco mas sem
  sessão viva pra entrar, e o visitante recebe erro pedindo um link novo. Reaproveita
  `_entrar_em_sessao_compartilhada()` sem nenhuma duplicação de lógica de anexação.
  **Nunca** abre conexão SSH/Telnet própria nem aceita `acesso_id` arbitrário.
  - `_usuario_pode_acessar()` sobrescrito para sempre retornar `True` — já validado pelo token antes
    de chegar lá.
  - `_label_usuario()` sobrescrito para `"Visitante externo"`.
- **Expiração automática:** ao entrar, agenda um `threading.Timer` para o tempo restante até
  `expira_em` — encerra a conexão sozinho (`close(code=4008)`) mesmo que ninguém revogue
  manualmente. Cancelado em `disconnect()` se a conexão cair antes.
- **Revogação manual:** `{"action": "revogar_link_externo", "token": "..."}` do lado do usuário
  interno (`_revogar_link_externo`) marca `revogado=True` no banco e localiza, na
  `_SharedTerminalSession`, qualquer viewer com `_link_externo_id` igual ao do link revogado —
  avisa (`share_ended`) e força o fechamento (`close(code=4008)`) imediatamente.

### Página pública (`clientes/views.py::terminal_link_externo`, sem `@login_required`)

`GET /clientes/terminal/link/<uuid:token>/` — valida o token no servidor (404 se não existe, 410 se
expirado/revogado, com `terminal_link_invalido.html`) e renderiza `terminal_externo.html`: uma
página **isolada**, sem sidebar de hosts, sem Wiki, sem Agent NOC, sem nenhuma outra parte do CRM —
só o terminal daquele único `Acesso`, com contador regressivo até a expiração (cosmético; quem
derruba a conexão de fato é o `threading.Timer` do lado do servidor). Conecta em `ws/ssh-link/`
mandando `{"action": "connect_link", "token": "..."}`.

### Frontend interno (`clientes/templates/terminal.html`)

Botão **🌐 Link Externo** (rodapé + menu mobile) abre um modal (`#linkExternoOverlay`) com seletor
de duração (15/30/60/120 min), botão "Gerar link" (`gerarLinkExterno()` → `criar_link_externo`),
campo com a URL pronta pra copiar e botão **"Revogar agora"**
(`revogarLinkExterno()` → `revogar_link_externo`).
