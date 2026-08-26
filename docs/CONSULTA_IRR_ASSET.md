# Consulta IRR e AS-SET (bgpq4) — abas da ferramenta de LG

Duas abas novas dentro de **Ferramentas → Pesquisa LG** (`/homeferramentas/lg/` — o
`include` das rotas do app `home` é `path('home', …)`, sem barra, então o prefixo cola no
caminho; é assim desde sempre, vale pra `/homegeral` também):

| Aba | Para quê |
|---|---|
| **Filtro IRR (bgpq4)** | Gerar o prefix-list/route-filter de um ASN ou as-set já no formato do fabricante |
| **AS-SET** | Ver o que um as-set contém de verdade: membros diretos, sets aninhados, ASNs do fechamento recursivo e em quais bases IRR o objeto existe |

A aba original de Looking Glass (consulta de prefixo nos coletores) continua igual, agora
como a primeira aba.

---

## Por que bgpq4 (e não bgpq3)

O `bgpq3` está sem manutenção desde 2019. O `bgpq4` é o fork mantido pelo NTT/Job Snijders,
tem os alvos que este CRM usa no dia a dia (**MikroTik RouterOSv6 e v7**, **Huawei VRP e XPL**,
Nokia MD-CLI/SR Linux, Arista, Junos `route-filter-list`), fala o protocolo IRRd com
pipelining (bem mais rápido em as-set grande) e sai empacotado no Debian/Ubuntu.

```bash
apt install bgpq4
```

O binário é procurado no `PATH` (`shutil.which`) e, se não achar, a tela responde
"bgpq4 não está instalado no servidor" com HTTP 503 — nada quebra em silêncio.

---

## Aba "Filtro IRR (bgpq4)"

Campos:

- **ASN ou AS-SET** — `AS53181`, `AS-CAMPELO`, `AS271699:AS-CLIENTES`. Um número solto
  (`53181`) vira `AS53181`.
- **Fabricante / formato** — Cisco IOS e IOS XR, Junos (prefix-list e route-filter-list),
  Huawei VRP e XPL, MikroTik v6 e v7, Nokia SR OS (clássico e MD-CLI), SR Linux, Arista,
  BIRD, OpenBGPD, JSON e "lista simples" (um prefixo por linha).
- **Família** — IPv4, IPv6 ou os dois (padrão).

Opções avançadas: nome da lista, servidor IRRd, fontes IRR (`-S RADB,LACNIC,TC`),
max-length v4/v6 (`-m`), agregar (`-A`) e "só ASNs com rota registrada" (`-w`).

O resultado traz, por família: contagem de prefixos, o **comando bgpq4 exato** que rodou
(copiável, pra reproduzir no terminal), a configuração pronta com botão **Copiar**, botão
**Baixar** e a lista de prefixos expandível.

### Tamanho da saída

As-set grande gera dezenas de MB — `AS-HURRICANE` dá ~954 mil prefixos IPv4. Por isso:

- a tela recebe no máximo **1,5 MB de configuração** e **8 mil prefixos** na lista
  (com aviso de truncagem);
- o botão **Baixar** roda o bgpq4 de novo **sem cache e sem limite**, e devolve o arquivo
  inteiro como `nome-fabricante.txt` (`.rsc` no MikroTik, `.conf` no BIRD/OpenBGPD).

---

## Aba "AS-SET"

Expande o objeto e mostra:

1. **Resumo** — ASNs do fechamento recursivo, quantos as-sets aninhados, prefixos IPv4 e IPv6.
2. **Objeto nas bases IRR** — o mesmo as-set costuma existir em RADB, LACNIC, TC, RIPE,
   ARIN… com **conteúdo diferente**. Cada base aparece com `descr`, `mnt-by`, data de
   alteração e a lista de `members` daquela base. Quando há mais de uma, sai um aviso: o
   upstream filtra pela base que *ele* consulta, e é aí que mora o "meu prefixo não passa".
3. **Membros diretos (nível 1)** — as-sets aninhados em roxo (clicáveis: abrem o filho, com
   trilha de navegação pra voltar) e os ASNs declarados direto no objeto.
4. **ASNs do fechamento recursivo** — grade com número e nome de cada ASN, campo de filtro,
   **Copiar ASNs** e **Baixar .txt**.

O botão **Gerar filtro deste objeto** leva o objeto pra aba de filtro já com o servidor IRRd
escolhido; e a aba de filtro tem o caminho inverso ("Ver membros do as-set").

### Limites da tela

| Item | Limite | Saída completa |
|---|---|---|
| ASNs listados | 2.000 | botão **Baixar .txt** |
| Nomes de ASN resolvidos | 600 primeiros | — |

O **Baixar .txt** não usa o resultado da tela: refaz só a expansão recursiva no IRRd (sem
nomes, sem contar prefixos) e devolve a lista inteira — `AS-HURRICANE` sai com 25.456 ASNs
em ~2,5 s.

---

## Como funciona por dentro

`home/irr_tools.py`:

- `gerar_filtro()` — dispara até 4 processos `bgpq4` em paralelo (config e JSON, v4 e v6).
- `contar_prefixos()` — usa `-F '%n/%l\n'` e conta linhas, em vez de parsear 30 MB de JSON
  só pra saber o total.
- `IRRd` — cliente do protocolo de consulta do IRRd na porta 43, com conexão persistente
  (`!!`): `!i<set>` traz os membros diretos, `!i<set>,1` o fechamento recursivo em ASNs.
- `objeto_rpsl()` — consulta whois comum, pra pegar o objeto cru de **todas** as bases.
- `consultar_as_set()` — junta tudo e resolve os nomes dos ASNs em lote na RIPEstat.

Views em `home/views.py` (`lg_irr_filtro`, `lg_as_set`), rotas em `home/urls.py`:

```
/homeferramentas/lg/irr/       ?objeto=&vendor=&af=&…[&download=1]
/homeferramentas/lg/as-set/    ?objeto=&host=&contar_prefixos=[&formato=txt]
```

Ambas exigem login e a ferramenta `lg` habilitada na instância
(`@ferramenta_instancia_required('lg')`), igual à aba de Looking Glass.

### Servidor IRRd

Padrão `rr.ntt.net`, que espelha NTTCOM, RADB, LACNIC, TC, RIPE, RIPE-NONAUTH, ARIN, APNIC,
AFRINIC, ALTDB, LEVEL3, REGISTROBR, IDNIC e outras. Dá pra trocar por `whois.radb.net`,
`irr.lacnic.net`, `rr.level3.net` ou `whois.bgp.net.br` no seletor.

### Cache

Resultado das duas abas fica **10 minutos no Redis** (`lg_irr:…` / `lg_asset:…`), com badge
"cache" na tela. Dado de IRR muda devagar, e isso poupa o mirror do NTT de repetição. Os dois
downloads não passam pelo cache.

### Validação da entrada

O objeto entra na linha de comando do `bgpq4` e num socket whois, então passa por
`validar_objeto()` antes: só `AS<n>`, `AS-NOME`, `RS-NOME` e combinações com `:`. Nada de
espaço, `-` inicial (que viraria flag) ou metacaractere — `; rm -rf /` volta 400. Servidor
IRRd, lista de fontes e nome da lista têm regex própria; os inteiros (`-m`, `-R`) são
validados por faixa.

### Tempo de resposta

O `bgpq4` tem timeout de 75 s na aba de filtro e 45 s na contagem da aba AS-SET — o gunicorn
corta em 120 s. As-set grande (`AS-HURRICANE`, 25 mil ASNs) responde em ~11 s.

---

## Armadilhas conhecidas

- **As-set vazio mas "existente"**: o objeto pode estar registrado e não ter `route`/`route6`
  pros membros — a tela avisa que o bgpq4 não devolveu prefixo e manda conferir as bases na
  aba AS-SET.
- **`AS64512:AS-CLIENTES` não é o mesmo que `AS-CLIENTES`**: as-set hierárquico só existe se
  registrado com o prefixo do ASN. A ferramenta consulta exatamente o que foi digitado.
- **Divergência entre bases**: veja o aviso da aba AS-SET. Se o upstream usa RADB e o objeto
  atualizado está só no TC, o filtro dele fica velho — e o LG mostra o prefixo sumindo em
  parte dos coletores.
