# clientes/api_views.py
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from .models import Chamado, ComentarioChamado, Categoria, Cliente
from .serializers import (
    ChamadoListSerializer, 
    ChamadoDetailSerializer, 
    ChamadoCreateSerializer,
    ComentarioChamadoSerializer,
    CategoriaSerializer,
    ClienteSerializer
)


class ChamadoViewSet(viewsets.ModelViewSet):
    """
    ViewSet para gerenciar Chamados via API
    
    Endpoints disponíveis:
    - GET    /api/chamados/          - Lista todos os chamados
    - POST   /api/chamados/          - Cria um novo chamado
    - GET    /api/chamados/{id}/     - Detalhes de um chamado
    - PUT    /api/chamados/{id}/     - Atualiza um chamado
    - PATCH  /api/chamados/{id}/     - Atualiza parcialmente um chamado
    - DELETE /api/chamados/{id}/     - Deleta um chamado
    - POST   /api/chamados/{id}/adicionar_comentario/ - Adiciona comentário
    - GET    /api/chamados/meus_chamados/ - Lista chamados do usuário
    """
    
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    
    # Campos para filtro
    filterset_fields = ['status', 'prioridade', 'departamento', 'categoria', 'cliente', 'responsavel']
    
    # Campos para busca
    search_fields = ['titulo', 'descricao', 'cliente__nome_empresa']
    
    # Campos para ordenação
    ordering_fields = ['data_criacao', 'data_atualizacao', 'prioridade']
    ordering = ['-data_criacao']
    
    def get_queryset(self):
        """Retorna chamados baseado no usuário"""
        user = self.request.user
        
        # Se for superusuário, retorna todos
        if user.is_superuser:
            return Chamado.objects.all().select_related(
                'categoria', 'cliente', 'responsavel', 'criado_por'
            ).prefetch_related('comentarios')
        
        # Senão, retorna apenas chamados do cliente do usuário ou onde é responsável
        return Chamado.objects.filter(
            models.Q(cliente__usuario=user) | 
            models.Q(responsavel=user) |
            models.Q(criado_por=user)
        ).select_related(
            'categoria', 'cliente', 'responsavel', 'criado_por'
        ).prefetch_related('comentarios').distinct()
    
    def get_serializer_class(self):
        """Retorna o serializer apropriado para cada ação"""
        if self.action == 'list':
            return ChamadoListSerializer
        elif self.action == 'create':
            return ChamadoCreateSerializer
        return ChamadoDetailSerializer
    
    def create(self, request, *args, **kwargs):
        """Cria um novo chamado"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chamado = serializer.save()
        
        # Retorna o chamado criado com todos os detalhes
        detail_serializer = ChamadoDetailSerializer(chamado)
        return Response(detail_serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'])
    def adicionar_comentario(self, request, pk=None):
        """
        Adiciona um comentário ao chamado
        
        Body JSON:
        {
            "comentario": "Texto do comentário",
            "is_internal": false
        }
        """
        chamado = self.get_object()
        
        comentario_texto = request.data.get('comentario')
        is_internal = request.data.get('is_internal', False)
        
        if not comentario_texto:
            return Response(
                {'error': 'O campo comentario é obrigatório'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        comentario = ComentarioChamado.objects.create(
            chamado=chamado,
            usuario=request.user,
            comentario=comentario_texto,
            is_internal=is_internal
        )
        
        serializer = ComentarioChamadoSerializer(comentario)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=False, methods=['get'])
    def meus_chamados(self, request):
        """Retorna apenas os chamados criados pelo usuário"""
        chamados = self.get_queryset().filter(criado_por=request.user)
        
        page = self.paginate_queryset(chamados)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(chamados, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def estatisticas(self, request):
        """Retorna estatísticas dos chamados"""
        queryset = self.get_queryset()
        
        stats = {
            'total': queryset.count(),
            'abertos': queryset.filter(status='ABERTO').count(),
            'em_andamento': queryset.filter(status='EM_ANDAMENTO').count(),
            'aguardando': queryset.filter(status='AGUARDANDO').count(),
            'resolvidos': queryset.filter(status='RESOLVIDO').count(),
            'fechados': queryset.filter(status='FECHADO').count(),
            'por_prioridade': {
                'urgente': queryset.filter(prioridade='URGENTE').count(),
                'alta': queryset.filter(prioridade='ALTA').count(),
                'normal': queryset.filter(prioridade='NORMAL').count(),
                'baixa': queryset.filter(prioridade='BAIXA').count(),
            }
        }
        
        return Response(stats)


class CategoriaViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet somente leitura para Categorias
    
    Endpoints:
    - GET /api/categorias/     - Lista todas as categorias
    - GET /api/categorias/{id}/ - Detalhes de uma categoria
    """
    queryset = Categoria.objects.all()
    serializer_class = CategoriaSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['nome', 'descricao']


class ClienteViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet somente leitura para Clientes
    
    Endpoints:
    - GET /api/clientes/     - Lista todos os clientes
    - GET /api/clientes/{id}/ - Detalhes de um cliente
    """
    queryset = Cliente.objects.all()
    serializer_class = ClienteSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['nome_empresa', 'cnpj', 'email']
    
    def get_queryset(self):
        """Retorna apenas o cliente do usuário se não for superusuário"""
        user = self.request.user
        
        if user.is_superuser:
            return Cliente.objects.all()
        
        # Retorna apenas o cliente vinculado ao usuário
        return Cliente.objects.filter(usuario=user)