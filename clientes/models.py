from django.db import models
from django.contrib.auth.models import User
from funcao_equipamento.models import Funcao_equipamento
from modelo_equipamento.models import Modelo_equipamento

# Extensão do User para armazenar dados específicos do cliente
class Cliente(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE)
    nome_empresa = models.CharField(max_length=255)
    cnpj = models.CharField(max_length=18, unique=True)
    endereco = models.CharField(max_length=200)
    telefone = models.CharField(max_length=15,blank=True, null=True)
    email = models.EmailField(unique=True)
    cep = models.CharField(max_length=10, blank=True, null=True)
    cidade = models.CharField(max_length=100, blank=True, null=True)
    data_criacao = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return self.nome_empresa


# Modelo para armazenar acessos vinculados ao cliente

class Acesso(models.Model):
    class ProtocoloChoices(models.TextChoices):
        HTTP = 'HTTP', 'HTTP'
        HTTPS = 'HTTPS', 'HTTPS'
        TELNET = 'TELNET', 'Telnet'
        SSH = 'SSH', 'SSH'
        WINBOX = 'WINBOX', 'Winbox'
        FTP = 'FTP', 'FTP'
        FTPS = 'FTPS', 'FTPS'

    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='acessos')
    funcao = models.ForeignKey('funcao_equipamento.Funcao_equipamento', on_delete=models.SET_NULL, null=True, blank=True)
    modelo = models.ForeignKey('modelo_equipamento.Modelo_equipamento', on_delete=models.SET_NULL, null=True, blank=True)
    tipo = models.CharField(max_length=250, null=False, blank=False) 
    host = models.CharField(max_length=255)
    host_ipv6 = models.GenericIPAddressField(null=True, blank=True)
    winbox = models.IntegerField(null=True, blank=True)
    porta = models.PositiveIntegerField()
    protocolo = models.CharField(
        max_length=10,
        choices=ProtocoloChoices.choices
    )
    usuario = models.CharField(max_length=50)
    senha = models.CharField(max_length=100)
    senha_adm = models.CharField(max_length=100, blank=True)
    vlan = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.tipo} - {self.host}:{self.porta} ({self.cliente.nome_empresa})"



class Documento(models.Model):
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='documentos')
    nome = models.CharField(max_length=255)
    arquivo = models.FileField(upload_to='documentos/')
    data_upload = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome
    

class ArquivoVPN(models.Model):
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='arquivos_vpn')
    nome = models.CharField(max_length=255)
    arquivo = models.FileField(upload_to='vpn/')
    usuario = models.CharField(max_length=100, blank=True, null=True)
    senha = models.CharField(max_length=100, blank=True, null=True)
    private_key = models.TextField(blank=True, null=True)  # Para chaves longas
    data_upload = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome


class ImagemTopologia(models.Model):
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='imagens_topologia')
    nome = models.CharField(max_length=255)
    imagem = models.ImageField(upload_to='topologia/')
    data_upload = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome



# Adicione estas classes ao final do seu models.py existente

class Categoria(models.Model):
    nome = models.CharField(max_length=100, unique=True)
    descricao = models.TextField(blank=True, null=True)
    data_criacao = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome

    class Meta:
        verbose_name = 'Categoria'
        verbose_name_plural = 'Categorias'
        ordering = ['nome']


class Chamado(models.Model):
    class PrioridadeChoices(models.TextChoices):
        BAIXA = 'BAIXA', 'Baixa'
        NORMAL = 'NORMAL', 'Normal'
        ALTA = 'ALTA', 'Alta'
        URGENTE = 'URGENTE', 'Urgente'

    class DepartamentoChoices(models.TextChoices):
        SUPORTE_REDES = 'SUPORTE_REDES', 'Suporte de Redes'
        SERVIDORES = 'SERVIDORES', 'Servidores'
        MONITORAMENTO = 'MONITORAMENTO', 'Monitoramento'

    class StatusChoices(models.TextChoices):
        ABERTO = 'ABERTO', 'Aberto'
        EM_ANDAMENTO = 'EM_ANDAMENTO', 'Em Andamento'
        AGUARDANDO = 'AGUARDANDO', 'Aguardando'
        RESOLVIDO = 'RESOLVIDO', 'Resolvido'
        FECHADO = 'FECHADO', 'Fechado'

    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='chamados')
    categoria = models.ForeignKey('Categoria', on_delete=models.SET_NULL, null=True, related_name='chamados')
    prioridade = models.CharField(
        max_length=20,
        choices=PrioridadeChoices.choices,
        default=PrioridadeChoices.NORMAL
    )
    departamento = models.CharField(
        max_length=30,
        choices=DepartamentoChoices.choices
    )
    responsavel = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='chamados_responsavel')
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='chamados_criados')
    titulo = models.CharField(max_length=200)
    descricao = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=StatusChoices.choices,
        default=StatusChoices.ABERTO
    )
    data_criacao = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"#{self.id} - {self.titulo} - {self.cliente.nome_empresa}"

    class Meta:
        verbose_name = 'Chamado'
        verbose_name_plural = 'Chamados'
        ordering = ['-data_criacao']


class ComentarioChamado(models.Model):
    chamado = models.ForeignKey('Chamado', on_delete=models.CASCADE, related_name='comentarios')
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    comentario = models.TextField()
    data_criacao = models.DateTimeField(auto_now_add=True)
    is_internal = models.BooleanField(default=False)  # Para comentários internos

    def __str__(self):
        return f"Comentário de {self.usuario} em {self.chamado}"

    class Meta:
        verbose_name = 'Comentário'
        verbose_name_plural = 'Comentários'
        ordering = ['data_criacao']


# models.py - Adicione esta classe
class ProxyServer(models.Model):
    nome = models.CharField(max_length=100)
    host = models.CharField(max_length=255)
    porta = models.IntegerField(default=22)
    usuario = models.CharField(max_length=100)
    senha = models.CharField(max_length=100)
    ativo = models.BooleanField(default=True)
    data_criacao = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome



class ProxyServer(models.Model):
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='proxies')
    nome = models.CharField(max_length=100)
    host = models.CharField(max_length=255)
    porta = models.IntegerField(default=22)
    usuario = models.CharField(max_length=100)
    senha = models.CharField(max_length=100)
    ativo = models.BooleanField(default=True)
    data_criacao = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nome} - {self.cliente.nome_empresa}"
    
    class Meta:
        verbose_name = 'Servidor Proxy'
        verbose_name_plural = 'Servidores Proxy'
        ordering = ['-ativo', 'nome']



