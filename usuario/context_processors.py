from . import perms


def perfil_context(request):
    if not request.user.is_authenticated:
        return {
            'is_admin_bo': False, 'is_consultor_bo': False, 'is_operador_bo': False,
            'is_backoffice_bo': False, 'ferramentas_habilitadas': {},
        }
    ferramentas = perms.ferramentas_habilitadas_dict_para(request.user)
    return {
        'is_admin_bo': perms.is_admin(request.user),
        'is_consultor_bo': perms.is_consultor(request.user),
        'is_operador_bo': perms.is_operador(request.user),
        'is_backoffice_bo': perms.is_backoffice(request.user),
        'ferramentas_habilitadas': ferramentas,
        'ferramentas_menu_bo_visivel': bool(ferramentas.get('lg') or ferramentas.get('geoip') or ferramentas.get('firmware')),
    }
