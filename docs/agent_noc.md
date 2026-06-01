# Agent NOC — Documentação Técnica

**Arquivos:** `home/views.py`, `home/urls.py`, `home/agent_engine.py`  
**Templates:** `home/templates/agent_config.html`, `home/templates/agent_knowledge.html`  
**Atualizado em:** 2026-05-26

---

## Visão Geral

O Agent NOC é um assistente de inteligência artificial integrado ao CRM que responde a
mensagens via WhatsApp (Evolution API) e executa ações automatizadas na infraestrutura
de rede (SSH, consultas, backups, etc.).

Suporta dois provedores de IA configuráveis:
- **Claude** (Anthropic): `claude-sonnet-4-6`, `claude-opus-4-6`, `claude-haiku-4-5`
- **OpenAI**: `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`

---

## Monitor de Tokens — Adicionado em 2026-05-26

### Endpoint

```
GET /agent/config/token-stats/?periodo=<periodo>
```

**Autenticação:** requer login + perfil administrador (`@admin_required`)

**Parâmetros:**

| Parâmetro | Valores aceitos       | Padrão |
|-----------|-----------------------|--------|
| `periodo` | `24h`, `7d`, `30d`, `all` | `7d`   |

### Resposta JSON

```json
{
  "ok": true,
  "periodo": "7d",
  "provedor": "claude",
  "modelo": "claude-sonnet-4-6",
  "tokens_input": 125000,
  "tokens_output": 43000,
  "total_tokens": 168000,
  "total_msgs": 87,
  "custo_usd": 0.0225,
  "custo_brl": 0.1316,
  "taxa_brl": 5.85,
  "por_dia": [
    {"dia": "2026-05-20", "input": 18000, "output": 6200, "msgs": 12},
    ...
  ]
}
```

O campo `por_dia` sempre cobre os últimos **14 dias** independente do período selecionado
(usado para o gráfico histórico).

### Cálculo de Custo

Os preços são aplicados por 1 milhão de tokens (valores aproximados de mercado):

| Provedor | Modelo       | Entrada (USD/1M) | Saída (USD/1M) |
|----------|--------------|------------------|----------------|
| Claude   | opus         | $15,00           | $75,00         |
| Claude   | sonnet       | $3,00            | $15,00         |
| Claude   | haiku        | $0,80            | $4,00          |
| OpenAI   | gpt-4o       | $2,50            | $10,00         |
| OpenAI   | gpt-4o-mini  | $0,15            | $0,60          |
| OpenAI   | gpt-4-turbo  | $10,00           | $30,00         |

A detecção do modelo é feita por `in` na string do nome (ex: `'opus' in modelo`).

### Cotação USD → BRL

A conversão é obtida em tempo real via [AwesomeAPI](https://economia.awesomeapi.com.br):

```
GET https://economia.awesomeapi.com.br/json/last/USD-BRL
```

Timeout de 3 segundos. Em caso de falha (API fora, timeout, etc.), utiliza taxa de fallback
**R$ 5,85**.

### Fonte dos Dados

Os dados são extraídos do model `AgentLog` com `tipo='agent_msg'`, somando os campos
`tokens_input` e `tokens_output`. O agrupamento diário usa `TruncDate` do Django ORM.

---

## Interface — Painel de Configuração

### Seção "Consumo de Tokens" (`agent_config.html`)

Localizada no início da página de configuração do Agent NOC, contém:

| Elemento          | Descrição                                                  |
|-------------------|------------------------------------------------------------|
| Card "Total Tokens" | Soma de tokens de entrada + saída no período            |
| Card "Entrada"    | Tokens de prompt (input)                                   |
| Card "Saída"      | Tokens de resposta gerada (output)                         |
| Card "Respostas"  | Número de interações (`AgentLog` com `tipo='agent_msg'`)  |
| Card "Custo (R$)" | Custo estimado convertido para BRL                         |
| Barra de proporção | Visual da razão entrada/saída                             |
| Gráfico 14 dias   | Barras diárias (azul = entrada, verde = saída)            |
| Seletor de período | Botões: 24h / 7d / 30d / Tudo                            |

**Tooltip dos cards:** hover exibe o valor em USD e a taxa de câmbio utilizada.

**Identificadores HTML relevantes:**

```
#tk-total       — valor total de tokens
#tk-input       — tokens de entrada
#tk-output      — tokens de saída
#tk-msgs        — número de respostas
#tk-custo       — custo em R$
#tk-bar-prop    — barra de proporção (CSS var --inp-pct)
#tk-chart       — container do gráfico de barras
#tk-provedor-info — linha de info (provedor/modelo/taxa)
#tk-period-btns — botões de período (.tk-period-btn)
```

**Função JS principal:** `tkPeriodo(periodo)` — dispara fetch para o endpoint e atualiza
todos os elementos acima.

---

## Rota URL

```python
# home/urls.py
path('agent/config/token-stats/', views.agent_token_stats, name='agent_token_stats')
```

---

## Configurações do Agent (`AgentConfig`)

O singleton `AgentConfig.get()` concentra todas as configurações:

| Campo                 | Descrição                                           |
|-----------------------|-----------------------------------------------------|
| `provedor_ia`         | `'claude'` ou `'openai'`                           |
| `claude_model`        | Modelo Claude selecionado                           |
| `claude_api_key`      | Chave de API Anthropic                              |
| `claude_max_tokens`   | Máximo de tokens por resposta (512–16384)           |
| `claude_temperature`  | Temperatura (0 = preciso, 1 = criativo)             |
| `openai_model`        | Modelo OpenAI selecionado                           |
| `openai_api_key`      | Chave de API OpenAI                                 |
| `prefixo_wa`          | Prefixo de invocação no WhatsApp (ex: `@noc`)      |
| `timeout_sessao_wa`   | Minutos de inatividade para encerrar sessão WA      |
| `max_comandos_sessao` | Limite de comandos SSH por sessão do agent          |

---

## Modelos de Dados

| Model              | Descrição                                                    |
|--------------------|--------------------------------------------------------------|
| `AgentConfig`      | Configuração singleton do agent                             |
| `AgentSessao`      | Sessão de conversa por grupo WhatsApp                       |
| `AgentLog`         | Registro de cada mensagem processada (com contagem de tokens)|
| `AgentKnowledge`   | Base de conhecimento do agent (documentos internos)         |
| `AgentKnowledgeDoc`| Documento individual da base de conhecimento                |
