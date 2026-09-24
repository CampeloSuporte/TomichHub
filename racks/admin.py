from django.contrib import admin

from .models import ConexaoFisica, LinkSemCabo, Rack, RackEquipamento


@admin.register(Rack)
class RackAdmin(admin.ModelAdmin):
    list_display = ('nome', 'cliente', 'local', 'altura_u', 'atualizado_em')
    search_fields = ('nome', 'local', 'cliente__nome_empresa')
    raw_id_fields = ('cliente',)


@admin.register(RackEquipamento)
class RackEquipamentoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'rack', 'tipo', 'u_inicial', 'altura_u', 'face')
    list_filter = ('tipo', 'face')
    search_fields = ('nome', 'rack__nome')
    raw_id_fields = ('rack', 'acesso')


@admin.register(ConexaoFisica)
class ConexaoFisicaAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'cliente', 'meio', 'identificacao', 'topologia_link_id', 'sincronizado')
    list_filter = ('meio', 'sincronizado')
    raw_id_fields = ('cliente', 'ponta_a', 'ponta_b', 'diagrama')


@admin.register(LinkSemCabo)
class LinkSemCaboAdmin(admin.ModelAdmin):
    list_display = ('topologia_link_id', 'cliente', 'criado_por', 'criado_em')
    raw_id_fields = ('cliente',)
