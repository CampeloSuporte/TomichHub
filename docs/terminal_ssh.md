# Terminal SSH — Documentação Técnica

**Arquivo:** `clientes/consumers.py`  
**Classe principal:** `SSHConsumer` (Django Channels WebSocket Consumer)  
**Atualizado em:** 2026-09-03

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
| `ServerAliveInterval`         | `20` s — mantém sessão viva em links instáveis     |
| `ServerAliveCountMax`         | `3` tentativas antes de desconectar                |
| `HostKeyAlgorithms`           | `+ssh-rsa,ssh-dss` — suporte a equipamentos legados|
| `Ciphers`                     | inclui `aes128-cbc`, `aes256-cbc`, `3des-cbc`      |
| `PreferredAuthentications`    | `password,keyboard-interactive`                    |

---

## Queda da sessão com o terminal aberto — Adicionado em 2026-09-03

O WebSocket com o browser e a sessão SSH com o equipamento são coisas separadas: a sessão
podia morrer (timeout de ociosidade do equipamento, `logout`, queda do caminho até ele) com
o WebSocket ainda aberto. Antes, isso era **silencioso**: a thread de leitura terminava, a
aba continuava com a bolinha de "conectado" e cada tecla digitada devolvia uma linha nova de
`✕ ERRO: Erro ao enviar comando: Socket is closed` — sem dizer o que tinha acontecido nem o
que fazer.

**Keepalive na sessão com o equipamento.** `connect_ssh_via_proxy` e
`_connect_ssh_paramiko_direct` chamam `transport.set_keepalive(20)` logo após o
`start_client()`. O `_ProxyPool` já fazia isso na conexão com o *proxy*; faltava na sessão
com o equipamento, que fica ociosa enquanto o operador lê a tela e podia ser descartada por
NAT/firewall no caminho proxy→equipamento sem FIN nenhum. O keepalive também é o que faz o
Paramiko *perceber* que o outro lado morreu, em vez de só descobrir na próxima escrita.
Validado ao vivo no BA-ALC-A01-SW-01 (Huawei, via proxy DS TECH): a sessão com keepalive
sobrevive a 180 s de ociosidade e continua ecoando.

**Aviso único e claro.** `_notificar_sessao_encerrada()` manda
`{"type": "error", "sessao_encerrada": true}` uma só vez por sessão. É chamado:

- pelo `finally` das quatro threads de leitura (`_read_paramiko_shell`, `read_ssh_output`,
  `read_telnet_output`, `_read_pexpect_shell`) **quando `is_reading` ainda estava `True`** —
  ou seja, o loop caiu sozinho e não foi `_fechar_recursos_fisicos()` pedindo parada;
- por `enviar_comando()`, quando a escrita falha com socket fechado/EOF.

Em sessão compartilhada o aviso vai para todos os espectadores (`_broadcast_para`). O
frontend (`terminal.html`) apaga a bolinha de conectado da aba e escreve a linha em amarelo
pedindo **↻ Reconectar**, em vez do vermelho de erro repetido.

**Reconectar mantém o histórico.** `reconectarTerminalAtivo()` (`terminal.html`) e
`reconectarTerminal()` (`modal_ssh_terminal.html`) fecham o WebSocket e abrem uma sessão SSH
nova, mas **não** limpam o xterm: escrevem só a linha `↻ Reconectando...` e a saída da nova
sessão continua embaixo, com a rolagem anterior preservada. Para apagar a tela continua
existindo o botão de limpar.

**Motivo da queda no log.** `_read_paramiko_shell` agora registra por que o loop terminou:
`motivo=EOF — o outro lado fechou o canal`, `select falhou: …`, `recv falhou: …`,
`canal já fechado` ou `parada solicitada`, mais `transporte_ativo=`. Sem isso o log só dizia
"Thread paramiko shell finalizada" e não dava para separar queda do equipamento de
encerramento normal.

### Uma aba = um WebSocket = uma sessão VTY

Cada WebSocket abre uma sessão SSH **nova** no equipamento. A janela do terminal recebia o
acesso por dois caminhos (`localStorage['acessoPendente']` na carga da página e o
`postMessage` `NOVA_CONEXAO` do opener, ambos em `terminal_tab_manager.js`) e, quando os dois
chegavam, nasciam duas abas para o mesmo host com ~1,5 s de diferença — dois logins VTY. Em
Huawei o segundo login costuma cair em
`Disconnect (code 2): Reenter times have reached the upper limit`, e a aba duplicada mostrava
"Senha incorreta ou acesso negado" com a sessão boa aberta ao lado.

Duas travas em `terminal.html`:

- `abrirTerminal()` ignora a abertura do mesmo `acesso.id` em menos de 5 s (ativa a aba que já
  existe). Abrir o mesmo host de propósito depois disso continua valendo.
- `conectarWebSocket()` não abre um segundo socket se o da aba está `CONNECTING`/`OPEN`.

---

## Equipamento com SSH legado (ssh-dss) — Adicionado em 2026-09-03

O Paramiko 4.0.0 **removeu o suporte a DSA** — `paramiko.DSSKey` não existe mais. Equipamento
antigo que só oferece `ssh-dss` como host key (e normalmente só
`diffie-hellman-group1-sha1` como KEX) faz o handshake abortar em ~0,1 s com:

```
Connected (version 1.99, client IPSSH-6.6.0)
paramiko.ssh_exception.IncompatiblePeer: Incompatible ssh peer (no acceptable host key)
```

Nenhum ajuste de cadastro resolve — é limitação da biblioteca. O `ssh` do sistema (OpenSSH 9.2
do Debian 12) ainda negocia com esses equipamentos, e o comando montado no caminho pexpect já
leva `HostKeyAlgorithms=+ssh-rsa,ssh-dss` e `diffie-hellman-group1-sha1` na lista de KEX.

Então `connect_ssh_via_proxy()`, ao pegar um erro com `no acceptable` na mensagem (cobre
"no acceptable host key" e "no acceptable kex algorithm"), fecha o transporte pela metade e cai
para `connect_ssh_parks_proxy(acesso, rotulo='SSH legado')` — o mesmo caminho
pexpect + `ProxyCommand` usado nas OLTs Parks. O usuário vê
`↩️ Equipamento com SSH legado (ssh-dss) — usando o ssh do sistema...` e a sessão abre normal.

`connect_ssh_parks_proxy()` deixou de ser exclusiva das Parks: recebe `rotulo` (usado no log e
nas mensagens) e serve aos dois casos em que o Paramiko não dá conta do peer — firmware Parks
que crasha no `invoke_shell` e peer com algoritmo legado.

**Parks sem "parks" no cadastro.** `conectar()` só manda para o caminho pexpect quando o
fabricante do modelo (ou o `tipo`) contém "parks". Acesso sem modelo e com tipo livre (ex.: acesso
1493, `OLT-IT_STO.ANT-23`, 10.1.23.1 via DS TECH) ia para o Paramiko; o banner `SSH-2.0-Parks` só
trocava o terminal para `vt100` e mandava o pty-req completo, e mesmo assim a CLI da OLT respondia
`Aiee, segfault! You should probably report this as a bug to the developer` e fechava o canal
(o usuário via "Sessão encerrada pelo equipamento"). Agora `connect_ssh_via_proxy()`, ao ler
`parks` no banner (e não sendo Huawei), fecha o transporte **antes da autenticação** e chama
`connect_ssh_parks_proxy(acesso)`. ZTE continua no Paramiko — o desvio é só para Parks.

**Senha errada deixou de virar "conectado".** Depois de enviar a senha do equipamento, o
`expect` só olhava por prompt ou TIMEOUT: com credencial errada no cadastro, esperava 15 s e
mesmo assim mandava `connected` — a aba ficava "conectada" parada num prompt de senha. Agora
`Permission denied` / `Access denied` / `Authentication failed` viram erro imediato:
*"Senha recusada pelo equipamento — confira usuário e senha no cadastro do acesso"*.

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

## Protocolo extra do host (`protocolo_id`) — Adicionado em 2026-09-11

O `connect` pode trazer `protocolo_id`, um `AcessoProtocolo` do host (ex.: SSH na 22 de um
equipamento com HTTP como padrão). `conectar_acesso` chama
`acesso.aplicar_protocolo_extra(..., permitidos=('SSH', 'TELNET'))`, que troca porta e protocolo
**só em memória**; o resto do fluxo (proxy, OpenVPN, direto, Huawei, Parks, SSH legado) não muda.
No extra, o protocolo é o cadastrado; no padrão, continua `detect_protocol(porta)`.

A sessão compartilhada de um extra usa a chave `(acesso_id, protocolo_id)`
(`_SharedTerminalSession.chave`), separada da sessão do padrão. Link externo só no acesso padrão.
Ver [acessos_protocolos_extras.md](acessos_protocolos_extras.md).

---

## Tela dividida (dois terminais lado a lado) — Adicionado em 2026-09-25

Com **duas ou mais abas** abertas aparece o botão **Dividir tela** à direita da barra de abas
(`#btnSplit`, `clientes/templates/terminal.html`). Ligado, a área do terminal mostra dois
painéis lado a lado (empilhados no celular, ≤ 640px); clicar de novo (**Tela única**) volta
ao modo de uma aba por vez.

Tudo é frontend, no `TerminalManager` — nenhuma sessão SSH é aberta ou fechada ao dividir:

- `split` / `splitPanes = [esquerda, direita]`: ids dos terminais visíveis. `activeTerminal` é
  sempre um dos dois e é a metade **em foco** (borda azul no topo): recebe o teclado, a Wiki,
  os Scripts, o Agent e o botão Compartilhar, como antes.
- Clicar numa aba já visível só passa o foco; clicar numa aba escondida (ou abrir um host
  novo) troca o terminal **da metade em foco**. Clicar dentro de um painel também passa o foco
  (no WinBox o clique fica no iframe — use a aba).
- Fechar uma das metades põe outra aba aberta no lugar; se não houver outra, volta à tela única.
- `aplicarLayout()` é o único lugar que mostra/esconde painéis e marca abas; depois dele
  `refitTerminaisVisiveis()` refaz o `fit()` dos dois xterm, e o `onResize` já existente manda o
  novo `cols/rows` ao PTY de cada host.
- **Divisor arrastável e orientação (2026-10-20).** Entre as duas metades fica o `#splitDivisor`
  (`order: 1`; os painéis usam `order` 0 e 2). Arrastar muda a variável CSS `--split-pct` da
  primeira metade (15–85%) e refaz o `fit()` a cada frame. Duplo clique volta a 50%. O botão ao lado
  de **Tela única** (ou `Ctrl+Shift+O`) alterna entre lado a lado e um em cima do outro
  (`.terminal-content.empilhado`). No celular é sempre empilhado. Tamanho e orientação ficam em
  `localStorage['terminalPrefs']`.

---

## Busca, copiar/colar, atalhos e renderizador — Adicionado em 2026-10-20

Tudo é frontend, em `clientes/templates/terminal.html`. O backend não mudou. O link externo
(`terminal_externo.html`) e o modal do terminal (`modal_ssh_terminal.html`) continuam como antes.

### Busca no histórico (`xterm-addon-search` 0.13.0)

- Arquivo: `/opt/crm/static/js/xterm-addon-search.min.js`. Assim como o `xterm.min.js` e os outros
  addons, é biblioteca de terceiros e **não é versionado**: num servidor novo, baixe com
  `curl -L https://cdn.jsdelivr.net/npm/xterm-addon-search@0.13.0/lib/xterm-addon-search.js -o static/js/xterm-addon-search.min.js`.
  Essa versão casa com o xterm 5.3.0.
- `Ctrl+Shift+F` ou **🔍 Buscar** abre a barra `#termBusca` no canto da área do terminal. A barra
  age sempre no terminal **ativo**: trocar de aba limpa o destaque da anterior e refaz a busca na
  nova. Texto selecionado no terminal vira o termo.
- Opções: diferenciar maiúsculas (`Aa`) e regex (`.*`). Uma regex inválida não é enviada ao
  addon. O contador vem do `onDidChangeResults`. O addon para de contar em 1000 ocorrências, e aí
  aparece `1000+`.
- Os destaques usam decorações, que exigem `allowProposedApi: true` no `new Terminal`.
- O `scrollback` subiu de 5.000 para 20.000 linhas. Assim um `show running-config` inteiro cabe e a
  busca alcança o começo.

### Copiar e colar

- `Ctrl+C` **com seleção** copia e não manda nada ao equipamento. Sem seleção, continua sendo o
  `^C` de interrupção. `Ctrl+Shift+C` só copia. `Ctrl+Shift+A` seleciona todo o histórico.
- `Ctrl+V` / `Ctrl+Shift+V`: o handler do xterm devolve `false` **sem** `preventDefault`, e o
  navegador faz a colagem nativa. Isso não precisa de permissão de leitura da área de transferência.
- **Toda colagem** passa por `colarNoTerminal()`: um listener de `paste` em captura no painel,
  antes do xterm. Com mais de uma linha (sem contar a quebra final), abre o `uiConfirm` com o host e
  as 10 primeiras linhas. Cada linha vira um comando, e colar um bloco de config no equipamento
  errado é um acidente comum. Com o terminal desconectado, nada é colado.
- Preferências na janela **⌨ Atalhos** (F1), todas ligadas por padrão:
  - copiar ao selecionar (PuTTY);
  - botão direito copia com seleção e cola sem seleção (`navigator.clipboard.readText`, que o
    navegador pode pedir para autorizar; se negar, avisa para usar Ctrl+V);
  - confirmar colagem de várias linhas.
- Escrever na área de transferência usa `navigator.clipboard.writeText` em contexto seguro. Fora
  dele, cai no `execCommand('copy')` com um textarea temporário e devolve o foco ao terminal.

### Atalhos

Os atalhos têm duas portas de entrada:

- `tratarAtalhoTerminal()`, chamado pelo `attachCustomKeyEventHandler` quando o foco está no
  xterm. Devolver `true` impede que a tecla vire bytes para o equipamento.
- `tratarAtalhoGlobal()`, chamado pelo `keydown` do documento quando o foco está no resto da
  página.

O evento já tratado sai marcado com `__atalhoTratado` e não roda duas vezes. Num campo de texto
(busca, Agent, Wiki) só valem `Ctrl+Shift+F` e `F1`; o resto das teclas é do campo. A comparação usa
`e.code` (tecla física), então funciona igual em ABNT2 e US.

| Tecla | Ação |
|---|---|
| `Ctrl+Shift+F` | Buscar no histórico |
| `Ctrl+Shift+K` | Limpar a tela |
| `Ctrl` `+` / `-` / `0` | Fonte de todas as abas (9–28px, salva). Só com foco no terminal; fora dele é o zoom do navegador |
| `Alt+1…9` | Ir para a aba N (na ordem da barra) |
| `Ctrl+Shift+←/→` | Aba anterior / próxima |
| `Ctrl+Shift+D` | Dividir / tela única |
| `Ctrl+Shift+O` | Lado a lado ↔ empilhado |
| `F6` | Passar o teclado para a outra metade |
| `F1` | Janela de atalhos e preferências |
| `Ctrl+R` | Reconectar (já existia) |

### Renderizador: Canvas (WebGL removido em 22/10/2026)

De 20 a 22/10/2026 o terminal usou WebGL (`xterm-addon-webgl` 0.16) nas primeiras 10 abas. Em
máquina real (GPU/driver do usuário) a tela ficava **preta**: o equipamento respondia, a saída
entrava no buffer do xterm, mas não era pintada. Só aparecia quando algo obrigava um redesenho
completo — por exemplo o "↻ Reconectando...", por isso o sintoma "clico em reconectar e o comando
aparece acima". Caso real: Startnet, `SW3-PE-JAPURANA-CEM-01` (172.24.3.0), usuário `mmarinho`;
o log do Daphne mostrava toda a saída do `dis transceiver ... verbose` chegando normalmente.

No Chrome headless (WebGL por software/SwiftShader) o defeito não reproduz, nem com perda e
restauração de contexto forçadas (`WEBGL_lose_context`). Como o Canvas rodou meses sem queixa,
`carregarRenderer()` voltou a carregar só o Canvas (`tObj.renderer` = `canvas` | `dom`) e a página
não inclui mais o `xterm-addon-webgl.min.js`. Não reintroduzir o WebGL sem testar em GPU real.

Rede de segurança: no `visibilitychange` (voltar para a aba do navegador) os terminais visíveis
recebem `terminal.refresh()` de todas as linhas.

### Teste

Foi testado num Chrome headless com um WebSocket falso injetado na página, que despeja 3.000 linhas.
O teste rodou com WebGL (swiftshader, antes da remoção) e sem GPU (Canvas) e cobriu: busca (contador, próximo, regex,
sem resultado, Esc), Ctrl+C com e sem seleção, colagem de 1 linha direta, de 3 linhas com
confirmação e pelo botão direito, Alt+1, Ctrl+Shift+→, divisor arrastado a 30% com `resize` no PTY,
empilhar, F6, fonte e F1.

## 💡 Comandos úteis do equipamento — Adicionado em 2026-10-26

Só no terminal web (`clientes/templates/terminal.html`). O link externo e o modal do terminal não
têm a lâmpada. Abre pelo botão **💡 Comandos** no rodapé, pelo menu mobile ou com `Ctrl+Shift+L`.

**Como identifica o equipamento:** `GET /clientes/acessos/<id>/comandos-uteis/`
(`comandos_uteis_acesso`, mesma permissão do `interfaces-backup`) chama
`clientes/comandos_uteis.py::identificar_familia`. Ela olha o Modelo do acesso (fabricante + nome)
e, sem modelo, o `tipo`. Famílias: `huawei_vrp` (S67xx, S5735, NE8000, ATN), `huawei_olt`
(MA5800/MA5600), `datacom` / `datacom_olt` (DM46xx), `cisco_ios` / `cisco_nxos` (Nexus), `juniper`,
`mikrotik`, `zte_c300` / `zte_titan` (C6xx), `raisecom`, `fiberhome_5k` (AN5516 — padrão para
qualquer FiberHome) / `fiberhome_6k` (AN6000), `linux`, `proxmox`. Parks,
Intelbras, VSOL e os outros sem catálogo, assim como acesso sem modelo nem tipo reconhecível, mostram
um seletor "Tipo". A escolha fica no `localStorage` do navegador (`cu-familia-<id>`) e vai como
`?familia=`.

**Painel:**
- Campos variáveis (`{porta}`, `{ip}`, `{mac}`, `{vlan}`, `{sn}`, `{slot}`, `{pon}`, `{onu}`,
  `{texto}`, `{usuario}`, `{senha}`, `{tipo_onu}`, `{placa}`, `{arquivo}`) ficam no topo e valem para todos os comandos da família. A porta sugere as interfaces do
  último backup (`interfaces-backup`), físicas com descrição primeiro.
- **⌨ Inserir** digita o comando no terminal ativo **sem Enter** (`terminal.paste`). **▶** digita e
  manda o Enter. Comando de várias linhas (sinal óptico da ONU no MA5800) só tem ▶ e pede
  confirmação. Campo vazio fica vermelho e nada é enviado.
- Trocar de aba com o painel aberto recarrega para o equipamento da nova aba.
- **Sub-menu de versão** (2026-10-28): família com `linha`/`versao` no catálogo (hoje FiberHome:
  `AN5516 · 5500` e `AN6000 · 6k`) mostra botões "Versão" abaixo do Tipo, e cada um lista os
  comandos daquela versão. O seletor "Tipo" mostra a linha uma vez só ("FiberHome OLT (escolha a
  versão)", `membros` em `lista_familias`). A versão escolhida fica salva por host como qualquer
  troca de tipo (`cu-familia-<id>`).

**Catálogo** (`FAMILIAS` em `comandos_uteis.py`): só leitura (show/display/print e diagnóstico
Linux), mais os comandos que só mudam de modo/diretório ou desligam a paginação. O teste
`CatalogoTest.test_todo_comando_e_de_leitura` barra qualquer outra coisa — **exceto** comando
criado com `_cfg()` (`altera: true` no JSON, 2026-10-28): ⚠ laranja ao lado da descrição, borda
laranja, e o **▶** pede confirmação ("Alterar a configuração do equipamento?"); **⌨ Inserir**
continua só digitando. Hoje só a FiberHome tem comandos ⚠. Senha/usuário entram pelos campos
`{usuario}`/`{senha}`, nunca fixos no catálogo.

**Grupos são assuntos.** Cada grupo é um assunto (Sistema, Placas, ONU — consulta, ONU — sinal
óptico, ONU — autorizar / remover, ONU — PPPoE / bridge, ONU — Wi-Fi / acesso remoto / TR-069,
PON, MAC / L2, Uplink / LAG, Gerência / SNMP / Syslog, Usuários da CLI, Backup / firmware na
5500), com leitura primeiro e configuração depois. O painel mostra um chip por assunto
("Todos" + um por grupo) para ver só aquele; vale para todas as famílias com mais de um grupo,
fica guardado por família enquanto a página está aberta, e a busca por texto filtra dentro do
assunto escolhido.

FiberHome AN5516: a CLI é por diretórios (`cd onu`, `cd gpononu`, `cd service`, `cd ..`), então
cada grupo começa com o `cd` do diretório. Há dois dialetos de ONU conforme o firmware —
`slot X pon Y onu Z` (diretório `onu`, RP1000) e `slot X link Y onu Z` (`gpononu`) — os dois
estão no catálogo. Distância da PON segue a lista da equipe: 6k usa `cd card` +
`set pon_distance slot … pon …`, 5500 usa `config` / `interface pon` / `distance …` (assunto
"PON" nas duas). O filtro do painel ignora acento e também procura no título do
grupo ("distancia" acha). Comandos de
telnet na própria ONU (`Config\wan#`) e o ONCONFIG do ANM2000 ficaram fora.

Cada comando tem `conferido`:
- ✓ **conferido**: rodado num equipamento da família. Raisecom em 26/10/2026 (RAX711), e os
  comandos que o Agent NOC e a automação BGP já usam validados (transceiver Huawei/Datacom, rotas
  BGP recebidas/anunciadas, sessões BGP do MikroTik).
- ○ **não conferido**: sintaxe documentada do fabricante, ainda não rodada aqui.

A validação em lote dos ○ (um equipamento por família, só `show`/`display`) foi barrada pela
política de segurança do assistente em 26/10/2026 e ficou pendente. Ao conferir um comando num
equipamento real, troque `conferido` para `True`. Comando que falhar sai do catálogo.

Testes: `clientes/tests_comandos_uteis.py` (identificação, catálogo, endpoint). O painel foi
testado num Chrome headless com WebSocket falso: abrir sem terminal, identificar o RAX700, campo
vazio, Inserir, ▶, filtro, troca de tipo e `Ctrl+Shift+L`.

## Celular: teclado virtual — Corrigido em 2026-10-27

O teclado do celular vai por um `<input id="mobileKeyboardInput">` escondido (o toque no
terminal ou o botão ⌨ foca o campo; `input`/`keydown` viram bytes no WebSocket).

- **Altura da página = área visível.** Chrome Android e Safari só encolhem o `visualViewport`
  quando o teclado abre; com `100vh` o prompt ficava atrás do teclado e o navegador deslocava a
  página, que (body `position: fixed`) não voltava. `ajustarAreaVisivel()` grava
  `--app-h`/`--app-top` a partir do `visualViewport` (resize e scroll) e o body, o
  `.terminal-container` e os painéis laterais usam essas variáveis; o `fit()` em seguida põe o
  prompt logo acima do teclado. O meta viewport também pede `interactive-widget=resizes-content`.
- **Arrastar rola, tocar abre o teclado.** O `touchend` do painel só ativa o teclado em toque
  parado (≤ 10 px); arrastar fica com a rolagem de histórico do próprio xterm.
- **Toque depois do "voltar" do Android.** O teclado some mas o campo segue focado e `focus()`
  não faz nada — parecia travado. Se o campo já está focado, `blur()` + `focus()`.
- **Digitação parava depois de um tempo.** O `blur` do campo recoloca `readonly` 200 ms depois;
  se nesse meio-tempo o campo voltava a ter foco (o próprio blur+focus acima, o toque no botão
  ⌨), ele ficava focado *e* readonly — teclado aberto, nada chegava ao terminal, e tocar de novo
  repetia a corrida. Agora o `readonly` só volta se o campo continuar sem foco, e o `focus` o tira.
- O campo escondido fica dentro da tela (canto inferior, `opacity: 0`) e foca com
  `preventScroll`; digitar volta o terminal para o fim (`scrollToBottom`).
