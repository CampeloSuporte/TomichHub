from django.urls import path
from . import views

urlpatterns = [
    
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
    path('vpn/buscar/<int:vpn_id>/', views.buscar_vpn, name='buscar_vpn'),  # ✅ NOVA
    path('vpn/editar/<int:vpn_id>/', views.editar_vpn, name='editar_vpn'),  # ✅ NOVA
    
    # URLs Topologia
    path('topologia/upload/', views.upload_topologia, name='upload_topologia'),
    path('topologia/deletar/<int:topologia_id>/', views.deletar_topologia, name='deletar_topologia'),

    # ... suas URLs existentes ...
    
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


    # URLs para Servidores Proxy
    path('proxies/listar/', views.listar_proxies, name='listar_proxies'),
    path('proxies/cadastrar/', views.cadastrar_proxy, name='cadastrar_proxy'),
    path('proxies/buscar/<int:proxy_id>/', views.buscar_proxy, name='buscar_proxy'),
    path('proxies/editar/<int:proxy_id>/', views.editar_proxy, name='editar_proxy'),
    path('proxies/deletar/<int:proxy_id>/', views.deletar_proxy, name='deletar_proxy'),
    path('proxies/testar/<int:proxy_id>/', views.testar_proxy, name='testar_proxy'),
    path('proxies/toggle/<int:proxy_id>/', views.toggle_proxy_status, name='toggle_proxy_status'),
    
    
]
