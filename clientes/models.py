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
    porta = models.PositiveIntegerField(null=True, blank=True)
    protocolo = models.CharField(
        max_length=10,
        choices=ProtocoloChoices.choices
    )
    usuario = models.CharField(max_length=50)
    senha = models.CharField(max_length=100)
    senha_adm = models.CharField(max_length=100, blank=True)
    vlan = models.IntegerField(null=True, blank=True)
    backup_habilitado = models.BooleanField(default=False, verbose_name="Habilitar Backup")
    backup_template = models.ForeignKey(
        'BackupTemplate', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='acessos',
        verbose_name="Template de Backup"
    )
    backup_automatico = models.BooleanField(
        default=False, 
        verbose_name="Backup Automático",
        help_text="Executar backup automaticamente via agendamento"
    )

    def __str__(self):
        return f"{self.tipo} - {self.host}:{self.porta} ({self.cliente.nome_empresa})"


class ComentarioAcesso(models.Model):
    """Comentários para acessos de equipamento"""
    acesso = models.ForeignKey('Acesso', on_delete=models.CASCADE, related_name='comentarios')
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    comentario = models.TextField()
    data_criacao = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Comentário de Acesso'
        verbose_name_plural = 'Comentários de Acesso'
        ordering = ['-data_criacao']

    def __str__(self):
        return f"Comentário de {self.usuario} em {self.acesso.tipo}"


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
    drawio_url = models.URLField(
        blank=True, 
        null=True,
        verbose_name="Link DrawIO",
        help_text="URL da topologia editável no DrawIO (ex: draw.io/...)"
    )
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


class BackupTemplate(models.Model):
    """Templates de backup para diferentes fabricantes"""
    FABRICANTES_CHOICES = [
        ('CISCO', 'Cisco'),
        ('HUAWEI', 'Huawei'),
        ('MIKROTIK', 'MikroTik'),
        ('JUNIPER', 'Juniper'),
        ('DELL', 'Dell'),
        ('HP', 'HP/Aruba'),
        ('EXTREME', 'Extreme Networks'),
        ('GENERICO', 'Genérico'),
    ]
    
    nome = models.CharField(max_length=100)
    fabricante = models.CharField(max_length=20, choices=FABRICANTES_CHOICES)
    comandos = models.TextField(help_text="Comandos separados por linha")
    ativo = models.BooleanField(default=True)
    descricao = models.TextField(blank=True, null=True)
    data_criacao = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Template de Backup'
        verbose_name_plural = 'Templates de Backup'
        ordering = ['fabricante', 'nome']
    
    def __str__(self):
        return f"{self.get_fabricante_display()} - {self.nome}"
    
    def get_comandos_list(self):
        """Retorna lista de comandos"""
        return [cmd.strip() for cmd in self.comandos.split('\n') if cmd.strip()]


class BackupLog(models.Model):
    """Log de backups realizados"""
    STATUS_CHOICES = [
        ('SUCESSO', 'Sucesso'),
        ('ERRO', 'Erro'),
        ('PARCIAL', 'Parcial'),
    ]
    
    acesso = models.ForeignKey('Acesso', on_delete=models.CASCADE, related_name='backups')
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='backups')
    template = models.ForeignKey('BackupTemplate', on_delete=models.SET_NULL, null=True, blank=True)
    
    arquivo_path = models.CharField(max_length=500)  # Caminho relativo do arquivo
    tamanho_bytes = models.IntegerField(default=0)
    
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='SUCESSO')
    mensagem = models.TextField(blank=True, null=True)
    
    executado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    data_backup = models.DateTimeField(auto_now_add=True)
    duracao_segundos = models.FloatField(default=0)
    
    class Meta:
        verbose_name = 'Log de Backup'
        verbose_name_plural = 'Logs de Backup'
        ordering = ['-data_backup']
    
    def __str__(self):
        return f"Backup {self.acesso.tipo} - {self.data_backup.strftime('%d/%m/%Y %H:%M')}"
    
    def get_tamanho_formatado(self):
        """Retorna tamanho formatado"""
        if self.tamanho_bytes < 1024:
            return f"{self.tamanho_bytes} B"
        elif self.tamanho_bytes < 1024 * 1024:
            return f"{self.tamanho_bytes / 1024:.2f} KB"
        else:
            return f"{self.tamanho_bytes / (1024 * 1024):.2f} MB"



class BlocoIP(models.Model):
    """Blocos de IP IPv4 e IPv6 do cliente"""
    TIPO_CHOICES = [
        ('IPV4', 'IPv4'),
        ('IPV6', 'IPv6'),
    ]
    
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='blocos_ip')
    tipo = models.CharField(max_length=4, choices=TIPO_CHOICES)
    bloco = models.CharField(max_length=100, help_text="Ex: 200.100.50.0/24 ou 2801:80:1234::/48")
    asn = models.CharField(max_length=20, help_text="Ex: AS12345", blank=True, null=True)
    irr_registry = models.CharField(max_length=50, help_text="Ex: LACNIC, RIPE, ARIN", blank=True, null=True)
    
    # Status de validação
    rpki_valido = models.BooleanField(default=False)
    irr_valido = models.BooleanField(default=False)
    
    # Informações da última validação
    ultima_validacao = models.DateTimeField(blank=True, null=True)
    rpki_status = models.CharField(max_length=20, blank=True, null=True)
    rpki_mensagem = models.TextField(blank=True, null=True)
    irr_status = models.CharField(max_length=20, blank=True, null=True)
    irr_mensagem = models.TextField(blank=True, null=True)
    
    data_criacao = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Bloco de IP'
        verbose_name_plural = 'Blocos de IP'
        ordering = ['cliente', 'tipo', 'bloco']
    
    def __str__(self):
        return f"{self.bloco} ({self.get_tipo_display()}) - {self.cliente.nome_empresa}"
    
    def get_status_rpki_display(self):
        if not self.rpki_status:
            return 'Não Validado'
        return self.rpki_status
    
    def get_status_irr_display(self):
        if not self.irr_status:
            return 'Não Validado'
        return self.irr_status


class ValidacaoRPKI_IRR_Log(models.Model):
    """Log de validações RPKI/IRR"""
    bloco = models.ForeignKey('BlocoIP', on_delete=models.CASCADE, related_name='logs_validacao')
    
    data_validacao = models.DateTimeField(auto_now_add=True)
    
    rpki_valido = models.BooleanField(default=False)
    rpki_status = models.CharField(max_length=20, blank=True, null=True)
    rpki_detalhes = models.TextField(blank=True, null=True)
    
    irr_valido = models.BooleanField(default=False)
    irr_status = models.CharField(max_length=20, blank=True, null=True)
    irr_detalhes = models.TextField(blank=True, null=True)
    
    duracao_segundos = models.FloatField(default=0)
    
    class Meta:
        verbose_name = 'Log de Validação RPKI/IRR'
        verbose_name_plural = 'Logs de Validação RPKI/IRR'
        ordering = ['-data_validacao']
    
    def __str__(self):
        return f"Validação {self.bloco.bloco} - {self.data_validacao.strftime('%d/%m/%Y %H:%M')}"


class DocumentacaoRedeConfig(models.Model):
    """
    Configuração de documentação de rede por cliente.
    Armazena URLs do PHP IPAM e NetBox (podem ser IPs privados).
    O acesso é feito via tunnel SSH do proxy configurado no cliente.
    """
    cliente = models.OneToOneField(
        Cliente,
        on_delete=models.CASCADE,
        related_name='documentacao_config'
    )
    
    # PHP IPAM
    phpipam_habilitado   = models.BooleanField(default=False)
    phpipam_url          = models.CharField(max_length=500, blank=True, null=True,
                                             help_text='Ex: http://192.168.1.10/phpipam')
    phpipam_usuario      = models.CharField(max_length=100, blank=True, null=True)
    phpipam_senha        = models.CharField(max_length=200, blank=True, null=True)

    # NetBox
    netbox_habilitado    = models.BooleanField(default=False)
    netbox_url           = models.CharField(max_length=500, blank=True, null=True,
                                             help_text='Ex: http://192.168.1.20:8000')
    netbox_token         = models.CharField(max_length=200, blank=True, null=True,
                                             help_text='Token de API do NetBox (opcional)')

    data_atualizacao = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração de Documentação de Rede'
        verbose_name_plural = 'Configurações de Documentação de Rede'

    def __str__(self):
        return f'DocConfig - {self.cliente.nome_empresa}'
    