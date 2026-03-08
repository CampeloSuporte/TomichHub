# ============================================================
# clientes/documentacao_views.py
#
# Reverse proxy HTTP para PHP IPAM e NetBox.
# Para IPs privados: abre um tunnel SSH (port forward) usando
# o ProxyServer ativo do cliente e redireciona as requisições
# do iframe pelo Django, servindo o conteúdo na própria página.
#
# Dependência extra (instalar uma vez):
#   pip install requests paramiko
# ============================================================

import re
import threading
import logging
import base64
from urllib.parse import urlparse

import socket
import select
import requests
import urllib3
import paramiko

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt

from .models import Cliente, ProxyServer, DocumentacaoRedeConfig

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------
# Cache de clientes SSH paramiko — reutiliza por processo Gunicorn
# key = "proxy_id:remote_host:remote_port"
# ----------------------------------------------------------------
_ssh_registry: dict = {}
_registry_lock = threading.Lock()

# Cache de cookies de sessão autenticados por cliente+tipo
# key = "cliente_id:tipo"  value = dict de cookies
_session_cache: dict = {}
_session_lock = threading.Lock()


def _is_private(host: str) -> bool:
    import ipaddress
    try:
        return ipaddress.ip_address(host).is_private
    except Exception:
        return False


def _get_or_create_ssh_client(proxy) -> paramiko.SSHClient:
    """
    Retorna um SSHClient paramiko ativo para o proxy.
    Cria ou recria se necessário.
    """
    key = f"ssh:{proxy.id}"

    with _registry_lock:
        entry = _ssh_registry.get(key)
        if entry:
            client: paramiko.SSHClient = entry['client']
            transport = client.get_transport()
            if transport and transport.is_active():
                return client
            try:
                client.close()
            except Exception:
                pass
            del _ssh_registry[key]

        logger.info("Conectando SSH %s:%s", proxy.host, proxy.porta)
        senha = proxy.senha or ''

        transport = paramiko.Transport((proxy.host, int(proxy.porta)))
        transport.set_keepalive(30)
        transport.start_client(timeout=15)

        # Descobrir métodos disponíveis via auth_none (pode lançar exceção)
        auth_methods = []
        try:
            transport.auth_none(proxy.usuario)
        except paramiko.BadAuthenticationType as e:
            auth_methods = e.allowed_types
            logger.info("Auth methods disponíveis: %s", auth_methods)
        except Exception:
            auth_methods = ['password', 'keyboard-interactive']

        # Se não conseguiu descobrir, tentar ambos
        if not auth_methods:
            auth_methods = ['password', 'keyboard-interactive']

        authenticated = False

        if 'password' in auth_methods:
            try:
                transport.auth_password(proxy.usuario, senha)
                authenticated = True
                logger.info("Auth por password OK")
            except paramiko.AuthenticationException:
                logger.info("Auth por password falhou")

        if not authenticated and 'keyboard-interactive' in auth_methods:
            try:
                transport.auth_interactive(
                    proxy.usuario,
                    lambda title, instructions, prompts: [senha] * len(prompts),
                )
                authenticated = True
                logger.info("Auth keyboard-interactive OK")
            except paramiko.AuthenticationException:
                logger.info("Auth keyboard-interactive falhou")

        if not authenticated:
            transport.close()
            raise paramiko.AuthenticationException("Todos os métodos de auth falharam")

        # Embrulhar transport no SSHClient
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client._transport = transport

        logger.info("SSH conectado: %s", proxy.host)
        _ssh_registry[key] = {'client': client}
        return client


class _ParamikoAdapter(requests.adapters.HTTPAdapter):
    """
    HTTPAdapter que usa um channel paramiko direct-tcpip
    para rotear a conexão pelo tunnel SSH.
    """

    def __init__(self, ssh_client: paramiko.SSHClient,
                 remote_host: str, remote_port: int, **kwargs):
        self._ssh_client  = ssh_client
        self._remote_host = remote_host
        self._remote_port = remote_port
        super().__init__(**kwargs)

    def send(self, request, **kwargs):
        import urllib3.connection as uc
        from urllib3.connectionpool import HTTPConnectionPool

        remote = (self._remote_host, self._remote_port)
        transport = self._ssh_client.get_transport()
        channel = transport.open_channel(
            'direct-tcpip', remote, ('127.0.0.1', 0)
        )

        # Criar socket a partir do channel paramiko
        sock = channel

        # Substituir o método de conexão para usar nosso socket
        parsed = requests.utils.urlparse(request.url)
        conn = uc.HTTPConnection(self._remote_host, self._remote_port)
        conn.sock = sock

        # Usar urllib3 diretamente
        pool = HTTPConnectionPool(self._remote_host, self._remote_port)
        pool._get_conn = lambda timeout=None: conn

        return super().send(request, **kwargs)


def _make_ssh_session(proxy, remote_host: str, remote_port: int) -> requests.Session:
    """
    Cria uma requests.Session que roteia HTTP via channel SSH direct-tcpip.
    """
    client = _get_or_create_ssh_client(proxy)
    transport = client.get_transport()

    # Abrir channel direct-tcpip para o destino
    channel = transport.open_channel(
        'direct-tcpip',
        (remote_host, remote_port),
        ('127.0.0.1', 0),
    )

    # Criar socket-like wrapper para o channel
    class ChannelSocket:
        def __init__(self, ch):
            self._ch = ch

        def makefile(self, mode):
            return self._ch.makefile(mode)

        def sendall(self, data):
            return self._ch.sendall(data)

        def recv(self, n):
            return self._ch.recv(n)

        def close(self):
            self._ch.close()

        def settimeout(self, t):
            self._ch.settimeout(t)

        def fileno(self):
            return self._ch.fileno()

    # Session que injeta o socket manualmente
    from urllib3.connectionpool import HTTPConnectionPool
    from urllib3.connection import HTTPConnection

    chan_sock = channel

    class SSHTunnelHTTPConnection(HTTPConnection):
        def connect(self):
            self.sock = chan_sock

    class SSHTunnelAdapter(requests.adapters.HTTPAdapter):
        def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
            # Para urllib3 >= 2.x
            parsed = requests.utils.urlparse(request.url)
            conn = SSHTunnelHTTPConnection(parsed.hostname, parsed.port or 80)
            return conn

        def get_connection(self, url, proxies=None):
            parsed = requests.utils.urlparse(url)
            conn = SSHTunnelHTTPConnection(parsed.hostname, parsed.port or 80)
            pool = HTTPConnectionPool(parsed.hostname, parsed.port or 80)
            pool._new_conn = lambda: conn
            return pool

    session = requests.Session()
    session.mount('http://', SSHTunnelAdapter())
    return session, chan_sock


def _close_all_tunnels():
    """Fecha todos os clientes SSH em cache."""
    with _registry_lock:
        for entry in _ssh_registry.values():
            try:
                entry['client'].close()
            except Exception:
                pass
        _ssh_registry.clear()


def _do_request(target_host: str, target_port: int, scheme: str,
                full_path: str, method: str,
                req_headers: dict, body: bytes,
                proxy_srv=None) -> requests.Response | None:
    """
    Executa a requisição HTTP:
    - IP privado + proxy_srv: abre channel direct-tcpip via SSH e faz HTTP raw
    - IP público: requests normal
    """
    try:
        if proxy_srv and _is_private(target_host):
            return _do_request_via_ssh(
                proxy_srv, target_host, target_port,
                full_path, method, req_headers, body
            )
        else:
            url = f"{scheme}://{target_host}:{target_port}{full_path}"
            safe = _safe_headers(req_headers)
            return requests.request(
                method=method, url=url, headers=safe,
                data=body or None, allow_redirects=False,
                timeout=30, verify=False,
            )
    except Exception as e:
        logger.error("Proxy request error [%s:%s%s]: %s",
                     target_host, target_port, full_path, e)
        return None


def _safe_headers(req_headers: dict) -> dict:
    safe = {}
    for h in ('Accept', 'Accept-Language', 'Content-Type', 'Cookie'):
        if h in req_headers:
            safe[h] = req_headers[h]
    safe['User-Agent'] = 'Mozilla/5.0 (CONEXA-CRM/proxy)'
    safe['Accept-Encoding'] = 'identity'
    for k, v in req_headers.items():
        if k.startswith('X-') or k == 'Authorization':
            safe[k] = v
    return safe


def _do_request_via_ssh(proxy_srv, target_host: str, target_port: int,
                         full_path: str, method: str,
                         req_headers: dict, body: bytes) -> requests.Response | None:
    """
    Abre um channel direct-tcpip via paramiko e faz a requisição
    HTTP manualmente pelo socket do channel.
    """
    client = _get_or_create_ssh_client(proxy_srv)
    transport = client.get_transport()

    channel = transport.open_channel(
        'direct-tcpip',
        (target_host, target_port),
        ('127.0.0.1', 0),
        timeout=15,
    )

    try:
        # Montar request HTTP/1.1 raw
        safe = _safe_headers(req_headers)
        safe['Host'] = f'{target_host}:{target_port}'
        safe['Connection'] = 'close'

        lines = [f"{method} {full_path} HTTP/1.1"]
        for k, v in safe.items():
            lines.append(f"{k}: {v}")

        if body:
            lines.append(f"Content-Length: {len(body)}")

        raw_request = "\r\n".join(lines) + "\r\n\r\n"
        channel.sendall(raw_request.encode('utf-8'))
        if body:
            channel.sendall(body)

        # Ler resposta
        response_bytes = b""
        channel.settimeout(30)
        while True:
            try:
                chunk = channel.recv(65536)
                if not chunk:
                    break
                response_bytes += chunk
            except socket.timeout:
                break

        # Parsear resposta HTTP
        return _parse_http_response(response_bytes)

    finally:
        try:
            channel.close()
        except Exception:
            pass


def _parse_http_response(raw: bytes) -> requests.Response:
    """Converte bytes de resposta HTTP raw em requests.Response."""
    from http.client import HTTPResponse
    from io import BytesIO
    from http.cookiejar import CookieJar
    import requests.cookies

    class FakeSocket:
        def __init__(self, data):
            self._file = BytesIO(data)
        def makefile(self, *args, **kwargs):
            return self._file

    source = FakeSocket(raw)
    response = HTTPResponse(source)
    response.begin()

    resp = requests.Response()
    resp.status_code = response.status

    # Capturar todos os headers incluindo múltiplos Set-Cookie
    headers = {}
    cookies_raw = []
    for k, v in response.getheaders():
        if k.lower() == 'set-cookie':
            cookies_raw.append(v)
        else:
            headers[k] = v
    resp.headers = requests.structures.CaseInsensitiveDict(headers)

    # Parsear cookies manualmente
    jar = requests.cookies.RequestsCookieJar()
    for sc in cookies_raw:
        parts = [p.strip() for p in sc.split(';')]
        if parts and '=' in parts[0]:
            name, val = parts[0].split('=', 1)
            jar.set(name.strip(), val.strip())
    resp.cookies = jar

    # Ler body
    content = response.read()
    encoding = headers.get('Content-Encoding', '')
    if encoding == 'gzip':
        import gzip
        content = gzip.decompress(content)
    elif encoding == 'deflate':
        import zlib
        content = zlib.decompress(content)

    resp._content = content
    resp.encoding = 'utf-8'
    return resp


def _phpipam_autologin(cfg, target_host, target_port, base_path, proxy_srv) -> dict:
    """
    Faz login automático no PHP IPAM e retorna os cookies de sessão.
    Retorna dict de cookies ou {} se falhar.
    """
    if not cfg.phpipam_usuario or not cfg.phpipam_senha:
        return {}

    try:
        # 1. GET na página de login para capturar token CSRF (se houver)
        login_path = base_path + '/index.php?page=login'
        resp = _do_request(target_host, target_port, 'http',
                           login_path, 'GET', {}, b'', proxy_srv)
        cookies = {}
        if resp:
            cookies = {c.name: c.value for c in resp.cookies}

        # 2. POST das credenciais
        post_body = (
            f"ipamusername={cfg.phpipam_usuario}"
            f"&ipampassword={cfg.phpipam_senha}"
            f"&phpipam_login_submit=Login"
        ).encode('utf-8')

        post_headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': '; '.join(f'{k}={v}' for k, v in cookies.items()),
        }

        resp2 = _do_request(target_host, target_port, 'http',
                            login_path, 'POST', post_headers, post_body, proxy_srv)

        if resp2:
            # Mesclar cookies da resposta do POST (sessão autenticada)
            for c in resp2.cookies:
                cookies[c.name] = c.value
            logger.info("PHP IPAM autologin: status=%s cookies=%s",
                        resp2.status_code, list(cookies.keys()))

        return cookies

    except Exception as e:
        logger.error("PHP IPAM autologin falhou: %s", e)
        return {}


def _get_phpipam_cookies(cfg, cliente_id, target_host, target_port,
                          base_path, proxy_srv) -> dict:
    """
    Retorna cookies de sessão autenticados, fazendo login se necessário.
    """
    key = f"{cliente_id}:phpipam"
    with _session_lock:
        if key in _session_cache:
            return _session_cache[key]

        cookies = _phpipam_autologin(cfg, target_host, target_port,
                                      base_path, proxy_srv)
        if cookies:
            _session_cache[key] = cookies
        return cookies


def _invalidate_session(cliente_id, tipo):
    """Remove sessão cacheada (chamar após salvar nova config)."""
    key = f"{cliente_id}:{tipo}"
    with _session_lock:
        _session_cache.pop(key, None)


def _rewrite_html(content: bytes, proxy_base: str,
                   prefill_user: str = '', prefill_pass: str = '') -> bytes:
    """
    Reescreve todas as URLs no HTML para passarem pelo proxy Django.
    proxy_base = '/clientes/doc/proxy/5/phpipam'
    Opcionalmente injeta script de prefill de credenciais no form de login.
    """
    try:
        html = content.decode('utf-8', errors='replace')
    except Exception:
        return content

    # Atributos: href="/  src="/  action="/  etc.
    for attr in ('href', 'src', 'action', 'data-src', 'data-url', 'data-href'):
        html = re.sub(
            rf'({attr}\s*=\s*["\'])/',
            rf'\1{proxy_base}/',
            html,
        )

    # CSS url(/ ...
    html = re.sub(r'(url\s*\(\s*["\']?)/', rf'\1{proxy_base}/', html)

    # JavaScript location.href = "/... window.location = "/...
    html = re.sub(r"(location(?:\.href)?\s*=\s*['\"])/", rf"\1{proxy_base}/", html)

    # Script de interceptação para fetch() e XMLHttpRequest dinâmicos
    inject_script = f"""
<script>
(function() {{
  var PB = '{proxy_base}';
  // Interceptar fetch()
  var _origFetch = window.fetch;
  window.fetch = function(url, opts) {{
    if (typeof url === 'string' && url.startsWith('/') && !url.startsWith(PB))
      url = PB + url;
    return _origFetch.call(this, url, opts);
  }};
  // Interceptar XMLHttpRequest
  var _origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url) {{
    if (typeof url === 'string' && url.startsWith('/') && !url.startsWith(PB))
      url = PB + url;
    return _origOpen.apply(this, arguments);
  }};
  // Interceptar history.pushState / replaceState
  var _origPush = history.pushState;
  history.pushState = function(state, title, url) {{
    if (typeof url === 'string' && url.startsWith('/') && !url.startsWith(PB))
      url = PB + url;
    return _origPush.call(this, state, title, url);
  }};
}})();
</script>"""

    # Injetar <base> + script logo após <head>
    base_tag = f'<base href="{proxy_base}/">\n'
    html = re.sub(
        r'(<head[^>]*>)',
        r'\1\n' + base_tag + inject_script,
        html, count=1, flags=re.IGNORECASE,
    )

    # Prefill de credenciais no formulário de login
    if prefill_user or prefill_pass:
        prefill_script = f"""
<script>
(function() {{
  function tryFill() {{
    // PHP IPAM
    var u = document.querySelector('input[name="ipamusername"], input[name="username"], input[type="text"]');
    var p = document.querySelector('input[name="ipampassword"], input[name="password"], input[type="password"]');
    if (u && '{prefill_user}') u.value = '{prefill_user}';
    if (p && '{prefill_pass}') p.value = '{prefill_pass}';
  }}
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', tryFill);
  }} else {{
    tryFill();
  }}
}})();
</script>"""
        html = html.replace('</body>', prefill_script + '\n</body>', 1)
        if '</body>' not in html:
            html += prefill_script

    return html.encode('utf-8')


# ============================================================
# VIEW: Salvar configuração
# ============================================================

@login_required(login_url='login')
def salvar_doc_config(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    if not request.user.is_staff and not request.user.is_superuser:
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    cliente_id = request.POST.get('cliente_id')
    cliente = get_object_or_404(Cliente, id=cliente_id)

    cfg, _ = DocumentacaoRedeConfig.objects.get_or_create(cliente=cliente)

    cfg.phpipam_habilitado = request.POST.get('phpipam_habilitado') == 'on'
    cfg.phpipam_url        = request.POST.get('phpipam_url', '').strip() or None
    cfg.phpipam_usuario    = request.POST.get('phpipam_usuario', '').strip() or None
    cfg.phpipam_senha      = request.POST.get('phpipam_senha', '').strip() or None

    cfg.netbox_habilitado  = request.POST.get('netbox_habilitado') == 'on'
    cfg.netbox_url         = request.POST.get('netbox_url', '').strip() or None
    cfg.netbox_token       = request.POST.get('netbox_token', '').strip() or None

    cfg.save()

    # Fechar tunnels antigos e invalidar sessões → serão recriados com nova config
    _close_all_tunnels()
    _invalidate_session(int(cliente_id), 'phpipam')
    _invalidate_session(int(cliente_id), 'netbox')

    return JsonResponse({'success': True, 'message': 'Configuração salva!'})


# ============================================================
# VIEW: Buscar configuração (AJAX)
# ============================================================

@login_required(login_url='login')
def buscar_doc_config(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)

    if not request.user.is_staff and not request.user.is_superuser:
        try:
            c = Cliente.objects.get(usuario=request.user)
            if c.id != cliente.id:
                return JsonResponse({'error': 'Sem permissão'}, status=403)
        except Cliente.DoesNotExist:
            return JsonResponse({'error': 'Sem permissão'}, status=403)

    try:
        cfg = DocumentacaoRedeConfig.objects.get(cliente=cliente)
        return JsonResponse({
            'phpipam_habilitado': cfg.phpipam_habilitado,
            'phpipam_url':        cfg.phpipam_url or '',
            'phpipam_usuario':    cfg.phpipam_usuario or '',
            'phpipam_senha':      cfg.phpipam_senha or '',
            'netbox_habilitado':  cfg.netbox_habilitado,
            'netbox_url':         cfg.netbox_url or '',
            'netbox_token':       cfg.netbox_token or '',
        })
    except DocumentacaoRedeConfig.DoesNotExist:
        return JsonResponse({
            'phpipam_habilitado': False, 'phpipam_url': '',
            'phpipam_usuario': '', 'phpipam_senha': '',
            'netbox_habilitado': False, 'netbox_url': '',
            'netbox_token': '',
        })


# ============================================================
# VIEW: Proxy reverso principal
# ============================================================

@csrf_exempt
@xframe_options_exempt
@login_required(login_url='login')
def proxy_documentacao(request, cliente_id, tipo, path=''):
    """
    Proxy HTTP reverso para PHP IPAM / NetBox.
    Rota: /clientes/doc/proxy/<cliente_id>/<tipo>/<path>
    """
    cliente = get_object_or_404(Cliente, id=cliente_id)

    # Permissão
    if not request.user.is_staff and not request.user.is_superuser:
        try:
            c = Cliente.objects.get(usuario=request.user)
            if c.id != cliente.id:
                return HttpResponse('Sem permissão', status=403)
        except Cliente.DoesNotExist:
            return HttpResponse('Sem permissão', status=403)

    # Buscar config
    try:
        cfg = DocumentacaoRedeConfig.objects.get(cliente=cliente)
    except DocumentacaoRedeConfig.DoesNotExist:
        return HttpResponse(
            _error_page('Documentação de rede não configurada para este cliente.'),
            content_type='text/html; charset=utf-8', status=404,
        )

    # Selecionar URL base
    if tipo == 'phpipam':
        if not cfg.phpipam_habilitado or not cfg.phpipam_url:
            return HttpResponse(
                _error_page('PHP IPAM não está habilitado. Configure na aba Documentação.'),
                content_type='text/html; charset=utf-8', status=404,
            )
        base_url = cfg.phpipam_url.rstrip('/')
    elif tipo == 'netbox':
        if not cfg.netbox_habilitado or not cfg.netbox_url:
            return HttpResponse(
                _error_page('NetBox não está habilitado. Configure na aba Documentação.'),
                content_type='text/html; charset=utf-8', status=404,
            )
        base_url = cfg.netbox_url.rstrip('/')
    else:
        return HttpResponse('Tipo inválido', status=400)

    # Parsear URL
    parsed      = urlparse(base_url)
    target_host = parsed.hostname
    target_port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    scheme      = parsed.scheme
    base_path   = parsed.path.rstrip('/')

    # Montar path completo
    sub_path  = '/' + path.lstrip('/') if path else '/'
    full_path = base_path + sub_path
    qs = request.META.get('QUERY_STRING', '')
    if qs:
        full_path += '?' + qs

    # Proxy SSH (se IP privado)
    proxy_srv = None
    if _is_private(target_host):
        proxy_srv = ProxyServer.objects.filter(cliente=cliente, ativo=True).first()
        if not proxy_srv:
            return HttpResponse(
                _error_page(
                    f'O endereço <strong>{target_host}</strong> é privado mas '
                    f'não há tunnel SSH ativo para este cliente.<br>'
                    f'Configure um em <strong>Túneis SSH</strong>.'
                ),
                content_type='text/html; charset=utf-8', status=503,
            )

    # Montar cabeçalhos
    req_headers = dict(request.headers)

    # NetBox usa token de API — enviar sempre
    if tipo == 'netbox' and cfg.netbox_token:
        req_headers['Authorization'] = f'Token {cfg.netbox_token}'

    # PHP IPAM: injetar cookie de sessão autenticado automaticamente
    if tipo == 'phpipam' and cfg.phpipam_usuario:
        session_cookies = _get_phpipam_cookies(
            cfg, cliente_id, target_host, target_port, base_path, proxy_srv
        )
        if session_cookies:
            # Mesclar com cookies do browser (browser pode ter outros cookies)
            existing = req_headers.get('Cookie', '')
            extra = '; '.join(f'{k}={v}' for k, v in session_cookies.items())
            req_headers['Cookie'] = (existing + '; ' + extra).strip('; ')

    # Body
    body = b''
    if request.method in ('POST', 'PUT', 'PATCH'):
        body = request.body

    # Executar requisição
    resp = _do_request(target_host, target_port, scheme,
                       full_path, request.method,
                       req_headers, body, proxy_srv)

    if resp is None:
        return HttpResponse(
            _error_page(f'Não foi possível conectar a <strong>{base_url}</strong>'),
            content_type='text/html; charset=utf-8', status=502,
        )

    # Sessão PHP IPAM expirou — relogar e tentar novamente
    if tipo == 'phpipam' and resp.status_code in (401, 403):
        _invalidate_session(cliente_id, 'phpipam')
        session_cookies = _get_phpipam_cookies(
            cfg, cliente_id, target_host, target_port, base_path, proxy_srv
        )
        if session_cookies:
            existing = req_headers.get('Cookie', '')
            extra = '; '.join(f'{k}={v}' for k, v in session_cookies.items())
            req_headers['Cookie'] = (existing + '; ' + extra).strip('; ')
            resp = _do_request(target_host, target_port, scheme,
                               full_path, request.method,
                               req_headers, body, proxy_srv)

    proxy_base = f'/clientes/doc/proxy/{cliente_id}/{tipo}'

    # Tratar redirecionamentos
    if resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get('Location', '/')
        parsed_loc = urlparse(location)
        # Converter para rota do proxy se for redirect interno
        if not parsed_loc.netloc or parsed_loc.netloc in (
            target_host,
            f'{target_host}:{target_port}',
            '127.0.0.1',
        ):
            new_path = parsed_loc.path or '/'
            new_qs   = ('?' + parsed_loc.query) if parsed_loc.query else ''
            # Remover o base_path para obter path relativo
            if new_path.startswith(base_path):
                new_path = new_path[len(base_path):]
            location = proxy_base + new_path + new_qs

        response = HttpResponse(status=resp.status_code)
        response['Location'] = location
        return response

    # Conteúdo
    content_type = resp.headers.get('Content-Type', 'text/html; charset=utf-8')
    content      = resp.content

    if 'text/html' in content_type:
        prefill_u = cfg.phpipam_usuario or '' if tipo == 'phpipam' else ''
        prefill_p = cfg.phpipam_senha or '' if tipo == 'phpipam' else ''
        content      = _rewrite_html(content, proxy_base, prefill_u, prefill_p)
        content_type = 'text/html; charset=utf-8'

    # Headers a remover da resposta do destino (impedem iframe)
    STRIP_HEADERS = {
        'x-frame-options',
        'content-security-policy',
        'x-content-security-policy',
        'x-webkit-csp',
        'content-security-policy-report-only',
        'content-encoding',   # já descomprimimos
        'transfer-encoding',  # já lemos completo
    }

    response = HttpResponse(content, content_type=content_type,
                            status=resp.status_code)

    # Copiar apenas headers seguros do destino
    for k, v in resp.headers.items():
        if k.lower() not in STRIP_HEADERS:
            try:
                response[k] = v
            except Exception:
                pass

    # Garantir remoção mesmo que Django middleware adicione depois
    if 'X-Frame-Options' in response:
        del response['X-Frame-Options']
    if 'Content-Security-Policy' in response:
        del response['Content-Security-Policy']

    # Propagar cookies do destino para o browser
    for c in resp.cookies:
        response.set_cookie(
            c.name, c.value,
            path='/',
            secure=False,
            samesite='Lax',
        )
    # Fallback: Set-Cookie direto no header
    raw_sc = resp.headers.get('Set-Cookie', '')
    if raw_sc and not resp.cookies:
        name_val = raw_sc.split(';')[0].strip()
        if '=' in name_val:
            cname, cval = name_val.split('=', 1)
            response.set_cookie(cname.strip(), cval.strip(), path='/', samesite='Lax')

    return response


# ============================================================
# VIEW: Verificar proxy ativo
# ============================================================

@login_required(login_url='login')
def proxy_ativo_cliente(request):
    cliente_id = request.GET.get('cliente_id')
    if not cliente_id:
        return JsonResponse({'tem_proxy_ativo': False})

    proxy = ProxyServer.objects.filter(cliente_id=cliente_id, ativo=True).first()
    return JsonResponse({
        'tem_proxy_ativo': bool(proxy),
        'nome': proxy.nome if proxy else None,
        'host': proxy.host if proxy else None,
    })


# ---- Helper página de erro ----

def _error_page(msg: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{{background:#0d1117;color:#ccc;font-family:'Courier New',monospace;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
  .box{{background:#161b22;border:1px solid #30363d;border-radius:8px;
        padding:40px;max-width:520px;text-align:center}}
  p{{color:#8b949e;line-height:1.6}}
</style></head><body>
<div class="box">
  <div style="font-size:48px;margin-bottom:16px">⚠️</div>
  <h2 style="color:#f85149">Erro de Conexão</h2>
  <p>{msg}</p>
</div></body></html>"""