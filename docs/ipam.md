# IPAM — Documentação Técnica

**Arquivo principal:** `clientes/ipam_views.py`  
**Models:** `IPAMVlan`, `IPAMPrefixo`, `IPAMSubRede`, `IPAMEndereco`, `IPAMVpnDoc`  
**Sub-abas L2VPN/VLANs por Switch:** views em `clientes/views.py`, parser em `clientes/l2vpn_parser.py`  
**Atualizado em:** 18/08/2026

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

## Models Relacionados

| Model          | Campos principais                                              |
|----------------|----------------------------------------------------------------|
| `IPAMVlan`     | `cliente`, `numero` (1-4094), `nome`, `status`               |
| `IPAMPrefixo`  | `cliente`, `prefixo` (CIDR), `tipo`, `status`, `pool_cheia`  |
| `IPAMSubRede`  | `cliente`, `rede` (CIDR), `prefixo` (FK), `vlan` (FK), `status` |
| `IPAMEndereco` | `cliente`, `ip`, `subrede` (FK), `hostname`, `descricao`     |
| `IPAMVpnDoc`   | documentação de VPNs vinculada ao cliente                     |

---

## Sub-abas "L2VPN" e "VLANs por Switch" — novas em 18/08/2026

Duas sub-abas novas dentro de Documentação de Rede, **separadas** da sub-aba "VPNs" acima
(essa é `IPAMVpnDoc`, VPN de camada 3 — IPSec/GRE/L2TP/WireGuard, documentada à mão). As novas são
camada 2 (VSI/VPWS/VPLS/L2VC e inventário de VLAN), **extraídas do backup**, não digitadas.

### Reaproveitamento — nada duplicado

O parser e a resolução de peer→host já existiam prontos, implementados dias antes pra alimentar o
modal "Mostrar L2VPN" do editor de Topologia (ver [topologia_l2vpn.md](topologia_l2vpn.md) — fonte
de verdade completa do parser, sintaxes suportadas por fabricante, cache, etc.). As sub-abas aqui
são só uma **segunda superfície de UI** pro mesmo backend: uma listagem tabular por switch, fora
do canvas SVG do editor, sem precisar abrir o diagrama de topologia pra ver os serviços L2VPN de
um cliente.

### "Quais Acessos são switch?"

Não existe campo `Acesso.eh_switch` — é inferido por palavra-chave em `funcao.descricao`/`tipo`
(`clientes/views.py::_acesso_eh_switch`), mesmo critério (reduzido) que `topologia_hosts` usa pra
desenhar o ícone `switch_l2`/`switch_l3` no editor:

```python
def _acesso_eh_switch(acesso):
    funcao_nome = ((acesso.funcao.descricao or '') if acesso.funcao else '').lower()
    tipo_lower = (acesso.tipo or '').lower()
    palavras = ['switch l3', 'sw-l3', 'camada 3', 'switch', 'sw-', 'catalyst', 'nexus']
    return any(p in funcao_nome or p in tipo_lower for p in palavras)
```

### Endpoints

| Rota | Descrição |
|---|---|
| `GET /clientes/<cliente_id>/ipam/l2vpn/switches/` | Switches do cliente + resumo `{vpls, vpws, l2vc, total}` por switch (roda `_l2vpn_servicos_do_acesso` uma vez por switch — mesmo cache por `BackupLog.id` do editor de Topologia) |
| `GET /clientes/acessos/<acesso_id>/l2vpn-backup/` | Detalhe de um switch — **reaproveitado direto** do editor de Topologia, endpoint já existente, nenhuma view nova |
| `GET /clientes/<cliente_id>/ipam/vlans-switch/switches/` | Switches do cliente + contagem total de VLANs em cada um |
| `GET /clientes/acessos/<acesso_id>/vlans-backup/` | VLANs configuradas naquele switch (número, interface, descrição, IP) |

Ambas as listagens são gated por `@modulo_habilitado_required('acessos')` — mesma exigência do
endpoint de detalhe já existente (`l2vpn_backup_acesso`), pra não ter um caminho de permissão
diferente conforme a UI de entrada.

### Extrator de VLAN — `clientes/l2vpn_parser.py::extrair_vlans`

Novo, complementar ao parser de L2VPN — devolve **todas** as VLANs configuradas no equipamento
(não só as amarradas a um serviço L2VPN). Mesma filosofia do parser de L2VPN: só cobre sintaxe já
vista em backup real deste ambiente.

- **Huawei VRP** (confirmado com dado real): cada bloco `interface Vlanif<N>` vira uma VLAN, com
  `description` e `ip address` quando presentes. Testado num switch real com 24 interfaces
  amarradas a um único VSI de agregação PPPoE (`l2 binding vsi` — serviço legítimo, "junta 24
  VLANs de acesso diferentes numa única instância de switch virtual pro servidor PPPoE", não bug)
  — o inventário completo desse mesmo switch tem **102 VLANs**, a maioria fora de qualquer L2VPN.
- **MikroTik** (`/interface vlan` + `add vlan-id=... name=... interface=...`): melhor esforço, sem
  amostra real deste ambiente conferida ainda.
- Outros fabricantes: lista vazia (mesmo princípio de "não inventar parser sem amostra real" do
  L2VPN).

### UI — pastas, busca e paginação

As duas sub-abas usam o mesmo componente visual: cada switch é uma **pasta** (ícone
`fa-folder`/`fa-folder-open`, cor âmbar), clicar expande e mostra o conteúdo indentado com uma
guia pontilhada (CSS compartilhado: `.switch-folder-row`, `.switch-folder-contents`). Campo de
busca por nome/IP do switch, 20 switches por página com paginação — tudo client-side (a lista
inteira já vem numa única chamada, com os resumos já calculados).
