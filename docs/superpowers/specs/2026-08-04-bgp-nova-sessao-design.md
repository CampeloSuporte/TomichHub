# Automação BGP — Configurar nova sessão (Cisco)

**Data:** 2026-08-04
**Status:** Aprovado para planejamento
**Escopo:** Cisco/Datacom apenas (outros fabricantes ficam para uma iteração futura)
**Depende de / estende:** `docs/bgp_automacao.md` (arquitetura geral da automação BGP já em produção)

## Objetivo

Hoje a automação BGP (`clientes/bgp_actions.py`, `bgp_views.py`, `bgp_automacao.html`) só permite
agir sobre sessões BGP **que já existem** no equipamento (ativar/desativar, prepend, parar de
anunciar, anunciar prefixo novo, communities). Esta feature adiciona um botão **"＋ Configurar nova
sessão"**, fora dos cards de sessão, que monta e aplica a configuração completa de uma sessão BGP
**nova** (Cisco): `neighbor` + `remote-as` + `description` + ativação por address-family + route-map
IN/OUT (reaproveitando prefix-list existente ou criando uma nova).

## Fora de escopo

- Outros fabricantes (Mikrotik/Huawei/Juniper) — o botão só aparece quando `SNAPSHOT.vendor` é
  `cisco`/`datacom`.
- Criar o processo `router bgp <ASN>` do zero — assume-se que ele já existe no equipamento (o AS
  local é lido do snapshot, `dados['sessoes'][0].as_local`).
- Editar/remover uma sessão já criada por esta feature (isso já é coberto pelas ações existentes —
  ativar/desativar, etc. — assim que a sessão aparecer no snapshot).
- Prefix-list com mais de um CIDR (cada prefix-list nova criada por esta feature tem exatamente 1
  entrada, seq 10).
- Modo trial (commit temporário) — Cisco/Datacom já não suporta trial em nenhuma ação existente
  (`_TRIAL_SUPORTADO = {'huawei','juniper'}`), sem mudança aqui.

## Arquitetura

Nenhum app novo, nenhuma tabela nova no banco. Estende o padrão já estabelecido pelas ações
existentes (ver `docs/bgp_automacao.md`):

- `AcaoBgp.TIPOS` ganha `('criar_sessao', 'Criar sessão nova')`.
- `clientes/bgp_actions.py` ganha:
  - `comandos_criar_sessao(vendor, dados, params) -> list[str]` — gera os comandos (Cisco only;
    outro vendor levanta `AcaoBgpNaoSuportada`).
  - `buscar_prefix_lists_ao_vivo(acesso) -> {"prefix_lists": {...}, "policies": {...}}` — leitura SSH
    ao vivo (ver seção própria abaixo).
- `clientes/bgp_views.py::_montar_comandos` ganha o case `tipo == 'criar_sessao'` — reaproveita o
  endpoint já existente `POST /clientes/bgp/<id>/acao/` (`preview=true/false`), sem URL nova.
- `clientes/bgp_views.py::bgp_escanear_prefixo` (endpoint `POST /clientes/bgp/<id>/escanear-prefixo/`)
  ganha dois parâmetros novos no body, opcionais e retrocompatíveis:
  - `sessao` passa a aceitar um nome que **não existe** no snapshot (sessão ainda não foi criada) —
    nesse caso pula a marcação `ja_anunciando` (sempre `False`) em vez de dar erro.
  - `ao_vivo: true` — usa `buscar_prefix_lists_ao_vivo(acesso)` no lugar de
    `BgpSnapshot.dados['prefix_lists']`/`['policies']` como fonte pro `listar_prefix_lists`.
- `clientes/backup_parser.py::parse_cisco` — as regexes de prefix-list/route-map (linhas 344-433,
  ver `docs/bgp_automacao.md`) são extraídas para uma função auxiliar
  `_extrair_prefix_lists_e_policies_cisco(conteudo: str) -> tuple[dict, dict]`, chamada tanto por
  `parse_cisco` (texto de backup) quanto por `buscar_prefix_lists_ao_vivo` (texto vindo do `show
  running-config | section ...`) — zero regex duplicada entre os dois caminhos.

## Fonte de dados para prefix-lists existentes

Duas fontes, escolhidas pelo operador dentro do modal:

1. **Snapshot (padrão, ao abrir o modal)** — `BgpSnapshot.dados['prefix_lists']`/`['policies']`, o
   mesmo dado já usado por "Anunciar prefixo novo". Rápido, não conecta no equipamento, pode estar
   até 1 dia desatualizado (só é regravado pela rotina noturna ou por "Atualizar agora", que também
   só relê o backup em disco).
2. **Leitura ao vivo (botão "🔄 atualizar" dentro do modal)** — conecta via
   `clientes/script_views.py::_conectar_script(acesso, 'cisco')`, roda:
   - `show running-config | section prefix-list`
   - `show running-config | section route-map`

   Concatena a saída dos dois comandos e passa por
   `_extrair_prefix_lists_e_policies_cisco()` (mesma regex do parser de backup, já que o texto de
   `show running-config` usa exatamente a mesma sintaxe de configuração que aparece num backup). O
   resultado substitui, só na sessão do modal em memória (não grava em `BgpSnapshot`), a lista de
   candidatas mostrada nos dois dropdowns (IN e OUT). Fecha a conexão (`_fechar_tunel`) logo depois de
   ler — não fica uma sessão SSH aberta enquanto o operador preenche o resto do formulário.

   Falha de conexão (equipamento fora do ar, timeout) mostra erro no modal e mantém os dados do
   snapshot como fallback — nunca bloqueia o formulário.

## Convenção de nomenclatura (gerada automaticamente, editável no preview)

| Campo | Contexto | Padrão |
|---|---|---|
| Descrição | Upstream | `UPSTREAM-{sufixo}-V4` / `UPSTREAM-{sufixo}-V6` |
| Descrição | Downstream | `DOWNSTREAM-{sufixo}-V4` / `DOWNSTREAM-{sufixo}-V6` |
| Route-map | Qualquer | `RM-PEER-{sufixo}-V4-IN`, `RM-PEER-{sufixo}-V4-OUT`, `RM-PEER-{sufixo}-V6-IN`, `RM-PEER-{sufixo}-V6-OUT` |
| Prefix-list nova | Upstream, direção OUT (nosso prefixo anunciado ao upstream) | `PL-ORIGIN-{cidr_underscore}` |
| Prefix-list nova | Upstream, direção IN (algo aceito do upstream — raro; normalmente reaproveita `PL-DEFAULT-ROUTE` existente) | `PL-UPSTREAM-{sufixo}-{cidr_underscore}` |
| Prefix-list nova | Downstream, direção IN (prefixo próprio do cliente) | `PL-CLIENTE-{sufixo}-{cidr_underscore}` |
| Prefix-list nova | Downstream, direção OUT (nosso prefixo entregue ao cliente) | `PL-ORIGIN-{cidr_underscore}` |

`{sufixo}` é digitado pelo operador (ex.: `CONECT`), maiúsculo, sem espaço (validação simples:
`^[A-Z0-9\-]+$`, ou normalizado automaticamente — normaliza para maiúsculo e troca espaço por hífen,
não rejeita). `{cidr_underscore}` = CIDR digitado com `/` trocado por `_` (ex.: `45.169.6.0/24` →
`45.169.6.0_24`), mesmo padrão visto no exemplo real (`PL-ORIGIN-45.169.6.0_24`).

Prefixo `UPSTREAM-`/`DOWNSTREAM-` da descrição é **fixo**, não aparece como campo editável — só o
`{sufixo}` é digitado.

## Geração de comandos (`comandos_criar_sessao`, Cisco)

Para cada address-family marcada (IPv4 e/ou IPv6), com `peer_ip`, `remote_as`, `descricao` próprios
dessa AF:

```
router bgp {as_local}
 neighbor {peer_ip} remote-as {remote_as}
 neighbor {peer_ip} description {descricao}
!
address-family {ipv4|ipv6}
 neighbor {peer_ip} activate
 neighbor {peer_ip} send-community both
 neighbor {peer_ip} route-map {rm_in} in
 neighbor {peer_ip} route-map {rm_out} out
exit-address-family
```

Em seguida, para cada direção (IN, OUT) em que o operador escolheu **criar prefix-list nova** (em vez
de reaproveitar uma existente):

```
{ip|ipv6} prefix-list {pl} seq 10 permit {cidr}
route-map {rm} permit 10
 match {ip|ipv6} address prefix-list {pl}
```

Quando o operador reaproveita uma prefix-list existente, só a linha `match ... prefix-list {pl}`
dentro do `route-map {rm} permit 10` é gerada (a prefix-list em si não é tocada — mesmo princípio já
usado em "Anunciar prefixo novo" e "Parar de anunciar": nunca editar um objeto compartilhado).

Isso reproduz exatamente a estrutura do exemplo fornecido (`router bgp 268080` / dois neighbors V4+V6
/ duas address-families / dois route-maps novos, um deles com prefix-list nova `PL-ORIGIN-...`, o
outro reaproveitando `PL-DEFAULT-ROUTE` existente).

## Validações

- Pelo menos uma address-family (IPv4 e/ou IPv6) marcada — senão erro 400 antes de gerar comandos.
- Upstream/Downstream é seleção única (toggle, não duas checkboxes independentes).
- CIDR validado como IPv4 ou IPv6 conforme a AF em questão (`ipaddress.ip_network(strict=True)`).
- Recusa com `AcaoBgpNaoSuportada` (mesmo padrão das outras ações — vira erro 422 na view) se:
  - `peer_ip` já existir como neighbor no snapshot/leitura em uso.
  - nome de route-map ou de prefix-list gerado já existir no snapshot/leitura em uso.
- Vendor diferente de `cisco`/`datacom` → `AcaoBgpNaoSuportada` na função de comando (defesa em
  profundidade; o botão já não aparece nesse caso na tela, mas a view não deve confiar só no
  frontend).
- Permissão: staff/superuser, igual ao resto da página (`bgp_views.py` já reforça isso em todo
  endpoint existente).

## Auditoria + atualização otimista do painel

- `AcaoBgp.objects.create(acesso=..., usuario=..., tipo='criar_sessao', alvo=peer_ip, comandos=...,
  output=..., status=...)` — **um registro por AF criada** (IPv4 e IPv6 têm `peer_ip` diferentes, então
  cada um vira uma linha de auditoria própria com seu `alvo`), mesmo que as duas tenham sido geradas e
  confirmadas no mesmo clique do modal.
- `clientes/bgp_actions.py::aplicar_efeito_localmente` ganha o case `tipo == 'criar_sessao'`: insere
  a(s) sessão(ões) nova(s) em `dados['sessoes']` (`peer_ip`, `peer_as`, `as_local`, `nome=peer_ip`,
  `descricao`, `habilitada=True`, `policy_in`, `policy_out`), mais as prefix-lists/policies novas
  criadas (quando aplicável) em `dados['prefix_lists']`/`dados['policies']` — mesmo princípio das
  outras ações: o painel reflete o resultado esperado imediatamente, sem esperar o próximo backup.
  Recalcula `dados['anuncios']` via `simular_anuncios` no final, igual às outras ações.

## Frontend (`bgp_automacao.html`)

- Botão `#btnConfigurarSessao` no `.header`, ao lado de `#btnAtualizar`, renderizado só quando
  `SNAPSHOT.vendor` é `cisco`/`datacom`.
- Modal novo `#criarSessaoOverlay` (mesmo padrão visual/JS dos outros modais da página):
  1. Toggle **Upstream / Downstream** (mutuamente exclusivo).
  2. Toggle **IPv4 / IPv6** (pode marcar os dois — cada um revela seu próprio bloco de campos).
  3. Campo **sufixo** (texto livre, vira parte da descrição/route-map/prefix-list).
  4. Por AF marcada: campo **peer IP**, campo **AS remoto**, e dois widgets **IN**/**OUT**, cada um
     com: dropdown buscável de prefix-list existente (com botão "🔄 atualizar" pra forçar leitura ao
     vivo, ver seção acima) **OU** opção "criar nova" com campo de CIDR.
  5. Botão **Visualizar comandos** → chama o endpoint de ação com `preview=true` → mostra o resultado
     num `<textarea>` editável (mesmo padrão de todas as outras ações).
  6. Botão **Executar** → `preview=false`, manda o conteúdo atual do textarea (permitindo ajuste
     manual antes de aplicar, igual ao resto da página).

## Testes

- Teste unitário de `comandos_criar_sessao` reproduzindo o **exemplo real fornecido pelo usuário**
  (upstream CONECT, V4+V6, prefix-list nova `PL-ORIGIN-45.169.6.0_24` no OUT e reaproveitamento de
  `PL-DEFAULT-ROUTE` existente no IN) como caso de regressão — comparar linha a linha com o texto
  original.
- Teste unitário de `_extrair_prefix_lists_e_policies_cisco` com texto de `show running-config |
  section ...` real (formato ligeiramente diferente de um backup completo — validar que ainda
  casa com a mesma regex).
- Teste de validação: peer IP duplicado, nome de route-map/prefix-list colidente, CIDR inválido,
  nenhuma AF marcada — cada um deve recusar sem gerar comando nenhum.
- Verificação manual (não automatizável sem um Cisco real) do fluxo ao vivo (`buscar_prefix_lists_ao_vivo`)
  contra pelo menos um `Acesso` Cisco de teste antes de liberar em produção — mesmo rigor de
  "validar contra dado real" já seguido no resto da automação BGP (ver `docs/bgp_automacao.md`).

## Documentação

Ao concluir a implementação, adicionar uma seção nova em `docs/bgp_automacao.md` (mesmo padrão das
seções "adicionado em ..." já existentes nesse arquivo) descrevendo a feature, em vez de manter só
neste spec — o spec é o documento de design pré-implementação; `docs/bgp_automacao.md` é a
documentação viva do módulo.
