from django.urls import path
from . import views



urlpatterns = [
    path('geral', views.quadro_geral, name='quadro_geral'),
    path('chamados/<str:status>/', views.listar_chamados_por_status, name='listar_chamados_status'),
    path('configuracoes/', views.configuracoes_sistema, name='configuracoes_sistema'),
    path('configuracoes/smtp-testar/', views.smtp_testar, name='smtp_testar'),
]



