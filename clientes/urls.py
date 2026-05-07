from django.urls import path, re_path
from . import views
from .documentacao_views import (
    salvar_doc_config,
    buscar_doc_config,
    proxy_documentacao,
    proxy_ativo_cliente,
)

urlpatterns = [
    path('dashboard/', views.cliente_dashboard, name='cliente_dashboard'),
    path('listar/', views.listar_clientes, name='listar_clientes'),
    path('cadastrar/', views.cadastrar_cliente, name='cadastrar_cliente'),
    path('cadastrar_acesso/', views.cadastrar_acesso, name='cadastrar_acesso'),
    path('editar-cliente/', views.editar_cliente, name='editar_cliente'),
    path('deletar-cliente/', views.deletar_cliente, name='deletar_cliente'),
    path('acessos/buscar/<int:acesso_id>/', views.buscar_acesso, name='buscar_acesso'),
    path('acessos/editar/<int:acesso_id>/', views.editar_acesso, name='editar_acesso'),
    path('acessos/deletar/<int:acesso_id>/', views.deletar_acesso, name='deletar_acesso'),
    path('upload_documento/', views.upload_documento, name='upload_documento'),
    path('deletar_documento/<int:documento_id>/', views.deletar_documento, name='deletar_documento'),

    # URLs VPN
    path('vpn/upload/', views.upload_vpn, name='upload_vpn'),
    path('vpn/deletar/<int:vpn_id>/', views.deletar_vpn, name='deletar_vpn'),
    path('vpn/buscar/<int:vpn_id>/', views.buscar_vpn, name='buscar_vpn'),
    path('vpn/editar/<int:vpn_id>/', views.editar_vpn, name='editar_vpn'),

    # URLs Topologia
    path('topologia/upload/', views.upload_topologia, name='upload_topologia'),
    path('topologia/deletar/<int:topologia_id>/', views.deletar_topologia, name='deletar_topologia'),
    path('topologias/editar-imagem/<int:topologia_id>/', views.editar_imagem_topologia, name='editar_imagem_topologia'),

    # Categorias
    path('categorias/cadastrar/', views.cadastrar_categoria, name='cadastrar_categoria'),
    path('categorias/buscar/', views.buscar_categorias, name='buscar_categorias'),

    # Chamados
    path('chamados/listar/', views.listar_chamados_cliente, name='listar_chamados_cliente'),
    path('chamados/cadastrar/', views.cadastrar_chamado, name='cadastrar_chamado'),
    path('chamados/buscar/<int:chamado_id>/', views.buscar_chamado, name='buscar_chamado'),
    path('chamados/editar/<int:chamado_id>/', views.editar_chamado, name='editar_chamado'),
    path('chamados/deletar/<int:chamado_id>/', views.deletar_chamado, name='deletar_chamado'),
    path('chamados/<int:chamado_id>/comentario/', views.adicionar_comentario, name='adicionar_comentario'),

    # Buscas
    path('usuarios/buscar/', views.buscar_usuarios, name='buscar_usuarios'),
    path('clientes/buscar-chamado/', views.buscar_clientes_chamado, name='buscar_clientes_chamado'),

    # Túneis SSH (Proxies)
    path('proxies/cadastrar/', views.cadastrar_proxy, name='cadastrar_proxy'),
    path('proxies/buscar/<int:proxy_id>/', views.buscar_proxy, name='buscar_proxy'),
    path('proxies/editar/<int:proxy_id>/', views.editar_proxy, name='editar_proxy'),
    path('proxies/deletar/<int:proxy_id>/', views.deletar_proxy, name='deletar_proxy'),
    path('proxies/testar/<int:proxy_id>/', views.testar_proxy, name='testar_proxy'),
    path('proxies/toggle/<int:proxy_id>/', views.toggle_proxy_status, name='toggle_proxy_status'),
    path('proxies/ativo/', proxy_ativo_cliente, name='proxy_ativo_cliente'),

    # Backups
    path('backups/executar/<int:acesso_id>/', views.executar_backup_acesso, name='executar_backup'),
    path('backups/listar/', views.listar_backups_cliente, name='listar_backups'),
    path('backups/download/<int:backup_id>/', views.download_backup, name='download_backup'),
    path('backups/deletar/<int:backup_id>/', views.deletar_backup, name='deletar_backup'),
    path('backups/templates/', views.buscar_templates_backup, name='buscar_templates_backup'),

    # Terminal e Winbox
    path('terminal/', views.terminal_page, name='terminal_page'),
    path('winbox/<int:acesso_id>/', views.winbox_page, name='winbox_page'),
    path('webfig-vnc/<int:acesso_id>/', views.webfig_vnc_page, name='webfig_vnc_page'),


    # Ping
    path('acessos/ping/<int:acesso_id>/', views.ping_acesso, name='ping_acesso'),
    path('<int:cliente_id>/testes/rede/', views.teste_rede_cliente, name='teste_rede_cliente'),
    path('<int:cliente_id>/testes/dns/', views.teste_dns_cliente, name='teste_dns_cliente'),

    # RPKI/IRR
    path('blocos/cadastrar/', views.cadastrar_bloco_ip, name='cadastrar_bloco_ip'),
    path('blocos/buscar/<int:bloco_id>/', views.buscar_bloco_ip, name='buscar_bloco_ip'),
    path('blocos/editar/<int:bloco_id>/', views.editar_bloco_ip, name='editar_bloco_ip'),
    path('blocos/deletar/<int:bloco_id>/', views.deletar_bloco_ip, name='deletar_bloco_ip'),
    path('blocos/validar/<int:bloco_id>/', views.validar_bloco_rpki_irr, name='validar_bloco_rpki_irr'),
    path('blocos/listar/', views.listar_blocos_cliente, name='listar_blocos_cliente'),

    # Comentários de Acesso
    path('acessos/<int:acesso_id>/comentarios/listar/', views.listar_comentarios_acesso, name='listar_comentarios_acesso'),
    path('acessos/<int:acesso_id>/comentarios/adicionar/', views.adicionar_comentario_acesso, name='adicionar_comentario_acesso'),
    path('comentarios/<int:comentario_id>/deletar/', views.deletar_comentario_acesso, name='deletar_comentario_acesso'),
    path('comentarios/<int:comentario_id>/editar/', views.editar_comentario_acesso, name='editar_comentario_acesso'),

    # Documentação de Rede (PHP IPAM / NetBox)
    path('doc/config/salvar/', salvar_doc_config, name='salvar_doc_config'),
    path('doc/config/buscar/<int:cliente_id>/', buscar_doc_config, name='buscar_doc_config'),
    path('doc/proxy/<int:cliente_id>/<str:tipo>/', proxy_documentacao, {'path': ''}, name='proxy_doc_root'),
    path('doc/proxy/<int:cliente_id>/<str:tipo>/<path:path>', proxy_documentacao, name='proxy_doc'),
    re_path(r'^acessos/(?P<acesso_id>[0-9]+)/web/(?P<porta>[0-9]+)/(?P<scheme>https?)(?P<path>/.*)?$', views.proxy_web_acesso, name='proxy_web_acesso'),
    re_path(r'^acessos/(?P<acesso_id>[0-9]+)/web/?$', views.proxy_web_acesso, name='proxy_web_acesso_legacy'),
]