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
   cada ONU, e marcar as que serão provisionadas. "Modo para as marcadas" troca o modo
   de todas de uma vez.
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

## Endpoints

| Método | URL | |
|---|---|---|
| GET | `/clientes/acessos/<id>/onu-prov/` | placas, perfis e modos (backup) |
| POST | `/clientes/acessos/<id>/onu-prov/autofind/` | autofind ao vivo + sugestão |
| POST | `/clientes/acessos/<id>/onu-prov/executar/` | `preview: true` → JSON; `false` → SSE |

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
