from django.urls import path
from . import views



urlpatterns = [
    path('cadastrar', views.cadastrar_funcao, name='cadastrar_funcao'),
    path('listar', views.listar_funcoes, name='listar_funcoes'),
    path('funcao/editar/', views.editar_funcao, name='editar_funcao'),
]
