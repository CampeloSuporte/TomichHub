"""
Modelos do módulo de Segurança.

Três frentes, todas alimentando o mesmo painel (`/seguranca/`):

1. `TentativaLogin` + `BloqueioLogin` — força bruta no login do CRM. Errar a
   senha N vezes tranca a CONTA por alguns minutos (padrão: 3 falhas / 5 min,
   ver `settings.SEGURANCA_*`). O mesmo modelo, com `tipo='ip'`, tranca o IP
   que fica varrendo usuários que nem existem — aí a conta não serve de chave.
2. `EventoSeguranca` — payloads maliciosos barrados no meio do caminho pelo
   `seguranca.middleware.ProtecaoInjecaoMiddleware` (SQL injection, path
   traversal, XSS refletido).
3. `AcaoSeguranca` — auditoria de quem destravou o quê pelo painel. Sem isso
   um desbloqueio não deixa rastro nenhum, e desbloquear é exatamente a ação
   que um invasor com sessão roubada ia querer usar.

Os banimentos de SSH NÃO têm modelo aqui: quem manda neles é o fail2ban
(o firewall é dele), e a fonte da verdade é o `fail2ban-client` — ver
`seguranca/fail2ban.py`.
"""
from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class TentativaLogin(models.Model):
    """Log append-only de toda tentativa de autenticação no CRM — inclusive
    as bem-sucedidas, pra o painel conseguir mostrar "de onde esse usuário
    entra normalmente" ao lado das tentativas falhas."""

    MOTIVO_SUCESSO = 'sucesso'
    MOTIVO_SENHA_INVALIDA = 'senha_invalida'
    MOTIVO_USUARIO_INEXISTENTE = 'usuario_inexistente'
    MOTIVO_USUARIO_INATIVO = 'usuario_inativo'
    MOTIVO_2FA_INVALIDO = '2fa_invalido'
    MOTIVO_BLOQUEADO = 'bloqueado'
    MOTIVO_CAPTCHA = 'captcha_falhou'

    MOTIVO_CHOICES = [
        (MOTIVO_SUCESSO, 'Login realizado'),
        (MOTIVO_SENHA_INVALIDA, 'Senha inválida'),
        (MOTIVO_USUARIO_INEXISTENTE, 'Usuário inexistente'),
        (MOTIVO_USUARIO_INATIVO, 'Usuário inativo'),
        (MOTIVO_2FA_INVALIDO, 'Código 2FA inválido'),
        (MOTIVO_BLOQUEADO, 'Tentativa com conta/IP bloqueado'),
        (MOTIVO_CAPTCHA, 'Captcha falhou'),
    ]

    username = models.CharField(max_length=150, blank=True, default='')
    usuario = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tentativas_login',
        help_text='Preenchido só quando o username existe de fato.',
    )
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default='')
    sucesso = models.BooleanField(default=False)
    motivo = models.CharField(max_length=30, choices=MOTIVO_CHOICES)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Tentativa de Login'
        verbose_name_plural = 'Tentativas de Login'
        ordering = ['-criado_em']
        indexes = [
            models.Index(fields=['-criado_em']),
            models.Index(fields=['username', '-criado_em']),
            models.Index(fields=['ip', '-criado_em']),
            models.Index(fields=['sucesso', '-criado_em']),
        ]

    def __str__(self):
        return f"{self.criado_em:%d/%m %H:%M} {self.username or '?'}@{self.ip or '?'} — {self.get_motivo_display()}"


class BloqueioLogin(models.Model):
    """Contador de falhas + janela de bloqueio, por conta ou por IP.

    Uma linha por chave (`unique_together`): ela é criada na primeira falha e
    reaproveitada pra sempre, com o contador zerando a cada login certo ou a
    cada janela expirada. Guardar histórico de bloqueios fica por conta de
    `TentativaLogin`/`AcaoSeguranca` — aqui interessa só o estado atual.

    `tipo='conta'` é o pedido literal do produto (errou a senha 3x, conta
    trancada por 5 min). `tipo='ip'` cobre o caso em que a conta não serve de
    chave: o robô que testa 500 usernames inventados nunca acumularia 3 falhas
    no mesmo username.
    """

    TIPO_CONTA = 'conta'
    TIPO_IP = 'ip'
    TIPO_CHOICES = [
        (TIPO_CONTA, 'Conta'),
        (TIPO_IP, 'Endereço IP'),
    ]

    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default=TIPO_CONTA)
    chave = models.CharField(max_length=150, help_text='username (tipo=conta) ou endereço IP (tipo=ip).')
    usuario = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True, related_name='bloqueios_login',
    )
    ultimo_ip = models.GenericIPAddressField(null=True, blank=True)
    falhas = models.PositiveIntegerField(default=0)
    bloqueado_ate = models.DateTimeField(null=True, blank=True)
    total_bloqueios = models.PositiveIntegerField(
        default=0, help_text='Quantas vezes essa chave já foi bloqueada (não zera no desbloqueio).')
    primeira_falha_em = models.DateTimeField(null=True, blank=True)
    ultima_falha_em = models.DateTimeField(null=True, blank=True)
    desbloqueado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='desbloqueios_realizados',
    )
    desbloqueado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Bloqueio de Login'
        verbose_name_plural = 'Bloqueios de Login'
        unique_together = ('tipo', 'chave')
        ordering = ['-bloqueado_ate', '-ultima_falha_em']
        indexes = [models.Index(fields=['tipo', 'bloqueado_ate'])]

    def __str__(self):
        return f"[{self.get_tipo_display()}] {self.chave} — {self.falhas} falha(s)"

    @property
    def ativo(self):
        return bool(self.bloqueado_ate and self.bloqueado_ate > timezone.now())

    @property
    def segundos_restantes(self):
        if not self.ativo:
            return 0
        return int((self.bloqueado_ate - timezone.now()).total_seconds())


class EventoSeguranca(models.Model):
    """Requisição barrada (ou só marcada) pelo middleware de proteção contra
    injeção. `bloqueado=False` significa modo observação — o payload casou com
    a assinatura mas o middleware está configurado pra só registrar."""

    TIPO_SQL_INJECTION = 'sql_injection'
    TIPO_PATH_TRAVERSAL = 'path_traversal'
    TIPO_XSS = 'xss'
    TIPO_CHOICES = [
        (TIPO_SQL_INJECTION, 'SQL Injection'),
        (TIPO_PATH_TRAVERSAL, 'Path Traversal'),
        (TIPO_XSS, 'Cross-Site Scripting'),
    ]

    ORIGEM_CHOICES = [
        ('querystring', 'Query string'),
        ('post', 'Corpo do POST'),
        ('path', 'Caminho da URL'),
        ('header', 'Cabeçalho'),
    ]

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    assinatura = models.CharField(max_length=60, help_text='Nome da regra que casou (ex.: union_select).')
    origem = models.CharField(max_length=20, choices=ORIGEM_CHOICES, default='querystring')
    campo = models.CharField(max_length=120, blank=True, default='')
    payload = models.TextField(blank=True, default='', help_text='Trecho suspeito, truncado.')
    caminho = models.CharField(max_length=500, blank=True, default='')
    metodo = models.CharField(max_length=10, blank=True, default='')
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default='')
    usuario = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='eventos_seguranca',
    )
    bloqueado = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Evento de Segurança'
        verbose_name_plural = 'Eventos de Segurança'
        ordering = ['-criado_em']
        indexes = [
            models.Index(fields=['-criado_em']),
            models.Index(fields=['tipo', '-criado_em']),
            models.Index(fields=['ip', '-criado_em']),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} de {self.ip or '?'} em {self.caminho}"


class AcaoSeguranca(models.Model):
    """Trilha de auditoria do painel: quem desbloqueou conta, quem tirou IP do
    banimento do fail2ban, quem baniu na mão."""

    ACAO_DESBLOQUEIO_CONTA = 'desbloqueio_conta'
    ACAO_DESBLOQUEIO_IP = 'desbloqueio_ip'
    ACAO_UNBAN_SSH = 'unban_ssh'
    ACAO_BAN_SSH = 'ban_ssh'
    ACAO_CHOICES = [
        (ACAO_DESBLOQUEIO_CONTA, 'Conta desbloqueada'),
        (ACAO_DESBLOQUEIO_IP, 'IP desbloqueado no CRM'),
        (ACAO_UNBAN_SSH, 'IP removido do fail2ban'),
        (ACAO_BAN_SSH, 'IP banido manualmente no fail2ban'),
    ]

    acao = models.CharField(max_length=30, choices=ACAO_CHOICES)
    alvo = models.CharField(max_length=150)
    detalhe = models.CharField(max_length=300, blank=True, default='')
    usuario = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='acoes_seguranca',
    )
    ip_origem = models.GenericIPAddressField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Ação de Segurança'
        verbose_name_plural = 'Ações de Segurança'
        ordering = ['-criado_em']

    def __str__(self):
        return f"{self.get_acao_display()}: {self.alvo} por {self.usuario or 'sistema'}"
