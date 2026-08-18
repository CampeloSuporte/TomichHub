"""
Funções puras de TOTP (2FA compatível com o app Google Authenticator,
RFC 6238) e de códigos de backup — sem tocar em request/response, pra
manter usuario/views.py enxuto.
"""
import base64
import io
import secrets

import pyotp
import qrcode
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from .models import TOTPBackupCode, DispositivoConfiavel

ISSUER_NAME = 'TOMICH HUB'
DISPOSITIVO_CONFIAVEL_COOKIE = 'dispositivo_confiavel'
DISPOSITIVO_CONFIAVEL_DIAS = 30


def gerar_secret():
    return pyotp.random_base32()


def provisioning_uri(device, user):
    return pyotp.TOTP(device.secret).provisioning_uri(name=user.username, issuer_name=ISSUER_NAME)


def qr_code_data_uri(uri):
    """PNG do QR code como data URI, pronto pro atributo src de uma <img>."""
    img = qrcode.make(uri)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    base64_png = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{base64_png}'


def verificar_codigo(device, codigo):
    if not codigo:
        return False
    return pyotp.TOTP(device.secret).verify(codigo.strip(), valid_window=1)


def gerar_backup_codes(device, n=10):
    """Substitui os códigos de backup existentes por `n` novos, retornando
    a lista em texto puro (só existe nesse retorno — exibir uma única vez)."""
    device.backup_codes.all().delete()
    codigos = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    TOTPBackupCode.objects.bulk_create([
        TOTPBackupCode(device=device, codigo_hash=make_password(codigo))
        for codigo in codigos
    ])
    return codigos


def verificar_backup_code(device, codigo):
    if not codigo:
        return False
    codigo = codigo.strip()
    for backup in device.backup_codes.filter(usado_em__isnull=True):
        if check_password(codigo, backup.codigo_hash):
            backup.usado_em = timezone.now()
            backup.save(update_fields=['usado_em'])
            return True
    return False


def criar_dispositivo_confiavel(user, descricao=''):
    """Gera um token novo pra 'confiar neste navegador' e grava só o hash
    no banco (mesmo padrão de backup code). Retorna (valor_do_cookie,
    expira_em) — o token em texto puro só existe nesse retorno, pra virar
    cookie; nunca é persistido."""
    token = secrets.token_urlsafe(32)
    expira_em = timezone.now() + timezone.timedelta(days=DISPOSITIVO_CONFIAVEL_DIAS)
    DispositivoConfiavel.objects.create(
        usuario=user,
        token_hash=make_password(token),
        descricao=descricao[:255],
        expira_em=expira_em,
    )
    return f"{user.id}:{token}", expira_em


def verificar_dispositivo_confiavel(cookie_valor):
    """Retorna o User dono do cookie 'dispositivo_confiavel' se ele
    corresponder a um registro válido (não expirado); None caso contrário.
    Atualiza `ultimo_uso_em` no acerto."""
    if not cookie_valor or ':' not in cookie_valor:
        return None
    user_id, token = cookie_valor.split(':', 1)
    if not user_id.isdigit():
        return None
    agora = timezone.now()
    for dispositivo in DispositivoConfiavel.objects.filter(usuario_id=user_id, expira_em__gt=agora):
        if check_password(token, dispositivo.token_hash):
            dispositivo.ultimo_uso_em = agora
            dispositivo.save(update_fields=['ultimo_uso_em'])
            return dispositivo.usuario
    return None
