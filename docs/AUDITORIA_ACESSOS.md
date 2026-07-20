# Auditoria de Acessos — Documentação Técnica

**Data de Implementação:** 2026-07-20
**Arquivos principais:** `clientes/models.py`, `clientes/consumers.py`, `clientes/browser_vnc.py`,
`clientes/winbox_vnc.py`, `clientes/views.py`, `clientes/admin.py`, `templates/modal_acessos.html`
**Status:** ✅ Produção

---

## Visão Geral

Toda sessão de acesso a um equipamento (SSH, Telnet, WinBox Web, WinBox Nativo, WebFig) passa a
ser registrada para auditoria: **quem** (usuário do CRM, não credencial do equipamento) acessou
**qual** host, **quando**, de **qual IP**, e **o que fez** — comandos digitados e saída completa
do terminal para sessões texto, gravação de tela `.mp4` para sessões gráficas via VNC.

O objetivo é responder "quem mexeu em tal equipamento e o que foi executado" sem depender de logs
do próprio equipamento (que podem não existir ou ser apagados).

---

## Modelos

### `AcessoSessao` (`clientes/models.py`)

Uma linha por sessão de conexão a um `Acesso`.

| Campo | Tipo | Descrição |
|---|---|---|
| `acesso` | FK → `Acesso` | Equipamento acessado (`related_name='sessoes_auditoria'`) |
| `usuario` | FK → `User`, nullable | Usuário do CRM que conectou (`SET_NULL` — sessão sobrevive à exclusão do usuário) |
| `tipo` | choice | `ssh`, `telnet`, `winbox` (WinBox Web/VNC), `winbox_nativo`, `webfig` |
| `ip_origem` | IP | IP de origem da conexão WebSocket (`scope['client']`) |
| `status` | choice | `ativa` / `encerrada` |
| `iniciada_em` / `encerrada_em` | datetime | `auto_now_add` / preenchido ao fechar |
| `arquivo_video` | string | Caminho relativo a `MEDIA_ROOT` do `.mp4` (só `winbox`/`webfig` via VNC) |
| `transcript` | text | Saída completa da tela, ANSI removido (só `ssh`/`telnet`) |
| `duracao_segundos` | property | `encerrada_em - iniciada_em`, ou `now() - iniciada_em` se ainda ativa |

### `AcessoComando` (`clientes/models.py`)

Um comando digitado (stdin), FK → `AcessoSessao` (`related_name='comandos'`). Campos: `comando`
(texto completo com Enter), `executado_em`.

**Migrações:** `0080_acessosessao_acessocomando.py` (cria os dois modelos), `0081_acessosessao_transcript.py`
(adiciona `transcript` — feito em migração separada porque foi implementado depois do `AcessoComando`).

---

## Por Protocolo

### SSH / Telnet — Transcript + Comandos

Implementado em `SSHConsumer` (`clientes/consumers.py`).

**Transcript** (`_registrar_saida`): todo output do equipamento passa por `send_output()`, que
agora também chama `_registrar_saida(text)` antes de enviar ao browser. O texto é limpo com
`_ANSI_RE` (remove CSI/OSC/seleção de charset) e caracteres de controle não imprimíveis, e
acumulado em `self._transcript_buf`. **Não** é um emulador de terminal completo — não reconstrói
telas com cursor pulando pra trás, mas cobre bem o caso comum de CLI de rede (eco de comando +
saída linear).

- Limite de **3.000.000 caracteres** por sessão (`_TRANSCRIPT_MAX`) — mantém a cauda (conteúdo
  mais recente) e prefixa `[...transcript truncado...]` quando estoura, evitando crescimento sem
  limite em sessões de horas.
- Só é gravado no banco no **fechamento** da sessão (`_encerrar_sessao_auditoria`) — gravar a
  cada chunk seria uma `UPDATE` por caractere ecoado, inviável dado o volume de tráfego do
  terminal.

**Comandos** (`_registrar_digitacao`): reconstrói linhas de comando a partir do stream de teclas
cru que chega keystroke-a-keystroke no hot path binário de `receive()`. Rastreia a posição do
cursor (`_cmd_cursor`) para que edições no meio da linha (setas, Home/End, Delete) fiquem no
lugar certo em vez de grudar no final. Reconhece sequências CSI (`ESC [ letra`) **e** SS3
(`ESC O letra`) para as setas — equipamentos de rede colocam o terminal em "application cursor
keys mode" ao entrar em paginadores tipo `more`/`less`, e nesse modo as setas saem como SS3 em vez
de CSI.

- `Enter` → grava o comando acumulado em `AcessoComando` e limpa o buffer.
- `Ctrl+C` → grava o que tinha no buffer com sufixo `  [Ctrl+C]` (comando abortado, mas registrado).
- Setas `UP`/`DOWN` (recall de histórico do próprio equipamento) → descarta o buffer em vez de
  tentar reconstruir, porque o texto recuperado pelo histórico vem via **stdout** (eco do
  equipamento), não há como reconstruí-lo a partir do stdin sozinho — gravar seria arriscar um
  comando incompleto/errado.

Ambas as funções são no-op se não houver `self._sessao_auditoria` (ex: antes do `connect` do
protocolo terminar).

### WinBox / WebFig via VNC — Gravação de Tela

Implementado em `BrowserVNCManager` (`clientes/browser_vnc.py`) e `WinboxVNCManager`
(`clientes/winbox_vnc.py`), acionado por `WinboxVNCConsumer` (`clientes/consumers.py`).

Ao abrir a sessão, o consumer monta o caminho de gravação
(`gravacoes_acessos/<acesso_id>/<sessao_id>_<timestamp>.mp4` dentro de `MEDIA_ROOT`) e passa via
`record_path=` ao manager. Depois que o navegador/WinBox termina de subir (delay de 1.5s — evitar
disputa de CPU durante o carregamento inicial, que no WinBox 3.43/Wine causava ícones renderizados
com fundo preto), o manager sobe um processo `ffmpeg` gravando o display X11 (`x11grab`) inteiro:

```bash
ffmpeg -y -loglevel error -f x11grab -video_size WxH -framerate 8 \
    -i :<display> -vcodec libx264 -preset ultrafast -crf 28 -pix_fmt yuv420p <record_path>
```

- Falha ao subir o `ffmpeg` (ex: não instalado) **não derruba a sessão** — só desliga a gravação
  com log de aviso (`manager.recording` fica `False`).
- Se `manager.recording` for `True` após `start()`, o consumer salva `arquivo_video` na
  `AcessoSessao`.

**Bug corrigido — gravação de 0 bytes em sessões longas (`stop()` idempotente):**
`limpar_recursos()` pode ser chamado concorrentemente pela thread de leitura do VNC e pela thread
de `disconnect()` do WebSocket. Sem proteção, o `ffmpeg` recebia **dois `SIGTERM` em sequência** e,
no segundo, abortava sem finalizar o `.mp4` (trailer nunca escrito — comportamento documentado do
próprio `ffmpeg`), gerando um arquivo de 0 bytes mesmo em sessões que duraram horas. Corrigido com
um `threading.Lock` + flag `_stopped` em ambos os managers, tornando `stop()` idempotente. O
timeout de `p.wait()` após o `terminate()` também subiu de 2s → **5s** especificamente para o
processo `ffmpeg` — ele precisa de mais tempo que os outros processos (Xvfb, x11vnc, navegador)
para finalizar o mux do `.mp4`.

---

## Autenticação Obrigatória no WebSocket

`SSHConsumer.connect()`, `WinboxConsumer.connect()` e `WinboxVNCConsumer.connect()` agora rejeitam
a conexão (`self.close(code=4001)`) se `self.scope['user']` não existir, for `AnonymousUser` ou
não estiver autenticado — pré-requisito para saber **quem** registrar na `AcessoSessao`
(`usuario=self._crm_user`). Antes dessa mudança a autenticação da sessão de terminal dependia
apenas do middleware HTTP da view que servia a página; a auditoria exige a checagem também no
nível do consumer, já que é ele quem cria o registro.

---

## API / Endpoints (`clientes/views.py`, `clientes/urls.py`)

| Endpoint | View | Descrição |
|---|---|---|
| `GET /clientes/acessos/<acesso_id>/auditoria/` | `listar_sessoes_auditoria` | Lista sessões do acesso, mais recentes primeiro, com `total_comandos` anotado (`Count`) |
| `GET /clientes/auditoria/sessao/<sessao_id>/comandos/` | `listar_comandos_sessao` | Lista comandos digitados numa sessão |
| `GET /clientes/auditoria/sessao/<sessao_id>/transcript/` | `ver_transcript_sessao` | Retorna o transcript completo da sessão |

**Permissão:** staff/superuser vêem tudo; usuário comum só vê sessões de acessos do **próprio**
cliente vinculado (`Cliente.objects.get_by_usuario_vinculado(request.user)`), mesmo padrão usado
nos outros endpoints de `Acesso` (comentários, etc.) — `403` caso contrário.

`listar_sessoes_auditoria` retorna, por sessão: tipo, usuário (nome ou username), IP, status,
datas formatadas `dd/mm/aaaa HH:MM:SS`, `duracao_segundos`, `video_url` (`MEDIA_URL + arquivo_video`
ou `null`), `total_comandos` e `tem_transcript` (booleano — evita o frontend precisar buscar o
transcript só para saber se existe).

---

## Admin (`clientes/admin.py`)

`AcessoSessaoAdmin` — lista com filtro por `tipo`/`status`, busca por acesso/host/usuário, inline
`AcessoComandoInline` (somente leitura) mostrando os comandos da sessão.

---

## Frontend

### Botão de acesso (`clientes/templates/listar.html`)

Novo ícone `fa-shield-halved` (roxo `#a371f7`) ao lado do botão de Comentários em cada card de
acesso, chamando `abrirModalAuditoriaAcesso(acesso.id, tipo)`.

### Modal (`templates/modal_acessos.html`)

`#modalAuditoriaAcesso` — lista as sessões (`carregarSessoesAuditoria` → `GET .../auditoria/`),
cada item mostra usuário/tipo/status/duração/IP e botões condicionais:

- **"Assistir gravação"** — só se `video_url` existir (abre em nova aba)
- **"Comandos (N)"** — só para `tipo` em `{ssh, telnet}` — expande/colapsa (`toggleComandosSessao`),
  carrega sob demanda (`dataset.carregado`) via `GET .../comandos/`
- **"Transcript completo"** — só se `tem_transcript` — expande/colapsa (`toggleTranscriptSessao`),
  mesmo padrão de carregamento sob demanda via `GET .../transcript/`

Todo texto vindo do backend (usuário, comandos, transcript) passa por `escapeHtml()` antes de ir
para o DOM.

---

## Volume de Dados — Cuidados Operacionais

- **Vídeos** (`MEDIA_ROOT/gravacoes_acessos/<acesso_id>/`) não têm rotina de expurgo automática —
  crescem indefinidamente. Sessões WinBox/WebFig longas geram arquivos grandes mesmo com CRF 28 e
  8fps. Considerar rotina de limpeza por idade caso o disco fique um gargalo (não implementada
  nesta versão).
- **Transcript** já tem limite embutido por sessão (3MB de texto), mas não há limite no total de
  sessões armazenadas no banco.

---

**Última atualização:** 20/07/2026
**Autor:** CampeloSuporte
