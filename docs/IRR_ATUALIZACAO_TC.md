# Atualização de Objetos IRR no TC (bgp.net.br) via API — Documentação Técnica

**Arquivos principais:**
- `clientes/models.py` — `IRRConfig` (config por cliente), `ConfiguracaoSistema` (não usado mais
  neste fluxo — ver histórico abaixo)
- `clientes/views.py` — `_irr_normalizar_rota`, `_irr_gerar_objetos`, `_irr_gerar_corpo`,
  `irr_config_get`, `irr_config_salvar`, `irr_preview`, `irr_enviar`, `irr_consultar_whois`
- `clientes/urls.py` — rotas `<cliente_id>/irr/*`
- `clientes/templates/listar.html` — aba RPKI/IRR, card "Atualização IRR — TC" (HTML ~linha 504,
  JS ~linha 5026); linhas dinâmicas de rota (`_irrRotaRow`, `irrRenderRotas`, `irrMergeRotas`,
  `irrGetRotas`) ~linha 5200
- `clientes/migrations/0102_irrconfig_add_api_key.py`
- `clientes/migrations/0103_irr_rotas_com_descr_member_of.py` — migra `ipv4_rotas`/`ipv6_rotas` de
  lista de strings pra lista de dicts

**Atualizado em:** 10/08/2026

**Ver também:** [RPKI_IRR.md](RPKI_IRR.md) — validação periódica (diferente deste fluxo, que é de
**envio/atualização** de objetos, não de checagem).

---

## Visão Geral

Cada cliente pode ter um `IRRConfig` (ASN, dados de mntner/person, prefixos IPv4/IPv6, AS-sets de
upstream/customer, participação em IX). A partir dele o CRM monta os objetos RPSL necessários
(`person`, `mntner`, `route-set`, `route`/`route6`, `as-set` ×3, `aut-num`) e os envia para o
registro IRR do TC (bgp.net.br), que é quem de fato distribui essas rotas para os outros IRRs/RPKI
relying parties consultarem.

### Antes: envio por e-mail (histórico)

Até 04/08/2026 o envio era feito por SMTP puro para `auto-dbm@bgp.net.br`, no formato tradicional
de e-mail IRR (`password:` + objetos RPSL separados por linha em branco). Como esse canal é
assíncrono, o CRM também tinha uma tela de "Verificar Resposta" que fazia IMAP na caixa de entrada
configurada em `ConfiguracaoSistema`, buscando e-mails de `nic.br` com "IRR" no assunto nos últimos
30 dias e tentando extrair `ACCEPTED`/`REJECTED` por objeto via parsing textual ingênuo do corpo.

### Agora: API HTTP síncrona

Desde a versão 4.2 do IRRd, o TC expõe `POST https://bgp.net.br/v1/submit/` (documentado em
https://irrd.readthedocs.io/en/stable/users/database-changes.html), que aceita os mesmos objetos
RPSL só que em JSON, autentica com a mesma senha do mntner (ou uma API key, para mntners migrados)
e **responde de forma síncrona**, com o resultado de cada objeto individualmente — sem precisar de
e-mail, IMAP ou espera de até 30 dias.

`irr_enviar` (`clientes/views.py`) faz esse POST e devolve ao frontend, na mesma resposta:

```json
{
  "ok": true,
  "status_geral": "sucesso|parcial|erro",
  "resumo": {"objects_found": 16, "successful": 14, "failed": 2, ...},
  "aceitos":    ["person: JOLJE19-NICBR", "mntner: MAINT-AS272418", ...],
  "rejeitados": ["route: 186.65.76.0/24AS272418", ...],
  "erros":      ["route: 186.65.76.0/24AS272418 — RPKI ROAs were found that conflict...", ...],
  "objetos":    [/* resposta bruta da API, por objeto */],
  "mensagem":   "14 objeto(s) aceito(s), 2 falha(s)."
}
```

O modal de resultado (`irrMostrarResultado` em `listar.html`) mostra isso na hora — não existe
mais um passo separado de "verificar depois"; a view `irr_verificar_resposta` (IMAP) e o botão
correspondente foram removidos por ficarem obsoletos.

## Formato do payload enviado

```json
{
  "objects": [
    {"object_text": "person:  Nome...\naddress: ...\n...\nsource:  TC\n"},
    {"object_text": "mntner:  MAINT-AS...\n...\n"},
    ...
  ],
  "passwords": ["<senha do mntner, em texto plano>"],
  "api_keys":  ["<opcional, só se IRRConfig.api_key estiver preenchida>"]
}
```

`_irr_gerar_objetos(cfg)` monta a **lista** de objetos (um item = um objeto RPSL completo); é
reaproveitada tanto pelo preview (`irr_preview`, que só junta tudo com `password:` no topo pra
leitura humana) quanto pelo envio real via API.

## Campo `IRRConfig.api_key`

Opcional. Mntners "migrados" no TC podem gerar uma API key pela interface web deles, usada como
alternativa (ou complemento) à senha. O CRM manda os dois campos (`passwords` e `api_keys`) sempre
que estiverem preenchidos — a API considera qualquer um válido.

---

## Correções feitas nesta implementação (05/08/2026)

### 1. `members: #` inválido no as-set `AS-CUSTOMERS` vazio

**Sintoma:** timeout de 30s (`Read timed out`) ao enviar a atualização de um cliente sem nenhum
`customer_asns` cadastrado.

**Causa raiz:** quando não havia clientes downstream, o código antigo emitia
`members: #` como placeholder — `#` é o marcador de comentário em RPSL, não um valor válido. Esse
as-set vazio é referenciado por outro (`AS-<nome>` → `members: AS<asn>:AS-CUSTOMERS`), e o conjunto
malformado provavelmente travava a validação/expansão do as-set no lado do TC.

**Correção:** `members` é opcional no `as-set` (RFC 2622) — a linha agora é simplesmente omitida
quando não há clientes, em vez de usar um valor inválido.

**Nota:** reproduzido e corrigido, mas o mesmo cliente **ainda deu timeout depois desse fix** — ou
seja, esse bug não era a única causa (ver item 2).

### 2. Timeout de 30s insuficiente para submissões reais

**Sintoma:** mesmo após o fix acima, o mesmo cliente (AS272418) voltou a estourar em exatos 30s.

**Causa raiz:** a API do TC responde rejeições de payload inválido/malformado quase
instantaneamente (confirmado testando objetos de teste isolados, < 1s), mas uma submissão real —
objetos válidos, senha correta, criando/alterando registros de fato — legitimamente demora mais
(ficou perto ou acima de 30s em produção).

**Correção:** timeout do `requests.post` subiu de 30s para 100s (o worker gunicorn aguenta até
120s, `--timeout 120` no systemd unit), com aviso na UI de que pode levar 1-2 minutos. Depois desse
ajuste, uma submissão real do mesmo cliente completou com sucesso (14 aceitos, 2 rejeitados por
conflito de ROA RPKI — ver item 3, não é mais timeout).

### 3. Explicação de conflito de ROA RPKI no resultado

**Contexto:** não é bug — é o IRRd do TC rejeitando corretamente um `route`/`route6` porque já
existe uma ROA RPKI válida para aquele prefixo que não autoriza o anúncio sendo enviado (origem
diferente, ou `max-length` mais restritivo que o prefixo submetido). Exemplo real: ROA existente
para `186.65.76.0/23` com `max-length /23` — o agregado é aceito, mas os `/24` dentro dele
(`186.65.76.0/24`, `186.65.77.0/24`) são rejeitados por serem mais específicos do que a ROA
autoriza.

**O que foi adicionado:** quando uma mensagem de erro da API contém `"rpki"` e `"roa"`
(case-insensitive), o modal de resultado (`irrExplicarErro` em `listar.html`) mostra uma caixa
explicando, em português, que o problema é uma inconsistência entre a ROA já cadastrada e o objeto
`route` sendo enviado — e que a correção é ajustar a ROA (max-length ou origem) no gerenciador RPKI
(ex: LACNIC), não no CRM.

Aproveitado o mesmo commit pra escapar (`irrEsc`) o texto vindo da API externa antes de injetar via
`innerHTML` nas listas de aceitos/rejeitados/erros — essas strings vêm de fora (resposta do TC) e
não devem ser tratadas como HTML confiável.

---

## Campos `descr` e `member-of` por rota (10/08/2026)

**Motivação:** cada objeto `route`/`route6` no IRR pode ter sua própria `descr:` e seu próprio
`member-of:` (route-set) — em clientes com prefixos de origens/route-sets diferentes (ex: um AS
que anuncia blocos de operadoras distintas agregadas sob route-sets separados, como
`RS-GOODNET-NORTE`, `RS-CALLFRAN-NORTE`, `RS-GSSNET-NORTE`), um único valor global não é
suficiente. Antes desta mudança, `descr`/`member-of` eram sempre globais (`IRRConfig.empresa_descr`
e uma route-set fixa `AS<asn>:RS-ROUTES`, hardcoded).

**Modelo:** `IRRConfig.ipv4_rotas`/`ipv6_rotas` deixam de ser `list[str]` (só o prefixo) e passam a
ser `list[dict]`: `{"prefix": "...", "descr": "...", "member_of": "..."}`. `descr`/`member_of` são
opcionais — quando vazios, `_irr_gerar_objetos` (`clientes/views.py`) cai no padrão global
(`empresa_descr` / `AS<asn>:RS-ROUTES`), então configs antigas continuam funcionando sem edição.
`_irr_normalizar_rota` aceita tanto o formato novo (dict) quanto o legado (string = só prefixo), por
segurança — mas a migração `0103_irr_rotas_com_descr_member_of` já converte todos os registros
existentes pra dict na aplicação.

**UI (`clientes/templates/listar.html`, aba Rotas):** os antigos `<textarea>` de "um prefixo por
linha" viraram listas de linhas dinâmicas (mesmo padrão visual dos upstreams/customers de AS-Sets),
com 3 campos por rota: prefixo, descr (opcional) e member-of (opcional).

**Consulta WHOIS (`irr_consultar_whois`) também traz `descr`/`member-of` por rota:**
`parse_route_objects` deixou de extrair só o prefixo — agora segmenta o retorno do whois por objeto
RPSL completo (cada bloco `route:`/`route6:` até a linha em branco seguinte) e captura
`descr`/`member-of`/`source` de cada um.

**🐛 Fix real (mesmo dia): duplicata de objeto route por prefixo confundia o preenchimento.** Um
mesmo prefixo pode aparecer em *mais de um* objeto no whois — o registro real (`source: TC`, o
mesmo que este sistema gerencia), uma versão auto-gerada a partir do RPKI (`source: RPKI`,
`descr: "RPKI ROA for ..."`) e, às vezes, um terceiro registrado por outro mantenedor via RADB (ex:
`descr: "Customer AS271699"`, sem `member-of`). A primeira versão do parser devolvia todos esses
objetos soltos, e a lógica de front-end pegava o primeiro que aparecesse — que nem sempre era o
correto (reproduzido com o cliente CALLFRAN/AS271699: um dos 4 prefixos vinha com a `descr`
genérica do objeto RADB em vez de `"ANTONIO CLAUDIO"`). Corrigido deduplicando por prefixo dentro de
`parse_route_objects`, sempre priorizando o objeto com `source: TC` quando existir.

**🐛 Fix real (mesmo dia): preenchimento da consulta não substituía rota já salva sem
descr/member-of.** `irrConsultarWhois` só preenchia as rotas quando a lista na tela estava
*totalmente vazia* — clientes com config já salva (rotas migradas do formato antigo, sem
descr/member-of) nunca recebiam esses campos, mesmo clicando em "Consultar IRR" de novo. Substituído
por `irrMergeRotas`, que casa cada rota do whois com a linha existente **pelo prefixo** e completa
só os campos vazios (sem sobrescrever edição manual já feita na tela); prefixo novo que ainda não
tem linha é adicionado ao final.

---

## Fluxo de uso (UI)

1. Aba **RPKI/IRR** → card **"Atualização IRR — TC"** → sub-abas **Dados Gerais / Rotas / AS-Sets**.
2. **Consultar IRR** — puxa o estado atual via whois público (`whois.nic.br`/`irr.nic.br`, porta 43)
   pra pré-popular o formulário, se o cliente já tiver algo registrado.
3. **Salvar Configuração** em cada sub-aba.
4. **Preview dos Objetos** — mostra o texto RPSL completo que será enviado (sem senha visível fica
   junto do preview pra referência manual, se precisar copiar/colar em outro lugar).
5. **Enviar Atualização (API)** — salva o formulário, faz o POST real pra
   `https://bgp.net.br/v1/submit/` e mostra o resultado (aceitos/rejeitados/erros) na hora, no
   mesmo modal.

## Limitações conhecidas

- Conflitos de ROA RPKI (ver item 3 acima) exigem ação do cliente no gerenciador RPKI dele — o CRM
  só explica o erro, não corrige a ROA.
- `irr_consultar_whois` continua usando socket raw na porta 43 (protocolo whois tradicional) — não
  foi migrado pra API porque a consulta pública já funciona bem e a API do TC é focada em
  **submissão**, não em leitura.

---

**Última atualização:** 10/08/2026
