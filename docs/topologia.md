# Topologia de Rede — Documentação Técnica

**Arquivos principais:**
- `clientes/templates/topologia_editor.html`
- `static/js/topo_engine.js`
- `static/js/topo_main.js`

**Atualizado em:** 2026-06-03

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

Versão atual: **v=10** (parâmetro de cache-busting no HTML).

---

## Tipos de Dispositivo (`DEVICES`)

Cada tipo tem `label`, `color` (hex) e `icon` (chave em `ICONS`).

| Tipo | Label | Cor | Ícone |
|---|---|---|---|
| `router` | Roteador | `#00d9ff` | Caixa com 3 círculos + antenas |
| `switch_l2` | Switch L2 | `#3fb950` | Rack com portas + label L2 |
| `switch_l3` | Switch L3 | `#58a6ff` | Rack com portas + label L3 |
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
| `iface_a` | string | Nome da interface no lado A (ex: `ge0/0/1`, `eth0`, `sfp1`) |
| `iface_b` | string | Nome da interface no lado B (ex: `ge0/0/2`, `eth1`, `sfp2`) |
| `style` | `solid`\|`dashed`\|`dotted` | Estilo do traço |
| `shape` | `straight`\|`curved`\|`wavy` | Forma da linha |
| `waypoints` | `[{x,y}, ...]` | Pontos intermediários que dobram a linha |

### Tipos de Interface (`IFACES`)

| Chave | Label | Cor |
|---|---|---|
| `100m` | 100 Mbps | cinza |
| `1g` | 1 Gbps | verde |
| `10g` | 10 Gbps | ciano |
| `40g` | 40 Gbps | azul |
| `100g` | 100 Gbps | roxo |
| `sfp` | SFP 1G | amarelo |
| `sfp+` | SFP+ 10G | laranja |
| `gpon` | GPON | salmão |
| `xpon` | XGS-PON | lilás |
| `wifi` | Wireless | amarelo |
| `mw` | Microwave | laranja |
| `other` | Outro | cinza |

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
