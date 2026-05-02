#!/usr/bin/env python3
"""
Teste diagnóstico do proxy V3.
Uso: python3 test_proxy_v3.py <acesso_id> [porta] [scheme]
Exemplo: python3 test_proxy_v3.py 205 443 https
"""
import os, sys, django
sys.path.append('/opt/crm')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.settings')
django.setup()

from clientes.models import Acesso, ProxyServer
import paramiko
import socket
import threading
import time
import requests
import urllib3
urllib3.disable_warnings()

def test(acesso_id, target_port=443, scheme='https'):
    acesso = Acesso.objects.get(id=acesso_id)
    target_host = acesso.host.strip()
    proxy_srv = ProxyServer.objects.filter(cliente=acesso.cliente, ativo=True).first()

    if not proxy_srv:
        print(f"❌ Nenhum proxy ativo para cliente {acesso.cliente}")
        return

    print(f"═══ Teste V3: {scheme}://{target_host}:{target_port} ═══")
    print(f"    Proxy: {proxy_srv.nome} ({proxy_srv.host}:{proxy_srv.porta})")

    # 1. Conectar SSH
    print("\n[1] Conectando SSH ao proxy...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=proxy_srv.host,
            port=int(proxy_srv.porta),
            username=proxy_srv.usuario,
            password=proxy_srv.senha,
            timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )
        print(f"    ✅ SSH conectado")
    except Exception as e:
        print(f"    ❌ SSH falhou: {e}")
        return

    transport = client.get_transport()

    # 2. Testar canal direto
    print(f"\n[2] Testando canal direct-tcpip → {target_host}:{target_port}...")
    try:
        ch = transport.open_channel(
            'direct-tcpip',
            (target_host, int(target_port)),
            ('127.0.0.1', 0),
            timeout=10,
        )
        ch.close()
        print(f"    ✅ Canal abriu e fechou com sucesso")
    except Exception as e:
        print(f"    ❌ Canal falhou: {e}")
        print(f"    → O proxy SSH não consegue alcançar {target_host}:{target_port}")
        client.close()
        return

    # 3. Criar túnel local
    print(f"\n[3] Criando túnel local...")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', 0))
    srv.listen(10)
    local_port = srv.getsockname()[1]
    srv.settimeout(30)
    stop = threading.Event()

    def _copy(src, dst):
        try: src.settimeout(2.0)
        except: pass
        while not stop.is_set():
            try:
                data = src.recv(65536)
                if not data: break
                dst.sendall(data)
            except socket.timeout: continue
            except: break
        for s in (src, dst):
            try: s.close()
            except: pass

    def _forward(conn):
        try:
            ch = transport.open_channel(
                'direct-tcpip',
                (target_host, int(target_port)),
                conn.getsockname(),
                timeout=15,
            )
            threading.Thread(target=_copy, args=(conn, ch), daemon=True).start()
            threading.Thread(target=_copy, args=(ch, conn), daemon=True).start()
        except Exception as e:
            print(f"    ❌ Forward falhou: {e}")
            conn.close()

    def _accept():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                threading.Thread(target=_forward, args=(conn,), daemon=True).start()
            except socket.timeout: continue
            except: break
        srv.close()

    threading.Thread(target=_accept, daemon=True).start()
    time.sleep(0.2)
    print(f"    ✅ Túnel local em 127.0.0.1:{local_port}")

    # 4. Fazer requisição HTTP/HTTPS
    print(f"\n[4] Fazendo requisição {scheme.upper()} via túnel...")

    host_header = target_host
    if str(target_port) not in ('80', '443'):
        host_header = f"{target_host}:{target_port}"

    paths_to_test = ['/', '/index.html', '/webfig/', '/cgi/home.php',
                     '/action/login_first.html', '/cgi/login.php']

    for path in paths_to_test:
        url = f"{scheme}://127.0.0.1:{local_port}{path}"
        try:
            r = requests.get(
                url,
                headers={
                    'Host': host_header,
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                    'Accept': 'text/html,*/*',
                    'Accept-Encoding': 'identity',
                },
                allow_redirects=False,
                verify=False,
                timeout=(5, 10),
            )
            redir_info = ''
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get('Location', '?')
                redir_info = f' → {loc}'
            print(f"    {path:40s} → {r.status_code} ({len(r.content)} bytes){redir_info}")
        except requests.exceptions.ConnectionError as e:
            print(f"    {path:40s} → ❌ ConnectionError: {e}")
        except requests.exceptions.Timeout:
            print(f"    {path:40s} → ❌ Timeout")
        except Exception as e:
            print(f"    {path:40s} → ❌ {e}")

    # 5. Seguir redirects manualmente (simula V3)
    print(f"\n[5] Seguindo redirects manualmente (simulação V3)...")
    session = requests.Session()
    current_path = '/'
    for i in range(5):
        url = f"{scheme}://127.0.0.1:{local_port}{current_path}"
        r = session.get(
            url,
            headers={'Host': host_header, 'User-Agent': 'Mozilla/5.0',
                     'Accept': 'text/html,*/*', 'Accept-Encoding': 'identity'},
            allow_redirects=False, verify=False, timeout=(5, 10),
        )
        print(f"    [{i}] {current_path} → {r.status_code} ({len(r.content)} bytes)")

        if r.status_code not in (301, 302, 303, 307, 308):
            content_type = r.headers.get('Content-Type', '')
            print(f"        Content-Type: {content_type}")
            if 'html' in content_type and len(r.content) > 0:
                preview = r.content[:200].decode('utf-8', errors='replace')
                print(f"        Preview: {preview[:100]}...")
            print(f"\n    ✅ Página final obtida com sucesso!")
            break

        location = r.headers.get('Location', '')
        if not location:
            print(f"        Redirect sem Location!")
            break

        from urllib.parse import urlparse
        parsed = urlparse(location)
        if parsed.scheme and parsed.netloc:
            # Redirect absoluto
            redir_host = parsed.hostname
            new_path = parsed.path or '/'
            if parsed.query:
                new_path += '?' + parsed.query
            print(f"        Location: {location} → reescrito para {new_path}")
        else:
            new_path = location
            print(f"        Location: {location} (relativo)")

        if not new_path.startswith('/'):
            new_path = '/' + new_path
        current_path = new_path

    # Cleanup
    stop.set()
    srv.close()
    client.close()
    print("\n═══ Teste concluído ═══")


if __name__ == '__main__':
    acesso_id = int(sys.argv[1]) if len(sys.argv) > 1 else 205
    porta = int(sys.argv[2]) if len(sys.argv) > 2 else 443
    scheme = sys.argv[3] if len(sys.argv) > 3 else 'https'
    test(acesso_id, porta, scheme)
