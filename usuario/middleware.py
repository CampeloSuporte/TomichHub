"""Torna o 2FA obrigatório: qualquer usuário autenticado que ainda não
confirmou o TOTPDevice é redirecionado pra tela de configuração em toda
requisição, até ativar — não dá pra navegar pro resto do sistema sem
configurar (só sai fazendo logout)."""
import json
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

# Marca d'água: como a captura em si não dá pra bloquear (limitação do
# navegador — nenhuma API web intercepta o compositor de tela do SO), a
# mitigação real aqui é rastreabilidade: usuário + horário sobrepostos e
# repetidos pela tela toda, em baixa opacidade. `mix-blend-mode:difference`
# com texto branco garante contraste tanto em fundo claro quanto escuro sem
# precisar saber o tema da página. O texto é montado via textContent (nunca
# innerHTML) para não abrir brecha de injeção via nome de usuário.
_WATERMARK_SCRIPT_TEMPLATE = """
<script>(function(){
var user = %s;
if (!user) return;
var wrap = document.createElement('div');
wrap.id = '__anticaptura_watermark__';
wrap.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483646;overflow:hidden;opacity:.1;mix-blend-mode:difference;';
document.documentElement.appendChild(wrap);
function render(){
  var stamp = new Date().toLocaleString('pt-BR');
  var label = user + ' \\u00b7 ' + stamp;
  var cellW = 260, cellH = 170;
  var cols = Math.ceil(window.innerWidth / cellW) + 2;
  var rows = Math.ceil(window.innerHeight / cellH) + 2;
  wrap.textContent = '';
  for (var r = 0; r < rows; r++){
    for (var c = 0; c < cols; c++){
      var span = document.createElement('span');
      span.textContent = label;
      span.style.cssText = 'position:absolute;left:' + (c*cellW - cellW/2) + 'px;top:' + (r*cellH) + 'px;'
        + 'display:inline-block;transform:rotate(-28deg);font:13px sans-serif;color:#fff;white-space:nowrap';
      wrap.appendChild(span);
    }
  }
}
render();
window.addEventListener('resize', render);
setInterval(render, 60000);
})();</script>
"""


class AntiCapturaMiddleware:
    """Escurece a tela quando a janela perde foco/fica oculta (troca de aba,
    minimizar) ou quando a tecla Print Screen é solta — sem nenhum alerta
    visível, só a tela preta — e sobrepõe uma marca d'água com usuário +
    horário pela tela toda. Injeta um <script> inline antes de </body> em
    toda resposta HTML, via middleware, porque o sistema não tem um único
    base.html: várias telas sensíveis (terminal, winbox/vnc, sala virtual)
    são HTML standalone que não estendem nenhum template comum.

    É uma mitigação, não um bloqueio garantido: não existe API web para
    impedir a tecla Print Screen do SO nem para detectar gravadores de tela
    de terceiros (OBS etc.) — cobre os casos mais comuns de captura casual
    (Snipping Tool, Win+Shift+S, print e troca de janela durante gravação).
    A marca d'água cobre o resto: não impede o print, mas identifica quem
    tirou."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get('Content-Type', '')
        if 'text/html' in content_type and getattr(response, 'streaming', False) is False:
            try:
                body = response.content
                if b'</body>' in body:
                    username = ''
                    try:
                        if request.user.is_authenticated:
                            username = request.user.get_username()
                    except Exception:
                        pass
                    # json.dumps escapa aspas/backslash/controle pra uso seguro
                    # como literal JS; o replace quebra um eventual "</" no nome
                    # de usuário pra não fechar a <script> antes da hora.
                    user_js = json.dumps(username).replace('</', '<\\/')
                    watermark_script = (_WATERMARK_SCRIPT_TEMPLATE % user_js).encode('utf-8')
                    injecao = _ANTI_CAPTURA_SCRIPT + watermark_script
                    response.content = body.replace(b'</body>', injecao + b'</body>', 1)
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
