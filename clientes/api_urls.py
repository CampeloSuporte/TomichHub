# clientes/api_urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token
from .api_views import ChamadoViewSet, CategoriaViewSet, ClienteViewSet

# Criar router para registrar os ViewSets
router = DefaultRouter()
router.register(r'chamados', ChamadoViewSet, basename='api-chamado')
router.register(r'categorias', CategoriaViewSet, basename='api-categoria')
router.register(r'clientes', ClienteViewSet, basename='api-cliente')

urlpatterns = [
    # Rota para obter token de autenticação
    path('auth/token/', obtain_auth_token, name='api_token_auth'),
    
    # Incluir todas as rotas do router
    path('', include(router.urls)),
]