from django.shortcuts import render,redirect, get_object_or_404
from django.views.decorators.http import require_http_methods
from django.contrib import messages
import threading
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.urls import reverse
from modelo_equipamento.models import Modelo_equipamento
from funcao_equipamento.models import Funcao_equipamento
from django.http import JsonResponse
from .models import Cliente, Acesso, Documento, ArquivoVPN, ImagemTopologia, Categoria, Chamado, ComentarioChamado, BackupLog,  BackupTemplate
from .models import ProxyServer
from .decorators import admin_required, cliente_login_required
from .decorators import (
    cliente_login_required, 
    admin_required, 
    cliente_or_admin_required,
    cliente_can_view_cliente
)
import json
import pexpect
import telnetlib
import threading
import ipaddress
import logging
import paramiko
import socket
import os
from pathlib import Path
from datetime import datetime
from django.conf import settings
from django.http import FileResponse
import time
from .models import BackupTemplate

# Instalar: pip install netmiko
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException


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
@admin_required  # ← ADICIONAR ESTA LINHA
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
        backup_habilitado = request.POST.get('backup_habilitado') == 'on'
        backup_template_id = request.POST.get('backup_template')
        backup_automatico = request.POST.get('backup_automatico') == 'on'

        # ✅ Se funcao_id for vazio ou None, usa o padrão 13
        if not funcao_id or funcao_id == '':
            funcao_id = 13

        # ✅ Tratar VLAN vazia ou inválida
        if vlan == '' or vlan is None:
            vlan = None
        else:
            try:
                vlan = int(vlan)
            except ValueError:
                vlan = None

        # ✅ Tratar WINBOX vazio ou inválido
        if winbox == '' or winbox is None:
            winbox = None
        else:
            try:
                winbox = int(winbox)
            except ValueError:
                winbox = None

        # 🧠 Verifica se já existe um Acesso com o mesmo tipo para o mesmo cliente
        if Acesso.objects.filter(tipo=tipo, cliente_id=cliente_id).exists():
            messages.error(request, f'O tipo "{tipo}" já está cadastrado para este cliente.')
            return redirect(reverse('listar_clientes') + f'?id={cliente_id}')

        # ✅ Cria o registro normalmente
        acesso = Acesso(
            cliente_id=cliente_id,
            funcao_id=funcao_id,  # ✅ Agora sempre terá um valor (13 por padrão)
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
            winbox=winbox,
            backup_habilitado=backup_habilitado,
            backup_template_id=backup_template_id if backup_template_id else None,
            backup_automatico=backup_automatico
        )
        acesso.save()

        messages.success(request, 'Acesso cadastrado com sucesso!')
        return redirect(reverse('listar_clientes') + f'?id={cliente_id}')
    
    else:
        return redirect('cadastrar_cliente')


@login_required(login_url='login')
@admin_required  # ← ADICIONAR ESTA LINHA
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
@admin_required  # ← ADICIONAR ESTA LINHA
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
            'backup_habilitado': acesso.backup_habilitado,
            'backup_template_id': acesso.backup_template.id if acesso.backup_template else '',
            'backup_template_nome': acesso.backup_template.nome if acesso.backup_template else '',
            'backup_automatico': acesso.backup_automatico,
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
            acesso.backup_habilitado = request.POST.get('backup_habilitado') == 'on'
            template_id = request.POST.get('backup_template')
            acesso.backup_template_id = template_id if template_id else None
            acesso.backup_automatico = request.POST.get('backup_automatico') == 'on'

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


@login_required(login_url='login')
def cliente_dashboard(request):
    """
    Dashboard exclusivo para clientes
    - Ferramentas de rede
    - Chamados abertos
    - Link para acessos
    """
    if not request.user.is_authenticated:
        return redirect('login')
    
    # Se for admin, redireciona para o dashboard do admin
    if request.user.is_staff or request.user.is_superuser:
        return redirect('quadro_geral')
    
    # Buscar cliente vinculado
    try:
        cliente = Cliente.objects.get(usuario=request.user)
    except Cliente.DoesNotExist:
        messages.error(request, 'Você não está vinculado a um cliente.')
        return redirect('login')
    
    # Buscar chamados abertos do cliente
    chamados_abertos = Chamado.objects.filter(
        cliente=cliente,
        status__in=['aberto', 'em_andamento']
    ).order_by('-data_criacao')[:5]
    
    return render(request, 'cliente_dashboard.html', {
        'cliente': cliente,
        'chamados_abertos': chamados_abertos,
    })

@login_required(login_url='login')
def executar_backup_acesso(request, acesso_id):
    """Executa backup manual"""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Não autenticado'}, status=401)
    
    try:
        acesso = Acesso.objects.get(id=acesso_id)
        
        # Verificar permissão
        if not request.user.is_staff and not request.user.is_superuser:
            try:
                cliente = Cliente.objects.get(usuario=request.user)
                if acesso.cliente.id != cliente.id:
                    return JsonResponse({'error': 'Sem permissão'}, status=403)
            except Cliente.DoesNotExist:
                return JsonResponse({'error': 'Sem permissão'}, status=403)
        
        # Verificar se backup está habilitado
        if not acesso.backup_habilitado:
            return JsonResponse({
                'error': 'Backup não está habilitado para este acesso'
            }, status=400)
        
        if not acesso.backup_template:
            return JsonResponse({
                'error': 'Template de backup não configurado'
            }, status=400)
        
        # Executar backup
        resultado = realizar_backup(acesso, request.user)
        
        if resultado['sucesso']:
            return JsonResponse({
                'success': True,
                'message': f'Backup realizado com sucesso!',
                'arquivo': resultado['arquivo'],
                'tamanho': resultado['tamanho'],
                'duracao': resultado['duracao']
            })
        else:
            return JsonResponse({
                'error': resultado['erro']
            }, status=500)
            
    except Acesso.DoesNotExist:
        return JsonResponse({'error': 'Acesso não encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url='login')
def listar_backups_cliente(request):
    """Lista backups do cliente"""
    cliente_id = request.GET.get('id')
    
    if not cliente_id:
        return JsonResponse({'error': 'Cliente não especificado'}, status=400)
    
    # Verificar permissão
    if not request.user.is_staff and not request.user.is_superuser:
        try:
            cliente = Cliente.objects.get(usuario=request.user)
            if str(cliente.id) != str(cliente_id):
                return JsonResponse({'error': 'Sem permissão'}, status=403)
        except Cliente.DoesNotExist:
            return JsonResponse({'error': 'Sem permissão'}, status=403)
    
    cliente = get_object_or_404(Cliente, id=cliente_id)
    
    # Buscar backups
    backups = BackupLog.objects.filter(cliente=cliente).select_related(
        'acesso', 'template', 'executado_por'
    ).order_by('-data_backup')
    
    # Validar arquivos
    backups_validos = []
    backups_para_deletar = []
    
    for backup in backups:
        arquivo_path = os.path.join(settings.MEDIA_ROOT, backup.arquivo_path)
        if os.path.exists(arquivo_path):
            backups_validos.append(backup)
        else:
            backups_para_deletar.append(backup.id)
    
    if backups_para_deletar:
        BackupLog.objects.filter(id__in=backups_para_deletar).delete()
    
    return JsonResponse({
        'backups': [{
            'id': backup.id,
            'acesso_tipo': backup.acesso.tipo,
            'acesso_host': backup.acesso.host,
            'template': backup.template.nome if backup.template else 'N/A',
            'status': backup.get_status_display(),
            'status_code': backup.status,
            'tamanho': backup.get_tamanho_formatado(),
            'data': backup.data_backup.strftime('%d/%m/%Y %H:%M:%S'),
            'duracao': f"{backup.duracao_segundos:.2f}s",
            'executado_por': backup.executado_por.username if backup.executado_por else 'Sistema',
            'mensagem': backup.mensagem or '',
            'arquivo_path': backup.arquivo_path
        } for backup in backups_validos]
    })


def realizar_backup(acesso, usuario=None):
    """
    ✅ ULTRA CORRIGIDO: Leitura contínua com silence detection
    - Lê dados em loop enquanto chegam
    - Detecta quando parou de chegar dados (silence de 2s)
    - Timeout máximo de 120s para configs MUITO grandes
    - Captura 100% do output
    """
    inicio = time.time()
    ssh_tunnel = None
    ssh_process = None
    
    try:
        print(f"\n{'='*80}")
        print(f"🔄 INICIANDO BACKUP COM PEXPECT")
        print(f"{'='*80}")
        print(f"📋 Equipamento: {acesso.tipo}")
        print(f"📡 Host: {acesso.host}:{acesso.porta}")
        print(f"👤 Usuário: {acesso.usuario}")
        print(f"🔧 Modelo: {acesso.modelo}")
        print(f"📝 Template: {acesso.backup_template.nome if acesso.backup_template else 'N/A'}")
        
        # ✅ DETECTAR IP PRIVADO
        eh_privado = is_private_ip(acesso.host)
        print(f"🔍 IP Privado? {eh_privado}")
        
        host_conexao = acesso.host
        porta_conexao = int(acesso.porta) if acesso.porta else 22
        
        # ✅ CRIAR TÚNEL SE IP PRIVADO
        if eh_privado:
            print(f"\n{'='*80}")
            print(f"⚠️ IP PRIVADO - CRIANDO TÚNEL SSH")
            print(f"{'='*80}")
            
            proxy = ProxyServer.objects.filter(
                cliente=acesso.cliente,
                ativo=True
            ).first()
            
            if not proxy:
                raise Exception(
                    "❌ IP privado, mas nenhum proxy SSH ativo!\n"
                    "Configure um túnel SSH na aba 'Túneis SSH'."
                )
            
            print(f"✅ Proxy encontrado: {proxy.nome}")
            
            ssh_tunnel = criar_ssh_tunnel(
                {
                    'host': proxy.host,
                    'porta': proxy.porta,
                    'usuario': proxy.usuario,
                    'senha': proxy.senha
                },
                acesso.host,
                porta_conexao
            )
            
            host_conexao = ssh_tunnel['local_host']
            porta_conexao = ssh_tunnel['local_port']
            
            print(f"✅ Túnel criado: localhost:{porta_conexao} → {acesso.host}:{acesso.porta}")
            time.sleep(1)
        
        # ✅ PREPARAR DIRETÓRIO
        backup_dir = preparar_diretorio_backup(acesso.cliente.id, acesso.id)
        print(f"\n📁 Diretório de backup: {backup_dir}")
        
        # ✅ DETECTAR PROTOCOLO
        protocolo = detectar_protocolo(porta_conexao)
        print(f"🔌 Protocolo: {protocolo.upper()}")
        
        # ✅ CONECTAR
        print(f"\n{'='*80}")
        print(f"🔐 CONECTANDO")
        print(f"{'='*80}")
        
        if protocolo == 'ssh':
            ssh_process = conectar_ssh_backup(
                host_conexao,
                porta_conexao,
                acesso.usuario,
                acesso.senha,
                acesso.senha_adm
            )
        else:
            ssh_process = conectar_telnet_backup(
                host_conexao,
                porta_conexao,
                acesso.usuario,
                acesso.senha
            )
        
        print(f"✅ Conectado e autenticado!")
        
        # ✅ EXECUTAR COMANDOS
        print(f"\n{'='*80}")
        print(f"📋 EXECUTANDO COMANDOS")
        print(f"{'='*80}")
        
        output = ""
        comandos = acesso.backup_template.get_comandos_list()
        print(f"🔢 Total de comandos: {len(comandos)}\n")
        
        for i, comando in enumerate(comandos, 1):
            print(f"  [{i}/{len(comandos)}] {comando}")
            output += f"\n{'='*60}\n"
            output += f"Comando: {comando}\n"
            output += f"{'='*60}\n"
            
            try:
                # ✅ ENVIAR COMANDO
                print(f"    📤 Enviando...")
                ssh_process.send(comando + '\r')
                time.sleep(0.3)
                
                # ✅ LEITURA CONTÍNUA COM SILENCE DETECTION
                print(f"    ⏳ Lendo resultado (silence detection)...")
                resultado = ler_saida_comando(ssh_process)
                
                # ✅ Adicionar ao output
                if resultado and len(resultado) > 0:
                    output += resultado + "\n"
                    print(f"    ✅ OK ({len(resultado)} bytes)")
                else:
                    print(f"    ⚠️ Output vazio")
                    output += "\n"
                
            except Exception as cmd_error:
                print(f"    ❌ Erro: {cmd_error}")
                output += f"ERRO: {str(cmd_error)}\n"
                continue
        
        # ✅ DESCONECTAR
        try:
            ssh_process.send('exit\r')
            time.sleep(0.5)
            ssh_process.close()
            print(f"\n🔌 Desconectado")
        except:
            pass
        
        if len(output) < 100:
            raise Exception("Backup vazio ou muito pequeno. Verifique comandos.")
        
        # ✅ SALVAR ARQUIVO
        print(f"\n{'='*80}")
        print(f"💾 SALVANDO ARQUIVO")
        print(f"{'='*80}")
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        nome_arquivo = f"{acesso.tipo.replace(' ', '_')}_{timestamp}.txt"
        arquivo_path = os.path.join(backup_dir, nome_arquivo)
        
        with open(arquivo_path, 'w', encoding='utf-8') as f:
            f.write(f"{'=' * 80}\n")
            f.write(f"BACKUP DE CONFIGURAÇÃO\n")
            f.write(f"{'=' * 80}\n")
            f.write(f"Cliente: {acesso.cliente.nome_empresa}\n")
            f.write(f"Equipamento: {acesso.tipo}\n")
            f.write(f"Host: {acesso.host}:{acesso.porta}\n")
            f.write(f"Acesso: {'VIA PROXY SSH' if eh_privado else 'DIRETO'}\n")
            f.write(f"Modelo: {acesso.modelo}\n")
            f.write(f"Template: {acesso.backup_template.nome}\n")
            f.write(f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
            f.write(f"Executado por: {usuario.username if usuario else 'Sistema'}\n")
            f.write(f"{'=' * 80}\n\n")
            f.write(output)
        
        tamanho = os.path.getsize(arquivo_path)
        duracao = time.time() - inicio
        
        print(f"✅ Arquivo: {nome_arquivo}")
        print(f"📊 Tamanho: {tamanho} bytes")
        print(f"⏱️ Duração: {duracao:.2f}s")
        
        arquivo_relativo = os.path.relpath(arquivo_path, settings.MEDIA_ROOT)
        
        # ✅ REGISTRAR LOG
        BackupLog.objects.create(
            acesso=acesso,
            cliente=acesso.cliente,
            template=acesso.backup_template,
            arquivo_path=arquivo_relativo,
            tamanho_bytes=tamanho,
            status='SUCESSO',
            mensagem=f"Backup realizado com sucesso via {protocolo.upper()}",
            executado_por=usuario,
            duracao_segundos=duracao
        )
        
        print(f"\n{'='*80}")
        print(f"✅ BACKUP CONCLUÍDO COM SUCESSO!")
        print(f"{'='*80}\n")
        
        return {
            'sucesso': True,
            'arquivo': nome_arquivo,
            'tamanho': tamanho,
            'duracao': f"{duracao:.2f}s"
        }
        
    except Exception as e:
        erro = f"Erro: {str(e)}"
        print(f"\n❌ {erro}\n")
        registrar_erro_backup(acesso, usuario, erro, time.time() - inicio)
        return {'sucesso': False, 'erro': erro}
        
    finally:
        # ✅ FECHAR CONEXÃO
        if ssh_process:
            try:
                ssh_process.close()
            except:
                pass
        
        # ✅ FECHAR TÚNEL
        if ssh_tunnel:
            try:
                if 'ssh_client' in ssh_tunnel:
                    ssh_tunnel['ssh_client'].close()
                if 'server_socket' in ssh_tunnel:
                    ssh_tunnel['server_socket'].close()
            except:
                pass


def ler_saida_comando(ssh_process, silence_timeout=2.0, max_timeout=120):
    """
    ✅ NOVO: Lê output até detectar silence (parou de chegar dados)
    
    Estratégia:
    1. Lê dados continuamente
    2. Se nenhum dado por 2 segundos = fim do comando
    3. Timeout máximo de 120 segundos
    
    Retorna: string com TUDO que foi lido
    """
    print(f"       🔍 Detectando fim do comando por silence...")
    
    resultado = ""
    tempo_inicio = time.time()
    ultimo_dado = time.time()
    silence_count = 0
    bytes_totais = 0
    
    while True:
        tempo_decorrido = time.time() - tempo_inicio
        
        # ✅ Timeout máximo: 120 segundos
        if tempo_decorrido > max_timeout:
            print(f"       ⚠️ Timeout máximo ({max_timeout}s) atingido")
            break
        
        try:
            # ✅ Tentar ler com timeout muito curto
            dados = ssh_process.read_nonblocking(timeout=0.1, size=65536)
            
            if dados:
                # ✅ Dados chegaram
                resultado += dados
                bytes_totais += len(dados)
                ultimo_dado = time.time()
                silence_count = 0
                print(f"       📥 {len(dados)} bytes ({bytes_totais} total)")
            else:
                # ✅ Nenhum dado, incremen silêncio
                silence_count += 1
                tempo_silencio = time.time() - ultimo_dado
                
                # Se ficou em silêncio por 2 segundos, assume que terminou
                if tempo_silencio >= silence_timeout:
                    print(f"       ✅ Silence detectado ({tempo_silencio:.1f}s) - comando terminou")
                    break
                
                time.sleep(0.1)
        
        except pexpect.exceptions.TIMEOUT:
            # ✅ Timeout normal do read_nonblocking
            tempo_silencio = time.time() - ultimo_dado
            
            if tempo_silencio >= silence_timeout:
                print(f"       ✅ Silence detectado ({tempo_silencio:.1f}s) - comando terminou")
                break
            
            time.sleep(0.1)
        
        except Exception as e:
            print(f"       ⚠️ Erro ao ler: {str(e)}")
            break
    
    print(f"       ✅ Leitura completa: {bytes_totais} bytes, {time.time() - tempo_inicio:.1f}s")
    return resultado



def conectar_ssh_backup(host, porta, usuario, senha, senha_adm, timeout=120):
    """
    ✅ MEGA ULTRA CORRIGIDO v2: Detecta e trata erro de SSH não encontrado
    - Retry automático com fallback para /bin/bash
    - Verifica SSH antes de conectar
    - Logs detalhados para debug
    """
    print(f"📤 SSH: Conectando a {host}:{porta}...")
    
    # ✅ VERIFICAR SSH
    ssh_path = "/usr/bin/ssh"
    if not os.path.exists(ssh_path):
        print(f"⚠️ SSH não encontrado em {ssh_path}")
        print(f"   Tentando via PATH...")
        ssh_path = "ssh"  # Fallback para PATH
    
    ssh_cmd = (
        f"{ssh_path} -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=30 "
        f"-o ServerAliveInterval=60 "
        f"-o LogLevel=ERROR "
        f"-p {porta} {usuario}@{host}"
    )
    
    ssh_process = None
    tentativas = 3
    
    for tentativa in range(tentativas):
        try:
            print(f"   [Tentativa {tentativa + 1}/{tentativas}]")
            
            if tentativa == 0 or tentativa == 1:
                # Primeiras tentativas: direto
                print(f"      Método: Direct spawn")
                ssh_process = pexpect.spawn(
                    ssh_cmd,
                    timeout=timeout,
                    encoding='utf-8',
                    maxread=65536,
                    cwd=os.path.expanduser('~')
                )
            else:
                # Última tentativa: via shell
                print(f"      Método: Shell wrapper")
                ssh_process = pexpect.spawn(
                    '/bin/bash',
                    ['-c', ssh_cmd],
                    timeout=timeout,
                    encoding='utf-8',
                    maxread=65536
                )
            
            print(f"   ✅ Spawn OK!")
            break
            
        except FileNotFoundError as e:
            print(f"   ❌ FileNotFoundError: {str(e)}")
            
            if tentativa < tentativas - 1:
                print(f"      Aguardando 2s antes de retry...")
                time.sleep(2)
                continue
            else:
                # Última tentativa falhou
                raise Exception(
                    f"❌ SSH não encontrado após {tentativas} tentativas\n"
                    f"Execute no servidor: sudo apt-get install openssh-client"
                )
        
        except Exception as e:
            print(f"   ❌ Erro inesperado: {str(e)}")
            raise
    
    # ✅ Resto do código continua igual (PASSO 1, 2, 3, etc)
    try:
        print(f"📤 Aguardando autenticação...")
        
        # PASSO 1
        try:
            index = ssh_process.expect([
                "password:",
                "Password:",
                r".*[#>$\]].*",
            ], timeout=30)
            
            if index == 0 or index == 1:
                print(f"🔐 Enviando senha...")
                ssh_process.sendline(senha)
                time.sleep(1)
                
                try:
                    ssh_process.read_nonblocking(timeout=1.0, size=65536)
                except:
                    pass
        
        except pexpect.exceptions.TIMEOUT:
            raise Exception("❌ Timeout ao autenticar SSH")
        
        # PASSO 2
        print(f"⏳ Aguardando sistema estabilizar (3s)...")
        time.sleep(3)
        
        # PASSO 3
        print(f"🧹 Limpando buffer com CTRL+U...")
        ssh_process.send("\x15")
        time.sleep(0.5)
        
        # PASSO 4
        print(f"🔍 Testando se prompt está respondendo...")
        ssh_process.send("\r")
        time.sleep(0.5)
        
        try:
            ssh_process.expect([
                r".*[\#\>\$\]]\s*$",
                r">",
                r"\$",
                r"\]",
            ], timeout=3)
            print(f"✅ Prompt detectado!")
        except pexpect.exceptions.TIMEOUT:
            print(f"⚠️ Timeout ao detectar, limpando...")
            try:
                ssh_process.read_nonblocking(timeout=0.5, size=65536)
            except:
                pass
        
        # PASSO 5-10 (mesmo código anterior)
        print(f"🔧 Desabilitando paginação...")
        ssh_process.send("terminal length 0\r")
        time.sleep(0.8)
        try:
            ssh_process.read_nonblocking(timeout=1.0, size=65536)
        except:
            pass
        
        print(f"🎨 Desabilitando cores ANSI do MikroTik...")
        ssh_process.send("set colors=never\r")
        time.sleep(0.8)
        try:
            ssh_process.read_nonblocking(timeout=1.0, size=65536)
        except:
            pass
        
        print(f"🧹 Limpando...")
        ssh_process.send("\r")
        time.sleep(1)
        try:
            ssh_process.read_nonblocking(timeout=1.0, size=65536)
        except:
            pass
        
        print(f"🔐 SINCRONIZANDO - Aguardando prompt 100%...")
        for tentativa_sync in range(3):
            print(f"   Tentativa {tentativa_sync + 1}/3...")
            ssh_process.send("\r")
            time.sleep(0.5)
            
            try:
                ssh_process.expect([r".*[\#\>\$\]]\s*$"], timeout=2)
                print(f"   ✅ Prompt respondeu!")
                break
            except pexpect.exceptions.TIMEOUT:
                print(f"   ⚠️ Timeout")
                continue
        
        print(f"⏳ Aguardando final (2s)...")
        time.sleep(2)
        
        print(f"🧹 Limpando buffer final...")
        try:
            while True:
                dados = ssh_process.read_nonblocking(timeout=0.2, size=65536)
                if not dados:
                    break
        except:
            pass
        
        print(f"✅✅✅ SSH: 100% PRONTO! ✅✅✅")
        return ssh_process
        
    except pexpect.exceptions.EOF:
        raise Exception("❌ Conexão SSH encerrada inesperadamente")
    except Exception as e:
        print(f"❌ {str(e)}")
        try:
            ssh_process.close()
        except:
            pass
        raise Exception(f"Erro SSH: {str(e)}")
    
    

def conectar_telnet_backup(host, porta, usuario, senha, timeout=120):
    """
    ✅ MEGA CORRIGIDO: Robustez TOTAL para Telnet também
    """
    print(f"📤 Telnet: Conectando a {host}:{porta}...")
    
    telnet_cmd = f"telnet {host} {porta}"
    
    telnet_process = pexpect.spawn(
        telnet_cmd,
        timeout=timeout,
        encoding='utf-8',
        maxread=65536  # 64KB
    )
    
    try:
        print(f"📤 Aguardando login prompt...")
        
        # ✅ PASSO 1: Aguardar login
        telnet_process.expect([
            "login:",
            "username:",
            "user:",
        ], timeout=15)
        
        print(f"🔐 Enviando usuário...")
        telnet_process.sendline(usuario)
        time.sleep(0.5)
        
        # ✅ PASSO 2: Aguardar senha
        telnet_process.expect([
            "password:",
            "Password:",
        ], timeout=10)
        
        print(f"🔐 Enviando senha...")
        telnet_process.sendline(senha)
        time.sleep(0.5)
        
        # ✅ PASSO 3: Aguardar prompt
        telnet_process.expect([
            r".*[\#\>\$\]]\s*$",
        ], timeout=15)
        
        print(f"✅ Autenticado!")
        
        # ✅ PASSO 4: Estabilizar (3s)
        print(f"⏳ Aguardando estabilizar (3s)...")
        time.sleep(3)
        
        # ✅ PASSO 5: Limpar
        print(f"🧹 Limpando...")
        telnet_process.send("\x15")  # Ctrl+U
        time.sleep(0.5)
        telnet_process.send("\r")
        time.sleep(0.5)
        
        try:
            telnet_process.read_nonblocking(timeout=1.0, size=65536)
        except:
            pass
        
        # ✅ PASSO 6: Sincronizar múltiplas vezes
        print(f"🔐 SINCRONIZANDO...")
        for tentativa in range(3):
            print(f"   Tentativa {tentativa + 1}/3...")
            telnet_process.send("\r")
            time.sleep(0.5)
            
            try:
                telnet_process.expect([r".*[\#\>\$\]]\s*$"], timeout=2)
                print(f"   ✅ Prompt respondeu!")
                break
            except pexpect.exceptions.TIMEOUT:
                print(f"   ⚠️ Timeout")
                continue
        
        # ✅ PASSO 7: Aguardar final
        print(f"⏳ Aguardando final (2s)...")
        time.sleep(2)
        
        # ✅ PASSO 8: Limpar buffer
        print(f"🧹 Limpando buffer...")
        try:
            while True:
                dados = telnet_process.read_nonblocking(timeout=0.2, size=65536)
                if not dados:
                    break
        except:
            pass
        
        print(f"✅✅✅ Telnet: 100% PRONTO! ✅✅✅")
        return telnet_process
        
    except pexpect.exceptions.TIMEOUT:
        raise Exception("❌ Timeout ao autenticar Telnet")
    except pexpect.exceptions.EOF:
        raise Exception("❌ Conexão Telnet encerrada")
    except Exception as e:
        print(f"❌ {str(e)}")
        try:
            telnet_process.close()
        except:
            pass
        raise Exception(f"Erro Telnet: {str(e)}")

    

               
def detectar_protocolo(porta):
    """Detecta protocolo pela porta"""
    porta_int = int(porta)
    
    if porta_int == 22:
        return 'ssh'
    elif porta_int == 23:
        return 'telnet'
    elif porta_int in [2222, 8022, 10022, 9022]:
        return 'ssh'
    elif porta_int in [2323, 9023]:
        return 'telnet'
    else:
        return 'ssh'



def preparar_diretorio_backup(cliente_id, acesso_id):
    """
    Cria estrutura de diretórios para backups
    """
    base_dir = os.path.join(settings.MEDIA_ROOT, 'backups')
    cliente_dir = os.path.join(base_dir, f'cliente_{cliente_id}')
    acesso_dir = os.path.join(cliente_dir, f'acesso_{acesso_id}')
    
    os.makedirs(acesso_dir, exist_ok=True)
    
    return acesso_dir


def mapear_device_type(modelo_nome):
    """
    Mapeia modelo do equipamento para device_type do Netmiko
    """
    modelo_lower = modelo_nome.lower()
    
    if 'cisco' in modelo_lower:
        if 'ios-xe' in modelo_lower or 'catalyst' in modelo_lower:
            return 'cisco_ios'
        elif 'nexus' in modelo_lower:
            return 'cisco_nxos'
        elif 'asa' in modelo_lower:
            return 'cisco_asa'
        else:
            return 'cisco_ios'
    
    elif 'huawei' in modelo_lower:
        return 'huawei'
    
    elif 'mikrotik' in modelo_lower:
        return 'mikrotik_routeros'
    
    elif 'juniper' in modelo_lower:
        return 'juniper_junos'
    
    elif 'dell' in modelo_lower:
        return 'dell_os10'
    
    elif 'hp' in modelo_lower or 'aruba' in modelo_lower:
        return 'hp_procurve'
    
    elif 'extreme' in modelo_lower:
        return 'extreme'
    
    else:
        return 'cisco_ios'  # Fallback


def is_private_ip(ip):
    """
    Verifica se IP é privado
    Intervalos privados:
    - 10.0.0.0/8
    - 172.16.0.0/12
    - 192.168.0.0/16
    """
    import ipaddress
    try:
        return ipaddress.ip_address(ip).is_private
    except:
        return False




def criar_ssh_tunnel(proxy_server, equipamento_host, equipamento_porta, timeout=10):
    """
    ✅ MESMA FUNÇÃO DO TERMINAL SSH
    Cria túnel com socket forwarding
    """
    print(f"🔧 Criando túnel SSH...")
    
    try:
        # Conectar ao proxy
        print(f"📤 Conectando ao proxy...")
        ssh_proxy = paramiko.SSHClient()
        ssh_proxy.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        ssh_proxy.connect(
            hostname=proxy_server['host'],
            port=int(proxy_server['porta']),
            username=proxy_server['usuario'],
            password=proxy_server['senha'],
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False
        )
        
        print(f"✅ Conectado ao proxy!")
        
        # Encontrar porta local
        sock_temp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock_temp.bind(('127.0.0.1', 0))
        local_port = sock_temp.getsockname()[1]
        sock_temp.close()
        
        print(f"📍 Porta local: {local_port}")
        
        # Criar servidor
        print(f"🔗 Iniciando servidor de forwarding...")
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('127.0.0.1', local_port))
        server_socket.listen(5)
        server_socket.settimeout(1)
        
        print(f"✅ Servidor escutando em 127.0.0.1:{local_port}")
        
        # Função de forwarding
        def forward_tunnel(client_socket, remote_host, remote_port, transport):
            """Forwarda dados via tunnel"""
            try:
                channel = transport.open_channel(
                    'direct-tcpip',
                    (remote_host, int(remote_port)),
                    ('127.0.0.1', local_port)
                )
                
                def forward_data(src, dst, direction):
                    """Forwards data"""
                    try:
                        while True:
                            data = src.recv(4096)
                            if not data:
                                break
                            dst.send(data)
                    except:
                        pass
                    finally:
                        try:
                            src.close()
                        except:
                            pass
                        try:
                            dst.close()
                        except:
                            pass
                
                t1 = threading.Thread(
                    target=forward_data, 
                    args=(client_socket, channel, "C→R")
                )
                t2 = threading.Thread(
                    target=forward_data, 
                    args=(channel, client_socket, "R→C")
                )
                t1.daemon = True
                t2.daemon = True
                t1.start()
                t2.start()
                
            except Exception as e:
                try:
                    client_socket.close()
                except:
                    pass
        
        # Thread de aceitação
        def accept_connections(server_socket, transport, remote_host, remote_port):
            """Accepts connections"""
            try:
                while True:
                    try:
                        client_socket, addr = server_socket.accept()
                        thread = threading.Thread(
                            target=forward_tunnel,
                            args=(client_socket, remote_host, remote_port, transport)
                        )
                        thread.daemon = True
                        thread.start()
                    except socket.timeout:
                        continue
                    except:
                        break
            except:
                pass
            finally:
                try:
                    server_socket.close()
                except:
                    pass
        
        # Iniciar thread
        transport = ssh_proxy.get_transport()
        accept_thread = threading.Thread(
            target=accept_connections,
            args=(server_socket, transport, equipamento_host, equipamento_porta)
        )
        accept_thread.daemon = True
        accept_thread.start()
        
        print(f"✅ Túnel criado!")
        time.sleep(0.5)
        
        return {
            'tunnel': None,
            'ssh_client': ssh_proxy,
            'local_host': '127.0.0.1',
            'local_port': local_port,
            'server_socket': server_socket,
            'channel': None,
            'transport': transport,
            'accept_thread': accept_thread
        }
        
    except Exception as e:
        print(f"❌ Erro: {str(e)}")
        raise


def registrar_erro_backup(acesso, usuario, erro, duracao):
    """Registra erro no log"""
    BackupLog.objects.create(
        acesso=acesso,
        cliente=acesso.cliente,
        template=acesso.backup_template,
        arquivo_path='',
        tamanho_bytes=0,
        status='ERRO',
        mensagem=erro,
        executado_por=usuario,
        duracao_segundos=duracao
    )

@login_required(login_url='login')
def listar_backups_cliente(request):
    """
    Lista backups de um cliente (AJAX)
    ✅ CORRIGIDO: Verifica se arquivo existe antes de exibir
    """
    cliente_id = request.GET.get('id')
    
    if not cliente_id:
        return JsonResponse({'error': 'Cliente não especificado'}, status=400)
    
    # Verificar permissão
    if not request.user.is_staff and not request.user.is_superuser:
        try:
            cliente = Cliente.objects.get(usuario=request.user)
            if str(cliente.id) != str(cliente_id):
                return JsonResponse({'error': 'Sem permissão'}, status=403)
        except Cliente.DoesNotExist:
            return JsonResponse({'error': 'Sem permissão'}, status=403)
    
    cliente = get_object_or_404(Cliente, id=cliente_id)
    
    # Buscar backups
    backups = BackupLog.objects.filter(cliente=cliente).select_related(
        'acesso', 'template', 'executado_por'
    ).order_by('-data_backup')
    
    # ✅ PASSO 1: Verificar quais arquivos existem e quais são órfãos
    backups_validos = []
    backups_para_deletar = []
    
    for backup in backups:
        arquivo_path = os.path.join(settings.MEDIA_ROOT, backup.arquivo_path)
        if os.path.exists(arquivo_path):
            backups_validos.append(backup)
        else:
            # Arquivo foi deletado manualmente da VM
            backups_para_deletar.append(backup.id)
            print(f"⚠️ Backup órfão: {backup.arquivo_path}")
    
    # ✅ PASSO 2: Remover registros órfãos do banco
    if backups_para_deletar:
        BackupLog.objects.filter(id__in=backups_para_deletar).delete()
        print(f"✅ {len(backups_para_deletar)} registro(s) órfão(s) removido(s)")
    
    # ✅ PASSO 3: Retornar apenas backups válidos
    return JsonResponse({
        'backups': [{
            'id': backup.id,
            'acesso_tipo': backup.acesso.tipo,
            'acesso_host': backup.acesso.host,
            'template': backup.template.nome if backup.template else 'N/A',
            'status': backup.get_status_display(),
            'status_code': backup.status,
            'tamanho': backup.get_tamanho_formatado(),
            'data': backup.data_backup.strftime('%d/%m/%Y %H:%M:%S'),
            'duracao': f"{backup.duracao_segundos:.2f}s",
            'executado_por': backup.executado_por.username if backup.executado_por else 'Sistema',
            'mensagem': backup.mensagem or '',
            'arquivo_path': backup.arquivo_path
        } for backup in backups_validos]
    })


@login_required(login_url='login')
def download_backup(request, backup_id):
    """Download de backup"""
    try:
        backup = BackupLog.objects.get(id=backup_id)
        
        # Verificar permissão
        if not request.user.is_staff and not request.user.is_superuser:
            cliente = Cliente.objects.get(usuario=request.user)
            if backup.cliente.id != cliente.id:
                messages.error(request, 'Sem permissão')
                return redirect('listar_clientes')
        
        arquivo_path = os.path.join(settings.MEDIA_ROOT, backup.arquivo_path)
        
        if not os.path.exists(arquivo_path):
            messages.error(request, 'Arquivo não encontrado')
            return redirect('listar_clientes')
        
        return FileResponse(
            open(arquivo_path, 'rb'),
            as_attachment=True,
            filename=os.path.basename(arquivo_path)
        )
        
    except BackupLog.DoesNotExist:
        messages.error(request, 'Backup não encontrado')
        return redirect('listar_clientes')
    except Exception as e:
        messages.error(request, f'Erro: {str(e)}')
        return redirect('listar_clientes')

@login_required(login_url='login')
@admin_required
def deletar_backup(request, backup_id):
    """Deleta backup"""
    if request.method == 'POST':
        try:
            backup = get_object_or_404(BackupLog, id=backup_id)
            cliente_id = backup.cliente.id
            
            # Deletar arquivo
            arquivo_path = os.path.join(settings.MEDIA_ROOT, backup.arquivo_path)
            if os.path.exists(arquivo_path):
                os.remove(arquivo_path)
            
            # Deletar registro
            backup.delete()
            
            messages.success(request, 'Backup excluído!')
            return redirect(reverse('listar_clientes') + f'?id={cliente_id}')
            
        except Exception as e:
            messages.error(request, f'Erro: {str(e)}')
            return redirect('listar_clientes')
    
    return redirect('listar_clientes')


@login_required(login_url='login')
def buscar_templates_backup(request):
    """Busca templates de backup"""
    templates = BackupTemplate.objects.filter(ativo=True).order_by('fabricante', 'nome')
    
    return JsonResponse({
        'templates': [{
            'id': t.id,
            'nome': t.nome,
            'fabricante': t.get_fabricante_display(),
            'descricao': t.descricao or ''
        } for t in templates]
    })

@require_http_methods(["GET"])
def terminal_page(request):
    """Renderiza a página de terminal SSH múltiplo"""
    return render(request, 'terminal.html')