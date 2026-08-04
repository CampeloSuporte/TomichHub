from django.urls import path
from . import views

urlpatterns = [
    path('criar/', views.tarefa_criar, name='tarefa_criar'),
    path('<int:tarefa_id>/editar/', views.tarefa_editar, name='tarefa_editar'),
    path('<int:tarefa_id>/assumir/', views.tarefa_assumir, name='tarefa_assumir'),
    path('<int:tarefa_id>/status/', views.tarefa_status, name='tarefa_status'),
    path('<int:tarefa_id>/usuarios/', views.tarefa_usuarios_json, name='tarefa_usuarios_json'),
]
