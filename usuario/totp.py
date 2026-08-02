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

from .models import TOTPBackupCode

ISSUER_NAME = 'TOMICH HUB'


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
