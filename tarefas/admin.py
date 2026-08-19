from django.contrib import admin
from .models import Tarefa


@admin.register(Tarefa)
class TarefaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'cliente', 'instancia', 'status', 'prioridade', 'prazo', 'responsaveis_display', 'criado_em')
    list_filter = ('status', 'prioridade', 'instancia')
    search_fields = ('titulo', 'descricao', 'cliente__nome_empresa')
    autocomplete_fields = ('cliente', 'criado_por')
    filter_horizontal = ('responsaveis',)
    raw_id_fields = ()

    def responsaveis_display(self, obj):
        return ', '.join(u.get_full_name() or u.username for u in obj.responsaveis.all()) or '—'
    responsaveis_display.short_description = 'Responsáveis'
