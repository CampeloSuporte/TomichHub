"""
Middleware para sobrescrever Content-Security-Policy
Permite Draw.io COMPLETAMENTE
"""
from django.utils.deprecation import MiddlewareMixin

class DrawIOCSPOverrideMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        # Definir CSP que permite Draw.io
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' app.diagrams.net *.diagrams.net cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' fonts.googleapis.com app.diagrams.net *.diagrams.net; "
            "font-src 'self' fonts.gstatic.com app.diagrams.net *.diagrams.net; "
            "img-src 'self' data: https: blob: app.diagrams.net *.diagrams.net; "
            "connect-src 'self' app.diagrams.net *.diagrams.net cdn.jsdelivr.net; "
            "frame-src 'self' https://app.diagrams.net https://embed.diagrams.net https://*.diagrams.net; "
            "frame-ancestors 'self' https://teams.microsoft.com https://*.cloud.microsoft https://app.diagrams.net https://embed.diagrams.net; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )
        
        # SOBRESCREVER qualquer CSP anterior
        response['Content-Security-Policy'] = csp
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'SAMEORIGIN'
        
        return response
