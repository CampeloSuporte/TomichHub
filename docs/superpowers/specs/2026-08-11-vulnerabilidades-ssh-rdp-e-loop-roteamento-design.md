# Aba Vulnerabilidades — portas SSH/RDP no AmpScan + detecção de loop de roteamento

**Data:** 2026-08-11
**Status:** Aprovado para planejamento
**Depende de / estende:** `docs/AMPSCAN_VARREDURA_AMPLIFICACAO.md` (arquitetura da varredura de
amplificação já em produção; este documento assume esse contexto como lido)

## Objetivo

Duas extensões independentes na aba **Vulnerabilidades** (`clientes/templates/listar.html`,
`tab-vulnerabilidades`):

1. Incluir as portas **22 (SSH)** e **3389 (RDP)** na varredura já existente (AmpScan), sinalizando
   exposição à internet sem tratar isso como a mesma categoria de vulnerabilidade real (amplificação
   DDoS, proxy comprometido).
2. Detectar **loop de roteamento** nos blocos IP (`BlocoIP`) cadastrados de cada cliente — feature
   nova, sem infraestrutura prévia no sistema.

## Fora de escopo

- Qualquer verificação além de "porta TCP aberta" para SSH/RDP (banner grabbing, força bruta de
  credencial, detecção de versão vulnerável). Só reaproveita o probe `tcp_connect` já existente.
- Campo de "IP de teste" manual por `BlocoIP` — o alvo do teste de loop é sempre calculado
  automaticamente (primeiro IP útil do bloco).
- Suporte a `traceroute`/`tracepath` como fallback estruturado para detecção de loop — a feature 2
  depende de `mtr --json` (confirmado instalado no servidor, `mtr 0.95`). Se `mtr` não estiver
  disponível a execução falha e fica registrada no log de execução, sem fallback de parsing de texto
  livre.
- Revezamento de grupos de clientes na task periódica de loop (diferente do AmpScan) — ver seção
  "Agendamento" da Feature 2 para a justificativa.

---

## Feature 1 — Portas 22 (SSH) e 3389 (RDP) no AmpScan

### Runner Rust

`tools/ampscan_runner/src/main.rs`, função `default_ports()` — acrescenta duas tuplas ao final da
lista existente (mesmo formato `(porta, protocolo, nome, descrição, probe_type, payload)`, mesmo
`probe_type` já usado por 4145/5678):

```rust
(22,   "tcp", "SSH", "Acesso SSH exposto à internet — alvo comum de brute-force/credential stuffing", "tcp_connect", vec![]),
(3389, "tcp", "RDP", "Acesso RDP exposto à internet — vetor comum de ransomware (credenciais fracas, exploits como BlueKeep)", "tcp_connect", vec![]),
```

Nenhuma mudança na engine (`ampscan::scanner`) — `probe_type: "tcp_connect"` já é tratado
genericamente. Binário precisa ser recompilado (`cargo build --release` dentro de
`tools/ampscan_runner/`) após a mudança, como já documentado no runbook do AmpScan.

### Novo status `exposto`

`clientes/models.py`, `AmpScanResultado.STATUS_CHOICES` (linha ~704) ganha uma terceira opção:

```python
STATUS_CHOICES = [
    ('vulneravel', 'Vulnerável'),
    ('protegido', 'Protegido (aberto, mitigado)'),
    ('exposto', 'Exposto (gestão remota)'),
]
```

`clientes/tasks.py::_ampscan_executar_para_cliente` (linha ~2451): ao processar cada resultado do
runner, se `status_raw == 'Open'` **e** `(porta, protocolo)` for `(22, 'tcp')` ou `(3389, 'tcp')`,
grava `status = 'exposto'` em vez de `'vulneravel'`. Constante local:

```python
_AMPSCAN_PORTAS_EXPOSICAO = {(22, 'tcp'), (3389, 'tcp')}
```

Contagem: `AmpScanExecucaoLog` ganha campo `total_expostos` (`PositiveIntegerField`, default 0),
somado à parte de `total_vulneraveis`/`total_protegidos` no mesmo loop que já popula os outros dois
contadores. Resultados com status `exposto` participam normalmente do ciclo de
`resolvido`/`resolvido_em` (mesma lógica que já existe para `vulneravel`/`protegido` — se a porta
fechar numa varredura seguinte, marca resolvido em vez de apagar).

### UI (`clientes/templates/listar.html`)

- Tabela "Ver as 21 portas testadas" (linhas 917-947) vira "Ver as 23 portas testadas", com as duas
  linhas novas (coluna "Risco" descreve exposição de gestão remota, não amplificação).
- `ampscanRenderizarResultados` (linha ~5147): badge para `status === 'exposto'` usa cor neutra
  (`#58a6ff`, mesma família do `--accent-cyan` já usado em outros alertas informativos da página) e
  ícone `fa-door-open`, distinto do vermelho/`fa-radiation` (`vulneravel`) e laranja/`fa-shield-halved`
  (`protegido`).
- `ampscanCarregarExecucoes` (linha ~5197): texto da última varredura passa a citar também "N
  exposto(s)": `"... — X IPs testados, Y vulnerável(is), Z protegido(s), W exposto(s)..."`.

### Migração

Uma migração Django em `clientes/migrations/`: altera `choices` de `AmpScanResultado.status` (não
requer alteração de schema, só metadata) e adiciona `AmpScanExecucaoLog.total_expostos`.

---

## Feature 2 — Detecção de loop de roteamento por bloco IP

### Alvo do teste

Para cada `BlocoIP` do cliente: `ipaddress.ip_network(bloco.bloco, strict=False)`, alvo =
`network_address + 1` (primeiro IP útil). Exceção: blocos com `num_addresses <= 2` (`/31` IPv4,
`/127` IPv6, ou `/32`/`/128` unitário) usam o próprio `network_address` — não há um "+1" distinto
para testar.

```python
def _rotaloop_ip_alvo(net):
    if net.num_addresses <= 2:
        return str(net.network_address)
    return str(net.network_address + 1)
```

Não há filtro de tamanho de prefixo aqui (diferente do AmpScan, que ignora blocos maiores que /16 ou
/112) — o teste sempre usa exatamente 1 IP por bloco, então o tamanho do bloco não afeta o custo.

### Execução do traceroute estruturado

Nova função em `clientes/tasks.py` (não reaproveita `_traceroute_direto`/`_traceroute_via_proxy` de
`clientes/views.py`, que retornam texto livre para exibição — aqui precisamos de hops estruturados
para detectar repetição de IP):

```python
def _rotaloop_mtr_json(host, timeout=30):
    """Roda mtr --json e retorna lista de hops [{'hop': int, 'ip': str}, ...].
    Levanta RuntimeError se mtr não estiver instalado ou o subprocess falhar/expirar."""
```

Comando: `mtr --report --json --no-dns -c 3 <host>`. Parse do JSON: `report.hubs` é uma lista
ordenada por hop (`count` = número do hop, `host` = IP do roteador que respondeu naquele hop, ou
ausente/`"???"` quando não houve resposta — hops sem resposta são mantidos na lista com `ip=None`,
não contam para detecção de loop mas aparecem no path salvo para inspeção visual).

### Critério de loop

Entre os hops com IP conhecido (não `None`), se o mesmo IP aparece em 2 ou mais posições da lista →
`status = 'loop_detectado'`, `ip_em_loop` = esse IP (o primeiro a repetir, se houver mais de um
candidato). Caso contrário, `status = 'normal'` (mtr rodou e não achou repetição — não significa
necessariamente que o destino respondeu, só que o caminho não tem ciclo). Falha ao rodar `mtr`
(ausente, timeout, erro) → `status = 'inconclusivo'`, sem hops.

### Modelos novos (`clientes/models.py`)

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
    hops       = models.JSONField(default=list, blank=True)
    ferramenta = models.CharField(max_length=10, default='mtr')

    primeira_deteccao = models.DateTimeField(auto_now_add=True)
    ultima_deteccao    = models.DateTimeField(auto_now=True)

    resolvido    = models.BooleanField(default=False)
    resolvido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Resultado de Loop de Roteamento'
        verbose_name_plural = 'Resultados de Loop de Roteamento'
        ordering = ['-ultima_deteccao']


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
```

`OneToOneField` (não `ForeignKey` + `unique_together` como no AmpScan) porque aqui é sempre 1 alvo
por bloco — não há múltiplas linhas possíveis por `bloco_ip` como há em `AmpScanResultado`
(ip:porta:protocolo variam por IP escaneado dentro do bloco).

### Tasks (`clientes/tasks.py`)

Espelha a estrutura do AmpScan:

- `_rotaloop_executar_para_bloco(bloco)` → calcula IP alvo, chama `_rotaloop_mtr_json`, aplica o
  critério de loop, retorna dict de resultado (não grava no banco — separa cálculo de persistência
  para facilitar teste unitário).
- `_rotaloop_executar_para_cliente(cliente)` → cria `RotaLoopExecucaoLog`, itera
  `cliente.blocos_ip.all()`, chama `_rotaloop_executar_para_bloco` por bloco (isolado em
  try/except por bloco — falha num bloco não derruba os demais, mesmo padrão do resto do arquivo),
  upsert `RotaLoopResultado` só quando `status == 'loop_detectado'`, marca `resolvido=True` nos que
  não apareceram mais nesta execução (mesma lógica de `abertos_anteriores` do AmpScan), popula
  contadores e `finalizado_em`, retorna a execução.
- `rotaloop_testar_cliente` (`@shared_task`) — sob demanda, botão "Testar Agora".
- `rotaloop_verificar_clientes_agendado` (`@shared_task`) — periódica, itera **todos** os clientes
  com `blocos_ip` (sem revezamento de grupo, diferente de `ampscan_varrer_clientes_agendado`):
  1 `mtr` por bloco é ordens de magnitude mais barato que escanear até 65536 hosts por bloco, então
  não há motivo de custo para fatiar em grupos rotativos aqui.

### Agendamento (`crm/celery.py`)

Nova entrada `rotaloop-verificar-clientes-agendado`, `timedelta(days=2)`, mesmo padrão de
configuração da entrada `ampscan-varrer-clientes-agendado` já existente.

### Views/URLs

Mesmo padrão do AmpScan, em `clientes/views.py` e `clientes/urls.py`:

- `GET /clientes/rotaloop/resultados/?id=<cliente_id>` → `listar_rotaloop_resultados` — lista
  `RotaLoopResultado` não resolvidos do cliente.
- `GET /clientes/rotaloop/execucoes/?id=<cliente_id>` → `listar_rotaloop_execucoes` — histórico de
  `RotaLoopExecucaoLog`, mesmo formato de resposta (`em_andamento` quando `finalizado_em is None`)
  usado pelo polling do AmpScan.
- `POST /clientes/rotaloop/testar/` (`id` no body) → `rotaloop_testar_agora` — dispara
  `rotaloop_testar_cliente.delay(cliente_id)`, mesma checagem de permissão
  (`_perms.pode_acessar_cliente`) usada em `ampscan_escanear_agora`.

### UI (`clientes/templates/listar.html`)

Novo card na aba Vulnerabilidades, logo abaixo do card do AmpScan (mesmo `tab-vulnerabilidades`):

- Título "Loop de Roteamento (Traceroute)", botão "Testar Agora" (`rotaloopTestarAgora()`) e
  "Atualizar" (`rotaloopCarregar()`), texto explicativo curto (o que é testado, primeiro IP útil de
  cada bloco, ferramenta `mtr`).
- Tabela de resultados: Bloco, IP alvo, IP em loop, Ferramenta, Detectado em — mesmo estilo visual
  (badge vermelho, ícone `fa-recycle` ou `fa-rotate` para loop) da tabela do AmpScan.
- Linha de última execução (`rotaloopUltimaExecucao`), mesmo texto informativo (blocos testados,
  loops detectados, horário) e mesmo mecanismo de polling (`setInterval` enquanto
  `em_andamento === true`) já usado por `ampscanCarregarExecucoes`.
- Estado vazio: "Nenhum loop de roteamento detectado nos blocos deste cliente." com ícone de escudo
  verde, mesmo padrão do AmpScan quando não há resultados.

### Migração

Uma migração Django em `clientes/migrations/` criando `RotaLoopResultado` e `RotaLoopExecucaoLog`.

---

## Testes

- **Feature 1**: teste unitário de `_ampscan_executar_para_cliente` (ou da lógica de classificação
  extraída, se for isolada em função própria) cobrindo: porta 22/tcp aberta → `status='exposto'`,
  não incrementa `total_vulneraveis`; porta 4145/tcp aberta → continua `status='vulneravel'` (não
  regride o comportamento existente).
- **Feature 2**: teste unitário de `_rotaloop_ip_alvo` (bloco normal, `/31`, `/32`, IPv6 `/127`) e de
  uma função pura de detecção de loop a partir de uma lista de hops fake (sem chamar `mtr` de
  verdade) cobrindo: sem repetição → `normal`; IP repetido em hops não consecutivos → ainda
  `loop_detectado` (critério é "aparece 2+ vezes em qualquer posição", não só consecutivo); hops com
  `ip=None` intercalados não quebram a detecção nem contam como repetição entre si.
- Sem teste de integração contra `mtr` real (ambiente de CI provavelmente não tem rota de internet
  igual à do servidor de produção) — mock do `subprocess.run`/JSON de saída.

## Riscos e limitações conhecidas

- `mtr --json` como única fonte estruturada: se a saída do `mtr` mudar de formato entre versões
  (`0.95` confirmado em produção), o parser pode quebrar silenciosamente — mitigado por
  `status='inconclusivo'` em vez de exceção não tratada quando o JSON não tem o formato esperado.
- Falso positivo de loop é possível em roteamento assimétrico raro onde o mesmo IP legitimamente
  responde em dois hops por causa de MPLS/multipath sem ser um loop real — aceito como trade-off
  (você escolheu esse critério como o de menor ruído entre as opções apresentadas).
- Blocos `/31`/`/32` testando o próprio `network_address` podem não ter esse IP atribuído a nenhum
  host real — o teste ainda é válido (mede o caminho de rede até ali, não exige resposta do host
  final), mas pode aparecer como `inconclusivo` com mais frequência que blocos maiores.
