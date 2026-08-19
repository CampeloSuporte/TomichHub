"""Torna o 2FA obrigatório: qualquer usuário autenticado que ainda não
confirmou o TOTPDevice é redirecionado pra tela de configuração em toda
requisição, até ativar — não dá pra navegar pro resto do sistema sem
configurar (só sai fazendo logout)."""
from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import resolve, Resolver404

_ROTAS_LIVRES = {'configurar_2fa', 'logout'}
_PREFIXOS_LIVRES = ('/static/', '/media/')


class Forcar2FAMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._precisa_configurar_2fa(request):
            return redirect('configurar_2fa')
        return self.get_response(request)

    def _precisa_configurar_2fa(self, request):
        user = request.user
        if not user.is_authenticated:
            return False
        if request.path.startswith(_PREFIXOS_LIVRES):
            return False
        try:
            url_name = resolve(request.path_info).url_name
        except Resolver404:
            return False
        if url_name in _ROTAS_LIVRES:
            return False
        device = getattr(user, 'totp_device', None)
        return not (device and device.confirmado)


_ANTI_CAPTURA_SCRIPT = b"""
<script>(function(){
var overlay=null;
function ensureOverlay(){
  if(overlay) return overlay;
  overlay=document.createElement('div');
  overlay.id='__anticaptura_overlay__';
  overlay.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:#000;z-index:2147483647;display:none;';
  document.documentElement.appendChild(overlay);
  return overlay;
}
function mostrar(){ ensureOverlay().style.display='block'; }
function esconder(){ if(overlay) overlay.style.display='none'; }
document.addEventListener('visibilitychange', function(){
  if(document.hidden){ mostrar(); } else { esconder(); }
});
window.addEventListener('blur', mostrar);
window.addEventListener('focus', esconder);
document.addEventListener('keyup', function(e){
  if(e.key === 'PrintScreen'){
    mostrar();
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText('').catch(function(){});
    }
    setTimeout(esconder, 1500);
  }
});
})();</script>
"""


class AntiCapturaMiddleware:
    """Escurece a tela quando a janela perde foco/fica oculta (troca de aba,
    minimizar) ou quando a tecla Print Screen é solta — sem nenhum alerta
    visível, só a tela preta. Injeta um <script> inline antes de </body> em
    toda resposta HTML, via middleware, porque o sistema não tem um único
    base.html: várias telas sensíveis (terminal, winbox/vnc, sala virtual)
    são HTML standalone que não estendem nenhum template comum.

    É uma mitigação, não um bloqueio garantido: não existe API web para
    impedir a tecla Print Screen do SO nem para detectar gravadores de tela
    de terceiros (OBS etc.) — cobre os casos mais comuns de captura casual
    (Snipping Tool, Win+Shift+S, print e troca de janela durante gravação)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get('Content-Type', '')
        if 'text/html' in content_type and getattr(response, 'streaming', False) is False:
            try:
                body = response.content
                if b'</body>' in body:
                    response.content = body.replace(b'</body>', _ANTI_CAPTURA_SCRIPT + b'</body>', 1)
                    if response.get('Content-Length') is not None:
                        response['Content-Length'] = len(response.content)
            except Exception:
                pass
        return response


class ProtegerAdminMiddleware:
    """Bloqueia o Django admin (/admin/) fora do fluxo de login do CRM.

    Sem isso, um usuário não logado que digita /admin/ cai no form de
    login nativo do Django, que autentica direto via auth_login() e
    contorna por completo o 2FA obrigatório da view `usuario.views.login`
    (que só chama auth_login depois do código confirmado). Por isso aqui
    o anônimo é redirecionado pro login do sistema — só entra em /admin/
    já autenticado pelo fluxo normal — e quem não é Administrador leva 403."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/admin/'):
            if not request.user.is_authenticated:
                return redirect(f"{settings.LOGIN_URL}?next={request.path}")
            from . import perms
            if not perms.is_admin(request.user):
                return HttpResponseForbidden("Acesso restrito ao administrador do sistema.")
        return self.get_response(request)
