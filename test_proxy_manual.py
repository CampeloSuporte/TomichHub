import os, sys, django
sys.path.append('/opt/crm')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.settings')
django.setup()

from clientes.models import Acesso, ProxyServer
from clientes.views import _web_get_ssh_client
import threading
import socket
import requests
import time
import urllib3
urllib3.disable_warnings()

def test_proxy(acesso_id, scheme, target_port, path):
    try:
        acesso = Acesso.objects.get(id=acesso_id)
        target_host = acesso.host
        proxy_srv = ProxyServer.objects.filter(cliente=acesso.cliente, ativo=True).first()
        if not proxy_srv:
            print(f"[{acesso_id}] No proxy")
            return
            
        print(f"[{acesso_id}] Testing {scheme}://{target_host}:{target_port}{path} via {proxy_srv.ip}")
        
        client = _web_get_ssh_client(proxy_srv)
        transport = client.get_transport()

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        local_port = srv.getsockname()[1]
        srv.settimeout(10)

        stop = threading.Event()

        def _copy(src, dst):
            try: src.settimeout(1.0)
            except: pass
            while not stop.is_set():
                try:
                    data = src.recv(65536)
                    if not data: break
                    dst.sendall(data)
                except socket.timeout: continue
                except: break
            stop.set()
            try: src.close()
            except: pass
            try: dst.close()
            except: pass

        def _tunnel_accept():
            try:
                conn, _ = srv.accept()
                ch = transport.open_channel(
                    'direct-tcpip',
                    (target_host, int(target_port)),
                    ('127.0.0.1', local_port),
                    timeout=15,
                )
                threading.Thread(target=_copy, args=(conn, ch), daemon=True).start()
                threading.Thread(target=_copy, args=(ch, conn), daemon=True).start()
            except Exception as e:
                print("Tunnel error:", e)
                stop.set()
                
        threading.Thread(target=_tunnel_accept, daemon=True).start()
        
        url = f"{scheme}://127.0.0.1:{local_port}{path}"
        headers = {
            'Host': target_host if str(target_port) in ('80', '443') else f"{target_host}:{target_port}",
            'Connection': 'close',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept': 'text/html,application/xhtml+xml,*/*',
            'Referer': f"{scheme}://{target_host}/",
            'Origin': f"{scheme}://{target_host}"
        }
        
        print("Sending request to", url)
        r = requests.request(
            method='GET',
            url=url,
            headers=headers,
            allow_redirects=False,
            timeout=10,
            verify=False
        )
        print("Status:", r.status_code)
        for k, v in r.headers.items():
            print(f" {k}: {v}")
        print("Body length:", len(r.content))
        if r.status_code in (301, 302, 303, 307, 308):
            print("Redirect to:", r.headers.get('Location'))
            
    except Exception as e:
        print("Exception:", e)
    finally:
        stop.set()

test_proxy(205, 'https', 443, '/cgi/home.php')
print("-" * 40)
test_proxy(203, 'https', 443, '/action/login_first.html')
print("-" * 40)
test_proxy(204, 'http', 80, '/cgi/login.php')
