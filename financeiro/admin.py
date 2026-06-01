from django.contrib import admin
from .models import (
    ConfiguracaoFinanceira, Consultoria, AluguelIPv4, Fatura,
    Boleto, Pagamento, RelatorioFinanceiro, VendaEquipamento, Despesa
)


@admin.register(ConfiguracaoFinanceira)
class ConfiguracaoFinanceiraAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Empresa', {
            'fields': ('empresa_nome', 'empresa_cnpj', 'empresa_logo')
        }),
        ('Banco', {
            'fields': ('banco_nome', 'banco_codigo', 'agencia', 'conta', 'digito_conta', 'nosso_numero_sequencia')
        }),
        ('Contato', {
            'fields': ('endereco', 'telefone', 'email')
        }),
        ('Configurações de Atraso', {
            'fields': ('juros_atraso_percentual', 'multa_atraso')
        }),
    )
    readonly_fields = ('data_criacao', 'data_atualizacao')


@admin.register(Consultoria)
class ConsultoriaAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'descricao', 'valor_unitario', 'quantidade_meses', 'get_valor_total', 'status', 'quitado')
    list_filter = ('status', 'quitado', 'data_criacao')
    search_fields = ('cliente__nome_empresa', 'descricao')
    readonly_fields = ('data_criacao', 'data_atualizacao')
    
    fieldsets = (
        ('Dados Básicos', {
            'fields': ('cliente', 'descricao', 'valor_unitario', 'quantidade_meses', 'periodicidade')
        }),
        ('Datas', {
            'fields': ('data_inicio', 'data_fim')
        }),
        ('Status', {
            'fields': ('status', 'quitado', 'data_quitacao')
        }),
        ('Sistema', {
            'fields': ('data_criacao', 'data_atualizacao'),
            'classes': ('collapse',)
        }),
    )


@admin.register(AluguelIPv4)
class AluguelIPv4Admin(admin.ModelAdmin):
    list_display = ('cliente', 'bloco_descricao', 'quantidade_ips', 'valor_mensal', 'status', 'quitado')
    list_filter = ('status', 'quitado', 'data_criacao')
    search_fields = ('cliente__nome_empresa', 'bloco_descricao')
    readonly_fields = ('data_criacao', 'data_atualizacao')
    
    fieldsets = (
        ('Dados Básicos', {
            'fields': ('cliente', 'bloco_ip', 'bloco_descricao', 'quantidade_ips', 'valor_mensal')
        }),
        ('Datas', {
            'fields': ('data_inicio', 'data_fim')
        }),
        ('Status', {
            'fields': ('status', 'quitado', 'data_quitacao')
        }),
        ('Sistema', {
            'fields': ('data_criacao', 'data_atualizacao'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Fatura)
class FaturaAdmin(admin.ModelAdmin):
    list_display = ('numero_fatura', 'cliente', 'tipo', 'valor_total', 'status', 'data_vencimento', 'privada')
    list_filter = ('status', 'tipo', 'privada', 'data_emissao')
    search_fields = ('numero_fatura', 'cliente__nome_empresa')
    readonly_fields = ('numero_fatura', 'uuid', 'data_criacao', 'data_atualizacao')

    fieldsets = (
        ('Informações Gerais', {
            'fields': ('numero_fatura', 'uuid', 'cliente', 'tipo', 'status')
        }),
        ('Itens', {
            'fields': ('consultorias', 'alugueis_ipv4')
        }),
        ('Valores', {
            'fields': ('subtotal', 'desconto', 'juros', 'valor_total')
        }),
        ('Datas', {
            'fields': ('data_emissao', 'data_vencimento', 'data_pagamento')
        }),
        ('Privacidade', {
            'fields': ('privada',)
        }),
        ('Observações', {
            'fields': ('observacoes',),
            'classes': ('collapse',)
        }),
        ('Sistema', {
            'fields': ('data_criacao', 'data_atualizacao'),
            'classes': ('collapse',)
        }),
    )

    filter_horizontal = ('consultorias', 'alugueis_ipv4')


@admin.register(Boleto)
class BoletoAdmin(admin.ModelAdmin):
    list_display = ('numero_boleto', 'cliente', 'valor', 'data_vencimento', 'status')
    list_filter = ('status', 'tipo', 'data_geracao')
    search_fields = ('numero_boleto', 'cliente__nome_empresa', 'nosso_numero')
    readonly_fields = ('uuid', 'numero_boleto', 'data_geracao')
    
    fieldsets = (
        ('Informações do Boleto', {
            'fields': ('uuid', 'numero_boleto', 'nosso_numero', 'cliente', 'fatura', 'tipo')
        }),
        ('Valores e Datas', {
            'fields': ('valor', 'data_vencimento', 'data_pagamento')
        }),
        ('Status e Rastreamento', {
            'fields': ('status', 'codigo_barras', 'url_banco')
        }),
        ('Arquivo', {
            'fields': ('arquivo_pdf',),
            'classes': ('collapse',)
        }),
    )


@admin.register(Pagamento)
class PagamentoAdmin(admin.ModelAdmin):
    list_display = ('numero_recibo', 'fatura', 'tipo', 'valor', 'data_pagamento')
    list_filter = ('tipo', 'data_pagamento')
    search_fields = ('numero_recibo', 'fatura__numero_fatura')
    readonly_fields = ('uuid', 'numero_recibo', 'data_criacao')
    
    fieldsets = (
        ('Dados do Pagamento', {
            'fields': ('numero_recibo', 'uuid', 'fatura', 'boleto', 'tipo', 'valor')
        }),
        ('Datas', {
            'fields': ('data_pagamento', 'data_confirmacao')
        }),
        ('Informações Adicionais', {
            'fields': ('referencia', 'observacoes', 'comprovante')
        }),
        ('Sistema', {
            'fields': ('data_criacao', 'criado_por'),
            'classes': ('collapse',)
        }),
    )


@admin.register(RelatorioFinanceiro)
class RelatorioFinanceiroAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'total_faturas', 'total_pago', 'saldo_aberto')
    list_filter = ('data_atualizacao',)
    search_fields = ('cliente__nome_empresa',)
    readonly_fields = (
        'total_consultorias', 'total_alugueis', 'total_faturas', 'total_pago',
        'saldo_aberto', 'faturas_abertas', 'faturas_vencidas', 'faturas_pagas',
        'data_atualizacao'
    )
    
    can_delete = False
    can_add = False


@admin.register(VendaEquipamento)
class VendaEquipamentoAdmin(admin.ModelAdmin):
    list_display = ('descricao', 'cliente', 'valor_total', 'quantidade_parcelas', 'status', 'quitado')
    list_filter = ('status', 'quitado', 'data_criacao')
    search_fields = ('descricao', 'cliente__nome_empresa')
    readonly_fields = ('data_criacao', 'data_atualizacao')
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('cliente', 'descricao', 'status')
        }),
        ('Valores', {
            'fields': ('valor_total', 'quantidade_parcelas')
        }),
        ('Datas', {
            'fields': ('data_inicio', 'data_fim', 'data_criacao', 'data_atualizacao')
        }),
        ('Quitação', {
            'fields': ('quitado', 'data_quitacao')
        }),
    )

@admin.register(Despesa)
class DespesaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'valor', 'categoria', 'recorrencia', 'data_vencimento', 'status', 'privada', 'criado_em')
    list_filter  = ('status', 'categoria', 'recorrencia', 'privada', 'data_vencimento')
    search_fields = ('nome', 'descricao')
    readonly_fields = ('criado_em', 'atualizado_em', 'criado_por')
    fieldsets = (
        ('Identificação', {'fields': ('nome', 'descricao', 'categoria', 'recorrencia')}),
        ('Valores e Datas', {'fields': ('valor', 'data_vencimento')}),
        ('Status', {'fields': ('status', 'data_pagamento', 'observacoes')}),
        ('Privacidade', {'fields': ('privada',)}),
        ('Auditoria', {'fields': ('criado_por', 'criado_em', 'atualizado_em'), 'classes': ('collapse',)}),
    )
