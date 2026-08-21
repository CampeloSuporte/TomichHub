"""
Escopo por instância do módulo de atendimento.

O atendimento é liberado para todo o back-office (`staff_required` →
`is_staff`, que Administrador, Consultor e Operador têm). Isso é correto
para o ACESSO, mas não diz nada sobre QUAIS dados cada um enxerga: sem os
filtros deste módulo, um Consultor via os grupos, conversas e clientes de
todas as outras instâncias.

Regra:
- Administrador: tudo, inclusive grupo/conversa ainda sem cliente vinculado.
- Consultor/Operador: só o que está vinculado a um Cliente da própria
  instância. Grupo do WhatsApp AINDA SEM cliente é a única exceção: ele
  aparece (e pode ser vinculado) porque é justamente daí que sai o fluxo
  de "vincular grupo ao meu cliente" — sem isso o Consultor não conseguiria
  cadastrar os próprios grupos. Grupo já vinculado a cliente de OUTRA
  instância nunca aparece nem pode ser alterado.
- Portal do cliente final: nada (nem chega aqui, `staff_required` barra).
"""
from clientes.models import Cliente
from usuario import perms


def clientes_visiveis(user):
    """Clientes que `user` pode ver — base de todos os outros escopos."""
    return Cliente.objects.visiveis_para(user)


def _tudo(user):
    return perms.is_admin(user)


def scope_por_cliente(qs, user, campo='cliente'):
    """Filtra `qs` pelo campo de Cliente informado, respeitando a instância."""
    if _tudo(user):
        return qs
    return qs.filter(**{f'{campo}__in': clientes_visiveis(user)})


def groups_visiveis(user, qs=None, incluir_sem_cliente=True):
    """Grupos do WhatsApp que `user` pode ver. `incluir_sem_cliente=True`
    mantém na lista os grupos ainda não vinculados a nenhum cliente, que é
    o que alimenta a tela de vinculação."""
    from django.db.models import Q
    from .models import ContactGroup
    qs = ContactGroup.objects.all() if qs is None else qs
    if _tudo(user):
        return qs
    cond = Q(cliente__in=clientes_visiveis(user))
    if incluir_sem_cliente:
        cond |= Q(cliente__isnull=True)
    return qs.filter(cond)


def conversations_visiveis(user, qs=None):
    """Conversa é escopada pelo próprio cliente OU pelo cliente do grupo —
    conversa antiga pode ter `cliente` nulo mas grupo já vinculado."""
    from django.db.models import Q
    from .models import Conversation
    qs = Conversation.objects.all() if qs is None else qs
    if _tudo(user):
        return qs
    permitidos = clientes_visiveis(user)
    return qs.filter(Q(cliente__in=permitidos) | Q(group__cliente__in=permitidos)).distinct()


def pode_ver_conversation(user, conversation):
    if _tudo(user):
        return True
    permitidos = clientes_visiveis(user)
    ids = set(permitidos.values_list('id', flat=True))
    return conversation.cliente_id in ids or (
        conversation.group_id is not None and conversation.group.cliente_id in ids
    )


def pode_ver_group(user, group):
    """Grupo sem cliente é "de ninguém" ainda — visível/vinculável por
    qualquer back-office. Grupo já vinculado só para a instância dona."""
    if _tudo(user):
        return True
    if group.cliente_id is None:
        return True
    return clientes_visiveis(user).filter(id=group.cliente_id).exists()
