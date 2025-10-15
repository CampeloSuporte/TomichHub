from django.urls import path
from . import views

urlpatterns = [
    
    path('listar/', views.listar_clientes, name='listar_clientes'),
    path('cadastrar/', views.cadastrar_cliente, name='cadastrar_cliente'),
    path('cadastrar_acesso/', views.cadastrar_acesso, name='cadastrar_acesso'),
    path('editar_cliente/', views.editar_cliente, name='editar_cliente'),
    
]
