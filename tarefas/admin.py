from django.contrib import admin
from .models import Tarefa


@admin.register(Tarefa)
class TarefaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'cliente', 'instancia', 'status', 'prioridade', 'prazo', 'assigned_to', 'criado_em')
    list_filter = ('status', 'prioridade', 'instancia')
    search_fields = ('titulo', 'descricao', 'cliente__nome_empresa')
    autocomplete_fields = ('cliente', 'assigned_to', 'criado_por')
    raw_id_fields = ()
