from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
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


class TopologiaDiagrama(models.Model):
    """Armazena o estado do editor de topologia SVG e/ou XML do draw.io."""
    cliente       = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='diagramas_topologia')
    nome          = models.CharField(max_length=255, default='Nova Topologia')
    dados_json    = models.TextField(default='{"nodes":[],"links":[]}', verbose_name='Dados do editor')
    drawio_xml    = models.TextField(blank=True, default='', verbose_name='XML draw.io')
    atualizado_em = models.DateTimeField(auto_now=True)
    criado_em     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-atualizado_em"]

    def __str__(self):
        return f"{self.nome} — {self.cliente.nome_empresa}"

    

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


class VPNServidorConfig(models.Model):
    """Configuração global do servidor WireGuard (singleton)."""
    servidor_public_key  = models.CharField(max_length=200)
    servidor_private_key = models.CharField(max_length=200)
    servidor_endpoint    = models.CharField(max_length=200, help_text='IP ou hostname público do servidor')
    servidor_porta       = models.IntegerField(default=51820)
    interface_criada     = models.BooleanField(default=False)
    criado_em            = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Configuração Servidor VPN'

    def __str__(self):
        return f'VPN Server — {self.servidor_endpoint}:{self.servidor_porta}'


class VPNWireGuard(models.Model):
    """VPN WireGuard por cliente (MikroTik)."""
    cliente             = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='vpns_wg')
    nome                = models.CharField(max_length=100, default='VPN MikroTik')
    cliente_private_key = models.CharField(max_length=200)
    cliente_public_key  = models.CharField(max_length=200)
    preshared_key       = models.CharField(max_length=200, blank=True)
    vpn_ip              = models.GenericIPAddressField(unique=True)
    redes_privadas      = models.TextField(blank=True, help_text='Uma rede por linha, ex: 192.168.1.0/24')
    ativo               = models.BooleanField(default=True)
    peer_no_servidor    = models.BooleanField(default=False, help_text='Peer adicionado ao wg0')
    criado_em           = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'VPN WireGuard'
        verbose_name_plural = 'VPNs WireGuard'
        ordering = ['-criado_em']

    def __str__(self):
        return f'{self.nome} — {self.cliente.nome_empresa} ({self.vpn_ip})'

    def redes_lista(self):
        import re
        return [r.strip() for r in re.split(r'[,\n]+', self.redes_privadas) if r.strip()]


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
        ('SEM_MUDANCAS', 'Sem mudanças'),
    ]

    acesso = models.ForeignKey('Acesso', on_delete=models.CASCADE, related_name='backups')
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='backups')
    template = models.ForeignKey('BackupTemplate', on_delete=models.SET_NULL, null=True, blank=True)

    arquivo_path = models.CharField(max_length=500, blank=True, default='')
    tamanho_bytes = models.IntegerField(default=0)
    hash_conteudo = models.CharField(max_length=64, blank=True, default='', verbose_name='Hash SHA-256')

    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='SUCESSO')
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


# =============================================================================
# IPAM — Documentação Nativa de IPs, VLANs, Sub-redes e VPNs
# =============================================================================

class IPAMVlan(models.Model):
    STATUS = [('ativo','Ativo'),('reservado','Reservado'),('deprecado','Deprecado')]

    cliente   = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='ipam_vlans')
    numero    = models.PositiveIntegerField()
    nome      = models.CharField(max_length=100)
    descricao = models.TextField(blank=True)
    status    = models.CharField(max_length=15, choices=STATUS, default='ativo')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('cliente', 'numero')
        ordering = ['numero']

    def __str__(self):
        return f'VLAN {self.numero} — {self.nome}'


class IPAMPrefixo(models.Model):
    TIPO   = [('container','Container'),('rede','Rede'),('pool','Pool')]
    STATUS = [('ativo','Ativo'),('reservado','Reservado'),('deprecado','Deprecado')]

    cliente      = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='ipam_prefixos')
    prefixo      = models.CharField(max_length=50)          # CIDR ex: 10.0.0.0/8
    tipo         = models.CharField(max_length=15, choices=TIPO,   default='rede')
    status       = models.CharField(max_length=15, choices=STATUS, default='ativo')
    descricao    = models.TextField(blank=True)
    local        = models.CharField(max_length=150, blank=True)
    pool_cheia   = models.BooleanField(default=False)
    criado_em    = models.DateTimeField(auto_now_add=True)
    atualizado_em= models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['prefixo']

    def __str__(self):
        return self.prefixo


class IPAMSubRede(models.Model):
    STATUS = [('ativo','Ativo'),('reservado','Reservado'),('deprecado','Deprecado')]

    cliente      = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='ipam_subredes')
    prefixo      = models.ForeignKey(IPAMPrefixo, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='subredes')
    vlan         = models.ForeignKey(IPAMVlan, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='subredes')
    rede         = models.CharField(max_length=50)           # CIDR ex: 192.168.1.0/24
    gateway      = models.CharField(max_length=45, blank=True)
    descricao    = models.TextField(blank=True)
    local        = models.CharField(max_length=150, blank=True)
    status       = models.CharField(max_length=15, choices=STATUS, default='ativo')
    criado_em    = models.DateTimeField(auto_now_add=True)
    atualizado_em= models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['rede']

    def __str__(self):
        return self.rede

    def total_hosts(self):
        import ipaddress
        try:
            return ipaddress.ip_network(self.rede, strict=False).num_addresses
        except Exception:
            return 0

    def usados(self):
        return self.ips.count()

    def utilizacao_pct(self):
        total = self.total_hosts()
        if total == 0:
            return 0
        return round(self.usados() / total * 100, 1)


class IPAMEndereco(models.Model):
    TIPO   = [('fixo','Fixo'),('dhcp','DHCP'),('reservado','Reservado'),
              ('gateway','Gateway'),('rede','Endereço de Rede'),('broadcast','Broadcast')]
    STATUS = [('ativo','Ativo'),('inativo','Inativo'),('reservado','Reservado')]

    cliente     = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='ipam_ips')
    subrede     = models.ForeignKey(IPAMSubRede, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='ips')
    ip          = models.CharField(max_length=45)
    tipo        = models.CharField(max_length=15, choices=TIPO,   default='fixo')
    status      = models.CharField(max_length=15, choices=STATUS, default='ativo')
    hostname    = models.CharField(max_length=255, blank=True)
    descricao   = models.TextField(blank=True)
    mac_address = models.CharField(max_length=20, blank=True)
    acesso      = models.ForeignKey('Acesso', null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='ipam_enderecos')
    criado_em   = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['ip']

    def __str__(self):
        return self.ip


class IPAMVpnDoc(models.Model):
    TIPO   = [('ipsec','IPSec'),('gre','GRE'),('l2tp','L2TP'),
              ('mpls','MPLS/L3VPN'),('wireguard','WireGuard'),
              ('openvpn','OpenVPN'),('outro','Outro')]
    STATUS = [('ativo','Ativo'),('inativo','Inativo')]

    cliente         = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='ipam_vpns')
    nome            = models.CharField(max_length=150)
    tipo            = models.CharField(max_length=15, choices=TIPO, default='ipsec')
    endpoint_local  = models.CharField(max_length=100, blank=True)
    endpoint_remoto = models.CharField(max_length=100, blank=True)
    rede_local      = models.CharField(max_length=200, blank=True)
    rede_remota     = models.CharField(max_length=200, blank=True)
    as_local        = models.CharField(max_length=20, blank=True)
    as_remoto       = models.CharField(max_length=20, blank=True)
    descricao       = models.TextField(blank=True)
    status          = models.CharField(max_length=10, choices=STATUS, default='ativo')
    criado_em       = models.DateTimeField(auto_now_add=True)
    atualizado_em   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nome']

    def __str__(self):
        return f'{self.nome} ({self.get_tipo_display()})'


# ── OpenVPN Server — configuração automatizada ──────────────────────────────
class OpenVPNConfig(models.Model):
    STATUS = [
        ('configurando', 'Configurando'),
        ('concluido',    'Concluído'),
        ('erro',         'Erro'),
    ]
    ROS = [('6', 'v6'), ('7', 'v7')]

    cliente         = models.ForeignKey(Cliente, on_delete=models.CASCADE,
                                        related_name='openvpn_configs')
    acesso          = models.ForeignKey('Acesso', on_delete=models.SET_NULL,
                                        null=True, related_name='openvpn_configs')
    nome_vpn        = models.CharField(max_length=60)       # nome do cert e usuário
    ip_publico      = models.CharField(max_length=45)
    porta           = models.IntegerField(default=61194)
    ros_version     = models.CharField(max_length=2, choices=ROS, default='7')
    vpn_pool        = models.CharField(max_length=100,
                                       default='192.168.250.128-192.168.250.254')
    vpn_local_ip    = models.CharField(max_length=45, default='192.168.250.1')
    vpn_username    = models.CharField(max_length=100)
    vpn_password    = models.CharField(max_length=100)
    cert_passphrase = models.CharField(max_length=100)
    rate_limit      = models.CharField(max_length=50, default='50M/50M')
    ovpn_path       = models.CharField(max_length=500, blank=True)
    status          = models.CharField(max_length=20, choices=STATUS,
                                       default='configurando')
    logs            = models.TextField(blank=True)
    erro_msg        = models.TextField(blank=True)
    criado_em       = models.DateTimeField(auto_now_add=True)
    atualizado_em   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-criado_em']

    def __str__(self):
        return f'{self.nome_vpn} ({self.cliente})'


class OpenVPNUsuario(models.Model):
    STATUS = [
        ('configurando', 'Configurando'),
        ('concluido',    'Concluído'),
        ('erro',         'Erro'),
    ]
    config      = models.ForeignKey(OpenVPNConfig, on_delete=models.CASCADE,
                                    related_name='usuarios')
    nome        = models.CharField(max_length=60)      # nome do cert / display
    username    = models.CharField(max_length=100)
    password    = models.CharField(max_length=100)
    ovpn_path   = models.CharField(max_length=500, blank=True)
    status      = models.CharField(max_length=20, choices=STATUS, default='configurando')
    logs        = models.TextField(blank=True)
    erro_msg    = models.TextField(blank=True)
    criado_em   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-criado_em']

    def __str__(self):
        return f'{self.username} → {self.config.nome_vpn}'


# ── Configuração Global do Sistema ────────────────────────────────────────────

class ConfiguracaoSistema(models.Model):
    """Singleton — configurações globais do sistema (SMTP, IMAP, etc.)."""
    smtp_host    = models.CharField(max_length=200, blank=True, verbose_name='Host SMTP')
    smtp_port    = models.IntegerField(default=587,  verbose_name='Porta SMTP')
    smtp_user    = models.CharField(max_length=200, blank=True, verbose_name='Usuário SMTP')
    smtp_pass    = models.CharField(max_length=200, blank=True, verbose_name='Senha SMTP')
    smtp_from    = models.EmailField(blank=True,    verbose_name='E-mail Remetente')
    smtp_use_tls = models.BooleanField(default=True, verbose_name='Usar TLS (STARTTLS)')

    # IMAP — para verificar respostas do TC após envio de atualização IRR
    imap_host    = models.CharField(max_length=200, blank=True, verbose_name='Host IMAP')
    imap_port    = models.IntegerField(default=993,  verbose_name='Porta IMAP')
    imap_use_ssl = models.BooleanField(default=True, verbose_name='Usar SSL (IMAP)')

    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração do Sistema'

    def __str__(self):
        return 'Configuração do Sistema'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ── IRR Config ────────────────────────────────────────────────────────────────

class IRRConfig(models.Model):
    """Configuração IRR por cliente — usada para gerar e enviar atualização ao TC via e-mail."""
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, related_name='irr_config')

    # Identificadores principais
    asn            = models.CharField(max_length=20, help_text='Número do AS sem prefixo. Ex: 272418')
    as_name        = models.CharField(max_length=100, help_text='Nome curto do AS. Ex: INFORLIMA')
    empresa_descr  = models.CharField(max_length=200, help_text='Descrição completa. Ex: INFORLIMA TELECOM')

    # Maintainer
    nic_hdl        = models.CharField(max_length=80, help_text='Handle NIC. Ex: JOLJE19-NICBR')
    irr_password   = models.CharField(max_length=200, help_text='Senha plaintext enviada no e-mail IRR')
    auth_bcrypt    = models.CharField(max_length=400, blank=True, help_text='Hash BCRYPT-PW do mntner')

    # Contato
    email_contato  = models.EmailField(help_text='E-mail do responsável técnico (changed, upd-to, notify)')
    email_abuse    = models.EmailField(blank=True, help_text='E-mail de abuse/rede (remarks)')
    website        = models.CharField(max_length=300, blank=True)

    # Person object
    person_name    = models.CharField(max_length=200)
    address        = models.TextField()
    phone          = models.CharField(max_length=50)

    # Prefixos e rotas (listas JSON)
    ipv4_rotas           = models.JSONField(default=list, blank=True, help_text='Lista de prefixos IPv4. Ex: ["186.65.76.0/22"]')
    ipv6_rotas           = models.JSONField(default=list, blank=True, help_text='Lista de prefixos IPv6. Ex: ["2804:80E0::/32"]')
    route_set_members    = models.JSONField(default=list, blank=True, help_text='mp-members do route-set com range. Ex: ["201.7.168.0/21^21-24"]')

    # AS-sets
    upstream_asns  = models.JSONField(default=list, blank=True, help_text='Lista [{"asn":"AS52554","nome":"MEGASNET"}]')
    customer_asns  = models.JSONField(default=list, blank=True, help_text='Lista [{"asn":"AS268024","nome":""}]')
    ix_members     = models.JSONField(default=list, blank=True, help_text='Lista de member-of (IX). Ex: ["AS-PTTMetro-SP","AS65001:AS-ANNOUNCEMENTS"]')

    # Geo (usado nos objetos route/route6)
    geo_pais       = models.CharField(max_length=5,   default='BR')
    geo_pais_alpha3= models.CharField(max_length=5,   default='BRA')
    geo_pais_num   = models.CharField(max_length=5,   default='076')
    geo_estado     = models.CharField(max_length=20,  blank=True)
    geo_cidade     = models.CharField(max_length=100, blank=True)

    criado_em      = models.DateTimeField(auto_now_add=True)
    atualizado_em  = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'IRR AS{self.asn} — {self.cliente.nome_empresa}'

    @property
    def mntner(self):
        return f'MAINT-AS{self.asn}'

    @property
    def as_full(self):
        return f'AS{self.asn}'


# ── Auto-create default IPAM prefixes for new clients ──────────────────────
_DEFAULT_PREFIXOS = [
    ('100.64.0.0/10', 'container', 'CGNAT / Carrier-grade NAT'),
    ('192.168.0.0/16', 'rede',      'Redes privadas RFC1918'),
    ('10.0.0.0/8',     'rede',      'Redes privadas RFC1918'),
    ('172.16.0.0/12',  'rede',      'Redes privadas RFC1918'),
    ('198.18.0.0/15',  'rede',      'Testes de benchmark RFC2544'),
    ('fc00::/7',       'rede',      'Endereços locais únicos IPv6 (ULA) RFC4193'),
]

@receiver(post_save, sender=Cliente)
def criar_prefixos_padrao(sender, instance, created, **kwargs):
    if not created:
        return
    existentes = set(IPAMPrefixo.objects.filter(cliente=instance).values_list('prefixo', flat=True))
    for cidr, tipo, descricao in _DEFAULT_PREFIXOS:
        if cidr not in existentes:
            IPAMPrefixo.objects.create(
                cliente=instance,
                prefixo=cidr,
                tipo=tipo,
                status='ativo',
                descricao=descricao,
            )
    


# ── Gerenciador de Firmware / Arquivos ──────────────────────────────────────
import os, secrets, string
from django.utils import timezone

class FirmwarePasta(models.Model):
    nome     = models.CharField(max_length=255)
    pai      = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='subpastas')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nome']

    def __str__(self):
        return self.caminho_completo

    @property
    def caminho_completo(self):
        partes = [self.nome]
        p = self.pai
        while p:
            partes.insert(0, p.nome)
            p = p.pai
        return '/'.join(partes)

    @property
    def caminho_fs(self):
        from django.conf import settings
        return os.path.join(settings.MEDIA_ROOT, 'firmware', self.caminho_completo)


def firmware_upload_path(instance, filename):
    pasta_path = instance.pasta.caminho_completo if instance.pasta else ''
    return os.path.join('firmware', pasta_path, filename)


class FirmwareArquivo(models.Model):
    nome       = models.CharField(max_length=500)
    arquivo    = models.FileField(upload_to=firmware_upload_path)
    tamanho    = models.BigIntegerField(default=0)
    mime_type  = models.CharField(max_length=200, blank=True)
    pasta      = models.ForeignKey(FirmwarePasta, null=True, blank=True, on_delete=models.SET_NULL, related_name='arquivos')
    criado_em  = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['nome']

    def __str__(self):
        return self.nome

    def tamanho_legivel(self):
        b = self.tamanho
        for u in ('B', 'KB', 'MB', 'GB'):
            if b < 1024:
                return f'{b:.1f} {u}'
            b /= 1024
        return f'{b:.1f} TB'

    @property
    def caminho_relativo(self):
        pasta = self.pasta.caminho_completo + '/' if self.pasta else ''
        return pasta + self.nome


class FirmwareCompartilhamento(models.Model):
    arquivo    = models.ForeignKey(FirmwareArquivo, on_delete=models.CASCADE, related_name='compartilhamentos')
    token      = models.CharField(max_length=64, unique=True)
    expira_em  = models.DateTimeField()
    ftp_user   = models.CharField(max_length=50, blank=True)
    ftp_senha  = models.CharField(max_length=50, blank=True)
    criado_em  = models.DateTimeField(auto_now_add=True)
    acessos    = models.IntegerField(default=0)

    class Meta:
        ordering = ['-criado_em']

    @property
    def valido(self):
        return timezone.now() < self.expira_em

    @staticmethod
    def gerar_token():
        return secrets.token_urlsafe(32)

    @staticmethod
    def gerar_credenciais():
        chars = string.ascii_letters + string.digits
        user  = 'fw_' + ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6))
        senha = ''.join(secrets.choice(chars) for _ in range(12))
        return user, senha


# ── Correções de Geolocalização IP ────────────────────────────────────────────

class CorrecaoGeoIP(models.Model):
    """Registro de solicitações de correção de geolocalização enviadas a RIRs."""

    prefixo    = models.CharField(max_length=50,  db_index=True, verbose_name='Prefixo / IP')
    pais       = models.CharField(max_length=5,   blank=True, verbose_name='País (ISO)')
    regiao     = models.CharField(max_length=100, blank=True, verbose_name='Estado / Região')
    cidade     = models.CharField(max_length=100, blank=True, verbose_name='Cidade')
    org        = models.CharField(max_length=200, blank=True, verbose_name='Organização / ISP')
    lat        = models.CharField(max_length=20,  blank=True, verbose_name='Latitude')
    lon        = models.CharField(max_length=20,  blank=True, verbose_name='Longitude')

    # Destinos que receberam e-mail: [{'label':'LACNIC','email':'hostmaster@lacnic.net'}, ...]
    destinos_email = models.JSONField(default=list, verbose_name='Destinos (e-mail)')

    data_envio   = models.DateTimeField(auto_now_add=True, verbose_name='Data de envio')
    solicitante  = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='correcoes_geoip', verbose_name='Solicitante',
    )

    # ── Resposta IMAP ────────────────────────────────────────────────────────
    resposta_recebida  = models.BooleanField(default=False, verbose_name='Resposta recebida')
    resposta_remetente = models.CharField(max_length=200, blank=True, verbose_name='Remetente da resposta')
    resposta_assunto   = models.CharField(max_length=300, blank=True, verbose_name='Assunto da resposta')
    resposta_texto     = models.TextField(blank=True, verbose_name='Corpo da resposta')
    data_resposta      = models.DateTimeField(null=True, blank=True, verbose_name='Data da resposta')

    # ── Verificação de aplicação ─────────────────────────────────────────────
    aplicado              = models.BooleanField(null=True, blank=True, verbose_name='Correção aplicada')
    ultima_verificacao    = models.DateTimeField(null=True, blank=True, verbose_name='Última verificação')
    resultado_verificacao = models.JSONField(null=True, blank=True, verbose_name='Resultado da verificação')
    fontes_aplicadas      = models.IntegerField(default=0, verbose_name='Fontes que aplicaram')
    fontes_total          = models.IntegerField(default=0, verbose_name='Total de fontes verificadas')

    class Meta:
        ordering = ['-data_envio']
        verbose_name = 'Correção GeoIP'
        verbose_name_plural = 'Correções GeoIP'

    def __str__(self):
        return f'{self.prefixo} – {self.data_envio:%d/%m/%Y %H:%M}'

    @property
    def status_resposta(self):
        if self.resposta_recebida:
            return 'respondido'
        return 'aguardando'

    @property
    def status_aplicacao(self):
        if self.aplicado is None:
            return 'nao_verificado'
        if self.aplicado:
            return 'aplicado'
        return 'pendente'


# ── Scripts de Automação ──────────────────────────────────────────────────────

from django.conf import settings as _dj_settings


class ScriptCRM(models.Model):
    """Script de automação com parâmetros dinâmicos para execução em equipamentos via SSH"""

    FABRICANTES = [
        ('zte',       'ZTE'),
        ('huawei',    'Huawei'),
        ('cisco',     'Cisco'),
        ('mikrotik',  'MikroTik'),
        ('datacom',   'Datacom'),
        ('parks',     'Parks'),
        ('generico',  'Genérico'),
    ]

    MODOS = [
        ('operacional',    'Operacional (show/get)'),
        ('configuracao',   'Configuração (config)'),
        ('zte_auto_prov',  'ZTE — Auto-Provisionamento em Massa'),
    ]

    nome           = models.CharField(max_length=255, verbose_name='Nome')
    descricao      = models.TextField(blank=True, verbose_name='Descrição')
    fabricante     = models.CharField(max_length=30, choices=FABRICANTES, default='generico', verbose_name='Fabricante')
    modo_execucao  = models.CharField(max_length=20, choices=MODOS, default='operacional', verbose_name='Modo')
    comandos       = models.TextField(verbose_name='Comandos', help_text='Um comando por linha. Use {PARAM} para variáveis. Suporte a #FOR i FROM {X} TO {Y} ... #ENDFOR')
    parametros     = models.JSONField(default=list, verbose_name='Parâmetros', help_text='Lista de parâmetros: [{nome, label, tipo, default, obrigatorio, ajuda, opcoes}]')
    ativo          = models.BooleanField(default=True, verbose_name='Ativo')
    criado_por     = models.ForeignKey(_dj_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='scripts_criados')
    criado_em      = models.DateTimeField(auto_now_add=True)
    atualizado_em  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['fabricante', 'nome']
        verbose_name = 'Script de Automação'
        verbose_name_plural = 'Scripts de Automação'

    def __str__(self):
        return f"[{self.get_fabricante_display()}] {self.nome}"


class ScriptExecucaoLog(models.Model):
    """Histórico de execuções de scripts"""

    STATUS = [
        ('executando', 'Executando'),
        ('sucesso',    'Sucesso'),
        ('erro',       'Erro'),
        ('parcial',    'Parcial'),
    ]

    script            = models.ForeignKey(ScriptCRM, on_delete=models.SET_NULL, null=True, related_name='execucoes')
    acesso            = models.ForeignKey('Acesso', on_delete=models.SET_NULL, null=True, related_name='script_execucoes')
    usuario           = models.ForeignKey(_dj_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    parametros_usados = models.JSONField(default=dict)
    output            = models.TextField(blank=True)
    status            = models.CharField(max_length=20, choices=STATUS, default='executando')
    iniciado_em       = models.DateTimeField(auto_now_add=True)
    finalizado_em     = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-iniciado_em']
        verbose_name = 'Log de Execução de Script'
        verbose_name_plural = 'Logs de Execução de Scripts'

    def __str__(self):
        return f"Execução #{self.id} — {self.script} [{self.status}]"
