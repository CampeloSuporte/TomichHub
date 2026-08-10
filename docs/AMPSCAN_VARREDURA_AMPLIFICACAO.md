# Varredura de Amplificação DDoS (AmpScan) — Documentação Técnica

**Arquivos principais:**
- `tools/ampscan_runner/` — runner Rust (`Cargo.toml`, `src/main.rs`, `tests/smoke.rs`), lib
  externa pinada: [github.com/gondimcodes/ampscan](https://github.com/gondimcodes/ampscan)
- `clientes/tasks.py` — `_ampscan_executar_para_cliente`, `_ampscan_prefixos_elegiveis`,
  `_ampscan_localizar_bloco`, `_ampscan_grupo_do_dia`, `ampscan_escanear_cliente` (sob demanda),
  `ampscan_varrer_clientes_agendado` (task Celery periódica)
- `clientes/views.py` — `listar_ampscan_resultados`, `listar_ampscan_execucoes`,
  `ampscan_escanear_agora`
- `clientes/models.py` — `AmpScanResultado`, `AmpScanExecucaoLog`
- `clientes/templates/listar.html` — aba "Vulnerabilidades" (`tab-vulnerabilidades`)
- `crm/celery.py` — agendamento (`ampscan-varrer-clientes-agendado`)

**Atualizado em:** 2026-08-10

**Ver também:** [RPKI_IRR.md](RPKI_IRR.md) — o cadastro (`BlocoIP`) que alimenta esta varredura é o
mesmo usado pela validação RPKI/IRR; aqui não se cadastra nada novo, só se reaproveita.

---

## Visão Geral

Cada `BlocoIP` cadastrado na aba RPKI/IRR de um cliente vira alvo de uma varredura periódica (e sob
demanda) por portas de amplificação DDoS — serviços UDP/TCP mal configurados que terceiros podem
abusar para amplificar um ataque de DDoS contra outro alvo qualquer, usando a rede do cliente como
refletor (DNS resolver aberto, NTP monlist, SNMP `public`, Memcached exposto, etc.).

O resultado atual fica em `AmpScanResultado` (uma linha por `cliente + ip + porta + protocolo`,
upsert a cada varredura) e o histórico de execuções em `AmpScanExecucaoLog`. A UI mostra isso na
aba **Vulnerabilidades**, ao lado de RPKI/IRR, com um botão "Escanear Agora" por cliente.

## Por que um binário Rust em vez de reimplementar em Python

O projeto [ampscan](https://github.com/gondimcodes/ampscan) (Marcelo Gondim) já implementa as
sondas de amplificação (DNS, NTP, SNMP, Memcached, SSDP, CLDAP, etc.) em Rust, com controle fino de
concorrência/timeout/retries. Reimplementar isso em Python só pra reaproveitar a infra Celery não
valia o esforço nem a chance de introduzir bugs sutis de protocolo.

O CLI original do ampscan, porém, foi desenhado pra uso interativo: banco próprio SQLCipher
(`AMPSCAN_DB_KEY`), autenticação de usuário, sem flag `--json` (só imprime tabela colorida ou gera
PDF). Nenhum dos dois serve bem a um pipeline automatizado. A solução foi **não usar o CLI** — usar
a lib do ampscan como dependência de um binário próprio, fino:

```toml
# tools/ampscan_runner/Cargo.toml
[dependencies]
ampscan = { git = "https://github.com/gondimcodes/ampscan.git", rev = "<commit pinado>" }
```

`tools/ampscan_runner/src/main.rs`:
- Lê JSON via stdin: `{"prefixes": [{"id", "prefix", "description"}], "concurrency", "timeout", "retries"}`.
- Monta a lista de portas padrão localmente (réplica exata dos 21 pares porta/protocolo/probe de
  `ampscan::db::port_repo::seed_default_ports`) — não usa o módulo `db` do ampscan (SQLCipher),
  só `ampscan::scanner::run_scan` e as structs `Port`/`Prefix` (dados puros, sem tocar banco).
- Chama `scanner::run_scan(ports, prefixes, config).await` direto.
- Imprime o `ScanReport` inteiro (já `Serialize` via serde) como JSON em stdout.

`clientes/tasks.py` chama esse binário via `subprocess.run(..., input=json, capture_output=True)` e
faz `json.loads(proc.stdout)`. Sem banco, sem prompt de senha, sem parsear tabela colorida.

**Build:** o binário compilado (`tools/ampscan_runner/target/release/crm_ampscan_runner`) está no
`.gitignore` — depois de um `git pull` com mudança em `tools/ampscan_runner/`, é preciso rodar
`cargo build --release` de novo lá dentro antes do Celery conseguir chamar o runner. O `rustc` do
`apt` em Ubuntu 24.04 é a versão 1.75 (velha demais — as deps do ampscan exigem `edition2024`);
use o toolchain instalado via [rustup](https://rustup.rs) (`~/.cargo/bin` no `PATH`).

## Bug confirmado na lib upstream (não usado por nós)

`ampscan::scanner::scan_single_ip` monta um `Prefix` cujo campo `prefix` é o IP puro, sem `/CIDR`
(ex: `"127.0.0.1"`), e passa isso pra `run_scan`, que tenta reparsear `prefix` como `IpNet` — falha
com `Invalid prefix: 127.0.0.1`. Afeta o comando `ampscan scan single <ip>` do CLI original.
Confirmado escrevendo um teste (`tests/smoke.rs`) que reproduziu o erro na primeira tentativa.
Não usado no runner: sempre montamos CIDR completo (`/32` ou `/128` pra IP único), que não passa
por esse caminho.

## Limites de tamanho de prefixo (hard-coded na lib, não é config nossa)

O ampscan só aceita prefixos até **/16 IPv4** (65536 hosts) e **/112 IPv6** (65536 hosts) — tanto no
cadastro quanto na hora de expandir o prefixo em hosts individuais pra varrer. Blocos maiores (ex:
um `/48` IPv6 inteiro, alocação comum via LACNIC) são **ignorados**, não geram erro —
`_ampscan_prefixos_elegiveis()` filtra antes de chamar o runner e conta em
`AmpScanExecucaoLog.blocos_ignorados`.

IPv6 grande fica sem varredura de amplificação neste primeiro corte. Se vier pedido futuro pra
cobrir isso, a saída é varrer só endereços IPv6 documentados individualmente (`IPAMEndereco`/
`Acesso.host_ipv6`), não o bloco inteiro — inviável brute-forçar um `/48`.

## Mapeamento IP → BlocoIP

O `ScanReport` do ampscan só guarda os prefixos escaneados como strings soltas
(`prefixes_scanned: Vec<String>`), não associa cada `ProbeResult.ip` a um prefixo de origem. Como os
blocos de um mesmo cliente normalmente não se sobrepõem, `_ampscan_localizar_bloco()` resolve isso
do lado Python: para cada IP resultado, testa containment (`ip in ipaddress.ip_network(...)`) contra
os blocos elegíveis daquele cliente.

## Rotina agendada — grupos rotativos a cada 2 dias

`ampscan_varrer_clientes_agendado` dispara a cada 2 dias (`crm/celery.py`,
`timedelta(days=2)`), mas cada execução só escaneia **1 de 3 grupos** de clientes
(`AMPSCAN_TOTAL_GRUPOS`, `clientes/tasks.py`) — evita disparar sondas de amplificação contra todos
os clientes no mesmo dia. Cobertura completa de todos os clientes leva
`AMPSCAN_TOTAL_GRUPOS * 2` dias (6 dias com o valor atual).

O grupo do dia é calculado por **data corrente**, não por um contador salvo em algum lugar:

```python
dias_desde_epoch = (timezone.localdate() - date(2026, 1, 1)).days
ciclo = dias_desde_epoch // 2       # cada ciclo (1 grupo) dura 2 dias
grupo_hoje = ciclo % AMPSCAN_TOTAL_GRUPOS
```

Cliente entra no grupo por `cliente.id % AMPSCAN_TOTAL_GRUPOS`. Determinístico e sobrevive a
reinício do Celery sem repetir/pular grupo por causa de drift do agendador (`timedelta` no Celery
Beat não garante horário fixo, só intervalo aproximado desde a última execução — a data real na
hora de rodar é o que decide o grupo, não um contador interno).

## Correção — regressão em `enviar_disparo_hotspot_lead` (2026-08-10)

A primeira versão deste código foi inserida no fim de `clientes/tasks.py` via um `old_string` que
capturou só a penúltima linha de `enviar_disparo_hotspot_lead` (`raise self.retry(...)`), sem saber
que havia mais uma linha depois dela (`return {'status': 'concluido', 'resultados': resultados}`,
o retorno normal quando não há falha transitória). O bloco novo do AmpScan foi inserido **entre**
essas duas linhas, órfão de sua função original, virando código morto dentro de
`ampscan_varrer_clientes_agendado` (nunca executado, porque vinha depois do `return` daquela
função — mas usava uma variável `resultados` que nem existe nesse escopo). `enviar_disparo_hotspot_lead`
passou a retornar `None` implicitamente no caminho sem retry, em vez do dict esperado.

**Corrigido** movendo o `return` de volta pro fim de `enviar_disparo_hotspot_lead` e removendo a
linha órfã. Lição: ao inserir código no fim de um arquivo/função via edição textual, sempre conferir
se o trecho substituído é de fato o *último* conteúdo, não confiar em heurística de "parece o fim".

## Correção — horário exibido em UTC, não no fuso local (2026-08-10)

`listar_ampscan_resultados`/`listar_ampscan_execucoes` formatavam `primeira_deteccao`/
`ultima_deteccao`/`iniciado_em`/`finalizado_em` direto com `.strftime(...)`, sem converter pro fuso
local antes — como `USE_TZ = True` e o banco guarda em UTC, a aba mostrava a última varredura ~3h
"no futuro" (ex: varredura terminada às 16:01 locais aparecia como 19:01). Corrigido com
`timezone.localtime(dt).strftime(...)`, mesmo padrão já usado em outras views do arquivo (ex:
`clientes/views.py` linha ~3533, formatação de `BackupLog`).

## Achado real do primeiro teste em produção

Cliente **CONECTONLINE** (id=10), bloco `192.140.67.0/24` — 78 dos 256 IPs com SNMP (porta
161/UDP, community pública) respondendo com payload SNMP `GetResponse-PDU` válido (não é falso
positivo de "recebeu qualquer pacote": o probe do ampscan valida o conteúdo real da resposta —
`response[0] == 0x30` e presença de `0xA2`/`0xA8`, ver `ampscan/src/scanner/probes.rs`). Indica
provisionamento em massa de CPE com SNMP público habilitado por padrão. Ficou registrado como
alerta real na aba, não foi apagado.

## As 21 portas testadas

Réplica exata de `ampscan::db::port_repo::seed_default_ports` (hoje fixa no `main.rs` do runner,
não configurável pela UI):

| Porta | Proto | Serviço | Risco |
|---|---|---|---|
| 17 | UDP | QOTD | legado — responde a qualquer pacote |
| 19 | UDP | CHARGEN | legado — gera stream de caracteres |
| 53 | UDP | DNS | resolver aberto — até 54x |
| 69 | UDP | TFTP | extração de arquivo + amplificação |
| 111 | UDP | RPC (Portmapper) | enumeração de serviço |
| 123 | UDP | NTP | monlist/readvar — até 556x |
| 137 | UDP | NetBIOS | informação de rede exposta |
| 161 | UDP | SNMP | community "public" — até 6.3x |
| 389 | UDP | CLDAP | até 70x |
| 427 | UDP | SLP | até 2200x |
| 520 | UDP | RIPv1 | legado — até 30x |
| 1900 | UDP | SSDP (UPnP) | até 30x |
| 3283 | UDP | ARMS | Apple Remote Management exposto |
| 3702 | UDP | WS-Discovery | até 153x |
| 5353 | UDP | mDNS | resolver aberto — até 4.7x |
| 5683 | UDP | CoAP | IoT — até 34x |
| 10001 | UDP | UBNT Discovery | dispositivo Ubiquiti exposto |
| 11211 | UDP | Memcached | até 51000x — a mais perigosa |
| 37810 | UDP | DVR-DHCPDiscover | DVR/câmera exposto |
| 4145 | TCP | MikroTik SOCKS | proxy aberto — indício de comprometimento (não é amplificação) |
| 5678 | TCP | MikroTik Meris | indício de botnet Meris (não é amplificação) |

---

**Última atualização:** 10/08/2026
