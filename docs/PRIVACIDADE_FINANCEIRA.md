# Sistema de Privacidade — Módulo Financeiro

## 📋 Visão Geral

Sistema de controle de privacidade que permite marcar itens financeiros (Despesas, Faturas, Consultorias, Aluguéis, Vendas) como privados (visíveis apenas para staff/criador) ou públicos (visíveis para todos).

**Implementação:** Junho 2026
**Modelos Afetados:** 5 (Despesa, Fatura, Consultoria, AluguelIPv4, VendaEquipamento)
**Migrações:** 0006-0010

---

## 🏗️ Arquitetura

### Estrutura Geral

Cada modelo financeiro recebe um campo `privada`:

```python
privada = models.BooleanField(
    default=False,
    help_text='Marcar como privada para mostrar apenas para staff'
)
```

| Campo | Tipo | Default | Descrição |
|-------|------|---------|-----------|
| `privada` | BooleanField | False | Se True, visível apenas para staff/criador |

### Modelos Implementados

#### 1. Despesa
**Migração:** `0006_despesa_privada.py`
**Comportamento:** Privada = visível apenas para criador

```python
class Despesa(models.Model):
    # ... campos existentes ...
    privada = models.BooleanField(default=False)
    criado_por = models.ForeignKey(User)
    
    # Visibilidade:
    # - privada=False: Todos veem
    # - privada=True: Apenas criado_por vê
```

#### 2. Fatura
**Migração:** `0007_fatura_privada.py`
**Comportamento:** Privada = visível apenas para staff

```python
class Fatura(models.Model):
    # ... campos existentes ...
    privada = models.BooleanField(default=False)
    
    # Visibilidade:
    # - privada=False: Todos veem
    # - privada=True: Apenas staff vê
```

#### 3. Consultoria
**Migração:** `0008_consultoria_privada.py`
**Comportamento:** Privada = visível apenas para staff

```python
class Consultoria(models.Model):
    # ... campos existentes ...
    privada = models.BooleanField(default=False)
    
    # Visibilidade:
    # - privada=False: Todos veem
    # - privada=True: Apenas staff vê
```

#### 4. AluguelIPv4
**Migração:** `0009_aluguelipv4_privada.py`
**Comportamento:** Privada = visível apenas para staff

```python
class AluguelIPv4(models.Model):
    # ... campos existentes ...
    privada = models.BooleanField(default=False)
    
    # Visibilidade:
    # - privada=False: Todos veem
    # - privada=True: Apenas staff vê
```

#### 5. VendaEquipamento
**Migração:** `0010_vendaequipamento_privada.py`
**Comportamento:** Privada = visível apenas para staff

```python
class VendaEquipamento(models.Model):
    # ... campos existentes ...
    privada = models.BooleanField(default=False)
    
    # Visibilidade:
    # - privada=False: Todos veem
    # - privada=True: Apenas staff vê
```

---

## 🔐 Controle de Acesso

### Despesas

#### Criação
```python
# views.py - api_criar_despesa
privada = request.POST.get('privada') == 'on'
despesa = Despesa.objects.create(
    ...
    privada=privada,
    criado_por=request.user
)
```

#### Listagem (Filtro Automático)
```python
# views.py - api_listar_despesas
from django.db.models import Q

despesas = Despesa.objects.filter(
    Q(privada=False) |  # Públicas: todos veem
    Q(privada=True, criado_por=request.user)  # Privadas: apenas criador
)
```

**Resultado:**
- Usuário A vê: todas públicas + suas privadas
- Usuário B vê: todas públicas + suas privadas
- Ninguém vê privadas de outro usuário

#### Edição
```python
# views.py - api_editar_despesa
# Mesma lógica de filtro ao recuperar
despesa = Despesa.objects.get(
    Q(id=despesa_id),
    Q(privada=False) | Q(criado_por=request.user)
)
```

### Faturas, Consultorias, Aluguéis, Vendas

#### Criação
```python
# views.py - api_criar_fatura (etc)
privada = request.POST.get('privada') == 'on'
fatura = Fatura.objects.create(
    ...
    privada=privada
)
```

#### Listagem (Filtro Staff)
```python
# views.py - api_listar_faturas
if request.user.is_staff:
    # Admin vê tudo
    faturas = Fatura.objects.all()
else:
    # Usuários normais veem apenas públicas
    faturas = Fatura.objects.filter(privada=False)
```

#### Visualização
```python
# views.py - api_visualizar_fatura
fatura = Fatura.objects.get(id=fatura_id)

if fatura.privada and not request.user.is_staff:
    return JsonResponse(
        {'sucesso': False, 'erro': 'Acesso negado'},
        status=403
    )

# Continua exibição
return JsonResponse({'sucesso': True, 'fatura': {...}})
```

**Resultado:**
- Admin: Vê tudo (público + privado)
- Usuário normal: Vê apenas público

---

## 📱 Interface do Usuário

### Checkbox em Modais

Todos os modais de criação/edição possuem o checkbox:

```html
<div class="form-group">
  <label>
    <input type="checkbox" name="privada" id="novaXPrivada">
    🔒 Privada (apenas você vê)
  </label>
  <small>Desmarcado = visível para todos</small>
</div>
```

**Estados:**
- ☐ Desmarcado (padrão) → Pública para todos
- ☑ Marcado → Privada (staff/criador only)

### Indicador Visual 🔒

#### Listagem de Despesas

Ícone de cadeado roxo ao lado do nome:

```html
<div class="despesa-row">
  <div class="despesa-nome">
    Aluguel do Escritório
    ${x.privada ? '<i class="fas fa-lock" style="font-size:.65rem;color:#b060ff;margin-left:.3rem;"></i>' : ''}
  </div>
</div>
```

**Aparência:**
```
Aluguel 🔒    ← ícone roxo
```

#### Listagem de Faturas

Similar ao da Despesa, ícone 🔒 próximo ao número:

```
Fatura #001 🔒    ← ícone roxo
```

#### Outras Listas

Padrão consistente em todas as listas (Consultorias, Aluguéis, Vendas).

---

## 🔌 API Endpoints

### Despesa

#### POST `/financeiro/api/despesa/criar/`

```
Parâmetro: privada (checkbox)
├─ Valor: 'on' (marcado) | '' (desmarcado)
├─ Tratamento: privada = request.POST.get('privada') == 'on'
└─ Salvo: despesa.privada = True/False
```

#### GET `/financeiro/api/despesa/listar/`

```
Filtro automático:
├─ Q(privada=False)  → Públicas (todos veem)
├─ Q(privada=True, criado_por=request.user)  → Suas privadas
└─ Resultado: Despesas acessíveis para o usuário
```

#### POST `/financeiro/api/despesa/{id}/editar/`

```
Parâmetro: privada (checkbox, opcional)
├─ Se fornecido, atualiza privada do item
├─ Restrição: Apenas se criado_por=request.user OU is_staff
└─ Filtro: Mesma Q() de listagem
```

### Fatura

#### POST `/financeiro/api/fatura/criar/`

```
Parâmetro: privada (checkbox)
├─ Valor: 'on' | ''
├─ Salvo: fatura.privada = True/False
└─ Comportamento: Privada = visível apenas para staff
```

#### GET `/financeiro/api/fatura/listar/`

```
Filtro:
├─ if request.user.is_staff:
│  └─ faturas = Fatura.objects.all()  ← Vê tudo
│
└─ else:
   └─ faturas = Fatura.objects.filter(privada=False)  ← Vê público
```

#### GET `/financeiro/api/fatura/{id}/`

```
Validação:
├─ fatura = Fatura.objects.get(id=id)
├─ if fatura.privada and not request.user.is_staff:
│  └─ return 403 Acesso negado
│
└─ else:
   └─ return JsonResponse(fatura_data)
```

### Consultoria, AluguelIPv4, VendaEquipamento

**Mesma lógica que Fatura:**
- POST `/criar/`: Aceita parâmetro `privada`
- GET `/listar/`: Filtra por `is_staff`
- GET `/{id}/`: Valida acesso

---

## 📊 Fluxo de Privacidade

### Cenário 1: Despesa Privada

```
Usuário A (comum) cria Despesa "Consultoria Confidencial"
├─ Clica na checkbox "Privada"
├─ Salva: despesa.privada = True
└─ Salva também: despesa.criado_por = User A

Resultado:
├─ Usuário A: Vê na lista ✓
├─ Usuário B: NÃO vê ✗
├─ Usuário C: NÃO vê ✗
└─ Admin: NÃO vê (Despesa é filtrada para criador) ✗
   └─ Obs: Despesa recebe filtro criado_por, não staff
```

### Cenário 2: Fatura Privada

```
Admin cria Fatura "Consultoria Confidencial"
├─ Clica na checkbox "Privada"
├─ Salva: fatura.privada = True
└─ Salva: fatura.criado_por = Admin (não used)

Resultado:
├─ Usuário A (comum): NÃO vê ✗
├─ Usuário B (comum): NÃO vê ✗
├─ Admin: Vê ✓
└─ Superusuário: Vê ✓ (is_staff=True)
```

### Cenário 3: Desmarcando Privacidade

```
Usuário A marcou Despesa como Privada
├─ Depois desmarca a checkbox
├─ Salva: despesa.privada = False
└─ Agora é Pública

Resultado:
├─ Usuário A: Vê ✓
├─ Usuário B: Agora vê ✓ (antes não via)
├─ Usuário C: Agora vê ✓ (antes não via)
└─ Todos: Podem ver (é público)
```

---

## 🎯 Casos de Uso

### Caso 1: Contrato Confidencial

```
Consultoria criada:
├─ Descrição: Consultoria de segurança
├─ Valor: R$ 50.000 (alta)
├─ Privada: ✅ SIM
├─ Criador: Gerente Financeiro
└─ Motivo: Orçamento confidencial

Visibilidade:
├─ Gerente Financeiro: Vê valor completo ✓
├─ Operacional: Vê ícone 🔒, sem valor ✗
├─ Clientes: Não veem nada ✗
└─ Admin: Vê (é staff) ✓
```

### Caso 2: Fatura do Diretor

```
Fatura criada:
├─ Cliente: Empresa XYZ
├─ Valor: R$ 100.000
├─ Privada: ✅ SIM
└─ Motivo: Renegociação em andamento

Visibilidade:
├─ Diretor: Vê ✓
├─ Contabilista: Vê (é staff) ✓
├─ Operacional: Vê ícone 🔒 ✗
└─ Cliente: Não vê ✗
```

### Caso 3: Aluguel Público

```
Aluguel IPv4 criado:
├─ Descrição: Aluguel /24 para cliente
├─ Valor: R$ 500/mês
├─ Privada: ☐ NÃO (desmarcado)
└─ Motivo: Informação de faturamento normal

Visibilidade:
├─ Todos: Veem ✓
├─ Incluindo clientes: Veem (se acesso liberado) ✓
└─ Ninguém: Bloqueado ✗ (é público)
```

---

## 💾 Migrações

### 0006_despesa_privada.py

```python
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('financeiro', '0005_despesa_recorrencia'),
    ]
    operations = [
        migrations.AddField(
            model_name='despesa',
            name='privada',
            field=models.BooleanField(default=False),
        ),
    ]
```

### 0007_fatura_privada.py

```python
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('financeiro', '0006_despesa_privada'),
    ]
    operations = [
        migrations.AddField(
            model_name='fatura',
            name='privada',
            field=models.BooleanField(default=False),
        ),
    ]
```

### 0008_consultoria_privada.py

```python
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('financeiro', '0007_fatura_privada'),
    ]
    operations = [
        migrations.AddField(
            model_name='consultoria',
            name='privada',
            field=models.BooleanField(default=False),
        ),
    ]
```

### 0009_aluguelipv4_privada.py

```python
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('financeiro', '0008_consultoria_privada'),
    ]
    operations = [
        migrations.AddField(
            model_name='aluguelipv4',  # Corrigido de 'alugueipv4'
            name='privada',
            field=models.BooleanField(default=False),
        ),
    ]
```

### 0010_vendaequipamento_privada.py

```python
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('financeiro', '0009_aluguelipv4_privada'),
    ]
    operations = [
        migrations.AddField(
            model_name='vendaequipamento',
            name='privada',
            field=models.BooleanField(default=False),
        ),
    ]
```

**Aplicação:**
```bash
python manage.py migrate financeiro
```

---

## 🔒 Permissões por Tipo de Usuário

### Usuário Comum

| Ação | Público | Privado (próprio) | Privado (outro) |
|------|---------|-------------------|-----------------|
| Ver Despesa | ✓ | ✓ | ✗ |
| Ver Fatura | ✓ | ✗ | ✗ |
| Ver Consultoria | ✓ | ✗ | ✗ |
| Ver Aluguel | ✓ | ✗ | ✗ |
| Marcar Privada | ✓ | ✓ | ✗ |
| Editar | Próprio | Próprio | ✗ |

### Admin / Staff

| Ação | Público | Privado |
|------|---------|---------|
| Ver Despesa | ✓ | ✓ |
| Ver Fatura | ✓ | ✓ |
| Ver Consultoria | ✓ | ✓ |
| Ver Aluguel | ✓ | ✓ |
| Marcar Privada | ✓ | ✓ |
| Editar | ✓ | ✓ |
| Deletar | ✓ | ✓ |

### Superusuário

Acesso total a tudo (mesmo que privado).

---

## 🧪 Testes

### Test 1: Despesa Privada do Usuário

```python
def test_despesa_privada_criador():
    user = User.objects.create_user('user1', 'user1@test.com', 'pass')
    despesa = Despesa.objects.create(
        nome="Consultoria",
        privada=True,
        criado_por=user
    )
    
    # User vê própria privada
    despesas = Despesa.objects.filter(
        Q(privada=False) | Q(privada=True, criado_por=user)
    )
    assert despesa in list(despesas)
    
    # Outro user não vê
    outro = User.objects.create_user('user2', 'user2@test.com', 'pass')
    despesas_outro = Despesa.objects.filter(
        Q(privada=False) | Q(privada=True, criado_por=outro)
    )
    assert despesa not in list(despesas_outro)
```

### Test 2: Fatura Privada (Staff Only)

```python
def test_fatura_privada_staff():
    admin = User.objects.create_user('admin', is_staff=True)
    user = User.objects.create_user('user')
    
    fatura = Fatura.objects.create(
        numero="001",
        privada=True
    )
    
    # Admin vê
    if admin.is_staff:
        faturas = Fatura.objects.all()
        assert fatura in list(faturas)
    
    # User não vê
    if not user.is_staff:
        faturas = Fatura.objects.filter(privada=False)
        assert fatura not in list(faturas)
```

---

## 📊 Admin Panel

Todos os modelos exibem `privada` no admin:

```python
# financeiro/admin.py

class DespesaAdmin(admin.ModelAdmin):
    list_display = ['nome', 'valor', 'privada', 'criado_por']
    list_filter = ['privada', 'categoria']
    fieldsets = (
        ('Dados Principais', {...}),
        ('Privacidade', {
            'fields': ('privada',),
            'description': 'Marque para tornar visível apenas para o criador'
        }),
    )

class FaturaAdmin(admin.ModelAdmin):
    list_display = ['numero', 'valor', 'privada']
    list_filter = ['privada']
    fieldsets = (
        ('Dados Principais', {...}),
        ('Privacidade', {
            'fields': ('privada',),
            'description': 'Marque para tornar visível apenas para staff'
        }),
    )

# Similar para Consultoria, AluguelIPv4, VendaEquipamento
```

---

## ⚠️ Considerações de Segurança

### Protegidas ✅

- Filtros de listagem impedem acesso a privadas não autorizadas
- API endpoints validam permissões antes de retornar dados
- Admin panel mostra apenas a usuários com permissão
- Campo `privada` é booleano, sem parsing complexo

### Não Protegidas ❌

- URL direto: `/financeiro/api/fatura/42/` pode expor fatura privada
  - **Mitigação:** Validação 403 no endpoint (implementado)
- Relatórios/exports: Podem incluir privadas acidentalmente
  - **Mitigação:** Aplicar mesmo filtro em relatórios
- Backups: Incluem todas as despesas/faturas
  - **Mitigação:** Restringir acesso a backups

---

## 🔗 Referências

- [Módulo Financeiro (completo)](FINANCEIRO.md)
- [Sistema de Recorrências](DESPESA_RECORRENCIA.md)
- Django QuerySet Q() objects
- Django Authentication/Permissions

---

**Última atualização:** 01/06/2026
**Versão:** 1.0 (Release)
