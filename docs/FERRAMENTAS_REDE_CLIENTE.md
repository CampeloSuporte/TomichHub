# Ferramentas de Rede do cliente

Tela inicial do portal do cliente (`/clientes/dashboard/`). Os seis cartões antes
abriam páginas externas (tomich.com.br) num iframe; desde 11/10/2026 são nativos
do CRM e os testes **saem da rede do cliente, pelo proxy SSH dele**.

| Ferramenta | Onde roda | Comando no proxy |
|---|---|---|
| Ping | proxy | `ping -4/-6 -c N -W 2` (1–20 pacotes) |
| Traceroute | proxy | `traceroute -n -w 2 -q 1 -m 30` → `tracepath` se não houver |
| MTR | proxy | `mtr --report --report-wide` (3–30 ciclos) → `traceroute -q 3` se não houver mtr |
| Teste de Portas | proxy | `bash /dev/tcp` com `timeout 3` (aberta / fechada / filtrada) → `nc -z` |
| Teste de Qualidade | proxy | `ping -i 0.2` (10–100 pacotes): latência, jitter, perda e nota |
| Calculadora IP | servidor | `ipaddress` do Python (IPv4 e IPv6, sem proxy) |

## Peças

- `clientes/ferramentas_rede.py` — validação, comandos, leitura das saídas e a
  calculadora. Não depende de Django; recebe o executor (`_ssh_exec` no proxy).
- `views.ferramenta_rede_cliente` — `POST /clientes/dashboard/ferramentas/`
  (JSON `{ferramenta, destino, ipv, pacotes|ciclos, portas, entrada}`).
  - Usuário do portal: usa o cliente vinculado (`get_by_usuario_vinculado`).
  - Equipe (backoffice): manda `cliente_id` e precisa de `pode_acessar_cliente`.
  - Proxy: primeiro `ProxyServer` ativo do cliente. Sem proxy só a calculadora
    funciona (o cartão avisa).
  - Um teste por vez por usuário (trava `ferramenta_rede:<user_id>` no Redis, 150 s).
- `clientes/templates/cliente_dashboard.html` — modal único com o formulário de
  cada ferramenta e o resultado em tabela/indicadores + saída completa.

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

Testes: `clientes/tests_ferramentas_rede.py`.
