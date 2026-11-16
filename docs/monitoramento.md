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
| `_cloneHours` | `number\|null` | Período herdado ao clonar; `null` em adicionar/editar |
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
| `abrirModalEdit(id, clonar=false)` | Abre modal de edição de um gráfico; com `clonar`, o mesmo modal preenchido cria um gráfico novo |
| `clonarChart(id)` | Botão **Clonar** do card (entre Expandir e Editar): `abrirModalEdit(id, true)` — `_editingChart=null` e `_cloneHours` = período do original, então salvar adiciona um card na aba ativa sem alterar o de origem |
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

O frontend chama `GET /monitoramento/zabbix/history/?cliente_id=&item_ids=<in>,<out>&hours=` —
Download e Upload **numa requisição só**, resposta `{"series": {"<itemid>": [{t, v}, …]}}`. O
formato antigo (`item_id=<id>` → `{"history": [...]}`) continua aceito. A view só aceita os
valores de `hours` da lista acima (qualquer outro vira `1`).

### Velocidade da carga (2026-11-16)

O gráfico demorava a aparecer por quatro custos que se somavam em cada poll de cada painel:

| Custo | Antes | Agora |
|-------|-------|-------|
| Sonda direto × túnel (Zabbix de IP público) | `apiinfo.version` a **cada** requisição (0,5–6 s) | decisão guardada no Redis por 10 min (`zbx_rota:*`); esquecida quando a chamada falha |
| Download e Upload | 2 requisições HTTP, 2 túneis/auth/item.get, 2 `history.get` | 1 requisição e 1 `history.get` por `value_type` (`services.historico_itens`) |
| Janela de 12h/24h | o poll baixava a janela inteira de novo (cliente 90: 2–12 s por poll, piorando) | pontos brutos ficam no Redis por item (`zbx_raw:*`, 15 min); o poll pede só de `fim − 3 min` em diante (~0,2 s) |
| Reabrir a aba / outro usuário no mesmo cliente | tudo do zero | resposta pronta por 10 s (`zbx_hist:*`) e, no navegador, o último resultado de cada painel em `sessionStorage` é desenhado na hora ("Atualizando…") |

Além disso, a aba do navegador em segundo plano não faz poll (volta a atualizar no
`visibilitychange`) e o rodapé do painel mostra o erro real ("⚠ …") em vez de ficar eterno em
"Aguardando dados...". Zabbix fora do ar (disjuntor `zbx_fora`) agora vira erro em vez de lista
vazia. Medido ao vivo: cliente 37 conexão 1,85 s → 0,01 s; cliente 90 poll de 24h 2–12 s → 0,16–0,2 s,
com os mesmos pontos da busca completa.

O cache bruto guarda tudo o que já cobriu (até 3 dias), não só a janela pedida: dois painéis
do mesmo item, um em 1h e outro em 24h, não se atrapalham.

### Busca de interface no modal

Depois de escolher o host aparece o campo **Interface**: lista as interfaces de tráfego do host
(par Download/Upload com o valor atual de cada sentido), em ordem natural (`0/0/2` antes de
`0/0/10`). A busca ignora acento e caixa e aceita várias palavras em qualquer ordem
(`operadora 0/0/1`), procurando no nome da interface, nos nomes dos itens e nas chaves; o
trecho encontrado fica realçado. Setas ↑/↓ andam na lista, Enter escolhe, Esc fecha. Escolher a
interface preenche os dois sentidos.

Os campos **Item Download** e **Item Upload** continuam para escolha avulsa, e escolher um
"Bits received" preenche sozinho o "Bits sent" da mesma interface (e vice-versa). O par vem do
servidor: `services.parear_trafego()` marca cada item com `sentido` (`in`/`out`), `iface` e
`par` (itemid do gêmeo), casando nesta ordem:

1. chave trocada — `net.if.in[ifHCInOctets.5]` ↔ `net.if.out[ifHCOutOctets.5]`;
2. nome trocado — `… Bits received` ↔ `… Bits sent`, `Traffic in` ↔ `Traffic out`, `Entrada` ↔ `Saída`, `Download` ↔ `Upload`, `RX` ↔ `TX`;
3. mesmo nome de interface, quando só existe um candidato do outro sentido.

Erros e descartes (`net.if.in.errors[…]`) ficam de fora. Conferido nos Zabbix reais dos
clientes 35, 37 e 90: 278, 106 e 152 itens de tráfego, nenhum sem par, e o par sugerido bateu
com o que o usuário tinha montado à mão nos painéis salvos.

O host só tem a interface escolhida sozinha quando possui **uma única** interface; antes o modal
pegava o primeiro "Bits received" da lista (uma porta qualquer em switch/OLT, e às vezes o Upload
de outra porta).

### Como o backend monta a janela (`services.historico_itens`)

| Janela | Fonte no Zabbix | Observação |
|--------|-----------------|------------|
| ≤ 3 dias (1h a 24h) | `history.get` | dado bruto do item, ordem cronológica |
| > 3 dias (7d, 30d)  | `trend.get`  | médias horárias (`value_avg`) — o `history` costuma ser expurgado antes disso |

Se a fonte preferida vier vazia, cai automaticamente na outra. O período **inteiro** é buscado
(`time_from` → `time_till`, teto de 20.000 pontos brutos) e reduzido aqui com `_downsample()`
para no máximo `limit` pontos (padrão 300) — é isso que mantém o gráfico leve em 30 dias.

Contador acumulativo de bytes continua virando taxa (`bps`) pela diferença entre pontos
consecutivos, tanto no `history` quanto no `trend`.

### Serrilhado de contador (falsa queda)

Quando o roteador atualiza o contador de octetos num ritmo diferente da coleta do Zabbix, um
intervalo recebe bytes "a mais" e o seguinte "a menos". Caso real (NE_8000_JUINA, coleta de 3 min):
`20.4 → 22.7 → 14.5 → 23.3 → 14.5 → 17.0` Gbps com tráfego estável em ~18.7 — o gráfico mostrava
quedas de ~8 Gbps que não existiram. `_corrigir_serrilhado()` procura pontos consecutivos fora da
mediana local (±8%, janela de 7) com **sinais alternados** e troca o trecho pela média dele, o que
preserva o volume trafegado. O pico pode vir até 2 coletas depois do vale (ex.: AVATO AFL
`6.17 → 4.67 → 4.10`). Em seguida passa uma suavização 1-2-1 (também preserva volume) para o resíduo.
Queda profunda — abaixo de 20% da mediana local, ex.: link caído — fica fora da suavização e aparece
crua; vários pontos baixos seguidos também não alternam sinal e continuam aparecendo. Só vale para séries de tráfego (`bps`/contador convertido)
vindas do `history` — `trend` já é média horária. Aplicado em `historico_item` (aba Monitoramento) e
`historico_janela` (gráficos dos Acessos e Agent NOC).

No front, a linha usa `cubicInterpolationMode: 'monotone'`: a spline antiga (`tension: 0.4`)
ultrapassava os pontos e aprofundava ainda mais os vales.

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

### 2026-11-16 — Gráfico mais rápido e busca de interface

**Problema:** o gráfico demorava a trazer os dados, e escolher a interface era garimpar item por
item nas duas listas. **Correção:** ver "Velocidade da carga" e "Busca de interface no modal"
acima. Arquivos: `monitoramento/services.py` (`historico_itens`, `_history_incremental`,
`parear_trafego`), `monitoramento/views.py` (`historico_item_zabbix` com `item_ids`, cache da rota
em `_get_config_com_tunel`), `tab_monitoramento.html`; testes em `monitoramento/tests.py` e
`monitoramento/tests_navegador.py` (Chrome headless).

### 2026-10-06 — Quedas falsas no gráfico de tráfego

**Problema:** o gráfico mostrava quedas bruscas (ex.: 23 → 14,5 Gbps) que o tráfego real não teve,
gerando falso positivo. **Causa:** serrilhado do contador do equipamento nos dados brutos do Zabbix +
spline que exagerava os vales. **Correção:** ver "Serrilhado de contador (falsa queda)" acima.
Segunda rodada no mesmo dia: compensação passou a aceitar o pico até 2 coletas depois do vale e
entrou a suavização 1-2-1 — varredura dos 26 gráficos salvos zerou as quedas > 15% nos links de Gbps.

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
