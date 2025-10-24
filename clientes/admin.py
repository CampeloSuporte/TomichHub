from django.contrib import admin
from .models import Cliente, Acesso

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nome_empresa', 'usuario', 'cnpj', 'telefone')
    search_fields = ('nome_empresa', 'cnpj')

@admin.register(Acesso)
class AcessoAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'host', 'porta', 'protocolo', 'cliente')
    list_filter = ('tipo', 'protocolo')
    search_fields = ('host', 'cliente__nome_empresa')

