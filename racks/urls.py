from django.urls import path

from . import views

app_name = 'racks'

urlpatterns = [
    path('cliente/<int:cliente_id>/', views.tela, name='tela'),
    path('cliente/<int:cliente_id>/estado/', views.api_estado, name='estado'),
    path('cliente/<int:cliente_id>/link/', views.api_link, name='link'),
    path('cliente/<int:cliente_id>/posicoes/', views.api_posicoes, name='posicoes'),
    path('cliente/<int:cliente_id>/racks/criar/', views.api_rack_criar, name='rack_criar'),
    path('cliente/<int:cliente_id>/conexoes/criar/', views.api_conexao_criar, name='conexao_criar'),
    path('cliente/<int:cliente_id>/conexoes/do-link/', views.api_conexao_do_link, name='conexao_do_link'),
    path('rack/<int:rack_id>/editar/', views.api_rack_editar, name='rack_editar'),
    path('rack/<int:rack_id>/excluir/', views.api_rack_excluir, name='rack_excluir'),
    path('rack/<int:rack_id>/equipamentos/criar/', views.api_equipamento_criar, name='equipamento_criar'),
    path('equipamento/<int:equipamento_id>/editar/', views.api_equipamento_editar, name='equipamento_editar'),
    path('equipamento/<int:equipamento_id>/excluir/', views.api_equipamento_excluir, name='equipamento_excluir'),
    path('conexao/<int:conexao_id>/editar/', views.api_conexao_editar, name='conexao_editar'),
    path('conexao/<int:conexao_id>/excluir/', views.api_conexao_excluir, name='conexao_excluir'),
]
