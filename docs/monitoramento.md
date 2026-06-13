# Monitoramento — Dashboard de Gráficos Zabbix

**Arquivos principais:**
- `monitoramento/models.py` — modelos Django
- `monitoramento/views.py` — views/API
- `monitoramento/urls.py` — rotas
- `monitoramento/templates/monitoramento/tab_monitoramento.html` — frontend JS/CSS
- `monitoramento/migrations/0002_monitordashconfig.py` — migration

**Última atualização:** 2026-06-13

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
