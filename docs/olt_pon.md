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
| Desligar laser | `port <porta> laser-switch off` | **escrita — derruba a porta** |
| Ligar laser | `port <porta> laser-switch on` | escrita |

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

É só palpite de exibição: se o backup mostrar uma porta além do previsto, o
`max(indice)+1` manda e a placa continua completa. Placa desconhecida assume 16.

## O raio de alcance do laser-switch

`port N laser-switch off` **apaga o laser da porta** — todas as ONTs penduradas
nela ficam sem sinal até o laser voltar. Por isso o inventário conta as ONTs por
porta e o número acompanha a operação em três lugares:

1. **na grade**, como número embaixo do índice da porta (e a cor: cinza = sem
   configuração, verde = com ONT, ciano = 50+);
2. **no preview**, num aviso vermelho — *"Apagar o laser da porta 0/1/4 derruba
   29 ONTs"* — com os nomes/SNs das primeiras ONTs listados logo acima;
3. **na auditoria** (`AcaoOltPon.onts_afetadas`), congelado no momento da ação:
   dá pra medir o impacto depois mesmo que o backup tenha mudado.

Ligar/desligar laser é **uma porta por vez** (`validar_alvo` recusa mais de
uma) e exige um segundo clique de confirmação. Consulta não exige — exigir
confirmação pra ler o estado de uma porta só ensinaria a clicar "sim" no
automático, que é justamente o reflexo que não pode existir no laser.

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

## Limitações conhecidas

- **Só Huawei MA5600T/MA5800.** ZTE, Datacom e Parks têm CLI própria e ficam de
  fora até haver config real conferida.
- O inventário é a foto do **último backup**: uma ONT cadastrada hoje de manhã
  aparece depois do próximo backup. O `display` ao vivo não tem esse atraso.
- `laser-switch` não aparece na running-config (é estado operacional), então o
  painel **não** mostra se o laser de uma porta está apagado — quem responde
  isso é o `display port state`.
- Uma ação por vez: não há fila nem execução em lote entre placas.
