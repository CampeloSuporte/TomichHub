from django.urls import path
from . import views

urlpatterns = [
    path('criar/', views.tarefa_criar, name='tarefa_criar'),
    path('<int:tarefa_id>/editar/', views.tarefa_editar, name='tarefa_editar'),
    path('<int:tarefa_id>/assumir/', views.tarefa_assumir, name='tarefa_assumir'),
    path('<int:tarefa_id>/status/', views.tarefa_status, name='tarefa_status'),
    path('<int:tarefa_id>/excluir/', views.tarefa_excluir, name='tarefa_excluir'),
    path('<int:tarefa_id>/usuarios/', views.tarefa_usuarios_json, name='tarefa_usuarios_json'),

    # Checklist (dashboard e Kanban) — JSON
    path('checklist/<int:item_id>/marcar/', views.checklist_item_marcar, name='tarefa_checklist_marcar'),
    path('checklist/<int:item_id>/remover/', views.checklist_item_remover, name='tarefa_checklist_remover'),
    path('checklist/tarefa/<int:tarefa_id>/adicionar/', views.checklist_item_adicionar, name='tarefa_checklist_adicionar'),
    path('kanban/<int:cliente_id>/rotinas/criar/', views.rotina_kanban_criar, name='rotina_kanban_criar'),

    # Rotinas mensais (seção "Rotinas mensais" do painel do dashboard)
    path('rotinas/criar/', views.rotina_criar, name='rotina_criar'),
    path('rotinas/<int:rotina_id>/editar/', views.rotina_editar, name='rotina_editar'),
    path('rotinas/<int:rotina_id>/ativar/', views.rotina_ativar, name='rotina_ativar'),
    path('rotinas/<int:rotina_id>/excluir/', views.rotina_excluir, name='rotina_excluir'),
    path('rotinas/<int:rotina_id>/usuarios/', views.rotina_usuarios_json, name='rotina_usuarios_json'),

    # Kanban (aba "Tarefas" na página do cliente — clientes/templates/listar.html)
    path('kanban/<int:cliente_id>/', views.tarefas_kanban_json, name='tarefas_kanban_json'),
    path('kanban/<int:cliente_id>/criar/', views.tarefa_kanban_criar, name='tarefa_kanban_criar'),
    path('kanban/mover/<int:tarefa_id>/', views.tarefa_kanban_mover, name='tarefa_kanban_mover'),
    path('kanban/editar/<int:tarefa_id>/', views.tarefa_kanban_editar, name='tarefa_kanban_editar'),
    path('kanban/excluir/<int:tarefa_id>/', views.tarefa_kanban_excluir, name='tarefa_kanban_excluir'),
]
