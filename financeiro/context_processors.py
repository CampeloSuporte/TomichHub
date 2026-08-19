from .decorators import pode_ver_financeiro


def financeiro_context(request):
    # getattr: uma página de erro (404/403) pode ser renderizada antes de o
    # AuthenticationMiddleware ter posto request.user — sem o guard o próprio
    # template de erro estoura e o usuário vê um 500 cru.
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return {'pode_ver_financeiro': pode_ver_financeiro(user)}
    return {'pode_ver_financeiro': False}
