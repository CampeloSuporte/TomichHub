from django.urls import path

from . import views

app_name = 'projeto_rede'

urlpatterns = [
    path('cliente/<int:cliente_id>/', views.lista, name='lista'),
    path('cliente/<int:cliente_id>/asis/gerar/', views.gerar_asis, name='gerar_asis'),
    path('cliente/<int:cliente_id>/hld/gerar/', views.gerar_hld, name='gerar_hld'),
    path('cliente/<int:cliente_id>/tobe/novo/', views.tobe_novo, name='tobe_novo'),
    path('cliente/<int:cliente_id>/tobe/gerar/', views.gerar_tobe, name='gerar_tobe'),
    path('cliente/<int:cliente_id>/cenarios/criar/', views.cenario_criar, name='cenario_criar'),
    path('cenario/<int:cenario_id>/', views.cenario_editor, name='cenario_editor'),
    path('cenario/<int:cenario_id>/salvar/', views.cenario_salvar, name='cenario_salvar'),
    path('cenario/<int:cenario_id>/excluir/', views.cenario_excluir, name='cenario_excluir'),
    path('documento/<int:doc_id>/', views.editor, name='editor'),
    path('documento/<int:doc_id>/visualizar/', views.visualizar, name='visualizar'),
    path('documento/<int:doc_id>/pdf/', views.baixar_pdf, name='pdf'),
    path('documento/<int:doc_id>/docx/', views.baixar_docx, name='docx'),
    path('documento/<int:doc_id>/salvar/', views.salvar, name='salvar'),
    path('documento/<int:doc_id>/secao/regenerar/', views.regenerar_secao, name='regenerar_secao'),
    path('documento/<int:doc_id>/regenerar/', views.regenerar_tudo, name='regenerar_tudo'),
    path('documento/<int:doc_id>/revisoes/', views.revisoes, name='revisoes'),
    path('documento/<int:doc_id>/revisoes/registrar/', views.registrar_revisao, name='registrar_revisao'),
    path('documento/<int:doc_id>/revisoes/<int:rev_id>/restaurar/', views.restaurar_revisao, name='restaurar_revisao'),
    path('documento/<int:doc_id>/excluir/', views.excluir, name='excluir'),
    path('documento/<int:doc_id>/convencao/', views.convencao, name='convencao'),
    path('documento/<int:doc_id>/convencao/salvar/', views.salvar_convencao, name='salvar_convencao'),
    path('documento/<int:doc_id>/mapeamentos/', views.tobe_mapeamentos, name='tobe_mapeamentos'),
    path('documento/<int:doc_id>/mapeamentos/salvar/', views.salvar_mapeamentos, name='salvar_mapeamentos'),
]
