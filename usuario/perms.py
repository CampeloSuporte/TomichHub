"""
usuario/perms.py

Ponto único de verdade para papel (Administrador/Consultor/Operador/portal
do cliente) e escopo de instância. Decorators e views do núcleo (clientes,
monitoramento, ipam, hotspot, bgp, scripts) devem checar permissão através
destas funções em vez de `request.user.is_staff` cru.
"""
from .models import PerfilUsuario, InstanciaFerramenta, ferramenta_habilitada as _ferramenta_habilitada


def get_perfil(user):
    if not user or not user.is_authenticated:
        return None
    return getattr(user, 'perfil', None)


def get_role(user):
    """'admin' | 'consultor' | 'operador' | None.

    Sem PerfilUsuario, um usuário is_staff=True é tratado como admin legado
    (conta criada antes desta feature) — compatibilidade retroativa, sem
    precisar de data migration."""
    perfil = get_perfil(user)
    if perfil:
        return perfil.role
    if user and user.is_authenticated and (user.is_staff or user.is_superuser):
        return PerfilUsuario.ROLE_ADMIN
    return None


def is_admin(user):
    return get_role(user) == PerfilUsuario.ROLE_ADMIN


def is_consultor(user):
    return get_role(user) == PerfilUsuario.ROLE_CONSULTOR


def is_operador(user):
    return get_role(user) == PerfilUsuario.ROLE_OPERADOR


def is_backoffice(user):
    """Admin, Consultor ou Operador — ou seja, não é login do portal do
    cliente final."""
    return get_role(user) is not None


def get_instancia(user):
    perfil = get_perfil(user)
    return perfil.instancia if perfil else None


def pode_gerenciar_usuarios(user):
    return is_admin(user) or is_consultor(user)


def pode_gerenciar_ferramentas_instancia(user):
    return is_admin(user)


def ferramenta_habilitada(user, ferramenta_key):
    if is_admin(user):
        return True
    if is_consultor(user) or is_operador(user):
        return _ferramenta_habilitada(get_instancia(user), ferramenta_key)
    return False


def pode_acessar_cliente(user, cliente):
    if cliente is None:
        return False
    if is_admin(user):
        return True
    if is_consultor(user) or is_operador(user):
        instancia = get_instancia(user)
        return instancia is not None and cliente.instancia_id == instancia.id
    # Portal do cliente final — mesma checagem já usada hoje.
    from clientes.models import Cliente
    try:
        cliente_do_usuario = Cliente.objects.get_by_usuario_vinculado(user)
    except Cliente.DoesNotExist:
        return False
    return cliente_do_usuario.id == cliente.id


def usuarios_gerenciaveis_por(user):
    from django.contrib.auth.models import User
    if is_admin(user):
        return User.objects.all()
    if is_consultor(user):
        instancia = get_instancia(user)
        if not instancia:
            return User.objects.none()
        return User.objects.filter(perfil__instancia=instancia)
    return User.objects.none()


def pode_gerenciar_usuarios_required(view_func):
    """Admin ou Consultor — Operador não cria/edita usuários."""
    from functools import wraps
    from django.shortcuts import redirect
    from django.contrib import messages

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not pode_gerenciar_usuarios(request.user):
            messages.error(request, 'Você não possui permissão para acessar esta página.')
            return redirect('login')
        return view_func(request, *args, **kwargs)
    return wrapper
