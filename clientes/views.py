from django.shortcuts import render,redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.urls import reverse
from modelo_equipamento.models import Modelo_equipamento
from funcao_equipamento.models import Funcao_equipamento
from django.http import JsonResponse
from .models import Cliente, Acesso, Documento, ArquivoVPN, ImagemTopologia, Categoria, Chamado, ComentarioChamado
from .models import ProxyServer
from .decorators import (
    cliente_login_required, 
    admin_required, 
    cliente_or_admin_required,
    cliente_can_view_cliente
)


@login_required(login_url='login')
@cliente_can_view_cliente  # ✅ NOVO: Validar se cliente pode ver este cliente
def listar_clientes(request):
    """
    View que lista acessos e dados do cliente.
    - Clientes podem ver APENAS seus próprios dados
    - Admins podem ver qualquer cliente
    """
    id_cliente = request.GET.get('id')
    
    if not id_cliente:
        messages.error(request, 'Cliente não especificado.')
        return redirect('quadro_geral')
    
    cliente = get_object_or_404(Cliente, id=id_cliente)
    
    # ✅ VALIDAÇÃO: Verificar permissão
    if not request.user.is_staff and not request.user.is_superuser:
        # Se é cliente, verificar se é o próprio cliente
        try:
            cliente_auth = Cliente.objects.get(usuario=request.user)
            if cliente_auth.id != cliente.id:
                messages.error(request, 'Você não possui permissão para visualizar este cliente.')
                return redirect('quadro_geral')
        except Cliente.DoesNotExist:
            messages.error(request, 'Você não é um cliente válido.')
            return redirect('login')
    
    # Restante do código existente...
    funcao_selecionada = request.GET.get('funcao')
    modelos = Modelo_equipamento.objects.all()
    funcao_equipamentos = Funcao_equipamento.objects.all()

    funcoes = cliente.acessos.values_list('funcao', flat=True).distinct()

    if funcao_selecionada:
        acessos = cliente.acessos.filter(funcao=funcao_selecionada)
    else:
        acessos = cliente.acessos.all()

    documentos = Documento.objects.filter(cliente=cliente).order_by('-data_upload')
    arquivos_vpn = ArquivoVPN.objects.filter(cliente=cliente).order_by('-data_upload')
    imagens_topologia = ImagemTopologia.objects.filter(cliente=cliente).order_by('-data_upload')
    proxies = ProxyServer.objects.filter(cliente=cliente).order_by('-ativo', 'nome')

    # ✅ NOVO: Adicionar flag de tipo de usuário ao contexto
    is_cliente = False
    try:
        if Cliente.objects.get(usuario=request.user).id == cliente.id:
            is_cliente = True
    except:
        pass

    return render(request, 'listar.html', {
        'cliente': cliente,
        'funcoes': funcoes,
        'acessos': acessos,
        'funcao_selecionada': funcao_selecionada,
        'modelos': modelos,
        'funcao_equipamentos': funcao_equipamentos,
        'documentos': documentos,
        'arquivos_vpn': arquivos_vpn,
        'imagens_topologia': imagens_topologia,
        'proxies': proxies,
        'is_cliente': is_cliente,  # ✅ NOVO: Flag para identificar cliente
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
        winbox = request.POST.get('winbox')

        # ✅ Tratar VLAN vazia ou inválida
        if vlan == '' or vlan is None:
            vlan = None
        else:
            try:
                vlan = int(vlan)
            except ValueError:
                vlan = None  # evita erro se o usuário digitar algo não numérico

        # ✅ Tratar WINBOX vazio ou inválido
        if winbox == '' or winbox is None:
            winbox = None
        else:
            try:
                winbox = int(winbox)
            except ValueError:
                winbox = None  # evita erro se o usuário digitar algo não numérico

        # 🧠 Verifica se já existe um Acesso com o mesmo tipo para o mesmo cliente
        if Acesso.objects.filter(tipo=tipo, cliente_id=cliente_id).exists():
            messages.error(request, f'O tipo "{tipo}" já está cadastrado para este cliente.')
            return redirect(reverse('listar_clientes') + f'?id={cliente_id}')

        # ✅ Cria o registro normalmente
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
            vlan=vlan,
            winbox=winbox
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



@login_required(login_url='login')
def buscar_acesso(request, acesso_id):
    """
    Validar se cliente pode acessar este acesso
    """
    try:
        acesso = Acesso.objects.get(id=acesso_id)
        
        # ✅ Verificar permissão
        if not request.user.is_staff and not request.user.is_superuser:
            cliente = Cliente.objects.get(usuario=request.user)
            if acesso.cliente.id != cliente.id:
                return JsonResponse({'error': 'Sem permissão'}, status=403)
        
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
            'winbox': acesso.winbox or '',
            'funcao_id': acesso.funcao.id if acesso.funcao and hasattr(acesso.funcao, 'id') else '',
            'funcao_nome': acesso.funcao.descricao if acesso.funcao and hasattr(acesso.funcao, 'descricao') else '',
            'modelo_id': acesso.modelo.id if acesso.modelo and hasattr(acesso.modelo, 'id') else '',
            'modelo_nome': acesso.modelo.nome if acesso.modelo and hasattr(acesso.modelo, 'nome') else '',
        }
        
        return JsonResponse(data)
        
    except Acesso.DoesNotExist:
        return JsonResponse({'error': 'Acesso não encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
        

@login_required(login_url='login')
def editar_acesso(request, acesso_id):
    if request.method == 'POST':
        try:
            acesso = get_object_or_404(Acesso, id=acesso_id)

            # Atualiza campos diretos
            acesso.tipo = request.POST.get('tipo')
            acesso.host = request.POST.get('hostname')
            acesso.host_ipv6 = request.POST.get('hostname_ipv6')
            acesso.protocolo = request.POST.get('protocolo')
            acesso.porta = request.POST.get('porta')
            acesso.usuario = request.POST.get('usuario')
            acesso.senha = request.POST.get('senha')
            acesso.senha_adm = request.POST.get('senha_adm')

            # ✅ Tratar WINBOX vazio ou inválido
            winbox = request.POST.get('winbox')
            if winbox == '' or winbox is None:
                acesso.winbox = None
            else:
                try:
                    acesso.winbox = int(winbox)
                except ValueError:
                    acesso.winbox = None  # evita erro se o campo não for numérico

            # ✅ Tratar VLAN vazia ou inválida
            vlan = request.POST.get('vlan')
            if vlan == '' or vlan is None:
                acesso.vlan = None
            else:
                try:
                    acesso.vlan = int(vlan)
                except ValueError:
                    acesso.vlan = None  # evita erro se o campo não for numérico

            # ✅ Atualizar função e modelo apenas se enviados
            funcao_id = request.POST.get('funcao')
            modelo_id = request.POST.get('modelo')

            if funcao_id:
                acesso.funcao = get_object_or_404(Funcao_equipamento, id=funcao_id)
            else:
                acesso.funcao = None

            if modelo_id:
                acesso.modelo = get_object_or_404(Modelo_equipamento, id=modelo_id)
            else:
                acesso.modelo = None

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


    # ========================================
# VIEWS PARA VPN
# ========================================

@login_required(login_url='login')
def upload_vpn(request):
    if request.method == 'POST':
        cliente_id = request.POST.get('cliente')
        arquivo = request.FILES.get('arquivo')
        nome = arquivo.name if arquivo else None
        usuario = request.POST.get('usuario')
        senha = request.POST.get('senha')
        private_key = request.POST.get('private_key')

        if not arquivo:
            messages.error(request, "Nenhum arquivo selecionado.")
            return redirect(reverse('listar_clientes') + f'?id={cliente_id}')

        ArquivoVPN.objects.create(
            cliente_id=cliente_id,
            nome=nome,
            arquivo=arquivo,
            usuario=usuario,
            senha=senha,
            private_key=private_key
        )
        messages.success(request, f'Arquivo VPN "{nome}" enviado com sucesso!')
        return redirect(reverse('listar_clientes') + f'?id={cliente_id}')
    else:
        return redirect('listar_clientes')


@login_required(login_url='login')
def deletar_vpn(request, vpn_id):
    vpn = get_object_or_404(ArquivoVPN, id=vpn_id)
    cliente_id = vpn.cliente.id

    # Deleta o arquivo do disco também
    if vpn.arquivo and vpn.arquivo.storage.exists(vpn.arquivo.name):
        vpn.arquivo.delete(save=False)

    vpn.delete()
    messages.success(request, f'Arquivo VPN "{vpn.nome}" excluído com sucesso!')
    return redirect(reverse('listar_clientes') + f'?id={cliente_id}')


# ========================================
# VIEWS PARA TOPOLOGIA
# ========================================

@login_required(login_url='login')
def upload_topologia(request):
    if request.method == 'POST':
        cliente_id = request.POST.get('cliente')
        imagem = request.FILES.get('imagem')
        nome = imagem.name if imagem else None

        if not imagem:
            messages.error(request, "Nenhuma imagem selecionada.")
            return redirect(reverse('listar_clientes') + f'?id={cliente_id}')

        # Validar se é uma imagem
        valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp']
        if not any(nome.lower().endswith(ext) for ext in valid_extensions):
            messages.error(request, "Apenas imagens são permitidas (JPG, PNG, GIF, SVG, WEBP).")
            return redirect(reverse('listar_clientes') + f'?id={cliente_id}')

        ImagemTopologia.objects.create(
            cliente_id=cliente_id,
            nome=nome,
            imagem=imagem
        )
        messages.success(request, f'Imagem de topologia "{nome}" enviada com sucesso!')
        return redirect(reverse('listar_clientes') + f'?id={cliente_id}')
    else:
        return redirect('listar_clientes')


@login_required(login_url='login')
def deletar_topologia(request, topologia_id):
    topologia = get_object_or_404(ImagemTopologia, id=topologia_id)
    cliente_id = topologia.cliente.id

    # Deleta a imagem do disco também
    if topologia.imagem and topologia.imagem.storage.exists(topologia.imagem.name):
        topologia.imagem.delete(save=False)

    topologia.delete()
    messages.success(request, f'Imagem de topologia "{topologia.nome}" excluída com sucesso!')
    return redirect(reverse('listar_clientes') + f'?id={cliente_id}')

@login_required(login_url='login')
def buscar_vpn(request, vpn_id):
    try:
        vpn = ArquivoVPN.objects.get(id=vpn_id)
        
        data = {
            'id': vpn.id,
            'nome': vpn.nome,
            'usuario': vpn.usuario or '',
            'senha': vpn.senha or '',
            'private_key': vpn.private_key or '',
        }
        
        return JsonResponse(data)
        
    except ArquivoVPN.DoesNotExist:
        return JsonResponse({'error': 'VPN não encontrada'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url='login')
def editar_vpn(request, vpn_id):
    if request.method == 'POST':
        try:
            vpn = get_object_or_404(ArquivoVPN, id=vpn_id)
            
            vpn.usuario = request.POST.get('usuario')
            vpn.senha = request.POST.get('senha')
            vpn.private_key = request.POST.get('private_key')
            
            vpn.save()
            
            messages.success(request, 'Configuração VPN atualizada com sucesso!')
            return redirect(f"{reverse('listar_clientes')}?id={vpn.cliente.id}")
        except Exception as e:
            messages.error(request, f'Erro ao editar VPN: {str(e)}')
            return redirect(f"{reverse('listar_clientes')}?id={vpn.cliente.id}")
    
    return redirect('listar_clientes')




    # ========================================
# VIEWS PARA CATEGORIAS
# ========================================

@login_required(login_url='login')
def cadastrar_categoria(request):
    if request.method == 'POST':
        nome = request.POST.get('nome')
        descricao = request.POST.get('descricao', '')

        if Categoria.objects.filter(nome__iexact=nome).exists():
            return JsonResponse({'error': 'Categoria já existe'}, status=400)

        categoria = Categoria.objects.create(
            nome=nome,
            descricao=descricao
        )
        
        return JsonResponse({
            'id': categoria.id,
            'nome': categoria.nome,
            'message': 'Categoria cadastrada com sucesso!'
        })
    
    return JsonResponse({'error': 'Método não permitido'}, status=405)


@login_required(login_url='login')
def buscar_categorias(request):
    query = request.GET.get('q', '')
    categorias = Categoria.objects.filter(nome__icontains=query)[:10]
    
    results = [{'id': cat.id, 'nome': cat.nome} for cat in categorias]
    return JsonResponse({'results': results})


@login_required(login_url='login')
def listar_chamados_cliente(request):
    """
    Cliente só pode listar seus próprios chamados
    """
    cliente_id = request.GET.get('id')
    
    # ✅ Verificar permissão
    if not request.user.is_staff and not request.user.is_superuser:
        cliente = Cliente.objects.get(usuario=request.user)
        if str(cliente.id) != str(cliente_id):
            return JsonResponse({'error': 'Sem permissão'}, status=403)
    
    cliente = get_object_or_404(Cliente, id=cliente_id)
    
    chamados = Chamado.objects.filter(cliente=cliente).select_related(
        'categoria', 'responsavel', 'criado_por'
    ).prefetch_related('comentarios')
    
    return JsonResponse({
        'chamados': [{
            'id': chamado.id,
            'titulo': chamado.titulo,
            'categoria': chamado.categoria.nome if chamado.categoria else '',
            'prioridade': chamado.get_prioridade_display(),
            'status': chamado.get_status_display(),
            'departamento': chamado.get_departamento_display(),
            'responsavel': chamado.responsavel.get_full_name() or chamado.responsavel.username if chamado.responsavel else 'Não atribuído',
            'data_criacao': chamado.data_criacao.strftime('%d/%m/%Y %H:%M'),
            'total_comentarios': chamado.comentarios.count()
        } for chamado in chamados]
    })



@login_required(login_url='login')
def cadastrar_chamado(request):
    if request.method == 'POST':
        try:
            cliente_id = request.POST.get('cliente')
            categoria_id = request.POST.get('categoria')
            prioridade = request.POST.get('prioridade')
            departamento = request.POST.get('departamento')
            responsavel_id = request.POST.get('responsavel')
            titulo = request.POST.get('titulo')
            descricao = request.POST.get('descricao')
            comentario_inicial = request.POST.get('comentario', '')

            # Validações
            if not all([cliente_id, prioridade, departamento, titulo, descricao]):
                messages.error(request, 'Preencha todos os campos obrigatórios.')
                return redirect(reverse('listar_clientes') + f'?id={cliente_id}')

            # Criar chamado
            chamado = Chamado.objects.create(
                cliente_id=cliente_id,
                categoria_id=categoria_id if categoria_id else None,
                prioridade=prioridade,
                departamento=departamento,
                responsavel_id=responsavel_id if responsavel_id else None,
                criado_por=request.user,
                titulo=titulo,
                descricao=descricao
            )

            # Adicionar comentário inicial se houver
            if comentario_inicial:
                ComentarioChamado.objects.create(
                    chamado=chamado,
                    usuario=request.user,
                    comentario=comentario_inicial
                )

            messages.success(request, f'Chamado #{chamado.id} cadastrado com sucesso!')
            return redirect(reverse('listar_clientes') + f'?id={cliente_id}')

        except Exception as e:
            messages.error(request, f'Erro ao cadastrar chamado: {str(e)}')
            return redirect(reverse('listar_clientes') + f'?id={cliente_id}')
    
    return redirect('listar_clientes')


@login_required(login_url='login')
def buscar_chamado(request, chamado_id):
    try:
        chamado = Chamado.objects.select_related(
            'categoria', 'cliente', 'responsavel', 'criado_por'
        ).prefetch_related('comentarios__usuario').get(id=chamado_id)
        
        data = {
            'id': chamado.id,
            'titulo': chamado.titulo,
            'descricao': chamado.descricao,
            'categoria_id': chamado.categoria.id if chamado.categoria else '',
            'categoria_nome': chamado.categoria.nome if chamado.categoria else '',
            'prioridade': chamado.prioridade,
            'departamento': chamado.departamento,
            'status': chamado.status,
            'responsavel_id': chamado.responsavel.id if chamado.responsavel else '',
            'responsavel_nome': chamado.responsavel.get_full_name() or chamado.responsavel.username if chamado.responsavel else '',
            'cliente_id': chamado.cliente.id,
            'cliente_nome': chamado.cliente.nome_empresa,
            'data_criacao': chamado.data_criacao.strftime('%d/%m/%Y %H:%M'),
            'comentarios': [{
                'id': comentario.id,
                'usuario': comentario.usuario.get_full_name() or comentario.usuario.username,
                'comentario': comentario.comentario,
                'data': comentario.data_criacao.strftime('%d/%m/%Y %H:%M'),
                'is_internal': comentario.is_internal
            } for comentario in chamado.comentarios.all()]
        }
        
        return JsonResponse(data)
        
    except Chamado.DoesNotExist:
        return JsonResponse({'error': 'Chamado não encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url='login')
def editar_chamado(request, chamado_id):
    if request.method == 'POST':
        try:
            chamado = get_object_or_404(Chamado, id=chamado_id)
            
            chamado.titulo = request.POST.get('titulo')
            chamado.descricao = request.POST.get('descricao')
            chamado.prioridade = request.POST.get('prioridade')
            chamado.departamento = request.POST.get('departamento')
            chamado.status = request.POST.get('status')
            
            categoria_id = request.POST.get('categoria')
            chamado.categoria_id = categoria_id if categoria_id else None
            
            responsavel_id = request.POST.get('responsavel')
            chamado.responsavel_id = responsavel_id if responsavel_id else None
            
            chamado.save()
            
            # Adicionar comentário de atualização se houver
            comentario_novo = request.POST.get('comentario_novo')
            if comentario_novo:
                ComentarioChamado.objects.create(
                    chamado=chamado,
                    usuario=request.user,
                    comentario=comentario_novo
                )
            
            messages.success(request, f'Chamado #{chamado.id} atualizado com sucesso!')
            return redirect(f"{reverse('listar_clientes')}?id={chamado.cliente.id}")
            
        except Exception as e:
            messages.error(request, f'Erro ao editar chamado: {str(e)}')
            return redirect('listar_clientes')
    
    return redirect('listar_clientes')


@login_required(login_url='login')
def deletar_chamado(request, chamado_id):
    if request.method == 'POST':
        chamado = get_object_or_404(Chamado, id=chamado_id)
        cliente_id = chamado.cliente.id
        chamado_numero = chamado.id
        
        chamado.delete()
        
        messages.success(request, f'Chamado #{chamado_numero} excluído com sucesso!')
        return redirect(f"{reverse('listar_clientes')}?id={cliente_id}")
    
    return redirect('listar_clientes')


@login_required(login_url='login')
def adicionar_comentario(request, chamado_id):
    if request.method == 'POST':
        try:
            chamado = get_object_or_404(Chamado, id=chamado_id)
            comentario_texto = request.POST.get('comentario')
            is_internal = request.POST.get('is_internal') == 'true'
            
            if comentario_texto:
                ComentarioChamado.objects.create(
                    chamado=chamado,
                    usuario=request.user,
                    comentario=comentario_texto,
                    is_internal=is_internal
                )
                messages.success(request, 'Comentário adicionado com sucesso!')
            else:
                messages.error(request, 'O comentário não pode estar vazio.')
                
            return redirect(f"{reverse('listar_clientes')}?id={chamado.cliente.id}")
            
        except Exception as e:
            messages.error(request, f'Erro ao adicionar comentário: {str(e)}')
            return redirect('listar_clientes')
    
    return redirect('listar_clientes')


@login_required(login_url='login')
def buscar_usuarios(request):
    query = request.GET.get('q', '')
    usuarios = User.objects.filter(
        models.Q(username__icontains=query) |
        models.Q(first_name__icontains=query) |
        models.Q(last_name__icontains=query)
    )[:10]
    
    results = [{
        'id': user.id,
        'nome': user.get_full_name() or user.username,
        'username': user.username
    } for user in usuarios]
    
    return JsonResponse({'results': results})


@login_required(login_url='login')
def buscar_clientes_chamado(request):
    query = request.GET.get('q', '')
    clientes = Cliente.objects.filter(
        models.Q(nome_empresa__icontains=query) |
        models.Q(cnpj__icontains=query)
    )[:10]
    
    results = [{
        'id': cliente.id,
        'nome': cliente.nome_empresa,
        'cnpj': cliente.cnpj
    } for cliente in clientes]
    
    return JsonResponse({'results': results})


# ========================================
# VIEWS PARA GERENCIAR SERVIDORES PROXY (POR CLIENTE)
# ========================================

@login_required(login_url='login')
def cadastrar_proxy(request):
    """Cadastra um novo servidor proxy para um cliente"""
    if request.method == 'POST':
        cliente_id = request.POST.get('cliente')
        nome = request.POST.get('nome')
        host = request.POST.get('host')
        porta = request.POST.get('porta', 22)
        usuario = request.POST.get('usuario')
        senha = request.POST.get('senha')
        ativo = request.POST.get('ativo') == 'on'
        
        # Validações básicas
        if not all([cliente_id, nome, host, porta, usuario, senha]):
            messages.error(request, 'Preencha todos os campos obrigatórios.')
            return redirect(reverse('listar_clientes') + f'?id={cliente_id}')
        
        # Criar proxy
        try:
            ProxyServer.objects.create(
                cliente_id=cliente_id,
                nome=nome,
                host=host,
                porta=int(porta),
                usuario=usuario,
                senha=senha,
                ativo=ativo
            )
            messages.success(request, f'Túnel SSH "{nome}" cadastrado com sucesso!')
        except Exception as e:
            messages.error(request, f'Erro ao cadastrar túnel: {str(e)}')
        
        return redirect(reverse('listar_clientes') + f'?id={cliente_id}')
    
    return redirect('listar_clientes')


@login_required(login_url='login')
def buscar_proxy(request, proxy_id):
    """Busca dados de um proxy específico (AJAX)"""
    try:
        proxy = ProxyServer.objects.get(id=proxy_id)
        
        data = {
            'id': proxy.id,
            'nome': proxy.nome,
            'host': proxy.host,
            'porta': proxy.porta,
            'usuario': proxy.usuario,
            'senha': proxy.senha,
            'ativo': proxy.ativo,
            'data_criacao': proxy.data_criacao.strftime('%d/%m/%Y %H:%M')
        }
        
        return JsonResponse(data)
        
    except ProxyServer.DoesNotExist:
        return JsonResponse({'error': 'Túnel SSH não encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url='login')
def editar_proxy(request, proxy_id):
    """Edita um servidor proxy existente"""
    if request.method == 'POST':
        try:
            proxy = get_object_or_404(ProxyServer, id=proxy_id)
            
            proxy.nome = request.POST.get('nome')
            proxy.host = request.POST.get('host')
            proxy.porta = int(request.POST.get('porta', 22))
            proxy.usuario = request.POST.get('usuario')
            proxy.senha = request.POST.get('senha')
            proxy.ativo = request.POST.get('ativo') == 'on'
            
            proxy.save()
            
            messages.success(request, f'Túnel SSH "{proxy.nome}" atualizado com sucesso!')
            return redirect(reverse('listar_clientes') + f'?id={proxy.cliente.id}')
            
        except Exception as e:
            messages.error(request, f'Erro ao editar túnel: {str(e)}')
            return redirect('listar_clientes')
    
    return redirect('listar_clientes')


@login_required(login_url='login')
def deletar_proxy(request, proxy_id):
    """Deleta um servidor proxy"""
    if request.method == 'POST':
        proxy = get_object_or_404(ProxyServer, id=proxy_id)
        cliente_id = proxy.cliente.id
        nome = proxy.nome
        
        proxy.delete()
        
        messages.success(request, f'Túnel SSH "{nome}" excluído com sucesso!')
        return redirect(reverse('listar_clientes') + f'?id={cliente_id}')
    
    return redirect('listar_clientes')


@login_required(login_url='login')
def testar_proxy(request, proxy_id):
    """Testa a conexão com um servidor proxy (AJAX)"""
    try:
        proxy = ProxyServer.objects.get(id=proxy_id)
        
        import paramiko
        
        # Tentar conectar ao proxy
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        ssh_client.connect(
            hostname=proxy.host,
            port=proxy.porta,
            username=proxy.usuario,
            password=proxy.senha,
            timeout=5,
            look_for_keys=False,
            allow_agent=False
        )
        
        ssh_client.close()
        
        return JsonResponse({
            'success': True,
            'message': f'✓ Conexão com túnel "{proxy.nome}" bem-sucedida!'
        })
        
    except paramiko.AuthenticationException:
        return JsonResponse({
            'success': False,
            'message': '✗ Erro de autenticação. Verifique usuário e senha.'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'✗ Erro ao conectar: {str(e)}'
        }, status=400)


@login_required(login_url='login')
def toggle_proxy_status(request, proxy_id):
    """Ativa/Desativa um servidor proxy (AJAX)"""
    try:
        proxy = ProxyServer.objects.get(id=proxy_id)
        proxy.ativo = not proxy.ativo
        proxy.save()
        
        status_texto = 'ativado' if proxy.ativo else 'desativado'
        
        return JsonResponse({
            'success': True,
            'ativo': proxy.ativo,
            'message': f'Túnel SSH "{proxy.nome}" {status_texto} com sucesso!'
        })
        
    except ProxyServer.DoesNotExist:
        return JsonResponse({'error': 'Túnel SSH não encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)