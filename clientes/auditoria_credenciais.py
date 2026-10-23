"""Auditoria de troca de credenciais no cadastro de um Acesso.

Toda gravação que pode mudar usuário/senha de um host (Editar Acesso, troca
de senhas em massa, Django admin) tira um `capturar()` do banco antes e chama
`registrar()` depois. Se algo mudou, vira um AcessoAlteracaoCredencial com
quem/quando/IP/campos — as senhas em si nunca são gravadas no registro.
"""
from django.utils import timezone

from .models import Acesso, AcessoAlteracaoCredencial

CAMPOS = ('usuario', 'senha', 'senha_adm')

ROTULOS = {
    'usuario': 'Usuário',
    'senha': 'Senha',
    'senha_adm': 'Senha ADM',
}


def capturar(acesso_id):
    """Credenciais atuais do Acesso direto do banco (não do objeto em memória,
    que a view já pode ter sobrescrito com o POST). {} se não existir."""
    return Acesso.objects.filter(pk=acesso_id).values(*CAMPOS).first() or {}


def registrar(acesso, antes, *, usuario=None, ip=None, origem='edicao'):
    """Compara `antes` (de `capturar`) com o que está no banco agora e grava
    um registro se usuário, senha ou senha ADM mudaram. Retorna o registro
    criado ou None."""
    if not antes:
        return None
    depois = capturar(acesso.pk)
    mudou = [c for c in CAMPOS if (antes.get(c) or '') != (depois.get(c) or '')]
    if not mudou:
        return None
    if usuario is not None and not getattr(usuario, 'is_authenticated', False):
        usuario = None
    return AcessoAlteracaoCredencial.objects.create(
        acesso=acesso,
        usuario=usuario,
        ip_origem=(ip or '')[:45],
        origem=origem,
        campos=','.join(mudou),
        login_antigo=(antes.get('usuario') or '')[:50] if 'usuario' in mudou else '',
        login_novo=(depois.get('usuario') or '')[:50] if 'usuario' in mudou else '',
    )


def serializar(reg):
    """Formato JSON usado pelas telas de auditoria."""
    u = reg.usuario
    return {
        'id': reg.id,
        'acesso_id': reg.acesso_id,
        'host': reg.acesso.host,
        'tipo': reg.acesso.tipo,
        'usuario': (u.get_full_name() or u.username) if u else '—',
        'ip_origem': reg.ip_origem or None,
        'origem': reg.origem,
        'origem_display': reg.get_origem_display(),
        'campos': [ROTULOS.get(c, c) for c in reg.campos_lista()],
        'login_antigo': reg.login_antigo,
        'login_novo': reg.login_novo,
        'alterado_em': timezone.localtime(reg.alterado_em).strftime('%d/%m/%Y %H:%M:%S'),
    }
