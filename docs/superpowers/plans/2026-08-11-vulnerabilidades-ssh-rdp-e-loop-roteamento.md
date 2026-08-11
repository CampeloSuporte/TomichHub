# Portas SSH/RDP no AmpScan + Loop de Roteamento — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar as portas 22 (SSH) e 3389 (RDP) à varredura AmpScan com um status `exposto`
separado de `vulneravel`, e construir do zero uma feature de detecção de loop de roteamento por
`BlocoIP` usando `mtr --json`.

**Architecture:** Duas extensões independentes na aba Vulnerabilidades (`clientes/templates/listar.html`).
Feature 1 estende o pipeline AmpScan já existente (runner Rust + `AmpScanResultado`/`AmpScanExecucaoLog`
+ views + template). Feature 2 replica o mesmo padrão arquitetural (models, tasks Celery, views,
template) só que sem depender do runner Rust — chama `mtr` diretamente via `subprocess` a partir de
uma task Python.

**Tech Stack:** Django 4/5, Celery (`shared_task` + Celery Beat em memória), Rust (`tools/ampscan_runner`),
`mtr 0.95` (já instalado no servidor, suporta `--json`), JS vanilla (fetch + template strings, sem
framework front-end) no template `listar.html`.

**Spec:** `docs/superpowers/specs/2026-08-11-vulnerabilidades-ssh-rdp-e-loop-roteamento-design.md`

---

## Feature 1 — Portas 22 (SSH) e 3389 (RDP) no AmpScan

### Task 1: Adicionar portas 22/3389 ao runner Rust

**Files:**
- Modify: `tools/ampscan_runner/src/main.rs:50-84`

- [ ] **Step 1: Adicionar as duas tuplas novas em `default_ports()`**

Em `tools/ampscan_runner/src/main.rs`, dentro do `vec![...]` da função `default_ports()` (linha 53),
troque a linha final da lista (linha 83, `(5678, "tcp", "MT5678", ...)`) e o fechamento `];` (linha 84)
por:

```rust
        (5678, "tcp", "MT5678", "MikroTik Meris botnet - indicates possible Meris botnet infection (DDoS)", "tcp_connect", vec![]),
        (22, "tcp", "SSH", "SSH exposed to the internet - common target for brute-force/credential stuffing attacks", "tcp_connect", vec![]),
        (3389, "tcp", "RDP", "RDP exposed to the internet - common ransomware attack vector (weak credentials, exploits like BlueKeep)", "tcp_connect", vec![]),
    ];
```

Também atualize o comentário de doc da função (linhas 50-51), que hoje diz "21 pares", para:

```rust
/// Réplica exata da lista semeada por `ampscan::db::port_repo::seed_default_ports`,
/// mais 2 portas próprias (22/SSH, 3389/RDP) fora do escopo de amplificação —
/// ver `_AMPSCAN_PORTAS_EXPOSICAO` em `clientes/tasks.py` (23 portas no total).
fn default_ports() -> Vec<Port> {
```

- [ ] **Step 2: Recompilar o runner**

Run: `cd /opt/crm/tools/ampscan_runner && ~/.cargo/bin/cargo build --release`
Expected: `Compiling crm_ampscan_runner...` seguido de `Finished release [optimized] target(s)`, sem
erros. (`rustc` do `apt` é velho demais — use o toolchain do rustup, já no PATH via `~/.cargo/bin`,
mesma observação do runbook do AmpScan.)

- [ ] **Step 3: Smoke test manual do binário com as portas novas**

Run:
```bash
cd /opt/crm/tools/ampscan_runner
echo '{"prefixes":[{"id":1,"prefix":"127.0.0.1/32","description":"smoke"}],"concurrency":4,"timeout":1,"retries":0}' | ./target/release/crm_ampscan_runner | python3 -c "import json,sys; r=json.load(sys.stdin); ports=sorted(set(p['port'] for p in r['results'])); print(ports)"
```
Expected: a lista impressa inclui `22` e `3389` entre as portas testadas (junto com as outras 21).
Isso confirma que o binário está usando a lista atualizada sem precisar rodar contra um alvo real.

- [ ] **Step 4: Commit**

```bash
git add tools/ampscan_runner/src/main.rs
git commit -m "feat(ampscan): adiciona portas 22/SSH e 3389/RDP ao runner"
```

---

### Task 2: Novo status `exposto` e contador `total_expostos` nos models

**Files:**
- Modify: `clientes/models.py:704-707` (`AmpScanResultado.STATUS_CHOICES`)
- Modify: `clientes/models.py:746-750` (`AmpScanExecucaoLog`)
- Create: `clientes/migrations/0105_ampscan_status_exposto.py` (gerado por `makemigrations`)

- [ ] **Step 1: Atualizar `STATUS_CHOICES` de `AmpScanResultado`**

Em `clientes/models.py`, troque (linhas 704-707):

```python
    STATUS_CHOICES = [
        ('vulneravel', 'Vulnerável'),
        ('protegido', 'Protegido (aberto, mitigado)'),
    ]
```

por:

```python
    STATUS_CHOICES = [
        ('vulneravel', 'Vulnerável'),
        ('protegido', 'Protegido (aberto, mitigado)'),
        ('exposto', 'Exposto (gestão remota)'),
    ]
```

- [ ] **Step 2: Adicionar `total_expostos` em `AmpScanExecucaoLog`**

Em `clientes/models.py`, na classe `AmpScanExecucaoLog` (linha 746-749), troque:

```python
    total_ips        = models.PositiveIntegerField(default=0)
    total_probes      = models.PositiveIntegerField(default=0)
    total_vulneraveis = models.PositiveIntegerField(default=0)
    total_protegidos  = models.PositiveIntegerField(default=0)
```

por:

```python
    total_ips        = models.PositiveIntegerField(default=0)
    total_probes      = models.PositiveIntegerField(default=0)
    total_vulneraveis = models.PositiveIntegerField(default=0)
    total_protegidos  = models.PositiveIntegerField(default=0)
    total_expostos    = models.PositiveIntegerField(default=0, help_text='Portas de gestão remota (SSH/RDP) abertas — não conta como vulnerabilidade')
```

- [ ] **Step 2: Gerar e revisar a migração**

Run: `cd /opt/crm && venv/bin/python manage.py makemigrations clientes`
Expected: cria `clientes/migrations/0105_ampscan_status_exposto.py` (ou nome similar escolhido pelo
Django) alterando `field` de `status` em `AmpScanResultado` e adicionando `total_expostos` em
`AmpScanExecucaoLog`. Abra o arquivo gerado e confirme que só essas duas mudanças aparecem (nenhuma
outra alteração pendente de model foi capturada junto).

- [ ] **Step 3: Aplicar a migração**

Run: `venv/bin/python manage.py migrate clientes`
Expected: `Applying clientes.0105_ampscan_status_exposto... OK` (ou o nome real do arquivo gerado).

- [ ] **Step 4: Commit**

```bash
git add clientes/models.py clientes/migrations/0105_*.py
git commit -m "feat(ampscan): adiciona status 'exposto' e contador total_expostos"
```

---

### Task 3: Classificar portas 22/3389 como `exposto` em `_ampscan_executar_para_cliente`

**Files:**
- Modify: `clientes/tasks.py:2385-2499` (`_ampscan_executar_para_cliente`)
- Test: `clientes/tests.py`

- [ ] **Step 1: Escrever o teste que falha**

`_ampscan_executar_para_cliente` faz I/O (subprocess + banco) — para testar só a regra de
classificação, extraia a decisão de status para uma função pura testável antes de usá-la no loop.
Adicione em `clientes/tests.py` (crie o arquivo se ele estiver vazio/for só boilerplate — confira
antes com `cat clientes/tests.py`):

```python
from django.test import SimpleTestCase

from clientes.tasks import _ampscan_status_para_porta


class AmpscanStatusParaPortaTest(SimpleTestCase):
    def test_porta_ssh_aberta_e_exposto_nao_vulneravel(self):
        self.assertEqual(_ampscan_status_para_porta('Open', 22, 'tcp'), 'exposto')

    def test_porta_rdp_aberta_e_exposto_nao_vulneravel(self):
        self.assertEqual(_ampscan_status_para_porta('Open', 3389, 'tcp'), 'exposto')

    def test_porta_mikrotik_aberta_continua_vulneravel(self):
        self.assertEqual(_ampscan_status_para_porta('Open', 4145, 'tcp'), 'vulneravel')

    def test_porta_snmp_aberta_continua_vulneravel(self):
        self.assertEqual(_ampscan_status_para_porta('Open', 161, 'udp'), 'vulneravel')

    def test_status_openprotected_e_sempre_protegido(self):
        self.assertEqual(_ampscan_status_para_porta('OpenProtected', 22, 'tcp'), 'protegido')
        self.assertEqual(_ampscan_status_para_porta('OpenProtected', 161, 'udp'), 'protegido')

    def test_status_desconhecido_retorna_none(self):
        self.assertIsNone(_ampscan_status_para_porta('Closed', 22, 'tcp'))
        self.assertIsNone(_ampscan_status_para_porta('Inconclusive', 22, 'tcp'))
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd /opt/crm && venv/bin/python manage.py test clientes.tests.AmpscanStatusParaPortaTest -v 2 --keepdb`
Expected: `ImportError: cannot import name '_ampscan_status_para_porta'` (a função ainda não existe).

- [ ] **Step 3: Implementar `_ampscan_status_para_porta` e usá-la no loop**

Em `clientes/tasks.py`, logo antes de `_ampscan_executar_para_cliente` (antes da linha 2385),
adicione:

```python
# Portas de gestão remota — abertas não são "vulnerabilidade" no mesmo sentido
# que amplificação DDoS/proxy comprometido (muito cliente expõe de propósito
# pra administração), mas merecem visibilidade separada na aba.
_AMPSCAN_PORTAS_EXPOSICAO = {(22, 'tcp'), (3389, 'tcp')}


def _ampscan_status_para_porta(status_raw, porta, protocolo):
    """Traduz o status bruto do runner ('Open'/'OpenProtected'/outro) pro
    status persistido em AmpScanResultado, separando portas de gestão remota
    (SSH/RDP) da categoria 'vulneravel'. Retorna None para status que não
    deve ser persistido (Closed/Inconclusive/Error)."""
    if status_raw == 'Open':
        if (porta, protocolo) in _AMPSCAN_PORTAS_EXPOSICAO:
            return 'exposto'
        return 'vulneravel'
    if status_raw == 'OpenProtected':
        return 'protegido'
    return None
```

Depois, dentro de `_ampscan_executar_para_cliente`, troque o bloco (linhas 2451-2460):

```python
    for r in report.get('results', []):
        status_raw = r.get('status')
        if status_raw == 'Open':
            status = 'vulneravel'
            total_vulneraveis += 1
        elif status_raw == 'OpenProtected':
            status = 'protegido'
            total_protegidos += 1
        else:
            continue  # Closed / Inconclusive / Error — não interessa persistir
```

por:

```python
    for r in report.get('results', []):
        status_raw = r.get('status')
        porta = r['port']
        protocolo = r['protocol']
        status = _ampscan_status_para_porta(status_raw, porta, protocolo)
        if status is None:
            continue  # Closed / Inconclusive / Error — não interessa persistir
        if status == 'vulneravel':
            total_vulneraveis += 1
        elif status == 'protegido':
            total_protegidos += 1
        elif status == 'exposto':
            total_expostos += 1
```

E logo abaixo, o resto do loop (linhas 2462-2479 originais) já usa `ip = r['ip']`, `porta = r['port']`,
`protocolo = r['protocolo']` — como agora `porta`/`protocolo` já foram extraídos acima, remova as
duas linhas duplicadas `porta = r['port']` e `protocolo = r['protocol']` que sobrariam logo depois de
`ip = r['ip']`. O trecho final desse pedaço do loop deve ficar:

```python
        ip = r['ip']
        chaves_vistas.add((ip, porta, protocolo))
        bloco = _ampscan_localizar_bloco(ip, blocos_por_id)
```

Inicialize o contador novo junto dos outros dois, antes do loop (linhas 2447-2449 originais):

```python
    chaves_vistas = set()
    total_vulneraveis = 0
    total_protegidos = 0
    total_expostos = 0
```

E popule no `execucao` junto dos outros contadores (linha 2493-2496 originais):

```python
    execucao.total_ips = report.get('total_ips', 0)
    execucao.total_probes = report.get('total_probes', 0)
    execucao.total_vulneraveis = total_vulneraveis
    execucao.total_protegidos = total_protegidos
    execucao.total_expostos = total_expostos
    execucao.finalizado_em = timezone.now()
    execucao.save()
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `venv/bin/python manage.py test clientes.tests.AmpscanStatusParaPortaTest -v 2 --keepdb`
Expected: `OK` (6 testes passando).

- [ ] **Step 5: Rodar a suíte completa de `clientes` pra checar regressão**

Run: `venv/bin/python manage.py test clientes -v 1 --keepdb`
Expected: `OK`, nenhum teste pré-existente quebrado (em especial os de BGP em
`clientes/tests_bgp_criar_sessao.py`/`tests_bgp_novo_anuncio.py`, que não têm relação com essa
mudança mas compartilham o mesmo test runner).

- [ ] **Step 6: Commit**

```bash
git add clientes/tasks.py clientes/tests.py
git commit -m "feat(ampscan): classifica SSH/RDP abertos como 'exposto', não 'vulneravel'"
```

---

### Task 4: Expor `total_expostos` na view de execuções

**Files:**
- Modify: `clientes/views.py:4565-4579` (`listar_ampscan_execucoes`)

- [ ] **Step 1: Adicionar o campo no JSON de resposta**

Em `clientes/views.py`, dentro de `listar_ampscan_execucoes`, troque:

```python
            'total_vulneraveis': e.total_vulneraveis,
            'total_protegidos': e.total_protegidos,
            'blocos_ignorados': e.blocos_ignorados,
```

por:

```python
            'total_vulneraveis': e.total_vulneraveis,
            'total_protegidos': e.total_protegidos,
            'total_expostos': e.total_expostos,
            'blocos_ignorados': e.blocos_ignorados,
```

- [ ] **Step 2: Commit**

```bash
git add clientes/views.py
git commit -m "feat(ampscan): inclui total_expostos na resposta de execuções"
```

---

### Task 5: UI — badge `exposto`, contador na tela e tabela de portas atualizada

**Files:**
- Modify: `clientes/templates/listar.html:917-947` (tabela de portas testadas)
- Modify: `clientes/templates/listar.html:5172-5174` (cor/ícone do badge)
- Modify: `clientes/templates/listar.html:5238` (texto de última varredura)

- [ ] **Step 1: Atualizar o título e a tabela de portas testadas**

Em `clientes/templates/listar.html`, troque o texto do `<summary>` (linha 918):

```html
                                        <summary style="cursor:pointer;font-size:.78rem;color:var(--accent-cyan);">Ver as 21 portas testadas</summary>
```

por:

```html
                                        <summary style="cursor:pointer;font-size:.78rem;color:var(--accent-cyan);">Ver as 23 portas testadas</summary>
```

E adicione duas linhas na tabela, logo após a linha do MikroTik Meris (linha 945, antes de
`</tbody>`):

```html
                                                    <tr><td>5678</td><td>TCP</td><td>MikroTik Meris</td><td>indício de botnet Meris — não é amplificação</td></tr>
                                                    <tr><td>22</td><td>TCP</td><td>SSH</td><td>gestão remota exposta — alvo de brute-force (categoria separada: "Exposto", não "Vulnerável")</td></tr>
                                                    <tr><td>3389</td><td>TCP</td><td>RDP</td><td>gestão remota exposta — vetor comum de ransomware (categoria separada: "Exposto", não "Vulnerável")</td></tr>
```

- [ ] **Step 2: Adicionar cor/ícone do badge `exposto`**

Em `clientes/templates/listar.html`, dentro de `ampscanRenderizarResultados` (linhas 5172-5174),
troque:

```javascript
            const corStatus = r.status === 'vulneravel' ? '#f85149' : '#d29922';
            const iconeStatus = r.status === 'vulneravel' ? 'fa-radiation' : 'fa-shield-halved';
```

por:

```javascript
            const CORES_STATUS = {vulneravel: '#f85149', protegido: '#d29922', exposto: '#58a6ff'};
            const ICONES_STATUS = {vulneravel: 'fa-radiation', protegido: 'fa-shield-halved', exposto: 'fa-door-open'};
            const corStatus = CORES_STATUS[r.status] || '#8b949e';
            const iconeStatus = ICONES_STATUS[r.status] || 'fa-question-circle';
```

- [ ] **Step 3: Incluir o contador no texto de última varredura**

Em `clientes/templates/listar.html`, dentro de `ampscanCarregarExecucoes` (linha 5238), troque:

```javascript
                info.innerHTML = `<i class="fas fa-check-circle me-1" style="color:#3fb950"></i>Última varredura em ${ultima.finalizado_em} — ${ultima.total_ips} IPs testados, ${ultima.total_vulneraveis} vulnerável(is), ${ultima.total_protegidos} protegido(s)${extra}.`;
```

por:

```javascript
                info.innerHTML = `<i class="fas fa-check-circle me-1" style="color:#3fb950"></i>Última varredura em ${ultima.finalizado_em} — ${ultima.total_ips} IPs testados, ${ultima.total_vulneraveis} vulnerável(is), ${ultima.total_protegidos} protegido(s), ${ultima.total_expostos} exposto(s)${extra}.`;
```

- [ ] **Step 4: Verificação manual no navegador**

Run: `sudo systemctl restart gunicorn` (só gunicorn — mudança em `views.py`/`templates`/`tasks.py`
serve por HTTP; `tasks.py` também precisa do celery reiniciado, feito no fim da Task 3, mas repita
aqui se a Task 3 ainda não tiver reiniciado nada — `sudo systemctl restart celery`)

Abra a aba Vulnerabilidades de um cliente com blocos IP cadastrados, clique em "Ver as 23 portas
testadas" e confirme que SSH/RDP aparecem na lista. Se esse cliente tiver algum host com SSH aberto
(comum em servidores de gestão), clique "Escanear Agora", espere terminar e confirme que a linha
aparece com o badge azul "Exposto (gestão remota)", não vermelho.

- [ ] **Step 5: Commit**

```bash
git add clientes/templates/listar.html
git commit -m "feat(ampscan): UI para status 'exposto' (SSH/RDP) na aba Vulnerabilidades"
```

---

### Task 6: Atualizar documentação do AmpScan

**Files:**
- Modify: `docs/AMPSCAN_VARREDURA_AMPLIFICACAO.md`

- [ ] **Step 1: Atualizar a tabela de portas e adicionar seção sobre `exposto`**

Em `docs/AMPSCAN_VARREDURA_AMPLIFICACAO.md`, na seção "As 21 portas testadas" (linha 151), troque o
título para "As 23 portas testadas" e adicione duas linhas na tabela (após a linha 5678/MikroTik
Meris, linha 178):

```markdown
| 22 | TCP | SSH | exposição de gestão remota — alvo de brute-force (status separado: `exposto`) |
| 3389 | TCP | RDP | exposição de gestão remota — vetor comum de ransomware (status separado: `exposto`) |
```

Adicione uma nova seção antes de "Última atualização", explicando o status `exposto`:

```markdown
## Status `exposto` — SSH/RDP não é tratado como vulnerabilidade

Diferente das outras 21 portas (amplificação DDoS ou indício de comprometimento), SSH (22) e RDP
(3389) abertos são um padrão legítimo em muitos clientes (gestão remota de equipamentos/servidores).
Por isso essas duas portas usam um terceiro status, `exposto` (`AmpScanResultado.STATUS_CHOICES`),
separado de `vulneravel` — não entra em `total_vulneraveis`, tem contador próprio
(`total_expostos`) e badge de cor neutra na UI. Ainda assim fica registrado, porque exposição de
SSH/RDP à internet é informação relevante de superfície de ataque, só não é tratada com a mesma
severidade de um resolver DNS aberto ou um Memcached exposto.
```

- [ ] **Step 2: Commit**

```bash
git add docs/AMPSCAN_VARREDURA_AMPLIFICACAO.md
git commit -m "docs(ampscan): documenta portas 22/3389 e status 'exposto'"
```

---

## Feature 2 — Detecção de loop de roteamento por bloco IP

### Task 7: Models `RotaLoopResultado` e `RotaLoopExecucaoLog`

**Files:**
- Modify: `clientes/models.py` (adicionar após `AmpScanExecucaoLog`, linha 762)
- Create: `clientes/migrations/0106_rotaloopresultado_rotaloopexecucaolog.py` (gerado)

- [ ] **Step 1: Adicionar os dois models**

Em `clientes/models.py`, logo depois do `__str__` de `AmpScanExecucaoLog` (linha 762, antes do
comentário `# IPAM` na linha 765), adicione:

```python
class RotaLoopResultado(models.Model):
    """Estado atual do teste de loop de roteamento por bloco — 1 linha por
    BlocoIP, upsert a cada teste. Só é gravado quando há loop detectado
    (mesmo padrão do AmpScanResultado: 'normal' não persiste linha); se um
    loop deixa de aparecer numa execução seguinte, marca resolvido em vez de
    apagar (histórico de quando o problema existiu)."""
    STATUS_CHOICES = [
        ('loop_detectado', 'Loop detectado'),
    ]

    cliente  = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='rotaloop_resultados')
    bloco_ip = models.OneToOneField('BlocoIP', on_delete=models.CASCADE, related_name='rotaloop_resultado')

    ip_alvo    = models.CharField(max_length=45)
    status     = models.CharField(max_length=15, choices=STATUS_CHOICES, default='loop_detectado')
    ip_em_loop = models.CharField(max_length=45)
    hops       = models.JSONField(default=list, blank=True, help_text='Lista [{"hop": int, "ip": str|None}, ...]')
    ferramenta = models.CharField(max_length=10, default='mtr')

    primeira_deteccao = models.DateTimeField(auto_now_add=True)
    ultima_deteccao    = models.DateTimeField(auto_now=True)

    resolvido    = models.BooleanField(default=False)
    resolvido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Resultado de Loop de Roteamento'
        verbose_name_plural = 'Resultados de Loop de Roteamento'
        ordering = ['-ultima_deteccao']

    def __str__(self):
        return f"Loop em {self.bloco_ip.bloco} via {self.ip_alvo} ({self.cliente.nome_empresa})"


class RotaLoopExecucaoLog(models.Model):
    """Histórico de execuções do teste de loop de roteamento por cliente."""
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='rotaloop_execucoes')

    iniciado_em   = models.DateTimeField(auto_now_add=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)

    total_blocos_testados  = models.PositiveIntegerField(default=0)
    total_loops_detectados = models.PositiveIntegerField(default=0)

    sucesso        = models.BooleanField(default=True)
    erro_mensagem  = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Execução de Teste de Loop de Roteamento'
        verbose_name_plural = 'Execuções de Teste de Loop de Roteamento'
        ordering = ['-iniciado_em']

    def __str__(self):
        status = 'OK' if self.sucesso else 'ERRO'
        return f"RotaLoop {self.cliente.nome_empresa} - {self.iniciado_em.strftime('%d/%m/%Y %H:%M')} [{status}]"
```

- [ ] **Step 2: Gerar, revisar e aplicar a migração**

Run: `cd /opt/crm && venv/bin/python manage.py makemigrations clientes`
Expected: cria `clientes/migrations/0106_rotaloopresultado_rotaloopexecucaolog.py` (ou nome
similar), criando as duas tabelas novas. Abra e confirme que não há mudanças inesperadas de outros
models.

Run: `venv/bin/python manage.py migrate clientes`
Expected: `Applying clientes.0106_..._OK`.

- [ ] **Step 3: Commit**

```bash
git add clientes/models.py clientes/migrations/0106_*.py
git commit -m "feat(rotaloop): models RotaLoopResultado e RotaLoopExecucaoLog"
```

---

### Task 8: Função `_rotaloop_ip_alvo` (cálculo do IP de teste)

**Files:**
- Modify: `clientes/tasks.py` (nova seção, após o bloco do AmpScan que termina na linha ~2555)
- Test: `clientes/tests.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicione em `clientes/tests.py`:

```python
import ipaddress

from clientes.tasks import _rotaloop_ip_alvo


class RotaloopIpAlvoTest(SimpleTestCase):
    def test_bloco_ipv4_normal_usa_primeiro_ip_util(self):
        net = ipaddress.ip_network('200.100.50.0/24')
        self.assertEqual(_rotaloop_ip_alvo(net), '200.100.50.1')

    def test_bloco_ipv4_barra_31_usa_network_address(self):
        net = ipaddress.ip_network('200.100.50.0/31')
        self.assertEqual(_rotaloop_ip_alvo(net), '200.100.50.0')

    def test_bloco_ipv4_barra_32_usa_network_address(self):
        net = ipaddress.ip_network('200.100.50.5/32')
        self.assertEqual(_rotaloop_ip_alvo(net), '200.100.50.5')

    def test_bloco_ipv6_normal_usa_primeiro_ip_util(self):
        net = ipaddress.ip_network('2801:80:1234::/48')
        self.assertEqual(_rotaloop_ip_alvo(net), '2801:80:1234::1')

    def test_bloco_ipv6_barra_127_usa_network_address(self):
        net = ipaddress.ip_network('2801:80:1234::/127')
        self.assertEqual(_rotaloop_ip_alvo(net), '2801:80:1234::')
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `venv/bin/python manage.py test clientes.tests.RotaloopIpAlvoTest -v 2 --keepdb`
Expected: `ImportError: cannot import name '_rotaloop_ip_alvo'`.

- [ ] **Step 3: Implementar**

Em `clientes/tasks.py`, após o fim da seção AmpScan (depois da última linha da função
`ampscan_varrer_clientes_agendado`, que termina por volta da linha 2570 — confirme com
`grep -n "^def ampscan_varrer_clientes_agendado" -A 30 clientes/tasks.py` para achar a linha exata
de fechamento antes de colar), adicione:

```python
# ─────────────────────────────────────────────────────────────────────────────
# ROTALOOP — DETECÇÃO DE LOOP DE ROTEAMENTO POR BLOCO IP
# ─────────────────────────────────────────────────────────────────────────────


def _rotaloop_ip_alvo(net):
    """IP usado como alvo do teste de loop pra um bloco: o primeiro IP útil
    (network_address + 1). Blocos minúsculos (/31, /32 IPv4; /127, /128 IPv6)
    não têm um '+1' distinto do endereço de rede, então usam o próprio
    network_address."""
    if net.num_addresses <= 2:
        return str(net.network_address)
    return str(net.network_address + 1)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `venv/bin/python manage.py test clientes.tests.RotaloopIpAlvoTest -v 2 --keepdb`
Expected: `OK` (5 testes).

- [ ] **Step 5: Commit**

```bash
git add clientes/tasks.py clientes/tests.py
git commit -m "feat(rotaloop): calcula IP alvo do teste por bloco"
```

---

### Task 9: Função pura de detecção de loop a partir de hops

**Files:**
- Modify: `clientes/tasks.py`
- Test: `clientes/tests.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicione em `clientes/tests.py`:

```python
from clientes.tasks import _rotaloop_detectar_loop


class RotaloopDetectarLoopTest(SimpleTestCase):
    def test_sem_repeticao_e_normal(self):
        hops = [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': '200.1.1.1'},
            {'hop': 3, 'ip': '200.1.1.2'},
        ]
        status, ip_em_loop = _rotaloop_detectar_loop(hops)
        self.assertEqual(status, 'normal')
        self.assertIsNone(ip_em_loop)

    def test_ip_repetido_consecutivo_e_loop(self):
        hops = [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': '200.1.1.1'},
            {'hop': 3, 'ip': '200.1.1.1'},
        ]
        status, ip_em_loop = _rotaloop_detectar_loop(hops)
        self.assertEqual(status, 'loop_detectado')
        self.assertEqual(ip_em_loop, '200.1.1.1')

    def test_ip_repetido_nao_consecutivo_tambem_e_loop(self):
        hops = [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': '200.1.1.1'},
            {'hop': 3, 'ip': '200.1.1.2'},
            {'hop': 4, 'ip': '200.1.1.1'},
        ]
        status, ip_em_loop = _rotaloop_detectar_loop(hops)
        self.assertEqual(status, 'loop_detectado')
        self.assertEqual(ip_em_loop, '200.1.1.1')

    def test_hops_sem_resposta_nao_contam_como_repeticao(self):
        hops = [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': None},
            {'hop': 3, 'ip': None},
            {'hop': 4, 'ip': '200.1.1.2'},
        ]
        status, ip_em_loop = _rotaloop_detectar_loop(hops)
        self.assertEqual(status, 'normal')
        self.assertIsNone(ip_em_loop)

    def test_lista_vazia_e_normal(self):
        status, ip_em_loop = _rotaloop_detectar_loop([])
        self.assertEqual(status, 'normal')
        self.assertIsNone(ip_em_loop)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `venv/bin/python manage.py test clientes.tests.RotaloopDetectarLoopTest -v 2 --keepdb`
Expected: `ImportError: cannot import name '_rotaloop_detectar_loop'`.

- [ ] **Step 3: Implementar**

Em `clientes/tasks.py`, logo após `_rotaloop_ip_alvo`, adicione:

```python
def _rotaloop_detectar_loop(hops):
    """Recebe a lista de hops [{'hop': int, 'ip': str|None}, ...] (ordenada
    por hop) e detecta loop de roteamento: o mesmo IP aparecendo em 2+
    posições, em qualquer lugar do caminho (não precisa ser consecutivo).
    Hops sem resposta (ip=None) são ignorados na contagem. Retorna
    (status, ip_em_loop) — status é 'normal' ou 'loop_detectado'."""
    vistos = set()
    for h in hops:
        ip = h.get('ip')
        if ip is None:
            continue
        if ip in vistos:
            return 'loop_detectado', ip
        vistos.add(ip)
    return 'normal', None
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `venv/bin/python manage.py test clientes.tests.RotaloopDetectarLoopTest -v 2 --keepdb`
Expected: `OK` (5 testes).

- [ ] **Step 5: Commit**

```bash
git add clientes/tasks.py clientes/tests.py
git commit -m "feat(rotaloop): detecta loop de roteamento a partir de hops de traceroute"
```

---

### Task 10: Função `_rotaloop_mtr_json` (executa `mtr` e extrai hops)

**Files:**
- Modify: `clientes/tasks.py`
- Test: `clientes/tests.py`

- [ ] **Step 1: Escrever os testes que falham (mockando `subprocess.run`)**

Adicione em `clientes/tests.py`:

```python
import json
import subprocess
from unittest import mock

from clientes.tasks import _rotaloop_mtr_json


MTR_JSON_EXEMPLO = json.dumps({
    "report": {
        "mtr": {"src": "servidor", "dst": "200.1.1.1"},
        "hubs": [
            {"count": 1, "host": "10.0.0.1", "Loss%": 0.0},
            {"count": 2, "host": "200.1.1.1", "Loss%": 0.0},
        ],
    }
})


class RotaloopMtrJsonTest(SimpleTestCase):
    @mock.patch('clientes.tasks.shutil.which', return_value='/usr/bin/mtr')
    @mock.patch('clientes.tasks.subprocess.run')
    def test_parseia_hops_do_json_do_mtr(self, mock_run, mock_which):
        mock_run.return_value = subprocess.CompletedProcess(
            args=['mtr'], returncode=0, stdout=MTR_JSON_EXEMPLO, stderr='',
        )
        hops = _rotaloop_mtr_json('200.1.1.1')
        self.assertEqual(hops, [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': '200.1.1.1'},
        ])

    @mock.patch('clientes.tasks.shutil.which', return_value=None)
    def test_levanta_erro_se_mtr_nao_instalado(self, mock_which):
        with self.assertRaises(RuntimeError):
            _rotaloop_mtr_json('200.1.1.1')

    @mock.patch('clientes.tasks.shutil.which', return_value='/usr/bin/mtr')
    @mock.patch('clientes.tasks.subprocess.run')
    def test_levanta_erro_se_json_invalido(self, mock_run, mock_which):
        mock_run.return_value = subprocess.CompletedProcess(
            args=['mtr'], returncode=0, stdout='não é json', stderr='',
        )
        with self.assertRaises(RuntimeError):
            _rotaloop_mtr_json('200.1.1.1')

    @mock.patch('clientes.tasks.shutil.which', return_value='/usr/bin/mtr')
    @mock.patch('clientes.tasks.subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='mtr', timeout=30))
    def test_levanta_erro_se_timeout(self, mock_run, mock_which):
        with self.assertRaises(RuntimeError):
            _rotaloop_mtr_json('200.1.1.1')

    @mock.patch('clientes.tasks.shutil.which', return_value='/usr/bin/mtr')
    @mock.patch('clientes.tasks.subprocess.run')
    def test_hop_sem_resposta_vira_ip_none(self, mock_run, mock_which):
        saida = json.dumps({"report": {"hubs": [
            {"count": 1, "host": "???", "Loss%": 100.0},
            {"count": 2, "host": "200.1.1.1", "Loss%": 0.0},
        ]}})
        mock_run.return_value = subprocess.CompletedProcess(
            args=['mtr'], returncode=0, stdout=saida, stderr='',
        )
        hops = _rotaloop_mtr_json('200.1.1.1')
        self.assertEqual(hops, [
            {'hop': 1, 'ip': None},
            {'hop': 2, 'ip': '200.1.1.1'},
        ])
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `venv/bin/python manage.py test clientes.tests.RotaloopMtrJsonTest -v 2 --keepdb`
Expected: `ImportError: cannot import name '_rotaloop_mtr_json'`.

- [ ] **Step 3: Implementar**

Em `clientes/tasks.py`, adicione `import shutil` junto dos outros imports do topo do arquivo (perto
da linha 16-17, junto de `import os` / `import re`). Depois, logo após `_rotaloop_detectar_loop`,
adicione:

```python
def _rotaloop_mtr_json(host, count=3, timeout=30):
    """Roda `mtr --report --json --no-dns -c <count> <host>` e devolve a
    lista de hops [{'hop': int, 'ip': str|None}, ...] ordenada. Levanta
    RuntimeError (não deixa exceção de subprocess/parsing vazar) se mtr não
    estiver instalado, o comando expirar, ou a saída não vier no formato
    esperado — quem chama trata isso como status='inconclusivo'."""
    if not shutil.which('mtr'):
        raise RuntimeError('mtr não está instalado no servidor.')

    cmd = ['mtr', '--report', '--json', '--no-dns', '-c', str(count), host]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'mtr excedeu o timeout de {timeout}s para {host}.')

    try:
        dados = _json.loads(proc.stdout)
        hubs = dados['report']['hubs']
    except (_json.JSONDecodeError, KeyError, TypeError) as e:
        raise RuntimeError(f'Saída do mtr em formato inesperado para {host}: {e}')

    hops = []
    for hub in hubs:
        host_str = hub.get('host')
        ip = None if not host_str or host_str == '???' else host_str
        hops.append({'hop': hub.get('count'), 'ip': ip})
    return hops
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `venv/bin/python manage.py test clientes.tests.RotaloopMtrJsonTest -v 2 --keepdb`
Expected: `OK` (5 testes).

- [ ] **Step 5: Rodar contra um alvo real pra validar o comando de verdade**

Run: `mtr --report --json --no-dns -c 2 1.1.1.1`
Expected: JSON válido com `report.hubs` (mesmo formato já confirmado durante o brainstorm) — essa
etapa é só uma checagem de sanidade de que o ambiente de produção realmente aceita esses flags
(já confirmado antes, mas repita se o `mtr` do servidor mudar de versão).

- [ ] **Step 6: Commit**

```bash
git add clientes/tasks.py clientes/tests.py
git commit -m "feat(rotaloop): executa mtr --json e extrai hops estruturados"
```

---

### Task 11: `_rotaloop_executar_para_bloco` e `_rotaloop_executar_para_cliente`

**Files:**
- Modify: `clientes/tasks.py`
- Test: `clientes/tests.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicione em `clientes/tests.py`:

```python
from clientes.tasks import _rotaloop_executar_para_bloco


class RotaloopExecutarParaBlocoTest(SimpleTestCase):
    @mock.patch('clientes.tasks._rotaloop_mtr_json')
    def test_bloco_com_loop(self, mock_mtr):
        mock_mtr.return_value = [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': '10.0.0.1'},
        ]
        bloco = mock.Mock(bloco='200.1.1.0/24')
        resultado = _rotaloop_executar_para_bloco(bloco)
        self.assertEqual(resultado['status'], 'loop_detectado')
        self.assertEqual(resultado['ip_alvo'], '200.1.1.1')
        self.assertEqual(resultado['ip_em_loop'], '10.0.0.1')
        self.assertEqual(resultado['ferramenta'], 'mtr')
        self.assertEqual(len(resultado['hops']), 2)

    @mock.patch('clientes.tasks._rotaloop_mtr_json')
    def test_bloco_sem_loop(self, mock_mtr):
        mock_mtr.return_value = [{'hop': 1, 'ip': '10.0.0.1'}]
        bloco = mock.Mock(bloco='200.1.1.0/24')
        resultado = _rotaloop_executar_para_bloco(bloco)
        self.assertEqual(resultado['status'], 'normal')
        self.assertIsNone(resultado['ip_em_loop'])

    @mock.patch('clientes.tasks._rotaloop_mtr_json', side_effect=RuntimeError('mtr ausente'))
    def test_bloco_com_erro_e_inconclusivo(self, mock_mtr):
        bloco = mock.Mock(bloco='200.1.1.0/24')
        resultado = _rotaloop_executar_para_bloco(bloco)
        self.assertEqual(resultado['status'], 'inconclusivo')
        self.assertEqual(resultado['erro'], 'mtr ausente')

    def test_bloco_ip_invalido_e_inconclusivo(self):
        bloco = mock.Mock(bloco='não-é-um-cidr')
        resultado = _rotaloop_executar_para_bloco(bloco)
        self.assertEqual(resultado['status'], 'inconclusivo')
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `venv/bin/python manage.py test clientes.tests.RotaloopExecutarParaBlocoTest -v 2 --keepdb`
Expected: `ImportError: cannot import name '_rotaloop_executar_para_bloco'`.

- [ ] **Step 3: Implementar `_rotaloop_executar_para_bloco`**

Em `clientes/tasks.py`, logo após `_rotaloop_mtr_json`, adicione:

```python
def _rotaloop_executar_para_bloco(bloco):
    """Roda o teste de loop pra um único BlocoIP e devolve um dict com o
    resultado (não grava no banco — quem chama decide o que persistir)."""
    try:
        net = ipaddress.ip_network(bloco.bloco, strict=False)
    except ValueError as e:
        return {'status': 'inconclusivo', 'erro': f'Bloco IP inválido: {e}', 'ip_alvo': None, 'ip_em_loop': None, 'hops': [], 'ferramenta': 'mtr'}

    ip_alvo = _rotaloop_ip_alvo(net)

    try:
        hops = _rotaloop_mtr_json(ip_alvo)
    except RuntimeError as e:
        return {'status': 'inconclusivo', 'erro': str(e), 'ip_alvo': ip_alvo, 'ip_em_loop': None, 'hops': [], 'ferramenta': 'mtr'}

    status, ip_em_loop = _rotaloop_detectar_loop(hops)
    return {'status': status, 'erro': None, 'ip_alvo': ip_alvo, 'ip_em_loop': ip_em_loop, 'hops': hops, 'ferramenta': 'mtr'}
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `venv/bin/python manage.py test clientes.tests.RotaloopExecutarParaBlocoTest -v 2 --keepdb`
Expected: `OK` (4 testes).

- [ ] **Step 5: Escrever teste de `_rotaloop_executar_para_cliente` (integração com banco)**

Esse precisa de banco de dados — troque `SimpleTestCase` por `TestCase` (import de
`django.test.TestCase`) num teste separado. Adicione em `clientes/tests.py`:

```python
from django.test import TestCase

from clientes.models import BlocoIP, Cliente, RotaLoopExecucaoLog, RotaLoopResultado
from clientes.tasks import _rotaloop_executar_para_cliente


class RotaloopExecutarParaClienteTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nome_empresa='Cliente Teste')
        self.bloco = BlocoIP.objects.create(cliente=self.cliente, tipo='IPV4', bloco='200.1.1.0/24')

    @mock.patch('clientes.tasks._rotaloop_mtr_json')
    def test_loop_detectado_persiste_resultado(self, mock_mtr):
        mock_mtr.return_value = [
            {'hop': 1, 'ip': '10.0.0.1'},
            {'hop': 2, 'ip': '10.0.0.1'},
        ]
        execucao = _rotaloop_executar_para_cliente(self.cliente)

        self.assertTrue(execucao.sucesso)
        self.assertEqual(execucao.total_blocos_testados, 1)
        self.assertEqual(execucao.total_loops_detectados, 1)

        resultado = RotaLoopResultado.objects.get(bloco_ip=self.bloco)
        self.assertEqual(resultado.status, 'loop_detectado')
        self.assertEqual(resultado.ip_em_loop, '10.0.0.1')
        self.assertFalse(resultado.resolvido)

    @mock.patch('clientes.tasks._rotaloop_mtr_json')
    def test_loop_que_some_marca_resolvido(self, mock_mtr):
        mock_mtr.return_value = [{'hop': 1, 'ip': '10.0.0.1'}, {'hop': 2, 'ip': '10.0.0.1'}]
        _rotaloop_executar_para_cliente(self.cliente)
        self.assertFalse(RotaLoopResultado.objects.get(bloco_ip=self.bloco).resolvido)

        mock_mtr.return_value = [{'hop': 1, 'ip': '10.0.0.1'}, {'hop': 2, 'ip': '200.1.1.1'}]
        _rotaloop_executar_para_cliente(self.cliente)

        resultado = RotaLoopResultado.objects.get(bloco_ip=self.bloco)
        self.assertTrue(resultado.resolvido)
        self.assertIsNotNone(resultado.resolvido_em)

    @mock.patch('clientes.tasks._rotaloop_mtr_json')
    def test_sem_loop_nao_persiste_linha(self, mock_mtr):
        mock_mtr.return_value = [{'hop': 1, 'ip': '10.0.0.1'}, {'hop': 2, 'ip': '200.1.1.1'}]
        execucao = _rotaloop_executar_para_cliente(self.cliente)

        self.assertEqual(execucao.total_loops_detectados, 0)
        self.assertFalse(RotaLoopResultado.objects.filter(bloco_ip=self.bloco).exists())

    def test_cliente_sem_blocos_finaliza_sem_erro(self):
        cliente_vazio = Cliente.objects.create(nome_empresa='Sem Blocos')
        execucao = _rotaloop_executar_para_cliente(cliente_vazio)
        self.assertTrue(execucao.sucesso)
        self.assertEqual(execucao.total_blocos_testados, 0)
```

Ajuste os campos de `Cliente.objects.create(...)` se `nome_empresa` não for o único campo
obrigatório do model — confira com `grep -n "class Cliente" -A 30 clientes/models.py` antes de rodar;
se houver outros campos `blank=False`/`null=False` sem default, adicione-os na chamada.

- [ ] **Step 6: Rodar e confirmar que falha**

Run: `venv/bin/python manage.py test clientes.tests.RotaloopExecutarParaClienteTest -v 2 --keepdb`
Expected: `ImportError: cannot import name '_rotaloop_executar_para_cliente'`.

- [ ] **Step 7: Implementar `_rotaloop_executar_para_cliente`**

Em `clientes/tasks.py`, logo após `_rotaloop_executar_para_bloco`, adicione:

```python
def _rotaloop_executar_para_cliente(cliente):
    """Roda o teste de loop pra todos os BlocoIP de um cliente e persiste o
    resultado. Isolado em try/except por bloco — uma falha num bloco não
    derruba o teste dos demais, mesmo padrão do AmpScan."""
    from .models import BlocoIP, RotaLoopExecucaoLog, RotaLoopResultado

    execucao = RotaLoopExecucaoLog.objects.create(cliente=cliente)

    blocos = list(BlocoIP.objects.filter(cliente=cliente))
    total_loops = 0
    blocos_com_loop_ids = set()

    for bloco in blocos:
        try:
            resultado = _rotaloop_executar_para_bloco(bloco)
        except Exception:
            logger.exception(f'rotaloop: falha inesperada no bloco {bloco.bloco} (cliente {cliente.id})')
            continue

        if resultado['status'] != 'loop_detectado':
            continue

        total_loops += 1
        blocos_com_loop_ids.add(bloco.id)
        RotaLoopResultado.objects.update_or_create(
            bloco_ip=bloco,
            defaults={
                'cliente': cliente,
                'ip_alvo': resultado['ip_alvo'],
                'status': 'loop_detectado',
                'ip_em_loop': resultado['ip_em_loop'],
                'hops': resultado['hops'],
                'ferramenta': resultado['ferramenta'],
                'resolvido': False,
                'resolvido_em': None,
            },
        )

    # Loops anteriores em blocos deste cliente que não apareceram de novo —
    # marca como resolvido em vez de apagar (mesmo padrão do AmpScan).
    anteriores = RotaLoopResultado.objects.filter(cliente=cliente, resolvido=False).exclude(bloco_ip_id__in=blocos_com_loop_ids)
    for resultado in anteriores:
        resultado.resolvido = True
        resultado.resolvido_em = timezone.now()
        resultado.save(update_fields=['resolvido', 'resolvido_em'])

    execucao.total_blocos_testados = len(blocos)
    execucao.total_loops_detectados = total_loops
    execucao.finalizado_em = timezone.now()
    execucao.save()
    return execucao
```

- [ ] **Step 8: Rodar e confirmar que passa**

Run: `venv/bin/python manage.py test clientes.tests.RotaloopExecutarParaClienteTest -v 2 --keepdb`
Expected: `OK` (4 testes).

- [ ] **Step 9: Rodar a suíte completa de novo**

Run: `venv/bin/python manage.py test clientes -v 1 --keepdb`
Expected: `OK`.

- [ ] **Step 10: Commit**

```bash
git add clientes/tasks.py clientes/tests.py
git commit -m "feat(rotaloop): executa teste de loop por cliente e persiste resultados"
```

---

### Task 12: Tasks Celery sob demanda e periódica

**Files:**
- Modify: `clientes/tasks.py`
- Modify: `crm/celery.py:94-95` (fim de `beat_schedule`)

- [ ] **Step 1: Adicionar as duas `@shared_task`**

Em `clientes/tasks.py`, logo após `_rotaloop_executar_para_cliente`, adicione:

```python
@shared_task
def rotaloop_testar_cliente(cliente_id):
    """Teste de loop de roteamento sob demanda (botão 'Testar Agora' na aba
    Vulnerabilidades) para um único cliente."""
    from .models import Cliente

    try:
        cliente = Cliente.objects.get(id=cliente_id)
    except Cliente.DoesNotExist:
        return {'status': 'ignorado', 'motivo': 'Cliente não encontrado'}

    execucao = _rotaloop_executar_para_cliente(cliente)
    return {
        'status': 'ok' if execucao.sucesso else 'erro',
        'execucao_id': execucao.id,
        'total_loops_detectados': execucao.total_loops_detectados,
    }


@shared_task
def rotaloop_verificar_clientes_agendado():
    """Teste de loop de roteamento a cada 2 dias (Celery Beat) — testa TODOS
    os clientes com blocos IP a cada execução (sem revezamento de grupo,
    diferente do AmpScan: aqui é 1 mtr por bloco, não milhares de probes por
    bloco, então o custo por execução é baixo o suficiente pra não precisar
    fatiar). Isolado em try/except por cliente."""
    from .models import Cliente

    clientes = Cliente.objects.filter(blocos_ip__isnull=False).distinct().order_by('id')
    total, ok, falhas = 0, 0, 0

    logger.info(f'rotaloop_verificar_clientes_agendado: {len(clientes)} cliente(s) nesta execução.')

    for cliente in clientes:
        total += 1
        try:
            execucao = _rotaloop_executar_para_cliente(cliente)
            if execucao.sucesso:
                ok += 1
            else:
                falhas += 1
        except Exception:
            falhas += 1
            logger.exception(f'rotaloop_verificar_clientes_agendado: falha no cliente {cliente.id}')

    logger.info(f'rotaloop_verificar_clientes_agendado: concluído — {ok}/{total} OK, {falhas} falha(s).')
    return {'total': total, 'ok': ok, 'falhas': falhas}
```

- [ ] **Step 2: Registrar a task periódica no Celery Beat**

Em `crm/celery.py`, dentro de `app.conf.beat_schedule`, logo depois da entrada
`'alertas-whatsapp-cobranca'` (linha 91-94, a última do dict), adicione uma vírgula após o `}` dessa
entrada (se ainda não tiver) e:

```python
    'rotaloop-verificar-clientes-agendado': {
        # Testa loop de roteamento em todos os clientes com blocos IP a
        # cada 2 dias. Sem revezamento de grupo (diferente do AmpScan) —
        # ver docstring de rotaloop_verificar_clientes_agendado em
        # clientes/tasks.py pra justificativa.
        'task': 'clientes.tasks.rotaloop_verificar_clientes_agendado',
        'schedule': timedelta(days=2),
    },
```

- [ ] **Step 3: Verificação manual — task sob demanda via shell**

Run:
```bash
cd /opt/crm && venv/bin/python manage.py shell -c "
from clientes.models import Cliente
from clientes.tasks import rotaloop_testar_cliente
c = Cliente.objects.filter(blocos_ip__isnull=False).first()
print('Testando cliente:', c)
print(rotaloop_testar_cliente(c.id))
"
```
Expected: dict `{'status': 'ok', 'execucao_id': <int>, 'total_loops_detectados': <int>}` (roda a
função direto, sem passar pelo worker Celery, só pra validar que a lógica funciona ponta a ponta
contra um cliente real).

- [ ] **Step 4: Commit**

```bash
git add clientes/tasks.py crm/celery.py
git commit -m "feat(rotaloop): tasks Celery sob demanda e periódica"
```

---

### Task 13: Views e URLs

**Files:**
- Modify: `clientes/views.py` (nova seção, após o fim da seção AmpScan, linha ~4602)
- Modify: `clientes/urls.py:93-97`

- [ ] **Step 1: Adicionar as três views**

Em `clientes/views.py`, logo após `ampscan_escanear_agora` (depois da linha 4601, antes do
comentário `# FUNÇÕES DE VALIDAÇÃO RPKI/IRR` na linha 4604), adicione:

```python
# ============================================
# ROTALOOP — DETECÇÃO DE LOOP DE ROTEAMENTO
# ============================================

@login_required(login_url='login')
@modulo_habilitado_required('rpki_irr')
def listar_rotaloop_resultados(request):
    """Lista os loops de roteamento atuais (não resolvidos) de um cliente (AJAX)."""
    from .models import RotaLoopResultado

    cliente_id = request.GET.get('id')
    if not cliente_id:
        return JsonResponse({'error': 'Cliente não especificado'}, status=400)

    cliente = get_object_or_404(Cliente, id=cliente_id)
    if not _perms.pode_acessar_cliente(request.user, cliente):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    resultados = RotaLoopResultado.objects.filter(cliente=cliente, resolvido=False).select_related('bloco_ip').order_by('-ultima_deteccao')

    return JsonResponse({
        'resultados': [{
            'id': r.id,
            'bloco': r.bloco_ip.bloco,
            'ip_alvo': r.ip_alvo,
            'ip_em_loop': r.ip_em_loop,
            'ferramenta': r.ferramenta,
            'hops': r.hops,
            'primeira_deteccao': timezone.localtime(r.primeira_deteccao).strftime('%d/%m/%Y %H:%M'),
            'ultima_deteccao': timezone.localtime(r.ultima_deteccao).strftime('%d/%m/%Y %H:%M'),
        } for r in resultados]
    })


@login_required(login_url='login')
@modulo_habilitado_required('rpki_irr')
def listar_rotaloop_execucoes(request):
    """Últimas execuções do teste de loop de roteamento de um cliente (AJAX)."""
    from .models import RotaLoopExecucaoLog

    cliente_id = request.GET.get('id')
    if not cliente_id:
        return JsonResponse({'error': 'Cliente não especificado'}, status=400)

    cliente = get_object_or_404(Cliente, id=cliente_id)
    if not _perms.pode_acessar_cliente(request.user, cliente):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    execucoes = RotaLoopExecucaoLog.objects.filter(cliente=cliente).order_by('-iniciado_em')[:5]

    return JsonResponse({
        'execucoes': [{
            'id': e.id,
            'iniciado_em': timezone.localtime(e.iniciado_em).strftime('%d/%m/%Y %H:%M:%S'),
            'finalizado_em': timezone.localtime(e.finalizado_em).strftime('%d/%m/%Y %H:%M:%S') if e.finalizado_em else None,
            'em_andamento': e.finalizado_em is None,
            'total_blocos_testados': e.total_blocos_testados,
            'total_loops_detectados': e.total_loops_detectados,
            'sucesso': e.sucesso,
            'erro_mensagem': e.erro_mensagem,
        } for e in execucoes]
    })


@login_required(login_url='login')
@modulo_habilitado_required('rpki_irr')
@require_http_methods(['POST'])
def rotaloop_testar_agora(request):
    """Dispara o teste de loop de roteamento sob demanda para um cliente
    (assíncrono via Celery)."""
    from .models import BlocoIP
    from .tasks import rotaloop_testar_cliente

    cliente_id = request.POST.get('id')
    cliente = get_object_or_404(Cliente, id=cliente_id)
    if not _perms.pode_acessar_cliente(request.user, cliente):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    if not BlocoIP.objects.filter(cliente=cliente).exists():
        return JsonResponse({'error': 'Cliente não tem blocos de IP cadastrados (RPKI/IRR).'}, status=400)

    rotaloop_testar_cliente.delay(cliente.id)
    return JsonResponse({'success': True})
```

- [ ] **Step 2: Registrar as URLs**

Em `clientes/urls.py`, logo depois da linha `path('ampscan/escanear/', ...)` (linha 96), antes do
comentário `# Comentários de Acesso` (linha 98), adicione:

```python
    # RotaLoop — Detecção de loop de roteamento
    path('rotaloop/resultados/', views.listar_rotaloop_resultados, name='listar_rotaloop_resultados'),
    path('rotaloop/execucoes/', views.listar_rotaloop_execucoes, name='listar_rotaloop_execucoes'),
    path('rotaloop/testar/', views.rotaloop_testar_agora, name='rotaloop_testar_agora'),
```

- [ ] **Step 3: Verificação manual das rotas**

Run: `cd /opt/crm && venv/bin/python manage.py show_urls 2>/dev/null | grep rotaloop || venv/bin/python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.settings')
django.setup()
from django.urls import reverse
print(reverse('listar_rotaloop_resultados'))
print(reverse('listar_rotaloop_execucoes'))
print(reverse('rotaloop_testar_agora'))
"`
Expected: os três paths resolvem sem `NoReverseMatch` (`show_urls` só existe se `django-extensions`
estiver instalado — o fallback em Python puro funciona sempre).

- [ ] **Step 4: Commit**

```bash
git add clientes/views.py clientes/urls.py
git commit -m "feat(rotaloop): views e URLs de resultados/execuções/teste sob demanda"
```

---

### Task 14: UI — novo card "Loop de Roteamento" na aba Vulnerabilidades

**Files:**
- Modify: `clientes/templates/listar.html` (novo card após o card do AmpScan, logo antes do
  fechamento da `tab-vulnerabilidades`)
- Modify: `clientes/templates/listar.html` (novo bloco `<script>` de JS, próximo às funções `ampscan*`)

- [ ] **Step 1: Localizar o fim do card AmpScan e o fim da aba**

Run: `grep -n "listaAmpScanResultados\|tab-vulnerabilidades" clientes/templates/listar.html`
Anote a linha de fechamento da `<div id="listaAmpScanResultados">...</div>` e da `</div>` que fecha
`tab-vulnerabilidades` (a estrutura vista durante a exploração tinha o container de resultados
abrindo na linha 956-960; o fechamento da aba vem depois — confirme as linhas exatas antes de editar,
porque a numeração pode ter mudado desde a Task 5).

- [ ] **Step 2: Adicionar o card HTML**

Insira, imediatamente depois do `</div>` que fecha o card do AmpScan (o `<div class="card">` aberto
na linha 890 da versão original) e antes do fechamento de `tab-vulnerabilidades`, o card novo:

```html
                <div class="card mt-4">
                    <div class="card-body">
                        <div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-2">
                            <h5 class="mb-0"><i class="fas fa-diagram-project me-2" style="color:#f85149"></i>Loop de Roteamento</h5>
                            <div class="d-flex gap-2">
                                <button class="btn btn-sm btn-outline-secondary" onclick="rotaloopCarregar()">
                                    <i class="fas fa-sync-alt me-1"></i> Atualizar
                                </button>
                                <button class="btn btn-sm btn-primary" id="rotaloopBtnTestar" onclick="rotaloopTestarAgora()">
                                    <i class="fas fa-route me-1"></i> Testar Agora
                                </button>
                            </div>
                        </div>

                        <div class="alert" style="background: rgba(0, 217, 255, 0.1); border-left: 3px solid var(--accent-cyan); color: var(--text-light); margin-bottom: 20px;">
                            <div class="d-flex align-items-start">
                                <i class="fas fa-info-circle me-3" style="font-size: 20px; margin-top: 2px;"></i>
                                <div>
                                    <strong>Teste de loop de roteamento (traceroute) por bloco de IP</strong><br>
                                    <small>
                                        Roda <code>mtr</code> a partir deste servidor até o primeiro IP útil de cada bloco
                                        cadastrado na aba RPKI/IRR, e verifica se o mesmo IP aparece mais de uma vez no
                                        caminho — sinal clássico de loop de roteamento. Executa automaticamente a cada
                                        2 dias, para todos os clientes com blocos cadastrados — ou a qualquer momento
                                        pelo botão "Testar Agora".
                                    </small>
                                </div>
                            </div>
                        </div>

                        <div id="rotaloopUltimaExecucao" style="font-size:.82rem;color:var(--text-muted);margin-bottom:14px;"></div>

                        <div id="listaRotaLoopResultados">
                            <div class="text-center py-5">
                                <i class="fas fa-spinner fa-spin" style="font-size: 64px; color: var(--accent-cyan); opacity: 0.3;"></i>
                                <p class="mt-3 text-muted">Carregando...</p>
                            </div>
                        </div>
                    </div>
                </div>
```

- [ ] **Step 3: Adicionar o JS**

Localize o `<script>` onde vivem as funções `ampscan*` (Task 5 mexeu nas linhas ~5122-5270 dessa
seção — rode `grep -n "function ampscanEscanearAgora" clientes/templates/listar.html` para achar o
fim exato dessa função). Logo depois do fechamento de `ampscanEscanearAgora`, adicione:

```javascript
    let rotaloopPollTimer = null;

    function rotaloopCarregar() {
        rotaloopCarregarExecucoes();
        rotaloopCarregarResultados();
    }

    function rotaloopCarregarResultados() {
        const clienteId = '{{ cliente.id }}';
        const container = document.getElementById('listaRotaLoopResultados');

        fetch(`/clientes/rotaloop/resultados/?id=${clienteId}`)
            .then(response => response.json())
            .then(data => rotaloopRenderizarResultados(data.resultados))
            .catch(error => {
                console.error('Erro:', error);
                container.innerHTML = `
                    <div class="text-center py-5">
                        <i class="fas fa-exclamation-circle" style="font-size: 64px; color: var(--error-red); opacity: 0.3;"></i>
                        <p class="mt-3 text-muted">Erro ao carregar resultados</p>
                    </div>
                `;
            });
    }

    function rotaloopRenderizarResultados(resultados) {
        const container = document.getElementById('listaRotaLoopResultados');

        if (!resultados || resultados.length === 0) {
            container.innerHTML = `
                <div class="text-center py-5">
                    <i class="fas fa-shield-alt" style="font-size: 64px; color: #3fb950; opacity: 0.4;"></i>
                    <p class="mt-3 text-muted">Nenhum loop de roteamento detectado nos blocos deste cliente.</p>
                </div>
            `;
            return;
        }

        let html = `
            <div class="table-responsive">
                <table class="table table-sm align-middle">
                    <thead>
                        <tr>
                            <th>Bloco</th><th>IP alvo</th><th>IP em loop</th><th>Ferramenta</th><th>Detectado em</th>
                        </tr>
                    </thead>
                    <tbody>
        `;

        resultados.forEach(r => {
            html += `
                <tr>
                    <td><code>${r.bloco}</code></td>
                    <td><code>${r.ip_alvo}</code></td>
                    <td>
                        <span class="badge" style="background:#f8514922;color:#f85149;border:1px solid #f8514966;">
                            <i class="fas fa-rotate me-1"></i>${r.ip_em_loop}
                        </span>
                    </td>
                    <td style="font-size:12px;">${r.ferramenta}</td>
                    <td style="font-size:12px;">${r.ultima_deteccao}</td>
                </tr>
            `;
        });

        html += `</tbody></table></div>`;
        container.innerHTML = html;
    }

    function rotaloopCarregarExecucoes() {
        const clienteId = '{{ cliente.id }}';
        const info = document.getElementById('rotaloopUltimaExecucao');

        fetch(`/clientes/rotaloop/execucoes/?id=${clienteId}`)
            .then(response => response.json())
            .then(data => {
                const execucoes = data.execucoes || [];
                const btn = document.getElementById('rotaloopBtnTestar');

                if (execucoes.length === 0) {
                    info.innerHTML = '<i class="fas fa-clock me-1"></i>Nenhum teste executado ainda.';
                    return;
                }

                const ultima = execucoes[0];

                if (ultima.em_andamento) {
                    info.innerHTML = `<i class="fas fa-spinner fa-spin me-1"></i>Teste em andamento (iniciado às ${ultima.iniciado_em})...`;
                    if (btn) btn.disabled = true;
                    if (!rotaloopPollTimer) {
                        rotaloopPollTimer = setInterval(() => rotaloopCarregarExecucoes(), 4000);
                    }
                    return;
                }

                if (rotaloopPollTimer) {
                    clearInterval(rotaloopPollTimer);
                    rotaloopPollTimer = null;
                    rotaloopCarregarResultados();
                }
                if (btn) btn.disabled = false;

                if (!ultima.sucesso) {
                    info.innerHTML = `<i class="fas fa-exclamation-triangle me-1" style="color:#f85149"></i>Último teste (${ultima.finalizado_em}) falhou: ${ultima.erro_mensagem || 'erro desconhecido'}`;
                    return;
                }

                info.innerHTML = `<i class="fas fa-check-circle me-1" style="color:#3fb950"></i>Último teste em ${ultima.finalizado_em} — ${ultima.total_blocos_testados} bloco(s) testado(s), ${ultima.total_loops_detectados} loop(s) detectado(s).`;
            })
            .catch(error => console.error('Erro ao carregar execuções RotaLoop:', error));
    }

    function rotaloopTestarAgora() {
        const clienteId = '{{ cliente.id }}';
        const btn = document.getElementById('rotaloopBtnTestar');
        if (btn) btn.disabled = true;

        fetch('/clientes/rotaloop/testar/', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': getCsrfToken()},
            body: `id=${clienteId}`,
        })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    showError('ERRO', data.error, 5000);
                    if (btn) btn.disabled = false;
                    return;
                }
                showInfo('TESTE INICIADO', 'O teste de loop de roteamento foi disparado em segundo plano — acompanhe o status acima.', 4000);
                rotaloopCarregarExecucoes();
            })
            .catch(error => {
                console.error('Erro:', error);
                showError('ERRO', 'Não foi possível iniciar o teste.', 5000);
                if (btn) btn.disabled = false;
            });
    }
```

- [ ] **Step 4: Disparar o carregamento junto com o AmpScan ao abrir a aba**

Localize o trecho (visto durante a exploração, por volta da linha 6210 da versão original) onde a
troca de aba chama `ampscanCarregar()`:

```javascript
            if (typeof ampscanCarregar === 'function') ampscanCarregar();
```

Adicione logo abaixo:

```javascript
            if (typeof rotaloopCarregar === 'function') rotaloopCarregar();
```

- [ ] **Step 5: Verificação manual no navegador**

Run: `sudo systemctl restart gunicorn && sudo systemctl restart celery`

Abra a aba Vulnerabilidades de um cliente com blocos IP cadastrados. Confirme que:
1. O card "Loop de Roteamento" aparece abaixo do card AmpScan, mostrando "Nenhum teste executado
   ainda." antes do primeiro clique.
2. Clicar em "Testar Agora" desabilita o botão, mostra "Teste em andamento...", e depois de alguns
   segundos mostra o resumo (`X bloco(s) testado(s), Y loop(s) detectado(s)`).
3. Se nenhum loop for detectado (esperado na maioria dos casos reais), a tabela mostra o estado
   vazio "Nenhum loop de roteamento detectado...".

- [ ] **Step 6: Commit**

```bash
git add clientes/templates/listar.html
git commit -m "feat(rotaloop): card e JS de Loop de Roteamento na aba Vulnerabilidades"
```

---

### Task 15: Documentação da feature nova

**Files:**
- Create: `docs/ROTALOOP_DETECCAO_LOOP_ROTEAMENTO.md`

- [ ] **Step 1: Escrever o doc, seguindo o padrão de `docs/AMPSCAN_VARREDURA_AMPLIFICACAO.md`**

```markdown
# Detecção de Loop de Roteamento (RotaLoop) — Documentação Técnica

**Arquivos principais:**
- `clientes/tasks.py` — `_rotaloop_ip_alvo`, `_rotaloop_detectar_loop`, `_rotaloop_mtr_json`,
  `_rotaloop_executar_para_bloco`, `_rotaloop_executar_para_cliente`, `rotaloop_testar_cliente`
  (sob demanda), `rotaloop_verificar_clientes_agendado` (task Celery periódica)
- `clientes/views.py` — `listar_rotaloop_resultados`, `listar_rotaloop_execucoes`,
  `rotaloop_testar_agora`
- `clientes/models.py` — `RotaLoopResultado`, `RotaLoopExecucaoLog`
- `clientes/templates/listar.html` — card "Loop de Roteamento" dentro da aba `tab-vulnerabilidades`
- `crm/celery.py` — agendamento (`rotaloop-verificar-clientes-agendado`)

**Atualizado em:** 11/08/2026

**Ver também:** [AMPSCAN_VARREDURA_AMPLIFICACAO.md](AMPSCAN_VARREDURA_AMPLIFICACAO.md) — mesma aba
Vulnerabilidades, mesmo `BlocoIP` cadastrado na aba RPKI/IRR como fonte de alvos, mas pipeline
independente (não usa o runner Rust do AmpScan).

## Visão Geral

Para cada `BlocoIP` cadastrado, testa se o caminho de rede (via `mtr`) até o primeiro IP útil do
bloco (`network_address + 1`) tem um loop de roteamento — o mesmo IP aparecendo mais de uma vez no
traceroute. Loop de roteamento nesse contexto normalmente indica erro de configuração BGP/estática
que faz pacotes ficarem "quicando" entre dois ou mais roteadores até o TTL expirar, sem nunca chegar
ao destino.

## Por que `mtr --json` e não parsing de texto

`mtr` suporta `--json`, que devolve os hops já estruturados (`report.hubs`, cada um com `count` e
`host`) — muito mais confiável que fazer regex em cima da saída colorida/tabular do CLI interativo.
Confirmado funcionando na versão instalada em produção (`mtr 0.95`). Se `mtr` não estiver disponível
no servidor, o teste falha com `status='inconclusivo'` — não há fallback pra `traceroute`/`tracepath`
com parsing de texto livre.

## Critério de loop

O mesmo IP aparecendo em 2 ou mais hops do caminho (não precisa ser consecutivo) —
`_rotaloop_detectar_loop()` em `clientes/tasks.py`. Hops sem resposta (`"???"` no JSON do mtr, viram
`ip=None` internamente) não contam pra detecção. Critério escolhido deliberadamente restritivo (só
IP repetido, não "esgotou os hops sem chegar ao destino") pra evitar falso positivo em blocos cujo
primeiro IP simplesmente não responde a ICMP mas a rota está correta.

## Alvo do teste

Sempre o primeiro IP útil do bloco (`network_address + 1`; blocos `/31`/`/32`/`/127`/`/128` usam o
próprio `network_address`, que é o único endereço disponível). Não há campo de IP de teste
configurável — o objetivo é medir o caminho de rede até a faixa do bloco, não necessariamente obter
resposta de um host real ali dentro.

## Persistência — só loops são gravados

Mesmo padrão do `AmpScanResultado`: `RotaLoopResultado` só ganha uma linha quando
`status == 'loop_detectado'`. Blocos que testam normal não geram registro. Um loop que estava
presente e deixa de aparecer numa execução seguinte é marcado `resolvido=True` (não apagado) —
preserva histórico de quando o problema existiu.

## Agendamento — sem revezamento de grupos

Diferente do AmpScan (que revezava clientes em grupos pra não sondar todo mundo no mesmo dia), o
RotaLoop testa **todos** os clientes com blocos IP a cada execução (`crm/celery.py`,
`timedelta(days=2)`). Justificativa: o custo por bloco é 1 execução de `mtr` (poucos segundos), não
milhares de probes como no AmpScan — não há necessidade de fatiar a carga.
```

Se essa task for executada em outro dia (não 11/08/2026), atualize a data em "Atualizado em" antes
de commitar, mesmo padrão dos outros docs da pasta (`DD/MM/YYYY`).

- [ ] **Step 2: Commit**

```bash
git add docs/ROTALOOP_DETECCAO_LOOP_ROTEAMENTO.md
git commit -m "docs: documenta a feature de detecção de loop de roteamento (RotaLoop)"
```

---

## Task 16: Verificação final e reinício de serviços em produção

**Files:** nenhum (só operação)

- [ ] **Step 1: Rodar a suíte de testes completa do app `clientes` uma última vez**

Run: `cd /opt/crm && venv/bin/python manage.py test clientes -v 1 --keepdb`
Expected: `OK`, sem falhas.

- [ ] **Step 2: Confirmar que todas as migrações estão aplicadas**

Run: `venv/bin/python manage.py showmigrations clientes | tail -5`
Expected: as duas migrações novas (`0105_...`, `0106_...`) aparecem marcadas com `[X]`.

- [ ] **Step 3: Confirmar que o binário do runner AmpScan foi recompilado com as portas novas**

Run: `ls -la /opt/crm/tools/ampscan_runner/target/release/crm_ampscan_runner`
Expected: timestamp do binário posterior ao commit da Task 1 (se mais antigo, rode
`cd tools/ampscan_runner && ~/.cargo/bin/cargo build --release` de novo).

- [ ] **Step 4: Reiniciar os serviços que leem código alterado**

`views.py`/`urls.py`/`templates` → HTTP → gunicorn. `tasks.py` → Celery worker + beat (mesmo
serviço, `celery.service`, roda os dois juntos neste servidor).

```bash
sudo systemctl restart gunicorn
sudo systemctl restart celery
```

- [ ] **Step 5: Checar os logs dos dois serviços depois do restart**

Run: `sudo systemctl status gunicorn celery --no-pager | head -40`
Expected: `active (running)` nos dois, sem traceback de import error (o erro mais provável aqui,
se algo estiver errado, é um import quebrado em `tasks.py` ou `views.py` — apareceria no log do
serviço correspondente logo no boot).

- [ ] **Step 6: Smoke test end-to-end no navegador**

Abra a aba Vulnerabilidades de um cliente real com blocos IP cadastrados:
1. Clique "Escanear Agora" (AmpScan) — confirme que a varredura conclui e, se houver SSH/RDP aberto
   nos hosts do bloco, aparece com badge "Exposto (gestão remota)".
2. Clique "Testar Agora" (Loop de Roteamento) — confirme que o teste conclui e mostra o resumo de
   blocos testados / loops detectados.

Isso encerra a implementação das duas features.
