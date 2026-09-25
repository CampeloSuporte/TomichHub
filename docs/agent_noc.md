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

## `shutdown` de interface bloqueado mesmo com nível admin — Corrigido em 2026-09-24

**Arquivos:** `home/agent_engine.py` (`BLOCKED_COMMANDS`, `OPERATIONAL_COMMANDS`), `home/views.py` (`aprovacao_wa`)

### Sintoma

Grupo WhatsApp nível `admin` (ex.: gestor do Call Center) pedia para desativar uma porta
(`interface hundred-gigabit-ethernet 1/1/2` + `shutdown` num switch Datacom) e o agent
respondia "O operador rejeitou o comando" — sem nenhum humano ter rejeitado nada.

### Causa raiz

`BLOCKED_COMMANDS` (compartilhada por `_is_safe_command`/`_is_operational_command` no
engine e por `aprovacao_wa()` em `views.py`) tinha o padrão
`r'(?<!undo )(?<!no )shutdown\s*$'`, verificado **antes** de qualquer checagem de
`nivel_permissao`. Isso vetava `shutdown` incondicionalmente para leitura, operacional
**e admin** — mesmo já estando explicitamente liberado em `OPERATIONAL_COMMANDS['huawei']`
(`r'^shutdown$'`), o que mostra que a intenção sempre foi tratar desativação de porta como
ação operacional reversível (`no shutdown`/`undo shutdown` desfaz), e não como destrutiva
(`reboot`, `erase`, `delete`, `format`, `factory`).

### Correção

- `shutdown` isolado saiu de `BLOCKED_COMMANDS` — a lista agora reserva-se a ações
  realmente irreversíveis/disruptivas.
- `OPERATIONAL_COMMANDS['cisco']` ganhou `shutdown` (só tinha `no shutdown`, ou seja,
  só conseguia religar porta, nunca desligar).
- `OPERATIONAL_COMMANDS['datacom']` foi criada do zero — não existia, então qualquer
  comando de interface num Datacom caía no fallback `generico` (sem `interface`/`shutdown`)
  e nível `operacional` nunca aprovava nada de configuração num Datacom.
- Nível `leitura` continua sem aprovar `shutdown` (não está em `SAFE_COMMANDS`); nível
  `admin` volta a aprovar (regra "tudo exceto `BLOCKED_COMMANDS`" já existente em
  `aprovacao_wa`); nível `operacional` passa a aprovar via `OPERATIONAL_COMMANDS`.
- Canal `terminal` não muda: continua sempre pedindo aprovação humana explícita
  (`requer_aprovacao = not eh_seguro or self.canal == 'terminal'`), independente desta lista.

---

## Continuação de conversa no WhatsApp sem repetir `@noc` — Adicionado em 2026-09-24

> **Revertido em 2026-09-25.** A pedido, o agent voltou a exigir o prefixo (`@noc` ou
> menção) em **toda** mensagem no WhatsApp. O campo `janela_continuacao_wa` saiu do
> `AgentConfig` (migração `0118`) e do painel. Continua valendo só a correção de
> `ultima_atividade` descrita abaixo (o `timeout_sessao_wa` conta inatividade real).

**Arquivos:** `home/views.py` (`_processar_wa_webhook`), `home/agent_engine.py`
(`AgentNOCEngine.processar_mensagem`), `clientes/models.py` (`AgentConfig.janela_continuacao_wa`)

### Motivação

Antes, **toda** mensagem no grupo precisava começar com o prefixo (`@noc` ou menção),
mesmo em pleno meio de uma conversa que o próprio agent tinha acabado de iniciar —
qualquer mensagem de acompanhamento sem o prefixo era silenciosamente ignorada.

### Como funciona

- Novo campo `AgentConfig.janela_continuacao_wa` (minutos, padrão **5**, `0` desliga).
- Em `_processar_wa_webhook`: se a mensagem não bate com nenhum prefixo válido, o
  webhook verifica se existe uma `AgentSessao` `ativa` para aquele JID com
  `ultima_atividade` dentro da janela configurada. Se sim, a mensagem inteira (sem
  stripping de prefixo) é roteada ao agent como continuação; se não, é ignorada como
  antes — a conversa nunca fica "solta" indefinidamente, expira sozinha depois da
  janela de inatividade.
- Cada mensagem processada (usuário ou agent, qualquer canal) renova a janela: o agent
  segue disponível sem `@noc` enquanto a conversa estiver realmente ativa.
- Corrigido de passagem: `AgentSessao.ultima_atividade` é `auto_now=True`, mas nada
  chamava `.save()` na sessão depois da criação — só `QuerySet.update()`, que **não**
  dispara `auto_now`. Ou seja, tanto esta janela quanto o `timeout_sessao_wa` existente
  mediam tempo desde a *criação* da sessão, não desde a última troca real.
  `processar_mensagem()` agora faz
  `AgentSessao.objects.filter(id=...).update(ultima_atividade=timezone.now())`
  explicitamente no início de cada mensagem processada.
- Painel: **Agent NOC → Configurações → Claude AI → "Janela de continuação sem prefixo"**.

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

---

## Zabbix via API — histórico e gráficos — Adicionado em 2026-08-20

**Arquivos:** `monitoramento/agent_zabbix.py`, `monitoramento/chart.py`,
`monitoramento/services.py`, `home/agent_engine.py`, `home/views.py`,
`clientes/templates/terminal.html`

### Motivação

O equipamento só responde sobre o **agora**. Perguntas do tipo "como estava o tráfego
do link da Wirelink hoje à tarde?" ou "qual era o sinal óptico antes do rompimento?"
não têm resposta via SSH — quem guarda isso é o **Zabbix do cliente**. O agent passou a
consultar esse Zabbix pela API JSON-RPC e a devolver, além dos números, um **gráfico
PNG** do período.

### De onde vem o Zabbix

Não há cadastro novo: o agent usa o que já existe.

1. `ZabbixConfig` do cliente (aba Monitoramento), se houver;
2. senão, qualquer **acesso do cliente** com "zabbix" no tipo e protocolo `HTTP`/`HTTPS`
   — usuário e senha do próprio acesso.

A URL é montada tolerando os formatos reais de cadastro
(`187.84.126.249:3032/zabbix`, `172.31.100.14/zabbix/`, `45.169.153.145` + porta): a
porta do cadastro só entra quando o host ainda não traz uma, e quando não há path a
variante `…/zabbix` também é testada. Cada candidato passa por
`_get_config_com_tunel()` — **o mesmo túnel SSH via ProxyServer que a aba Monitoramento
usa** — e só é aceito depois de responder `apiinfo.version` e autenticar. A combinação
que funcionou fica em cache por 30 min (`zbx_agent_cfg:<cliente_id>`), então a segunda
pergunta não repete a descoberta.

### Tools do agent

| Tool | O que faz |
|---|---|
| `zabbix_buscar_item(host, item, cliente_nome)` | Acha hosts e itens monitorados. `host` casa no nome do host no Zabbix; `item` casa em **nome e key** do item. Sem parâmetros, lista os hosts. Sem achar o item, lista o que existe naquele host em vez de devolver "não encontrei". |
| `zabbix_historico(itemids, periodo, inicio, fim, marcador, titulo, grafico, cliente_nome)` | Até **4 itens no mesmo gráfico**. Devolve mín/méd/máx/último + amostras ao longo da janela e **envia o PNG ao usuário**. |

Janela: `periodo` relativo (`30m`, `6h`, `2d`, `1w` — padrão `6h`) ou `inicio`/`fim`
absolutos (`AAAA-MM-DD HH:MM`, `DD/MM/AAAA HH:MM`, `agora`). `marcador` desenha uma
**linha vermelha tracejada** na hora do evento — é o que atende "antes e depois do
rompimento".

### history × trends

`historico_janela()` busca `history.get` para janelas curtas e `trend.get` (médias
horárias, retidas por muito mais tempo) para janelas acima de 3 dias — e cai de uma
fonte para a outra automaticamente quando a primeira volta vazia. Contadores de octetos
(`value_type=3`, unidade `B`) viram taxa em **bps** com a mesma regra do gráfico da aba
Monitoramento; séries acima de 320 pontos são reamostradas por média de bucket.

### Gráfico (PNG)

`monitoramento/chart.py` desenha com **Pillow** (já no `requirements.txt`) — sem
matplotlib/numpy. Fundo claro, grade, eixo Y formatado pela unidade do item
(`4.05 Gbps`, `-23.87 dBm`, `72%`), horas no fuso do Django (não no do sistema) e
legenda com mín/méd/máx por série, quebrada em linhas **antes** de a altura do PNG ser
fixada (senão a segunda linha saía cortada).

### Entrega da imagem

A tool emite `{'type': 'agent_image', 'b64': …, 'caption': …}` no `notify_cb`:

- **WhatsApp** (`_processar_wa_webhook`): as imagens são acumuladas e enviadas por
  `_evolution_send_media()` (`/message/sendMedia/`) logo depois da resposta em texto —
  mesmo payload do envio de mídia do Atendimento, que já roda em produção.
- **Terminal web** (`AgentNOCConsumer`): `notify_cb` é o próprio `_send_json`, e o chat
  renderiza `case 'agent_image'` como `<img src="data:image/png;base64,…">` clicável.

### Validado ao vivo

Startnet Provedor (Zabbix em `198.18.1.13/zabbix`, IP privado → túnel SSH pelo
ProxyServer): "@noc me traga o histórico do tráfego das últimas 3 horas do link paineiras
no switch brasnorte" → o agent chamou `zabbix_buscar_item(host='brasnorte',
item='paineiras bits')`, escolheu os itemids de `Bits received`/`Bits sent` da
`100GE0/0/4(LINK-PAINEIRAS)`, chamou `zabbix_historico` e respondeu com a análise do pico
noturno + o PNG das duas curvas.
