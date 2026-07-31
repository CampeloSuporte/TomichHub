# Automação BGP — Documentação Técnica

**Arquivos principais:** `clientes/backup_parser.py`, `clientes/bgp_matcher.py`, `clientes/bgp_actions.py`,
`clientes/bgp_views.py`, `clientes/tasks.py` (`atualizar_snapshots_bgp`), `clientes/models.py`
(`BgpSnapshot`, `AcaoBgp`), `clientes/templates/bgp_automacao.html`
**Fabricantes suportados:** Mikrotik (RouterOS 6 e 7), Huawei (VRP), Cisco/Datacom (IOS-like), Juniper (Junos)
**Status:** ✅ Produção
**Data de implementação:** 2026-07-31

---

## Visão Geral

A partir do backup mais recente de cada `Acesso`, o sistema monta uma estrutura das sessões BGP,
prefix-lists e route-policies/route-maps aplicadas, **simula de verdade** quais prefixos cada sessão
está anunciando (percorrendo os termos da policy na ordem certa, respeitando permit/deny e faixa de
tamanho de máscara) e oferece, por sessão/prefixo, botões para:

- **Ativar/desativar** uma sessão BGP específica.
- **Adicionar prepend** a um anúncio já existente.
- **Parar de anunciar** um prefixo específico.

Cada ação primeiro devolve os comandos reais que seriam enviados (preview, sem tocar em nada) e só
executa de verdade num segundo clique de confirmação. Restrito a staff/superuser — é engenharia de
rede em produção, não uma ferramenta de portal de cliente.

**Limitação importante:** a simulação de anúncios avalia a lógica de match/permit/deny/prefix-length
contra os prefixos que aparecem nas **próprias prefix-lists do backup** — não há acesso à RIB viva do
equipamento (isso exigiria uma consulta live, tipo `display bgp routing-table`/`/routing bgp
advertisements`, feature diferente e mais pesada). É fiel à configuração, não ao estado de rotas
anunciadas neste exato segundo.

---

## Arquitetura

```
Backup mais recente (BackupLog)
    │
    ▼
clientes/backup_parser.py::parse_backup()   ← parsers já existentes (Agent NOC), estendidos aqui
    │  retorna: bgp[] (+ nome/habilitada/policy_in/policy_out por peer),
    │           prefix_lists{}, policies{}, networks[]
    ▼
clientes/bgp_matcher.py::simular_anuncios() ← 1 simulador só, vendor-agnóstico
    │  retorna: anuncios{sessao: [{prefixo,permitido,prepend}]}
    ▼
BgpSnapshot.dados (JSONField)               ← gravado por clientes.tasks.atualizar_snapshots_bgp
    │  (rotina noturna, 02:45 — depois do backup e do snapshot de conhecimento)
    ▼
clientes/bgp_views.py + bgp_automacao.html  ← UI: tabela de sessões + anúncios, botões de ação
    │
    ▼
clientes/bgp_actions.py                     ← gera comandos por fabricante, executa via Netmiko
    (reaproveita clientes/script_views.py::_conectar_script/_fechar_tunel)
```

### Por que estender `backup_parser.py` em vez de escrever um parser novo

`clientes/backup_parser.py` já existia — usado por `gerar_snapshots_conhecimento` (Celery, a cada 4
dias) para alimentar a base de conhecimento do Agent NOC — com um parser por fabricante
(`parse_mikrotik`, `parse_huawei`, `parse_cisco`, `parse_juniper`) que já localizava blocos BGP e
extraía peers básicos (IP, AS remoto, descrição). Faltava: estado habilitado/desabilitado, nome/
identificador usado nos comandos de ação, e toda a estrutura de prefix-list/route-policy. Em vez de
duplicar a detecção de vendor e os blocos de regex já testados em produção, cada `parse_*` ganhou
campos novos no `bgp[]` (`nome`, `habilitada`, `policy_in`, `policy_out`) e duas chaves novas no
dict de retorno (`prefix_lists`, `policies`) — **sem quebrar** o contrato usado por
`gerar_snapshots_conhecimento` (que só lê `peer_ip`/`peer_as`/`descricao`/`as_local`/`equipamento`).

### Representação canônica (`prefix_lists` / `policies`)

Os 4 parsers traduzem a sintaxe nativa de cada fabricante pro MESMO formato, o que permite escrever
o simulador de match **uma única vez** (`clientes/bgp_matcher.py`) em vez de reimplementar a lógica
de avaliação de policy 4 vezes:

```python
prefix_lists = {"NOME": [{"acao": "permit"|"deny", "prefixo": "X.X.X.X/Y",
                           "len_min": int|None, "len_max": int|None, ...}, ...]}
policies     = {"NOME": [{"ordem": int, "prefix_lists": ["NOME_PL", ...],  # vazio = match-all
                           "acao": "accept"|"reject", "prepend": int,
                           "extra": {...}  # dados vendor-specific pra reconstruir o comando depois
                          }, ...]}
```

`clientes/bgp_matcher.py::simular_anuncios(prefix_lists, policies, policy_nome)` monta o conjunto de
prefixos candidatos (união de tudo que aparece em `prefix_lists`, incluindo o bucket sintético
`__networks__` — ver abaixo) e, pra cada prefixo, percorre os termos da policy em ordem (`ordem`
crescente) aplicando o **primeiro** que bater — mesma semântica sequencial dos 4 fabricantes (first
match wins, deny implícito no final).

### Termos "não suportados" — melhor descartar do que simular errado

Alguns termos de policy usam critérios de match que não dá pra avaliar estaticamente a partir de um
prefixo isolado (`if-match community-filter` no Huawei, `match as-path`/`match community` no
Cisco). Em vez de tratar esses termos como catch-all (o que faria a policy inteira "rejeitar tudo"
incorretamente assim que um desses aparecesse antes do termo certo), eles são **descartados** da
lista de `policies` — a simulação passa a refletir só o que ela sabe avaliar com certeza. Documentado
inline em `backup_parser.py` (`extra.nao_suportado`).

### Peculiaridades por fabricante no parser

- **Mikrotik**: o `/export terse` do RouterOS quebra linhas longas em ~80 colunas com continuação
  sem prefixo `/` — `_juntar_continuacao_mikrotik()` rejunta antes de qualquer regex. A versão
  (6 ou 7) vem do comentário `# ... by RouterOS X.Y.Z` no topo do arquivo — decide se o parser usa
  `peer`/`network`/`filter chain` (v6) ou `connection`/`filter rule` (v7, cuja regra é um mini-script
  `if (dst == X && dst-len...) { accept|reject }`, parseado à parte). Redes anunciadas: `/routing bgp
  network` (v6) ou address-list de firewall referenciada por `.network=` na connection (v7) — viram
  o bucket `prefix_lists['__networks__']`.
- **Huawei**: duas formas de "desativado" coexistem no mesmo bloco `bgp <ASN>` — `peer X ignore`
  (sessão inteira) e `undo peer X enable` (só a address-family, dentro de `ipv4-family unicast`/
  `ipv6-family unicast`) — qualquer um dos dois já conta como desabilitada.
- **Cisco/Datacom**: `neighbor X shutdown` desativa a sessão. Datacom não tem nenhuma evidência real
  de BGP em backup de produção até agora — `atualizar_snapshots_bgp` reaproveita o parser Cisco pra
  qualquer `Acesso` detectado como `datacom` (mesmo tratamento já usado em `DEVICE_TYPES` do Netmiko).
- **Juniper**: não existe uma keyword `disable` na hierarquia `protocols bgp` — o mecanismo real é
  `deactivate` (`protocols bgp group NOME` inteiro **ou** `protocols bgp group NOME neighbor X`
  específico; os dois efeitos se acumulam — um neighbor está desabilitado se ELE ou o GRUPO dele
  estiver `deactivate`d). O mais comum nos backups reais é `from route-filter X/Y exact|orlonger|upto`
  embutido direto no term (em vez de prefix-list nomeada) — vira uma prefix-list sintética de uma
  entrada só. `deactivate`/`activate` também se aplica a sub-caminhos de um term (ex: só o
  `then as-path-prepend`), tratado à parte de "o term inteiro está desativado".

---

## Modelos (`clientes/models.py`)

### `BgpSnapshot`

Uma linha por `Acesso` (`OneToOneField`) — todo o estado (sessões, prefix-lists, policies, anúncios
simulados) fica num único `JSONField` (`dados`), não normalizado em várias tabelas: é sempre lido/
escrito como uma unidade só, uma vez por dia, nunca há necessidade de JOIN entre sessão/prefix-list/
policy. Mesmo padrão já usado em `ScriptCRM.parametros`/`AgentLog.tool_input`.

| Campo | Descrição |
|---|---|
| `acesso` | FK 1:1 → `Acesso` |
| `vendor` | `mikrotik`/`huawei`/`cisco`/`juniper` |
| `backup_log` | FK → `BackupLog` que originou o snapshot |
| `dados` | `{"sessoes":[...], "prefix_lists":{...}, "policies":{...}, "anuncios":{...}}` |
| `erro` | Se o parser/simulação falhar num backup novo, o motivo fica aqui **sem apagar** `dados` do snapshot anterior — a tela continua mostrando o último estado válido conhecido |

### `AcaoBgp`

Auditoria — uma linha por ação disparada pela tela (`ativar_sessao`/`desativar_sessao`/`prepend`/
`parar_anuncio`): `acesso`, `usuario`, `alvo` (peer ou prefixo), `comandos` (texto real enviado),
`output`, `status` (`sucesso`/`erro`), `executado_em`.

**Migration:** `0096_acaobgp_bgpsnapshot.py`.

---

## Geração de comandos e execução (`clientes/bgp_actions.py`)

Cada fabricante usa o mecanismo **mais natural e reversível da própria CLI** — não força um padrão
único onde a sintaxe não suporta de forma limpa.

### Ativar / desativar sessão

| Fabricante | Comando |
|---|---|
| Mikrotik v6 | `/routing bgp peer {enable\|disable} [find name="NOME"]` |
| Mikrotik v7 | `/routing bgp connection {enable\|disable} [find name="NOME"]` |
| Huawei | `bgp ASN` + `{undo peer IP ignore \| peer IP ignore}` + `commit` |
| Cisco/Datacom | `router bgp ASN` + `{no neighbor IP shutdown \| neighbor IP shutdown}` |
| Juniper | `{activate\|deactivate} protocols bgp group GRUPO neighbor IP` + `commit` |

### Prepend (soma/subtrai do valor atual, reconstruído a partir do termo simulado)

| Fabricante | Comando |
|---|---|
| Mikrotik v6 | `/routing filter set [find chain="CHAIN" prefix="PREFIXO"] set-bgp-prepend=N` |
| Mikrotik v7 | reescreve a `rule=` inserindo/substituindo `set bgp-path-prepend=N;` — **best-effort, não confirmado em backup real** (todo prepend real visto em produção é RouterOS 6) |
| Huawei | `route-policy NOME permit node N` + `apply as-path ASN...ASN additive` + `commit` |
| Cisco/Datacom | `route-map NOME permit SEQ` + `set as-path prepend ASN...ASN` |
| Juniper | `set policy-options policy-statement NOME term TERM then as-path-prepend "ASN...ASN"` + `commit` |

### Parar de anunciar

| Fabricante | Comando | Observação |
|---|---|---|
| Juniper | `deactivate policy-options policy-statement NOME term TERM` + `commit` | Reversível (`activate`); padrão já visto ativo em produção |
| Mikrotik v6 | `/routing bgp network disable [...]` OU `/routing filter disable [...]` | Conforme a origem do anúncio (network object vs. regra de filtro) |
| Mikrotik v7 | `/ip firewall address-list disable [...]` | ⚠️ `.network=` pode ser compartilhado por mais de uma connection — mesma lista, mesmo efeito em todas |
| Huawei | `undo network IP MASCARA` + `commit` | Só suportado quando o anúncio vem de um `network` statement explícito; senão a ação recusa (`AcaoBgpNaoSuportada`) em vez de arriscar um edit de route-policy |
| Cisco/Datacom | `ip prefix-list PL seq SEQ_MENOR deny PREFIXO` | Insere um `deny` ANTES do `permit` existente (não edita a entrada original); se não houver seq livre abaixo, recusa e pede renumeração manual |

### Execução (`executar_acao_bgp`)

Reaproveita `clientes/script_views.py::_conectar_script`/`_fechar_tunel` (mesma conexão Netmiko com
resolução de túnel via `ProxyServer`/VPN já usada pelo Painel de Scripts) — só foi necessário
adicionar `'juniper': 'juniper_junos'` em `DEVICE_TYPES`. Por fabricante:

- **Mikrotik**: `send_command()` — um comando único, RouterOS não tem "modo configuração" separado.
- **Cisco/Datacom**: `send_config_set(comandos)` — mesmo padrão já usado por `executar_script`.
- **Huawei e Juniper**: `send_config_set(comandos, exit_config_mode=False)` seguido de `commit()`
  **explícito** — sem isso a mudança fica só na config candidata, nunca aplicada de verdade.
  **Bug real encontrado em produção (31/07/2026)**: a versão inicial só tratava isso pro Juniper;
  o driver Huawei `huawei_vrpv8` (usado por TODO equipamento Huawei deste projeto — ver
  `DEVICE_TYPES`) tem o **mesmo modelo de config candidata/commit** — sem o `commit()` explícito o
  prompt fica em `[*...]` (mudança pendente, nunca aplicada) mesmo com a conexão "funcionando" sem
  nenhum erro. Corrigido adicionando `'huawei'` a `_PRECISA_COMMIT`. Nenhum outro lugar do projeto
  chamava `commit()` antes desta feature.
  - `comandos_*` do Huawei e Juniper incluem `'commit'` como último item da lista só pro
    preview/auditoria mostrarem a ação completa — `executar_acao_bgp` filtra essa string antes de
    mandar pro `send_config_set` (senão o commit sairia duplicado: uma vez como linha de config
    comum, outra pela chamada real `conn.commit()`, que faz o handshake de confirmação/erro da
    config candidata, diferente de só mandar o texto "commit").

Toda `AcaoBgpNaoSuportada` (fabricante/situação sem comando seguro conhecido) é capturada na view e
devolvida como erro 422 — a UI mostra o motivo em vez de tentar um comando arriscado.

---

## Rotina noturna (`clientes/tasks.py::atualizar_snapshots_bgp`)

Agendada às **02:45** (`crm/celery.py`, depois do backup às 01h e do snapshot de conhecimento às
02:30). Pra cada `Acesso`: pega o `BackupLog` mais recente com `status='SUCESSO'`, roda
`parse_backup(conteudo, nome, vendor_hint=_detectar_vendor(acesso))` — reaproveitando
`_detectar_vendor` (funcao → modelo → conteúdo do backup) já usado por `detectar_modelos_via_backup`
— e, se achou peers BGP, roda `simular_anuncios` por sessão e grava em `BgpSnapshot`. Acessos sem
BGP no backup são ignorados silenciosamente (não é erro); falhas de parser/simulação ficam em
`BgpSnapshot.erro` sem derrubar a rotina nem apagar o snapshot anterior daquele Acesso.

**Validado em produção (primeira execução manual):** 393 acessos com backup no disco, 53 com BGP
identificado, 2 com erro (um peer configurado por hostname em vez de IP, um prefix-list com notação
de range `X-Y` em vez de CIDR — ambos casos reais, não bugs do parser).

---

## Frontend (`clientes/bgp_views.py`, `clientes/templates/bgp_automacao.html`)

| Endpoint | Descrição |
|---|---|
| `GET /clientes/bgp/<acesso_id>/` | Página (staff/superuser; `render` com `terminal_link_invalido.html` reaproveitado como tela de acesso negado) |
| `GET /clientes/bgp/<acesso_id>/dados/` | JSON do `BgpSnapshot.dados` + `vendor`/`gerado_em`/`erro`; 404 se não houver snapshot ainda |
| `POST /clientes/bgp/<acesso_id>/acao/` | `{tipo, alvo, params, preview}` — `preview=true` só monta e devolve os comandos (sem tocar no equipamento); `preview=false` executa de verdade e grava `AcaoBgp` |

Página dedicada (sem sidebar de hosts, no padrão de `terminal.html`): uma tabela de sessões
(clicável, expande a lista de prefixos anunciados simulados) com botão Ativar/Desativar por sessão,
e por prefixo anunciado, botões "+1 Prepend"/"Parar de anunciar". Todo botão abre um modal mostrando
os **comandos reais** (via `preview=true`) antes de um segundo clique confirmar a execução.

Ícone novo no card de cada `Acesso` em `listar.html` (`fa-diagram-project`, ao lado do de auditoria),
visível só pra staff e só quando `acesso.bgp_snapshot` existe (Django trata o `OneToOneField`
reverso inexistente como `AttributeError` — `{% if acesso.bgp_snapshot %}` não quebra o template
quando não há snapshot pra aquele Acesso).

---

**Última atualização:** 31/07/2026
