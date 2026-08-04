from django.contrib.auth.models import User
from django.db.models import Q

from usuario.perms import is_admin, get_instancia


def instancia_da_tarefa(user, cliente):
    """Instância a gravar na tarefa: a do cliente (se houver), senão a de
    quem está criando (Consultor/Operador); Administrador sem cliente cria
    tarefa de plataforma (instancia=None, só ele vê)."""
    if cliente is not None:
        return cliente.instancia
    return get_instancia(user)


def usuarios_atribuiveis(instancia):
    """Quem pode aparecer no seletor de 'designar para': back-office da
    mesma instância da tarefa; sem instância (tarefa de plataforma), só
    Administradores."""
    if instancia is not None:
        return User.objects.filter(perfil__instancia=instancia).order_by('first_name', 'username')
    return User.objects.filter(
        Q(perfil__role='admin') | Q(is_staff=True, perfil__isnull=True)
    ).distinct().order_by('first_name', 'username')
