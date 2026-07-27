# Validação RPKI/IRR de Blocos IP — Documentação Técnica

**Arquivos principais:**
- `clientes/views.py` — `executar_validacao_rpki_irr`, `validar_rpki`, `validar_irr`,
  `consultar_lacnic_whois`
- `clientes/tasks.py` — `validar_blocos_rpki_irr_agendado` (task Celery)
- `clientes/models.py` — `BlocoIP`, `ValidacaoRPKI_IRR_Log`

**Atualizado em:** 2026-07-27

---

## Visão Geral

Cada `BlocoIP` cadastrado (com `asn` e `irr_registry` preenchidos) é validado periodicamente contra
duas fontes independentes:

- **RPKI** — o prefixo está coberto por uma ROA (Route Origin Authorization) válida para o ASN
  informado?
- **IRR** — o prefixo está registrado no IRR (ex: LACNIC) e o ASN confere?

O resultado atualiza `rpki_valido`/`rpki_status`/`rpki_mensagem` e
`irr_valido`/`irr_status`/`irr_mensagem` no `BlocoIP`, além de gravar um log em
`ValidacaoRPKI_IRR_Log` a cada rodada. A task `validar_blocos_rpki_irr_agendado` roda diariamente
às 4h via Celery Beat e processa todos os blocos cadastrados.

## Validação RPKI (`validar_rpki`)

Duas fontes, em ordem, com fallback automático:

1. **RIPE Stat** (`https://stat.ripe.net/data/rpki-validation/data.json?resource=AS{asn}&prefix={bloco}`)
   — fonte primária.
2. **Cloudflare RPKI** (`https://rpki.cloudflare.com/api/v1/validity/{asn}/{bloco}`) — fallback,
   usado sempre que a fonte primária falhar por qualquer motivo (erro HTTP, timeout, resposta
   inesperada).

### Correção — timeout do RIPE Stat pulava o fallback (2026-07-27)

**Sintoma:** `RPKI — 2804:7fa0:4000::/34: Error — Timeout ao conectar ao RIPE Stat`, mesmo o
prefixo tendo ROA válida publicada (confirmado manualmente logo em seguida, RIPE Stat respondeu em
< 1s).

**Causa raiz:** `validar_rpki()` tinha um `except requests.exceptions.Timeout` que **retornava
erro imediatamente**, diferente de qualquer outra exceção (`except Exception`), que cai no bloco de
fallback do Cloudflare RPKI logo abaixo. Como o timeout do RIPE Stat é tipicamente pontual/
transitório (confirmado — voltou a responder normalmente segundos depois), esse tratamento
diferenciado descartava, sem necessidade, a segunda fonte que o próprio código já implementa pra
exatamente esse cenário.

**Correção:** o timeout agora cai no mesmo fluxo de fallback que qualquer outra exceção — só chega
ao "Validação RPKI não disponível" final se **as duas** fontes falharem na mesma rodada.

Caso real: bloco `2804:7fa0:4000::/34` (cliente **CALLFRAN**, AS271699) — revalidado manualmente
após o fix, resultado `Valid` (ROA publicada corretamente).

## Validação IRR (`validar_irr`)

Consulta whois do registro informado em `irr_registry` (ex: `whois.lacnic.net:43`) e confere se o
prefixo está registrado (`inetnum`/`inet6num`) com o ASN esperado em `aut-num`. Suporta blocos
agregados (o prefixo cadastrado pode ser mais específico que o bloco publicado no whois).

---

**Última atualização:** 27/07/2026
