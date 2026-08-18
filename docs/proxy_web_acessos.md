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
                     uma VPN WireGuard/OpenVPN do cliente já cobre o IP
                     (vpn_cobre_ip, views.py)
Equipamento (interface web nativa)
```

O HTML/CSS de resposta passa por `ProxyEngine.rewrite_content`, que:
- Reescreve URLs absolutas do próprio host do equipamento para o `proxy_base` (`/clientes/acessos/<id>/web/<porta>/<scheme>`)
- Isola cookies por acesso (`a<id>_NOME`) — impede que a sessão de um equipamento vaze pra outro
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

Clientes que só têm uma VPN WireGuard/OpenVPN própria (sem `ProxyServer` SSH cadastrado) também
funcionam: `vpn_cobre_ip(cliente, host)` (`clientes/views.py`) confere se o IP do equipamento cai
dentro de alguma rede roteada por uma VPN ativa do cliente — nesse caso a rota já existe no kernel
via a interface da VPN, e a conexão é feita direto, sem túnel SSH.

Desde 13/08/2026 não basta a rede estar **declarada** em `redes_privadas`: `vpn_cobre_ip` também
confere, via `ip route get` (`vpn_manager.rota_dev_para`), se o `dev` real da rota é a interface
daquele túnel (`wgN` / `tun-crm-N`). A tabela de rotas do kernel é única e roteia por destino, então
quando dois clientes declaram a mesma faixa ampla só uma rota vale — sem essa conferência o proxy
"tinha certeza" de alcançar o equipamento e entrava na rede do **outro** cliente. Não batendo, a
função retorna `False` e o chamador cai no `ProxyServer` SSH, que é o caminho correto. Se o
`ip route get` falhar, mantém-se o comportamento antigo (confia na declaração).

**Atenção:** esse fallback existe em `proxy_web_acesso` (HTTP) mas **não** em todos os consumers
WebSocket que também usam IP privado (Terminal SSH, Telnet, WinBox) — ver `docs/winbox_vnc.md`
para o que já foi corrigido e o que ainda falta.

---

## Problemas Conhecidos e Soluções

### Host cadastrado com path embutido quebra o proxy (e o roteamento SSH) — Corrigido em 17/08/2026

**Sintoma:** acesso Zabbix (`Acesso.host = "198.18.1.13/zabbix"`, campo cadastrado com IP **e**
path juntos em vez de só o IP) dava "Sem resposta" no proxy — mesmo com túnel SSH ativo e
funcional pro cliente. Outros dois acessos (Grafana porta 3000, host limpo; um host com IP
público em porta não-padrão) reportados como quebrados no mesmo lote — investigação confirmou que
esses dois já funcionavam (a real causa era só o host malformado; o de IP público tinha um
problema de rede externo, não de CRM).

**Causa** (`clientes/views.py::proxy_web_acesso`): o código assumia que `acesso.host` é sempre um
IP/hostname puro. Com `"198.18.1.13/zabbix"`:
- `ProxyEngine.is_private_ip(target_host)` recebe a string suja, `ipaddress.ip_address()` levanta
  `ValueError`, e a função **retorna `False` silenciosamente** — mesmo o IP sendo privado
  (`198.18.0.0/15` é RFC reservado), o acesso deixava de passar pelo túnel SSH e tentava ir
  direto, inalcançável a partir do servidor do CRM.
- `target_url = f"{scheme}://{target_host}:{porta_web}{full_path}"` virava
  `"http://198.18.1.13/zabbix:80/..."` — `urlparse` corta o `netloc` no primeiro `/`, então
  `":80/"` vira parte literal do **path**, não a porta.

**Correção:** separa hostname puro de path fixo eventualmente embutido, e reinjeta esse path na
requisição só quando ele ainda não veio embutido no path atual (evita duplicar quando os links do
próprio dispositivo já trazem o prefixo, ex: `href="/zabbix/menu.php"`):

```python
target_host = acesso.host.strip()
base_path = ''
if '://' in target_host:
    parsed_host = urlparse(target_host)
    target_host = parsed_host.hostname
    base_path = parsed_host.path.rstrip('/')
elif '/' in target_host:
    target_host, _, rest = target_host.partition('/')
    rest = rest.strip('/')
    if rest:
        base_path = '/' + rest

if base_path:
    bp = base_path.strip('/')
    p_no_slash = path.lstrip('/')
    if p_no_slash != bp and not p_no_slash.startswith(bp + '/'):
        path = base_path + path
```

Não foi feita nenhuma migração de dado — o parsing lida com o valor existente no banco
(`"198.18.1.13/zabbix"`) em tempo de requisição; criar um campo separado só pra isso seria mudança
de schema desnecessária.

**Validado ao vivo:** testado via `requests` direto contra `https://crm.tomich.com.br` (Django test
client pra sessão + `requests.Session` pra bater no Gunicorn/Daphne reais) — os 3 acessos do
relato: 200 OK (Zabbix, antes falhava), 200 OK (Grafana, já funcionava), 502 (o de IP público —
confirmado por teste TCP direto que é a rede do cliente que não responde nessa porta a partir do
servidor do CRM, não um bug de código).

**Nota operacional:** essa rota é servida pelo **Daphne**, não pelo Gunicorn (ver seção
"Roteado pelo nginx" acima) — reiniciar só o Gunicorn depois de mexer em `proxy_web_acesso` não
aplica a mudança; é preciso `systemctl restart daphne` também.

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

**Última atualização:** 17/08/2026
**Autor:** CampeloSuporte
