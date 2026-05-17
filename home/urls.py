from django.urls import path
from . import views
from clientes.firmware_views import firmware_download



urlpatterns = [
    path('geral', views.quadro_geral, name='quadro_geral'),
    path('chamados/<str:status>/', views.listar_chamados_por_status, name='listar_chamados_status'),
    path('configuracoes/', views.configuracoes_sistema, name='configuracoes_sistema'),
    path('configuracoes/smtp-testar/', views.smtp_testar, name='smtp_testar'),
    path('ferramentas/lg/', views.lg_pesquisa, name='lg_pesquisa'),
    path('ferramentas/lg/buscar/', views.lg_pesquisa_buscar, name='lg_pesquisa_buscar'),
    # Download público de firmware via token (sem login)
    path('ferramentas/firmware/dl/<str:token>/<path:nome_arquivo>', firmware_download, name='firmware_download'),
]



