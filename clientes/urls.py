from django.urls import path, re_path
from . import views
from .documentacao_views import (
    salvar_doc_config,
    buscar_doc_config,
    proxy_documentacao,
    proxy_ativo_cliente,
)
from . import ipam_views as ipam
from . import firmware_views as fw
from . import script_views as sv

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
    path('backups/conteudo/<int:backup_id>/', views.backup_conteudo, name='backup_conteudo'),
    path('backups/download/<int:backup_id>/', views.download_backup, name='download_backup'),
    path('backups/deletar/<int:backup_id>/', views.deletar_backup, name='deletar_backup'),
    path('backups/templates/', views.buscar_templates_backup, name='buscar_templates_backup'),

    # Terminal e Winbox
    path('terminal/', views.terminal_page, name='terminal_page'),
    path('terminal/acessos/', views.listar_acessos_terminal, name='listar_acessos_terminal'),
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

    # ── VPN WireGuard ────────────────────────────────────────────────────
    path('<int:cliente_id>/vpn-wg/listar/',          views.vpn_wg_listar,        name='vpn_wg_listar'),
    path('<int:cliente_id>/vpn-wg/criar/',           views.vpn_wg_criar,         name='vpn_wg_criar'),
    path('<int:cliente_id>/vpn-wg/status/',          views.vpn_wg_status,        name='vpn_wg_status'),
    path('vpn-wg/<int:vpn_id>/script/',              views.vpn_wg_script,        name='vpn_wg_script'),
    path('vpn-wg/<int:vpn_id>/deletar/',             views.vpn_wg_deletar,       name='vpn_wg_deletar'),
    path('vpn-wg/<int:vpn_id>/reativar/',            views.vpn_wg_reativar_peer, name='vpn_wg_reativar'),
    path('vpn-wg/<int:vpn_id>/editar/',              views.vpn_wg_editar,        name='vpn_wg_editar'),

    # ── IPAM Nativo ──────────────────────────────────────────────────────
    path('<int:cliente_id>/ipam/vlans/',                 ipam.ipam_vlans_listar,    name='ipam_vlans_listar'),
    path('<int:cliente_id>/ipam/vlans/salvar/',          ipam.ipam_vlan_salvar,     name='ipam_vlan_salvar'),
    path('ipam/vlans/<int:vlan_id>/deletar/',            ipam.ipam_vlan_deletar,    name='ipam_vlan_deletar'),
    path('<int:cliente_id>/ipam/prefixos/',              ipam.ipam_prefixos_listar, name='ipam_prefixos_listar'),
    path('<int:cliente_id>/ipam/prefixos/salvar/',       ipam.ipam_prefixo_salvar,  name='ipam_prefixo_salvar'),
    path('ipam/prefixos/<int:prefixo_id>/deletar/',      ipam.ipam_prefixo_deletar,    name='ipam_prefixo_deletar'),
    path('ipam/prefixos/<int:prefixo_id>/breakdown/',    ipam.ipam_prefixo_breakdown,  name='ipam_prefixo_breakdown'),
    path('ipam/prefixos/<int:prefixo_id>/dividir/',        ipam.ipam_prefixo_dividir,       name='ipam_prefixo_dividir'),
    path('ipam/prefixos/<int:prefixo_id>/marcar-em-uso/', ipam.ipam_prefixo_marcar_em_uso,  name='ipam_prefixo_marcar_em_uso'),
    path('ipam/prefixos/<int:prefixo_id>/pool-cheia/',   ipam.ipam_prefixo_pool_cheia,      name='ipam_prefixo_pool_cheia'),
    path('ipam/subredes/<int:subrede_id>/dividir/',        ipam.ipam_subrede_dividir,        name='ipam_subrede_dividir'),
    path('<int:cliente_id>/ipam/subredes/',              ipam.ipam_subredes_listar, name='ipam_subredes_listar'),
    path('<int:cliente_id>/ipam/subredes/salvar/',       ipam.ipam_subrede_salvar,  name='ipam_subrede_salvar'),
    path('ipam/subredes/<int:subrede_id>/deletar/',      ipam.ipam_subrede_deletar,   name='ipam_subrede_deletar'),
    path('ipam/subredes/<int:subrede_id>/pool-cheia/',   ipam.ipam_subrede_pool_cheia, name='ipam_subrede_pool_cheia'),
    path('ipam/subredes/<int:subrede_id>/ips/',          ipam.ipam_subrede_ips,     name='ipam_subrede_ips'),
    path('<int:cliente_id>/ipam/ips/',                   ipam.ipam_ips_listar,      name='ipam_ips_listar'),
    path('<int:cliente_id>/ipam/ips/salvar/',            ipam.ipam_ip_salvar,       name='ipam_ip_salvar'),
    path('ipam/ips/<int:ip_id>/deletar/',                ipam.ipam_ip_deletar,      name='ipam_ip_deletar'),
    path('<int:cliente_id>/ipam/vpns/',                  ipam.ipam_vpns_listar,     name='ipam_vpns_listar'),
    path('<int:cliente_id>/ipam/vpns/salvar/',           ipam.ipam_vpn_salvar,      name='ipam_vpn_salvar'),
    path('ipam/vpns/<int:vpn_id>/deletar/',              ipam.ipam_vpn_deletar,     name='ipam_vpn_deletar'),
    path('<int:cliente_id>/ipam/importar/',              ipam.ipam_importar,        name='ipam_importar'),
    path('<int:cliente_id>/ipam/analisar-backups/',      ipam.ipam_analisar_backups, name='ipam_analisar_backups'),

    # ── OpenVPN ──────────────────────────────────────────────────────────
    path('<int:cliente_id>/openvpn/listar/',          views.openvpn_listar,      name='openvpn_listar'),
    path('<int:cliente_id>/openvpn/criar/',           views.openvpn_criar,       name='openvpn_criar'),
    path('openvpn/<int:config_id>/status/',           views.openvpn_status,      name='openvpn_status'),
    path('openvpn/<int:config_id>/download/',         views.openvpn_download,    name='openvpn_download'),
    path('openvpn/<int:config_id>/deletar/',          views.openvpn_deletar,     name='openvpn_deletar'),
    path('openvpn/<int:config_id>/logs/',             views.openvpn_logs,        name='openvpn_logs'),
    path('openvpn/<int:config_id>/reexecutar/',       views.openvpn_reexecutar,      name='openvpn_reexecutar'),
    path('openvpn/<int:config_id>/usuario/criar/',    views.openvpn_usuario_criar,    name='openvpn_usuario_criar'),
    path('openvpn/usuario/<int:usuario_id>/status/',  views.openvpn_usuario_status,   name='openvpn_usuario_status'),
    path('openvpn/usuario/<int:usuario_id>/download/',views.openvpn_usuario_download, name='openvpn_usuario_download'),
    path('openvpn/usuario/<int:usuario_id>/deletar/', views.openvpn_usuario_deletar,  name='openvpn_usuario_deletar'),

    # ── IRR Config ───────────────────────────────────────────────────────
    path('<int:cliente_id>/irr/config/',       views.irr_config_get,       name='irr_config_get'),
    path('<int:cliente_id>/irr/salvar/',       views.irr_config_salvar,    name='irr_config_salvar'),
    path('<int:cliente_id>/irr/preview/',      views.irr_preview,          name='irr_preview'),
    path('<int:cliente_id>/irr/enviar/',       views.irr_enviar,           name='irr_enviar'),
    path('<int:cliente_id>/irr/consultar/',    views.irr_consultar_whois,   name='irr_consultar_whois'),
    path('<int:cliente_id>/irr/verificar/',    views.irr_verificar_resposta, name='irr_verificar_resposta'),

    # ── Firmware / Gerenciador de Arquivos ───────────────────────────────
    path('firmware/',                                       fw.firmware_index,            name='firmware_index'),
    path('firmware/listar/',                                fw.firmware_listar,           name='firmware_listar'),
    path('firmware/pasta/criar/',                           fw.firmware_criar_pasta,      name='firmware_criar_pasta'),
    path('firmware/upload/',                                fw.firmware_upload,           name='firmware_upload'),
    path('firmware/upload-url/',                            fw.firmware_upload_url,             name='firmware_upload_url'),
    path('firmware/upload-url/progresso/<str:task_id>/',    fw.firmware_upload_url_progresso,   name='firmware_upload_url_progresso'),
    path('firmware/arquivo/<int:arquivo_id>/deletar/',      fw.firmware_deletar_arquivo,  name='firmware_deletar_arquivo'),
    path('firmware/pasta/<int:pasta_id>/deletar/',          fw.firmware_deletar_pasta,    name='firmware_deletar_pasta'),
    path('firmware/arquivo/<int:arquivo_id>/compartilhar/', fw.firmware_compartilhar,     name='firmware_compartilhar'),
    path('firmware/arquivo/<int:arquivo_id>/links/',        fw.firmware_links_ativos,     name='firmware_links_ativos'),
    path('firmware/link/<int:comp_id>/revogar/',            fw.firmware_revogar_link,     name='firmware_revogar_link'),

    # ── Editor de Topologia de Rede ──────────────────────────────────────
    path('<int:cliente_id>/senhas/pdf/',        views.exportar_senhas_pdf, name='exportar_senhas_pdf'),
    path('<int:cliente_id>/topologia/editor/', views.topologia_editor, name='topologia_editor'),
    path('<int:cliente_id>/topologia/drawio/', views.topologia_drawio, name='topologia_drawio'),
    path('<int:cliente_id>/topologia/dados/', views.topologia_dados, name='topologia_dados'),
    path('<int:cliente_id>/topologia/salvar/', views.topologia_salvar, name='topologia_salvar'),
    path('<int:cliente_id>/topologia/hosts/', views.topologia_hosts, name='topologia_hosts'),

    # ── Trocar Senhas em Massa ───────────────────────────────────────────────
    path('trocar-senhas/hosts/', views.trocar_senha_massa_listar_hosts, name='trocar_senha_massa_listar_hosts'),
    path('trocar-senhas/',                              views.trocar_senha_massa,           name='trocar_senha_massa'),
    path('trocar-senhas/iniciar/',                      views.trocar_senha_massa_iniciar,   name='trocar_senha_massa_iniciar'),
    path('trocar-senhas/<int:job_id>/status/',          views.trocar_senha_massa_status,    name='trocar_senha_massa_status'),
    path('trocar-senhas/<int:job_id>/remover-antigos/', views.trocar_senha_remover_antigos, name='trocar_senha_remover_antigos'),

    # ── Scripts de Automação ─────────────────────────────────────────────────
    path('scripts/',                                sv.listar_scripts,      name='listar_scripts'),
    path('scripts/gerenciar/',                      sv.gerenciar_scripts,   name='gerenciar_scripts'),
    path('scripts/salvar/',                         sv.salvar_script,       name='salvar_script'),
    path('scripts/salvar/<int:script_id>/',         sv.salvar_script,       name='salvar_script_edit'),
    path('scripts/deletar/<int:script_id>/',        sv.deletar_script,      name='deletar_script'),
    path('scripts/executar/',                       sv.executar_script,     name='executar_script'),
    path('scripts/historico/<int:acesso_id>/',      sv.historico_execucoes, name='historico_scripts'),
    path('scripts/<int:script_id>/',                sv.detalhe_script,      name='detalhe_script'),
]