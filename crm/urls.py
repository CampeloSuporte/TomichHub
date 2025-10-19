
from django.contrib import admin
from django.urls import path,include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('clientes/', include('clientes.urls')),
    path('funcao_equipamento/', include('funcao_equipamento.urls')),
    path('modelo_equipamento/', include('modelo_equipamento.urls')),
    path("auth/", include('usuario.urls')),
    path('home', include('home.urls'))
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
