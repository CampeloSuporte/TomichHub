"""
✅ CORRIGIDO v2: Middleware de Segurança para Draw.io
PROBLEMA: X-Frame-Options 'SAMEORIGIN' estava bloqueando iframe
SOLUÇÃO: Remover X-Frame-Options para editor de topologia

Adicione isso em: crm/middleware.py
"""

from django.utils.deprecation import MiddlewareMixin

class DrawIOCSPOverrideMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        # ✅ APENAS para página do editor de topologia
        if '/topologia/editor/' in request.path:
            # CSP PERMISSIVA para Draw.io
            csp = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
                    "app.diagrams.net *.diagrams.net embed.diagrams.net "
                    "cdn.jsdelivr.net wss: https:; "
                "style-src 'self' 'unsafe-inline' "
                    "fonts.googleapis.com app.diagrams.net *.diagrams.net embed.diagrams.net; "
                "font-src 'self' fonts.gstatic.com "
                    "app.diagrams.net *.diagrams.net embed.diagrams.net; "
                "img-src 'self' data: https: blob: "
                    "app.diagrams.net *.diagrams.net embed.diagrams.net; "
                "connect-src 'self' "
                    "app.diagrams.net *.diagrams.net embed.diagrams.net "
                    "cdn.jsdelivr.net wss: https:; "
                "frame-src 'self' "
                    "https://app.diagrams.net https://embed.diagrams.net "
                    "https://*.diagrams.net data:; "
                "frame-ancestors 'self' "
                    "https://teams.microsoft.com https://*.cloud.microsoft "
                    "https://app.diagrams.net https://embed.diagrams.net; "
                "worker-src 'self' blob: "
                    "app.diagrams.net *.diagrams.net embed.diagrams.net; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self';"
            )
            
            response['Content-Security-Policy'] = csp
            response['X-Content-Type-Options'] = 'nosniff'
            
            # ✅ CRÍTICO: NÃO definir X-Frame-Options
            # Remover se estiver presente
            if 'X-Frame-Options' in response:
                del response['X-Frame-Options']
            
            return response
        
        # Para outras páginas: CSP normal
        return response