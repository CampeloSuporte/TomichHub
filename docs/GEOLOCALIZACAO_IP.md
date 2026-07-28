# Geolocalização de IP — Consulta, Correção e Geofeed Público (RFC 8805)

**Data de Implementação:** 2026-05-17 (base) · **2026-07-26** (múltiplos blocos/localizações) ·
**2026-07-28** (token ipinfo.io, correção IPligence, fix Geofeed)
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
| `/homeferramentas/geo/geofeed.csv` | GET | 🌐 público | Arquivo Geofeed servido para os RIRs/bancos |

---

## Migrations

- `0092_geofeed_bloco` — cria a tabela `GeofeedBloco`.
- `0093_geofeed_bloco_migrar_historico` — `RunPython` que popula `GeofeedBloco` a partir do prefixo mais
  recente de cada `CorrecaoGeoIP`, preservando o conteúdo do `geofeed.csv` público no deploy.

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

---

## 2026-07-28 — Token ipinfo.io, correção automática IPligence, fix crítico no Geofeed

Motivado por um caso real de suporte: bloco `186.65.76.0/22` alocado recentemente, geolocalização
corrigida via MaxMind + Geofeed, mas ainda aparecendo em país errado em algumas bases. O Registro.br
respondeu que a correção de bancos de terceiros precisa ser feita diretamente com cada um (listou
ipinfo.io, DB-IP, IPligence, IP2Location).

### 1. ipinfo.io autenticado com token

As duas chamadas anônimas a `https://ipinfo.io/{ip}/json` (`query_ipinfo` na busca e `q_ipinfo` na
verificação de aplicação, `home/views.py`) agora enviam `token=` — sem token, a API responde num rate
limit baixo e compartilhado globalmente entre todos os usuários anônimos, o que causava falhas
intermitentes dessa fonte no consenso das 6 fontes.

```python
IPINFO_TOKEN = _os.environ.get('IPINFO_TOKEN', '<token>')  # crm/settings.py
```

Testado contra o token real: o endpoint clássico (`ipinfo.io/{ip}/json`) retorna city/region/loc/postal/
timezone — o endpoint `/lite/` (usado no exemplo de teste do token) só retorna país/ASN, por isso o
código continua no endpoint clássico.

### 2. Correção automática por e-mail — IPligence

`RIR_DESTINOS` (LACNIC/ARIN) virou `EMAIL_DESTINOS`, com `ipligence: sales@ipligence.com` adicionado —
mesmo corpo de e-mail em texto (prefixo/país/região/cidade/org/coordenadas) já usado para os RIRs.
Novo checkbox "IPligence" em **Enviar para:** (`home/templates/geo_consulta.html`), desmarcado por
padrão (é contato comercial, não registro obrigatório).

DB-IP.com e IP2Location **não** foram automatizados: DB-IP tem formulário dedicado (`db-ip.com/report/`)
mas atrás de proteção anti-bot (bloqueou até uma checagem simples via `curl`); IP2Location não tem
formulário público de correção, só contato genérico com e-mail ofuscado. Automatizar contra esses seria
arriscar reportar "enviado" quando na real falhou silenciosamente — os dois entraram na lista `portais`
como links manuais, junto com o ipinfo.io (URL corrigida de `/data-correction`, que retorna 404, para
`/corrections`, o formulário real — que também aceita geofeed).

### 3. Bug corrigido — `geo_blocos_salvar` não atualizava o prefixo

Ao editar uma linha existente em "Blocos do Geofeed" (por exemplo, trocar `186.65.76.0/22` por
`186.65.76.0/24` na mesma linha) e clicar em salvar, o backend retornava sucesso mas o prefixo **não
era atualizado no banco** — o dict `defaults` passado para `.update()` no caminho de edição por `id`
não incluía o campo `prefixo`, só país/região/cidade/postal_code/ativo:

```python
# Antes (bug)
atualizados = GeofeedBloco.objects.filter(id=bloco_id).update(**defaults)  # sem prefixo!

# Depois
atualizados = GeofeedBloco.objects.filter(id=bloco_id).update(prefixo=prefixo, **defaults)
```

Foi exatamente esse bug que causou o caso de suporte: o operador editou `/22` para `/24` na UI, o
sistema confirmou "sucesso", mas o `geofeed.csv` público continuou publicando `/22`. Corrigido também
o dado que já estava errado em produção (registro `GeofeedBloco` id=1).

### 4. Nova URL pública limpa para o Geofeed

`geo_geofeed_csv` é servido sob `/homeferramentas/geo/geofeed.csv` — resultado de um bug legado em
`crm/urls.py` (`path('home', include('home.urls'))` sem barra, concatenando o prefixo `home` direto
com as rotas do app sem separador). Essa URL **funciona** (retorna 200), só é feia/não-óbvia para
colar num campo `geofeed:` de WHOIS. Corrigir o prefixo globalmente foi descartado — afetaria também o
webhook do WhatsApp do Agent NOC e os links de download de firmware, que podem já estar configurados
externamente com a URL "quebrada". Em vez disso, `crm/urls.py` ganhou uma rota dedicada:

```python
path('geofeed.csv', geo_geofeed_csv, name='geofeed_csv_publico'),  # -> https://.../geofeed.csv
```

`geo_consulta` (a view que monta o contexto da página) passou a usar `reverse('geofeed_csv_publico')`
em vez de `reverse('geo_geofeed_csv')` para preencher o campo de URL exibido/copiado na tela. A rota
antiga continua registrada e funcionando — nenhuma URL já publicada no Registro.br/RIPE/LACNIC quebra.
