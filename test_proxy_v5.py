import os
import sys
import django

# Setup Django
sys.path.append('/opt/crm')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.settings')
django.setup()

from clientes.models import Acesso, ProxyServer
from clientes.proxy_engine import ProxyEngine
import logging

# Configurar logging para ver o que está acontecendo
logging.basicConfig(level=logging.INFO)

def test_proxy_v5(acesso_id, porta=80, scheme='http', path='/'):
    try:
        acesso = Acesso.objects.get(id=acesso_id)
        target_host = acesso.host.strip()
        proxy_srv = ProxyServer.objects.filter(cliente=acesso.cliente, ativo=True).first()
        
        print(f"--- Testando Proxy V5 ---")
        print(f"Alvo: {scheme}://{target_host}:{porta}{path}")
        print(f"Proxy: {proxy_srv.nome if proxy_srv else 'DIRETO'}")
        
        engine = ProxyEngine(proxy_srv)
        target_url = f"{scheme}://{target_host}:{porta}{path}"
        
        # Testar múltiplas requisições para validar o Pool
        for i in range(3):
            print(f"\nRequisição {i+1}...")
            resp = engine.do_request('GET', target_url, headers={'User-Agent': 'TestV5'})
            
            if resp:
                print(f"Status: {resp.status_code}")
                print(f"Content-Type: {resp.headers.get('Content-Type')}")
                print(f"Tamanho: {len(resp.content)} bytes")
                if 'text/html' in resp.headers.get('Content-Type', ''):
                    print(f"Preview: {resp.content[:100].decode('utf-8', errors='replace')}...")
            else:
                print("Falha: Sem resposta")
                
    except Exception as e:
        print(f"Erro no teste: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    aid = int(sys.argv[1]) if len(sys.argv) > 1 else 205
    p = int(sys.argv[2]) if len(sys.argv) > 2 else 80
    s = sys.argv[3] if len(sys.argv) > 3 else 'http'
    test_proxy_v5(aid, p, s)
