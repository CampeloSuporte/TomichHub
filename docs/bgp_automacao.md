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
| Huawei | `route-policy NOME deny node N` + `commit` (preferido) OU `undo network IP MASCARA` + `commit` (fallback) | Ver nota abaixo — corrigido em 2026-08-01 |
| Cisco/Datacom | `route-map NOME deny SEQ_MENOR` + `match ip[v6] address prefix-list PL` | Ver nota abaixo — corrigido em 2026-08-01. Se não houver seq livre abaixo (ou o seq alvo já estiver ocupado no route-map), recusa e pede renumeração manual |

#### Huawei/Cisco: por que editar a prefix-list era um bug (corrigido em 2026-08-01)

A versão original só sabia remover a origem via `network IP MASCARA` (comando global do processo
BGP — desliga aquela rede pra **todas** as sessões que a originam, não só a sessão em questão).
Isso quebrou num caso real: `RP-UPSTREAM-MEGASNET-V4-OUT permit node 10` casava
`179.0.110.0/24` via `if-match ip-prefix PL-179.0.110.0/24`, mas a ação gerou
`undo network 179.0.110.0 255.255.255.0` — que teria efeito colateral em qualquer outra sessão que
também originasse essa rede.

Uma primeira correção tentou remover a entrada de `ip ip-prefix` responsável pelo match
(`undo ip ip-prefix LISTA index N`) — também errado: a prefix-list é um objeto nomeado à parte que
pode estar referenciada por OUTRO node/route-policy (de outra sessão, ou até de outro node da mesma
policy), então editá-la vazaria o efeito pra fora da sessão em questão, exatamente o mesmo problema
do `undo network`, só que num escopo menor.

A forma correta (a que o node já usa desde sempre pra decidir permit/deny) é trocar o **modo do
próprio node** dentro do route-policy de export DESSA sessão, mantendo o mesmo número de node — o
`if-match`/`apply` continuam intactos, só o `permit`/`deny` muda:

```
route-policy RP-UPSTREAM-MEGASNET-V4-OUT permit node 10        route-policy RP-UPSTREAM-MEGASNET-V4-OUT deny node 10
 if-match ip-prefix PL-179.0.110.0/24              -->           if-match ip-prefix PL-179.0.110.0/24
 apply as-path 272418 additive                                   apply as-path 272418 additive
#                                                                #
```

Isso é escopado ao peer porque cada sessão tem seu próprio route-policy de export — nenhum outro
node, policy ou sessão é tocado, e a prefix-list em si nunca é editada. `undo network` (global)
continua só como último recurso, quando o prefixo não é controlado por nenhuma route-policy (ex:
`network` statement sem filtro algum aplicado).

**O mesmo problema existia no Cisco/Datacom** e foi confirmado em backup real, não só em teoria: a
versão original inseria um `deny` direto na prefix-list (`ip prefix-list PL seq N deny PREFIXO`).
Só que prefix-lists de prefixo próprio (`PL-ORIGIN-*`, `PL-MY-PREFIX-*`) são tipicamente
**reaproveitadas por vários route-maps/peers ao mesmo tempo** — ex: `PL-ORIGIN-45.71.73.0_24` (backup
real, `cliente_8/acesso_348`) está referenciada em `RM-PEER-1TELECOM-V4-OUT`,
`RM-PEER-LOCALLINK-V4-OUT` e `RM-PEER-LOCALLINK-BACKUP-V4-OUT` simultaneamente (mesmo prefixo próprio
anunciado a três upstreams diferentes). Editar a lista pararia de anunciar nos três peers, não só no
selecionado. Corrigido pro mesmo padrão do Huawei: insere um `deny` novo dentro do **route-map de
export DESSA sessão** (mesma prefix-list como critério de match, mas o `deny` só existe dentro desse
route-map específico), num seq menor que a entrada `permit` existente:

```
route-map RM-PEER-1TELECOM-V4-OUT deny 9
 match ip address prefix-list PL-ORIGIN-45.71.73.0_24
```

Os outros route-maps (`RM-PEER-LOCALLINK-V4-OUT`, `RM-PEER-LOCALLINK-BACKUP-V4-OUT`) que também
casam com essa prefix-list continuam intocados — só o peer selecionado para de anunciar. Recusa
(`AcaoBgpNaoSuportada`) se o seq calculado (`seq_do_route-map_existente - 1`) já estiver ocupado por
outra entrada do mesmo route-map, ou se não houver seq livre abaixo (`<= 1`).

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

### Cisco/Datacom sempre salva na NVRAM — `end`+`write` (adicionado em 2026-08-04)

`clientes/bgp_views.py::_montar_comandos` acrescenta `'end'` e `'write'` no final de QUALQUER ação
Cisco/Datacom (ativar/desativar, prepend, parar de anunciar, community, anunciar prefixo novo, criar
sessão — todas, pedido explícito do usuário) antes de devolver os comandos, tanto no preview quanto na
execução real. Motivo: Cisco/Datacom aplica direto no running-config (sem candidate-config/commit como
Huawei/Juniper) — sem salvar, a mudança se perde no próximo reload do equipamento. `end` garante que
`write` rode em modo EXEC privilegiado mesmo quando os comandos gerados terminam aninhados (ex: dentro
de `address-family`, como em `criar_sessao`). As duas linhas aparecem no textarea editável do modal de
confirmação como qualquer outro comando — o operador pode apagá-las antes de confirmar se não quiser
salvar dessa vez (mesma flexibilidade de "editar antes de confirmar" já usada pelo resto da
automação). Huawei/Juniper (que já salvam via `commit()`) e Mikrotik (sem "modo configuração") não são
afetados.

### Modo trial — commit temporário com rollback automático (adicionado em 2026-08-02)

Todo modal de confirmação ganhou **dois botões de execução** em vez de um: "▶ Executar em modo
trial" e "▶ Executar sem trial". Trial troca o commit final pelo mecanismo de **commit temporário**
nativo do fabricante — a mudança fica ativa só por um tempo (campo numérico no modal, em segundos,
default 60) e **reverte sozinha** se ninguém confirmar depois. Pensado pra testar o efeito de uma
mudança arriscada (ex: desativar uma sessão BGP upstream) com uma rede de segurança: se algo quebrar,
o próprio equipamento desfaz sem precisar de intervenção manual.

| Fabricante | Comando de trial | Suportado? |
|---|---|---|
| Huawei | `commit trial N` (`N` em segundos, 5–65534) no lugar de `commit` | ✅ |
| Juniper | `commit confirmed N` (`N` em **minutos** — `trial_segundos` é convertido, arredondado pra cima, mínimo 1) no lugar de `commit` | ✅ |
| Cisco/Datacom | — | ❌ recusado (`AcaoBgpNaoSuportada`) |
| Mikrotik | — | ❌ recusado (`AcaoBgpNaoSuportada`) |

**Por que só Huawei e Juniper**: os dois têm modelo de config candidata + commit explícito (já
reaproveitado por `_PRECISA_COMMIT`), e o commit temporário opera sobre a MESMA config candidata —
risco contido à sessão/policy sendo editada. Cisco/Datacom (IOS clássico) não tem candidate-config:
comandos aplicam na hora, direto no running-config. O único jeito de conseguir um rollback
temporizado ali seria agendar `reload in N` (reagenda um **reboot do equipamento inteiro** se
ninguém confirmar com `reload cancel`) — decidido com o usuário (`AskUserQuestion`) que o risco é
desproporcional ao benefício e **não implementar** trial pra esses dois. Mikrotik só tem "safe mode"
(reverte no *disconnect* da sessão, não por tempo) — incompatível com o modelo
conecta→executa→desconecta desta automação (cada ação abre e fecha uma conexão nova).

`clientes/bgp_actions.py::validar_trial_suportado(vendor)` recusa cedo (antes de conectar no
equipamento) quando o vendor não suporta; `_comando_commit_trial(vendor, trial_segundos)` monta o
comando certo. `executar_acao_bgp(acesso, vendor, comandos, trial=False, trial_segundos=60)` ganhou
os dois parâmetros novos — quando `trial=True`, chama `conn.send_command(comando_trial)` no lugar de
`conn.commit()`.

**Importante**: esta automação ainda não tem uma ação de "confirmar" separada — trial serve pra
testar com segurança sabendo que desfaz sozinho, não pra aplicar permanentemente em duas etapas. Por
isso, quando `trial=True`, `bgp_views.py::bgp_executar_acao` **não chama**
`aplicar_efeito_localmente` (a atualização otimista do painel) — marcar o painel como se a mudança
fosse permanente seria enganoso, já que ela reverte sozinha e nem esta automação nem o resto do CRM
sabem exatamente quando isso acontece no equipamento. O botão "Executar sem trial" continua com o
comportamento de sempre (commit normal + atualização otimista do painel).

Validado com um teste de regressão comparando `trial=True`/`trial=False` a partir do MESMO estado
original em 89 combinações reais (sessão × prefixo anunciado, todos os 4 fabricantes) — zero
discrepâncias entre aceitar/recusar a ação nos dois modos, e confirmado que vendors sem suporte
recusam antes de tocar no equipamento.

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
`'sem_novidade'`, `'sem_backup'`, `'fabricante_nao_suportado'`, `'erro_leitura'`, `'erro_parser'`,
`'sem_bgp'`, `'erro_simulacao'`). A task noturna passou a chamar essa função em loop (mesmo
comportamento de antes, só refatorado); e um botão novo **"🔄 Atualizar agora"** no cabeçalho da tela
(`POST /clientes/bgp/<acesso_id>/atualizar/`, `bgp_views.bgp_atualizar_snapshot`) chama a mesma
função **síncrono**, pra um único host, sem esperar a rotina das 02:45 — não precisa de Celery
porque só lê um arquivo já salvo em disco e roda regex, não conecta em nada.

#### `'sem_novidade'` — proteção contra reverter a atualização otimista (adicionado em 2026-08-01)

Regressão real pega em produção: como "Atualizar agora" relê o backup mais recente já salvo em
disco, se nenhum backup NOVO foi tirado desde uma ação real (o backup ainda não capturou a mudança
feita no equipamento), reprocessar esse mesmo backup reescrevia `dados` do zero — apagando a
atualização otimista de `aplicar_efeito_localmente` (ver seção abaixo) e voltando o painel pro
estado de ANTES da ação. Reproduzido com um caso real: `parar_anuncio` bem-sucedido, seguido de
"Atualizar agora" alguns minutos depois — reverteu, porque o backup em disco ainda era o mesmo de
antes da ação.

Corrigido comparando o `backup_log_id` do `BgpSnapshot` atual com o do backup mais recente
encontrado: se forem o MESMO (e o snapshot não estiver em erro), a função não reprocessa nada e
devolve `'sem_novidade'` — preserva `dados` como está, incluindo qualquer patch otimista. Isso vale
tanto pro botão quanto pra rotina noturna (mesma função, mesmo risco de reverter um patch otimista
recente se o equipamento não tiver sido rebackupeado ainda). `bgp_atualizar_snapshot` trata isso como
sucesso (não erro); o frontend só mostra um tooltip discreto no badge, sem alterar o fluxo normal.

#### Regressão do próprio fix acima: bloqueava refresh legítimo, não só o indevido (corrigido em 2026-08-03)

A condição original só olhava `backup_log_id == backup.id` — **sem nenhuma noção de "existe um patch
pra proteger"**. Isso quebrou o caso comum: um snapshot antigo (gerado ANTES de alguma melhoria no
parser/matcher, ex: o campo `interface` por sessão, adicionado depois) nunca conseguia se atualizar
sozinho, porque o backup em disco quase nunca muda de um dia pro outro — `'sem_novidade'` bloqueava
pra sempre, mesmo sem nenhum patch otimista real em jogo.

**Reportado com caso real**: sessões BGP de alguns clientes (G5, Green Telecom) apareciam com
`interface: null` (sem botão "Ver tráfego") e poucos/nenhum prefixo simulado como anunciado — não
por bug no parser ou no matcher, mas porque o snapshot deles foi gerado em `2026-08-01`, ANTES do
campo `interface` (e de correções no matcher) existirem no código, e nunca mais foi reprocessado
desde então (backup em disco idêntico há dias). Rodando `identificar_interface`/`simular_anuncios`
manualmente contra os MESMOS dados armazenados, tudo funcionava corretamente — a única coisa errada
era o snapshot nunca ter sido atualizado com o código atual.

`BgpSnapshot` ganhou o campo `patch_local_pendente` (bool, migration `0099`): setado `True` só quando
`aplicar_efeito_localmente` de fato muta `dados` sem um backup novo por trás; voltado `False` sempre
que um reparse de verdade acontece (bem-sucedido, com ou sem backup novo). A condição de
`'sem_novidade'` passou a exigir os dois: `backup_log_id == backup.id` **E**
`patch_local_pendente`. Sem um patch pendente pra proteger, reprocessar o mesmo backup agora é
sempre permitido — é assim que um snapshot antigo se atualiza com o código atual sem precisar
esperar um backup novo do equipamento.

Corrigido com um backfill único rodado manualmente contra todos os 55 `BgpSnapshot` reais (nenhum
com `patch_local_pendente` real pendente) — 53 reprocessados com sucesso (refletindo o parser/
matcher atual), 2 com erro de simulação pré-existente e já conhecido (não relacionado: um peer
configurado por hostname em vez de IP, um prefix-list com notação de range `X-Y` em vez de CIDR).
Também validado que a proteção original continua funcionando: aplicar um patch otimista de teste,
marcar `patch_local_pendente=True` e rodar "atualizar" de novo devolve `'sem_novidade'` preservando o
patch, exatamente como antes.

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

## Anunciar prefixo novo — anexar prefix-list existente via node/termo novo (adicionado em 2026-08-01, redesenhado no mesmo dia)

Até aqui só dava pra mexer em anúncios que **já existiam** (prepend/parar). Esta extensão permite
fazer uma sessão passar a anunciar uma prefix-list que **já existe** no equipamento (de outra sessão,
ou cadastrada por outro motivo) mas que essa sessão específica ainda não anuncia.

### Duas tentativas de design — por que a primeira era outro caso do mesmo bug

A primeira versão pedia o **prefixo novo** digitado, escolhia uma prefix-list já usada pela sessão
como candidata, e **adicionava uma entrada nova** nela (`ip ip-prefix LISTA index N permit ...` no
Huawei, por exemplo). Reportado como errado pelo mesmo motivo já corrigido em "parar de anunciar":
prefix-lists são objetos nomeados **compartilháveis** — adicionar uma entrada numa lista que também é
referenciada pelo route-map/route-policy de OUTRA sessão faria aquele prefixo passar a ser anunciado
por essa outra sessão também, não só a selecionada.

A forma correta: a prefix-list escolhida **nunca é editada**. Em vez disso, a ação cria um
**node/termo/entrada de route-map NOVO**, exclusivo da export policy DESSA sessão, que só faz
`if-match`/`match` na prefix-list já existente — mesmo princípio do fix de "parar de anunciar"
(mexer só no objeto escopado à sessão, nunca no objeto compartilhado).

### `clientes/bgp_matcher.py::listar_prefix_lists(prefix_lists, policies, policy_nome)`

Lista **todas** as prefix-lists nomeadas conhecidas no snapshot do equipamento inteiro — não só as
já usadas por essa sessão — com uma amostra de até 3 prefixos de cada, pra UI mostrar algo
reconhecível sem decorar nome de lista. Marca `ja_anunciando: True` nas que essa sessão já anuncia
(via algum termo `accept` existente), pra UI não oferecer redundante. Exclui prefix-lists sintéticas
(nome com `#` — route-filter/regra embutida direto num term/chain, Mikrotik/Juniper) e a chave
interna `__networks__` (união de `network` statements pra simulação, Huawei/Cisco — não é um objeto
real referenciável).

### `clientes/bgp_actions.py::comandos_novo_anuncio(vendor, dados, nome_sessao, lista_escolhida=None, prefixo_novo=None)`

`lista_escolhida` é uma das `candidatas` de `listar_prefix_lists`. `prefixo_novo` só é usado pelo
Mikrotik (ver linha própria abaixo); os outros fabricantes o ignoram.

| Fabricante | Comando |
|---|---|
| Huawei | `route-policy NOME permit node N` + `if-match ip-prefix {lista}` + `commit` — `N` = próximo node livre (múltiplo de 10) **antes** de qualquer node catch-all existente (sem `if-match`, geralmente o `deny node 2000` final) |
| Cisco/Datacom | `route-map NOME permit N` + `match ip[v6] address prefix-list {lista}` — `N` = próximo seq livre (múltiplo de 10) antes de um eventual catch-all explícito; route-maps Cisco costumam ter deny implícito no final, então normalmente não há restrição |
| Juniper | `set policy-options policy-statement NOME term T from prefix-list {lista}` + `then accept` + `insert ... term T before term CATCHALL` (se existir um term catch-all `then reject` sem `from`) + `commit` — Junos avalia terms pela ORDEM DE DEFINIÇÃO no arquivo, não pelo nome/número, então um `set` novo entraria no fim por padrão (depois do catch-all, nunca alcançado); o `insert` garante a posição correta |
| Mikrotik v6 | **usa `prefixo_novo`, não `lista_escolhida`** — Mikrotik não tem objeto de prefix-list separado (cada "prefix-list" do nosso parser é sintética, 1:1 com uma regra de filter). Insere uma regra `accept` nova direto na chain de export da sessão, com `place-before=[find chain=... action=discard]` pra garantir que fica ANTES do catch-all final |
| Mikrotik v7 | `AcaoBgpNaoSuportada` — mesma razão do community v7 |

Recusa (`AcaoBgpNaoSuportada`) se não sobrar node/seq/term livre antes do catch-all (pede
renumeração manual), ou se a `lista_escolhida` for sintética/interna (`#`/`__`).

### Endpoint

`POST /clientes/bgp/<id>/escanear-prefixo/` — `{sessao}`, devolve
`{candidatas: [{nome, amostra, ja_anunciando}], vendor}`. Leitura pura sobre o snapshot já em
memória, não toca em nada — **não pede prefixo** (redesenhado em 2026-08-01, era `{sessao, prefixo?}`
antes). `_montar_comandos` ganhou `tipo == 'novo_anuncio'` (`params: {sessao, lista}` pros 3
fabricantes com prefix-list nomeada, `params: {sessao, prefixo}` pro Mikrotik).

### Frontend

Botão "➕ Anunciar prefixo novo" no cabeçalho de cada sessão abre um modal que já lista **todas** as
prefix-lists do equipamento assim que abre (nome + amostra, marcando "já anunciada nesta sessão" nas
que já estão em uso, desabilitadas pra evitar redundância) — com um campo de busca (mostrado quando
há mais de 8 candidatas) pra filtrar por nome ou por prefixo da amostra, já que um equipamento real
pode ter dezenas de prefix-lists cadastradas. Clicar numa candidata abre direto o modal de
preview/edição de sempre — **nenhum prefixo é digitado** nesse fluxo, só a escolha da lista. Mikrotik
é exceção: como não tem prefix-list separada, mostra direto um campo pra digitar o prefixo.

---

## Atualização otimista do painel após uma ação real (adicionado em 2026-08-01)

Problema: `BgpSnapshot.dados` só é reescrito pela rotina noturna ou pelo botão "Atualizar agora" —
e "Atualizar agora" relê o **último backup já salvo em disco**, que não muda só porque uma ação foi
executada (precisa de um backup NOVO do equipamento, que só acontece na rotina de backup normal).
Sem isso, o painel continuava mostrando um prefixo como anunciado mesmo depois do operador já ter
executado "Parar de anunciar" nele com sucesso — reportado com um caso real
(`179.0.110.0/24` continuava na tabela mesmo após a ação real).

### `clientes/bgp_actions.py::aplicar_efeito_localmente(vendor, dados, tipo, nome_sessao, alvo, params)`

Chamada por `bgp_views.py::bgp_executar_acao` logo depois de `executar_acao_bgp` devolver
`status == 'sucesso'` — atualiza o MESMO dict `dados` que é salvo em `BgpSnapshot.dados`:

| `tipo` | Efeito aplicado |
|---|---|
| `ativar_sessao`/`desativar_sessao` | `sessao['habilitada'] = True/False` |
| `prepend` | `termo['prepend'] += delta` no termo responsável pelo match (`_termo_e_entrada_responsaveis`) |
| `parar_anuncio` | `termo['acao'] = 'reject'` no termo responsável — reproduz o resultado observável pro prefixo alvo independente do mecanismo real usado por cada fabricante (node vira deny no Huawei, deny novo inserido no Cisco, term desativado no Juniper, regra/network desabilitada no Mikrotik) |
| `novo_anuncio` | Insere um termo novo (`acao: accept`, referenciando a `lista`/prefixo sintético) na policy da sessão |
| `community` | Nada — communities não afetam o que é simulado como anunciado |

No final, se a sessão tem `policy_out`, recalcula `dados['anuncios'][sessao]` via
`simular_anuncios` — é isso que a tabela "Prefixo anunciado" da UI usa pra renderizar.

**É uma aproximação otimista**, não uma leitura real do equipamento: assume que o comando aplicado
fez exatamente o que a mesma lógica usada pra gerá-lo (`comandos_*`) previu. Qualquer divergência
real é corrigida no próximo backup de verdade (rotina noturna ou "Atualizar agora" após um backup
novo) — isso já era uma limitação inerente do snapshot (sempre foi cópia ponto-no-tempo do backup,
nunca a config viva do equipamento), só ficou mais visível sem essa atualização otimista. Nunca
levanta exceção — falha em aplicar localmente é logada mas não derruba a resposta de sucesso já
obtida do equipamento (a ação real já aconteceu; só a exibição ficaria desatualizada até o próximo
backup, o que já era o comportamento anterior a esta mudança).

### Bug pego durante a implementação (`novo_anuncio`, corrigido antes de ir pra produção)

A primeira versão inseria o termo novo com `ordem = max(ordem existente) + 1` — quebra quando o
termo/node catch-all final já tem a maior `ordem` (ex: Huawei `route-policy NOME deny node 2000` tem
`ordem=2000`, a mesma semântica usada por `simular_anuncios` pra ordenar a avaliação), colocando o
termo novo DEPOIS do catch-all e fazendo o prefixo nunca aparecer como anunciado na simulação — o
mesmo tipo de bug de posicionamento já visto e corrigido em `comandos_novo_anuncio`/Juniper. Corrigido
pra sempre inserir com `ordem` menor que a de qualquer termo catch-all existente na policy.

Validado com 9802 combinações (toggle/prepend/parar/novo_anuncio, incluindo `novo_anuncio` do
Mikrotik) contra os 53 `BgpSnapshot` reais — zero erros inesperados. Testado também o fluxo HTTP
completo (view real, `executar_acao_bgp` mockada pra não tocar equipamento) confirmando que o
prefixo some da tabela de anunciados no mesmo request em que a ação é executada.

---

## Ver tráfego em tempo real (Huawei 2026-08-01, Cisco/Juniper 2026-08-03)

Cada sessão BGP Huawei, Cisco ou Juniper tenta identificar automaticamente por qual interface local
o peer é alcançado, e — quando consegue — ganha um botão **"📶 Ver tráfego"** que abre uma janela
mostrando o tráfego da interface ao vivo.

### `clientes/bgp_matcher.py::identificar_interface(dados, peer_ip)`

Vendor-agnóstica (opera só sobre `dados['ips']`, já extraído pelo parser — lista de
`{"ip": "X.X.X.X/Y", "interface": "NOME"}`): acha a interface local cuja subnet CONTÉM `peer_ip`.
Peers eBGP diretamente conectados (a maioria em ambiente de borda) ficam na mesma subnet do lado
local — ex: interface com `177.85.201.250/30` e peer `177.85.201.249` no mesmo /30 — então dá pra
inferir a interface sem consultar rota/ARP ao vivo no equipamento. Devolve `None` quando não acha
(peer iBGP multihop via loopback/IGP, IPv6 — o parser Huawei só extrai `ip address` IPv4 de
interface hoje — ou qualquer IP fora de toda subnet local conhecida no backup): nesses casos não tem
como inferir com segurança, e o botão simplesmente não aparece.

Chamada em `clientes/tasks.py::_atualizar_snapshot_bgp_de_acesso`, pra `vendor_parser in ('huawei',
'cisco', 'juniper')` (ampliado em 2026-08-03 — antes só Huawei), populando `sessao['interface']` no
snapshot salvo. Validado contra todos os 53 `BgpSnapshot` reais via os backups em disco (229 sessões
Huawei): 114 com interface identificada, 115 sem (a maioria peers iBGP via loopback ou IPv6) — zero
erros inesperados, e as amostras conferidas manualmente batem com a config real (inclusive o exemplo
usado nesta seção: `177.85.201.249` → `GigabitEthernet0/7/1.3179`, que tem `ip address 177.85.201.250
255.255.255.252`). Ao ampliar pra Cisco/Juniper, rodado o mesmo `identificar_interface` contra os
snapshots reais desses fabricantes: 8 Cisco (achou interface em 15 de 22 sessões) e 1 Juniper — o
único acesso Juniper do sistema, `BORDA` (10.20.1.1, cliente INFOLINE) — achou 23 de 52 sessões (não
testado ao vivo contra o equipamento: esse host está fora do ar desde 2026-07-29, ver
[[project_infoline_site_wg_down_2026_07]]).

### Frontend — reaproveita o WebSocket do terminal SSH (`ws/ssh/`), sem endpoint novo

O botão abre um modal que conecta na MESMA conexão WebSocket já usada pelo painel de Terminal
normal (`ws/ssh/`, `SSHConsumer` — nenhuma mudança em `consumers.py`, nenhum endpoint HTTP novo),
mas **não exibe um terminal** — só usa a conexão pra alimentar um gráfico (ver "Gráfico ao vivo"
abaixo). A primeira versão embutia um terminal de verdade (xterm.js) mostrando a saída bruta do
comando; removido a pedido do usuário ("a tela do comando não precisa") — o gráfico já é a única
coisa exibida.

1. `{action: 'connect', acesso_id, independente: true, cols, rows}` — `independente: true` é
   importante: evita entrar numa sessão SSH já compartilhada por outro operador nesse host (abre uma
   conexão isolada só pra esse monitoramento, sem interferir em quem já estiver usando o terminal).
   `cols`/`rows` são fixos (120×24) já que não há mais um terminal visual pra medir o tamanho real.
2. Ao receber `{type: 'connected'}`, envia `{action: 'command', command: ...}` — o mesmo mecanismo
   que o handler `action == 'command'` do consumer já usa pra `enviar_comando()` (idêntico ao que
   orquestra digitação normal, só que com o texto pronto em vez de tecla por tecla). O comando
   depende do fabricante (`_trafegoConfigVendor(vendor, interfaceNome)`, `bgp_automacao.html`) — ver
   "Multi-fabricante" abaixo.
3. Saída chega como já era esperado (frames binários = texto puro, hot path; frames de texto =
   mensagens de controle JSON) — só que em vez de escrever num terminal, o texto vai direto pro
   parser do gráfico (ver abaixo).
4. Ao fechar o modal, manda Ctrl+C (`{action:'command', command:'\x03'}`) antes de fechar o socket —
   encerra o `| refresh 1` do Huawei com elegância em vez de só derrubar a conexão SSH no meio do
   loop (inofensivo pros comandos de foto única do Cisco/Juniper, que não deixam nada rodando).

Não gera registro de auditoria `AcaoBgp` (é leitura pura, mesmo padrão do "Atualizar agora").

### Gráfico ao vivo — única saída exibida (Chart.js)

Pedido do usuário: mostrar o tráfego em forma de gráfico, não uma tabela de texto rolando (e depois,
"a tela do comando não precisa" — removendo até a exibição de texto bruto que existiu por uma
versão). Reaproveita `chart.umd.min.js` (Chart.js, já vendorizado no projeto e usado em
`monitoramento/tab_monitoramento.html` — mesma paleta de cores e `_fmtBps` copiados de lá pra manter
consistência visual entre as duas telas).

**Formato real do comando** (capturado ao vivo direto do equipamento antes de escrever o parser —
mesmo rigor de "validar contra dado real" usado no resto do projeto): o Huawei **não usa sequências
ANSI** pra redesenhar a tela em `| refresh 1` — cada ciclo já vem delimitado em texto puro:

```
  ---- (Refreshed at 2026-08-02 10:58:31) ----
Inbound
Interface   Octets(bytes/s) Unicast(pkts/s) Multicast(pkts/s) Broadcast(pkts/s)
GE0/7/1.3179      649239128          529958                 0                 0
Outbound
Interface   Octets(bytes/s) Unicast(pkts/s) Multicast(pkts/s) Broadcast(pkts/s)
GE0/7/1.3179       55782043          146817                 0                 0

  ---- (Finish) ----
```

`_trafegoReceberTexto(texto)` (dentro de `abrirTrafego`, `bgp_automacao.html`) acumula o texto recebido
num buffer (`trafegoState.bufferParse`) que é reprocessado a cada chunk por uma regex (`cfg.regex`,
modo `refresh_servidor` — ver "Multi-fabricante" abaixo) que só aceita um ciclo como completo quando o
marcador `(Finish)` já chegou — evita processar um bloco cortado no meio por um frame de WebSocket
parcial (testado simulando o chunk quebrado no meio do texto). Extrai `Octets(bytes/s)` de
Inbound/Outbound, multiplica por 8 (bytes→bits) e empurra um ponto novo nos dois datasets do Chart.js
(`Entrada ↓`/`Saída ↑`), limitado a 60 pontos (~1 minuto de histórico a 1 ciclo/segundo) — depois de
cada ciclo processado, o buffer é cortado até onde já foi consumido, pra não crescer sem limite numa
sessão longa.

### Multi-fabricante (Cisco e Juniper, adicionado em 2026-08-03)

Cisco (IOS/IOS-XE) e Juniper (Junos) não têm um comando de auto-refresh no próprio equipamento como o
`| refresh 1` do Huawei — a solução usa `_trafegoConfigVendor(vendor, interfaceNome)`
(`bgp_automacao.html`) pra decidir, por `SNAPSHOT.vendor`, entre dois modos:

- **`refresh_servidor`** (Huawei, único que tem hoje): comando único enviado uma vez, o próprio
  equipamento manda os ciclos sozinho — descrito acima.
- **`poll`** (Cisco e Juniper): sem auto-refresh no equipamento, o frontend reenvia o comando `show`
  periodicamente (`setInterval`, um timer por sessão de tráfego aberta, limpo em `fecharTrafego()`).
  A cada envio o buffer de parse é zerado (`_enviarComando()`); assim que a regex do fabricante acha
  um par entrada+saída completo no texto acumulado desde o último envio, registra um ponto no gráfico
  e zera o buffer de novo — evita casar de novo com o eco do comando ou o prompt sobrando na tela.

**Cisco** — interface **base**, não a sub-interface. Confirmado ao vivo (acesso 887,
`Port-channel1.3062`, ASR1000/IOS-XE 16.9.5): `show interfaces Port-channel1.3062` não traz nenhuma
linha de taxa — sub-interface 802.1Q não tem contador de taxa próprio nessa plataforma. A interface
física/port-channel **base** (`Port-channel1`, tudo antes do primeiro `.`) tem:

```
show interfaces Port-channel1 | include rate
  5 minute input rate 1226501000 bits/sec, 160701 packets/sec
  5 minute output rate 1235442000 bits/sec, 160187 packets/sec
```

(o filtro `include rate` também deixa passar outras linhas com a substring "rate", ex:
`Queueing strategy: fifo` — inofensivo, a regex de parse exige a frase completa `input rate .. bits/sec`
/ `output rate .. bits/sec`, então não há falso positivo.) Testado direto via canal SSH raw
(`paramiko.invoke_shell`, mesmo mecanismo que `consumers.py::connect_ssh` usa) simulando exatamente o
que o WebSocket recebe — eco do comando + linhas de taxa + prompt de volta, tudo num único bloco de
texto. Reenviado a cada 3s (`intervaloMs: 3000`). Nota: a "5 minute input/output rate" do Cisco é uma
média móvel de 5 minutos por padrão (o `load-interval` da interface, que o CRM não altera — seria
mudança de config num recurso que é só leitura) — reenviar mais rápido que isso não traz valor mais
"instantâneo", só a mesma média recalculada conforme o próprio equipamento atualiza.

**Juniper** — interface **exata identificada** (sem tirar sufixo — diferente do Cisco, o Junos mantém
contador de taxa por unidade lógica). O formato da saída depende do **tipo** de interface — descoberto
em duas rodadas de validação ao vivo em 2026-08-03, contra dois tipos reais diferentes no INFOLINE-BGP:

**1) Bundle agregado (`ae*`)** — ex: `ae0.1694`:

```
show interfaces {interface} extensive | match "Input :|Output:"
        Input :  523164884173     372108 595708859852103   3353064280
        Output:  318391137537     263274 125602267755030    977728704
```

Não existe uma linha "Input rate:" separada (a primeira tentativa, com esse formato clássico, deu
`match` vazio — corrigida depois de ver a saída real). O formato é uma tabela
`Statistics  Packets  pps  Bytes  bps` por Bundle/Link, cada linha com 4 números — a taxa é a
**última coluna**. Detalhe não-óbvio: `"Output:"` **não tem espaço** antes dos dois-pontos, enquanto
`"Input :"` **tem**. Regex: `/Input\s*:\s*\d+\s+\d+\s+\d+\s+(\d+)[\s\S]*?Output\s*:\s*\d+\s+\d+\s+\d+\s+(\d+)/i`.
Reenviado a cada 2s.

**2) Porta física (`et-`/`xe-`/`ge-`/`so-`…)** — ex: `et-0/0/3.0`:

```
show interfaces {interface} extensive | match "Input  bytes|Output bytes"
    Traffic statistics:                      ← SEM bps (só contador total)
     Input  bytes  :    59044842383109703
     Output bytes  :     9181354704904532
     IPv6 transit statistics:                ← SEM bps, subconjunto IPv6
     ...
    Local statistics:                        ← SEM bps (tráfego pro RE)
     ...
    Transit statistics:                      ← COM bps — É ESSA que quer
     Input  bytes  :    59044730447364608           7356444688 bps
     Output bytes  :     9181293135836809           1905685568 bps
     IPv6 transit statistics:                ← COM bps, mas só IPv6 (subconjunto)
      Input  bytes  :    5372420195111380            951758992 bps
      Output bytes  :                   0                    0 bps
```

Formato completamente diferente do bundle: o Junos tem várias seções de estatística por interface
física (`Traffic statistics:`, `Local statistics:`, `Transit statistics:`, e `IPv6 transit
statistics:` aninhada dentro de cada uma). A primeira tentativa (regex igual à do bundle, esperando
`Input :`) não bateu nada — o formato real usa `Input  bytes  :` com o total seguido da taxa em bps
só quando a linha pertence a uma seção que rastreia taxa. Só `Transit statistics:` (tráfego total
IPv4+IPv6 pela interface) tem bps; `Traffic statistics:` e `Local statistics:` só têm contador
acumulado, sem bps algum. A regex final
(`/Input\s+bytes\s*:\s*\d+\s+(\d+)\s*bps[\s\S]*?Output\s+bytes\s*:\s*\d+\s+(\d+)\s*bps/i`) exige a
presença de "bps" na linha — isso sozinho já pula as seções sem taxa (`Traffic`/`Local statistics`,
que não têm "bps" na linha). Como o regex é não-guloso e casa a PRIMEIRA ocorrência, para no par de
`Transit statistics:` (total) sem nunca alcançar o `IPv6 transit statistics:` aninhado logo depois
(que é só o subconjunto IPv6, um valor menor). Testada em Node contra o texto real colado pelo
usuário — extrai `7356444688`/`1905685568` bps corretamente (bate com `Transit statistics:`, não com
o bloco IPv6). Reenviado a cada 2s.

O JS (`_trafegoConfigVendor`, `bgp_automacao.html`) decide qual dos dois formatos usar checando se
`interfaceNome` começa com `ae` (regex `/^ae\d/i`).

### Dois bugs reais pegos em produção durante o desenvolvimento (versão com terminal embutido)

Histórico — a versão que exibia um terminal xterm.js junto do gráfico teve dois bugs reais antes de
funcionar, ambos por esquecer de copiar configuração que `terminal.html` (terminal SSH normal) já
tinha. Removidos junto com o terminal (não se aplicam mais), mas registrados aqui porque o padrão
pode se repetir se um terminal embutido for reintroduzido no futuro:

1. **Terminal ficava em branco, nada aparecia** — `xterm.css` base não define `width`/`height` em
   `.xterm` (só `position: relative`); sem essas regras o terminal renderiza com 0px de altura e
   fica invisível mesmo recebendo dados.
2. **Depois do fix acima, ficava travado em "Conectando…" pra sempre** — mesmo com os logs do
   daphne confirmando que a sessão SSH conectou de verdade no equipamento. Causa:
   `consumers.py::send_output` manda a saída do terminal como frame **binário puro** (sem JSON
   overhead, de propósito). Sem `socket.binaryType = 'arraybuffer'`, o navegador usa o default
   `'blob'` — `e.data instanceof ArrayBuffer` dava falso pra todo frame de saída, caindo no
   `JSON.parse(blob)`, que estoura uma exceção não tratada dentro do `onmessage`. Resultado: a
   mensagem `{type:'connected'}` (frame de texto) processava normalmente, mas nenhuma saída real
   (frames binários) jamais chegava a aparecer.

---

**Validado em 2026-08-01** contra os 53 `BgpSnapshot` reais de produção existentes (todos os 4
fabricantes), rodando os 4 endpoints novos (`atualizar`, `community` preview, `escanear-prefixo`,
`novo_anuncio` preview) em cada um — sem nenhum erro inesperado. Nenhuma ação real (`preview=false`)
executada contra equipamento durante a validação.

---

## Validar anúncios ao vivo (adicionado em 2026-08-03)

Dentro do card de cada sessão (expandindo), botão **"🔍 Validar anúncios"** conecta AO VIVO no equipamento
(leitura pura, nenhum comando muda config, nunca gera `AcaoBgp`) e mostra o que essa sessão está
anunciando/recebendo de verdade agora — via `peer_ip` da sessão. É complementar, não substitui, a
simulação já existente baseada em config (`dados['anuncios']`, de `bgp_matcher.simular_anuncios`): a
simulação mostra o que a policy DEVERIA deixar passar; este botão mostra o que está passando de fato no
RIB/Adj-RIB do equipamento nesse instante.

### `clientes/bgp_actions.py` — `validar_anuncios_ao_vivo(acesso, vendor, dados, sessao)`

Conecta via `_conectar_script` (mesma infra de `executar_acao_bgp`), roda os comandos e desconecta.
Nunca levanta exceção de conexão — devolve `{'status': 'erro', 'mensagem': ...}`.

**Descoberta crítica que mudou o design:** ao testar contra os 53 `BgpSnapshot` reais de produção, um
peer Huawei real (acesso 990) tinha **1.084.769 prefixos recebidos** (full-table/transit) e um peer
Juniper real (acesso 495, PTTRJ) tinha **160.204** — listar tudo isso travaria a conexão SSH (o comando
de listagem sozinho demora mais que qualquer `read_timeout` razoável) e estouraria a resposta HTTP.
Solução: `comando_contar_recebidos(vendor, dados, sessao)` roda PRIMEIRO um comando barato (contador já
computado pelo equipamento, não uma varredura da RIB inteira) pra saber a quantidade; só busca a lista
completa (`comandos_validar_anuncios`) se `total_recebidos <= LIMITE_PREFIXOS_LISTAR` (500) — acima
disso devolve só a contagem (`recebidos: null, recebidos_truncado: true`). Não existe equivalente pro
lado ANUNCIADO porque em todo peer testado ao vivo (Huawei, Cisco, Juniper, Mikrotik) esse número ficou
sempre pequeno (1 a 14) — o risco de explosão é só do lado recebido.

**Comandos por fabricante — todos confirmados ao vivo contra equipamento real** (não só documentação):

| Fabricante | Contagem barata (recebidos) | Lista anunciados | Lista recebidos |
|---|---|---|---|
| Huawei | `display bgp peer {ip} verbose` (`Received total routes:`) | `display bgp routing-table peer {ip} advertised-routes` | `display bgp routing-table peer {ip} received-routes` |
| Cisco/Datacom | `show ip bgp neighbors {ip} \| include Prefixes Current` | `show ip bgp neighbors {ip} advertised-routes` | `show ip bgp neighbors {ip} received-routes` (fallback abaixo) |
| Juniper | `show bgp neighbor {ip} \| match "Received prefixes"` | `show route advertising-protocol bgp {ip}` | `show route receive-protocol bgp {ip}` |
| Mikrotik v6 | `/routing bgp peer print detail where name="{nome}"` (`prefix-count=`) | `/routing bgp advertisements print peer="{nome}"` | `/ip route print where received-from="{nome}"` |
| Mikrotik v7 | `/routing bgp session print detail where name="{nome}"` (`prefix-count=`) | `/routing bgp advertisements print where peer="{nome}"` | `/ip route print where gateway={ip} bgp=yes` |

**Cisco — fallback de `received-routes`:** sem `soft-reconfiguration inbound` configurado no peer, o
comando erra com `% Inbound soft reconfiguration not enabled` (confirmado ao vivo, acesso 887) — nesse
caso usa `show ip bgp neighbors {ip} routes` como fallback (mostra o equivalente PÓS-política, o que
realmente entrou na RIB local, sem precisar dessa config extra no equipamento — não altera config pra
viabilizar a consulta).

**Mikrotik v6 vs v7 — sintaxes completamente diferentes**, descoberto testando ao vivo contra os dois
(não presumido):
- v6: `print peer=NOME` (sem `where`); rota recebida tem a propriedade `received-from=NOME` no `/ip route`.
- v7: exige `print where peer=NOME` (com `where` — sem, dá erro de sintaxe); a propriedade
  `received-from` **não existe mais** em `/ip route` (removida no redesenho do BGP do v7, confirmado via
  tab-completion ao vivo: `.dead .id .nextid active bgp bgp-mpls-vpn ... ` sem `received-from`) — usa
  `gateway={peer_ip}` como proxy (o gateway da rota aprendida via BGP É o endereço remoto do peer).

**`_extrair_prefixos(texto)`** (`bgp_actions.py`) — extrai só os prefixos CIDR do texto bruto, igual pra
todos os 4 fabricantes: regex genérica `\d{1,3}(\.\d{1,3}){3}/\d{1,2}` funciona porque em toda saída real
testada o prefixo é o único token da linha no formato IP/máscara (next-hop/gateway aparecem sem barra).
**Bug real pego testando:** Cisco imprime a rota padrão como `0.0.0.0` **sem `/0`** (diferente de todo
outro prefixo da mesma tabela, que sempre tem máscara) — a regex genérica não pegava. Corrigido com
`_DEFAULT_ROUTE_RE`, que exige `0.0.0.0` como PRIMEIRO campo da linha (só flags de status antes) seguido
de outro IP (o next-hop) — evita falso positivo quando `0.0.0.0` aparece como next-hop de rota local
(ex: `45.169.6.0/24  0.0.0.0  ...`, onde `0.0.0.0` vem DEPOIS do prefixo de verdade).

### `clientes/bgp_views.py::bgp_validar_anuncios` — `POST /clientes/bgp/<acesso_id>/validar-anuncios/`

Body `{sessao}`. Mesma checagem de permissão (`_checar_staff`/`_checar_acesso`) das outras ações. Devolve
`{status, anunciados: [...], recebidos: [...]|null, total_recebidos, recebidos_truncado}` ou
`{error}` (404/422). Não grava `AcaoBgp` — é leitura pura, mesmo padrão do "Atualizar agora"/"Ver
tráfego".

### Frontend

Botão dentro do card expandido de cada sessão (não no cabeçalho, pra não competir com "Ver tráfego"/
"Ativar"/"Desativar" que já lotam a linha). `validarAnuncios(idx, nomeSessao)` (`bgp_automacao.html`)
faz o POST e renderiza duas colunas (Anunciados/Recebidos) com contagem no título; peer truncado mostra
só um aviso amarelo com a contagem, sem tentar listar.

**Validado em 2026-08-03** com `Client()` do Django direto contra a rota HTTP real (não só a função
isolada) em 4 casos: sucesso normal (Cisco, 1 anunciado + rota padrão recebida), peer full-table
truncado (Huawei, 1M+), sessão inexistente (404) e body sem `sessao` (400) — todos com o resultado
esperado.

---

## Configurar nova sessão — Cisco (adicionado em 2026-08-04)

Botão **"＋ Configurar nova sessão"** no cabeçalho da tela (fora dos cards de sessão), restrito a
Cisco/Datacom por enquanto. Monta a config completa de uma sessão BGP nova — `neighbor`+`remote-as`+
`description`, ativação por address-family (IPv4 e/ou IPv6 do mesmo peer no mesmo clique) e route-map
IN/OUT, reaproveitando uma prefix-list já existente ou criando uma nova exclusiva da sessão (mesmo
princípio de nunca editar um objeto compartilhado já usado no resto da automação BGP).

### Convenção de nomenclatura

Descrição sempre `UPSTREAM-{sufixo}-V4`/`V6` ou `DOWNSTREAM-{sufixo}-V4`/`V6`; route-map
`RM-PEER-{sufixo}-{V4|V6}-{IN|OUT}`; prefix-list nova `PL-ORIGIN-{cidr}` (prefixo nosso, direção OUT
em qualquer tipo de peer), `PL-UPSTREAM-{sufixo}-{cidr}` (algo aceito de um upstream específico, IN),
ou `PL-CLIENTE-{sufixo}-{cidr}` (prefixo próprio de um cliente downstream, IN).

### Fonte de prefix-lists existentes: snapshot ou leitura ao vivo

O modal busca candidatas no snapshot por padrão (mesmo dado usado por "Anunciar prefixo novo"); um
botão "🔄 Existente (ao vivo)" força uma leitura SSH real (`show running-config | section prefix-list`
+ `section route-map`) via `clientes/bgp_actions.py::buscar_prefix_lists_ao_vivo`, reaproveitando a
MESMA regex do parser de backup (`clientes/backup_parser.py::_extrair_prefix_lists_e_policies_cisco`,
extraída de `parse_cisco` pra esse fim) — o texto de `show running-config` usa a mesma sintaxe de um
backup, então nenhum parser novo foi necessário.

### Geração de comandos (`clientes/bgp_actions.py::comandos_criar_sessao`)

Ordem (ajustada em 2026-08-04, a pedido do usuário): 1) prefix-list NOVA (só quando o operador
escolheu "criar nova" em vez de reaproveitar), 2) route-map novo (sempre criado, mesmo reaproveitando
prefix-list existente), 3) `router bgp`+`neighbor`s+blocos `address-family` (a config da sessão em si,
que é quem referencia o route-map) — sempre definir o que vai ser referenciado antes de referenciar,
evitando a sessão subir momentaneamente sem filtro. `send-community both` é sempre incluído em toda
address-family nova. Recusa (`AcaoBgpNaoSuportada`) peer IP/route-map/prefix-list colidente com o que
já existe (snapshot ou leitura ao vivo, conforme o operador escolheu na busca).

Um route-map novo (`RM-PEER-*-IN`/`OUT`) é **sempre** criado pra uma sessão nova, mesmo quando a
prefix-list que ele referencia é reaproveitada — só a prefix-list em si é condicional a "criar nova"
(bug pego no code review deste próprio task antes de ir pra produção: a primeira versão só gerava o
`route-map .../match ...` dentro do branch de prefix-list nova, deixando a direção "reaproveitar
existente" sem route-map nenhum).

**Sobe em `shutdown` (adicionado em 2026-08-04):** todo `neighbor` novo recebe `neighbor X shutdown`
logo depois do `description` — a sessão nunca ativa direto na criação. Rede de segurança pedida pelo
usuário: dá pro operador conferir a config aplicada no equipamento antes de trazer a sessão pra cima
de verdade, usando o botão "Ativar" que já existe pra qualquer sessão (`comandos_toggle_sessao`).
`aplicar_efeito_localmente` grava `habilitada: False` na sessão nova, então o painel já mostra o
status correto (desativada) assim que a ação é confirmada.

**Reaproveita route-map órfão (adicionado em 2026-08-04):** caso real em produção — uma sessão criada
por esta automação foi removida manualmente no equipamento (só o `neighbor`; o route-map/prefix-list
associados não somem sozinhos, Cisco não limpa objeto referenciado automaticamente), e tentar recriar
com o mesmo sufixo esbarrava sempre em "já existe um route-map chamado ... — escolha outro sufixo".
Corrigido: quando o nome do route-map calculado já existe no equipamento, `_route_map_em_uso_por_
sessao_ativa(dados, nome)` confere se alguma sessão CONHECIDA ainda usa esse nome como `policy_in`/
`policy_out`. Se nenhuma usa, é órfão — reaproveitado como está (não gera `route-map .../match ...`
novo pra essa direção, só a anexação `neighbor X route-map NOME in|out`; a escolha de prefix-list do
operador pra essa direção é ignorada, já que o route-map já tem seu próprio match). Se alguma sessão
ativa ainda usa esse nome, continua recusando (`AcaoBgpNaoSuportada`) — reaproveitar corromperia a
policy dessa outra sessão. `_aplicar_criar_sessao_localmente` espelha a mesma checagem (`rm_nome in
dados['policies']`) pra não duplicar o termo local quando o route-map foi reaproveitado.

### Auditoria e atualização otimista

`AcaoBgp.tipo = 'criar_sessao'`. `aplicar_efeito_localmente` insere a(s) sessão(ões) nova(s) +
prefix-lists/policies novas direto em `BgpSnapshot.dados` (mesmo princípio de toda outra ação) — a
sessão aparece no painel no mesmo request da confirmação, sem esperar o próximo backup.

### Frontend

Reaproveita o modal de preview/confirmação genérico já usado por toda ação desta tela
(`pedirAcao`/`confirmarAcao`) — o único fluxo novo é o formulário de coleta (toggle upstream/
downstream, checkboxes IPv4/IPv6, e um "picker" de prefix-list por direção/AF que busca candidatas via
`escanear-prefixo` com um novo parâmetro opcional `ao_vivo`).

### Aviso visível quando "Atualizar agora" não muda nada (adicionado em 2026-08-04)

Bug real em produção: uma sessão criada por "Configurar nova sessão" foi removida manualmente no
equipamento (fora da automação, via SSH direto); "Atualizar agora" parecia não fazer nada e a
automação continuava recusando recriar a sessão com "já existe peer/route-map". Causa: `Atualizar
agora` só relê o **último backup já salvo em disco** (nunca conecta ao vivo — ver "Atualizar snapshot
sob demanda" acima) e a proteção `patch_local_pendente`/`'sem_novidade'` recusa reprocessar enquanto
não existir um backup NOVO — o único aviso disso era um tooltip discreto no badge, fácil de não notar.
Corrigido: `atualizarAgora()` (`bgp_automacao.html`) agora também colore o badge de âmbar
(`.aviso-sem-novidade`) e mostra um `alert()` explicando a causa e a ação certa (tirar um backup
manual do host fora desta tela, depois clicar em "Atualizar agora" de novo). Não muda a lógica do
backend — só torna o estado existente visível de verdade.

**Nota operacional:** se uma sessão criada por esta automação for removida manualmente no
equipamento, o route-map/prefix-list associados **não são removidos junto** (Cisco não limpa objetos
referenciados automaticamente) — tentar recriar com o mesmo sufixo esbarra na mesma checagem de
colisão de nome que protege contra sobrescrever objetos compartilhados (ver "Geração de comandos"
acima). Prefix-lists com prefixo próprio (`PL-ORIGIN-*`) costumam ser reaproveitadas por VÁRIOS
route-maps/upstreams ao mesmo tempo — nunca remova uma sem antes conferir (`bgp_matcher.
listar_prefix_lists`/a tela de escanear-prefixo) se outra sessão ainda a referencia.

---

## Anúncios por community — descoberta automática + intenção (Huawei, adicionado em 2026-08-13)

**Arquivos:** `clientes/bgp_community_auto.py` (novo), `clientes/backup_parser.py` (`parse_huawei`),
`clientes/bgp_views.py` (`bgp_community_mapa` + 3 tipos novos em `_gerar_comandos_por_tipo`),
`clientes/bgp_actions.py` (`aplicar_efeito_localmente`), `clientes/templates/bgp_automacao.html`
(painel "🎯 Anúncios por community"), migration `0107_alter_acaobgp_tipo`.

Vários roteadores de borda deste CRM já usam uma convenção de **anúncio por community**, em que o
prefixo carrega a "intenção" e a policy de saída de cada circuito traduz essa intenção em
comportamento BGP. Esta feature **descobre essa convenção sozinha** a partir do backup e deixa o
operador trabalhar por intenção (prefixo + circuito + ação), sem escrever `route-policy`,
`community-filter` nem decorar número de community.

```
Prefixo → Circuito → Ação → Community → Route-Policy → Anúncio BGP
```

### A convenção (confirmada em 8 equipamentos reais)

```
ip community-filter basic c-02-export-2p index 10 permit 65100:50203
                          └┬─┘ └───┬───┘                 └─┬─┘└┬┘└┬┘
                       circuito   ação                    asn grupo sufixo

route-policy AS263941-TOGONET-V4-OUT permit node 12     ← traduz a intenção
 if-match community-filter c-02-export-2p
 apply as-path 268080 268080 additive

route-policy RT-BGP-LOCAL-52DC-38 permit node 10        ← declara a intenção
 apply community 65100:50203 additive

network 2804:52DC:3000:: 38 route-policy RT-BGP-LOCAL-52DC-38
```

Catálogo de ações (`ACOES`, sufixo fixo da community e node canônico na policy OUT — ordem do
template, ver a seção de 18/08/2026): `export-bl` (67/09, `deny`), `export-bh` (66/10),
`export` (01/11), o node 12 reservado à community global, `export-1p` (02/13), `export-2p`
(03/14), `export-3p` (04/15), `export-4p` (05/16), `export-ne` (08/17, `permit` + `apply
community no-export`), `export-df` (09, legada — reconhecida, não gerada), `import-rr` (00, só
na policy de entrada). **O número de prepends é propriedade da AÇÃO** — nunca é cadastrado por
anúncio.

### Extração (`parse_huawei`)

Três chaves novas no snapshot, sem alterar nenhum contrato existente:

| Chave | Conteúdo |
|---|---|
| `community_filters` | `{"c-02-export-2p": [{"index": 10, "acao": "permit", "valores": ["65100:50203"]}]}` |
| `community_nodes` | por route-policy, só os nodes que mexem com community (`if-match community-filter` e/ou `apply community`), com `prepend_as`/`local_preference`/`prefix_lists` |
| `networks[].route_policy` / `.familia` / `.ip` / `.mascara` | a policy local de cada prefixo originado e a address-family (`ipv4-family`/`ipv6-family`) em que o `network` está |

`community_nodes` existe **em separado** de `policies` justamente porque a simulação de anúncios
descarta esses nós (community é atributo da rota, não do prefixo — ver "Termos não suportados"):
a automação por community precisa exatamente do que a simulação joga fora.

### Descoberta (`mapear_circuitos` / `mapear_anuncios`)

- **Circuito ↔ sessão é resolvido pela EXPORT POLICY da sessão** — a policy que dá `if-match
  community-filter c-NN-*` é, por definição, a saída daquele circuito. Foi a única regra que se
  sustentou nos 8 equipamentos: o nome segue `AS<asn>-<NOME>-V4-OUT` na maioria mas nem sempre
  (`RP-IX-SP-V4-OUT`), e há caixas onde a policy IN de um circuito carimba a community de origem de
  **outro** (copy/paste real — vira aviso, não vira mapeamento errado).
- **Grupo/ASN do circuito** vêm da community da ação `export`, não de uma média das ações: em
  produção existem caixas onde `c-04-export-bh`/`c-04-export-bl` apontam pro grupo 503 (bloco
  clonado do c-03 sem trocar o número). O circuito continua sendo o 504 e a divergência é reportada.
- **Matriz de anúncios**: para cada `network` com route-policy local, as communities aplicadas são
  resolvidas em `{circuito: ação}`. Communities fora do catálogo (ex: `65100:10091`, convenções
  próprias do cliente) ficam em `communities_extras` e são **reemitidas intactas** em qualquer
  reescrita.

### Validações (§16 da especificação) — `validar_mapa`

Rodam a cada carga do painel, sobre a config **já existente**: filtro referenciado que não existe,
ação com community-filter mas sem node na policy OUT, quantidade de prepend divergente da ação,
`deny node 999` ausente, policy referenciada e não criada, ASN de prepend não identificado, policy
de entrada que não carimba a community de origem, mais de uma policy de saída por família. São
avisos (não bloqueiam nada) — nos 8 equipamentos reais apontaram **58 inconsistências**, todas de
config pré-existente.

### Ações geradas

| Tipo (`AcaoBgp.tipo`) | O que faz |
|---|---|
| `anuncio_community` | Muda a intenção de UM prefixo para UM circuito (inclusive "deixar de anunciar") |
| `novo_prefixo_community` | Passa a originar um prefixo novo já com a intenção definida (route-policy local + `network` na address-family certa) |
| `provisionar_circuito` | Gera só o que **falta** de um circuito: community-filters ausentes + nodes correspondentes na policy de saída + `deny node 999` |

**Por que `undo apply community` + `apply community …`:** no VRP um `apply community` repetido no
mesmo node SOMA à lista já configurada — sem o `undo` não haveria como remover uma community. Como
o Huawei desta automação usa config candidata + `commit` (ver `_PRECISA_COMMIT`), as duas linhas
viram uma alteração atômica: o prefixo nunca fica um instante sem communities na config em vigor.

**Nunca edita objeto compartilhado:** a mudança é sempre na route-policy LOCAL do prefixo (que só
serve àquele `network`), nunca na policy de uma sessão nem numa prefix-list — mesmo princípio já
adotado em "parar de anunciar"/"anunciar prefixo novo".

**Encaixe de node** (`_node_livre`): usa o node canônico do catálogo quando livre, senão o próximo
livre. A ordem entre nodes de ação não é crítica (cada um casa um filtro diferente e um prefixo
carrega no máximo uma community por circuito); as exceções respeitadas são `export-bl` antes dos
permits e o `deny node 999` por último. Caso real que motivou o fallback: policies de peering CDN
(`AS40027-NETFLIX-OCA-V4-OUT`) já usam o node 15, reservado no catálogo pro `export-ne`.

### Endpoint e frontend

`GET /clientes/bgp/<acesso_id>/community-mapa/` — leitura pura sobre `BgpSnapshot.dados` (não
conecta em nada); 422 para fabricante diferente de Huawei, e o painel simplesmente não aparece.

Painel novo no topo da tela: cards dos circuitos (grupo de community, ASN remoto, ASN de prepend,
famílias, sessões, o que falta) e a **matriz prefixo × circuito**, onde cada célula é um `<select>`
com o catálogo de ações. Trocar a ação abre o mesmo modal de preview/edição/trial já usado por toda
ação desta tela. O `<select>` volta ao valor atual assim que o modal abre — quem muda a tela de
verdade é a confirmação (que recarrega o painel), então cancelar não deixa a tela mentindo.

O limite de sanidade do textarea editado subiu de 30 para **300 linhas** só em
`provisionar_circuito` (o bloco padrão de um circuito passa fácil de 30 linhas); as demais ações
continuam em 30.

### Fonte da verdade

A especificação original pedia o banco como fonte da verdade dos circuitos. A implementação
mantém o **equipamento** como fonte (via snapshot do backup), que é o modelo do resto desta
automação: os circuitos são descobertos a cada carga, não cadastrados. Isso evita o problema real
de um cadastro paralelo divergir da caixa sem ninguém perceber — e as caixas já tinham a convenção
configurada, que era o ponto de partida do pedido. O que o operador informa (nome, ASN remoto, ASN
de prepend, policy alvo) é pedido no momento de gerar config que ainda não existe.

### Validado em 2026-08-13

Contra os 8 equipamentos Huawei reais que usam a convenção (acessos 20, 175, 324, 746, 826, 923,
990, 1216): descoberta de 64 circuitos no total, matriz de anúncios de 159 prefixos, e o fluxo HTTP
completo (mapa → preview → execução com `executar_acao_bgp` mockada → efeito otimista → auditoria)
rodado ponta a ponta com `Client()` do Django — incluindo as recusas esperadas (prefixo não
originado, circuito inexistente, ação `import-rr` num anúncio, prefixo inválido, circuito sem ASN
de prepend, policy de saída ambígua). Nenhum comando real foi enviado a equipamento. Backfill dos
57 `BgpSnapshot` existentes rodado depois da mudança do parser: 54 ok, 3 com erro pré-existente e
não relacionado (2 já documentados acima — peer por hostname e prefix-list com notação de range —
e um Mikrotik cujo backup tem caractere acentuado que o Postgres em SQL_ASCII recusa gravar no
JSON; reproduzido também com o parser anterior).

---

## Anúncios por community — IX, CDN, grupos globais e painel por destino (Huawei, 2026-08-18)

**Arquivos:** `clientes/bgp_community_auto.py`, `clientes/backup_parser.py` (`parse_huawei`),
`clientes/bgp_views.py`, `clientes/templates/bgp_automacao.html`,
`clientes/tests_bgp_community_auto.py` (novo).

A versão anterior só enxergava circuitos `c-NN`. As caixas que já migraram para o template
otimizado usam **três famílias de circuito** e as **communities globais** — e nelas o sistema
mostrava menos da metade do que estava configurado (no acesso 20: 10 circuitos de 25).

### O que passou a ser reconhecido

| Estrutura | Community | Exemplo |
|---|---|---|
| `c-NN-<ação>` | `<asn>:50N<sufixo>` | operadora/upstream |
| `ix-NN-<ação>` | `<asn>:60N<sufixo>` | IX/PTT |
| `cdn-NN-<ação>` | `<asn>:61N<sufixo>` | CDN |
| `glob-all-upstream[-Np]` | `<asn>:6000N` | anunciar para **todos** os upstreams |
| `glob-all-ptts-ixbr[-Np]` | `<asn>:6001N` | idem, IX |
| `glob-all-cdns[-Np]` | `<asn>:6002N` | idem, CDNs |

O `id` do circuito passou a ser o próprio prefixo do filtro (`c-01`, `ix-05`, `cdn-02`) — é ele
que vai em `AcaoBgp.alvo` e no parâmetro `destino` da ação. O campo antigo `circuito` continua
aceito no `params` (`bgp_views._gerar_comandos_por_tipo`).

**Tipo do circuito:** vem do nome do filtro; quando a caixa é antiga e chama tudo de `c-NN`
(inclusive os IX — `c-81`..`c-83`), o tipo real vem do `glob-all-*` que a policy de saída daquele
circuito referencia. É informação da própria config, não heurística de nome.

**Alcance dos grupos globais:** um `glob-*` só faz alguma coisa se alguma policy de saída der
`if-match community-filter glob-all-…`. O mapa calcula esse alcance circuito a circuito, e
marcar um prefixo com uma community global sem alcance é **recusado** com essa explicação —
nas caixas antigas o bloco `glob-*` inteiro foi colado e nunca referenciado (config morta,
reportada em UM aviso consolidado).

### Efeito real × intenção (`efetivo`)

No VRP vale o **primeiro node que casa**. Como a global fica no node 12 e os prepends
individuais nos nodes 13-16, um prefixo marcado com `glob-all-upstream` **e** com
`c-01-export-2p` é anunciado **sem prepend** — o node individual nunca roda.

`mapear_anuncios` resolve isso: além de `destinos` (intenções individuais) e `globais`
(intenções "para todos"), cada linha traz `efetivo[<circuito>]` com o node vencedor, se
anuncia, quantos prepends, se leva no-export e quais filtros ficaram `ignorados`. É o que o
painel mostra na coluna "Efeito hoje" — a intenção declarada e o efeito real lado a lado.

### Correções no catálogo

- **`export-ne` virou `permit`** (era `deny`): "anunciar com no-export" é `permit` + `apply
  community no-export`; num node `deny` a rota não é anunciada e o `apply` nem roda. As caixas
  antigas têm esse `deny` e agora isso aparece como aviso ("o efeito é o contrário do rótulo").
- **Nodes na ordem do template** (§9/§18): bloqueio 9, blackhole 10, anúncio 11, **global 12**,
  prepends 13-16, no-export 17, `deny node 999` no fim. O preview sai em ordem de node.
- **`export-df` é legado** (§20, CORREÇÃO 4): continua sendo lida e manipulável onde já existe,
  mas não entra em config nova nem conta como "faltando".
- Provisionar um circuito também emite o `if-match community-filter glob-all-<tipo>` (node 12)
  quando esse filtro já existe na caixa — sem ele, "anunciar para todos" não alcança o circuito
  novo. O bloco `glob-*` em si nunca é criado por tabela: padronizar a caixa é decisão do
  operador, não efeito colateral de completar um circuito.

### Peer-group no `parse_huawei`

Os IX são configurados em grupo (`group EBGP-PTT-SP-V4 external` + N route servers), com as
route-policies **no grupo**, não no peer. O parser passou a mapear `peer <IP> group <NOME>` e a
herdar `route-policy … import/export` (e `ignore`/`undo … enable`) do grupo quando o peer não
tem a sua própria — é assim que o VRP se comporta. Sem isso, todo circuito de IX aparecia "sem
sessão BGP" e a automação se recusava a agir sobre ele. No acesso 20, os circuitos com sessão
vinculada foram de 3 para 7. As sessões agora também trazem `grupo`.

### Painel reorganizado (o pedido que originou a mudança)

A matriz prefixo × todos-os-circuitos deixou de ser a tela principal — com 25 circuitos ela era
uma parede de seletores. Agora são **dois passos**:

1. **Destinos** em cards agrupados por tipo (operadoras, IX, CDNs, "anunciar para todos"), cada
   um com grupo de community, ASN remoto, famílias, sessões, o que falta de config e quantos
   prefixos vão para lá hoje.
2. **Painel do destino** (clique no card): sessões BGP daquele circuito, como a policy de saída
   traduz cada community (`node → filtro`) e a lista de prefixos com busca, filtro por família,
   "só os que vão para cá", o efeito real de cada um e o seletor de como anunciar (agrupado em
   Anúncio / Com prepend / Especiais, só com as ações que aquele destino tem).

A matriz continua disponível no botão **⊞ Visão geral**, agora só leitura: `●` anunciado, `●N`
com N prepends, azul quando quem decide é a community global, `⊘` bloqueado; clicar numa coluna
abre o painel daquele circuito. Toda mudança continua passando pelo mesmo modal de
preview/edição/trial.

### Validado em 2026-08-18

Contra os 8 equipamentos Huawei reais que usam a convenção (acessos 20, 175, 324, 746, 826, 923,
990, 1216): **83 circuitos** descobertos (eram 67), matriz de 160 prefixos, 79 avisos (a
consolidação dos globais mortos e o silêncio nas ações legadas tiraram ~2/3 do ruído das
checagens novas). Geração de comandos conferida caso a caso pelo mesmo caminho da view
(`_gerar_comandos_por_tipo`), incluindo as recusas esperadas (global sem alcance, variante de
global que ninguém casa, circuito sem sessão, família errada, ação repetida). O bloco gerado
para um circuito IX novo sai idêntico ao template padrão. Nenhum comando real foi enviado a
equipamento. 22 testes novos em `clientes/tests_bgp_community_auto.py` (e os 36 já existentes
seguem passando).

Os snapshots em banco só passam a mostrar IX/CDN depois de reprocessados — "🔄 Atualizar agora"
na tela do host, ou a rotina noturna das 02:45.

---

**Última atualização:** 18/08/2026
---

## Subir circuito e sessão do zero — slots vagos e downstream (Huawei, 2026-08-18)

Até aqui a automação por community só sabia mexer no que a caixa **já tinha**:
completar o bloco de um circuito existente e mudar a intenção de um prefixo. O
passo que faltava é o começo — configurar a sessão. É o que esta parte faz:
clicar num slot vago (`c-02`, `ix-04`, `cdn-03`), preencher a sessão e sair com
tudo aplicado: community-filters, route-policy de entrada, route-policy de
saída e o bloco `bgp <ASN>`.

### O slot vago

O template reserva uma faixa por família (§6/§7/§8), e o grupo de community de
cada slot é uma **conta**, não um cadastro:

| Família   | Slots            | Grupo               | Community global |
|-----------|------------------|---------------------|------------------|
| `c-NN`    | c-01 … c-10      | `500 + NN` (501-510) | `<asn>:60001`   |
| `ix-NN`   | ix-01 … ix-10    | `600 + NN` (601-610) | `<asn>:60011`   |
| `cdn-NN`  | cdn-01 … cdn-05  | `610 + NN` (611-615) | `<asn>:60021`   |

`slot_padrao('c-02')` devolve `{tipo: upstream, numero: 2, grupo: '502'}` e
`mapear_slots()` devolve os que a caixa ainda não tem — que o painel mostra
como cards tracejados, na ordem do id, no meio dos circuitos que existem. O
ASN da community não é perguntado: sai de `_asn_community_prevalente()`, a
maioria entre os circuitos já configurados (nas caixas de referência é 65100
ou 65101 — ASN privado, diferente do ASN do `bgp <N>`).

### O que `comandos_criar_circuito` gera

Na ordem em que uma coisa depende da outra — nada é referenciado antes de
existir, e nada existente é reemitido:

1. **prefix-lists de apoio** (`BOGONS-*`, `FULL-ROUTING*`), só se faltarem;
2. **community-filters** do slot, mais o `glob-all-<tipo>` quando a caixa não
   o tem (sem ele o node de "anunciar para todos" seria config morta);
3. **route-policy de entrada** no layout do §9: bogons fora no node 5, tabela
   cheia aceita no node 10 com a community de origem (`<asn>:<grupo>00`) e a
   local-preference do papel, `deny node 999` no fim;
4. **route-policy de saída**: o mesmo `_blocos_policy_out` usado por "gerar
   config faltante" — bloqueio 9, blackhole 10, anúncio 11, global 12,
   prepends 13-16, no-export 17, `deny node 999`;
5. **a sessão**, no formato do fabricante conforme o papel (abaixo);
6. `commit`.

### Operadora/CDN x IX: dois arranjos de sessão

Operadora e CDN levam peer individual com as policies **no peer**:

```
peer 201.16.70.254 as-number 14840
peer 201.16.70.254 description EBGP-AS14840-BR-DIGITAL-V4
ipv4-family unicast
 peer 201.16.70.254 enable
 peer 201.16.70.254 route-policy AS14840-BR-DIGITAL-V4-IN import
 peer 201.16.70.254 route-policy AS14840-BR-DIGITAL-V4-OUT export
 peer 201.16.70.254 advertise-community
 peer 201.16.70.254 advertise-ext-community
```

IX leva peer-group com as policies **no grupo** e os route servers como
membros — que é como o IX.br é configurado nas caixas em produção e, não por
acaso, o que o parser consegue ler de volta (`parse_huawei` herda a policy do
grupo desde 18/08; antes disso todo circuito de IX aparecia "sem sessão"):

```
group EBGP-PTT-CE-V4 external
peer 45.68.79.253 as-number 26162
peer 45.68.79.253 group EBGP-PTT-CE-V4
peer 45.68.79.253 description RS1.PTT-CE
ipv4-family unicast
 peer EBGP-PTT-CE-V4 enable
 peer EBGP-PTT-CE-V4 public-as-only
 peer EBGP-PTT-CE-V4 route-policy AS26162-PTT-CE-V4-IN import
 peer EBGP-PTT-CE-V4 route-policy AS26162-PTT-CE-V4-OUT export
 peer 45.68.79.253 enable
 peer 45.68.79.253 group EBGP-PTT-CE-V4
```

**Detalhe de VRP que não pode faltar:** um peer nasce habilitado na
`ipv4-family unicast`, mesmo sendo IPv6. Por isso todo peer/grupo v6 gerado
leva um `undo peer … enable` na family v4 além do `enable` na v6 — é o que as
caixas em produção têm (`undo peer EBGP-PTT-SP-V6 enable`) e sem isso o peer
v6 fica pendurado na family errada.

### Bogons: a lista de `deny` não filtra nada

`if-match ip-prefix X` só casa quando **X permite** a rota. A `BOGONS-V4` do
template (§2) é escrita toda em `deny`, então ela nunca casa: o `deny node 5`
que a usa é config morta e os bogons seguem para o node 10, que aceita
`0.0.0.0/0 less-equal 24` — 10.0.0.0/8 incluído.

As caixas de referência já tinham resolvido isso com uma segunda lista, a
`BOGONS-V4-IN`, com as mesmas redes em `permit`. É essa forma que a geração
usa: `_prefix_list_bogons()` reaproveita uma lista de bogons da caixa **se ela
tiver entradas `permit`** e não permitir a tabela inteira; senão cria
`BOGONS-V4-IN`/`BOGONS-V6-IN` a partir do §2.

A recusa da lista que "permite tudo" não é teórica: a BOGONS de uma das caixas
tem `permit 0.0.0.0/0 greater-equal 25 less-equal 32` (prefixo longo demais, que
é bogon por tamanho). Usá-la num `deny node` derrubaria a sessão inteira, e ela
também enganava a detecção de full routing — daí `_e_lista_full_routing()`
exigir `len_min == 0`.

### Downstream: o cliente

Downstream não é circuito de community — para um cliente não se escolhe
"anunciar ou não o prefixo X". O desenho é o inverso do de um upstream:

- **saída**: o cliente recebe a tabela cheia (`if-match ip-prefix
  FULL-ROUTING`), `deny node 999` no fim;
- **entrada**: só passam os prefixos dele. Os blocos informados no formulário
  viram `PL-DOWNSTREAM-<NOME>-V4/V6` (com `greater-equal <len> less-equal 24`,
  /48 em IPv6, para o cliente poder desagregar) e é essa lista que a policy de
  entrada casa.

E o pulo do gato: **as communities de reanúncio entram na policy de ENTRADA.**
As policies de saída dos upstreams terminam em `deny node 999`, que só deixa
passar o que tem community de anúncio — sem carimbar as rotas do cliente na
entrada, elas ficariam presas no equipamento. Por isso o formulário do
downstream traz a mesma lista de destinos do "originar prefixo": escolher
"todos os upstreams" ali significa `apply community <asn>:60001 additive` na
policy de entrada do cliente.

`mapear_downstreams()` descobre os que já existem sem depender de nome: sessão
que não pertence a nenhum circuito do catálogo **e** cuja policy de saída
libera a tabela cheia (node `permit` sem `if-match`, ou casando uma lista de
full routing). iBGP fica de fora. IPv4 e IPv6 do mesmo cliente são agrupados
pelo nome da policy sem o sufixo de família — as três formas que aparecem nas
caixas (`-V4-IN`, `-IPv4_out`, `-IPV6-OUT`) caem no mesmo cliente. Nas 8 caixas
Huawei isso encontrou 11 clientes e 119 slots do template ainda vagos.

### Proteções

- **peer repetido**: IP já configurado no equipamento é recusado (erro de
  digitação, não intenção);
- **família errada**: IPv6 informado no campo de IPv4 é recusado;
- **nome que colide**: subir `ix-05` chamando de "PTT-SP" quando
  `AS26162-PTT-SP-V4-OUT` já é do `ix-01` faria os nodes do circuito novo
  entrarem na policy do antigo — `_recusar_colisao_de_nomes()` barra isso, e
  também o peer-group já em uso (aplicar nele trocaria as policies de uma
  sessão de IX no ar). Nenhum dos dois apareceria como erro no equipamento;
- **prefix-list/policy de downstream já existente**: recusada, porque pode
  pertencer a outra sessão;
- **subir desabilitada**: o checkbox "subir a sessão já habilitada" (marcado
  por padrão) controla o `peer … ignore`. Não basta omitir o `enable`: no VRP
  o peer nasce ativo, então a sessão subiria junto com a config. Desmarcado, a
  config entra completa e a sessão fica administrativamente parada até o botão
  Ativar do painel (`undo peer … ignore`) — o mesmo par de comandos que o resto
  da automação já usa.

Como toda ação desta automação, o preview é editável antes de aplicar e o
modo trial (`commit trial N`) continua disponível.

### Atualização otimista

Diferente de "gerar config faltante" (que só registra os community-filters),
aqui o snapshot local é atualizado com o circuito **inteiro** — filtros, nodes
da policy de saída e as sessões —, porque tudo isso foi gerado por este módulo
linha a linha. Sem isso o slot recém-criado continuaria aparecendo como vago
até o próximo backup, e o operador aplicaria tudo de novo.
