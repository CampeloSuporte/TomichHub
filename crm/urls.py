
from django.contrib import admin
from django.urls import path,include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('clientes/', include('clientes.urls')),
    path('funcao_equipamento/', include('funcao_equipamento.urls')),
    path('modelo_equipamento/', include('modelo_equipamento.urls')),
    path("auth/", include('usuario.urls')),
]
