"""
usuario/perms.py

Ponto único de verdade para papel (Administrador/Consultor/Operador/portal
do cliente) e escopo de instância. Decorators e views do núcleo (clientes,
monitoramento, ipam, hotspot, bgp, scripts) devem checar permissão através
destas funções em vez de `request.user.is_staff` cru.
"""
from .models import Instancia, PerfilUsuario, InstanciaFerramenta, ferramenta_habilitada as _ferramenta_habilitada


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


def instancia_principal():
    """A operação própria do Administrador (`Instancia.principal`), ou None.

    Não é uma revenda: é o Administrador operando com Operadores próprios.
    Serve para os módulos que são exclusivos da plataforma, hoje o
    Atendimento. Ver a migração `usuario.0011_marcar_instancia_principal`.
    """
    return Instancia.objects.filter(principal=True, ativo=True).first()


def pode_acessar_atendimento(user):
    """Quem entra no módulo de Atendimento.

    Regra do produto: o Atendimento é **exclusivo da instância principal**.
    Administrador sempre; Consultor/Operador só se a instância deles for a
    principal. Consultor de revenda não entra — nem as telas, nem as APIs,
    nem os WebSockets.

    Isso NÃO sai de `is_staff`: Consultor e Operador também têm
    `is_staff=True` (é o que libera Scripts de Automação e o WebSocket de
    firmware, entre outros), então `is_staff` deixaria o módulo aberto para
    todas as revendas.
    """
    if not is_backoffice(user):
        return False
    if is_admin(user):
        return True
    instancia = get_instancia(user)
    return bool(instancia and instancia.principal and instancia.ativo)


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


def pode_editar_wiki(user):
    """Quem pode CRIAR/EDITAR artigo da Wiki.

    Administrador sempre; Consultor se a Wiki estiver liberada pra instância
    dele. Operador e portal do cliente final continuam só com leitura.

    Atenção: a Wiki é uma base GLOBAL (`ArtigoWiki` não tem instancia nem
    cliente), então o artigo que um Consultor edita é o mesmo que todas as
    outras instâncias leem. Excluir artigo segue exclusivo do Administrador.
    """
    if is_admin(user):
        return True
    if is_consultor(user):
        return ferramenta_habilitada(user, 'wiki')
    return False


def colegas_de_instancia(user):
    """Usuários de back-office que dividem a instância de `user` — base para
    listas de "atendentes" (transferir chamado, atribuir tarefa).

    Administrador enxerga todo o back-office; Consultor e Operador só a
    própria instância. Portal do cliente final não entra em lista nenhuma.

    Diferente de `usuarios_gerenciaveis_por`: aqui o Operador também recebe
    resultado (ele não gerencia ninguém, mas precisa transferir chamado pros
    colegas) e logins de portal nunca aparecem (não atendem chamado).
    """
    from django.contrib.auth.models import User
    from django.db.models import Q

    if is_admin(user):
        # Admin legado (is_staff sem PerfilUsuario) + qualquer conta com
        # perfil de back-office. Portal do cliente final fica de fora por
        # não ter nem is_staff nem PerfilUsuario.
        return User.objects.filter(is_active=True).filter(
            Q(is_staff=True) | Q(perfil__isnull=False)
        ).distinct()

    if is_consultor(user) or is_operador(user):
        instancia = get_instancia(user)
        if not instancia:
            return User.objects.none()
        return User.objects.filter(is_active=True, perfil__instancia=instancia)

    return User.objects.none()


def usuarios_gerenciaveis_por(user):
    from django.contrib.auth.models import User
    from django.db.models import Q
    if is_admin(user):
        return User.objects.all()
    if is_consultor(user):
        instancia = get_instancia(user)
        if not instancia:
            return User.objects.none()
        # perfil__instancia cobre Operadores da instância;
        # portal_instancia__instancia cobre usuários de portal
        # (role='cliente', sem PerfilUsuario) criados por essa instância —
        # ver PortalUsuarioInstancia.
        return User.objects.filter(
            Q(perfil__instancia=instancia) | Q(portal_instancia__instancia=instancia)
        ).distinct()
    return User.objects.none()


def pode_resetar_2fa(user, alvo):
    """Quem pode apagar o 2FA de `alvo`. Mesmo escopo de
    `usuarios_gerenciaveis_por` (Administrador vê todos; Consultor vê os
    Operadores da própria instância) — mais os logins do portal do Cliente
    final vinculados à própria instância, que ficam de fora daquela lista
    por não terem `PerfilUsuario` (não são "usuário gerenciável" pra edição
    de dados/role, mas o Consultor ainda é quem destrava o 2FA deles)."""
    if usuarios_gerenciaveis_por(user).filter(id=alvo.id).exists():
        return True
    if is_consultor(user):
        instancia = get_instancia(user)
        if not instancia:
            return False
        from django.db.models import Q
        from clientes.models import Cliente
        return Cliente.objects.filter(instancia=instancia).filter(
            Q(usuario=alvo) | Q(usuarios_adicionais=alvo)
        ).exists()
    return False


def ferramentas_habilitadas_dict_para(user):
    """Dict {ferramenta_key: bool} pronto pra uso em template (nav) —
    Administrador vê tudo habilitado; Consultor/Operador conforme a
    instância; portal do cliente final e anônimo, tudo desabilitado (essas
    ferramentas são todas de back-office, não do portal)."""
    if is_admin(user):
        return {chave: True for chave, _ in InstanciaFerramenta.FERRAMENTA_CHOICES}
    if is_consultor(user) or is_operador(user):
        from .models import ferramentas_habilitadas_dict as _dict
        return _dict(get_instancia(user))
    return {chave: False for chave, _ in InstanciaFerramenta.FERRAMENTA_CHOICES}


# A aba "Documentação de Rede" (UsuarioModulo) é a mesma feature que o
# IPAM (InstanciaFerramenta) — nomes históricos diferentes pro mesmo recurso.
_MODULO_PARA_FERRAMENTA = {'documentacao': 'ipam'}


def modulos_habilitados_dict_para_listagem(user, cliente):
    """Dict {modulo_key: bool} pras abas de listar.html (painel do
    cliente) — um único cálculo pra qualquer tipo de viewer:
    - Administrador: tudo habilitado.
    - Consultor/Operador: conforme as ferramentas que o Administrador
      liberou pra própria instância (`InstanciaFerramenta`).
    - Portal do cliente final: toggle por login (`UsuarioModulo`, default
      ligado) capado pelas ferramentas liberadas pra instância do
      Consultor dono do cliente — o cliente final nunca vê mais do que o
      próprio Consultor tem liberado. Cliente sem instância (da
      plataforma) não tem teto, comportamento igual a antes desta feature.
    """
    from .models import UsuarioModulo, modulos_habilitados_dict as _modulos_dict, ferramentas_habilitadas_dict as _ferramentas_dict

    if is_admin(user):
        return {chave: True for chave, _ in UsuarioModulo.MODULO_CHOICES}

    if is_consultor(user) or is_operador(user):
        ferramentas = _ferramentas_dict(get_instancia(user))
        return {
            chave: ferramentas.get(_MODULO_PARA_FERRAMENTA.get(chave, chave), False)
            for chave, _ in UsuarioModulo.MODULO_CHOICES
        }

    base = _modulos_dict(user)
    if cliente and cliente.instancia_id:
        ferramentas = _ferramentas_dict(cliente.instancia)
        for chave in list(base.keys()):
            chave_ferramenta = _MODULO_PARA_FERRAMENTA.get(chave, chave)
            if chave_ferramenta in ferramentas and not ferramentas[chave_ferramenta]:
                base[chave] = False
    return base


def portal_pode_usar_ferramenta(user, ferramenta_key):
    """Para login do portal do cliente final: toggle por login
    (`UsuarioModulo`, default ligado) — aceita tanto a chave nativa da
    ferramenta quanto o nome histórico usado em UsuarioModulo (ex:
    'ipam' -> 'documentacao') — capado pelas ferramentas liberadas pra
    instância do Consultor dono do cliente, se houver."""
    from clientes.models import Cliente
    from .models import UsuarioModulo, modulo_habilitado as _modulo_habilitado

    chave_modulo = ferramenta_key
    if ferramenta_key not in dict(UsuarioModulo.MODULO_CHOICES):
        reversa = {v: k for k, v in _MODULO_PARA_FERRAMENTA.items()}
        chave_modulo = reversa.get(ferramenta_key)
        if chave_modulo is None:
            return False

    if not _modulo_habilitado(user, chave_modulo):
        return False

    try:
        cliente = Cliente.objects.get_by_usuario_vinculado(user)
    except Cliente.DoesNotExist:
        return False

    if cliente.instancia_id and not _ferramenta_habilitada(cliente.instancia, ferramenta_key):
        return False

    return True


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
