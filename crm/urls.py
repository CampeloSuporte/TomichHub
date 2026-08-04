
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    # Compatibilidade: login_url='login' em todo o projeto aponta para /login/
    path('login/', RedirectView.as_view(url='/auth/login/', query_string=True)),
    path('admin/', admin.site.urls),
    path('clientes/', include('clientes.urls')),
    path('funcao_equipamento/', include('funcao_equipamento.urls')),
    path('modelo_equipamento/', include('modelo_equipamento.urls')),
    path("auth/", include('usuario.urls')),
    path('home', include('home.urls')),
    path('api/', include('clientes.api_urls')),
    path('financeiro/', include('financeiro.urls')),
    path('atendimento/', include('atendimento.urls', namespace='atendimento')),
    path('wiki/', include('wiki.urls')),
    path('monitoramento/', include('monitoramento.urls', namespace='monitoramento')),
    path('tarefas/', include('tarefas.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
