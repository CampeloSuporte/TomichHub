from . import perms


def perfil_context(request):
    if not request.user.is_authenticated:
        return {'is_admin_bo': False, 'is_consultor_bo': False, 'is_operador_bo': False, 'is_backoffice_bo': False}
    return {
        'is_admin_bo': perms.is_admin(request.user),
        'is_consultor_bo': perms.is_consultor(request.user),
        'is_operador_bo': perms.is_operador(request.user),
        'is_backoffice_bo': perms.is_backoffice(request.user),
    }
