# 📞 Sistema de Atendimento — Documentação Técnica

## 📋 Visão Geral

Plataforma de atendimento ao cliente integrada ao CRM, similar ao Chatwoot. Centraliza o gerenciamento de tickets de suporte via WhatsApp (Evolution API v2), com tarefas, alertas automáticos, lembretes pessoais e relatórios completos.

**Última atualização:** 12/08/2026  
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
| **Mensagens Agendadas** | Programa envio de mensagem/mídia para data e hora futuras, com painel para cancelar |
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

### Mensagens Agendadas
| URL | Método | Descrição |
|-----|--------|-----------|
| `/atendimento/api/conversation/<id>/schedule-message/` | GET | Listar agendadas pendentes da conversa |
| `/atendimento/api/conversation/<id>/schedule-message/` | POST | Agendar mensagem (texto ou mídia) |
| `/atendimento/api/scheduled-message/<id>/cancel/` | POST | Cancelar agendamento pendente |

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
'notificar-chamados-abertos':              timedelta(minutes=10)
'alerta-diario-atendimento':               timedelta(minutes=5)   # verifica horário internamente
'lembretes-pessoais-atendentes':           timedelta(minutes=5)   # verifica horário internamente
'atendimento-enviar-mensagens-agendadas':  timedelta(minutes=1)   # agendador de mensagens
```

> O beat roda **embutido no worker** (`celery -A crm worker --beat --concurrency=1`),
> não como serviço separado — só existe `celery.service`. Mudança em `beat_schedule`
> só vale após `systemctl restart celery`.

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
| 31/08/2026 | **Correção "Iniciar conversa"** — chamado criado pela plataforma só aparecia em "Assumidos" após F5 (WS `conversation_created`); chamado anterior encerrado junto virava item fantasma na lista |
| 04/09/2026 | **Menção "@" mostra quem é quem** — lista vinha só com telefone; nome vem de 3 fontes (agenda da instância, `pushName` aprendido dos grupos, equipe), com foto, selo de admin e número formatado |
| 04/09/2026 | **Sala Virtual** — botão para não escutar ninguém (silenciar a sala sem desligar o microfone) |
| 04/09/2026 | **Sala Virtual** — arrastar a tela compartilhada travava: zoom/pan de verdade no vídeo e captura em 30 fps com resolução preservada ao mover janelas |
| 04/09/2026 | **Editar mensagem enviada** — corrige o balão no CRM e reescreve a mensagem no WhatsApp do grupo (janela de 15 min, só do autor, só texto); edição feita pelo cliente no celular passou a atualizar o balão em vez de virar "[sem conteúdo]" |

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


### Silenciar a sala — não escutar ninguém (04/09/2026)

Botão de fone (`#ctrl-deafen`) na barra de controles, ao lado do microfone. Corta **todo** o
áudio que chega — voz de todos os atendentes e o som da tela compartilhada — sem mexer no
microfone: dá para continuar falando na sala enquanto se atende alguém no telefone.

Implementado mutando o **elemento de saída** (`audioEl.muted`, `<video>.muted`), não a track nem
a `RTCPeerConnection`. O áudio continua chegando e volta na hora em que a pessoa desliga o botão,
sem renegociar nada com os outros participantes — desligar as tracks obrigaria a uma rodada de
offer/answer com cada peer só para voltar a ouvir.

- Quem entra na sala **depois** do botão ligado também entra mudo (`aplicarSurdez()` roda no
  `ontrack` e sempre que a área de tela aparece).
- A preferência fica no `localStorage` (`sala_deafen`): é do ouvinte, não um estado da sala, então
  não é transmitida aos outros participantes.
- A tela que **eu** compartilho continua sempre muda no meu `<video>`, senão eu ouviria meu
  próprio som de volta.

### Arrastar a tela compartilhada travava (04/09/2026)

Eram dois problemas somados, um de cada lado da conexão.

**Do lado de quem assiste:** o `<video>` tinha `cursor:zoom-in` e **nenhum comportamento por
trás**. Quem tentava arrastar para ver um canto da tela do outro disparava o *drag nativo* do
navegador — a imagem fantasma grudada no cursor, seleção de página junto — e parecia que o
compartilhamento tinha travado. Agora:

- `dragstart` barrado explicitamente, mais `user-select`/`-webkit-user-drag` desligados e
  `touch-action:none` (no toque, o pan chegava como scroll da página).
- **Roda do mouse dá zoom** (1× a 6×) ancorado no ponto sob o cursor — o pixel embaixo do cursor
  continua embaixo do cursor, que é o que faz o gesto não "pular".
- **Arraste move de verdade** (pan), via pointer events com `setPointerCapture`: o movimento
  continua mesmo quando o cursor sai da área do vídeo.
- O deslocamento é **clampado pela caixa real da imagem**, não pelo elemento: com
  `object-fit:contain` sobra tarja preta nas laterais, e clampar pelo elemento deixaria arrastar
  até sobrar só tarja na tela — parecendo, de novo, que o compartilhamento caiu.
- Barra com **zoom +/−, enquadrar (100%) e tela cheia**, mais um indicador de percentual. O zoom
  zera sozinho quando o compartilhamento começa, troca de dono ou termina, e é reaplicado ao
  entrar/sair da tela cheia e ao redimensionar a janela (o tamanho do vídeo muda e com ele os
  limites do pan).

**Do lado de quem compartilha:** a captura estava em `frameRate: 15` e sem nenhum ajuste de
encoder. Ao arrastar uma janela, metade dos quadros do movimento não era capturada e o navegador,
para caber na banda, derrubava a resolução por conta própria — o texto virava borrão. Agora:

- captura em **30 fps** (`ideal`/`max`), limitada a 1920×1080;
- `contentHint = 'detail'` na track de vídeo: prioriza nitidez sobre fluidez (o padrão `'motion'`
  é o que borrava tudo);
- `degradationPreference = 'maintain-resolution'` e teto de banda de 3 Mbps em cada `RTCRtpSender`
  (`ajustarEnvioTela()`), aplicado também a quem **entra na sala no meio** de um compartilhamento.

`setParameters()` fica dentro de `try`: navegador sem suporte a algum desses campos continua
compartilhando, só sem o ajuste fino.

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


## Agendador de Mensagens (2026-08-12)

Permite ao atendente escrever a mensagem (ou anexar mídia) e programar o envio
para uma data/hora futura, em vez de mandar na hora.

### Como usar

Botão **Agendar** ao lado das abas WhatsApp / Comentário Interno: digita a
mensagem, clica, escolhe data e hora. O botão **Agendadas** no cabeçalho da
conversa abre um painel lateral com as pendentes e permite cancelar.

Agendamento vale só para mensagem ao cliente — o botão fica desabilitado no
modo Comentário Interno.

### Como funciona

`ScheduledMessage` (`atendimento/models.py`) guarda o conteúdo e o horário.
A task `enviar_mensagens_agendadas` roda **a cada 1 minuto** e envia as
pendentes vencidas, reaproveitando o mesmo caminho do envio imediato
(`ConversationService.send_message` / `.send_media`).

Mídia: o arquivo é gravado em disco **no momento do agendamento** e só a URL
fica no banco; na hora do envio é lido de volta por `_read_attachment_as_base64`.
Se salvar falhar, o agendamento é recusado na hora (400) em vez de criar um
registro fadado a falhar horas depois.

### Decisões de comportamento

| Situação na hora do envio | O que acontece |
|---|---|
| Conversa resolvida/encerrada | Reabre (`open`) e envia |
| Conversa mesclada em outra | Segue `merged_into` e envia no destino |
| Falha no envio | Tenta de novo no próximo ciclo; após `MAX_ATTEMPTS` (5) marca `failed` |
| Cancelada antes da hora | Não envia (cancelamento vence a corrida com o worker) |

**Legenda de mídia:** o agendamento guarda a legenda **crua** (pode ser vazia).
O rótulo padrão (`"Imagem"`, `"Vídeo"`…) só é calculado na hora do envio — se
fosse gravado no agendamento, a mídia sairia com esse texto colado como legenda.

**Ciclo de mesclagem:** ao seguir `merged_into`, a task guarda as conversas já
visitadas e para ao reencontrar uma (registrando no log). Como worker e beat
dividem o mesmo processo com `--concurrency=1`, um ciclo A→B→A travaria *todo* o
processamento em background do CRM, não só o agendador.

**Corrida com o cancelamento:** antes de enviar, cada item é relido do banco para
confirmar que ainda está `pending` — o atendente pode ter cancelado entre a
consulta e o envio.

### Arquivos

| Arquivo | Papel |
|---|---|
| `atendimento/models.py` | `ScheduledMessage` |
| `atendimento/tasks.py` | `enviar_mensagens_agendadas` |
| `atendimento/services.py` | `ConversationService.send_media`, `_read_attachment_as_base64` |
| `atendimento/views.py` | `api_schedule_message`, `api_cancel_scheduled_message` |
| `atendimento/templates/atendimento/_chat_content.html` | UI + módulo JS `scheduledMsgs` |

Testes: `atendimento/tests.py` (25, incluindo `AgendadorFluxoCompletoTest` ponta a ponta).

---

## Correção — "respondi e a conversa não ficou como assumida" + balões mal formatados (2026-08-12)

Três defeitos independentes que apareciam juntos na tela de conversa.

### Bug 1 — auto-atribuição só valia para mensagem de texto pela view

**Sintoma:** o atendente respondia pela plataforma e a conversa continuava sem
responsável — não entrava na aba "Assumidos" e o cabeçalho seguia oferecendo
"Assumir".

**Causa:** a regra "quem responde, assume" morava dentro da view
`api_send_message`. Os demais caminhos de envio chamam o service direto e
pulavam a atribuição:

| Caminho | Chama | Atribuía antes? |
|---|---|---|
| Chat (texto) | `api_send_message` → `send_message` | sim |
| Chat (mídia) | `api_send_media` → `send_media` | **não** |
| Mensagem agendada (Celery) | `enviar_mensagens_agendadas` → `send_message` | **não** |
| Mensagem de encerramento | view de status → `send_message` | **não** |

Caso real encontrado no banco: a conversa **TOMICH TEC - NOC** recebeu um envio
agendado às 10:00 de 12/08 e seguia com `assigned_to = None`.

**Correção:** a regra virou `services.auto_assign_on_reply(conversation, agent)`,
chamada de dentro de `ConversationService.send_message` e `send_media` — ou seja,
vale para todo caminho, presente e futuro. Ela cria a atividade `assigned` e
dispara `notify_reassignment` (WebSocket). A view não decide mais nada: só compara
`assigned_to_id` antes e depois para devolver `newly_assigned` ao front.

> Ao criar um novo caminho de envio de agente, mande pelo `ConversationService`
> em vez de gravar `Message` na mão — é o que garante atribuição, atividade e WS.

### Bug 2 — balões com linhas em branco e recuo (só depois do F5)

**Sintoma:** mensagem recém-enviada aparecia certa; a mesma mensagem depois de
recarregar a página aparecia com duas linhas em branco e um recuo grande antes do
texto.

**Causa:** `.msg-bubble` usava `white-space: pre-wrap` para preservar as quebras de
linha vindas do WhatsApp. Com isso, **a própria indentação do template** (as
quebras de linha e ~24 espaços entre as tags) era renderizada dentro da bolha. O
caminho JS não sofria disso porque monta o HTML sem indentação — daí a diferença
entre "ao vivo" e "depois do F5".

**Correção:** o `pre-wrap` saiu da bolha e foi para um `.msg-text` que é **emitido
colado nas tags**. O `|linebreaksbr` do template e o `\n → <br>` do `escapeHTML`
foram removidos: quem quebra linha agora é só o `pre-wrap`, nos dois caminhos.

> ⚠️ Não reindentar o interior de `.msg-bubble` "para ficar legível" — volta o bug.
> Comentários ali dentro precisam de `{% comment %}`: o `{# #}` do Django é de uma
> linha só e, em várias linhas, o texto vaza para a tela.

### Bug 3 — a lista de conversas dentro do chat era outra

`conversation_detail.html` sobrescrevia o bloco `conv_panel` com uma cópia própria
da lista, que tinha divergido do partial `_conv_item.html` e **não emitia
`data-conv-id`** — justamente dentro de uma conversa, `markConvRead`/`markConvUnread`
(que buscam por `[data-conv-id]`) não achavam o item, e a lista também perdia badge
de não lidas, tags e SLA. A cópia foi removida; a página herda a lista de
`base.html`.

### O que mudou na tela

- **Responsável visível:** `.conv-assignee` na lista (verde quando é você, "Sem
  responsável" quando não tem dono) e "Assumido por você" no cabeçalho — substitui
  o antigo "online", que na prática só indicava que a conversa tinha dono. Os dois
  partials da lista (`_conv_item.html` e `_inbox_conv_item.html`) foram atualizados.
- **Troca sem recarregar:** "Assumir" e "Transferir" agora existem sempre no DOM e
  só alternam a visibilidade, então `_onNewlyAssigned()` atualiza o cabeçalho na
  hora (antes só escondia "Assumir", e por isso parecia que nada tinha acontecido).
- **Hora e ✓✓ dentro do balão**, canto inferior direito, via `float` — a última
  linha do texto flui em volta e, se não couber, a hora desce sozinha.
- **Agrupamento:** mensagens seguidas do mesmo remetente viram um bloco só, sem
  repetir nome nem rabicho (`{% ifchanged %}` no servidor, `data-sender-key` no JS).
- **Divisores de data** entre os dias — o CSS `.day-divider` já existia e nunca
  tinha sido usado.
- **Links clicáveis** (`|urlize` no servidor, `linkify()` no JS) e balões mais
  largos no celular (85% em vez de 68%).

### Efeito colateral fora do módulo

A migração `clientes/0032_remove_topologiamapeamento_topologia_and_more.py`
removia o campo `topologia` **antes** de derrubar o `unique_together`
`('topologia', 'acesso')` que o referenciava. O Django resolve as colunas do índice
pelo estado do modelo, então a criação de qualquer banco de teste quebrava com
`FieldDoesNotExist: TopologiaMapeamento has no field named 'topologia'` — nenhum
teste do projeto rodava do zero. As operações foram reordenadas.

### Arquivos

| Arquivo | Papel |
|---|---|
| `atendimento/services.py` | `auto_assign_on_reply`, chamada em `send_message`/`send_media` |
| `atendimento/views.py` | `api_send_message`/`api_send_media` devolvem `newly_assigned` |
| `atendimento/templates/atendimento/base.html` | CSS dos balões, `.conv-assignee`, `.chat-header-assignee` |
| `atendimento/templates/atendimento/_chat_content.html` | markup do balão, `_onNewlyAssigned`, `linkify` |
| `atendimento/templates/atendimento/_conv_item.html` | responsável na lista lateral |
| `atendimento/templates/atendimento/_inbox_conv_item.html` | responsável na lista do Inbox |
| `atendimento/templates/atendimento/conversation_detail.html` | deixou de sobrescrever `conv_panel` |
| `clientes/migrations/0032_...py` | ordem das operações |

Testes: `atendimento/tests.py` — 37 no total, sendo `AutoAtribuicaoAoResponderTest`
(5) e `ChatRenderTest` (7) novos.

### Deploy

Alteração em `services.py`/`views.py` exige reiniciar **Gunicorn e Celery**. O
Celery é fácil de esquecer e é justamente quem envia mensagem agendada — sem
reiniciá-lo, o worker segue com o código antigo e o caso do Bug 1 continua
acontecendo. Daphne não precisa: `consumers.py` não chama `send_message`.


---

## Correção — reações do cliente viravam balão "[sem conteúdo]" (2026-08-12)

**Sintoma:** quando o cliente respondia/reagia a uma mensagem enviada pelo
atendente, aparecia um balão vazio com o texto literal `[sem conteúdo]` no meio
da conversa. Havia **327 mensagens** nesse estado no banco.

### Causa

O webhook extraía texto testando `conversation`, `extendedTextMessage.text` e as
legendas de mídia; o que não casasse com nenhum caía num fallback
`"[sem conteúdo]"`. Levantando os tipos reais na Evolution API, os balões vazios
eram três coisas que não são mensagem de texto:

| `messageType` | O que é | Amostra (60) |
|---|---|---|
| `reactionMessage` | reação com emoji em texto puro | 36 |
| `secretEncryptedMessage` | **reação criptografada** | 23 |
| `albumMessage` | cabeçalho de álbum de fotos | 1 |

O caso relatado é o `secretEncryptedMessage`: **100% deles têm
`targetMessageKey.fromMe = true`**, ou seja, são reações a mensagens que *nós*
enviamos. O WhatsApp criptografa a reação quando o alvo é uma mensagem sua — o
emoji é cifrado com o `messageSecret` da mensagem original, que não guardamos.

> **Não dá para descobrir qual emoji foi** nesse formato. Decifrar exigiria
> reter o `messageSecret` de cada mensagem enviada e reimplementar o HKDF +
> AES-GCM do WhatsApp. O que dá para saber — e é o que mostramos — é que houve
> uma reação e a qual mensagem.

### Correção

Reação deixou de virar balão e passou a ser um detalhe da mensagem que a
recebeu, como no WhatsApp. Novo modelo `MessageReaction` (migration
`0013_messagereaction`) e `_extrair_reacao()` em `services.py`, que normaliza os
dois formatos.

- **`reactionMessage`** → salva o emoji.
- **`secretEncryptedMessage`** → salva a reação com `emoji=''`; a tela mostra a
  pílula "reagiu", com o motivo no `title`.
- **Reação a mensagem que não temos** (anterior ao histórico) → ignorada, em vez
  de criar um balão órfão no fim da conversa.

O levantamento dos 327 casos revelou outros tipos que também caíam no fallback,
corrigidos junto:

| Tipo | Antes | Agora |
|---|---|---|
| `albumMessage` | balão vazio | ignorado — as fotos chegam depois, uma por evento |
| `pinInChatMessage` | balão vazio | ignorado — é "fixou mensagem", não conteúdo |
| `associatedChildMessage` | balão vazio | ignorado |
| `contactMessage` / `contactsArrayMessage` | balão vazio | mostra `👤 Nome` do vCard |
| `lottieStickerMessage` | balão vazio | tratado como figurinha (mídia) |

> Esses eventos são testados por **presença da chave**, não pelo valor: chegam com
> objeto vazio (`{}`) em alguns casos, que é *falsy* — com `.get()` escapariam do
> filtro e voltariam a virar balão vazio.

Cuidados de comportamento:

- Reagir de novo **substitui** a reação anterior da mesma pessoa (busca por
  `sender_jid`), em vez de acumular.
- `reactionMessage` com **texto vazio** é o WhatsApp *removendo* a reação — é
  diferente de "não sei qual emoji", e por isso `_extrair_reacao` devolve a flag
  `cifrada` para separar os dois casos.
- O WebSocket manda sempre a **lista completa** de reações da mensagem
  (`type: "reactions"`), então a tela só troca o bloco inteiro — não precisa
  somar, subtrair nem saber se foi inclusão, troca ou remoção.

### Arquivos

| Arquivo | Papel |
|---|---|
| `atendimento/models.py` | `MessageReaction` |
| `atendimento/services.py` | `_extrair_reacao`, `_registrar_reacao`, `_broadcast_reacoes` |
| `atendimento/views.py` | `prefetch_related('reactions')` na tela da conversa |
| `atendimento/templates/atendimento/base.html` | CSS `.msg-reactions` / `.msg-reaction` |
| `atendimento/templates/atendimento/_chat_content.html` | pílulas + `applyReactions()` no WS |

Testes: `ReacaoWebhookTest` (7), com payloads reais capturados da Evolution API.

### Limpeza do histórico (12/08/2026)

Os 327 balões vazios que já estavam no banco foram reprocessados consultando a
Evolution API por `external_id` (backup em
`backups/msgs_sem_conteudo_20260812.json` antes de qualquer alteração):

| Resultado | Qtd |
|---|---|
| virou `MessageReaction` na mensagem alvo | 300 |
| álbum/pin/contato/outros — balão removido | 19 |
| reação a mensagem fora do histórico — balão removido | 8 |

Sobraram **298** reações, não 300: duas delas apontavam para balões que eram
*eles próprios* reações vazias e foram removidos no mesmo lote, então o
`ON DELETE CASCADE` levou as duas junto. É o comportamento correto — uma reação
a um balão que deixou de existir não teria onde aparecer.

Resultado final: **zero** mensagens com `[sem conteúdo]`; 214 reações com emoji
e 84 criptografadas (mostradas como "reagiu").

### Deploy

Exige `migrate` e restart de **Gunicorn e Celery**.


---

**Mantido por:** CampeloSuporte  
**Repositório:** /opt/crm

## Conclusão do chamado deixa de ir pro grupo (2026-08-13)

**Pedido:** ao fechar um chamado, o grupo do cliente não deve receber a mensagem de chamado
concluído — ela só aparece internamente, no histórico da conversa.

**Como era** (`atendimento/views.py`, `api_update_conversation`): ao mudar o status para
`resolved`/`closed`, uma thread em background chamava
`EvolutionAPIClient(...).send_text(group_jid, "✅ Chamado concluído! 📋 Protocolo: #N")` e só
gravava a `Message` **se o envio desse certo** — ou seja, o registro interno dependia da
Evolution API estar de pé (falha de envio = nenhum rastro no histórico).

**Como ficou:** a mensagem é gravada direto como `Message(sender_type='system')` na conversa,
sem nenhuma chamada externa, e é empurrada pro chat aberto com
`ConversationService._broadcast_msg(..., inbox=False)` — sem isso a linha só apareceria ao
recarregar a conversa. Efeitos colaterais bem-vindos: some a thread em background, some a
dependência da API pra registrar o fechamento, e a resposta HTTP não espera I/O nenhum.

`inbox=False` de propósito: o card já saiu das listas pelo `conversation_status` disparado logo
antes; mandar um `new_message` pra caixa de entrada faria a conversa fechada reaparecer.

**A "Mensagem de encerramento" das configurações continua sendo enviada ao cliente** (o campo
`msg_encerramento` em Configurações → texto livre, hoje vazio nesta instalação). Ela é a via
oficial pra avisar o cliente no fechamento; quem não quer aviso nenhum deixa o campo em branco.
Só a linha automática de protocolo virou interna.

**Verificado** fechando uma conversa via `POST /atendimento/api/conversation/<id>/update/`
com `EvolutionAPIClient.send_text` sob `mock` (transação revertida no fim): 0 chamadas de envio,
status `resolved`, e 1 mensagem interna `sender_type='system'` com o texto do protocolo.

---

## 🔁 Lista lateral em tempo real (14/08/2026)

**Sintoma relatado:** "tenho que ficar atualizando a página para aparecer as mensagens em aberto".

**Causa:** o WebSocket do inbox funcionava — tocava o som, mostrava o toast e piscava o item —
mas nenhum dos dois handlers sabia **inserir** um chamado que ainda não estava na tela:

- `inbox.html` só percorria os itens já renderizados (`querySelectorAll`) e atualizava hora e
  ordem. Chamado novo simplesmente não tinha item pra atualizar.
- `base.html` tinha `__refreshConvPanel()`, que refaz a lista com HTML do servidor, mas buscava
  `location.pathname`. Dentro de um chamado essa URL é o `conversation_detail`, que **não**
  renderiza o bloco `conv_panel` (herda a lista que já está na tela) — a resposta vinha com o
  bloco vazio e a função saía sem fazer nada. Como o atendente passa o dia dentro de um chamado,
  na prática o refresh nunca acontecia.

**Como ficou:**

| Situação | O que acontece agora |
|---|---|
| Mensagem de conversa que já está na lista | Atualiza hora e sobe pro topo (client-side, sem fetch) |
| Mensagem de conversa que **não** está na lista | `__scheduleConvPanelRefresh()` → refaz a lista pelo Inbox |
| `conversation_reassigned` (qualquer atendente) | Refaz a lista — antes o chamado que outro assumiu ficava na minha aba "Abertos" |
| `conversation_status` resolved/closed | Remove o item também na sidebar do template base |
| WebSocket reconectou | Um refresh de recuperação (eventos perdidos não chegam retroativamente) |

`__refreshConvPanel()` agora busca sempre `/atendimento/inbox/?tab=<aba ativa>` — o Inbox é a
única view que renderiza `conv_panel`. Ele preserva busca, scroll e o destaque do chamado aberto,
e só roda no Inbox ou dentro de um chamado (em Dashboard/Relatórios a sidebar é outra coisa).
O agendamento tem debounce de 700ms com teto de 3s, pra rajada de mensagens não adiar o refresh
indefinidamente.

**Também corrigido no mesmo caminho:**

- Badge da "Caixa de Entrada" somava **+1 por mensagem** em vez de contar chamados: 5 mensagens
  do mesmo grupo viravam "5 chamados sem atendente". Agora é recalculado a partir da aba "Abertos".
- Contador de não lidas subia **2 por mensagem** com o Inbox aberto — `base.html` e `inbox.html`
  chamavam `markConvUnread` para o mesmo evento. Ficou só em `base.html`, que roda em toda tela.
- Bolha flutuante de chamado recém-atribuído só aparecia no recarregamento de 60s: a checagem
  `[data-conv-id]` varria o documento inteiro e o item da lista lateral também tem esse atributo,
  então o chamado "já existia". Agora a busca é dentro de `#gchatBubbles` e só pro próprio usuário.

**Testes:** `AtualizacaoAutomaticaDaListaTest` cobre o Inbox via AJAX devolvendo a lista no bloco
`conv_panel`, o `conversation_detail` devolvendo o bloco vazio (o motivo de o refresh apontar pro
Inbox) e um chamado novo aparecendo na lista que o refresh busca.

---

## 🆕 Chamado iniciado pela plataforma aparece na hora (31/08/2026)

**Sintoma relatado:** "quando inicio uma nova conversa ela não aparece na aba de Assumidos, só
aparece quando atualizo a página".

**Causa:** `api_start_conversation_by_group` era o único caminho de criação de chamado que não
avisava ninguém. Ele cria a conversa **já assumida** por quem clicou em "Iniciar", e é justamente
por isso que caía num ponto cego:

- não passa pelo webhook, então não há `_broadcast_msg` (o chamado nasce sem mensagem);
- não passa por `notify_reassignment`, que só dispara quando o dono **muda** — aqui o chamado
  nasce com dono, `old_assigned_to_id` e `assigned_to_id` nunca divergem.

Sem evento nenhum, os dois handlers de WebSocket (`base.html` e `inbox.html`) não tinham o que
processar, e o painel só se atualizava no F5.

E a mesma view **encerra automaticamente** os chamados ativos anteriores do grupo (é o que garante
um chamado novo e limpo, sem arrastar o histórico). Esse encerramento também era silencioso: o
chamado resolvido continuava na lista de todos os atendentes como item fantasma, e quem clicasse
nele abria um chamado já resolvido.

**Como ficou:**

| Evento WS | Quando | O que a tela faz |
|---|---|---|
| `conversation_created` | Chamado aberto por "Iniciar conversa" | Refaz a lista (`__scheduleConvPanelRefresh`) — imediato pra quem abriu, 900ms pros demais. **Sem som e sem toast**: quem abriu foi o próprio atendente |
| `conversation_status` (`resolved`) | Chamados anteriores do grupo, encerrados automaticamente | Remove o item de todas as abas, como qualquer outro encerramento |

Detalhes que fazem a diferença na prática:

- `doStartConv` define `window._activeInboxTab = 'mine'` **antes** do POST — o refresh é disparado
  pelo WebSocket, que pode chegar antes da resposta da requisição, e sem isso a lista voltava do
  servidor na aba "Abertos", com o chamado recém-criado escondido na aba ao lado.
- Depois de navegar para o chamado, `doStartConv` agenda um refresh de segurança (1200ms). O
  evento do WS chega **antes** da navegação, e naquele instante a lista da tela pode não ser a do
  Inbox (ex.: iniciou a conversa pelo Dashboard), caso em que `__refreshConvPanel` ignora o
  pedido de propósito. Também cobre o WebSocket fora do ar.
- `window.__markActiveConvItem()` (extraído de `_applyConvPanelHtml`) roda de novo ao fim do
  `loadPage`: a marcação otimista do início da navegação não acha o item de um chamado que
  acabou de ser criado — ele só entra no painel quando o refresh chega.
- Os dois handlers usam o **mesmo atraso** para o mesmo evento. O debounce de
  `__scheduleConvPanelRefresh` só junta os pedidos dos dois sockets num refresh só se ambos
  pedirem o mesmo tempo. Em `inbox.html` o id do usuário vem do template (`_meuUserId`) porque o
  `currentUserId` de `base.html` está preso dentro de uma IIFE.
- Falha no envio do WS é logada e engolida: WebSocket fora do ar não pode virar erro na tela de
  quem clicou em "Iniciar" — o chamado é o que importa.

**Testes:** `IniciarConversaTempoRealTest` (4 testes) — evento do chamado criado, evento do
encerramento automático do anterior, criação sobrevivendo a um WS quebrado e o chamado novo
aparecendo em `inbox?tab=mine` (a URL que o refresh busca).

---

## 🤖 Auto atendimento não escreve mais nos grupos (14/08/2026)

O fluxo ativo disparava, **a cada chamado aberto**, a saudação e a mensagem de conclusão direto no
grupo do cliente (`ConversationService._flow_enviar` → `EvolutionAPIClient.send_text`). Como o
chamado já é aberto na primeira mensagem, sem depender de resposta do bot, essas duas mensagens só
poluíam a conversa do grupo.

O envio automático saiu de `process_webhook`. O que **continua** existindo:

- a tela `/atendimento/auto-atendimento/` e os fluxos cadastrados (com um aviso no topo);
- a notificação de "novo chamado em aberto", que vai para o **grupo interno** configurado em
  Configurações (`notif_abertos_group_id`), não para o grupo do cliente;
- a "Mensagem de encerramento" das configurações, que é a via oficial de avisar o cliente.

`ChatFlowSession` não é criada em lugar nenhum desde a simplificação do fluxo (0 linhas no banco),
então `_processar_passo_fluxo`/`_finalizar_fluxo` já eram caminhos inalcançáveis.

**Testes:** `AutoAtendimentoNaoPoluiGrupoTest` — com um fluxo universal ativo, uma primeira
mensagem de grupo não gera nenhuma chamada de `send_text` nem balão `sender_type='system'`.
Verificado que os mesmos testes falham no código anterior (2 chamadas de `send_text`).

---

## 🤖 Agente IA fecha o chamado com a resolução (20/08/2026)

O agente "Tomichinho" já lia as mensagens do chamado e abria tarefa; agora também **encerra o
chamado escrevendo a resolução**. Vale nos três caminhos por onde uma mensagem entra no chamado:

| Onde escrever | Exemplo | O que acontece |
|---|---|---|
| Grupo do WhatsApp | "Tomichinho fechar atendimento" | fecha + confirma no grupo |
| Caixa normal do chat (plataforma) | "Tomichinho fechar atendimento" | fecha + confirma no grupo |
| Comentário interno | "fechar atendimento" | fecha + confirma **só no CRM** |

O caminho da caixa normal do chat era um buraco: `ConversationService.send_message` só olhava os
gatilhos quando a mensagem era nota interna, então o pedido digitado na caixa de resposta não fazia
nada (funcionava só se a mesma frase viesse do WhatsApp). Hoje os dois passam por
`_disparar_acoes_ia()`, que dispara **as ações** (abrir tarefa / fechar chamado). A resposta
conversacional a "tomichinho" continua exclusiva do grupo — senão toda menção ao agente numa
mensagem do atendente viraria mais uma mensagem para o cliente.

### Gatilho

`atendimento/services.py` → `_pede_fechamento_de_chamado()`: a mensagem precisa citar o alvo
(`chamado`, `atendimento`, `ticket`, `protocolo`) **e** um verbo de fechamento (`fechar`, `encerrar`,
`finalizar`, `concluir` e suas flexões). Acento errado não atrapalha (`_normalizar_texto`).

Ele é de propósito mais exigente que o gatilho de tarefa — fechar o chamado errado custa mais caro
que abrir uma tarefa a mais:

| Mensagem | Fecha? | Por quê |
|---|---|---|
| "pode fechar o chamado" / "Tomichinho, encerrar o atendimento" | ✅ | alvo + verbo |
| "pode fechar a porta do rack" | ❌ | sem alvo — "fechar" solto não encerra nada |
| "fechar a tarefa da antena" | ❌ | fala de tarefa, não de chamado |
| "o chamado ainda não está resolvido" | ❌ | "resolvido" é adjetivo, não é verbo de fechamento |
| "não pode fechar o chamado ainda" | ❌ | pedido negado (`_FECHAR_NEGADO`) |

### O que a IA faz (`atendimento/tasks.py` → `fechar_chamado_ia`)

1. Lê as **últimas 30 mensagens** do chamado (mais que o gatilho de tarefa: a resolução sai do
   atendimento inteiro, não da última linha).
2. Pede à IA configurada um JSON `{"resolucao": "..."}` — o prompt manda usar **principalmente as
   mensagens do Atendente e as notas internas** (o que foi verificado e feito), usando as do cliente
   só para descrever o problema original, e proíbe inventar o que não está no histórico.
3. Encerra via `services.finalizar_conversa()`: `status='resolved'`, `closed_at`, `resolution`,
   atividade `status_changed`, aviso da caixa de entrada por WebSocket e marco
   "✅ Chamado concluído! 📋 Protocolo #N" no histórico interno.
4. Confirma no mesmo canal do pedido: `✅ Chamado #N encerrado. 📝 Resolução: ...` — no grupo, se o
   pedido veio do WhatsApp; **só no CRM** (nota interna, `is_internal=True`), se veio de comentário
   interno. Nada que começou privado vaza para o cliente.

Chamado já `resolved`/`closed` nem chega a enfileirar a task — o que também evita que a "Mensagem
de encerramento" das configurações ("Finalizamos seu atendimento..."), que sai por `send_message`
logo depois do fechamento, realimente o gatilho.

**Degradação:** sem IA configurada (ou se a chamada falhar), o chamado fecha do mesmo jeito — o
pedido foi explícito — com a resolução montada a partir do histórico. Chamado já
`resolved`/`closed` é ignorado, então repetir o pedido não sobrescreve a resolução anterior.
Esse fallback foi refeito em 02/09/2026 (ver *"Resolução saía como 'pode finalizar o chamado'"*,
abaixo): ele pegava a última fala do atendente, que era o **próprio pedido de fechamento**.

`finalizar_conversa()` nasceu deste trabalho: o fechamento vivia dentro de
`views.api_update_conversation` e a view passou a chamar o mesmo serviço, então tela e IA encerram
chamado exatamente do mesmo jeito.

**Testes:** `GatilhoFechamentoIATest` (gatilhos e não-gatilhos, incluindo negação e nota interna) e
`FecharChamadoIATaskTest` (resolução da IA gravada, prompt recebendo a resposta do atendente,
pedido interno sem `send_text`, fallback sem IA, chamado já encerrado, marco com protocolo).

---

## 🧾 "Listar Chamados" na aba Tarefas do cliente (20/08/2026)

A aba **Tarefas** da página do cliente (`clientes/templates/listar.html`) ganhou o botão **Listar
Chamados**, ao lado de "Nova Tarefa". Ele abre um modal com o histórico de chamados daquele cliente
no módulo de Atendimento, no mesmo formato da tela `/atendimento/historico/` (protocolo, grupo,
status, categoria, agente, criado em, última mensagem). Clicar em uma linha abre a **conversa
completa num segundo modal, dentro do próprio CRM** — ninguém é mandado pro módulo de Atendimento.

O chat do modal é somente leitura, com a mesma leitura do módulo: cliente à esquerda, equipe à
direita, separador por dia, imagem/áudio/vídeo/anexo renderizados, e o cabeçalho com status,
responsável, datas e a resolução em destaque.

- **APIs:** `GET /atendimento/api/cliente/<cliente_id>/conversations/` (lista) e
  `GET /atendimento/api/cliente/<cliente_id>/conversations/<conversation_id>/` (um chamado:
  cabeçalho + mensagens), em `atendimento/views.py`.
- **Vínculo:** busca por `Conversation.cliente` **ou** `group.cliente`. Chamados antigos, abertos
  antes de o grupo do WhatsApp ser vinculado ao cliente, ficaram sem `Conversation.cliente` — sem os
  dois lados o histórico aparece pela metade.
- **Status `pre`** (buffer de pré-abertura, chamado que ainda não abriu) fica de fora, como na caixa
  de entrada. Chamados em tarefa aparecem com o protocolo `T-N`, e a resolução, quando existe, vem
  como segunda linha embaixo do grupo.
- **Permissão:** `@login_required` + `pode_acessar_cliente` (helper `_cliente_do_request`), **não**
  `staff_required` — o próprio cliente, logado no portal, acompanha e valida os chamados dele por
  aqui. Quem não tem vínculo com o cliente leva 403. Como a conversa abre dentro do CRM, o
  staff-only do módulo de Atendimento deixou de ser um limite.
- **Nota interna nunca sai para quem não é staff:** a API do detalhe filtra `is_internal` /
  `sender_type='internal'` — é conversa da equipe sobre o chamado, não algo que o cliente deva ler.
  O chamado também precisa ser mesmo daquele cliente (404 caso contrário), senão um id de conversa
  viraria porta de entrada pro histórico de outro cliente.
- Limite de 300 chamados por consulta e 1000 mensagens por chamado. Quando o filtro devolve mais que
  isso, a lista avisa que está mostrando os 300 mais recentes e sugere refinar o período.

### Filtros (todos no servidor, combináveis)

| Campo | O que faz |
|---|---|
| Busca | protocolo (aceita `#123` / `T-123`), grupo, responsável, categoria, assunto e **texto da resolução** |
| Status | "Em aberto" (new/open/pending), "Encerrados" (resolved/closed) ou um status específico |
| Responsável | um agente ou "Sem responsável" |
| Categoria | uma categoria ou "Sem categoria" |
| Período | `date_from`/`date_to` + **`date_field`**: abertura, última mensagem ou encerramento |
| Atalhos | chips Hoje / 7 dias / 30 dias / Este mês / Este ano |

O `date_field` existe porque "chamados de julho" quer dizer coisas diferentes dependendo de quem
pergunta — quem abriu no mês não é quem fechou no mês. Os selects de responsável e categoria são
montados só com o que aquele cliente realmente tem (a lista do sistema inteiro seria ruído), e são
preenchidos uma única vez: recriá-los a cada filtro apagaria a seleção em curso.

Filtrar no servidor (e não na lista já carregada) é o que faz o filtro valer pro histórico inteiro,
não só pelos 300 chamados que couberam na tela. A busca tem debounce de 350ms.

Acima da tabela há um resumo do **conjunto filtrado** — Chamados, Em aberto, Encerrados e tempo médio
de resolução (`closed_at - created_at`, formatado como `2d 4h` / `3h 12min`). Os três primeiros são
clicáveis e aplicam o status correspondente.

**Testes:** `ChamadosDoClienteAPITest` (lista com resolução, vínculo só pelo grupo, `pre` fora da
lista, usuário do portal vendo os próprios chamados, 403 para quem não tem vínculo),
`ChamadoDetalheDoClienteAPITest` (staff vê a nota interna, cliente do portal não vê, chamado de
outro cliente dá 404) e `ChamadosDoClienteFiltrosTest` (período por abertura vs. encerramento,
status agrupado, responsável, busca por `#protocolo` e por texto da resolução, resumo acompanhando o
filtro, opções só com os responsáveis do cliente).


### Duas armadilhas de UI que apareceram aqui (e valem pra qualquer modal do CRM)

- **Modal dentro de modal precisa de `z-index` acima de 9999.** `.modal-overlay`
  (`static/css/style.css`) já é `z-index:9999`; o modal do chamado tinha nascido com `2000` inline e
  abria *atrás* do overlay preto da lista — o clique parecia não fazer nada. Hoje é `10050`.
- **Modal que nasce dentro de uma aba deve subir pro `<body>` ao abrir.** Basta um ancestral com
  `transform`/`filter` para ele virar o containing block de um `position:fixed`, prendendo o modal
  dentro do outro. `abrirChamadoDetalhe()` faz o `appendChild(document.body)` na abertura, mesmo
  padrão do `ovpnAbrirModal()`.
- Bônus da mesma leva: `<th>` com `position:sticky` precisa de **fundo sólido**
  (`var(--card-bg)`), não `rgba(...)` translúcido — senão as linhas passam por baixo e se leem
  através dos títulos ao rolar.


---

## 📣 Marcar alguém do grupo com "@" no chat (20/08/2026)

No compositor do chat (`atendimento/templates/atendimento/_chat_content.html`), digitar **`@`**
abre a lista de participantes do grupo do WhatsApp. Escolhendo um (clique, ou setas + Enter/Tab), o
nome entra no texto como `@João Silva`; ao enviar, a pessoa recebe a **notificação de menção** no
WhatsApp, com o nome destacado na mensagem.

### Como funciona

| Camada | O quê |
|---|---|
| `EvolutionAPIClient.get_group_participants_info()` | participantes com número, `lid`, nome (quando a Evolution sabe), foto e flag de admin |
| `services.completar_nomes_participantes()` | preenche quem veio sem nome, cruzando três fontes (ver abaixo) |
| `GET /atendimento/api/conversation/<id>/participants/` | alimenta o autocomplete; cache de 5 min por grupo (`?refresh=1` força releitura) |
| `services.aplicar_mencoes()` | troca `@Nome` por `@<número>` no texto que vai pro WhatsApp e devolve os números |
| `ConversationService.send_message(..., mentions=[{nome, phone}])` | salva o texto legível no CRM e envia com `mentioned` pra Evolution |

**Por que nome no CRM e número no WhatsApp:** o WhatsApp só destaca a menção quando o corpo da
mensagem traz `@<número>` batendo com o `mentioned` do envio. Guardar isso no histórico deixaria o
chat do CRM cheio de números; então o CRM fica com `@João Silva` e só o texto que sai pro grupo
carrega o número — mesma coisa que o WhatsApp Web faz.

### Detalhes de comportamento

- **Nota interna não menciona ninguém:** no modo "Comentário Interno" a lista nem abre (nada sai
  pro grupo, então não há quem notificar).
- **`@` no meio de palavra não abre a lista** — `fulano@empresa.com` digitado numa frase não é
  pedido de menção; só `@` que começa palavra.
- **Apagou, não marca:** vão como menção apenas os nomes que continuam escritos na mensagem na hora
  do envio.
- **Nome mais longo primeiro** na substituição: com "João" e "João Silva" no mesmo grupo, trocar o
  curto antes deixaria `@5511... Silva` na frase.
- Escolha por `mousedown` (não `click`): o clique chegaria depois do `blur` do textarea, quando a
  posição do cursor já se perdeu. Setas/Enter/Esc são capturados antes do handler de envio, senão
  Enter mandaria a mensagem em vez de escolher o contato.

**Testes:** `MencaoNoChatTest` — substituição nome→número (inclusive a ordem por tamanho), texto
intacto sem menção, envio guardando o nome no CRM e mandando o número com `mentions`, nota interna
sem `send_text`, API repassando as menções e o endpoint de participantes servindo do cache na
segunda chamada.

---

## 🙋 Menção do "@" passa a mostrar quem é quem (04/09/2026)

**Sintoma:** a lista aberta pelo `@` vinha só com telefone. Sem saber de cor o número de cada
pessoa do grupo do cliente, não dava para escolher quem marcar.

**Causa:** `/group/participants` da Evolution devolve `name: null` para praticamente todo
participante — só vem preenchido para conta Business ou contato salvo na agenda da instância.
Como o código caía para "sem nome, usa o número como rótulo", o resultado era uma lista de
números repetidos em nome e em telefone. Medido ao vivo: dos 11 a 13 participantes de cada
grupo, **1 tinha nome**.

### As três fontes de nome

`services.completar_nomes_participantes()` completa quem veio sem nome, na ordem:

| # | Fonte | Cobre |
|---|---|---|
| 1 | `/chat/findContacts` da instância (`get_contacts_map()`) | quem está salvo na agenda do WhatsApp ou já teve o `pushName` visto pela Evolution — uma chamada só resolve o grupo inteiro, em cache de 10 min |
| 2 | `GroupMemberName` | nomes que o próprio CRM aprendeu do `pushName` de quem já escreveu em algum grupo |
| 3 | `AttendantContact` | números da nossa equipe, que viram o nome do usuário do CRM |

O cruzamento tenta todas as chaves possíveis do mesmo participante (`<id>@lid`, o `lid` puro,
`<numero>@s.whatsapp.net` e o número), porque o WhatsApp hoje identifica gente de grupo pelo
**`@lid`** e não pelo telefone — é o mesmo valor que chega no campo `participant` dos webhooks.

Resultado medido nos grupos reais: de 1 nome por grupo para **8 a 12 de 11 a 13**.

### Nomes aprendidos das mensagens (`GroupMemberName`)

Migração `0014_groupmembername`. Toda mensagem de grupo recebida passa por
`services.aprender_nome_participante(connection, participant, push_name)`, que grava o par
`jid → nome`. É chamada em **todo** webhook de mensagem, então tem um cache em memória de 12 h
por pessoa: só vai ao banco quando o nome muda. A lista vai ficando mais completa sozinha,
conforme o pessoal do grupo escreve.

### Na tela

- **Nome em cima, número formatado embaixo** (`+55 (27) 98176-1251`) — dá para achar tanto pela
  pessoa quanto pelo número, e a busca aceita as duas coisas (digitando número, a máscara é
  ignorada: `74 9925` acha `557499255512`).
- **Foto do WhatsApp** no avatar quando a Evolution manda (`imgUrl`); essas URLs expiram, então
  o `error` da `<img>` devolve a inicial em vez de deixar ícone quebrado.
- **Selo `admin`** em quem administra o grupo.
- **Ordenação**: quem tem nome primeiro, em ordem alfabética; os números sem dono vão para o fim.
- **Quem continua sem nome aparece só com o número formatado, em itálico** — de propósito.
  Repetir o telefone no lugar do nome era exatamente o que confundia.
- **Botão de releitura** no cabeçalho da lista: o cache é de 5 min, então quem acabou de entrar
  no grupo só aparece forçando (`?refresh=1`, que também descarta a agenda da instância).

**Testes:** `NomeDosParticipantesTest` — cada uma das três fontes preenchendo o nome, nome que já
veio da Evolution não sendo sobrescrito (e nem consultando a agenda à toa), participante
desconhecido saindo com nome vazio, aprendizado idempotente pelo webhook, troca de nome e
`pushName` vazio não gravando nada.

---

## ✏️ Editar mensagem já enviada — no CRM e no WhatsApp (04/09/2026)

O atendente corrige o que escreveu direto no balão do chat, e a correção vale
**também no WhatsApp do grupo** — o cliente vê a mensagem reescrita com o selo
"Editada", igual a uma edição feita pelo celular.

### Como funciona

| Camada | O quê |
|---|---|
| `ConversationService.pode_editar(msg, user)` | única fonte da regra: quem pode, o quê e até quando. Usada pela API **e** pelo que a tela mostra, para o lápis e o backend nunca discordarem |
| `ConversationService.edit_message(msg, texto, agent, mentions)` | reescreve no WhatsApp e, **só se der certo lá**, no CRM |
| `EvolutionAPIClient.edit_text(jid, message_id, texto, mentions)` | `POST /chat/updateMessage/{instance}` com a `key` da mensagem original |
| `POST /atendimento/api/message/<id>/edit/` | endpoint da tela; devolve o texto novo e a hora da edição |
| WS `message_edited` | atualiza o balão nas outras abas e nas telas dos outros atendentes |

### Regras (e o porquê de cada uma)

- **Só mensagem sua.** O corpo da mensagem no grupo leva a assinatura de quem
  escreveu (`*Fulano*`); reescrever a fala de outra pessoa sob o nome dela é
  coisa de supervisão, então **só o autor — ou um Administrador** — edita.
- **Só texto.** O WhatsApp não permite editar áudio, imagem ou documento.
- **15 minutos.** É a janela do próprio WhatsApp
  (`ConversationService.JANELA_EDICAO_MIN`). Passado o prazo a Evolution
  responde `Message not compatible`; o lápis some sozinho da tela, sem precisar
  recarregar (varredura de 1 min sobre `data-editavel-ate`).
- **Mensagem automática não se edita.** IA e fluxo escrevem sem `sender`;
  mudar o texto no CRM só criaria divergência com o que de fato saiu.
- **Nota interna não tem prazo** e não fala com o WhatsApp — nunca saiu do CRM.
- **Mensagem ainda não confirmada não se edita.** Enquanto o envio em
  background não devolve o `wamid`, o `external_id` é um id interno
  (`sending_…`) e não existe `key` para editar lá.

### Por que o salvamento é síncrono

O envio de mensagem vai para uma thread em background (não travar a resposta
HTTP). A **edição não**: se a Evolution recusar e o CRM tivesse gravado assim
mesmo, a tela passaria a mostrar um texto que o cliente nunca viu — o oposto do
que o recurso existe para resolver. Falhou lá, não muda aqui, e o motivo da
recusa aparece embaixo do campo de edição.

### A assinatura continua a mesma

A edição sai com `*NomeDeQuemEscreveu*` — não o de quem editou. Senão a
mensagem corrigida apareceria no grupo sem a assinatura que todas as outras
têm, ou assinada por quem não a escreveu.

### Quando o cliente edita no celular

`_extrair_edicao()` reconhece o `protocolMessage` do tipo `MESSAGE_EDIT` que
chega no `MESSAGES_UPSERT` e atualiza o balão existente
(`_registrar_edicao_recebida`) em vez de criar um novo. Sem isso o evento não
batia com nenhum extrator de conteúdo e virava um balão **"[sem conteúdo]"** no
meio da conversa — o mesmo estrago que as reações faziam antes de ganharem
tratamento próprio.

**Limitação conhecida:** o webhook assina apenas `MESSAGES_UPSERT`. Se a versão
da Evolution mandar a edição como `MESSAGES_UPDATE`, ela não chega ao CRM — o
balão continua com o texto antigo (sem quebrar nada). O formato tratado aqui é
o que a 2.3.7 usa.

### Na tela

- Lápis no hover da mensagem, fora do balão à esquerda (dentro brigaria com o
  texto e com a hora flutuante), com borda e fundo sólido. **Duplo-clique na
  própria mensagem também abre a edição** — é o gesto que a pessoa tenta antes
  de procurar botão.
- Onde não existe hover (celular, tablet) o lápis fica **sempre visível**
  (`@media (hover: none)`), só mais discreto: a primeira versão era um ícone
  sem contorno com `opacity:0`, invisível no toque e difícil de achar no
  desktop — na prática o recurso não existia para quem não passasse o mouse
  exatamente ali.
- **O lápis não some quando o prazo vence.** Ele continua na mensagem e o
  clique explica o motivo ("O WhatsApp só deixa editar até 15 minutos depois do
  envio"). Sumir deixava o atendente sem saber se o recurso existe, se quebrou
  ou se ele fez algo errado. Quem decide é a API, que continua recusando —
  `pode_editar(..., ignorar_prazo=True)` serve só para a tela mostrar o botão.
- Ao clicar, o balão dá lugar a um textarea no mesmo espaço — **Enter salva,
  Esc cancela**, e o botão trava enquanto espera a Evolution.
- Selo **"editada"** antes da hora, como no WhatsApp. O histórico não guarda a
  versão anterior: o selo é o único sinal de que o texto mudou.
- Menção com `@` funciona na edição igual ao envio — o CRM guarda `@Fulano` e o
  corpo que vai pro grupo leva o número, para a mensagem editada ficar igual às
  outras. **Mas editar não notifica ninguém:** o `updateMessageSchema` da
  Evolution só carrega `number`, `text` e `key`, e o controller ignora
  `mentioned`. Marcar alguém novo numa edição não avisa essa pessoa — mandar o
  campo assim mesmo só daria a impressão de que funciona.

**Validado ao vivo** (04/09/2026) contra a instância `atendimento_n3`, no chat da própria
instância: mensagem enviada (`3EB0ED87234BD07B0EC86B`), editada por `edit_text` e conferida em
`/chat/findMessages` — o histórico do WhatsApp passou a devolver o texto novo. A mensagem de
teste foi removida em seguida.

**Testes:** `EditarMensagemTest` (19 casos) — cada regra de `pode_editar`,
assinatura original preservada, menção virando número, **Evolution recusando
não muda o CRM**, nota interna sem chamada ao WhatsApp, texto igual não chama
ninguém, edição vinda do cliente atualizando o balão sem criar mensagem nova, e
a API recusando mensagem de outro atendente e texto vazio.

---

## Quem entra no módulo (2026-08-21)

O Atendimento é **exclusivo da instância principal** — a operação própria do Administrador. Não é
um módulo de revenda: Consultor e Operador de outra instância não entram, nem tela, nem API, nem
WebSocket.

**A regra vive em `usuario.perms.pode_acessar_atendimento`:**

```
Administrador                          → entra
Consultor/Operador da instância com
  Instancia.principal = True           → entra
Consultor/Operador de qualquer outra   → não entra
Login do portal do cliente final       → não entra
```

`Instancia.principal` é um booleano no modelo (migrações `usuario.0010` e `0011`). A migração de
dados marca a instância chamada "Principal", criada em 19/08/2026 para receber os clientes que
estavam com `instancia = NULL` (ver `docs/PERMISSOES_CONSULTOR.md`). **Se nenhuma instância
estiver marcada** — instalação nova, banco de teste — o módulo fica só com o Administrador; ele
nunca cai aberto por falta de configuração.

### Por que não é `is_staff`

`staff_required` checava `request.user.is_staff` cru. Isso funcionou enquanto só o Administrador
era `is_staff`, mas `_is_staff_para_role` passou a criar Consultor e Operador com `is_staff=True`
— coisa de que eles **precisam** para outras features (Scripts de Automação em
`clientes/script_views.py` e o WebSocket de firmware em `clientes/consumers.py`). Com isso o
módulo inteiro ficou aberto para todas as revendas. Não dá para "resolver" tirando o `is_staff`
deles: quebraria scripts e firmware. Quem decide é o papel + a instância.

### Onde o gate está aplicado

- **HTTP**: `atendimento.views.staff_required` (toda tela e API do módulo). Configuração de
  plataforma — conexões WhatsApp, permissões, settings — continua um degrau acima, no
  `admin_required`, exclusivo do Administrador.
- **WebSocket**: `atendimento/consumers.py` (`ConversationConsumer`, `InboxConsumer`,
  `VirtualRoomConsumer`) via `_pode_atendimento`. Antes checavam só `is_authenticated`: qualquer
  conta logada — inclusive login de portal — assinava `atendimento_inbox` e recebia em tempo real
  toda mensagem que passasse pelo módulo, mesmo sem conseguir abrir a tela.
- **Menu**: `templates/base.html` usa `pode_atendimento_bo` (context processor de `usuario`).

Dois endpoints estavam sem gate nenhum do módulo e foram corrigidos junto: as sete APIs de
**kanban** (só `@login_required` — qualquer conta logada lia e escrevia nos quadros) e
`api_tags_list`, que **não tinha decorator algum**.

### O que continua fora do gate, de propósito

`api_cliente_conversations` e `api_cliente_conversation_detail` — são o botão "Listar Chamados" da
página do cliente no CRM (`clientes/listar.html`), não o módulo. Já passam por
`pode_acessar_cliente`, então cada um só alcança os próprios clientes. `webhook_evolution` é
webhook público por natureza (`csrf_exempt`, validado pela instância da Evolution).

### Escopo dos dados dentro do módulo

`atendimento/scope.py` continua valendo e não virou redundante: o Operador da instância principal
vê os clientes/conversas/grupos **dela**, não os de uma revenda, e os guardas por id
(`pode_ver_conversation` / `pode_ver_group`) seguem fechando os IDOR.

**Regressão:** `atendimento.tests.AtendimentoExclusivoDaPrincipalTest` (8 testes) e
`EscopoDeDadosNoAtendimentoTest` (8 testes).


---

## 🤖 Resolução do chamado saía como "pode finalizar o chamado" (02/09/2026)

Todo chamado fechado pelo agente vinha com a **resolução errada** — literalmente o comando que
pediu o fechamento:

| Protocolo | Resolução gravada |
|---|---|
| #1508 | `pode finalizar o chamado` |
| #1509 | `Fechar atendimento` |
| #1506 | `OK` |

### Causa raiz — a IA estava fora e ninguém sabia

`atendimento/ai.py` devolvia `None` em toda chamada. A conta configurada em
**Configurações → Integração IA** (provedor `openai`, modelo `gpt-4o`) estava **sem crédito**:

```
429 - {'error': {'message': 'You have no credits remaining. Add credits to continue using the
API…', 'type': 'insufficient_quota', 'code': 'credit_balance_exhausted'}}
```

A falha ia para o log em `WARNING` e nada mais. Como `fechar_chamado_ia` fecha o chamado de
qualquer jeito (é comportamento desejado — o pedido foi explícito), o fechamento parecia normal e
a resolução seguia sendo gravada em silêncio.

### Causa 2 — o fallback devolvia o próprio gatilho

O fallback era "última mensagem do atendente/nota interna". Quando a task roda, **o pedido de
fechamento já está gravado como mensagem** — então a "última fala do atendente" era exatamente
`"pode finalizar o chamado"`. O fallback nunca teve chance de acertar.

### O que mudou

**`atendimento/ai.py`**

- **Fallback de provedor**: falhou o provedor escolhido, tenta o outro, desde que tenha API key
  salva. Uma conta sem crédito não derruba as automações quando existe um segundo provedor
  configurado ali do lado.
- **O erro para de ser invisível**: o motivo fica em `SystemSetting` (`ai_last_error` /
  `ai_last_error_at`), lido por `ultimo_erro_ia()`, e o log sobe de `WARNING` para `ERROR`.
  `_motivo_legivel()` traduz a exceção do SDK no que precisa ser feito ("conta sem crédito/quota —
  recarregue o saldo do provedor", "API key inválida ou revogada", "rate limit", "modelo
  indisponível para esta chave"). Chamada bem-sucedida limpa o registro.

**`atendimento/tasks.py`**

- **`_resolucao_de_fallback(historico)`** substitui o "última fala do atendente": descarta o pedido
  de fechamento (`_pede_fechamento_de_chamado`), as confirmações do próprio Tomichinho (começam com
  `✅`) e os marcos do Sistema, e junta as **últimas 3 falas úteis** do atendente. Sem nenhuma,
  descreve o relato do cliente (`"Encerrado sem tratativa registrada no chat. Relato do cliente:
  …"`). Nunca mais grava o gatilho como resolução.
- **`_contexto_conversa(conv, limite, incluir_inicio)`**: `fechar_chamado_ia` passa
  `incluir_inicio=4`, prendendo as 4 primeiras mensagens do chamado no começo do contexto. Em grupo
  movimentado, as últimas 30 linhas são só o desfecho e o relato original do cliente — o "o que o
  cliente pediu" que a resolução precisa dizer — já teria saído da janela. Um `(…)` marca o trecho
  omitido, e o prompt explica o que ele significa.
- **Guarda contra a IA devolver o gatilho**: resolução com menos de 60 caracteres que bata em
  `_pede_fechamento_de_chamado` é descartada e cai no fallback.
- **A confirmação avisa**: quando a resolução saiu do fallback, a mensagem de encerramento ganha
  `⚠️ Resolução montada a partir do histórico: a IA não respondeu (<motivo>). Revise em
  Configurações → Integração IA.` — no canal em que o pedido foi feito.

**Tela** (`atendimento/templates/atendimento/configuracoes.html` + `views.configuracoes`): a aba
**Integração IA** mostra um aviso em destaque com o último erro e quando aconteceu. Salvar as
configurações da IA limpa o aviso (`api_settings`), pra não deixar erro velho pendurado depois de
trocar a chave.

### O que ainda depende de você

O código agora avisa, mas não conjura crédito: enquanto a conta da OpenAI estiver zerada **e** o
campo *API Key (Claude)* estiver vazio, as resoluções continuam saindo do fallback (agora com o
texto certo e com o aviso). Recarregue o saldo da OpenAI **ou** preencha a chave do Claude em
Configurações → Integração IA.

O prompt em si estava correto — validado ao vivo contra o chamado #1508 real, que devolveu:
*"Cliente relatou que o link da Garra foi desativado e questionou se houve rompimento. Atendente
confirmou que se tratava de problema no link… Protocolo #1508."*

**Testes:** `FecharChamadoIATaskTest` ganhou 5 casos — fallback não usa o pedido de fechamento,
aviso da IA fora na confirmação, relato do cliente quando não há fala do atendente, IA devolvendo o
gatilho cai no fallback, e o relato original chegando ao prompt em conversa longa.
