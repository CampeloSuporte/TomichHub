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
