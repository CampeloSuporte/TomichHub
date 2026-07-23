from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from functools import wraps
from .models import Cliente
from usuario.models import modulo_habilitado as _usuario_modulo_habilitado

# ============================================
# DECORADORES DE PERMISSÃO
# ============================================

def cliente_login_required(view_func):
    """
    Decorador que verifica se o usuário é um cliente.
    Redireciona para a página de acessos do cliente.
    
    Cliente = is_staff=False E vinculado a um Cliente
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        
        # ✅ CORRIGIDO: Verificar is_staff
        if request.user.is_staff or request.user.is_superuser:
            messages.error(request, 'Apenas clientes podem acessar esta página.')
            return redirect('quadro_geral')
        
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
    Decorador que verifica se o usuário é um administrador.
    Apenas usuários com is_staff=True têm acesso.
    
    ✅ CORRIGIDO: Usa is_staff em vez de is_superuser/is_staff
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        
        # ✅ CORRIGIDO: Verificar is_staff corretamente
        if not request.user.is_staff:
            messages.error(request, 'Você não possui permissão para acessar esta página.')
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
    Decorador que verifica se o usuário é cliente ou admin.
    Ambos podem acessar, mas com permissões diferentes.
    
    ✅ CORRIGIDO: Usa is_staff para distinguir
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        
        # Se for admin (is_staff=True), permite tudo
        if request.user.is_staff or request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        
        # Se for cliente (is_staff=False), verifica se está vinculado
        try:
            Cliente.objects.get_by_usuario_vinculado(request.user)
            return view_func(request, *args, **kwargs)
        except Cliente.DoesNotExist:
            messages.error(request, 'Você não está vinculado a um cliente.')
            return redirect('login')
    
    return wrapper


def modulo_habilitado_required(modulo_key):
    """
    Bloqueia o acesso de clientes (is_staff=False) a uma view quando o
    módulo `modulo_key` estiver desabilitado para o **usuário logado**
    (login individual, ver `usuario.models.UsuarioModulo`) — não para o
    Cliente/empresa como um todo. Admins (is_staff/is_superuser) sempre
    passam — o bloqueio é só para o portal do cliente final.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.is_staff or request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            if not _usuario_modulo_habilitado(request.user, modulo_key):
                messages.error(request, 'Este módulo não está disponível para o seu usuário. Fale com o suporte.')
                try:
                    cliente = Cliente.objects.get_by_usuario_vinculado(request.user)
                    return redirect(f"{reverse('listar_clientes')}?id={cliente.id}")
                except Cliente.DoesNotExist:
                    return redirect('login')

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def cliente_can_view_cliente(view_func):
    """
    Decorador que verifica se um cliente pode visualizar dados de um cliente específico.
    
    Regras:
    - Clientes (is_staff=False) só podem ver seus próprios dados
    - Admins (is_staff=True) podem ver qualquer cliente
    
    ✅ CORRIGIDO: Usa is_staff para verificar tipo de usuário
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        
        # Admin pode ver qualquer coisa
        if request.user.is_staff or request.user.is_superuser:
            print(f"✅ [ADMIN] {request.user.username} (is_staff={request.user.is_staff})")
            return view_func(request, *args, **kwargs)
        
        # Cliente só pode ver a si mesmo
        cliente_id = request.GET.get('id') or kwargs.get('cliente_id')
        
        print(f"\n🔍 [CLIENTE] {request.user.username} tentando acessar cliente ID: {cliente_id}")
        print(f"   is_staff: {request.user.is_staff}")
        print(f"   is_superuser: {request.user.is_superuser}")
        
        try:
            cliente_request = Cliente.objects.get_by_usuario_vinculado(request.user)
            cliente_target = Cliente.objects.get(id=cliente_id)
            
            print(f"   Seu cliente: ID {cliente_request.id} ({cliente_request.nome_empresa})")
            print(f"   Cliente solicitado: ID {cliente_target.id} ({cliente_target.nome_empresa})")
            
            if cliente_request.id != cliente_target.id:
                print(f"❌ [BLOQUEADO] Acesso negado!")
                messages.error(request, 'Você não possui permissão para visualizar este cliente.')
                return redirect('login')
            
            print(f"✅ [PERMITIDO] Acesso concedido!\n")
            return view_func(request, *args, **kwargs)
        except Cliente.DoesNotExist as e:
            print(f"❌ [ERRO] Cliente não encontrado: {e}\n")
            messages.error(request, 'Cliente não encontrado.')
            return redirect('login')
    
    return wrapper