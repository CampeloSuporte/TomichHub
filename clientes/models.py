from django.db import models
from django.contrib.auth.models import User
from funcao_equipamento.models import Funcao_equipamento
from modelo_equipamento.models import Modelo_equipamento

# Extensão do User para armazenar dados específicos do cliente
class Cliente(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE)
    nome_empresa = models.CharField(max_length=100)
    cnpj = models.CharField(max_length=18, unique=True)
    endereco = models.CharField(max_length=200)
    telefone = models.CharField(max_length=15,blank=True, null=True)
    email = models.EmailField(unique=True)
    cep = models.CharField(max_length=10, blank=True, null=True)
    cidade = models.CharField(max_length=100, blank=True, null=True)
    data_criacao = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=2, blank=True, null=True)

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
    tipo = models.CharField(max_length=250, unique=True, null=False, blank=False) 
    host = models.GenericIPAddressField()
    host_ipv6 = models.GenericIPAddressField(null=True, blank=True)
    porta = models.PositiveIntegerField()
    protocolo = models.CharField(
        max_length=10,
        choices=ProtocoloChoices.choices
    )
    usuario = models.CharField(max_length=50)
    senha = models.CharField(max_length=100)
    senha_adm = models.CharField(max_length=100, blank=True)
    vlan = models.IntegerField(blank=True)

    def __str__(self):
        return f"{self.tipo} - {self.host}:{self.porta} ({self.cliente.nome_empresa})"





