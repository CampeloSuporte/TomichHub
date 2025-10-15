from django.shortcuts import render,redirect
from django.contrib import messages
from django.contrib.auth.models import User
from  django.contrib.auth import authenticate,login as auth_login
from django.contrib.auth.decorators import login_required


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
            return redirect('cadastrar_usuario')
        else:
            messages.error(request, "Usuário ou senha inválidos.")
            return redirect('login')