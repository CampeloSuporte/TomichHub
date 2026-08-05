# 📞 Sistema de Atendimento — Documentação Técnica

## 📋 Visão Geral

Plataforma de atendimento ao cliente integrada ao CRM, similar ao Chatwoot. Centraliza o gerenciamento de tickets de suporte via WhatsApp (Evolution API v2), com tarefas, alertas automáticos, lembretes pessoais e relatórios completos.

**Última atualização:** 05/08/2026  
**Status:** ✅ FUNCIONAL  
**Stack:** Django, PostgreSQL, Celery, WebSocket (Django Channels), JavaScript vanilla

---

## 🎯 Recursos Principais

| Recurso | Descrição |
|---------|-----------|
| Dashboard | Estatísticas em tempo real de chamados abertos, resolvidos, agentes online |
| Inbox | 3 abas: Assumidos / Abertos / Em Andamento — **indicador de mensagem não lida** em tempo real |
| Chat | Visual estilo **WhatsApp Dark** — bolhas com rabicho, ✓✓ de enviado, campo em pílula; envio de mídia, tags, transferência, terminal de hosts |
| **Tarefas** | Board em 4 colunas com vinculação de conversas e lembretes automáticos |
| Auto Atendimento | Fluxo de boas-vindas que coleta assunto e categoria automaticamente |
| Relatórios | Tabela + PDF com assunto, categoria, agente, duração por empresa/período |
| Alertas Celery | Resumo diário no grupo + lembretes pessoais manhã e meio-dia por WhatsApp |
| Configurações | IA, tags, categorias, permissões, alertas, contatos de atendentes |

---

## 🗂️ Arquitetura de Modelos

### `WhatsAppConnection`
Instância da Evolution API.

```
id (UUID), name, evolution_url, api_key, instance_name, business_phone,
color, is_active, webhook_configured, last_sync
```

### `ContactGroup`
Grupo ou contato WhatsApp sincronizado.

```
jid, connection (FK), name, is_group, cliente (FK), company (FK),
ai_enabled, blocked, status (active/archived/deleted)
```

### `Conversation`
Chamado/ticket de atendimento.

```
id (UUID), conversation_id (int, auto), group (FK), cliente (FK),
category (FK), status (new/open/pending/resolved/closed),
priority (low/medium/high/urgent), assigned_to (FK User),
subject, title, resolution, tags (M2M),
is_task_conv (bool) ← se True, novas mensagens abrem novo chamado
closed_at, resolved_at, last_message_at
```

### `Message`
Mensagem dentro de uma conversa.

```
id (UUID), conversation (FK), sender_type (customer/agent/ai/system/internal),
sender (FK User), message_type (text/image/document/audio/video/location/system),
content, external_id (unique), attachment_url, is_internal, is_read
```

`is_read` já existia mas não era usado pra exibir nada — desde 05/08/2026 é a fonte de
verdade do **indicador de mensagem não lida** (ver seção própria abaixo). É marcado
`True` para mensagens `sender_type='customer'` sempre que a conversa é aberta
(`conversation_detail` ou o mini-chat flutuante das bolhas).

### `Task` *(novo — migration 0005)*
Tarefa da equipe de atendimento.

```
id (UUID), title, description,
status (pending/in_progress/done/cancelled),
priority (low/medium/high/urgent),
assigned_to (FK User), created_by (FK User),
due_date, conversations (M2M via TaskConversation)
is_overdue: property (bool)
```

### `TaskConversation` *(novo — migration 0005)*
Vínculo M2M entre tarefa e conversa.

```
task (FK), conversation (FK), added_by (FK User), added_at
unique_together: (task, conversation)
```

### `AttendantContact` *(novo — migration 0005)*
Número de WhatsApp do atendente para receber lembretes pessoais.

```
user (OneToOne), phone (str, ex: "5511999999999"),
connection (FK WhatsAppConnection), reminders_enabled (bool)
get_jid() → formata JID automaticamente
```

### Outros modelos existentes
`Category`, `Tag`, `ConversationActivity`, `AgentStatus`, `QuickMessage`,  
`ChatFlow`, `ChatFlowSession`, `SystemSetting`, `ChatbotConfig`

---

## ✅ Sistema de Tarefas

### Visão geral
Substitui o antigo Kanban. Permite criar tarefas, associar conversas a elas e acompanhar o andamento da equipe.

### Board de Tarefas (`/atendimento/tarefas/`)
- 4 colunas: **Pendente** | **Em Andamento** | **Concluída** | **Cancelada**
- Filtros por status e "Minhas tarefas"
- Botão "Nova Tarefa" abre modal com: título, descrição, prioridade, prazo, responsável
- Clicar no card abre edição completa com lista de conversas vinculadas

### Menu de Contexto (botão direito na conversa)
- Botão direito em qualquer conversa da sidebar → menu contextual
- Opções: "Adicionar a Tarefa" (busca tarefa existente) ou "Criar nova tarefa"
- Modal com lista de tarefas abertas + formulário de nova tarefa

### Painel de Tarefas na Conversa
- Botão "Tarefas" no header da conversa (roxo quando em tarefa)
- Painel lateral deslizante com:
  - Lista de tarefas vinculadas (status, prioridade, prazo, responsável)
  - Botão de desvincular por tarefa
  - Busca de tarefa existente + criação de nova
  - Campo de responsável, prazo e prioridade

### Protocolo especial — `is_task_conv`
Quando uma conversa é vinculada a uma tarefa:
- Campo `is_task_conv = True` é definido na `Conversation`
- Faixa roxa aparece no chat: *"Novas mensagens abrirão um novo chamado"*
- Protocolo exibido como **T-XXXX** (ex: T-1042)
- No webhook (`services.py`), `get_or_create_conversation()` detecta `is_task_conv=True` e cria uma **nova conversa** em vez de adicionar à existente
- Ao desvincular todas as tarefas, `is_task_conv` volta para `False`

### Conversas em Tarefa — item no menu principal *(movido em 05/08/2026)*
- Antes era a 4ª aba do Inbox (Assumidos/Abertos/Em Andamento/**Tarefas**); com 4 abas o
  painel ficava mais largo que o espaço disponível e sempre mostrava uma barra de
  rolagem horizontal. Removida a aba e criado o item **"Conversas em Tarefa"** no menu
  principal (ícone roxo, abaixo de "Caixa de Entrada"), apontando para
  `/atendimento/inbox/?tab=task` — o conteúdo (lista de conversas com
  `is_task_conv=True` e status ativo, com a faixa informativa no topo) continua o
  mesmo, só a forma de chegar até ele mudou.
- Badge de contagem (`task_conv_count`) calculado em `_base_ctx()` (`views.py`).

---

## 🔔 Alertas e Lembretes Automáticos (Celery)

### `notificar_chamados_abertos` — a cada 10 min
Para cada conversa sem atendente com mais de 10 min sem interação, envia mensagem (com @everyone) no grupo configurado em `notif_abertos_group_id`.

### `enviar_alerta_diario` — a cada 5 min *(corrigido)*
**Bug corrigido:** O schedule anterior `crontab(minute='0,5')` só disparava nos minutos `:00` e `:05`. Qualquer horário configurado fora disso nunca funcionava.

**Solução:** Schedule mudado para `timedelta(minutes=5)`. A task verifica internamente se o horário atual está em uma janela de 5 min em relação ao configurado. **Guard anti-duplo-envio:** salva data em `SystemSetting['daily_alert_sent_date']` — dispara apenas uma vez por dia.

**Conteúdo do alerta:**
- Chamados em aberto sem atendente (lista com nomes dos grupos)
- Chamados assumidos agrupados por atendente
- Conversas em tarefa (📌)
- Total de resolvidos no dia

### `enviar_lembretes_pessoais` — a cada 5 min *(novo)*
Envia lembrete WhatsApp **individual** para cada atendente cadastrado no `AttendantContact`. Disparado em **dois turnos por dia**: manhã e meio-dia (horários configuráveis).

**Guards:** `reminder_morning_sent_date` e `reminder_noon_sent_date` — cada turno só dispara uma vez por dia.

**Conteúdo do lembrete:**
- Chamados assumidos em aberto (com flag 📌 se for conversa em tarefa)
- Tarefas pendentes com prazo (⚠️ ATRASADA se vencida)
- Se não há nada pendente, o atendente **não recebe mensagem** (sem spam)

### Configuração dos alertas (`/atendimento/settings/configuracoes/` → aba "Alerta Diário")
| Configuração | Chave SystemSetting | Padrão |
|---|---|---|
| Ativo | `daily_alert_enabled` | false |
| Horário do alerta diário | `daily_alert_time` | 08:00 |
| Grupo destinatário | `daily_alert_group` | — |
| Horário lembrete manhã | `reminder_morning_time` | 08:00 |
| Horário lembrete meio-dia | `reminder_noon_time` | 12:00 |

---

## 📱 Contatos dos Atendentes

Aba **"Contatos Atendentes"** nas configurações (exibe apenas usuários `is_staff=True`).

Para cada atendente:
- **Número WhatsApp** (formato DDI+número, ex: `5511999999999`)
- **Conexão WhatsApp** usada para envio
- **Toggle** de lembretes habilitados/desabilitados
- **Botão ✈️ Testar**: envia mensagem de teste imediata
  - Se o atendente tem tarefas/chamados ativos → envia prévia real do lembrete
  - Se não tem pendências → envia mensagem padrão de confirmação do sistema

---

## 📊 Relatórios

### Tela HTML (`/atendimento/relatorios/`)
Tabela "Resolvidos Recentes" com colunas:
**Protocolo | Grupo | Assunto | Categoria | Agente | Resolvido em**

- `Assunto` exibe o `subject` coletado pelo auto atendimento (ou `title`)
- `Categoria` usa cor da categoria cadastrada (fundo + borda coloridos)

### PDF exportável
Gerado via ReportLab. Colunas:
**# | Grupo | Assunto | Categoria | Atendente | Abertura | Fechamento | Duração**

- `Assunto`: `subject` (fallback para `title`) com quebra de linha automática
- `Categoria`: em negrito na cor da categoria cadastrada
- Larguras ajustadas para caber em A4 (17,27cm útil)

---

## 🔌 APIs e Endpoints

### Tarefas
| URL | Método | Descrição |
|-----|--------|-----------|
| `/atendimento/tarefas/` | GET | Página do board de tarefas |
| `/atendimento/api/tasks/` | GET, POST | Listar / criar tarefa |
| `/atendimento/api/tasks/<id>/` | GET, PUT, DELETE | Detalhe / editar / excluir |
| `/atendimento/api/tasks/<id>/conversations/<id>/` | POST, DELETE | Vincular / desvincular conversa |
| `/atendimento/api/conversations/<id>/add-to-task/` | POST | Adicionar conversa a tarefa (menu de contexto) |

### Contatos de Atendentes
| URL | Método | Descrição |
|-----|--------|-----------|
| `/atendimento/api/attendant-contacts/` | GET, POST | Listar / salvar contato |
| `/atendimento/api/attendant-contacts/<user_id>/` | DELETE | Remover contato |
| `/atendimento/api/attendant-contacts/<user_id>/test/` | POST | Enviar mensagem de teste |

### Demais APIs existentes
| URL | Descrição |
|-----|-----------|
| `/atendimento/api/connection/*/` | Criar, testar, sincronizar, configurar webhook |
| `/atendimento/api/conversation/<id>/messages/` | Mensagens da conversa |
| `/atendimento/api/conversation/<id>/send-message/` | Enviar mensagem |
| `/atendimento/api/conversation/<id>/update/` | Atualizar status/prioridade |
| `/atendimento/api/tags/`, `/api/categories/` | CRUD tags e categorias |
| `/atendimento/api/settings/` | Salvar configurações do sistema |
| `/atendimento/api/test-alerta/`, `/api/test-notif/` | Testar alertas |
| `/atendimento/webhook/evolution/` | Receber eventos da Evolution API v2 |

---

## ⚙️ Schedule Celery (`crm/celery.py`)

```python
'notificar-chamados-abertos':     timedelta(minutes=10)
'alerta-diario-atendimento':      timedelta(minutes=5)   # verifica horário internamente
'lembretes-pessoais-atendentes':  timedelta(minutes=5)   # verifica horário internamente
```

---

## 🔄 Fluxo: Nova mensagem em conversa de tarefa

```
Mensagem chega no grupo WhatsApp
        │
        ▼
ConversationService.process_webhook()
        │
        ▼
get_or_create_conversation(group)
        │
        ├─ Conversa ativa existe?
        │   ├─ NÃO → cria nova conversa
        │   └─ SIM → is_task_conv == True?
        │              ├─ SIM → cria NOVA conversa (chamado separado)
        │              └─ NÃO → usa conversa existente
        │
        ▼
Mensagem adicionada à conversa correta
```

---

## 🔄 Fluxo: Alerta diário

```
Celery beat dispara enviar_alerta_diario() a cada 5 min
        │
        ├─ daily_alert_enabled == 'true'?    NÃO → skip
        ├─ Horário atual ≈ daily_alert_time? NÃO → skip
        ├─ Já enviado hoje?                  SIM → skip
        │
        ▼
Busca chamados: abertos + assumidos + em tarefa + resolvidos hoje
        │
        ▼
Monta e envia mensagem no grupo configurado
        │
        ▼
Marca 'daily_alert_sent_date' = hoje  (guard anti-duplo)
```

---

## 🔄 Fluxo: Lembrete pessoal (manhã / meio-dia)

```
Celery beat dispara enviar_lembretes_pessoais() a cada 5 min
        │
        ├─ Para turno MANHÃ: horário ≈ reminder_morning_time && não enviou hoje?
        │   └─ SIM → _enviar_lembretes_atendentes(turno='manhã')
        │              → marca 'reminder_morning_sent_date'
        │
        └─ Para turno MEIO-DIA: horário ≈ reminder_noon_time && não enviou hoje?
            └─ SIM → _enviar_lembretes_atendentes(turno='meio-dia')
                       → marca 'reminder_noon_sent_date'

Para cada AttendantContact com reminders_enabled=True:
    ├─ Busca chamados assumed by user
    ├─ Busca tarefas assigned to user (pending / in_progress)
    ├─ Se nada pendente → NÃO envia (sem spam)
    └─ Envia WhatsApp individual com resumo personalizado
```

---

## 🚀 Setup Inicial

```bash
# Virtualenv
source /opt/crm/venv/bin/activate

# Migrações
python manage.py migrate atendimento

# Verificar tasks celery
python manage.py shell -c "from atendimento.tasks import enviar_alerta_diario; print('OK')"

# Reiniciar serviços
systemctl restart gunicorn daphne celery
```

---

## 🐛 Troubleshooting

### Alerta diário não dispara
1. Verificar `daily_alert_enabled = 'true'` em SystemSetting
2. Confirmar que o celery está rodando: `systemctl status celery`
3. Verificar se `daily_alert_sent_date` tem a data de hoje (limpá-la para reenviar):
   ```python
   SystemSetting.objects.filter(key='daily_alert_sent_date').delete()
   ```
4. Testar manualmente: aba "Alerta Diário" → botão "Testar agora"

### Lembrete pessoal não chega
1. Verificar se o contato está cadastrado na aba "Contatos Atendentes"
2. Conexão WhatsApp selecionada está ativa?
3. O atendente tem chamados ou tarefas pendentes? (sem pendências = sem lembrete)
4. Usar botão ✈️ "Testar" para envio imediato e ver retorno de erro

### Conversa em tarefa não cria novo chamado
1. Verificar `Conversation.is_task_conv = True` no banco
2. O novo chamado é criado no próximo webhook — não retroativamente

---

## 📚 Arquivos Principais

| Arquivo | Responsabilidade |
|---------|-----------------|
| `atendimento/models.py` | Todos os modelos + Task + TaskConversation + AttendantContact |
| `atendimento/views.py` | Views HTML + todas as APIs REST |
| `atendimento/services.py` | EvolutionAPIClient, ConversationService, webhook processor |
| `atendimento/tasks.py` | Celery tasks: alertas, lembretes, notificações |
| `atendimento/urls.py` | Roteamento de URLs |
| `crm/celery.py` | Schedules das tasks periódicas |
| `atendimento/migrations/` | 0001→0006 |
| `atendimento/templates/atendimento/` | Todos os templates |

---

## 📅 Histórico de Implementações

| Data | Implementação |
|------|--------------|
| 01/06/2026 | MVP inicial — inbox, chat, webhook, conexões WhatsApp |
| 02/06/2026 | Notificações em tempo real (WebSocket), badges, sons |
| 03/06/2026 | **Sistema de Tarefas** (substitui Kanban), menu de contexto, painel de tarefas na conversa |
| 03/06/2026 | **Protocolo T-XXXX** para conversas em tarefa, nova conversa automática no webhook |
| 03/06/2026 | **Aba "Em Tarefa"** no inbox |
| 03/06/2026 | **Contatos de Atendentes** com teste de envio individual |
| 03/06/2026 | **Lembretes pessoais** manhã e meio-dia via WhatsApp |
| 03/06/2026 | **Correção alerta diário** — bug crontab, guard anti-duplo-envio |
| 03/06/2026 | **Relatórios** — campos Assunto e Categoria na tabela HTML e PDF |
| 16/06/2026 | **Sala Virtual** — corrige queda de áudio após alguns minutos (ICE/negociação WebRTC) |
| 04/08/2026 | **Correção de flicker** ao resolver/encerrar chamado — card sumia e reaparecia intercalando as abas Aberto/Andamento/Aguardando |
| 04/08/2026 | **Correção de fluidez** — WebSocket duplicado do Inbox, polling redundante do chat, remoção de card sem transição |
| 04/08/2026 | **Correção alerta NOC** — falha real de envio ao WhatsApp era marcada como sucesso silenciosamente (bug de tupla) |
| 05/08/2026 | **Indicador de mensagem não lida** em conversas assumidas — badge + destaque em tempo real (reaproveita `Message.is_read`) |
| 05/08/2026 | **Correção transferência/atribuição** — troca de atendente não avisava ninguém em tempo real (WS `conversation_reassigned`) |
| 05/08/2026 | **Visual estilo WhatsApp Dark** no chat e na lista de conversas |
| 05/08/2026 | **Correção UI**: barra de rolagem visível nas abas do Inbox; aba "Tarefas" movida pro menu principal |

---

## Sala Virtual de Atendentes — WebRTC

**Arquivo:** `atendimento/templates/atendimento/sala_virtual.html` (frontend),
`atendimento/consumers.py` (`VirtualRoomConsumer`, sinalização via WebSocket)

Chamada de voz/tela em malha completa (full mesh) entre atendentes conectados na sala —
cada participante mantém uma `RTCPeerConnection` direta com todos os demais.

### Bug 1 — candidatos ICE descartados (várias pessoas, um par não se ouve)

**Sintoma:** com 3 ou 4 pessoas na sala, a maioria se ouvia normalmente, mas um par
específico não se ouvia — de forma inconsistente, variando a cada entrada na sala.

**Causa:** candidatos ICE (`ice_candidate`) que chegavam **antes** da
`RTCPeerConnection` estar totalmente negociada (offer/answer ainda em trânsito) eram
descartados silenciosamente (`catch(e){}`), sem nunca serem reaplicados. Com mais
participantes, a janela de corrida da sinalização aumenta, tornando o problema mais
frequente.

**Correção (2026-06-16):** buffer de candidatos pendentes por peer
(`pendingCandidates`). Candidatos que chegam antes da conexão estar pronta são
guardados e aplicados (`flushPendingCandidates`) assim que `setRemoteDescription`
é concluído — tanto no fluxo de quem oferta quanto no de quem responde.

### Bug 2 — áudio cai sozinho após alguns minutos e não volta

**Sintoma:** a sala funcionava normalmente por alguns minutos e depois o áudio parava
de funcionar entre todos os participantes, sem nenhuma ação do usuário.

**Causa:** o único mecanismo de recuperação existente era
`pc.onconnectionstatechange` chamando `pc.restartIce()` quando a conexão caía para
`failed`. Isso **não tinha efeito real**: `restartIce()` apenas agenda a renegociação
e dispara o evento `negotiationneeded` — e não havia **nenhum listener** para esse
evento no código. Sem alguém criando e enviando uma nova oferta, a renegociação nunca
acontecia. Quando a conexão ICE expira (timeout comum de NAT/firewall após alguns
minutos — a sala só usa servidores STUN, sem TURN de apoio), o áudio cai e nunca mais
volta.

**Correção (2026-06-16):** implementado o padrão **Perfect Negotiation**:
- `pc.onnegotiationneeded` agora cria e envia a oferta de fato quando necessário —
  fazendo `restartIce()` funcionar de verdade.
- Cada par decide deterministicamente quem é "polite" (`myId < peerId`) para resolver
  colisões de oferta simultânea sem precisar de um líder fixo.
- Quem entra como ouvinte (sem microfone) cria um transceiver de áudio `recvonly`
  explícito, garantindo que a negociação aconteça mesmo sem track de envio.
- Bônus: corrige também um bug latente em que ligar o microfone ou compartilhar tela
  *depois* de entrar na sala não notificava quem havia recebido a oferta original (a
  mesma falta do `onnegotiationneeded`).

**Limitação conhecida:** a sala usa apenas STUN público (Google), sem servidor TURN.
Em redes com NAT simétrico/firewalls muito restritivos, a conexão direta pode não ser
estabelecida mesmo com a renegociação corrigida — um TURN de apoio resolveria esse
caso residual, mas está fora do escopo desta correção.

---

## Correção — Flicker ao resolver/encerrar chamado (2026-08-04)

**Sintoma:** ao resolver ou encerrar um chamado, o card não sumia instantaneamente
da tela — aparecia e sumia, intercalando entre as abas Aberto / Em Andamento /
Aguardando, de forma inconsistente.

Eram três causas encadeadas, cada uma abrindo ou alimentando a janela de corrida das
outras duas:

### Bug 1 — broadcast do WebSocket atrasado por I/O bloqueante

**Arquivo:** `atendimento/views.py` (`api_update_conversation`)

**Causa:** o card só sumia quando o evento `conversation_status` era enviado via
WebSocket (`_ws_send_inbox`), mas esse envio só acontecia **depois** de uma chamada
HTTP síncrona e bloqueante à Evolution API para mandar a mensagem de conclusão
("✅ Chamado concluído! 📋 Protocolo #..."). Esse atraso (podendo chegar a vários
segundos se a Evolution API estivesse lenta) era a janela onde o usuário trocava de
aba e disparava a corrida dos Bugs 2 e 3.

**Correção:** o broadcast `_ws_send_inbox` agora dispara logo após salvar o novo
status, antes de qualquer chamada externa. O envio da mensagem de conclusão ao
WhatsApp foi movido para uma thread em background (mesmo padrão já usado em
`ConversationService.send_message`), sem bloquear a resposta HTTP. De brinde, corrigiu
um bug latente ali: o retorno de `send_text()` (tupla `(sucesso, msg_id)`) era tratado
como `bool` simples — uma tupla de 2 elementos é sempre "verdadeira" em Python, então
uma falha real de envio nunca era percebida.

### Bug 2 — WebSocket do Inbox vazando a cada navegação SPA

**Arquivo:** `atendimento/templates/atendimento/inbox.html`

**Causa:** a navegação é uma SPA que troca o conteúdo do painel de conversas via AJAX
e reexecuta os `<script>` do HTML recebido (`execScripts`). O script do Inbox abria um
`WebSocket` novo a cada execução **sem fechar o anterior** — e como o `onclose` de
cada instância agendava reconexão própria, instâncias órfãs continuavam vivas
indefinidamente, reconectando sozinhas. Com uso normal ao longo do dia, um atendente
acumulava várias conexões redundantes ao mesmo grupo `atendimento_inbox`, cada uma
manipulando o DOM de forma independente e fora de sincronia — produzindo exatamente o
efeito de um card sumir e reaparecer.

**Correção:** a conexão anterior (guardada em `window.__inboxListWS`) é fechada
explicitamente antes de abrir uma nova, e o `onclose` só reagenda reconexão se a
instância ainda for a atual (evita duas conexões concorrentes reconectando ao mesmo
tempo).

### Bug 3 — cache de prefetch obsoleto "ressuscitava" o card já removido

**Arquivo:** `atendimento/templates/atendimento/base.html`

**Causa:** ao passar o mouse sobre um link (aba, card), o HTML da página é
pré-carregado e guardado em `_prefetchCache`. Se o atendente passava o mouse sobre
outra aba pouco antes ou logo depois de resolver o chamado, esse HTML ficava em cache
com o card ainda listado — e ao navegar em seguida, `loadPage()` usava o cache e
sobrescrevia a remoção que o WebSocket já tinha feito.

**Correção:** exposta `window.__spaClearPrefetchCache()`, chamada pelos handlers de
`conversation_status` (tanto no Inbox quanto no `ws2` de `base.html`) sempre que um
chamado é resolvido/encerrado, descartando qualquer HTML pré-carregado obsoleto.

### Bugs menores corrigidos de passagem

- **Aba ativa fixa no sidebar** (`base.html`) — o botão "Abertos" tinha
  `class="conv-tab active"` fixo no HTML, então a tela de conversa sempre mostrava
  "Abertos" como aba ativa mesmo quando o chamado exibido pertencia a "Assumidos" ou
  "Em Andamento". O backend já calculava `sidebar_active_tab` corretamente
  (`views.py:164-169`) — só faltava o template usar essa variável.
- **Remoção abrupta de card** — os handlers de WebSocket (`inbox.html`, `ws2` em
  `base.html`) agora usam `window.fadeOutRemove()` (fade + slide, 220ms) em vez de
  `el.remove()` direto, igual ao padrão já usado no fluxo de resolução do chat.
- **Polling redundante no chat** (`_chat_content.html`) — dois `setInterval`
  sobrepostos (4s/8s) mais um terceiro só para vigiar qual deveria estar ativo foram
  substituídos por um único timer recursivo que relê o estado do WebSocket a cada
  ciclo — menos timers concorrentes rodando por conversa aberta.

**Serviços a reiniciar após alterações nesses arquivos:** `views.py`/`services.py`/
`tasks.py` → **Gunicorn** (e **Celery**, se `tasks.py`/`services.py` mudarem); os
templates recarregam sozinhos (`DEBUG=True`, sem cached loader).

---

## Correção — Alerta NOC perdia aviso silenciosamente em falha de envio (2026-08-04)

**Arquivos:** `atendimento/tasks.py` (`notificar_chamados_abertos`),
`atendimento/services.py` (`_notify_new_open_conversation`)

**Contexto:** o alerta de "chamado sem atendimento" para o grupo NOC já era enviado
corretamente **uma única vez por chamado** (guard `Conversation.notif_aberto_enviada`)
e de forma **consolidada** (uma mensagem só, listando todos os chamados pendentes,
marcando todos em lote) — esse mecanismo já estava implementado desde 16/07/2026 e
funcionando em produção.

**Causa do bug encontrado:** `EvolutionAPIClient.send_text()` retorna uma tupla
`(sucesso: bool, msg_id: str)`, mas os dois pontos acima faziam `ok = ....send_text(...)`
e depois `if ok:` / `if not ok:` — uma tupla de 2 elementos é sempre truthy em Python,
então esses checks nunca refletiam o sucesso real do envio. Na prática: se a Evolution
API falhasse de verdade, o chamado era marcado como `notif_aberto_enviada=True` (ou o
`return {'notified': 0, 'error': 'send_failed'}` nunca disparava) — o alerta se perdia
sem ninguém ser avisado e sem tentar de novo no próximo ciclo. O mesmo padrão de bug já
tinha sido corrigido antes em `_alertar_atendente_pessoal` (mesmo arquivo), que serviu
de referência para esta correção.

**Correção:** desempacotar a tupla explicitamente (`ok, _msg_id = ....send_text(...)`)
nos dois pontos, para que o guard de "enviado" só marque `True` quando o envio de fato
teve sucesso.

---

## Indicador de mensagem não lida em conversas assumidas (2026-08-05)

**Pedido:** quando um atendente está com uma conversa assumida e o cliente manda uma
mensagem nova, nada indicava visualmente que havia mensagem não lida.

**Fonte de verdade:** o campo `Message.is_read` já existia no modelo (desde a migration
inicial) mas nunca era exibido em lugar nenhum da UI — só era zerado (marcado `True`
para *todas* as mensagens, não só as do cliente) ao abrir a tela de conversa completa.
O que existia antes disso era puramente client-side e efêmero: uma classe CSS `unread`
adicionada via WebSocket que sumia ao recarregar a página, e um contador heurístico
("mensagens do cliente nas últimas 48h", não "não lidas de verdade") no widget de
bolhas flutuantes.

**Implementação:**
- **Backend** (`atendimento/views.py`): as querysets do Inbox (`inbox`), do sidebar
  (`conversation_detail`, `_base_ctx`) e do widget de bolhas (`api_my_conversations`)
  passaram a anotar `unread_count = Count('messages', filter=Q(sender_type='customer',
  is_read=False))`. `api_my_conversations` trocou a heurística de janela de 48h por
  essa contagem real.
- Abrir a conversa completa (`conversation_detail`) ou o mini-chat flutuante
  (`api_conversation_messages`) marca `is_read=True` **só** para mensagens do cliente
  (antes marcava indiscriminadamente todas, incluindo as do próprio agente).
- Um novo evento WebSocket `messages_read` (`type: 'messages_read', conversation_id`)
  é disparado ao marcar como lida, pra sumir o indicador em outras abas/dispositivos
  do mesmo atendente sem precisar de F5.
- **Frontend**: `_inbox_conv_item.html`/`_conv_item.html` ganham badge com a contagem
  e destaque (borda azul + negrito) quando `unread_count > 0` — antes esse destaque
  era baseado em `status == 'new'`, que não tinha nada a ver com leitura de fato.
  `base.html` ganhou `window.markConvUnread`/`window.markConvRead`, chamados pelos
  handlers de WebSocket (`new_message` de cliente → marca; `messages_read` → some).

**Limitação conhecida:** `is_read` é um campo único por mensagem (não por
atendente/usuário) — se dois atendentes olham a mesma conversa, o primeiro que abrir
marca como lida pra todo mundo. Suficiente pro caso de uso real (uma conversa
normalmente tem um atendente responsável por vez), mas não é "lida por mim
especificamente" no sentido de um sistema multi-usuário genérico.

---

## Correção — Transferência/atribuição não avisava outros atendentes em tempo real (2026-08-05)

**Sintoma:** transferir um chamado para outro atendente não fazia o chamado aparecer
na aba "Assumidos" dele — só depois de recarregar a página manualmente. O dado no
banco estava sempre correto (`assigned_to` mudava certinho); o problema era
exclusivamente de notificação em tempo real.

**Causa:** quatro pontos do código trocavam `Conversation.assigned_to` sem avisar
ninguém via WebSocket:
1. Transferência manual (botão "Transferir") — `api_update_conversation`
2. "Assumir" um chamado em aberto — `conversation_detail`
3. Auto-atribuição ao responder um chamado sem atendente — `api_send_message`
4. Reatribuição automática por estouro de SLA (task Celery) — `tasks.py:escalar_chamados_sla`
   — este era o mais grave, pois não há navegador nenhum aberto pra se auto-atualizar.

**Correção:** `services.notify_reassignment(conversation, old_assigned_to_id)` —
dispara um evento WS `conversation_reassigned` (`conversation_id`,
`old_assigned_to_id`, `assigned_to_id`, `group_name`) sempre que o atendente muda,
chamado nos 4 pontos acima. Em `base.html`, quem ganha ou perde o chamado tem o
sidebar recarregado automaticamente — reaproveitando a técnica de refetch que já
existia localmente pra quem transfere/assume (`_onNewlyAssigned`/`_afterTransfer` em
`_chat_content.html`, agora extraída pra uma função global `window.__refreshConvPanel`
usada nos três lugares). Quem **recebe** o chamado ganha som + toast "🔄 Chamado
transferido para você".

**De brinde:** corrigido o fallback de nome vazio (`get_full_name()` retorna string
vazia pra usuários sem nome cadastrado — trocado por `get_full_name() or username`) e
a perda da query string (`?tab=mine`) no refresh do sidebar, que fazia o atendente
voltar pra aba errada depois de assumir/ser transferido.

---

## Visual do Chat e da Lista de Conversas — estilo WhatsApp Dark (2026-08-05)

Redesign visual da tela de chat e da lista de conversas (Inbox/sidebar) pra ficar
parecido com o WhatsApp Web no modo escuro. Escopo: só chat + lista — nav-sidebar,
cores de status do chamado (aberto/pendente/resolvido) e botões utilitários
(Transferir/Mesclar/Hosts/Tarefas) **não foram alterados**, mantêm a semântica de cor
já existente do CRM.

**Paleta nova** (`atendimento/templates/atendimento/base.html`, variáveis `--wa-*` no
`:root`): fundo do chat `#0b141a`, painéis/cabeçalho `#202c33`/`#111b21`, bolha do
cliente `#202c33`, bolha do atendente (verde-escuro) `#005c4b`, acento
`#00a884`/`#008069`/`#06cf9c`.

**Detalhes implementados:**
- Bolhas de mensagem com cantos arredondados e "rabicho" apontando pro remetente
  (via `::before`/`::after` com `mask: radial-gradient(...)`, sem imagem externa).
- Marca **✓✓** nas mensagens do atendente — só um toque visual (cinza, não azul):
  **não há confirmação real de entrega/leitura do WhatsApp** nesse sistema, então usar
  a cor azul do WhatsApp real (que significa "lida pelo cliente") seria enganoso.
- Campo de digitar em formato pílula + botão de enviar circular verde.
- Lista de conversas: painel escuro, aba ativa/badge de não lida/busca em foco na
  cor verde de acento.

---

## Correção — barra de rolagem nas abas do Inbox + menu "Tarefas" (2026-08-05)

**Sintoma:** as 4 abas do Inbox (Assumidos/Abertos/Em Andamento/Tarefas) ficavam mais
largas que o painel de conversas, forçando uma barra de rolagem horizontal sempre
visível.

**Correção:**
- A aba **"Tarefas"** foi removida da barra de abas do painel de conversas
  (`atendimento/templates/atendimento/inbox.html`) — o conteúdo (lista de conversas
  com `is_task_conv=True`) continua existindo, agora acessível por um item próprio
  **"Conversas em Tarefa"** no menu principal (`base.html`, logo abaixo de "Caixa de
  Entrada"), apontando pra `/atendimento/inbox/?tab=task`. Contador
  `task_conv_count` calculado em `_base_ctx()`.
- A barra de rolagem das abas restantes ficou invisível
  (`scrollbar-width: none` + `::-webkit-scrollbar{display:none}` em `.conv-tabs`) —
  o scroll continua funcionando por toque/roda do mouse em telas muito estreitas,
  só não aparece mais a barra visualmente.

---

**Mantido por:** CampeloSuporte  
**Repositório:** /opt/crm
