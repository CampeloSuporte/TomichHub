from django.contrib import admin
from .models import Rotina, RotinaItem, Tarefa, TarefaChecklistItem


class TarefaChecklistItemInline(admin.TabularInline):
    model = TarefaChecklistItem
    extra = 0
    fields = ('ordem', 'texto', 'verificado', 'verificado_por', 'verificado_em')
    readonly_fields = ('verificado_por', 'verificado_em')


@admin.register(Tarefa)
class TarefaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'cliente', 'instancia', 'status', 'prioridade', 'prazo', 'responsaveis_display', 'rotina', 'criado_em')
    list_filter = ('status', 'prioridade', 'instancia')
    search_fields = ('titulo', 'descricao', 'cliente__nome_empresa')
    autocomplete_fields = ('cliente', 'criado_por')
    filter_horizontal = ('responsaveis',)
    raw_id_fields = ('rotina',)
    inlines = [TarefaChecklistItemInline]

    def responsaveis_display(self, obj):
        return ', '.join(u.get_full_name() or u.username for u in obj.responsaveis.all()) or '—'
    responsaveis_display.short_description = 'Responsáveis'


class RotinaItemInline(admin.TabularInline):
    model = RotinaItem
    extra = 1
    fields = ('ordem', 'texto')


@admin.register(Rotina)
class RotinaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'cliente', 'instancia', 'dia_do_mes', 'ativa', 'ultima_competencia', 'criado_em')
    list_filter = ('ativa', 'instancia')
    search_fields = ('titulo', 'descricao', 'cliente__nome_empresa')
    autocomplete_fields = ('cliente', 'criado_por')
    filter_horizontal = ('responsaveis',)
    inlines = [RotinaItemInline]
