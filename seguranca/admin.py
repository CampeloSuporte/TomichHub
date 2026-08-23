from django.contrib import admin

from .models import AcaoSeguranca, BloqueioLogin, EventoSeguranca, TentativaLogin


@admin.register(TentativaLogin)
class TentativaLoginAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'username', 'ip', 'motivo', 'sucesso')
    list_filter = ('sucesso', 'motivo', 'criado_em')
    search_fields = ('username', 'ip')
    date_hierarchy = 'criado_em'
    readonly_fields = tuple(f.name for f in TentativaLogin._meta.fields)


@admin.register(BloqueioLogin)
class BloqueioLoginAdmin(admin.ModelAdmin):
    list_display = ('chave', 'tipo', 'falhas', 'bloqueado_ate', 'total_bloqueios', 'ultima_falha_em')
    list_filter = ('tipo',)
    search_fields = ('chave', 'ultimo_ip')


@admin.register(EventoSeguranca)
class EventoSegurancaAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'tipo', 'assinatura', 'ip', 'caminho', 'bloqueado')
    list_filter = ('tipo', 'bloqueado', 'criado_em')
    search_fields = ('ip', 'caminho', 'payload')
    date_hierarchy = 'criado_em'


@admin.register(AcaoSeguranca)
class AcaoSegurancaAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'acao', 'alvo', 'usuario')
    list_filter = ('acao',)
    search_fields = ('alvo',)
