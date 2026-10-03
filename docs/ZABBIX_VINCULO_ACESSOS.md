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
| Óptico | unidade dBm/!dBm/µW/mW ou nome "RX/TX Power"; agrupado por interface; multilane com média calculada → só a média | µW convertido para dBm; porta sem módulo (tudo 0) fica oculta por padrão |

Desempenho: a descoberta de quais itens são CPU/uptime de cada host é uma busca
filtrada (`search` em chave/nome) guardada por 30 min; a cada 60s o status só
lê o `lastvalue` desses itens. A janela de gráficos lista todos os itens do host
(cache de 2 min) e busca o histórico **em série** — em paralelo as requisições
disputam o mesmo túnel SSH do proxy e estouram o timeout de 15s (medido).
Janelas acima de 3 dias usam `trends` (médias horárias). O servidor só devolve
histórico de itens do host vinculado ao acesso pedido.

Rotas: `zabbix/vinculos/itens/?acesso_id=` e
`zabbix/vinculos/historico/?acesso_id=&itemids=1,2&periodo=1h|6h|24h|7d|30d`.

## Também corrigido

`services.listar_hosts` tinha `limit: 300` e cortava hosts em Zabbix maiores
(cliente com 355 hosts perdia 55 na aba Monitoramento).

## Próximas fases (não implementadas)

- Cadastrar o host no Zabbix pela API (`host.create`), com template por
  Função/Modelo, tag `crm_acesso_id` e Zabbix proxy.
- Clientes sem Zabbix: SNMP pelo proxy SSH (exec de `snmpbulkget` no proxy).
