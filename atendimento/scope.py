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

Em cima disso existe um SEGUNDO recorte, independente da instância: um
contato/grupo pode ter atendentes escolhidos (`UserGroupPermission`), e aí só
eles veem os chamados dele. Contato sem ninguém escolhido é aberto — cai no
atendimento geral, como sempre foi.

**Esse segundo recorte vale para TODO MUNDO, administrador incluído.** Não é o
que valeu no primeiro dia (04/09/2026 de manhã o admin passava direto), e a
razão da mudança está na realidade do cadastro: `perms.get_role` trata todo
`is_staff` SEM `PerfilUsuario` como "admin legado", e nesta base 8 dos 12
usuários caíam nisso — 6 deles sem ninguém nunca ter decidido que eram
administradores. Na prática "admin vê tudo" queria dizer "quase ninguém é
filtrado", e um contato marcado para uma pessoa continuava visível para o
escritório inteiro.

A válvula de escape fica em `groups_visiveis`/`pode_ver_group`, não aqui: o
administrador continua enxergando o CONTATO na tela de Grupos/Contatos (para
mudar a lista ou tirar a restrição), mas não os CHAMADOS dele. Sem isso, um
contato restrito a alguém que sai da empresa viraria uma armadilha sem volta —
ninguém veria o chamado e ninguém conseguiria achar o contato para desfazer.

Ver `_restricao_q` e docs/ATENDIMENTO.md → "Contatos restritos a atendentes".
"""
from django.db.models import Exists, OuterRef, Q

from clientes.models import Cliente
from usuario import perms


def clientes_visiveis(user):
    """Clientes que `user` pode ver — base de todos os outros escopos."""
    return Cliente.objects.visiveis_para(user)


def _tudo(user):
    return perms.is_admin(user)


def _restricao_q(user, campo_group='group'):
    """Condição "este chamado/contato é visível para `user`", considerando os
    atendentes escolhidos no contato/grupo.

    Visível quando **não há ninguém escolhido** (contato aberto) **ou** quando
    `user` é um dos escolhidos. Feito com dois `Exists` correlacionados em vez
    de um `filter(...)` no relacionamento porque um join simples multiplicaria
    a linha do chamado por atendente autorizado e a negação (`exclude`) mataria
    junto os contatos abertos.

    `campo_group` é o caminho até o ContactGroup a partir do modelo consultado:
    `'group'` para Conversation, `''` (o próprio objeto) para ContactGroup.
    """
    from .models import UserGroupPermission

    ref = OuterRef('pk') if not campo_group else OuterRef(f'{campo_group}_id')
    tem_dono = UserGroupPermission.objects.filter(group_id=ref)
    sou_dono = UserGroupPermission.objects.filter(group_id=ref, user=user)
    return Q(~Exists(tem_dono) | Exists(sou_dono))


def _permitido_no_grupo(user, group) -> bool:
    """Versão objeto-a-objeto de `_restricao_q`, para os `pode_ver_*`."""
    if group is None:
        return True
    ids = group.atendentes_ids()
    return (not ids) or (user.id in ids)


def scope_por_cliente(qs, user, campo='cliente'):
    """Filtra `qs` pelo campo de Cliente informado, respeitando a instância."""
    if _tudo(user):
        return qs
    return qs.filter(**{f'{campo}__in': clientes_visiveis(user)})


def groups_visiveis(user, qs=None, incluir_sem_cliente=True):
    """Grupos/contatos do WhatsApp que `user` pode ver. `incluir_sem_cliente=True`
    mantém na lista os grupos ainda não vinculados a nenhum cliente, que é
    o que alimenta a tela de vinculação.

    Contato com atendentes escolhidos some da lista para quem não está entre
    eles — senão o nome do contato restrito continuaria aparecendo na tela de
    Grupos/Contatos e nos filtros, que é metade do que se quer esconder.

    **O administrador é a exceção, e de propósito:** aqui ele vê tudo. Esta é a
    tela de ADMINISTRAÇÃO do contato, não de atendimento — é por ela que se
    muda a lista ou se tira a restrição. Se o admin também não enxergasse, um
    contato marcado para alguém que sai da empresa ficaria sem volta: ninguém
    veria o chamado e ninguém acharia o contato para corrigir. Os CHAMADOS
    desse contato continuam escondidos dele (ver `conversations_visiveis`)."""
    from .models import ContactGroup
    qs = ContactGroup.objects.all() if qs is None else qs
    if _tudo(user):
        return qs
    cond = Q(cliente__in=clientes_visiveis(user))
    if incluir_sem_cliente:
        cond |= Q(cliente__isnull=True)
    return qs.filter(cond).filter(_restricao_q(user, campo_group=''))


def conversations_visiveis(user, qs=None):
    """Conversa é escopada pelo próprio cliente OU pelo cliente do grupo —
    conversa antiga pode ter `cliente` nulo mas grupo já vinculado — e, por
    cima disso, pelos atendentes escolhidos no contato/grupo.

    A restrição por atendente é aplicada ANTES do `_tudo`: ela vale para o
    administrador também (ver o cabeçalho do módulo)."""
    from .models import Conversation
    qs = Conversation.objects.all() if qs is None else qs
    qs = qs.filter(_restricao_q(user))
    if _tudo(user):
        return qs
    permitidos = clientes_visiveis(user)
    return (qs
            .filter(Q(cliente__in=permitidos) | Q(group__cliente__in=permitidos))
            .distinct())


def pode_ver_conversation(user, conversation):
    # Atendentes escolhidos primeiro: vale inclusive para o administrador.
    if not _permitido_no_grupo(user, conversation.group):
        return False
    if _tudo(user):
        return True
    permitidos = clientes_visiveis(user)
    ids = set(permitidos.values_list('id', flat=True))
    return conversation.cliente_id in ids or (
        conversation.group_id is not None and conversation.group.cliente_id in ids
    )


def pode_ver_group(user, group):
    """Grupo sem cliente é "de ninguém" ainda — visível/vinculável por
    qualquer back-office. Grupo já vinculado só para a instância dona. Em
    qualquer caso, contato restrito só para os atendentes escolhidos — com o
    administrador de fora da regra, pela mesma razão de `groups_visiveis`:
    é por aqui que a restrição é desfeita."""
    if _tudo(user):
        return True
    if not _permitido_no_grupo(user, group):
        return False
    if group.cliente_id is None:
        return True
    return clientes_visiveis(user).filter(id=group.cliente_id).exists()


def atendentes_do_chamado(conversation):
    """Ids dos atendentes que podem ver este chamado, ou `None` quando ele é
    aberto para todos. É o que viaja no payload do WebSocket para o
    `InboxConsumer` decidir se entrega ou descarta — ver consumers.py. Como a
    restrição vale para o administrador também, o consumer não abre exceção
    para ninguém que não esteja nesta lista."""
    if conversation.group_id is None:
        return None
    ids = conversation.group.atendentes_ids()
    return sorted(ids) or None
