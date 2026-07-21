from django.contrib import admin
from .models import CategoriaWiki, TagWiki, ArtigoWiki, BlocoCodigoWiki, AnexoWiki


class BlocoCodigoInline(admin.TabularInline):
    model = BlocoCodigoWiki
    extra = 0


class AnexoInline(admin.TabularInline):
    model = AnexoWiki
    extra = 0


@admin.register(CategoriaWiki)
class CategoriaWikiAdmin(admin.ModelAdmin):
    list_display = ('nome', 'icone', 'ordem')
    prepopulated_fields = {'slug': ('nome',)}
    ordering = ('ordem', 'nome')


@admin.register(TagWiki)
class TagWikiAdmin(admin.ModelAdmin):
    list_display = ('nome',)
    prepopulated_fields = {'slug': ('nome',)}
    search_fields = ('nome',)


@admin.register(ArtigoWiki)
class ArtigoWikiAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'categoria', 'fabricante', 'favorito', 'destaque', 'ativo', 'visualizacoes', 'data_atualizacao')
    list_filter = ('categoria', 'fabricante', 'favorito', 'destaque', 'ativo')
    search_fields = ('titulo', 'descricao_curta', 'conteudo', 'modelo_especifico')
    prepopulated_fields = {'slug': ('titulo',)}
    autocomplete_fields = ('tags',)
    readonly_fields = ('visualizacoes', 'data_criacao', 'data_atualizacao')
    inlines = [BlocoCodigoInline, AnexoInline]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.criado_por = request.user
        obj.atualizado_por = request.user
        super().save_model(request, obj, form, change)
