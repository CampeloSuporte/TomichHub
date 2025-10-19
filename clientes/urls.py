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
]
