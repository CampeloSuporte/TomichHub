from django.urls import path
from . import views
from clientes.firmware_views import firmware_download



urlpatterns = [
    path('geral', views.quadro_geral, name='quadro_geral'),
    path('relatorio/backups/', views.relatorio_backups, name='relatorio_backups'),
    path('chamados/<str:status>/', views.listar_chamados_por_status, name='listar_chamados_status'),
    path('configuracoes/', views.configuracoes_sistema, name='configuracoes_sistema'),
    path('configuracoes/smtp-testar/', views.smtp_testar, name='smtp_testar'),
    path('configuracoes/backup/', views.backup_config, name='backup_config'),
    path('configuracoes/backup/template/salvar/', views.backup_template_salvar, name='backup_template_salvar'),
    path('configuracoes/backup/template/<int:template_id>/deletar/', views.backup_template_deletar, name='backup_template_deletar'),
    path('configuracoes/backup/acesso/<int:acesso_id>/', views.backup_acesso_config, name='backup_acesso_config'),
    path('ferramentas/lg/', views.lg_pesquisa, name='lg_pesquisa'),
    path('ferramentas/lg/buscar/', views.lg_pesquisa_buscar, name='lg_pesquisa_buscar'),
    path('ferramentas/lg/asn/', views.lg_asn_info, name='lg_asn_info'),
    path('ferramentas/geo/', views.geo_consulta, name='geo_consulta'),
    path('ferramentas/geo/buscar/', views.geo_consulta_buscar, name='geo_consulta_buscar'),
    path('ferramentas/geo/atualizar/', views.geo_atualizar, name='geo_atualizar'),
    path('ferramentas/geo/historico/', views.geo_historico, name='geo_historico'),
    path('ferramentas/geo/<int:correcao_id>/resposta/', views.geo_verificar_resposta, name='geo_verificar_resposta'),
    path('ferramentas/geo/<int:correcao_id>/aplicacao/', views.geo_verificar_aplicacao, name='geo_verificar_aplicacao'),
    path('ferramentas/geo/<int:correcao_id>/confirmar-maxmind/', views.geo_confirmar_maxmind, name='geo_confirmar_maxmind'),
    # Download público de firmware via token (sem login)
    path('ferramentas/firmware/dl/<str:token>/<path:nome_arquivo>', firmware_download, name='firmware_download'),
]



