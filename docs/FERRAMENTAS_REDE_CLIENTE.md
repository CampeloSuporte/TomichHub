# Ferramentas de Rede do cliente

Aba **Ferramentas** da página do cliente (`/clientes/listar/?id=…`), que substituiu
a aba "Testes de Rede". As ferramentas são sub-abas; "Conectividade" (lote de
destinos padrão) e "DNS", que já existiam, continuam como as duas últimas.
Até 11/10/2026 eram cartões na tela inicial do portal (`/clientes/dashboard/`)
abrindo páginas do tomich.com.br num iframe; agora são nativas do CRM e os testes
**saem da rede do cliente, pelo proxy SSH dele**. O dashboard deixou de existir:
`/clientes/dashboard/` redireciona direto para a página de acessos do cliente.

| Ferramenta | Onde roda | Comando no proxy |
|---|---|---|
| Ping | proxy | `ping -4/-6 -c N -W 2` (1–20 pacotes) |
| Traceroute | proxy | `traceroute -n -w 2 -q 1 -m 30` → `tracepath` se não houver |
| MTR | proxy | `mtr --report --report-wide` (3–30 ciclos) → `traceroute -q 3` se não houver mtr |
| Teste de Portas | proxy | `bash /dev/tcp` com `timeout 3` (aberta / fechada / filtrada) → `nc -z` |
| Teste de Qualidade | proxy | `ping -i 0.2` (10–100 pacotes): latência, jitter, perda e nota |
| Calculadora IP | servidor | `ipaddress` do Python (IPv4 e IPv6, sem proxy) |
| Acessar Roteador | proxy | túnel SSH (`direct-tcpip`) até IP:porta — abre a interface web numa aba nova |

## Peças

- `clientes/ferramentas_rede.py` — validação, comandos, leitura das saídas e a
  calculadora. Não depende de Django; recebe o executor (`_ssh_exec` no proxy).
- `views.ferramenta_rede_cliente` — `POST /clientes/ferramentas-rede/`
  (JSON `{cliente_id, ferramenta, destino, ipv, pacotes|ciclos, portas, entrada}`).
  - Equipe (backoffice): precisa de `pode_acessar_cliente`.
  - Login do portal: o `cliente_id` tem que estar entre os clientes vinculados.
  - Liberação: mesmo módulo `testes_rede` da aba (rótulo "Ferramentas de Rede");
    bloqueado responde 403 em JSON.
  - Proxy: primeiro `ProxyServer` ativo do cliente. Sem proxy só a calculadora
    funciona (o cartão avisa).
  - Um teste por vez por usuário (trava `ferramenta_rede:<user_id>` no Redis, 150 s).
- `clientes/templates/_ferramentas_rede.html` — painel incluído na aba
  (`{% include %}` em listar.html): formulário de cada ferramenta e o resultado em
  tabela/indicadores + saída completa. `testeRedeSubAba` troca a ferramenta
  (`frSelecionar`) ou mostra Conectividade/DNS.

## Segurança

Destino e portas vêm do navegador e viram shell no proxy. `validar_destino`
só aceita IP (v4/v6) ou hostname (RFC 1123, sem começar com `-`);
`validar_portas` só números/intervalos (máx. 20). Ainda assim tudo passa por
`shlex.quote`. A mesma validação passou a valer na aba interna "Testes de Rede"
(`teste_rede_cliente`), que antes interpolava `destino_unico` sem checar.

## Proxies sem mtr

Vários proxies (ex.: Sartor) não têm `mtr`: o MTR cai no traceroute com 3
sondas por salto e mostra a perda por salto calculada pelos `*`. Para o MTR
completo, instalar `mtr-tiny` no proxy.

## Acessar Roteador (04/11/2026)

Sub-aba para abrir a interface web de um equipamento da rede do cliente **sem
cadastrar um Acesso**: digita IP (IPv4), porta (opcional) e protocolo
(Automático / HTTPS / HTTP) e clica **Acessar**.

- `views.roteador_web_sondar` — `POST /clientes/ferramentas-rede/roteador/`
  (JSON `{cliente_id, ip, porta, protocolo}`). Mesma permissão das outras
  ferramentas (`_cliente_das_ferramentas`). Faz um `GET /` pelo proxy: no modo
  automático tenta HTTPS e, sem resposta, HTTP (sem porta: 443 e depois 80).
  Qualquer resposta HTTP, até 401/404, conta — quem respondeu foi o equipamento.
  Devolve a URL do proxy; o front abre a aba já no clique (senão o bloqueador de
  popup barra) e só troca o endereço depois. Os 6 últimos alvos ficam em
  `localStorage` por cliente (chips "Recentes").
- `views.proxy_web_roteador` — `/clientes/acessos/r<cliente_id>-<ip>/web/<porta>/<scheme>/…`.
  O formato é o mesmo do proxy web dos Acessos de propósito: toda a reescrita do
  `ProxyEngine` (links, redirects, cookies, WebSocket) funciona sem caso especial.
  A parte comum das duas views é `_proxy_web_executar`. Cookies do equipamento
  ficam isolados como `r<cliente>_<ip com _>_NOME`. Login é o da tela do próprio
  roteador (ou Basic digitado no navegador); não há credencial de cadastro.
- **Sempre pelo proxy SSH do cliente** (`ProxyEngine(forcar_tunel=True)`), IP
  público inclusive: o alvo foi digitado, então o CRM nunca abre conexão direta
  a partir do servidor. Sem proxy ativo, só funciona se um túnel VPN do cliente
  cobrir o IP (`vpn_cobre_ip`); senão dá erro.
- WebSocket: `ws/proxy/r<cliente>-<ip>/…` no `WebSocketProxyConsumer`, mesma
  regra (permissão das Ferramentas, sempre pelo proxy/túnel).

Testes: `clientes/tests_ferramentas_rede.py`, `clientes/tests_acessar_roteador.py`.
