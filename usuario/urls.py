from django.urls import path
from . import views



urlpatterns = [
    path('cadastrar_usuario/', views.cadastrar_usuario, name='cadastrar_usuario'),
    path("login/", views.login, name="login"),
    path('editar-usuario/', views.editar_usuario, name='editar_usuario'),
    path('excluir-usuario/', views.deletar_usuario, name='deletar_usuario'),
    path('logout/', views.logout, name='logout'),
    path('trocar-senha/', views.trocar_senha, name='trocar_senha'),
    path('2fa/', views.configurar_2fa, name='configurar_2fa'),
    path('2fa/verificar/', views.verificar_2fa, name='verificar_2fa'),
    path('2fa/resetar/', views.resetar_2fa_admin, name='resetar_2fa_admin'),
]