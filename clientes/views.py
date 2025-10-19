from django.shortcuts import render,redirect, get_object_or_404
from . models import Cliente
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from . models import Acesso
from django.urls import reverse
from modelo_equipamento.models import Modelo_equipamento
from funcao_equipamento.models import Funcao_equipamento
from django.http import JsonResponse
from .models import Documento

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

    # ✅ ADICIONE ESTA LINHA - Busca os documentos do cliente
    documentos = Documento.objects.filter(cliente=cliente).order_by('-data_upload')

    return render(request, 'listar.html', {
        'cliente': cliente,
        'funcoes': funcoes,
        'acessos': acessos,
        'funcao_selecionada': funcao_selecionada,
        'modelos': modelos,
        'funcao_equipamentos': funcao_equipamentos,
        'documentos': documentos,  # ✅ ADICIONE ESTA LINHA
    })


@login_required(login_url='login')
def cadastrar_cliente(request):
    if request.method == 'GET':
        clientes = Cliente.objects.all()
        usuario = User.objects.all()
        return render(request, 'cadastrar_cliente.html', {
            'clientes': clientes, 'usuario': usuario})
    
    elif request.method == 'POST':
        nome_empresa = request.POST.get('nome_empresa')
        email = request.POST.get('email')
        cnpj = request.POST.get('cnpj')
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

        # ✅ NOVA VALIDAÇÃO: Verifica se o usuário já está vinculado a outro cliente
        if Cliente.objects.filter(usuario_id=usuario_id).exists():
            messages.error(request, 'Erro: Este usuário já está vinculado a outro cliente.')
            return redirect('cadastrar_cliente')

        cliente = Cliente(
            nome_empresa=nome_empresa,
            email=email,
            telefone=telefone,
            endereco=endereco,
            cidade=cidade,
            estado=estado,
            cep=cep,
            cnpj=cnpj,
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
        # 🧠 Verifica se já existe um Acesso com o mesmo tipo para o mesmo cliente
        if Acesso.objects.filter(tipo=tipo, cliente_id=cliente_id).exists():
            messages.error(request, f'O tipo "{tipo}" já está cadastrado para este cliente.')
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


@login_required(login_url='login')
def editar_cliente(request):
    if request.method == 'POST':
        cliente_id = request.POST.get('id')
        cliente = get_object_or_404(Cliente, id=cliente_id)
        
        email = request.POST.get('email')
        telefone = request.POST.get('telefone')
        
        # Verifica se email já existe em outro cliente
        if Cliente.objects.filter(email=email).exclude(id=cliente_id).exists():
            messages.error(request, 'Erro: Já existe um cliente com esse email cadastrado.')
            return redirect('cadastrar_cliente')
        
        # Verifica se telefone já existe em outro cliente
        if Cliente.objects.filter(telefone=telefone).exclude(id=cliente_id).exists():
            messages.error(request, 'Erro: Já existe um cliente com esse telefone cadastrado.')
            return redirect('cadastrar_cliente')
        
        # Atualiza os dados
        cliente.nome_empresa = request.POST.get('nome_empresa')
        cliente.cnpj = request.POST.get('cnpj')
        cliente.cep = request.POST.get('cep')
        cliente.endereco = request.POST.get('endereco')
        cliente.estado = request.POST.get('estado')
        cliente.cidade = request.POST.get('cidade')
        cliente.telefone = telefone
        cliente.email = email
        cliente.usuario_id = request.POST.get('usuario')
        
        cliente.save()
        messages.success(request, "Cliente atualizado com sucesso!")
        return redirect('cadastrar_cliente')
    
    messages.error(request, "Método não permitido.")
    return redirect('cadastrar_cliente')


@login_required(login_url='login')
def deletar_cliente(request):
    if request.method == 'POST':
        cliente_id = request.POST.get('id')
        cliente = get_object_or_404(Cliente, id=cliente_id)
        
        nome_empresa = cliente.nome_empresa
        
        # Deleta o cliente (os acessos relacionados serão deletados automaticamente se houver CASCADE)
        cliente.delete()
        
        messages.success(request, f'Cliente "{nome_empresa}" excluído com sucesso!')
        return redirect('cadastrar_cliente')
    
    messages.error(request, 'Método não permitido.')
    return redirect('cadastrar_cliente')



# views.py
def buscar_acesso(request, acesso_id):
    try:
        acesso = Acesso.objects.get(id=acesso_id)
        
        # DEBUG - verifique o que está None
        print(f"DEBUG - Acesso: {acesso}")
        print(f"DEBUG - Funcao: {acesso.funcao}")
        print(f"DEBUG - Modelo: {acesso.modelo}")
        
        data = {
            'id': acesso.id,
            'tipo': acesso.tipo,
            'host': acesso.host,
            'host_ipv6': acesso.host_ipv6 or '',
            'protocolo': acesso.protocolo,
            'porta': acesso.porta,
            'usuario': acesso.usuario,
            'senha': acesso.senha,
            'senha_adm': acesso.senha_adm or '',
            'vlan': acesso.vlan or '',
            
            # Corrigindo os campos que podem ser None
            'funcao_id': acesso.funcao.id if acesso.funcao and hasattr(acesso.funcao, 'id') else '',
            'funcao_nome': acesso.funcao.descricao if acesso.funcao and hasattr(acesso.funcao, 'descricao') else '',
            
            'modelo_id': acesso.modelo.id if acesso.modelo and hasattr(acesso.modelo, 'id') else '',
            'modelo_nome': acesso.modelo.nome if acesso.modelo and hasattr(acesso.modelo, 'nome') else '',
        }
        
        print(f"DEBUG - Data enviada: {data}")
        return JsonResponse(data)
        
    except Acesso.DoesNotExist:
        return JsonResponse({'error': 'Acesso não encontrado'}, status=404)
    except Exception as e:
        print(f"ERRO: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)


        

@login_required(login_url='login')
def editar_acesso(request, acesso_id):
    if request.method == 'POST':
        try:
            acesso = get_object_or_404(Acesso, id=acesso_id)
            
            acesso.tipo = request.POST.get('tipo')
            acesso.host = request.POST.get('hostname')
            acesso.host_ipv6 = request.POST.get('hostname_ipv6')
            acesso.protocolo = request.POST.get('protocolo')
            acesso.porta = request.POST.get('porta')
            acesso.usuario = request.POST.get('usuario')
            acesso.senha = request.POST.get('senha')
            acesso.senha_adm = request.POST.get('senha_adm')
            acesso.vlan = request.POST.get('vlan')
            
            funcao_id = request.POST.get('funcao')
            modelo_id = request.POST.get('modelo')
            
            acesso.funcao = get_object_or_404(Funcao_equipamento, id=funcao_id)
            acesso.modelo = get_object_or_404(Modelo_equipamento, id=modelo_id)
            
            acesso.save()
            
            messages.success(request, 'Acesso atualizado com sucesso!')
            return redirect(f"{reverse('listar_clientes')}?id={acesso.cliente.id}")
        except Exception as e:
            messages.error(request, f'Erro ao editar acesso: {str(e)}')
            return redirect(f"{reverse('listar_clientes')}?id={acesso.cliente.id}")
    
    return redirect('listar_clientes')



@login_required(login_url='login')
def deletar_acesso(request, acesso_id):
    acesso = get_object_or_404(Acesso, id=acesso_id)
    cliente_id = acesso.cliente.id
    tipo_acesso = acesso.tipo
    
    acesso.delete()
    
    messages.success(request, f'Acesso "{tipo_acesso}" excluído com sucesso!')
    return redirect(f"{reverse('listar_clientes')}?id={cliente_id}")



@login_required(login_url='login')
def upload_documento(request):
    if request.method == 'POST':
        cliente_id = request.POST.get('cliente')
        arquivo = request.FILES.get('arquivo')
        nome = arquivo.name if arquivo else None

        if not arquivo:
            messages.error(request, "Nenhum arquivo selecionado.")
            return redirect(reverse('listar_clientes') + f'?id={cliente_id}')

        Documento.objects.create(
            cliente_id=cliente_id,
            nome=nome,
            arquivo=arquivo
        )
        messages.success(request, f'Documento "{nome}" enviado com sucesso!')
        return redirect(reverse('listar_clientes') + f'?id={cliente_id}')
    else:
        return redirect('listar_clientes')

@login_required(login_url='login')
def deletar_documento(request, documento_id):
    documento = get_object_or_404(Documento, id=documento_id)
    cliente_id = documento.cliente.id

    # Deleta o arquivo do disco também
    if documento.arquivo and documento.arquivo.storage.exists(documento.arquivo.name):
        documento.arquivo.delete(save=False)

    documento.delete()
    messages.success(request, f'Documento "{documento.nome}" excluído com sucesso!')
    return redirect(reverse('listar_clientes') + f'?id={cliente_id}')