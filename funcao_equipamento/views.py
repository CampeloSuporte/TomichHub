from django.shortcuts import render, redirect
from django.contrib import messages
from .models import Funcao_equipamento
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404

@login_required(login_url='login')
def cadastrar_funcao(request):
    if request.method == 'POST':
        descricao = request.POST.get('descricao', '').strip()

        # Verifica se o campo está vazio
        if not descricao:
            messages.error(request, "A descrição não pode estar em branco.")
            return redirect('listar_funcoes')

        # Verifica se já existe
        if Funcao_equipamento.objects.filter(descricao__iexact=descricao).exists():
            messages.error(request, "Essa função já existe.")
            return redirect('listar_funcoes')

        # Cria o registro
        Funcao_equipamento.objects.create(descricao=descricao)
        messages.success(request, "Função cadastrada com sucesso!")
        return redirect('listar_funcoes')

    # Se não for POST, redireciona
    return redirect('listar_funcoes')

@login_required(login_url='login')
def listar_funcoes(request):
    funcao_equipamentos = Funcao_equipamento.objects.all()
    return render(request, 'listar_funcao.html', {'funcao_equipamentos': funcao_equipamentos})


@login_required(login_url='login')
def editar_funcao(request):
    if request.method == 'POST':
        id = request.POST.get('id')
        descricao = request.POST.get('descricao')

        funcao = get_object_or_404(Funcao_equipamento, id=id)
        funcao.descricao = descricao
        funcao.save()
        messages.success(request, "Função editada com sucesso!")
        return redirect('listar_funcoes')  # ou o nome da view de listagem



