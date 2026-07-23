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
