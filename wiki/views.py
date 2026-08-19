from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Count
from django.db import IntegrityError
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods
import markdown
from .models import ArtigoWiki, CategoriaWiki, TagWiki
from clientes.decorators import admin_required, ferramenta_instancia_required, wiki_edicao_required

# ============================================
# VIEWS PRINCIPAIS
# ============================================

@login_required(login_url='login')
@ferramenta_instancia_required('wiki')
def dashboard_wiki(request):
    """Dashboard principal da Wiki"""
    categorias = CategoriaWiki.objects.all()
    favoritos = ArtigoWiki.objects.filter(favorito=True, ativo=True)[:5]
    recentes = ArtigoWiki.objects.filter(ativo=True).order_by('-data_atualizacao')[:10]
    
    return render(request, 'wiki/dashboard.html', {
        'categorias': categorias,
        'favoritos': favoritos,
        'recentes': recentes,
    })


@login_required(login_url='login')
@ferramenta_instancia_required('wiki')
def visualizar_artigo(request, slug):
    """Visualiza um artigo específico"""
    artigo = get_object_or_404(ArtigoWiki, slug=slug, ativo=True)
    artigo.incrementar_visualizacao()
    
    # Converter Markdown para HTML
    conteudo_html = markdown.markdown(
        artigo.conteudo,
        extensions=['fenced_code', 'codehilite', 'tables', 'toc']
    )
    
    return render(request, 'wiki/visualizar_artigo.html', {
        'artigo': artigo,
        'conteudo_html': conteudo_html,
    })


@login_required(login_url='login')
@wiki_edicao_required
def cadastrar_artigo(request):
    """Cadastra novo artigo"""
    if request.method == 'POST':
        titulo = request.POST.get('titulo')
        categoria_id = request.POST.get('categoria')
        fabricante = request.POST.get('fabricante')
        modelo_especifico = request.POST.get('modelo_especifico', '')
        descricao_curta = request.POST.get('descricao_curta')
        conteudo = request.POST.get('conteudo')
        favorito = request.POST.get('favorito') == 'on'
        destaque = request.POST.get('destaque') == 'on'
        
        artigo = ArtigoWiki.objects.create(
            titulo=titulo,
            categoria_id=categoria_id,
            fabricante=fabricante,
            modelo_especifico=modelo_especifico,
            descricao_curta=descricao_curta,
            conteudo=conteudo,
            favorito=favorito,
            destaque=destaque,
            criado_por=request.user,
            atualizado_por=request.user
        )

        pdf_file = request.FILES.get('pdf')
        if pdf_file:
            if pdf_file.name.lower().endswith('.pdf'):
                artigo.pdf = pdf_file
                artigo.save(update_fields=['pdf'])
            else:
                messages.warning(request, 'O arquivo enviado não é um PDF e foi ignorado.')

        # Adicionar tags
        tags_str = request.POST.get('tags', '')
        if tags_str:
            tags_list = [t.strip() for t in tags_str.split(',') if t.strip()]
            for tag_nome in tags_list:
                tag, _ = TagWiki.objects.get_or_create(nome=tag_nome)
                artigo.tags.add(tag)
        
        messages.success(request, f'Artigo "{titulo}" criado com sucesso!')
        return redirect('wiki:visualizar_artigo', slug=artigo.slug)
    
    categorias = CategoriaWiki.objects.all()
    return render(request, 'wiki/cadastrar_artigo.html', {
        'categorias': categorias,
        'fabricantes': ArtigoWiki.FABRICANTES,
    })


@login_required(login_url='login')
@wiki_edicao_required
def editar_artigo(request, slug):
    """Edita um artigo existente"""
    artigo = get_object_or_404(ArtigoWiki, slug=slug)
    
    if request.method == 'POST':
        artigo.titulo = request.POST.get('titulo')
        artigo.categoria_id = request.POST.get('categoria')
        artigo.fabricante = request.POST.get('fabricante')
        artigo.modelo_especifico = request.POST.get('modelo_especifico', '')
        artigo.descricao_curta = request.POST.get('descricao_curta')
        artigo.conteudo = request.POST.get('conteudo')
        artigo.favorito = request.POST.get('favorito') == 'on'
        artigo.destaque = request.POST.get('destaque') == 'on'
        artigo.atualizado_por = request.user

        if request.POST.get('remover_pdf') == '1' and artigo.pdf:
            artigo.pdf.delete(save=False)
            artigo.pdf = None

        pdf_file = request.FILES.get('pdf')
        if pdf_file:
            if pdf_file.name.lower().endswith('.pdf'):
                if artigo.pdf:
                    artigo.pdf.delete(save=False)
                artigo.pdf = pdf_file
            else:
                messages.warning(request, 'O arquivo enviado não é um PDF e foi ignorado.')

        artigo.save()

        # Atualizar tags
        artigo.tags.clear()
        tags_str = request.POST.get('tags', '')
        if tags_str:
            tags_list = [t.strip() for t in tags_str.split(',') if t.strip()]
            for tag_nome in tags_list:
                tag, _ = TagWiki.objects.get_or_create(nome=tag_nome)
                artigo.tags.add(tag)
        
        messages.success(request, f'Artigo "{artigo.titulo}" atualizado!')
        return redirect('wiki:visualizar_artigo', slug=artigo.slug)
    
    categorias = CategoriaWiki.objects.all()
    tags_atuais = ','.join([t.nome for t in artigo.tags.all()])
    
    return render(request, 'wiki/editar_artigo.html', {
        'artigo': artigo,
        'categorias': categorias,
        'fabricantes': ArtigoWiki.FABRICANTES,
        'tags_atuais': tags_atuais,
    })


@login_required(login_url='login')
@admin_required
@require_http_methods(["POST"])
def deletar_artigo(request, slug):
    """Deleta um artigo"""
    artigo = get_object_or_404(ArtigoWiki, slug=slug)
    titulo = artigo.titulo
    artigo.delete()
    
    messages.success(request, f'Artigo "{titulo}" deletado com sucesso!')
    return redirect('wiki:dashboard')


# ============================================
# BUSCA E FILTROS
# ============================================

@login_required(login_url='login')
@ferramenta_instancia_required('wiki')
def buscar_wiki(request):
    """Busca artigos"""
    query = request.GET.get('q', '')
    fabricante = request.GET.get('fabricante', '')
    categoria_id = request.GET.get('categoria', '')
    
    artigos = ArtigoWiki.objects.filter(ativo=True)
    
    if query:
        artigos = artigos.filter(
            Q(titulo__icontains=query) |
            Q(descricao_curta__icontains=query) |
            Q(conteudo__icontains=query) |
            Q(tags__nome__icontains=query)
        ).distinct()
    
    if fabricante:
        artigos = artigos.filter(fabricante=fabricante)
    
    if categoria_id:
        artigos = artigos.filter(categoria_id=categoria_id)
    
    categorias = CategoriaWiki.objects.all()

    return render(request, 'wiki/buscar.html', {
        'artigos': artigos,
        'query': query,
        'fabricante': fabricante,
        'categoria_id': categoria_id,
        'categorias': categorias,
        'fabricantes': ArtigoWiki.FABRICANTES,
    })


@login_required(login_url='login')
@ferramenta_instancia_required('wiki')
def listar_por_categoria(request, slug):
    """Lista artigos por categoria — com filtro adicional por fabricante
    dentro da própria categoria (ex.: categoria "BGP" + fabricante
    "MikroTik"). Os fabricantes oferecidos como filtro são só os que
    realmente têm artigo nesta categoria, não a lista fixa inteira."""
    categoria = get_object_or_404(CategoriaWiki, slug=slug)
    artigos_categoria = ArtigoWiki.objects.filter(categoria=categoria, ativo=True)

    fabricante_labels = dict(ArtigoWiki.FABRICANTES)
    contagem_por_fabricante = (
        artigos_categoria.values('fabricante')
        .annotate(total=Count('id'))
        .order_by('fabricante')
    )
    fabricantes_disponiveis = [
        {
            'valor': item['fabricante'],
            'label': fabricante_labels.get(item['fabricante'], item['fabricante']),
            'total': item['total'],
        }
        for item in contagem_por_fabricante
    ]

    fabricante_selecionado = request.GET.get('fabricante', '').strip()
    artigos = artigos_categoria
    if fabricante_selecionado:
        artigos = artigos.filter(fabricante=fabricante_selecionado)

    if fabricante_selecionado:
        nome_fabricante = fabricante_labels.get(fabricante_selecionado, fabricante_selecionado)
        empty_msg = f'Nenhum artigo de {nome_fabricante} nesta categoria ainda.'
    else:
        empty_msg = 'Nenhum artigo encontrado nesta categoria ainda.'

    return render(request, 'wiki/listar_artigos.html', {
        'artigos': artigos,
        'page_title': categoria.nome,
        'page_icon': categoria.icone,
        'empty_msg': empty_msg,
        'categoria_atual': categoria,
        'fabricantes_disponiveis': fabricantes_disponiveis,
        'fabricante_selecionado': fabricante_selecionado,
        'total_categoria': artigos_categoria.count(),
    })


@login_required(login_url='login')
@ferramenta_instancia_required('wiki')
def listar_por_tag(request, slug):
    """Lista artigos por tag"""
    tag = get_object_or_404(TagWiki, slug=slug)
    artigos = tag.artigos.filter(ativo=True)

    return render(request, 'wiki/listar_artigos.html', {
        'artigos': artigos,
        'page_title': f'Tag: {tag.nome}',
        'page_icon': 'fa-tag',
        'empty_msg': 'Nenhum artigo encontrado com esta tag ainda.',
    })


@login_required(login_url='login')
@ferramenta_instancia_required('wiki')
def listar_por_fabricante(request, fabricante):
    """Lista artigos por fabricante"""
    artigos = ArtigoWiki.objects.filter(fabricante=fabricante, ativo=True)

    fabricante_nome = dict(ArtigoWiki.FABRICANTES).get(fabricante, fabricante)

    return render(request, 'wiki/listar_artigos.html', {
        'artigos': artigos,
        'page_title': f'Fabricante: {fabricante_nome}',
        'page_icon': 'fa-microchip',
        'empty_msg': 'Nenhum artigo encontrado para este fabricante ainda.',
    })


# ============================================
# API PARA TERMINAL (AJAX)
# ============================================

@login_required(login_url='login')
@ferramenta_instancia_required('wiki')
def api_buscar_wiki(request):
    """API de busca para o terminal"""
    query = request.GET.get('q', '')
    fabricante = request.GET.get('fabricante', '')
    categoria_id = request.GET.get('categoria', '')

    artigos = ArtigoWiki.objects.filter(ativo=True)

    if query:
        artigos = artigos.filter(
            Q(titulo__icontains=query) |
            Q(descricao_curta__icontains=query) |
            Q(conteudo__icontains=query) |
            Q(tags__nome__icontains=query)
        ).distinct()

    if fabricante:
        artigos = artigos.filter(fabricante=fabricante)

    if categoria_id:
        artigos = artigos.filter(categoria_id=categoria_id)

    artigos = artigos[:20]

    return JsonResponse({
        'artigos': [{
            'id': a.id,
            'titulo': a.titulo,
            'slug': a.slug,
            'categoria': a.categoria.nome,
            'categoria_icone': a.categoria.icone,
            'fabricante': a.get_fabricante_display(),
            'descricao': a.descricao_curta,
            'favorito': a.favorito,
            'destaque': a.destaque,
            'pdf': bool(a.pdf),
        } for a in artigos]
    })


@login_required(login_url='login')
@ferramenta_instancia_required('wiki')
def api_visualizar_artigo(request, slug):
    """API para visualizar artigo no terminal"""
    artigo = get_object_or_404(ArtigoWiki, slug=slug, ativo=True)
    artigo.incrementar_visualizacao()
    
    conteudo_html = markdown.markdown(
        artigo.conteudo,
        extensions=['fenced_code', 'codehilite', 'tables', 'toc']
    )
    
    return JsonResponse({
        'id': artigo.id,
        'titulo': artigo.titulo,
        'categoria': artigo.categoria.nome,
        'fabricante': artigo.get_fabricante_display(),
        'modelo': artigo.modelo_especifico,
        'conteudo_html': conteudo_html,
        'pdf_url': artigo.pdf.url if artigo.pdf else None,
        'pdf_nome': artigo.pdf_nome,
        'tags': [t.nome for t in artigo.tags.all()],
        'blocos_codigo': [{
            'id': b.id,
            'titulo': b.titulo,
            'linguagem': b.linguagem,
            'codigo': b.codigo,
            'descricao': b.descricao,
        } for b in artigo.blocos_codigo.all()],
        'data_atualizacao': artigo.data_atualizacao.strftime('%d/%m/%Y %H:%M'),
    })

# ============================================
# CADASTRO RÁPIDO DE CATEGORIA (AJAX)
# ============================================

@login_required(login_url='login')
@wiki_edicao_required
@require_http_methods(["POST"])
def cadastrar_categoria_ajax(request):
    """Cadastra categoria via AJAX"""
    nome = request.POST.get('nome', '').strip()
    icone = request.POST.get('icone', 'fa-folder').strip()
    
    if not nome:
        return JsonResponse({'error': 'Nome da categoria é obrigatório'}, status=400)

    # Verificar se já existe (por nome ou pelo slug que o nome geraria)
    if CategoriaWiki.objects.filter(nome__iexact=nome).exists() or \
       CategoriaWiki.objects.filter(slug=slugify(nome)).exists():
        return JsonResponse({'error': 'Categoria já existe'}, status=400)

    # Criar categoria
    try:
        categoria = CategoriaWiki.objects.create(
            nome=nome,
            icone=icone,
            ordem=CategoriaWiki.objects.count() + 1
        )
    except IntegrityError:
        return JsonResponse({'error': 'Categoria já existe'}, status=400)

    return JsonResponse({
        'success': True,
        'id': categoria.id,
        'nome': categoria.nome,
        'icone': categoria.icone
    })