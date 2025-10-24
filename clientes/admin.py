from django.contrib import admin
from .models import Cliente, Acesso
from .models import ProxyServer


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