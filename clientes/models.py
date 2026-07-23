import logging
import uuid as _uuid_mod

from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.utils import timezone
from funcao_equipamento.models import Funcao_equipamento
from modelo_equipamento.models import Modelo_equipamento

class ClienteManager(models.Manager):
    """
    Um usuário pode acessar o painel do cliente como `usuario` (principal,
    obrigatório) ou como um dos `usuarios_adicionais` (opcional). Os métodos
    abaixo centralizam essa checagem para manter o mesmo contrato de
    `Cliente.objects.get(usuario=x)` / `.filter(usuario=x)` usado em dezenas
    de views — assim quem já chamava `.get()` continua recebendo o cliente
    ou `Cliente.DoesNotExist`, sem precisar saber que agora existem 2 vínculos.
    """

    def get_by_usuario_vinculado(self, user):
        cliente = self.filter(
            models.Q(usuario=user) | models.Q(usuarios_adicionais=user)
        ).distinct().first()
        if cliente is None:
            raise self.model.DoesNotExist(
                f"Cliente matching query does not exist for usuario={user!r}"
            )
        return cliente

    def filter_by_usuario_vinculado(self, user):
        return self.filter(
            models.Q(usuario=user) | models.Q(usuarios_adicionais=user)
        ).distinct()


# Extensão do User para armazenar dados específicos do cliente
class Cliente(models.Model):
    # Login do cliente no portal — opcional: um cliente pode ser cadastrado
    # e gerenciado só pela equipe interna, sem acesso próprio ao portal,
    # e vinculado a um usuário depois.
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    usuarios_adicionais = models.ManyToManyField(
        User, related_name='clientes_adicionais', blank=True,
        verbose_name='Usuários adicionais',
        help_text='Outros usuários que também podem logar e acessar o painel deste cliente.'
    )
    nome_empresa = models.CharField(max_length=255)
    cnpj = models.CharField(max_length=18, unique=True)
    endereco = models.CharField(max_length=200)
    telefone = models.CharField(max_length=15,blank=True, null=True)
    email = models.EmailField(unique=True)
    cep = models.CharField(max_length=10, blank=True, null=True)
    cidade = models.CharField(max_length=100, blank=True, null=True)
    data_criacao = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=255, blank=True, null=True)
    notas = models.TextField(
        blank=True, default='',
        verbose_name='Notas do Agent NOC',
        help_text='Informações, peculiaridades e contexto do cliente para o Agent NOC (topologia, acordos, restrições).'
    )

    objects = ClienteManager()

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
    notas = models.TextField(
        blank=True, default='',
        verbose_name='Notas do Agent NOC',
        help_text='Notas sobre este host para o Agent NOC (senha ADM, restrições de horário, comportamentos especiais).'
    )
    contexto_backup = models.TextField(
        blank=True, default='',
        verbose_name='Contexto do Backup (Agent NOC)',
        help_text='Resumo de interfaces, IPs e VLANs extraído do último backup — usado pelo Agent NOC'
    )
    contexto_backup_em = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Contexto atualizado em'
    )
    modelo_auto_em = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Modelo auto-detectado em',
        help_text='Última vez que a task tentou detectar o modelo via backup'
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


class AcessoSessao(models.Model):
    """Auditoria: uma sessão de acesso (SSH/Telnet/WinBox) a um host, com
    o usuário do CRM que conectou, não o usuário/senha do equipamento."""

    TIPOS = [
        ('ssh', 'SSH'),
        ('telnet', 'Telnet'),
        ('winbox', 'WinBox Web'),
        ('winbox_nativo', 'WinBox Nativo'),
        ('webfig', 'WebFig'),
    ]
    STATUS = [
        ('ativa', 'Ativa'),
        ('encerrada', 'Encerrada'),
    ]

    acesso        = models.ForeignKey('Acesso', on_delete=models.CASCADE, related_name='sessoes_auditoria')
    usuario       = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='sessoes_acesso')
    tipo          = models.CharField(max_length=20, choices=TIPOS)
    ip_origem     = models.GenericIPAddressField(null=True, blank=True)
    status        = models.CharField(max_length=20, choices=STATUS, default='ativa')
    iniciada_em   = models.DateTimeField(auto_now_add=True)
    encerrada_em  = models.DateTimeField(null=True, blank=True)
    # Caminho relativo a MEDIA_ROOT do .mp4 gravado (só winbox/webfig via VNC)
    arquivo_video = models.CharField(max_length=500, blank=True, default='')
    # Transcript completo da tela (stdout, ANSI removido) — só ssh/telnet.
    # Complementa AcessoComando: mostra o comando expandido/completo tal como
    # o equipamento ecoou, incluindo o que ajuda contextual ('?')/tab
    # completion revelam e que não dá pra saber só pelo que foi digitado.
    transcript    = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Sessão de Acesso (Auditoria)'
        verbose_name_plural = 'Sessões de Acesso (Auditoria)'
        ordering = ['-iniciada_em']

    def __str__(self):
        quem = self.usuario.get_username() if self.usuario else '?'
        return f'[{self.tipo}] {quem} → {self.acesso} em {self.iniciada_em:%d/%m/%Y %H:%M}'

    @property
    def duracao_segundos(self):
        fim = self.encerrada_em or timezone.now()
        return int((fim - self.iniciada_em).total_seconds())


class AcessoComando(models.Model):
    """Um comando digitado (stdin) numa AcessoSessao SSH/Telnet."""

    sessao       = models.ForeignKey(AcessoSessao, on_delete=models.CASCADE, related_name='comandos')
    comando      = models.TextField()
    executado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Comando de Sessão (Auditoria)'
        verbose_name_plural = 'Comandos de Sessão (Auditoria)'
        ordering = ['executado_em']

    def __str__(self):
        return self.comando[:80]


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
    # Interface isolada: cada cliente tem sua própria interface wg com routing table dedicada
    interface_nome      = models.CharField(max_length=20, blank=True, default='wg0',
                          help_text='Interface WireGuard (ex: wg1, wg2). wg0=legado compartilhado')
    servidor_ip_local   = models.CharField(max_length=45, blank=True, default='',
                          help_text='IP do servidor nesta interface (ex: 10.201.0.1). '
                                    'Usado como source-bind para routing isolado.')
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


def _ovpn_redes_padrao():
    return (
        '100.64.0.0/10\n'
        '172.16.0.0/12\n'
        '10.0.0.0/8\n'
        '192.168.0.0/16\n'
        '198.18.0.0/15'
    )


def _ovpn_token_default():
    import secrets
    return secrets.token_urlsafe(32)


class VPNOpenVPN(models.Model):
    """Túnel OpenVPN por cliente (MikroTik) — cada túnel roda em sua PRÓPRIA
    instância de servidor (porta/interface/sub-rede dedicadas), igual ao
    modelo já usado pelo WireGuard (VPNWireGuard.interface_nome). Isso evita
    que dois clientes com as mesmas redes "alcançáveis" (o padrão CGNAT+
    RFC1918) tenham tráfego roteado para o cliente errado quando ambos estão
    conectados ao mesmo tempo — cada instância só aceita e só serve UM
    cliente. Ver clientes/openvpn_tunnel_manager.py."""
    cliente        = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='vpns_ovpn')
    nome           = models.CharField(max_length=100, default='VPN MikroTik')
    common_name    = models.CharField(max_length=100, unique=True, help_text='CN do certificado do cliente')
    redes_privadas = models.TextField(blank=True, default=_ovpn_redes_padrao,
                          help_text='Uma rede por linha, ex: 192.168.1.0/24')
    vpn_ip         = models.GenericIPAddressField(unique=True, help_text='IP do cliente no túnel (10.91.N.2)')
    porta          = models.IntegerField(unique=True, null=True, blank=True,
                          help_text='Porta TCP dedicada desta instância (1195+N)')
    interface_nome = models.CharField(max_length=30, blank=True, default='',
                          help_text='Nome da instância/interface dedicada (ex: server-crm-1)')
    subnet_n       = models.IntegerField(null=True, blank=True,
                          help_text='N usado para compor a sub-rede dedicada 10.91.{N}.0/30')
    token          = models.CharField(max_length=64, unique=True, default=_ovpn_token_default,
                          help_text='Token do endpoint público de bootstrap — regenerar invalida o link antigo')
    cert_emitido   = models.BooleanField(default=False)
    ativo          = models.BooleanField(default=True)
    criado_em      = models.DateTimeField(auto_now_add=True)
    atualizado_em  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'VPN OpenVPN'
        verbose_name_plural = 'VPNs OpenVPN'
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
    ultima_verificacao = models.DateTimeField(null=True, blank=True, verbose_name='Última verificação sem mudanças')
    
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
    # Prefixo container mais específico que contém este (calculado por CIDR em
    # _computar_pai_prefixo, ipam_views.py) — persiste a árvore em vez de
    # recalculá-la a cada request, permitindo navegação hierárquica na UI.
    pai          = models.ForeignKey('self', null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='filhos')
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
    pool_cheia   = models.BooleanField(default=False)
    # Scan automático de disponibilidade (ping em lote) — opt-in por sub-rede
    # pra não varrer a rede de todo cliente sem necessidade.
    scan_automatico = models.BooleanField(default=False)
    ultimo_scan     = models.DateTimeField(null=True, blank=True)
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
                                    on_delete=models.CASCADE, related_name='ips')
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


class IPAMScanResultado(models.Model):
    """
    Resultado do último ping (scan em lote) de um IP dentro do cliente.
    Existe independente de haver um IPAMEndereco cadastrado — é o que permite
    a grade visual mostrar "host respondendo mas não documentado" (descoberta,
    como o scan de sub-rede do phpIPAM).
    """
    cliente    = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='ipam_scan_resultados')
    ip         = models.CharField(max_length=45, db_index=True)
    online     = models.BooleanField(default=False)
    checado_em = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('cliente', 'ip')

    def __str__(self):
        return f'{self.ip} ({"online" if self.online else "offline"})'


class IPAMAuditLog(models.Model):
    """Histórico de alterações nos objetos do IPAM — quem mudou o quê."""
    MODELO  = [('vlan','VLAN'),('prefixo','Prefixo'),('subrede','Sub-rede'),
               ('ip','Endereço IP'),('vpn','VPN')]
    ACAO    = [('created','Criado'),('updated','Atualizado'),('deleted','Removido')]

    cliente     = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='ipam_audit_logs')
    modelo      = models.CharField(max_length=15, choices=MODELO)
    objeto_id   = models.IntegerField()
    # Snapshot do __str__ do objeto — continua legível mesmo se o registro for apagado depois.
    objeto_repr = models.CharField(max_length=255, blank=True)
    acao        = models.CharField(max_length=10, choices=ACAO)
    # {"campo": {"antes": x, "depois": y}} — só os campos que mudaram (updated),
    # ou snapshot relevante (created/deleted).
    mudancas    = models.JSONField(default=dict, blank=True)
    usuario     = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    criado_em   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-criado_em']

    def __str__(self):
        return f'{self.get_acao_display()} {self.get_modelo_display()} #{self.objeto_id}'


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


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT NOC TOMICH
# ═══════════════════════════════════════════════════════════════════════════════

class AgentConfig(models.Model):
    """Singleton — configuração global do Agent NOC."""

    PROVEDORES = [
        ('claude',  'Claude (Anthropic)'),
        ('openai',  'ChatGPT (OpenAI)'),
    ]

    # Provedor ativo
    provedor_ia         = models.CharField(max_length=20, choices=PROVEDORES, default='claude', verbose_name='Provedor de IA')

    # Claude API
    claude_api_key      = models.CharField(max_length=300, blank=True, verbose_name='API Key Claude')
    claude_model        = models.CharField(max_length=100, default='claude-sonnet-4-6', verbose_name='Modelo Claude')
    claude_max_tokens   = models.IntegerField(default=4096, verbose_name='Max Tokens')
    claude_temperature  = models.FloatField(default=0.2, verbose_name='Temperature')

    # OpenAI API
    openai_api_key      = models.CharField(max_length=300, blank=True, verbose_name='API Key OpenAI')
    openai_model        = models.CharField(max_length=100, default='gpt-4o', verbose_name='Modelo OpenAI')
    openai_max_tokens   = models.IntegerField(default=4096, verbose_name='Max Tokens OpenAI')
    openai_temperature  = models.FloatField(default=0.2, verbose_name='Temperature OpenAI')

    # Comportamento
    aprovacao_padrao    = models.BooleanField(default=True, verbose_name='Exigir aprovação para comandos de escrita')
    timeout_sessao_wa   = models.IntegerField(default=120, verbose_name='Timeout sessão WhatsApp (min)')
    prefixo_wa          = models.CharField(max_length=20, default='@noc', verbose_name='Prefixo de invocação WA')
    max_comandos_sessao = models.IntegerField(default=50, verbose_name='Máx. comandos por sessão')

    # Escalonamento
    wa_grupo_noc        = models.CharField(max_length=150, blank=True, verbose_name='JID grupo NOC interno (escalonamento)')
    wa_noc_numero       = models.CharField(max_length=30, blank=True, verbose_name='Número plantão NOC (escalonamento)')

    ativo               = models.BooleanField(default=True)
    atualizado_em       = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração Agent NOC'

    def __str__(self):
        return 'Configuração Agent NOC'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class EvolutionAPIConfig(models.Model):
    """Singleton — conexão com a Evolution API (WhatsApp)."""

    url             = models.CharField(max_length=300, blank=True, verbose_name='URL da API')
    api_key         = models.CharField(max_length=300, blank=True, verbose_name='API Key')
    instance_name   = models.CharField(max_length=100, blank=True, verbose_name='Nome da Instância')
    webhook_secret  = models.CharField(max_length=300, blank=True, verbose_name='Webhook Secret')

    # Status (atualizado na sincronização)
    conectado       = models.BooleanField(default=False, verbose_name='Conectado')
    numero_wa       = models.CharField(max_length=30, blank=True, verbose_name='Número WhatsApp conectado')
    ultima_sync     = models.DateTimeField(null=True, blank=True, verbose_name='Última sincronização')

    atualizado_em   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração Evolution API'

    def __str__(self):
        return f'Evolution API — {self.instance_name or "não configurado"}'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class WhatsAppGrupo(models.Model):
    """Grupo ou contato do WhatsApp vinculado a um cliente da plataforma."""

    NIVEIS = [
        ('leitura',     'Leitura — apenas show/display/get'),
        ('operacional', 'Operacional — comandos pré-aprovados'),
        ('admin',       'Admin — aprovação inline via WA'),
    ]
    TIPOS = [
        ('grupo',   'Grupo'),
        ('contato', 'Contato individual'),
    ]

    # Identificação no WhatsApp
    jid             = models.CharField(max_length=150, unique=True, verbose_name='JID (ID WhatsApp)')
    nome            = models.CharField(max_length=300, verbose_name='Nome')
    tipo            = models.CharField(max_length=10, choices=TIPOS, default='grupo', verbose_name='Tipo')
    foto_url        = models.URLField(blank=True, verbose_name='URL da foto')

    # Vínculo com o cliente
    cliente         = models.ForeignKey(
        Cliente, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='wa_grupos', verbose_name='Cliente vinculado',
        help_text='Grupo só poderá acessar hosts deste cliente'
    )
    nivel_permissao = models.CharField(max_length=20, choices=NIVEIS, default='leitura', verbose_name='Nível de permissão')
    acesso_global   = models.BooleanField(
        default=False,
        verbose_name='Acesso Global',
        help_text='Permite que este grupo acesse hosts de qualquer cliente da plataforma. Use apenas em grupos NOC internos.',
    )

    # Restrição adicional de hosts (vazio = todos os hosts do cliente)
    hosts_permitidos = models.ManyToManyField(
        'Acesso', blank=True,
        related_name='wa_grupos_permitidos',
        verbose_name='Hosts permitidos',
        help_text='Vazio = todos os hosts do cliente vinculado'
    )

    # API Key individual do cliente (Agent NOC)
    claude_api_key  = models.CharField(
        max_length=300, blank=True, verbose_name='API Key Claude do cliente',
        help_text='Chave Anthropic própria deste grupo/cliente — consome os créditos dele. '
                   'O Agent NOC só responde neste grupo se esta chave estiver configurada.',
    )

    # Estado
    ativo           = models.BooleanField(default=True, verbose_name='Ativo')
    sincronizado_em = models.DateTimeField(auto_now=True, verbose_name='Última sincronização')
    criado_em       = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Grupo/Contato WhatsApp'
        verbose_name_plural = 'Grupos/Contatos WhatsApp'
        ordering = ['nome']

    def __str__(self):
        cliente_nome = self.cliente.nome_empresa if self.cliente else 'Não vinculado'
        return f'{self.nome} → {cliente_nome}'

    def pode_acessar(self, acesso):
        """
        Verifica se este grupo tem permissão para solicitar acesso a um Acesso específico.
        Grupos globais acessam qualquer host. Grupos normais são isolados ao cliente vinculado.
        """
        if not self.ativo:
            return False
        if self.acesso_global:
            return True
        if not self.cliente:
            return False
        if acesso.cliente != self.cliente:
            return False
        hosts_restritos = self.hosts_permitidos.all()
        if hosts_restritos.exists():
            return hosts_restritos.filter(id=acesso.id).exists()
        return True

    def hosts_disponiveis(self):
        """Retorna os acessos que este grupo pode usar."""
        if self.acesso_global:
            return Acesso.objects.select_related('cliente', 'modelo', 'funcao').all()
        if not self.cliente:
            return Acesso.objects.none()
        hosts_restritos = self.hosts_permitidos.all()
        if hosts_restritos.exists():
            return hosts_restritos.filter(cliente=self.cliente)
        return Acesso.objects.filter(cliente=self.cliente)


class AgentKnowledge(models.Model):
    """Base de conhecimento editável pelos operadores — injetada no contexto do agent."""

    CATEGORIAS = [
        ('comando',         'Referência de Comandos'),
        ('procedure',       'Procedimento Operacional'),
        ('troubleshooting', 'Troubleshooting'),
        ('topologia',       'Topologia / Infraestrutura'),
        ('equipamento',     'Equipamento / Modelo'),
        ('alarme',          'Interpretação de Alarme'),
        ('geral',           'Geral'),
    ]
    FABRICANTES = [
        ('zte',      'ZTE'),
        ('huawei',   'Huawei'),
        ('cisco',    'Cisco'),
        ('mikrotik', 'MikroTik'),
        ('datacom',  'Datacom'),
        ('parks',    'Parks'),
        ('generico', 'Genérico'),
    ]

    titulo      = models.CharField(max_length=300, verbose_name='Título')
    conteudo    = models.TextField(verbose_name='Conteúdo (Markdown)')
    categoria   = models.CharField(max_length=30, choices=CATEGORIAS, default='geral', verbose_name='Categoria')
    fabricante  = models.CharField(max_length=30, choices=FABRICANTES, default='generico', verbose_name='Fabricante')
    tags        = models.JSONField(default=list, verbose_name='Tags', help_text='Ex: ["onu","gpon","los"]')

    # Escopo: None = global (todos os clientes), preenchido = específico do cliente
    cliente     = models.ForeignKey(
        Cliente, null=True, blank=True, on_delete=models.CASCADE,
        related_name='agent_knowledge', verbose_name='Cliente (específico)',
        help_text='Vazio = conhecimento global disponível para todos'
    )

    ativo       = models.BooleanField(default=True)
    uso_count   = models.IntegerField(default=0, verbose_name='Vezes consultado pelo agent')
    criado_por  = models.ForeignKey(
        'auth.User', null=True, on_delete=models.SET_NULL,
        related_name='agent_knowledge_criados'
    )
    criado_em   = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Base de Conhecimento Agent'
        verbose_name_plural = 'Base de Conhecimento Agent'
        ordering = ['-uso_count', 'fabricante', 'titulo']

    def __str__(self):
        return f'[{self.get_fabricante_display()}] {self.titulo}'


class AgentKnowledgeDoc(models.Model):
    """Documentos PDF carregados na base de conhecimento — o Agent consulta o texto extraído."""

    CATEGORIAS = AgentKnowledge.CATEGORIAS
    FABRICANTES = AgentKnowledge.FABRICANTES

    titulo           = models.CharField(max_length=300, verbose_name='Título')
    arquivo          = models.FileField(upload_to='agent_docs/', verbose_name='Arquivo PDF')
    nome_arquivo     = models.CharField(max_length=255, blank=True)
    tamanho_kb       = models.PositiveIntegerField(default=0)
    paginas          = models.PositiveIntegerField(default=0)
    conteudo_extraido = models.TextField(blank=True, verbose_name='Texto extraído do PDF')

    categoria  = models.CharField(max_length=30, choices=CATEGORIAS, default='geral',    verbose_name='Categoria')
    fabricante = models.CharField(max_length=30, choices=FABRICANTES, default='generico', verbose_name='Fabricante')
    tags       = models.JSONField(default=list, blank=True, verbose_name='Tags')

    cliente = models.ForeignKey(
        Cliente, null=True, blank=True, on_delete=models.CASCADE,
        related_name='agent_docs', verbose_name='Cliente (específico)',
        help_text='Vazio = disponível para todos'
    )

    ativo      = models.BooleanField(default=True)
    uso_count  = models.IntegerField(default=0, verbose_name='Vezes consultado pelo agent')
    criado_por = models.ForeignKey(
        'auth.User', null=True, on_delete=models.SET_NULL,
        related_name='agent_docs_criados'
    )
    criado_em     = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Documento PDF — Agent'
        verbose_name_plural = 'Documentos PDF — Agent'
        ordering = ['-uso_count', 'fabricante', 'titulo']

    def __str__(self):
        return f'[PDF][{self.get_fabricante_display()}] {self.titulo}'


class AgentSessao(models.Model):
    """Sessão de conversa com o Agent NOC — agrupa mensagens trocadas em um contexto."""

    CANAIS = [
        ('whatsapp', 'WhatsApp'),
        ('terminal', 'Terminal Web'),
    ]
    STATUS = [
        ('ativa',      'Ativa'),
        ('encerrada',  'Encerrada'),
        ('expirada',   'Expirada'),
    ]

    canal           = models.CharField(max_length=20, choices=CANAIS, verbose_name='Canal')
    # Para WhatsApp: JID do grupo/contato.  Para terminal: vazio.
    canal_id        = models.CharField(max_length=200, blank=True, verbose_name='ID do canal (JID ou session key)')

    # Contexto de cliente / host selecionado na sessão
    cliente         = models.ForeignKey(
        Cliente, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='agent_sessoes', verbose_name='Cliente'
    )
    acesso_ativo    = models.ForeignKey(
        'Acesso', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='agent_sessoes', verbose_name='Host ativo na sessão'
    )
    wa_grupo        = models.ForeignKey(
        WhatsAppGrupo, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='sessoes', verbose_name='Grupo WhatsApp'
    )

    # Usuário da plataforma que iniciou (terminal) ou None (WhatsApp)
    usuario         = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='agent_sessoes'
    )

    status          = models.CharField(max_length=20, choices=STATUS, default='ativa')
    iniciada_em     = models.DateTimeField(auto_now_add=True)
    ultima_atividade = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Sessão Agent NOC'
        verbose_name_plural = 'Sessões Agent NOC'
        ordering = ['-iniciada_em']

    def __str__(self):
        canal_desc = self.canal_id or self.usuario_id or '?'
        return f'Sessão [{self.canal}] {canal_desc} — {self.iniciada_em:%d/%m/%Y %H:%M}'


class AgentLog(models.Model):
    """Log individual de cada interação com o Agent NOC."""

    TIPOS = [
        ('user_msg',   'Mensagem do usuário'),
        ('agent_msg',  'Resposta do agent'),
        ('tool_call',  'Chamada de ferramenta'),
        ('tool_result','Resultado de ferramenta'),
        ('error',      'Erro'),
        ('system',     'Evento de sistema'),
    ]

    sessao          = models.ForeignKey(
        AgentSessao, on_delete=models.CASCADE,
        related_name='logs', verbose_name='Sessão'
    )
    tipo            = models.CharField(max_length=20, choices=TIPOS, verbose_name='Tipo')
    conteudo        = models.TextField(verbose_name='Conteúdo')

    # Para tool_call: nome da ferramenta e argumentos
    tool_name       = models.CharField(max_length=100, blank=True, verbose_name='Ferramenta')
    tool_input      = models.JSONField(null=True, blank=True, verbose_name='Input da ferramenta')
    tool_output     = models.TextField(blank=True, verbose_name='Output da ferramenta')

    # Métricas de custo/performance
    tokens_input    = models.IntegerField(default=0, verbose_name='Tokens entrada')
    tokens_output   = models.IntegerField(default=0, verbose_name='Tokens saída')
    duracao_ms      = models.IntegerField(default=0, verbose_name='Duração (ms)')

    criado_em       = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Log Agent NOC'
        verbose_name_plural = 'Logs Agent NOC'
        ordering = ['criado_em']

    def __str__(self):
        return f'[{self.tipo}] sessão {self.sessao_id} — {self.criado_em:%H:%M:%S}'


class TrocaSenhaJob(models.Model):
    """Job de troca de senhas em massa para todos os acessos SSH de um cliente."""
    STATUS_CHOICES = [
        ('PENDENTE',     'Pendente'),
        ('EM_ANDAMENTO', 'Em Andamento'),
        ('CONCLUIDO',    'Concluído'),
        ('ERRO',         'Erro'),
    ]
    cliente       = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='troca_senha_jobs')
    criado_por    = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='troca_senha_jobs')
    status        = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDENTE')
    novo_usuario  = models.CharField(max_length=50)
    nova_senha    = models.CharField(max_length=100)
    criado_em     = models.DateTimeField(auto_now_add=True)
    concluido_em  = models.DateTimeField(null=True, blank=True)
    total_acessos = models.IntegerField(default=0)
    total_sucesso = models.IntegerField(default=0)
    total_erro    = models.IntegerField(default=0)

    class Meta:
        verbose_name        = 'Job Troca de Senha'
        verbose_name_plural = 'Jobs Troca de Senha'
        ordering            = ['-criado_em']

    def __str__(self):
        return f'Job #{self.id} — {self.cliente.nome_empresa} ({self.status})'


class TrocaSenhaAcesso(models.Model):
    """Resultado por acesso de um TrocaSenhaJob."""
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('SUCESSO',  'Sucesso'),
        ('ERRO',     'Erro'),
    ]
    job              = models.ForeignKey('TrocaSenhaJob', on_delete=models.CASCADE, related_name='itens')
    acesso           = models.ForeignKey('Acesso', on_delete=models.SET_NULL, null=True, related_name='troca_senha_historico')
    status           = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDENTE')
    mensagem         = models.TextField(blank=True)
    usuario_antigo   = models.CharField(max_length=50, blank=True)
    senha_antiga     = models.CharField(max_length=100, blank=True)
    executado_em     = models.DateTimeField(null=True, blank=True)
    duracao_segundos = models.FloatField(null=True, blank=True)
    usuario_removido = models.BooleanField(default=False)

    class Meta:
        verbose_name        = 'Troca de Senha — Acesso'
        verbose_name_plural = 'Trocas de Senha — Acessos'
        ordering            = ['id']

    def __str__(self):
        acesso_str = str(self.acesso) if self.acesso else '(removido)'
        return f'TrocaSenha #{self.id} — {acesso_str} ({self.status})'


# ─────────────────────────────────────────────────────────────────────────────
# Hotspot
# ─────────────────────────────────────────────────────────────────────────────

class HotspotConfig(models.Model):
    cliente       = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='hotspot_configs')
    acesso        = models.ForeignKey('Acesso', on_delete=models.SET_NULL, null=True, blank=True, related_name='hotspot_configs')
    uuid          = models.UUIDField(default=_uuid_mod.uuid4, unique=True, editable=False)
    nome          = models.CharField(max_length=100)

    # MikroTik network
    # "bridge" (default) cria/usa a bridge própria do hotspot (hs-<nome>), usando
    # `interface_fisica` como bridge port. Qualquer outro valor (ex: "ether5") ativa
    # o modo direto: IP/DHCP/hotspot server vão direto nessa interface, sem bridge
    # (ver _aplicar_mikrotik em hotspot_views.py).
    interface       = models.CharField(max_length=50, default='bridge')
    # Interface física (ex: wlan1, ether2) adicionada como bridge port — só usada
    # quando `interface` = "bridge". Deixar vazio se a interface já está no bridge
    # ou se a bridge foi criada manualmente.
    interface_fisica = models.CharField(max_length=50, blank=True, default='')
    network       = models.CharField(max_length=18, default='192.168.88.0/24')
    gateway       = models.CharField(max_length=15, default='192.168.88.1')
    pool_start    = models.CharField(max_length=15, default='192.168.88.10')
    pool_end      = models.CharField(max_length=15, default='192.168.88.254')
    dns_servidor  = models.CharField(max_length=15, default='8.8.8.8')

    # Profile
    session_timeout = models.IntegerField(default=0, help_text='Minutos (0=ilimitado)')
    idle_timeout    = models.IntegerField(default=30, help_text='Minutos')
    rate_limit_down = models.CharField(max_length=20, default='10M')
    rate_limit_up   = models.CharField(max_length=20, default='5M')

    # Controle de banda via DHCP lease script (Queue Simple)
    dhcp_controle_banda = models.BooleanField(default=False, help_text='Ativar queue simple por IP via DHCP lease script')
    dhcp_banda_limit    = models.CharField(max_length=20, default='10M/10M', help_text='Ex: 10M/10M (download/upload)')

    # Guest credentials (shared account on MikroTik)
    guest_usuario = models.CharField(max_length=50, default='guest')
    guest_senha   = models.CharField(max_length=50, default='wifi123')

    # Portal appearance
    portal_titulo    = models.CharField(max_length=100, default='WiFi Grátis')
    portal_subtitulo = models.CharField(max_length=200, blank=True)
    cor_primaria     = models.CharField(max_length=7, default='#1a73e8')
    cor_secundaria   = models.CharField(max_length=7, blank=True, default='',
                          help_text='Cor secundária para o gradiente dos botões/fundo (opcional — usa um tom da primária se vazio)')
    logo             = models.ImageField(upload_to='hotspot/logos/', null=True, blank=True)

    # Fundo da página de login
    ESTILO_FUNDO_CHOICES = [
        ('gradiente', 'Gradiente (padrão)'),
        ('solido', 'Cor sólida'),
        ('imagem', 'Imagem'),
    ]
    estilo_fundo  = models.CharField(max_length=10, choices=ESTILO_FUNDO_CHOICES, default='gradiente')
    cor_fundo     = models.CharField(max_length=7, default='#0a0a0f')
    imagem_fundo  = models.ImageField(upload_to='hotspot/fundos/', null=True, blank=True)

    configurado_em = models.DateTimeField(null=True, blank=True)
    ativo          = models.BooleanField(default=True)
    criado_em      = models.DateTimeField(auto_now_add=True)
    atualizado_em  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Configuração Hotspot'
        verbose_name_plural = 'Configurações Hotspot'
        ordering            = ['-criado_em']

    def __str__(self):
        return f'{self.nome} — {self.cliente.nome_empresa}'


class HotspotInterface(models.Model):
    """Interface adicional de um Hotspot: outra pool/DHCP (ex: ether2) além da
    interface principal do HotspotConfig, servindo o mesmo portal de login."""
    hotspot          = models.ForeignKey(HotspotConfig, on_delete=models.CASCADE, related_name='interfaces')
    nome             = models.CharField(max_length=100, blank=True, default='')
    # "bridge" (padrão) cria/usa uma bridge própria desta interface, usando
    # `interface_fisica` como bridge port. Qualquer outro valor (ex: "ether5")
    # ativa o modo direto: IP/DHCP/hotspot server vão direto nessa interface,
    # sem bridge (mesma semântica de HotspotConfig.interface).
    interface        = models.CharField(max_length=50, default='bridge')
    interface_fisica = models.CharField(max_length=50, blank=True, default='',
                          help_text='Só usada quando "Interface" = bridge')
    network          = models.CharField(max_length=18, default='192.168.89.0/24')
    gateway          = models.CharField(max_length=15, default='192.168.89.1')
    pool_start       = models.CharField(max_length=15, default='192.168.89.10')
    pool_end         = models.CharField(max_length=15, default='192.168.89.254')
    dns_servidor     = models.CharField(max_length=15, default='8.8.8.8')
    ativo            = models.BooleanField(default=True)
    criado_em        = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Interface Adicional — Hotspot'
        verbose_name_plural = 'Interfaces Adicionais — Hotspot'
        ordering            = ['id']

    def __str__(self):
        return f'{self.nome or self.interface_fisica} — {self.hotspot.nome}'


class HotspotBanner(models.Model):
    hotspot   = models.ForeignKey(HotspotConfig, on_delete=models.CASCADE, related_name='banners')
    imagem    = models.ImageField(upload_to='hotspot/banners/')
    titulo    = models.CharField(max_length=100, blank=True)
    subtitulo = models.CharField(max_length=200, blank=True)
    ordem     = models.IntegerField(default=0)
    ativo     = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Banner Hotspot'
        verbose_name_plural = 'Banners Hotspot'
        ordering            = ['ordem', 'criado_em']

    def __str__(self):
        return f'Banner #{self.ordem} — {self.hotspot.nome}'


class HotspotLead(models.Model):
    hotspot         = models.ForeignKey(HotspotConfig, on_delete=models.CASCADE, related_name='leads')
    nome            = models.CharField(max_length=100)
    telefone        = models.CharField(max_length=20)
    data_nascimento = models.DateField(null=True, blank=True)
    cpf             = models.CharField(max_length=14, blank=True)
    mac             = models.CharField(max_length=17, blank=True)
    ip_cliente      = models.CharField(max_length=15, blank=True)
    termos_aceitos  = models.BooleanField(default=False, help_text='Aceite dos Termos de Uso e Política de Privacidade no login')
    criado_em       = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Lead Hotspot'
        verbose_name_plural = 'Leads Hotspot'
        ordering            = ['-criado_em']

    def __str__(self):
        return f'{self.nome} ({self.telefone})'


# ─────────────────────────────────────────────────────────────────────────────
# Integração Disparo — envio automático de WhatsApp (HSM) para leads do Hotspot
# ─────────────────────────────────────────────────────────────────────────────

# Templates HSM do Chatmix podem exigir qualquer quantidade de variáveis (não
# só nome/telefone) — o padrão abaixo é só um ponto de partida com 2 posições;
# o operador ajusta a quantidade de linhas para bater com o template dele.
DISPARO_VARIAVEIS_EXEMPLO = ['{nome}', '{telefone}']


def _disparo_variaveis_padrao():
    return list(DISPARO_VARIAVEIS_EXEMPLO)


class ClienteIntegracaoDisparo(models.Model):
    """Configuração de disparo de WhatsApp (HSM) por empresa de integração
    (Chatmix, Opa Suite, ...), usada para notificar automaticamente novos
    leads capturados no Hotspot deste cliente."""

    PROVIDER_CHOICES = [
        ('chatmix', 'Chatmix'),
        ('opa_suit', 'Opa Suite'),
    ]

    cliente    = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='integracoes_disparo')
    provider   = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    habilitado = models.BooleanField(default=False)

    # Credenciais (Chatmix: menu "Chaves para Acesso" — key + token)
    api_key     = models.CharField(max_length=255, blank=True, default='')
    api_token   = models.CharField(max_length=255, blank=True, default='')
    # Opa Suite é multi-tenant por domínio próprio (ex: https://minhaempresa.opasuite.com.br) —
    # cada cliente tem um domínio diferente, diferente do Chatmix que tem 1 endpoint fixo global.
    api_dominio = models.CharField(max_length=255, blank=True, default='',
                      help_text='Domínio da conta (só Opa Suite), ex: https://minhaempresa.opasuite.com.br')
    # Opa Suite exige o ID do canal de comunicação (WhatsApp) que fará o envio.
    canal_id    = models.CharField(max_length=64, blank=True, default='',
                      help_text='ID do canal de comunicação (só Opa Suite)')
    template_id = models.CharField(max_length=64, blank=True, default='',
                      help_text='ID do template (Chatmix: número no final da URL do template; Opa Suite: campo _id do template)')
    # Lista ordenada de variáveis a enviar (1 posição por variável exigida
    # pelo template). Cada item pode ser {nome}/{telefone} (substituído pelo
    # dado do lead) ou um texto fixo.
    variaveis_modelo = models.JSONField(default=_disparo_variaveis_padrao,
                      help_text='Uma entrada por variável exigida pelo template, na mesma ordem. Use {nome}/{telefone} ou texto fixo.')

    criado_em     = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Integração de Disparo (Cliente)'
        verbose_name_plural = 'Integrações de Disparo (Cliente)'
        unique_together     = ('cliente', 'provider')

    def __str__(self):
        return f'{self.get_provider_display()} — {self.cliente.nome_empresa}'


@receiver(post_save, sender=HotspotLead)
def disparar_integracao_lead(sender, instance, created, **kwargs):
    """Ao capturar um novo lead do Hotspot, dispara (em background via Celery)
    uma mensagem HSM via WhatsApp usando a integração habilitada do cliente.

    Nunca deve derrubar o cadastro do lead: os pontos que criam HotspotLead
    são endpoints públicos e críticos do captive portal (o usuário precisa
    conseguir se conectar ao WiFi mesmo que o broker Celery esteja fora do
    ar), então qualquer falha ao enfileirar a task é só logada.
    """
    if not created:
        return
    try:
        from .tasks import enviar_disparo_hotspot_lead
        enviar_disparo_hotspot_lead.delay(instance.id)
    except Exception:
        logging.getLogger(__name__).exception('Falha ao enfileirar disparo de integração p/ lead %s', instance.id)
