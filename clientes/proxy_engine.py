"""
proxy_engine.py — Motor de Proxy Web HTTP/HTTPS via túnel SSH (Paramiko)

Arquitetura:
  1. SSHConnectionPool  — mantém conexões SSH persistentes por proxy_server.id
  2. LocalTunnelServer  — cria um socket TCP local que encaminha tráfego para o
                          equipamento via Paramiko direct-tcpip.
  3. ProxyEngine        — abre socket TCP real para o túnel local, aplica SSL com
                          server_hostname correto (SNI real do equipamento) e executa
                          a requisição via http.client com socket customizado.

Por que http.client em vez de requests para o túnel:
  - requests/urllib3 define SNI a partir do hostname da URL (127.0.0.1).
  - ssl.wrap_socket(socket_real, server_hostname=ip_equipamento) define SNI correto.
  - Equipamentos embedded (OLTs, roteadores, switches) precisam do SNI correto.
"""

import re
import ssl
import gzip
import zlib
import socket
import select
import hashlib
import os as _os
import threading
import ipaddress
import logging
import http.client
from urllib.parse import urlparse

import paramiko
import requests
import urllib3
from requests.structures import CaseInsensitiveDict
from requests.auth import HTTPDigestAuth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Pool de Conexões SSH
# ──────────────────────────────────────────────────────────────────────────────

class SSHConnectionPool:
    """
    Singleton — uma conexão SSH por servidor proxy.
    Reconecta automaticamente caso o transport esteja morto.
    """
    _instance = None
    _lock     = threading.Lock()
    _pool     = {}  # proxy_id -> paramiko.Transport

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def get_transport(self, proxy) -> paramiko.Transport:
        """Retorna um paramiko.Transport ativo, criando/reconectando se necessário."""
        pid = proxy.id

        with self._lock:
            transport = self._pool.get(pid)

            # Verificar se ainda está vivo
            if transport is not None:
                if transport.is_active():
                    try:
                        transport.send_ignore()
                        return transport
                    except Exception:
                        pass
                try:
                    transport.close()
                except Exception:
                    pass
                del self._pool[pid]

            logger.info("[SSH_POOL] Conectando ao proxy SSH %s:%s", proxy.host, proxy.porta)
            t = paramiko.Transport((proxy.host, int(proxy.porta)))
            t.set_keepalive(30)

            try:
                t.start_client(timeout=15)
            except Exception as e:
                raise RuntimeError(f"Handshake SSH falhou com {proxy.host}: {e}")

            auth_methods = ['password', 'keyboard-interactive']
            try:
                t.auth_none(proxy.usuario)
            except paramiko.BadAuthenticationType as exc:
                auth_methods = exc.allowed_types
            except Exception:
                pass

            authed = False
            if 'password' in auth_methods:
                try:
                    t.auth_password(proxy.usuario, proxy.senha)
                    authed = True
                except paramiko.AuthenticationException:
                    pass

            if not authed and 'keyboard-interactive' in auth_methods:
                try:
                    t.auth_interactive(
                        proxy.usuario,
                        lambda title, instr, prompts: [proxy.senha] * len(prompts)
                    )
                    authed = True
                except paramiko.AuthenticationException:
                    pass

            if not authed:
                t.close()
                raise RuntimeError(f"Autenticação SSH falhou para {proxy.usuario}@{proxy.host}")

            self._pool[pid] = t
            logger.info("[SSH_POOL] Conectado ao proxy %s (%s)", proxy.nome, proxy.host)
            return t

    def invalidate(self, proxy_id: int):
        with self._lock:
            t = self._pool.pop(proxy_id, None)
            if t:
                try:
                    t.close()
                except Exception:
                    pass

    def close_all(self):
        with self._lock:
            for t in self._pool.values():
                try:
                    t.close()
                except Exception:
                    pass
            self._pool.clear()


# ──────────────────────────────────────────────────────────────────────────────
# 2. Servidor local de port-forward via Paramiko
# ──────────────────────────────────────────────────────────────────────────────

class LocalTunnelServer:
    """
    Inicia um socket TCP local (porta efêmera) e encaminha cada conexão
    recebida via Paramiko direct-tcpip para (target_host, target_port).

    Uso:
        with LocalTunnelServer(transport, '10.0.0.1', 443) as local_port:
            raw = socket.create_connection(('127.0.0.1', local_port), timeout=10)
            # raw é um socket TCP real → pode ser envolvido com ssl.wrap_socket
    """

    def __init__(self, transport: paramiko.Transport, target_host: str, target_port: int):
        self.transport   = transport
        self.target_host = target_host
        self.target_port = target_port
        self._stop       = threading.Event()
        self._server     = None
        self.local_port  = None

    def __enter__(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(('127.0.0.1', 0))
        self._server.listen(32)
        self._server.settimeout(1.0)
        self.local_port = self._server.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self.local_port

    def __exit__(self, *_):
        self._stop.set()
        try:
            self._server.close()
        except Exception:
            pass

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, client_sock: socket.socket):
        try:
            channel = self.transport.open_channel(
                'direct-tcpip',
                (self.target_host, self.target_port),
                ('127.0.0.1', 0),
                timeout=15,
            )
        except Exception as e:
            logger.error("[TUNNEL] Falha direct-tcpip → %s:%s: %s",
                         self.target_host, self.target_port, e)
            client_sock.close()
            return
        self._bridge(client_sock, channel)

    @staticmethod
    def _bridge(sock: socket.socket, channel: paramiko.Channel):
        """
        Encaminha bytes entre o socket TCP local e o canal SSH.

        ATENÇÃO: channel.fileno() retorna o FD do transporte SSH inteiro,
        não do canal específico. Por isso, select pode acordar este bridge
        quando os dados são de OUTRO canal simultâneo. Nesse caso,
        channel.recv() levanta socket.timeout (canal sem dados agora).
        Isso deve ser ignorado (continue), nunca tratado como EOF (return).
        """
        sock.settimeout(0)
        channel.settimeout(0)
        try:
            while True:
                r, _, _ = select.select([sock, channel], [], [], 5.0)
                if not r:
                    continue
                for src in r:
                    try:
                        data = src.recv(65536)
                    except socket.timeout:
                        # select acordou por dados de outro canal SSH — ignorar
                        continue
                    except Exception:
                        return  # erro real de I/O — encerrar
                    if not data:
                        return  # EOF — encerrar
                    dst = channel if src is sock else sock
                    try:
                        dst.sendall(data)
                    except Exception:
                        return
        finally:
            for s in (sock, channel):
                try:
                    s.close()
                except Exception:
                    pass


# ──────────────────────────────────────────────────────────────────────────────
# 3. Motor principal
# ──────────────────────────────────────────────────────────────────────────────

class ProxyEngine:
    """
    Decide a rota (direto vs. túnel SSH) e executa a requisição HTTP/HTTPS.

    Para IPs privados via túnel SSH:
      - Cria LocalTunnelServer (TCP local → SSH → equipamento)
      - Conecta com socket.create_connection() → socket TCP real
      - Para HTTPS: ssl.wrap_socket(socket_real, server_hostname=ip_equipamento)
        → SNI correto enviado ao equipamento
      - Executa requisição com http.client usando o socket pré-conectado
    """

    STRIP_HEADERS = {
        'x-frame-options',
        'content-security-policy',
        'x-content-security-policy',
        'x-webkit-csp',
        'content-security-policy-report-only',
        'content-encoding',
        'content-length',   # Django recalcula após rewrite; original truncaria a resposta
        'transfer-encoding',
        'connection',
        'keep-alive',
        'public-key-pins',
        'strict-transport-security',
    }

    _ssh_pool = SSHConnectionPool()

    def __init__(self, proxy_server=None, device_username: str = None, device_password: str = None):
        self.proxy_server    = proxy_server
        self.device_username = device_username or ''
        self.device_password = device_password or ''

    # ── Helpers estáticos ─────────────────────────────────────────────────────

    @staticmethod
    def is_private_ip(hostname: str) -> bool:
        try:
            return ipaddress.ip_address(hostname).is_private
        except (ValueError, TypeError):
            return False

    @staticmethod
    def get_error_page(msg: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<title>Erro de Conexão</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#cdd9e5;font-family:'Segoe UI',system-ui,sans-serif;
     display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:12px;
       padding:48px 40px;max-width:540px;text-align:center;box-shadow:0 8px 32px #0006}}
.icon{{font-size:56px;margin-bottom:20px}}
h2{{color:#f85149;font-size:1.6rem;margin-bottom:16px}}
p{{color:#8b949e;line-height:1.7;font-size:.95rem}}
code{{background:#21262d;padding:2px 6px;border-radius:4px;font-size:.85rem;color:#79c0ff}}
</style></head><body>
<div class="card">
<div class="icon">&#9888;&#65039;</div>
<h2>Erro de Conexão</h2>
<p>{msg}</p>
</div></body></html>"""

    # ── Contexto SSL permissivo para equipamentos embedded ────────────────────

    @staticmethod
    def _make_ssl_context(server_hostname: str) -> ssl.SSLContext:
        """
        Cria SSLContext sem verificação de certificado e com suporte a TLS antigo.
        O server_hostname é usado APENAS para SNI — não para validar o cert.
        """
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE

        # TLS 1.0/1.1 são comuns em equipamentos antigos; tentar do mais antigo
        for ver in ('TLSv1', 'TLSv1_1', 'TLSv1_2'):
            try:
                ctx.minimum_version = getattr(ssl.TLSVersion, ver)
                break
            except (AttributeError, ssl.SSLError, OSError):
                continue

        # Reduzir nível de segurança de cifras (SECLEVEL=0 aceita RSA 512+)
        for ciphers in ('DEFAULT@SECLEVEL=0', 'DEFAULT'):
            try:
                ctx.set_ciphers(ciphers)
                break
            except ssl.SSLError:
                continue

        return ctx

    # ── Método principal ──────────────────────────────────────────────────────

    def do_request(self, method: str, url: str,
                   headers: dict = None, body: bytes = None,
                   timeout: tuple = (12, 30)):
        """
        Executa requisição HTTP/HTTPS.
        Retorna objeto _Resp ou None em caso de falha.
        """
        parsed      = urlparse(url)
        target_host = parsed.hostname
        target_port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        scheme      = parsed.scheme

        use_tunnel = bool(self.proxy_server and self.is_private_ip(target_host))

        if use_tunnel:
            return self._via_tunnel(method, url, target_host, target_port, scheme,
                                    headers or {}, body, timeout)
        else:
            return self._direct(method, url, headers or {}, body, timeout)

    # ── Digest Auth ───────────────────────────────────────────────────────────

    @staticmethod
    def _compute_digest_auth(method: str, uri: str, username: str,
                              password: str, www_auth: str) -> str:
        """
        Computa o valor do header Authorization: Digest para resposta a
        WWW-Authenticate: Digest.  Suporta qop=auth e sem qop.
        """
        def _find(pattern, s):
            m = re.search(pattern, s, re.IGNORECASE)
            return m.group(1) if m else ''

        realm  = _find(r'realm="([^"]*)"',   www_auth)
        nonce  = _find(r'nonce="([^"]*)"',   www_auth)
        opaque = _find(r'opaque="([^"]*)"',  www_auth)
        qop_s  = _find(r'qop="?([^",\s]+)', www_auth)

        if not realm or not nonce:
            return ''

        def md5h(s: str) -> str:
            return hashlib.md5(s.encode('utf-8')).hexdigest()

        ha1 = md5h(f'{username}:{realm}:{password}')
        ha2 = md5h(f'{method.upper()}:{uri}')

        if 'auth' in qop_s:
            nc     = '00000001'
            cnonce = _os.urandom(8).hex()
            response = md5h(f'{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}')
            hdr = (f'Digest username="{username}", realm="{realm}", '
                   f'nonce="{nonce}", uri="{uri}", '
                   f'qop=auth, nc={nc}, cnonce="{cnonce}", '
                   f'response="{response}"')
        else:
            response = md5h(f'{ha1}:{nonce}:{ha2}')
            hdr = (f'Digest username="{username}", realm="{realm}", '
                   f'nonce="{nonce}", uri="{uri}", '
                   f'response="{response}"')

        if opaque:
            hdr += f', opaque="{opaque}"'
        return hdr

    # ── Requisição direta (IPs públicos) ──────────────────────────────────────

    def _direct(self, method, url, headers, body, timeout):
        try:
            parsed_url = urlparse(url)
            port = parsed_url.port
            host_hdr = (parsed_url.hostname if port in (None, 80, 443)
                        else f'{parsed_url.hostname}:{port}')
            device_origin = f'{parsed_url.scheme}://{host_hdr}'

            merged = {**self._base_headers(),
                      'Origin':  device_origin,
                      'Referer': f'{device_origin}{parsed_url.path or "/"}'}
            for k, v in headers.items():
                kl = k.lower()
                if kl in ('origin', 'referer'):
                    continue
                # Não repassar Authorization: Digest do browser (URI incorreta);
                # o requests+HTTPDigestAuth cuida disso automaticamente
                if kl == 'authorization' and isinstance(v, str) and v.strip().lower().startswith('digest '):
                    continue
                clean_v = self._sanitize_header(k, v)
                if clean_v is not None:
                    merged[k] = clean_v

            # Se temos credenciais, requests faz o handshake Digest automaticamente
            digest_auth = (HTTPDigestAuth(self.device_username, self.device_password)
                           if self.device_username else None)

            r = requests.request(
                method=method, url=url, headers=merged,
                data=body or None, verify=False,
                allow_redirects=False, timeout=timeout,
                auth=digest_auth,
            )
            return self._wrap(r)
        except Exception as e:
            logger.error("[PROXY_DIRECT] %s %s → %s", method, url, e)
            return None

    # ── Requisição via túnel SSH ──────────────────────────────────────────────

    def _via_tunnel(self, method, url, target_host, target_port, scheme,
                    headers, body, timeout):
        """
        Fluxo:
          1. Obtém transport SSH do pool
          2. LocalTunnelServer cria porta TCP local → SSH → equipamento
          3. socket.create_connection('127.0.0.1', local_port) → socket TCP real
          4. HTTPS: ssl.wrap_socket(socket_real, server_hostname=target_host)
             → handshake TLS com SNI correto (ip do equipamento, não 127.0.0.1)
          5. http.client.HTTPConnection com conn.sock = socket_pronto
          6. Retorna resposta sem seguir redirects
             (proxy_web_acesso na view já reescreve Location e devolve ao browser)
        """
        try:
            transport = self._ssh_pool.get_transport(self.proxy_server)
        except Exception as e:
            logger.error("[TUNNEL] Falha ao obter transport SSH: %s", e)
            return None

        parsed   = urlparse(url)
        path     = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query

        host_hdr = (target_host
                    if target_port in (80, 443)
                    else f'{target_host}:{target_port}')

        # Origin e Referer devem SEMPRE apontar para o equipamento.
        # views.py não os repassa; e mesmo que o browser os enviasse, apontariam
        # para crm.tomich.com.br — o que faz dispositivos com CSRF retornarem 403.
        device_origin = f'{scheme}://{host_hdr}'
        req_headers = {
            'Host':            host_hdr,
            'Origin':          device_origin,
            'Referer':         f'{device_origin}{parsed.path or "/"}',
            'User-Agent':      ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                'AppleWebKit/537.36 (KHTML, like Gecko) '
                                'Chrome/124.0 Safari/537.36'),
            'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
            'Connection':      'close',
        }
        # NÃO enviar Accept-Encoding — evita ter que descomprimir manualmente
        # NÃO repassar Authorization: Digest do browser — a URI no hash aponta para
        # o path do proxy, não para o path real do equipamento; o hash não confere.
        # O proxy faz a negociação Digest transparentemente usando device_username/password.
        for k, v in headers.items():
            kl = k.lower()
            if kl in ('accept-encoding', 'origin', 'referer'):
                continue
            if (kl == 'authorization' and isinstance(v, str)
                    and v.strip().lower().startswith('digest ')):
                continue  # Recomputado abaixo se o equipamento exigir Digest
            clean_v = self._sanitize_header(k, v)
            if clean_v is not None:
                req_headers[k] = clean_v

        try:
            with LocalTunnelServer(transport, target_host, target_port) as local_port:
                logger.info("[TUNNEL] %s %s → local_port=%d", method, url, local_port)

                def _open_socket():
                    """Abre (e opcionalmente envolve com SSL) um socket para o túnel."""
                    try:
                        s = socket.create_connection(
                            ('127.0.0.1', local_port), timeout=timeout[0]
                        )
                        s.settimeout(timeout[1])
                    except Exception as e:
                        logger.error("[TUNNEL] Falha ao conectar na porta local %d: %s",
                                     local_port, e)
                        return None
                    if scheme == 'https':
                        try:
                            ctx = self._make_ssl_context(target_host)
                            cs = ctx.wrap_socket(s, server_hostname=target_host)
                            cs.settimeout(timeout[1])
                            logger.info("[TUNNEL] SSL OK (SNI=%s)", target_host)
                            return cs
                        except Exception as e:
                            logger.error("[TUNNEL] SSL falhou (SNI=%s): %s", target_host, e)
                            try:
                                s.close()
                            except Exception:
                                pass
                            return None
                    return s

                def _do_request(sock, hdrs):
                    """Executa uma requisição HTTP no socket e retorna (http_resp, content, cookies_raw)."""
                    conn = http.client.HTTPConnection('_')
                    conn.sock = sock
                    conn.request(method, path,
                                 body=body if method in ('POST', 'PUT', 'PATCH') else None,
                                 headers=hdrs)
                    r = conn.getresponse()
                    c = r.read()
                    ck = [v for n, v in r.headers.items() if n.lower() == 'set-cookie']
                    return r, c, ck

                # ── 1ª tentativa ──────────────────────────────────────────────
                conn_sock = _open_socket()
                if conn_sock is None:
                    return None

                try:
                    resp, content, cookies_raw = _do_request(conn_sock, req_headers)
                    logger.info("[TUNNEL] status=%d size=%d", resp.status, len(content))
                except Exception as e:
                    logger.error("[TUNNEL] Erro na requisição HTTP: %s", e)
                    return None
                finally:
                    try:
                        conn_sock.close()
                    except Exception:
                        pass

                # ── Retry com Digest se necessário ────────────────────────────
                if (resp.status == 401 and self.device_username):
                    www_auth = resp.getheader('WWW-Authenticate', '')
                    if 'Digest' in www_auth:
                        auth_hdr = self._compute_digest_auth(
                            method, path,
                            self.device_username, self.device_password,
                            www_auth,
                        )
                        if auth_hdr:
                            logger.info("[TUNNEL] 401 Digest → retry com credenciais")
                            retry_headers = {**req_headers, 'Authorization': auth_hdr}
                            conn_sock2 = _open_socket()
                            if conn_sock2 is not None:
                                try:
                                    resp, content, cookies_raw = _do_request(
                                        conn_sock2, retry_headers
                                    )
                                    logger.info("[TUNNEL] retry status=%d size=%d",
                                                resp.status, len(content))
                                except Exception as e:
                                    logger.error("[TUNNEL] Erro no retry Digest: %s", e)
                                finally:
                                    try:
                                        conn_sock2.close()
                                    except Exception:
                                        pass

                return self._build_resp(resp, content, cookies_raw)

        except Exception as e:
            logger.exception("[TUNNEL] Erro inesperado: %s", e)
            if self.proxy_server:
                self._ssh_pool.invalidate(self.proxy_server.id)
            return None

    # ── Construir objeto de resposta a partir de http.client.HTTPResponse ─────

    @staticmethod
    def _build_resp(http_resp: http.client.HTTPResponse,
                    content: bytes, cookies_raw: list):
        """
        Converte http.client.HTTPResponse em _Resp compatível com
        o restante do código (proxy_web_acesso em views.py).
        """
        # Descomprimir se o servidor ignorou nosso pedido e comprimiu mesmo assim
        enc = http_resp.headers.get('Content-Encoding', '').lower()
        if enc == 'gzip':
            try:
                content = gzip.decompress(content)
            except Exception:
                pass
        elif enc == 'deflate':
            try:
                content = zlib.decompress(content)
            except Exception:
                try:
                    content = zlib.decompress(content, -15)
                except Exception:
                    pass

        class _Resp:
            pass

        r              = _Resp()
        r.status_code  = http_resp.status
        r.content      = content
        r.cookies_raw  = cookies_raw
        r.content_type = http_resp.headers.get('Content-Type', 'text/html')

        # CaseInsensitiveDict garante que views.py possa fazer
        # resp.headers.get('Content-Type') ou resp.headers.get('Location')
        # independente do capitalização enviada pelo servidor.
        r.headers = CaseInsensitiveDict()
        for name, value in http_resp.headers.items():
            if name.lower() != 'set-cookie':
                r.headers[name] = value

        return r

    # ── Helpers internos ──────────────────────────────────────────────────────

    @staticmethod
    def _base_headers() -> dict:
        return {
            'User-Agent':      ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                'AppleWebKit/537.36 (KHTML, like Gecko) '
                                'Chrome/124.0 Safari/537.36'),
            'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
        }

    @staticmethod
    def _sanitize_header(name: str, value: str):
        if not isinstance(value, str):
            return None
        if name.lower() == 'cookie':
            pairs = []
            for part in value.split(';'):
                part = part.strip()
                if not part or '=' not in part:
                    continue
                k, v = part.split('=', 1)
                k = k.strip()
                v = v.strip()
                if not k:
                    continue
                pairs.append(f'{k}={v}')
            return '; '.join(pairs) if pairs else None
        clean = value.replace('\r', '').replace('\n', '').strip()
        return clean if clean else None

    @staticmethod
    def _wrap(r: requests.Response, extra_cookies: list = None):
        """Converte requests.Response em _Resp (usado pelo método _direct)."""
        content = r.content
        enc     = r.headers.get('Content-Encoding', '').lower()
        if enc == 'gzip':
            try:
                content = gzip.decompress(content)
            except Exception:
                pass
        elif enc == 'deflate':
            try:
                content = zlib.decompress(content)
            except Exception:
                try:
                    content = zlib.decompress(content, -15)
                except Exception:
                    pass

        cookies_raw = list(extra_cookies or [])
        if hasattr(r.raw, 'headers'):
            for cv in r.raw.headers.getlist('Set-Cookie'):
                if cv not in cookies_raw:
                    cookies_raw.append(cv)

        class _Resp:
            pass

        resp              = _Resp()
        resp.status_code  = r.status_code
        resp.headers      = CaseInsensitiveDict(
            {k: v for k, v in r.headers.items() if k.lower() != 'set-cookie'}
        )
        resp.content      = content
        resp.cookies_raw  = cookies_raw
        resp.content_type = r.headers.get('Content-Type', 'text/html')
        return resp

    # ── Reescrita de conteúdo ─────────────────────────────────────────────────

    def rewrite_content(self, content: bytes, content_type: str,
                        proxy_base: str, target_host: str,
                        cookie_prefix: str = '') -> bytes:
        if not content:
            return content
        ct = content_type.lower()
        if 'text/html' in ct:
            return self._rewrite_html(content, proxy_base, target_host, cookie_prefix)
        if 'text/css' in ct:
            return self._rewrite_css(content, proxy_base)
        return content

    def _rewrite_html(self, content: bytes, proxy_base: str, target_host: str,
                      cookie_prefix: str = '') -> bytes:
        try:
            html = content.decode('utf-8', errors='replace')
        except Exception:
            return content

        for proto in ('https', 'http'):
            for suffix in (f':{80}', f':{443}', ''):
                old = f'{proto}://{target_host}{suffix}'
                html = html.replace(old, proxy_base)

        for attr in ('href', 'src', 'action', 'data-src', 'data-url'):
            html = re.sub(
                rf'({attr}\s*=\s*["\'])/(?!clientes/acessos)',
                rf'\1{proxy_base}/',
                html
            )

        html = re.sub(
            r'(url\s*\(\s*["\']?)/(?!clientes/acessos)',
            rf'\1{proxy_base}/',
            html
        )

        html = re.sub(
            r'<meta[^>]+http-equiv=["\']refresh["\'][^>]*>',
            '<!-- meta-refresh removido pelo proxy -->',
            html, flags=re.IGNORECASE
        )

        # NOTA: <base href> foi removido intencionalmente.
        # Com <base href>, URLs relativas no JS (ex: location.href="login.html")
        # resolvem contra a base do proxy, quebrando navegação de devices que usam
        # paths relativos. Sem <base href>, resolvem contra o URL atual do browser,
        # que já inclui o path correto do proxy (/acessos/ID/web/PORTA/SCHEME/DIR/).
        inject = f"""<script>
(function(){{
  var B='{proxy_base}';
  var P='{cookie_prefix}';
  // Intercepta document.cookie para isolar cookies por acesso:
  // getter: devolve apenas cookies com prefixo P (sem o prefixo);
  // setter: adiciona prefixo P antes de armazenar.
  // Isso impede que AIROS_SESSIONID (ou qualquer cookie de sessão) de um
  // equipamento vaze para requisições de outro equipamento.
  if(P){{
    (function(){{
      var _d=Object.getOwnPropertyDescriptor(Document.prototype,'cookie')||
             Object.getOwnPropertyDescriptor(HTMLDocument.prototype,'cookie');
      if(!_d||!_d.set)return;
      var _cg=_d.get,_cs=_d.set;
      Object.defineProperty(document,'cookie',{{
        configurable:true,
        get:function(){{
          var raw=_cg.call(this)||'';
          var out=[];
          raw.split(';').forEach(function(c){{
            c=c.trim();var eq=c.indexOf('=');
            var nm=eq>=0?c.substring(0,eq).trim():c.trim();
            var vl=eq>=0?c.substring(eq+1):'';
            if(nm.indexOf(P)===0)out.push(nm.substring(P.length)+'='+vl);
          }});
          return out.join('; ');
        }},
        set:function(v){{
          var parts=v.split(';');
          var first=parts[0];
          var eq=first.indexOf('=');
          if(eq>=0){{
            var nm=first.substring(0,eq).trim();
            if(nm&&nm.indexOf(P)!==0){{
              parts[0]=P+first.trim();
              v=parts.join(';');
            }}
          }}
          return _cs.call(this,v);
        }}
      }});
    }})();
  }}
  // Reescreve qualquer URL que aponte para o mesmo origin mas fora do proxy_base.
  // Aceita: string relativa (/path), string absoluta (https://host/path),
  //         objeto URL, objeto Request.
  function _rw(u){{
    try{{
      if(typeof u==='string'){{
        if(u.startsWith('/')&&!u.startsWith(B))return B+u;
        var p=new URL(u);
        if(p.origin===location.origin){{
          var path=p.pathname+(p.search||'');
          if(path.startsWith('/')&&!path.startsWith(B))return B+path;
        }}
      }}else if(u&&typeof u==='object'){{
        var href=(typeof u.url==='string')?u.url:(u.href||'');
        var p2=new URL(href,location.href);
        if(p2.origin===location.origin){{
          var path2=p2.pathname+(p2.search||'');
          if(path2.startsWith('/')&&!path2.startsWith(B)){{
            if(typeof u.clone==='function')return new Request(location.origin+B+path2,u);
            return new URL(B+path2,location.origin);
          }}
        }}
      }}
    }}catch(e){{}}
    return u;
  }}
  var _f=window.fetch;
  window.fetch=function(u,o){{return _f.call(this,_rw(u),o);}};
  var _x=XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open=function(m,u){{
    var args=Array.prototype.slice.call(arguments);
    args[1]=_rw(u);
    return _x.apply(this,args);
  }};
  // Utilitário: despachar popstate+hashchange para acionar o router da SPA
  // sem recarregar a página (preservando o estado JS com role=2 pós-login).
  function _triggerRouter(){{
    try{{history.pushState(null,'',window.location.href);}}catch(e){{}}
    try{{window.dispatchEvent(new PopStateEvent('popstate',{{state:null}}));}}catch(e){{}}
    try{{window.dispatchEvent(new Event('hashchange'));}}catch(e){{}}
  }}
  try{{
    var d=Object.getOwnPropertyDescriptor(Location.prototype,'href');
    if(d&&d.set){{
      var os=d.set;
      Object.defineProperty(Location.prototype,'href',{{
        get:d.get,
        set:function(u){{
          if(u&&u.startsWith('/')&&!u.startsWith(B))u=B+u;
          // Se a SPA navega para a raiz do proxy (equivalente a '/' no device),
          // não recarregar — usar pushState para preservar estado pós-login.
          if(u===B+'/'){{_triggerRouter();return;}}
          return os.call(this,u);
        }},
        configurable:true
      }});
    }}
  }}catch(e){{}}
  // Intercepta location.reload() que a SPA chama pós-login.
  // Em vez de recarregar (que destrói role=2 e mostra login novamente),
  // dispara eventos de roteamento para o SPA resolver a rota internamente.
  try{{
    var _origReload=Location.prototype.reload;
    Location.prototype.reload=function(){{_triggerRouter();}};
  }}catch(e){{}}
  // Sobrepõe location.protocol para que o device JS veja o protocolo correto
  // (evita que firmware com "https:false" tente navegar para http://).
  try{{
    var _ppd=Object.getOwnPropertyDescriptor(Location.prototype,'protocol');
    if(_ppd&&_ppd.get){{
      var _oppg=_ppd.get;
      Object.defineProperty(Location.prototype,'protocol',{{
        get:function(){{
          var p=_oppg.call(this);
          // Se estamos no proxy (URL contém proxy_base), retornar 'https:'
          // para que o SPA não tente fazer downgrade para HTTP.
          if(window.location.href.indexOf(B)>=0)return 'https:';
          return p;
        }},
        set:function(v){{
          // Bloquear tentativa de mudar para http:// via JS
          if(typeof v==='string'&&v.replace(':','').toLowerCase()==='http')return;
          if(_ppd.set)_ppd.set.call(this,v);
        }},
        configurable:true
      }});
    }}
  }}catch(e){{}}
  // Sobrepõe location.pathname para que o SPA router veja o path real do device
  // (ex: "/" em vez de "/clientes/acessos/891/web/80/http/").
  // Sem isso, o SPA não reconhece a rota e redireciona para login.
  try{{
    var _pd=Object.getOwnPropertyDescriptor(Location.prototype,'pathname');
    if(_pd&&_pd.get){{
      var _opg=_pd.get;
      Object.defineProperty(Location.prototype,'pathname',{{
        get:function(){{
          var p=_opg.call(this);
          if(p.indexOf(B)===0)return p.slice(B.length)||'/';
          return p;
        }},
        configurable:true
      }});
    }}
  }}catch(e){{}}
  // Sobrepõe src/href de elementos HTML para que URLs absolutas sem base do proxy
  // sejam corrigidas (ex: img.src='/client/img/foo.svg' → proxy_base+'/client/img/foo.svg').
  (function(){{
    function _fixProp(Proto,prop){{
      try{{
        var _d=Object.getOwnPropertyDescriptor(Proto,prop);
        if(!_d||!_d.set)return;
        var _gs=_d.get,_ss=_d.set;
        Object.defineProperty(Proto,prop,{{
          get:_gs,
          set:function(v){{
            if(typeof v==='string'&&v.indexOf('/')===0&&v.indexOf(B)!==0)v=B+v;
            return _ss.call(this,v);
          }},
          configurable:true
        }});
      }}catch(e){{}}
    }}
    _fixProp(HTMLImageElement.prototype,'src');
    _fixProp(HTMLScriptElement.prototype,'src');
    _fixProp(HTMLMediaElement.prototype,'src');
    _fixProp(HTMLLinkElement.prototype,'href');
    _fixProp(HTMLAnchorElement.prototype,'href');
  }})();
  ['assign','replace'].forEach(function(fn){{
    var o=window.location[fn].bind(window.location);
    window.location[fn]=function(u){{if(u&&u.startsWith('/')&&!u.startsWith(B))u=B+u;return o(u);}};
  }});
  // React Router / Vue Router usam history.pushState e history.replaceState.
  // Sem interceptar, a navegação SPA muda a URL para a raiz do CRM (ex: https://crm/),
  // saindo do contexto do proxy.
  ['pushState','replaceState'].forEach(function(fn){{
    var o=history[fn].bind(history);
    history[fn]=function(st,ti,u){{
      if(typeof u==='string'&&u.startsWith('/')&&!u.startsWith(B))u=B+u;
      return o(st,ti,u);
    }};
  }});
  function _fixAction(f){{
    var fixed=_rw(f.action||'');
    if(typeof fixed==='string'&&fixed!==f.action)f.setAttribute('action',fixed);
  }}
  // 1) Intercepta submit via evento (clique no botão, Enter, etc.)
  document.addEventListener('submit',function(e){{
    if(e.target&&e.target.tagName==='FORM')_fixAction(e.target);
  }},true);
  // 2) Intercepta form.submit() programático (não dispara evento)
  (function(){{
    var _fs=HTMLFormElement.prototype.submit;
    HTMLFormElement.prototype.submit=function(){{_fixAction(this);return _fs.call(this);}};
  }})();
  document.addEventListener('DOMContentLoaded',function(){{
    document.querySelectorAll('form').forEach(function(f){{
      var a=f.getAttribute('action')||'';
      if(a.startsWith('/')&&!a.startsWith(B))f.setAttribute('action',B+a);
      f.setAttribute('autocomplete','off');
    }});
    document.querySelectorAll('input[type=text],input[type=password],input:not([type])').forEach(function(i){{
      i.setAttribute('autocomplete','off');
    }});
  }});
}})();
</script>"""

        # Tenta injetar após <head>; caso não exista, tenta <body>, <html> ou prepend
        if re.search(r'<head[^>]*>', html, re.IGNORECASE):
            html = re.sub(
                r'(<head[^>]*>)',
                r'\1\n' + inject,
                html, count=1, flags=re.IGNORECASE
            )
        elif re.search(r'<body[^>]*>', html, re.IGNORECASE):
            html = re.sub(
                r'(<body[^>]*>)',
                r'\1\n' + inject,
                html, count=1, flags=re.IGNORECASE
            )
        elif re.search(r'<html[^>]*>', html, re.IGNORECASE):
            html = re.sub(
                r'(<html[^>]*>)',
                r'\1\n' + inject,
                html, count=1, flags=re.IGNORECASE
            )
        else:
            # Resposta sem estrutura HTML (ex: bare <script>) — injetar no início
            html = inject + '\n' + html

        return html.encode('utf-8')

    def _rewrite_css(self, content: bytes, proxy_base: str) -> bytes:
        try:
            css = content.decode('utf-8', errors='replace')
            css = re.sub(
                r'url\s*\(\s*["\']?/(?!clientes/acessos)',
                f'url({proxy_base}/',
                css
            )
            return css.encode('utf-8')
        except Exception:
            return content
