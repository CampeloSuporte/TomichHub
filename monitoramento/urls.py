"""
monitoramento/urls.py

Registre no urls.py PRINCIPAL do projeto:
    path('monitoramento/', include('monitoramento.urls', namespace='monitoramento')),
"""
from django.urls import path
from . import views

app_name = 'monitoramento'

urlpatterns = [
    # ── Configuração Zabbix ──────────────────────────────────
    path('zabbix/salvar/',      views.salvar_zabbix_config,    name='zabbix_salvar'),
    path('zabbix/buscar/',      views.buscar_zabbix_config,    name='zabbix_buscar'),
    path('zabbix/testar/',      views.testar_zabbix_conexao,   name='zabbix_testar'),
    path('zabbix/autoconfig/',  views.autoconfig_zabbix,       name='zabbix_autoconfig'),

    # ── Dados do Zabbix ──────────────────────────────────────
    path('zabbix/hosts/',       views.listar_hosts_zabbix,     name='zabbix_hosts'),
    path('zabbix/interfaces/',  views.listar_interfaces_zabbix, name='zabbix_interfaces'),
    path('zabbix/historico/',   views.historico_item_zabbix,   name='zabbix_historico'),

    # ── Topologias (CRUD) ────────────────────────────────────
    path('topologias/listar/',  views.listar_topologias,       name='topologias_listar'),
    path('topologias/criar/',   views.criar_topologia,         name='topologias_criar'),
    path('topologias/salvar/',  views.salvar_topologia,        name='topologias_salvar'),
    path('topologias/carregar/', views.carregar_topologia,     name='topologias_carregar'),
    path('topologias/deletar/<int:topo_id>/', views.deletar_topologia, name='topologias_deletar'),

    # ── Status em tempo real ─────────────────────────────────
    path('status/',             views.status_topologia,        name='status'),
    path('zabbix/history/', views.historico_item_zabbix, name='zbx_history'),
    path('zabbix/itens/', views.listar_itens_zabbix, name='listar_itens_zabbix'),
]
