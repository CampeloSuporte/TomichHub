# Changelog — CRM NOC

Todas as mudanças relevantes do projeto são registradas aqui.  
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).

---

## [Não publicado] — 2026-08-31 (IPAM: loopback /32 documentado com o nome do host)

### Alterado

- **Loopback sem descrição agora é documentado com o nome do equipamento**
  (`clientes/ipam_views.py`, `clientes/tasks.py::analisar_backups_ipam`).
  A auto-documentação por backup usava o nome da interface como descrição de último recurso, e
  o IPAM ficava com 700+ endereços descritos como `LoopBack0` — justamente nos IPs que
  identificam o equipamento. Agora, endereço **/32 (ou /128) em cima de interface de loopback e
  sem `description`/`comment` na interface** recebe o **hostname declarado no próprio backup**
  (`sysname`, `/system identity set name=`, `set system host-name`, `hostname`).
  - `_eh_interface_loopback` cobre `LoopBack0`, `lo`, `lo0`, `lo0.0` e `bridge2-LOOPBACK`
    (MikroTik não tem loopback nativa — usa bridge).
  - Descrição escrita por gente nunca é tocada: só é sobrescrita a que está vazia ou é o nome
    cru da interface de rodada anterior (`_descricao_e_so_nome_de_loopback`).
  - Efeito na base atual: 708 IPs reclassificados e 81 loopbacks novos documentados.

### Corrigido

- **Comentário MikroTik sem aspas era descartado** (`clientes/ipam_views.py::_parse_mikrotik`).
  O parser só lia `comment="com aspas"`; o `.rsc` omite as aspas quando o comentário não tem
  espaço (`comment=MKAUTH`, `comment=BGP`), então essas descrições sumiam do IPAM — e um
  loopback comentado passava por "sem descrição" na regra nova. As duas formas são lidas agora.
  Mesma coisa em `interface=nome-sem-aspas`, que é como a bridge de loopback costuma aparecer —
  com isso a VLAN também volta a ser identificada em `interface=vlan3010-WAN` (sem aspas):
  818 endereços que estavam sem VLAN no IPAM passam a ficar vinculados à VLAN correta.

---

## [Não publicado] — 2026-08-28 (IPAM: blocos livres completos e pasta sem recarregar)

### Corrigido

- **"Livres" só mostrava uma fatia do prefixo e o menu ficava cortado**
  (`clientes/ipam_views.py::ipam_prefixo_disponiveis`, `clientes/templates/listar.html`).
  A rota tinha dois tetos de quando o cadastro era pequeno: 6 máscaras e 40 blocos por máscara.
  Num `/16` a lista ia de `/17` a `/22` — **um `/24` não existia como opção**, e é o tamanho que
  se cadastra o dia inteiro. O painel ainda era um `position:absolute` de 260 px dentro de um
  modal com `overflow-y:auto`, então a lista era cortada na borda: o "menu bugado".
  - Máscaras agora vão de `pai+1` até `/32` em IPv4 (`_mascaras_disponiveis`); em IPv6, nibble
    até `/64` mais `/112 /126 /127 /128`, as únicas abaixo de /64 que aparecem no cadastro.
  - Sem `?prefixlen` a rota devolve os **gaps** (maiores blocos livres inteiros) e a contagem de
    livres por máscara — `_livres_contagem` soma `2^(pl - gap.prefixlen)` em vez de enumerar.
    Com `?prefixlen=N` devolve página de 240 blocos, e o salto até o `offset` é aritmético
    (`_livres_pagina`): `?prefixlen=30&offset=100000` num `/12` custa o mesmo que a 1ª página.
  - Busca por pedaço do CIDR (`?q=`) com teto de varredura de 20.000 blocos; quando para no
    meio devolve `busca_truncada` e a UI diz isso, em vez de fingir lista completa.
  - **Prefixo filho passou a ocupar espaço** (`_ocupadas_no_prefixo`): antes só `IPAMSubRede`
    contava, e um `/24` cadastrado como *prefixo* dentro do `/16` era oferecido como livre.
  - Front: o dropdown virou painel embutido de largura total (chips por máscara com contagem,
    busca, grade responsiva e "carregar mais") e o **modal de Sub-rede abre em 1040 px** — em
    720 px a grade ficava com 3 colunas e muito scroll.

- **Clicar na pasta do prefixo recarregava a árvore e jogava a página pro topo**
  (`clientes/templates/listar.html`). Os três toggles (`ipamToggleBreakdown`,
  `ipamToggleArvorePrefixo`, `ipamToggleSrChildren`) chamavam `ipamCarregarPrefixos()` — `fetch`
  mais `innerHTML` da tabela inteira —, então abrir uma pasta lá embaixo devolvia o usuário pro
  começo da página e ele tinha que rolar de novo até onde clicou.
  - As linhas de sub-rede **já estão no DOM desde o render** (fechada nasce com `display:none`),
    então `ipamAplicarVisibilidade()` só reavalia `style.display` das linhas e troca os ícones de
    pasta/caret (ids novos `#pfx-caret-*` e `#sr-folder-*`). Sem requisição e sem salto.
  - Onde o redesenho é mesmo necessário (criar/editar/excluir), `ipamCarregarPrefixos()` guarda e
    devolve `window.scrollY`, e o spinner só entra quando ainda não há árvore na tela — trocar
    uma tabela cheia por uma linha de "carregando" encolhia a página antes da resposta chegar.

---

## [Não publicado] — 2026-08-28 (BGP: AS-path na consulta de anúncios)

### Adicionado

- **A consulta ao vivo de anúncios agora devolve o AS-path de cada prefixo**, não só o prefixo
  (`clientes/bgp_actions.py::_extrair_anuncios`, `clientes/templates/bgp_automacao.html`). É a única
  forma de conferir prepend de verdade: o `apply as-path <asn> <asn> additive` da policy de saída não
  aparece em lugar nenhum da RIB local, só no que sai pela sessão.
  - Cada anunciado vira `{'prefixo', 'as_path', 'asns', 'prepends'}` e o painel mostra
    `45.187.123.0/24 · 268546 268546 (1 prepend)`.
  - `prepends` conta a repetição do PRIMEIRO ASN — `268546 268546 268546` são 2 prepends, enquanto
    `268546 26162` são dois saltos e nenhum prepend.
  - Duas gramáticas do VRP mapeadas contra captura real (transcripts dos acessos 324 e 923): a tabela
    de largura fixa do IPv4, onde o `Path/Ogn` é a última célula, e o bloco por prefixo do IPv6, onde
    o path vem em linha própria. O que ancora a célula do IPv4 é a largura do espaço (coluna = 2+
    espaços, ASNs = 1): uma regex de cauda de dígitos engole o fim do next-hop e as colunas
    MED/LocPrf/PrefVal junto.
  - `as_path: null` significa **"não consegui ler"**, não "sem prepend" — acontece em fabricante cuja
    saída ainda não foi mapeada contra captura real (Cisco/Juniper/Mikrotik) e no Huawei quando a
    paginação corta o bloco. O painel mostra `AS-path —` com tooltip, nunca um número inventado.

---

## [Não publicado] — 2026-08-28 (BGP: community de prefixo que não casa filtro nenhum)

### Adicionado

- **Aviso de community órfã na automação de anúncios BGP** (`clientes/bgp_community_auto.py`,
  `clientes/templates/bgp_automacao.html`). A route-policy local de um prefixo pode carregar uma
  community que tem a **cara** do padrão e não corresponde a community-filter nenhum do
  equipamento — config que está lá e não produz efeito. Até agora ela era tratada como qualquer
  convenção própria do cliente: preservada em silêncio a cada reescrita, sem nada na tela.
  - Achado que motivou: na borda do A2+, `65146:65203` digitado à mão no lugar de `65146:50203`
    (`c-02-export-2p`). O prefixo `45.187.123.0/24` parou de ser anunciado com 2 prepends pelo
    circuito de backup, e cada edição pelo painel reemitia o valor errado junto.
  - `_classificar_orfa` separa o que dá pra deduzir do backup do que não dá: quando a parte depois
    do `:` casa um filtro vivo e só o ASN difere (`65100:50104` numa caixa que usa `65101:50104`),
    o aviso diz qual é o destino e qual a community correta; quando o grupo não existe na caixa, o
    aviso aponta que não produz efeito e pede conferência do dígito.
  - **O padrão canônico agora vale para conferir, não só para criar** (`_grupo_canonico`,
    `GRUPOS_CANONICOS`): o grupo de um slot é uma conta (`base_grupo + numero`), então os 25 slots
    são conhecidos sem depender do que a caixa tem configurado. O aviso passa a dizer de quem é o
    grupo (`507` é do `c-07`) ou que ele não é de ninguém (`652` está fora de 501-510/601-610/
    611-615), e os circuitos existentes passam a ser conferidos contra a conta — `c-04` carimbando
    503 (bloco clonado sem trocar o número) vira aviso em vez de passar batido. Fora das faixas não
    há canônico: o `c-81` das caixas antigas é config legítima, não erro.
  - O aviso lista as communities vivas **a um dígito** da órfã — e é o que mostra por que corrigir
    sozinho seria errado: `65203` está a um dígito de `60203` (ix-02) e a dois de `50203` (c-02),
    que era a intenção real.
  - Em nenhum dos casos a automação corrige sozinha — corrigir mudaria o que o prefixo faz na rede.
    A reescrita continua preservando o valor intacto; o que muda é a **visibilidade**: entra nos
    avisos do painel (consolidados, porque a mesma órfã se repete em dezenas de prefixos) e sai em
    ⚠ na linha do prefixo. Varredura nos snapshots atuais: 3 caixas afetadas.

---

## [Não publicado] — 2026-08-27 (Topologia: rótulos sobrepostos e Áreas de documentação)

### Corrigido

- **Nome da interface cobria o IP do enlace** em links quase verticais
  (`static/js/topo_main.js`, `_renderLink`). O IP ia sempre 8 px **acima** e a interface 14 px
  **abaixo** do ponto na linha — um deslocamento fixo no eixo Y. Num enlace na horizontal isso
  separa bem, mas num enlace vertical "acima/abaixo" vira "quase no mesmo lugar" e os dois
  retângulos se empilhavam (visível na topologia com o `SW3-PE-TREVO-PARANAITA`). Agora o
  afastamento é **perpendicular à linha**: o IP sai para um lado, a interface para o lado oposto,
  então eles se separam em qualquer ângulo do enlace.

### Adicionado

- **Áreas de documentação no editor de topologia** (`static/js/topo_engine.js`,
  `static/js/topo_main.js`, `clientes/templates/topologia_editor.html`). Novo item **"Área"** no
  grupo *Anotações* da paleta de dispositivos: arraste para o canvas e ganha um retângulo de fundo
  com cor configurável e um **rótulo no topo** (ex.: "POP Central", "Sala de servidores", "Borda").
  - Desenhada numa camada própria (`#areas-layer`) **atrás** de links e equipamentos — serve para
    circundar e nomear um conjunto de nós sem atrapalhar o clique em nada.
  - O preenchimento não captura o mouse (não rouba o *pan* nem o clique nos equipamentos por cima);
    seleciona/move pela **borda** ou pela aba de título, e **redimensiona pelos quatro cantos**.
  - Nome, cor e tamanho (largura/altura) no painel de propriedades. Não entra na contagem de
    "dispositivos" da barra de status, não participa de conexões e não é capturada pelo laço de
    seleção em área.
  - Cache-busting: `topo_engine.js?v=26` e `topo_main.js?v=45`.
  - **Fix (mesma sessão):** redimensionar a área pelos cantos e clicar "Aplicar" a
    encolhia de volta — os campos Largura/Altura do painel não acompanhavam o arraste, então
    "Aplicar" relia os valores antigos. Agora os campos seguem o arraste e o painel é
    redesenhado com o tamanho final ao soltar.

---

## [Não publicado] — 2026-08-26 (Topologia: tela cheia cortada e navegação lenta)

### Corrigido

- **Tela cheia abria o editor cortado** quando ele roda embutido na aba Topologia do cadastro
  do cliente (`static/js/topo_main.js`, `clientes/templates/topologia_editor.html`). O pedido de
  fullscreen ia pro `<html>` de dentro do `<iframe>`: o navegador pintava a moldura no tamanho da
  tela, mas o **viewport do iframe continuava com a altura antiga** (`calc(100vh - 200px)`), então
  o editor aparecia espremido numa faixa no topo e o resto da tela ficava preto. Agora quem vai
  pra tela cheia é o **próprio `<iframe>`**, no documento pai (`window.frameElement`) — ele vai
  pra *top layer* em tela inteira e o viewport de dentro é redimensionado de verdade. Fora de
  iframe (aba própria) nada muda: continua sendo o `<html>` local. Junto: `_emFullscreen()` olha
  os dois documentos, o `exitFullscreen()` é pedido no documento que entrou e o listener de
  `fullscreenchange` também é registrado no pai (sem isso o botão nunca virava "sair").

### Desempenho

- **Navegação do mapa (pan, zoom e arrastar host) muito mais leve** (`static/js/topo_main.js`,
  `clientes/templates/topologia_editor.html`). O mapa é um `<svg>` só, então qualquer movimento
  rasteriza a cena inteira a cada frame — com ~35 hosts e ~40 enlaces isso derrubava o arraste a
  poucos quadros por segundo. Mudanças:
  - `mousemove` e roda do mouse agora só **agendam** o desenho: ele roda uma vez por frame
    (`requestAnimationFrame`), em vez de uma vez por evento (mouse de 1000Hz mandava ~8 por frame).
  - Arrastar um host redesenha **só os enlaces que tocam nele** — antes reconstruía os 40 enlaces
    do mapa, com `<animateMotion>` e tudo, a cada frame.
  - Arrastar um host agora só muda o `transform` do ícone, em vez de reconstruir ~15 elementos SVG
    via `innerHTML`.
  - `body.nav-busy`: enquanto o mapa está em movimento saem de cena os efeitos decorativos (fluxo
    animado, pacotes, pulso dos hosts do CRM, `drop-shadow` dos ícones, `backdrop-filter` dos
    painéis) e voltam 200ms depois que ele para. É só visual — nenhum dado muda.
  - `getBoundingClientRect()` do canvas passou a ser cacheado por frame (cada chamada no meio do
    arraste força o navegador a recalcular layout), e `_setDirty()` não reescreve mais a barra de
    status e o botão Salvar a cada frame.
- Documentação: `docs/topologia.md` — seção "Tela Cheia" reescrita com a tabela de qual elemento
  entra em fullscreen em cada situação, e nova seção "Desempenho da navegação".

---

## [Não publicado] — 2026-08-25 (Pesquisa LG: consulta IRR com bgpq4 e expansão de as-set)

### Adicionado

- **Aba "Filtro IRR (bgpq4)" na Pesquisa LG** (`home/irr_tools.py`, `home/views.py`,
  `home/urls.py`, `home/templates/lg_pesquisa.html`). Informa um ASN ou as-set
  (`AS53181`, `AS-CAMPELO`, `AS271699:AS-CLIENTES`) e sai o prefix-list/route-filter pronto
  no formato do fabricante: Cisco IOS e IOS XR, Junos (prefix-list e `route-filter-list`),
  **Huawei VRP e XPL**, **MikroTik RouterOS v6 e v7**, Nokia SR OS clássico e MD-CLI,
  SR Linux, Arista EOS, BIRD, OpenBGPD, JSON e lista simples. Opções avançadas: nome da
  lista, servidor IRRd, fontes IRR (`-S`), max-length v4/v6 (`-m`), agregação (`-A`) e
  "só ASNs com rota registrada" (`-w`). A tela mostra a contagem de prefixos por família, o
  **comando bgpq4 exato** que rodou (pra reproduzir no terminal), a config com botão Copiar,
  o botão Baixar e a lista de prefixos expandível.
- **Aba "AS-SET"**: expande o objeto e mostra os membros diretos, os **as-sets aninhados
  clicáveis** (abrem o filho, com trilha pra voltar), os ASNs do fechamento recursivo com
  nome e filtro, a contagem de prefixos IPv4/IPv6 e — o mais útil no dia a dia — **o objeto
  em cada base IRR** (RADB, LACNIC, TC, RIPE, ARIN…), com `descr`, `mnt-by`, data e
  `members` de cada uma, mais um aviso quando divergem: o upstream filtra pela base que
  *ele* consulta, e é aí que mora o "meu prefixo não passa". Botões pra copiar/baixar a
  lista de ASNs e pra pular direto pro filtro do objeto.
- `home/irr_tools.py`: cliente do protocolo IRRd na porta 43 com conexão persistente
  (`!!`, `!i<set>`, `!i<set>,1`), leitura do objeto RPSL cru de todas as bases, resolução
  de nomes de ASN em lote na RIPEstat e execução do `bgpq4` em paralelo (config e JSON,
  v4 e v6).
- Rotas `GET /home/ferramentas/lg/irr/` e `GET /home/ferramentas/lg/as-set/`, ambas com
  `@ferramenta_instancia_required('lg')` — mesma permissão da aba de Looking Glass.
- Documentação: `docs/CONSULTA_IRR_ASSET.md` (novo), com entrada em `docs/INDEX.md` e
  seção atualizada em `SISTEMA.md`.

### Detalhes de implementação

- **bgpq4, não bgpq3**: o bgpq3 está sem manutenção desde 2019; o bgpq4 (fork do NTT) tem os
  alvos que este CRM usa — MikroTik v7, Huawei VRP/XPL, Nokia MD-CLI — fala IRRd com
  pipelining e vem empacotado no Debian (`apt install bgpq4`). Sem o binário no PATH, as duas
  abas respondem 503 com mensagem clara.
- **Entrada validada antes de virar argumento**: o objeto entra na linha de comando do bgpq4
  e num socket whois, então `validar_objeto()` só aceita `AS<n>`, `AS-NOME`, `RS-NOME` e
  combinações com `:` — nada de espaço, `-` inicial (viraria flag) ou metacaractere.
  Servidor IRRd, fontes e nome da lista têm regex própria; `-m`/`-R` são validados por faixa.
- **Saída grande**: `AS-HURRICANE` dá 954 mil prefixos IPv4 (dezenas de MB). A tela recebe no
  máximo 1,5 MB de config, 8 mil prefixos e 2 mil ASNs, sempre com aviso de truncagem; os
  botões Baixar refazem a consulta **sem cache e sem limite** e devolvem o arquivo inteiro.
  A contagem de prefixos da aba AS-SET usa `-F '%n/%l'` e conta linhas, em vez de parsear
  30 MB de JSON só pra saber o total.
- **Cache Redis de 10 min** por consulta (`lg_irr:…`/`lg_asset:…`), com badge "cache" na
  tela — dado de IRR muda devagar e isso poupa o mirror do NTT.
- Timeout do bgpq4: 75 s na aba de filtro, 45 s na contagem do as-set (o gunicorn corta em
  120 s). `AS-HURRICANE` (25.456 ASNs) responde em ~11 s.

---

## [Não publicado] — 2026-08-25 (Topologia: agrupar hosts num ícone)

### Adicionado

- **Agrupar hosts num ícone só, com mapa próprio** (`static/js/topo_main.js`,
  `static/js/topo_engine.js`, `clientes/templates/topologia_editor.html`, `clientes/views.py`).
  Com 2+ dispositivos selecionados (laço de área ou Shift+clique), o botão **Agrupar** da toolbar
  (ou a tecla **G**) troca o bloco por um único node do tipo `grupo` — ícone novo de pilha de
  chassis com badge de contagem — e cria um **sub-mapa** com aqueles hosts mais uma cópia de borda
  do vizinho de onde saíam os enlaces, pra o mapa de baixo abrir já com "switch + OLTs". No mapa de
  cima, os N enlaces que cruzavam a fronteira do grupo viram **um enlace por vizinho**, herdando o
  mais rápido deles e o rótulo `N enlaces`; as interfaces e IPs do lado que aponta pro grupo saem
  (descreviam a porta de um host, e o ícone não é um host). Duplo-clique no ícone — ou o botão
  "Abrir mapa do grupo →" no painel de propriedades — abre o sub-mapa; a toolbar de lá já tinha o
  botão de voltar. O nome sugerido sai do prefixo comum dos hosts
  (`OLT-ALCOBACA-02`…`-06` → `OLT-ALCOBACA (5)`).
- **Desagrupar** no painel do grupo: devolve os hosts e os enlaces originais para o mapa pai
  (posições, ids e campos de link intactos) e exclui o sub-mapa. Remover o ícone pela lixeira segue
  sendo o caminho destrutivo, agora com confirmação explícita.
- `POST /clientes/<id>/topologia/<diagrama_id>/submapa/` passou a aceitar `dados_json` (sub-mapa
  criado já com conteúdo) e ganhou o par
  `POST /clientes/<id>/topologia/<diagrama_id>/submapa/excluir/`, que recusa o mapa raiz e
  **reponta** os sub-mapas netos para o avô antes do `delete()` — sem isso o CASCADE levaria junto
  o mapa de um grupo aninhado que tinha acabado de voltar pro mapa pai.
- `importHosts()` ignora hosts que estão dentro de um grupo (`grupo_membros`/`_idsAgrupados()`).
  Sem isso a reimportação traria as OLTs agrupadas de volta pro mapa de cima, duplicadas com as que
  já estão no sub-mapa.
- **Botão de tela cheia** no editor de topologia (`⛶` no último cluster da toolbar, ou a tecla
  **F**). Pede fullscreen no `<html>`, então toolbar e painéis vão junto do canvas. O motivo é o
  editor embutido no cadastro do cliente, cujo `<iframe>` tem `calc(100vh - 200px)` — esse iframe
  ganhou `allowfullscreen allow="fullscreen"` (`clientes/templates/listar.html`), sem o que o
  navegador recusa o pedido vindo de dentro dele. Quando a permissão não existe, o botão avisa por
  toast ("abra o editor em nova aba") em vez de não reagir; o ícone e o tooltip acompanham o estado
  por `fullscreenchange`, então sair pelo Esc também atualiza o botão.
- **Ctrl+clique** virou o gesto de selecionar vários dispositivos (`static/js/topo_main.js`,
  `_ehAditivo`) — segurar Ctrl e ir clicando nos hosts, depois **Agrupar**. Ctrl+arrastar no vazio
  também desenha o laço de seleção. **Shift** (que era o único modificador desde 2026-07-31) e
  **Cmd** no Mac continuam valendo como alias. Duplo-clique com o modificador segurado deixou de
  abrir sub-mapa: dois Ctrl+cliques seguidos no mesmo host chegam ao handler como duplo-clique, e
  num ícone de grupo isso tirava a pessoa da tela no meio da seleção.
- **O botão de voltar do sub-mapa passou a parecer um botão de voltar**
  (`clientes/templates/topologia_editor.html`). Ele já existia desde os sub-mapas, mas mostrava
  só a seta e o **nome do mapa de cima** ("Nova Topologia"), em cinza, entre a marca e o campo de
  nome — lia como título, não como navegação, e quem entrava num grupo não achava a saída. Agora
  diz **"Voltar"** em cor de destaque, com o nome do mapa pai como legenda ao lado (escondida
  abaixo de 1200px, onde a barra aperta).
- Detalhes em [docs/topologia.md](docs/topologia.md) — seção "Agrupar Hosts num Ícone".

---

## [Não publicado] — 2026-08-24 (BGP: subir circuito no Huawei parava na confirmação do VRP)

### Corrigido

- **Criar sessão/circuito BGP no Huawei morria com `Pattern not detected: '(?:VS\-BGP.*$|#.*$)'`**
  (`clientes/bgp_actions.py` → `_enviar_config_vrp`). Pego ao subir o `ix-03` (PTT-RS): o
  `send_config_set` do Netmiko espera o prompt depois de cada linha, mas o VRP responde
  `Continue? [Y/N]:` em comandos como o `undo peer <grupo> enable` da `ipv4-family unicast` (o que
  tira o peer v6 da family v4, onde ele nasce habilitado). Sem ninguém responder, a leitura estourava
  o timeout e a exceção subia **sem dizer em que comando parou** e **descartando o output já lido** —
  com metade do circuito na config candidata (o `commit` nunca chegava a rodar). Agora o Huawei envia
  comando a comando, para no prompt OU na pergunta, responde `Y` (a ação inteira já foi confirmada no
  modal) e segue; teto de 5 confirmações por comando. O prompt passa a ser reconhecido pela forma
  (`<...>`/`[...]`), não pelo nome do host, o que cobre prompt truncado e sub-views
  (`[*VS-BGP-bgp-af-ipv6]`), e o eco deixa de ser verificado (linha longa quebrada na largura do
  terminal era um segundo modo de falha). Em modo trial o `commit trial N` vai pelo mesmo caminho.
- **Erro no meio do envio deixava de mostrar o que já tinha entrado** (mesmo arquivo,
  `ErroEnvioBgp`). A ação agora registra na auditoria (`AcaoBgp`) o transcript parcial e o comando
  exato onde parou, que é o que o operador precisa pra decidir entre repetir ou limpar o que ficou
  pela metade.
- Testes novos em `clientes/tests_bgp_envio_vrp.py` (9 casos). Detalhes em
  [docs/bgp_automacao.md](docs/bgp_automacao.md).

---

## [Não publicado] — 2026-08-24 (Proxy web: Grafana e URLs com porta)

### Corrigido

- **Grafana aberto pelo proxy caía no "Page not found" dele mesmo**
  (`clientes/proxy_engine.py` → `_rewrite_grafana_bootdata`). O front do Grafana descobre o
  sub-caminho em que está servido pelo `appSubUrl` do bootdata embutido no HTML; instalado na
  raiz ele manda `""`, o router tenta casar o caminho inteiro do proxy
  (`/clientes/acessos/1301/web/3000/http/login`) e renderiza o 404 interno — com **tudo 200**
  no log do nginx, o que fazia o sintoma parecer erro do CRM. Agora o `appSubUrl` é reescrito
  para o `proxy_base`, o que conserta de uma vez o router, o prefixo das chamadas de API e o
  `__webpack_public_path__` dos chunks. Verificado ao vivo (harness local + chromium headless):
  a tela de login aparece e o login abre o dashboard, sem requisição falhando.
- **URL absoluta do device com porta explícita virava caminho quebrado**
  (`clientes/proxy_engine.py` → `_rewrite_urls_absolutas`). A reescrita só conhecia `:80`,
  `:443` e host sem porta: `http://198.18.1.13:3000/d/abc` saía como
  `/clientes/acessos/1301/web/3000/http:3000/d/abc` (404 do Django) em qualquer device de porta
  alta — Grafana 3000, Proxmox 8006, Zabbix 8080. Agora a porta é lida junto com o host: igual à
  proxyada → `proxy_base`; explícita e diferente → a base daquela porta (o link continua dentro
  do proxy); sem porta → `proxy_base`, porque muito firmware se anuncia sem porta mesmo servindo
  numa porta alta. A view passa `target_port` para a reescrita.
- Testes novos em `clientes/tests_proxy_web.py` (8 casos). Detalhes em
  [docs/proxy_web_acessos.md](docs/proxy_web_acessos.md).

---

## [Não publicado] — 2026-08-24 (Topologia: importação agrupada por função)

### Alterado

- **Hosts importados entram agrupados por função** (`static/js/topo_main.js` →
  `importHosts` / novo `_layoutImportados`, cache-buster `topo_main.js?v=39`). Abrir uma
  topologia ainda não configurada dispara a importação automática, e até agora ela jogava
  todos os hosts num grid único de 5 colunas na ordem do backend — switch, OLT, roteador e
  servidor embaralhados num bloco só. Agora cada função vira uma **faixa horizontal própria**,
  empilhada na hierarquia da rede (trânsito → core → switch L3 → switch L2 → acesso FTTH →
  wireless → servidores), com um rótulo `text_box` na cor do device à esquerda de cada faixa.
  - Rótulo tem id fixo `grp_<tipo>`: reimportar não cria um segundo "Switch L3" ao lado do
    primeiro. É node normal — dá pra mover, editar ou apagar.
  - Reimportação de host cujo tipo **já existe** no canvas coloca ele ao lado dos irmãos
    (à direita do de maior `x`), então a faixa continua junta mesmo depois de a pessoa
    arrastar tudo pra outro canto. Tipo inédito abre faixa nova **abaixo** do desenho atual,
    nunca por cima do que já foi posicionado à mão.
  - Sem host novo, a importação só sincroniza função/ícone e avisa "Nenhum host novo para
    importar" — antes ela recalculava posição em cima de índice que incluía os já existentes.
  - Detalhes e constantes de layout em [docs/topologia.md](docs/topologia.md).

---

## [Não publicado] — 2026-08-23 (Segurança: proteção contra invasão)

### Adicionado

- **Bloqueio por tentativa de login** (`seguranca/services.py`, chamado por `usuario/views.py`).
  Errar a senha **3 vezes tranca a conta por 5 minutos**; 10 falhas trancam o **IP** por 15
  minutos (o robô que testa 500 usernames inventados nunca acumularia 3 falhas no mesmo
  username). Decisões que valem registrar:
  - A verificação roda **antes do `authenticate()`** — durante o bloqueio nem a senha certa
    entra. Se passasse, o bloqueio só atrasaria quem já errou, não seguraria um ataque de
    dicionário que acerta na tentativa seguinte.
  - Janela deslizante de 15 min: falhas antigas não somam (senão dois erros em janeiro mais um
    em março trancariam a conta). Login certo zera o contador; bloqueio expirado recomeça do zero.
  - **O 2FA entrou no mesmo contador.** O `2fa_tentativas` da sessão que já existia só derrubava
    aquela sessão — trocar de aba zerava, e código de 6 dígitos é adivinhável por força bruta.
  - Usuário inexistente **não** cria linha de bloqueio de conta (evita encher a tabela); captcha
    do Turnstile reprovado **não** conta (o widget falha sozinho por rede/extensão, e trancar a
    conta por isso puniria quem nem chegou a errar a senha).
  - Mensagem na tela continua genérica (não confirma se o usuário existe); a partir da 2ª falha
    informa quantas tentativas restam.
- **Fail2ban instalado e configurado**, com duas jails: `sshd` e `crm-login` (alimentada por
  `/var/log/crm/auth.log`, escrito pelo logger `seguranca.auth`). Ban progressivo para
  reincidente (1h → 2h → … → 1 semana). Duas armadilhas resolvidas na configuração:
  - **o SSH deste servidor está na porta 22002**, então sem `port = 22002` a regra de firewall
    iria para a porta errada e não bloquearia nada;
  - **no Ubuntu o backend padrão do fail2ban é `systemd`**, e com ele a jail ignora `logpath` em
    silêncio (status mostra `Journal matches` em vez de `File list`); `crm-login` exige
    `backend = auto`.
  - O sudoers (`/etc/sudoers.d/crm-fail2ban`) libera **só** `ping|status|set … banip|unbanip|unban`
    para o `www-data` — `NOPASSWD` no binário solto seria escalada a root, porque
    `fail2ban-client set <jail> action …` executa shell.
  - Os banimentos **não** são espelhados em tabela: a fonte da verdade é o `fail2ban-client`, que
    é quem fala com o firewall. Um espelho mentiria se alguém mexesse no fail2ban por fora.
- **Filtro de injeção** (`seguranca/middleware.py`): SQL injection, path traversal e XSS refletido
  barrados na query string, no caminho e no POST urlencoded, antes de sessão/auth. Auditoria
  confirmou que o projeto **não monta SQL com string** (os dois `cursor.execute` existentes são
  literais fixos) — o middleware é cinto de segurança contra regressão futura e, principalmente,
  dá **visibilidade**: sem ele uma varredura de `sqlmap` não deixaria rastro nenhum.
  - Multipart e JSON ficam de fora **de propósito**: ler o corpo no middleware o consome antes da
    view (100 MB de upload de firmware processados no middleware, e nenhuma view poderia trocar
    os upload handlers depois).
  - Contra falso positivo: assinaturas específicas (a palavra "select" sozinha não dispara),
    prefixos isentos (`/wiki/`, `/atendimento/`, `/clientes/scripts/`, `/clientes/terminal/`…),
    campos de texto livre isentos em qualquer rota, e modo observação
    (`SEGURANCA_INJECAO_BLOQUEAR=0`).
- **Painel Sistema → Segurança** (`/seguranca/`), cinco abas: Bloqueios (com contagem regressiva,
  **Liberar** e **Liberar todos**), Tentativas de login (filtros por usuário/IP/resultado/período
  e ranking de IPs), SSH/Fail2ban (jails, blacklist, liberar, banir manual, histórico do
  `/var/log/fail2ban.log`), Injeção/SQLi e Auditoria.
  - **Administrador** vê tudo; **Consultor** vê e destrava só as contas que já gerencia
    (`perms.usuarios_gerenciaveis_por`) — bloqueio por IP, fail2ban e eventos de injeção são do
    servidor inteiro, não de uma instância. Operador e portal do cliente não entram.
  - Todo desbloqueio/banimento grava `AcaoSeguranca` com autor e IP de origem: desbloquear é
    exatamente a ação que um invasor com sessão roubada ia querer usar.
- **Endurecimento**: `SECURE_PROXY_SSL_HEADER` (sem ele o Django achava que toda requisição era
  `http` atrás do nginx, e `request.is_secure()` mentia), `SECURE_CONTENT_TYPE_NOSNIFF`,
  `SECURE_REFERRER_POLICY`, `SESSION_COOKIE_HTTPONLY`, `SameSite=Lax`. Cookies `secure` ficam
  atrás de `SEGURANCA_COOKIES_HTTPS=1` porque o servidor ainda atende em `http://` no IP bruto.
- Task Celery `seguranca.limpar_registros` (diária, 03:40) poda tentativas/eventos além de 90
  dias — a tabela cresce com tráfego de robô, que é justamente o que não para.

### Corrigido

- **Login de conta sem Cliente vinculado dava erro 500** em vez da mensagem "sua conta não possui
  acesso ao sistema": `usuario.views.redirect_user_by_role` chamava `messages.error(None, …)`, e
  `add_message` levanta `TypeError` com `None`. Agora recebe o `request` e desloga a conta nesse
  caminho — sem o logout ela ficaria autenticada e o `GET` de `/auth/login/` mandaria de volta
  para a mesma função, em laço infinito de redirect.

**Regressão:** `seguranca/tests.py` (21 testes), incluindo o caso que impede a regressão mais
perigosa — a senha certa continuar sendo recusada durante o bloqueio —, o de falso positivo
(`O'Brien Telecom`, `select-fibra`, `update de contrato` passam) e o escopo por papel (Consultor
leva 403 ao tentar desbloquear conta de outra instância ou mexer no fail2ban). Suíte existente:
288 testes OK (`usuario clientes atendimento tarefas financeiro`).

**Arquivos:** `seguranca/` (app novo), `usuario/views.py`, `crm/settings.py`, `crm/urls.py`,
`crm/celery.py`, `templates/base.html`, `docs/SEGURANCA.md`, `docs/INDEX.md`.
**Servidor:** `/etc/fail2ban/jail.d/crm.local`, `/etc/fail2ban/filter.d/crm-login.conf`,
`/etc/sudoers.d/crm-fail2ban`, `/etc/logrotate.d/crm-seguranca`, `/etc/logrotate.d/fail2ban`.

---

## [Não publicado] — 2026-08-21 (BGP: validar anúncios funciona em sessão IPv6)

### Corrigido

- **"Validar anúncios" em sessão IPv6 voltava sempre `Anunciados (0) / Recebidos (0)`**, mesmo com
  a sessão estabelecida e anunciando de verdade (caso real: acesso 20, `BDR-DNO`, sessão
  `RS1.PTT-CE-V6` — o próprio equipamento reporta `Advertised total routes: 4` e o painel mostrava
  zero). Duas causas, ambas confirmadas ao vivo no equipamento:
  - Os comandos de consulta eram só da árvore **IPv4**. `comandos_validar_anuncios` /
    `comando_contar_recebidos` agora trocam de address-family quando o `peer_ip` é v6
    (`_sessao_e_v6`): Huawei usa `display bgp ipv6 routing-table peer X ...` e `display bgp ipv6
    peer X verbose`; Cisco/Datacom usam `show bgp ipv6 unicast neighbors X ...` (inclusive no
    fallback `routes`); Mikrotik lê os recebidos de `/ipv6 route` no lugar de `/ip route`. Juniper
    não muda — infere a family pelo endereço do peer.
  - `_extrair_prefixos` só reconhecia CIDR IPv4. Agora também extrai CIDR IPv6 (validando o
    candidato com `ipaddress.IPv6Network`) e o par `Network : X   PrefixLen : N` do Huawei, que
    **não** imprime o prefixo v6 em CIDR.
  - Validado ao vivo nos três fabricantes com sessão v6 nesta base: Huawei acesso 20 (4 prefixos),
    Cisco acesso 887 (`2804:57B0:EFF0::/44` anunciado, `::/0` recebido) e Mikrotik acesso 390
    (4 prefixos). Sessões IPv4 seguem com exatamente os mesmos comandos de antes.
  - `clientes/tests_bgp_validar_v6.py` (10 testes) cobre os comandos por fabricante/família e o
    parser de prefixos.

---

## [Não publicado] — 2026-08-21 (BGP: route-policy local vazia volta a ser editável)

### Corrigido

- **Prefixos com route-policy local ainda "crua" apareciam como não editáveis** no painel de
  automação BGP, com o aviso errado *"a route-policy X referenciada pelo network não foi
  encontrada"* — quando ela existe na caixa, só está **vazia** (`route-policy RT-BGP-LOCAL-57B0-34
  permit node 10`, sem `apply community` nenhum). `parse_huawei` guarda em `community_nodes`
  apenas os nós que mexem com community, então essas policies só chegavam ao mapa via `policies`
  e o seletor "Anunciar como" ficava desabilitado. Na borda do AS268080 (JMA) isso travava 7 dos
  12 prefixos originados, incluindo todos os `/34` de IPv6.
  - `bgp_community_auto._node_local_vazio` adota o node vazio — o caso mais seguro de editar que
    existe (não há community pra preservar nem `if-match` que possa deixar de casar). Só quando a
    policy tem **um único** node, `permit`, sem `if-match` e sem `apply as-path`.
  - `_comandos_reescrever_intencao` omite o `undo apply community` quando o node não tem nenhuma
    community configurada: o VRP recusa o undo de um atributo inexistente e a sessão abortaria
    antes do `apply`.
  - `aplicar_efeito_local` cria o node em `community_nodes` no primeiro anúncio, senão o painel
    seguiria mostrando "não anunciado" até o backup seguinte mesmo com o comando já aplicado.
  - Aviso novo e específico pra policy local que existe mas **não** é adotável (mais de um node,
    ou node com `if-match`): *"não tem um node único e vazio onde a automação possa carimbar
    community com segurança"*.

**Regressão:** `clientes.tests_bgp_community_auto.PolicyLocalVaziaTest` (5 testes). Suítes BGP: 70 OK.

**Arquivos:** `clientes/bgp_community_auto.py`, `clientes/tests_bgp_community_auto.py`,
`docs/bgp_automacao.md`.

---

## [Não publicado] — 2026-08-21 (Excluir tarefa pelo painel do dashboard)

### Adicionado

- **Botão "Excluir" nas tarefas do painel do dashboard** (`quadro_geral` e
  `quadro_instancia`), com confirmação. Antes o painel só tinha "Assumir" e "Editar": a
  única exclusão existente era a do kanban da página do cliente
  (`tarefa_kanban_excluir`), que lista tarefa **por cliente** — então uma tarefa de
  plataforma (`cliente = NULL`, criada pelo modal "Nova Tarefa" sem escolher cliente)
  não aparecia em kanban nenhum e ficava impossível de excluir por qualquer caminho.
  - `tarefas.views.tarefa_excluir` (`POST /tarefas/<id>/excluir/`): `backoffice_required`
    + `require_POST`, escopo por `Tarefa.objects.visiveis_para` — **404** (não 403) fora
    da instância, pra não revelar que a tarefa existe do outro lado. A checagem não passa
    por `pode_acessar_cliente`, que é justamente o que permite excluir a tarefa sem
    cliente.

**Regressão:** `tarefas.tests.ExcluirTarefaTest` (7 testes). Suíte completa: 287 OK.

**Arquivos:** `tarefas/views.py`, `tarefas/urls.py`, `tarefas/tests.py`,
`tarefas/templates/tarefas/_linha.html`, `tarefas/templates/tarefas/_painel.html`,
`docs/TAREFAS.md`.

---

## [Não publicado] — 2026-08-21 (Atendimento passa a ser exclusivo da instância principal)

### Alterado

- **O módulo de Atendimento é exclusivo da instância principal** — a operação própria do
  Administrador. Consultor e Operador de revenda não entram mais: nem tela, nem API, nem
  WebSocket. Antes o módulo escopava os dados por instância; agora a porta é fechada antes disso.
  - `Instancia.principal` (novo campo, migrações `usuario.0010` e `0011`) marca essa operação. A
    migração de dados marca a instância "Principal". Sem nenhuma marcada, o módulo fica só com o
    Administrador — nunca cai aberto por falta de configuração.
  - Regra em `usuario.perms.pode_acessar_atendimento`, usada por
    `atendimento.views.staff_required`, pelos três consumers de `atendimento/consumers.py` e pelo
    menu (`pode_atendimento_bo`).
  - Não dá pra resolver tirando o `is_staff` de Consultor/Operador: eles precisam dele para os
    Scripts de Automação e para o WebSocket de firmware.

### Corrigido

- **WebSockets do atendimento sem autorização**: `ConversationConsumer`, `InboxConsumer` e
  `VirtualRoomConsumer` checavam só `is_authenticated`. Qualquer conta logada — inclusive login de
  portal do cliente final — assinava `atendimento_inbox` e recebia em tempo real toda mensagem que
  passasse pelo módulo, mesmo sem conseguir abrir a tela.
- **`api_tags_list` não tinha decorator nenhum** — leitura e criação de tag abertas.
- **As sete APIs de kanban** tinham só `@login_required`: qualquer conta logada lia e escrevia nos
  quadros.
- `sala_virtual` estava com `@login_required`; é tela do módulo, passou pro mesmo gate.

**Regressão:** `atendimento.tests.AtendimentoExclusivoDaPrincipalTest` (8 testes) e
`EscopoDeDadosNoAtendimentoTest` (8 testes, reescrita da anterior).

**Arquivos:** `usuario/models.py`, `usuario/perms.py`, `usuario/context_processors.py`,
`usuario/migrations/0010_instancia_principal.py`,
`usuario/migrations/0011_marcar_instancia_principal.py`, `atendimento/views.py`,
`atendimento/consumers.py`, `templates/base.html`, `atendimento/tests.py`,
`docs/ATENDIMENTO.md`.

---

## [Não publicado] — 2026-08-21 (Segurança: Consultor via os clientes das outras instâncias)

### Corrigido

- **Falha de isolamento entre instâncias.** O Consultor `mmarinho` (2 clientes) enxergava os 47
  clientes da instância Principal. Causa: `is_staff` passou a ser `True` também para
  Consultor/Operador (necessário para o módulo de atendimento), mas `admin_required` continuou
  usando `is_staff` como se fosse "é o Administrador" — abrindo todas as telas globais, que
  listam `Cliente.objects.all()` sem escopo. Crawl autenticado nas 795 rotas encontrou **21
  rotas** devolvendo cliente de outra instância; hoje são **0**, para Consultor e Operador, sem
  nenhuma mudança de acesso para o Administrador.
  - `admin_required` (em `clientes/decorators.py` e o alias de `atendimento/views.py`) passou a
    checar o papel via `usuario.perms.is_admin`. `cliente_login_required` e
    `cliente_or_admin_required` usam `is_backoffice` em vez de `is_staff`.
  - Menus de plataforma nos templates saíram de `request.user.is_staff` para `is_admin_bo`.
  - **`atendimento/scope.py` (novo)**: `clientes_visiveis`, `conversations_visiveis`,
    `groups_visiveis`, `pode_ver_conversation`, `pode_ver_group` — aplicados em todas as
    listagens do atendimento, mais **10 guardas em conversas e 4 em grupos** que fecharam os
    IDOR por id na URL (enviar mensagem, mesclar, agendar, taguear, e `api_conversation_hosts`,
    que entregava os hosts do cliente de qualquer instância).
  - Telas que são operação legítima do Consultor viraram `backoffice_required` **com queryset
    escopado**: relatório de backups, chamados por status, config de backup por acesso (agora
    valida `pode_acessar_cliente`), catálogos de modelo/função e as correções de GeoIP
    (dono = `solicitante`).
  - Seletores de usuário (`conversation_detail`, `tarefas`, `kanban`) saíram de
    `User.objects.filter(is_active=True)` para `perms.colegas_de_instancia` — o kanban listava
    até os logins de portal dos clientes alheios.
  - **Financeiro**: `api_painel_blocos_ip`, `listar_contratos_aluguel` e `assinatura_locador`
    tinham só `@login_required` — qualquer autenticado listava os aluguéis de IP de todos os
    clientes. Passaram a `@acesso_financeiro_restrito`.

**Regressão:** `atendimento.tests.IsolamentoInstanciaTest` (8 testes; 6 falham se o escopo for
desligado). Suíte completa: 272 testes OK.

**Arquivos:** `atendimento/scope.py` (novo), `atendimento/views.py`, `clientes/decorators.py`,
`home/views.py`, `financeiro/views.py`, `modelo_equipamento/views.py`,
`funcao_equipamento/views.py`, `templates/base.html`,
`atendimento/templates/atendimento/{base,dashboard}.html`, `home/templates/geo_consulta.html`,
`atendimento/tests.py`, `docs/PERMISSOES_CONSULTOR.md`.

Detalhes em `docs/PERMISSOES_CONSULTOR.md`.

---

## [Não publicado] — 2026-08-20 (Agent NOC: Zabbix via API — histórico e gráficos)

### Adicionado

- **O Agent NOC agora consulta o Zabbix do cliente e responde com gráfico**: perguntas de
  histórico ("me traga o histórico do tráfego do link da Wirelink", "sinal óptico antes e depois
  do rompimento") deixaram de depender de SSH — quem guarda o passado é o Zabbix.
  - `zabbix_buscar_item(host, item)` — acha hosts e itens monitorados; o termo do item casa em
    **nome e key** do item, então buscar pela descrição da interface ("painera", "wirelink") basta.
    Sem achar, lista o que existe naquele host em vez de responder "não encontrei".
  - `zabbix_historico(itemids, periodo|inicio+fim, marcador, titulo)` — até **4 itens no mesmo
    gráfico**, com mín/méd/máx/último e amostras da janela, e o **PNG enviado ao usuário**
    (mídia no WhatsApp, `<img>` no chat do terminal web).
  - `marcador` desenha linha vermelha tracejada na hora do evento — é o "antes e depois do
    rompimento" com as duas pontas do enlace no mesmo gráfico.
- **Sem cadastro novo**: o Zabbix sai do `ZabbixConfig` do cliente ou, na falta dele, de um
  **acesso HTTP/HTTPS com "zabbix" no tipo** (usuário/senha do próprio acesso). A URL tolera os
  formatos reais de cadastro (`ip:porta/zabbix`, `ip/zabbix/`, `ip` + porta) e o acesso passa pelo
  **mesmo túnel SSH do ProxyServer** que a aba Monitoramento usa quando o Zabbix está em IP privado.
  A combinação que respondeu fica em cache por 30 min.
- **`monitoramento/chart.py`** — gráfico de série temporal em PNG com Pillow (sem matplotlib):
  eixo Y formatado pela unidade do item (`4.05 Gbps`, `-23.87 dBm`), horas no fuso do Django e
  legenda com mín/méd/máx por série.
- **`historico_janela()`** em `monitoramento/services.py` — janela absoluta com fallback automático
  entre `history.get` e `trend.get` (médias horárias), então evento de dias atrás ainda tem gráfico
  mesmo com o histórico bruto já expirado. Contadores de octetos viram taxa em bps.

**Validado ao vivo** (Startnet Provedor, Zabbix em IP privado via túnel SSH): "@noc me traga o
histórico do tráfego das últimas 3 horas do link paineiras no switch brasnorte" → o agent buscou os
itens da `100GE0/0/4(LINK-PAINEIRAS)`, trouxe os números das duas direções e enviou o gráfico.

**Arquivos:** `monitoramento/agent_zabbix.py` (novo), `monitoramento/chart.py` (novo),
`monitoramento/services.py`, `home/agent_engine.py`, `home/views.py`,
`clientes/templates/terminal.html`, `docs/agent_noc.md`, `docs/monitoramento.md`, `AGENT_NOC.md`.

---

## [Não publicado] — 2026-08-20 (Acessos: RDP abria terminal SSH)

### Corrigido

- **Acesso RDP abria o terminal SSH**: clicar em "Acessar" num acesso com protocolo `RDP` levava
  para `/clientes/terminal/?cliente=<id>` e o CRM tentava conectar por SSH, em vez de abrir a área
  de trabalho remota em `/clientes/rdp/<id>/`.
  - `static/js/terminal_tab_manager.js` — a implementação de `acessarEquipamento()` que a listagem
    de clientes realmente carrega tratava `HTTP/HTTPS` e `WINBOX`, mas não `RDP`; todo protocolo
    não previsto caía no ramo final "SSH, Telnet, etc". Agora há ramo explícito para `RDP`.
  - O caso `RDP` que existia em `static/js/acessar_equipamento.js` nunca rodava — nenhum template
    inclui esse arquivo.
  - Backend (`clientes/rdp_vnc.py` + `VncConsumer` em modo `rdp`, com túnel via ProxyServer para
    IP privado) já estava pronto; só faltava o front chegar nele.

- **RDP abria só tela preta**: o `RdpVNCManager` forçava `/sec:tls` no `xfreerdp`, e Windows Server
  com NLA obrigatório recusa TLS puro (`HYBRID_REQUIRED_BY_SERVER`). O cliente RDP morria em ~100 ms
  enquanto Xvfb e x11vnc seguiam de pé — o noVNC transmitia um display vazio.
  - Sem `/sec:...`, o FreeRDP negocia sozinho (NLA → TLS → RDP legado), atendendo servidor novo e
    antigo.
  - O stderr do `xfreerdp` deixou de ir pra `DEVNULL`: é lido num thread e vai pro log do daphne.
  - Se o cliente RDP morrer nos 2 s iniciais, o erro é traduzido ("Usuário ou senha inválidos",
    "O servidor exige NLA…", "Não foi possível abrir a conexão TCP…") e aparece na tela do usuário,
    em vez de tela preta silenciosa.
  - Linhas que o FreeRDP marca como `[ERROR]` mas ocorrem em sessão saudável (Kerberos sem realm)
    ou no encerramento normal (`fsig_term_handler`, `ERRCONNECT_CONNECT_CANCELLED`) vão pro log em
    DEBUG — no log do daphne, `ERROR` de `xfreerdp` é falha de verdade.

---

## [Não publicado] — 2026-08-20 (Atendimento: marcar alguém do grupo com "@" no chat)

### Adicionado

- **Menção com "@" no compositor do chat**: digitar `@` abre a lista de participantes do grupo do
  WhatsApp (clique ou setas + Enter/Tab para escolher). O nome entra no texto como `@João Silva` e a
  pessoa recebe a notificação de menção no WhatsApp.
  - `EvolutionAPIClient.get_group_participants_info()` — participantes **com nome**, lendo
    `phoneNumber`/`id`/`jid` e `name`/`pushName`/`notify` (variam entre versões da Evolution); sem
    nome, o número vira o rótulo.
  - `GET /atendimento/api/conversation/<id>/participants/` — alimenta o autocomplete, com cache de
    5 min por grupo (`?refresh=1` força releitura). Sem cache, cada `@` digitado viraria uma chamada
    HTTP externa no meio da conversa.
  - `services.aplicar_mencoes()` + `ConversationService.send_message(..., mentions=[{nome, phone}])`:
    **o CRM guarda `@João Silva`, o WhatsApp recebe `@<número>`** — é o número no corpo, batendo com
    o `mentioned` do envio, que faz o WhatsApp destacar a menção e notificar; guardar isso no
    histórico encheria o chat do CRM de números.
  - Nome mais longo primeiro na substituição: com "João" e "João Silva" no mesmo grupo, trocar o
    curto antes deixaria `@5511... Silva` na frase.
  - Nota interna não menciona ninguém (a lista nem abre — nada sai pro grupo); `@` no meio de
    palavra não abre a lista (`fulano@empresa.com` não é pedido de menção); e só vai como menção o
    nome que continua escrito na mensagem na hora do envio.

### Testes

- `MencaoNoChatTest` — substituição nome→número e a ordem por tamanho, texto intacto sem menção,
  envio guardando o nome no CRM e mandando o número em `mentions`, nota interna sem `send_text`, API
  repassando as menções, endpoint de participantes servindo do cache na segunda chamada. Suíte do
  módulo: 122 testes, OK.

### Documentação

- `docs/ATENDIMENTO.md` — seção "Marcar alguém do grupo com @ no chat".

---

## [Não publicado] — 2026-08-20 (Correção: modal do chamado não abria; cabeçalho da tabela sobrepondo)

### Corrigido

- **Clicar no chamado não abria nada.** O modal de detalhe foi criado com `z-index:2000` inline, mas
  `.modal-overlay` já é `z-index:9999` no `static/css/style.css` — o modal abria *atrás* do modal da
  lista, escondido pelo overlay preto. Passou a `10050`. Ele também é movido para o `<body>` ao
  abrir: nascendo dentro da aba Tarefas, qualquer ancestral com `transform`/`filter` viraria o
  containing block do `position:fixed` e o prenderia dentro do outro modal.
- **Cabeçalho da tabela por cima dos chamados ao rolar.** O `<th>` é `position:sticky` mas tinha
  fundo translúcido (`rgba(255,255,255,.02)`), então as linhas apareciam por baixo dos títulos.
  Agora usa `var(--card-bg)` sólido, `z-index:2` e uma linha de separação.

---

## [Não publicado] — 2026-08-20 (Clientes: filtros no "Listar Chamados")

### Adicionado

- **Barra de filtros no modal de chamados** da aba Tarefas, tudo aplicado no servidor (vale pro
  histórico inteiro do cliente, não só pelos 300 chamados já carregados):
  - **Busca** por protocolo — aceitando o `#123`/`T-123` como aparece na tela, não só o número —,
    grupo, responsável, categoria, assunto e **texto da resolução**; com debounce de 350ms.
  - **Status** ("Em aberto" = new/open/pending, "Encerrados" = resolved/closed, ou um status
    específico), **Responsável** (incluindo "Sem responsável") e **Categoria** (incluindo "Sem
    categoria"). Os selects são montados só com o que aquele cliente tem, e uma única vez —
    recriá-los a cada filtro apagaria a seleção em curso.
  - **Período** com `date_from`/`date_to` e um seletor de **qual data filtrar**: abertura, última
    mensagem ou encerramento. "Chamados de julho" quer dizer coisas diferentes dependendo de quem
    pergunta — quem abriu no mês não é quem fechou no mês.
  - **Atalhos de período**: Hoje, 7 dias, 30 dias, Este mês, Este ano. A data é montada em horário
    local (não `toISOString()`, que à noite no UTC-3 jogaria o "hoje" pro dia seguinte).
- **Resumo do conjunto filtrado** acima da tabela: Chamados, Em aberto, Encerrados e **tempo médio de
  resolução** (`closed_at - created_at`, formatado `2d 4h` / `3h 12min` por `_duracao_humana`). Os
  três primeiros são clicáveis e aplicam o status correspondente.
- Coluna **Encerrado em** na tabela e aviso quando o resultado passa de 300 chamados ("mostrando os
  300 mais recentes — refine o período").

### Testes

- `ChamadosDoClienteFiltrosTest` — período por abertura vs. encerramento (mesma janela, conjuntos
  diferentes), status agrupado, responsável, busca por `#protocolo` e por texto da resolução, resumo
  acompanhando o filtro e opções trazendo só os responsáveis daquele cliente. Suíte do módulo: 115
  testes, OK.

### Documentação

- `docs/ATENDIMENTO.md` — tabela de filtros na seção "Listar Chamados".

---

## [Não publicado] — 2026-08-20 (Clientes: chamado abre dentro do CRM, sem ir pro Atendimento)

### Alterado

- **Clicar no chamado abre a conversa num modal do próprio CRM**, na página do cliente — antes
  abria `/atendimento/conversation/<id>/` em outra aba. Chat somente leitura com a mesma leitura do
  módulo (cliente à esquerda, equipe à direita, separador por dia, imagem/áudio/vídeo/anexo), com
  status, responsável, datas e resolução no cabeçalho.
- **A lista de chamados deixou de ser staff-only** (`@staff_required` → `@login_required` +
  `pode_acessar_cliente`, no helper `_cliente_do_request`): o próprio cliente, logado no portal,
  acompanha e valida os chamados dele por essa tela. Quem não tem vínculo com o cliente leva 403, e
  o botão voltou a ser renderizado para todo mundo que enxerga a aba Tarefas.

### Adicionado

- **API `GET /atendimento/api/cliente/<cliente_id>/conversations/<conversation_id>/`**
  (`api_cliente_conversation_detail`): cabeçalho do chamado + mensagens, somente leitura.
  **Nota interna não sai para quem não é staff** (filtra `is_internal`/`sender_type='internal'`) —
  é conversa da equipe sobre o chamado, não algo que o cliente deva ler. O chamado precisa pertencer
  ao cliente da URL (404 caso contrário), senão um id de conversa viraria porta de entrada pro
  histórico de outro cliente. Teto de 1000 mensagens por chamado.

### Testes

- `ChamadoDetalheDoClienteAPITest` (staff vê nota interna, cliente do portal não vê, chamado de
  outro cliente dá 404) e dois casos novos em `ChamadosDoClienteAPITest` (usuário do portal vendo os
  próprios chamados, 403 para quem não tem vínculo). Suíte do módulo: 107 testes, OK.

---

## [Não publicado] — 2026-08-20 (Clientes: botão "Listar Chamados" na aba Tarefas)

### Adicionado

- **Botão "Listar Chamados"** na aba Tarefas da página do cliente (`clientes/templates/listar.html`),
  ao lado de "Nova Tarefa": abre um modal com o histórico de chamados daquele cliente no módulo de
  Atendimento, no mesmo formato da tela `/atendimento/historico/` (protocolo, grupo, status,
  categoria, agente, criado em, última mensagem). Clicar numa linha abre o chamado em
  `/atendimento/conversation/<id>/`, em nova aba. Busca no cliente por protocolo, grupo, agente,
  categoria, status e resolução.
- **API `GET /atendimento/api/cliente/<cliente_id>/conversations/`**
  (`atendimento.views.api_cliente_conversations`): busca os chamados por `Conversation.cliente`
  **ou** `group.cliente` — chamados antigos, abertos antes de o grupo do WhatsApp ser vinculado ao
  cliente, ficaram sem `Conversation.cliente` e o histórico apareceria pela metade. Exclui o status
  `pre` (buffer de pré-abertura), marca chamado em tarefa como `T-N` e devolve a resolução junto.
  Protegida por `@staff_required` + `pode_acessar_cliente` (403 para staff de outra instância); o
  botão só é renderizado para `request.user.is_staff`, já que o destino do clique é staff-only.

### Testes

- `ChamadosDoClienteAPITest` — lista com resolução e URL do chamado, vínculo só pelo grupo, `pre`
  fora da lista, acesso negado a quem não é staff. Suíte do módulo: 103 testes, OK.

### Documentação

- `docs/ATENDIMENTO.md` — seção "Listar Chamados na aba Tarefas do cliente (20/08/2026)".

---

## [Não publicado] — 2026-08-20 (Atendimento: agente IA encerra o chamado com a resolução)

### Adicionado

- **Fechamento de chamado pelo agente IA** — o "Tomichinho" já abria tarefa a partir da mensagem;
  agora também **encerra o chamado escrevendo a resolução**, tanto por mensagem no grupo do WhatsApp
  quanto por comentário interno no CRM:
  - Gatilho `_pede_fechamento_de_chamado` (`atendimento/services.py`): exige alvo
    (`chamado`/`atendimento`/`ticket`/`protocolo`) **e** verbo de fechamento
    (`fechar`/`encerrar`/`finalizar`/`concluir` e flexões), tolerante a acento errado. É mais
    exigente que o gatilho de tarefa de propósito — fechar chamado errado custa caro: "pode fechar a
    porta do rack" (sem alvo), "fechar a tarefa" (outro alvo), "o chamado ainda não está resolvido"
    (adjetivo, não verbo) e "não pode fechar o chamado ainda" (`_FECHAR_NEGADO`) não disparam.
  - Task `fechar_chamado_ia` (`atendimento/tasks.py`): lê as últimas 30 mensagens do chamado e pede à
    IA um JSON `{"resolucao": "..."}` orientado a usar **principalmente as respostas do atendente e
    as notas internas** (o que foi verificado e feito), com as mensagens do cliente servindo só para
    descrever o problema — e sem inventar o que não está no histórico.
  - Confirmação `✅ Chamado #N encerrado. 📝 Resolução: ...` sai no mesmo canal do pedido: no grupo,
    se veio do WhatsApp; só como nota interna, se veio de comentário interno (nada que começou
    privado vaza pro cliente).
  - Sem IA configurada ou com falha na chamada, o chamado fecha do mesmo jeito (o pedido foi
    explícito) usando a última resposta do atendente como resolução. Chamado já `resolved`/`closed`
    é ignorado — repetir o pedido não sobrescreve a resolução anterior.

### Alterado

- **`services.finalizar_conversa()`** — o encerramento (status, `closed_at`, resolução, atividade
  `status_changed`, aviso da caixa de entrada por WebSocket, mensagem de encerramento configurada e
  marco "✅ Chamado concluído! 📋 Protocolo #N" no histórico interno) saiu de dentro de
  `views.api_update_conversation` e virou serviço. A view passou a chamá-lo, então tela e agente IA
  fecham chamado exatamente do mesmo jeito. No caminho da IA a mensagem de encerramento configurada
  não é disparada — a confirmação da própria IA já avisa o grupo, e duas mensagens seguidas só
  poluiriam a conversa.

- **Gatilhos do agente IA na caixa normal do chat** — `ConversationService.send_message` só olhava
  os gatilhos quando a mensagem era **nota interna**: "Tomichinho fechar atendimento" (ou "criar
  tarefa") digitado na caixa de resposta do chat não fazia nada, funcionava só se a mesma frase
  viesse do WhatsApp. O novo `_disparar_acoes_ia()` centraliza as AÇÕES (abrir tarefa / fechar
  chamado) e roda nos três caminhos: webhook, caixa normal do chat e comentário interno. A resposta
  conversacional a "tomichinho" continua só no grupo do WhatsApp — no que o atendente manda pela
  plataforma ela viraria mais uma mensagem pro cliente.
- Chamado já `resolved`/`closed` não chega a enfileirar `fechar_chamado_ia` (a task já descartava,
  agora nem dispara): a "Mensagem de encerramento" das configurações ("Finalizamos seu
  atendimento...") sai por `send_message` logo após o fechamento e casava com o gatilho.

### Testes

- `GatilhoFechamentoIATest` (gatilhos e não-gatilhos, negação, nota interna, caixa normal do chat,
  mensagem de encerramento não realimentando o gatilho) e
  `FecharChamadoIATaskTest` (resolução da IA gravada no chamado, prompt recebendo a resposta do
  atendente, pedido interno sem `send_text`, fallback sem IA, chamado já encerrado, marco com
  protocolo). Suíte do módulo: 99 testes, OK.

### Documentação

- `docs/ATENDIMENTO.md` — seção "Agente IA fecha o chamado com a resolução (20/08/2026)".

---

## [Não publicado] — 2026-08-19 (CRM: remove widgets globais de conversas)

### Removido

- **Widgets globais de conversas** (`templates/base.html`), a pedido do usuário — conversas passam
  a ser tratadas exclusivamente dentro do módulo de Atendimento
  (`atendimento/templates/atendimento/base.html`, que é standalone e não foi tocado):
  - Ícones flutuantes por conversa (`#gchatBubbles`) e o modal de chat rápido (`#gchatModal`) que
    apareciam sobre qualquer página do CRM para usuários staff, permitindo responder um atendimento
    sem entrar no módulo.
  - Toast/pop-up de notificação de novo chamado ou mensagem (`#globalTicketToasts`, `showToast`,
    conexão websocket em `/ws/atendimento/inbox/`) que aparecia no canto da tela em qualquer página.
  - Removido em duas passadas na mesma sessão: a primeira review deixou o toast de propósito (achava
    que "ícone de conversas" era só as bolhas+modal), mas o usuário reportou que "os widgets ainda
    estão aparecendo" — o toast também conta como widget de conversa e foi removido.

  O badge de não lidos no botão "Atendimento" da barra lateral (`#globalAtendBtn`) foi mantido, pois
  é o próprio link de entrada no módulo, não um widget de conversa — só perdeu a fonte que o
  alimentava (o toast), então hoje não mostra mais contagem (nenhuma outra rotina escreve nele).

---

## [Não publicado] — 2026-08-19 (Tarefas: aba Kanban na página do cliente, corrige bug de layout)

### Corrigido

- **Aba "Tarefas" aparecia sempre visível, empilhada abaixo da aba "Acessos"**
  ([`clientes/templates/listar.html`](clientes/templates/listar.html)): a `div#tab-tarefas` nascia
  com `style="display: {% if ... %}block{% else %}none{% endif %}"` — o mesmo padrão usado pela aba
  padrão "Acessos" — em vez de `display: none` como as demais abas não-padrão. Resultado: as duas
  ficavam com `display:block` ao mesmo tempo até o usuário clicar em qualquer aba (o JS
  `trocarAba()` já escondia/mostrava corretamente, só a div nascia errada). Corrigido para nascer
  oculta, igual às outras abas.
- **Causa por trás do bug ter sobrevivido a deploy, refresh forçado e aba anônima**: em produção
  `DEBUG=False` e o `TEMPLATES` deste projeto não define `loaders` explicitamente — o Django então
  ativa sozinho o `django.template.loaders.cached.Loader`, que guarda cada template compilado em
  memória por processo. Os workers do `gunicorn` já estavam de pé havia horas (de antes da correção
  no arquivo), então continuavam servindo a versão antiga mesmo com o `.html` já corrigido em disco —
  confirmado batendo direto no socket do gunicorn com uma sessão real antes/depois do
  `systemctl restart gunicorn`. **Mudança em template só entra em produção depois de reiniciar o
  `gunicorn`** (Daphne não serve estas páginas — só `/ws/` e o proxy web de acessos).

### Adicionado

- **Aba "Tarefas" (Kanban) na página do cliente** (`clientes/templates/listar.html`, API em
  [`tarefas/views.py`](tarefas/views.py) — `tarefas_kanban_json`, `tarefa_kanban_criar`,
  `tarefa_kanban_mover`, `tarefa_kanban_editar`, `tarefa_kanban_excluir`,
  [`tarefas/urls.py`](tarefas/urls.py)): colunas Atrasada/Pendente/Em Andamento/Concluída/Cancelada
  com drag-and-drop (SortableJS) escopadas ao cliente da página — tarefa com prazo vencido cai
  sozinha em "Atrasada". Aba controlada pelo módulo `tarefas` em `InstanciaFerramenta`/
  `UsuarioModulo` (migrações `usuario/0007`, `usuario/0009` — adicionam a choice `tarefas`).
- **Tarefa passa a aceitar múltiplos responsáveis** ([`tarefas/models.py`](tarefas/models.py)):
  `Tarefa.assigned_to` (FK) virou `Tarefa.responsaveis` (M2M). Migração de dados
  (`tarefas/migrations/0002_...`, editada à mão) copia cada `assigned_to` existente para
  `responsaveis` antes de remover o campo antigo, com reversão best-effort (guarda só o primeiro
  responsável por ordem de id, já que o FK antigo não suportava múltiplos). Todo lugar que lia
  `assigned_to` foi ajustado: `home/views.py` (`_contexto_tarefas`, quadro geral),
  `tarefas/templates/tarefas/_linha.html` e `_painel.html`, `tarefas/admin.py`.
- **Quem pode ser escolhido como responsável** ([`tarefas/services.py`](tarefas/services.py) —
  `usuarios_atribuiveis`, agora recebe `cliente` em vez de `instancia`): Administradores reais do
  sistema (excluindo contas sem e-mail — sobra de instância de teste), atendentes
  (Consultor/Operador) da instância do cliente, e usuários de portal vinculados a esse cliente
  (principal + adicionais), para o próprio cliente poder participar do vínculo. Nova
  `usuario.perms.colegas_de_instancia` dá a mesma base (back-office da instância) para outros
  seletores futuros de "atendente" (ex. transferir chamado).

---

## [Não publicado] — 2026-08-17/19 (Diversos: 2FA dispositivo confiável, Wiki editável pelo Consultor, sessão de 7 dias, backup em massa, VLAN de switch, Zabbix com IP público bloqueado)

### Adicionado

- **2FA: "confiar neste navegador por 30 dias"** ([`usuario/totp.py`](usuario/totp.py) —
  `criar_dispositivo_confiavel`/`verificar_dispositivo_confiavel`, model `DispositivoConfiavel` em
  [`usuario/models.py`](usuario/models.py), migração `usuario/0008_dispositivoconfiavel`,
  checkbox em [`templates/verificar_2fa.html`](templates/verificar_2fa.html)): marcando a opção na
  verificação do código, um token aleatório é gerado e só o hash (mesmo padrão dos backup codes) vai
  pro banco — o valor original existe só o suficiente pra virar cookie, nunca é persistido.
  Pula o pedido de código nas próximas vezes nesse navegador até expirar ou o dispositivo ser
  revogado.
- **Wiki: Consultor pode criar/editar artigo** (`usuario/perms.py` — `pode_editar_wiki`,
  `clientes/decorators.py` — `wiki_edicao_required`): antes só quem tinha `is_admin_bo` via os
  botões de criar/editar nas 3 telas da Wiki (`buscar.html`, `dashboard.html`,
  `visualizar_artigo.html`). Agora Consultor também vê os botões se a ferramenta `wiki` estiver
  liberada pra instância dele — Operador e portal do cliente final continuam só leitura. Deletar
  artigo segue exclusivo do Administrador (a Wiki é uma base global, sem `instancia`/`cliente` no
  model — o artigo que um Consultor edita é o mesmo que todas as instâncias leem).
- **Backup em massa de hosts** (`clientes/views.py` — `listar_acessos_backup_habilitado`,
  `listar_acessos_sem_backup`, `configurar_backup_massa`, rotas em
  [`clientes/urls.py`](clientes/urls.py)): tela pra ver de uma vez quais acessos já têm backup
  automático habilitado e quais não têm, e ligar/desligar em lote em vez de host por host.
- **Backup de VLANs de switch por cliente** (`clientes/views.py` — `l2vpn_switches_cliente`,
  `vlans_switches_cliente`, `vlans_backup_acesso`, extensão de
  [`clientes/l2vpn_parser.py`](clientes/l2vpn_parser.py)): lista os switches do cliente e guarda um
  snapshot das VLANs configuradas em cada um, no mesmo espírito do inventário de portas PON já
  existente pra OLT.

### Corrigido

- **Zabbix com IP público, mas só acessível de dentro da rede do cliente**
  (`monitoramento/views.py` — `_responde_como_zabbix`, `_get_config_com_tunel`): antes, IP público
  do Zabbix = sempre conexão direta, sem tentar o túnel SSH do proxy. Caso real: DS TECH
  (186.235.160.21) tem IP público mas o acesso externo à API é bloqueado — de fora cai num redirect
  do Apache pro site institucional, e o gráfico de Monitoramento simplesmente não teve dado nenhum.
  Agora, se a conexão direta não responder como Zabbix de verdade (`apiinfo.version` sem `result` no
  JSON) e o cliente tiver um proxy SSH cadastrado, cai pro túnel automaticamente.
- **Sessão expirava em 1h no meio de navegação longa** ([`crm/settings.py`](crm/settings.py) —
  `SESSION_COOKIE_AGE`): 1 hora era curto demais pra sessões mais longas dentro do proxy web de
  acessos, por exemplo. Ampliado pra 7 dias de inatividade (o timer renova a cada request,
  `SESSION_SAVE_EVERY_REQUEST=True`).
- **Badge "Assumidos" ficava com contagem fantasma** (`atendimento/templates/atendimento/base.html`
  — `window.__syncOpenBadges`): ao resolver ou transferir o último chamado assumido, o item saía da
  lista mas nada recalculava o `#badge-mine` — sobrava um "1" sobre uma aba vazia até dar F5. Passou
  a contar os itens que restaram na própria lista, igual às outras abas. O SPA do Inbox
  (`_chat_content.html`) tinha o mesmo problema pra `.inbox-tab-list` — corrigido junto.
- **Login perdia o destino após erro** ([`templates/login.html`](templates/login.html)):
  formulário não reenviava o parâmetro `next`, então um erro de senha ou token expirado no
  login jogava o usuário pro dashboard em vez de voltar pra página que ele tentou acessar
  originalmente. Adicionado `<input type="hidden" name="next">`.

---

## [Não publicado] — 2026-08-19 (Wiki: vendors de GPON/OLT nas escolhas de fabricante)

### Adicionado

- **ZTE, Fiberhome, Parks, VSOL, Intelbras e Raisecom** adicionados a `ArtigoWiki.FABRICANTES`
  ([`wiki/models.py`](wiki/models.py), migração `wiki/migrations/0003_alter_artigowiki_fabricante.py`) —
  vendors de OLT/GPON pedidos pelo usuário. Escopo intencionalmente restrito à Wiki: o projeto tem mais 3
  listas de fabricante (`BackupTemplate`, `ScriptCRM`, `AgentKnowledge`), mas nenhuma é "a" lista de GPON —
  o conceito de OLT/GPON no sistema hoje usa texto livre (`Modelo_equipamento.fabricante`) com
  reconhecimento heurístico em `clientes/tasks.py`, sem choices fixas; mexer em `BackupTemplate` exigiria
  também ajustar essa lógica de seleção de template pra não regredir cadastros já existentes — fora do
  pedido desta vez. Os formulários de cadastro/edição/busca de artigo já iteram sobre `FABRICANTES`
  genericamente, então os novos vendors aparecem sem mudança de template. Cobertura de teste:
  `wiki/tests.py` (`FabricantesGponTest`).

---

## [Não publicado] — 2026-08-19 (Wiki: filtro por fabricante dentro da categoria)

### Adicionado

- **Filtro por fabricante dentro da tela de categoria** ([`wiki/views.py`](wiki/views.py) — `listar_por_categoria`,
  [`wiki/templates/wiki/listar_artigos.html`](wiki/templates/wiki/listar_artigos.html)): o campo
  `ArtigoWiki.fabricante` já existia, mas só dava pra filtrar por ele na tela de busca geral ou numa URL à
  parte (`wiki/fabricante/<x>/`), nunca combinado com uma categoria específica. Agora, ao entrar numa
  categoria (ex. "BGP"), aparece uma barra de pills com os fabricantes que **realmente têm artigo naquela
  categoria** (ex. MikroTik (2), Huawei (1)) — clicar filtra a lista via `?fabricante=X` no servidor, sem
  misturar contagem com outras categorias. Sem alteração de modelo/migration — o dado já existia, faltava
  só a UI e o filtro combinado categoria+fabricante. Cobertura de teste: `wiki/tests.py`
  (`ListarPorCategoriaFabricanteTest`).

---

## [Não publicado] — 2026-08-19 (Atendimento: erro de acentuação no gatilho de tarefa)

### Corrigido

- **"tarefá" (acento errado) não disparava a criação de tarefa** ([`atendimento/services.py`](atendimento/services.py) —
  `_normalizar_texto`, `_pede_abertura_de_tarefa`, `_disparar_agente_ia`): caso real, "Tomichinho, criar
  tarefá de configuração do radius do erp hubsoft." (nota interna) não criou tarefa nenhuma — a comparação
  era caractere a caractere e "tarefá" ≠ "tarefa". Agora o texto é normalizado (acentos removidos via
  `unicodedata`) antes de qualquer checagem de gatilho, tanto pra "tomichinho" quanto pro pedido de tarefa
  — cobre erro de digitação com acento em qualquer uma das palavras-chave. Cobertura de teste:
  `atendimento/tests.py` (`test_erro_de_acentuacao_em_tarefa_ainda_dispara`,
  `test_nota_interna_com_acento_errado_em_tarefa_ainda_dispara`).

---

## [Não publicado] — 2026-08-19 (Usuários: Consultor/Operador nasciam sem is_staff e sumiam do atendimento)

### Corrigido

- **CRÍTICO — todo Consultor e Operador criado ou editado ficava trancado fora do atendimento**
  ([`usuario/views.py`](usuario/views.py) — `cadastrar_usuario`, `editar_usuario`, novo helper
  `_is_staff_para_role`): `is_staff` só era setado como `True` quando o role era Administrador
  (`is_staff=(role == PerfilUsuario.ROLE_ADMIN)`) — Consultor e Operador sempre recebiam `is_staff=False`,
  mesmo sendo back-office de verdade (`perms.is_backoffice`). Como TODO o módulo de atendimento é protegido
  por `staff_required` (checa `request.user.is_staff` cru), isso significava: nenhum Consultor/Operador
  conseguia sequer abrir a tela de atendimento, e nenhum aparecia na lista de "atendentes" pra transferir
  chamado (`api_agents_list` também filtra por `is_staff=True`). Achado ao investigar por que um Operador
  específico (Guilherme) não aparecia como atendente — na checagem, **100% das contas Consultor/Operador
  do banco** (4 de 4) estavam com `is_staff=False`, não era um caso isolado.
  `is_staff` agora é `True` para Admin, Consultor e Operador (só "cliente"/portal fica de fora), tanto na
  criação quanto na edição. As 4 contas já existentes foram corrigidas diretamente no banco (mesma regra).
  Cobertura de teste: `usuario/tests.py` (`IsStaffParaRoleTest`, `CadastrarUsuarioIsStaffTest`).

---

## [Não publicado] — 2026-08-19 (Atendimento: tarefa criada pela IA agora usa a mensagem do cliente)

### Corrigido

- **"abrir tarefa" só olhava pro próprio comando, não pro problema relatado** ([`atendimento/tasks.py`](atendimento/tasks.py) —
  `abrir_tarefa_ia`, `_contexto_conversa`): o comando que dispara a tarefa é quase sempre curto e sem
  detalhe ("Tomichinho, criar tarefa") — o pedido de verdade está na mensagem do cliente logo antes. A IA
  só recebia o comando isolado, então o título saía genérico tipo "Criar tarefa para Tomichinho". Agora
  `abrir_tarefa_ia` manda o histórico recente da conversa (até 12 mensagens) pra IA, com instrução explícita
  pra montar a tarefa a partir do que o CLIENTE relatou, não do comando em si. Sem IA configurada, o
  fallback também melhorou: usa a última mensagem do cliente como título em vez do comando vazio. Caso real
  testado: cliente relatou problema de MTU (pacotes acima de 1442 bytes não passavam) e "Tomichinho, criar
  tarefa" sozinho não dizia nada — agora a tarefa sai com "Verificar MTU" e a descrição do problema.
  `responder_tomichinho` foi ajustado pra usar o mesmo helper de histórico (`_contexto_conversa`), sem
  mudança de comportamento. Cobertura de teste: `atendimento/tests.py` (novos casos em `AbrirTarefaIATaskTest`).

---

## [Não publicado] — 2026-08-19 (Atendimento: "criar tarefa" não disparava o agente IA)

### Corrigido

- **Gatilho de tarefa só reconhecia o literal "abrir tarefa"** ([`atendimento/services.py`](atendimento/services.py) —
  `_pede_abertura_de_tarefa`): caso real relatado, "Tomichinho, criar tarefa" não criava tarefa nenhuma
  porque a mensagem não continha exatamente a frase "abrir tarefa". Trocado por uma checagem mais tolerante
  (palavra "tarefa" perto de um verbo comum de criação: abrir/criar/gerar/nova), usada tanto no gatilho por
  WhatsApp quanto por nota interna. "essa tarefa está atrasada" continua não disparando nada.

---

## [Não publicado] — 2026-08-19 (Atendimento: nota interna vazava pro WhatsApp do cliente)

### Corrigido

- **CRÍTICO — "Comentário Interno" nunca foi realmente interno**
  ([`atendimento/services.py`](atendimento/services.py) — `ConversationService.send_message`,
  [`atendimento/views.py`](atendimento/views.py) — `api_send_message`): o toggle "Comentário Interno" do
  chat manda `is_internal: true` pro backend desde que a tela existe, mas `api_send_message` nunca lia esse
  campo — toda mensagem, marcada como nota interna ou não, seguia o mesmo caminho e era enviada de verdade
  pro grupo do WhatsApp do cliente como se fosse uma resposta normal (`sender_type` sempre `'agent'`).
  Ou seja: qualquer anotação privada da equipe sobre o cliente (cobrança, reclamação interna, etc.)
  digitada com o toggle ligado estava sendo entregue ao próprio cliente.
  `send_message` agora aceita `is_internal` de verdade: quando ligado, salva a mensagem com
  `sender_type='internal'`, **não** dispara o envio ao WhatsApp, e não conta como primeira resposta
  (`first_response_at`) nem como atividade recente do chamado (`last_message_at`) — nota interna não é
  resposta ao cliente, então não pode mascarar um chamado sem atendimento de verdade pra SLA/varredura de
  "chamado sem resposta". Cobertura de teste: `atendimento/tests.py` (`NotaInternaTest`).

---

## [Não publicado] — 2026-08-19 (Atendimento: agente IA "Tomichinho" também lê nota interna)

### Adicionado

- **Gatilho "abrir tarefa" também funciona em nota interna** ([`atendimento/tasks.py`](atendimento/tasks.py) —
  `abrir_tarefa_ia`, [`atendimento/services.py`](atendimento/services.py)): além de mensagens recebidas do
  WhatsApp, uma nota interna contendo "abrir tarefa" agora também dispara a criação da `Tarefa` vinculada
  ao cliente do grupo. A confirmação ("✅ Tarefa aberta: ...") fica só no CRM como outra nota interna —
  nunca sai pro WhatsApp, já que a nota que originou o pedido também não saiu. "tomichinho" continua só
  valendo pra mensagem recebida do WhatsApp: resposta automática a partir de uma nota interna vazaria pro
  grupo do cliente, então não foi habilitado. Cobertura de teste: `atendimento/tests.py`
  (`AbrirTarefaIAInternaTest`, casos de nota interna em `NotaInternaTest`).

---

## [Não publicado] — 2026-08-19 (Atendimento: agente IA "Tomichinho" — resumo, resposta e abertura de tarefa)

### Adicionado

- **Agente IA do atendimento** ([`atendimento/ai.py`](atendimento/ai.py) — novo,
  [`atendimento/tasks.py`](atendimento/tasks.py), [`atendimento/services.py`](atendimento/services.py)),
  usando o provedor configurado em Configurações → Integração IA (Claude ou ChatGPT):
  - **Chamado parado ganha resumo por IA**: `notificar_chamados_abertos` (varredura a cada 10 min, já
    existente) agora inclui, por chamado sem resposta, um resumo de 1 linha do que o cliente pediu —
    gerado a partir das últimas mensagens dele. Sem IA configurada ou se a chamada falhar, a notificação
    sai igual a antes, só sem a linha de resumo (nunca depende da IA pra funcionar). Habilitado agora
    para o grupo **TOMICH TEC - NOC** (`notif_abertos_enabled`/`notif_abertos_group_id`).
  - **"tomichinho" na mensagem**: qualquer remetente do grupo (atendente ou cliente) escrevendo
    "tomichinho" dispara `responder_tomichinho` — a IA lê as últimas mensagens do chamado e responde
    direto no grupo do WhatsApp (mensagem salva com `sender_type='ai'`).
  - **"abrir tarefa" na mensagem**: dispara `abrir_tarefa_ia` — a IA extrai título/descrição do pedido e
    cria uma `Tarefa` (app `tarefas`) vinculada ao Cliente do grupo, sem responsável definido. Sem
    cliente vinculado ao grupo, a tarefa não é criada (não há onde colocá-la); sem IA configurada, usa o
    próprio texto da mensagem como título, pra não perder o pedido.
  - Os dois gatilhos são detectados no `process_webhook` e disparados como tasks Celery (`_disparar_agente_ia`),
    sem bloquear o webhook nem depender da IA responder a tempo.
  - Cobertura de teste: `atendimento/tests.py` (`AgenteIACallTest`, `GatilhoAgenteIATest`,
    `ResponderTomichinhoTaskTest`, `AbrirTarefaIATaskTest`).

---

## [Não publicado] — 2026-08-19 (Atendimento: ChatGPT como provedor de IA)

### Adicionado

- **Tela de Configurações → Integração IA** ([`atendimento/templates/atendimento/configuracoes.html`](atendimento/templates/atendimento/configuracoes.html),
  [`atendimento/views.py`](atendimento/views.py) — `configuracoes`/`api_settings`) ganhou um seletor de **Provedor**
  (Claude/Anthropic ou ChatGPT/OpenAI). Antes só existia um campo genérico de API key/modelo, implicitamente
  fixo em Claude — não dava para cadastrar credenciais OpenAI. Agora Claude e ChatGPT têm API key e modelo
  próprios, salvos em paralelo (`ai_openai_api_key`, `ai_openai_model`, além de `ai_provider`), e a tela mostra
  só os campos do provedor selecionado. O System Prompt continua único, compartilhado entre os dois provedores.

---

## [Não publicado] — 2026-08-19 (Atendimento: transferência para si mesmo)

### Corrigido

- **Não era possível transferir um chamado em andamento para o próprio usuário** ([`atendimento/views.py`](atendimento/views.py) — `api_agents_list`):
  o endpoint que alimenta o modal de transferência excluía sempre `request.user` da lista de
  atendentes (`.exclude(id=request.user.id)`). Isso impedia um cenário legítimo: o chamado está
  atribuído a um colega e o operador quer puxá-lo para si via "Transferir" — o próprio nome
  simplesmente não aparecia como opção. O `exclude` foi removido; o usuário logado agora aparece
  normalmente na lista quando faz parte da instância (via `colegas_de_instancia`).

---

## [Não publicado] — 2026-08-19 (2FA: tela de configuração travava no primeiro login)

### Corrigido

- **A tela de configuração do 2FA prendia o usuário atrás de um overlay** ([`usuario/templates/configurar_2fa.html`](usuario/templates/configurar_2fa.html)):
  no primeiro login o aviso "autenticação em duas etapas obrigatória" era um modal Bootstrap com
  `data-bs-backdrop="static"` e `data-bs-keyboard="false"` — clique fora não fecha, ESC não fecha.
  Como o `.modal-backdrop` tem `z-index: 1040 !important` e a `.top-bar` do sistema tem
  `z-index: 1000`, o backdrop cobria a barra inteira: Dashboard, menu do usuário e o próprio
  "Deslogar" ficavam atrás dele, e todo clique era engolido. Pior, o script reabria o modal a cada
  carregamento e o `Forcar2FAMiddleware` devolve o usuário para essa mesma página a cada tentativa
  de navegar — o resultado era tela escura e nada clicável, em loop.
- O aviso virou **inline**, no topo do card: mesma informação, sem overlay, sem backdrop e sem
  auto-abertura. A barra superior volta a responder, o formulário fica utilizável de imediato e o
  aviso traz um link direto para o logout, para quem preferir configurar depois.

A obrigatoriedade não mudou: o middleware continua devolvendo qualquer rota para `/auth/2fa/`
enquanto o dispositivo não estiver confirmado. Conferido com um login recém-criado (criado e
revertido em transação). Detalhes em [`docs/2FA_GOOGLE_AUTHENTICATOR.md`](docs/2FA_GOOGLE_AUTHENTICATOR.md).

---

## [Não publicado] — 2026-08-19 (Instâncias: limpeza do lixo de teste e criação da Principal)

### Corrigido

- **O cadastro de Operador listava seis instâncias onde deveriam existir duas.** Cinco delas
  eram lixo de um script de verificação rodado contra o banco de produção em 02/08/2026
  (`I_ea19cb`, `I_1ae493`, `Instancia1_466dee`, `Instancia2_466dee`, `Instancia1_6cb110`), junto
  com cinco logins (`c_ea19cb`, `c_1ae493`, `cons_466dee`, `op_466dee`, `cons_6cb110`) e dois
  clientes fake sem nenhum acesso. Removidos, com dump prévio em
  `backups/lixo_teste_instancias_20260819.json`. Nada no repositório recria esses registros.
- **A operação do Administrador não existia como instância**: cliente criado por ele sem escolher
  instância nascia com `instancia = NULL` — 47 clientes e 16 tarefas assim. Além de nunca aparecer
  no dropdown, isso tornava impossível ter Operador da operação principal:
  `pode_acessar_cliente` exige, para Consultor/Operador, instância não-nula batendo com a do
  cliente, então esse operador não enxergaria cliente nenhum. Criada a instância **Principal**,
  com os 47 clientes e as 16 tarefas migrados e as 18 ferramentas habilitadas.

Administradores seguem fora de qualquer instância (`get_role` trata `is_staff` sem
`PerfilUsuario` como admin legado) e continuam vendo tudo — 49 clientes visíveis antes e depois.
Conferido com um Operador simulado na Principal, criado e revertido em transação: 47 clientes
visíveis, ferramentas liberadas e acesso negado a cliente de outra instância. Detalhes em
[`docs/PERMISSOES_CONSULTOR.md`](docs/PERMISSOES_CONSULTOR.md).

---

## [Não publicado] — 2026-08-18 (Páginas de erro próprias no lugar da tela de debug do Django)

### Corrigido

- **O CRM rodava com `DEBUG = True` em produção** ([`crm/settings.py`](crm/settings.py)): qualquer
  página que falhasse devolvia a tela de debug do Django — o 404 listava todas as rotas do projeto
  e um 500 mostraria traceback, código-fonte, variáveis locais e as configurações (banco, chaves).
  Agora `DEBUG = os.environ.get('DJANGO_DEBUG', '0') == '1'`, ou seja, desligado por padrão e
  religável pontualmente pelo serviço sem editar código.

### Adicionado

- **Telas de erro no visual do CRM** (`templates/400.html`, `403.html`, `403_csrf.html`,
  `404.html`, `500.html`): mensagem em português, sem detalhe técnico, com botões de voltar e ir
  para o início. O 500 informa "Erro interno do sistema" e avisa que a falha foi registrada; o
  `403_csrf` explica sessão expirada e leva de volta ao login. São páginas autocontidas (CSS
  embutido, sem `extends` nem `{% static %}`), porque o handler de 500 renderiza sem contexto —
  um template de erro que depende de contexto vira outro erro.
- **`LOGGING` gravando em `logs/django-erros.log`** ([`crm/settings.py`](crm/settings.py)): com
  `DEBUG=False` o traceback deixaria de aparecer na tela **e** não iria para lugar nenhum (o
  handler de console padrão do Django é filtrado por `require_debug_true` e o `mail_admins` não
  tem e-mail configurado). Agora `django.request`, `clientes` e `atendimento` escrevem em arquivo
  rotativo (10 MB × 5) e no journal (`journalctl -u gunicorn`).

### Corrigido (robustez)

- **Context processors não quebram mais a própria página de erro**
  ([`financeiro/context_processors.py`](financeiro/context_processors.py),
  [`usuario/context_processors.py`](usuario/context_processors.py)): ambos liam `request.user`
  direto. Quando o Django renderiza 404/403 antes de o `AuthenticationMiddleware` ter posto o
  atributo, isso estoura `AttributeError` e o usuário recebe um 500 cru no lugar da tela amigável.
  Passaram a usar `getattr(request, 'user', None)`.

## [Não publicado] — 2026-08-18 (Proxy web: SPA do device não sequestra mais a barra de endereços)

### Corrigido

- **404 do Django ao recarregar a página web de um acesso** ([`clientes/proxy_engine.py`](clientes/proxy_engine.py)):
  o shim injetado no proxy só reescrevia `history.pushState`/`replaceState` quando a URL começava
  com `/`. O vue-router monta a URL **absoluta** (`location.protocol + '//' + location.host + base + path`),
  então nada era reescrito e a barra de endereços saía do proxy: na OLT do acesso 889 (base `/admin/`)
  virava `https://crm.tomich.com.br/admin/monitor/overview/device_information`. A página seguia
  funcionando — fetch/XHR continuavam reescritos —, mas um F5 pedia esse caminho ao CRM e caía no
  `catch_all_view` do admin do Django (*Page not found (404)*). Agora os dois interceptadores usam
  `_rw()`, que já normaliza string relativa, string absoluta do mesmo origin e objeto `URL`; a URL
  passa a ser `/clientes/acessos/889/web/443/https/admin/monitor/...` e o F5 volta pelo proxy
  (o device responde o `index.html` da SPA nessa rota). Mesmo tratamento em `location.assign/replace`,
  que é o fallback do vue-router quando o `pushState` falha.

## [Não publicado] — 2026-08-18 (BGP: downstream sem filtro de bogons)

### Corrigido

- **A sessão de cliente não leva mais a lista de bogons** ([`clientes/bgp_community_auto.py`](clientes/bgp_community_auto.py)):
  na policy de entrada de um downstream o node 10 casa a prefix-list **do próprio cliente** —
  os blocos informados no formulário, e só. Bogon nenhum chega até ali, então as 15 linhas de
  `BOGONS-V4-IN` mais o node que as usava eram config que nunca casa. A config de um cliente
  v4+v6 caiu de 63 para 47 comandos nas caixas que já tinham a lista pronta, e de 78 para 47
  nas que ainda precisavam criá-la.
- O filtro continua onde faz falta: upstream, IX e CDN aceitam a tabela cheia no node 10, e é o
  `deny node 5` que segura os bogons — com a lista em `permit`, única forma que casa
  (`if-match ip-prefix` só casa o que a lista permite).

---

## [Não publicado] — 2026-08-18 (BGP: grade só com circuito no ar, visão geral removida)

### Alterado

- **A grade de circuitos mostra só o que está no ar** ([`clientes/templates/bgp_automacao.html`](clientes/templates/bgp_automacao.html)):
  pedido do operador olhando o painel de uma das caixas — na linha de upstream só o `c-01` tem
  sessão, mas a grade exibia os 13 circuitos mapeados, 12 deles marcados "sem sessão BGP"; em
  IX eram 10 cards e nenhuma sessão. Agora fica na grade apenas circuito **com sessão BGP**.
  Circuito que tem os community-filters mas nenhum peer (config pela metade) e slot do template
  nunca usado são a mesma coisa para quem opera — "dá pra subir uma sessão aqui" — e ficam os
  dois atrás do card **＋** de cada família. Naquela caixa a grade caiu de 28 cards para 4.
- O seletor do formulário lista os dois casos distinguindo-os (`c-02 — 65084:502xx · config
  pronta, sem sessão` / `c-08 — 65084:508xx · slot livre`); escolher um circuito que já existe
  traz junto o nome, o ASN remoto e o ASN de prepend dele, e a geração continua emitindo só o
  que falta.

### Removido

- **Visão geral (matriz prefixo × circuito)**: com 25 circuitos não ficava legível nem como
  consulta só-leitura. O painel do destino, que já mostra o efeito real prefixo a prefixo,
  passa a ser o único caminho.

---

## [Não publicado] — 2026-08-18 (BGP: fake-AS na sessão e painel só com o que está configurado)

### Adicionado

- **fake-AS no formulário de subir sessão** ([`clientes/bgp_community_auto.py`](clientes/bgp_community_auto.py)):
  a opção "apresentar outro ASN nesta sessão" emite `peer <IP> fake-as N`. Com ela marcada, o
  campo de prepend vira espelho do fake-AS — e isso não é conveniência de tela, é correção:
  **prepend só conta se repetir o ASN que o peer enxerga**. Prepender o ASN real numa sessão
  com fake-as não alonga o caminho para aquele vizinho, a rota chega com o mesmo AS_PATH de
  sempre. A combinação divergente (`fake_as=52995` com `prepend_as=268080`) é recusada em vez
  de gerar config que parece certa e não faz nada.
- O fake-as é emitido **por peer**, não no peer-group, porque é assim que `parse_huawei` o lê
  de volta — no grupo, o painel perderia de vista qual ASN aquele vizinho enxerga. Sessões que
  já usam fake-as passaram a mostrá-lo no painel, e o mapa avisa quando a policy de saída
  prepende um ASN diferente do fake-as da sessão (vale para config feita à mão antes desta
  automação).

### Alterado

- **O painel voltou a mostrar só o que existe** ([`clientes/templates/bgp_automacao.html`](clientes/templates/bgp_automacao.html)):
  os slots livres do template saíram da grade. Cada família lista os circuitos configurados e
  termina num card "＋ adicionar", que abre o formulário já com os livres num seletor
  (`c-04 — community 65100:504xx`). Uma caixa com 6 upstreams e nenhum IX passa de 25 cards,
  19 deles buracos, para 6 cards e dois botões de adicionar.

52 testes em [`clientes/tests_bgp_community_auto.py`](clientes/tests_bgp_community_auto.py).

---

## [Não publicado] — 2026-08-18 (BGP: subir circuito e sessão do zero pelo painel)

### Adicionado

- **Slot vago virou ponto de partida** ([`clientes/bgp_community_auto.py`](clientes/bgp_community_auto.py)):
  o painel passou a mostrar também os circuitos que o template prevê e a caixa não tem — 119
  slots vagos nas 8 caixas Huawei. Clicar num deles (`c-02`, `ix-04`, `cdn-03`) abre o
  formulário que sobe o circuito **inteiro** em um `commit` só: community-filters,
  route-policy de entrada, route-policy de saída no layout do template e o bloco
  `bgp <ASN>` com a sessão. O grupo de community não é perguntado — é conta do próprio slot
  (§6/§7/§8: c-02 → 502, ix-07 → 607, cdn-03 → 613) —, e o ASN da community sai da maioria
  dos circuitos já configurados, não de um padrão fixo (as caixas usam 65100 e 65101, ASN
  privado diferente do ASN do `bgp <N>`).
- **IX sai no formato de IX**: `group EBGP-<NOME>-V4 external` com os route servers como
  membros, `public-as-only` e as route-policies **no grupo** — que é como o IX.br é
  configurado nas caixas em produção e, não por acaso, o que o parser consegue ler de volta.
  Operadora e CDN saem com peer individual e as policies no peer.
- **Sessão de downstream** ([`comandos_criar_downstream`](clientes/bgp_community_auto.py)):
  na saída o cliente recebe a tabela cheia; na entrada só passam os prefixos dele, pela
  prefix-list `PL-DOWNSTREAM-<NOME>` gerada a partir do formulário, com `deny node 999` no
  fim. E o detalhe que faz a sessão servir para alguma coisa: **as communities de reanúncio
  vão na policy de ENTRADA** — as policies de saída terminam em `deny node 999`, então sem
  carimbar as rotas do cliente na entrada elas ficariam presas no equipamento. O formulário
  traz a mesma lista de destinos do "originar prefixo".
- **Os clientes que já existem apareceram sozinhos**: `mapear_downstreams` descobre uma sessão
  de downstream sem depender de nome — sessão fora do catálogo de communities cuja policy de
  saída libera a tabela cheia, com iBGP excluído. Achou 11 clientes nas caixas atuais, cada um
  com o que pode mandar e para onde é reanunciado, agrupando IPv4 e IPv6 do mesmo cliente
  mesmo quando as policies seguem convenções diferentes (`-V4-IN`, `-IPv4_out`, `-IPV6-OUT`).

### Corrigido

- **"Subir desabilitada" não desabilitava nada**: a primeira versão apenas omitia o
  `peer … enable`, mas no VRP o peer nasce ativo — a sessão subiria junto com a config. Agora
  sai `peer … ignore`, exatamente o comando que o botão Ativar do painel desfaz.
- **Filtro de bogons que não filtra**: `if-match ip-prefix X` só casa quando **X permite** a
  rota, então a `BOGONS-V4` do template, escrita toda em `deny`, nunca casa — o `deny node 5`
  que a usa é config morta e os bogons passam pelo node seguinte, que aceita `0.0.0.0/0
  less-equal 24`. A geração reaproveita uma lista de bogons em `permit` (as caixas já têm a
  `BOGONS-V4-IN`) ou cria uma a partir do §2. E recusa a lista que permite a tabela inteira:
  a BOGONS de uma das caixas tem `permit 0.0.0.0/0 greater-equal 25 less-equal 32`, que num
  `deny node` derrubaria a sessão inteira — a mesma entrada enganava a detecção de full
  routing, agora resolvida exigindo `len_min == 0`.
- **Peer IPv6 na address-family errada**: todo peer/grupo v6 gerado leva `undo peer … enable`
  na `ipv4-family unicast` além do `enable` na v6 — no VRP ele nasce habilitado na v4 mesmo
  sendo IPv6, e é isso que as caixas em produção têm.

### Alterado

- Proteções contra o erro caro deste formulário: nome que colida com a route-policy ou o
  peer-group **de outro circuito** é recusado (subir `ix-05` chamando de "PTT-SP" quando
  `AS26162-PTT-SP-V4-OUT` já é do `ix-01` faria os nodes do circuito novo entrarem na policy
  do antigo, e no caso do peer-group trocaria as policies de uma sessão de IX no ar — nenhum
  dos dois apareceria como erro no equipamento). Também recusa peer já configurado, IP na
  família errada e prefix-list de cliente já existente.
- Circuito que já existe reaproveita a policy de saída que ele tem, mesmo com nome fora da
  convenção; o formulário passou a mostrar isso enquanto o operador preenche, em vez de deixar
  a descoberta para o preview.
- A atualização otimista do painel passou a registrar o circuito completo (filtros, nodes da
  policy de saída e sessões), e não só os community-filters: sem isso o slot recém-criado
  continuaria aparecendo como vago até o próximo backup, e o operador aplicaria tudo de novo.

26 gerações conferidas contra as 8 caixas Huawei reais (todos os tipos, com e sem
`habilitar`), 48 testes em [`clientes/tests_bgp_community_auto.py`](clientes/tests_bgp_community_auto.py).
Nenhum comando foi enviado a equipamento durante o desenvolvimento. Detalhamento em
[`docs/bgp_automacao.md`](docs/bgp_automacao.md).

---

## [Não publicado] — 2026-08-18 (BGP por community: IX, CDN, "anunciar para todos" e painel por destino)

### Adicionado

- **Circuitos de IX e CDN entraram no mapa** ([`clientes/bgp_community_auto.py`](clientes/bgp_community_auto.py)):
  a automação só reconhecia `c-NN`, mas as caixas que já seguem o template otimizado usam três
  famílias — `c-NN` (operadora/upstream), `ix-NN` e `cdn-NN`. No acesso 20 o sistema enxergava
  10 dos 25 circuitos configurados; no acesso 923, 9 de 28. Nas caixas antigas, em que até o IX
  se chama `c-NN` (c-81..c-83), o tipo real vem do `glob-all-*` que a própria policy de saída
  referencia — informação da config, não palpite pelo nome.
- **Communities globais viraram destino manipulável**: `glob-all-upstream`, `glob-all-ptts-ixbr`
  e `glob-all-cdns` ("anunciar para todos os upstreams/IX/CDNs" — §4/§15 da especificação) são
  descobertas com o **alcance real**, isto é, quais circuitos têm um node casando aquele filtro.
  Marcar um prefixo com uma community global sem alcance é recusado com essa explicação: nas
  caixas antigas o bloco `glob-*` inteiro foi colado e nunca referenciado (config morta, hoje
  reportada em um aviso consolidado).
- **Efeito real de cada prefixo, além da intenção declarada**: no VRP vale o primeiro node que
  casa, e a global fica no node 12 enquanto os prepends individuais ficam em 13-16 — um prefixo
  marcado com `glob-all-upstream` **e** `c-01-export-2p` é anunciado **sem prepend**, e o node
  individual nunca roda. O mapa resolve isso por ordem de node e o painel mostra o efeito em
  vigor, com quais filtros perderam a disputa.
- 22 testes novos em [`clientes/tests_bgp_community_auto.py`](clientes/tests_bgp_community_auto.py).

### Corrigido

- **"Anunciar com no-export" não anunciava nada**: a ação `export-ne` era gerada como node
  `deny` + `apply community no-export` — num node `deny` a rota não é anunciada e o `apply` nem
  chega a rodar, ou seja, o oposto do rótulo. Agora é `permit` + `apply` (§9 do template), e o
  `deny` que existe nas caixas antigas passou a ser reportado como inconsistência.
- **Sessões de IX apareciam como inexistentes** ([`clientes/backup_parser.py`](clientes/backup_parser.py)):
  IX é configurado em peer-group (`group EBGP-PTT-SP-V4 external` + N route servers), com as
  route-policies **no grupo**, não no peer. O parser lia só o peer, então todo circuito de IX
  ficava "sem sessão BGP" e a automação se recusava a agir sobre ele. Passou a herdar
  `route-policy … import/export` do grupo quando o peer não tem a sua — como o VRP se comporta.
  No acesso 20, circuitos com sessão vinculada foram de 3 para 7; em 33 backups conferidos,
  nenhuma sessão perdida e 80 `policy_out` preenchidas.
- **`enable`/`undo … enable` passaram a respeitar a address-family**: um grupo IPv6 é desligado
  na `ipv4-family unicast` e ligado na `ipv6-family unicast` — lendo o arquivo inteiro de uma
  vez, todos os peers de IX v6 apareciam desabilitados.

### Alterado

- **O painel deixou de despejar tudo de uma vez**
  ([`clientes/templates/bgp_automacao.html`](clientes/templates/bgp_automacao.html)): a matriz
  prefixo × todos-os-circuitos, com 25 circuitos, era uma parede de seletores. Agora são dois
  passos — cards de destino agrupados por tipo (operadoras, IX, CDNs, "anunciar para todos") e,
  ao clicar em um, o painel daquele destino com as sessões BGP, como a policy de saída traduz
  cada community (`node → filtro`) e os prefixos com busca, filtro de família, "só os que vão
  para cá", o efeito atual de cada um e o seletor de como anunciar. A matriz continua no botão
  **⊞ Visão geral**, agora só leitura, e clicar numa coluna abre o painel daquele circuito.
- **Config gerada saiu igual ao template padrão**: nodes na ordem do template (bloqueio 9,
  blackhole 10, anúncio 11, global 12, prepends 13-16, no-export 17, `deny node 999` no fim), o
  preview emitido em ordem de node, e um circuito novo já leva o `if-match community-filter
  glob-all-<tipo>` quando esse filtro existe na caixa — sem ele, "anunciar para todos" não
  alcança o circuito recém-criado.
- **`export-df` virou legado** (§20 do template): continua sendo lida e manipulável onde já
  existe, mas não entra em config nova nem conta como "faltando".
- O parâmetro da ação passou a se chamar `destino` (circuito ou grupo global); o antigo
  `circuito` continua aceito.

Validado contra os 8 Huawei reais que usam a convenção (acessos 20, 175, 324, 746, 826, 923,
990, 1216): 83 circuitos descobertos contra 67 antes, 160 prefixos mapeados e 79 avisos de
config pré-existente. Nenhum comando foi enviado a equipamento durante o desenvolvimento.
Detalhamento técnico em [`docs/bgp_automacao.md`](docs/bgp_automacao.md).

---

## [Não publicado] — 2026-08-14 (PON: porta inventada e "sucesso" mentiroso)

### Corrigido

- **O painel oferecia porta PON que não existe** ([`clientes/olt_pon.py`](clientes/olt_pon.py)):
  quando a placa não tem `board add` no backup (confirmada em campo, não entra
  no `[pre-config]`), o código assumia 16 portas e desenhava portas 8–15 numa
  placa de 8. Na primeira operação real isso virou `% Parameter error` na
  OLT-HU-LEAL. Agora, sem o tipo da placa, vale só o que o backup prova
  (`port 0..7` → 8 portas) e o painel avisa "portas vistas no backup". Das 61
  placas das 18 OLTs Huawei, 1 estava nessa situação.
- **Comando recusado pelo equipamento era gravado como sucesso**: o VRP responde
  a recusa no texto e segue no prompt, e o `executar` só olhava se a conexão
  estourou exceção. Numa ação destrutiva é o pior bug silencioso — o operador
  sai achando que desativou a porta. `detectar_erro_cli` agora varre a saída,
  o status vira `erro` e o painel mostra a linha exata do equipamento com um
  "nada foi alterado na porta". A migração `0110_corrige_status_acao_olt_pon`
  reavaliou o histórico: 2 registros passaram de `sucesso` para `erro`.

### Alterado

- Consulta de porta (`display port info/state`) não passa mais pelo preview de
  comandos — executa e mostra só o retorno. Preview editável e confirmação
  continuam valendo para as ações de escrita (laser).
- Cada porta da grade ganhou o próprio botão de desativar, e os rótulos das
  ações passaram a falar do efeito ("Desativar porta") em vez do comando.

---

## [Não publicado] — 2026-08-14 (Topologia: portas PON de OLT Huawei)

### Adicionado

- **Automação de portas PON para OLT Huawei MA5600T/MA5800**
  ([`clientes/olt_pon.py`](clientes/olt_pon.py), [`docs/olt_pon.md`](docs/olt_pon.md)):
  o painel de propriedades de um host OLT na topologia ganhou o botão
  **"Portas PON"**, que lê do backup mais recente as placas do chassi e a grade
  de portas de cada uma — com quantas ONTs estão em cada porta e quem são elas.
  Sobre esse inventário, o operador dispara no equipamento
  `display port info <porta>`, `display port state <porta>` e
  `port <porta> laser-switch on/off`, com preview editável antes de enviar
  (mesmo contrato das automações de BGP e L2VPN).
- **O impacto do laser aparece antes do clique**: `laser-switch off` derruba
  todas as ONTs da porta, então o número (e os nomes das primeiras ONTs) sai na
  grade, no aviso vermelho do preview e no registro de auditoria
  (`AcaoOltPon.onts_afetadas`, congelado no momento da ação). Laser é uma porta
  por vez e exige confirmação explícita; consulta executa direto.
- Endpoints `GET/POST /clientes/acessos/<id>/olt-pon/` com a mesma régua de
  permissão do clone de L2VPN (backoffice + ferramenta `topologia` + posse do
  cliente) e auditoria em `AcaoOltPon` (migração `0109_acao_olt_pon`).

Detecção validada nos backups reais: dos 69 acessos com `interface gpon`, os 18
Huawei entram e os 51 ZTE/Datacom/Parks ficam de fora com mensagem explicando —
0 falso positivo e 0 falso negativo.

---

## [Não publicado] — 2026-08-14 (Atendimento em tempo real e tráfego animado na topologia)

### Corrigido

- **Chamado novo só aparecia na lista depois de F5**
  ([`atendimento/templates/atendimento/base.html`](atendimento/templates/atendimento/base.html),
  [`inbox.html`](atendimento/templates/atendimento/inbox.html)): o WebSocket avisava (som, toast,
  pisca-pisca), mas os dois handlers só sabiam atualizar itens **já** renderizados. E o
  `__refreshConvPanel()`, que refaz a lista com HTML do servidor, buscava a URL da página atual —
  dentro de um chamado essa URL é o `conversation_detail`, que não renderiza o bloco `conv_panel`,
  então a resposta vinha vazia e a função saía sem fazer nada. Como o atendente fica o dia dentro
  de um chamado, o refresh nunca rodava. Agora a lista vem sempre do Inbox (`?tab=<aba ativa>`),
  preservando busca, scroll e o chamado destacado, com debounce de 700ms (teto de 3s).
- **Chamado assumido por outro atendente ficava na minha aba "Abertos"**: o `conversation_reassigned`
  só refazia a lista de quem ganhou ou perdeu o chamado. Agora refaz para todo mundo.
- **Badge da "Caixa de Entrada" contava mensagens, não chamados**: cada mensagem recebida somava
  +1, então um grupo que mandasse 5 mensagens virava "5 chamados sem atendente". Passou a ser
  recalculado a partir da aba "Abertos".
- **Contador de não lidas subia de 2 em 2** com o Inbox aberto — `base.html` e `inbox.html`
  chamavam `markConvUnread` para o mesmo evento de WebSocket.
- **Bolha flutuante de chamado recém-atribuído demorava até 60s**: a checagem de "já tem bolha?"
  varria o documento inteiro, e o item da lista lateral também tem `data-conv-id`.
- **Fluxo animado dos links da topologia era invisível**
  ([`clientes/templates/topologia_editor.html`](clientes/templates/topologia_editor.html)): o
  `.link-flow` herdava o `stroke` do próprio link, ou seja, tracinhos da mesma cor da linha sólida
  desenhada logo abaixo. A animação existia e nunca dava pra ver.

### Alterado

- **Auto atendimento não escreve mais nos grupos**
  ([`atendimento/services.py`](atendimento/services.py)): a saudação e a mensagem de conclusão do
  fluxo eram enviadas ao grupo do cliente a cada chamado aberto, poluindo a conversa. O chamado já
  abre na 1ª mensagem sem depender do bot. A tela de configuração continua (com aviso no topo), e a
  notificação de "novo chamado" segue indo pro grupo **interno** configurado.
- **Tráfego dos links da topologia ficou visível de verdade**: traço claro com `mix-blend-mode:
  screen`, halo desfocado na cor do enlace e "pacotes" com brilho mais forte. Com
  `prefers-reduced-motion` o fluxo some por completo (antes só a animação parava, o que deixaria um
  tracejado branco fixo por cima de todo link).

---

## [Não publicado] — 2026-08-13 (L2VPN: clone fiel à origem e sessão LDP junto)

### Corrigido

- **Clone do DmOS trocava a VLAN de acesso pela do pseudowire**
  ([`clientes/l2vpn_parser.py`](clientes/l2vpn_parser.py)): `pw-type vlan N` (a tag que trafega
  dentro do túnel) e o `dot1q` da `access-interface` (a VLAN do cliente) caíam no mesmo campo, e
  como o `access-interface` vem **antes** do `dot1q` dele, a interface herdava a VLAN do
  pseudowire e o dot1q real era ignorado. Caso real do ambiente: serviço com `pw-type vlan 2400`
  e `dot1q 86` era clonado com `dot1q 2400` — e sem o `2400` no `pw-type`, que saía pelado.
  Agora são campos separados (`vlan` = acesso, `pw_vlan` = pseudowire), a VLAN lida na própria
  interface sobrepõe a herdada, e o formulário mostra os dois.
- **VSI Huawei vinha sem VLAN** (238 VSIs distintos nos backups): o bloco `vsi` não tem VLAN — ela
  é a da interface do `l2 binding vsi` (`Vlanif200`). Como o clone do VSI é aplicado na `Vlanif`
  da VLAN, o formulário abria com o campo obrigatório em branco e o número só na linha da
  interface. O binding agora preenche a VLAN do serviço.
- **`qinq` e o vlan-id do `pw-type` sumiam no clone** ([`clientes/l2vpn_actions.py`](clientes/l2vpn_actions.py)):
  serviço qinq é regenerado com o `qinq` no `vpn` e o `dot1q` dentro do bloco `encapsulation`,
  como na config de origem.

### Adicionado

- **Fechar a sessão LDP targeted junto com o clone**: o pseudowire não sobe sem sessão LDP com o
  peer, e ela mora fora do bloco do serviço. O formulário ganhou a opção (marcada por padrão),
  mostrando peer a peer quem já tem sessão no backup e quem será criado. Gera
  `mpls ldp remote-peer` + `remote-ip` no Huawei e `mpls ldp` → `lsr-id` → `neighbor targeted` no
  DmOS — com o `lsr-id` **lido do backup** (qual loopback está em uso muda por equipamento) e o
  nome do `remote-peer` resolvido pelo host do CRM. MikroTik fica de fora (sintaxe não conferida
  em config real deste ambiente).

---

## [Não publicado] — 2026-08-13 (Topologia: repaginação visual e ícones de rede)

### Alterado

- **Set de ícones redesenhado** ([`static/js/topo_engine.js`](static/js/topo_engine.js)): todos os
  devices ganharam desenho novo numa linguagem visual única (mesma área ótica, mesma escala de
  opacidade, mesma espessura de traço), com vocabulário de rede — roteador como cilindro de
  fluxo contrário, switch como chassi 1U com portas e pill L2/L3, DWDM com prisma separando
  comprimentos de onda, firewall como parede de tijolos com escudo, OLT com leque PON. Critério
  de aceitação: continuar legível a 22px no tile da paleta.
- **Editor repaginado** ([`clientes/templates/topologia_editor.html`](clientes/templates/topologia_editor.html),
  [`static/js/topo_main.js`](static/js/topo_main.js)): toolbar em clusters segmentados, tooltip
  próprio no lugar do `title` nativo, busca na paleta, controle de zoom flutuante no canvas,
  node com chassi de vidro + LED de host do CRM, âncoras de conexão menores, pastilhas de rótulo
  de link unificadas e painel de propriedades com o ícone do device selecionado no cabeçalho.
  Nenhuma mudança no `dados_json` — topologias salvas abrem iguais, só com o desenho novo.

### Adicionado

- **Painéis laterais viraram cartões flutuantes e abrem sob demanda**: ao abrir a topologia o
  canvas ocupa a tela inteira — a paleta de dispositivos fica fechada e volta pelo botão
  "Dispositivos" no canto superior esquerdo, e o painel de propriedades aparece sozinho só
  quando um dispositivo ou conexão é selecionado (some ao desmarcar, no Esc ou no X). Legenda e
  controle de zoom se afastam sozinhos para não ficarem atrás dos painéis.
- **5 tipos de dispositivo de rede** na paleta: `internet` (WAN), `ix` (IX.br/PTT), `splitter`
  (splitter óptico), `ap` (access point) e os grupos **Wireless** e **Trânsito / Peering**. O
  mapeamento automático função→tipo da importação de hosts ([`clientes/views.py`](clientes/views.py))
  reconhece as palavras-chave dos novos tipos, com IX/trânsito avaliados antes de router/switch.
- Indicador de alteração não salva no próprio botão **Salvar** e suporte a
  `prefers-reduced-motion` (desliga fluxo dos links e pulso dos nodes).

### Corrigido

- **Campos do painel de propriedades apareciam centralizados**: o `text-align:center` do estado
  vazio vinha inline no `#props-body` e, como o JS só troca o `innerHTML`, continuava valendo
  para os formulários. O estilo passou para a classe `.prop-empty`.

---

## [Não publicado] — 2026-08-14 (Backup barrado em cliente que só tem túnel OpenVPN)

### Corrigido

- **"Este equipamento tem IP privado mas não há proxy SSH ativo" no backup manual**, mesmo com o
  túnel OpenVPN de pé e o equipamento alcançável. O backend nunca chegava a ser chamado: a
  pré-checagem no front-end (`executarBackup`, `clientes/templates/listar.html`) detectava IP
  privado por regex e perguntava a `/clientes/proxies/ativo/` só pela existência de um
  `ProxyServer` — conceito anterior ao túnel. `realizar_backup()` já cai em `vpn_cobre_ip()` e
  conecta direto pelo túnel desde sempre.
- **`proxy_ativo_cliente` passou a responder a pergunta certa**: com `acesso_id`, devolve
  `privado`, `tem_tunel` e `alcancavel` considerando as duas saídas (proxy SSH **ou** túnel
  OpenVPN, com conferência da rota real). Sem `acesso_id`, mantém a resposta antiga. O front-end
  parou de duplicar a regra em regex — que, de quebra, não cobria CGNAT (100.64.0.0/10) nem
  198.18.0.0/15, faixas que os túneis servem.
- **Mensagens de erro** de backup, proxy web, OLT PON e monitoramento diziam só "configure um túnel
  SSH"; agora citam as duas opções e apontam a aba "Túneis".

Validado com backup real da OLT ZTE da TOPNET (`198.18.10.2`, cliente sem nenhum ProxyServer):
conectou pelo túnel e salvou 49 KB de configuração.

---

## [Não publicado] — 2026-08-14 (WireGuard removido — OpenVPN é o único tipo de VPN)

### Removido

- **VPN WireGuard, por completo.** Dois tipos de VPN para o mesmo fim significavam duas
  implementações do mesmo roteamento, dois caminhos de fallback em cada consumer e duas fontes de
  rota disputando a mesma tabela do kernel. O que saiu:
  - **Modelos** `VPNWireGuard` e `VPNServidorConfig` (migração `0111_remover_wireguard`).
  - **Código**: `clientes/vpn_manager.py`, as views `vpn_wg_*` e as 7 rotas `/clientes/**/vpn-wg/**`,
    `_wg_peer_ativo()` e `_vpn_cobre_ip()` em `clientes/consumers.py`, incluindo o source-bind por
    interface isolada (`ssh -b`), que o OpenVPN não precisa — a rota do kernel já sai pela tun certa.
  - **Frontend**: seção "VPN WireGuard — MikroTik" da aba Túneis e todo o JS `wg*`.
  - **Servidor**: interfaces `wg0`–`wg4` (`wg-quick@` parado e desabilitado), `/etc/wireguard/` e
    `/etc/sudoers.d/crm-wireguard`. Backup em `/root/backup-wireguard-removido-20260814/`.
- **Impacto assumido**: DS TECH (peer ativo no `wg0`, 17 redes `/16`) e DIONES ficaram sem acesso
  até criarem o túnel OpenVPN e rodarem o bootstrap no MikroTik.

### Alterado

- **Ping e checagem de DNS pelo servidor** (`clientes/views.py`) usavam handshake de peer WireGuard
  para decidir se podiam rodar local; passaram a usar `openvpn_tunnel_manager.tunel_conectado()`.
- **`rota_dev_para()`** mudou de `vpn_manager` para `openvpn_tunnel_manager` (o módulo que sobrou).
- `sudoers` do OpenVPN ganhou `systemctl reset-failed openvpn-server@server-crm-*`, que o código já
  chamava desde 13/08 sem ter permissão.

---

## [Não publicado] — 2026-08-14 (Túnel OpenVPN: bootstrap falhava no RouterOS 7.6+)

### Corrigido

- **`cipher=aes256` recusado pelo RouterOS 7.6+** ([`clientes/openvpn_tunnel_manager.py`](clientes/openvpn_tunnel_manager.py)):
  na 7.6 a MikroTik renomeou os valores do ovpn-client ao acrescentar GCM (`aes256-cbc`,
  `aes256-gcm`) e `aes256` puro deixou de existir. O `/import` morria com
  `Script Error: syntax error (line 20 column 109)` — a coluna do cipher — sem sequer tentar
  conectar (reproduzido no túnel da Conecta ISP, RouterOS 7.21.4). `gerar_setup_rsc()` passou a ler
  major **e** minor da versão (`_parse_versao_ros`) e emitir `aes256` até a 7.5 e `aes256-cbc` da
  7.6 em diante; sem versão informada assume 7.6+.
- **Conflito de redes agora considera a tabela de rotas do kernel**, não só o banco: rotas de peers
  antigos do `wg0` compartilhado não têm registro em `VPNWireGuard` e escapavam da checagem. Rota
  órfã (prefixo que nenhum peer WireGuard reivindica) é ignorada de propósito — não entrega tráfego
  a ninguém e só travaria o operador por resto de configuração antiga.

---

## [Não publicado] — 2026-08-13 (Túnel OpenVPN MikroTik: tráfego interno não passava)

### Corrigido

- **`iroute` obrigatório no client-config-dir** ([`clientes/openvpn_tunnel_manager.py`](clientes/openvpn_tunnel_manager.py)):
  em modo `--server` o OpenVPN tem tabela de roteamento interna própria e a `route` do `.conf` só
  entrega o pacote na `tun` — sem `iroute` ele descartava tudo em silêncio. Sintoma: túnel
  `running`, handshake fechado, ping do `/29` respondendo e **nenhuma** rede interna alcançável.
  Afetava os dois túneis em produção. `atualizar_redes_instancia` passou a reescrever o CCD junto
  com o `.conf` (antes, editar as redes só mexia no lado kernel).
- **Colisão de redes entre túneis**: `redes_em_conflito()` recusa criar/editar túnel com rede
  idêntica à de outro túnel OpenVPN ou VPN WireGuard ativa, nomeando o cliente dono; o modal de
  criação sugere as `/24` dos acessos privados do cliente (`sugerir_redes`) no lugar das faixas
  CGNAT+RFC1918, que colidiam entre clientes e faziam o kernel usar as rotas de um só deles.
- **`vpn_cobre_ip` mandava o proxy para o túnel errado** ([`clientes/views.py`](clientes/views.py)):
  respondia "coberto" só pela declaração em `redes_privadas`. Agora confere o `dev` real da rota
  (`vpn_manager.rota_dev_para`, via `ip route get`) contra a interface daquele túnel — não batendo,
  cai no ProxyServer SSH. Mesmo guard (`_rota_confere`) nos consumers de Terminal/WinBox.
- **Unit systemd zumbi**: falha ao subir a instância não desfazia o `enable`, e
  `openvpn-server@server-crm-999` acumulou 558 mil reinícios apontando para um `.conf` inexistente.
  `criar_instancia_servidor` agora limpa (`disable --now` + `reset-failed`), `remover_instancia_servidor`
  também dá `reset-failed`, e `alocar_proxima_instancia` não reaproveita N com `.conf`/CCD em disco.

### Documentação

- Novo [`docs/tunel_openvpn_mikrotik.md`](docs/tunel_openvpn_mikrotik.md) — arquitetura da instância
  dedicada, `route` × `iroute`, escolha das redes e diagnóstico. Seção de VPN direta atualizada em
  [`docs/proxy_web_acessos.md`](docs/proxy_web_acessos.md).

---

## [Não publicado] — 2026-08-10 (Nova aba Vulnerabilidades: varredura de amplificação DDoS nos blocos RPKI/IRR)

### Adicionado

- **Aba "Vulnerabilidades" por cliente**, ao lado de RPKI/IRR — varre os blocos de IP já cadastrados
  (`BlocoIP`) em busca de 21 portas de amplificação DDoS mal configuradas (DNS, NTP, SNMP,
  Memcached, SSDP, CLDAP e outras), com botão "Escanear Agora" e tabela expansível das portas
  testadas. Varredura automática a cada 2 dias, em 3 grupos rotativos de clientes (cobertura
  completa em 6 dias, evita disparar sondas contra todos os clientes no mesmo dia). Baseado em
  [`tools/ampscan_runner/`](tools/ampscan_runner/), binário Rust fino sobre a lib
  [ampscan](https://github.com/gondimcodes/ampscan) (dependência git pinada por commit), trocando
  JSON por stdin/stdout com o Celery. Novos modelos `AmpScanResultado`/`AmpScanExecucaoLog`.
  Detalhes em
  [AMPSCAN_VARREDURA_AMPLIFICACAO.md](docs/AMPSCAN_VARREDURA_AMPLIFICACAO.md).

### Corrigido

- **Regressão introduzida no mesmo dia**: a inserção do bloco AmpScan no fim de `clientes/tasks.py`
  cortou o `return` final de `enviar_disparo_hotspot_lead` (função pré-existente), que passou a
  retornar `None` no caminho sem retry. Detalhes em
  [AMPSCAN_VARREDURA_AMPLIFICACAO.md](docs/AMPSCAN_VARREDURA_AMPLIFICACAO.md#correção--regressão-em-enviar_disparo_hotspot_lead-2026-08-10).
- **Horário da última varredura exibido em UTC, não no fuso local** — `listar_ampscan_resultados`/
  `execucoes` formatavam os datetimes sem `timezone.localtime()`, mostrando a última execução ~3h
  "no futuro". Detalhes em
  [AMPSCAN_VARREDURA_AMPLIFICACAO.md](docs/AMPSCAN_VARREDURA_AMPLIFICACAO.md#correção--horário-exibido-em-utc-não-no-fuso-local-2026-08-10).

---

## [Não publicado] — 2026-08-06 (Fix: `/homegeral` quebrava com tarefa/artigo sem responsável)

### Corrigido

- **`VariableDoesNotExist` em `/homegeral`** — o painel de tarefas do dashboard
  (`home.views.quadro_geral`) derrubava a página inteira quando existia uma tarefa sem
  responsável (`assigned_to=None`). A causa é uma armadilha do template do Django:
  `{{ t.assigned_to.get_full_name|default:t.assigned_to.username }}` avalia
  `t.assigned_to.username` como *argumento* do filtro `default`, e — diferente da
  variável principal — Django não silencia falha de lookup em argumento de filtro,
  então `None.username` sobe como exceção fatal em vez de cair no fallback. Corrigido
  em [`tarefas/_linha.html`](tarefas/templates/tarefas/_linha.html) com um guard
  `{% if t.assigned_to %}` explícito. Detalhes em
  [TAREFAS.md](docs/TAREFAS.md#correção--variabledoesnotexist-em-homegeral-com-tarefa-sem-responsável-2026-08-06).
- **Mesmo padrão latente em `wiki/visualizar_artigo.html`** — `artigo.criado_por` é
  `SET_NULL`/nullable, então um artigo com autor apagado quebraria a página do mesmo
  jeito. Corrigido preventivamente com o mesmo guard. Detalhes em
  [WIKI_ARTIGOS.md](docs/WIKI_ARTIGOS.md#correção--mesmo-crash-latente-em-criado_por-2026-08-06).

---

## [Não publicado] — 2026-08-05 (Atendimento: indicador de não lida, fix transferência, visual WhatsApp)

### Adicionado

- **Indicador de mensagem não lida em conversas assumidas** — quando um atendente está
  com uma conversa assumida e o cliente manda mensagem nova, o item da conversa ganha
  badge com a contagem e destaque visual (borda + negrito), atualizado em tempo real
  via WebSocket. Reaproveita o campo `Message.is_read` (já existia, não era usado pra
  exibir nada) como fonte de verdade — inclusive corrigindo `api_my_conversations`, que
  usava uma heurística de janela de 48h em vez de leitura real. Detalhes em
  [ATENDIMENTO.md](docs/ATENDIMENTO.md#indicador-de-mensagem-não-lida-em-conversas-assumidas-2026-08-05).
- **Item "Conversas em Tarefa" no menu principal**, substituindo a antiga 4ª aba
  "Tarefas" do Inbox (que deixava a barra de abas mais larga que o painel, forçando
  scroll horizontal permanente).

### Corrigido

- **Transferir/atribuir um chamado não avisava o atendente que ganhou o chamado em
  tempo real** — só aparecia em "Assumidos" depois de um F5 manual. Afetava os 4
  pontos que trocam `assigned_to`: transferência manual, "Assumir", auto-atribuição ao
  responder e reatribuição automática por SLA (Celery — o mais grave, sem navegador
  algum aberto pra se auto-atualizar). Corrigido com um evento WS
  `conversation_reassigned` centralizado em `services.notify_reassignment()`. Detalhes
  em [ATENDIMENTO.md](docs/ATENDIMENTO.md#correção--transferênciaatribuição-não-avisava-outros-atendentes-em-tempo-real-2026-08-05).
- **Barra de rolagem horizontal sempre visível** nas abas do Inbox (Assumidos/Abertos/
  Em Andamento/Tarefas) — corrigida junto com a remoção da aba Tarefas do painel.

### Alterado

- **Visual do chat e da lista de conversas** agora no estilo **WhatsApp Dark**: bolhas
  de mensagem com rabicho e cantos arredondados, ✓✓ cinza (não azul — não há
  confirmação real de entrega/leitura do WhatsApp nesse sistema) nas mensagens do
  atendente, campo de digitar em pílula com botão de enviar circular verde, e acentos
  verdes na lista de conversas. Escopo limitado a chat + lista — cores de status do
  chamado e botões utilitários do CRM não mudaram. Detalhes em
  [ATENDIMENTO.md](docs/ATENDIMENTO.md#visual-do-chat-e-da-lista-de-conversas--estilo-whatsapp-dark-2026-08-05).

---

## [Não publicado] — 2026-08-04 (Fix: Atendimento — card "piscando" ao resolver chamado + alerta NOC)

### Corrigido

- **Ao resolver/encerrar um chamado, o card não sumia na hora — aparecia e sumia,
  intercalando entre as abas Aberto/Em Andamento/Aguardando.** Três causas em cadeia:
  o broadcast do WebSocket que remove o card só disparava depois de uma chamada
  síncrona e bloqueante à Evolution API (mensagem de conclusão); o WebSocket do Inbox
  vazava uma conexão nova a cada navegação SPA sem fechar a anterior, acumulando
  instâncias órfãs brigando entre si; e o cache de prefetch (hover) reintroduzia HTML
  obsoleto com o card ainda listado. `atendimento/views.py` (broadcast agora imediato,
  envio ao WhatsApp em background), `atendimento/templates/atendimento/inbox.html`
  (fecha WebSocket anterior antes de abrir novo) e `atendimento/templates/atendimento/
  base.html` (invalida cache de prefetch no evento de status). Detalhes em
  [ATENDIMENTO.md](docs/ATENDIMENTO.md#correção--flicker-ao-resolverencerrar-chamado-2026-08-04).
- **Aba ativa do sidebar ficava travada em "Abertos"** independente do chamado exibido
  — `class="conv-tab active"` fixo no HTML em vez de usar `sidebar_active_tab`, que o
  backend já calculava corretamente. Corrigido em `atendimento/templates/atendimento/
  base.html`.
- **Alerta ao grupo NOC de chamado sem atendimento podia se perder silenciosamente**
  em caso de falha real de envio ao WhatsApp: `send_text()` retorna uma tupla
  `(sucesso, msg_id)`, mas o código checava a tupla inteira como `bool` — sempre
  truthy, então uma falha de envio era tratada como sucesso e o chamado ficava
  marcado como já notificado sem ninguém ter sido avisado. Corrigido em
  `atendimento/tasks.py` (`notificar_chamados_abertos`) e `atendimento/services.py`
  (`_notify_new_open_conversation`). O mecanismo de envio único + mensagem consolidada
  marcando todos os chamados em lote já estava correto (desde 16/07). Detalhes em
  [ATENDIMENTO.md](docs/ATENDIMENTO.md#correção--alerta-noc-perdia-aviso-silenciosamente-em-falha-de-envio-2026-08-04).

### Melhorado

- **Fluidez do módulo de Atendimento**: remoção de card ao resolver chamado agora usa
  transição suave (fade + slide) em vez de sumir abruptamente; polling do chat reduzido
  de dois `setInterval` sobrepostos + um terceiro de vigia para um único timer
  recursivo auto-ajustável.

---

## [Não publicado] — 2026-08-04 (Fix: Proxy Web — loop de login + WinBox Web pra clientes só-VPN)

### Corrigido

- **Proxy web de acessos: login funcionava mas a página recarregava de volta pra tela de login em
  loop** (reproduzido com AP Mimosa/Airspan C5c) — o firmware do equipamento reporta `"https":false`
  no JSON de login/status e o próprio JS dele tentava "corrigir" o scheme navegando pra `http://`,
  gerando reload completo (comprovado nos logs do Daphne) e apagando o login da SPA a cada ciclo.
  Fix em `clientes/proxy_engine.py`: guard contra `location.href`/`assign`/`replace` que só trocam
  o scheme, e reescrita do campo `"https":false→true` na resposta JSON quando o proxy fala HTTP com
  o equipamento — a condição que dispara a troca nunca mais fica verdadeira. Detalhes em
  [proxy_web_acessos.md](docs/proxy_web_acessos.md).
- **WinBox Web (VNC e nativo) falhava com "Nenhum proxy SSH ativo"** pra qualquer cliente que só
  tem VPN WireGuard/OpenVPN própria, sem `ProxyServer` SSH cadastrado (`clientes/consumers.py`
  não tinha o fallback de VPN que o proxy HTTP já usava). Corrigido em
  `WinboxVNCConsumer.conectar_vnc()`/`conectar_winbox()`. Mesmo bug pendente em Terminal SSH, OLT
  Parks e Telnet — sinalizado, não corrigido nesta rodada. Detalhes em
  [winbox_vnc.md](docs/winbox_vnc.md#winbox-web-não-abre-para-clientes-que-só-têm-vpn-sem-proxyserver-ssh--corrigido-em-04082026).
- Removido debug hardcoded (`DBG891`, de uma sessão anterior) que logava usuário/senha do
  equipamento em texto puro no log do Daphne.

---

## [Não publicado] — 2026-08-03 (Fix: Automação BGP — "sem_novidade" bloqueava refresh legítimo)

### Corrigido

- **Regressão do próprio fix de "sem_novidade" (02/08)**: a condição só olhava se o backup era o
  mesmo, sem checar se havia de fato um patch otimista pra proteger — bloqueando refresh legítimo de
  snapshots antigos pra sempre (backup em disco quase nunca muda de um dia pro outro). Reportado com
  caso real: sessões BGP de alguns clientes (G5, Green Telecom) apareciam com `interface: null` (sem
  botão "Ver tráfego") e poucos/nenhum prefixo simulado como anunciado — não por bug no parser/
  matcher, mas porque o snapshot deles foi gerado antes do campo `interface` existir no código e
  nunca mais foi reprocessado.
- `BgpSnapshot` ganhou `patch_local_pendente` (bool, migration `0099`) — `aplicar_efeito_localmente`
  marca `True` ao mutar `dados` sem backup novo; um reparse de verdade sempre volta pra `False`.
  `'sem_novidade'` agora exige backup igual **e** patch pendente — sem patch pra proteger,
  reprocessar o mesmo backup passa a ser sempre permitido (é assim que um snapshot antigo pega
  melhorias de parser/matcher sem esperar um backup novo do equipamento).
- Backfill único rodado contra os 55 `BgpSnapshot` reais: 53 reprocessados com sucesso, 2 com erro
  de simulação pré-existente já conhecido (não relacionado a esta mudança).

---

## [Não publicado] — 2026-08-02 (Feat: Dashboard da instância pra Consultor e Operador)

### Adicionado

- **Dashboard da instância** (`quadro_instancia`, `/homeinstancia`) — mesma visão do dashboard do
  Administrador (`quadro_geral`): stats de clientes/hosts, backups de hoje, gráfico dos últimos 14
  dias, últimos backups, top clientes por hosts, blocos RPKI/IRR inválidos — escopada aos clientes
  da própria instância via `Cliente.objects.visiveis_para(user)`. Consultor e todos os seus
  Operadores veem os mesmos números. Reaproveita o template `quadro_geral.html` (parametrizado)
  em vez de duplicar HTML. Login e o botão "Dashboard" do menu agora levam Consultor/Operador
  direto pra esse painel. Detalhes na
  [Sessão 17 do SISTEMA.md](SISTEMA.md#sessão-17--dashboard-da-instância-pra-consultor-e-operador).

---

## [Não publicado] — 2026-08-02 (Feat: Multi-tenant — papéis Consultor e Operador)

### Adicionado

- **Papéis Consultor e Operador**, além de Administrador e do portal do cliente final já existentes.
  Consultor cadastra e gerencia seus próprios clientes, isolados dos clientes de outros consultores;
  Operador é um funcionário do consultor (ou do admin) com acesso quase igual, exceto que não
  gerencia usuários nem escolhe ferramentas da instância. `is_staff` não mudou de significado —
  Consultor/Operador continuam `is_staff=False`, o que já os exclui automaticamente de
  Financeiro/Atendimento/Wiki/dashboards administrativos sem precisar tocar nesses apps.
- Modelos novos: `usuario.Instancia` (a "conta" de um Consultor), `usuario.PerfilUsuario` (papel:
  admin/consultor/operador), `usuario.InstanciaFerramenta` (quais ferramentas do núcleo — acessos,
  backups, vpn, topologia, túneis, documentos, rpki/irr, monitoramento, hotspot, ipam, scripts, bgp,
  testes de rede, pesquisa LG, geolocalização IP, firmware — o Administrador liberou pra cada
  instância, default desabilitado). `Cliente.instancia` (FK nullable) +
  `Cliente.objects.visiveis_para(user)`.
- `usuario/perms.py` — ponto único de verdade pra papel e escopo de instância, usado pelos
  decorators e views do núcleo (`clientes`, `monitoramento`, `ipam`, `hotspot`, `bgp`, `scripts`,
  LG/GeoIP em `home`).
- Tela de gestão de usuários (`cadastrar_usuario.html`) ganhou seletor de 4 papéis no lugar do
  checkbox único `is_staff`, com checkboxes de `InstanciaFerramenta` pro Administrador liberar ao
  criar/editar um Consultor.
- Ver detalhamento completo (modelos, decorators, integração com o `UsuarioModulo` existente do
  portal do cliente) na [Sessão 16 do SISTEMA.md](SISTEMA.md#sessão-16--multi-tenant-consultor-e-operador).

### Corrigido

- Busca de cliente do menu (`buscar_clientes_chamado`) listava clientes de outras instâncias nos
  resultados (o clique já era bloqueado, mas a lista não deveria nem mostrar o nome).
- Endpoints do IPAM identificados só por objeto (`vlan_id`/`prefixo_id`/`subrede_id`/`ip_id`/
  `vpn_id`) não validavam posse do cliente — um Consultor/Operador com IPAM liberado podia mutar
  registros de outra instância adivinhando o id.
- Aba/ferramenta não liberada pra instância (ex.: Hotspot) continuava aparecendo tanto pro
  Consultor quanto pro cliente final que ele cadastrou — o portal do cliente não tinha teto nenhum
  da instância do Consultor dono do cliente.
- Wiki não tinha nenhum controle de `is_staff` (reachable por qualquer usuário autenticado);
  adicionado `@admin_required` nas 12 views.

---

## [Não publicado] — 2026-08-02 (Feat: Automação BGP — execução em modo trial)

### Adicionado

- **Modo trial (commit temporário com rollback automático)**: todo modal de confirmação ganhou dois
  botões — "▶ Executar em modo trial" e "▶ Executar sem trial" — mais um campo numérico pra duração
  do trial (segundos, default 60). Trial troca o commit final pelo mecanismo nativo de cada
  fabricante: Huawei `commit trial N`, Juniper `commit confirmed N` (convertido pra minutos,
  arredondado pra cima) — a mudança reverte sozinha se ninguém confirmar depois, útil pra testar o
  efeito de uma mudança arriscada (ex: desativar sessão upstream) com rede de segurança.
- Cisco/Datacom e Mikrotik **não suportam trial** (`clientes/bgp_actions.py::
  validar_trial_suportado`, recusa antes de conectar no equipamento) — decidido com o usuário via
  pergunta explícita: IOS clássico não tem candidate-config, o único rollback temporizado possível
  seria agendar `reload in N` (reboot do equipamento INTEIRO), risco desproporcional; RouterOS só
  tem "safe mode" (reverte no disconnect da sessão, não por tempo), incompatível com o modelo
  conecta→executa→desconecta desta automação.
- Quando `trial=True`, a atualização otimista do painel (`aplicar_efeito_localmente`) é **pulada** —
  marcar como permanente uma mudança que reverte sozinha seria enganoso, já que a automação não sabe
  quando o rollback automático do equipamento efetivamente acontece.
- Validado com 89 combinações reais (sessão × prefixo, todos os 4 fabricantes) comparando
  `trial=True`/`trial=False` a partir do mesmo estado original — zero discrepâncias.

---

## [Não publicado] — 2026-08-02 (Simplificação: Ver tráfego mostra só o gráfico)

### Modificado

- **Removido o terminal xterm.js embutido do modal "Ver tráfego"** — pedido do usuário ("a tela do
  comando não precisa"). O gráfico já é a única saída exibida; a conexão WebSocket continua a mesma
  (`ws/ssh/`, `independente: true`), só que o texto recebido vai direto pro parser do gráfico em vez
  de também ser escrito num terminal visual. Removidos os assets de xterm.js
  (`xterm.min.js`/`.css`, `xterm-addon-fit`, `xterm-addon-canvas`) da página, já que não são mais
  usados em lugar nenhum dela — só `chart.umd.min.js` continua carregado.

---

## [Não publicado] — 2026-08-02 (Fix + Feat: Ver tráfego em tempo real — 2 bugs reais e gráfico ao vivo)

### Corrigido

- **Terminal de tráfego ficava em branco** — `xterm.css` base não define `width`/`height` em
  `.xterm`; sem essas regras (que `terminal.html` já tinha e eu esqueci de copiar) o terminal
  renderiza com 0px e some, mesmo recebendo dados.
- **Depois do fix acima, ficava travado em "Conectando…" pra sempre**, mesmo com o daphne
  confirmando SSH conectado nos logs — `consumers.py::send_output` manda a saída como frame
  **binário puro**; sem `socket.binaryType = 'arraybuffer'` (também esquecido), o navegador usa
  `'blob'` por padrão, `instanceof ArrayBuffer` dava falso pra toda saída, e caía num
  `JSON.parse(blob)` que estourava silenciosamente dentro do `onmessage`. `{type:'connected'}`
  (frame de texto) processava normal, mas nenhuma saída real (binária) jamais aparecia.

### Adicionado

- **Gráfico ao vivo no modal "Ver tráfego"**: pedido do usuário depois de confirmar que o texto
  bruto já funcionava. Reaproveita `chart.umd.min.js` (Chart.js, já vendorizado, mesma paleta/
  `_fmtBps` de `monitoramento/tab_monitoramento.html`). Capturei o formato real do
  `display counters rate interface X | refresh 1` direto do equipamento antes de escrever o parser
  — ao contrário do que a doc anterior presumia, o Huawei **não usa ANSI** pra redesenhar a tela,
  cada ciclo vem delimitado em texto puro (`---- (Refreshed at ...) ----` ... `---- (Finish) ----`).
  Uma regex extrai `Octets(bytes/s)` de Inbound/Outbound de cada ciclo completo (só processa depois
  do `(Finish)`, evitando cortar um frame de WS parcial no meio), multiplica por 8 e plota
  Entrada/Saída em Mbps, até 60 pontos (~1 min de histórico).

---

## [Não publicado] — 2026-08-01 (Feat: Automação BGP — ver tráfego em tempo real, Huawei)

### Adicionado

- **Identificação automática da interface de cada sessão BGP** (`clientes/bgp_matcher.py::
  identificar_interface`): acha a interface local cuja subnet contém o IP do peer (peers eBGP
  diretamente conectados ficam na mesma subnet do lado local) — vendor-agnóstica, mas só chamada
  hoje pra Huawei (`clientes/tasks.py::_atualizar_snapshot_bgp_de_acesso`), populando
  `sessao['interface']` no snapshot. Validado contra os 53 `BgpSnapshot` reais (229 sessões Huawei):
  114 identificadas, 115 sem match (peers iBGP via loopback/IGP ou IPv6, corretamente não
  adivinhados) — zero erros.
- **Botão "📶 Ver tráfego"** por sessão (quando a interface foi identificada): abre um modal com
  terminal embutido (xterm.js, mesmos assets já usados em `terminal.html`) conectado ao MESMO
  WebSocket do terminal SSH normal (`ws/ssh/`) — sem endpoint HTTP novo, sem mudança em
  `consumers.py`. Conecta com `independente: true` (não entra numa sessão compartilhada de outro
  operador) e roda `display counters rate interface {interface} | refresh 1`, que atualiza sozinho
  a cada segundo. Ctrl+C automático ao fechar o modal, antes de desconectar. Terminal
  somente-leitura, sem registro de auditoria (leitura pura, mesmo padrão do "Atualizar agora").

---

## [Não publicado] — 2026-08-01 (Fix: Automação BGP — modal de "anunciar prefixo novo" sem scroll)

### Corrigido

- **Modal "➕ Anunciar prefixo novo" não rolava com uma lista grande de prefix-lists** — em
  equipamentos com muitas cadastradas (ex: 20+), a lista de candidatas simplesmente esticava o modal
  pra fora da tela, sem barra de rolagem, cortando o conteúdo. `.modal` ganhou
  `max-height: calc(100vh - 32px)` + `overflow-y: auto` (afeta todos os modais da tela, sem risco —
  os outros são curtos o bastante pra nunca precisar rolar). O campo de busca (`#naBusca`) ficou
  `position: sticky` no topo, então continua visível enquanto a lista rola por baixo dele.

---

## [Não publicado] — 2026-08-01 (Fix: Automação BGP — painel não refletia ação recém-executada)

### Corrigido

- **Painel continuava mostrando um prefixo como anunciado depois de rodar "Parar de anunciar" nele**
  (reportado com screenshot real: `179.0.110.0/24` continuava na tabela mesmo depois da ação real ter
  sido executada com sucesso no equipamento). Causa: `BgpSnapshot.dados` só é reescrito pela rotina
  noturna ou pelo botão "Atualizar agora" (que relê o ÚLTIMO BACKUP salvo — que não muda só porque
  uma ação foi executada, precisa de um backup novo do equipamento). O painel ficava mostrando o
  estado de ANTES da ação até o próximo backup real.
- Adicionado `clientes/bgp_actions.py::aplicar_efeito_localmente(vendor, dados, tipo, nome_sessao,
  alvo, params)`: depois de qualquer ação real (`preview=false`) bem-sucedida, atualiza o mesmo dict
  `dados` (sessão habilitada/desabilitada, prepend do termo responsável, termo vira `reject` no
  "parar de anunciar", termo novo inserido no "anunciar prefixo novo") e recalcula
  `dados['anuncios'][sessao]` via `simular_anuncios` — grava de volta no `BgpSnapshot` antes de
  responder. É uma aproximação otimista (assume que o comando fez o que a mesma lógica que o gerou
  previu); o próximo backup real sempre corrige qualquer divergência. Nunca derruba a resposta de
  sucesso já obtida do equipamento se a atualização local falhar.
- Bug pego durante a implementação (não em produção): a primeira versão do "anunciar prefixo novo"
  local inseria o termo novo com `ordem = max(ordem existente) + 1` — quebrava quando o node/termo
  catch-all final já tinha a maior `ordem` (ex: Huawei `deny node 2000` tem `ordem=2000`), colocando
  o termo novo DEPOIS do catch-all na simulação e fazendo o prefixo nunca aparecer como anunciado.
  Corrigido pra sempre inserir com `ordem` menor que a de qualquer termo catch-all existente.

Validado com 9802 combinações (toggle/prepend/parar/novo_anuncio) contra os 53 `BgpSnapshot` reais —
zero erros inesperados. Testado também o fluxo HTTP completo (view real, execução do equipamento
mockada) confirmando que o prefixo some da tabela de anunciados no mesmo request.

- **Regressão pega em produção logo depois do fix acima**: "Atualizar agora" relê o backup mais
  recente já salvo em disco e reescreve `BgpSnapshot.dados` do zero — se nenhum backup NOVO foi
  tirado desde a ação real (backup ainda não capturou a mudança no equipamento), isso sobrescrevia a
  atualização otimista de volta pro estado de ANTES da ação, exatamente o problema que o fix acima
  deveria ter resolvido. Reproduzido com um caso real: ação `parar_anuncio` bem-sucedida em
  `179.0.110.0/24`, seguida de "Atualizar agora" 3 minutos depois — reverteu, porque o backup em
  disco (`BDR_20260801_022110.txt`) ainda era o mesmo de antes da ação.
- `clientes/tasks.py::_atualizar_snapshot_bgp_de_acesso` ganhou um resultado novo, `'sem_novidade'`:
  se o backup mais recente encontrado é o MESMO já usado pelo `BgpSnapshot` atual (mesmo
  `backup_log_id`) e o snapshot não está em erro, não reprocessa nada — preserva o `dados` atual
  (com qualquer atualização otimista já aplicada) em vez de sobrescrever. Vale tanto pro botão
  "Atualizar agora" quanto pra rotina noturna (mesma função, mesmo risco). `bgp_views.py::
  bgp_atualizar_snapshot` trata `sem_novidade` como sucesso (não erro); frontend mostra um tooltip
  discreto no badge em vez de alterar o comportamento.

---

## [Não publicado] — 2026-08-01 (Feat: Automação BGP — atualizar sob demanda, communities, anunciar prefixo novo)

### Adicionado

- **Botão "Atualizar agora"** (`clientes/tasks.py`, `clientes/bgp_views.py`): `atualizar_snapshots_bgp`
  refatorada — o trabalho de um Acesso virou `_atualizar_snapshot_bgp_de_acesso`, reutilizada pela
  rotina noturna (loop) e por um botão novo (`POST /clientes/bgp/<id>/atualizar/`) que refaz a
  extração+simulação de um host na hora, síncrono, sem esperar a próxima rodada das 02:45.
- **Communities cadastráveis por sessão** (`BgpCommunity`, migration `0097`;
  `clientes/bgp_actions.py::comandos_aplicar_community`): cadastro manual (rótulo + valor) por
  sessão/upstream, com botão "Usar community" que aplica num anúncio. Huawei
  (`apply community ... additive`) e Juniper (`policy-options community` nomeada, sempre por nome)
  confirmados em backup real; Mikrotik v6 confirmado (`append-bgp-communities=`); Mikrotik v7
  recusado (sem evidência de `set` de community no dialeto de script); Cisco/Datacom implementado
  como best-effort (`set community ... additive`, sintaxe IOS padrão-de-mercado, mas zero
  ocorrências reais em 38 backups Cisco deste ambiente).
- **Anunciar prefixo novo via varredura de prefix-lists** (`clientes/bgp_matcher.py::escanear_prefix_lists`,
  `clientes/bgp_actions.py::comandos_novo_anuncio`, endpoint `POST /clientes/bgp/<id>/escanear-prefixo/`):
  dado um prefixo ainda não anunciado, varre as prefix-lists já usadas pela export policy da sessão,
  diz se já bateria em alguma (nada a fazer) ou lista candidatas pra adicionar uma entrada nova — sem
  mexer na route-policy/term. Implementado pros 4 fabricantes; Mikrotik usa um mecanismo diferente
  (insere regra `accept` direto na chain de export, por não ter prefix-list nomeada separada);
  Juniper recusa se a candidata for um route-filter sintético embutido no term, não uma prefix-list
  de verdade. `parse_huawei` ganhou `index` nas entradas de `ip ip-prefix` (paridade com o `seq` que
  o Cisco já tinha) — necessário pra calcular o próximo índice livre.

Validado contra os 53 `BgpSnapshot` reais de produção existentes (todos os 4 fabricantes) nos 4
endpoints novos — ver [docs/bgp_automacao.md](docs/bgp_automacao.md).

### Corrigido

- **"Parar de anunciar" no Huawei usava `undo network` (global) mesmo quando o prefixo era
  controlado por route-policy** (`clientes/bgp_actions.py::comandos_parar_anuncio`): reportado com
  um caso real (`RP-UPSTREAM-MEGASNET-V4-OUT` node 10 → `if-match ip-prefix PL-179.0.110.0/24`) —
  o comando antigo (`undo network 179.0.110.0 255.255.255.0`) desliga a origem BGP daquela rede pra
  **todas** as sessões do equipamento, não só a sessão em questão. Agora a ação troca o modo do
  node responsável pelo match, de `permit` pra `deny`, dentro do route-policy de export DESSA
  sessão (`route-policy NOME deny node N`), mantendo o mesmo node/if-match/apply — escopado ao
  peer, sem editar a prefix-list (que pode ser um objeto compartilhado por outro node/policy).
  `undo network` (global) só entra como último recurso, quando o prefixo não é controlado por
  nenhuma route-policy.
- **Mesmo problema confirmado no Cisco/Datacom**: a ação inseria um `deny` direto na prefix-list
  (`ip prefix-list PL seq N deny PREFIXO`) — só que prefix-lists de prefixo próprio são reaproveitadas
  por vários route-maps/peers ao mesmo tempo (confirmado em backup real: `PL-ORIGIN-45.71.73.0_24`
  em `cliente_8/acesso_348` está referenciada em 3 route-maps OUT diferentes ao mesmo tempo — mesmo
  prefixo anunciado a 3 upstreams). Editar a lista pararia de anunciar nos três peers. Corrigido pro
  mesmo padrão do Huawei: insere um `deny` novo dentro do route-map de export DESSA sessão (mesma
  prefix-list como match, mas escopado a esse route-map), num seq menor que o `permit` existente —
  os outros route-maps que também usam a mesma lista continuam intocados.
- **"Anunciar prefixo novo" também editava uma prefix-list compartilhável, mesma classe de bug**
  (`clientes/bgp_actions.py::comandos_novo_anuncio`): a versão original adicionava uma entrada nova
  numa prefix-list já usada pela sessão (`ip ip-prefix LISTA index N permit ...`) — se essa lista
  também fosse referenciada por OUTRA sessão, o prefixo novo passaria a ser anunciado por ela também.
  Redesenhado: agora escolhe uma prefix-list **já existente** no equipamento (sem editá-la) e cria um
  **node/termo/entrada de route-map NOVO**, exclusivo da export policy dessa sessão, que só faz
  `if-match`/`match` nela (`route-policy NOME permit node N` + `if-match ip-prefix LISTA` no Huawei;
  `route-map NOME permit N` + `match ip address prefix-list LISTA` no Cisco/Datacom; novo `term` +
  `insert ... before` no Juniper, pra garantir a posição antes de um catch-all — Junos avalia terms
  pela ordem de definição, não pelo nome). `bgp_matcher.py::escanear_prefix_lists` virou
  `listar_prefix_lists`: agora lista TODAS as prefix-lists do equipamento (não só as já usadas pela
  sessão), sem pedir prefixo algum — o endpoint `POST /clientes/bgp/<id>/escanear-prefixo/` deixou de
  aceitar `prefixo`. UI ganhou um campo de busca no modal (útil com dezenas de prefix-lists por
  equipamento) e marca as já anunciadas nessa sessão como desabilitadas. Mikrotik é a única exceção,
  continua digitando o prefixo direto (não tem prefix-list nomeada separada).

Reverificado contra os 53 `BgpSnapshot` reais (todos os 4 fabricantes) — nenhum erro inesperado em
4716 combinações sessão×prefix-list testadas pro "anunciar prefixo novo".

---

## [Não publicado] — 2026-07-31 (Feat: Automação BGP)

### Adicionado

- **Automação BGP** (`clientes/backup_parser.py`, `clientes/bgp_matcher.py`, `clientes/bgp_actions.py`,
  `clientes/bgp_views.py`, `clientes/tasks.py` — `atualizar_snapshots_bgp`; nova página
  `/clientes/bgp/<acesso_id>/`, staff-only): a partir do backup mais recente de cada host, monta a
  estrutura de sessões BGP/prefix-lists/route-policies (Mikrotik RouterOS 6 e 7, Huawei, Cisco/
  Datacom, Juniper) e **simula de verdade** — não só lista — quais prefixos cada sessão está
  anunciando agora, com quantos prepends. Botões pra ativar/desativar uma sessão, adicionar prepend
  e parar de anunciar um prefixo, cada um com preview dos comandos reais antes de confirmar a
  execução no equipamento. Snapshot recalculado toda madrugada (02:45, depois do backup e do
  snapshot de conhecimento do Agent NOC) e persistido em `BgpSnapshot`; toda ação fica auditada em
  `AcaoBgp`. Reaproveita a conexão Netmiko já usada pelo Painel de Scripts
  (`script_views.py::_conectar_script`), só adicionando Juniper ao `DEVICE_TYPES` e tratamento de
  `commit()` explícito (nenhum outro fluxo do projeto chamava commit no driver Juniper antes).
  Validado ponta a ponta contra backups reais dos 4 fabricantes (não dados sintéticos) antes de
  entrar em produção — ver [docs/bgp_automacao.md](docs/bgp_automacao.md).

### Corrigido

- **Ações BGP no Huawei não eram aplicadas de verdade** (`clientes/bgp_actions.py`): reportado em
  produção — um `apply as-path ... additive` real rodou sem erro nenhum (prompt `[~...]` →
  `[*...]`, indicando mudança pendente), mas nunca foi commitado. O driver Netmiko `huawei_vrpv8`
  (usado por todo equipamento Huawei deste projeto) tem o mesmo modelo de config candidata/commit
  do Juniper — a versão inicial só tratava isso pro Juniper. Corrigido adicionando `'huawei'` a
  `_PRECISA_COMMIT`; `comandos_toggle_sessao`/`comandos_prepend`/`comandos_parar_anuncio` do Huawei
  agora incluem `'commit'` no preview/auditoria (filtrado antes do `send_config_set` pra não
  duplicar o commit — a execução real usa `conn.commit()`, não o texto literal).

### Adicionado (2)

- **Edição do comando antes de confirmar a ação BGP** (`clientes/bgp_views.py`,
  `clientes/templates/bgp_automacao.html`): o textarea do modal de confirmação passou a ser
  editável — dá pra ajustar o comando gerado automaticamente antes de enviar (ex: trocar o ASN
  usado no prepend por um diferente do AS local da sessão). `POST .../acao/` com `preview=false`
  agora aceita `comandos` (lista de strings) e usa exatamente esse texto em vez de gerar de novo;
  sem esse campo, cai de volta pra geração automática (comportamento anterior inalterado).

### Adicionado (3)

- **Stepper de quantidade no prepend** (`clientes/templates/bgp_automacao.html`): cada prefixo
  anunciado ganhou um contador `−`/`+` (1 a 20) ao lado do botão "Prepend" — dá pra adicionar mais
  de um prepend numa ação só, sem clicar repetidamente. O backend já aceitava `delta` arbitrário; só
  a UI estava fixa em `+1`.
- **Suporte a `fake-as` (Huawei) no prepend** (`clientes/backup_parser.py::parse_huawei`,
  `clientes/bgp_actions.py`): reportado em produção — quando um peer Huawei tem `peer IP fake-as N`
  configurado, o roteador se apresenta com o AS `N` só pra esse peer, e o AS_PATH que esse peer
  enxerga já usa `N`, não o AS real do `bgp <ASN>`. `parse_huawei` agora extrai `peer.fake_as` e
  calcula `peer.prepend_as = fake_as or as_local`; `comandos_prepend` prependa `prepend_as` em vez
  do AS real quando houver `fake-as` configurado (fallback automático pros outros fabricantes, que
  não têm esse campo). Validado contra backup real com `fake-as 271699` ≠ AS real `266550`.

---

## [Não publicado] — 2026-07-31 (Feat: Terminal Compartilhado + Link Externo)

### Adicionado

- **Terminal compartilhado (opt-in)** (`clientes/consumers.py` — `_SharedTerminalSession`,
  `_TerminalSessionRegistry`; `clientes/templates/terminal.html` — botão 🔗 Compartilhar): usuário
  conectado a um `Acesso` pode compartilhar sua sessão viva; outro usuário autorizado sobre o mesmo
  host, ao abrir o terminal, entra na mesma conexão física (mesmo shell) em vez de abrir a sua
  própria — vê o output em tempo real e digita junto. Se quem compartilhou sai, a conexão real com
  o equipamento continua viva para quem ainda está assistindo; só é fechada quando o último
  espectador sai. Ver [docs/terminal_ssh.md](docs/terminal_ssh.md#terminal-compartilhado-opt-in--adicionado-em-2026-07-31).
- **Link externo temporário para suporte** (`clientes/models.py` — `TerminalLinkExterno`;
  `clientes/consumers.py` — `TerminalLinkExternoConsumer`, rota `ws/ssh-link/`; nova página pública
  `/clientes/terminal/link/<uuid>/`, sem login): a partir de uma sessão compartilhada, gera um link
  com expiração configurável (15/30/60/120 min) para alguém de fora do CRM (ex: suporte de
  fabricante) acessar aquele terminal — leitura e escrita, como um espectador comum. Autorização
  100% pelo token (UUID); página isolada, sem sidebar de hosts nem qualquer outra parte do CRM;
  expira sozinho ou pode ser revogado manualmente antes da hora. `AcessoSessao` ganhou FK
  `link_externo` para auditoria (migration `0095`). Ver
  [docs/terminal_ssh.md](docs/terminal_ssh.md#link-externo--compartilhar-terminal-sem-login-adicionado-em-2026-07-31).

### Corrigido

- **Lacuna de autorização em `conectar_acesso()`** (`clientes/consumers.py`): não validava se o
  usuário autenticado tinha permissão sobre o `acesso_id` recebido do frontend — qualquer usuário
  autenticado podia abrir o terminal de qualquer host cadastrado, de qualquer cliente, bastando
  descobrir/adivinhar o ID. Adicionado `_usuario_pode_acessar()` (mesma regra de
  `listar_acessos_terminal`), chamado em toda conexão, compartilhada ou não.

---

## [Não publicado] — 2026-07-30 (Feat: Geofeed por Empresa — Fix LACNIC "prefixo não contido no bloco")

### Adicionado

- **Geofeed por empresa** (`clientes/models.py` — `GeofeedBloco.empresa`/`empresa_slug`; migration
  `0094_geofeed_bloco_empresa`; `home/views.py` — `geo_geofeed_csv_empresa`; nova rota
  `/homeferramentas/geo/geofeed/<empresa_slug>.csv`): o `geofeed.csv` global mistura blocos de
  empresas diferentes, e o LACNIC rejeita a URL cadastrada por uma empresa se qualquer linha do
  CSV pertencer a outro dono (`Prefixo IP do CSV de Geofeed não está contido no bloco original`).
  Cada empresa agora tem sua própria URL filtrada. UI em `geo_consulta.html`: coluna Empresa na
  tabela de blocos + seletor que troca a URL/preview exibidos. A rota antiga (`/geofeed.csv`, todos
  os blocos) continua existindo só para conferência interna — não deve mais ser cadastrada em RIR.

### Corrigido

- **Dados incorretos no Geofeed, achados via WHOIS**: dos 6 blocos cadastrados, 4
  (`186.65.76-79.0/24`) marcados como `empresa="INFORLIMA"` (dono real, AS272418); 1
  (`2804:57b0:efe0::/44`) marcado como `empresa="JMA Provedor"` (pertence ao `/32` da JMA, AS268080,
  não à INFORLIMA); 1 (`38.210.126.0/24`) desativado (`ativo=False`) por não estar alocado a
  ninguém no LACNIC. Ver `docs/GEOLOCALIZACAO_IP.md`.

---

## [Não publicado] — 2026-07-30 (Fix: LACNIC Rejeitando o Geofeed — Estado por Extenso)

### Corrigido

- **Geofeed rejeitado pela LACNIC com "CSV de Geofeed inválido (linha 6)"** (`home/views.py` —
  `_geo_regiao_iso`): blocos cadastrados com o estado por extenso (ex: "Bahia", "Rio de Janeiro")
  não eram convertidos para ISO 3166-2 (`BR-BA`, `BR-RJ`) — só siglas prontas eram reconhecidas.
  Adicionado mapa `_BR_UF_POR_NOME` (estado sem acento → sigla) como fallback na conversão. CSV
  também passou a usar quebra de linha CRLF conforme RFC 4180/8805. Esse fix já existia numa
  branch separada de uma sessão anterior e nunca tinha sido mesclado no `main` em produção — só
  aplicado agora (cherry-pick `007cee947`).
- **Bloco `186.65.78.0/24` com cidade inválida**: campo Cidade estava preenchido com o próprio
  prefixo IP (erro de digitação) — limpo diretamente no banco (`GeofeedBloco`), pois causaria
  rejeição na linha seguinte assim que a linha do Region fosse aceita. Ver `docs/GEOLOCALIZACAO_IP.md`.

---

## [Não publicado] — 2026-07-30 (Fix: Autofill do Chrome nos Campos de Pesquisa)

### Corrigido

- **Chrome preenchendo/sugerindo login automaticamente no campo de pesquisa** (`clientes/templates/listar.html`):
  os campos de busca da aba Acessos (`filtro-acessos-input`) e de Backups (`pesquisaBackup`) não
  tinham `autocomplete="off"` nem `name`, e o navegador os reconhecia como campo de login,
  exibindo sugestões de senhas salvas (ex: relatado em produção com a senha da "greentelecom").
  Adicionado `autocomplete="off"` e `name` dedicado nos dois campos.
- **Causa raiz do login salvo indevidamente pelo Chrome** (`clientes/templates/listar.html`,
  `templates/modal_acessos.html`): o formulário de cadastro/edição de Túnel Proxy tinha um par
  "Usuário" + "Senha" sem nenhum atributo de autocomplete — padrão que faz o navegador oferecer
  "salvar senha?" e gravar como login do site. Mesmo ajuste aplicado aos campos "Usuário" dos
  modais de Acesso/VPN (`autocomplete="off"` no usuário, `autocomplete="new-password"` na senha).
  Ver `docs/frontend_acessos.md`.

### Observado (não é bug)

- Widget do Cloudflare Turnstile no login passando sem exigir clique: comportamento esperado do
  modo "Managed" para sessões que não parecem suspeitas. A validação real continua no backend
  via `siteverify` (`usuario/views.py` — `_verificar_turnstile`).

---

## [Não publicado] — 2026-07-29 (Exportação de Senhas: TXT + fix de corte no PDF)

### Adicionado

- **Exportação de senhas em .txt** (`clientes/views.py` — `exportar_senhas_txt`, rota
  `/clientes/<id>/senhas/txt/`): mesma regra de permissão (`is_superuser`) e parâmetro `?root=`
  do PDF, gerando um arquivo de texto plano com um bloco por acesso. Novas opções no dropdown
  "Exportar Senhas" em `listar.html`. Ver `docs/frontend_acessos.md`.

### Corrigido

- **PDF de senhas cortando nas laterais / nomes não aparecendo** (`clientes/views.py` —
  `exportar_senhas_pdf`): o modo "Sem Senha Root" usava A4 retrato (18cm úteis) com colunas
  somando 22,5cm — a tabela ultrapassava a borda direita da página e o ReportLab cortava o
  conteúdo fora da área visível. Os dois modos (com/sem root) agora usam A4 paisagem (26,7cm
  úteis) com larguras de coluna recalculadas para caber com folga. Ver `docs/frontend_acessos.md`.

---

## [Não publicado] — 2026-07-27 (Correções: KEX SSH do Backup, WhatsApp Nono Dígito, Timeout RPKI)

### Corrigido

- **KEX SSH do backup restrito ao ZTE** (`clientes/views.py` — `realizar_backup`): o disable de
  `group-exchange-sha256/sha1`/`group16-sha512`/`group18-sha512` (pensado só pra CPU embarcada
  fraca de OLTs ZTE) era aplicado pra **todos** os fabricantes. Um Huawei NE8000 M8 só oferece
  `group-exchange-sha256` como KEX — desabilitar geral zerava o KEX em comum e o backup falhava
  com `Incompatible ssh peer (no acceptable kex algorithm)`. Restrito à flag `is_zte`. Ver
  `docs/backup_automatico.md`.
- **Cobrança WhatsApp com número BR sem o nono dígito** (`financeiro/whatsapp.py`,
  `atendimento/services.py`): números de celular BR podem existir no WhatsApp com ou sem o 9º
  dígito (contas antigas/portadas) e a Evolution API rejeitava com 400 `exists: false` sem nenhuma
  tentativa de variante. `_normalizar_telefone()` ainda rejeitava de propósito (`None`) o formato
  de 8 dígitos, bloqueando o número correto antes mesmo do envio. Agora o envio tenta
  automaticamente a variante alternada do 9º dígito. Ver `docs/FINANCEIRO.md`.
- **Timeout do RIPE Stat pulava o fallback RPKI** (`clientes/views.py` — `validar_rpki`): timeout
  na fonte primária (RIPE Stat) retornava erro na hora em vez de cair no fallback Cloudflare RPKI
  que já existe no código — um timeout pontual bastava pra marcar o bloco como erro sem tentar a
  segunda fonte. Ver `docs/RPKI_IRR.md` (novo).

---

## [Não publicado] — 2026-07-26 (Geolocalização de IP — Múltiplos Blocos/Localizações no Geofeed)

### Adicionado

- **Model `GeofeedBloco`** (`clientes/models.py`): fonte única de verdade do `geofeed.csv` público
  (RFC 8805) — antes o arquivo era montado deduplicando por prefixo dentro do histórico
  `CorrecaoGeoIP` (registro de solicitações de correção), o que era frágil e não permitia editar
  ou remover um bloco já publicado. Migrações `0092_geofeed_bloco` (cria a tabela) e
  `0093_geofeed_bloco_migrar_historico` (popula a partir do prefixo mais recente de cada
  `CorrecaoGeoIP`, sem perder o conteúdo já publicado no deploy).
- **Card "Blocos do Geofeed"** (`home/templates/geo_consulta.html`): tabela editável na tela de
  Geolocalização de IP com botão "+ Adicionar bloco" — cadastra quantos prefixos/localizações
  forem necessários (Prefixo, País, Região, Cidade, Postal Code), cada linha com salvar/remover
  independentes. Antes só era possível publicar 1 bloco por vez, repetindo manualmente todo o
  fluxo de busca + modal de correção para cada prefixo.
- **Endpoints** (`home/views.py`/`home/urls.py`): `geo_blocos_listar` (GET), `geo_blocos_salvar`
  (POST — aceita lista de blocos em uma única requisição) e `geo_blocos_excluir` (POST).
- **Coluna Postal-Code do RFC 8805** (`Prefix,Country,Region,City,Postal-Code`): antes sempre
  vazia — o formato agora inclui o campo quando informado no cadastro de blocos.
- `geo_atualizar` (fluxo de correção via busca de 1 IP, inalterado na UI) agora também grava em
  `GeofeedBloco`, mantendo as duas formas de cadastro na mesma fonte de verdade.

Ver `docs/GEOLOCALIZACAO_IP.md` para detalhes.

---

## [Não publicado] — 2026-07-24 (Hotspot — Cor do Painel/Texto e Tela de Sucesso)

### Adicionado

- **Cor do painel e cor do texto do formulário do hotspot** (`clientes/models.py`,
  `clientes/hotspot_views.py`, `clientes/templates/listar.html`): antes só a cor dos botões e
  do fundo da página eram customizáveis — o card de login em si tinha fundo escuro fixo e texto
  branco fixo, ficando ilegível em painéis claros. Dois campos novos em `HotspotConfig`
  (`cor_painel`, `cor_texto`) com seletor de cor na aba Hotspot, aplicados ao card de login e à
  tela de sucesso. Migrações `0089_merge_20260724_1552` (resolve conflito pré-existente no
  grafo de migrações do app `clientes`), `0090_hotspotconfig_cor_painel`,
  `0091_hotspotconfig_cor_texto`.
- **Tela de sucesso pós-conexão redesenhada** (`clientes/hotspot_views.py::_sucesso_page_html`):
  a tela exibida entre o envio do formulário e a liberação da internet — antes só um spinner
  sobre fundo liso — agora reaproveita a identidade visual do portal (logo, cores, painel),
  com ícone de check animado e saudação com o nome do lead. Mantém o redirect automático (agora
  em 2200ms) e o link de fallback para quando o redirect demora.

Ver `docs/HOTSPOT_CAPTIVE_PORTAL.md` para detalhes de cada item.

---

## [Não publicado] — 2026-07-20 (Editor de Topologia — Design e Efeitos Visuais)

### Adicionado

- **Passe de design completo no editor de Topologia** (`clientes/templates/topologia_editor.html`,
  `static/js/topo_main.js`, `static/js/topo_engine.js`): toolbar com sombra e botão "Salvar" em
  destaque, paleta de dispositivos agrupada por categoria (Rede/Core, Acesso/FTTH, Servidores,
  Outros, Anotações), grid de fundo "blueprint" (pontos + linhas a cada 100px), sheen sutil nos
  nodes, painel de propriedades com transições suaves e campos com anel de foco, nova legenda de
  interfaces (botão "Legenda" na toolbar) e dica de canvas vazio. Puramente visual/aditivo — não
  muda o `dados_json` salvo nem o comportamento de nenhuma ação existente.
- **Ícones de Roteador e Switch redesenhados** (`topo_engine.js`): roteador agora é um círculo
  com 4 setas retas apontando pra fora (estilo AWS "VPC Router"/Cisco "Router"); switch virou uma
  caixa de hardware física com porta uplink redonda + 4 portas RJ45, sem mais setas de
  encaminhamento (uma primeira tentativa com setas de "exchange" foi refeita a pedido, por não
  bater com a referência visual real).
- **Efeitos animados:** brilho nos ícones dos nodes (mais forte no hover/seleção), anel pulsante
  em nodes vinculados a um Acesso do CRM (indica "equipamento real monitorado"), tráfego
  simulado nos links — tracejado correndo + 2 "pacotes" (`<circle>` com `<animateMotion>`)
  viajando do Lado A pro Lado B na velocidade proporcional ao tamanho do link. Botão "Efeitos"
  na toolbar (ligado por padrão) desliga tudo de uma vez para topologias muito grandes.
- **IP de gerência em negrito** abaixo do nome de cada node, com o fundo do rótulo ligeiramente
  mais largo para não cortar IPs longos.

### Corrigido

- **Regressão no rótulo "Interface Lado A/B"**: a correção anterior (sessão passada) só afastava
  o *centro* do texto do node, sem considerar a própria largura — nomes de interface longos (ex.
  `ten-gigabit-ethernet 1/1/5`) ainda ficavam com metade do texto em cima do node em links
  horizontais. Corrigido somando `largura_do_rótulo/2` à distância mínima de afastamento.

Ver `docs/topologia.md` para detalhes de cada item.

---

## [Não publicado] — 2026-07-20 (Editor de Topologia, Backup)

### Adicionado

- **Sugestão de interface a partir do backup no editor de Topologia**
  (`clientes/views.py::interfaces_backup_acesso`, `static/js/topo_main.js`): os campos
  "Interface Lado A/B" do painel de propriedades do link agora são `<input list="...">` ligados
  a um `<datalist>` populado com os nomes de interface (+ descrição, quando o backup tiver) do
  backup mais recente do host em cada ponta do link. Sem backup do host, o campo continua texto
  livre normal, sem sugestões. Parser cobre MikroTik, Juniper e a sintaxe genérica
  Cisco/Huawei/Datacom/ZTE/HP/Dell/Extreme. Ver `docs/topologia.md`.
- **Troca manual do ícone do dispositivo** (`static/js/topo_main.js`): painel de propriedades do
  node ganhou seletor de Ícone/Tipo. Nodes importados do CRM ganham a flag `type_manual` ao ter
  o ícone trocado na mão, para a sincronização automática função→ícone não reverter a escolha do
  usuário na próxima reimportação/recarregamento — com botão para voltar ao modo automático. Ver
  `docs/topologia.md`.
- **Velocidades de interface 20/30/50 Gbps** no editor de Topologia (`static/js/topo_engine.js`).

### Corrigido

- **XSS armazenado no editor de Topologia** (`clientes/templates/topologia_editor.html`): o JSON
  da topologia era injetado no `<script>` de carregamento via `{{ dados_json|safe }}`, sem
  escape — texto livre salvo pelo usuário (ex. um nó "Texto/Legenda") podia fechar a tag
  `<script>` e executar JS arbitrário para quem abrisse aquela topologia depois. Corrigido para
  `JSON.parse("{{ dados_json|escapejs }}")`, mesmo padrão já usado em `topologia_drawio.html`.
- **Atalhos de teclado do editor de Topologia disparavam com foco em `<select>`**
  (`static/js/topo_main.js`): o guard só excluía `INPUT`/`TEXTAREA`; `Delete`/`Backspace` com um
  dropdown de propriedades focado apagava o nó/link selecionado sem intenção. Guard estendido
  para incluir `SELECT`.
- **Rótulo "Interface Lado A/B" escondido atrás do node em links curtos**
  (`static/js/topo_main.js`): posição calculada como % do comprimento do link caía dentro do
  raio visual do próprio node (desenhado por cima na camada SVG) em conexões curtas. Corrigido
  para distância fixa em pixels a partir da borda de cada node.
- **`FileNotFoundError` ao salvar backup de acesso com "/" no campo `tipo`**
  (`clientes/views.py::realizar_backup`): o nome do arquivo de backup só sanitizava espaços;
  `/` num `tipo` como `"BRAS/CGNAT/BORDA - JUNIPER"` virava separador de diretório inexistente
  no `os.path.join()`. Agora qualquer caractere fora de letras/números/`-`/`_` vira `_`. Afeta
  tanto o botão manual quanto o pipeline automático de backup. Ver `docs/backup_automatico.md`.

---

## [Não publicado] — 2026-07-20 (Auditoria de Acessos, Hotspot, Backup)

### Adicionado

- **Auditoria de Acessos** (`clientes/models.py`, `clientes/consumers.py`, `clientes/browser_vnc.py`,
  `clientes/winbox_vnc.py`, `clientes/views.py`, `clientes/admin.py`, `templates/modal_acessos.html`,
  migrações `0080`/`0081`): toda sessão SSH/Telnet/WinBox/WebFig passa a ser registrada —
  usuário do CRM, IP de origem, duração. Para SSH/Telnet grava comandos digitados
  (`AcessoComando`) e transcript completo da tela (ANSI removido). Para WinBox/WebFig via VNC
  grava a tela em `.mp4` via `ffmpeg`. Novo modal "Auditoria de Acessos" na aba de Acessos lista
  sessões, comandos e gravações. WebSocket dos consumers de terminal agora exige usuário
  autenticado (`code=4001` se anônimo). Ver `docs/AUDITORIA_ACESSOS.md`.

### Corrigido

- **Gravação de vídeo de sessões WinBox/WebFig às vezes ficava com 0 bytes**
  (`clientes/winbox_vnc.py`, `clientes/browser_vnc.py`): `stop()` podia ser chamado
  concorrentemente (thread de leitura do VNC + `disconnect()` do WebSocket), enviando dois
  `SIGTERM` ao `ffmpeg` em sequência — no segundo, o processo abortava sem finalizar o `.mp4`.
  Corrigido com trava (`threading.Lock`) tornando `stop()` idempotente. Ver `docs/winbox_vnc.md`.
- **Hotspot — `login.html` não aparecia em profiles recriados via SSH** (`clientes/hotspot_views.py`):
  o RouterOS resolve o `html-directory` do hotspot profile de forma inconsistente entre profiles
  (`<dir>` no profile `default`, `flash/<dir>` em profiles recriados via SSH). O CRM agora grava o
  `login.html` nos dois caminhos possíveis via SFTP e `/tool fetch`. Ver `docs/HOTSPOT_CAPTIVE_PORTAL.md`.
- **Hotspot — tela de status "Hi, guest!" aparecia em vez de liberar a navegação**
  (`clientes/hotspot_views.py`): quando `$(link-orig)` chegava vazio (caso comum, ver bug do
  `<meta refresh>` na sessão anterior), o `dst` do login ficava vazio e o RouterOS mostrava a
  tela de status. Corrigido com destino padrão por sistema operacional (detecção de captive
  portal nativa do Android/iOS/Windows), que fecha o mini-browser automaticamente. Ver
  `docs/HOTSPOT_CAPTIVE_PORTAL.md`.
- **Backup automático — detecção de fabricante falhava com modelo cadastrado errado**
  (`clientes/views.py::realizar_backup`): detecção usava só `modelo.nome`; passou a combinar
  `modelo.fabricante` + `modelo.nome` + `acesso.tipo`. Também adicionado
  `disabled_algorithms={'kex': [...]}` para evitar timeout de KEX em equipamentos ZTE durante o
  backup (mesmo problema já corrigido no terminal interativo). Ver `docs/backup_automatico.md`.

---

## [Não publicado] — 2026-06-16 (Agent NOC, Sala Virtual, Hotspot, Financeiro)

### Adicionado

- **API Key Claude individual por grupo WhatsApp** (`clientes/models.py`,
  `home/views.py`, `home/templates/agent_grupos.html`, `home/agent_engine.py`):
  cada grupo WhatsApp vinculado ao Agent NOC pode agora ter sua própria chave
  Anthropic, consumindo os créditos do próprio cliente em vez da chave global do
  sistema. Sem chave configurada, o agent fica em **silêncio total** naquele grupo
  (nenhuma mensagem de erro é enviada). Campo nunca exibe a chave real na UI — só o
  status configurada/não configurada — e o valor é mantido se o campo for deixado
  em branco ao salvar. Ver `docs/agent_noc.md`.

### Corrigido

- **Agent NOC não buscava sinal óptico em equipamentos Datacom (DmOS)**
  (`home/agent_engine.py`): o comando usado (`show interface <iface> transceiver`)
  não existe no DmOS; o correto é `show interface transceivers` (plural, sem
  interface). O agent agora executa esse comando automaticamente ao identificar uma
  interface física Datacom e filtra a saída para a interface relevante. Ver
  `docs/agent_noc.md`.
- **Sala Virtual de atendentes (WebRTC) — áudio cai sozinho após alguns minutos**
  (`atendimento/templates/atendimento/sala_virtual.html`): faltava o listener
  `onnegotiationneeded`, então a tentativa de recuperação via `restartIce()` nunca
  surtia efeito de fato. Implementado o padrão Perfect Negotiation (papéis
  polite/impolite determinísticos) e buffer de candidatos ICE recebidos antes da
  conexão estar pronta (corrige também o caso de "3 pessoas se ouvem, uma não" ao
  entrar várias pessoas ao mesmo tempo). Ver `docs/ATENDIMENTO.md`.
- **Hotspot — entrega do `login.html` ao MikroTik falhava** (`clientes/hotspot_views.py`):
  `/tool fetch` via HTTP falhava por DNS e depois por timeout de conexão em redes
  restritas; passou a usar SFTP pelo canal SSH já aberto, com fetch como fallback.
  Também corrigido `expected end of command` por falta de aspas em parâmetros
  RouterOS. Ver `docs/HOTSPOT_CAPTIVE_PORTAL.md`.
- **Alerta de cobrança via WhatsApp "não enviava"**: não era bug — a flag
  `ConfiguracaoFinanceira.wa_ativo` estava desativada (padrão de fábrica). A task
  agendada (`financeiro.tasks.enviar_alertas_whatsapp`, seg–sex 8:30) sempre rodava e
  sempre pulava silenciosamente.
- **Mensagem de cobrança de venda de equipamento não informava qual serviço**
  (`financeiro/models.py`, `financeiro/views.py`, `financeiro/whatsapp.py`): `Fatura`
  nunca teve de fato o campo M2M `vendas_equipamentos` que o código já tentava usar
  (`hasattr` sempre `False`), então a venda nunca era vinculada à fatura. Adicionado
  o campo (migração `0019_fatura_vendas_equipamentos`), corrigida a montagem da
  mensagem para incluir parcelas/data de início, e religadas retroativamente as 55
  faturas já existentes sem vínculo. Ver `docs/FINANCEIRO.md`.
- **Config do Agent NOC (API Key) não salvava — erro 500 silencioso**
  (`home/templates/agent_config.html`, `home/views.py`): localização pt-BR
  (`USE_L10N=True`) renderizava `0.2` como `0,2` no campo numérico de temperatura,
  invalidando o `<input type="number">` no navegador e quebrando o salvamento
  inteiro (incluindo a API Key) com `ValueError` no backend. Corrigido o
  template (`stringformat`) e tornado o backend resiliente a campos numéricos
  vazios. Ver `docs/agent_noc.md`.

---

## [Não publicado] — 2026-06-16 (VPN WireGuard — Isolamento por cliente)

### Corrigido

- **Conecta ISP perdeu acesso às redes internas após exclusão de outra VPN**
  (`clientes/vpn_manager.py`): `remover_peer()` apagava do kernel rotas
  (`ip route del <rede> dev wg0`) sem checar se **outro** cliente ainda
  dependia da mesma rota. Em 14/06, excluir a VPN do cliente 41 (Sartor
  Internet) apagou as rotas compartilhadas `10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`, `198.18.0.0/15` em `wg0`, das quais Conecta ISP (e outros
  clientes legados) ainda dependiam — o túnel UDP continuava de pé, só o
  roteamento interno parou. `remover_peer()` agora verifica
  (`_outro_peer_usa_rede()`) se algum outro `VPNWireGuard` ativo ainda
  declara a mesma rede antes de remover a rota.
- Rotas compartilhadas de `wg0` restauradas manualmente e persistidas em
  `/etc/wireguard/wg0.conf`.

### Adicionado

- **Interfaces isoladas por cliente** (`clientes/vpn_manager.py`,
  `clientes/views.py`): toda VPN WireGuard criada a partir de agora ganha
  sua própria interface dedicada (`wg5`, `wg6`, ...; porta e `/30` próprios)
  em vez de compartilhar `wg0` — elimina por completo a classe de bug em que
  criar/excluir a VPN de um cliente afeta as rotas de outro. Novas funções:
  `alocar_proxima_interface()`, `criar_interface_isolada()`,
  `adicionar_peer_isolado()`, `remover_interface_isolada()`,
  `vpn_e_isolada()`. `gerar_script_mikrotik()` agora gera o script com a
  porta/sub-rede corretas conforme o tipo de interface do cliente.
  Clientes legados (ids 3, 7, 8, 9, todos em `wg0`) não foram migrados —
  migração requer reconfigurar o WireGuard em cada MikroTik remotamente,
  registrado como recomendação futura.
- **Documentação** (`docs/vpn_wireguard.md`): arquitetura, causa raiz do
  incidente, limitação conhecida de faixas amplas idênticas entre clientes,
  e guia de diagnóstico rápido de roteamento.

---

## [Não publicado] — 2026-06-13 (Monitor de Tráfego com Abas + Hotspot Captive Portal)

### Adicionado

- **Sistema de abas no Monitor de Tráfego**
  (`monitoramento/templates/monitoramento/tab_monitoramento.html`,
  `monitoramento/views.py`):
  A aba de monitoramento ganhou uma barra de abas independentes. Cada aba tem seu próprio
  conjunto de painéis de gráficos Zabbix. Funcionalidades:
  - Criar nova aba (botão "+ Nova aba") — abre automaticamente input de renomeação
  - Trocar de aba — destrói instâncias Chart.js anteriores para liberar memória/CPU
  - Renomear aba por duplo-clique no nome ou pelo menu de contexto (clique direito)
  - Fechar aba pelo botão × ou pelo menu de contexto; bloqueado quando há apenas 1 aba
  - Badge com contador de gráficos por aba
  - Persistência no banco no formato `{ "tabs": [...] }` com compatibilidade retroativa
    (formato antigo de lista plana é migrado automaticamente para aba "Geral")
  - Chave localStorage migrada de `grph_charts_v2_<id>` para `grph_tabs_v1_<id>`

- **Menu de contexto (clique direito) nas abas do Monitor de Tráfego**
  (`monitoramento/templates/monitoramento/tab_monitoramento.html`):
  Clique direito em qualquer aba exibe menu com "Renomear aba" e "Fechar aba".
  O menu é posicionado junto ao cursor, respeita os limites da janela e fecha ao clicar fora.

### Corrigido

- **Hotspot captive portal não redirecionava para login antes de liberar internet**
  (`clientes/hotspot_views.py`): Quatro causas raiz identificadas e corrigidas:

  1. **JS bloqueado em mini-browsers** — `_gerar_login_html` agora usa
     `<meta http-equiv="refresh">` como redirecionamento primário (funciona sem JS).
     O `window.location.replace()` é mantido como secundário e um link `<a>` como
     último recurso.

  2. **Injeção HTML via `&` em URLs** — `$(link-login)` e `$(link-orig)` do MikroTik
     contêm `&` que quebravam atributos `value="..."`. Corrigido com
     `html.escape(..., quote=True)` em todas as variáveis inseridas em HTML.

  3. **Mixed content bloqueava POST do formulário** — `scheme` estava hardcoded como
     `'http'`. Quando o portal era acessado via HTTPS, o browser bloqueava o POST.
     Corrigido com `scheme = 'https' if request.is_secure() else 'http'`.

  4. **`link` vazio quando meta-refresh era usado** — O redirect via `<meta>` não passa
     parâmetros na URL, deixando o campo `link` do POST vazio. Adicionado fallback:
     `raw_link = link if link else f'http://{h.gateway}/login'`.

### Documentação

- `docs/monitoramento.md` — Atualizado com sistema de abas, nova API, variáveis de estado
  e histórico de alterações
- `docs/HOTSPOT_CAPTIVE_PORTAL.md` — Criado: fluxo completo do captive portal, 4 bugs
  corrigidos, compatibilidade com mini-browsers, configuração nginx e walled garden
- `docs/INDEX.md` — Atualizado com sessão 3, novos arquivos e histórico

---

## [Não publicado] — 2026-06-03 (WinBox Web VNC — Correções)

### Corrigido

- **`WinboxVNCManager.__init__()` — parâmetros `width` e `height` faltando**
  (`clientes/winbox_vnc.py`): O consumer passava `width=` e `height=` ao construtor
  mas o `__init__` não declarava esses parâmetros, causando `NameError` e impedindo
  qualquer sessão WinBox de iniciar. Parâmetros adicionados com defaults `width=1366, height=768`.

- **WinBox abrindo minúsculo no browser** (`clientes/winbox_vnc.py`):
  Flag `-ncache 10` no x11vnc fazia o servidor reportar ao noVNC uma tela 10× mais alta
  que a real (ex: 1400×8000 em vez de 1400×800). O noVNC escalava todo o conteúdo para
  caber nessa altura virtual, fazendo o WinBox aparecer como um quadradinho minúsculo no
  topo da tela. Removidos os flags problemáticos: `-ncache`, `-noscr`, `-xrandr`, `-threads`,
  `-nowireframe`. x11vnc restaurado ao comando simples e estável.

- **`rfb.resizeSession = true` desmaximizando o WinBox** (`clientes/templates/winbox.html`):
  O noVNC com `resizeSession=true` enviava `SetDesktopSize` ao x11vnc após conectar,
  podendo causar resize do Xvfb e desmaximizar o WinBox. Definido como `false` pois
  o Xvfb já é criado com as dimensões exatas do viewport do usuário.

### Adicionado

- **Maximização via `xdotool`** (`clientes/winbox_vnc.py`):
  Após iniciar o WinBox, um processo background aguarda a janela aparecer
  (`xdotool search --sync --name 'WinBox'`) e a redimensiona para a resolução correta,
  servindo como fallback ao Openbox.

- **`xdotool` e `wmctrl`** instalados no servidor como dependências do WinBox VNC.

- **Documentação** (`docs/winbox_vnc.md`): Documentação técnica completa do módulo
  WinBox Web VNC, incluindo arquitetura, fluxo de inicialização, problemas conhecidos
  e como testar manualmente.

---

## [Não publicado] — 2026-06-02 (Notificações de Chamados em Aberto no CRM)

### Adicionado

- **Notificador global de chamados em aberto** (`templates/base.html`):
  Sistema de notificação em tempo real via WebSocket para usuários fora do módulo de
  atendimento (em páginas como Clientes, Financeiro, Monitoramento, etc.).
  Ao chegar um novo chamado em aberto (conversa sem atendente), aparece um toast vermelho
  no canto inferior direito com animação de pulso, nome do grupo e link direto para a conversa.
  Badge vermelho com contador aparece no botão "Atendimento" da barra de navegação global.
  Som de alerta duplo e notificação do navegador (se permissão concedida) também são emitidos.
  Apenas para `is_staff`. Reconecta automaticamente com backoff exponencial em caso de queda.
  Arquivos: `templates/base.html` (container `#globalTicketToasts`, estilos `.gtkt-*`,
  script com `connect()`, `showToast()`, `updateBadge()`, `dismissToast()`).

- **Toast visual diferenciado para chamados em aberto** (`atendimento/templates/atendimento/base.html`):
  Dentro do módulo de atendimento, toasts de *novo chamado em aberto* (conversa não assumida)
  agora têm estilo vermelho distinto do toast de mensagem normal (azul/cinza).
  Estilo `msg-toast-ticket`: borda vermelha, fundo vermelho translúcido, animação de pulso
  `ticketPulse`. Ícone de sino vermelho no lugar das iniciais do grupo.
  Label "NOVO CHAMADO EM ABERTO" em vermelho acima do nome do grupo.
  Toasts de mensagens da própria conversa assumida continuam com estilo original.
  Arquivos: `atendimento/templates/atendimento/base.html` (CSS `.msg-toast-ticket`,
  `.msg-toast-icon-ticket`, `.msg-toast-label`, `@keyframes ticketPulse`;
  função `showToast(groupName, msgText, convId, initials, isNewTicket)`).

### Alterado

- **`showToast()` na base do atendimento** (`atendimento/templates/atendimento/base.html`):
  Assinatura alterada de `showToast(groupName, msgText, convId, initials)` para
  `showToast(groupName, msgText, convId, initials, isNewTicket)`. Chamada atualizada
  para passar `isUnassigned` como quinto argumento, ativando o estilo vermelho apenas
  em chamados sem atendente.

- **Botão "Atendimento" na navegação global** (`templates/base.html`):
  Adicionados `id="globalAtendBtn"` e `style="position:relative;"` para permitir
  anexar o badge numérico vermelho via JavaScript sem alterar layout.

---

## [Não publicado] — 2026-06-01 (sessão 4 — Módulo Financeiro)

### Adicionado

- **Sistema de Recorrência de Despesas** (`financeiro/models.py`, `financeiro/views.py`,
  `financeiro/templates/`, migrations `0004-0005`): Novo sistema para despesas recorrentes
  com suporte a UNICA/MENSAL/BIMESTRAL/TRIMESTRAL/SEMESTRAL/ANUAL.
  Campos adicionados em `Despesa`: `recorrencia`, `meses_recorrencia`, `ocorrencia_atual`,
  `status` (PENDENTE/PAGO), `data_pagamento`.
  Endpoint `POST /financeiro/api/despesa/{id}/pagar/` marca como pago e auto-gera próxima
  ocorrência. Interface com checkbox "Recorrência" e campo "Total de meses (vazio=indefinido)".
  Exibição visual: "2/12 mensal" em cor roxa. (docs: `docs/FINANCEIRO.md`)

- **Sistema de Privacidade para Despesas** (`financeiro/models.py`, `financeiro/views.py`,
  migrations `0006`): Campo `privada` em Despesa. Despesas privadas visíveis apenas para
  criador, públicas para todos. Checkbox "🔒 Privada (apenas você vê)" nos modais.
  Indicador visual com ícone de cadeado roxo. Filtro automático em `api_listar_despesas`
  via `Q(privada=False) | Q(privada=True, criado_por=request.user)`.

- **Sistema de Privacidade para Faturas** (`financeiro/models.py`, `financeiro/views.py`,
  migrations `0007`): Campo `privada` em Fatura. Faturas privadas visíveis apenas para staff.
  Checkbox "🔒 Privada (apenas você vê)" no modal "Nova Fatura Manual".
  Indicador visual com ícone de cadeado roxo.
  Validação: `api_visualizar_fatura` retorna 403 para não-staff ao acessar privada.

- **Sistema de Privacidade para Consultorias, Aluguéis e Vendas**
  (`financeiro/models.py`, `financeiro/views.py`, migrations `0008-0010`):
  Mesmo padrão de privacidade aplicado a: `Consultoria`, `AluguelIPv4`, `VendaEquipamento`.
  Todos com campo `privada` (default=False), checkbox nos modais, indicador visual 🔒.
  Controle de acesso: staff vê privadas, usuários veem apenas públicas.

### Alterado

- **Layout de Listagem de Despesas** (`financeiro/templates/financeiro/dashboard.html`):
  CSS Grid ajustado para evitar sobreposição de nome na data de vencimento.
  Colunas finais: `2.5fr 80px 90px 180px 120px` (Nome | Recorrência | Valor | Vencimento | Status).
  Vencimento expandido de 120px para 180px.
- **Admin Panel** (`financeiro/admin.py`): Adicionado display e filtro de `privada`
  em DespesaAdmin, FaturaAdmin, ConsultoriaAdmin, AluguelIPv4Admin, VendaEquipamentoAdmin.

### Corrigido

- **Sobreposição de nome em vencimento**: Aumentado grid-column de vencimento
  de 120px → 140px → 180px em iterações sucessivas.
- **Nome incorreto em migração 0009**: `model_name='alugueipv4'` corrigido para
  `model_name='aluguelipv4'`.
- **Servidor 502 Bad Gateway**: Gunicorn reiniciado após migrações.

---

## [Não publicado] — 2026-05-27 (sessão 3)

### Adicionado

- **Dashboard de Monitoramento — persistência no banco** (`monitoramento/models.py`,
  `monitoramento/views.py`, `monitoramento/urls.py`, migration `0002`):
  Novo modelo `MonitorDashConfig` (OneToOne com Cliente, JSONField `dados`).
  Endpoints `GET /monitoramento/dash/carregar/` e `POST /monitoramento/dash/salvar/`.
  O frontend agora carrega do backend na inicialização e salva a cada alteração.
  Migração automática do localStorage para o banco na primeira abertura da aba.
  **Problema resolvido:** gráficos adicionados por um usuário agora aparecem para todos
  os usuários com acesso ao mesmo cliente. (docs: `docs/monitoramento.md`)

- **Senha Root — controle de visibilidade** (`clientes/templates/listar.html`,
  `templates/modal_acessos.html`, `clientes/views.py`): campo `senha_adm` (Senha Root)
  ocultado para usuários do tipo cliente em quatro locais: card de acesso, modal Novo
  Acesso, modal Editar Acesso, modal Duplicar Acesso. A API `buscar_acesso` também retorna
  `senha_adm=''` para não-staff. Visível apenas para `is_staff` ou `is_superuser`.

- **Gerador de senha aleatória** (`templates/modal_acessos.html`): botão 🎲 adicionado
  ao lado dos campos Senha e Senha Admin nos modais Novo Acesso, Editar Acesso e Duplicar
  Acesso. Gera 16 caracteres via `crypto.getRandomValues`, exibe em texto, copia para
  clipboard automaticamente e confirma com ✓ verde por 1,5s.

- **Exportação de credenciais em PDF** (`clientes/views.py`, `clientes/urls.py`,
  `clientes/templates/listar.html`): botão dropdown "Exportar Senhas" no cabeçalho da
  página do cliente (visível apenas para `is_superuser`). Duas opções:
  - *Sem Senha Root* — A4 retrato, 7 colunas, arquivo `*_sem_root.pdf`
  - *Com Senha Root* — A4 paisagem, 8 colunas, arquivo `*_com_root.pdf`
  Endpoint: `GET /clientes/<id>/senhas/pdf/?root=0|1`.
  PDF gerado via ReportLab. (docs: `docs/frontend_acessos.md`)

- **Envio periódico de PDF com credenciais** (`clientes/tasks.py`, `crm/celery.py`):
  task `enviar_pdf_credenciais` gera PDF A4 paisagem com todos os clientes e acessos
  e envia a cada 2 dias para `campelosuporte.ti@gmail.com`, `noc@tomich.com.br` e
  `danilo@tomich.com.br`. Usa a configuração SMTP do sistema (`ConfiguracaoSistema`).
  (docs: `docs/envio_credenciais_email.md`)

### Alterado

- **Habilitação automática de backup — exclusão de VM e Hipervisor**
  (`clientes/tasks.py`): `habilitar_backups_automaticos` agora exclui acessos com função
  contendo `vm`, `hipervisor` ou `hypervisor` (case-insensitive) ao habilitar backup e ao
  corrigir templates. Adicionada varredura de limpeza: a cada execução remove `backup_habilitado`,
  `backup_automatico` e `backup_template` de qualquer acesso com função VM/Hipervisor já
  marcado. Resultado da primeira varredura: 112 equipamentos removidos (511 → 399).
  (docs: `docs/backup_automatico.md`)

---

## [Não publicado] — 2026-05-26 (sessão 2)

### Adicionado

- **Ícones de topologia por função** (`static/js/topo_engine.js`): novos tipos de dispositivo
  `cgnat` (ícone NAT com setas many-to-one, laranja) e `vm` (caixas empilhadas, roxo).
  Mapeamento automático de função → tipo no endpoint `topologia/hosts/`:
  CGNAT/CG-NAT → `cgnat`; BRAS/BNG → `router`; VM/KVM/VMware/VPS → `vm`.
  Ícones existentes de Borda/Border/Core já mapeavam para `router`.

- **Atualização automática de ícones em topologias salvas** (`static/js/topo_main.js`):
  ao abrir uma topologia salva, o método `_refreshCrmNodeTypes()` consulta o backend e
  atualiza o tipo/ícone dos nós CRM sem mover posições, garantindo que mudanças de
  mapeamento reflitam em diagramas existentes.

- **Remoção de host da topologia**: já estava implementado (`_deleteSelected()`) —
  botão "Remover" no painel de propriedades e tecla `Delete`/`Backspace`.

- **Portfólio de modelos de equipamento** (`modelo_equipamento`): 225 modelos carrier-grade
  inseridos para Huawei (52), Cisco (38), Fiberhome (16), Datacom (20), Intelbras (18),
  Mikrotik (37). Modelos Juniper (46), TP-Link (8) e VSOL (16) adicionados posteriormente
  totalizando **287 modelos**. Fabricantes normalizados para grafia consistente.

- **Auto-detecção de modelo via backup** (`clientes/tasks.py`, `clientes/models.py`,
  migration `0064`): campo `modelo_auto_em` adicionado em `Acesso`. Task
  `detectar_modelos_via_backup` lê o arquivo de backup mais recente de cada host SSH,
  extrai o model string via regex (RouterOS, Cisco IOS/XE/XR, Huawei VRP, ZTE, Datacom,
  A10) e faz match contra `Modelo_equipamento`. Resultado: 91 modelos detectados
  automaticamente (99% de cobertura). Roda a cada 3 dias via Celery Beat.

- **Habilitação automática de backup** (`clientes/tasks.py`): task
  `habilitar_backups_automaticos` habilita `backup_habilitado`, `backup_automatico` e
  seleciona o template correto para todos os acessos SSH com modelo. Regras:
  ZTE/Parks → template Cisco; Huawei OLTs (MA5xxx) → template OLT Huawei;
  demais → match por fabricante. 511 equipamentos com backup ativo.

- **Rotina de backup completa** (`clientes/tasks.py`,
  `clientes/management/commands/rotina_backup.py`): task `rotina_backup_completa`
  encadeia detecção de modelo + habilitação de backup. Management command
  `python manage.py rotina_backup` executa o pipeline com saída formatada e suporta
  flags `--forcar-modelo` e `--apenas-templates`. Agendado diariamente às 01h.

### Alterado

- **Versão dos arquivos JS de topologia**: `topo_engine.js` e `topo_main.js`
  atualizados para `v=8` para invalidar cache do browser.

- **Normalização de fabricantes** no banco `modelo_equipamento`: registros com
  `HUAWEI`, `CISCO`, `MIKROTIK`, `DATACOM`, `INTELBRAS`, `FIBERHOME`, `ZTE Corporation`
  normalizados para grafia título (`Huawei`, `Cisco`, etc.).

---

## [Não publicado] — 2026-05-26

### Corrigido

- **Terminal SSH** (`clientes/consumers.py`): reordenação dos KexAlgorithms para colocar
  `diffie-hellman-group14-sha256` antes de `diffie-hellman-group16-sha512`, eliminando
  timeout em equipamentos ZTE com CPU lenta (DH 4096-bit causava atraso crítico no handshake).

- **Modais de Acesso** (`static/js/main.js`): modais (`modalAcesso`, `modalEditarAcesso`,
  `modalDuplicarAcesso`) agora são movidos para `document.body` no momento de abertura,
  corrigindo bug de posicionamento causado por containers ancestrais com `position: relative`
  ou `transform` interferindo no `position: fixed`.

### Adicionado

- **IPAM — Agrupamento /24** (`clientes/ipam_views.py`): a função `_get_or_create_prefixo_pai`
  passou a criar também um registro `IPAMSubRede` (status `reservado`) para o bloco /24 pai,
  além do `IPAMPrefixo` container já existente. Resultado: blocos /24 aparecem tanto na aba
  de Prefixos quanto na aba de Sub-redes do IPAM.

- **Filtro inline de Acessos** (`clientes/templates/listar.html`): campo de busca em tempo
  real por nome (tipo) e por endereço IP (host / host_ipv6), substituindo o antigo botão
  "Filtrar Acessos". Inclui contador de resultados e restauração da visão em abas ao limpar.

- **Monitor de Tokens — Agent NOC** (`home/views.py`, `home/urls.py`,
  `home/templates/agent_config.html`): nova view `agent_token_stats` com endpoint
  `GET /agent/config/token-stats/?periodo=<24h|7d|30d|all>` que devolve consumo de tokens,
  custo estimado em USD e BRL (cotação em tempo real via AwesomeAPI) e histórico diário dos
  últimos 14 dias. Interface exibida no painel de configuração do Agent NOC.

### Alterado

- **CSS Modal Overlay** (`static/css/style.css`): `.modal-overlay` usa `inset: 0` em vez de
  `top/left/width/height` individuais e `overflow-y: auto`; `.modal-acesso` utiliza
  `margin: 40px auto` para centralização correta independente do contêiner pai.

---

## Versões anteriores

| Commit     | Descrição                                         |
|------------|---------------------------------------------------|
| `314b907e` | Agent NOC melhorado, plataforma estável           |
| `8751731e` | Implementado Agent NOC inicial                    |
| `4c90ccc1` | Terminal melhorado e redesign geral               |
| `d248e753` | Pesquisa LG, gerenciador de firmware, UI temática |
| `4559c99e` | IRR automatizado pela plataforma                  |
