# L2VPN na Topologia — VSI / VPLS / VPWS / L2VC

**Arquivos principais:**
- `clientes/l2vpn_parser.py` — parser dos serviços L2VPN e dos IPs de identidade
- `clientes/l2vpn_actions.py` — geração e execução da config de um serviço clonado
- `clientes/views.py` — `l2vpn_backup_acesso`, `l2vpn_clonar_acesso` e helpers `_l2vpn_*`
- `static/js/topo_main.js` — modal "Mostrar L2VPN" e painel de clonagem (`abrirL2vpn`, `clonarL2vpn`)
- `clientes/templates/topologia_editor.html` — CSS do modal
- Model `AcaoL2vpn` (migração `0108_acao_l2vpn`) — auditoria das aplicações

**Atualizado em:** 2026-08-13

---

## Visão Geral

Todo host da topologia que tem backup coletado ganha, no painel de propriedades,
o botão **Mostrar L2VPN**. Ele abre um modal que documenta os serviços de
camada 2 configurados naquele equipamento — VSI/VPLS, VPWS e L2VC/pseudowire —
lidos direto do backup mais recente, com:

- **id** do serviço (`vsi-id`, `pw-id`, `vc-id`) e **nome** como está na config;
- peers/neighbors do túnel, **já resolvidos para o host do outro lado**;
- interfaces de acesso e VLAN (`dot1q`) de cada serviço;
- MTU, sinalização (LDP/BGP/static), `pw-type` e o trecho cru da config.

Clicar num peer identificado leva direto ao host correspondente no diagrama
(centraliza, seleciona e pisca o nó). Se o host existe no CRM mas ainda não foi
colocado no diagrama, o modal abre a documentação L2VPN dele.

Cada serviço também pode ser **clonado**: o botão "Clonar" abre um painel com a
config de origem já preenchida, o operador ajusta o que muda (nome, id, VLAN,
peer, interface), revisa os comandos gerados e manda aplicar — o CRM conecta no
equipamento e cria o serviço. Ver "Clonar um serviço" mais abaixo.

Nada disso é digitado à mão nem fica guardado em tabela: é sempre derivado do
backup, então acompanha a config real do equipamento.

---

## Como o peer vira um host ("dar match")

O `peer 198.18.255.2` de um VSI nunca é o IP de gerência do equipamento — é o
loopback/LSR-ID dele. Por isso a resolução usa um **mapa de identidade**:
para cada acesso do cliente, `extrair_ips_identidade()` lê do backup os IPs
pelos quais aquele equipamento é conhecido pelos vizinhos de MPLS:

| Fonte | Exemplo |
|---|---|
| `mpls lsr-id` (Huawei) | `mpls lsr-id 10.1.1.1` |
| `lsr-id=` / `transport-addresses=` (MikroTik) | `/mpls ldp add lsr-id=10.255.0.50 …` |
| `mpls ldp router-id` (Cisco) | `mpls ldp router-id Loopback0` |
| `router-id` (qualquer vendor, inclusive OSPF/BGP) | `router-id 26.26.26.26` |
| Bloco de loopback | `interface LoopBack0` + `ip address …` / `interface loopback 0` + `ipv4 address …/32` |
| MikroTik | `/ip address add address=10.255.0.58/32 interface=lo` |
| Juniper | `set interfaces lo0 unit 0 family inet address …/32` |
| IP de gerência do CRM | `Acesso.host` (entra por último, só se o IP ainda não foi reivindicado) |

Peer que não bate com nenhum host aparece em amarelo como **não identificado** —
normalmente é um equipamento que não está cadastrado no cliente ou que ainda não
tem backup. Isso é informação útil, não erro: mostra túnel apontando pra fora do
inventário.

Quando o backup mais recente de um host não revela nenhum IP de identidade
(coleta truncada por paginação `---- More ----`, por exemplo), o mapa tenta até
`_L2VPN_FALLBACK_BACKUPS` (5) backups anteriores daquele acesso — loopback e
LSR-ID praticamente não mudam, então um backup um pouco mais velho ainda
identifica o equipamento corretamente.

---

## Sintaxes reconhecidas

O parser **não confia no `fabricante` do template de backup** (muitos Datacom e
ZTE estão cadastrados como `GENERICO`): ele roda apenas os sub-parsers cujas
marcas sintáticas aparecem no texto. As gramáticas não se confundem entre si.

### Huawei VRP — VSI (VPLS)

```
vsi 127-PPPOE-OLT-LEAL
 pwsignal ldp
  vsi-id 127
  flow-label both
  peer 198.18.255.2
  peer 198.18.255.5
 mtu 1550
#
```

A interface de acesso vem do binding na sub-interface:

```
interface GigabitEthernet0/1/3.3118
 vlan-type dot1q 3118
 description 3118-TRANSP-SIM-NDD-DNO
 l2 binding vsi 3118-TRANSP-SIM-DNO-DNO
#
```

### Huawei VRP — L2VC (VLL ponta a ponta)

```
interface Vlanif2301
 mpls l2vc 198.18.255.2 2301 mtu 1550
#
interface GigabitEthernet0/2/3.101
 vlan-type dot1q 101
 description O1/P1/NLT/Nilton
 mpls l2vc 10.1.1.1 101 raw
#
```

O nome do serviço é a `description` da interface (ou o nome da interface, quando
não há descrição) — é assim que o circuito é identificado na operação.

### Huawei MA5800 (OLT) — VSI por referência

Nas OLTs o pseudowire fica num bloco separado e o VSI só aponta pro índice:

```
pw-para pwindex 1
 service-type vpls
 pwid 3087
 peer-address 198.19.255.1
 pw-type ethernet tagged
#
vsi "PPPOE"
 mtu 1550
 pwsignal ldp
  vsi-id 3087
  vsi-pw-binding pwindex 1
 vsi-ac-binding vlan 3087
#
```

O parser indexa os `pw-para` antes e resolve o `pwindex` para o peer real.

### Datacom DmOS — VPWS e VPLS

```
mpls l2vpn
 vpws-group PPPOE
  vpn 2301-SUPORTE_ONLINE-PATOS-LEAL
   neighbor 198.18.255.0
    pw-type vlan
    pw-id 2301
    pw-mtu 1550
   !
   access-interface gigabit-ethernet-1/1/5
    dot1q 2301
   !
  !
 !
 vpls-group PPPOE
  vpn 127-PPPOE-OLT-LEAL-PATOS-LEAL
   vfi
    pw-type vlan
    neighbor 198.18.255.0
     pw-id 127
     pw-mtu 1550
    !
   !
   bridge-domain
    dot1q 127
    access-interface ten-gigabit-ethernet-1/1/3
    !
   !
  !
 !
!
```

Detalhes que o parser trata:

- a presença de `vfi` classifica o `vpn` como **VPLS** mesmo dentro de um
  `vpws-group` (acontece em config real);
- no `bridge-domain` o `dot1q` vem **antes** das `access-interface` (o contrário
  do VPWS) — as interfaces herdam a VLAN do serviço;
- um `vpn` pode ter mais de uma `access-interface`;
- backups em que o coletor traz a `show running-config` inteira **numa única
  linha** também funcionam: o parser recorta a região do `mpls l2vpn` e varre
  por tokens, guardando o offset de cada `vpn` para reconstruir a linha e o
  trecho de config de cada serviço.

### MikroTik RouterOS

```
/interface vpls add name=vpls-127-pindaiba peer=198.18.255.2 cisco-static-id=127 \
    pw-l2mtu=1550 pw-type=tagged-ethernet
```

O IP do outro lado é `remote-peer=` (v6) ou `peer=` (v7); o VC-ID pode estar em
`vpls-id`, `cisco-style-id` ou `cisco-static-id`. As interfaces de acesso são as
VLANs criadas sobre a interface VPLS e as bridges que a têm como porta.

### Cisco IOS / IOS-XR e Juniper

Suportados pela doc oficial (sem amostra no ambiente até 2026-08-13):
`xconnect IP VC-ID encapsulation mpls`, `l2 vfi NOME manual` + `vpn id`,
`xconnect group`/`p2p`/`neighbor ipv4 … pw-id`, `set protocols l2circuit
neighbor … virtual-circuit-id` e `routing-instances` com `instance-type vpls`.

---

## Endpoint

| Método | URL | Descrição |
|---|---|---|
| `GET` | `/clientes/acessos/<acesso_id>/l2vpn-backup/` | Serviços L2VPN do host + resolução dos peers |
| `GET` | `/clientes/acessos/<acesso_id>/l2vpn-peers/` | Candidatos a peer: hosts do cliente com identidade MPLS, ordenados por quem já tem L2VPN |
| `POST` | `/clientes/acessos/<acesso_id>/l2vpn-clonar/` | Gera (preview) ou aplica a config de um serviço clonado |

Resposta:

```json
{
  "tem_backup": true,
  "data_backup": "11/08/2026 02:19",
  "arquivo": "BRAS_20260811_021919.txt",
  "host": {"id": 10, "nome": "BRAS", "ip": "45.228.38.254",
           "ips_identidade": {"10.1.1.1": "mpls lsr-id"}},
  "resumo": {"vpls": 7, "vpws": 0, "l2vc": 21, "total": 28},
  "servicos": [{
    "tipo": "l2vc", "tecnologia": "L2VC", "nome": "REDE_NEUTRA_RAPIDUS",
    "id": "295", "grupo": "", "descricao": "REDE_NEUTRA_RAPIDUS",
    "sinalizacao": "ldp", "mtu": "9000", "pw_type": "", "encapsulamento": "raw",
    "vlan": "295", "vendor": "huawei", "linha": 1380, "trecho": "interface …",
    "peers": [{"ip": "10.1.1.2", "pw_id": "295", "mtu": "", "flow_label": false,
               "destino": {"acesso_id": 106, "nome": "SW-ATN-PE-BELA-VISTA",
                           "host": "10.1.1.2", "porta": 2299, "protocolo": "SSH",
                           "origem": "mpls lsr-id"}}],
    "interfaces": [{"nome": "Virtual-Ethernet0/1/0.295", "vlan": "295",
                    "descricao": "REDE_NEUTRA_RAPIDUS"}]
  }]
}
```

`tipo` é a família (usada no filtro e na cor); `tecnologia` é o rótulo do
comando que gerou o serviço (VSI, VPLS, VPWS, L2VC, XCONNECT, L2CIRCUIT, VFI) —
o operador procura pelo nome que ele digitou no equipamento.

`destino` é `null` quando o peer não bate com nenhum host do cliente.

### Cache

| Chave | Conteúdo | TTL |
|---|---|---|
| `l2vpn:svc:<backup_log_id>` | serviços parseados de um backup | 6 h |
| `l2vpn:ident:acesso:<acesso_id>` | `{ip: origem}` de identidade do host | 6 h |

Os serviços são cacheados por **id do BackupLog**: o conteúdo de um backup nunca
muda depois de gravado, então só um backup novo invalida (a chave muda junto).
Sem cache, o primeiro clique num cliente de ~40 hosts custa ~0,3 s (leitura +
parse de todos os backups para montar o mapa de identidade).

---

## Interface

- **Botão**: painel de propriedades do host (só aparece em nó vinculado a um
  `Acesso` do CRM) → *Mostrar L2VPN*.
- **Filtro por família** (Todos / VPLS·VSI / VPWS / L2VC) e busca livre por
  nome, id, VLAN, grupo, peer, nome do host do outro lado ou interface.
- **Cores**: VPLS/VSI roxo, VPWS laranja, L2VC ciano — o mesmo par cor/fundo no
  chip do filtro e no badge da linha.
- **Peer verde** = identificado (clicável); **amarelo** = não identificado;
  **vermelho "sem peer"** = serviço configurado sem peer nenhum no equipamento
  (túnel incompleto — vale investigar).
- **Expandir a linha** mostra as interfaces de acesso e o trecho cru da config,
  com o número da linha no arquivo de backup.
- **Copiar tabela** copia os serviços filtrados como tabela Markdown (pra colar
  em chamado, wiki ou e-mail).
- `Esc` fecha o modal; com ele aberto, os atalhos do canvas ficam suspensos.

---

## Artigo de infraestrutura (wiki / Agent NOC)

`formatar_artigo()` em `clientes/backup_parser.py` passou a usar o mesmo parser:
a antiga seção rasa "VSI" + "L2VC/VPWS" virou uma única seção **L2VPN — VSI /
VPLS / VPWS / L2VC**, em tabela por equipamento (tipo, id, nome, VLAN, MTU,
peers, interfaces de acesso). Isso passou a documentar também os serviços
Datacom, que a leitura antiga não reconhecia.

A chave `l2vpn` entra no dict de `parse_backup()`; as chaves rasas `vsi` e
`l2vc` continuam lá por compatibilidade com quem já as consumia.

> Só a task `gerar_snapshots_conhecimento` alimenta essa seção de verdade — ela
> lê o **arquivo** de backup. O caminho `_atualizar_snapshot_cliente` parte do
> `Acesso.contexto_backup`, que é um resumo textual sem os blocos de L2VPN.

---

## Clonar um serviço

O caminho mais comum de operação não é criar um circuito do zero: é repetir um
que já existe trocando VLAN, id e cliente. O botão **Clonar** em cada linha
abre um painel com três passos, o mesmo desenho da automação BGP:

1. **Formulário** pré-preenchido com a config de origem — nome, id (já sugerido
   como o primeiro livre no equipamento), VLAN, MTU, grupo (Datacom),
   descrição, peers e interfaces de acesso. Peers e interfaces são listas: dá
   pra adicionar e remover linhas.
2. **Comandos gerados** pelo backend num textarea **editável** — é a config
   exata que vai pro equipamento, revisável antes de enviar.
3. **Aplicar no equipamento**, com confirmação explícita nomeando host e IP.
   O CRM conecta (mesma conexão Netmiko do Painel de Scripts), envia e mostra
   a saída crua do equipamento.

### Escolher o peer por nome

O campo de peer é um combo: digitar filtra **por nome do host ou por IP** entre
os outros hosts do cliente, mostrando o IP, o nome, de onde aquele IP saiu
(`mpls lsr-id`, `LoopBack0`, `router-id`…) e quantos serviços L2VPN o host já
tem. Clicar preenche o IP; digitar um IP à mão continua valendo — a lista é
atalho, não trava.

A ordem da lista é a ordem em que o operador pensa: primeiro os hosts que **já
têm serviço L2VPN** (o outro lado de um circuito quase sempre é um equipamento
que já faz L2VPN), depois os demais com identidade MPLS. Dentro de cada host, o
IP mais provável vem primeiro — LSR-ID, depois transport-address, loopback,
router-id e, por último, o IP de gerência (que raramente é o peer, e só aparece
quando o backup não revelou nenhuma identidade).

### O preview vem antes de aplicar

"Gerar comandos" nunca toca no equipamento: ele só devolve a config montada, que
aparece num textarea editável rolado até a vista e destacado por um instante. O
botão "Aplicar no equipamento" **só existe depois** que os comandos foram
gerados, e ainda passa pela confirmação nomeando host e IP. Se a geração for
recusada (id em uso, peer inválido…), o motivo aparece em vermelho no painel e
fica lá — não some como um toast.

A VLAN por interface fica **vazia** quando é igual à do serviço: assim ela herda
o campo "VLAN (dot1q)" e mudar a VLAN do clone num lugar só vale pra todas as
interfaces. Só aparece preenchida quando a origem realmente usa uma VLAN
diferente naquela interface.

### O que é gerado, por fabricante

| Fabricante / tipo | Config gerada |
|---|---|
| Huawei VSI (VPLS) | `vsi NOME` + `pwsignal ldp` + `vsi-id` + um `peer` por peer + `mtu`; depois `interface <porta>.<vlan>` com `vlan-type dot1q`, `description`, `mtu` e `l2 binding vsi NOME` |
| Huawei L2VC | `interface <porta>.<vlan>` + `vlan-type dot1q` + `description` + `mtu` + `mpls l2vc <peer> <vc-id> [raw\|tagged]` |
| Datacom VPWS | `mpls l2vpn` → `vpws-group` → `vpn` → `neighbor` (`pw-type`/`pw-id`/`pw-mtu`) → `access-interface` + `dot1q` → `commit` |
| Datacom VPLS | igual, com `vfi` (peers) e `bridge-domain` (`dot1q` + `access-interface`) |
| MikroTik | `/interface vpls add …` + uma `/interface vlan add interface=<vpls>` por interface de acesso |

Detalhes que o gerador acerta e que um copiar-colar não acertaria:

- **Sub-interface Huawei**: a interface da origem já vem como `Gi0/2/3.3127`
  (é assim que aparece no backup). O sufixo antigo é **trocado** pela VLAN nova,
  não concatenado — senão sairia `Gi0/2/3.3127.3200`.
- **Dialeto do RouterOS**: `peer=` (v7) x `remote-peer=` (v6) e
  `cisco-static-id`/`cisco-style-id`/`vpls-id` são copiados da linha de origem
  daquele equipamento, não fixados no código.
- **Commit**: Huawei usa o `conn.commit()` do Netmiko (o `commit` na lista é só
  pro preview/auditoria mostrarem a ação completa); no DmOS o `commit` vai como
  linha de config mesmo — é o comando certo dentro do modo de configuração e
  evita o prompt de "uncommitted changes" na saída.
- **RouterOS não tem modo de configuração**: os comandos são enviados um a um,
  diferente da automação BGP (que só precisava mandar um).

### Recusas (`L2vpnNaoSuportado`)

Validado no backend antes de gerar qualquer comando, com mensagem pronta pra UI:

- id já em uso no equipamento (ou o próprio id da origem);
- nome já existente, ou fora de `[A-Za-z0-9._@:-]{1,63}`;
- peer que não é IPv4 válido, ou repetido;
- VLAN fora de 1–4094, MTU fora de 46–65535, id não numérico;
- nenhum peer, ou nenhuma interface de acesso;
- Datacom sem grupo;
- VPWS/L2VC/MikroTik com mais de um peer (são ponto a ponto — para multiponto,
  clone um VPLS/VSI);
- fabricante fora de Huawei/Datacom/MikroTik.

O texto editado à mão ainda passa por um teto de sanidade (80 linhas, 500
colunas) antes de ir pro equipamento.

### Permissão e auditoria

Restrito a **backoffice** (`is_backoffice` + ferramenta `topologia` habilitada +
`pode_acessar_cliente`): criar pseudowire em equipamento de produção é
engenharia de rede, não função de portal de cliente — mesma régua da automação
BGP.

Toda aplicação grava um `AcaoL2vpn`: quem clicou, serviço de origem, nome e id
do serviço criado, fabricante, comandos exatos enviados, saída do equipamento e
status.

> Depois de aplicar, o serviço novo **não aparece na listagem na hora**: a lista
> é lida do backup, não do equipamento ao vivo. O painel avisa isso — ele
> aparece na próxima coleta de backup do host.

---

## Limitações conhecidas

- Serviços aparecem conforme o **último backup**: mudança feita no equipamento
  hoje só aparece depois da próxima coleta.
- Peer só é identificado dentro do **mesmo cliente** — túnel para equipamento de
  outro cliente (ou de terceiro) fica como não identificado.
- Sem backup coletado, o modal explica o motivo em vez de mostrar lista vazia.
- EVPN (`evpn instance`, `bgp evpn`) ainda não é reconhecido.
- A clonagem cria o serviço **só no host de origem** — o outro lado do túnel
  (no peer) continua sendo config manual. Clonar nos dois lados de uma vez é um
  passo natural daqui, mas hoje não existe.
- Cisco e Juniper são lidos pelo parser mas não podem ser clonados: não há
  ocorrência real de xconnect/l2circuit nos backups deste ambiente pra conferir
  a sintaxe gerada.
