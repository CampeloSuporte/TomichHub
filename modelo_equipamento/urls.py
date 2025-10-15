from django.urls import path
from . import views



urlpatterns = [
    path('cadastrar', views.cadastrar_modelo, name='cadastrar_modelo'),
    path('listar', views.listar_modelos, name='listar_modelos'),
    path('editar_modelo/', views.editar_modelo, name='editar_modelo'),
    path('deletar_modelo/', views.deletar_modelo, name='deletar_modelo'),
]
