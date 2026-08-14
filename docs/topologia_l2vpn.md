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
- MTU, sinalização (LDP/BGP/static), `pw-type`, `flow-label` e o trecho cru da
  config.

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

A interface de acesso vem do `l2 binding vsi`. Na prática deste ambiente ele
está quase sempre na `Vlanif` da VLAN do serviço (1092 dos 1128 bindings reais)
e, com menos frequência, numa sub-interface:

```
interface Vlanif200
 description 200-GERENCIA_POP-PRADO
 l2 binding vsi 200-GERENCIA_POP-PRADO
#
interface GigabitEthernet0/1/3.3118
 vlan-type dot1q 3118
 description 3118-TRANSP-SIM-NDD-DNO
 l2 binding vsi 3118-TRANSP-SIM-DNO-DNO
#
```

O parser lê as duas formas; o **gerador** de clone usa sempre a `Vlanif` (ver
"O acesso do VSI Huawei é a Vlanif").

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

### LDP targeted (a sessão que sustenta o pseudowire)

Lido por `extrair_ldp` para saber quais peers já têm sessão e qual `lsr-id` o
equipamento usa:

```
mpls ldp remote-peer sw-core-01          | mpls ldp
 remote-ip 198.18.255.0                  |  lsr-id loopback-0
#                                        |   neighbor targeted 21.21.21.21
        (Huawei VRP)                     |        (Datacom DmOS)
```

Também entra `mpls lsr-id <IP>` (Huawei) como LSR-ID local quando não há um
`lsr-id` nomeado. Nos 2656 backups deste ambiente, 331 têm bloco de LDP legível.

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
  "id_sugerido": 3503,
  "pode_clonar": true,
  "servicos": [{
    "tipo": "l2vc", "tecnologia": "L2VC", "nome": "REDE_NEUTRA_RAPIDUS",
    "id": "295", "grupo": "", "descricao": "REDE_NEUTRA_RAPIDUS",
    "sinalizacao": "ldp", "mtu": "9000", "pw_type": "", "encapsulamento": "raw",
    "flow_label": "", "vlan": "295", "vendor": "huawei", "linha": 1380,
    "trecho": "interface …",
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
| `l2vpn:svc:v<N>:<backup_log_id>` | serviços parseados de um backup | 6 h |
| `l2vpn:ident:v<N>:acesso:<acesso_id>` | `{ip: origem}` de identidade do host | 6 h |

O `v<N>` das chaves é `_L2VPN_CACHE_VERSAO` (`clientes/views.py`): **suba junto
com qualquer mudança em `l2vpn_parser.py`**. Sem isso, um campo novo no parser
demora até 6 h para aparecer — o painel continua servindo o parse antigo, no
formato antigo.

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
   como o primeiro livre no equipamento), VLAN, MTU, grupo (Datacom), descrição,
   `flow-label`, peers e — conforme o fabricante — interfaces de acesso ou as
   portas físicas onde a VLAN entra. Peers, interfaces e portas são listas: dá
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
| Huawei VSI (VPLS) | `vsi NOME` + `pwsignal ldp` + `vsi-id` + `flow-label` + um `peer` por peer + `mtu`; depois `vlan <VLAN>`, `interface Vlanif<VLAN>` com `l2 binding vsi NOME` e, para cada porta física escolhida, `port trunk allow-pass vlan <VLAN>` (tagged) ou `port default vlan <VLAN>` (untagged) |
| Huawei L2VC | `interface <porta>.<vlan>` + `vlan-type dot1q` + `description` + `mtu` + `mpls l2vc <peer> <vc-id> [raw\|tagged]` |
| Datacom VPWS | `mpls l2vpn` → `vpws-group` → `vpn` → [`qinq`] → `neighbor` (`pw-type vlan [N]`, `pw-load-balance`/`flow-label`, `pw-id`, `pw-mtu`) → `access-interface` + `dot1q` (dentro de `encapsulation` quando o serviço é qinq) → `commit` |
| Datacom VPLS | igual, com `vfi` (peers) e `bridge-domain` (`dot1q` + `access-interface`) |
| MikroTik | `/interface vpls add …` + uma `/interface vlan add interface=<vpls>` por interface de acesso |

### Fechar a sessão LDP junto — 2026-08-13

O pseudowire só sobe se existir **sessão LDP targeted** com o peer, e ela mora
**fora** do bloco do serviço: clonar um VSI/VPWS apontando para um peer novo
criava o serviço e deixava o circuito down. O formulário passou a ter a opção
**"Fechar também a sessão LDP com o peer"** (marcada por padrão), que mostra
peer a peer quem já tem sessão no backup e quem será criado — quem já tem é
pulado, nada de comando duplicado.

| Fabricante | Config gerada |
|---|---|
| Huawei VRP | `mpls ldp remote-peer <nome>` + `remote-ip <peer>` (um bloco por peer) |
| Datacom DmOS | `mpls ldp` → `lsr-id <loopback>` → `neighbor targeted <peer>` |

Dois detalhes que vêm do backup, não de valor fixo no código:

- **`lsr-id` do DmOS** — o `neighbor targeted` vive *dentro* dele e qual loopback
  está em uso muda por equipamento, então `extrair_ldp` lê o que o host usa
  (`lsr-id loopback-0`) e o campo aparece no formulário já preenchido, editável.
  Sem `lsr-id` no backup a clonagem recusa em vez de chutar `loopback-0`.
- **nome do `remote-peer` no Huawei** — é rótulo livre; nos backups daqui
  aparece tanto o IP quanto o nome do equipamento do outro lado. Usa o nome do
  host quando o CRM identificou o peer (resolvido no servidor, pelo mesmo mapa
  de identidade que casa peer → host no modal) e cai no IP quando não
  identificou.

MikroTik fica de fora: a sessão LDP do RouterOS é outro modelo
(`/mpls ldp neighbor`) e não há ocorrência nos backups deste ambiente para
conferir a sintaxe — com ele a opção nem aparece.

### VLAN de acesso × VLAN do pseudowire — corrigido em 2026-08-13

No DmOS o `pw-type vlan N` traz a **VLAN que trafega dentro do túnel**, que não
é obrigatoriamente a VLAN de acesso do cliente. Existe config real assim neste
ambiente:

```
vpn CGNAT_NAS03_PUTIRI
 qinq
 neighbor 24.24.24.24
  pw-type vlan 2400        ← VLAN do pseudowire
 !
 access-interface gigabit-ethernet-1/1/3
  encapsulation
   dot1q 86                ← VLAN de acesso
  !
 !
```

O parser guardava as duas no mesmo campo (`vlan`) e, como o `access-interface`
aparece **antes** do `dot1q` dele, a interface herdava a VLAN do pseudowire e o
`dot1q 86` era ignorado — o clone saía com `dot1q 2400` e `pw-type vlan` sem o
id. Agora são campos separados (`vlan` = acesso, `pw_vlan` = pseudowire), a VLAN
lida na própria interface sobrepõe a herdada (`_add_interface(sobrepor_vlan=True)`)
e o formulário mostra os dois campos.

### VLAN do VSI Huawei vem do binding — corrigido em 2026-08-13

O bloco `vsi` não tem VLAN nenhuma (só o `vsi-id`): a VLAN do serviço é a da
interface onde ele foi amarrado (`interface Vlanif200` + `l2 binding vsi`). O
parser não fazia essa ligação, então o serviço ficava com VLAN vazia — **238
VSIs distintos** nos backups deste ambiente — e, como o clone do VSI é aplicado
na `Vlanif` da VLAN, o formulário abria com o campo obrigatório em branco e o
número aparecendo só na linha da interface. Agora o `l2 binding vsi` preenche a
VLAN do serviço quando ela ainda não é conhecida.

### O acesso do VSI Huawei é a Vlanif

No VSI Huawei o serviço **não** é aplicado numa sub-interface: ele é aplicado na
`Vlanif` da VLAN designada — 1092 dos 1128 bindings reais deste ambiente são em
`Vlanif`, contra 36 em porta física. Por isso o formulário do VSI não pede
"interface de acesso": a `Vlanif` sai da VLAN informada (que passa a ser
obrigatória), e a VLAN é criada antes, já que a `Vlanif` não existe sem ela.

O que se escolhe são as **portas físicas por onde a VLAN entra**, cada uma
`tagged` ou `untagged`:

| Modo | Comando gerado |
|---|---|
| tagged | `port trunk allow-pass vlan <VLAN>` |
| untagged | `port default vlan <VLAN>` |

O `port link-type` da porta **não** é alterado: trocar o tipo de uma porta em
produção derruba o que já passa por ela. Se a porta ainda não for do tipo certo,
a linha entra à mão no preview (que é editável). As portas são opcionais — a
VLAN pode já estar liberada nos uplinks.

### Interfaces vêm listadas do backup

Os campos de interface (o `access-interface` do Datacom, a porta do L2VC Huawei
e as portas físicas do VSI) são combos: digitar filtra **por nome da porta ou
pela descrição** — é pela descrição (`CLIENTE-NETCENTER`, `LACP-CGNAT`) que se
sabe qual porta é a certa. A lista vem do backup do próprio host, via
`/clientes/acessos/<id>/interfaces-backup/`, e mostra **só interfaces físicas**:
`Vlanif`, `LoopBack`, `NULL`, `MEth`, túneis, `l3 <nome>` do DmOS e
sub-interfaces ficam de fora — num switch com 48 portas e 300 `Vlanif`, listar
tudo junto torna a busca inútil, e `Vlanif` nunca é a porta onde a VLAN entra.

No DmOS a interface é **declarada** com espaço (`interface gigabit-ethernet
1/1/1`) e **referenciada** com hífen (`access-interface
gigabit-ethernet-1/1/1`). A lista vem da declaração, então o que o operador
escolhe é convertido para a forma de referência antes de virar comando.

Detalhes que o gerador acerta e que um copiar-colar não acertaria:

- **Sub-interface Huawei (L2VC)**: a interface da origem já vem como
  `Gi0/2/3.3127` (é assim que aparece no backup). O sufixo antigo é **trocado**
  pela VLAN nova, não concatenado — senão sairia `Gi0/2/3.3127.3200`.
- **Dialeto do RouterOS**: `peer=` (v7) x `remote-peer=` (v6) e
  `cisco-static-id`/`cisco-style-id`/`vpls-id` são copiados da linha de origem
  daquele equipamento, não fixados no código.
- **`flow-label`**: é o que faz o tráfego do pseudowire balancear entre os
  caminhos do core, e sai da origem junto com o resto (dá pra trocar ou
  desligar no formulário). Cada fabricante o coloca num lugar diferente, e o
  gerador respeita a posição da config real: no Huawei entre o `vsi-id` e os
  `peer`; no VPWS Datacom dentro do `neighbor`, **antes** do `pw-id`; no VPLS
  Datacom dentro do `neighbor`, **depois** do `pw-id`/`pw-mtu`.
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
- nenhum peer; nenhuma interface de acesso (exceto no VSI Huawei, que usa a
  `Vlanif` e não pede interface);
- Datacom sem grupo;
- VSI Huawei sem VLAN (é nela que a `Vlanif` do serviço se apoia);
- porta com modo diferente de `tagged`/`untagged`;
- `flow-label` diferente de `both`/`transmit`/`receive`;
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
