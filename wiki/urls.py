from django.urls import path
from . import views

app_name = 'wiki'

urlpatterns = [
    # Dashboard/Listagem
    path('', views.dashboard_wiki, name='dashboard'),
    
    # CRUD Artigos
    path('artigo/novo/', views.cadastrar_artigo, name='cadastrar_artigo'),
    path('artigo/<slug:slug>/', views.visualizar_artigo, name='visualizar_artigo'),
    path('artigo/<slug:slug>/editar/', views.editar_artigo, name='editar_artigo'),
    path('artigo/<slug:slug>/deletar/', views.deletar_artigo, name='deletar_artigo'),
    
    # Blocos de Código
    path('artigo/<slug:slug>/codigo/novo/', views.adicionar_bloco_codigo, name='adicionar_bloco_codigo'),
    path('codigo/<int:codigo_id>/editar/', views.editar_bloco_codigo, name='editar_bloco_codigo'),
    path('codigo/<int:codigo_id>/deletar/', views.deletar_bloco_codigo, name='deletar_bloco_codigo'),
    
    # Busca e Filtros
    path('buscar/', views.buscar_wiki, name='buscar'),
    path('tag/<slug:slug>/', views.listar_por_tag, name='tag'),
    path('fabricante/<str:fabricante>/', views.listar_por_fabricante, name='fabricante'),
    
    # API para Terminal (AJAX)
    path('api/buscar/', views.api_buscar_wiki, name='api_buscar'),
    path('api/artigo/<slug:slug>/', views.api_visualizar_artigo, name='api_visualizar'),
    path('categoria/nova/', views.cadastrar_categoria_ajax, name='cadastrar_categoria_ajax'),
    path('categoria/<slug:slug>/', views.listar_por_categoria, name='categoria'),
]