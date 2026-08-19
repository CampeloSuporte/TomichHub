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


def usuarios_atribuiveis(cliente):
    """Quem pode aparecer no seletor de responsáveis (múltiplos) de uma
    tarefa vinculada a `cliente` (ou None, tarefa de plataforma):
    - Administradores reais do sistema — sempre, independente do cliente.
      Exclui contas sem e-mail: sobra de cadastro/instância de teste que
      nunca correspondeu a uma pessoa de verdade (mesmo filtro que
      `atendimento.views.api_agents_list` já usa pra não vazar "conta
      fantasma" no seletor de transferir chamado).
    - Atendentes (Consultor/Operador) da instância do cliente, se houver.
    - Usuários de portal vinculados a esse cliente (principal +
      adicionais) — pra que o próprio cliente possa participar do vínculo.
    """
    admins = User.objects.filter(is_active=True).filter(
        Q(perfil__role='admin') | Q(is_staff=True, perfil__isnull=True)
    ).exclude(email='')

    instancia = cliente.instancia if cliente is not None else None
    atendentes = (
        User.objects.filter(is_active=True, perfil__instancia=instancia)
        if instancia is not None else User.objects.none()
    )

    portal = (
        User.objects.filter(is_active=True).filter(Q(cliente=cliente) | Q(clientes_adicionais=cliente))
        if cliente is not None else User.objects.none()
    )

    return (admins | atendentes | portal).distinct().order_by('first_name', 'username')
