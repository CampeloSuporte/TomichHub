# Monitoramento — Dashboard de Gráficos Zabbix

**Arquivos principais:**
- `monitoramento/models.py` — modelos Django
- `monitoramento/views.py` — views/API
- `monitoramento/urls.py` — rotas
- `monitoramento/templates/monitoramento/tab_monitoramento.html` — frontend JS
- `monitoramento/migrations/0002_monitordashconfig.py` — migration

**Atualizado em:** 2026-05-27

---

## Visão Geral

A aba **Monitoramento** exibe gráficos de tráfego em tempo real via Zabbix API.
Cada cliente pode ter múltiplos gráficos configurados (host + item In + item Out).
Os gráficos são compartilhados entre todos os usuários com acesso ao cliente.

---

## Modelo `MonitorDashConfig` (adicionado 2026-05-27)

```python
class MonitorDashConfig(models.Model):
    cliente = models.OneToOneField('clientes.Cliente', on_delete=CASCADE,
                                   related_name='monitor_dash_config')
    dados   = models.JSONField(default=list)          # lista de charts
    data_atualizacao = models.DateTimeField(auto_now=True)
```

Persiste no banco a configuração dos gráficos por cliente, substituindo o armazenamento
em `localStorage` (que era browser-específico e impedia outros usuários de ver os gráficos).

### Estrutura de cada chart em `dados`

```json
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
```

---

## API de Persistência

### `GET /monitoramento/dash/carregar/?id=<cliente_id>`

Retorna os gráficos salvos no banco para o cliente.

```json
{ "charts": [ ... ] }
```

Retorna `{ "charts": [] }` se não houver configuração ainda.

### `POST /monitoramento/dash/salvar/`

Salva (upsert) a lista de charts para o cliente.

```json
{ "cliente_id": "42", "charts": [ ... ] }
```

Ambas as rotas exigem login e verificam permissão via `_pode_acessar_cliente`.

---

## Comportamento do Frontend (JavaScript)

### Inicialização (`async function init()`)

1. Busca charts do backend (`/monitoramento/dash/carregar/`)
2. Se backend retorna charts → usa-os e atualiza localStorage como cache
3. Se backend está vazio → tenta migrar do localStorage (primeira vez de um usuário já configurado)
   - Se houver dados no localStorage → salva automaticamente no backend (migração automática)
4. Renderiza o grid de gráficos

### Persistência (`_salvarStorage`)

A cada alteração (add/edit/remove) a função:
1. Atualiza o localStorage (cache local para carregamento rápido)
2. Faz POST assíncrono para `/monitoramento/dash/salvar/` (persiste no banco)

---

## Permissões

A função `_pode_acessar_cliente(request, cliente_id)` controla acesso:

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

Clientes só podem acessar o dashboard do próprio cliente. Admins/operadores acessam qualquer um.

---

## Modelos Auxiliares

| Modelo | Descrição |
|---|---|
| `ZabbixConfig` | Credenciais da API Zabbix (OneToOne com Cliente) |
| `MonitorTopology` | Canvas de topologia de monitoramento (FK com Cliente) |
| `MonitorNode` | Nó no canvas da topologia |
| `MonitorLink` | Enlace entre dois nós com item IDs Zabbix |
| `MonitorDashConfig` | Configuração dos gráficos do dashboard (adicionado 2026-05-27) |

---

## Problema Resolvido (2026-05-27)

**Sintoma:** Gráficos adicionados por um usuário não apareciam para outros usuários
ou para o próprio cliente ao acessar de outro browser/dispositivo.

**Causa raiz:** Os gráficos eram armazenados exclusivamente em `localStorage` do browser,
chaveados por `grph_charts_v2_<cliente_id>`. Cada browser/usuário tinha seu próprio estado.

**Solução:** Introdução do modelo `MonitorDashConfig` + endpoints de carregar/salvar.
O frontend agora carrega do banco na inicialização e salva no banco a cada mudança.
O localStorage é mantido como cache local para carregamento imediato.
