# Automação BGP — Documentação Técnica

**Arquivos principais:** `clientes/backup_parser.py`, `clientes/bgp_matcher.py`, `clientes/bgp_actions.py`,
`clientes/bgp_views.py`, `clientes/tasks.py` (`atualizar_snapshots_bgp`), `clientes/models.py`
(`BgpSnapshot`, `AcaoBgp`, `BgpCommunity`), `clientes/templates/bgp_automacao.html`
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
  `ipv6-family unicast`) — qualquer um dos dois já conta como desabilitada. `peer X fake-as N`
  (o roteador se apresenta com AS `N` só pra esse peer) vira `peer.fake_as`/`peer.prepend_as` —
  ver "Qual ASN é repetido" na seção de comandos, abaixo.
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

**Qual ASN é repetido — `fake-as` (adicionado em 2026-07-31):** por padrão, o prepend repete o AS
local da sessão (`sessao.as_local`). Exceção real confirmada em produção: quando o peer Huawei tem
`peer IP fake-as N` configurado (o roteador se apresenta com o AS `N` só pra esse peer, em vez do
AS real do `bgp <ASN>`), o AS_PATH que ESSE peer enxerga já usa o `fake-as` — prepender o AS real
nesse caso produziria um valor que não bate com o que o peer já vê. `parse_huawei` extrai
`peer.fake_as` e calcula `peer.prepend_as = fake_as or as_local`; `comandos_prepend` usa
`sessao.prepend_as` em vez de `sessao.as_local` (fallback automático pros outros 3 fabricantes, que
não têm esse campo). Not confirmado equivalente pra Cisco `neighbor ... local-as`/Juniper
`local-as` nos backups reais deste ambiente (o único `local-as` visto no dataset Juniper repete o
próprio AS do roteador, não é mascaramento de fato) — não implementado por falta de evidência real.

**Quantos prepends de uma vez — stepper na UI (adicionado em 2026-07-31):** cada linha de prefixo
anunciado tem um contador `−`/`+` (1 a 20) ao lado do botão "Prepend", pra adicionar mais de um de
uma vez sem precisar clicar "Prepend" repetidamente — `delta` no `POST .../acao/` já aceitava
qualquer inteiro desde o início, só a UI estava fixa em `delta:1`.

### Parar de anunciar

| Fabricante | Comando | Observação |
|---|---|---|
| Juniper | `deactivate policy-options policy-statement NOME term TERM` + `commit` | Reversível (`activate`); padrão já visto ativo em produção |
| Mikrotik v6 | `/routing bgp network disable [...]` OU `/routing filter disable [...]` | Conforme a origem do anúncio (network object vs. regra de filtro) |
| Mikrotik v7 | `/ip firewall address-list disable [...]` | ⚠️ `.network=` pode ser compartilhado por mais de uma connection — mesma lista, mesmo efeito em todas |
| Huawei | `undo ip ip-prefix LISTA index N` + `commit` (preferido) OU `undo network IP MASCARA` + `commit` (fallback) | Ver nota abaixo — corrigido em 2026-08-01 |
| Cisco/Datacom | `ip prefix-list PL seq SEQ_MENOR deny PREFIXO` | Insere um `deny` ANTES do `permit` existente (não edita a entrada original); se não houver seq livre abaixo, recusa e pede renumeração manual |

#### Huawei: por que `undo network` era um bug (corrigido em 2026-08-01)

A versão original só sabia remover a origem via `network IP MASCARA` (comando global do processo
BGP — desliga aquela rede pra **todas** as sessões que a originam, não só a sessão em questão).
Isso quebrou num caso real: `RP-UPSTREAM-MEGASNET-V4-OUT permit node 10` casava
`179.0.110.0/24` via `if-match ip-prefix PL-179.0.110.0/24`, mas a ação gerou
`undo network 179.0.110.0 255.255.255.0` — que teria efeito colateral em qualquer outra sessão que
também originasse essa rede.

A ordem correta (e agora implementada) é: primeiro procurar, dentro da export policy DESSA sessão
(via `_termo_e_entrada_responsaveis`, mesma função já usada por prepend/community), a entrada de
`ip ip-prefix` responsável pelo match — e remover só ela (`undo ip ip-prefix LISTA index N`).
Isso é escopado ao peer: só afeta o que essa sessão especificamente anuncia via essa entrada,
mesmo que a prefix-list tenha outras entradas (só o `index` alvo é removido) ou seja referenciada
por outro node/policy. `undo network` (global) só é usado como último recurso — quando o prefixo
não é controlado por nenhuma route-policy (ex: `network` statement sem filtro algum aplicado).

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
| `POST /clientes/bgp/<acesso_id>/acao/` | `{tipo, alvo, params, preview, comandos}` — `preview=true` só monta e devolve os comandos gerados automaticamente (sem tocar no equipamento); `preview=false` executa de verdade e grava `AcaoBgp`. Se o body trouxer `comandos` (lista de strings) nesse segundo caso, usa exatamente esses em vez de gerar de novo — ver "Edição do comando antes de confirmar" abaixo |

Página dedicada (sem sidebar de hosts, no padrão de `terminal.html`): uma tabela de sessões
(clicável, expande a lista de prefixos anunciados simulados) com botão Ativar/Desativar por sessão,
e por prefixo anunciado, botões "+1 Prepend"/"Parar de anunciar". Todo botão abre um modal mostrando
os **comandos reais** (via `preview=true`) antes de um segundo clique confirmar a execução.

### Edição do comando antes de confirmar (adicionado em 2026-07-31)

O textarea do modal de confirmação é **editável** — o texto inicial vem do `preview=true` (geração
automática), mas o staff pode ajustar antes de clicar em "Executar" (ex: trocar o ASN usado no
prepend por um diferente do AS local da sessão, corrigir um nome). No confirm (`preview=false`), o
frontend manda o conteúdo atual do textarea (`comandos: [...]`, uma linha por elemento) — a view
usa exatamente esse texto em vez de rechamar `_montar_comandos`. Se o body não trouxer `comandos`
(ex: chamada direta na API sem passar pelo modal), cai de volta pra geração automática — comportamento
inalterado. Limite de sanidade: até 30 linhas, 500 caracteres cada, senão a view recusa com 400.

Ícone novo no card de cada `Acesso` em `listar.html` (`fa-diagram-project`, ao lado do de auditoria),
visível só pra staff e só quando `acesso.bgp_snapshot` existe (Django trata o `OneToOneField`
reverso inexistente como `AttributeError` — `{% if acesso.bgp_snapshot %}` não quebra o template
quando não há snapshot pra aquele Acesso).

---

## Atualizar snapshot sob demanda (adicionado em 2026-08-01)

`clientes/tasks.py::atualizar_snapshots_bgp` foi refatorada — o trabalho de UM Acesso (ler backup
mais recente, `parse_backup`, `simular_anuncios`, gravar `BgpSnapshot`) virou uma função reutilizável
`_atualizar_snapshot_bgp_de_acesso(acesso)`, que devolve `(resultado, detalhe)` (`'ok'`,
`'sem_backup'`, `'fabricante_nao_suportado'`, `'erro_leitura'`, `'erro_parser'`, `'sem_bgp'`,
`'erro_simulacao'`). A task noturna passou a chamar essa função em loop (mesmo comportamento de
antes, só refatorado); e um botão novo **"🔄 Atualizar agora"** no cabeçalho da tela
(`POST /clientes/bgp/<acesso_id>/atualizar/`, `bgp_views.bgp_atualizar_snapshot`) chama a mesma
função **síncrono**, pra um único host, sem esperar a rotina das 02:45 — não precisa de Celery
porque só lê um arquivo já salvo em disco e roda regex, não conecta em nada.

---

## Communities por sessão — cadastro + "usar community" (adicionado em 2026-08-01)

Cada upstream/operadora costuma publicar sua própria lista de communities aceitas (blackhole,
no-export seletivo, tag de prioridade — confirmado em produção: `blackhole-fortetelecom` no Juniper
= `61663:666`, `apply community 65001:100 additive` no Huawei). Modelo novo `BgpCommunity`
(`clientes/models.py`) guarda isso por **sessão** (`acesso` + `sessao_nome` + `label` + `valor`,
`unique_together` nos três primeiros) — cadastro manual, não vem do backup.

### Comando gerado (`clientes/bgp_actions.py::comandos_aplicar_community`)

Mesmo mecanismo de "achar o termo responsável" já usado no prepend (`_termo_e_entrada_responsaveis`).

| Fabricante | Comando | Confiança |
|---|---|---|
| Mikrotik v6 | `/routing filter set [...] append-bgp-communities={valor}` (aditivo, não `set-` — não arrisca sobrescrever outros atributos já setados na mesma regra) | ✅ confirmado |
| Mikrotik v7 | `AcaoBgpNaoSuportada` | ❌ sem evidência real de **set** de community no dialeto de script v7 (só *match*, `if (bgp-communities includes ...)`) |
| Huawei | `route-policy NOME permit node N` + `apply community {valor} additive` + `commit` | ✅ confirmado, muito consistente |
| Cisco/Datacom | `route-map NOME permit SEQ` + `set community {valor} additive` | ⚠️ best-effort — **zero ocorrências reais** de `set community` em 38 backups Cisco deste ambiente (só existe `neighbor X send-community both`, que é outra coisa) |
| Juniper | `set policy-options community {nome} members {valor}` + `set policy-options policy-statement NOME term T then community add {nome}` + `commit` | ✅ confirmado — Junos **sempre** referencia community por nome, nunca valor literal inline. `{nome}` vem do `label` cadastrado (slugificado: minúsculo, não-`[a-z0-9]` vira hífen) — reemitir o mesmo `members` é idempotente, não precisa checar se já existe no equipamento |

### Endpoints (`clientes/bgp_views.py`)

| Endpoint | Descrição |
|---|---|
| `GET /clientes/bgp/<id>/communities/[?sessao=NOME]` | Sem `?sessao=`, devolve TODAS agrupadas por sessão (`{"communities": {"<sessao_nome>": [...]}}`) — a UI carrega uma vez só no load da página em vez de 1 fetch por sessão |
| `POST /clientes/bgp/<id>/communities/criar/` | `{sessao, label, valor}` |
| `POST /clientes/bgp/<id>/communities/<community_id>/deletar/` | remove |

`_montar_comandos` ganhou `tipo == 'community'` (`params: {sessao, valor, label}`).

### Frontend

Painel colapsável "📡 Communities desta sessão" dentro de cada card de sessão (lista + form de
adicionar). Por prefixo anunciado, um `<select>` "Usar community…" (desabilitado se a sessão não
tiver nenhuma cadastrada) que dispara o mesmo modal de preview/edição já existente.

---

## Anunciar prefixo novo — varredura de prefix-lists (adicionado em 2026-08-01)

Até aqui só dava pra mexer em anúncios que **já existiam** (prepend/parar). Esta extensão permite
anunciar um prefixo **novo**, achando automaticamente qual prefix-list já cadastrada (e já
referenciada por um termo `accept` da export policy da sessão) faz sentido receber esse prefixo —
sem precisar mexer na route-policy/term em si.

### `clientes/bgp_matcher.py::escanear_prefix_lists(prefix_lists, policies, policy_nome, prefixo_novo=None)`

Reúne as prefix-lists referenciadas por termos `accept` de `policy_nome` (candidatas) — essa lista
não depende de `prefixo_novo`, é só "quais prefix-lists esta sessão usa pra anunciar". Por isso
`prefixo_novo` é opcional (adicionado em 2026-08-01): sem ele devolve só as candidatas (é o que a UI
usa pra popular o modal assim que abre, antes do usuário escolher qualquer coisa); com ele, roda
também a MESMA lógica de match do `simular_anuncios` (`entrada_que_bate`) pra conferir se
`prefixo_novo` já bate em alguma — se sim, `ja_coberto=True` e não tem nada a fazer (já seria
anunciado automaticamente se a rota existisse na tabela). Devolve também uma amostra de até 3
prefixos de cada candidata, pra UI mostrar algo reconhecível em vez de só o nome da lista.

### `clientes/bgp_actions.py::comandos_novo_anuncio(vendor, dados, nome_sessao, prefixo_novo, lista_escolhida)`

| Fabricante | Comando |
|---|---|
| Huawei | `ip ip-prefix {lista} index {próximo múltiplo de 10 livre} permit {ip} {tamanho}` |
| Cisco/Datacom | `ip prefix-list {lista} seq {próximo múltiplo de 5 livre} permit {prefixo}` |
| Juniper | `set policy-options prefix-list {lista} {prefixo}` + `commit` — recusa (`AcaoBgpNaoSuportada`) se a "lista" escolhida for um `route-filter` sintético embutido direto no term (nome contém `#`) em vez de uma prefix-list nomeada de verdade, porque não dá pra adicionar entrada nesse tipo de objeto |
| Mikrotik v6 | **não usa `lista_escolhida`** — Mikrotik não tem objeto de prefix-list separado (cada "prefix-list" do nosso parser é sintética, 1:1 com uma regra de filter). Insere uma regra `accept` nova direto na chain de export da sessão (`sessao.policy_out`), com `place-before=[find chain=... action=discard]` pra garantir que fica ANTES do catch-all final — senão nunca seria alcançada |
| Mikrotik v7 | `AcaoBgpNaoSuportada` — mesma razão do community v7 |

"Próximo índice/seq livre" = maior `index`/`seq` já usado nas entradas daquela lista + o incremento
padrão do fabricante (10 Huawei, 5 Cisco) — não tenta preencher buracos. Huawei precisou ganhar
`index` nas entradas de `ip ip-prefix` em `backup_parser.py::parse_huawei` (mesmo campo que o Cisco
já tinha como `seq`).

### Endpoint

`POST /clientes/bgp/<id>/escanear-prefixo/` — `{sessao, prefixo?}` (`prefixo` opcional desde
2026-08-01), devolve `{ja_coberto, lista_cobertura, candidatas: [{nome, amostra}], vendor}`. Leitura
pura sobre o snapshot já em memória, não toca em nada. `_montar_comandos` ganhou
`tipo == 'novo_anuncio'` (`params: {sessao, lista}`).

### Frontend

Botão "➕ Anunciar prefixo novo" no cabeçalho de cada sessão abre um modal dedicado que já chama o
endpoint de varredura **sem prefixo** assim que abre, listando de cara as prefix-lists candidatas
(nome + amostra de prefixos, pra reconhecer sem decorar nome de lista) — o usuário escolhe a lista
primeiro e só digita o prefixo novo depois, num campo ao lado dela (não precisa digitar nada só pra
ver quais listas existem). Ao confirmar, faz uma segunda checagem (agora com o prefixo) pra avisar
se ele já cai em alguma candidata (`ja_coberto`) antes de abrir o modal de preview/edição de sempre.
Mikrotik é exceção: como não tem prefix-list separada, mostra direto um campo pra digitar o prefixo
(insere regra na chain de export, sem conceito de "lista" pra escolher).

UX original (adicionada em 2026-08-01, corrigida no mesmo dia): exigia digitar o prefixo antes de
ver qualquer coisa (campo de texto + botão "Verificar"). Trocado porque forçava o operador a digitar
um prefixo "no escuro" pra só então descobrir quais prefix-lists existiam — mais natural escolher a
lista primeiro (contexto conhecido do upstream) e digitar o prefixo depois.

---

**Validado em 2026-08-01** contra os 53 `BgpSnapshot` reais de produção existentes (todos os 4
fabricantes), rodando os 4 endpoints novos (`atualizar`, `community` preview, `escanear-prefixo`,
`novo_anuncio` preview) em cada um — sem nenhum erro inesperado. Nenhuma ação real (`preview=false`)
executada contra equipamento durante a validação.

---

**Última atualização:** 01/08/2026
