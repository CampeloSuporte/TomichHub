# Detecção de Loop de Roteamento (RotaLoop) — Documentação Técnica

**Arquivos principais:**
- `clientes/tasks.py` — `_rotaloop_ip_alvo`, `_rotaloop_detectar_loop`, `_rotaloop_mtr_json`,
  `_rotaloop_executar_para_bloco`, `_rotaloop_executar_para_cliente`, `rotaloop_testar_cliente`
  (sob demanda), `rotaloop_verificar_clientes_agendado` (task Celery periódica)
- `clientes/views.py` — `listar_rotaloop_resultados`, `listar_rotaloop_execucoes`,
  `rotaloop_testar_agora`
- `clientes/models.py` — `RotaLoopResultado`, `RotaLoopExecucaoLog`
- `clientes/templates/listar.html` — card "Loop de Roteamento" dentro da aba `tab-vulnerabilidades`
- `crm/celery.py` — agendamento (`rotaloop-verificar-clientes-agendado`)

**Atualizado em:** 11/08/2026

**Ver também:** [AMPSCAN_VARREDURA_AMPLIFICACAO.md](AMPSCAN_VARREDURA_AMPLIFICACAO.md) — mesma aba
Vulnerabilidades, mesmo `BlocoIP` cadastrado na aba RPKI/IRR como fonte de alvos, mas pipeline
independente (não usa o runner Rust do AmpScan).

---

## Visão Geral

Para cada `BlocoIP` cadastrado, testa se o caminho de rede (via `mtr`) até o primeiro IP útil do
bloco (`network_address + 1`) tem um loop de roteamento — o mesmo IP aparecendo mais de uma vez no
traceroute. Loop de roteamento nesse contexto normalmente indica erro de configuração BGP/estática
que faz pacotes ficarem "quicando" entre dois ou mais roteadores até o TTL expirar, sem nunca chegar
ao destino.

## Por que `mtr --json` e não parsing de texto

`mtr` suporta `--json`, que devolve os hops já estruturados (`report.hubs`, cada um com `count` e
`host`) — muito mais confiável que fazer regex em cima da saída colorida/tabular do CLI interativo.
Confirmado funcionando na versão instalada em produção (`mtr 0.95`). Se `mtr` não estiver disponível
no servidor, o teste falha com `status='inconclusivo'` — não há fallback pra `traceroute`/`tracepath`
com parsing de texto livre.

## Critério de loop

O mesmo IP aparecendo em 2 ou mais hops do caminho (não precisa ser consecutivo) —
`_rotaloop_detectar_loop()` em `clientes/tasks.py`. Hops sem resposta (`"???"` no JSON do mtr, viram
`ip=None` internamente) não contam pra detecção. Critério escolhido deliberadamente restritivo (só
IP repetido, não "esgotou os hops sem chegar ao destino") pra evitar falso positivo em blocos cujo
primeiro IP simplesmente não responde a ICMP mas a rota está correta.

## Alvo do teste

Sempre o primeiro IP útil do bloco (`network_address + 1`; blocos `/31`/`/32`/`/127`/`/128` usam o
próprio `network_address`, que é o único endereço disponível). Não há campo de IP de teste
configurável — o objetivo é medir o caminho de rede até a faixa do bloco, não necessariamente obter
resposta de um host real ali dentro.

## Persistência — só loops são gravados

Mesmo padrão do `AmpScanResultado`: `RotaLoopResultado` só ganha uma linha quando
`status == 'loop_detectado'`. Blocos que testam normal não geram registro. Um loop que estava
presente e deixa de aparecer numa execução seguinte é marcado `resolvido=True` (não apagado) —
preserva histórico de quando o problema existiu. IMPORTANTE (ajuste feito na Task 11 depois de um
achado de code review): um loop só é marcado como resolvido quando o bloco foi de fato RETESTADO COM
SUCESSO nessa execução (status `normal` ou `loop_detectado`) e voltou limpo — uma falha transitória
do `mtr` (`status='inconclusivo'`) NUNCA marca um loop anterior como resolvido, pra não dar um falso
"tudo certo" sobre um problema de rede real e não verificado.

## Agendamento — sem revezamento de grupos

Diferente do AmpScan (que revezava clientes em grupos pra não sondar todo mundo no mesmo dia), o
RotaLoop testa **todos** os clientes com blocos IP a cada execução (`crm/celery.py`,
`timedelta(days=2)`). Justificativa: o custo por bloco é 1 execução de `mtr` (poucos segundos), não
milhares de probes como no AmpScan — não há necessidade de fatiar a carga.

## Observabilidade de falhas parciais

`RotaLoopExecucaoLog.sucesso`/`erro_mensagem` refletem quando um ou mais blocos não puderam ser
testados nesta execução (mtr indisponível, timeout, saída malformada, exceção inesperada) —
`sucesso=False` e `erro_mensagem` com a contagem de blocos afetados. Isso é distinto de "nenhum loop
encontrado" (`sucesso=True`, `total_loops_detectados=0`) — a UI e qualquer alerta futuro devem checar
`sucesso` antes de interpretar `total_loops_detectados=0` como "tudo limpo".

---

**Última atualização:** 11/08/2026
