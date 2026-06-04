# 📞 Sistema de Atendimento — Documentação Técnica

## 📋 Visão Geral

Plataforma de atendimento ao cliente integrada ao CRM, similar ao Chatwoot. Centraliza o gerenciamento de tickets de suporte via WhatsApp (Evolution API v2), com tarefas, alertas automáticos, lembretes pessoais e relatórios completos.

**Última atualização:** 03/06/2026  
**Status:** ✅ FUNCIONAL  
**Stack:** Django, PostgreSQL, Celery, WebSocket (Django Channels), JavaScript vanilla

---

## 🎯 Recursos Principais

| Recurso | Descrição |
|---------|-----------|
| Dashboard | Estatísticas em tempo real de chamados abertos, resolvidos, agentes online |
| Inbox | 4 abas: Assumidos / Abertos / Em Andamento / **Em Tarefa** |
| Chat | Interface WhatsApp com envio de mídia, tags, transferência, terminal de hosts |
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

### Aba "Tarefas" no Inbox
- Quarta aba com badge roxo
- Lista todas as conversas com `is_task_conv=True` e status ativo
- Faixa informativa no topo da lista

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

---

**Mantido por:** CampeloSuporte  
**Repositório:** /opt/crm
