from django.contrib import admin

from .models import CenarioTopologia, DocumentoRede, DocumentoRedeRevisao


@admin.register(DocumentoRede)
class DocumentoRedeAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'cliente', 'tipo', 'versao', 'status', 'atualizado_em')
    list_filter = ('tipo', 'status')
    search_fields = ('titulo', 'cliente__nome_empresa')
    raw_id_fields = ('cliente', 'documento_base')


@admin.register(DocumentoRedeRevisao)
class DocumentoRedeRevisaoAdmin(admin.ModelAdmin):
    list_display = ('documento', 'versao', 'motivo', 'autor', 'criado_em')
    raw_id_fields = ('documento',)


@admin.register(CenarioTopologia)
class CenarioTopologiaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'cliente', 'atualizado_em')
    raw_id_fields = ('cliente', 'origem')
