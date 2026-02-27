from django.urls import path
from . import views

urlpatterns = [

    # ===== VIEWS RENDERIZADAS =====
    path('', views.dashboard_financeiro, name='dashboard_financeiro'),

    # ===== APIS: DASHBOARD =====
    path('api/dashboard/', views.api_dashboard_financeiro, name='api_dashboard_financeiro'),
    path('api/faturamento-por-mes/', views.api_faturamento_por_mes, name='api_faturamento_por_mes'),
    path('api/pesquisar-clientes/', views.api_pesquisar_clientes, name='api_pesquisar_clientes'),
    path('api/resumo-cliente/', views.api_resumo_cliente, name='api_resumo_cliente'),

    # ===== APIS: RELATÓRIOS =====
    path('api/aging/', views.api_aging_report, name='api_aging_report'),
    path('api/top-clientes/', views.api_top_clientes, name='api_top_clientes'),

    # ===== APIS: FATURAS =====
    # ⚠️ IMPORTANTE: rotas estáticas (criar, listar) ANTES das dinâmicas (<int:fatura_id>)
    path('api/fatura/criar/', views.api_criar_fatura, name='api_criar_fatura'),
    path('api/fatura/listar/', views.api_listar_faturas, name='api_listar_faturas'),
    path('api/fatura/<int:fatura_id>/', views.api_visualizar_fatura, name='api_visualizar_fatura'),
    path('api/fatura/<int:fatura_id>/deletar/', views.api_deletar_fatura, name='api_deletar_fatura'),

    # ===== APIS: CONSULTORIAS =====
    path('api/consultoria/criar/', views.api_criar_consultoria, name='api_criar_consultoria'),
    path('api/consultoria/listar/', views.api_listar_consultorias, name='api_listar_consultorias'),
    path('api/consultoria/<int:consultoria_id>/editar/', views.api_editar_consultoria, name='api_editar_consultoria'),
    path('api/consultoria/<int:consultoria_id>/deletar/', views.api_deletar_consultoria, name='api_deletar_consultoria'),

    # ===== APIS: ALUGUEIS IPv4 =====
    path('api/aluguel/criar/', views.api_criar_aluguel_ipv4, name='api_criar_aluguel_ipv4'),
    path('api/aluguel/listar/', views.api_listar_alugueis, name='api_listar_alugueis'),
    path('api/aluguel/<int:aluguel_id>/editar/', views.api_editar_aluguel, name='api_editar_aluguel'),
    path('api/aluguel/<int:aluguel_id>/deletar/', views.api_deletar_aluguel, name='api_deletar_aluguel'),

    # ===== APIS: VENDAS DE EQUIPAMENTO =====
    path('api/venda/', views.api_criar_venda_equipamento, name='api_criar_venda_equipamento'),
    path('api/vendas/', views.api_listar_vendas_equipamentos, name='api_listar_vendas_equipamentos'),
    path('api/venda/<int:venda_id>/editar/', views.api_editar_venda, name='api_editar_venda'),
    path('api/venda/<int:venda_id>/deletar/', views.api_deletar_venda, name='api_deletar_venda'),

    # ===== APIS: PAGAMENTOS =====
    path('api/pagamento/registrar/', views.api_registrar_pagamento, name='api_registrar_pagamento'),
    path('api/pagamento/<int:fatura_id>/listar/', views.api_listar_pagamentos, name='api_listar_pagamentos'),
    path('api/pagamento/<int:pagamento_id>/deletar/', views.api_deletar_pagamento, name='api_deletar_pagamento'),
    path('api/clientes-pagaram-vencidos/', views.api_clientes_pagaram_vencidos, name='api_clientes_pagaram_vencidos'),

]