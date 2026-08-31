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
        ('tarefas', 'Tarefas'),
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


class UsuarioAcesso(models.Model):
    """Quais hosts (`clientes.Acesso`) do cliente um login do portal enxerga
    e pode usar.

    **Sem nenhum registro para o usuário — nem aqui nem em `UsuarioFuncao` —
    = todos os hosts do cliente**: é o comportamento de sempre, então nenhum
    login existente muda ao subir essa tabela, e um host cadastrado depois já
    nasce visível para quem não tem restrição. Havendo registro, o login vê
    **só** os hosts liberados (nas listas e nas ações: terminal, WinBox,
    backup, proxy web, scripts...).

    Esta tabela é a liberação **host a host**; `UsuarioFuncao` é a liberação
    **por função** (todas as OLTs, por exemplo). Quando as duas têm registro,
    o login vê a **união** — a tela de usuários grava uma de cada vez, mas a
    permissão não depende disso.

    Só vale para o portal do cliente final. Administrador, Consultor e
    Operador continuam limitados pelo cliente/instância
    (`perms.pode_acessar_cliente`), nunca por host.
    """

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='acessos_permitidos')
    acesso = models.ForeignKey('clientes.Acesso', on_delete=models.CASCADE, related_name='usuarios_permitidos')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('usuario', 'acesso')
        verbose_name = 'Host liberado para o Usuário'
        verbose_name_plural = 'Hosts liberados para o Usuário'

    def __str__(self):
        return f"{self.usuario.username} → {self.acesso.tipo} ({self.acesso.host})"


class UsuarioFuncao(models.Model):
    """Quais **funções de equipamento** um login do portal enxerga — "só as
    OLTs", "só os BRAS".

    Diferente de `UsuarioAcesso`, é uma regra e não uma lista: um host novo
    do cliente com uma função liberada **já nasce visível** para esse login,
    sem ninguém reeditar o usuário. Por isso é o jeito certo de dizer "esse
    técnico cuida das OLTs".

    Host sem função cadastrada nunca entra por aqui (não tem como casar com
    regra nenhuma) — para liberá-lo, use `UsuarioAcesso`.
    """

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='funcoes_permitidas')
    funcao = models.ForeignKey(
        'funcao_equipamento.Funcao_equipamento', on_delete=models.CASCADE,
        related_name='usuarios_permitidos',
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('usuario', 'funcao')
        verbose_name = 'Função liberada para o Usuário'
        verbose_name_plural = 'Funções liberadas para o Usuário'

    def __str__(self):
        return f"{self.usuario.username} → função {self.funcao}"


def acessos_permitidos_ids(user):
    """`set` de ids de Acesso liberados host a host, ou `None` quando não há
    registro nenhum."""
    if not user or not user.is_authenticated:
        return None
    ids = set(UsuarioAcesso.objects.filter(usuario=user).values_list('acesso_id', flat=True))
    return ids or None


def funcoes_permitidas_ids(user):
    """`set` de ids de Funcao_equipamento liberadas, ou `None` quando não há
    registro nenhum."""
    if not user or not user.is_authenticated:
        return None
    ids = set(UsuarioFuncao.objects.filter(usuario=user).values_list('funcao_id', flat=True))
    return ids or None


class Instancia(models.Model):
    """Uma 'conta' de revenda: um Consultor cadastra e gerencia seus próprios
    Clientes dentro da sua Instancia, isolados de outras instâncias. O
    Administrador da plataforma não pertence a nenhuma (vê todas)."""

    nome = models.CharField(max_length=255)
    ativo = models.BooleanField(default=True)
    principal = models.BooleanField(
        default=False,
        verbose_name='Instância principal',
        help_text=(
            'Marca a operação própria do Administrador (não uma revenda). '
            'Só ela tem acesso aos módulos exclusivos da plataforma — hoje, '
            'o Atendimento. Deve existir no máximo uma.'
        ),
    )
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


class PortalUsuarioInstancia(models.Model):
    """Rastreia a Instancia (Consultor) responsável por um usuário do
    portal do cliente final (role='cliente', sem PerfilUsuario) enquanto
    ele ainda não está vinculado a nenhum Cliente. Sem isso,
    `usuarios_gerenciaveis_por` (usuario/perms.py) não tinha como saber que
    um Consultor podia gerenciar/selecionar um login de portal recém
    criado por ele mesmo — o usuário sumia da própria listagem e do
    dropdown de vínculo em Cliente."""

    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='portal_instancia')
    instancia = models.ForeignKey(Instancia, on_delete=models.CASCADE, related_name='usuarios_portal')
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='usuarios_portal_criados')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Instância do Usuário de Portal'
        verbose_name_plural = 'Instâncias de Usuários de Portal'

    def __str__(self):
        return f"{self.usuario.username} → {self.instancia.nome}"


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
        # Wiki é base de conhecimento GLOBAL (ArtigoWiki não tem instancia nem
        # cliente): liberar aqui dá leitura, busca e — desde 14/08/2026 — criar
        # e editar artigo pro Consultor (ver `usuario.perms.pode_editar_wiki`).
        # Como a base é global, o artigo que ele edita é o mesmo que as outras
        # instâncias leem; foi decisão do produto. EXCLUIR artigo continua só
        # com Administrador, pra manter limitado o estrago possível.
        ('wiki', 'Wiki'),
        ('tarefas', 'Tarefas'),
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


class DispositivoConfiavel(models.Model):
    """'Confiar neste navegador' — pula a segunda etapa do 2FA por um
    tempo, no mesmo navegador, sem abrir mão da segurança pra sempre: o
    token é aleatório e guardado como hash (nunca em texto puro, mesmo
    padrão de TOTPBackupCode), amarrado a um usuário e com expiração."""

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='dispositivos_confiaveis')
    token_hash = models.CharField(max_length=128)
    descricao = models.CharField(max_length=255, blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)
    expira_em = models.DateTimeField()
    ultimo_uso_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Dispositivo Confiável (2FA)'
        verbose_name_plural = 'Dispositivos Confiáveis (2FA)'
        indexes = [models.Index(fields=['usuario', 'expira_em'])]

    def __str__(self):
        return f"{self.usuario.username} — {self.descricao or 'dispositivo'} (expira {self.expira_em:%d/%m/%Y})"
