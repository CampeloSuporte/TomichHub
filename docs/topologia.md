# Topologia de Rede — Documentação Técnica

**Arquivos principais:**
- `clientes/templates/topologia_editor.html`
- `static/js/topo_engine.js`
- `static/js/topo_main.js`

**Atualizado em:** 2026-08-14

---

## Visão Geral

Editor visual de topologia de rede baseado em SVG, com suporte a:
- Drag & drop de dispositivos a partir da paleta lateral
- Conexões entre nós com drag a partir dos pontos de ancoragem
- Importação automática de hosts do CRM
- Exportação PNG (via canvas 2×), Undo/Redo, Grid, Snap
- Acesso direto aos hosts via terminal/browser
- Waypoints: dobrar conexões arrastando pontos intermediários
- Seleção múltipla por laço de área (ou Shift+clique) e movimentação de vários dispositivos
  em grupo, preservando a posição relativa entre eles
- Documentação de serviços L2VPN (VSI/VPLS, VPWS e L2VC) lida do backup de cada host,
  com o peer de cada túnel ligado ao host do outro lado, e clonagem de serviço aplicada
  no equipamento — ver [topologia_l2vpn.md](topologia_l2vpn.md)
- Diagnóstico e operação das portas PON de OLT Huawei (placas e ONTs lidas do backup,
  `display port info/state` e `laser-switch` ao vivo) — ver [olt_pon.md](olt_pon.md)

---

## Arquitetura dos Arquivos JS

| Arquivo | Responsabilidade |
|---|---|
| `topo_engine.js` | Definição de tipos (`DEVICES`), interfaces (`IFACES`) e paths SVG dos ícones (`ICONS`) |
| `topo_main.js` | Classe `TopoEditor` — lógica de renderização, eventos, persistência e importação |

Versão atual: **topo_engine v=24 / topo_main v=35** (parâmetro de cache-busting no HTML).

**Estes dois JS ficam em `static/` e mesmo assim são versionados.** `static/` é o
`STATIC_ROOT` (destino do `collectstatic`) e está no `.gitignore`, mas esses dois
arquivos são fonte escrita à mão que existe **só ali** — não vêm de `staticfiles/`
nem de app nenhum, e o nginx serve o diretório direto (`alias /opt/crm/static/`).
Ficaram fora do versionamento de 2026-03 até 2026-08-13 (saíram junto quando
`static/` virou ignorado por inteiro): o editor rodava em produção com o front
inteiro fora do git. Hoje o `.gitignore` ignora `static/` **por conteúdo** e
reinclui arquivo a arquivo:

```gitignore
static/*
!static/js/
static/js/*
!static/js/topo_engine.js
!static/js/topo_main.js
```

Ao criar um JS novo do editor, acrescente a linha `!static/js/<arquivo>` — sem
ela o arquivo nasce invisível pro git e some no próximo clone.

> **Cuidado com `collectstatic` neste ambiente:** `staticfiles/js/` e
> `static/js/` estão fora de sincronia nos dois sentidos (o `main.js` servido é
> mais novo que a fonte; o `cadastrar_cliente.js` é mais velho). Rodar
> `collectstatic` hoje sobrescreveria o `main.js` de produção por uma versão de
> 2025. Os arquivos da topologia não correm esse risco — não existem em
> `staticfiles/`, então o collectstatic não os toca.

---

## Tipos de Dispositivo (`DEVICES`)

Cada tipo tem `label`, `color` (hex) e `icon` (chave em `ICONS`).

| Tipo | Label | Grupo | Cor | Ícone |
|---|---|---|---|---|
| `router` | Roteador | Rede / Core | `#00d9ff` | Cilindro (linguagem Cisco) com duas setas de fluxo contrário na face de cima |
| `switch_l3` | Switch L3 | Rede / Core | `#58a6ff` | Chassi 1U com 4 portas + pill "L3" e o par de setas de roteamento acima |
| `switch_l2` | Switch L2 | Rede / Core | `#3fb950` | Mesmo chassi 1U com 4 portas + pill "L2" (sem as setas) |
| `firewall` | Firewall | Rede / Core | `#f85149` | Parede de tijolos com escudo em contorno branco |
| `cgnat` | CGNAT | Rede / Core | `#ff6b35` | 3 entradas convergindo → caixa "NAT" → 1 saída |
| `dwdm` | DWDM | Rede / Core | `#bc8cff` | Chassi com prisma separando o feixe em 3 comprimentos de onda (cores reais) |
| `olt` | OLT | Acesso / FTTH | `#e3b341` | Chassi com 2 placas de linha + leque PON de 3 saídas |
| `splitter` | Splitter | Acesso / FTTH | `#2dd4bf` | Caixa passiva: 1 fibra entra, 5 saem |
| `onu` | ONU/ONT | Acesso / FTTH | `#63e6be` | Caixinha do assinante com rabicho de fibra e porta LAN |
| `cpe` | CPE | Acesso / FTTH | `#d2a8ff` | Gateway com 2 antenas, LEDs frontais e leque Wi-Fi |
| `radio` | Rádio PTP | Wireless | `#ffa657` | Parabólica de perfil no mastro + frentes de onda |
| `ap` | Access Point | Wireless | `#ffd166` | Disco de teto + cone de cobertura |
| `internet` | Internet/WAN | Trânsito / Peering | `#38bdf8` | Globo com meridianos |
| `ix` | IX / PTT | Trânsito / Peering | `#f778ba` | Hexágono "IX" com 6 participantes conectados |
| `cloud` | Cloud/ISP | Trânsito / Peering | `#8b98a5` | Nuvem com malha interna |
| `server` | Servidor | Servidores | `#8b949e` | Rack 3U com baias e LEDs |
| `vm` | VM | Servidores | `#a78bfa` | Três caixas empilhadas + "VM" na da frente |
| `host` | Host/PC | Servidores | `#79c0ff` | Monitor com prompt na tela |
| `text_box` | Texto/Legenda | Anotações | `#e3b341` | Cartão pontilhado com linhas de texto |

**Linguagem visual do set (redesign 2026-08-13):** todo ícone é desenhado num viewBox 48×48
ocupando a área ótica `x:3..45 / y:6..42`, para nenhum device pesar mais que o outro na paleta.
`currentColor` é a cor do device; recessos usam `#04121a` com opacidade e os detalhes (portas,
LEDs) usam `#fff` em três níveis (.9/.6/.35) — é o que dá profundidade sem filtro nenhum.
Nada de `url(#...)` dentro dos ícones: o mesmo markup é injetado no SVG do canvas **e** em
`<svg>` soltos da paleta, onde os `<defs>` do template não existem. Detalhe importante de
legibilidade: cada ícone precisa sobreviver a **22px** (tile da paleta), então o limite prático
é ~5 formas grandes — as primeiras versões com régua de portinhas e setas de fluxo viravam
ruído nesse tamanho.

---

## Mapeamento Automático Função → Tipo (importação do CRM)

Ao importar hosts via `GET /clientes/<id>/topologia/hosts/`, o backend
(`clientes/views.py → topologia_hosts`) mapeia o campo `funcao.descricao`
e `acesso.tipo` (ambos lowercased) para o tipo de dispositivo:

| Keywords no nome da função/tipo | Tipo resultante |
|---|---|
| `cgnat`, `cg-nat`, `carrier grade nat` | `cgnat` |
| `ix.br`, `ixbr`, `ix br`, `ix-`, `ptt `, `ptt-`, `ptt.`, `peering` | `ix` |
| `transito`, `trânsito`, `upstream`, `internet`, `wan-`, `wan ` | `internet` |
| `bras`, `bng`, `broadband network` | `router` |
| `router`, `roteador`, `core`, `border`, `borda` | `router` |
| `switch l3`, `sw-l3`, `camada 3` | `switch_l3` |
| `switch`, `sw-`, `catalyst`, `nexus` | `switch_l2` |
| `access point`, `acess point`, `ponto de acesso`, `unifi`, `ap-`, `ap_` | `ap` |
| `radio`, `rádio`, `wireless`, `ubiquiti`, `mikrotik`, `ap `, `airmax`, `ltu` | `radio` |
| `dwdm`, `oadm`, `ots`, `mstp`, `transponder` | `dwdm` |
| `splitter`, `divisor optico`, `divisor óptico` | `splitter` |
| `olt`, `gpon`, `xgs`, `epon` | `olt` |
| `onu`, `ont` | `onu` |
| `server`, `servidor`, `zabbix`, `grafana`, `proxmox` | `server` |
| `firewall`, `utm`, `fortigate`, `pfsense`, `sophos` | `firewall` |
| `vm`, `virtual machine`, `virtualizado`, `kvm`, `qemu`, `vmware`, `vps` | `vm` |
| `cpe`, `modem` | `cpe` |
| *(padrão)* | `host` |

A ordem da lista é significativa: `ix`/`internet` vêm **antes** de `router`/`switch` (um host
chamado "Router IX.br" é, no desenho, o ponto de troca) e `ap` vem antes de `radio`. Palavras
curtas (`ix`, `ptt`, `wan`) só entram com separador — sem isso casariam com "matrix", "unix"
e qualquer nome que contenha as letras.

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

## Seleção Múltipla e Movimentação em Grupo — Adicionado em 2026-07-31

Permite selecionar vários dispositivos de uma vez e arrastá-los juntos, preservando a
posição relativa entre eles.

**Como selecionar um grupo:**

| Ação | Efeito |
|---|---|
| Botão **"Área"** na toolbar (`topo.toggleAreaSelect()`) | Ativa o modo seleção — arrastar na área vazia do canvas desenha um laço em vez de fazer pan |
| Segurar **Shift** e arrastar na área vazia | Mesmo laço de seleção, sem precisar ativar o modo (funciona a qualquer momento) |
| **Shift+clique** num dispositivo | Adiciona/remove esse dispositivo da seleção atual, um de cada vez |

O laço de seleção (`_finishRubberBand`) captura todo node cujo **centro** (`x,y`) caia
dentro do retângulo desenhado — não é preciso envolver o node inteiro. Um laço que captura
só 1 node vira uma seleção normal (abre o painel de propriedades); 2 ou mais viram um grupo.

**Movendo o grupo:** com 2+ nodes selecionados, clicar (sem Shift) e arrastar qualquer um
deles que já esteja no grupo move todos juntos. O deslocamento do mouse é aplicado a partir
da posição inicial de cada node (`groupDragging.positions`), com snap ao grid aplicado ao
delta como um todo — assim a formação do grupo não "desalinha" mesmo com o snap ativo.

**Deletar em grupo:** `Delete`/`Backspace` com 2+ nodes selecionados remove todos os nodes
do grupo e qualquer link conectado a algum deles (mesma regra de remoção em cascata de um
node único).

**Visual:** nodes na seleção múltipla ganham a classe `.multi-selected` (brilho ciano +
anel tracejado, ver CSS em `topologia_editor.html`), a mesma familia visual do `.selected`
de um node único, mas distinguível dele (o `.selected` não some quando o painel de
propriedades de um node individual está aberto simultaneamente a um grupo, porque as duas
seleções — `this.selected` e `this.selectedNodes` — são independentes; clicar um node novo
sem Shift limpa o grupo).

Implementação: `static/js/topo_main.js` — `this.selectedNodes` (Set de ids), `this.rubberBand`
(retângulo em andamento), `this.groupDragging` (arrasto em grupo em andamento),
`this.areaSelectMode` (toggle do botão "Área"). Estado transitório de UI, não é salvo no
`dados_json` do diagrama.

---

## Conexões (Links)

### Propriedades de uma conexão

| Campo | Tipo | Descrição |
|---|---|---|
| `iface` | string | Velocidade/tipo de interface (chave em `IFACES`) |
| `label` | string | Rótulo livre exibido no meio da linha |
| `ip_local` | string | IP P2P lado A (ex: `10.0.0.1/30`) |
| `ip_remote` | string | IP P2P lado B (ex: `10.0.0.2/30`) |
| `vlan` | string | VLAN ID — exibida como `VLAN 100` em linha própria, abaixo do label de velocidade |
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

### Rótulo do Link (banda + VLAN) e Cor do IP P2P — Ajustado em 2026-07-31

**Rótulo do meio do link (`_renderLink`, `topo_main.js`):** antes a VLAN aparecia grudada na
mesma linha da banda (`"100 Gbps V100"`), o que ficava confuso e cortava em links curtos. Agora
cada informação tem sua própria linha, sempre nesta ordem: nome do link (se houver, em negrito
na cor da interface) → banda → `VLAN <id>` (só aparece quando o link tem VLAN configurada). A
caixa de fundo (`<rect>`) cresce dinamicamente com a quantidade de linhas (`lblLines.length`) em
vez de assumir só 1 ou 2 linhas como antes.

**Cor do IP P2P (`.link-ip`, CSS em `topologia_editor.html`):** o texto dos rótulos de IP Local/
Remoto ao lado dos nodes usava `fill:#8b949e` (cinza apagado, baixo contraste sobre o fundo
escuro do rótulo). Trocado para branco (`#ffffff`) em negrito (`font-weight:700`) para ficar
legível à distância no canvas.

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

### Datalist de IP Local/Remoto a partir do Backup — Adicionado em 2026-07-31

Os campos **IP Local (P2P)** e **IP Remoto (P2P)** ganharam a mesma lógica de sugestão via
`<datalist>` já usada nos campos **Interface Lado A/B** (`_populateIfaceDatalist`), em vez de
serem inputs de texto puro como antes:

- `pl-ipl` (IP Local) lista `datalist id="dl-ipl"`, ligada ao **nó de origem** do link
  (`link.src`) — mesmo lado A usado pela interface.
- `pl-ipr` (IP Remoto) lista `datalist id="dl-ipr"`, ligada ao **nó de destino** (`link.tgt`)
  — mesmo lado B.
- A nova função `_populateIpDatalist(datalistId, acessoId, gen)` reaproveita **exatamente a
  mesma busca** (`_fetchInterfaces`/`_ifaceCache`) usada pelos campos de interface — nenhuma
  chamada de rede adicional é feita. A diferença é o filtro e o valor do `<option>`: só
  interfaces com `item.ip` preenchido entram na lista (a maioria das portas L2/trunk não tem
  endereço e não faria sentido aparecer aqui), e o **valor da opção é o IP/CIDR** (não o nome
  da interface) — a legenda (`nome — descricao`) ajuda a identificar de qual interface veio
  aquele IP.
- Mesmo padrão de robustez dos demais datalists: guardado pelo contador de geração
  (`_propsGen`) contra respostas que chegam depois de trocar a seleção, e continua sendo um
  campo 100% texto livre — o `<datalist>` só sugere, nunca restringe o valor digitado.
- Continua existindo o preenchimento automático (`_sugerirIpPorInterface`, seção acima): ao
  digitar/escolher uma interface em Lado A/B que bate com o nome exato de uma interface do
  backup, o campo de IP correspondente ainda é preenchido sozinho se estiver vazio. O novo
  datalist é um caminho **adicional** para chegar no mesmo IP — direto pelo campo de IP, sem
  precisar passar pelo campo de interface primeiro (útil quando o usuário já sabe o IP mas não
  o nome da porta, ou quando quer só conferir a sugestão sem alterar a interface preenchida).

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
| `GET` | `/clientes/acessos/<acesso_id>/interfaces-backup/` | Interfaces extraídas do backup mais recente do acesso (sugestão para Lado A/B e para os combos de interface do painel de clonagem L2VPN; cada item traz `logica`/`subinterface` para filtrar só as físicas) |
| `GET` | `/clientes/acessos/<acesso_id>/l2vpn-backup/` | Serviços L2VPN (VSI/VPLS/VPWS/L2VC) do backup mais recente, com peers resolvidos para hosts — ver [topologia_l2vpn.md](topologia_l2vpn.md) |
| `GET` | `/clientes/acessos/<acesso_id>/l2vpn-peers/` | Candidatos a peer de um túnel (hosts do cliente com identidade MPLS) para a busca por nome/IP no painel de clonagem |
| `POST` | `/clientes/acessos/<acesso_id>/l2vpn-clonar/` | Gera (preview) ou aplica no equipamento a config de um serviço L2VPN clonado — backoffice, audita em `AcaoL2vpn` |

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

## Incidente — Campos de Link (Interface/IP P2P) Zerados — 2026-07-31

**Sintoma relatado:** no diagrama do cliente 41 (Sinop), o link entre **SW DT ENLACE SINOP**
(`acesso_id=1027`) e **SWITCH HU SINOP** (`acesso_id=1055`) apareceu com `iface_a`, `iface_b`,
`ip_local` e `ip_remote` vazios no editor, embora uma captura de tela anterior mostrasse os
quatro campos preenchidos (`hundred-gigabit-ethernet 1/1/1` / `100GE1/0/3` /
`198.18.103.54/30` / `198.18.103.53/30`).

**Investigação:** os quatro campos vêm de `dados_json` (`TopologiaDiagrama`, não têm modelo
próprio — ver seção "Conexões (Links)" acima). Consulta direta ao banco (`manage.py shell`)
confirmou que os campos estavam de fato gravados como string vazia no `dados_json` salvo — não
era um problema de renderização do editor. Verificação em paralelo confirmou que:

- O JS publicado em `static/js/topo_main.js` batia byte a byte com o que estava em disco (sem
  problema de deploy/cache).
- `_extrair_interfaces_backup` continuava extraindo corretamente os dois lados a partir dos
  backups mais recentes de cada acesso — reconstruindo exatamente os mesmos valores da captura
  de tela original (porta física em cada equipamento + IP da interface lógica correspondente,
  já que tanto o Huawei quanto o Datacom roteiam a VLAN numa interface separada da porta física).

**Causa raiz não confirmada.** Não foi possível reproduzir em navegador (sem acesso a login na
sessão que investigou). A hipótese mais provável é uma ação de "Aplicar" no painel de
propriedades do link com os campos aparentando vazios — o pré-preenchimento
(`value="${link.ip_local||''}"` em `_showLinkProps`) está correto no código atual, então, se o
problema se repetir, vale investigar se o navegador está de fato exibindo o `<datalist>` ao
focar um campo já preenchido (alguns navegadores só mostram sugestões quando o valor digitado
ainda não bate exatamente com uma opção).

**Correção aplicada:** os 4 campos foram restaurados por escrita direta no `dados_json` desse
link específico (`manage.py shell`), usando os valores reconstruídos a partir do backup de cada
equipamento (mesma fonte de dados que o próprio recurso de sugestão usa). O `dados_json` anterior
à correção foi salvo em `/tmp/topologia_cliente41_backup_<timestamp>.json` antes da escrita, para
rollback caso necessário.

**Follow-up em aberto:** se o mesmo sumiço acontecer de novo em outro link/cliente, reproduzir no
navegador com o console aberto (Network + Console) para capturar se `/interfaces-backup/` retorna
vazio, se o `<datalist>` chega a ser populado, e se "Aplicar" está sendo clicado com o campo
realmente vazio na tela.

---

## Portas PON de OLT Huawei — 2026-08-14

O painel de propriedades de um host **OLT** ganhou o botão **"Portas PON"**:
inventário de placas/portas lido do backup (com as ONTs de cada porta) e, sobre
ele, `display port info/state` e o `laser-switch` ao vivo no equipamento.

Documentado em **[olt_pon.md](olt_pon.md)** — inclui a tabela de portas por
família de placa, o preâmbulo de comandos (`undo interactive`/`scroll`), por que
a execução usa shell Paramiko em vez de Netmiko e como o número de ONTs
afetadas acompanha um `laser-switch off`.

---

## Repaginação Visual — 2026-08-13

Redesenho do set de ícones e da interface inteira do editor. Nada aqui muda o modelo de dados:
`dados_json` continua com os mesmos campos, e topologias salvas antes disso abrem iguais — só
com o desenho novo. Arquivos: `topo_engine.js` (ícones), `topo_main.js` (render de node/link e
paleta) e o `<style>` de `topologia_editor.html`.

### Ícones

- **Set inteiro redesenhado** numa linguagem só (ver "Tipos de Dispositivo" acima): mesma área
  ótica, mesma escala de opacidade, mesma espessura de traço. O critério de aceitação foi o
  ícone continuar legível no tile de **22px** da paleta, não só nos 64px do node.
- **5 tipos novos, todos de rede:** `internet` (WAN/globo), `ix` (IX.br/PTT), `splitter`
  (splitter óptico da planta FTTH), `ap` (access point) e o `switch_l3` agora separado do L2
  pelas setas de roteamento. A paleta ganhou os grupos **Wireless** e **Trânsito / Peering**.
- Os tipos antigos continuam todos existindo com a mesma chave — nenhum node salvo perde ícone.

### Node no canvas

- Chassi "de vidro": fundo na cor do device a ~11%, borda a ~35%, brilho no topo
  (`#node-gloss`) e sombra interna na base (`#node-shade`, novo `<defs>`). O contorno cheio
  de antes competia com o ícone e deixava a tela pesada com 20+ nodes.
- Âncoras de conexão viraram "portas" vazadas (fundo do canvas + anel na cor), menores
  (r=5.5) e com `scale(1.35)` no hover.
- Host vindo do CRM (`node.acesso_id`) ganhou um **LED verde** no canto do chassi, além do anel
  pulsante que já existia.
- IP embaixo do nome agora sai na cor do device (antes cinza) — amarra rótulo e node.
- Pastilhas dos links (IP P2P, interface, banda/VLAN) unificadas: fundo escuro, canto
  arredondado e borda fina na cor da interface; a do meio passou a ter largura calculada pelo
  texto mais longo (era fixa em 56/64px e estourava com "VLAN 3100").

### Painéis flutuantes (paleta e propriedades)

Os dois painéis laterais deixaram de ser colunas do flex e viraram **cartões flutuantes sobre o
canvas** (`.panel-card`), que agora ocupa a largura toda. Entram e saem com `transform`, sem
re-layout do SVG — o desenho não "pula" quando um painel abre.

| Painel | Estado inicial | Como abre | Como fecha |
|---|---|---|---|
| Dispositivos (`#palette`) | **fechado** (`body.pal-off`, já no HTML) | botão `#btn-palette` na borda superior esquerda → `topo.togglePalette()` | X no cabeçalho (mesmo toggle) |
| Propriedades (`#props`) | **fechado** (`body.props-off`) | sozinho, ao selecionar node ou link (`_select`) | ao perder a seleção (`_deselect`, Esc, clique no vazio) ou no X (`topo.fecharProps()`, que também limpa a seleção) |

As classes nascem no `<body>` do template, não via JS: setadas depois, os painéis apareceriam
abertos no primeiro paint e "fugiriam" da tela quando o script rodasse. `togglePalette()` põe o
foco na busca ao abrir (depois da transição). O botão da borda some enquanto o painel está
aberto — os dois ocupam o mesmo canto.

Para não ficar nada escondido atrás dos cartões, a legenda de interfaces desloca para
`left:236px` com a paleta aberta e o controle de zoom para `right:292px` com as propriedades
abertas (regras `body:not(.pal-off) #legend-panel` / `body:not(.props-off) #zoom-ctl`).

### Interface

- **Toolbar** em clusters segmentados (`.tb-group`): exportar/desfazer/refazer, os toggles de
  modo (Grid/Snap/Conectar/Área) e legenda/efeitos viram blocos únicos com divisória interna —
  14 botões soltos em fila viravam uma parede de ícones. Marca com tile em gradiente.
- **Tooltip próprio** (`[data-tip]`) no lugar do `title` nativo: 450ms de atraso, saída
  imediata, ancorado no botão. Variantes `.tip-top` / `.tip-left`.
- **Busca na paleta** (`#pal-search` → `_filtrarPaleta`): filtra por rótulo, tipo interno e
  grupo, sem acento (`_semAcento`), escondendo o título de grupo que ficou vazio.
- **Controle de zoom flutuante** no canto inferior direito do canvas (− / % / + / ajustar), no
  lugar dos três botões de lupa na toolbar; clicar no "%" também ajusta à tela.
- **Painel de propriedades** com cabeçalho mostrando o próprio ícone do device selecionado
  (`.prop-hero`), rótulos em caixa alta e "Aplicar" como botão primário.
  *Bug de arrasto corrigido junto:* o `text-align:center` do estado vazio vinha inline no
  `#props-body` e, como o JS só troca o `innerHTML`, ficava valendo para os formulários — todo
  campo do painel aparecia centralizado. Agora o estilo vive em `.prop-empty`.
- **Barra de status** com pontos coloridos e números tabulares; o botão Salvar mostra um ponto
  (`.tb-btn.primary.dirty`) enquanto houver alteração não salva — o aviso do rodapé passava
  despercebido.
- `prefers-reduced-motion` desliga fluxo dos links, pulso dos nodes e deslocamentos.

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

**Sexta leva (2026-07-31 — micro-interações e correção de animações "mortas"):** revisão de
todas as transições/animações do editor com foco em consistência e em duas animações que
existiam no CSS mas nunca chegavam a rodar de fato:

- **Fade do painel de propriedades nunca retocava:** `#props-body{animation:props-fade...}` só
  disparava uma vez, no carregamento da página — trocar de seleção substitui o `innerHTML` do
  mesmo elemento, o que não reinicia uma CSS `animation` já concluída. Corrigido com um
  `MutationObserver` no construtor do `TopoEditor` (`topo_main.js`) que força um reflow
  (`style.animation='none'` → `offsetWidth` → `style.animation=''`) toda vez que o conteúdo do
  painel muda — sem precisar tocar nos 4 pontos do código que escrevem em `#props-body`.
- **Dica de canvas vazio e legenda de interfaces trocavam com `display:none`/`block`:** isso
  impede qualquer transição (não dá pra animar de/para `display:none`). Ambas passaram a usar
  `opacity`/`transform` com uma classe `.show`, então a `transition` que já existia no CSS (da
  dica) ou uma nova (da legenda, que ganhou um leve `scale(.95)→scale(1)` com
  `transform-origin:bottom left`, coerente com a posição fixa do painel no canto inferior
  esquerdo) finalmente tem efeito.
- **`transition:all` no botão "Aplicar"/"Remover" (`.prop-btn`)** trocado por propriedades
  explícitas (`background,border-color,color,transform`) — mais previsível e mais barato pro
  navegador que recalcular todas as propriedades a cada troca de estado.
- **Feedback de clique nos botões:** `.tb-btn:active`/`.prop-btn:active` ganharam
  `scale(.97)` além do `translateY` que já existia, para os botões "responderem" visualmente ao
  clique (não só ao hover).
- **Curva de easing consistente:** nova variável `--ease-out:cubic-bezier(.23,1,.32,1)` usada no
  toast, na legenda, na dica de canvas vazio e no fade do painel de propriedades — essas quatro
  animações de entrada/saída agora compartilham a mesma "personalidade" de movimento em vez de
  cada uma usar o `ease`/`ease-out` padrão do navegador.
- **Anel do node (`.node-ring`) ganhou transição de cor** (`transition:...,stroke .15s`), não só
  de opacidade — a troca de cor entre anel normal (cor do device) e anel de multi-seleção (ciano
  forçado por `!important`) deixou de "saltar" instantaneamente.
- **`:focus-visible` nos controles** (`tb-btn`, `prop-btn`, `prop-input`, `prop-select`) — anel
  ciano consistente com o resto da UI ao navegar por teclado (Tab), em vez do contorno azul
  padrão do navegador que destoava do tema escuro.
- **Efeitos respeitam `prefers-reduced-motion`:** se o usuário tem "reduzir movimento" ativado no
  SO/navegador, o editor já inicia com pulso/fluxo/pacotes desligados (mesmo estado do botão
  "Efeitos" manual), em vez de exigir que a pessoa descubra e desligue na mão.

Nenhuma dessas mudanças altera o modelo de dados salvo em `dados_json` nem o comportamento
funcional de nenhuma ação — é puramente polish visual/tátil.

---

## Atalhos de Teclado

| Tecla | Ação |
|---|---|
| `Ctrl+S` | Salvar |
| `Ctrl+Z` | Desfazer |
| `Ctrl+Y` | Refazer |
| `C` | Alternar modo conexão |
| `Delete` / `Backspace` | Remover nó(s) ou conexão selecionado(s) — inclui grupo em multi-seleção |
| `Escape` | Cancelar conexão / Desselecionar (inclui limpar multi-seleção) |
| `Escape` | Com o modal de L2VPN aberto: fecha o modal (os atalhos do canvas ficam suspensos enquanto ele está aberto) |
| Scroll do mouse | Zoom |
| Duplo-clique em waypoint | Remover waypoint |
| `Shift` + arrastar (área vazia) | Laço de seleção em área — seleciona vários dispositivos |
| `Shift` + clique num dispositivo | Adiciona/remove esse dispositivo da multi-seleção |

---

## Fluxo de Tráfego nos Links — Corrigido em 2026-08-14

O fluxo animado descrito acima ("tracinhos que correm do Lado A pro Lado B") **existia no código
desde 2026-07-20 e nunca foi visível.** A `<path class="link-flow">` era renderizada com
`stroke="${color}"` — a mesma cor da `<path class="link">` sólida desenhada logo abaixo dela.
Um tracejado da cor exata da linha que está por baixo não aparece: o que se via na tela era só a
linha sólida, mais as 2 bolinhas de `.link-packet`. Por isso o efeito era pedido de novo como se
nunca tivesse sido feito.

**Como ficou:**

| Camada (ordem de desenho) | Papel |
|---|---|
| `.link` | Linha sólida, cor e espessura da interface — carrega a semântica (banda, estilo, seleção) |
| `.link-glow` | Halo desfocado (`blur(2.5px)`, `opacity:.22`) na cor do enlace, acompanhando o dash — dá "corpo" ao tráfego sem engordar a linha sólida |
| `.link-flow` | O tracejado que corre. Traço **claro** (`#eaf6ff`) com `mix-blend-mode:screen`, para acender sobre a linha colorida em vez de sumir nela |
| `.link-packet` | As 2 bolinhas de `<animateMotion>`, agora com halo na cor do link + núcleo branco |

O `drop-shadow` do `.link-flow` usa `currentColor`, e o JS passa `style="color:${color}"` nas duas
paths novas — assim o brilho continua sendo da cor do enlace mesmo com o traço branco. A cor do
traço vem do CSS (não do atributo `stroke`), porque regra CSS vence atributo de apresentação.

**`prefers-reduced-motion` agora usa `display:none`, não `animation:none`.** Com o traço claro,
congelar a animação deixaria um tracejado branco **fixo** por cima de todo link — inclusive dos
configurados como sólidos, escondendo o `stroke-dasharray` de estilo (sólido/tracejado/pontilhado).
O mesmo já valia para `body.effects-off`, que sempre usou `display:none`.

Cache-busting: `topo_main.js?v=35` em [`topologia_editor.html`](../clientes/templates/topologia_editor.html)
— ver [`docs/../CHANGELOG.md`] e a nota sobre versionamento de `static/` antes de rodar `collectstatic`.
