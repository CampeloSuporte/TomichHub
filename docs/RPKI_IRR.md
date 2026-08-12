# Validação RPKI/IRR de Blocos IP — Documentação Técnica

**Arquivos principais:**
- `clientes/views.py` — `executar_validacao_rpki_irr`, `validar_rpki`, `validar_irr`,
  `consultar_lacnic_whois`, `listar_blocos_cliente` (AJAX da aba)
- `clientes/tasks.py` — `validar_blocos_rpki_irr_agendado` (task Celery)
- `clientes/models.py` — `BlocoIP`, `ValidacaoRPKI_IRR_Log`
- `clientes/templates/listar.html` — bloco `<script>` da aba RPKI/IRR (painéis e helpers de fetch)

**Atualizado em:** 2026-08-12

**Ver também:** [IRR_ATUALIZACAO_TC.md](IRR_ATUALIZACAO_TC.md) — envio/atualização de objetos IRR
pro TC via API (fluxo diferente deste: aqui é só validação/checagem periódica).

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

## Painel da aba RPKI/IRR (frontend)

Os painéis da aba (Blocos IP, Vulnerabilidades/AmpScan, RotaLoop e configuração de IRR) vivem no
bloco `<script>` de `clientes/templates/listar.html` e carregam por AJAX. Todos passam pelos
helpers descritos abaixo — **não use `response.json()` cru em fetch novo dessa aba.**

| Helper | Para quê |
|--------|----------|
| `rpkiSessaoExpirou(response)` | Detecta o redirect de login (`response.redirected` + path `/auth/login/`) |
| `rpkiJson(response)` | Substitui `response.json()`: sinaliza sessão expirada e propaga `{'error': ...}` de 403/500 |
| `rpkiPainelErro(error, texto, funcao)` | HTML do estado de erro do painel — troca "Tentar Novamente" por "Fazer Login" quando é sessão |
| `rpkiToastErro(error, texto)` | Mesma distinção, para as ações que respondem via toast |
| `irrChecarSessao(response)` | Só traduz o redirect de login, preservando o contrato `{ok, erro}` das telas de IRR |

### Correção — "Erro ao carregar blocos IP" era sessão expirada (2026-08-12)

**Sintoma:** a aba RPKI/IRR abria direto no estado de erro, com "Erro ao carregar blocos IP" e um
botão "Tentar Novamente" que nunca resolvia — o usuário clicou 8 vezes seguidas.

**Causa raiz:** quando a sessão cai, `@login_required` responde ao AJAX com **302 para
`/auth/login/`**. O `fetch()` segue o redirect, recebe o HTML da tela de login e o
`response.json()` estoura com `Unexpected token '<'`, caindo no `.catch()` genérico. O painel
culpava o carregamento dos blocos por um problema que era de autenticação. Confirmado no log do
gunicorn — 9 respostas 302 contra 2 de 200 no mesmo período:

```
"GET /clientes/blocos/listar/?id=90" 302 0
"GET /auth/login/?next=/clientes/blocos/listar/%3Fid%3D90" 200 12566
```

**Correção:** os fetches da aba passaram a usar `rpkiJson`/`irrChecarSessao`. Sessão expirada agora
mostra "Sua sessão expirou. Faça login novamente" com botão **Fazer Login** (recarrega a página, o
que leva ao login preservando o `next`), em vez do retry inútil.

**Dois bugs a mais corrigidos junto:**

1. **403 mentia.** `Sem permissão` (403) e `Cliente não especificado` (400) devolvem
   `{'error': ...}`, que fazia `data.blocos` virar `undefined` — e o painel exibia *"Nenhum bloco IP
   cadastrado"*, como se o cliente não tivesse bloco nenhum. Agora `rpkiJson` levanta a mensagem
   real do servidor.
2. **Polls infinitos.** AmpScan e RotaLoop têm `setInterval` de 4s enquanto a varredura roda. Sem
   `clearInterval` no catch, com a sessão caída eles ficavam batendo na tela de login para sempre.

### Por que a sessão cai sem timeout — sessão única por usuário

A sessão é de 1h **deslizante** (`SESSION_COOKIE_AGE = 3600` com `SESSION_SAVE_EVERY_REQUEST =
True`), então quem está usando o sistema não deveria ser deslogado. O que derruba é
`_force_single_session` em `home/apps.py`, ligado ao sinal `user_logged_in`:

> Ao fazer login, encerra todas as outras sessões ativas do mesmo usuário.

Ou seja: **todo login mata as outras sessões daquela conta**. Com a mesma conta aberta em mais de
um navegador ou máquina, quem loga por último derruba os demais — e os outros só percebem quando
algum AJAX falha. É comportamento intencional, mas é a origem mais comum do "erro" acima.

⚠️ Isso vale também para verificação automatizada: `Client.force_login()` dispara `user_logged_in`
e **desloga o usuário de verdade**. Não use conta real de produção para testar renderização de
página.

---

**Última atualização:** 12/08/2026
