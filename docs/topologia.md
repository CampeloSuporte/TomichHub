# Topologia de Rede — Documentação Técnica

**Arquivos principais:**
- `clientes/templates/topologia_editor.html`
- `static/js/topo_engine.js`
- `static/js/topo_main.js`

**Atualizado em:** 2026-05-26

---

## Visão Geral

Editor visual de topologia de rede baseado em SVG, com suporte a:
- Drag & drop de dispositivos a partir da paleta lateral
- Conexões entre nós com drag a partir dos pontos de ancoragem
- Importação automática de hosts do CRM
- Exportação SVG, Undo/Redo, Grid, Snap
- Acesso direto aos hosts via terminal/browser

---

## Arquitetura dos Arquivos JS

| Arquivo | Responsabilidade |
|---|---|
| `topo_engine.js` | Definição de tipos (`DEVICES`), interfaces (`IFACES`) e paths SVG dos ícones (`ICONS`) |
| `topo_main.js` | Classe `TopoEditor` — lógica de renderização, eventos, persistência e importação |

Versão atual: **v=8** (parâmetro de cache-busting no HTML).

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

## Remoção de Dispositivos

Selecionar um nó ou conexão e:
- Clicar no botão **"Remover"** no painel de propriedades (direita)
- Ou pressionar `Delete` / `Backspace`

A remoção de um nó também remove todas as conexões associadas a ele.
Suporta Undo (`Ctrl+Z`).

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
| `Delete` / `Backspace` | Remover selecionado |
| `Escape` | Cancelar conexão / Desselecionar |
| Scroll do mouse | Zoom |
