"""
financeiro/decorators.py

Decoradores para controlar acesso ao módulo Financeiro
"""

from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required

# ═════════════════════════════════════════════════════════════════
# CONFIGURE AQUI OS IDS DOS USUÁRIOS PERMITIDOS
# ═════════════════════════════════════════════════════════════════

USUARIOS_FINANCEIRO = [1, 2]  # ← MUDE PARA OS IDS REAIS

# Exemplo:
# USUARIOS_FINANCEIRO = [
#     1,  # admin
#     5,  # lucas
# ]

# ═════════════════════════════════════════════════════════════════


def acesso_financeiro_restrito(view_func):
    """
    ✅ Decorator que restringe acesso ao Financeiro
    
    Uso:
        @login_required
        @acesso_financeiro_restrito
        def dashboard_financeiro(request):
            ...
    
    ✅ Verifica se:
    1. Usuário está logado (feito por @login_required)
    2. Usuário é STAFF
    3. Usuário está na lista de IDs permitidos
    """
    
    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        
        # Se não é staff
        if not request.user.is_staff:
            messages.error(
                request, 
                '❌ Acesso Negado: Você não tem permissão para acessar o Financeiro!'
            )
            return redirect('home')
        
        # Se é staff mas não está na lista permitida
        if request.user.id not in USUARIOS_FINANCEIRO:
            messages.error(
                request, 
                f'❌ Acesso Restrito: Apenas usuários autorizados podem acessar o Financeiro. '
                f'(Seu ID: {request.user.id})'
            )
            return redirect('home')
        
        print(f'✅ Acesso concedido ao Financeiro para: {request.user.username}')
        
        # Se passou em todas as verificações, executa a view normalmente
        return view_func(request, *args, **kwargs)
    
    return wrapped_view


def pode_ver_financeiro(user):
    """
    ✅ Função auxiliar para verificar permissão em templates
    
    Uso no template:
        {% if pode_ver_financeiro %}
        <a href="{% url 'dashboard_financeiro' %}">Financeiro</a>
        {% endif %}
    
    Ou em código Python:
        if pode_ver_financeiro(request.user):
            # fazer algo
    """
    return user.is_staff and user.id in USUARIOS_FINANCEIRO


class AcessoFinanceiroRestrito:
    """
    ✅ Versão alternativa usando class-based decorator
    
    Uso com Class-Based Views:
        @method_decorator(acesso_financeiro_restrito, name='dispatch')
        class DashboardFinanceiroView(View):
            def get(self, request):
                ...
    """
    
    def __init__(self, view_func):
        self.view_func = view_func
    
    def __call__(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        
        if not request.user.is_staff:
            messages.error(request, '❌ Acesso Negado!')
            return redirect('home')
        
        if request.user.id not in USUARIOS_FINANCEIRO:
            messages.error(request, '❌ Você não tem permissão!')
            return redirect('home')
        
        return self.view_func(request, *args, **kwargs)
