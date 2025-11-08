from django.contrib import admin
from .models import Cliente, Acesso
from .models import ProxyServer


from .models import BackupTemplate, BackupLog

@admin.register(BackupTemplate)
class BackupTemplateAdmin(admin.ModelAdmin):
    list_display = ['nome', 'fabricante', 'ativo', 'data_criacao']
    list_filter = ['fabricante', 'ativo']
    search_fields = ['nome', 'descricao']

@admin.register(BackupLog)
class BackupLogAdmin(admin.ModelAdmin):
    list_display = ['acesso', 'cliente', 'status', 'tamanho_bytes', 'data_backup', 'duracao_segundos']
    list_filter = ['status', 'data_backup', 'cliente']
    search_fields = ['acesso__tipo', 'cliente__nome_empresa']
    readonly_fields = ['data_backup']

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nome_empresa', 'usuario', 'cnpj', 'telefone')
    search_fields = ('nome_empresa', 'cnpj')

@admin.register(Acesso)
class AcessoAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'host', 'porta', 'protocolo', 'cliente')
    list_filter = ('tipo', 'protocolo')
    search_fields = ('host', 'cliente__nome_empresa')



@admin.register(ProxyServer)
class ProxyServerAdmin(admin.ModelAdmin):
    list_display = ['nome', 'host', 'porta', 'ativo', 'data_criacao']
    list_filter = ['ativo', 'data_criacao']
    search_fields = ['nome', 'host']