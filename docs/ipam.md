# IPAM — Documentação Técnica

**Arquivo principal:** `clientes/ipam_views.py`  
**Models:** `IPAMVlan`, `IPAMPrefixo`, `IPAMSubRede`, `IPAMEndereco`, `IPAMVpnDoc`  
**Atualizado em:** 2026-08-28

---

## Visão Geral

O IPAM (IP Address Management) nativo do CRM organiza endereços IP em quatro níveis hierárquicos:

```
IPAMPrefixo (container /24 ou bloco maior)
  └── IPAMSubRede  (ex: /30, /29 — sub-rede real)
        └── IPAMEndereco  (host individual, ex: 192.168.1.1/30)

IPAMVlan  →  associada a IPAMSubRede
```

---

## Estrutura de URLs

Todas as rotas são prefixadas com `/clientes/<cliente_id>/ipam/`.

| Método | Rota                        | View                      | Descrição                         |
|--------|-----------------------------|---------------------------|-----------------------------------|
| GET    | `vlans/`                    | `ipam_vlans_listar`       | Lista VLANs do cliente            |
| POST   | `vlans/salvar/`             | `ipam_vlan_salvar`        | Cria ou atualiza VLAN             |
| POST   | `vlans/<id>/deletar/`       | `ipam_vlan_deletar`       | Remove VLAN                       |
| GET    | `prefixos/`                 | `ipam_prefixos_listar`    | Lista prefixos                    |
| POST   | `prefixos/salvar/`          | `ipam_prefixo_salvar`     | Cria ou atualiza prefixo          |
| POST   | `prefixos/<id>/deletar/`    | `ipam_prefixo_deletar`    | Remove prefixo                    |
| GET    | `subredes/`                 | `ipam_subredes_listar`    | Lista sub-redes (filtrável)       |
| POST   | `subredes/salvar/`          | `ipam_subrede_salvar`     | Cria ou atualiza sub-rede         |
| POST   | `subredes/<id>/deletar/`    | `ipam_subrede_deletar`    | Remove sub-rede                   |
| GET    | `ips/`                      | `ipam_ips_listar`         | Lista endereços (busca e filtro)  |
| POST   | `ips/salvar/`               | `ipam_ip_salvar`          | Cria ou atualiza endereço         |
| POST   | `ips/<id>/deletar/`         | `ipam_ip_deletar`         | Remove endereço                   |
| POST   | `importar/`                 | `ipam_importar`           | Importa IPs dos Acessos           |

---

## Importação Automática de Acessos

A view `ipam_importar` percorre todos os `Acesso` do cliente que possuam `host` ou `host_ipv6`
no formato CIDR (ex: `192.168.1.1/30`) e cria automaticamente a hierarquia:

```
Acesso.host (CIDR)
  → IPAMPrefixo container (/24)
  → IPAMSubRede para o bloco /24 pai   ← novo em 2026-05-26
  → IPAMSubRede com CIDR real (/30, etc.)
  → IPAMEndereco (host individual)
```

### Função `_get_or_create_prefixo_pai` — Alteração de 2026-05-26

**Arquivo:** `clientes/ipam_views.py`, linha ~1831

**Comportamento anterior:**  
Criava apenas um `IPAMPrefixo` do tipo `container` para o bloco /24. O bloco /24 aparecia
somente na aba de Prefixos; a aba de Sub-redes só exibia os CIDRs reais (/30, /29 etc.).

**Comportamento atual:**  
Além do `IPAMPrefixo`, cria também um `IPAMSubRede` para o bloco /24 pai com:
- `status = 'reservado'`
- `descricao = 'Bloco <cidr> (auto)'`
- `prefixo` vinculado ao `IPAMPrefixo` correspondente

**Resultado visível:**  
Os blocos /24 passam a aparecer nas duas abas — Prefixos (como container) e Sub-redes
(como sub-rede reservada) — facilitando a navegação e visualização da ocupação do bloco.

**Código simplificado:**

```python
def _get_or_create_prefixo_pai(net):
    # Calcula /24 pai (ou /48 para IPv6)
    preflen = min(net.prefixlen, 24)  # 48 para IPv6
    pai_net = net.supernet(new_prefix=preflen) if net.prefixlen > preflen else net
    cidr_pai = str(pai_net)

    # Cria IPAMPrefixo container
    obj, created = IPAMPrefixo.objects.get_or_create(
        cliente=c, prefixo=cidr_pai,
        defaults={'tipo': 'container', 'descricao': f'Bloco {cidr_pai} (auto)'}
    )

    # NOVO: cria também IPAMSubRede para o /24 aparecer na aba Sub-redes
    sub_pai, sub_criada = IPAMSubRede.objects.get_or_create(
        cliente=c, rede=cidr_pai,
        defaults={
            'prefixo': obj,
            'descricao': f'Bloco {cidr_pai} (auto)',
            'status': 'reservado',
        }
    )
    return obj, created
```

---

## Agrupamento por Prefixo

A listagem de Sub-redes aceita o parâmetro `?prefixo_id=<id>` para filtrar por bloco pai.  
O frontend utiliza o select `#ipam-sr-filtro-prefixo` para acionar este filtro.

## Busca de Endereços

A listagem de IPs aceita `?q=<texto>` para busca por IP ou hostname e
`?subrede_id=<id>` para filtrar por sub-rede específica. A busca tem debounce de 350 ms
no frontend (`ipamBuscarIPs`).

---

## Sub-rede como pasta — criar filhas de /25 a /31 (2026-08-13)

Antes, só o `IPAMPrefixo` (container) tinha o botão **+** e o seletor visual de blocos:
dentro de uma sub-rede /24 o único caminho era a tesoura (**Dividir**), que cria *todos*
os blocos do tamanho escolhido de uma vez. Agora cada sub-rede que ainda cabe subdividir
é uma pasta na árvore e ganha o **+** pra criar **uma** filha, escolhendo o bloco na mão.

**Backend** (`clientes/ipam_views.py`):

- `_subdivisoes_payload(cliente, parent_net, prefixlen_raw, excluir_pai=False, limite_pl=None)`
  — o cálculo de blocos livre/em uso/cheia/parcial saiu de `ipam_prefixo_subdivisoes` pra
  esta função, compartilhada pelas duas entradas.
- `?prefixlen=todos` (**padrão** nas duas rotas) devolve `grupos` com a divisão **completa**:
  num /24 são os 2 `/25`, os 4 `/26`, … até os 128 `/31`, todos marcados, numa resposta só —
  assim não é preciso trocar de máscara no dropdown pra achar o bloco. Um prefixlen numérico
  (`?prefixlen=26`) devolve `blocos` só daquele tamanho, como antes. `_alocadas_no_bloco` e
  `_marcar_blocos` foram extraídas pra servir os dois modos sem repetir consulta.
  Teto de `SUBDIVISOES_TODOS_LIMITE = 3000` blocos somados: num bloco grande (ex: /12) as
  máscaras finais são milhares de blocos, então param de entrar na lista e a resposta vem com
  `truncado: true` (o dropdown continua alcançando cada tamanho individualmente).
- `ipam_subrede_subdivisoes` (`GET ipam/subredes/<id>/subdivisoes/?prefixlen=<pl>`) — usa a
  sub-rede como bloco pai. Dois detalhes que **não** valem pro caminho de prefixo:
  - `excluir_pai=True`: a sub-rede pai está cadastrada em `IPAMSubRede`, então sem isso ela
    apareceria como ocupante de todos os blocos filhos e o /24 inteiro vinha como `parcial`
    contra si próprio.
  - `limite_pl = max_pl - 1` (**/31** em IPv4, /127 em IPv6): /32 é host individual, e host
    é `IPAMEndereco`, não sub-rede.
- Ambas as views agora devolvem `prefixo_id` no payload, pro modal de criação já vir com o
  prefixo dono pré-selecionado.

**Frontend** (`clientes/templates/listar.html`):

- `ipamAbrirSubdivisoes(id, cidr, pfxLen, version, tipo)` — ganhou `tipo`
  (`'prefixo'` | `'subrede'`), que escolhe a rota e o teto de máscara.
- **A escolha do bloco é uma lista em linhas (`role="listbox"`), não um grid de cartões nem um
  `<select>` nativo.** Cada linha é `ponto de status · CIDR · faixa de IPs · etiqueta`, e cada
  máscara tem um cabeçalho *sticky* com contagem, hosts e um medidor de livres
  (`/25 · 2 bloco(s) · 126 hosts · [▁▁] 1/2 livres`). Os cartões saíram porque num /24 são 254
  blocos — em grid virava parede ilegível e estourava a largura; o `<select>` nativo saiu
  porque `<option>` não aceita layout (nem faixa de IPs, nem medidor, nem etiqueta).
  `IPAM._subdiv` guarda `{blocos, grupos, sel, busca, mask, soLivres, pfxId, srPai}` — a linha
  só carrega o CIDR em `data-rede`, o resto vem do estado na confirmação.
- Filtros no lugar do dropdown de tamanho: chips de máscara (`todas · /25 … /31`), toggle
  **só livres** e busca por CIDR. `ipamSubdivSelecionarRede()` mexe só nos dois nós que mudam
  (não re-renderiza as 254 linhas, senão o scroll salta a cada seta).
- Teclado: `↑ ↓ PageUp PageDown Home End` navegam, `Enter` confirma, `Esc` fecha; o handler
  está no card inteiro pra as setas funcionarem com o cursor no campo de busca (padrão de
  command palette), com exceção de Home/End dentro do input, que continuam sendo do cursor.
- Movimento: entrada do modal 200 ms `cubic-bezier(.23,1,.32,1)` de `scale(.97)` (nunca de
  `scale(0)`), saída em 150 ms, `:active` com `scale(.97)` nos botões, hover atrás de
  `@media (hover:hover) and (pointer:fine)` e **nenhuma** transição na linha selecionada —
  seleção vem de tecla repetida e precisa ser instantânea. Bloco `prefers-reduced-motion`
  mantém o fade e corta escala/deslocamento.
- `_renderSrLeafRow`: o traço (`fa-minus`) virou ícone de pasta (`fa-folder` /
  `fa-folder-open`, clicável) em toda sub-rede com `prefixlen < max-1`; /31 e /32 seguem com
  o traço, porque não têm o que aninhar.
- **Estado de expansão saiu do DOM.** Era `data-open` + `_srSetDescendants()` varrendo
  `[data-sr-parent]`; agora é `IPAM._srOpen[id]` + `_srAncestraisAbertos()`. Motivo: a árvore
  é re-renderizada inteira a cada mudança, então criar uma /25 dentro de uma /24 fechava a
  pasta e a linha nova "não aparecia". `IPAM._srGradeOpen` passou a ser zerado em
  `ipamCarregarPrefixos()` pelo mesmo motivo (as linhas de grade morrem no re-render).

### IPv6 no seletor de blocos (2026-08-13)

"Divisão completa" não existe em IPv6 — um /32 tem 65.536 /48 e 4 bilhões de /64. O que mudou
pra a mesma feature valer nas duas famílias:

- **Máscaras oferecidas** (`_mascaras_candidatas`): IPv4 segue `pai+1 … pai+9`; IPv6 vai de
  nibble em nibble e **para no /64** (`V6_MASCARA_MINIMA`). Um `/32` oferece
  `/36 /40 /44 /48 /52 /56 /60 /64`; um `/44` oferece `/48 /52 /56 /60 /64`; um `/64` não
  oferece nada. Ninguém corta um /32 IPv6 em /33, e abaixo de /64 (p2p `/126`, loopback
  `/128`) o endereçamento é escolhido a dedo — **entra digitando o CIDR em "Nova Sub-rede"**,
  não por lista, que teria 2^62 blocos. O teto vale nos três caminhos: seletor de blocos,
  `?prefixlen=` explícito e tesoura "Dividir" (`_checar_limite_divisao`), cada um com a
  mensagem apontando pro cadastro manual.
- **Amostragem** (`_indices_amostra` + `_marcar_blocos(indices=…)`): acima de
  `SUBDIVISOES_LISTA_CHEIA` (512) a máscara vira amostra montada por aritmética de índice —
  percorrer `subnets()` seria 2^32 iterações. A amostra prioriza **alocação exata na máscara →
  vizinho seguinte (o próximo livre) → alocações mais específicas → começo do bloco**.
  Alocações mais *amplas* que a máscara de propósito não viram âncora: com um /36 alocado e a
  lista em /64, ancorar nele só produzia fileiras de "parcial" separadas por
  "⋯ 224 omitidos" (434 marcadores de salto numa tela — virou 1).
  Blocos pulados marcam `salto: N`, que a UI mostra como `⋯ N bloco(s) omitido(s)`.
- **Números**: `total` só vai no JSON quando cabe (`≤ SUBDIVISOES_LIMITE`); o resto usa
  `total_label` com `2^N` (`_num_curto`), e o cabeçalho vira `32 de 2^82 bloco(s)`.
- **Teto de máscara em IPv4**: subiu de `/31` pra `/32`. Na teoria host é `IPAMEndereco`, mas o
  cadastro tem **1.170 /32** (loopbacks de equipamento) — cortar em /31 impedia justamente o
  caso mais comum. Na árvore, `podeDividir = pfxLen < (v6 ? 64 : 32)` controla o `+` e a
  tesoura; o ícone de pasta usa `podeDividir || hasChildren`, senão um /64 com /126 cadastrado
  na mão apareceria como folha tendo filha.
- **Front**: `_bigIntParaIpv6` comprime o endereço (RFC 5952) e `_subdivFaixa` mostra
  `início – fim` só quando o bloco tem ≤ 65.536 endereços; acima disso o fim é sempre
  `…ffff:ffff` e não informa nada, então fica só o endereço de rede.
- **Guarda-corpo do "Dividir"** (`_checar_limite_divisao`, `DIVIDIR_LIMITE = 4096`): dividir um
  /32 IPv6 em /48 eram 65.536 `INSERT` num clique — hoje devolve 400 com a saída ("use o +",
  ou "criar apenas o primeiro bloco"). Vale pras duas rotas de dividir, prefixo e sub-rede.

---

## Seletor de "Blocos livres" e árvore que não recarrega (2026-08-28)

Duas coisas na aba **Documentação de Rede** que quebravam em cadastro grande.

### 1. "Livres" mostrava só uma fatia do prefixo — e o menu ficava cortado

`ipam_prefixo_disponiveis` (o botão **Livres** do modal Sub-rede) tinha dois tetos herdados de
quando o cadastro era pequeno: `DISPONIVEIS_TAMANHOS_MAX = 6` (só as 6 primeiras máscaras) e
`DISPONIVEIS_LIMITE_POR_PL = 40` (40 blocos por máscara). Num `/16` isso significava a lista ir
de `/17` a `/22` e **um `/24` simplesmente não existir como opção** — justamente o tamanho que
se cadastra o dia inteiro. No front, o painel era um `position:absolute` de 260 px dentro de um
modal com `overflow-y:auto`: a lista era **cortada na borda do modal**, e é isso que aparecia
como "menu bugado".

O que passou a valer:

- **Máscaras completas** (`_mascaras_disponiveis`): IPv4 vai de `pai+1` até `/32`. IPv6 anda de
  nibble até `/64` e ainda oferece `/112 /126 /127 /128`, as únicas abaixo de /64 que aparecem
  de verdade no cadastro (p2p e loopback). Diferente de `_mascaras_candidatas`, que para em
  `pai+9` porque lá o assunto é *subdividir*, não *escolher um bloco*.
- **Dois modos na mesma rota.** Sem `?prefixlen`, devolve os **gaps** — os maiores blocos livres
  alinhados, que já são a resposta exata e curta — mais `tamanhos[]`, a contagem de blocos
  livres por máscara (`_livres_contagem`: soma `2^(pl - gap.prefixlen)`, sem enumerar nada).
  Com `?prefixlen=N`, devolve uma **página** de `DISPONIVEIS_PAGINA = 240` blocos.
- **Paginação por aritmética** (`_livres_pagina`): o salto até o `offset` é
  `base + i * passo`, então pedir `?prefixlen=30&offset=100000` num `/12` custa o mesmo que
  pedir a primeira página. Enumerar era o que travava — um `/12` tem 260.836 `/30` livres.
- **Busca** (`?q=`, `_livres_busca`): filtro textual não tem como pular, então a varredura tem
  teto (`DISPONIVEIS_BUSCA_SCAN = 20000`) e devolve `busca_truncada`, que a UI mostra como
  "busca parou no limite de varredura". Melhor do que uma lista parcial se passando por completa.
- **Prefixos filhos passaram a ocupar espaço** (`_ocupadas_no_prefixo`). Antes só `IPAMSubRede`
  contava: um `/24` cadastrado como **prefixo** dentro do `/16` era oferecido como bloco livre
  do `/16`. Igualdade fica de fora de propósito — um prefixo duplicado com o mesmo CIDR zeraria
  o espaço livre do pai.
- **Contagem legível** (`_num_blocos`): número cheio até o bilhão, `2^N` acima. `_num_curto`
  corta em 65.536, o que em IPv4 é cedo demais — "2^20" no lugar de "1.048.576 blocos /30" não
  ajuda a decidir nada. `total` cru só vai no JSON quando cabe em número JS exato (`≤ 2^53`).

No front (`clientes/templates/listar.html`), o dropdown virou **painel embutido de largura
total** (`.livres-painel`, `col-12` do formulário) e o modal de Sub-rede abre em **1040 px** em
vez de 720 — é o único formulário com o seletor dentro, e em 720 px a grade ficava com 3 colunas
e muito scroll. O painel tem cabeçalho com o prefixo e o total livre, chip por máscara com a
contagem (`buracos · /13 · /14 … /32`), busca, grade responsiva
(`repeat(auto-fill, minmax(168px, 1fr))`) e **carregar mais**. Estado em `IPAM._livres`;
`_livresRender()` guarda foco e cursor da busca antes de trocar o `innerHTML`, senão digitar o
segundo caractere era impossível.

### 2. Clicar na pasta recarregava a árvore e jogava a página pro topo

`ipamToggleBreakdown`, `ipamToggleArvorePrefixo` e `ipamToggleSrChildren` chamavam
`ipamCarregarPrefixos()` — `fetch` + `innerHTML` da tabela inteira. Com a página rolada, abrir
uma pasta lá embaixo devolvia o usuário pro topo e ele tinha que rolar de novo até o ponto onde
clicou.

As linhas de sub-rede **já estão no DOM desde o render** (pasta fechada nasce com
`display:none`, via o parâmetro `oculta` de `_renderSrLeafRow`), então abrir é reavaliar
visibilidade, não redesenhar:

- `ipamAplicarVisibilidade()` percorre `IPAM.prefixos` + `_srAgruparPorPrefixo()` e mexe **só em
  `style.display`** de `#pfx-row-*`, `#sr-row-*` e `#sr-grade-*`, trocando de passagem os ícones
  `#pfx-caret-*`, `#ipam-bd-icon-*` e `#sr-folder-*` (ids novos). Zero requisição, zero salto.
- `ipamFiltrarSubredes` (clique no número de sub-redes) também virou DOM puro.
  `ipamSubdivVerOcupante` **continua** redesenhando: ali `ipamCarregarSubRedes()` acabou de
  trocar o cache, e as linhas no DOM podem estar desatualizadas.
- Quando o redesenho é mesmo necessário (criar/editar/excluir), `ipamCarregarPrefixos()` guarda
  `window.scrollY` e devolve depois de remontar a tabela; e o spinner só entra quando ainda não
  há árvore na tela — trocar uma tabela cheia por uma linha de "carregando" encolhe a página e
  desloca tudo antes mesmo da resposta chegar.

---

## Auto-documentação por backup — descrição de loopback (2026-08-31)

Quem preenche o IPAM a partir dos backups é a dupla
`clientes/ipam_views.py::ipam_analisar_backups` (botão "Analisar backups", por cliente) e
`clientes/tasks.py::analisar_backups_ipam` (Celery, a cada 3 dias). Ambas usam os mesmos
parsers — `_parse_mikrotik`, `_parse_huawei`, `_parse_generic` — que devolvem
`(ip_cidr, descricao, vlan)` por endereço encontrado.

### O problema

A descrição de cada IP saía da `description` (Huawei/Cisco) ou do `comment=` (MikroTik) da
interface. Quando a interface não tinha nenhuma, o fallback era o **nome da interface**:

| Backup                                            | Descrição gravada antes |
|---------------------------------------------------|-------------------------|
| `interface LoopBack0` + `ip address 198.19.255.0 255.255.255.255` | `LoopBack0`   |
| `/ip address add address=10.255.255.1 interface=bridge2-LOOPBACK` | *(vazia)*     |

Um loopback é justamente o IP que **identifica o equipamento** — e `LoopBack0` repetido em 700
linhas do IPAM não diz de qual equipamento se trata.

### A regra

Endereço **/32 (IPv4) ou /128 (IPv6)** em cima de interface de loopback e **sem descrição na
interface** → a descrição passa a ser o **nome do host declarado no próprio backup**
(`_hostname_do_backup`: `/system identity set name=` no MikroTik, `sysname` no Huawei,
`set system host-name` no Juniper, `hostname` no Cisco/Parks).

- `_eh_interface_loopback` reconhece `LoopBack0`, `loopback0`, `lo`, `lo0`, `lo0.0` e qualquer
  nome que contenha "loopback" — inclusive `bridge2-LOOPBACK`, o padrão MikroTik, que não tem
  interface de loopback nativa.
- Endereço de loopback com máscara diferente de /32|/128 continua com o comportamento antigo —
  existe de verdade (`LoopBack0` com /30 em ATN Huawei) e não é o caso da regra.
- Se o backup não declara hostname nenhum (Hillstone, por exemplo), as duas rotinas caem no nome
  do equipamento cadastrado no CRM (`Acesso.tipo`) — assim nenhum loopback /32 fica descrito
  como "LoopBack0".
- A interface **com** `description`/`comment` nunca é sobrescrita.

### Rodadas anteriores

Descrição já gravada só é substituída quando está vazia **ou** quando é o nome cru da interface
deixado por uma rodada antiga — `_descricao_e_so_nome_de_loopback`, que casa apenas
`^(loopback|lo)\d*(\.\d+)?$`. `LOOPBACK - INTERNEXA` ou `Loopback MPLS` foi alguém que
escreveu e fica intacto. Na base atual isso reclassifica 708 IPs (`LoopBack0` → `BDR-SINOP`,
`SW3-PE-CNZ-CEN-01`, …) e documenta 81 loopbacks novos.

### Efeito colateral corrigido junto

O parser MikroTik só lia `comment="entre aspas"`. O `.rsc` grava sem aspas quando o comentário
não tem espaço (`comment=MKAUTH`, `comment=BGP`, `comment=LOOPBACK`), e essas descrições eram
descartadas — agora as duas formas são lidas. Sem isso, um loopback comentado passaria por
"sem descrição" e receberia o nome do host à toa.

O mesmo valia para `interface=` — só o formato com aspas era lido, e o nome sem aspas
(`interface=loopback`, `interface=vlan3010-WAN`) chegava vazio ao parser. Além do loopback, isso
devolve a detecção de VLAN pelo nome da interface: 818 endereços que estavam sem VLAN passam a
ficar vinculados à VLAN certa.

### Nome de VLAN estourando o campo

`IPAMVlan.nome` é `varchar(100)`; o MikroTik aceita nome de interface bem maior. Um
`/interface vlan add name="vlan1152 - L(83)PacPon Riacho de Areia, Faz. …"` de 270 caracteres
estourava `DataError: value too long` no meio de `analisar_backups_ipam` — que não isola
cliente por cliente, então tudo o que viria depois daquele acesso ficava sem documentar.
`_parse_mikrotik` corta o nome em 100.

> Um caso real do porquê da regra: `CGNAT-BORDA` tem 512 `/32` públicos pendurados na bridge
> `loopback`, todos sem `comment` — os 512 eram documentados com descrição vazia e agora
> carregam `BORDA_CGNAT_FRIENDS_150`.

---

## Prefixo filho aparecia no bloco errado da árvore (2026-09-02)

O `100.64.1.0/24` (prefixo, tipo container, filho do `100.64.0.0/10`) era desenhado logo abaixo do
`100.120.0.0/13` — dois blocos que não têm relação nenhuma. O `pai_id` no banco estava certo
(`_computar_pai_id` acha o prefixo mais específico que contém o novo); o problema era **só o
desenho**: `ipamCarregarPrefixos()` montava a tabela em duas hierarquias paralelas.

### O que acontecia

A ordem das linhas era `_ordenarArvorePrefixos()` (DFS de prefixo por `pai_id`) e, para cada
prefixo, **a linha dele + a árvore inteira de sub-redes da pasta**. Um prefixo filho só entrava
depois de tudo isso, com indentação de "filho do pai" — ou seja, o `/24` saía no fim da lista de
sub-redes do `/10`, logo abaixo da última (`100.124.0.0/14`, dentro do `/13`), como se pertencesse
a ela. Sub-rede aninhava em sub-rede e prefixo aninhava em prefixo, mas **prefixo nunca aninhava em
sub-rede** — e é exatamente esse o caso aqui: o `100.64.1.0/24` mora dentro da sub-rede
`100.64.0.0/12`.

### Como ficou

`_pfxBloco(p, nivel, escondida)` (recursiva, substitui `_ordenarArvorePrefixos`) monta a linha do
prefixo e o conteúdo da pasta numa árvore só:

- **Âncora**: cada prefixo filho procura a sub-rede **mais específica da pasta do pai que o
  contém** (`_srContains`). Achou → é renderizado como filho dela, no nível
  `nivel + _depth da âncora + 2`; o par vai para `IPAM._pfxAncora[filho] = {sr, pasta}`.
- **Sem âncora** → continua filho direto do prefixo pai (nível `nivel + 1`), como antes.
- **Ordem**: dentro de cada nível, sub-redes e prefixos ancorados saem misturados e ordenados por
  CIDR (`_cmpCidrStr` — endereço asc, máscara asc), então um `/24` cadastrado como prefixo vem
  antes do `/25` que mora dentro dele, e um filho sem âncora entra na sequência numérica das
  sub-redes raiz em vez de ir para o fim.
- **Pasta da âncora**: `ancora._pfxFilhos` conta o filho, e `_renderSrLeafRow` usa
  `_childCount + _pfxFilhos` — sem isso a sub-rede nasceria como folha e não daria para expandir o
  prefixo pendurado nela.
- **Visibilidade**: `ipamAplicarVisibilidade()` ganhou `_pfxOculto()` (memoizada, corta ciclo de
  `pai_id`). Um prefixo ancorado só aparece se nenhum ancestral estiver colapsado **e** a pasta do
  pai estiver aberta **e** a sub-rede âncora estiver expandida — mesma regra das sub-redes filhas.
  Sem âncora, vale a regra antiga (só o colapso dos ancestrais).

A ordenação por `nivel` que vem do backend (`ipam_prefixos_listar`) deixou de ser usada no desenho
— o nível agora sai da recursão, porque depende da profundidade da sub-rede âncora. O campo
continua no JSON.

### Segunda camada: a sub-rede do próprio filho ficava na pasta do avô

Com o filho já ancorado, o `100.64.1.0/24` (prefixo) aparecia **logo abaixo da sub-rede
`100.64.1.0/24`**, de mesmo CIDR — parecia estar dentro dela. A causa está em
`_srAgruparPorPrefixo()`: a pasta era decidida **só na raiz** de cada árvore de sub-redes
(`curPfxId` só mudava em `_depth === 0`) e toda a descendência ia junto, ignorando o
`prefixo_id` de cada uma. Como o bloco é cadastrado **duas vezes** — um `IPAMPrefixo` e um
`IPAMSubRede` de mesmo CIDR, ver `_get_or_create_prefixo_pai` —, a sub-rede `100.64.1.0/24`
(FK → prefixo `871`) era arrastada para a pasta do `100.64.0.0/10` junto com a raiz
`100.64.0.0/12`, deixando o prefixo `871` com a pasta vazia e a linha solta.

Agora o dono de cada sub-rede é decidido nó a nó:

1. **FK explícito manda** — `prefixo_id` apontando para um prefixo que contém (ou é igual a) a
   sub-rede abre uma pasta nova, mesmo em sub-rede aninhada;
2. senão **herda** a pasta do nó pai;
3. só na raiz, sem FK e sem herança, vale o fallback por containment (`_srFolderPrefixo`).

Como uma sub-rede pode abrir pasta no meio da árvore, a indentação passou a usar `_depthRel`
(profundidade **dentro da pasta**, zerada em quem abre grupo novo) no lugar de `_depth` (a
profundidade na árvore global de sub-redes) — em `_renderSrLeafRow`, no `qtdSub`, na varredura de
`_emitirSr` e no nível do prefixo ancorado. O resultado é o mesmo desenho que qualquer outro bloco
auto-cadastrado já tinha (`128.41.0.0/16` prefixo → `128.41.0.0/16` sub-rede dentro dele):

```
100.64.0.0/10          prefixo
  100.64.0.0/12        sub-rede
    100.64.1.0/24      prefixo   ← pasta própria, com a contagem 1
      100.64.1.0/24    sub-rede
        100.64.1.1/32  sub-rede
```

Efeito colateral bom: sub-rede aninhada cuja raiz não tinha pasta nenhuma era **descartada** da
árvore (o `curPfxId` ficava `null` e ninguém a mostrava); com o FK valendo por nó, ela aparece na
pasta do prefixo a que está vinculada.

### Terceira camada: uma linha só por bloco (sub-rede espelho)

Sobrou o desenho abaixo — o mesmo `/24` duas vezes, um embaixo do outro: a linha do prefixo e a
linha da sub-rede de mesmo CIDR. Não é dado errado, é como o sistema cadastra
(`_get_or_create_prefixo_pai` cria o `IPAMPrefixo` **e** o `IPAMSubRede`) — na base atual são
**295 pares** para 631 prefixos, quase metade da árvore repetida.

A sub-rede de mesmo CIDR do prefixo dono virou **espelho** (`_espelho`, marcado em
`_srAgruparPorPrefixo`): não ganha linha e é **transparente na indentação** — as filhas dela são a
raiz da pasta. A linha do prefixo absorve o que era só dela:

| Da espelho                | Onde foi parar na linha do prefixo                        |
|---------------------------|-----------------------------------------------------------|
| `gateway`                 | `gw x.x.x.x` ao lado do CIDR                              |
| `vlan`                    | chip âmbar ao lado do badge de tipo                       |
| utilização / "Cheia"      | coluna **Sub-redes**, depois da contagem (`3 · 12% (30/254)`) |
| `pool_cheia`              | badge "Pool Cheia" e o **toggle** da linha (o do prefixo é derivado por `_sync_prefixo_pool_cheia`, marcar nele seria desfeito) |
| grade de IPs              | botão `fa-table-cells` (só em bloco indiviso, mesma regra da linha de sub-rede) — a `<tr id="sr-grade-*">` sai logo depois da linha do prefixo |
| editar                    | botão `fa-network-wired` — "Editar a sub-rede deste bloco (gateway, VLAN, status)" |

O que **não** foi duplicado: deletar. Some da árvore só o prefixo (`ipamDeletarPrefixo`); apagado
ele, a sub-rede espelho deixa de ter dono e volta a aparecer como linha própria (fallback por
containment), de onde dá pra apagá-la. Âncora de prefixo filho nunca é uma espelho — sem linha,
o filho ficaria indentado um nível fundo demais; ele vira "solto" e entra na sequência numérica
das raízes da pasta.

Na visibilidade, a espelho não segura ninguém: `_srAncestraisAbertos` pula nós `_espelho` (não há
pasta pra abrir) e a linha da grade dela acompanha a **linha do prefixo**, não o estado da pasta.

Medido nos 8 clientes com mais blocos: as únicas linhas que sumiram são espelhos — nenhuma
sub-rede comum deixou de aparecer (cliente 90: 1017 → 822 linhas, exatamente as 195 espelhos).

---

## Models Relacionados

| Model          | Campos principais                                              |
|----------------|----------------------------------------------------------------|
| `IPAMVlan`     | `cliente`, `numero` (1-4094), `nome`, `status`               |
| `IPAMPrefixo`  | `cliente`, `prefixo` (CIDR), `tipo`, `status`, `pool_cheia`  |
| `IPAMSubRede`  | `cliente`, `rede` (CIDR), `prefixo` (FK), `vlan` (FK), `status` |
| `IPAMEndereco` | `cliente`, `ip`, `subrede` (FK), `hostname`, `descricao`     |
| `IPAMVpnDoc`   | documentação de VPNs vinculada ao cliente                     |
