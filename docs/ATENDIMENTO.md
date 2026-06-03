# 📞 Sistema de Atendimento — Documentação Técnica

## 📋 Visão Geral

Plataforma de atendimento ao cliente integrada diretamente no CRM, similar ao Chatwoot. Sistema centralizado para gerenciar tickets de suporte através de WhatsApp com integração Evolution API.

**Data de Implementação:** 01/06/2026
**Status:** ✅ FUNCIONAL
**Stack:** Django, PostgreSQL, Bootstrap 5, JavaScript vanilla

---

## 🎯 Recursos Principais

### 1. Dashboard de Atendimento
- Visão geral de tickets abertos, em atendimento e resolvidos
- Estatísticas em tempo real
- Últimos tickets com atualização rápida
- Gráficos de tickets por status

### 2. Caixa de Entrada (Inbox)
- Lista de tickets com filtros por status e conexão
- Busca global em tempo real
- Visualização em tempo real do chat
- Indicadores de prioridade e status

### 3. Chat de Ticket
- Interface semelhante ao WhatsApp
- Envio de mensagens via Evolution API
- Histórico de conversas
- Dados do cliente e grupo WhatsApp

### 4. Gerenciamento de Grupos
- Sincronização automática de grupos WhatsApp
- Vinculação de grupos a clientes
- Auto-associação por nome (inteligência artificial)
- Visualização de participantes

### 5. Configuração de Conexões
- Adicionar múltiplas instâncias Evolution API
- Testar conexão com validação
- Configurar webhooks automaticamente
- Sincronizar grupos de qualquer instância
- Codificação de cores por instância

### 6. Vinculação com Hosts/Clientes
- Botão para abrir hosts do cliente no ticket
- Acesso rápido aos equipamentos
- Integração com módulo de clientes

---

## 🗂️ Arquitetura

### Modelos (Models)

#### WhatsappConnection
```python
- id: UUID (PK)
- name: CharField (nome da instância)
- evolution_url: URLField
- evolution_api_key: CharField
- instance_name: CharField
- color: CharField (código hex)
- active: BooleanField
- webhook_configured: BooleanField
- last_sync: DateTimeField
```

#### GrupoWhatsapp
```python
- jid: CharField (ID do WhatsApp)
- connection: ForeignKey(WhatsappConnection)
- name: CharField
- owner: CharField
- participants_count: IntegerField
- description: TextField
- picture_url: URLField
- cliente: ForeignKey(Cliente, null=True)
- status: CharField (active/archived/deleted)
- unique_together: (jid, connection)
```

#### Ticket
```python
- id: UUID (PK)
- numero: IntegerField (auto-incrementado)
- grupo: ForeignKey(GrupoWhatsapp)
- cliente: ForeignKey(Cliente, null=True)
- assunto: CharField
- descricao: TextField
- status: CharField (aberto/em_atendimento/aguardando_cliente/resolvido/fechado)
- atendente: ForeignKey(User, null=True)
- prioridade: CharField (baixa/normal/alta/urgente)
- criado_em, atualizado_em, fechado_em: DateTimeField
- tempo_resposta: DurationField (null=True)
```

#### Mensagem
```python
- id: UUID (PK)
- ticket: ForeignKey(Ticket)
- remetente_tipo: CharField (cliente/atendente/sistema)
- remetente: ForeignKey(User, null=True)
- numero_whatsapp: CharField
- tipo: CharField (texto/imagem/documento/audio/video/localizacao/sistema)
- conteudo: TextField
- arquivo: FileField (null=True)
- url_midia: URLField (null=True)
- message_id: CharField (ID único no WhatsApp)
- criada_em, editada_em: DateTimeField
- reacoes: JSONField
- unique: message_id
```

#### TagTicket
```python
- nome: CharField (unique)
- cor: CharField (código hex)
- tickets: ManyToManyField(Ticket)
```

#### HistoricoAtendimento
```python
- ticket: ForeignKey(Ticket)
- usuario: ForeignKey(User, null=True)
- acao: CharField
- descricao: TextField
- campo_anterior, campo_novo: TextField
- criado_em: DateTimeField
```

#### ConfiguracaoAtendimento
```python
- key: CharField (unique, PK)
- value: JSONField
- descricao: TextField
- atualizado_em: DateTimeField
```

---

## 🔌 APIs e Endpoints

### URLs (atendimento/urls.py)

#### Páginas
| URL | Método | Descrição |
|-----|--------|-----------|
| `/atendimento/` | GET | Dashboard de atendimento |
| `/atendimento/inbox/` | GET | Caixa de entrada |
| `/atendimento/ticket/<id>/` | GET, POST | Detalhes e chat do ticket |
| `/atendimento/grupos/` | GET | Gerenciamento de grupos |
| `/atendimento/configuracoes/` | GET | Configurações de conexão |

#### APIs JSON
| URL | Método | Descrição |
|-----|--------|-----------|
| `/atendimento/api/tickets/` | GET | Lista tickets com filtros |
| `/atendimento/api/ticket/<id>/` | GET, POST | Detalhes do ticket |
| `/atendimento/api/ticket/<id>/mensagem/` | POST | Enviar mensagem |
| `/atendimento/api/conexao/criar/` | POST | Criar nova conexão |
| `/atendimento/api/conexao/<id>/testar/` | POST | Testar conexão |
| `/atendimento/api/conexao/<id>/webhook/` | POST | Configurar webhook |
| `/atendimento/api/conexao/<id>/sincronizar/` | POST | Sincronizar grupos |
| `/atendimento/api/grupo/<id>/vincular/` | POST | Vincular grupo a cliente |
| `/atendimento/webhook/` | POST | Receber mensagens da Evolution API |

---

## 🔄 Fluxo de Funcionamento

### 1. Configuração Inicial

```
Usuário acessa Atendimento → Configurações
├─ Preenche dados da Evolution API
│  ├─ Nome da instância
│  ├─ URL da Evolution
│  ├─ API Key
│  └─ Nome da instância no Evolution
│
└─ Clica "Criar Conexão"
   └─ Conexão criada no banco
```

### 2. Sincronização de Grupos

```
Usuário clica "Sincronizar" em uma conexão
├─ Sistema faz requisição GET para Evolution API
│  └─ Obtém lista de grupos (chats)
│
├─ Para cada grupo:
│  ├─ Cria ou atualiza registro GrupoWhatsapp
│  ├─ Tenta vincular automaticamente ao cliente
│  └─ Obtém foto e detalhes do grupo
│
└─ Atualiza last_sync da conexão
```

### 3. Auto-associação de Cliente

```
Grupo "Cliente ABC" é sincronizado
├─ Sistema procura por Cliente com nome contendo "ABC"
├─ Se encontrado:
│  └─ Vincula automaticamente
│
└─ Se não encontrado:
   └─ Grupo fica sem cliente (pode ser vinculado manualmente)
```

### 4. Recebimento de Mensagem

```
Mensagem chega no WhatsApp Group
├─ Evolution API envia webhook para /atendimento/webhook/
│  
├─ Sistema recebe e processa
│  ├─ Encontra a conexão pela instance_name
│  ├─ Encontra ou cria o grupo
│  ├─ Obtém ou cria ticket aberto do grupo
│  └─ Cria registro de Mensagem
│
└─ Mensagem aparece no chat em tempo real
```

### 5. Resposta do Atendente

```
Atendente digita e clica enviar no ticket
├─ JavaScript envia POST para /atendimento/api/ticket/<id>/mensagem/
│
├─ Sistema:
│  ├─ Cria registro de Mensagem (tipo=atendente)
│  ├─ Muda status do ticket para em_atendimento
│  ├─ Faz requisição POST para Evolution API
│  │  └─ Envia mensagem para o grupo WhatsApp
│  │
│  └─ Retorna JSON com sucesso
│
└─ Mensagem aparece no chat do atendente
   └─ Mensagem é entregue no WhatsApp
```

---

## 🚀 Instalação e Setup

### 1. Verificar Instalação

```bash
# App registrado
grep 'atendimento' /opt/crm/crm/settings.py

# Migrações aplicadas
/opt/crm/venv/bin/python manage.py showmigrations atendimento
```

### 2. Criar Superuser (se necessário)

```bash
/opt/crm/venv/bin/python manage.py createsuperuser
```

### 3. Acessar Admin Panel

```
http://localhost/admin/atendimento/
```

### 4. Configurar Primeira Conexão

1. Acesse http://localhost/atendimento/configuracoes/
2. Na aba "Conexões WhatsApp", preencha:
   - **Nome da Instância:** "WhatsApp Principal"
   - **URL da Evolution API:** `https://seu-evolution-api.com`
   - **API Key:** `sua-api-key`
   - **Nome da Instância no Evolution:** `minha-instancia`
   - **Cor:** escolha uma cor

3. Clique "Criar Conexão"
4. Clique "Testar" para validar
5. Clique "Configurar Webhook" para registrar a rota

### 5. Sincronizar Grupos

1. Clique "Sincronizar" na conexão criada
2. Aguarde a mensagem de sucesso
3. Vá para "Gerenciar Grupos"
4. Veja os grupos sincronizados
5. Vincule cada grupo a um cliente (manual ou automático)

---

## 🔐 Controle de Acesso

- **Autenticação:** Requer login (`@login_required`)
- **Webhook:** Público (`@csrf_exempt`) — proteger com validação de IP em produção
- **Admin:** Registrado em `admin.py` — acesso apenas para staff

---

## 🔧 Configuração Evolution API

### Webhook Esperado

```json
{
  "instance": "minha-instancia",
  "data": {
    "message": {
      "id": "message_id_unico",
      "from": "grupo_jid",
      "fromMe": false,
      "body": "Conteúdo da mensagem",
      "text": "Conteúdo da mensagem"
    }
  }
}
```

### Requisições para Evolution API

```python
# Test
GET https://evolution.api.com/instances/minha-instancia
Header: apikey = API_KEY

# Get Chats
GET https://evolution.api.com/message/minha-instancia/chats

# Send Message
POST https://evolution.api.com/message/minha-instancia/sendText
{
  "number": "grupo_jid",
  "text": "Mensagem"
}

# Configure Webhook
POST https://evolution.api.com/webhooks/minha-instancia
{
  "webhookUrl": "https://seu-crm.com/atendimento/webhook/",
  "webhookByEvents": true,
  "webhookEvents": ["messages.upsert"]
}
```

---

## 📊 Fluxo Visual

```
┌─────────────────────────────────────────────────────────┐
│                    ATENDIMENTO CRM                       │
├──────────────────────────────────────────────────────────┤
│                                                           │
│  ┌────────────────┐         ┌─────────────────┐         │
│  │  Evolution API │◄────────│  WhatsApp Group │         │
│  └────────────────┘         └─────────────────┘         │
│         ▲                            │                   │
│         │                            ▼                   │
│         │                    ┌──────────────┐            │
│         │                    │   Webhook    │            │
│         │                    │ /atendimento/│            │
│         │                    │  webhook/    │            │
│         │                    └──────────────┘            │
│         │                            │                   │
│         │                            ▼                   │
│         │                    ┌──────────────┐            │
│         │                    │   Processor  │            │
│         │                    │   Services   │            │
│         │                    └──────────────┘            │
│         │                            │                   │
│         │                            ▼                   │
│         │                    ┌──────────────┐            │
│         ├────────────────────│  Database    │            │
│         │                    │  Models      │            │
│         │                    └──────────────┘            │
│         │                            │                   │
│         │                            ▼                   │
│         │                    ┌──────────────┐            │
│         └────────────────────│   Frontend   │            │
│                              │  Templates   │            │
│                              └──────────────┘            │
│                                    │                    │
│                                    ▼                    │
│                            ┌──────────────┐            │
│                            │   Atendente  │            │
│                            │   (Usuário)  │            │
│                            └──────────────┘            │
└──────────────────────────────────────────────────────────┘
```

---

## 🎨 Customizações Criativas

### 1. Cores Dinâmicas por Conexão
Cada conexão WhatsApp tem uma cor única que aparece em:
- Badge no ticket
- Indicador no grupo
- Filtro de conexão

### 2. Auto-vinculação Inteligente
Sistema tenta automaticamente vincular grupos a clientes pelo nome similar.

### 3. Chat em Tempo Real
Integração Ajax para atualização de chats sem recarregar página.

### 4. Indicadores de Status
Badges coloridas por status do ticket (aberto, em atendimento, resolvido, etc).

### 5. Responsive Design
Interface funciona bem em desktop, tablet e mobile.

---

## 🐛 Troubleshooting

### Webhook Não Recebe Mensagens

1. Verificar se URL webhook está acessível:
   ```bash
   curl -X POST https://seu-crm.com/atendimento/webhook/ \
     -H "Content-Type: application/json" \
     -d '{"test": true}'
   ```

2. Verificar logs do Django:
   ```bash
   tail -f /opt/crm/celery.log
   ```

3. Testar conexão Evolution:
   - Clicar "Testar" na configuração da conexão
   - Deve retornar "Conexão estabelecida"

### Grupos Não Sincronizam

1. Validar credenciais Evolution API
2. Verificar se instância está ativa na Evolution
3. Ver logs: `django.log` ou `/tmp/atendimento.log`

### Mensagens Não Enviam

1. Verificar se Evolution API está acessível
2. Validar se grupo JID está correto
3. Ver erro na resposta do webhook

---

## 📚 Referências

- **Models:** `/opt/crm/atendimento/models.py`
- **Views:** `/opt/crm/atendimento/views.py`
- **Services:** `/opt/crm/atendimento/services.py`
- **URLs:** `/opt/crm/atendimento/urls.py`
- **Templates:** `/opt/crm/atendimento/templates/atendimento/`
- **Admin:** `/opt/crm/atendimento/admin.py`

---

## 🔮 Recursos Futuros

- [ ] Integração de IA (Claude) para respostas automáticas
- [ ] WhatsApp Web (integração alternativa)
- [ ] Transferência de tickets entre atendentes
- [ ] Sistema de avaliação de atendimento
- [ ] Relatórios e analytics
- [ ] Integração com CRM de clientes
- [ ] Automação de resposta padrão
- [ ] Agendamento de tickets
- [ ] Priorização automática
- [ ] Integração com ferramentas externas (Jira, Linear, etc)

---

**Última atualização:** 01/06/2026 11:30 UTC
**Versão:** 1.0 (MVP)
**Mantidor:** CampeloSuporte
