# Topologia de Rede — Documentação Técnica

**Arquivos principais:**
- `clientes/templates/topologia_editor.html`
- `static/js/topo_engine.js`
- `static/js/topo_main.js`

**Atualizado em:** 2026-07-20

---

## Visão Geral

Editor visual de topologia de rede baseado em SVG, com suporte a:
- Drag & drop de dispositivos a partir da paleta lateral
- Conexões entre nós com drag a partir dos pontos de ancoragem
- Importação automática de hosts do CRM
- Exportação PNG (via canvas 2×), Undo/Redo, Grid, Snap
- Acesso direto aos hosts via terminal/browser
- Waypoints: dobrar conexões arrastando pontos intermediários

---

## Arquitetura dos Arquivos JS

| Arquivo | Responsabilidade |
|---|---|
| `topo_engine.js` | Definição de tipos (`DEVICES`), interfaces (`IFACES`) e paths SVG dos ícones (`ICONS`) |
| `topo_main.js` | Classe `TopoEditor` — lógica de renderização, eventos, persistência e importação |

Versão atual: **v=23** (parâmetro de cache-busting no HTML).

---

## Tipos de Dispositivo (`DEVICES`)

Cada tipo tem `label`, `color` (hex) e `icon` (chave em `ICONS`).

| Tipo | Label | Cor | Ícone |
|---|---|---|---|
| `router` | Roteador | `#00d9ff` | Círculo preenchido com 4 setas retas apontando pra fora (N/S/L/O) — estilo AWS/Cisco |
| `switch_l2` | Switch L2 | `#3fb950` | Caixa física com porta uplink (jack redondo) + 4 portas RJ45 + badge "L2" |
| `switch_l3` | Switch L3 | `#58a6ff` | Caixa física com porta uplink (jack redondo) + 4 portas RJ45 + badge "L3" |
| `radio` | Rádio | `#ffa657` | Ondas de rádio |
| `dwdm` | DWDM | `#bc8cff` | Caixa com elipses ópticas |
| `olt` | OLT | `#e3b341` | Rack com slots + label OLT |
| `onu` | ONU/ONT | `#63e6be` | Box compacto + label ONU |
| `server` | Servidor | `#8b949e` | Rack de 3 unidades |
| `firewall` | Firewall | `#f85149` | Escudo |
| `cgnat` | CGNAT | `#ff6b35` | Caixa com 3 entradas → 1 saída + "NAT" |
| `vm` | VM | `#a78bfa` | Caixas empilhadas + "VM" |
| `cloud` | Cloud/ISP | `#6e7681` | Nuvem |
| `cpe` | CPE | `#d2a8ff` | Caixa com antena |
| `host` | Host/PC | `#79c0ff` | Monitor |
| `text_box` | Texto/Legenda | `#e3b341` | Caixa tracejada |

---

## Mapeamento Automático Função → Tipo (importação do CRM)

Ao importar hosts via `GET /clientes/<id>/topologia/hosts/`, o backend
(`clientes/views.py → topologia_hosts`) mapeia o campo `funcao.descricao`
e `acesso.tipo` (ambos lowercased) para o tipo de dispositivo:

| Keywords no nome da função/tipo | Tipo resultante |
|---|---|
| `cgnat`, `cg-nat`, `carrier grade nat` | `cgnat` |
| `bras`, `bng`, `broadband network` | `router` |
| `router`, `roteador`, `core`, `border`, `borda` | `router` |
| `switch l3`, `sw-l3`, `camada 3` | `switch_l3` |
| `switch`, `sw-`, `catalyst`, `nexus` | `switch_l2` |
| `radio`, `wireless`, `ubiquiti`, `mikrotik`, `ap `, `airmax`, `ltu` | `radio` |
| `dwdm`, `oadm`, `ots`, `mstp`, `transponder` | `dwdm` |
| `olt`, `gpon`, `xgs`, `epon` | `olt` |
| `onu`, `ont` | `onu` |
| `server`, `servidor`, `zabbix`, `grafana`, `proxmox` | `server` |
| `firewall`, `utm`, `fortigate`, `pfsense`, `sophos` | `firewall` |
| `vm`, `virtual machine`, `virtualizado`, `kvm`, `qemu`, `vmware`, `vps` | `vm` |
| `cpe`, `modem` | `cpe` |
| *(padrão)* | `host` |

---

## Atualização Automática de Ícones em Topologias Salvas

**Problema anterior:** ao salvar uma topologia, o `type` dos nós era gravado no JSON.
Se o mapeamento de funções mudava, as topologias já salvas mantinham o tipo antigo.

**Solução (`topo_main.js → _refreshCrmNodeTypes`):**
Após carregar uma topologia salva (`fromJSON`), o método consulta o endpoint de hosts e
atualiza silenciosamente o `type` de qualquer nó CRM (`id` prefixado com `crm_`) cujo
tipo tenha mudado, sem alterar posição ou outros atributos.

```javascript
// Chamado automaticamente após fromJSON()
topo._refreshCrmNodeTypes();
```

Ao clicar em "Importar Hosts" com topologia já existente, nós já presentes têm o tipo
atualizado sem ser movidos ou duplicados.

### Trocar o Ícone Manualmente — Adicionado em 2026-07-20

O painel de propriedades de um dispositivo (clique no node) ganhou um campo **Ícone / Tipo**
(`<select>` com todos os tipos de `TOPO_DEVICES`, exceto `text_box`) para trocar o ícone de
qualquer node — inclusive os importados do CRM, que antes só podiam ter o ícone definido pelo
mapeamento automático função→tipo.

**Conflito com a atualização automática (acima) e como foi resolvido:** como
`_refreshCrmNodeTypes`/`importHosts` reescrevem o `type` de nós CRM sempre que ele diverge do
mapeamento automático, uma troca manual de ícone seria **revertida silenciosamente** no próximo
"Importar Hosts" ou logo ao recarregar a página (que chama `_refreshCrmNodeTypes()` depois de
`fromJSON`). Para evitar isso, a troca manual (`_applyNodeProps`) marca o node com
`node.type_manual = true`; `_refreshCrmNodeTypes` e `importHosts` pulam a sincronização
automática de `type` (mas continuam atualizando `funcao`) para qualquer node com essa flag.

- Nodes com `acesso_id` mostram uma nota no painel indicando se o ícone está no modo
  **automático** (segue a função cadastrada no CRM) ou **fixado manualmente**, com um botão
  **"Voltar a ícone automático"** que limpa `type_manual` e resincroniza imediatamente
  (`_resetNodeTypeAuto` → `await _refreshCrmNodeTypes()`).
- Nodes sem `acesso_id` (adicionados manualmente da paleta) não têm essa nota — nunca são
  tocados pela sincronização automática de qualquer forma.
- `type_manual` é salvo junto do node no `dados_json` da topologia (persiste entre sessões).

---

## Conexões (Links)

### Propriedades de uma conexão

| Campo | Tipo | Descrição |
|---|---|---|
| `iface` | string | Velocidade/tipo de interface (chave em `IFACES`) |
| `label` | string | Rótulo livre exibido no meio da linha |
| `ip_local` | string | IP P2P lado A (ex: `10.0.0.1/30`) |
| `ip_remote` | string | IP P2P lado B (ex: `10.0.0.2/30`) |
| `vlan` | string | VLAN ID — exibida como `V100` junto ao label de velocidade |
| `iface_a` | string | Nome da interface no lado A — nó de origem do link (ex: `ge0/0/1`, `eth0`, `sfp1`) |
| `iface_b` | string | Nome da interface no lado B — nó de destino do link (ex: `ge0/0/2`, `eth1`, `sfp2`) |
| `style` | `solid`\|`dashed`\|`dotted` | Estilo do traço |
| `shape` | `straight`\|`curved`\|`wavy` | Forma da linha |
| `waypoints` | `[{x,y}, ...]` | Pontos intermediários que dobram a linha |

### Tipos de Interface (`IFACES`)

| Chave | Label | Cor |
|---|---|---|
| `100m` | 100 Mbps | cinza |
| `1g` | 1 Gbps | verde |
| `10g` | 10 Gbps | ciano |
| `20g` | 20 Gbps | ciano-claro |
| `30g` | 30 Gbps | azul |
| `40g` | 40 Gbps | azul |
| `50g` | 50 Gbps | violeta |
| `100g` | 100 Gbps | roxo |
| `sfp` | SFP 1G | amarelo |
| `sfp+` | SFP+ 10G | laranja |
| `gpon` | GPON | salmão |
| `xpon` | XGS-PON | lilás |
| `wifi` | Wireless | amarelo |
| `mw` | Microwave | laranja |
| `other` | Outro | cinza |

`20g`/`30g`/`50g` adicionados em 2026-07-20 (útil também para representar link
aggregation/LACP, ex. 2×10G = 20G).

### Sugestão de Interface a partir do Backup — Adicionado em 2026-07-20

Os campos **Interface Lado A** e **Interface Lado B** do painel de propriedades do link são
`<input list="...">` ligados a um `<datalist>` populado a partir do **backup mais recente** do
host em cada ponta do link (`node.acesso_id` — só existe em nós importados do CRM via "Importar
Hosts"). O campo continua 100% texto livre: o `<datalist>` só oferece sugestões clicáveis, nunca
restringe o valor digitado.

- **Sem backup do host** (nó não veio do CRM, ou o CRM nunca fez backup daquele acesso) → a
  lista de sugestões fica vazia e o campo se comporta como um input comum, sem dropdown.
- Lado A busca interfaces do **nó de origem** (`link.src`), lado B do **nó de destino**
  (`link.tgt`) — os mesmos nós usados para renderizar os rótulos "Lado A"/"Lado B" na linha.

**Backend:** `GET /clientes/acessos/<acesso_id>/interfaces-backup/`
(`clientes.views.interfaces_backup_acesso`) — busca o `BackupLog` mais recente com
status `SUCESSO`/`PARCIAL` daquele acesso, lê o arquivo (limite 2MB) e extrai os nomes de
interface com `_extrair_interfaces_backup(conteudo, fabricante)`, usando o **fabricante do
`BackupTemplate`** que gerou aquele backup (mais confiável que tentar re-detectar pelo
`Modelo_equipamento`, que pode estar cadastrado errado):

| Fabricante | Fonte no backup | Exemplo extraído |
|---|---|---|
| `MIKROTIK` | `/export` — `name=` (interfaces renomeadas) + `interface=` (referências, cobre nomes default nunca renomeados) | `sfp-sfpplus2 - P2P-SW-CORE-P6` |
| `JUNIPER` | `show configuration \| display set` — `set interfaces <if> [unit N] description <texto>` | nome `et-0/0/1.5`, descrição `VLAN.PTP.CCR.SG` |
| Todos os outros (Cisco/Huawei/Datacom/ZTE/HP/Dell/Extreme/genérico) | `show running-config` / `display current-configuration` — linha `interface <nome>` (nome pode ter espaço, ex. Datacom `gigabit-ethernet 1/1/1`) seguida, quando existe, de `description <texto>` na linha imediatamente abaixo | nome `100GE1/0/10`, descrição `P2P-AVATO-ROTA-3-FO_AVATO` |

**Filtro de sub-interfaces de ONU:** nomes terminados em `:<número>` (ex.
`gpon-onu_1/2/1:5`, padrão de OLTs ZTE — uma linha `interface` por ONU registrada na porta
GPON) são descartados. Nunca são o lado de um link de topologia (a ONU do cliente não é um nó
da topologia física entre equipamentos), e em OLTs grandes esse padrão pode gerar milhares de
linhas que ofuscariam as interfaces físicas relevantes (uplinks, portas PON) dentro do limite de
500 resultados por consulta.

**Descrição — Adicionado em 2026-07-20:** cada item retornado por `_extrair_interfaces_backup`
é `{'nome': str, 'descricao': str}`, não só o nome. Para MikroTik a "descrição" já vem embutida
no próprio nome renomeado (não há campo separado a extrair); para os demais fabricantes, a
descrição é lida do texto associado à interface no backup — `description <texto>` na linha
seguinte a `interface <nome>` (Cisco/Huawei/ZTE) ou no comando `... description <texto>`
(Juniper). No frontend, a descrição é usada como legenda da sugestão no `<datalist>` (atributo
`label` + texto do `<option>`, para cobrir tanto Firefox quanto Chrome/Edge) — ex. o usuário vê
"et-0/0/1 — P2P-SW-CORE-P6" na lista em vez de só o nome físico da porta, o que ajuda a
identificar qual interface é o link certo sem precisar abrir o backup.

**Permissão:** mesmo padrão dos outros endpoints escopados por acesso — staff/superuser veem
qualquer acesso; usuário comum só vê acessos do próprio cliente vinculado.

**Frontend (`topo_main.js`):** `_showLinkProps` monta os `<datalist>` vazios e dispara
`_populateIfaceDatalist` para cada lado de forma assíncrona. Resultados são cacheados por
`acesso_id` na sessão do editor (`this._ifaceCache`) e um contador de geração (`_propsGen`,
incrementado em `_deselect()`) descarta respostas que chegam depois que o usuário já trocou de
seleção — evita popular o datalist do link errado se duas buscas estiverem em voo ao mesmo tempo.

### Preenchimento automático do IP P2P a partir da Interface — Adicionado em 2026-07-20

Quando a interface escolhida em **Lado A** ou **Lado B** tem um endereço IP roteado configurado
no backup (não é uma porta L2 pura/trunk), o campo **IP Local/Remoto (P2P)** correspondente é
preenchido automaticamente com esse IP — só quando esse campo ainda está **vazio** (nunca
sobrescreve um IP já digitado manualmente).

- `_extrair_interfaces_backup` agora retorna `{'nome', 'descricao', 'ip'}` por interface. O `ip`
  vem vazio quando a interface não tem endereçamento (a maioria das portas de switch/trunk).
- Fonte do IP por fabricante:

  | Fabricante | Fonte no backup |
  |---|---|
  | `MIKROTIK` | `/ip address add address=IP/CIDR interface=<nome>` |
  | `JUNIPER` | `set interfaces <if> unit N family inet address IP/CIDR` |
  | Todos os outros | linha `ip address IP MÁSCARA` (Cisco/Huawei/ZTE, máscara decimal convertida pra CIDR contando bits) ou `ipv4 address IP/CIDR` (Datacom), dentro do bloco da interface |

- **Frontend:** `pl-ifa`/`pl-ifb` ganham um listener de `input` (`_sugerirIpPorInterface`) — ao
  digitar/selecionar um valor que bate **exatamente** com o nome de uma interface do backup
  (cache já populado pelo datalist), preenche `pl-ipl`/`pl-ipr` com o IP/CIDR encontrado e mostra
  um toast. Só altera o valor do `<input>` — como todo o resto do painel, a mudança só é
  persistida no link ao clicar em **Aplicar**.
- Reaproveita o mesmo cache (`_ifaceCache`) do datalist de interfaces — não dispara uma chamada
  de rede adicional.

---

### Waypoints — editar o caminho da linha

Os waypoints permitem dobrar conexões de forma livre:

| Ação | Como fazer |
|---|---|
| Adicionar ponto de dobra | Arrastar o **círculo vazio** (○) no centro de um segmento |
| Mover waypoint | Arrastar o **círculo preenchido** (●) azul |
| Remover waypoint | **Duplo-clique** no círculo preenchido |
| Limpar todos os waypoints | Botão "Limpar waypoints (N)" no painel de propriedades |

Os handles de waypoints ficam visíveis somente quando o link está selecionado.

---

## Remoção de Dispositivos

Selecionar um nó ou conexão e:
- Clicar no botão **"Remover"** no painel de propriedades (direita)
- Ou pressionar `Delete` / `Backspace`

A remoção de um nó também remove todas as conexões associadas a ele.
Suporta Undo (`Ctrl+Z`).

---

## Exportação PNG

O botão **PNG** na toolbar exporta a topologia atual como imagem PNG em resolução 2× (retina):

1. O SVG atual é serializado e convertido para `Blob`
2. Um `<canvas>` recebe o SVG renderizado via `drawImage`
3. O fundo é preenchido com `#0d1117` (tema escuro)
4. O arquivo `topologia.png` é gerado via `canvas.toBlob`

---

## URLs Backend

| Método | URL | Descrição |
|---|---|---|
| `GET` | `/clientes/<id>/topologia/` | Editor visual |
| `GET` | `/clientes/<id>/topologia/dados/` | Carrega JSON do diagrama salvo |
| `POST` | `/clientes/<id>/topologia/salvar/` | Salva diagrama (nome + dados_json) |
| `GET` | `/clientes/<id>/topologia/hosts/` | Lista hosts CRM com tipo mapeado |
| `GET` | `/clientes/acessos/<acesso_id>/interfaces-backup/` | Interfaces extraídas do backup mais recente do acesso (sugestão para Lado A/B) |

---

## Bugs Corrigidos — 2026-07-20

### Injeção de script via `dados_json \| safe` no carregamento do editor

**Arquivo:** `clientes/templates/topologia_editor.html`

**Causa:** O JSON salvo da topologia (`diagrama.dados_json`) era injetado direto no `<script>`
da página com o filtro `|safe`, sem nenhum escape para o contexto JS:

```html
window.TOPO_DADOS = {{ dados_json|safe }};
```

Qualquer texto livre salvo pelo usuário em campos do diagrama (ex: o conteúdo de um nó
"Texto/Legenda", ou o `label` de um nó/link) é gravado sem sanitização dentro desse JSON. Um
valor contendo `</script><script>...</script>` fecharia a tag `<script>` prematuramente e
executaria JS arbitrário para **qualquer usuário** (inclusive staff) que abrisse aquela
topologia depois — um XSS armazenado. O template irmão `topologia_drawio.html` já tratava o
mesmo tipo de dado corretamente com `escapejs`; só o editor SVG tinha o `|safe` sem proteção.

**Correção:** mesmo padrão já usado em `topologia_drawio.html`:

```html
window.TOPO_DADOS = JSON.parse("{{ dados_json|escapejs }}");
```

`escapejs` escapa `<`, `>`, aspas e barra invertida para sequências `\uXXXX`, então o HTML da
página nunca contém um `</script>` literal vindo de dado do usuário; `JSON.parse` reconstrói o
objeto original a partir da string escapada.

### Atalhos de teclado disparavam com foco em `<select>`

**Arquivo:** `static/js/topo_main.js` (`_bindEvents`)

**Causa:** O guard que desativa atalhos de teclado com o foco em um campo de formulário só
checava `INPUT`/`TEXTAREA`:

```js
if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
```

Os `<select>` do painel de propriedades (Interface/Velocidade, Traço, Forma da linha) não
entravam nesse guard. Com um desses dropdowns focado, teclas de navegação/type-ahead do próprio
`<select>` (ex. `Delete`/`Backspace`, ou `C`) também disparavam os atalhos globais —
apagando o nó/link selecionado ou alternando o modo de conexão sem intenção do usuário.

**Correção:** guard estendido para incluir `SELECT`:

```js
if (['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) return;
```

### Rótulo de "Interface Lado A/B" escondido atrás do node em links curtos

**Arquivo:** `static/js/topo_main.js` (`_renderLink`)

**Causa:** a posição dos rótulos de interface era calculada como **9%/91% do comprimento do
link** (`src.x + (tgt.x-src.x) * 0.09`). Isso funciona para links longos, mas o SVG desenha
`links-layer` **antes** de `nodes-layer` (nodes ficam por cima) — em links curtos entre nodes
grandes, 9% do comprimento cai dentro do raio visual do próprio node (~36px), e o texto do
rótulo era desenhado por baixo do ícone/caixa do node, ficando parcial ou totalmente encoberto.

**Correção (1ª parte):** a posição passou a ser uma **distância fixa em pixels a partir da borda
de cada node** (`node.w/2 + 14`), na direção do link, em vez de uma porcentagem do comprimento —
garantindo que o rótulo sempre apareça fora do node. Um `Math.min(..., linkLen * 0.4)` protege
links muito curtos para o rótulo não ultrapassar o meio da linha. O mesmo tratamento foi
aplicado aos rótulos de IP P2P (`ip_local`/`ip_remote`), que tinham o mesmo problema em potencial.

**Correção (2ª parte, 2026-07-20 — regressão vista em produção):** a 1ª parte só afastava o
**centro** do rótulo (`text-anchor="middle"`), sem considerar a própria largura do texto. Nomes
de interface longos (ex. `ten-gigabit-ethernet 1/1/5`, `Eth-Trunk10`) tinham metade do texto
ainda caindo em cima do node, mesmo com a distância fixa aplicada — o texto ficava cortado pela
borda/sombra do node em links horizontais. Corrigido calculando a largura do rótulo (`ifAW`/
`ifBW`/`ipLocalW`/`ipRemoteW`) **antes** da posição e somando `largura/2` à distância — assim a
borda do rótulo mais próxima do node (não o centro) é que respeita a distância mínima:

```js
const clearIfA = Math.min(raioA + ifAW/2 + 6, linkLen * 0.4);
```

---

## Passe de Design — 2026-07-20

Ajustes visuais em `clientes/templates/topologia_editor.html` (CSS) e `topo_main.js`/`topo_engine.js`
(pontos pontuais e aditivos — nenhum mudou o modelo de dados salvo em `dados_json` nem o
comportamento de nenhuma ação existente):

- **Toolbar:** sombra sutil, botão "Salvar" com destaque em gradiente (`.tb-btn.primary`), rótulo
  de marca ("Topologia") à esquerda, badge do nome do cliente em pílula à direita.
- **Paleta:** dispositivos agrupados por categoria (`Rede/Core`, `Acesso/FTTH`, `Servidores`,
  `Outros`, `Anotações`) via novo campo `group` em `TOPO_DEVICES` (`topo_engine.js`) — só
  metadado de exibição, não afeta `node.type` nem a importação de hosts. Itens da paleta com
  leve elevação/deslocamento ao passar o mouse.
- **Canvas:** leve gradiente radial de fundo (profundidade sem tirar contraste do grid), dica
  "Arraste um dispositivo..." quando o diagrama está vazio (`#canvas-hint`, controlada por
  `_updateStatus()` a partir de `this.nodes.length`).
- **Nodes:** sombra sutil (`drop-shadow`) por trás de cada ícone para dar profundidade, mais
  forte no hover; texto do nome/IP ganhou um fundo semi-transparente (`.node-label-bg`) atrás
  para legibilidade sobre os pontos do grid.
- **Links:** brilho leve no hover, sombra na cor do link quando selecionado.
- **Painel de propriedades:** título com linha divisória, transição suave ao trocar de
  seleção (`@keyframes props-fade`), campos com anel de foco (`box-shadow`) em vez de só borda,
  botão "Aplicar" com destaque em gradiente (`.prop-btn.primary` — a classe existe no CSS, mas
  os botões "Aplicar" não a usam por padrão para não mudar a hierarquia visual existente entre
  Aplicar/Remover; disponível para uso futuro).
- **Legenda de interfaces (novo):** botão "Legenda" na toolbar (`topo.toggleLegend()`) abre um
  painel flutuante no canto inferior esquerdo do canvas com a cor de cada velocidade de
  interface (`TOPO_IFACES`) — construído sob demanda na primeira vez que é aberto.

**Segunda leva (mesmo dia, pedido de mais acabamento):**

- **Grid "blueprint":** o pattern do fundo do canvas passou de só pontinhos a cada 20px para
  pontinhos + linhas discretas a cada 100px (5 células) — dá a sensação de grade técnica de
  ferramenta de diagrama profissional. Continua um único `<pattern>`/`<rect id="grid-bg">`, então
  `toggleGrid()` (JS) não precisou mudar: esconde as duas camadas de uma vez, como antes.
- **Sheen nos nodes:** gradiente branco bem sutil (`#node-gloss`, 12%→0% de opacidade) sobreposto
  na metade de cima do corpo do node — dá uma leve sensação de profundidade/vidro sem exigir
  gradiente por node (é um único `<linearGradient>` compartilhado nos `<defs>` do SVG).
- **Toolbar:** borda inferior trocada de linha sólida para `border-image` em gradiente (ciano →
  azul → transparente), reforçando a identidade visual sem pesar.

**Terceira leva (mesmo dia — ícones e efeitos nos links):**

- **Brilho nos ícones:** cada ícone de dispositivo (`.node-icon`) ganhou um `drop-shadow` sutil
  na própria cor do node, mais forte no hover e na seleção — o ícone "acende" em vez de ficar
  totalmente chapado.
- **Anel pulsante em nodes do CRM:** nodes importados do CRM (`node.acesso_id` preenchido) têm o
  anel de destaque (`.node-ring`) pulsando suavemente (`@keyframes pulse-ring`, 2.6s) mesmo sem
  seleção — dá uma pista visual de "equipamento real monitorado" vs. um node desenhado à mão.
  Ao selecionar o node, o `!important` da regra de seleção sempre vence a animação (mostra o
  anel sólido, como antes).
- **Fluxo animado nos links:** cada link ganhou uma segunda `<path>` sobreposta (`.link-flow`),
  com traços curtos que "correm" do Lado A pro Lado B (`@keyframes link-flow` anima
  `stroke-dashoffset`) — dá a sensação de tráfego passando pela conexão. É puramente decorativo:
  `pointer-events:none`, não interfere no clique (`.link-hit` continua sendo a área de detecção)
  nem é salvo no `dados_json`.
- **Pacotes de tráfego simulado (2026-07-20, reforço a pedido):** além do tracejado correndo,
  cada link ganhou 2 `<circle class="link-packet">` viajando ao longo do próprio path do link
  via `<animateMotion path="${d}">` (SVG anima o círculo seguindo exatamente a curva/reta do
  link, na direção em que ele foi desenhado: src→tgt, ou seja Lado A → Lado B) — o resultado lê
  muito mais como "tráfego" do que só o tracejado. A duração da volta é proporcional ao
  comprimento do link (`linkLen / 220`, entre 0.6s e 4s) para todo link parecer andar na mesma
  velocidade visual, em vez de um link curto parecer mais lento/rápido que um comprido com
  duração fixa. As 2 bolinhas têm início espaçado em meio ciclo (`begin="metade da duração"`)
  para nunca viajarem "coladas". Cor = cor da interface (`TOPO_IFACES`), com leve brilho
  (`drop-shadow`). Mesma regra de `effects-off`/`pointer-events:none` do item acima.
- **Botão "Efeitos" (novo, ligado por padrão):** `topo.toggleEffects()` adiciona/remove a classe
  `effects-off` no `<body>`, desligando o fluxo animado + pacotes dos links e o pulso dos nodes
  do CRM de uma vez — útil em topologias muito grandes onde a animação pode distrair ou pesar
  visualmente. É uma preferência de sessão (não é salva no diagrama).

**Quarta leva (mesmo dia — ícones de Roteador e Switch redesenhados, 2 iterações):**

Os ícones de `router`, `switch_l2` e `switch_l3` em `TOPO_ICONS` (`topo_engine.js`) foram
redesenhados para lembrar mais os símbolos padrão usados em diagramas de rede reais.

*1ª tentativa* — círculo com duas setas curvas opostas (estilo "exchange") pro roteador, e o
rack de portas original com setas de encaminhamento acima/abaixo pro switch. **Rejeitada** —
não bateu com a referência visual real de ferramentas de diagrama (AWS/Cisco Network Icons) nem
com a aparência de hardware físico.

*2ª tentativa (final)* — a partir de referências visuais concretas (ícones oficiais AWS Network
Diagram e Cisco Network Diagram para roteador; ilustração de switch/hardware físico):

- **Roteador:** círculo preenchido na cor do device com **4 setas retas apontando pra fora**
  (N/S/L/O, em branco) — o mesmo padrão visual do "VPC Router" da AWS e do "Router" da Cisco
  (ambos usam um círculo/cilindro com setas em cruz saindo do centro).
- **Switch L2/L3:** deixou de ter qualquer seta de encaminhamento — virou uma **caixa de
  hardware física**: porta uplink/SFP redonda à esquerda (jack escuro com centro branco) + 4
  portas RJ45 (formato trapezoidal, lembrando o conector de rede visto de frente) à direita,
  com o badge "L2"/"L3" abaixo da caixa.

Só o conteúdo de `TOPO_ICONS[...]` mudou (paths SVG) — nenhuma chave, cor (`TOPO_DEVICES`) ou
lógica de renderização (`_renderNode`) foi alterada, então o resto do editor (seleção, drag,
importação de hosts, salvar/carregar) continua igual.

**Quinta leva (mesmo dia — IP em negrito):** o texto do IP de gerência exibido abaixo do nome do
node (`_renderNode`, `font-family:'Courier New'`) ganhou `font-weight="700"`. A largura do fundo
atrás do texto (`.node-label-bg`) também foi ligeiramente aumentada (multiplicador de 6 para 6.4
por caractere) porque texto em negrito ocupa um pouco mais de espaço horizontal que o mesmo texto
normal — sem o ajuste, IPs mais longos ficariam com a última letra encostando na borda do fundo.

---

## Atalhos de Teclado

| Tecla | Ação |
|---|---|
| `Ctrl+S` | Salvar |
| `Ctrl+Z` | Desfazer |
| `Ctrl+Y` | Refazer |
| `C` | Alternar modo conexão |
| `Delete` / `Backspace` | Remover nó ou conexão selecionado |
| `Escape` | Cancelar conexão / Desselecionar |
| Scroll do mouse | Zoom |
| Duplo-clique em waypoint | Remover waypoint |
