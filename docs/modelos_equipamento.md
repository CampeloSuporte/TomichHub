# Modelos de Equipamento — Documentação Técnica

**Model:** `modelo_equipamento.Modelo_equipamento`  
**Task:** `clientes.tasks.detectar_modelos_via_backup`  
**Migration:** `clientes/migrations/0064_acesso_modelo_auto_em.py`  
**Atualizado em:** 2026-05-26

---

## Visão Geral

O sistema mantém um catálogo de modelos de equipamentos de rede (carrier-grade)
usado para:
- Identificar o fabricante/plataforma de cada acesso
- Selecionar o template de backup correto automaticamente
- Popular o editor de topologia com o ícone adequado

---

## Model `Modelo_equipamento`

```python
class Modelo_equipamento(models.Model):
    nome        = models.CharField(max_length=100, unique=True)
    fabricante  = models.CharField(max_length=100)
    descricao   = models.TextField(blank=True, null=True)
    data_criacao = models.DateTimeField(auto_now_add=True)
```

O campo `nome` é único — `get_or_create` pelo nome evita duplicatas.

---

## Portfólio Cadastrado (287 modelos — 2026-05-26)

| Fabricante | Qtd | Linhas cobertas |
|---|---|---|
| **Huawei** | 52 | NE40E/NE9000 (core), NE20E/ATN/CX600 (metro), CE5800–CE16816 (DC), S5720–S7712 (campus), MA5600T/MA5608T/MA5683T/MA5800 (OLT) |
| **Cisco** | 38 | ASR 1001-X→9922 (PE/core), NCS 540/5504/5516 (SR/SRv6), Catalyst 9300–9600, Nexus 3132–9516 |
| **Fiberhome** | 16 | AN5516/AN6000/AN6002/AN6516 (OLT), S5800/S6500 (switches) |
| **Datacom** | 20 | DM4605–DM4670 (OLT), DM700/DM2500/DM4100–DM4570 (switches), DM3110/DM3600 (roteadores) |
| **Intelbras** | 18 | OLT G4/G8/G16/1400/3200/4840E/8020i/8820i/8840G/14400G, SG/SF switches ISP |
| **Mikrotik** | 37 | CCR1009–CCR2216 (roteadores), CRS309–CRS518/CSS610 (switches) |
| **Juniper** | 46 | MX10–MX2020 (PE/core), PTX1000–PTX10016 (backbone), ACX710–ACX7100 (acesso/agregação), QFX5100–QFX10016 (DC switches), EX4300–EX9250 (campus) |
| **TP-Link** | 8 | TL-GP1004/1008/1016/3004/3008/3016 (OLTs GPON) |
| **VSOL** | 16 | V1600D4/D8/D16/D-M2 (GPON), V1600G2/G4/G8/G16 (XGS-PON), V2802G/V2804G (mini), V1600E8/E16 (EPON) |
| Outros | ~36 | ZTE, Parks, Raisecom, A10, Hillstone, Dell, IBM, etc. |

---

## Campo `modelo_auto_em` em `Acesso`

Adicionado pela migration `0064`. Controla o ciclo de detecção automática:

| Estado | Comportamento |
|---|---|
| `modelo_auto_em=None` | Nunca verificado — processa na próxima execução |
| `modelo_auto_em` preenchido + `modelo` preenchido | Modelo encontrado — **nunca mais verifica** |
| `modelo_auto_em` preenchido + `modelo=None` + < 3 dias | Verificado sem match recente — aguarda próximo ciclo |
| `modelo_auto_em` preenchido + `modelo=None` + > 3 dias | Re-verifica (pode ter novo backup ou novo modelo no banco) |

---

## Task `detectar_modelos_via_backup`

**Arquivo:** `clientes/tasks.py`  
**Agendamento:** a cada **3 dias** via Celery Beat

### Fluxo

```
Para cada Acesso com backup_habilitado=True:
  1. Verifica regras de skip (modelo já encontrado ou verificação recente)
  2. Busca o BackupLog mais recente com status='SUCESSO' e arquivo físico
  3. Lê os primeiros 8 KB do arquivo
  4. Extrai o model string via _extrair_modelo_do_backup()
  5. Faz match contra Modelo_equipamento via _match_modelo_equipamento()
  6. Se match → atualiza Acesso.modelo + modelo_auto_em
  7. Se não → marca modelo_auto_em (retenta no próximo ciclo)
```

### Padrões de Extração por Fabricante

| Fabricante | Padrão regex | Exemplo |
|---|---|---|
| **MikroTik RouterOS** | `# model = (.+)` | `# model = CCR2004-1G-12S+2XS` |
| **Cisco IOS-XE** | `Model Number\s*:\s*(\S+)` | `Model Number    : ASR1001-X` |
| **Cisco IOS-XR** | `^cisco\s+([A-Za-z0-9\-]+)\s+\(` | `cisco NCS5501 ()` |
| **Cisco IOS** | `^cisco\s+(\S+)\s+processor` | `cisco ASR1002X processor` |
| **Huawei VRP** | `HUAWEI\s+(NE\w+\|CE\w+\|MA\d+\w*\|...)` | `HUAWEI NE40E-X8` |
| **Huawei board** | `\b(NE\d+E?-?[A-Z0-9]+\|CE\d+...\|MA\d+...)\b` | `NE40E-X8` |
| **ZTE ZXAN** | `Platform\s*:\s*(ZXA10\s*\w+)` | `Platform: ZXA10 C650` |
| **Datacom DmOS** | `DmOS.*?running on\s+(\S+)` | `DmOS running on DM4380` |
| **A10 Networks** | `Thunder\s+([\w\-]+)\s` | `Thunder TH1040` |

### Algoritmo de Match

```python
def _match_modelo_equipamento(model_str):
    # 1. Coincidência exata (case-insensitive)
    qs = Modelo_equipamento.objects.filter(nome__iexact=model_str)
    if qs.exists(): return qs.first()

    # 2. Containment normalizado (sem espaços/hífens, lowercase)
    # Critério: string mais curta >= 5 chars contida na mais longa
    # Prefere o match com maior comprimento comum (mais específico)
    dev_norm = re.sub(r'[\s\-_/]', '', model_str).lower()
    for obj in Modelo_equipamento.objects.all():
        nome_norm = re.sub(r'[\s\-_/]', '', obj.nome).lower()
        if dev_norm in nome_norm or nome_norm in dev_norm:
            # retorna o com maior score
```

**Exemplos de match por containment:**
- `RB4011iGS+` → `Mikrotik RB4011iGS+RM` ✓ (rb4011igs em rb4011igsrm)
- `CCR1009-7G-1C-1S+` → `Mikrotik CCR1009-7G-1C-1S+` ✓ (exato normalizado)
- `NE8000M8` → `Huawei NE8000 M8` ✓

---

## Resultado da Detecção (2026-05-26)

| Métrica | Valor |
|---|---|
| Acessos processados | 267 |
| Modelos detectados automaticamente | 91 |
| Cobertura total de modelos | **99%** (817/824 acessos) |
| Sem arquivo de backup | 84 |
| Sem match no banco | 92 (maioria tinha modelo manual já) |
