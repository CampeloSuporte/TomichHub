from django.db import models
from django.contrib.auth.models import User


class UsuarioModulo(models.Model):
    """
    Controla, por usuário (login individual), se uma ferramenta do sistema
    (aba da tela do cliente) está habilitada. Ausência de registro =
    habilitado (ver `modulo_habilitado`) para não quebrar usuários já
    existentes. Dois usuários da mesma empresa podem ter seleções diferentes.
    """
    MODULO_CHOICES = [
        ('acessos', 'Acessos'),
        ('backups', 'Backups'),
        ('vpn', 'VPN'),
        ('topologia', 'Topologia'),
        ('tuneis', 'Túneis SSH'),
        ('documentos', 'Documentos'),
        ('rpki_irr', 'RPKI/IRR'),
        ('monitoramento', 'Monitoramento'),
        ('documentacao', 'Documentação de Rede'),
        ('hotspot', 'Hotspot'),
        ('testes_rede', 'Testes de Rede'),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='modulos')
    modulo = models.CharField(max_length=30, choices=MODULO_CHOICES)
    habilitado = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('usuario', 'modulo')
        verbose_name = 'Módulo do Usuário'
        verbose_name_plural = 'Módulos do Usuário'

    def __str__(self):
        return f"{self.usuario.username} - {self.get_modulo_display()}: {'ON' if self.habilitado else 'OFF'}"


def modulo_habilitado(user, modulo_key):
    """Módulo sem registro para esse usuário = habilitado."""
    registro = UsuarioModulo.objects.filter(usuario=user, modulo=modulo_key).values_list('habilitado', flat=True).first()
    return True if registro is None else registro


def modulos_habilitados_dict(user):
    """Dict {modulo_key: bool} para todos os módulos conhecidos, pra uso no template."""
    estado = {m.modulo: m.habilitado for m in UsuarioModulo.objects.filter(usuario=user)}
    return {chave: estado.get(chave, True) for chave, _ in UsuarioModulo.MODULO_CHOICES}


class Instancia(models.Model):
    """Uma 'conta' de revenda: um Consultor cadastra e gerencia seus próprios
    Clientes dentro da sua Instancia, isolados de outras instâncias. O
    Administrador da plataforma não pertence a nenhuma (vê todas)."""

    nome = models.CharField(max_length=255)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='instancias_criadas')

    class Meta:
        verbose_name = 'Instância'
        verbose_name_plural = 'Instâncias'

    def __str__(self):
        return self.nome


class PerfilUsuario(models.Model):
    """Papel de um usuário de back-office (admin/consultor/operador).
    Ausência deste registro para um usuário is_staff=True = admin legado
    (compatibilidade com contas criadas antes desta feature — ver
    `usuario.perms.get_role`). Usuários do portal do cliente final
    (is_staff=False, vinculados via Cliente) não têm PerfilUsuario."""

    ROLE_ADMIN = 'admin'
    ROLE_CONSULTOR = 'consultor'
    ROLE_OPERADOR = 'operador'
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Administrador'),
        (ROLE_CONSULTOR, 'Consultor'),
        (ROLE_OPERADOR, 'Operador'),
    ]

    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    instancia = models.ForeignKey(Instancia, on_delete=models.CASCADE, null=True, blank=True, related_name='usuarios')
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='usuarios_criados')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Perfil de Usuário'
        verbose_name_plural = 'Perfis de Usuário'

    def __str__(self):
        return f"{self.usuario.username} ({self.get_role_display()})"


class InstanciaFerramenta(models.Model):
    """Controla, por Instancia (não por login), se uma ferramenta do
    núcleo do sistema está liberada para o Consultor e seus Operadores.
    Ausência de registro = desabilitado (o oposto de UsuarioModulo): é o
    Administrador concedendo acesso a um revendedor pago, não um toggle
    opcional por login."""

    FERRAMENTA_CHOICES = [
        ('acessos', 'Acessos'),
        ('backups', 'Backups'),
        ('vpn', 'VPN'),
        ('topologia', 'Topologia'),
        ('tuneis', 'Túneis SSH'),
        ('documentos', 'Documentos'),
        ('rpki_irr', 'RPKI/IRR'),
        ('monitoramento', 'Monitoramento'),
        ('hotspot', 'Hotspot'),
        ('ipam', 'IPAM'),
        ('scripts', 'Scripts'),
        ('bgp', 'BGP'),
        ('testes_rede', 'Testes de Rede'),
        ('lg', 'Pesquisa LG'),
        ('geoip', 'Geolocalização IP'),
        ('firmware', 'Firmware'),
    ]

    instancia = models.ForeignKey(Instancia, on_delete=models.CASCADE, related_name='ferramentas')
    ferramenta = models.CharField(max_length=30, choices=FERRAMENTA_CHOICES)
    habilitado = models.BooleanField(default=False)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('instancia', 'ferramenta')
        verbose_name = 'Ferramenta da Instância'
        verbose_name_plural = 'Ferramentas da Instância'

    def __str__(self):
        return f"{self.instancia.nome} - {self.get_ferramenta_display()}: {'ON' if self.habilitado else 'OFF'}"


def ferramenta_habilitada(instancia, ferramenta_key):
    """Ferramenta sem registro para essa instância = desabilitada."""
    if instancia is None:
        return False
    registro = InstanciaFerramenta.objects.filter(instancia=instancia, ferramenta=ferramenta_key).values_list('habilitado', flat=True).first()
    return bool(registro)


def ferramentas_habilitadas_dict(instancia):
    """Dict {ferramenta_key: bool} para todas as ferramentas conhecidas, pra uso no template."""
    if instancia is None:
        return {chave: False for chave, _ in InstanciaFerramenta.FERRAMENTA_CHOICES}
    estado = {f.ferramenta: f.habilitado for f in InstanciaFerramenta.objects.filter(instancia=instancia)}
    return {chave: estado.get(chave, False) for chave, _ in InstanciaFerramenta.FERRAMENTA_CHOICES}


class TOTPDevice(models.Model):
    """2FA via app Google Authenticator (TOTP, RFC 6238). Um por usuário;
    só passa a valer no login depois de `confirmado=True` (evita travar a
    conta caso o usuário abandone o setup antes de escanear o QR)."""

    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='totp_device')
    secret = models.CharField(max_length=32)
    confirmado = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    confirmado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Dispositivo 2FA (TOTP)'
        verbose_name_plural = 'Dispositivos 2FA (TOTP)'

    def __str__(self):
        return f"{self.usuario.username} - {'confirmado' if self.confirmado else 'pendente'}"


class TOTPBackupCode(models.Model):
    """Código de uso único pra login sem o app, caso o usuário perca o
    celular. Guardado como hash (mesmo hasher de senha) — nunca em texto
    puro depois de gerado."""

    device = models.ForeignKey(TOTPDevice, on_delete=models.CASCADE, related_name='backup_codes')
    codigo_hash = models.CharField(max_length=128)
    criado_em = models.DateTimeField(auto_now_add=True)
    usado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Código de Backup 2FA'
        verbose_name_plural = 'Códigos de Backup 2FA'
