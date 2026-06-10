# Despesas Operacionais — Funcionalidades Avançadas

## Visão Geral

Documentação das funcionalidades avançadas implementadas no módulo de Despesas Operacionais após a versão inicial (Junho 2026, sessão 2).

**Data de Implementação:** 10/06/2026  
**Módulo:** `financeiro/`  
**Status:** ✅ Produção

---

## 1. Parcelamento de Despesas (1x–12x)

Substitui o campo "Recorrência" por um sistema de parcelamento simples: ao criar uma despesa com N parcelas, o sistema cria automaticamente N registros separados, com nome sufixado e datas mensais consecutivas.

### Como funciona

```
Usuário cria "Servidor Cloud" com 3x, vencimento 01/07/2026
   └─ Sistema cria:
       ├─ "Servidor Cloud (1/3)" — vencimento 01/07/2026
       ├─ "Servidor Cloud (2/3)" — vencimento 01/08/2026
       └─ "Servidor Cloud (3/3)" — vencimento 01/09/2026
```

Cada parcela é uma despesa independente — pode ser paga, editada ou deletada individualmente.

### Interface

- Select "Parcelas" de 1x a 12x no modal "Nova Despesa"
- Ao selecionar > 1x: aparece aviso verde com contagem
- Campo Recorrência **removido** (incompatível com parcelamento)

### View

```python
# financeiro/views.py — api_criar_despesa
parcelas = max(1, min(12, int(data.get('parcelas', 1))))
if parcelas > 1:
    from dateutil.relativedelta import relativedelta
    for i in range(1, parcelas + 1):
        venc_i = vencimento_base + relativedelta(months=i - 1)
        Despesa.objects.create(
            nome=f'{nome} ({i}/{parcelas})',
            data_vencimento=venc_i,
            recorrencia='UNICA',
            ...
        )
    return JsonResponse({'sucesso': True, 'msg': f'{parcelas} parcelas criadas com sucesso!'})
```

---

## 2. Remoção do Campo Recorrência

O campo Recorrência foi **removido da interface** (mantido no banco de dados para compatibilidade com dados antigos). As razões:

- Conflito conceitual com parcelamento
- Complexidade desnecessária para o fluxo atual
- Substituído pelo parcelamento mais intuitivo

### O que foi removido

| Local | O que havia | Status |
|-------|-------------|--------|
| Modal Nova Despesa | Select "Recorrência" + "Repetir por N meses" | ✅ Removido |
| Modal Editar Despesa | Select "Recorrência" + campo meses | ✅ Removido |
| Tabela de listagem | Coluna "Recorrência" | ✅ Removida |
| JS payload `hsSalvar` | Campos `recorrencia`, `meses_recorrencia` | ✅ Removidos |

### O que foi mantido

- Campo `recorrencia` no modelo (banco de dados) — dados históricos preservados
- Lógica na view `api_pagar_despesa` — despesas recorrentes antigas continuam funcionando

---

## 3. Página Dedicada de Despesas

URL: `/financeiro/despesas/`

Página standalone com listagem completa de todas as despesas, desacoplada do dashboard financeiro.

### Funcionalidades da página

- **Cards de resumo** no topo: Total, Vencidas, Vencem Hoje, Pendentes, Pagas (com valores e contagem)
- **Barra de filtros**: busca textual por nome, filtro por status, filtro por categoria
- **Seleção em massa** com checkboxes por linha e "Selecionar todos" no cabeçalho
- **Barra de ações em massa** (aparece ao selecionar): Marcar Privada, Marcar Pública, Marcar Paga, Excluir
- **Botões por linha**: Pagar, Editar, Excluir
- **Modal Nova Despesa** e **Modal Editar** embutidos na página

### View

```python
# financeiro/views.py
@login_required
@acesso_financeiro_restrito
def listar_despesas_page(request):
    return render(request, 'financeiro/despesas.html')
```

### URL

```python
# financeiro/urls.py
path('despesas/', views.listar_despesas_page, name='listar_despesas_page'),
```

### Arquivo de template

`financeiro/templates/financeiro/despesas.html` — standalone, não usa o dashboard.

---

## 4. Ações em Massa (Bulk Actions)

Permite selecionar múltiplas despesas e executar uma ação em todas de uma vez.

### Ações disponíveis

| Ação | Descrição |
|------|-----------|
| Marcar Privada | Define `privada=True` em todas selecionadas |
| Marcar Pública | Define `privada=False` em todas selecionadas |
| Marcar Paga | Define `status='PAGO'` e `data_pagamento=hoje` (apenas pendentes) |
| Excluir Selecionadas | Deleta permanentemente todas selecionadas |

### API Endpoint

```
POST /financeiro/api/despesas/bulk/
Content-Type: application/json

{
  "ids": [1, 5, 12, 33],
  "acao": "privada"  // "privada" | "publica" | "pagar" | "deletar"
}
```

**Resposta:**
```json
{"sucesso": true, "msg": "4 despesa(s) marcada(s) como privada."}
```

### Segurança

- Apenas despesas visíveis ao usuário logado podem ser afetadas (filtro `Q(privada=False) | Q(privada=True, criado_por=request.user)`)
- Confirmação via `confirm()` nativo antes de qualquer ação destrutiva

### View

```python
# financeiro/views.py — api_despesas_bulk
@login_required
@acesso_financeiro_restrito
@require_http_methods(['POST'])
def api_despesas_bulk(request):
    data = json.loads(request.body)
    ids  = [int(i) for i in data.get('ids', [])]
    acao = data.get('acao', '')
    qs   = Despesa.objects.filter(id__in=ids).filter(
               Q(privada=False) | Q(privada=True, criado_por=request.user)
           )
    if acao == 'deletar':   qs.delete()
    elif acao == 'privada': qs.update(privada=True)
    elif acao == 'publica': qs.update(privada=False)
    elif acao == 'pagar':   qs.filter(status='PENDENTE').update(status='PAGO', data_pagamento=hoje)
```

---

## 5. Filtro Padrão "Vencidas" no Dashboard

O card de despesas no dashboard financeiro agora carrega por padrão mostrando apenas **despesas vencidas** (pendentes com data passada), em vez de mostrar todas.

### Mudanças

- Select `despesas-filtro-status`: opção "Vencidas" com atributo `selected`
- Botão "Ver todas as despesas" no card de vencidas: seta o select para VENCIDO antes de rolar

```html
<!-- dashboard.html -->
<option value="VENCIDO" selected>Vencidas</option>

<!-- onclick do botão "Ver todas" -->
onclick="document.getElementById('despesas-filtro-status').value='VENCIDO';
         carregarDespesas();
         document.getElementById('card-despesas').scrollIntoView({behavior:'smooth'});
         return false;"
```

---

## 6. Correção do Bug de Excluir/Pagar

**Problema:** Clicar em "Excluir" ou "Pagar" no dashboard não fazia nada.

**Causa raiz:** As funções `deletarDespesa` e `pagarDespesa` chamavam `uiConfirm()` e `uiAlert()`, que **não existem** no escopo do dashboard financeiro. A chamada lançava `ReferenceError` silencioso, interrompendo a execução sem feedback.

**Correção:**
```javascript
// Antes (quebrado)
const ok = await uiConfirm({ titulo: 'Excluir...', ... });

// Depois (funcionando)
if (!confirm(`Excluir a despesa "${nome}"?`)) return;
```

**Também corrigido:** A view `api_deletar_despesa` aceitava apenas `DELETE`, mas o código JS enviava `POST` em alguns contextos. Corrigido para aceitar ambos:

```python
@require_http_methods(['POST', 'DELETE'])
def api_deletar_despesa(request, despesa_id):
```

---

## 7. Card de Despesas Vencidas no Dashboard Financeiro

Card vermelho exibido **acima** do card principal de despesas, mostrando apenas as despesas vencidas (PENDENTE + data_vencimento < hoje).

### Comportamento

- Oculto por padrão quando não há despesas vencidas
- Exibe badge com contagem de despesas vencidas
- Lista as despesas vencidas com nome, categoria, valor, vencimento
- Botão "Ver todas as despesas" → navega para o card principal com filtro "Vencidas"

### Dados via API

```
GET /financeiro/api/despesas/dashboard/
Retorna: { vencidas: [...], hoje: [...], proximas: [...] }
```

---

## 8. Botão "Aluguéis de IP Ativos" — Dashboard Principal

Botão adicionado no header do dashboard principal (home) que abre um modal listando:

- Todos os clientes com `AluguelIPv4.status = 'ATIVO'`
- Para cada cliente: blocos ativos e faturas em aberto (ABERTA ou RASCUNHO)
- Cards de resumo: total de clientes, total de blocos, valor mensal, faturas abertas

**Endpoint:**
```
GET /financeiro/api/clientes-aluguel-ativo/
```

**Localização:** `home/templates/quadro_geral.html` — botão no `dash-header`

---

## API Endpoints — Resumo

| Método | Endpoint | Descrição | Novo? |
|--------|----------|-----------|-------|
| GET | `/financeiro/api/despesa/listar/?status=VENCIDO` | Despesas vencidas | Parâmetro existente |
| GET | `/financeiro/api/despesas/dashboard/` | Resumo vencidas/hoje/próximas | Existente |
| POST | `/financeiro/api/despesas/bulk/` | Ações em massa | ✅ Novo |
| POST,DELETE | `/financeiro/api/despesa/{id}/deletar/` | Deletar despesa (corrigido) | Corrigido |
| GET | `/financeiro/despesas/` | Página dedicada | ✅ Novo |
| GET/POST | `/financeiro/api/assinatura-locador/` | Assinatura do locador | ✅ Novo |
| GET | `/financeiro/api/clientes-aluguel-ativo/` | Clientes com aluguel ativo | ✅ Novo |

---

## Arquivos Modificados

| Arquivo | Mudanças |
|---------|----------|
| `financeiro/views.py` | `api_criar_despesa` (parcelas), `api_despesas_bulk` (novo), `listar_despesas_page` (novo), `api_deletar_despesa` (POST+DELETE), `assinatura_locador` (novo), `api_clientes_aluguel_ativo` (novo) |
| `financeiro/urls.py` | Rotas: `despesas/`, `api/despesas/bulk/`, `api/assinatura-locador/`, `api/clientes-aluguel-ativo/` |
| `financeiro/templates/financeiro/dashboard.html` | Filtro padrão VENCIDO, remoção do campo Recorrência, card vencidas, botão "Ver Todas", correção `uiConfirm` |
| `financeiro/templates/financeiro/despesas.html` | Página nova (criada do zero) |
| `home/templates/quadro_geral.html` | Botão "Aluguéis de IP Ativos" + modal |
| `home/views.py` | Remoção de `despesas_vencidas`/`despesas_a_vencer` do contexto |
