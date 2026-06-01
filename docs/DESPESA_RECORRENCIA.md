# Sistema de Recorrência de Despesas — Documentação Técnica

## 📋 Visão Geral

O sistema de recorrência permite criar despesas que se repetem automaticamente em intervalos regulares (mensal, bimestral, etc.) com controle de quantos ciclos devem repetir.

**Implementação:** Junho 2026
**Modelo:** `Despesa` em `financeiro/models.py`
**Migration:** `0005_despesa_recorrencia`

---

## 🏗️ Arquitetura

### Modelos de Dados

#### Campos da Despesa

```python
class Despesa(models.Model):
    # ... campos existentes ...
    
    # Recorrência
    RECORRENCIA_CHOICES = [
        ('UNICA', 'Única'),
        ('MENSAL', 'Mensal'),
        ('BIMESTRAL', 'Bimestral (2 meses)'),
        ('TRIMESTRAL', 'Trimestral (3 meses)'),
        ('SEMESTRAL', 'Semestral (6 meses)'),
        ('ANUAL', 'Anual (12 meses)'),
    ]
    
    recorrencia = models.CharField(
        max_length=20,
        choices=RECORRENCIA_CHOICES,
        default='UNICA'
    )
    
    # Quantos ciclos a despesa vai se repetir
    # NULL = indefinidamente
    meses_recorrencia = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Número de ciclos. Vazio = indefinido"
    )
    
    # Qual é a ocorrência atual (1, 2, 3...)
    ocorrencia_atual = models.PositiveIntegerField(
        default=1
    )
    
    # Status da despesa
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('PAGO', 'Pago'),
    ]
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='PENDENTE'
    )
    
    # Quando foi pago
    data_pagamento = models.DateField(
        null=True,
        blank=True
    )
    
    # Campo existente
    data_vencimento = models.DateField()
```

### Exemplo de Dados

```
Despesa: "Aluguel do escritório"
├─ recorrencia = 'MENSAL'
├─ meses_recorrencia = 12
├─ ocorrencia_atual = 1
├─ status = 'PENDENTE'
├─ data_vencimento = 2026-06-15
├─ data_pagamento = NULL
└─ Resultado: Será gerada mais 11 vezes (total 12)
```

---

## 🔄 Fluxo de Funcionamento

### 1. Criação Inicial

```
Usuário preenche formulário:
├─ Nome: "Energia Elétrica"
├─ Valor: R$ 500.00
├─ Vencimento: 10/06/2026
├─ Recorrência: MENSAL
├─ Total de meses: 12
└─ Clica [Salvar]

Sistema:
├─ Valida campos obrigatórios
├─ Cria Despesa 1:
│  ├─ nome = "Energia Elétrica"
│  ├─ valor = 500.00
│  ├─ data_vencimento = 2026-06-10
│  ├─ recorrencia = 'MENSAL'
│  ├─ meses_recorrencia = 12
│  ├─ ocorrencia_atual = 1
│  ├─ status = 'PENDENTE'
│  └─ data_pagamento = NULL
└─ Salva no banco de dados

Resultado na lista:
└─ Energia Elétrica (1/12 mensal) - Pendente - Vencimento: 10/06/2026
```

### 2. Marcando como Pago

```
Usuário clica ✓ (botão Pagar) na Despesa 1

Confirmação no modal:
├─ "Confirmar pagamento?"
├─ Botão [Sim] [Não]
└─ Clica [Sim]

Sistema (api_pagar_despesa):
├─ 1. Marca Despesa 1 como PAGO:
│  ├─ status = 'PAGO'
│  └─ data_pagamento = hoje (2026-06-10)
│
├─ 2. Verifica recorrência:
│  ├─ if recorrencia == 'UNICA':
│  │  └─ Não faz nada (não cria próxima)
│  │
│  └─ else (mensal, bimestral, etc):
│     ├─ if ocorrencia_atual < meses_recorrencia:
│     │  └─ Cria próxima Despesa (Despesa 2):
│     │     ├─ Copia campos: nome, valor, categoria, descricao, privada
│     │     ├─ ocorrencia_atual = 2
│     │     ├─ meses_recorrencia = 12
│     │     ├─ recorrencia = 'MENSAL'
│     │     ├─ data_vencimento = 2026-06-10 + 1 mês = 2026-07-10
│     │     ├─ status = 'PENDENTE'
│     │     └─ data_pagamento = NULL
│     │
│     └─ else if ocorrencia_atual >= meses_recorrencia:
│        └─ Não cria (recorrência encerrada)
│
└─ Retorna JSON:
   ├─ sucesso: true
   ├─ mensagem: "Despesa 1 marcada como paga"
   ├─ proxima_gerada: true
   └─ proxima_ocorrencia: 2/12
```

### 3. Listagem com Filtro

```
Usuário acessa aba Despesas

API retorna:
├─ Despesa 1 (1/12 mensal) - PAGO - Data pag: 10/06
│  └─ Não aparece mais na lista (filtro: status='PENDENTE')
│
└─ Despesa 2 (2/12 mensal) - PENDENTE - Vencimento: 10/07

Comportamento:
├─ Listagem mostra apenas PENDENTE por padrão
├─ Pode filtrar para mostrar também PAGOS (checkbox)
└─ Clicando [Histórico], mostra todas as ocorrências
```

### 4. Recorrência Indefinida

```
Criação:
├─ Recorrência: MENSAL
├─ Total de meses: [vazio/NULL]
└─ Significado: Vai gerar indefinidamente

Comportamento:
├─ Despesa 1 marcada como pago
│  └─ Cria Despesa 2
│
├─ Despesa 2 marcada como pago
│  └─ Cria Despesa 3
│
└─ Continua indefinidamente até:
   └─ Usuário deletar a despesa
      └─ Não gera mais (quebra a cadeia)
```

### 5. Encerramento de Recorrência

```
Cenário: 12 despesas mensais, última é a número 12

Usuário marca Despesa 12 como pago:
├─ status = 'PAGO'
├─ data_pagamento = hoje
│
└─ Sistema verifica:
   ├─ ocorrencia_atual (12) >= meses_recorrencia (12) ✓
   └─ Recorrência encerrada!
      └─ Não cria Despesa 13

Resultado:
├─ Despesas 1-11: PAGO (não aparecem na lista)
└─ Despesa 12: PAGO (aparece no histórico)
   └─ Fim da recorrência ✅
```

---

## 📊 Cálculo de Data de Vencimento

### Intervalo em Meses

| Recorrência | Intervalo | Exemplo |
|-------------|-----------|---------|
| UNICA | - | 1x (sem recorrência) |
| MENSAL | +1 mês | 10/06 → 10/07 → 10/08 |
| BIMESTRAL | +2 meses | 10/06 → 10/08 → 10/10 |
| TRIMESTRAL | +3 meses | 10/06 → 10/09 → 10/12 |
| SEMESTRAL | +6 meses | 10/06 → 10/12 → 10/06 (ano seguinte) |
| ANUAL | +12 meses | 10/06/2026 → 10/06/2027 → 10/06/2028 |

### Implementação em Python

```python
from dateutil.relativedelta import relativedelta

# Mapping de recorrência para meses
MESES_MAP = {
    'MENSAL': 1,
    'BIMESTRAL': 2,
    'TRIMESTRAL': 3,
    'SEMESTRAL': 6,
    'ANUAL': 12,
}

def calcular_proximo_vencimento(data_atual, recorrencia):
    """Retorna nova data de vencimento"""
    meses = MESES_MAP.get(recorrencia, 0)
    return data_atual + relativedelta(months=meses)

# Exemplo:
data_pago = date(2026, 6, 10)
proximo = calcular_proximo_vencimento(data_pago, 'MENSAL')
print(proximo)  # 2026-07-10
```

---

## 🔌 API Endpoints

### POST `/financeiro/api/despesa/criar/`

**Descrição:** Criar nova despesa (único ou recorrente)

**Parâmetros (form-data):**
```
nome: string (obrigatório)
valor: decimal (obrigatório)
categoria: string (obrigatório)
data_vencimento: date (YYYY-MM-DD, obrigatório)
descricao: string (opcional)
recorrencia: string (padrão='UNICA') - UNICA|MENSAL|BIMESTRAL|TRIMESTRAL|SEMESTRAL|ANUAL
meses_recorrencia: integer (opcional) - NULL = indefinido
privada: boolean (padrão=false)
```

**Exemplo de Request:**
```json
POST /financeiro/api/despesa/criar/
Content-Type: application/x-www-form-urlencoded

nome=Energia+Elétrica&
valor=500.00&
categoria=INFRAESTRUTURA&
data_vencimento=2026-06-10&
recorrencia=MENSAL&
meses_recorrencia=12&
privada=false
```

**Response (Sucesso):**
```json
{
  "sucesso": true,
  "despesa_id": 42,
  "ocorrencia_atual": 1,
  "mensagem": "Despesa criada com sucesso"
}
```

### POST `/financeiro/api/despesa/{id}/pagar/`

**Descrição:** Marcar despesa como paga e gerar próxima se recorrente

**Parâmetros (JSON body):**
```json
{
  "confirmar": true
}
```

**Response (Sucesso):**
```json
{
  "sucesso": true,
  "despesa_id": 42,
  "status": "PAGO",
  "data_pagamento": "2026-06-10",
  "proxima_gerada": true,
  "proxima_ocorrencia": "2/12",
  "proxima_despesa_id": 43,
  "mensagem": "Despesa marcada como paga. Próxima ocorrência gerada."
}
```

**Response (Última ocorrência):**
```json
{
  "sucesso": true,
  "despesa_id": 53,
  "status": "PAGO",
  "data_pagamento": "2026-05-10",
  "proxima_gerada": false,
  "mensagem": "Despesa marcada como paga. Recorrência encerrada."
}
```

### GET `/financeiro/api/despesa/listar/`

**Descrição:** Listar despesas pendentes (ou todas com filtro)

**Query Parameters:**
```
apenas_pendentes: boolean (padrão=true) - false mostra também pagas
```

**Response:**
```json
{
  "sucesso": true,
  "despesas": [
    {
      "id": 42,
      "nome": "Energia Elétrica",
      "valor": 500.00,
      "categoria": "INFRAESTRUTURA",
      "data_vencimento": "2026-06-10",
      "recorrencia": "MENSAL",
      "meses_recorrencia": 12,
      "ocorrencia_atual": 2,
      "status": "PENDENTE",
      "privada": false,
      "criado_por": "user@example.com"
    }
  ]
}
```

### POST `/financeiro/api/despesa/{id}/editar/`

**Descrição:** Editar despesa pendente

**Parâmetros:** Mesmo de `/criar/` (apenas valores alterados)

**Restrições:**
- Apenas despesas com `status='PENDENTE'` podem ser editadas
- Não pode editar `recorrencia` ou `meses_recorrencia` de recorrência em andamento
- Histórico de recorrências anteriores não é afetado

---

## 🎨 Interface do Usuário

### Modal "Nova Despesa"

```html
<form id="formNovaDespesa">
  <div class="form-group">
    <label>Nome da Despesa *</label>
    <input type="text" name="nome" placeholder="Ex: Aluguel do escritório">
  </div>
  
  <div class="form-row">
    <div class="form-group col">
      <label>Valor (R$) *</label>
      <input type="number" name="valor" step="0.01" placeholder="0.00">
    </div>
    <div class="form-group col">
      <label>Data de Vencimento *</label>
      <input type="date" name="data_vencimento">
    </div>
  </div>
  
  <div class="form-row">
    <div class="form-group col">
      <label>Categoria *</label>
      <select name="categoria">
        <option value="">-- Selecionar --</option>
        <option value="INFRAESTRUTURA">Infraestrutura</option>
        <option value="PESSOAL">Pessoal</option>
        <option value="SERVICOS">Serviços</option>
        <option value="ADMINISTRATIVO">Administrativo</option>
        <option value="FISCAL">Fiscal</option>
        <option value="OUTROS">Outros</option>
      </select>
    </div>
    <div class="form-group col">
      <label>Recorrência</label>
      <select name="recorrencia">
        <option value="UNICA">Única</option>
        <option value="MENSAL">Mensal</option>
        <option value="BIMESTRAL">Bimestral (2 meses)</option>
        <option value="TRIMESTRAL">Trimestral (3 meses)</option>
        <option value="SEMESTRAL">Semestral (6 meses)</option>
        <option value="ANUAL">Anual (12 meses)</option>
      </select>
    </div>
  </div>
  
  <div class="form-row">
    <div class="form-group col">
      <label>Total de Meses</label>
      <input type="number" name="meses_recorrencia" min="1" 
             placeholder="Vazio = indefinido">
      <small>Número de ciclos que a despesa vai se repetir</small>
    </div>
  </div>
  
  <div class="form-group">
    <label>
      <input type="checkbox" name="privada">
      🔒 Privada (apenas você vê)
    </label>
    <small>Desmarcado = visível para todos</small>
  </div>
  
  <div class="form-group">
    <label>Observações</label>
    <textarea name="descricao" placeholder="Notas adicionais..."></textarea>
  </div>
  
  <div class="modal-footer">
    <button type="button" class="btn btn-secondary" data-dismiss="modal">Cancelar</button>
    <button type="submit" class="btn btn-primary">Salvar Despesa</button>
  </div>
</form>
```

### Listagem de Despesas

```
┌─────────────────────────────────────────────────────────────────┐
│ 📊 DESPESAS OPERACIONAIS                    [+ Nova] [Histórico]│
├─────────────────────────────────────────────────────────────────┤
│ NOME                    │ RECOR    │ VALOR   │ VENCIMENTO │ AÇÃO │
├─────────────────────────┼──────────┼─────────┼────────────┼──────┤
│ Aluguel Escritório 🔒   │ 2/12 ▲   │ 5.000   │ 15/06/2026 │ ✓ ⋮  │
│ Energia Elétrica        │ MENSAL   │ 500     │ 10/06/2026 │ ✓ ⋮  │
│ Internet e Tel          │ ─        │ 300     │ 20/06/2026 │ ✓ ⋮  │
│ Folha Pessoal           │ MENSAL   │ 12.000  │ 30/06/2026 │ ✓ ⋮  │
│                         │          │         │ (VENCIDO)  │      │
├─────────────────────────┴──────────┴─────────┴────────────┴──────┤
│ Legenda: ▲=Recorrendo  ─=Única  🔒=Privada                      │
└─────────────────────────────────────────────────────────────────┘

Ações ao clicar ⋮:
├─ ✏️ Editar
├─ 🔒 Privacidade
├─ 📋 Histórico de Recorrências
├─ 🗑️ Deletar
└─ 📊 Estatísticas
```

### Botões de Ação

```
✓ Pagar
├─ Marca como PAGO
├─ Gera próxima ocorrência
└─ Saiu da lista

⋮ Menu
├─ Editar
├─ Duplicar
├─ Histórico
└─ Deletar

▲ Recorrência (badge)
└─ Clicável para ver histórico de ocorrências
```

---

## 💾 Migração

**Arquivo:** `financeiro/migrations/0005_despesa_recorrencia.py`

```python
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0004_despesa'),
    ]

    operations = [
        migrations.AddField(
            model_name='despesa',
            name='recorrencia',
            field=models.CharField(
                choices=[
                    ('UNICA', 'Única'),
                    ('MENSAL', 'Mensal'),
                    ('BIMESTRAL', 'Bimestral (2 meses)'),
                    ('TRIMESTRAL', 'Trimestral (3 meses)'),
                    ('SEMESTRAL', 'Semestral (6 meses)'),
                    ('ANUAL', 'Anual (12 meses)'),
                ],
                default='UNICA',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='despesa',
            name='meses_recorrencia',
            field=models.PositiveIntegerField(
                blank=True,
                help_text='Número de ciclos. Vazio = indefinido',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='despesa',
            name='ocorrencia_atual',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='despesa',
            name='status',
            field=models.CharField(
                choices=[('PENDENTE', 'Pendente'), ('PAGO', 'Pago')],
                default='PENDENTE',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='despesa',
            name='data_pagamento',
            field=models.DateField(blank=True, null=True),
        ),
    ]
```

**Aplicação:**
```bash
python manage.py migrate financeiro
```

---

## 🐛 Tratamento de Erros

### Erro: Criar despesa com data no passado

```
POST /financeiro/api/despesa/criar/
data_vencimento=2026-01-01

Response:
{
  "sucesso": false,
  "erro": "Data de vencimento não pode ser anterior a hoje"
}
```

### Erro: Editar despesa já paga

```
POST /financeiro/api/despesa/42/editar/
(Despesa 42 tem status='PAGO')

Response:
{
  "sucesso": false,
  "erro": "Não é possível editar despesa já paga"
}
```

### Erro: Deletar despesa com recorrência ativa

```
DELETE /financeiro/api/despesa/42/deletar/
(Despesa 42 é ocorrência 1 de uma cadeia)

Comportamento:
├─ Aviso: "Deletar vai quebrar a recorrência"
├─ Usuário confirma
└─ Deletada apenas a despesa 42
   └─ Próximas (43, 44...) não são deletadas
```

---

## 📈 Exemplos Reais

### Exemplo 1: Recorrência Mensal (12 meses)

```
Despesa: Aluguel do escritório
├─ Valor: R$ 5.000
├─ Recorrência: MENSAL
├─ Total: 12 ocorrências
└─ Início: 2026-06-10

Calendário:
├─ Junho:   10 - Despesa 1 (1/12)  - PENDENTE → [Pagar] → PAGO
├─ Julho:   10 - Despesa 2 (2/12)  - PENDENTE → [Pagar] → PAGO
├─ Agosto:  10 - Despesa 3 (3/12)  - PENDENTE
├─ Setembro: 10 - Despesa 4 (4/12)
├─ ...
└─ Maio:    10 - Despesa 12 (12/12) - [Última] → PAGO → FIM

Status final:
├─ Despesas 1-11: PAGO (arquivo)
└─ Despesa 12: PAGO (última)
   └─ Recorrência encerrada ✓
```

### Exemplo 2: Recorrência Indefinida

```
Despesa: Manutenção de servidor
├─ Valor: R$ 1.000
├─ Recorrência: TRIMESTRAL
├─ Total: [vazio - indefinido]
└─ Início: 2026-06-01

Comportamento:
├─ Trimestre 1 (jun-ago): Despesa 1 → PAGO → cria Despesa 2
├─ Trimestre 2 (set-nov): Despesa 2 → PAGO → cria Despesa 3
├─ Trimestre 3 (dez-fev): Despesa 3 → PAGO → cria Despesa 4
├─ ... (continua indefinidamente)
└─ Até usuário deletar ou mudar configuração

Quando parar:
├─ Usuário delete a despesa ativa
├─ Próximas não são geradas
└─ Histórico fica intacto (arquivo)
```

### Exemplo 3: Recorrência com Privacidade

```
Despesa: Consultoria confidencial
├─ Valor: R$ 2.000
├─ Recorrência: SEMESTRAL
├─ Total: 2 (12 meses)
├─ Privada: ✅ SIM
└─ Criador: Diretor Geral

Visibilidade:
├─ Diretor Geral: Vê ambas (1/2 e 2/2) ✓
├─ Gerente Financeiro: Vê ícone 🔒, não acessa ✗
└─ Usuário comum: Não vê nem ícone ✗
```

---

## 🔐 Permissões

### Criar Despesa
- ✅ Qualquer usuário autenticado
- ✅ Pode marcar como privada (visível apenas p/ criador)

### Editar Despesa
- ✅ Criador da despesa
- ✅ Admin/Staff
- ❌ Outros usuários

### Deletar Despesa
- ✅ Criador da despesa
- ✅ Admin/Staff
- ❌ Outros usuários

### Pagar Despesa
- ✅ Qualquer usuário autenticado
- ✅ Gera próxima automaticamente

### Visualizar Despesa Privada
- ✅ Criador da despesa
- ✅ Admin/Staff
- ❌ Outros usuários

---

## 📚 Referências

- [Módulo Financeiro (completo)](FINANCEIRO.md)
- [Django DateField Documentation](https://docs.djangoproject.com/en/stable/ref/models/fields/#datefield)
- [python-dateutil](https://dateutil.readthedocs.io/) para cálculos de data

---

**Última atualização:** 01/06/2026
**Versão:** 1.0 (Release)
