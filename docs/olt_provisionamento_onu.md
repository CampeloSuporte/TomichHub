# Provisionamento de ONU/ONT — OLT Huawei

Adicionado em 2026-10-29. Aba **ONU** do painel **⚡ Scripts** do terminal
(`/clientes/terminal/`). Provisiona uma ONU ou um lote inteiro (até 64) em OLT
Huawei MA5600T/MA5800.

Código: `clientes/olt_provisionamento.py` (lógica), `clientes/onu_prov_views.py`
(endpoints), aba no `clientes/templates/terminal.html`, testes em
`clientes/tests_olt_provisionamento.py`.

## Fluxo

1. Selecionar a OLT em **Executar** e abrir a aba **ONU**. O CRM lê do backup mais
   recente (sem conectar) as placas, os line/srv profiles e as VLANs. Se o host não
   for OLT Huawei, a aba recusa (ZTE/Datacom/Parks usam outra CLI).
2. **🔍 Buscar ONUs pendentes**: roda `display ont autofind all` ao vivo (só leitura).
   Cada ONU vem num cartão com SN, F/S/P, modelo e os valores sugeridos.
3. Ajustar VLAN, line profile, srv profile, gemport, modo e descrição (opcional) de
   cada ONU, e marcar as que serão provisionadas. No lote, "Modo", "Line profile" e
   "Srv profile para as marcadas" aplicam um valor único a todas as marcadas, sobrescrevendo
   a sugestão do backup. Cada cartão continua editável depois.
4. **👁 Pré-visualizar** mostra os comandos e os avisos do backup. **▶ Provisionar**
   pede confirmação e executa em streaming (SSE): o status de cada cartão muda para
   `ONT <id>`, `parcial`, `erro` ou `ignorada`.

## Os dois modos (modelos passados pelo operador)

| Modo | Comandos |
|---|---|
| `onu`: Genérico ONU + ONT | `interface gpon F/S` → `ont add P sn-auth SN omci ont-lineprofile-id L ont-srvprofile-id S [desc "..."]` → `ont port native-vlan P ID eth 1 vlan V priority 0` → `quit` → `service-port vlan V gpon F/S/P ont ID gemport G multi-service user-vlan V tag-transform translate` |
| `ont`: Só ONT | o mesmo, sem o `native-vlan` |

O `ont add` vai **sem ONT-ID**: a OLT escolhe o primeiro livre e devolve
`ONTID :N`. Um ID calculado pelo backup erraria sempre que alguém tivesse
provisionado depois do último backup. Por isso a execução é por etapas: o
`native-vlan` e o `service-port` só são montados depois de lido o ID. Se o firmware
não ecoar o ID, o CRM pergunta `display ont info by-sn <SN>`. No preview o ID
aparece como `{ONT_ID}`.

## VLAN translate: criar srv/line profile e provisionar — Adicionado em 2026-10-29

Seção **➕ Criar srv/line profile para VLAN translate** na aba ONU. Também abre pelo
menu SCRIPT (OLT Huawei selecionada) → **[Huawei] Criar srv/line profile (VLAN
translate)**, que já abre a aba com a seção expandida. Segue o formato do modelo
passado pelo operador:

```
ont-srvprofile gpon profile-id 8 profile-name "translate"
 ont-port eth adaptive 8
 native-vlan unconcern
 commit
 quit
ont-lineprofile gpon profile-id 20 profile-name "LINE-MIGRACAO"
 tcont 1 dba-profile-id 10
 gem add 101 eth tcont 1          ← um por VLAN
 gem mapping 101 1 vlan 101       ← um por VLAN
 commit
 quit
```

- **VLANs:** você adiciona uma por vez (`101`), em faixa (`101-105`) ou em lista
  (`101,110,3304`). Cada VLAN vira um GEM. O contador mostra `N/32 GEMs`, e o campo
  trava no limite (`MAX_GEMS_PERFIL = 32`, o limite prático de GEM ports das ONTs
  Huawei). No máximo 5 GEMs num mesmo perfil foi o que se viu nos 34 backups Huawei
  do ambiente; se a OLT aceitar menos, ela recusa e a recusa aparece. Funciona com uma
  VLAN só ou com várias.
- **Índice do GEM:** é igual ao da VLAN quando cabe (até 1023), como no modelo (gem
  101 ↔ vlan 101). Acima disso, ou com a opção desmarcada, usa o próximo índice livre.
- **Srv e line:** cada um pode ser criado separadamente (checkbox). Os IDs vêm
  sugeridos (primeiro livre no backup).
- **DBA:** a lista vem do backup (`dba-profile add profile-id N profile-name "X" ...`). Se
  o backup não tiver nenhum DBA próprio, ou se você escolher **➕ Criar novo DBA...**,
  aparecem ID (sugerido: primeiro livre a partir de 10), nome, tipo e banda. O DBA é
  criado **antes** do line profile, no formato do backup:
  `dba-profile add profile-id 11 profile-name "DBA-TRANSLATE" type4 max 1024000`
  (type1 `fix`, type2 `assure`, type3 `assure`+`max`, type4 `max`, em kbps). Os IDs 0–9
  são os DBA padrão do MA5800: existem sem aparecer no backup (o line profile 0 da SENTO
  SE usa o 0 e o 2), então não são oferecidos para criação. O novo DBA também passa pela
  conferência ao vivo (`display dba-profile profile-id N` precisa dizer "does not
  exist"). O texto exato dessa recusa ainda não foi visto ao vivo: na hora do teste, o
  terminal do operador ocupava o login. Se vier diferente, a criação aborta sem mexer em
  nada.
- **Segurança:** um ID que já existe no backup é recusado. Entrar em
  `ont-lineprofile gpon profile-id 300` **edita** o perfil, e um `gem add` ali mudaria as
  96 ONTs ligadas a ele. Antes de criar, o CRM ainda confere ao vivo
  (`display ont-srvprofile/lineprofile gpon profile-id N`). Só cria com
  `Failure: The ... profile does not exist`; qualquer outra resposta aborta sem criar
  nada.
- **Execução e auditoria:** pelo terminal aberto (ou pelo servidor, sem terminal). A
  auditoria fica em `AcaoOltPon` (`acao='perfil'`, migração 0127). Os perfis criados
  entram na hora nos selects dos cartões, sem esperar o próximo backup.

### Modelo "ONU com VLAN fixa" (ex.: VLAN 1)

Seletor **MODELO** na mesma seção, ou menu SCRIPT → **[Huawei] Criar perfis ONU com
VLAN fixa (VLAN 1)**. Saiu da config real passada pelo operador (perfis
`SRV_DKY_100`/`VLAN_1`). A ONU sai com uma VLAN setada nas portas, e o service-port
traduz essa VLAN para a VLAN da OLT:

```
ont-srvprofile gpon profile-id 100 profile-name "SRV_DKY_100"
 ont-port pots adaptive 32 eth adaptive 8
 port vlan eth 1 translation 1 user-vlan 1      ← eth 1..N ("portas ETH com a VLAN", padrão 4)
 commit
 quit
ont-lineprofile gpon profile-id 100 profile-name "VLAN_1"
 omcc encrypt on
 tcont 4 dba-profile-id 100
 gem add 1 eth tcont 4 encrypt on
 gem mapping 1 0 vlan 1
 commit
 quit
```

Os campos são VLAN setada (padrão 1), portas ETH com a VLAN (4) e POTS adaptive (32;
0 tira o `pots`). O T-CONT padrão vira 4, e os nomes sugeridos são `SRV_VLAN_<v>` e
`VLAN_<v>`. O DBA segue a mesma regra (backup ou criar novo).

Para provisionar, escolha o line e o srv profile, o modo **Translate** e a VLAN da OLT
(ex.: 101) na linha "GEM 1 · user-vlan 1":

```
ont add 0 sn-auth 4D4F4E55004C22F1 omci ont-lineprofile-id 100 ont-srvprofile-id 100 desc "TESTE_001"
service-port vlan 101 gpon 0/7/0 ont {ONT_ID} gemport 1 multi-service user-vlan 1 tag-transform translate
```

Quando a maioria das ONTs da PON no backup tem service-port com `user-vlan` ≠ `vlan`
(o caso desse modelo), a sugestão já vem no modo **Translate**. Na SENTO SE, onde
`user-vlan` é igual à `vlan`, as 49 PONs continuam sugerindo ONU+ONT.

### Modelo "Passagem de 2+ VLANs" (todas no GEM 1)

Menu SCRIPT → **[Huawei] Criar perfis passagem de 2+ VLANs**, ou MODELO "Passagem de 2+
VLANs na ONU/ONT". Saiu do perfil `FULLCAM` passado pelo operador:

```
ont-srvprofile gpon profile-id 3998 profile-name "FULLCAM"
 ont-port pots adaptive eth adaptive
 port vlan eth 1 translation 3998 user-vlan 3998   ← por VLAN (eth 1..N, "portas ETH com as VLANs")
 port vlan eth 1 translation 3084 user-vlan 3084
 commit
 quit
ont-lineprofile gpon profile-id 3998 profile-name "FULLCAM"
 tcont 4 dba-profile-id 101
 gem add 1 eth tcont 4
 gem mapping 1 1 vlan 3998                          ← índice 1, 2, ... (o 0 fica para a 8ª)
 gem mapping 1 2 vlan 3084
 commit
 quit
```

No máximo 8 VLANs (`MAX_VLANS_GEM`): o índice do `gem mapping` vai de 0 a 7.

## Backup automático depois de criar perfis — Adicionado em 2026-10-29

O provisionamento lê os perfis do **backup** (o inventário é cacheado pelo id do
BackupLog). Por isso, todo script que cria srv/line profile (ou DBA) dispara um backup
logo depois de criar, e a tela recarrega os perfis quando ele termina:

- **Com o terminal aberto na OLT**, a OLT não aceita um segundo login. A configuração
  é lida pela própria sessão (`scroll` + `display current-configuration`, que corre
  na tela do terminal) e gravada como backup em `onu-prov/backup/` (`{"saida"}`), no
  mesmo diretório e com o mesmo cabeçalho do `realizar_backup` ("Acesso: TERMINAL DO
  OPERADOR"). Captura incompleta (sem `return` no fim, sem line profile ou sem placa
  PON) é recusada: um backup truncado viraria o "mais recente" e esconderia placas e
  perfis.
- **Sem terminal**, o servidor roda o `realizar_backup` normal numa thread (não no
  Celery, que tem concurrency=1 e deixaria o backup na fila), logo depois de fechar a
  sessão da criação. A tela consulta o inventário a cada 5 s até o `data_backup`
  mudar (no máximo 5 min).
- Os perfis criados já entram na hora nos selects. O backup só confirma e traz o resto
  (DBAs, uplink, IDs livres). Se ele falhar, a tela avisa para rodar um backup antes de
  provisionar com os perfis novos.

## VLANs na OLT e na uplink — Adicionado em 2026-10-29

Os scripts que criam srv/line profile criam a VLAN (`vlan V smart`) e a passam na uplink
(`port vlan V F/S P`). Sem isso, o service-port entra, mas o tráfego não sai da OLT.

- **Onde está a uplink:** vem do backup. São as portas que recebem `port vlan` no
  nível de config (`uplinks_do_backup`), ordenadas pela quantidade de VLANs. Vêm
  marcadas as que levam pelo menos metade do que a principal leva. Na SENTO SE é a
  `0/8 0` (34 VLANs). Nos 34 backups Huawei aparecem `0/8 0`, `0/8 1`, `0/4 1`,
  `0/17 0`, `0/3 0`... Com LAG, a VLAN vai na porta mestre, que é a que aparece no
  backup. Também dá para digitar outra porta.
- **Só o que falta:** VLAN que já está no backup não é recriada, e porta que já leva
  a VLAN fica como está. Se a OLT responder "already exists" (backup velho), isso não
  conta como falha.
- **Nos perfis:** o bloco de VLAN/uplink roda depois de criar os perfis.
  - No multi-VLAN, as VLANs da OLT são as do próprio perfil.
  - No translate e na VLAN fixa, a VLAN da OLT é outra (ex.: 3304, 101) e é
    informada no campo "VLAN(S) DA OLT".
- **No provisionamento, não.** Por pedido do operador (29/10/2026), o bloco de
  VLAN/uplink aparece só nos scripts que criam srv/line profile. O provisionamento de
  ONU assume que as VLANs já existem e já passam na uplink; VLAN fora do backup só
  gera aviso no preview. O backend ainda aceita `uplink` no `executar/`, mas a tela não
  envia.

## Provisionar com passagem de 1 ou mais VLANs (modo "Passagem VLANs")

Modo `transparente` nos cartões: uma service-port por VLAN do line profile escolhido
(todas marcadas, dá para desmarcar), com a mesma VLAN dos dois lados:

```
ont add 0 sn-auth 53484C4E3C685A47 omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc "2_VLAN_TAG"
service-port vlan 1553 gpon 0/1/0 ont {ONT_ID} gemport 1 multi-service user-vlan 1553 tag-transform transparent
service-port vlan 3869 gpon 0/1/0 ont {ONT_ID} gemport 1 multi-service user-vlan 3869 tag-transform transparent
```

**VLANs à mão no cartão:** nos dois modos (Passagem VLANs e Translate), embaixo da
lista há um campo **+ VLAN** (aceita `1553,3869` ou `101-105`) e o GEM das VLANs
adicionadas (padrão: o GEMPORT do cartão). Assim dá para passar 2+ VLANs numa ONU
única mesmo quando o line profile não tem os gem mappings no backup (ex.: perfil
criado depois do backup, ou o line 10 do exemplo). As linhas manuais aparecem como
"(manual)" e têm ✕; um GEM ausente do perfil gera só aviso.

O modo **Translate** também aceita várias VLANs: marque mais linhas (GEM · user-vlan →
VLAN da OLT). Um GEM pode levar várias VLANs; só não pode repetir a mesma VLAN no mesmo
GEM. Quando a maioria das ONTs da PON tem service-port `transparent`, a sugestão já
vem em "Passagem VLANs".

**Provisionar em modo Translate** (modo novo nos cartões): o cartão lista os GEMs do
line profile escolhido (`gem mapping` do backup, ou do perfil recém-criado). Cada linha
marcada vira um service-port, traduzindo a user-vlan do GEM para a VLAN da OLT:

```
service-port vlan 3304 gpon 0/4/0 ont 0 gemport 101 multi-service user-vlan 101 tag-transform translate
```

Sem `native-vlan`. A primeira linha já vem marcada com a VLAN do cartão; as outras você
marca e preenche se quiser mais de um serviço na mesma ONU.

## De onde vem a sugestão (`sugerir`)

Vem da maioria das ONTs que **já estão naquela PON** no backup, juntando as três
linhas de cada assinante (`ont add`, `ont port native-vlan`, `service-port`):

- VLAN: a do `service-port` (ou, sem ele, a do `native-vlan`);
- line profile e gemport: os mais usados na PON;
- srv profile: primeiro o das ONTs do **mesmo vendor** (os 8 primeiros hex do SN:
  `48575443` = HWTC). Na OLT HU SENTO SE (WTD), as Huawei usam o srv 1001
  "adaptive" e as MKPG/VSOL usam o srv da VLAN. Pela maioria da PON, metade sairia
  errada;
- modo: `onu`, a não ser que a maioria da PON esteja sem `native-vlan`;
- PON vazia: sem VLAN sugerida (o operador preenche), e os perfis ficam com o
  número da VLAN quando existirem (a convenção dos modelos).

## Proteções

- Placa/porta conferidas contra o inventário do backup (`olt_pon.parse_pon`), como no
  painel de portas PON. SN de 16 hex, VLAN entre 1 e 4094, sem aspas no `desc`
  (máximo de 64 caracteres).
- Os avisos não bloqueiam. Aparecem para perfil, VLAN ou gemport que não estão no
  backup, e para SN que já consta no backup (ONU removida depois do backup, ou
  cadastrada em outro lugar).
- Antes do primeiro `ont add`, o lote **relê o autofind** na mesma sessão. Uma ONU
  que já não está pendente, ou que está em outra PON, fica `ignorada`.
- Uma ONU que falha não para o lote. Se o `ont add` entrou e o `service-port` ou o
  `native-vlan` foi recusado, o status é `parcial`, com o ID: a ONT ficou cadastrada
  sem serviço e precisa de correção manual.
- Uma OLT aceita um lote por vez (lock em cache por 15 min, liberado no fim). Um
  segundo pedido recebe 409 com o nome de quem está provisionando.
- A recusa da CLI é detectada por `olt_pon.detectar_erro_cli` (`Failure:`,
  `% Parameter error`...).
- Auditoria: um `AcaoOltPon` por ONU (`acao='provisionar'`, `escrita=True`), com os
  comandos reais (já com o ID) e a saída. Status novo `parcial` (migração 0126).

## Sessão (`ShellOlt`)

A conexão usa o mesmo caminho do backup diário e do painel PON
(`olt_pon.conectar`: proxy SSH do cliente ou túnel OpenVPN), mas lê **até o prompt
voltar** (`MA5800-X7(config-if-gpon-0/1)#`), e não por 3 s de silêncio: 30 ONUs × 5
comandos × 3 s dariam 7 minutos parados. Responde sozinho `{ <cr>||<K> }:` e
`---- More`. O preâmbulo é o mesmo do backup (`enable`, `config`,
`undo interactive`, `undo smart`, `scroll`).

## Logout e VTY cheia — corrigido em 2026-10-29

Na primeira versão, `ShellOlt.fechar` mandava um `quit` só e fechava o canal. No modo
config, isso volta só um nível e não faz logout. Depois dos testes do dia, a SENTO SE
passou a aceitar o login SSH e recusar o shell com
`ChannelException(4, 'Resource shortage')` (VTYs todas ocupadas). O proxy estava
saudável e alcançava a porta 22. Agora o `fechar` manda `quit` até a OLT encerrar a
sessão (no máximo 5 vezes, respondendo `y` se pedir confirmação). A recusa aparece
para o operador como "VTYs ocupadas" (`mensagem_de_falha`). Para liberar na hora, num
terminal já aberto na OLT: `display client` mostra quem está conectado, e
`kill client <id>` derruba uma sessão esquecida.

## Pelo terminal já aberto — Adicionado em 2026-10-29

A OLT da WTD **não aceita o mesmo login duas vezes ao mesmo tempo** (o terminal mostra
o aviso de reenter). Com o terminal do operador aberto na OLT, a conexão própria do
servidor era recusada (`Resource shortage`). Por isso:

- **Com uma aba do terminal conectada nesta OLT**, a aba ONU usa **a mesma sessão**. O
  JS envia os comandos pelo WebSocket da aba e lê a resposta pelo próprio fluxo de saída
  (`tObj._taps`, alimentado no `onmessage`) até o prompt voltar. Responde sozinho
  `{ <cr>||<K> }:` e `---- More`. O operador vê tudo acontecendo no terminal.
- **Sem terminal aberto**, o servidor conecta sozinho, como antes (SSE).

Roteiro no modo terminal (`opProvisionarTerminal`, igual ao `provisionar` do servidor):

1. Leva a sessão até `(config)#` de onde ela estiver (`>` → `enable`; `#` → `config`;
   `(config-if-…)#` → `quit`).
2. Relê `display ont autofind all`. A saída vai para o servidor interpretar
   (`autofind/` com `{"saida": ...}`, sem conectar).
3. Para cada ONU, usa os comandos montados pelo preview do servidor. O `ont add` sai
   primeiro, o ONTID é lido da resposta (ou do `display ont info by-sn`), e o resto vai
   com `{ONT_ID}` trocado pelo número.
4. Cada ONU é auditada em `onu-prov/registrar/` (`AcaoOltPon`, igual ao modo servidor).
5. No fim, volta um nível se o operador não estava em config.

Não digite no terminal enquanto o lote roda, porque o que você digitar entra no meio dos
comandos.

## Menu de scripts e fabricante

- O `terminal/acessos/` devolve `fabricante`, `familia` e `modelo`, tirados do
  **Modelo** cadastrado (`comandos_uteis.identificar_familia`). Antes, o painel
  adivinhava pelo nome do acesso, e "OLT HU SENTO SE" ficava sem fabricante.
- A lista de scripts mostra só os do fabricante selecionado. A carga é numerada
  (`spListaSeq`): a resposta sem filtro, disparada ao abrir o painel, não sobrescreve a
  filtrada que chega antes dela.
- Com uma OLT Huawei selecionada, o menu SCRIPT mostra **[Huawei] Provisionar ONU
  (único)** e **[Huawei] Provisionar ONUs em massa**. As duas abrem a aba ONU e já
  buscam o autofind; no "único", só a primeira ONU fica marcada.

## Endpoints

| Método | URL | |
|---|---|---|
| GET | `/clientes/acessos/<id>/onu-prov/` | placas, perfis e modos (backup) |
| POST | `/clientes/acessos/<id>/onu-prov/autofind/` | autofind ao vivo + sugestão; com `{"saida"}` só interpreta |
| POST | `/clientes/acessos/<id>/onu-prov/executar/` | `preview: true` → JSON; `false` → SSE (modo servidor) |
| POST | `/clientes/acessos/<id>/onu-prov/registrar/` | auditoria de ONU provisionada pelo terminal |
| POST | `/clientes/acessos/<id>/onu-prov/perfis/` | srv/line profile translate: `acao` preview, executar ou registrar |
| POST | `/clientes/acessos/<id>/onu-prov/backup/` | backup após criar perfis: `{"saida"}` grava a captura do terminal; vazio dispara o backup normal |

Permissão: back-office com a ferramenta `scripts` e acesso ao host
(`pode_acessar_acesso`).

## Validação

- Parsers e sugestão conferidos com o backup real da OLT HU SENTO SE (acesso 9486,
  2907 ONTs, 44 line e 44 srv profiles) e com o autofind ao vivo de 29/10/2026 (2 HWTC
  7756ACG4 na 0/1/0 → VLAN 300, line 300, srv 1001, gemport 1).
- Escrita validada ao vivo em 29/10/2026, na mesma OLT: `485754433B654F8B` em 0/1/0
  (modo ONU+ONT, VLAN 300, line 300, srv 1001). A OLT respondeu
  `Number of ONTs that can be added: 1, success: 1` / `PortID :0, ONTID :76`, e o CRM
  usou o 76 no `native-vlan` e no `service-port` (índice 3378, gerado pela OLT).
  Conferido depois: `display ont info 0 76` mostra online/match/normal, e
  `display service-port port 0/1/0 ont 76` mostra `up`. Lote de 1 ONU em ~10 s, com o
  shell lendo até o prompt.
