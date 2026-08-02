"""Torna o 2FA obrigatório: qualquer usuário autenticado que ainda não
confirmou o TOTPDevice é redirecionado pra tela de configuração em toda
requisição, até ativar — não dá pra navegar pro resto do sistema sem
configurar (só sai fazendo logout)."""
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
