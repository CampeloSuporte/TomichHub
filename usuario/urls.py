from django.urls import path
from . import views



urlpatterns = [
    path('cadastrar_usuario/', views.cadastrar_usuario, name='cadastrar_usuario'),
    path("login/", views.login, name="login"),
    path('editar-usuario/', views.editar_usuario, name='editar_usuario'),
    path('logout/', views.logout, name='logout'),
    path('trocar-senha/', views.trocar_senha, name='trocar_senha'),
]