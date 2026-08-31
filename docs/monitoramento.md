# Monitoramento — Dashboard de Gráficos Zabbix

**Arquivos principais:**
- `monitoramento/models.py` — modelos Django
- `monitoramento/views.py` — views/API
- `monitoramento/urls.py` — rotas
- `monitoramento/templates/monitoramento/tab_monitoramento.html` — frontend JS/CSS
- `monitoramento/migrations/0002_monitordashconfig.py` — migration

**Última atualização:** 2026-08-31

---

## Visão Geral

A aba **Monitoramento** exibe gráficos de tráfego em tempo real via Zabbix API.
Cada cliente pode ter múltiplos gráficos organizados em **abas independentes**.
A configuração é compartilhada entre todos os usuários com acesso ao cliente (persiste no banco).

---

## Sistema de Abas (adicionado 2026-06-13)

Cada cliente pode criar quantas abas quiser. Cada aba tem seu próprio conjunto de painéis de gráficos. Exemplos de uso: "Upstream", "Downstream", "Links Críticos", "Clientes VIP", etc.

### Interface

| Ação | Como fazer |
|------|-----------|
| Criar nova aba | Botão **"+ Nova aba"** na barra de abas |
| Trocar de aba | Clicar na aba desejada |
| Renomear aba | **Clique direito** → "Renomear aba" **ou** duplo-clique no nome |
| Fechar aba | **Clique direito** → "Fechar aba" **ou** botão **×** na própria aba |
| Adicionar gráfico | Com a aba ativa, clicar em **"Adicionar"** na toolbar |

- O botão × de fechar só aparece quando há mais de uma aba
- Ao criar uma nova aba, o campo de nome abre automaticamente para edição
- Renomear com Enter confirma; Escape cancela

### Menu de contexto das abas

Clique com o botão direito em qualquer aba para abrir o menu:

```
┌─────────────────────┐
│ ✏ Renomear aba      │
│ ─────────────────── │
│ ✕ Fechar aba        │  ← oculto quando há apenas 1 aba
└─────────────────────┘
```

O menu é posicionado próximo ao cursor e fechado ao clicar fora.

---

## Modelo `MonitorDashConfig`

```python
class MonitorDashConfig(models.Model):
    cliente = models.OneToOneField('clientes.Cliente', on_delete=CASCADE,
                                   related_name='monitor_dash_config')
    dados   = models.JSONField(default=list)
    data_atualizacao = models.DateTimeField(auto_now=True)
```

### Estrutura do campo `dados` (formato novo — tabs)

```json
{
  "tabs": [
    {
      "id": 1,
      "nome": "Upstream",
      "charts": [
        {
          "id": 1,
          "hostid": "12345",
          "hostname": "Router-Cliente-X",
          "titulo": "Link Principal",
          "ifaceName": "Bits received on eth0",
          "inId": "54321",
          "outId": "54322",
          "hours": 1
        }
      ]
    },
    {
      "id": 2,
      "nome": "Downstream",
      "charts": []
    }
  ]
}
```

### Compatibilidade com formato antigo

O backend e o frontend detectam automaticamente o formato antigo (lista plana de charts) e
o migram para a nova estrutura com uma aba "Geral":

```python
# views.py — carregar_dash_config
if isinstance(dados, list):
    return JsonResponse({'tabs': [{'id': 1, 'nome': 'Geral', 'charts': dados}]})
```

No frontend, o localStorage antigo (`grph_charts_v2_<id>`) também é migrado automaticamente
para o novo formato de abas na primeira inicialização.

---

## API de Persistência

### `GET /monitoramento/dash/carregar/?id=<cliente_id>`

Retorna a configuração salva no banco.

**Resposta (formato novo):**
```json
{
  "tabs": [
    { "id": 1, "nome": "Geral", "charts": [...] }
  ]
}
```

Retorna `{ "tabs": [] }` se não houver configuração.

### `POST /monitoramento/dash/salvar/`

Salva (upsert) a configuração para o cliente.

```json
{
  "cliente_id": "42",
  "tabs": [
    { "id": 1, "nome": "Upstream", "charts": [...] },
    { "id": 2, "nome": "Downstream", "charts": [] }
  ]
}
```

Ambas as rotas exigem login e verificam permissão via `_pode_acessar_cliente`.

---

## Comportamento do Frontend (JavaScript — módulo `GRAPH`)

### Variáveis de estado

| Variável | Tipo | Descrição |
|----------|------|-----------|
| `tabs` | `Array` | Lista de abas `{ id, nome, charts[] }` |
| `activeTabId` | `number` | ID da aba atualmente visível |
| `nextTabId` | `number` | Próximo ID de aba a ser criada |
| `nextChartId` | `number` | Próximo ID de gráfico a ser criado |
| `charts` | `Array` | Alias para `_activeTab().charts` — sincronizado por `_sync()` |

### Funções públicas da API `GRAPH`

**Gerenciamento de abas:**

| Função | Descrição |
|--------|-----------|
| `criarAba()` | Cria nova aba, ativa ela e abre input de renomeação |
| `trocarAba(id)` | Destrói instâncias Chart.js da aba atual e carrega a nova |
| `removerAba(id)` | Remove aba e destroi suas instâncias; bloqueado com 1 aba |
| `renomearAba(id, nome)` | Persiste o novo nome |
| `iniciarRenomear(id)` | Substitui o `<span>` do nome por `<input>` inline |
| `_abrirCtxMenu(e, id)` | Abre menu de contexto (clique direito) |

**Gerenciamento de gráficos:**

| Função | Descrição |
|--------|-----------|
| `abrirModalAdd()` | Abre modal de adição (opera na aba ativa) |
| `abrirModalEdit(id)` | Abre modal de edição de um gráfico |
| `salvarChart()` | Salva gráfico novo ou editado na aba ativa |
| `removerChart(id)` | Remove gráfico da aba ativa |
| `atualizarTodos()` | Força atualização de todos os gráficos da aba |

### Inicialização (`async function init()`)

1. Busca tabs do backend (`/monitoramento/dash/carregar/`)
2. Se backend retorna tabs → usa-as e atualiza localStorage como cache
3. Se backend vazio → tenta migrar do localStorage (primeira vez)
4. Se localStorage também vazio → cria aba "Geral" vazia
5. Renderiza barra de abas e grid de gráficos

### Persistência (`_salvarStorage`)

A cada alteração (criar/renomear/fechar aba, add/edit/remove gráfico):
1. Atualiza o localStorage com chave `grph_tabs_v1_<cliente_id>`
2. Faz POST assíncrono para `/monitoramento/dash/salvar/`

### Troca de aba (`trocarAba`)

Ao trocar de aba, todas as instâncias `Chart.js` e intervalos `setInterval` da aba anterior
são destruídos para liberar memória e CPU. A nova aba inicializa seus próprios gráficos do zero.

---

## Períodos do gráfico

Cada painel tem seu próprio seletor de período — **1h, 3h, 6h, 12h, 24h, 7d e 30d** — salvo
junto do gráfico (campo `hours` do chart, em horas: `168` = 7 dias, `720` = 30 dias).

O frontend chama `GET /monitoramento/zabbix/history/?cliente_id=&item_id=&hours=`; a view só
aceita os valores da lista acima (qualquer outro vira `1`).

### Como o backend monta a janela (`services.historico_item`)

| Janela | Fonte no Zabbix | Observação |
|--------|-----------------|------------|
| ≤ 3 dias (1h a 24h) | `history.get` | dado bruto do item, ordem cronológica |
| > 3 dias (7d, 30d)  | `trend.get`  | médias horárias (`value_avg`) — o `history` costuma ser expurgado antes disso |

Se a fonte preferida vier vazia, cai automaticamente na outra. O período **inteiro** é buscado
(`time_from` → `time_till`, teto de 20.000 pontos brutos) e reduzido aqui com `_downsample()`
para no máximo `limit` pontos (padrão 300) — é isso que mantém o gráfico leve em 30 dias.

Contador acumulativo de bytes continua virando taxa (`bps`) pela diferença entre pontos
consecutivos, tanto no `history` quanto no `trend`.

### Intervalo do poll por período

Janela longa não muda a cada 15s, então o `setInterval` acompanha o período escolhido (o badge
ao lado do seletor mostra o valor em uso):

| Período | Poll |
|---------|------|
| até 6h  | 15s  |
| 12h e 24h | 60s |
| 7d e 30d | 5min |

O eixo X também acompanha: `HH:MM:SS` até 6h, `HH:MM` em 12h/24h e `dd/mm HH:MM` em 7d/30d.

---

## Permissões

```python
def _pode_acessar_cliente(request, cliente_id):
    if request.user.is_staff or request.user.is_superuser:
        return True
    try:
        c = Cliente.objects.get(usuario=request.user)
        return str(c.id) == str(cliente_id)
    except Exception:
        return False
```

Clientes só acessam o dashboard do próprio cliente. Admins/operadores acessam qualquer um.

---

## Modelos Auxiliares

| Modelo | Descrição |
|--------|-----------|
| `ZabbixConfig` | Credenciais da API Zabbix (OneToOne com Cliente) |
| `MonitorTopology` | Canvas de topologia de monitoramento (FK com Cliente) |
| `MonitorNode` | Nó no canvas da topologia |
| `MonitorLink` | Enlace entre dois nós com item IDs Zabbix |
| `MonitorDashConfig` | Configuração das abas e gráficos do dashboard |

---

## Histórico de Alterações

### 2026-08-31 — Períodos de 12h/24h corrigidos e 7d/30d adicionados

**Problema:** escolher 12h ou 24h no painel mostrava o mesmo gráfico curto de sempre.

**Causa raiz:** `historico_item()` pedia ao Zabbix `sortorder: DESC` com `limit` (300 por
padrão) — ou seja, os 300 pontos **mais recentes**. Com item de 1 em 1 minuto isso é sempre a
mesma fatia de ~5h no fim da janela, independente do `hours` pedido.

**Solução:** a janela inteira passou a ser buscada em ordem cronológica (`ASC`, com
`time_till`) e o corte de pontos virou reamostragem (`_downsample`) no servidor. Junto disso
entraram os períodos de 7 e 30 dias, servidos por `trend.get` (o `history` não sobrevive tanto
tempo no Zabbix), o poll escalonado por período e o rótulo do eixo X com data nas janelas longas.

**Arquivos modificados:**
- `monitoramento/services.py` — `historico_item()` reescrita (fallback history/trends + downsample)
- `monitoramento/views.py` — `historico_item_zabbix` aceita `hours` 168 e 720
- `monitoramento/templates/monitoramento/tab_monitoramento.html` — lista `PERIODOS`, botões por
  `data-h`, `_pollMs()`, `_fmtEixo()`

### 2026-08-20 — Camada Zabbix reutilizada pelo Agent NOC

O Agent NOC passou a consultar o Zabbix do cliente para responder perguntas de histórico
(tráfego, sinal óptico, CPU) e devolver gráficos. Ele **não** tem uma configuração própria:
usa `ZabbixConfig` ou, na falta dela, um acesso HTTP/HTTPS do cliente com "zabbix" no tipo,
e passa pelo mesmo `_get_config_com_tunel()` (túnel SSH via ProxyServer) desta aba.

**Adicionado em `monitoramento/services.py`:**
- `buscar_hosts(config, busca)` / `buscar_itens(config, host_busca, item_busca)` — busca textual
  de hosts e itens (casa em `name` e `key_`).
- `historico_janela(config, item_id, ts_from, ts_till)` — histórico em janela absoluta, com
  fallback automático `history.get` ↔ `trend.get` e reamostragem por bucket.

**Novos módulos:** `monitoramento/agent_zabbix.py` (descoberta do Zabbix + tools do agent) e
`monitoramento/chart.py` (gráfico PNG com Pillow). Ver [agent_noc.md](agent_noc.md).


### 2026-06-13 — Sistema de Abas

**Problema:** Dashboard de monitoramento tinha lista plana de gráficos sem organização.

**Solução:**
- Frontend: refatorado para suportar múltiplas abas independentes, cada uma com seu grid de gráficos
- Backend: `carregar_dash_config` e `salvar_dash_config` agora operam no formato `{ "tabs": [...] }`
- Menu de contexto (clique direito) nas abas com opções de renomear e fechar
- Renomeação inline (duplo-clique ou menu de contexto): `<span>` vira `<input>`, confirma com Enter/blur, cancela com Escape
- Storage key migrada de `grph_charts_v2_<id>` para `grph_tabs_v1_<id>`
- Compatibilidade retroativa: formato antigo (lista plana) convertido automaticamente

**Arquivos modificados:**
- `monitoramento/templates/monitoramento/tab_monitoramento.html` — frontend completo
- `monitoramento/views.py` — `carregar_dash_config`, `salvar_dash_config`

### 2026-05-27 — Persistência no Banco

**Problema:** Gráficos adicionados por um usuário não apareciam para outros ou em outros browsers.

**Causa raiz:** Armazenamento exclusivo em `localStorage` (`grph_charts_v2_<id>`).

**Solução:** Modelo `MonitorDashConfig` + endpoints de carregar/salvar. O localStorage foi mantido como cache local para carregamento imediato.
