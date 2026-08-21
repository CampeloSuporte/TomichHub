from django.urls import path
from . import views

urlpatterns = [
    path('criar/', views.tarefa_criar, name='tarefa_criar'),
    path('<int:tarefa_id>/editar/', views.tarefa_editar, name='tarefa_editar'),
    path('<int:tarefa_id>/assumir/', views.tarefa_assumir, name='tarefa_assumir'),
    path('<int:tarefa_id>/status/', views.tarefa_status, name='tarefa_status'),
    path('<int:tarefa_id>/excluir/', views.tarefa_excluir, name='tarefa_excluir'),
    path('<int:tarefa_id>/usuarios/', views.tarefa_usuarios_json, name='tarefa_usuarios_json'),

    # Kanban (aba "Tarefas" na página do cliente — clientes/templates/listar.html)
    path('kanban/<int:cliente_id>/', views.tarefas_kanban_json, name='tarefas_kanban_json'),
    path('kanban/<int:cliente_id>/criar/', views.tarefa_kanban_criar, name='tarefa_kanban_criar'),
    path('kanban/mover/<int:tarefa_id>/', views.tarefa_kanban_mover, name='tarefa_kanban_mover'),
    path('kanban/editar/<int:tarefa_id>/', views.tarefa_kanban_editar, name='tarefa_kanban_editar'),
    path('kanban/excluir/<int:tarefa_id>/', views.tarefa_kanban_excluir, name='tarefa_kanban_excluir'),
]
