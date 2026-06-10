from django.urls import path
from . import views

urlpatterns = [

    # ===== VIEWS RENDERIZADAS =====
    path('', views.dashboard_financeiro, name='dashboard_financeiro'),
    path('despesas/', views.listar_despesas_page, name='listar_despesas_page'),

    # ===== APIS: DASHBOARD =====
    path('api/dashboard/', views.api_dashboard_financeiro, name='api_dashboard_financeiro'),
    path('api/faturamento-por-mes/', views.api_faturamento_por_mes, name='api_faturamento_por_mes'),
    path('api/pesquisar-clientes/', views.api_pesquisar_clientes, name='api_pesquisar_clientes'),
    path('api/resumo-cliente/', views.api_resumo_cliente, name='api_resumo_cliente'),

    # ===== APIS: RELATÓRIOS =====
    path('api/aging/', views.api_aging_report, name='api_aging_report'),
    path('api/top-clientes/', views.api_top_clientes, name='api_top_clientes'),
    path('api/proximas-vencer/', views.api_proximas_vencer, name='api_proximas_vencer'),

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
    path('api/aluguel/<int:aluguel_id>/contrato/',           views.gerar_contrato_aluguel,      name='gerar_contrato_aluguel'),
    path('api/aluguel/<int:aluguel_id>/link-assinatura/',   views.gerar_link_assinatura,       name='gerar_link_assinatura'),
    path('api/aluguel/<int:aluguel_id>/contratos/',         views.listar_contratos_aluguel,    name='listar_contratos_aluguel'),
    # Públicas (sem login)
    path('contrato/<uuid:token>/assinar/',                  views.assinar_contrato,            name='assinar_contrato'),
    path('contrato/<uuid:token>/confirmar/',                views.confirmar_assinatura,        name='confirmar_assinatura'),
    path('contrato/<uuid:token>/download/',                 views.download_contrato_assinado,  name='download_contrato_assinado'),

    # ===== APIS: VENDAS DE EQUIPAMENTO =====
    path('api/venda/', views.api_criar_venda_equipamento, name='api_criar_venda_equipamento'),
    path('api/vendas/', views.api_listar_vendas_equipamentos, name='api_listar_vendas_equipamentos'),
    path('api/venda/<int:venda_id>/editar/', views.api_editar_venda, name='api_editar_venda'),
    path('api/venda/<int:venda_id>/deletar/', views.api_deletar_venda, name='api_deletar_venda'),

    # ===== APIS: PAGAMENTOS =====
    path('api/fatura/ajustar-vencimento/', views.api_ajustar_vencimento, name='api_ajustar_vencimento'),
    path('api/pagamento/registrar/', views.api_registrar_pagamento, name='api_registrar_pagamento'),
    path('api/pagamento/<int:fatura_id>/listar/', views.api_listar_pagamentos, name='api_listar_pagamentos'),
    path('api/pagamento/<int:pagamento_id>/deletar/', views.api_deletar_pagamento, name='api_deletar_pagamento'),
    path('api/clientes-pagaram-vencidos/', views.api_clientes_pagaram_vencidos, name='api_clientes_pagaram_vencidos'),

    # ===== APIS: DESPESAS =====
    path('api/despesa/criar/',                    views.api_criar_despesa,   name='api_criar_despesa'),
    path('api/despesa/listar/',                   views.api_listar_despesas, name='api_listar_despesas'),
    path('api/despesa/<int:despesa_id>/editar/',  views.api_editar_despesa,  name='api_editar_despesa'),
    path('api/despesa/<int:despesa_id>/pagar/',   views.api_pagar_despesa,   name='api_pagar_despesa'),
    path('api/despesa/<int:despesa_id>/deletar/', views.api_deletar_despesa, name='api_deletar_despesa'),
    path('api/despesas/bulk/', views.api_despesas_bulk, name='api_despesas_bulk'),
    path('api/despesas/dashboard/',               views.api_despesas_dashboard, name='api_despesas_dashboard'),

    # ===== ASSINATURA DO LOCADOR =====
    path('api/assinatura-locador/', views.assinatura_locador, name='assinatura_locador'),

    # ===== RELATÓRIO: CLIENTES COM ALUGUEL ATIVO =====
    path('api/clientes-aluguel-ativo/', views.api_clientes_aluguel_ativo, name='api_clientes_aluguel_ativo'),
]