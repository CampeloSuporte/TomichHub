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
| `WhatsAppGrupo`    | Grupo/contato WhatsApp vinculado a um cliente — desde 2026-06-16 tem `claude_api_key` próprio (ver seção abaixo) |

---

## Sinal Óptico Datacom (DmOS) — Corrigido em 2026-06-16

**Arquivos:** `home/agent_engine.py`, `AgentKnowledge` (artigo "Datacom" no banco)

### Problema

O agent encontrava a interface física correta (ex.: `ten-gigabit-ethernet 1/1/4`,
identificada pela descrição configurada no equipamento), mas respondia "o sinal óptico
específico não foi fornecido" em vez de buscar o valor. A causa raiz: o comando que o
agent tentava executar — `show interface <iface> transceiver` — **não existe** no DmOS da
Datacom. O comando correto é:

```
show interface transceivers
```

(plural, **sem** especificar a interface — retorna uma tabela única com Temperature,
Voltage, Current, Tx-Power e Rx-Power de **todos** os SFPs instalados no equipamento).

### Correção

- `_physical_prefixes` passou a reconhecer os prefixos de interface física da Datacom
  (`ten-gigabit-ethernet`, `gigabit-ethernet`, `hundred-gigabit-ethernet`, `fast-ethernet`).
- Quando o agent identifica uma interface física Datacom (seja via busca por descrição,
  seja via `show interface <iface>` direto), o código agora **executa automaticamente**
  `show interface transceivers` no host via SSH e filtra a saída apenas para as linhas da
  interface em questão (mantendo cabeçalho da tabela), anexando o resultado à resposta
  antes de devolver ao modelo.
- O artigo da base de conhecimento (`AgentKnowledge`, Datacom) foi atualizado com o comando
  correto e um exemplo de saída, para reforçar via system prompt.

---

## API Key Claude por Grupo WhatsApp — Adicionado em 2026-06-16

**Arquivos:** `clientes/models.py` (campo `WhatsAppGrupo.claude_api_key`),
`home/views.py` (`agent_grupo_salvar`), `home/templates/agent_grupos.html`,
`home/agent_engine.py` (`processar_mensagem`)

### Motivação

O Agent NOC é compartilhado entre todos os clientes, mas cada cliente deve consumir os
**próprios créditos** da API Anthropic ao usar o agent no seu grupo WhatsApp — em vez de
tudo sair da chave global configurada em Sistema → Agent NOC → Configurações.

### Como funciona

- `WhatsAppGrupo` ganhou o campo `claude_api_key` (opcional, por grupo).
- Na tela **Agent NOC → Grupos** (`agent_grupos.html`), o modal de edição de cada grupo
  tem um campo de chave Claude dedicado:
  - O campo **nunca é pré-preenchido** com a chave real (mesmo padrão de segurança da
    config global) — mostra apenas o status `✓ configurada` / `○ não configurada`.
  - Deixar o campo vazio ao salvar **preserva** a chave já gravada.
  - Checkbox "Remover chave atual" permite limpar explicitamente.
  - Ícone 🔑 aparece ao lado do nome do grupo na listagem quando há chave configurada.
- Em `agent_engine.py`, `processar_mensagem()`:
  - Para sessões com `canal == 'whatsapp'`, busca `sessao.wa_grupo.claude_api_key`.
  - **Sem chave configurada no grupo → o agent fica em silêncio total** (retorna string
    vazia; nenhuma mensagem de erro é enviada ao grupo — não deve haver spam para grupos
    que ainda não configuraram a própria chave).
  - Com chave configurada, uma cópia em memória da `AgentConfig` global é usada apenas
    para essa chamada (`config.claude_api_key = chave_do_grupo`), sem nunca persistir a
    chave do cliente na configuração global.
  - Sessões via canal `terminal` (chat interno de teste no CRM) continuam usando a chave
    global normalmente — a regra de "silêncio sem chave" vale **apenas** para WhatsApp.
  - O provedor OpenAI **não** foi incluído nessa individualização (fora do escopo pedido) —
    continua usando a chave global mesmo em grupos WhatsApp.

### Migração

`clientes/migrations/0073_whatsappgrupo_claude_api_key_alter_acesso_notas_and_more.py`

---

## Bug: salvar API Key na config global falhava com erro 500 — Corrigido em 2026-06-16

**Arquivos:** `home/templates/agent_config.html`, `home/views.py`

**Sintoma:** ao colar a API Key do Claude e clicar em salvar, nada parecia acontecer; o
teste de conexão sempre respondia "API Key não configurada."

**Causa raiz:** `USE_L10N=True` + `LANGUAGE_CODE='pt-BR'` fazia o Django renderizar o
valor padrão `0.2` do campo `claude_temperature` como `0,2` (vírgula) no atributo
`value` de um `<input type="number">`. Esse tipo de input HTML5 exige ponto decimal —
com vírgula, o navegador considera o campo **inválido** e `.value` retorna string
vazia. O JS enviava `claude_temperature: ''`, e o backend quebrava com
`ValueError: could not convert string to float: ''` (HTTP 500), abortando a transação
inteira **antes** de salvar a API Key.

**Correção:**
- Template: campos de temperatura (Claude e OpenAI) agora usam
  `{{ valor|stringformat:'0.2f' }}` para forçar ponto decimal independente do locale.
- Backend (`agent_config` view): todas as conversões `int()`/`float()` de campos do
  formulário passaram a usar `data.get(campo) or valor_atual` em vez de
  `data.get(campo, valor_atual)` — assim, um valor vazio por qualquer outro motivo cai
  no valor já salvo em vez de derrubar a request com 500.
- JS (`salvarClaude`, `salvarOpenAI`, `salvarEvolution`): adicionado tratamento de
  erro visível (toast) para qualquer falha de rede/HTTP, evitando que uma falha de
  salvamento passe silenciosamente sem feedback ao usuário.
