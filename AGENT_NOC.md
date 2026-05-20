# Agent NOC Tomich — Documentação de Especificação

> Agente de inteligência artificial integrado ao CRM Tomich para suporte NOC via terminal web e WhatsApp.  
> Suporta os provedores **Claude (Anthropic)** e **ChatGPT (OpenAI)**, selecionável nas configurações.  
> Este documento serve tanto como especificação de implementação quanto como base de conhecimento que alimenta o próprio agente.

---

## Sumário

1. [Visão Geral](#1-visão-geral)
2. [Arquitetura](#2-arquitetura)
3. [Módulos](#3-módulos)
   - [3.1 Terminal — Interface do Operador](#31-terminal--interface-do-operador)
   - [3.2 WhatsApp — Interface de Campo](#32-whatsapp--interface-de-campo)
   - [3.3 Motor do Agent (Claude / OpenAI)](#33-motor-do-agent-claude--openai)
   - [3.4 Base de Conhecimento](#34-base-de-conhecimento)
4. [Modelos de Dados](#4-modelos-de-dados)
5. [Configuração do Sistema](#5-configuração-do-sistema)
6. [Segurança e Permissões](#6-segurança-e-permissões)
7. [Fluxos de Uso Detalhados](#7-fluxos-de-uso-detalhados)
8. [Endpoints e WebSockets](#8-endpoints-e-websockets)
9. [Base de Conhecimento — Estrutura de Conteúdo](#9-base-de-conhecimento--estrutura-de-conteúdo)
10. [Plano de Implementação](#10-plano-de-implementação)

---

## 1. Visão Geral

O **Agent NOC Tomich** é um assistente de inteligência artificial baseado na API Claude (Anthropic) integrado diretamente ao CRM. Ele atua em dois canais:

| Canal | Quem usa | Como acessa |
|---|---|---|
| **Terminal Web** | Operadores NOC internos | Botão "Chamar Agent" no terminal SSH/Telnet |
| **WhatsApp** | Técnicos de campo, clientes, gestores | Mensagem num grupo vinculado a um cliente |

### Capacidades do Agent

- **Lê e executa comandos** nos equipamentos do cliente via as conexões SSH/Telnet já existentes na plataforma
- **Analisa saídas** de equipamentos e interpreta erros, estados de interface, logs de OLT, tabelas BGP, etc.
- **Responde perguntas** sobre o cliente, sua topologia, histórico de backups, alarmes ativos
- **Age de forma supervisionada** no terminal (operador vê e aprova cada ação) ou de forma **autônoma pré-autorizada** no WhatsApp (apenas comandos read-only da lista segura)
- **Escalona** para o NOC humano via WhatsApp quando não consegue resolver autonomamente
- **Aprende** com a base de conhecimento editável pelos operadores: comandos por fabricante, procedures de troubleshooting, topologias, notas de clientes

### O que o Agent NUNCA faz

- Acessar hosts de um cliente diferente do que originou a solicitação
- Executar comandos destrutivos sem aprovação explícita do operador
- Responder em grupos de WhatsApp não vinculados a um cliente ativo
- Compartilhar informações de um cliente com outro grupo

---

## 2. Arquitetura

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CRM Tomich                                  │
│                                                                     │
│  ┌─────────────┐    ┌──────────────────────────────────────────┐   │
│  │  Terminal   │    │           Agent NOC Engine               │   │
│  │  Web (WS)  │◄──►│                                          │   │
│  │             │    │  ┌──────────────┐  ┌──────────────────┐ │   │
│  │  [Chamar    │    │  │ Claude API   │  │  Base de         │ │   │
│  │   Agent]    │    │  │ (Anthropic)  │  │  Conhecimento    │ │   │
│  └─────────────┘    │  └──────┬───────┘  └──────────────────┘ │   │
│                     │         │ tools                          │   │
│  ┌─────────────┐    │  ┌──────▼───────────────────────────┐   │   │
│  │  Evolution  │    │  │         Tool Executor             │   │   │
│  │  API (WA)  │◄──►│  │  execute_command / read_output /  │   │   │
│  │  Webhook    │    │  │  list_hosts / get_client_info /   │   │   │
│  └─────────────┘    │  │  escalate_to_noc                  │   │   │
│                     │  └──────────────────────────────────┘   │   │
│  ┌─────────────┐    │         │                               │   │
│  │  SSH/Telnet │◄───┼─────────┘                               │   │
│  │  Consumers  │    │   usa conexões existentes da plataforma  │   │
│  └─────────────┘    └──────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────┐                      │
│  │  Banco de Dados (PostgreSQL)             │                      │
│  │  AgentConfig · EvolutionConfig ·         │                      │
│  │  WhatsAppGrupo · AgentSessao ·           │                      │
│  │  AgentLog · AgentKnowledge               │                      │
│  └──────────────────────────────────────────┘                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Fluxo WhatsApp

```
Técnico de campo envia mensagem no grupo do cliente
        │
        ▼
Evolution API recebe e dispara webhook POST /agent/wa/webhook/
        │
        ▼
Django valida: grupo está vinculado a qual cliente?
        │ (não vinculado → ignora silenciosamente)
        ▼
AgentNOCEngine carrega contexto do cliente
  - Acessos (hosts disponíveis)
  - Topologia e notas
  - Últimos backups
  - Knowledge base relevante
        │
        ▼
Claude API processa com tools disponíveis
        │
  ┌─────┴──────────────────────────┐
  │ precisa executar comando?       │
  │   YES → tool execute_command   │
  │         → SSH/Telnet consumer  │
  │         → captura output        │
  │         → retorna ao Claude    │
  │   NO  → resposta direta        │
  └────────────────────────────────┘
        │
        ▼
Evolution API envia resposta ao grupo
```

### Fluxo Terminal

```
Operador clica "Chamar Agent" no terminal
        │
        ▼
Abre aba "Agent NOC" ao lado do terminal SSH ativo
        │
        ▼
WebSocket /ws/agent/<sessao_id>/
        │
        ▼
Operador digita mensagem ou seleciona contexto
(pode arrastar output do terminal para o agent)
        │
        ▼
AgentNOCEngine (mesma engine, contexto diferente)
        │
        ▼
Modo Supervisionado:
  Agent propõe comando → operador aprova/rejeita
  → executa no terminal ativo → captura output
  → agent analisa e responde
```

---

## 3. Módulos

### 3.1 Terminal — Interface do Operador

#### Botão "Chamar Agent"

Localização: barra de ferramentas do terminal, ao lado dos botões existentes.

```
[ ↻ Reconectar ] [ 🤖 Chamar Agent ] [ 🎨 Cores: ON ] ...
```

#### Painel do Agent (aba lateral)

Ao clicar, abre um painel dividido:

```
┌─────────────────────────┬─────────────────────────────────┐
│   Terminal SSH ativo    │   🤖 Agent NOC Tomich            │
│                         │                                  │
│  router> show int brief │  ─── Contexto Ativo ───          │
│  Gi0/0  up/up           │  Cliente: ISP Exemplo            │
│  Gi0/1  down/down       │  Host: 192.168.1.1 (Cisco)       │
│  ...                    │  Sessão: #1234                   │
│                         │                                  │
│                         │  [operador]: Gi0/1 está down.    │
│                         │  Pode verificar o que aconteceu? │
│                         │                                  │
│                         │  [agent]: Vou verificar os logs  │
│                         │  da interface. Executando:       │
│                         │  `show log | inc Gi0/1`          │
│                         │  ⏳ aguardando aprovação...      │
│                         │                                  │
│                         │  [✓ Aprovar]  [✗ Rejeitar]       │
│                         │                                  │
│                         │  [agent]: Encontrei o evento:    │
│                         │  "Line protocol down" às 14:32   │
│                         │  Causa: perda de sinal físico.   │
│                         │  Recomendo verificar o cabo ou   │
│                         │  o equipamento conectado.        │
│                         │                                  │
│                         │  ┌─────────────────────────────┐│
│                         │  │ Digite sua mensagem...    ▶  ││
│                         │  └─────────────────────────────┘│
└─────────────────────────┴─────────────────────────────────┘
```

#### Funcionalidades do painel

- **Enviar output ao agent:** botão "📋 Enviar ao Agent" em cada bloco de output do terminal
- **Modo aprovação:** toggle `Aprovação manual` / `Automático` (apenas para staff)
- **Histórico:** botão para ver todas as sessões anteriores com aquele host
- **Exportar sessão:** gera relatório da sessão (cliente, host, problema, ações, resolução)
- **Status do agent:** indicador de tokens usados na sessão e modelo ativo

---

### 3.2 WhatsApp — Interface de Campo

#### Vinculação Grupo ↔ Cliente

Na aba **Configurações → Agent NOC → WhatsApp**, o administrador:

1. Clica em **"Sincronizar Grupos"** — a plataforma consulta a Evolution API e lista todos os grupos do WhatsApp conectado
2. Para cada grupo, seleciona o cliente correspondente no CRM
3. Define o **nível de permissão** do grupo:

| Nível | Capacidades |
|---|---|
| `leitura` | Apenas consultas (show, get, display) |
| `operacional` | Leitura + comandos operacionais pré-aprovados |
| `admin` | Leitura + operacional + aprovação inline via WhatsApp |

4. Define quais **hosts** do cliente o grupo pode acessar (todos ou lista restrita)

#### Como o agent é invocado no WhatsApp

O agent responde quando mencionado com o prefixo configurado (padrão: `@noc`):

```
Técnico: @noc a OLT da central norte está com alarme, consegue verificar?

Agent NOC: Verificando a OLT 10.20.1.5 (ZTE C320)...

  Executando: show pon onu state gpon-onu_1/3/1
  ─────────────────────────────────────────
  ONU 1: ONLINE  SN: ZTEG12345678
  ONU 2: ONLINE  SN: ZTEG87654321
  ONU 3: LOS     SN: ZTEG11223344  ← ALARME
  ─────────────────────────────────────────

  ⚠️ ONU 3 está com LOS (Loss of Signal).
  SN: ZTEG11223344

  Possíveis causas:
  • Fibra rompida ou dobrada
  • Conector sujo na ONU ou splitter
  • ONU sem energia

  Deseja que eu verifique o histórico de eventos dessa ONU?
```

#### Comandos especiais via WhatsApp

| Comando | Ação |
|---|---|
| `@noc status` | Resumo geral: hosts online/offline, último backup, alarmes |
| `@noc conectar <host>` | Abre sessão interativa (modo admin) |
| `@noc histórico <host>` | Últimas 10 execuções de script naquele host |
| `@noc backup <host>` | Solicita backup imediato |
| `@noc escalar` | Notifica o NOC humano com o contexto da conversa |
| `@noc ajuda` | Lista comandos disponíveis para o nível do grupo |

#### Sessão interativa via WhatsApp (nível admin)

Quando um operador usa `@noc conectar <host>`, o agent entra em modo de sessão:

```
Agent NOC: ✅ Sessão aberta com 10.20.1.5 (ZTE C320)
  Modo: Interativo — nível admin
  Timeout: 15 min de inatividade
  
  Você pode digitar comandos diretamente ou
  me pedir para fazer algo. Digite `sair` para encerrar.

Técnico: show version

Agent NOC: Executando `show version`...

  ZTE Corporation
  C320 version: V2.1.10P1T4
  Uptime: 47 days, 3 hours
  [output completo]
```

---

### 3.3 Motor do Agent (Claude / OpenAI)

O motor suporta dois provedores de IA, selecionável nas configurações sem necessidade de reiniciar a aplicação.

| Provedor | Módulo | Formato de tool call |
|---|---|---|
| **Claude (Anthropic)** | `anthropic.Anthropic` | `input_schema` + `role: user / tool_result` |
| **ChatGPT (OpenAI)** | `openai.OpenAI` | `parameters` + `role: tool` |

A seleção ativa é lida de `AgentConfig.provedor_ia` no início de cada `processar_mensagem`.

#### Modelos recomendados

- **Claude:** `claude-sonnet-4-6` — equilíbrio entre raciocínio técnico e custo por token. Configurável no painel.
- **OpenAI:** `gpt-4o` — padrão; `gpt-4-turbo` e `gpt-4o-mini` também disponíveis no seletor.

#### System Prompt dinâmico

O system prompt é montado dinamicamente a cada sessão, injetando:

```
[IDENTIDADE]
Você é o Agent NOC Tomich, assistente técnico especializado em redes de provedores de internet (ISP).
Você está integrado ao CRM Tomich e tem acesso aos equipamentos do cliente {CLIENTE_NOME}.

[CONTEXTO DO CLIENTE]
- CNPJ: {CNPJ}
- Hosts disponíveis: {LISTA_HOSTS}
- Última sincronização de backup: {DATA_ULTIMO_BACKUP}
- Topologia: {RESUMO_TOPOLOGIA}
- Notas do cliente: {NOTAS}

[RESTRIÇÃO DE SEGURANÇA]
Você SOMENTE pode acessar os hosts listados acima.
NUNCA acesse, mencione ou interaja com dados de outros clientes.
Qualquer tentativa de acessar hosts fora desta lista deve ser recusada.

[MODO DE OPERAÇÃO]
Canal: {TERMINAL | WHATSAPP}
Nível de permissão: {leitura | operacional | admin}
Aprovação manual: {ativada | desativada}

[BASE DE CONHECIMENTO RELEVANTE]
{KNOWLEDGE_CHUNKS_RELEVANTES}

[REGRAS DE COMPORTAMENTO]
- Sempre explique o que vai fazer ANTES de executar
- Para comandos de escrita/configuração: aguarde aprovação
- Responda em português brasileiro
- Seja objetivo: problema → diagnóstico → ação → resultado
- Se não souber, diga que não sabe e sugira escalar para o NOC
```

#### Tools disponíveis (Function Calling)

```python
tools = [
    {
        "name": "execute_command",
        "description": "Executa um comando no equipamento via SSH ou Telnet",
        "input_schema": {
            "type": "object",
            "properties": {
                "acesso_id": {"type": "integer", "description": "ID do acesso no CRM"},
                "command": {"type": "string", "description": "Comando a executar"},
                "requires_approval": {"type": "boolean", "description": "True para comandos de escrita/configuração"}
            },
            "required": ["acesso_id", "command"]
        }
    },
    {
        "name": "get_client_info",
        "description": "Retorna informações do cliente: acessos, backups recentes, alarmes, topologia",
        "input_schema": {
            "type": "object",
            "properties": {
                "include_hosts": {"type": "boolean"},
                "include_backups": {"type": "boolean"},
                "include_topology": {"type": "boolean"}
            }
        }
    },
    {
        "name": "list_hosts",
        "description": "Lista todos os hosts do cliente com status de conectividade",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter_tipo": {"type": "string", "description": "Filtrar por tipo: SSH, Telnet, HTTP, Winbox"}
            }
        }
    },
    {
        "name": "search_knowledge",
        "description": "Busca na base de conhecimento por fabricante, sintoma ou comando",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "fabricante": {"type": "string"},
                "categoria": {"type": "string"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "escalate_to_noc",
        "description": "Escala o incidente para um operador humano via WhatsApp ou notificação interna",
        "input_schema": {
            "type": "object",
            "properties": {
                "resumo": {"type": "string", "description": "Resumo do problema e o que foi tentado"},
                "urgencia": {"type": "string", "enum": ["baixa", "media", "alta", "critica"]}
            },
            "required": ["resumo", "urgencia"]
        }
    },
    {
        "name": "get_command_history",
        "description": "Retorna histórico de comandos executados neste host nas últimas N horas",
        "input_schema": {
            "type": "object",
            "properties": {
                "acesso_id": {"type": "integer"},
                "horas": {"type": "integer", "default": 24}
            },
            "required": ["acesso_id"]
        }
    }
]
```

#### Tools — formato OpenAI (Function Calling)

O mesmo conjunto de 6 tools é reexportado no formato OpenAI em `TOOLS_OPENAI`:

```python
TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Executa um comando no equipamento via SSH ou Telnet",
            "parameters": {
                "type": "object",
                "properties": {
                    "acesso_id": {"type": "integer"},
                    "command": {"type": "string"},
                    "requires_approval": {"type": "boolean"}
                },
                "required": ["acesso_id", "command"]
            }
        }
    },
    # … demais tools com mesma estrutura
]
```

Diferenças de implementação entre os loops:

| Aspecto | Claude | OpenAI |
|---|---|---|
| Detectar tool call | `response.stop_reason == 'tool_use'` | `choice.finish_reason == 'tool_calls'` |
| Extrair tool + args | `block.type == 'tool_use'` → `block.input` | `msg.tool_calls[i]` → `json.loads(call.function.arguments)` |
| Enviar resultado | `role: "user"`, content `type: "tool_result"` | `role: "tool"`, `tool_call_id: call.id` |
| Extrair texto final | Bloco `type == 'text'` | `choice.message.content` |

#### Gestão de contexto e tokens

- **Janela de contexto:** mantém as últimas 20 trocas da sessão + system prompt
- **Resumo automático:** quando a sessão ultrapassa 80% do limite de tokens, o agent resume automaticamente as trocas anteriores e comprime o histórico
- **Memória de sessão WhatsApp:** por grupo, persiste entre mensagens (expiração configurável, padrão: 2 horas de inatividade)

---

### 3.4 Base de Conhecimento

A base de conhecimento é editável pelos operadores diretamente no sistema e é consultada dinamicamente pelo agent via tool `search_knowledge`.

**Acesso:** Menu Ferramentas → Agent NOC → Base de Conhecimento

A página usa duas abas:

- **Artigos** — artigos Markdown criados/editados inline no formulário
- **Documentos** — PDFs/TXTs carregados por upload; texto extraído automaticamente e indexado

#### Upload de documentos PDF/TXT

Operadores podem enviar arquivos de documentação (manuais de equipamentos, procedures internas) e o sistema extrai o texto automaticamente para uso na busca do agent.

**Fluxo:**

```
Operador faz upload de arquivo PDF
        │
        ▼
View agent_knowledge_doc_upload (POST /agent/knowledge/docs/upload/)
        │
        ▼
PyMuPDF (fitz) extrai texto de todas as páginas
        │
        ▼
AgentKnowledgeDoc salvo: nome_arquivo, texto_extraido, tamanho_bytes
        │
        ▼
Artigo AgentKnowledge criado automaticamente:
  titulo  = nome do arquivo
  conteudo = texto extraído
  categoria = 'geral'
  fabricante = 'generico'
        │
        ▼
Exibido na aba "Documentos" com botão excluir
```

**Exclusão:** `DELETE /agent/knowledge/docs/<id>/deletar/` remove tanto o `AgentKnowledgeDoc` quanto o `AgentKnowledge` vinculado e o arquivo físico se existir.

#### Tipos de conhecimento

| Tipo | Uso pelo agent | Exemplo |
|---|---|---|
| `comando` | Referência de sintaxe por fabricante | `show pon onu state` ZTE |
| `procedure` | Passo a passo de tarefas comuns | "Como provisionar ONU na ZTE C320" |
| `troubleshooting` | Diagnóstico por sintoma | "OLT com LOS em massa → causas e verificações" |
| `topologia` | Descrição da rede do cliente | "Central Norte: OLT ZTE C320 — 48 PONs — splits 1:32" |
| `equipamento` | Características de modelos | "ZTE C320: 16 slots, 48 PONs, GPON" |
| `alarme` | Interpretação de mensagens de erro | "`GPON_ONU_DYINGGASP`: ONU perdeu energia" |

#### Estrutura de um artigo de conhecimento

```markdown
---
titulo: ZTE — Autorizar ONU manualmente
fabricante: zte
categoria: procedure
tags: [onu, gpon, provisionamento, zte]
severidade: normal
---

## Problema
ONU conectada na PON mas não autorizada no sistema.

## Diagnóstico
```
show pon onu un sn gpon-onu_X/Y/Z
```
Lista ONUs aguardando autorização com seus serial numbers.

## Solução passo a passo
1. Entrar no modo de configuração da interface PON:
```
configure terminal
interface gpon-onu_X/Y/Z:N
```
2. Definir tipo e nome:
```
onu-type {TIPO_ONU}
name {NOME_ONU}
```
3. Configurar VLAN e tráfego:
```
vport-mode manual
tcont 1 name 1g profile {PROFILE}
gemport 1 name 1g tcont 1
service-port 1 vport 1 user-vlan {VLAN} vlan {VLAN}
```
4. Verificar provisionamento:
```
show gpon onu state gpon-onu_X/Y/Z
```
ONU deve aparecer com estado `online`.

## Notas
- Substituir X/Y/Z pelo slot/subslot/porta da PON
- N é o número da ONU (1–128)
- TIPO_ONU: F601, F660, F670L, etc.
```

---

## 4. Modelos de Dados

### `AgentConfig` — Configuração global do agent (singleton)

```python
class AgentConfig(models.Model):
    # ── Seleção de provedor ──────────────────────────────────────────────────
    PROVEDORES = [
        ('claude', 'Claude (Anthropic)'),
        ('openai', 'ChatGPT (OpenAI)'),
    ]
    provedor_ia         = CharField(max_length=20, choices=PROVEDORES, default='claude')

    # ── Claude (Anthropic) ──────────────────────────────────────────────────
    claude_api_key      = CharField(max_length=200)
    claude_model        = CharField(max_length=100, default='claude-sonnet-4-6')
    claude_max_tokens   = IntegerField(default=4096)
    claude_temperature  = FloatField(default=0.2)  # baixo = mais preciso/determinístico

    # ── OpenAI (ChatGPT) ─────────────────────────────────────────────────────
    openai_api_key      = CharField(max_length=300, blank=True)
    openai_model        = CharField(max_length=100, default='gpt-4o')
    openai_max_tokens   = IntegerField(default=4096)
    openai_temperature  = FloatField(default=0.2)

    # ── Comportamento ────────────────────────────────────────────────────────
    aprovacao_padrao    = BooleanField(default=True)   # exige aprovação para comandos de escrita
    timeout_sessao_wa   = IntegerField(default=120)    # minutos de inatividade para expirar sessão WA
    prefixo_wa          = CharField(max_length=20, default='@noc')  # prefixo de invocação no WA
    max_comandos_sessao = IntegerField(default=50)     # limite de comandos por sessão

    # ── Notificação de escalonamento ─────────────────────────────────────────
    wa_grupo_noc        = CharField(max_length=100, blank=True)  # JID do grupo interno do NOC
    wa_noc_numero       = CharField(max_length=30, blank=True)   # Número direto do plantão

    ativo               = BooleanField(default=True)
    atualizado_em       = DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração do Agent NOC'
```

> **Migration:** `0059_agentconfig_openai.py` — adiciona `provedor_ia`, `openai_api_key`, `openai_model`, `openai_max_tokens`, `openai_temperature`.

### `EvolutionAPIConfig` — Conexão com Evolution API (singleton)

```python
class EvolutionAPIConfig(models.Model):
    url             = CharField(max_length=300)     # ex: https://evo.empresa.com.br
    api_key         = CharField(max_length=200)
    instance_name   = CharField(max_length=100)     # nome da instância no Evolution
    webhook_secret  = CharField(max_length=200, blank=True)  # para validar os POSTs recebidos

    # Status (preenchido automaticamente na sincronização)
    conectado       = BooleanField(default=False)
    numero_wa       = CharField(max_length=30, blank=True)   # número conectado
    ultima_sync     = DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Configuração Evolution API'
```

### `WhatsAppGrupo` — Grupos sincronizados e vinculados

```python
class WhatsAppGrupo(models.Model):
    NIVEIS = [
        ('leitura',      'Leitura (apenas show/display)'),
        ('operacional',  'Operacional (comandos pré-aprovados)'),
        ('admin',        'Admin (aprovação inline)'),
    ]

    jid             = CharField(max_length=100, unique=True)  # ID único no WhatsApp
    nome            = CharField(max_length=300)
    cliente         = ForeignKey('Cliente', null=True, blank=True, on_delete=SET_NULL,
                                 related_name='wa_grupos')
    nivel_permissao = CharField(max_length=20, choices=NIVEIS, default='leitura')
    hosts_permitidos = ManyToManyField('Acesso', blank=True,
                                       help_text='Vazio = todos os hosts do cliente')
    ativo           = BooleanField(default=True)
    criado_em       = DateTimeField(auto_now_add=True)
    sincronizado_em = DateTimeField(auto_now=True)

    def pode_acessar(self, acesso):
        """Verifica se este grupo pode solicitar acesso a um Acesso específico."""
        if not self.cliente or acesso.cliente != self.cliente:
            return False
        if self.hosts_permitidos.exists():
            return self.hosts_permitidos.filter(id=acesso.id).exists()
        return True

    class Meta:
        verbose_name = 'Grupo WhatsApp'
        verbose_name_plural = 'Grupos WhatsApp'
        ordering = ['nome']
```

### `AgentSessao` — Sessão de conversa

```python
class AgentSessao(models.Model):
    CANAIS = [('terminal', 'Terminal Web'), ('whatsapp', 'WhatsApp')]
    STATUS = [
        ('ativa',     'Ativa'),
        ('encerrada', 'Encerrada'),
        ('expirada',  'Expirada por inatividade'),
        ('escalada',  'Escalada para NOC humano'),
    ]

    canal           = CharField(max_length=20, choices=CANAIS)
    cliente         = ForeignKey('Cliente', on_delete=CASCADE, related_name='agent_sessoes')
    usuario         = ForeignKey(User, null=True, blank=True, on_delete=SET_NULL)  # terminal
    wa_grupo        = ForeignKey(WhatsAppGrupo, null=True, blank=True, on_delete=SET_NULL)
    wa_remetente    = CharField(max_length=50, blank=True)  # número que iniciou no WA
    acesso_ativo    = ForeignKey('Acesso', null=True, blank=True, on_delete=SET_NULL,
                                 help_text='Host sendo acessado no momento')

    historico       = JSONField(default=list)   # lista de {role, content, timestamp}
    tokens_usados   = IntegerField(default=0)
    status          = CharField(max_length=20, choices=STATUS, default='ativa')

    iniciada_em     = DateTimeField(auto_now_add=True)
    ultima_atividade = DateTimeField(auto_now=True)
    encerrada_em    = DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Sessão do Agent'
        ordering = ['-iniciada_em']
```

### `AgentLog` — Log de ações e comandos

```python
class AgentLog(models.Model):
    TIPOS = [
        ('mensagem',   'Mensagem do usuário'),
        ('resposta',   'Resposta do agent'),
        ('comando',    'Comando executado'),
        ('aprovacao',  'Aprovação/rejeição de comando'),
        ('tool_call',  'Chamada de tool interna'),
        ('escalamento', 'Escalonamento para NOC'),
        ('erro',       'Erro'),
    ]

    sessao          = ForeignKey(AgentSessao, on_delete=CASCADE, related_name='logs')
    tipo            = CharField(max_length=20, choices=TIPOS)
    conteudo        = TextField()           # texto da mensagem, comando, output, etc.
    acesso          = ForeignKey('Acesso', null=True, blank=True, on_delete=SET_NULL)
    aprovado        = BooleanField(null=True)  # None=pendente, True=aprovado, False=rejeitado
    tokens_entrada  = IntegerField(default=0)
    tokens_saida    = IntegerField(default=0)
    latencia_ms     = IntegerField(default=0)  # tempo de resposta da API Claude
    criado_em       = DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Log do Agent'
        ordering = ['criado_em']
```

### `AgentKnowledge` — Base de conhecimento

```python
class AgentKnowledge(models.Model):
    CATEGORIAS = [
        ('comando',         'Referência de Comandos'),
        ('procedure',       'Procedimento Operacional'),
        ('troubleshooting', 'Troubleshooting'),
        ('topologia',       'Topologia/Infraestrutura'),
        ('equipamento',     'Equipamento/Modelo'),
        ('alarme',          'Interpretação de Alarme'),
        ('geral',           'Geral'),
    ]
    FABRICANTES = [
        ('zte', 'ZTE'), ('huawei', 'Huawei'), ('cisco', 'Cisco'),
        ('mikrotik', 'MikroTik'), ('datacom', 'Datacom'), ('generico', 'Genérico'),
    ]

    titulo          = CharField(max_length=300)
    conteudo        = TextField(help_text='Markdown. Use blocos de código para comandos.')
    categoria       = CharField(max_length=30, choices=CATEGORIAS)
    fabricante      = CharField(max_length=30, choices=FABRICANTES, default='generico')
    tags            = JSONField(default=list)    # ['onu', 'gpon', 'los']
    cliente         = ForeignKey('Cliente', null=True, blank=True, on_delete=SET_NULL,
                                 help_text='Vazio = conhecimento global; preenchido = específico do cliente')
    ativo           = BooleanField(default=True)
    criado_por      = ForeignKey(User, null=True, on_delete=SET_NULL, related_name='+')
    criado_em       = DateTimeField(auto_now_add=True)
    atualizado_em   = DateTimeField(auto_now=True)
    uso_count       = IntegerField(default=0)  # quantas vezes foi consultado pelo agent

    class Meta:
        verbose_name = 'Base de Conhecimento'
        verbose_name_plural = 'Base de Conhecimento'
        ordering = ['-uso_count', 'fabricante', 'titulo']
```

### `AgentKnowledgeDoc` — Documentos enviados por upload

```python
class AgentKnowledgeDoc(models.Model):
    """PDF ou TXT carregado por upload. O texto é extraído com PyMuPDF e
    um AgentKnowledge correspondente é criado automaticamente."""

    nome_arquivo    = CharField(max_length=300)
    arquivo         = FileField(upload_to='agent_knowledge_docs/', blank=True)
    texto_extraido  = TextField(blank=True)
    tamanho_bytes   = IntegerField(default=0)
    artigo          = OneToOneField(
                          AgentKnowledge,
                          null=True, blank=True,
                          on_delete=SET_NULL,
                          related_name='documento',
                      )
    criado_por      = ForeignKey(User, null=True, on_delete=SET_NULL, related_name='+')
    criado_em       = DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Documento da Base de Conhecimento'
        ordering = ['-criado_em']
```

> **Migration:** `0058_agent_knowledge_doc.py` — cria a tabela `clientes_agentknowledgedoc`.

---

## 5. Configuração do Sistema

### Painel: Sistema → Configurações → Agent NOC

O painel de configuração é expandido com três seções:

#### Seção: Provedor de IA Ativo

Seletor visual com dois cards clicáveis. A seleção é salva imediatamente via `POST` com `secao=provedor`.

```
┌─────────────────────────────────────────────────────┐
│  🧠 Provedor de IA Ativo                            │
│                                                     │
│  ┌──────────────────┐  ┌──────────────────┐         │
│  │  ✅ Claude       │  │     ChatGPT       │         │
│  │  (Anthropic)     │  │     (OpenAI)      │         │
│  │  [ATIVO]         │  │                  │         │
│  └──────────────────┘  └──────────────────┘         │
│                                                     │
│  Clique no provedor desejado para ativar            │
└─────────────────────────────────────────────────────┘
```

#### Seção: Claude AI

```
┌─────────────────────────────────────────────────────┐
│  🤖 Claude AI (Anthropic)                           │
│                                                     │
│  API Key:    [sk-ant-••••••••••••••••••••] [Testar] │
│  Modelo:     [claude-sonnet-4-6         ▼]          │
│  Max Tokens: [4096   ]                              │
│  Temperature:[0.2    ] (0=determinístico, 1=criativo)│
│                                                     │
│  Aprovação manual por padrão:  [✓]                  │
│  Timeout sessão WhatsApp:      [120] minutos         │
│  Prefixo de invocação WA:      [@noc]               │
│  Máx. comandos por sessão:     [50]                 │
│                                                     │
│  Grupo NOC (escalonamento):    [jid do grupo...]    │
│                                                     │
│  Status: ● Conectado  Modelo: claude-sonnet-4-6     │
└─────────────────────────────────────────────────────┘
```

#### Seção: ChatGPT (OpenAI)

```
┌─────────────────────────────────────────────────────┐
│  🤖 ChatGPT (OpenAI)                                │
│                                                     │
│  API Key:    [sk-••••••••••••••••••••••••] [Testar] │
│  Modelo:     [gpt-4o             ▼]                  │
│              gpt-4o / gpt-4-turbo / gpt-4o-mini     │
│  Max Tokens: [4096   ]                              │
│  Temperature:[0.2    ]                              │
│                                                     │
│  Status: ● Conectado                               │
└─────────────────────────────────────────────────────┘
```

Endpoint de teste: `POST /home/agent/config/testar-openai/`
Retorna `{"ok": true, "resposta": "...", "modelo": "gpt-4o"}`.

#### Seção: Evolution API (WhatsApp)

```
┌─────────────────────────────────────────────────────┐
│  💬 Evolution API — WhatsApp                        │
│                                                     │
│  URL da API: [https://evo.empresa.com.br]           │
│  API Key:    [B6C8A9F2••••••••••••••••] [Testar]    │
│  Instância:  [tomich-noc]                           │
│  Webhook Secret: [••••••••••••••••••••]             │
│                                                     │
│  Status: ● Conectado  Número: +55 11 99999-9999     │
│  Última sync: 18/05/2026 às 14:32                   │
│                                                     │
│  [↻ Sincronizar Grupos]  [Ver Grupos Vinculados]    │
└─────────────────────────────────────────────────────┘
```

#### Sub-página: Gerenciar Grupos WhatsApp

`/home/configuracoes/agent/grupos/`

```
┌───────────────────────────────────────────────────────────────────┐
│  Grupos WhatsApp — 12 grupos sincronizados                        │
│                              [↻ Sincronizar]  [● Todos Ativos]    │
├─────────────────────────────┬──────────────┬──────────┬──────────┤
│  Nome do Grupo              │  Cliente     │  Nível   │  Ações   │
├─────────────────────────────┼──────────────┼──────────┼──────────┤
│  NOC - ISP Exemplo          │  ISP Exemplo │  admin   │ [✎] [⊘]  │
│  Suporte Técnico - Cliente2 │  Cliente 2   │ operac.  │ [✎] [⊘]  │
│  Grupo Não Vinculado        │  — vincular — │  —       │ [✎]      │
│  ...                        │  ...         │  ...     │ ...      │
└─────────────────────────────┴──────────────┴──────────┴──────────┘
```

---

## 6. Segurança e Permissões

### Isolamento de clientes (regra principal)

O isolamento é garantido em múltiplas camadas:

1. **Model layer:** `WhatsAppGrupo.pode_acessar(acesso)` valida que o acesso pertence ao cliente vinculado ao grupo antes de qualquer execução
2. **Engine layer:** `AgentNOCEngine` recebe `cliente_id` no construtor e filtra `Acesso.objects.filter(cliente_id=self.cliente_id)` em todas as queries
3. **System prompt:** a lista de hosts do cliente é injetada no system prompt; hosts de outros clientes nunca aparecem no contexto
4. **Tool layer:** `execute_command` valida `acesso_id` contra `Acesso.objects.filter(cliente=self.cliente)` antes de executar — se não pertencer ao cliente, retorna erro e registra tentativa em log

### Lista de comandos seguros (execução sem aprovação)

Comandos que podem ser executados automaticamente no canal WhatsApp (nível `leitura` e `operacional`):

```python
SAFE_COMMANDS = {
    'zte': [
        r'^show\s+', r'^display\s+', r'^ping\s+',
        r'^show pon onu state', r'^show gpon onu detail-info',
        r'^show log\s*$', r'^show version\s*$',
    ],
    'huawei': [
        r'^display\s+', r'^ping\s+', r'^tracert\s+',
        r'^display ont info\s+', r'^display alarm\s*$',
    ],
    'cisco': [
        r'^show\s+', r'^ping\s+', r'^traceroute\s+',
        r'^debug\s+' ,  # apenas para admin
    ],
    'mikrotik': [
        r'^/interface print', r'^/ip address print',
        r'^/ip route print', r'^/log print',
        r'^/ping\s+', r'^/tool traceroute\s+',
    ],
    'generico': [
        r'^show\s+', r'^display\s+', r'^get\s+',
        r'^ping\s+',
    ],
}

BLOCKED_COMMANDS = [
    r'reboot', r'reload', r'reset', r'erase',
    r'delete\s', r'no\s+interface', r'shutdown\s*$',
    r'rm\s+-', r'format\s+', r'factory',
]
```

### Rate limiting

| Canal | Limite |
|---|---|
| WhatsApp por grupo | 30 mensagens / 10 minutos |
| Terminal por usuário | 100 mensagens / hora |
| Comandos SSH por sessão | 50 (configurável) |
| Chamadas API Claude por hora | 200 (global) |

### Audit log

Toda ação do agent é registrada em `AgentLog` com:
- Sessão, canal, cliente, acesso alvo
- Conteúdo do comando e output
- Aprovado/rejeitado (e por quem)
- Timestamp e latência

O audit log é visível em **Sistema → Configurações → Agent NOC → Logs** com filtros por cliente, data, canal e tipo de ação.

---

## 7. Fluxos de Uso Detalhados

### Fluxo 1: Operador NOC diagnostica interface down

```
1. Operador está com terminal SSH aberto em um Cisco de um cliente
2. Clica em "🤖 Chamar Agent"
3. Painel do agent abre ao lado do terminal
4. Operador escreve: "A Gi0/1 está down, preciso saber o que aconteceu e se devo reativar"
5. Agent lê o contexto: cliente X, host Cisco 192.168.1.1
6. Agent responde: "Vou verificar os logs da interface. Executando: show log | inc Gi0/1"
7. [MODO APROVAÇÃO] Operador clica "✓ Aprovar"
8. Agent executa via SSHConsumer, captura output
9. Agent analisa: "Interface caiu às 14:32 por 'Line protocol down — hardware failure'"
10. Agent sugere: "Recomendo executar 'show interface Gi0/1' para ver contadores de erro"
11. Operador aprova, agent executa, analisa 1.2M erros de CRC
12. Agent conclui: "Alta taxa de CRC indica problema físico. Não recomendo reativar sem trocar o cabo ou o transceiver primeiro."
13. Operador exporta relatório da sessão como registro do incidente
```

### Fluxo 2: Técnico de campo via WhatsApp

```
1. Técnico envia no grupo "NOC - ISP Exemplo": "@noc OLT da Central Sul com alarme"
2. Sistema valida: grupo vinculado ao cliente ISP Exemplo, nível operacional
3. Agent carrega contexto: 3 OLTs do cliente, topologia da Central Sul
4. Agent responde automaticamente com status de todas as OLTs do cliente
5. Técnico: "@noc verifica a 10.10.5.1"
6. Agent executa show alarm (comando seguro, sem aprovação) na OLT ZTE 10.10.5.1
7. Agent reporta: "2 alarmes ativos — LOS na PON 1/3/4 e temperatura elevada no slot 3"
8. Técnico: "@noc quantas ONUs com LOS na 1/3/4?"
9. Agent executa show pon onu state, conta e responde: "7 ONUs com LOS"
10. Técnico: "Fibra rompida confirmado. @noc pode fazer backup da config antes de eu ir lá?"
11. Agent: "Solicitação de backup requer aprovação. Isso envolve escrita no sistema de backup."
    [MODO ADMIN] "Confirme com 'sim' para prosseguir."
12. Técnico: "sim"
13. Agent dispara task Celery de backup imediato, confirma quando concluído
```

### Fluxo 3: Escalonamento para NOC humano

```
1. Agent tenta 3 abordagens para resolver problema de BGP
2. Nenhuma resolveu — problema persiste
3. Agent: "Não consegui identificar a causa raiz. Vou escalar para o NOC."
4. Agent chama tool escalate_to_noc com resumo e urgência="alta"
5. Sistema envia mensagem no grupo do NOC (wa_grupo_noc):
   "🚨 ESCALONAMENTO — Cliente: ISP Exemplo
    Host: 10.10.5.1 (Cisco ASR)
    Problema: BGP session down — PEER 203.0.113.1
    Tentativas: [resumo das 3 abordagens]
    Solicitante: João (WhatsApp)
    [Ver sessão completa: https://crm.empresa.com.br/agent/sessao/1234/]"
6. NOC humano assume e continua no terminal web com todo o histórico da sessão
```

---

## 8. Endpoints e WebSockets

### WebSocket

```
ws://<host>/ws/agent/<sessao_id>/
```

Mensagens do cliente → servidor:

```json
{"type": "message",  "content": "Verifique a interface Gi0/1"}
{"type": "approve",  "log_id": 42}
{"type": "reject",   "log_id": 42, "motivo": "Não executar agora"}
{"type": "context",  "terminal_output": "<output selecionado>"}
{"type": "close"}
```

Mensagens do servidor → cliente:

```json
{"type": "agent_message",  "content": "Vou verificar...", "sessao_id": 1}
{"type": "tool_call",      "tool": "execute_command", "command": "show int Gi0/1", "requires_approval": true, "log_id": 42}
{"type": "tool_result",    "log_id": 42, "output": "...", "approved": true}
{"type": "thinking",       "content": "Analisando output..."}
{"type": "tokens",         "used": 1250, "remaining": 2846}
{"type": "error",          "message": "..."}
```

### HTTP Endpoints

```
# Configuração
GET/POST  /home/configuracoes/agent/              — config Claude + Evolution
GET       /home/configuracoes/agent/grupos/       — listar grupos WA
POST      /home/configuracoes/agent/grupos/sync/  — sincronizar grupos do Evolution
POST      /home/configuracoes/agent/grupos/<id>/  — salvar vínculo grupo ↔ cliente
POST      /home/agent/config/testar-claude/        — testa a API key Claude
POST      /home/agent/config/testar-openai/        — testa a API key OpenAI
POST      /home/agent/config/testar-evolution/     — testa conexão Evolution API

# Webhook Evolution API (recebe mensagens WA)
POST      /agent/wa/webhook/

# Sessões e logs
GET       /agent/sessoes/                         — listar sessões (staff)
GET       /agent/sessoes/<id>/                    — detalhes + histórico
DELETE    /agent/sessoes/<id>/                    — encerrar sessão

# Base de Conhecimento — Artigos
GET       /home/agent/knowledge/                  — listar artigos + documentos (duas abas)
POST      /home/agent/knowledge/salvar/           — criar artigo
POST      /home/agent/knowledge/<id>/salvar/      — editar artigo
POST      /home/agent/knowledge/<id>/deletar/     — remover artigo
GET       /home/agent/knowledge/<id>/dados/       — retorna JSON do artigo (para edição inline)

# Base de Conhecimento — Documentos PDF/TXT
POST      /home/agent/knowledge/docs/upload/      — upload de arquivo (extrai texto via PyMuPDF)
POST      /home/agent/knowledge/docs/<id>/deletar/ — remover documento e artigo vinculado

# Logs
GET       /agent/logs/                            — audit log com filtros
```

---

## 9. Base de Conhecimento — Estrutura de Conteúdo

Esta seção documenta o conteúdo que DEVE ser cadastrado na base de conhecimento para que o agent funcione efetivamente. É também a referência usada para alimentar o agent com contexto.

### 9.1 Comandos ZTE (GPON)

```
Fabricante: zte | Categoria: comando

# Listar ONUs da PON
show pon onu state gpon-onu_{slot}/{subslot}/{porta}
show gpon onu detail-info gpon-onu_{slot}/{subslot}/{porta}:{onu_id}

# ONUs não autorizadas
show pon onu un sn gpon-onu_{slot}/{subslot}/{porta}

# Alarmes
show alarm active all
show alarm history slot {slot}

# Temperatura e hardware
show temperature
show card

# Tráfego
show interface gpon-onu_{slot}/{subslot}/{porta}:{onu_id} counters

# VLAN e serviços
show vlan brief
show service-port {id}
```

### 9.2 Comandos Huawei (OLT MA5800/MA5600)

```
Fabricante: huawei | Categoria: comando

# ONUs por PON
display ont info {frame}/{slot}/{port}

# Status de ONUs
display ont optical-info {frame}/{slot}/{port} {ont_id}

# Alarmes
display alarm active all

# Temperatura
display temperature all

# VLAN
display vlan all
display service-port port {frame}/{slot}/{port}

# Histórico de eventos ONU
display ont alarm-state {frame}/{slot}/{port} {ont_id}
```

### 9.3 Interpretação de Alarmes Comuns

```
Fabricante: generico | Categoria: alarme

LOS (Loss of Signal):
  Causa: perda de sinal óptico na ONU
  Verificar: fibra, conector, potência óptica, status da ONU

DYINGGASP:
  Causa: ONU perdeu energia e enviou sinal de encerramento
  Verificar: queda de energia no local do cliente

LOF (Loss of Frame):
  Causa: problema na sincronização do frame GPON
  Verificar: potência óptica limítrofe, degradação de fibra

LOFI (Loss of Physical Layer):
  Causa: falha física na camada óptica
  Verificar: dano físico, conector oxidado, fibra com curvatura

SD (Signal Degraded):
  Causa: sinal dentro do limiar mas degradado
  Verificar: limpeza de conectores, verificar splitter
```

### 9.4 Troubleshooting: BGP Session Down (Cisco)

```
Fabricante: cisco | Categoria: troubleshooting

## Sintoma
BGP session down ou "Idle" para um peer

## Diagnóstico passo a passo

1. Verificar estado atual:
   show bgp ipv4 unicast summary | inc {IP_PEER}

2. Verificar conectividade L3:
   ping {IP_PEER} source {IP_LOCAL} repeat 100

3. Verificar logs de BGP:
   show log | inc BGP|{IP_PEER}

4. Verificar política de rota:
   show ip bgp neighbors {IP_PEER} | inc policy|prefix

5. Verificar se porta 179 está acessível:
   telnet {IP_PEER} 179

## Causas comuns e soluções
- Rota para o peer ausente → verificar tabela de roteamento
- Senha MD5 diferente → conferir com o peer
- AS-path incorreto → verificar configuração do neighbor
- Prefix limit atingido → show ip bgp neighbors {IP_PEER} | inc prefixes
```

### 9.5 Procedimento: Reset de ONU ZTE

```
Fabricante: zte | Categoria: procedure

## Quando usar
ONU está com estado "auth_fail", "config_fail" ou travada após atualização.

## Passo a passo

1. Localizar a ONU na PON:
   show gpon onu detail-info gpon-onu_{X}/{Y}/{Z}:{N}

2. Remover a ONU (sem apagar config):
   configure terminal
   interface gpon-onu_{X}/{Y}/{Z}:{N}
   shutdown

3. Aguardar 10 segundos

4. Reativar:
   no shutdown
   exit

5. Verificar novo estado:
   show gpon onu state gpon-onu_{X}/{Y}/{Z}:{N}

## Alternativa: reinicialização remota
   onu reboot gpon-onu_{X}/{Y}/{Z}:{N}

## Nota
Se o problema persistir após reset remoto, verificar:
- Potência óptica (show interface gpon-onu_X/Y/Z:N counters)
- Perfil TCONT e GEM ports
- VLAN de serviço
```

---

## 10. Plano de Implementação

### Fase 1 — Infraestrutura base (Backend)

- [ ] Migrations: `AgentConfig`, `EvolutionAPIConfig`, `WhatsAppGrupo`, `AgentSessao`, `AgentLog`, `AgentKnowledge`
- [ ] `AgentNOCEngine` — classe principal com Claude API client, gerenciamento de sessão, tool executor
- [ ] Tool implementations: `execute_command`, `get_client_info`, `list_hosts`, `search_knowledge`, `escalate_to_noc`
- [ ] `AgentNOCConsumer` — WebSocket consumer para o terminal
- [ ] Validação de segurança: isolamento de cliente, lista de comandos seguros, rate limiting
- [ ] Rota WebSocket `/ws/agent/<sessao_id>/`

### Fase 2 — Interface Terminal

- [ ] Botão "🤖 Chamar Agent" na toolbar do terminal (`terminal.html`)
- [ ] Painel lateral do agent: chat, aprovação de comandos, indicador de tokens
- [ ] Integração com terminal ativo: capturar output e enviar ao agent
- [ ] Histórico de sessões por host

### Fase 3 — Painel de Configuração

- [ ] Seção Claude AI em `home/templates/configuracoes_sistema.html`
- [ ] Seção Evolution API
- [ ] Sub-página de grupos WhatsApp com vincular/desvincular
- [ ] Endpoint `/home/configuracoes/agent/grupos/sync/` (integração Evolution API)

### Fase 4 — WhatsApp (Evolution API)

- [ ] Webhook `POST /agent/wa/webhook/` com validação de secret
- [ ] Parser de mensagens: detectar prefixo `@noc`, extrair intenção
- [ ] Roteamento: grupo → cliente → permissões
- [ ] Session manager para grupos (persistência entre mensagens)
- [ ] Envio de resposta via Evolution API (text + formatação)
- [ ] Modo interativo por grupo

### Fase 5 — Base de Conhecimento

- [ ] CRUD de artigos em `/agent/knowledge/`
- [ ] Editor Markdown com preview
- [ ] Sistema de busca semântica (por tags, fabricante, categoria)
- [ ] Seed inicial: comandos ZTE, Huawei, Cisco, MikroTik, alarmes comuns
- [ ] Integração com `search_knowledge` tool

### Fase 6 — Audit, Logs e Refinamentos

- [ ] Página de logs do agent com filtros
- [ ] Exportação de sessão como relatório de incidente
- [ ] Métricas: tokens usados por dia/semana, comandos mais executados, tempo médio de resolução
- [ ] Ajuste fino do system prompt com base em sessões reais
- [ ] Documentação de usuário final

---

## Dependências a instalar

```bash
pip install anthropic>=0.40.0     # Claude API SDK oficial
pip install httpx>=0.27.0          # HTTP client (já pode ser dep do anthropic)
pip install openai>=1.50.0         # OpenAI SDK (ChatGPT)
pip install pymupdf>=1.23.0        # Extração de texto de PDFs (fitz)
```

Adicionar ao `requirements.txt`:
```
anthropic>=0.40.0
openai>=1.50.0
pymupdf>=1.23.0
```

---

## Variáveis de configuração (settings.py)

Nenhuma variável hardcoded. Toda configuração é gerenciada pelo modelo `AgentConfig` (singleton) e `EvolutionAPIConfig`, editáveis no painel de administração do sistema.

---

## Notas de Design

### Por que Evolution API e não WhatsApp Cloud API direta?

A Evolution API é um wrapper self-hosted sobre o WhatsApp Web que não exige aprovação da Meta para uso corporativo interno. Permite instâncias múltiplas, tem suporte a grupos, e é amplamente usada no Brasil por ISPs para automação de suporte. A integração é via REST + Webhook, idêntica para qualquer instância.

### Por que aprovação manual por padrão?

Provedores de internet operam redes de produção. Um comando incorreto pode derrubar dezenas de clientes. O modo supervisionado garante que o operador sempre revisa o que o agent vai executar antes que aconteça, construindo confiança gradual no sistema.

### Claude vs OpenAI — como escolher

O sistema suporta ambos. A recomendação padrão é Claude, mas OpenAI está disponível para operadores que já têm chave OpenAI ou que preferem o ChatGPT.

**Por que Claude é o padrão:**
- **Janela de contexto grande:** 200K tokens permitem injetar toda a topologia do cliente e um histórico longo de sessão sem truncar
- **Function Calling confiável:** Tool use do Claude é preciso e não alucina chamadas de função
- **Raciocínio técnico:** Claude lida bem com outputs de equipamentos de rede, tabelas BGP, logs de OLT
- **Latência:** claude-sonnet-4-6 tem resposta em ~1–2s, adequado para uso interativo no terminal

**Quando usar OpenAI:**
- Operador já tem conta/créditos OpenAI e prefere centralizar custos lá
- Testes de qualidade de resposta para casos de uso específicos do cliente
- Requisito de compliance interno que exige uso de determinado provedor

**Diferença de implementação:**
Os dois provedores usam formatos de API incompatíveis (ver tabela em 3.3). O engine mantém dois loops separados (`_processar_com_claude` / `_processar_com_openai`) e um único ponto de entrada `processar_mensagem` que despacha baseado em `AgentConfig.provedor_ia`.

---

*Documento criado em: 18/05/2026 — Versão 1.0*  
*Atualizado em: 19/05/2026 — Versão 1.1 — suporte OpenAI, upload de documentos PDF, AgentKnowledgeDoc*  
*Atualizado em: 19/05/2026 — Versão 1.2 — URL Evolution API corrigida para EasyPanel; campo cliente com busca em agent_grupos*  
*Próxima revisão: após conclusão da Fase 2 de implementação*
