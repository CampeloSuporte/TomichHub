import json
import requests
from django.conf import settings
from django.shortcuts import render,redirect,get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from  django.contrib.auth import authenticate,login as auth_login
from django.contrib.auth.decorators import login_required
from clientes.decorators import admin_required  # ← ADICIONAR ESTA LINHA
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from clientes.models import Cliente
from .models import UsuarioModulo, modulos_habilitados_dict, Instancia, PerfilUsuario, InstanciaFerramenta, ferramentas_habilitadas_dict
from . import perms

TURNSTILE_VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'


def _verificar_turnstile(request):
    """Valida o token do Cloudflare Turnstile enviado pelo form de login."""
    token = request.POST.get('cf-turnstile-response')
    if not token:
        return False
    try:
        resp = requests.post(TURNSTILE_VERIFY_URL, data={
            'secret': settings.TURNSTILE_SECRET_KEY,
            'response': token,
            'remoteip': request.META.get('REMOTE_ADDR'),
        }, timeout=5)
        return resp.json().get('success', False)
    except requests.RequestException:
        return False


def _sincronizar_modulos_usuario(request, usuario):
    """
    Grava UsuarioModulo a partir dos checkboxes 'modulos' do POST — só se o
    form realmente enviou a seção (marcador oculto 'modulos_form_present'),
    pra nunca desabilitar tudo por um form incompleto (checkbox desmarcado
    não aparece no POST, então "nada marcado" é indistinguível de "campo
    ausente" sem esse marcador).
    """
    if not request.POST.get('modulos_form_present'):
        return
    modulos_marcados = set(request.POST.getlist('modulos'))
    for chave, _ in UsuarioModulo.MODULO_CHOICES:
        UsuarioModulo.objects.update_or_create(
            usuario=usuario, modulo=chave,
            defaults={'habilitado': chave in modulos_marcados},
        )


def _sincronizar_ferramentas_instancia(request, instancia):
    """Grava InstanciaFerramenta a partir dos checkboxes 'ferramentas' do
    POST — mesmo padrão de `_sincronizar_modulos_usuario`, mas por
    instância (Consultor) em vez de por login. Só o Administrador edita
    isso (`pode_gerenciar_ferramentas_instancia`)."""
    if not request.POST.get('ferramentas_form_present'):
        return
    if not perms.pode_gerenciar_ferramentas_instancia(request.user):
        return
    ferramentas_marcadas = set(request.POST.getlist('ferramentas'))
    for chave, _ in InstanciaFerramenta.FERRAMENTA_CHOICES:
        InstanciaFerramenta.objects.update_or_create(
            instancia=instancia, ferramenta=chave,
            defaults={'habilitado': chave in ferramentas_marcadas},
        )


_TIPO_LABELS = {
    PerfilUsuario.ROLE_ADMIN: 'Administrador',
    PerfilUsuario.ROLE_CONSULTOR: 'Consultor',
    PerfilUsuario.ROLE_OPERADOR: 'Operador',
    'cliente': 'Cliente',
}


def _validar_role_permitida(request, role):
    """Um Consultor só pode conceder role 'operador' ou 'cliente' — nunca
    'admin'/'consultor'. Mesmo que o POST seja manipulado diretamente."""
    if role in (PerfilUsuario.ROLE_ADMIN, PerfilUsuario.ROLE_CONSULTOR) and not perms.is_admin(request.user):
        return False
    if role == PerfilUsuario.ROLE_OPERADOR and not perms.pode_gerenciar_usuarios(request.user):
        return False
    return True


def _resolver_instancia_operador(request):
    """Instância de um Operador sendo criado/editado: fixa (a do próprio
    Consultor) quando quem está operando é Consultor; escolhida via POST
    quando é o Administrador."""
    if perms.is_consultor(request.user):
        return perms.get_instancia(request.user)
    instancia_id = request.POST.get('instancia') or None
    return Instancia.objects.filter(id=instancia_id).first() if instancia_id else None


@login_required(login_url='login')
@perms.pode_gerenciar_usuarios_required
def cadastrar_usuario(request):
        if request.method == 'GET':
            usuario = perms.usuarios_gerenciaveis_por(request.user).select_related('perfil', 'perfil__instancia')
            for u in usuario:
                u.modulos_json = json.dumps(modulos_habilitados_dict(u))
                perfil = getattr(u, 'perfil', None)
                u.role_efetivo = perfil.role if perfil else 'cliente'
                u.instancia_id_efetivo = perfil.instancia_id if perfil else None
                ferramentas = ferramentas_habilitadas_dict(perfil.instancia) if (perfil and perfil.role == PerfilUsuario.ROLE_CONSULTOR) else {}
                u.ferramentas_json = json.dumps(ferramentas)
            return render(request, 'cadastrar_usuario.html', {
                'usuario': usuario,
                'modulos_disponiveis': UsuarioModulo.MODULO_CHOICES,
                'ferramentas_disponiveis': InstanciaFerramenta.FERRAMENTA_CHOICES,
                'instancias': Instancia.objects.filter(ativo=True).order_by('nome') if perms.is_admin(request.user) else Instancia.objects.none(),
                'pode_criar_consultor': perms.is_admin(request.user),
            })
        else:
            username = request.POST.get('username')
            email = request.POST.get('email')
            password = request.POST.get('password')
            role = request.POST.get('role') or 'cliente'

            if not _validar_role_permitida(request, role):
                messages.error(request, 'Você não possui permissão para criar esse tipo de usuário.')
                return redirect('cadastrar_usuario')

            if User.objects.filter(username=username).exists():
                messages.error(request, "Nome de usuário já existe.")
                return redirect('cadastrar_usuario')

            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                is_staff=(role == PerfilUsuario.ROLE_ADMIN),
            )

            if role == PerfilUsuario.ROLE_CONSULTOR:
                instancia = Instancia.objects.create(nome=request.POST.get('instancia_nome') or username, criado_por=request.user)
                PerfilUsuario.objects.create(usuario=user, role=PerfilUsuario.ROLE_CONSULTOR, instancia=instancia, criado_por=request.user)
                _sincronizar_ferramentas_instancia(request, instancia)
            elif role == PerfilUsuario.ROLE_OPERADOR:
                instancia = _resolver_instancia_operador(request)
                if not instancia:
                    user.delete()
                    messages.error(request, 'Selecione uma instância válida para o Operador.')
                    return redirect('cadastrar_usuario')
                PerfilUsuario.objects.create(usuario=user, role=PerfilUsuario.ROLE_OPERADOR, instancia=instancia, criado_por=request.user)
            elif role == PerfilUsuario.ROLE_ADMIN:
                PerfilUsuario.objects.create(usuario=user, role=PerfilUsuario.ROLE_ADMIN, criado_por=request.user)
            # role == 'cliente': sem PerfilUsuario, igual ao comportamento de antes.

            _sincronizar_modulos_usuario(request, user)

            tipo_usuario = _TIPO_LABELS.get(role, 'Cliente')
            messages.success(request, f"Usuário '{username}' cadastrado com sucesso como {tipo_usuario}.")
            return redirect('cadastrar_usuario')


@login_required(login_url='login')
@perms.pode_gerenciar_usuarios_required
def editar_usuario(request):
    if request.method == 'POST':
        usuario_id = request.POST.get('id')

        if not perms.is_admin(request.user) and not perms.usuarios_gerenciaveis_por(request.user).filter(id=usuario_id).exists():
            messages.error(request, 'Você não possui permissão para editar este usuário.')
            return redirect('cadastrar_usuario')

        usuario = get_object_or_404(User, id=usuario_id)

        username = request.POST.get('username')
        email = request.POST.get('email')
        nova_senha = request.POST.get('password')
        role = request.POST.get('role') or 'cliente'

        if not _validar_role_permitida(request, role):
            messages.error(request, 'Você não possui permissão para definir esse tipo de usuário.')
            return redirect('cadastrar_usuario')

        # Verifica se username já existe em outro usuário
        if User.objects.filter(username=username).exclude(id=usuario_id).exists():
            messages.error(request, 'Erro: Já existe um usuário com esse username.')
            return redirect('cadastrar_usuario')

        # Verifica se email já existe em outro usuário
        if User.objects.filter(email=email).exclude(id=usuario_id).exists():
            messages.error(request, 'Erro: Já existe um usuário com esse email.')
            return redirect('cadastrar_usuario')

        # Atualiza os dados
        usuario.username = username
        usuario.email = email
        usuario.is_staff = (role == PerfilUsuario.ROLE_ADMIN)

        # Atualiza a senha apenas se foi fornecida
        if nova_senha and nova_senha.strip():
            usuario.set_password(nova_senha)

        usuario.save()

        perfil = getattr(usuario, 'perfil', None)

        if role == PerfilUsuario.ROLE_CONSULTOR:
            if perfil and perfil.role == PerfilUsuario.ROLE_CONSULTOR and perfil.instancia:
                instancia = perfil.instancia
                if request.POST.get('instancia_nome'):
                    instancia.nome = request.POST.get('instancia_nome')
                    instancia.save(update_fields=['nome'])
            else:
                instancia = Instancia.objects.create(nome=request.POST.get('instancia_nome') or usuario.username, criado_por=request.user)
            if perfil:
                perfil.role, perfil.instancia = PerfilUsuario.ROLE_CONSULTOR, instancia
                perfil.save()
            else:
                PerfilUsuario.objects.create(usuario=usuario, role=PerfilUsuario.ROLE_CONSULTOR, instancia=instancia, criado_por=request.user)
            _sincronizar_ferramentas_instancia(request, instancia)
        elif role == PerfilUsuario.ROLE_OPERADOR:
            instancia = _resolver_instancia_operador(request)
            if not instancia:
                messages.error(request, 'Selecione uma instância válida para o Operador.')
                return redirect('cadastrar_usuario')
            if perfil:
                perfil.role, perfil.instancia = PerfilUsuario.ROLE_OPERADOR, instancia
                perfil.save()
            else:
                PerfilUsuario.objects.create(usuario=usuario, role=PerfilUsuario.ROLE_OPERADOR, instancia=instancia, criado_por=request.user)
        elif role == PerfilUsuario.ROLE_ADMIN:
            if perfil:
                perfil.role, perfil.instancia = PerfilUsuario.ROLE_ADMIN, None
                perfil.save()
            else:
                PerfilUsuario.objects.create(usuario=usuario, role=PerfilUsuario.ROLE_ADMIN, criado_por=request.user)
        else:  # cliente
            if perfil:
                perfil.delete()

        _sincronizar_modulos_usuario(request, usuario)

        tipo_usuario = _TIPO_LABELS.get(role, 'Cliente')
        messages.success(request, f'Usuário atualizado com sucesso como {tipo_usuario}!')
        return redirect('cadastrar_usuario')

    messages.error(request, 'Método não permitido.')
    return redirect('cadastrar_usuario')

def login(request):
    """
    View de login com redirecionamento automático para cliente ou admin.
    ✅ CORRIGIDO: Agora usa is_staff para determinar o tipo de usuário
    """
    if request.method == 'GET':
        # Se já está logado, redireciona para o dashboard apropriado
        if request.user.is_authenticated:
            return redirect_user_by_role(request.user)
        
        return render(request, 'login.html', {
            'turnstile_site_key': settings.TURNSTILE_SITE_KEY,
        })

    else:
        if not _verificar_turnstile(request):
            messages.error(request, "Verificação de segurança falhou. Tente novamente.")
            return render(request, 'login.html', {
                'turnstile_site_key': settings.TURNSTILE_SITE_KEY,
            })

        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            auth_login(request, user)

            tipo_usuario = _TIPO_LABELS.get(perms.get_role(user), 'Cliente')
            messages.success(request, f"Login realizado com sucesso. Bem-vindo, {tipo_usuario}!")

            # Redirecionar baseado no tipo de usuário
            return redirect_user_by_role(user)
        else:
            messages.error(request, "Usuário ou senha inválidos.")
            return redirect('login')


def redirect_user_by_role(user):
    role = perms.get_role(user)
    if role == PerfilUsuario.ROLE_ADMIN:
        return redirect('quadro_geral')
    if role in (PerfilUsuario.ROLE_CONSULTOR, PerfilUsuario.ROLE_OPERADOR):
        return redirect('quadro_instancia')

    try:
        cliente = Cliente.objects.get_by_usuario_vinculado(user)
        return redirect('cliente_dashboard')  # ← ALTERADO
    except Cliente.DoesNotExist:
        messages.error(None, 'Sua conta não possui acesso ao sistema.')
        return redirect('login')


@login_required(login_url='login')
def quadro_geral(request):
    """
    View do quadro geral - apenas para administradores
    ✅ CORRIGIDO: Verifica is_staff em vez de is_superuser/is_staff
    """
    # Verificar se é admin
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, 'Você não possui permissão para acessar esta página.')
        return redirect('login')
    
    # Restante do código do quadro geral...
    return render(request, 'quadro_geral.html')

@login_required(login_url='login')
def logout(request):
    auth_logout(request)
    messages.success(request, "Você foi deslogado com sucesso.")
    return redirect('login')


@login_required(login_url='login')
def trocar_senha(request):
    if request.method == 'GET':
        return render(request, 'trocar_senha.html')
    else:
        senha_atual = request.POST.get('senha_atual')
        nova_senha = request.POST.get('nova_senha')
        confirmar_senha = request.POST.get('confirmar_senha')
        
        # Verifica se a senha atual está correta
        if not request.user.check_password(senha_atual):
            messages.error(request, "Senha atual incorreta.")
            return redirect('trocar_senha')
        
        # Verifica se as senhas coincidem
        if nova_senha != confirmar_senha:
            messages.error(request, "As senhas não coincidem.")
            return redirect('trocar_senha')
        
        # Verifica se a senha tem no mínimo 6 caracteres
        if len(nova_senha) < 6:
            messages.error(request, "A senha deve ter no mínimo 6 caracteres.")
            return redirect('trocar_senha')
        
        # Atualiza a senha
        request.user.set_password(nova_senha)
        request.user.save()
        
        # Mantém o usuário logado após trocar a senha
        update_session_auth_hash(request, request.user)
        
        messages.success(request, "Senha alterada com sucesso!")
        return redirect('cadastrar_usuario')