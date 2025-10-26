# clientes/serializers.py
from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Chamado, ComentarioChamado, Categoria, Cliente


class UserSerializer(serializers.ModelSerializer):
    """Serializer para usuários"""
    nome_completo = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'nome_completo']
        
    def get_nome_completo(self, obj):
        return obj.get_full_name() or obj.username


class CategoriaSerializer(serializers.ModelSerializer):
    """Serializer para categorias"""
    class Meta:
        model = Categoria
        fields = ['id', 'nome', 'descricao']


class ClienteSerializer(serializers.ModelSerializer):
    """Serializer para clientes"""
    class Meta:
        model = Cliente
        fields = ['id', 'nome_empresa', 'cnpj', 'email', 'telefone']


class ComentarioChamadoSerializer(serializers.ModelSerializer):
    """Serializer para comentários de chamados"""
    usuario = UserSerializer(read_only=True)
    usuario_nome = serializers.SerializerMethodField()
    
    class Meta:
        model = ComentarioChamado
        fields = ['id', 'chamado', 'usuario', 'usuario_nome', 'comentario', 
                  'data_criacao', 'is_internal']
        read_only_fields = ['id', 'data_criacao', 'usuario']
    
    def get_usuario_nome(self, obj):
        if obj.usuario:
            return obj.usuario.get_full_name() or obj.usuario.username
        return "Usuário Desconhecido"


class ChamadoListSerializer(serializers.ModelSerializer):
    """Serializer simplificado para listagem de chamados"""
    categoria_nome = serializers.CharField(source='categoria.nome', read_only=True)
    cliente_nome = serializers.CharField(source='cliente.nome_empresa', read_only=True)
    responsavel_nome = serializers.SerializerMethodField()
    criado_por_nome = serializers.SerializerMethodField()
    prioridade_display = serializers.CharField(source='get_prioridade_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    departamento_display = serializers.CharField(source='get_departamento_display', read_only=True)
    total_comentarios = serializers.SerializerMethodField()
    
    class Meta:
        model = Chamado
        fields = [
            'id', 'titulo', 'descricao', 'status', 'status_display',
            'prioridade', 'prioridade_display', 'departamento', 'departamento_display',
            'categoria', 'categoria_nome', 'cliente', 'cliente_nome',
            'responsavel', 'responsavel_nome', 'criado_por', 'criado_por_nome',
            'data_criacao', 'data_atualizacao', 'total_comentarios'
        ]
    
    def get_responsavel_nome(self, obj):
        if obj.responsavel:
            return obj.responsavel.get_full_name() or obj.responsavel.username
        return "Não atribuído"
    
    def get_criado_por_nome(self, obj):
        if obj.criado_por:
            return obj.criado_por.get_full_name() or obj.criado_por.username
        return "Desconhecido"
    
    def get_total_comentarios(self, obj):
        return obj.comentarios.count()


class ChamadoDetailSerializer(serializers.ModelSerializer):
    """Serializer completo para detalhes do chamado"""
    categoria = CategoriaSerializer(read_only=True)
    categoria_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    
    cliente = ClienteSerializer(read_only=True)
    cliente_id = serializers.IntegerField(write_only=True)
    
    responsavel = UserSerializer(read_only=True)
    responsavel_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    
    criado_por = UserSerializer(read_only=True)
    
    comentarios = ComentarioChamadoSerializer(many=True, read_only=True)
    
    prioridade_display = serializers.CharField(source='get_prioridade_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    departamento_display = serializers.CharField(source='get_departamento_display', read_only=True)
    
    class Meta:
        model = Chamado
        fields = [
            'id', 'titulo', 'descricao', 'status', 'status_display',
            'prioridade', 'prioridade_display', 'departamento', 'departamento_display',
            'categoria', 'categoria_id', 'cliente', 'cliente_id',
            'responsavel', 'responsavel_id', 'criado_por',
            'data_criacao', 'data_atualizacao', 'comentarios'
        ]
        read_only_fields = ['id', 'criado_por', 'data_criacao', 'data_atualizacao']
    
    def create(self, validated_data):
        # Define o usuário que está criando o chamado
        validated_data['criado_por'] = self.context['request'].user
        return super().create(validated_data)


class ChamadoCreateSerializer(serializers.ModelSerializer):
    """Serializer para criação simplificada de chamados"""
    comentario_inicial = serializers.CharField(write_only=True, required=False, allow_blank=True)
    
    class Meta:
        model = Chamado
        fields = [
            'cliente', 'categoria', 'titulo', 'descricao',
            'prioridade', 'departamento', 'responsavel', 'comentario_inicial'
        ]
    
    def create(self, validated_data):
        comentario_inicial = validated_data.pop('comentario_inicial', None)
        validated_data['criado_por'] = self.context['request'].user
        
        chamado = Chamado.objects.create(**validated_data)
        
        # Adicionar comentário inicial se fornecido
        if comentario_inicial:
            ComentarioChamado.objects.create(
                chamado=chamado,
                usuario=self.context['request'].user,
                comentario=comentario_inicial,
                is_internal=False
            )
        
        return chamado