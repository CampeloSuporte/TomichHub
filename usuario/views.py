from django.shortcuts import render,redirect,get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from  django.contrib.auth import authenticate,login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash

@login_required(login_url='login')
def cadastrar_usuario(request):

        if request.method == 'GET':
            usuario = User.objects.all()
            return render(request, 'cadastrar_usuario.html', {'usuario': usuario})
        else:
            username = request.POST.get('username')
            email = request.POST.get('email')
            password = request.POST.get('password')

            if User.objects.filter(username=username).exists():
                messages.error(request, "Nome de usuário já existe.")
                return redirect('cadastrar_usuario')
            user = User.objects.create_user(username=username, email=email, password=password)
            user.save()
            messages.error(request, "usuário cadastrado com sucesso.")
            return redirect('cadastrar_usuario')


@login_required(login_url='login')
def editar_usuario(request):
    if request.method == 'POST':
        usuario_id = request.POST.get('id')
        usuario = get_object_or_404(User, id=usuario_id)
        
        username = request.POST.get('username')
        email = request.POST.get('email')
        nova_senha = request.POST.get('password')
        
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
        
        # Atualiza a senha apenas se foi fornecida
        if nova_senha and nova_senha.strip():
            usuario.set_password(nova_senha)
        
        usuario.save()
        messages.success(request, 'Usuário atualizado com sucesso!')
        return redirect('cadastrar_usuario')
    
    messages.error(request, 'Método não permitido.')
    return redirect('cadastrar_usuario')

def login(request):
    if request.method == 'GET':
     return render(request, 'login.html')
    else:
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            auth_login(request, user)
            messages.success(request, "Login realizado com sucesso.")
            return redirect('quadro_geral')
        else:
            messages.error(request, "Usuário ou senha inválidos.")
            return redirect('login')

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