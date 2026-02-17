from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify

class CategoriaWiki(models.Model):
    """Categorias para organizar os artigos"""
    nome = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    icone = models.CharField(max_length=50, default='fa-book', help_text='FontAwesome icon class')
    ordem = models.IntegerField(default=0)
    
    class Meta:
        verbose_name = 'Categoria Wiki'
        verbose_name_plural = 'Categorias Wiki'
        ordering = ['ordem', 'nome']
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nome)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.nome


class TagWiki(models.Model):
    """Tags para busca rápida"""
    nome = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=50, unique=True, blank=True)
    
    class Meta:
        verbose_name = 'Tag Wiki'
        verbose_name_plural = 'Tags Wiki'
        ordering = ['nome']
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nome)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.nome


class ArtigoWiki(models.Model):
    """Artigo principal da Wiki"""
    
    FABRICANTES = [
        ('MIKROTIK', 'MikroTik'),
        ('CISCO', 'Cisco'),
        ('HUAWEI', 'Huawei'),
        ('JUNIPER', 'Juniper'),
        ('EXTREME', 'Extreme Networks'),
        ('HP', 'HP/Aruba'),
        ('DELL', 'Dell'),
        ('DATACOM', 'Datacom'),
        ('GENERICO', 'Genérico/Múltiplos'),
    ]
    
    titulo = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True, blank=True)
    categoria = models.ForeignKey(CategoriaWiki, on_delete=models.CASCADE, related_name='artigos')
    tags = models.ManyToManyField(TagWiki, blank=True, related_name='artigos')
    
    # Metadados
    fabricante = models.CharField(max_length=20, choices=FABRICANTES, default='GENERICO')
    modelo_especifico = models.CharField(max_length=100, blank=True, help_text='Ex: RB4011, Catalyst 3750')
    
    # Conteúdo
    descricao_curta = models.TextField(max_length=300, help_text='Resumo que aparece na busca')
    conteudo = models.TextField(help_text='Conteúdo em Markdown')
    
    # Prioridade
    favorito = models.BooleanField(default=False)
    destaque = models.BooleanField(default=False)
    ordem = models.IntegerField(default=0, help_text='Ordem de exibição (menor = primeiro)')
    
    # Controle
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='artigos_criados')
    atualizado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='artigos_atualizados')
    data_criacao = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)
    ativo = models.BooleanField(default=True)
    
    # Estatísticas
    visualizacoes = models.IntegerField(default=0)
    
    class Meta:
        verbose_name = 'Artigo Wiki'
        verbose_name_plural = 'Artigos Wiki'
        ordering = ['-destaque', '-favorito', 'ordem', '-data_atualizacao']
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.titulo)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.titulo
    
    def incrementar_visualizacao(self):
        self.visualizacoes += 1
        self.save(update_fields=['visualizacoes'])


class BlocoCodigoWiki(models.Model):
    """Blocos de código separados para melhor organização"""
    
    LINGUAGENS = [
        ('routeros', 'RouterOS (MikroTik)'),
        ('cisco', 'Cisco IOS'),
        ('junos', 'JunOS (Juniper)'),
        ('bash', 'Bash/Shell'),
        ('python', 'Python'),
        ('javascript', 'JavaScript'),
        ('yaml', 'YAML'),
        ('json', 'JSON'),
        ('plaintext', 'Texto Puro'),
    ]
    
    artigo = models.ForeignKey(ArtigoWiki, on_delete=models.CASCADE, related_name='blocos_codigo')
    titulo = models.CharField(max_length=200, help_text='Ex: Configuração BGP, Script de Backup')
    linguagem = models.CharField(max_length=20, choices=LINGUAGENS, default='routeros')
    codigo = models.TextField()
    descricao = models.TextField(blank=True, help_text='Explicação do código')
    ordem = models.IntegerField(default=0)
    
    class Meta:
        verbose_name = 'Bloco de Código'
        verbose_name_plural = 'Blocos de Código'
        ordering = ['ordem']
    
    def __str__(self):
        return f"{self.artigo.titulo} - {self.titulo}"


class AnexoWiki(models.Model):
    """Anexos (PDFs, imagens, configs)"""
    artigo = models.ForeignKey(ArtigoWiki, on_delete=models.CASCADE, related_name='anexos')
    nome = models.CharField(max_length=200)
    arquivo = models.FileField(upload_to='wiki/anexos/%Y/%m/')
    descricao = models.TextField(blank=True)
    data_upload = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Anexo Wiki'
        verbose_name_plural = 'Anexos Wiki'
        ordering = ['-data_upload']
    
    def __str__(self):
        return self.nome