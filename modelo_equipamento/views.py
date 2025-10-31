from django.shortcuts import render, redirect
from .models import Modelo_equipamento
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import get_object_or_404
from clientes.decorators import admin_required 


@login_required(login_url='login')
@admin_required 
def listar_modelos(request):
    modelos = Modelo_equipamento.objects.all()
    return render(request, 'listar_equipamentos.html', {'modelos': modelos})



@login_required(login_url='login')
@admin_required 
def cadastrar_modelo(request):
    if request.method == 'POST':
        nome = request.POST.get('nome', '').strip()
        fabricante = request.POST.get('fabricante', '').strip()
        descricao = request.POST.get('descricao', '').strip()

        # ⚠️ Verifica se o campo nome está vazio
        if not nome:
            messages.error(request, "O campo 'Nome' não pode estar em branco.")
            return redirect('listar_modelos')

        # ⚠️ Verifica se já existe um modelo com o mesmo nome (case-insensitive)
        if Modelo_equipamento.objects.filter(nome__iexact=nome).exists():
            messages.error(request, "Já existe um modelo com este nome.")
            return redirect('listar_modelos')

        # ✅ Cria o novo modelo
        Modelo_equipamento.objects.create(
            nome=nome,
            fabricante=fabricante,
            descricao=descricao
        )

        messages.success(request, "Modelo cadastrado com sucesso!")
        return redirect('listar_modelos')

    return redirect('listar_modelos')



@login_required(login_url='login')
@admin_required 
def editar_modelo(request):
    if request.method == 'POST':
        id = request.POST.get('id')
        nome = request.POST.get('nome', '').strip()
        fabricante = request.POST.get('fabricante', '').strip()
        descricao = request.POST.get('descricao', '').strip()

        modelo = get_object_or_404(Modelo_equipamento, id=id)

        # ⚠️ Verifica se o campo nome está vazio
        if not nome:
            messages.error(request, "O campo 'Nome' não pode estar em branco.")
            return redirect('listar_modelos')

        # ⚠️ Verifica se já existe outro modelo com o mesmo nome (case-insensitive)
        if Modelo_equipamento.objects.filter(nome__iexact=nome).exclude(id=id).exists():
            messages.error(request, "Já existe outro modelo com este nome.")
            return redirect('listar_modelos')

        # ✅ Atualiza o modelo
        modelo.nome = nome
        modelo.fabricante = fabricante
        modelo.descricao = descricao
        modelo.save()

        messages.success(request, "Modelo editado com sucesso!")
        return redirect('listar_modelos')

    return redirect('listar_modelos')




def deletar_modelo(request):
    if request.method == 'POST':
        modelo_id = request.POST.get('id')
        modelo = get_object_or_404(Modelo_equipamento, id=modelo_id)
        nome_modelo = modelo.nome
        modelo.delete()
        messages.success(request, f"O modelo '{nome_modelo}' foi excluído com sucesso!")
        return redirect('listar_modelos')  # ajuste para sua URL de listagem

    messages.error(request, "Erro ao tentar excluir o modelo.")
    return redirect('listar_modelos')
