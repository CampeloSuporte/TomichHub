from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from functools import wraps
from .models import Cliente

# ============================================
# DECORADORES DE PERMISSÃO
# ============================================

def cliente_login_required(view_func):
    """
    Decorador que verifica se o usuário é um cliente.
    Redireciona para a página de acessos do cliente.

    Cliente = login do portal (sem PerfilUsuario) E vinculado a um Cliente
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        from usuario.perms import is_backoffice, is_admin
        if is_backoffice(request.user):
            messages.error(request, 'Apenas clientes podem acessar esta página.')
            return redirect('quadro_geral' if is_admin(request.user) else 'quadro_instancia')

        # Verificar se o usuário é um cliente
        try:
            cliente = Cliente.objects.get_by_usuario_vinculado(request.user)
            return view_func(request, *args, **kwargs)
        except Cliente.DoesNotExist:
            messages.error(request, 'Você não está vinculado a um cliente.')
            return redirect('login')

    return wrapper


def admin_required(view_func):
    """
    Decorador que verifica se o usuário é o Administrador da plataforma.

    Checa o PAPEL (`perms.is_admin`), não `is_staff`. Consultor e Operador
    também têm `is_staff=True` — é o que libera o módulo de atendimento
    (`staff_required`) e o que os coloca na lista de atendentes —, então
    `is_staff` NÃO distingue Administrador de back-office e usá-lo aqui
    abria todas as telas globais (quadro geral, relatório de backups,
    Agent NOC, configurações) para Consultor/Operador, vazando clientes de
    outras instâncias.

    Use `backoffice_required` para views do núcleo que Consultor/Operador
    também devem acessar — sempre com o queryset escopado por
    `Cliente.objects.visiveis_para`.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        from usuario.perms import is_admin, is_backoffice
        if not is_admin(request.user):
            messages.error(request, 'Você não possui permissão para acessar esta página.')
            # Consultor/Operador estão logados e são da equipe: mandar pro
            # login seria um beco sem saída (ficariam presos num loop de
            # "já estou logado"). Vão pro dashboard da própria instância.
            if is_backoffice(request.user):
                return redirect('quadro_instancia')
            return redirect('login')

        return view_func(request, *args, **kwargs)

    return wrapper


def superuser_required(view_func):
    """
    Decorador que verifica se o usuário é superusuário (administrador).
    Apenas usuários com is_superuser=True têm acesso.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not request.user.is_superuser:
            messages.error(request, 'Você não possui permissão para acessar esta página.')
            return redirect('quadro_geral')
        return view_func(request, *args, **kwargs)
    return wrapper


def cliente_or_admin_required(view_func):
    """
    Decorador que verifica se o usuário é do back-office ou do portal.
    Ambos podem acessar, mas com permissões diferentes — a view é que faz
    o escopo por instância (`Cliente.objects.visiveis_para`).
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        # Equipe (Administrador, Consultor, Operador) passa direto
        from usuario.perms import is_backoffice
        if is_backoffice(request.user):
            return view_func(request, *args, **kwargs)

        # Se for cliente (is_staff=False), verifica se está vinculado
        try:
            Cliente.objects.get_by_usuario_vinculado(request.user)
            return view_func(request, *args, **kwargs)
        except Cliente.DoesNotExist:
            messages.error(request, 'Você não está vinculado a um cliente.')
            return redirect('login')

    return wrapper


def backoffice_required(view_func):
    """
    Decorador que verifica se o usuário é da "equipe" (Administrador,
    Consultor ou Operador) — ou seja, não é um login do portal do cliente
    final. Usado nas telas do núcleo do produto que Consultor/Operador
    também devem acessar (cadastro de clientes, por exemplo), diferente de
    `admin_required` que é exclusivo do Administrador da plataforma.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        from usuario.perms import is_backoffice
        if not is_backoffice(request.user):
            messages.error(request, 'Você não possui permissão para acessar esta página.')
            return redirect('login')

        return view_func(request, *args, **kwargs)

    return wrapper


def modulo_habilitado_required(modulo_key):
    """
    Bloqueia o acesso a uma view quando a ferramenta `modulo_key` estiver
    desabilitada para quem está logado:
    - Administrador: sempre passa.
    - Consultor/Operador: passa só se a Instancia tiver essa ferramenta
      liberada pelo Administrador (ver `usuario.models.InstanciaFerramenta`).
    - Portal do cliente final: passa só se o próprio login tiver o módulo
      habilitado (login individual, ver `usuario.models.UsuarioModulo`) —
      comportamento inalterado.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            from usuario.perms import is_admin, is_consultor, is_operador, ferramenta_habilitada

            if is_admin(request.user):
                return view_func(request, *args, **kwargs)

            if is_consultor(request.user) or is_operador(request.user):
                if not ferramenta_habilitada(request.user, modulo_key):
                    messages.error(request, 'Esta ferramenta não está disponível para sua instância. Fale com o administrador.')
                    return redirect('cadastrar_cliente')
                return view_func(request, *args, **kwargs)

            from usuario.perms import portal_pode_usar_ferramenta
            if not portal_pode_usar_ferramenta(request.user, modulo_key):
                messages.error(request, 'Este módulo não está disponível para o seu usuário. Fale com o suporte.')
                try:
                    cliente = Cliente.objects.get_by_usuario_vinculado(request.user)
                    return redirect(f"{reverse('listar_clientes')}?id={cliente.id}")
                except Cliente.DoesNotExist:
                    return redirect('login')

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def ferramenta_instancia_required(ferramenta_key):
    """
    Equivalente a `modulo_habilitado_required`, para views que hoje não têm
    nenhum controle de ferramenta (só `@login_required`) — ex.: IPAM,
    Hotspot, Scripts, BGP. Portal do cliente final: ver
    `usuario.perms.portal_pode_usar_ferramenta` (toggle por login, capado
    pela instância do Consultor dono do cliente; ferramentas sem
    equivalente no portal, como 'scripts'/'bgp', ficam bloqueadas).
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')

            from usuario.perms import is_admin, is_consultor, is_operador, ferramenta_habilitada

            if is_admin(request.user):
                return view_func(request, *args, **kwargs)

            if is_consultor(request.user) or is_operador(request.user):
                if not ferramenta_habilitada(request.user, ferramenta_key):
                    messages.error(request, 'Esta ferramenta não está disponível para sua instância. Fale com o administrador.')
                    return redirect('cadastrar_cliente')
                return view_func(request, *args, **kwargs)

            from usuario.perms import portal_pode_usar_ferramenta
            if portal_pode_usar_ferramenta(request.user, ferramenta_key):
                return view_func(request, *args, **kwargs)

            messages.error(request, 'Você não possui permissão para acessar esta página.')
            return redirect('login')

        return wrapper
    return decorator


def wiki_edicao_required(view_func):
    """
    Criar/editar artigo da Wiki: Administrador e Consultor (com a ferramenta
    'wiki' liberada pra instância). Operador e portal do cliente final
    continuam só com leitura — ver `usuario.perms.pode_editar_wiki`.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        from usuario.perms import pode_editar_wiki
        if not pode_editar_wiki(request.user):
            messages.error(request, 'Você não possui permissão para editar a Wiki.')
            return redirect('wiki:dashboard')

        return view_func(request, *args, **kwargs)

    return wrapper


def cliente_can_view_cliente(view_func):
    """
    Decorador que verifica se o usuário logado pode visualizar dados de um
    cliente específico (`id` na querystring ou `cliente_id` na URL):
    - Administrador: pode ver qualquer cliente.
    - Consultor/Operador: só clientes da própria instância.
    - Portal do cliente final: só o próprio cliente.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        from usuario.perms import is_admin, pode_acessar_cliente

        if is_admin(request.user):
            return view_func(request, *args, **kwargs)

        cliente_id = request.GET.get('id') or kwargs.get('cliente_id')
        cliente_target = Cliente.objects.filter(id=cliente_id).first()

        if not cliente_target or not pode_acessar_cliente(request.user, cliente_target):
            messages.error(request, 'Você não possui permissão para visualizar este cliente.')
            return redirect('login')

        return view_func(request, *args, **kwargs)

    return wrapper
