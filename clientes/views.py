from django.shortcuts import render,redirect, get_object_or_404
from . models import Cliente
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from . models import Acesso
from django.urls import reverse
from modelo_equipamento.models import Modelo_equipamento
from funcao_equipamento.models import Funcao_equipamento

@login_required(login_url='login')
def listar_clientes(request):
    id_cliente = request.GET.get('id')
    cliente = Cliente.objects.get(id=id_cliente)
    funcao_selecionada = request.GET.get('funcao')
    modelos = Modelo_equipamento.objects.all()
    funcao_equipamentos = Funcao_equipamento.objects.all()

    # todas as funções disponíveis
    funcoes = cliente.acessos.values_list('funcao', flat=True).distinct()

    # se uma função foi escolhida, filtra os acessos
    if funcao_selecionada:
        acessos = cliente.acessos.filter(funcao=funcao_selecionada)
    else:
        acessos = cliente.acessos.all()

    return render(request, 'listar.html', {
        'cliente': cliente,
        'funcoes': funcoes,
        'acessos': acessos,
        'funcao_selecionada': funcao_selecionada,
        'modelos': modelos ,
        'funcao_equipamentos': funcao_equipamentos
    })


@login_required(login_url='login')
def cadastrar_cliente(request):
    if request.method == 'GET':
        clientes  = Cliente.objects.all()
        usuario = User.objects.all()
        return render(request, 'cadastrar_cliente.html', {
            'clientes': clientes, 'usuario': usuario})
    elif request.method == 'POST':
        nome_empresa = request.POST.get('nome_empresa')
        email = request.POST.get('email')
        cpnj = request.POST.get('cnpj')
        telefone = request.POST.get('telefone')
        endereco = request.POST.get('endereco')
        cidade = request.POST.get('cidade')
        estado = request.POST.get('estado')
        cep = request.POST.get('cep')
        usuario_id = request.POST.get('usuario')

         # Verifica se o email ou telefone já estão cadastrados 
        if Cliente.objects.filter(email=email).exists():
            messages.error(request, 'Erro: Já existe um cliente com esse email cadastrado.')
            return redirect('cadastrar_cliente')

        if Cliente.objects.filter(telefone=telefone).exists():
            messages.error(request, 'Erro: Já existe um cliente com esse telefone cadastrado.')
            return redirect('cadastrar_cliente')

        cliente = Cliente(
            nome_empresa=nome_empresa,
            email=email,
            telefone=telefone,
            endereco=endereco,
            cidade=cidade,
            estado=estado,
            cep=cep,
            cnpj=cpnj,
            usuario_id=usuario_id
        )
        cliente.save()
        messages.success(request, 'Cliente cadastrado com sucesso!')
        return redirect('cadastrar_cliente')



@login_required(login_url='login')
def cadastrar_acesso(request):
    if request.method == 'POST':
        cliente_id = request.POST.get('cliente')
        funcao_id = request.POST.get('funcao')
        modelo_id = request.POST.get('modelo')
        tipo = request.POST.get('tipo')
        host = request.POST.get('hostname')
        host_ipv6 = request.POST.get('hostname_ipv6')
        porta = request.POST.get('porta')
        protocolo = request.POST.get('protocolo')
        usuario = request.POST.get('usuario')
        senha = request.POST.get('senha')
        senha_adm = request.POST.get('senha_adm')
        vlan = request.POST.get('vlan')

        # 🧠 Verifica se já existe um Acesso com o mesmo tipo
        if Acesso.objects.filter(tipo=tipo).exists():
            messages.error(request, f'O nome "{tipo}" já está cadastrado.')
            return redirect(reverse('listar_clientes') + f'?id={cliente_id}')

        # Se não existir, cria normalmente
        acesso = Acesso(
            cliente_id=cliente_id,
            funcao_id=funcao_id,
            modelo_id=modelo_id,
            tipo=tipo,
            host=host,
            host_ipv6=host_ipv6,
            porta=porta,
            protocolo=protocolo,
            usuario=usuario,
            senha=senha,
            senha_adm=senha_adm,
            vlan=vlan
        )
        acesso.save()
        messages.success(request, 'Acesso cadastrado com sucesso!')
        return redirect(reverse('listar_clientes') + f'?id={cliente_id}')
    
    else:
        return redirect('cadastrar_cliente')


def editar_cliente(request):
    if request.method == 'POST':
        cliente = get_object_or_404(Cliente, id=request.POST.get('id'))

        cliente.nome_empresa = request.POST.get('nome_empresa')
        cliente.cnpj = request.POST.get('cnpj')
        cliente.cep = request.POST.get('cep')
        cliente.endereco = request.POST.get('endereco')
        cliente.estado = request.POST.get('estado')
        cliente.cidade = request.POST.get('cidade')
        cliente.telefone = request.POST.get('telefone')
        cliente.email = request.POST.get('email')

        cliente.save()
        messages.success(request, "Cliente atualizado com sucesso!")
        return redirect('listar_clientes')

    messages.error(request, "Erro ao atualizar cliente.")
    return redirect('listar_clientes')


