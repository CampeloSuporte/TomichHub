from .decorators import pode_ver_financeiro


def financeiro_context(request):
    if request.user.is_authenticated:
        return {'pode_ver_financeiro': pode_ver_financeiro(request.user)}
    return {'pode_ver_financeiro': False}
