# Hotspot — Captive Portal (Redirecionamento para Login)

**Data de Implementação:** 2026-06-13  
**Arquivo principal:** `clientes/hotspot_views.py`  
**Status:** ✅ Produção

---

## Visão Geral

O captive portal do hotspot MikroTik redireciona dispositivos não autenticados para a
página de login/cadastro do CRM antes de liberar o acesso à internet.

### Fluxo Completo

```
Usuário conecta ao WiFi do hotspot
   └─ MikroTik intercepta requisição HTTP
   └─ Redireciona para /clientes/hotspot/login-html/<id>/
      └─ CRM gera login.html com redirect para /clientes/hotspot/portal/<id>/
         └─ Usuário preenche CPF/Telefone
            └─ CRM chama MikroTik API → libera MAC
               └─ Redireciona para o site original (ou gateway)
```

---

## Endpoints

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/clientes/hotspot/login-html/<id>/` | GET | Gera `login.html` para o MikroTik |
| `/clientes/hotspot/portal/<id>/` | GET | Página de cadastro/login do usuário |
| `/clientes/hotspot/portal/<id>/conectar/` | POST | Autentica o usuário via API MikroTik |

Todos os endpoints acima são servidos via **HTTP** (sem HTTPS) pelo nginx, permitindo
que mini-browsers de captive portal os acessem sem bloqueio de mixed content:

```nginx
# /etc/nginx/sites-enabled/crm
location ~ ^/clientes/hotspot/(portal|login-html)/ {
    proxy_pass http://gunicorn;
    # sem redirect para HTTPS
}
```

---

## Bugs Corrigidos (2026-06-13)

### Bug 1 — Mini-browsers bloqueavam JavaScript

**Sintoma:** iOS e Android não redirecionavam para o portal; a internet era liberada sem
o usuário passar pela tela de cadastro.

**Causa:** Mini-browsers de captive portal (iOS CNA, Android Portal) frequentemente não
executam JavaScript. O redirect anterior dependia apenas de `window.location.replace()`.

**Correção:** `_gerar_login_html` agora usa `<meta http-equiv="refresh">` como redirecionamento
primário (funciona sem JS). O `window.location.replace()` é mantido como secundário (quando
há JS disponível, dispara antes e consegue passar os parâmetros codificados).

```python
def _gerar_login_html(hotspot, portal_url):
    http_portal = portal_url.replace('https://', 'http://', 1).rstrip('/')
    return (
        '...'
        '<meta http-equiv="refresh" content="0;url=' + http_portal + '/">\n'  # primário
        '<script>\n'
        'var p="' + http_portal + '/";\n'
        'window.location.replace(p+q);\n'                                      # secundário
        '</script>\n'
        '<a href="' + http_portal + '/">Clique aqui...</a>\n'                  # último recurso
        '...'
    )
```

### Bug 2 — Injeção HTML via `&` em URLs

**Sintoma:** Parâmetro `$(link-login)` (URL do MikroTik com `&`) quebrava os atributos
`value="..."` dos campos hidden no formulário do portal.

**Causa:** A URL `$(link-login)` contém `&` (ex: `http://192.168.1.1/login?mac=AA&ip=...`)
que era inserida diretamente no atributo HTML sem escape, partindo o atributo.

**Correção:** Adicionado `import html as _html` e escape com `html.escape(..., quote=True)`
em todas as variáveis injetadas em atributos HTML:

```python
import html as _html

link_h = _html.escape(link, quote=True)
mac_h  = _html.escape(mac,  quote=True)
ip_h   = _html.escape(ip,   quote=True)
orig_h = _html.escape(orig, quote=True)

# Uso nos campos hidden:
# <input type="hidden" name="link" value="{link_h}">
```

### Bug 3 — Mixed Content bloqueava o POST

**Sintoma:** Portal carregado via HTTPS não conseguia submeter o formulário para `http://`.

**Causa:** `_portal_page_html` tinha `scheme = 'http'` hardcoded. Quando acessado via HTTPS,
o formulário apontava para `http://` e o browser bloqueava o POST (mixed content).

**Correção:** Scheme detectado dinamicamente:

```python
scheme = 'https' if request.is_secure() else 'http'
```

### Bug 4 — `link` vazio quando meta-refresh era usado

**Sintoma:** Após autenticação, `hotspot_portal_conectar` recebia `link` vazio e não sabia
para qual URL do MikroTik chamar o login.

**Causa:** O `<meta http-equiv="refresh">` não passa parâmetros na URL de destino (ao
contrário do JS que usava `encodeURIComponent`). Logo, o campo hidden `link` ficava vazio.

**Correção:** Fallback para o gateway do hotspot quando `link` está vazio:

```python
raw_link = link if link else f'http://{h.gateway}/login'
safe_link = raw_link.replace('"', '%22').replace("'", '%27')
```

---

## Detalhes de Implementação

### `_gerar_login_html` — Geração do login.html

Gera o HTML que o MikroTik serve como página de login. Esse HTML faz o redirect imediato
para o portal do CRM.

Estratégia de redirect (ordem de prioridade):

1. `<meta http-equiv="refresh">` — funciona em todos os browsers, incluindo mini-browsers sem JS
2. `window.location.replace()` — quando JS está disponível, dispara primeiro e passa parâmetros codificados
3. `<a href="...">` — fallback manual caso os anteriores falhem

O portal URL é sempre convertido para HTTP (mesmo se o CRM usa HTTPS) porque o mini-browser
de captive portal não confia em certificados HTTPS na rede não autenticada.

### `_portal_page_html` — Página de Cadastro

Renderiza o formulário de CPF/Telefone para identificar o usuário. Os campos hidden contêm:

| Campo | Valor | Origem |
|-------|-------|--------|
| `link` | URL de login do MikroTik | `$(link-login)` |
| `mac`  | MAC do dispositivo | `$(mac)` |
| `ip`   | IP do dispositivo | `$(ip)` |
| `orig` | URL original solicitada | `$(link-orig)` |

Todos passam por `html.escape(..., quote=True)` antes de serem inseridos no HTML.

### `hotspot_portal_conectar` — Autenticação

Recebe o POST do formulário, valida o usuário (CPF ou telefone) e chama a API MikroTik
para liberar o MAC address. Em seguida redireciona para:

1. A URL `link` (MikroTik login URL) se preenchida — o MikroTik finaliza a auth e redireciona para `orig`
2. `http://<gateway>/login` como fallback quando `link` está vazio (caso do meta-refresh sem JS)

---

## Compatibilidade com Mini-Browsers

Os sistemas operacionais de smartphones detectam portais cativos fazendo uma requisição
HTTP para um host conhecido e verificando a resposta. Quando detectam um redirect, abrem
um mini-browser especial (CNA no iOS, Portal no Android) com comportamento restritivo:

| Recurso | iOS CNA | Android Portal |
|---------|---------|----------------|
| JavaScript | ⚠ Às vezes bloqueado | ⚠ Às vezes bloqueado |
| `<meta http-equiv="refresh">` | ✅ Suportado | ✅ Suportado |
| HTTPS em rede não autenticada | ❌ Bloqueado | ❌ Bloqueado |
| POST para domínio diferente | ⚠ Depende da versão | ⚠ Depende da versão |

Por isso, o portal é sempre servido via HTTP e o redirect usa `<meta>` como método primário.

---

## Configuração no MikroTik

O campo **Login Page** do Hotspot Server Profile deve apontar para:

```
http://<IP-DO-CRM>/clientes/hotspot/login-html/<HOTSPOT_ID>/
```

O **Walled Garden** deve permitir acesso pré-autenticação ao CRM:

```
# IP via HTTP
http: dst-host=<IP-DO-CRM>

# Domínio via HTTPS (se necessário)
https: dst-host=<DOMINIO-DO-CRM>
```

Isso garante que o mini-browser consiga alcançar o portal do CRM antes da autenticação.
