# Provisionamento de ONU — OLT Datacom DmOS (DM4610 / DM4615)

Adicionado em 2026-11-12. Aba **ONU Datacom** do painel **⚡ Scripts** do terminal
(`/clientes/terminal/`). É a contraparte Datacom do provisionamento Huawei
(`docs/olt_provisionamento_onu.md`).

Código: `clientes/olt_datacom.py` (lógica), `clientes/onu_prov_datacom_views.py`
(endpoints), aba e JS `dm*` em `clientes/templates/terminal.html`, testes em
`clientes/tests_olt_datacom.py`.

Base: os backups (`show running-config | nomore`) das 5 OLTs Datacom do G5
(OLT-DATACOM-01..05, até 1279 ONUs) e o guia rápido DM4610 rev 1.4 (ONU Bridge e ONU
Router).

## Menu SCRIPT

Com uma OLT Datacom selecionada (família `datacom_olt`, tirada do Modelo: `DM46xx` ou
"OLT"), o menu mostra e a aba **ONU Datacom** aparece:

| Entrada | O que abre |
|---|---|
| `[Datacom OLT] Provisionar ONU (único)` | busca as pendentes e deixa só a 1ª marcada |
| `[Datacom OLT] Provisionar ONUs em massa` | busca as pendentes, todas marcadas (até 64) |
| `[Datacom OLT] Criar perfis ONU Bridge` | criação de perfis, tipo bridge |
| `[Datacom OLT] Criar perfis ONU Router / PPPoE na ONU` | criação de perfis, tipo router (veip) |

Um switch Datacom (DM4370, família `datacom`) não mostra nada disso. Se o backup não
tiver `interface gpon` + `profile gpon`, o endpoint recusa com 422.

## Diferenças em relação ao Huawei

- **O ID da ONU e o do service-port são escolhidos pelo CRM.** O DmOS não tem "ont add
  sem ID". Entrar em `onu 5` ou `service-port 3181` que já existem **edita** o cadastro
  de outro assinante. Por isso cada ID sai do backup (primeiro livre na PON, 0–127;
  primeiro service-port livre a partir de 1) e é **conferido ao vivo** antes de gravar:
  `show running-config interface gpon P onu N` e `show running-config service-port S`
  precisam responder `syntax error: element does not exist`. Se o ID existe, o CRM tenta
  o próximo (até 10 vezes). Uma resposta que não diga nem uma coisa nem outra aborta a
  ONU sem gravar nada.
- **Um `commit` por ONU.** A ONU e o service-port entram juntos ou não entram. Não
  existe o status "parcial" do Huawei. A primeira recusa da CLI (`syntax error`,
  `Aborted:`, `Error:`...) ou um `commit` sem `Commit complete` faz `abort`, que sai do
  config descartando o que não foi gravado.
- **Sempre pela conexão do servidor.** O DmOS aceita várias sessões do mesmo usuário (o
  terminal do operador fica aberto), e a sessão do servidor tem candidato próprio: o
  `commit` dela só leva o que ela digitou, nunca um config pela metade do operador.
- Antes do lote, o CRM relê `show interface gpon discovered-onus`. Uma ONU que não está
  mais pendente naquela PON fica `ignorada`.

## Comandos de uma ONU

Mesma forma do guia DM4610 e do running-config do G5:

```
config
interface gpon 1/1/12
onu 5
name NOME_DO_CLIENTE            ← opcional; espaço vira _
serial-number VSOL007B1566
service-profile ONU_BRIDGE
line-profile BRIDGE_G5
ethernet 1                       ← bridge
negotiation
no shutdown
native vlan vlan-id 2603
top
service-port 3181 gpon 1/1/12 onu 5 gem 1 match vlan vlan-id 2603 action vlan replace vlan-id 2603
commit
end
```

No modo **router**, o bloco `ethernet 1 ...` vira `veip 1` (a PPPoE fica na ONU). No G5,
as ONUs router usam o service-profile `ONU-BRIDGE` com um line-profile de `veip`
(`pppoe-router-2525`, `pppoe-router`).

**VLAN** é a do `match vlan` (e a `native vlan` no bridge). **VLAN OLT** é a do `replace`;
por padrão é igual. A OLT-DATACOM-04 tem um caso diferente (2607 → 4040, L2L).

## Sugestão (maioria da PON)

Cada ONU do backup vira uma "receita": modo, service-profile, line-profile, GEM, VLAN e
VLAN OLT (as três últimas tiradas do service-port dela). A sugestão é a receita mais comum,
na ordem:

1. ONUs da mesma PON e do mesmo fabricante (4 primeiras letras do SN: DACM, VSOL, ZTEG...);
2. a PON toda;
3. a OLT toda, mesmo fabricante;
4. a OLT toda.

Trocar o line-profile no cartão traz o GEM dele, o modo (bridge/router) e, se ele mapeia uma
VLAN só, a VLAN.

Avisos (não bloqueiam): line ou service fora do backup, GEM que o line não tem, line de outro
modo, VLAN sem `dot1q` ou sem `service vlan` no backup, e SN que já consta no backup (ex.: o
VSOL007B1566 pendente na OLT03 em 1/1/12 aparece no backup em 1/1/13 onu 11).

## Criar perfis e VLAN

Seção **➕ Criar perfis** da aba. Tudo num `commit` só:

```
profile gpon bandwidth-profile PPPoE          ← só se "Criar novo"
traffic type-4 max-bw 1106944
top
profile gpon line-profile pppoe-router-3100
upstream-fec                                  ← checkbox (todos os line do G5 têm)
tcont 1 bandwidth-profile PPPoE
gem 1
tcont 1 priority 0
map pppoe
veip 1 vlan 3100 cos any                      ← bridge: ethernet any vlan V cos any
top
profile gpon onu-profile ONU-ROUTER           ← só se "Criar novo" (router: [pots adaptive] veip 1; bridge: ethernet adaptive)
...
profile gpon service-profile ONU-ROUTER       ← só se "Criar novo"
onu-profile ONU-ROUTER
top
dot1q                                         ← só o que falta: VLAN nova (com nome) e/ou uplink que não leva a VLAN
vlan 3100
name VLAN-3100
interface ten-gigabit-ethernet-1/1/1
top
service vlan 3100                             ← só se a VLAN não tem service vlan
type n:1
top
```

- **VLAN do map:** a própria VLAN ou `any` (como `BRIDGE_G5` e `pppoe-router-2525`).
- **Uplink:** as interfaces do `dot1q` do backup, ordenadas por quantas VLANs levam (no G5 é a
  `ten-gigabit-ethernet-1/1/1`). Vêm marcadas as que levam pelo menos metade do que a principal
  leva. Dá para digitar outra.
- **Nome que já existe é recusado.** No DmOS entrar num perfil existente edita o perfil de todas
  as ONUs dele. Além do backup, o CRM confere ao vivo (`show running-config profile gpon
  line-profile X` → `element does not exist`) antes de entrar em config.
- Depois de criar, dispara o backup normal em segundo plano (`_agendar_backup` do Huawei). Os
  perfis novos já entram nos selects dos cartões na hora.

## Remover a ONU recém-provisionada

Depois de `sucesso`, o cartão mostra **🗑 Remover ONU**. O CRM confere que
`show running-config interface gpon P onu N` ainda tem `serial-number <SN>` e grava:

```
config
no service-port 3181
interface gpon 1/1/12
no onu 5
top
commit
end
```

## Endpoints

| Método | URL | |
|---|---|---|
| GET | `/clientes/acessos/<id>/onu-dm/` | PONs, perfis, VLANs e uplinks do backup (sem conectar) |
| POST | `/clientes/acessos/<id>/onu-dm/discovered/` | `show interface gpon discovered-onus` ao vivo + sugestão |
| POST | `/clientes/acessos/<id>/onu-dm/executar/` | `preview: true` → JSON; `false` → SSE |
| POST | `/clientes/acessos/<id>/onu-dm/perfis/` | `acao` preview ou executar |
| POST | `/clientes/acessos/<id>/onu-dm/remover/` | `acao` preview ou executar |

Permissão: back-office com a ferramenta `scripts` e acesso ao host (`pode_acessar_acesso`), a
mesma régua do Huawei. Um lote ou criação de perfis por OLT de cada vez (lock em cache, 409 para
o segundo). Auditoria em `AcaoOltPon` (`acao` provisionar, perfil ou remover; `slot` = PON,
`portas` = ID da ONU), com os comandos e a saída.

## Validação

- Parser conferido nos backups das 5 OLTs do G5 (OLT03: 16 PONs, 1279 ONUs, 1277 service-ports,
  21 line-profiles; contagem por PON batendo com o `show interface gpon 1/1/1 onu`).
- Ao vivo, **só leitura**, na OLT03 (acesso 15) em 12/11/2026: prompt `OLT03#`, formato do
  `discovered-onus`, a resposta `syntax error: element does not exist` para ONU, service-port e
  line-profile inexistentes, e o `ShellDatacom` lendo até o prompt (discovered em 1,5 s). Nesse
  teste a conferência ao vivo pegou o caso real: o backup das 04:12 dava a ONU 127 da PON 1/1/12
  como livre, e ela já estava em uso.
- **Escrita validada ao vivo em 12/11/2026** na OLT-DATACOM-02 (G5DM02, acesso 113): a
  ZTEGCD0FD4F5, pendente em 1/1/11, foi provisionada em modo bridge (`ONU_BRIDGE` +
  `pppoe-dados`, VLAN 2620). As duas conferências responderam `element does not exist` (ONU 32,
  service-port 2), o `commit` respondeu `Commit complete.` e o running-config ficou igual ao das
  outras ONUs. Em seguida ela foi removida pelo mesmo fluxo do 🗑: conferência pela SN,
  `no service-port 2` + `no onu 32`, `Commit complete.`, e os dois IDs voltaram a "não existe".
  Prompts vistos: `G5DM02(config)#`, `(config-gpon-1/1/11)#`, `(config-gpon-onu-32)#`,
  `(config-ethernet-1)#`, `(config-service-port-2)#`.
- Depois da remoção, a ONU leva de 1 a 2 minutos para voltar ao `discovered-onus` (voltou em
  ~2 min). Essa ZTE ficou `Down / Inactive` enquanto esteve provisionada. Não foi o script: o
  `show log` mostra a ONU sendo redescoberta o dia todo (11:32, 11:44, 12:02 … 19:45), ou seja,
  é instável no campo.
- O caminho de recusa (`abort` depois de um erro da CLI) ainda não aconteceu ao vivo. Ele só
  está coberto pelos testes.
