# Geolocalização de IP — Consulta, Correção e Geofeed Público (RFC 8805)

**Data de Implementação:** 2026-05-17 (base) · **2026-07-26** (múltiplos blocos/localizações)
**Arquivo principal:** `home/views.py`, `home/templates/geo_consulta.html`
**Status:** ✅ Produção

---

## Visão Geral

Ferramenta em **Ferramentas → Geolocalização de IP** (`/homeferramentas/geo/`) que:

1. Consulta um IP/prefixo em 6 bancos de geolocalização em paralelo e mostra consenso/divergência.
2. Gera solicitações de correção de geolocalização (MaxMind, LACNIC, ARIN) a partir do resultado da consulta.
3. Publica um **Geofeed público RFC 8805** (`geofeed.csv`) — arquivo sem autenticação, referenciado no
   objeto `inetnum`/NET do RIR (campo `geofeed:`) e consumido automaticamente por bancos de geolocalização.
4. (Novo) Permite cadastrar **múltiplos blocos IP e localizações** diretamente, sem depender do fluxo de
   consulta + correção de um IP por vez.

---

## Arquitetura de Dados

Dois models no app `clientes`, com responsabilidades separadas:

### `CorrecaoGeoIP` — histórico de solicitações (auditoria)

Registra cada envio do formulário de correção: prefixo, país/região/cidade/org/lat/lon informados,
quais destinos (MaxMind Geo, MaxMind ISP/Org, LACNIC, ARIN) receberam a solicitação, resposta IMAP dos
RIRs e verificação posterior de aplicação. É **só histórico** — não é mais lido pelo `geofeed.csv`.

### `GeofeedBloco` — fonte única do Geofeed público (novo, 2026-07-26)

```python
class GeofeedBloco(models.Model):
    prefixo      = models.CharField(max_length=50, unique=True, db_index=True)
    pais         = models.CharField(max_length=5, blank=True)
    regiao       = models.CharField(max_length=100, blank=True)
    cidade       = models.CharField(max_length=100, blank=True)
    postal_code  = models.CharField(max_length=20, blank=True)
    ativo        = models.BooleanField(default=True)
    criado_em    = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    criado_por   = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True)
```

`geo_geofeed_csv` lê **só** `GeofeedBloco.objects.filter(ativo=True)`. Um prefixo = uma linha no CSV,
com `unique=True` garantindo que não há duplicata (antes o dedup era feito em Python pegando o registro
mais recente por prefixo dentro do histórico `CorrecaoGeoIP`, o que era frágil e não permitia edição/remoção
de um bloco já publicado).

Migração `0093_geofeed_bloco_migrar_historico` populou `GeofeedBloco` a partir do prefixo mais recente
de cada `CorrecaoGeoIP` já existente, para o conteúdo do `geofeed.csv` público não regredir no deploy.

---

## Por que existia só 1 bloco por vez (antes de 2026-07-26)

O fluxo original era: buscar 1 IP → abrir modal de correção pré-preenchido com o consenso → enviar.
`geo_atualizar` recebia um único conjunto de campos (`prefixo`, `pais`, `regiao`, `cidade`, ...) e criava
um único `CorrecaoGeoIP`. Não havia tela de gerenciamento — só era possível "acumular" prefixos no
geofeed repetindo manualmente o fluxo completo de busca + modal para cada bloco, um de cada vez, e não
havia como remover ou editar um bloco já publicado sem mexer direto no banco.

---

## Blocos do Geofeed — cadastro de múltiplos blocos/localizações

Nova seção na própria tela (`home/templates/geo_consulta.html`, card "Blocos do Geofeed"), entre o painel
"Geofeed Público" e o "Histórico de Correções": uma tabela editável, com botão **+ Adicionar bloco** que
insere uma nova linha em branco (Prefixo, País, Região, Cidade, Postal Code, Ativo). Cada linha tem botões
próprios de **Salvar** e **Remover** — não é um form único, cada bloco é independente.

### Endpoints

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/homeferramentas/geo/blocos/` | GET | Lista todos os `GeofeedBloco` (JSON) |
| `/homeferramentas/geo/blocos/salvar/` | POST | Cria/atualiza um ou mais blocos em uma única requisição — body `{"blocos": [{...}, {...}]}` |
| `/homeferramentas/geo/blocos/<id>/excluir/` | POST | Remove um bloco |

Todos exigem login + `is_staff` (`@admin_required`), assim como o resto da ferramenta.

`geo_blocos_salvar` aceita uma **lista** de blocos por requisição (suporte real a múltiplos blocos de uma
vez, não só um por request) e retorna `salvos`/`erros` por item — cada linha da UI chama o endpoint com
uma lista de 1 item, mas a API já está pronta para lotes maiores se algum outro fluxo precisar.

Validação: cada `prefixo` passa por `ipaddress.ip_network(prefixo, strict=False)` — prefixo/IP inválido
vira erro por item, sem derrubar os outros blocos do mesmo lote.

### Fluxo de correção continua alimentando o Geofeed

`geo_atualizar` (o fluxo de busca + modal de correção, inalterado na experiência do usuário) agora também
faz `GeofeedBloco.objects.update_or_create(prefixo=..., defaults={...})` além de criar o `CorrecaoGeoIP`
de auditoria — ou seja, tanto o cadastro manual em lote quanto o fluxo de correção de 1 IP escrevem na
mesma fonte de verdade.

### Coluna Postal-Code (RFC 8805)

O formato RFC 8805 tem 5 colunas — `Prefix,Country,Region,City,Postal-Code` — mas a 5ª sempre saía vazia
(não existia campo para isso). `GeofeedBloco.postal_code` preenche essa lacuna; o campo aparece na UI
de cadastro de blocos (o modal de correção via consulta automática não pede Postal Code, já que os
bancos de geolocalização consultados não retornam esse dado).

### Helper de conversão ISO 3166-2

A lógica de converter `regiao` livre (ex: `"SP"`) para o formato ISO 3166-2 exigido pelo RFC 8805
(`"BR-SP"`) estava duplicada em `geo_atualizar` e `geo_geofeed_csv`. Extraída para
`home/views.py::_geo_regiao_iso(pais, regiao)`, reaproveitada nos dois lugares e também no cálculo do CSV
gerado a partir dos blocos.

### Fix — LACNIC rejeitando o Geofeed com nome de estado por extenso (2026-07-30)

`_geo_regiao_iso` só convertia siglas (`"SP"` → `"BR-SP"`); blocos cadastrados com o estado por
extenso (ex: `"Bahia"`, `"Rio de Janeiro"`) ficavam sem conversão e o CSV publicado saía com
`BR,Bahia,...` em vez de `BR,BR-BA,...`. A LACNIC valida o campo Region contra a lista ISO 3166-2 e
rejeitava o arquivo já na primeira linha de dados (`CSV de Geofeed inválido (linha 6)`).

Adicionado `_BR_UF_POR_NOME` (mapa nome do estado, sem acento → sigla) em `home/views.py`; quando a
sigla direta não bate, `_geo_regiao_iso` tenta esse mapa antes de desistir e devolver o texto cru.
O CSV também passou a usar `\r\n` (CRLF) entre linhas, conforme RFC 4180/8805.

Nessa mesma checagem foi encontrado um bloco (`186.65.78.0/24`) com o campo Cidade preenchido com o
próprio prefixo IP por engano — corrigido (limpo) diretamente na tabela `GeofeedBloco`, já que causaria
o mesmo tipo de rejeição na linha seguinte assim que a linha do Region fosse aceita.

### Fix — LACNIC rejeitando o Geofeed com prefixo de outra empresa (2026-07-30)

Corrigido o erro de Region, a LACNIC passou a rejeitar com
`Prefixo IP do CSV de Geofeed não está contido no bloco original`. Causa: `geo_geofeed_csv` publica
**todos** os `GeofeedBloco` ativos num único arquivo, mas o RIR valida se cada prefixo do CSV
pertence ao recurso da conta que está cadastrando a URL — um arquivo compartilhado entre empresas
diferentes é sempre rejeitado na primeira linha que não pertence ao dono da conta.

Conferido via WHOIS: dos 6 blocos cadastrados, 4 (`186.65.76-79.0/24`) pertencem à
**INFORLIMA TELECOMUNICAÇÃO EIRELI** (AS272418), 1 (`2804:57b0:efe0::/44`) está dentro do `/32` da
**JMA Provedor de Internet** (AS268080, outra empresa) e 1 (`38.210.126.0/24`) **não está alocado a
ninguém** no LACNIC (`whois -h whois.lacnic.net`) — provavelmente dado de teste.

**Solução — Geofeed por empresa:**
- Novo campo `GeofeedBloco.empresa` (texto livre) + `empresa_slug` (`SlugField`, calculado
  automaticamente em `save()` via `slugify(empresa)`) — migration `0094_geofeed_bloco_empresa`.
- Nova rota pública `home/urls.py` → `/homeferramentas/geo/geofeed/<empresa_slug>.csv`
  (`views.geo_geofeed_csv_empresa`), servindo só os blocos daquela empresa. A rota antiga
  `/geofeed.csv` continua existindo com todos os blocos, mas passa a ser só para conferência
  interna — **não deve mais ser cadastrada em nenhum RIR**, já que sempre vai misturar empresas.
  Lógica de montagem do CSV extraída para `_geo_geofeed_csv_response(request, blocos_qs)`,
  reaproveitada pelas duas views.
- UI (`geo_consulta.html`): coluna "Empresa" na tabela de Blocos do Geofeed, e um seletor de
  empresa acima da URL pública que troca a URL/preview mostrados (usa `slugifyJs`, réplica em JS
  do `django.utils.text.slugify`, pra montar o dropdown otimisticamente antes do round-trip ao
  servidor confirmar o slug oficial).
- Dados existentes corrigidos: os 4 blocos da INFORLIMA marcados com `empresa="INFORLIMA"`, o bloco
  da JMA com `empresa="JMA Provedor"` (preservado, caso a JMA vire cliente com Geofeed próprio no
  futuro) e o bloco não alocado (`38.210.126.0/24`) desativado (`ativo=False`, não apagado) até
  alguém confirmar o prefixo correto.

---

## Endpoints (visão completa da ferramenta)

| Endpoint | Método | Login | Descrição |
|----------|--------|-------|-----------|
| `/homeferramentas/geo/` | GET | ✅ | Página principal |
| `/homeferramentas/geo/buscar/` | GET | ✅ | Consulta 6 fontes (ip-api, ipinfo, MaxMind/RIPE, DB-IP, ipwhois, LACNIC RDAP) |
| `/homeferramentas/geo/atualizar/` | POST | ✅ | Gera CSV de 1 linha, envia correção (MaxMind/RIRs), grava `CorrecaoGeoIP` + `GeofeedBloco` |
| `/homeferramentas/geo/historico/` | GET | ✅ | Últimos 50 `CorrecaoGeoIP` |
| `/homeferramentas/geo/blocos/` | GET | ✅ | Lista `GeofeedBloco` |
| `/homeferramentas/geo/blocos/salvar/` | POST | ✅ | Cria/atualiza blocos em lote |
| `/homeferramentas/geo/blocos/<id>/excluir/` | POST | ✅ | Remove um bloco |
| `/homeferramentas/geo/<id>/resposta/` | GET | ✅ | Resposta IMAP do RIR |
| `/homeferramentas/geo/<id>/aplicacao/` | GET | ✅ | Re-verifica se a correção foi aplicada |
| `/homeferramentas/geo/<id>/confirmar-maxmind/` | GET | ✅ | Confirma e-mail MaxMind via IMAP |
| `/homeferramentas/geo/geofeed.csv` | GET | 🌐 público | Todos os blocos ativos — só para conferência interna, não cadastrar em RIR |
| `/homeferramentas/geo/geofeed/<empresa_slug>.csv` | GET | 🌐 público | Só os blocos daquela empresa — URL a cadastrar no RIR |

---

## Migrations

- `0092_geofeed_bloco` — cria a tabela `GeofeedBloco`.
- `0093_geofeed_bloco_migrar_historico` — `RunPython` que popula `GeofeedBloco` a partir do prefixo mais
  recente de cada `CorrecaoGeoIP`, preservando o conteúdo do `geofeed.csv` público no deploy.
- `0094_geofeed_bloco_empresa` — adiciona `empresa` e `empresa_slug` em `GeofeedBloco`, base do Geofeed
  por empresa (ver seção "Fix — LACNIC rejeitando o Geofeed com prefixo de outra empresa").

---

## Como usar (cadastro de múltiplos blocos)

1. Acesse **Ferramentas → Geolocalização de IP**.
2. No card **Blocos do Geofeed**, clique em **+ Adicionar bloco** para cada prefixo que quiser publicar.
3. Preencha Prefixo (obrigatório, ex: `200.100.50.0/24` ou `2801:80:1234::/48`), País (ISO 2 letras),
   Região, Cidade e Postal Code.
4. Clique no ícone de salvar (💾) da linha — cada bloco é salvo individualmente.
5. O `geofeed.csv` público (card acima) já reflete o novo bloco imediatamente — use **Preview** para
   conferir sem precisar abrir a URL em outra aba.
6. Para remover um bloco do geofeed, clique no ícone de lixeira (🗑) da linha.

---

## Testes realizados

Via Django test client (`manage.py shell`), em banco local:

- Criação de 2 blocos (1 IPv4 `/24`, 1 IPv6 `/32`) em uma única requisição a `/homeferramentas/geo/blocos/salvar/`.
- `geofeed.csv` público (sem login) refletindo os blocos migrados do histórico + os 2 novos, com Postal-Code
  preenchido no bloco que o informou.
- Prefixo inválido (`"not-a-prefix"`) retornando erro por item, sem afetar os demais do lote.
- Exclusão de um bloco via `/homeferramentas/geo/blocos/<id>/excluir/` e confirmação da contagem.

---

## Arquivos Modificados

- `clientes/models.py` — novo model `GeofeedBloco`.
- `clientes/migrations/0092_geofeed_bloco.py`, `0093_geofeed_bloco_migrar_historico.py`.
- `home/views.py` — helper `_geo_regiao_iso`, `geo_blocos_listar`/`geo_blocos_salvar`/`geo_blocos_excluir`,
  `geo_atualizar` grava também em `GeofeedBloco`, `geo_geofeed_csv` lê de `GeofeedBloco`.
- `home/urls.py` — 3 rotas novas (`geo_blocos_listar`, `geo_blocos_salvar`, `geo_blocos_excluir`).
- `home/templates/geo_consulta.html` — card "Blocos do Geofeed" (tabela editável, JS de carregar/salvar/excluir).
