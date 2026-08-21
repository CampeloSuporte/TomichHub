from . import perms


def perfil_context(request):
    # getattr: idem financeiro_context — request.user pode não existir ainda
    # quando o Django renderiza um template de erro.
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {
            'is_admin_bo': False, 'is_consultor_bo': False, 'is_operador_bo': False,
            'is_backoffice_bo': False, 'ferramentas_habilitadas': {},
            'pode_editar_wiki': False, 'pode_atendimento_bo': False,
        }
    ferramentas = perms.ferramentas_habilitadas_dict_para(user)
    is_admin = perms.is_admin(user)
    is_consultor = perms.is_consultor(user)
    return {
        'is_admin_bo': is_admin,
        'is_consultor_bo': is_consultor,
        'is_operador_bo': perms.is_operador(user),
        'is_backoffice_bo': perms.is_backoffice(user),
        # Atendimento é exclusivo da instância principal — ver
        # perms.pode_acessar_atendimento.
        'pode_atendimento_bo': perms.pode_acessar_atendimento(user),
        # Botões de criar/editar artigo da Wiki. Reaproveita `ferramentas`
        # (já calculado acima) em vez de chamar perms.pode_editar_wiki, que
        # refaria a consulta de InstanciaFerramenta em toda requisição.
        'pode_editar_wiki': is_admin or (is_consultor and bool(ferramentas.get('wiki'))),
        'ferramentas_habilitadas': ferramentas,
        'ferramentas_menu_bo_visivel': bool(ferramentas.get('lg') or ferramentas.get('geoip') or ferramentas.get('firmware')),
    }
