# Vínculo Acesso ↔ host Zabbix (status na aba Acessos)

Cada card da aba **Acessos** mostra uma linha **Zabbix** com o host do Zabbix do
cliente correspondente e o status dele (disponível, com alerta, indisponível).
A bolinha do card também passa a refletir o Zabbix até alguém clicar em **Ping**.
Quem faz a coleta é o Zabbix do próprio cliente; o CRM só lê pela API.

## Quando aparece

- O cliente tem Zabbix cadastrado: configuração da aba Monitoramento **ou** um
  acesso HTTP/HTTPS com "Zabbix" no tipo (mesma descoberta do Agent NOC,
  `monitoramento/agent_zabbix.py::resolver_config`, que já aplica o túnel SSH do
  proxy em IP privado e cai para as credenciais do acesso quando a configuração
  está com senha errada).
- A conta pode usar a ferramenta **Monitoramento** (mesma regra do
  `ferramenta_instancia_required('monitoramento')`).
- Login somente leitura (`acessos_somente_leitura`) vê o status, mas não muda o
  vínculo nem sincroniza.

## Como o vínculo é feito

`monitoramento/vinculo_zabbix.py`, gravado em `ZabbixHostVinculo` (OneToOne com
`Acesso`):

1. IP do acesso (sem esquema, caminho ou porta) e `host_ipv6` contra **todos** os
   IPs de interface de cada host (`services.hosts_para_vinculo`, sem limite).
2. 1 host com aquele IP → vínculo `auto`. Vários acessos no mesmo IP (SSH, Web)
   vão para o mesmo host.
3. Vários hosts no mesmo IP → desempata só por nome **idêntico** ao tipo do acesso
   (ignorando pontuação e caixa). Sem desempate fica ambíguo: não vincula e o
   resumo do sincronismo lista para escolher na tela. Casos reais: o mesmo BRAS
   cadastrado duas vezes (PPPoE e BGP), host duplicado de teste.
4. `127.0.0.1`/`0.0.0.0` são ignorados (agent do próprio servidor Zabbix).
5. Vínculo `auto` que não casa mais (IP mudou, host removido) é apagado.

Escolha manual (linha "Zabbix" do card → seletor com busca por nome/IP):

- **host escolhido** → `origem='manual'`, o sincronismo nunca altera (só atualiza
  o nome se o host for renomeado; se o host sumir, aparece em
  `manuais_sem_host`).
- **Não monitorado** → manual com `zabbix_hostid=''`; não tenta casar de novo.
- **Automático (IP)** → apaga o vínculo; o próximo sincronismo decide.

NAT (acesso por IP público + porta, Zabbix monitorando o IP privado) não casa
por IP: é o caso típico da escolha manual.

## Quando sincroniza

- Ao **salvar a configuração do Zabbix** na aba Monitoramento: limpa os caches do
  cliente (`vinculo_zabbix.limpar_cache`) e já vincula; a mensagem diz quantos
  acessos casaram (Sartor: 69 de 87). Antes disso, configurar o Zabbix com a
  página aberta não mostrava nada nos cards até recarregar.
- A parte do Zabbix nos cards (oculta) vem para toda conta que pode usar
  Monitoramento, mesmo sem Zabbix: o status responde `zabbix: false` e a página
  tenta de novo ao abrir a aba Acessos e a cada 60s, sem desistir.
- Botão **Sincronizar Zabbix** na barra da aba Acessos (mostra o resumo).
- Ao abrir a aba, se o cliente não foi sincronizado nas últimas 6h.
- Task `monitoramento.tasks.sincronizar_vinculos_zabbix` às 05:20 (beat em
  `crm/celery.py`); um cliente fora do ar não interrompe os outros.

## Desempenho

- Status: `GET /monitoramento/zabbix/vinculos/status/?id=<cliente>` — um
  `host.get` + um `trigger.get` filtrados pelos hostids vinculados, cache de 60s
  no Redis por cliente. A página consulta a cada 60s só com a aba Acessos visível
  e o navegador em primeiro plano; para de vez se a sessão expirar.
- Zabbix inalcançável: o erro fica em cache por 5 min (`zbx_vinc_indisp:<id>`)
  para a aba não prender um worker do gunicorn tentando todos os candidatos a
  cada abertura.
- Lista de hosts para o seletor: cache de 5 min.

## Rotas

| Rota | Método | O quê |
|---|---|---|
| `zabbix/vinculos/status/` | GET | status por acesso |
| `zabbix/vinculos/hosts/` | GET | hosts para o seletor manual |
| `zabbix/vinculos/sincronizar/` | POST | refaz os vínculos automáticos |
| `zabbix/vinculos/definir/` | POST | `modo=host` (+`hostid`), `nenhum` ou `auto` |
| `zabbix/vinculos/itens/` | GET | interfaces, óptico, CPU, temperatura, voltagem do host (`acesso_id`) |
| `zabbix/vinculos/historico/` | GET | séries (`acesso_id`, `itemids`, `periodo` 1h…30d); só itens do host vinculado |
| `zabbix/vinculos/cadastro/previa/` | GET | sugestão de cadastro (nada é escrito) |
| `zabbix/vinculos/cadastro/criar/` | POST JSON | SNMP no MikroTik + `host.create` + item de voltagem |
| `zabbix/vinculos/voltagem/ativar/` | POST | item de voltagem num MikroTik já monitorado |

Todas exigem a ferramenta Monitoramento e acesso ao cliente; as de escrita
(`sincronizar`, `definir`, `cadastro/*`, `voltagem/ativar`) recusam login
somente leitura.

## CPU, uptime e gráficos (só com host vinculado)

Com o acesso vinculado a um host, o card ganha as linhas **CPU** (barra; amarela
≥70%, vermelha ≥90%) e **Uptime**, e o botão **Gráficos** na linha Zabbix. Clicar
no nome do host vinculado também abre os gráficos; trocar o host fica no lápis
(sem vínculo, o clique no nome abre o seletor). Sem
vínculo, nada disso aparece. Host sem item de CPU ou de uptime no Zabbix
também não mostra a linha correspondente.

Os templates variam muito entre clientes, então `monitoramento/zabbix_metricas.py`
escolhe os itens por chave + nome + unidade (funções puras, testadas com casos
reais):

| Métrica | Regra | Armadilhas reais |
|---|---|---|
| CPU | `%` (ou sem unidade com util/uso/load) e "cpu"/"processor"/`hwAvgDuty` no texto; prioriza `system.cpu.util` "CPU utilization" > 5 min > 1 min > outros > 5 s; várias placas → o card mostra a maior | Linux tem 20+ itens "CPU * time" (excluídos); OLT tem "Fan Duty Cycle" em `%`; Datacom usa `cpuLoad*`, MikroTik/Cisco `hrProcessorLoad` — "load" só é excluído como *load average* |
| Uptime | unidade `uptime` ou `s`; `system.hw.uptime` > `system.uptime` > `sysUpTime` > outros; **prefere item com valor > 0** | Huawei VRP devolve 0 em `hrSystemUptime`; LDP/BGP/sessão excluídos; item sem unidade (centésimos? texto?) é ignorado |
| Interfaces | `net.if.in[…]`/`net.if.out[…]` em `bps`, pareados pelo nome antes do ":" | `net.if.in.errors/discards` ficam de fora |
| Óptico | unidade dBm/!dBm/µW/mW ou nome "RX/TX Power"; agrupado por interface (nome sem o sentido e sem o ramo/lane/canal, para RX e TX da mesma porta caírem juntos); multilane com média calculada → só a média | µW convertido para dBm; porta sem módulo (tudo 0) fica oculta por padrão; formatos: "RxPower Lane2", "Potência Recebida (RX) na Interface X - Ramo 1", "SFP X RX Power", "X: Rx Power", "Ch1" |

Desempenho: a descoberta de quais itens são CPU/uptime de cada host é uma busca
filtrada (`search` em chave/nome) guardada por 30 min; a cada 60s o status só
lê o `lastvalue` desses itens. A janela de gráficos lista todos os itens do host
(cache de 2 min) e busca o histórico **em série** — em paralelo as requisições
disputam o mesmo túnel SSH do proxy e estouram o timeout de 15s (medido).
Janelas acima de 3 dias usam `trends` (médias horárias). O servidor só devolve
histórico de itens do host vinculado ao acesso pedido.

Rotas: `zabbix/vinculos/itens/?acesso_id=` e
`zabbix/vinculos/historico/?acesso_id=&itemids=1,2&periodo=1h|6h|24h|7d|30d`.

## Cadastrar no Zabbix (acesso que não casou pelo IP)

Botão **Cadastrar** na linha Zabbix do card sem vínculo (e "Cadastrar no Zabbix"
no seletor de host). Só para quem edita acessos. Abre uma prévia — nada é
escrito no Zabbix até confirmar. `monitoramento/zabbix_cadastro.py`:

**Templates** (o "padrão" é o que o próprio cliente já usa):
1. Mesmo conjunto dos hosts vinculados a acessos **do mesmo modelo**.
2. Mesmo conjunto dos hosts do **mesmo fabricante e mesma função** (BRAS com BRAS).
3. **Template oficial do modelo** lido do backup (`# model = CCR1009-7G-1C-1S+` →
   "MikroTik CCR1009-7G-1C-1S+ by SNMP") + complementos que ≥50% dos vizinhos do
   fabricante usam e que não são "by SNMP" (ex.: "Template Sinal SFP Mikrotik - BEE").
4. Template oficial genérico do fabricante ("Huawei VRP by SNMP") — antes de copiar
   vizinho do fabricante de **outra** função (templates de switch S57xx num BRAS).
5. Vizinhos do fabricante; por fim "Generic by SNMP"/ICMP.

Se o conjunto copiado traz o template oficial de outro modelo (vizinho com
RB3011 num CCR1009), ele é trocado pelo do modelo deste equipamento.

**Grupos**: dos mesmos vizinhos (modelo → fabricante+função → fabricante); sem
vizinho, grupo com o nome do fabricante. **Proxy**: o mais comum nos vizinhos.

**SNMP**: versão/community dos vizinhos, conferida com o backup do equipamento:
- RouterOS nos dois formatos de export (terse `/snmp community set ... name=X`, o
  dos backups do CRM, e em bloco); `addresses=` restrito vira aviso.
- Macro global (`{$SNMP_COMMUNITY}`) é resolvida: se o valor não é uma community do
  equipamento, usa a do backup (caso real: macro `TOMICH`, equipamento `green-ro`).
- Huawei guarda cifrada (`cipher`): usa a dos vizinhos, com aviso.
- SNMPv3: só copiando de um host vizinho; as credenciais nunca vão ao navegador.

**Criação** (`host.create`): nome técnico saneado e checado (não duplica), tag
`crm_acesso_id`, proxy por `monitored_by`+`proxyid` (7.0+) ou `proxy_hostid`
(5.0/6.x). Erro de permissão da API vira mensagem pedindo acesso de escrita ao
grupo. O vínculo é gravado como manual, com `criado_no_zabbix_por/em`.

A prévia avisa quando já existe host com o mesmo IP (oferece "Vincular a").

Rotas: `zabbix/vinculos/cadastro/previa/?acesso_id=` (GET) e
`zabbix/vinculos/cadastro/criar/` (POST JSON).

## SNMP configurado no equipamento (MikroTik) ao cadastrar

Na prévia do cadastro, "Configurar o SNMP no equipamento por SSH" (marcado por
padrão em MikroTik com SSH cadastrado). Ao confirmar, **antes** do `host.create`:

1. Lê `/snmp print` + `/snmp community print terse` pelo `platform_ssh_exec`
   (proxy/VPN/direto; RouterOS por exec sem PTY), na porta SSH do acesso.
2. Aplica, no padrão do NOC:
   - community com esse nome já existe → `/snmp community set [ find name="X" ] addresses=<IP do Zabbix>/32`
   - senão → `/snmp community set [ find default=yes ] addresses=<IP do Zabbix>/32 name="X"`
   - `/snmp set enabled=yes`
   - SNMPv3 (copiado de host vizinho): `security=`, `authentication-protocol/password`,
     `encryption-protocol/password` (MD5/SHA1 e DES/AES).
3. Lê de volta e confere community + addresses + SNMP ligado.

Se o equipamento recusar ou a leitura não conferir, **o host não é criado**.
Community que é macro (`{$SNMP_COMMUNITY}`) fica como macro no Zabbix, mas o
equipamento recebe o valor da macro global.

**IP do Zabbix (addresses)**: o da URL da API (DNS resolvido), editável na
prévia. Atenção — achado real: há clientes em que o equipamento hoje libera só
um IP interno (ex.: `172.31.10.2`) enquanto a API está em outro (`170.79.92.46`):
o Zabbix consulta por NAT/rede interna. A prévia avisa quando o IP da API não
está entre os liberados hoje, porque o `addresses` passa a ter **só** o IP
informado e cortaria quem coleta pelo IP antigo.

## Voltagem

Linha **Voltagem** no card (clique abre o histórico na aba "Voltagem" dos
gráficos; a linha de CPU também abre a aba CPU).

- Item de voltagem **do equipamento**: unidade V/mV e "volt"/"tensão" no nome,
  excluindo SFP/PON/módulo/status/limite; leitura fora de 0–300 V é descartada
  (OLT Parks devolve 1195 para 11,95 V). Várias fontes → mostra a primeira e lista
  as outras no tooltip.
- **MikroTik**: nenhum template do Zabbix (oficiais nem "Sinal SFP") traz
  voltagem. O cadastro pelo botão já cria o item `crm.voltagem[mtxrHlVoltage.0]`
  (SNMP `1.3.6.1.4.1.14988.1.1.3.8.0`, ×0,1, 5 min, histórico 30 d). Para MikroTik
  que já estava no Zabbix, o card mostra **Ativar**, que cria o mesmo item
  (rota `zabbix/vinculos/voltagem/ativar/`). Equipamento sem sensor deixa o item
  sem leitura ("sem leitura" no card).

## Temperatura

Linha **Temperatura** no card (mais quente dos sensores do equipamento;
amarelo ≥70 °C, vermelho ≥85 °C) e aba "Temperatura" nos gráficos.
`zabbix_metricas.escolher_temperatura`: "temp" na chave/nome, unidade °C/C/ºC/Cº
(variações reais dos templates) ou vazia; exclui SFP/XFP/QSFP/óptico/DDM/TCV,
PON/GPON/EPON, interface/porta/`x/y/z`, limites (`tempTholdLow`), walks/JSON e
"Tempo de resposta". Prioridade: CPU/MPU/placa/chassi/slot/sensor > genérica >
fonte/ventoinha. Leitura 0 (sem suporte) ou fora de −20…150 °C (ex.: 214748 de
OLT Huawei) é descartada.

## Estado das portas (UP/DOWN)

Bolinha antes do nome em cada linha do **Sinal óptico** e das **Interfaces**:
verde UP, vermelha DOWN, amarela testing/dormant. `zabbix_metricas.status_portas`
lê o status operacional (`ifOperStatus`: 1 up, 2 down, 3 testing, 5 dormant,
6 notPresent, 7 lowerLayerDown) nos formatos reais:

- `net.if.status[ifOperStatus.N]` "Interface X(desc): Operational status" (padrão);
- `ifOperStatus[XGigabitEthernet0/0/1]` "Status da Interface X - desc" (Sartor);
- `datacom.if.status[N]` "Interface X: Operational status".

Ficam de fora status administrativo, ONU, PON, BGP, OSPF, ventoinha, fonte,
corrente, shutdown e os "Status RX/TX" de limiar. O casamento com a linha é pelo
nome da porta sem a descrição (`porta_base`: até o primeiro espaço, "(" ou ":").

**DOWN inferido**: templates com LLD de interfaces filtrado em `{#IFOPER}=^1$`
(caso Sartor, regras "Interfaces Físicas" e "Interfaces Físicas Erros -
Velocidade") só criam item de status para porta que estava UP — porta down nunca
tem status. Sem item de status e **sem luz** (RX ou TX ≤ −40 dBm, piso do
Huawei/Datacom), a linha óptica sai DOWN com `status_inferido` e o tooltip explica.
Com luz e sem item, fica sem bolinha (não dá para afirmar). A correção de verdade
é tirar esse filtro do template no Zabbix do cliente.

**Limiares não são sinal**: itens "High Threshold", "Low Warning", alarmes e
limites do módulo óptico são ignorados no agrupamento (no NE8000 Juína viravam 16
"interfaces" a mais).

## Compatibilidade com Zabbix 5.0/5.2

Até a 5.2 a disponibilidade fica no **host** (`available`, `snmp_available`,
`ipmi_available`, `jmx_available`); da 5.4 em diante, em cada interface. Pedir
`selectInterfaces: ["available"]` num 5.2 devolve `interfaces: [[]]` e o status
quebrava com `'list' object has no attribute 'get'` (Sartor 5.2.7, cliente 37
5.0). `services.status_nodes` e `listar_hosts` escolhem a forma pela versão
(`_avail_no_host`) e interpretam as duas (`_disponibilidade`) — vale também para a
topologia da aba Monitoramento.

Outras diferenças tratadas: grupos via `selectHostGroups` (≥ 6.2) ou
`selectGroups`; proxy por `monitored_by`+`proxyid` (≥ 7.0) ou `proxy_hostid`;
nome do proxy em `name` (≥ 7.0) ou `host`; tags em item só a partir da 5.4.

## Cadastro: trava contra duplicidade e confirmação visível

- As caixas `uiConfirm`/`uiAlert`/`uiToast` do `base.html` ficam dentro de um
  bloco com camada própria e abriam **por trás** do painel "Cadastrar no Zabbix"
  (que fica no `<body>`). `zbxCaixasNoTopo()` move as três para o fim do `<body>`
  ao abrir qualquer janela do Zabbix.
- Com a confirmação escondida, cliques repetidos criaram o host duas vezes no
  Sartor (12125/12126). Agora: `ZC.enviando` trava desde o 1º clique; o servidor
  usa `cache.add('zbx_cad_trava:<acesso>')` (180 s) e recusa acesso que já tem
  vínculo com host.

## Armadilhas conhecidas

- **IP do Zabbix ≠ IP de coleta**: a URL da API pode não ser o IP de onde o Zabbix
  consulta (NAT/rede interna; ex.: equipamento liberando só `172.31.10.2` com a
  API em `170.79.92.46`). A prévia avisa; o `addresses` fica só com o IP informado.
- **Macro global ≠ community do equipamento** (`{$SNMP_COMMUNITY}`=TOMICH com
  equipamento em `green-ro`): a prévia usa a do backup.
- **Worktree sem `media/`**: renderizar a página de um cliente com documento
  anexado ou ler backup a partir do worktree falha/engana — validar pelo
  `/opt/crm`.
- **SNMP no MikroTik não foi validado contra equipamento real** no
  desenvolvimento (SSH aos equipamentos dos clientes bloqueado no ambiente do
  assistente); o parser cobre a saída de `/snmp print` e
  `/snmp community print terse` por testes.
- **Voltagem em RouterOS v7**: o OID `mtxrHlVoltage` pode não responder; o card
  mostra "sem leitura".

## Também corrigido

`services.listar_hosts` tinha `limit: 300` e cortava hosts em Zabbix maiores
(cliente com 355 hosts perdia 55 na aba Monitoramento).

## Próximas fases (não implementadas)

- Clientes sem Zabbix: SNMP pelo proxy SSH (exec de `snmpbulkget` no proxy).
