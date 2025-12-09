from django.urls import path
from . import views


urlpatterns = [
    # Views renderizadas
    path('', views.dashboard_financeiro, name='dashboard_financeiro'),
    
    # APIs - Dashboard
    path('api/dashboard/', views.api_dashboard_financeiro, name='api_dashboard_financeiro'),
    path('api/fatura/<int:fatura_id>/', views.api_visualizar_fatura, name='api_visualizar_fatura'),
    path('api/pesquisar-clientes/', views.api_pesquisar_clientes, name='api_pesquisar_clientes'),
    path('api/fatura/<int:fatura_id>/visualizar/', views.api_visualizar_fatura, name='api_visualizar_fatura'),
    
    # APIs - Consultorias
    path('api/consultoria/criar/', views.api_criar_consultoria, name='api_criar_consultoria'),
    path('api/consultoria/listar/', views.api_listar_consultorias, name='api_listar_consultorias'),
    
    # APIs - Alugueis IPv4
    path('api/aluguel/criar/', views.api_criar_aluguel_ipv4, name='api_criar_aluguel_ipv4'),
    path('api/aluguel/listar/', views.api_listar_alugueis, name='api_listar_alugueis'),
    
    # APIs - Faturas
    path('api/fatura/criar/', views.api_criar_fatura, name='api_criar_fatura'),
    path('api/fatura/listar/', views.api_listar_faturas, name='api_listar_faturas'),
    path('api/pagamento/registrar/', views.api_registrar_pagamento, name='api_registrar_pagamento'),
    path('api/pagamento/<int:fatura_id>/listar/', views.api_listar_pagamentos, name='api_listar_pagamentos'),
    path('api/pagamento/<int:pagamento_id>/deletar/', views.api_deletar_pagamento, name='api_deletar_pagamento'),
    path('api/faturamento-por-mes/', views.api_faturamento_por_mes, name='api_faturamento_por_mes'),
    path('api/venda/', views.api_criar_venda_equipamento, name='api_criar_venda_equipamento'),
    path('api/vendas/', views.api_listar_vendas_equipamentos, name='api_listar_vendas_equipamentos'),
]