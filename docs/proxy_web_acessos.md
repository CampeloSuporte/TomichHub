# Proxy Web de Acessos — Documentação Técnica

## O que é

Permite acessar a interface **web** de um equipamento (roteador, OLT, AP, etc.) cadastrado como
`Acesso` diretamente pelo browser, através do CRM — sem VPN/túnel manual do lado do operador,
mesmo quando o equipamento só é alcançável por um IP privado do cliente.

URL: `/clientes/acessos/<acesso_id>/web/<porta>/<scheme>/<path...>`

---

## Arquitetura

```
Browser
   ↕ HTTPS (crm.tomich.com.br)
Django (proxy_web_acesso, clientes/views.py)
   ↕ ProxyEngine (clientes/proxy_engine.py)
   ├── IP público → requests direto ao equipamento
   └── IP privado → túnel SSH (ProxyServer do cliente) OU rota direta se
                     um túnel OpenVPN do cliente já cobre o IP
                     (vpn_cobre_ip, views.py)
Equipamento (interface web nativa)
```

O HTML/CSS de resposta passa por `ProxyEngine.rewrite_content`, que:
- Reescreve URLs absolutas do próprio host do equipamento para o `proxy_base` (`/clientes/acessos/<id>/web/<porta>/<scheme>`)
- Isola cookies por acesso (`a<id>_NOME`) — impede que a sessão de um equipamento vaze pra outro
  (cookie gravado pelo servidor do equipamento passa por `_repassar_cookie_do_device`, em `views.py`;
  cookie gravado pelo JS dele passa pelo interceptador de `document.cookie` do script injetado)
- Injeta um `<script>` que intercepta `fetch`/`XMLHttpRequest`/`location.*`/`history.pushState`/
  `WebSocket`/`window.open`/envio de formulário, reescrevendo qualquer URL que aponte pro mesmo
  origin do CRM mas fora do `proxy_base` — necessário porque a maioria das interfaces web de
  equipamento (SPAs, principalmente) assume que está rodando na raiz do próprio domínio.

Roteado pelo nginx para o **Daphne** (não o Gunicorn) — ver comentário em
`/etc/nginx/sites-enabled/crm`, location `^/clientes/acessos/[0-9]+/web(/|$)`: o pool de conexões
SSH/TLS reaproveitadas vive em memória por processo, e um único processo Daphne mantém um pool
sempre quente (com múltiplos workers Gunicorn cada um teria que aquecer o próprio).

---

## Acesso Direto via VPN (sem ProxyServer SSH)

Clientes que só têm um túnel OpenVPN próprio (sem `ProxyServer` SSH cadastrado) também
funcionam: `vpn_cobre_ip(cliente, host)` (`clientes/views.py`) confere se o IP do equipamento cai
dentro de alguma rede roteada por uma VPN ativa do cliente — nesse caso a rota já existe no kernel
via a interface da VPN, e a conexão é feita direto, sem túnel SSH.

Desde 13/08/2026 não basta a rede estar **declarada** em `redes_privadas`: `vpn_cobre_ip` também
confere, via `ip route get` (`openvpn_tunnel_manager.rota_dev_para`), se o `dev` real da rota é a
interface daquele túnel (`tun-crm-N`). A tabela de rotas do kernel é única e roteia por destino, então
quando dois clientes declaram a mesma faixa ampla só uma rota vale — sem essa conferência o proxy
"tinha certeza" de alcançar o equipamento e entrava na rede do **outro** cliente. Não batendo, a
função retorna `False` e o chamador cai no `ProxyServer` SSH, que é o caminho correto. Se o
`ip route get` falhar, mantém-se o comportamento antigo (confia na declaração).

**Atenção:** esse fallback existe em `proxy_web_acesso` (HTTP) mas **não** em todos os consumers
WebSocket que também usam IP privado (Terminal SSH, Telnet, WinBox) — ver `docs/winbox_vnc.md`
para o que já foi corrigido e o que ainda falta.

---

## Falha do proxy e conexão direta (`X-CRM-Proxy-Falha`) — Adicionado em 11/09/2026

As páginas de erro geradas pelo **próprio proxy** saem por `_falha_proxy_web` com o header
`X-CRM-Proxy-Falha: 1`: 400 (IP privado sem ProxyServer ativo e sem OpenVPN cobrindo o IP), 502
(sem resposta do equipamento) e 500 (erro interno). Resposta do equipamento, com qualquer status,
nunca leva o header.

O card usa esse header em HTTP/HTTPS com IP privado (`abrirWebProxyComFallback`): testa o proxy e,
se vier o header, der erro de rede ou passarem 15 s sem resposta, abre a conexão direta no
navegador. Detalhes em [acessos_protocolos_extras.md](acessos_protocolos_extras.md).

## Problemas Conhecidos e Soluções

### Login funciona mas a página fica voltando pra tela de login (loop) — Corrigido em 04/08/2026

**Sintoma:** em equipamentos com interface SPA própria (reproduzido com um AP Mimosa/Airspan C5c),
o login era aceito pelo equipamento (resposta `200`, `role` de usuário válido), a página carregava
o dashboard normalmente por alguns segundos, e então **recarregava por completo** de volta pra
tela de login — em loop, repetindo a cada poucos segundos.

**Diagnóstico:** o firmware do equipamento devolve um campo `"https":false` (ou `true`) na resposta
JSON de login/status, e o próprio JS dele compara isso com `location.protocol` pra decidir se deve
navegar pra `http://` ou `https://` — pensado pra equipamentos com interface própria em rede local,
onde esse campo reflete a config real do servidor web dele. Dentro do proxy isso não faz sentido:
o browser sempre fala HTTPS com o CRM, independente de estarmos falando HTTP ou HTTPS com o
equipamento por trás (o scheme real já está embutido no path, ex: `.../web/80/http/`). Como o
equipamento reportava `https:false` mas `location.protocol` (real, do CRM) é sempre `"https:"`, a
condição batia toda vez e o JS forçava `location.href = "http://..."` — nginx redireciona `http://`
de volta pra `https://` no mesmo path (`/etc/nginx/sites-enabled/crm`), mas a viagem de ida e volta
já é um reload completo da página, que apaga todo o estado da SPA (login) guardado só em memória.

**Fix (duas camadas, `clientes/proxy_engine.py`):**

1. **Guard `_isSchemeSwapNoop`** no script injetado — intercepta `location.href =`,
   `location.assign()` e `location.replace()`; se o valor novo é o `location.href` atual com o
   scheme trocado (`http` ↔ `https`), a navegação é cancelada (no-op) em vez de executada. Cobre o
   caso em que o equipamento tenta essa troca via essas três APIs.
2. **Reescrita do campo na origem** (`clientes/views.py::proxy_web_acesso`) — mais robusta que
   depender de interceptar toda navegação client-side possível (formulário, `<a>`, `window.top`,
   etc.): sempre que a resposta é `application/json` e o proxy está falando **HTTP** com o
   equipamento, `"https":false` no corpo da resposta é reescrito pra `"https":true"` antes de
   devolver ao browser — a condição que dispara a troca de protocolo nunca mais fica verdadeira,
   não importa por qual API o JS do equipamento tente fazer a navegação.

```python
# proxy_web_acesso, clientes/views.py
if scheme == 'http' and 'json' in content_type:
    content = re.sub(rb'"https"\s*:\s*false', b'"https":true', content)
```

**Como foi confirmado:** o loop foi capturado ao vivo nos logs do Daphne — o padrão característico
é o script do Google Analytics (`//www.google-analytics.com/analytics.js`, carregado uma vez por
execução real do JS de bootstrap do app) sendo buscado de novo a cada ciclo, prova de que era um
reload de página de verdade e não só uma re-renderização interna da SPA. Depois do fix, o ciclo
parou de se repetir.

---

### Grafana abre no "Page not found" dele mesmo — Corrigido em 24/08/2026

**Sintoma:** `/clientes/acessos/<id>/web/3000/http/login` carrega o Grafana, mas a tela é a
página **"Page not found — We're looking but can't seem to find this page"** do próprio
Grafana. No log do nginx **tudo responde 200** (HTML, CSS, chunks JS, fontes), o que faz
parecer erro do CRM; a pista é o `grot-not-found.svg` sendo baixado — é a ilustração do 404
do Grafana, ou seja, quem decidiu que a rota não existe foi o front dele.

**Diagnóstico:** o Grafana é uma SPA que descobre em que sub-caminho está servida pelo
`appSubUrl` do bootdata embutido no HTML (`window.grafanaBootData.settings`). Instalado na
raiz — o padrão, e o caso aqui — ele manda `"appSubUrl":""`. O router então tenta casar o
caminho **inteiro** do proxy (`/clientes/acessos/1301/web/3000/http/login`) com as rotas dele,
não acha nenhuma e renderiza o 404 interno. O backend do Grafana nunca viu problema nenhum:
ele recebeu `/login` e respondeu 200.

**Fix (`ProxyEngine._rewrite_grafana_bootdata`):** quando o HTML tem `grafanaBootData`, o
`appSubUrl` é reescrito para o `proxy_base`. Um único campo resolve as três pontas:

| Uso do `appSubUrl` no front do Grafana | Efeito da reescrita |
|---|---|
| `basename` do router | rota vira `/login`, `/d/<uid>`, etc. — casa de novo |
| prefixo das chamadas de API | `proxy_base + /api/...`, que o proxy entrega como `/api/...` |
| `__webpack_public_path__` | chunks lazy vêm de `proxy_base + /public/build/` |

O `proxy_base` nunca termina em `/` (é `.../web/<porta>/<scheme>`), que é exatamente o formato
que o Grafana espera. Páginas sem `grafanaBootData` não são tocadas.

**Confirmado ao vivo** com um harness local que replica a view sem autenticação + chromium
headless: antes, "Page not found - Grafana"; depois, a tela de login, e o login com as
credenciais do acesso abre o dashboard (`Home - Dashboards - Grafana`) sem nenhuma requisição
falhando. Zabbix no mesmo host (porta 80, path `/zabbix`) foi testado junto, antes e depois,
pra garantir que a mudança não mexeu em quem já funcionava.

---

### SPA React "simula abrir mas não abre" (TOMICH OBSERVER) — Corrigido em 15/09/2026

**Sintoma:** acesso 1116 (JMA, `172.27.38.117:80`, TOMICH OBSERVER) abre a aba e fica em branco.
No log do nginx **tudo responde 200**: HTML, `assets/index-*.js`, CSS, logo e até a API
(`/api/visibility`, `/api/updates`, `/api/license/status`). Nenhuma chamada de login depois disso.

**Diagnóstico:** o Observer é uma SPA React com `<BrowserRouter>` **sem `basename`**, com rotas
`/login`, `/portal/:slug/*`, ... e o coringa `path:"*"` → `<Navigate to="/" replace>`. O router lê
`window.location.pathname` (`/clientes/acessos/1116/web/80/http/`), não casa nada e cai no coringa.
O `Navigate` pede `/`, o `replaceState` injetado recoloca o prefixo do proxy e o router cai no
coringa de novo — nada renderiza.

O override de `Location.prototype.pathname` do script injetado **não resolve isso**: no Chrome,
`pathname` é propriedade própria (unforgeable) do objeto `location`, não do protótipo, então
redefinir no protótipo não muda o que a SPA lê.

**Fix (`ProxyEngine._rewrite_js` + helper `window.__crmRouterBase`):** mesma ideia do `appSubUrl`
do Grafana, só que no JS. Respostas `*javascript*` passam pela reescrita, que troca o default `"/"`
do `basename` do componente `Router` do react-router 6 (build de produção:
`function EG(e){let{basename:t="/",children:...`) por
`window.__crmRouterBase ? window.__crmRouterBase(e.location) : "/"`.

| Caso | `__crmRouterBase` devolve |
|---|---|
| `BrowserRouter` dentro do proxy (pathname começa com o `proxy_base`) | `proxy_base` — rota vira `/`, `/login`; `navigate()`/`<Link>` já saem com o prefixo |
| `HashRouter` (localização vem do hash, `/login`) | `"/"` — igual a antes |
| `basename` explícito na SPA | não chamado — o prop tem precedência sobre o default |
| JS aberto fora do proxy (helper não existe) | `"/"` |

O JS só é tocado se tiver `basename:` (bundle de SPA tem MB). Quando a reescrita muda o bundle,
a view tira `ETag`/`Last-Modified`/`Expires` e manda `Cache-Control: no-cache`: o Observer serve
os assets com `max-age=2592000` (30 dias) e uma cópia antiga no navegador ignoraria a correção.
Isso não alcança quem abriu o acesso **antes** do fix. Ele ainda tem o bundle original em cache, e
o F5 comum nem pede o JS: no log, logo depois do deploy, o HTML novo veio e o `index-*.js` nem
apareceu.

**Versão na URL dos scripts (`ProxyEngine._versionar_assets`):** no HTML reescrito, `<script src>`
e `<link rel="modulepreload">` do próprio device ganham `?crmv=<VERSAO_ASSETS>`. Essa URL nunca
esteve em cache, então o navegador busca o bundle já reescrito. Query só no `<script type="module">`
quebraria app Vite com code-split: um chunk que importa `./index-X.js` pediria a URL sem query, o
módulo seria carregado duas vezes e haveria dois Reacts. Por isso vai junto um **import map**
(`"<url original>": "<url versionada>"`), inserido antes do primeiro módulo. Toda importação que
resolver para a URL original cai na versionada, e fica uma instância só. Página com import map
próprio não é tocada. Script externo (`https://`, `//`) e CSS também não.

Quando uma mudança futura em `rewrite_content` precisar chegar a quem tem cache antigo, suba
`ProxyEngine.VERSAO_ASSETS`.

Antes disso foi tentado `Clear-Site-Data: "cache"` na primeira navegação. No harness abaixo, o header
saía, mas o bundle antigo continuava vindo do cache. Como não deu pra comprovar a limpeza, foi
descartado.

**Confirmado ao vivo** com Chrome headless controlado pelo DevTools Protocol (`--remote-debugging-pipe`
e `Fetch.requestPaused` respondendo cada requisição pela view real). O Chrome deste servidor não abre
socket, então a rede dele não é usada. Antes, `#root` vazio. Depois, o app vai sozinho para
`.../http/login` e mostra a tela de login do Observer, sem exceção no console. O login com as
credenciais do acesso abre o painel (Home, Hosts, Alertas, tráfego BGP), com toda a API pelo proxy.

O cache foi testado na mesma sessão do Chrome, em 4 cargas:

1. HTML e JS "antigos" (sem versão e com `max-age` de 30 dias): tela em branco.
2. Igual à carga 1: o JS **nem é pedido** (sai do cache) e a tela continua em branco. É o estado
   do navegador do usuário depois do primeiro deploy.
3. Código novo: o navegador pede `index-*.js?crmv=1` e a tela de login aparece.
4. Código novo de novo: continua abrindo.

Testes em `clientes/tests_proxy_web.py` (`RewriteReactRouterBasenameTest`).

**Não confundir** com o acesso 1455 (DS TECH, mesmo Observer): lá o HTML abre, mas o CSS/JS
voltam **502** — é MTU 4096 na interface do servidor do proxy SSH num caminho de 1500 (pacote
grande some), problema de rede do cliente, não do CRM.

---

### Porta órfã em URL absoluta do device — Corrigido em 24/08/2026

**Sintoma:** link do próprio equipamento apontando pra ele mesmo com porta explícita
(`http://198.18.1.13:3000/d/abc`) virava `/clientes/acessos/1301/web/3000/http:3000/d/abc` —
404 do Django, sem nenhuma pista de por quê.

**Causa:** a reescrita de URL absoluta trocava `http://<host>` pelo `proxy_base` testando só
os sufixos `:80`, `:443` e vazio. Numa porta alta (Grafana 3000, Proxmox 8006, Zabbix 8080) a
troca casava pelo host "pelado" e deixava o `:3000` grudado no meio do caminho.

**Fix (`ProxyEngine._rewrite_urls_absolutas`):** a porta passa a ser lida junto com o host, por
regex, e o destino sai conforme o caso:

| URL no HTML do device | Vira |
|---|---|
| porta igual à que está sendo proxyada | `proxy_base` |
| porta explícita **diferente** (ex: `:8006` num acesso na 3000) | `.../web/8006/<scheme>` — segue dentro do proxy, na base daquela porta |
| **sem** porta (`http://198.18.1.13/x`) | `proxy_base` — a porta que já está funcionando |

O último caso é deliberado: muito firmware imprime a própria URL canônica sem porta mesmo
servindo numa porta alta, e mandar esse link pra porta 80 quebraria um acesso que funcionava.

Testes em `clientes/tests_proxy_web.py`.

---

### Proxmox (PBS/PVE novos) responde 401 em tudo depois do login — Corrigido em 14/09/2026

**Sintoma:** Proxmox Backup Server da Conecta ISP (acesso 1482, `172.18.234.5:8007`, pelo túnel
OpenVPN) abria a tela de login e aceitava usuário e senha (`POST /api2/extjs/access/ticket` → 200),
mas logo depois toda chamada da API voltava **401** e aparecia "Connection error 401: Unauthorized".

**Diagnóstico:** as versões novas do Proxmox não gravam mais o ticket pelo JS
(`PBSAuthCookie`/`PVEAuthCookie` via `document.cookie`). Quem grava é o servidor, com
`Set-Cookie: __Host-PBSAuthCookie=PBS:root@pam:…::<assinatura base64>; Secure; HttpOnly; Path=/`.
O indício no log do nginx é uma rajada de `DELETE /api2/extjs/access/ticket` ao abrir a página (o
logout que limpa esse cookie HttpOnly). O proxy repassava o cookie com `HttpResponse.set_cookie`, e o
`SimpleCookie` do Python **põe aspas** em valor com `@`, `/`, `=` etc.:

```
Set-Cookie: a1482___Host-PBSAuthCookie="PBS:root@pam:…"; Path=/
```

O browser devolvia o valor com as aspas, o proxy repassava assim, e o PBS não reconhecia o ticket.
Além disso o `Expires` do equipamento era descartado: o cookie de logout (`Expires=1970`) virava um
cookie vazio que nunca expirava.

**Fix (`clientes/views.py::_repassar_cookie_do_device`):** os dois pontos que copiam `Set-Cookie`
(resposta normal e redirect) usam o helper, que:

- mantém o nome isolado por acesso (`a<id>_NOME`);
- grava o valor **cru**, sem aspas (`Morsel.set(chave, valor, valor)`, o `coded_value` é o próprio
  valor);
- preserva `Expires`, `Max-Age` e `HttpOnly`; `Path` sempre `/`, `Domain` descartado, `SameSite=Lax` e
  `Secure` conforme a requisição;
- ignora `Set-Cookie` sem `=` e nome que o `SimpleCookie` recusa (log de warning) em vez de estourar
  500.

Vale para qualquer equipamento cujo cookie de sessão tenha caractere fora do conjunto "seguro" do
`SimpleCookie` (JWT com `/`/`+`, base64 com `=`, `user@realm`).

**Como conferir o `Set-Cookie` de um equipamento** (sem login; o `DELETE` do ticket só limpa o cookie):

```python
from clientes.proxy_engine import ProxyEngine
r = ProxyEngine(None).do_request(method='DELETE', url='https://172.18.234.5:8007/api2/extjs/access/ticket')
r.cookies_raw   # ['__Host-PBSAuthCookie=; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Secure; SameSite=Lax; HttpOnly; Path=/;']
```

Com IP privado atrás de ProxyServer SSH, passe o `ProxyServer` do cliente no lugar de `None`.

---

### Acesso web demorando para abrir — Melhorado em 14/09/2026

**Medição** (PBS da Conecta ISP, `172.18.234.5:8007`, caminho direto pelo OpenVPN; Proxmox da
CALLFRAN, `10.201.201.2:8006`, pelo túnel SSH):

| Onde | Antes | Depois |
|---|---|---|
| Direto, requisição pequena | ~546 ms (TCP + TLS novos) | ~150–180 ms (conexão reaproveitada) |
| Direto, 6–7 assets do PBS (1ª vez) | 6,3 s | 4,2 s |
| Direto, os mesmos assets de novo | 6,3 s | 2,0 s |
| Túnel SSH, por asset depois do 1º | ~95 ms (já reaproveitava) | igual |
| Verificações do Django por requisição (`vpn_cobre_ip` + permissão + ProxyServer) | ~7 ms | igual |

**Causas e o que mudou:**

1. **Caminho direto sem reuso de conexão** (`ProxyEngine._direct`): usava `requests.request()`, que cria
   uma `Session` por chamada, e cada asset abria TCP e refazia o handshake TLS. Agora usa
   `ProxyEngine._sessao_direta()`, uma `requests.Session` única no processo (o proxy web roda num
   Daphne só), com `HTTPAdapter(pool_connections=64, pool_maxsize=16)`. **O cookie jar recusa tudo**
   (`DefaultCookiePolicy(allowed_domains=[])`): a sessão é compartilhada entre usuários e acessos, então
   o `Cookie` de cada requisição vem só do browser (`a<id>_NOME`), e o `Set-Cookie` do equipamento
   continua indo para `cookies_raw` e daí para o browser. Testes em
   `clientes/tests_proxy_engine_sessao.py`.
2. **JS/CSS iam sem compressão até o operador**: no `nginx.conf`, `gzip_proxied` e `gzip_types` estão
   comentados, então só `text/html` era comprimido e resposta de proxy nem isso. O `ext-all.js` do
   Proxmox tem 2,3 MB; com gzip, ~670 KB. A location do proxy web em `/etc/nginx/sites-enabled/crm`
   (fora do git) ganhou `gzip on; gzip_proxied any; gzip_vary on; gzip_comp_level 5;
   gzip_min_length 1024;` e `gzip_types` para CSS, JS, JSON, XML e SVG. O `ProxyEngine` já devolve o
   corpo descomprimido, então não há compressão dupla.

**O que ficou de fora, de propósito:**

- **HTTP/2 no nginx**: tiraria o limite de 6 conexões do browser, mas vale para o site inteiro e faria
  o browser disparar dezenas de requisições simultâneas contra equipamentos com CPU fraca (cada uma é
  uma thread no Daphne e, no túnel, um handshake TLS novo acima de 4 sockets ociosos). Se for ativar,
  antes limitar a concorrência por host no `ProxyEngine`.
- **gzip entre CRM e equipamento no túnel**: o `ext-all.js` foi de 478 para 411 ms; ganho pequeno, e
  resposta gzip costuma vir `chunked`, o que impede o reuso do socket (`_reusable` exige
  `Content-Length`).
- **Página inicial buscada duas vezes** (o `fetch` de teste do fallback proxy→direto e depois a aba):
  é só o HTML inicial, poucos KB.

**Como medir de novo** (rodar do diretório do código que se quer medir):

```python
import time
from clientes.proxy_engine import ProxyEngine
e = ProxyEngine(None)   # ou ProxyEngine(proxy_server) para IP privado atrás de SSH
for p in ['/', '/extjs/ext-all.js', '/js/proxmox-backup-gui.js']:
    t = time.time(); r = e.do_request(method='GET', url='https://172.18.234.5:8007' + p)
    print(p, r.status_code, len(r.content), round((time.time() - t) * 1000), 'ms')
```

---

## Como Testar Manualmente

```bash
# Acompanhar em tempo real as requisições de um acesso específico
journalctl -u daphne -f | grep -E "acesso.*<ID>|/clientes/acessos/<ID>/web"
```

Não existe mais um flag de debug hardcoded pra um `acesso_id` específico (havia um, `DBG891`,
usado durante o diagnóstico do bug acima — removido depois do fix por logar usuário/senha em texto
puro no log do Daphne). Se precisar depurar um host específico de novo, prefira adicionar
`logger.debug(...)` temporário (nunca a senha) e reverter antes de commitar.

---

**Última atualização:** 24/08/2026
**Autor:** CampeloSuporte
