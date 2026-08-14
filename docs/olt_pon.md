# Portas PON de OLT Huawei — diagnóstico e laser pela topologia

**Arquivos principais:**
- `clientes/olt_pon.py` — parser do inventário, geração dos comandos e execução
- `clientes/views.py` — endpoints `olt_pon_acesso` e `olt_pon_executar`
- `static/js/topo_main.js` — modal "Portas PON" (`mostrarPon` e seguintes)
- `clientes/templates/topologia_editor.html` — CSS `.pon-*`
- `clientes/models.py` — `AcaoOltPon` (auditoria), migração `0109_acao_olt_pon`

**Adicionado em:** 2026-08-14

---

## Visão Geral

No painel de propriedades de um host **OLT** da topologia aparece o botão
**"Portas PON"**. Ele abre um modal que mostra, lido do **backup mais recente**
do equipamento, as placas PON do chassi e a grade de portas de cada uma — com
quantas ONTs estão em cada porta e quem são elas.

Selecionada uma porta, dá pra disparar no equipamento:

| Ação | Comando | Peso |
|---|---|---|
| Informações da porta | `display port info <porta>` | leitura |
| Estado da porta | `display port state <porta>` | leitura |
| Desativar porta (laser off) | `port <porta> laser-switch off` | **escrita — derruba a porta** |
| Ativar porta (laser on) | `port <porta> laser-switch on` | escrita |

O laser **é** o liga/desliga da porta PON — por isso o rótulo fala do efeito
("desativar a porta"), não do comando, e por isso **cada porta da grade tem o
próprio botão de desativar** (o ícone de power no canto do quadradinho), sem
precisar selecionar a porta antes. O botão não executa nada sozinho: ele abre o
preview daquela porta, que ainda exige a confirmação explícita.

Os três comandos são os do treinamento oficial de MA5800 (Aula 7 — comandos
úteis) e rodam **dentro do modo de configuração da placa PON**, que é o que o
`interface gpon <frame/slot>` abre.

## Por que o inventário vem do backup

Mesma escolha do L2VPN: abrir a tela **não pode** custar uma conexão SSH numa
OLT de produção. O backup diário destes equipamentos já traz a
`display current-configuration` inteira, e dela sai tudo que a tela precisa:

```
 board add 0/1 H903GPSF          ← que placa está em cada slot
 ...
 interface gpon 0/1
  port 0 ont-auto-find enable    ← porta configurada
  port 3 range min-distance 0 max-distance 20
  ont add 0 12 sn-auth "GPON00B449B5" ... desc "adilson@pereira"
                                 ↑ porta   ↑ ONT id            ↑ assinante
```

O equipamento só é tocado quando o operador dispara uma ação — e aí com preview
editável antes, igual às outras automações.

### Quantas portas tem cada placa

O `board add` diz o modelo da placa, e o modelo diz o número de portas. Tabela
levantada dos 18 backups de OLT Huawei deste ambiente:

| Família | Portas | Exemplos vistos |
|---|---|---|
| `GPBD`, `GPBH` | 8 | H805GPBD, H806GPBD, H807GPBH |
| `GPSF`, `GPLF`, `GPHF`, `GPUF`, `GPFD`, `CGHF`, `FLSF` | 16 | H903GPSF, H901GPHF, H902GPUF |

A regra tem dois ramos, e a diferença entre eles já custou um incidente:

- **Tipo conhecido** → a família manda (uma GPBD tem 8 portas mesmo com só 3
  configuradas), mas nunca menos do que o backup mostra: uma porta além do
  previsto entra do mesmo jeito (`max(indice)+1`).
- **Tipo desconhecido** (sem `board add` para aquele slot) → vale **só o que o
  backup prova**, `max(indice)+1`, sem nenhum padrão. A placa vem marcada com
  `portas_inferidas: true` e o painel mostra o aviso *"portas vistas no
  backup"*: se a placa física tiver mais portas, elas não aparecem ali.

Esconder uma porta que existe é errado; **oferecer uma que não existe é pior** —
manda comando para um alvo inexistente. Por isso o ramo desconhecido erra para
menos.

## O raio de alcance do laser-switch

`port N laser-switch off` **apaga o laser da porta** — todas as ONTs penduradas
nela ficam sem sinal até o laser voltar. Por isso o inventário conta as ONTs por
porta e o número acompanha a operação em três lugares:

1. **na grade**, como número embaixo do índice da porta (e a cor: cinza = sem
   configuração, verde = com ONT, ciano = 50+) — e no `title` do botão de
   desativar de cada porta, que já diz quantas ONTs ela derruba;
2. **no preview**, num aviso vermelho — *"Apagar o laser da porta 0/1/4 derruba
   29 ONTs"* — com os nomes/SNs das primeiras ONTs listados logo acima;
3. **na auditoria** (`AcaoOltPon.onts_afetadas`), congelado no momento da ação:
   dá pra medir o impacto depois mesmo que o backup tenha mudado.

Ligar/desligar laser é **uma porta por vez** (`validar_alvo` recusa mais de
uma) e passa pelo preview editável + confirmação explícita.

**Consulta não passa por nada disso.** `display port info/state` vai direto ao
equipamento e mostra só o retorno — o comando é fixo, não muda nada e não há o
que revisar; um textarea de comandos entre a pergunta e a resposta seria só um
passo a mais. E confirmação em leitura teria o efeito contrário do desejado:
ensinaria a clicar "sim" no automático, que é justamente o reflexo que não pode
existir na hora de desativar uma porta. O preview continua valendo **só** para
as ações de escrita.

## O preâmbulo dos comandos

Todo envio começa com:

```
enable
config
undo interactive
undo smart
scroll
interface gpon 0/1
```

Não é enfeite — é o mesmo preâmbulo do template **"backup olt huawei"** que roda
todo dia nesses 22 acessos:

- `undo interactive` tira os prompts de confirmação (é o que faz o
  `laser-switch` não parar esperando um `y`);
- `undo smart` desliga o autocomplete, que suja a saída;
- `scroll` desliga a paginação — sem isso o `display port info` vem cortado no
  `---- More ----`;
- `interface gpon <slot>` entra no modo da placa, onde os três comandos existem.

A sessão nasce zerada a cada execução, então o preâmbulo vai junto sempre.

## Execução: shell Paramiko, não Netmiko

Diferente do BGP e do L2VPN, que usam Netmiko, aqui a execução reaproveita
`views._executar_comandos_huawei` — o mesmo shell Paramiko (terminal de 10000
colunas + detecção de silêncio) do backup diário destas OLTs.

Motivo: o driver `huawei_vrpv8` do Netmiko briga com o prompt do MA5800, que
troca de `MA5800-X7#` para `MA5800-X7(config-if-gpon-0/1)#` conforme o modo — e
é exatamente essa troca de contexto que o `interface gpon` provoca. O shell cru
já roda todo dia nestes 18 equipamentos, então é o caminho provado.

A conexão respeita IP privado do mesmo jeito que o backup: ProxyServer ativo do
cliente vira túnel SSH; sem proxy, confere se uma VPN WireGuard cobre o host;
sem nenhum dos dois, recusa com mensagem em vez de tentar e travar.

## Detecção: só OLT Huawei

69 acessos deste ambiente têm `interface gpon` no backup — mas 51 são ZTE,
Datacom ou Parks, com CLI completamente diferente. `eh_olt_huawei` exige a
assinatura Huawei (`board add` ou prompt `MA5xxx`) **além** do bloco GPON.
Validado contra os backups reais: 18 detectados, 0 falso positivo, 0 falso
negativo.

O botão no painel de propriedades aparece quando o node é OLT — pelo tipo do
ícone (`olt`), pela função cadastrada no CRM ou pelo nome do host (`OLT`,
`MA5800`). Um host que não for OLT Huawei recebe a explicação no modal em vez
de um erro seco.

## Endpoints

| Método | URL | O que faz |
|---|---|---|
| `GET` | `/clientes/acessos/<id>/olt-pon/` | Inventário de placas/portas do backup mais recente |
| `POST` | `/clientes/acessos/<id>/olt-pon/executar/` | `preview=true` monta os comandos; `preview=false` executa e audita |

```jsonc
// POST body
{"acao": "info|state|laser_off|laser_on", "slot": "0/1", "portas": [4],
 "preview": false, "comandos": ["...", "..."]}   // comandos = texto revisado no modal
```

### Cache

O inventário é cacheado por **id do BackupLog** (`pon:inv:v1:<log_id>`, TTL 6h):
o conteúdo de um backup nunca muda depois de gravado, então só um backup novo
invalida. `_PON_CACHE_VERSAO` entra na chave — **suba junto com qualquer
mudança em `parse_pon`**, senão o painel serve o parse antigo por até 6h.

### Recusas (`OltPonNaoSuportado`)

- placa que não existe no backup (`interface gpon 0/9` num chassi que vai até
  0/5 entraria no modo de configuração de um slot vazio, e o comando seguinte
  rodaria em contexto errado);
- porta além do número de portas da placa;
- laser em mais de uma porta de uma vez;
- ação desconhecida;
- texto editado no modal com mais de 40 linhas ou linha acima de 200 colunas.

## Permissão e auditoria

Mesma régua do clone de L2VPN: `is_backoffice` + ferramenta `topologia`
habilitada + posse do cliente. Mexer em porta de OLT é engenharia de rede, não
função de portal de cliente.

Toda execução grava `AcaoOltPon` (usuário, ação, placa/portas, ONTs afetadas,
se foi escrita, comandos enviados, output e status). As ações de escrita ainda
saem no log da aplicação em nível `warning`, com o número de ONTs.

## Incidente — porta inventada e "sucesso" mentiroso (2026-08-14)

Primeira operação real de `laser-switch` em produção, na OLT-HU-LEAL, placa
`0/1`, porta 8. O equipamento respondeu:

```
port 8 laser-switchoff
                                    ^
  % Parameter error, the error locates at '^'
```

E a auditoria registrou **`status: sucesso`**. Dois defeitos distintos, os dois
meus:

**1. O painel ofereceu uma porta que não existe.** O backup dessa OLT não tem
`board add 0/1` — a placa foi confirmada em campo (`board confirm`) e por isso
não entra no bloco `[pre-config]`. Sem o tipo, o código assumia o padrão de 16
portas e desenhava as portas 8–15. A informação certa estava no próprio backup o
tempo todo: as linhas `port 0` a `port 7` provam uma placa de 8. Confirmado ao
vivo no equipamento com um comando inofensivo — `display port state 8` devolve o
mesmo `% Parameter error`, e `display port state 7` responde normal.

O eco `laser-switchoff` (sem o espaço) despistou: parecia corrupção de
transmissão, mas era só como o VRP redesenha a linha ao apontar o erro. Os
`display` da mesma sessão chegaram intactos.

**2. Conexão sem exceção virou "sucesso".** O `executar` só olhava se o Paramiko
estourou; o VRP recusa comando **no texto** e segue no prompt, então a recusa
passava batido. Numa ação destrutiva isso é o pior tipo de bug silencioso: o
operador sai achando que desativou a porta e ela continua no ar (ou, na direção
oposta, acha que religou e não religou).

Hoje `detectar_erro_cli` varre a saída atrás da recusa (`% Parameter error`,
`% Unknown command`, `Failure:` e afins — o `%` sozinho não serve de gatilho,
aparece em percentual de saída legítima), o status vira `erro` e o painel mostra
a linha exata do equipamento com um "nada foi alterado na porta". A migração
`0110_corrige_status_acao_olt_pon` reavaliou os registros já gravados: os dois
`laser_off` recusados passaram de `sucesso` para `erro`.

Impacto do ajuste de portas em toda a base: das 61 placas PON das 18 OLTs
Huawei, **1** tinha o tipo desconhecido — justamente a que falhou, agora com 8
portas em vez de 16. Nenhuma ficou sem portas.

## Limitações conhecidas

- **Só Huawei MA5600T/MA5800.** ZTE, Datacom e Parks têm CLI própria e ficam de
  fora até haver config real conferida.
- O inventário é a foto do **último backup**: uma ONT cadastrada hoje de manhã
  aparece depois do próximo backup. O `display` ao vivo não tem esse atraso.
- `laser-switch` não aparece na running-config (é estado operacional), então o
  painel **não** mostra se o laser de uma porta está apagado — quem responde
  isso é o `display port state`.
- Uma ação por vez: não há fila nem execução em lote entre placas.
- Placa sem `board add` no backup só mostra as portas que aparecem
  configuradas — uma porta física vazia e nunca tocada fica de fora até a
  primeira configuração (ou até o `board add` existir no backup).
