# Módulo Financeiro - Documentação Técnica

## 📋 Visão Geral

O módulo financeiro (`financeiro/`) gerencia toda a estrutura de faturamento, despesas operacionais, recorrências e privacidade de dados financeiros. Implementado em Django com interface moderna em tempo real.

**Data de Atualização:** 01/06/2026

---

## ✨ Implementações Recentes (Junho 2026)

### 1. Sistema de Recorrência de Despesas
**Commit:** `cdb719cc6` - feat: melhorias no sistema de despesas com recorrência

#### O que foi implementado:
- ✅ Campo `recorrencia` no modelo Despesa (UNICA, MENSAL, BIMESTRAL, TRIMESTRAL, SEMESTRAL, ANUAL)
- ✅ Campo `meses_recorrencia` para indicar quantos meses/ciclos a recorrência vai durar
- ✅ Campo `ocorrencia_atual` para rastrear qual é a ocorrência atual
- ✅ Auto-geração automática de próximas despesas ao marcar como paga
- ✅ Interface com checkboxes e campos de entrada no modal
- ✅ Exibição melhorada: "2/12 mensal" (ocorrência/total tipo)
- ✅ Indicador visual com cor roxa para recorrências

#### Modelos:
- `Despesa` (linhas 513-579 em models.py)
  - `recorrencia`: CharField com 6 opções
  - `meses_recorrencia`: PositiveIntegerField (null=True, blank=True)
  - `ocorrencia_atual`: PositiveIntegerField (padrão=1)
  - `status`: PENDENTE ou PAGO
  - `data_pagamento`: DateField para registro de quitação

#### Views/APIs:
- `api_criar_despesa`: POST `/financeiro/api/despesa/criar/`
- `api_editar_despesa`: POST `/financeiro/api/despesa/{id}/editar/`
- `api_pagar_despesa`: POST `/financeiro/api/despesa/{id}/pagar/`
  - Marca como paga e gera próxima ocorrência automaticamente
  - Retorna informação sobre próxima gerada ou se recorrência encerrou
- `api_listar_despesas`: GET `/financeiro/api/despesa/listar/`
- `api_deletar_despesa`: DELETE `/financeiro/api/despesa/{id}/deletar/`

#### Migração:
- `0005_despesa_recorrencia`: Adicionou campos de recorrência

#### Interface:
- Modal "Nova Despesa" com campos:
  - Recorrência (dropdown: UNICA/MENSAL/etc)
  - Meses de recorrência (input numérico, opcional)
  - Dica: "vazio = indefinido"
- Listagem com exibição: "2/12 mensal" em cor roxa
- Botão rápido "✓" para marcar como pago

---

### 2. Sistema de Privacidade para Despesas
**Commit:** `943fb667e` - feat: privacidade de despesas e melhorias de layout

#### O que foi implementado:
- ✅ Campo `privada` no modelo Despesa
- ✅ Despesas privadas visíveis apenas para o criador
- ✅ Despesas públicas visíveis para todos
- ✅ Checkbox "Privada (apenas você vê)" no modal
- ✅ Indicador visual com ícone de cadeado 🔒
- ✅ Filter automático em `api_listar_despesas`
- ✅ Admin panel com filtro de privacidade

#### Modelos:
- `Despesa.privada`: BooleanField (default=False)

#### Controle de Acesso:
```python
# em api_listar_despesas:
Q(privada=False) | Q(privada=True, criado_por=request.user)
```

#### Migração:
- `0006_despesa_privada`: Adicionou campo privada

#### Interface:
- Checkbox: "🔒 Privada (apenas você vê)"
- Ícone de cadeado roxo próximo ao nome se privada
- Desmarcado = visível para todos

---

### 3. Sistema de Privacidade para Faturas
**Commit:** `2e0438ad7` - feat: privacidade de faturas com indicador visual

#### O que foi implementado:
- ✅ Campo `privada` no modelo Fatura
- ✅ Faturas privadas visíveis apenas para staff
- ✅ Faturas públicas visíveis para todos
- ✅ Checkbox no modal "Nova Fatura Manual"
- ✅ Indicador visual com ícone de cadeado 🔒
- ✅ Filter automático na listagem
- ✅ Validação de acesso ao visualizar fatura
- ✅ Admin panel com filtro

#### Modelos:
- `Fatura.privada`: BooleanField (default=False)

#### Controle de Acesso:
```python
# em api_listar_faturas:
if request.user.is_staff:
    # vê tudo
else:
    # vê apenas públicas
    privada=False

# em api_visualizar_fatura:
if fatura.privada and not request.user.is_staff:
    return 403 Acesso negado
```

#### Migração:
- `0007_fatura_privada`: Adicionou campo privada

#### Interface:
- Checkbox: "🔒 Privada (apenas você vê)"
- Ícone de cadeado roxo próximo ao número da fatura
- "Desmarcado = visível para todos"

---

### 4. Sistema de Privacidade para Consultorias, Aluguéis e Vendas
**Commit:** `42aeb341c` - feat: privacidade para consultorias, aluguéis e vendas

#### O que foi implementado:
- ✅ Campo `privada` em Consultoria
- ✅ Campo `privada` em AluguelIPv4
- ✅ Campo `privada` em VendaEquipamento
- ✅ Checkboxes em todos os modais de criação
- ✅ Indicadores visuais com ícones de cadeado
- ✅ Mesma lógica de controle de acesso (staff)
- ✅ Admin panel com filtros

#### Modelos:
- `Consultoria.privada`: BooleanField (default=False)
- `AluguelIPv4.privada`: BooleanField (default=False)
- `VendaEquipamento.privada`: BooleanField (default=False)

#### Migrações:
- `0008_consultoria_privada`: Consultoria
- `0009_aluguelipv4_privada`: AluguelIPv4 (corrigido nome do modelo)
- `0010_vendaequipamento_privada`: VendaEquipamento

#### Interface:
- Mesmo padrão de checkbox em todos os modais
- Ícone de cadeado roxo quando privada
- "Desmarcado = visível para todos"

---

### 5. Melhorias de Layout
**Commits:**
- `943fb667e`: Layout inicial despesas
- `189ee3613`: Ajuste coluna vencimento (120px)
- `95fd3074b`: Aumento drástico coluna vencimento (180px)

#### O que foi implementado:
- ✅ Ajuste de grid CSS para evitar sobreposição
- ✅ Redimensionamento de colunas na listagem
- ✅ Ícones adicionados ao header da lista
- ✅ Layout responsivo melhorado

#### Layout Final (`.despesa-row`):
```css
grid-template-columns: 2.5fr 80px 90px 180px 120px;
/* Nome | Recorrência | Valor | Vencimento | Status+Botões */
```

#### Mudanças:
- Nome: 2.5fr (maior espaço)
- Recorrência: 80px (comprimido)
- Valor: 90px (comprimido)
- **Vencimento: 180px** (expandido para evitar sobreposição)
- Status: 120px (espaço para ícones + botões)

---

## 📊 Estrutura de Modelos

### Despesa
```python
class Despesa(models.Model):
    nome: CharField
    descricao: TextField
    valor: DecimalField
    categoria: CharField (INFRAESTRUTURA/PESSOAL/SERVICOS/ADMINISTRATIVO/FISCAL/OUTROS)
    recorrencia: CharField (UNICA/MENSAL/BIMESTRAL/TRIMESTRAL/SEMESTRAL/ANUAL)
    meses_recorrencia: PositiveIntegerField (null=True, blank=True)
    ocorrencia_atual: PositiveIntegerField (default=1)
    data_vencimento: DateField
    status: CharField (PENDENTE/PAGO)
    data_pagamento: DateField (null=True, blank=True)
    privada: BooleanField (default=False)  # ✨ Novo
    criado_por: ForeignKey(User)
    criado_em: DateTimeField
    atualizado_em: DateTimeField
```

### Fatura
```python
class Fatura(models.Model):
    # ... campos existentes ...
    privada: BooleanField (default=False)  # ✨ Novo
```

### Consultoria
```python
class Consultoria(models.Model):
    # ... campos existentes ...
    privada: BooleanField (default=False)  # ✨ Novo
```

### AluguelIPv4
```python
class AluguelIPv4(models.Model):
    # ... campos existentes ...
    privada: BooleanField (default=False)  # ✨ Novo
```

### VendaEquipamento
```python
class VendaEquipamento(models.Model):
    # ... campos existentes ...
    privada: BooleanField (default=False)  # ✨ Novo
```

---

## 🔌 API Endpoints

### Despesas
| Método | Endpoint | Descrição |
|--------|----------|-----------|
| POST | `/financeiro/api/despesa/criar/` | Criar despesa |
| GET | `/financeiro/api/despesa/listar/` | Listar despesas (com filtro privacidade) |
| POST | `/financeiro/api/despesa/{id}/editar/` | Editar despesa |
| POST | `/financeiro/api/despesa/{id}/pagar/` | Marcar como paga (gera próxima se recorrente) |
| DELETE | `/financeiro/api/despesa/{id}/deletar/` | Deletar despesa |
| GET | `/financeiro/api/despesas/dashboard/` | Dashboard de despesas |

### Faturas
| Método | Endpoint | Descrição |
|--------|----------|-----------|
| POST | `/financeiro/api/fatura/criar/` | Criar fatura |
| GET | `/financeiro/api/fatura/listar/` | Listar faturas (com filtro privacidade) |
| GET | `/financeiro/api/fatura/{id}/` | Visualizar detalhes fatura |

---

## 🎯 Fluxo de Recorrência de Despesas

### 1. Criar Despesa Recorrente
```
Usuário cria despesa "Aluguel"
├─ Recorrência: MENSAL
├─ Meses: 12
└─ Ocorrência: 1/1

Salvo em BD:
└─ Despesa (id=1, nome="Aluguel", ocorrencia_atual=1, meses_recorrencia=12, status=PENDENTE)
```

### 2. Marcar como Pago
```
Usuário clica ✓ em "Aluguel"
├─ Confirma pagamento
├─ Sistema marca como PAGO
├─ Sistema gera próxima:
│  └─ Despesa (id=2, nome="Aluguel", ocorrencia_atual=2/12, status=PENDENTE, data_vencimento=próximo mês)
└─ Aluguel original sai da lista (status=PAGO)

Lista agora mostra:
└─ Aluguel (2/12 mensal) - PENDENTE
```

### 3. Após 12 Pagamentos
```
Último pagamento (ocorrência 12):
├─ Sistema marca como PAGO
├─ Sistema verifica: ocorrencia_atual (12) >= meses_recorrencia (12)
├─ Recorrência encerrada ✅
└─ Próxima NÃO é gerada
```

### 4. Recorrência Indefinida
```
Se meses_recorrencia = NULL (campo vazio):
├─ Cria: 1, 2, 3, 4, 5, 6...
└─ Continua indefinidamente até deletar
```

---

## 🔒 Fluxo de Privacidade

### Despesas
```
Usuário A (criador) cria Despesa com "Privada" marcado:
├─ Visível para: Usuário A (criador) ✅
└─ Visível para: Outros usuários ❌

Usuário A cria Despesa sem "Privada":
├─ Visível para: Todos os usuários ✅
└─ Privacidade: Pública
```

### Faturas, Consultorias, Aluguéis, Vendas
```
Admin cria Fatura com "Privada" marcado:
├─ Visível para: Admin (staff) ✅
└─ Visível para: Usuários normais ❌

Admin cria Fatura sem "Privada":
├─ Visível para: Todos (staff + usuários) ✅
└─ Privacidade: Pública
```

---

## 📱 Interface do Usuário

### Modal "Nova Despesa"
```
┌─────────────────────────────────┐
│ Nova Despesa                    │
├─────────────────────────────────┤
│ Nome *                          │
│ [Conta de Energia]              │
│                                 │
│ Valor (R$) *    Vencimento *    │
│ [500.00]        [01/06/2026]    │
│                                 │
│ Categoria              Recorrência │
│ [Infraestrutura]      [Mensal]  │
│                                 │
│ [checkbox] 🔒 Privada           │
│ (apenas você vê)                │
│ Desmarcado = visível para todos │
│                                 │
│ Total de meses (vazio=indefinido)│
│ [12]                            │
│                                 │
│ Observações                     │
│ [_______________________]       │
│                                 │
│           [Cancelar] [Salvar]   │
└─────────────────────────────────┘
```

### Listagem de Despesas
```
┌──────────────────────────────────────────────────────────────┐
│ NOME | RECORRÊNCIA | VALOR | VENCIMENTO | STATUS            │
├──────────────────────────────────────────────────────────────┤
│ Aluguel 🔒        │ 2/12 mensal │ R$ 5.000│ 15/06/2026│ ✓ │
│ Infraestrutura    │ MENSAL      │ R$ 1.500│ 10/06/2026│ ⚠  │
│ Internet          │ ─           │ R$ 300  │ 20/06/2026│ ⏳  │
└──────────────────────────────────────────────────────────────┘

Legenda:
✓ = Pago
⚠️ = Vencido (pendente)
⏳ = Pendente
🔒 = Privada
```

---

## 🔧 Instalação / Aplicação de Migrações

```bash
# Aplicar todas as migrações do módulo financeiro
python manage.py migrate financeiro

# Migrações aplicadas:
# ✅ 0001_initial
# ✅ 0002_vendaequipamento
# ✅ 0003_pagamento_comprovante_pdf_gerado_and_more
# ✅ 0004_despesa
# ✅ 0005_despesa_recorrencia
# ✅ 0006_despesa_privada
# ✅ 0007_fatura_privada
# ✅ 0008_consultoria_privada
# ✅ 0009_aluguelipv4_privada
# ✅ 0010_vendaequipamento_privada
```

---

## 📈 Dados Técnicos

### Modelos Afetados
- `Despesa` (5 campos adicionados)
- `Fatura` (1 campo adicionado)
- `Consultoria` (1 campo adicionado)
- `AluguelIPv4` (1 campo adicionado)
- `VendaEquipamento` (1 campo adicionado)

### Views Modificadas
- `api_criar_despesa`: Agora aceita campo `privada`
- `api_editar_despesa`: Agora suporta edição de `privada` e auto-geração de recorrências
- `api_pagar_despesa`: Marca como pago e gera próxima ocorrência
- `api_listar_despesas`: Filtra por privacidade do usuário
- `api_criar_fatura`: Agora aceita campo `privada`
- `api_listar_faturas`: Filtra por privacidade (staff only)
- `api_visualizar_fatura`: Validação de acesso por privacidade

### Templates Modificados
- Modal "Nova Despesa": Adicionado checkbox privada
- Modal "Editar Despesa": Adicionado checkbox privada + info de recorrência
- Modal "Nova Fatura": Adicionado checkbox privada
- Modal "Nova Consultoria": Adicionado checkbox privada
- Modal "Novo Aluguel": Adicionado checkbox privada
- Modal "Nova Venda": Adicionado checkbox privada
- Listagem Despesas: Layout ajustado, indicador visual privacidade

### Admin Panel Afetado
- DespesaAdmin: Filtro e display de `privada`
- FaturaAdmin: Filtro e display de `privada`
- ConsultoriaAdmin: Filtro e display de `privada`
- AluguelIPv4Admin: Filtro e display de `privada`
- VendaEquipamentoAdmin: Filtro e display de `privada`

---

## ⚠️ Problemas Conhecidos / TODO

### Resolvidos ✅
- ✅ Nome da despesa sobrepondo data de vencimento (aumentado coluna para 180px)
- ✅ Migração com nome incorreto do modelo (alugueipv4 → aluguelipv4)
- ✅ Servidor 502 Bad Gateway (Gunicorn reiniciado)

### Pendente de Investigação
- Auto-migration TopologiaDiagrama: "no unique or exclusion constraint matching the ON CONFLICT specification" - não afeta financeiro
- RuntimeWarning sobre ProxyServer reregistration - não afeta funcionalidade

---

## 📝 Commits Documentados

| Hash | Data | Descrição |
|------|------|-----------|
| `95fd3074b` | 01/06 | fix: aumenta drasticamente coluna de vencimento |
| `189ee3613` | 01/06 | fix: aumenta coluna de vencimento |
| `42aeb341c` | 01/06 | feat: privacidade para consultorias, aluguéis e vendas |
| `2e0438ad7` | 01/06 | feat: privacidade de faturas com indicador visual |
| `943fb667e` | 01/06 | feat: privacidade de despesas e melhorias de layout |
| `cdb719cc6` | 01/06 | feat: melhorias no sistema de despesas com recorrência |

---

## 🔗 Referências

- **Models**: `/opt/crm/financeiro/models.py` (linhas 513-579 Despesa)
- **Views**: `/opt/crm/financeiro/views.py` (APIs de despesa, fatura, consultoria, etc)
- **Templates**: `/opt/crm/financeiro/templates/financeiro/dashboard.html`
- **Migrations**: `/opt/crm/financeiro/migrations/` (0004-0010)
- **URLs**: `/opt/crm/financeiro/urls.py`
- **Admin**: `/opt/crm/financeiro/admin.py`

---

## Cobrança via WhatsApp — Diagnóstico e Correção (2026-06-16)

**Arquivos:** `financeiro/tasks.py`, `financeiro/whatsapp.py`, `financeiro/models.py`

### "Alerta de cobrança não está sendo enviado"

Não era um bug de código: a task `financeiro.tasks.enviar_alertas_whatsapp` está
agendada corretamente no Celery Beat (seg–sex às 8:30,
`crm/celery.py`), mas a flag `ConfiguracaoFinanceira.wa_ativo` ("Alertas WhatsApp
ativos", configurável no dashboard) estava desativada — valor padrão do campo é
`False`. A task sempre executava e sempre pulava silenciosamente
(`financeiro.tasks: alertas WhatsApp desativados, pulando.` no log), sem nunca
verificar faturas. Ativada manualmente e disparo manual confirmou o fluxo completo
funcionando (2 envios ok, 2 com erro transitório da Evolution API, resolvidos em
nova tentativa).

### Mensagem de cobrança não informava o serviço (venda de equipamento)

**Causa raiz:** o model `Fatura` nunca teve, de fato, o campo M2M
`vendas_equipamentos` que o código já tentava usar. Em
`financeiro/views.py` (`gerar_faturas_venda_equipamento`), o código fazia:

```python
if hasattr(fatura, 'vendas_equipamentos'):
    fatura.vendas_equipamentos.add(venda)
```

Como o campo nunca existiu em `Fatura`, `hasattr` sempre retornava `False` e a venda
nunca era vinculada à fatura — `_coletar_itens()` em `financeiro/whatsapp.py`
(usado para montar o detalhamento da mensagem de cobrança) nunca encontrava o item,
e a cobrança saía sem nenhuma indicação de qual serviço/produto estava sendo cobrado.

**Correção:**
- Adicionado `vendas_equipamentos = models.ManyToManyField('VendaEquipamento', blank=True, related_name='faturas')`
  em `Fatura` (migração `0019_fatura_vendas_equipamentos`).
- `_coletar_itens()` agora formata o item como
  `"{descrição} ({N}x — início {data_inicio})"`, deixando claro de qual contrato a
  parcela cobrada faz parte.
- **Backfill:** as 55 faturas de venda de equipamento já existentes e sem vínculo
  foram religadas retroativamente à sua `VendaEquipamento` de origem, casando por
  cliente + valor da parcela + data de vencimento esperada (todas resolvidas sem
  ambiguidade).

---

**Última atualização:** 16/06/2026
**Versão:** 2.0 (com recorrências e privacidade)
