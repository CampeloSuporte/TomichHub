from django.urls import path

from . import views

app_name = 'projeto_rede'

urlpatterns = [
    path('cliente/<int:cliente_id>/', views.lista, name='lista'),
    path('cliente/<int:cliente_id>/asis/gerar/', views.gerar_asis, name='gerar_asis'),
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
]
