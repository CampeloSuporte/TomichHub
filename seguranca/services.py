"""
Regras de bloqueio por força bruta no login do CRM.

Fluxo, do ponto de vista da view de login (`usuario/views.py`):

    bloqueio = seguranca.verificar_bloqueio(request, username)
    if bloqueio: -> recusa sem nem chamar authenticate()
    ...
    registrar_falha(request, username, motivo)   # senha errada / 2FA errado
    registrar_sucesso(request, user)             # zera os contadores

Toda falha também vai pro arquivo de log lido pelo fail2ban
(`SEGURANCA_AUTH_LOG`), que é quem barra o IP no firewall — o bloqueio do
banco só protege a aplicação, o do fail2ban tira o atacante da porta.
"""
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from .models import AcaoSeguranca, BloqueioLogin, TentativaLogin

logger = logging.getLogger(__name__)

# Logger dedicado ao arquivo que o fail2ban vigia (jail `crm-login`). É um
# logger separado do 'seguranca' porque o formato das linhas é um contrato
# com o filtro do fail2ban (/etc/fail2ban/filter.d/crm-login.conf) — mudar o
# formato aqui exige mudar o regex lá.
auth_logger = logging.getLogger('seguranca.auth')


def _cfg(nome, padrao):
    return getattr(settings, nome, padrao)


def max_tentativas():
    """Falhas toleradas antes de trancar a CONTA. Padrão 3 — o produto pediu
    'errar mais de 3 vezes', então a 3ª falha já fecha a porta."""
    return int(_cfg('SEGURANCA_MAX_TENTATIVAS', 3))


def minutos_bloqueio():
    return int(_cfg('SEGURANCA_BLOQUEIO_MINUTOS', 5))


def max_tentativas_ip():
    """Limite por IP — mais folgado que o da conta, porque um IP legítimo pode
    ser o NAT de um escritório inteiro errando senha ao mesmo tempo."""
    return int(_cfg('SEGURANCA_MAX_TENTATIVAS_IP', 10))


def minutos_bloqueio_ip():
    return int(_cfg('SEGURANCA_BLOQUEIO_IP_MINUTOS', 15))


def janela_minutos():
    """Falhas mais antigas que isso não contam — senão duas senhas erradas em
    janeiro + uma em março trancariam a conta."""
    return int(_cfg('SEGURANCA_JANELA_MINUTOS', 15))


def get_client_ip(request):
    """IP real do cliente atrás do nginx (e do Cloudflare, quando o domínio
    está proxiado). Ordem: CF-Connecting-IP > primeiro item do
    X-Forwarded-For > X-Real-IP > REMOTE_ADDR.

    O primeiro item do XFF é o cliente porque o nginx usa
    `$proxy_add_x_forwarded_for`, que ANEXA o peer no fim da lista.
    """
    if request is None:
        return None
    cf = (request.META.get('HTTP_CF_CONNECTING_IP') or '').strip()
    if cf:
        return cf[:45]
    xff = (request.META.get('HTTP_X_FORWARDED_FOR') or '').strip()
    if xff:
        primeiro = xff.split(',')[0].strip()
        if primeiro:
            return primeiro[:45]
    real = (request.META.get('HTTP_X_REAL_IP') or '').strip()
    if real:
        return real[:45]
    return (request.META.get('REMOTE_ADDR') or '')[:45] or None


def _user_agent(request):
    if request is None:
        return ''
    return (request.META.get('HTTP_USER_AGENT') or '')[:300]


def _ip_valido(ip):
    """GenericIPAddressField recusa lixo; um XFF forjado não pode derrubar o
    login com ValidationError."""
    if not ip:
        return None
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_ipv46_address
    try:
        validate_ipv46_address(ip)
    except ValidationError:
        return None
    return ip


def registrar_tentativa(request, username, motivo, sucesso=False, usuario=None):
    """Grava a linha de auditoria. Nunca levanta exceção: log de segurança que
    derruba o login é pior que log perdido."""
    ip = _ip_valido(get_client_ip(request))
    try:
        return TentativaLogin.objects.create(
            username=(username or '')[:150],
            usuario=usuario,
            ip=ip,
            user_agent=_user_agent(request),
            sucesso=sucesso,
            motivo=motivo,
        )
    except Exception:
        logger.exception('Falha ao registrar tentativa de login')
        return None


def _log_fail2ban(username, ip, motivo):
    """Linha consumida pelo filtro do fail2ban. Formato fixo:

        LOGIN FAILED user=<username> ip=<ip> reason=<motivo>

    Sem IP não escreve nada: o fail2ban só sabe banir a partir de um <HOST>.
    """
    if not ip:
        return
    try:
        auth_logger.warning(
            'LOGIN FAILED user=%s ip=%s reason=%s',
            (username or '-').replace(' ', '_')[:150], ip, motivo,
        )
    except Exception:
        logger.exception('Falha ao escrever no log de autenticação do fail2ban')


def _obter_bloqueio(tipo, chave, usuario=None):
    obj, _ = BloqueioLogin.objects.get_or_create(
        tipo=tipo, chave=chave[:150], defaults={'usuario': usuario},
    )
    if usuario and obj.usuario_id != usuario.id:
        obj.usuario = usuario
    return obj


def verificar_bloqueio(request, username):
    """Bloqueio ativo que impede este login agora, ou None.

    Checa a conta e o IP; devolve o que travar primeiro. Chamada ANTES de
    `authenticate()`, pra senha certa também não passar enquanto durar o
    castigo — senão o bloqueio só atrasaria quem já errou, não quem acertou
    na tentativa 4 de um ataque de dicionário.
    """
    agora = timezone.now()
    chaves = []
    if username:
        chaves.append((BloqueioLogin.TIPO_CONTA, username[:150]))
    ip = _ip_valido(get_client_ip(request))
    if ip:
        chaves.append((BloqueioLogin.TIPO_IP, ip))
    if not chaves:
        return None

    from django.db.models import Q
    filtro = Q()
    for tipo, chave in chaves:
        filtro |= Q(tipo=tipo, chave=chave)

    return (BloqueioLogin.objects
            .filter(filtro, bloqueado_ate__gt=agora)
            .order_by('-bloqueado_ate')
            .first())


@transaction.atomic
def registrar_falha(request, username, motivo=TentativaLogin.MOTIVO_SENHA_INVALIDA):
    """Conta mais uma falha e, se estourou o limite, aplica o bloqueio.

    Devolve `(bloqueio_conta_ou_None, tentativas_restantes)` — a view usa isso
    pra avisar "restam N tentativas" antes de trancar.

    A linha do `BloqueioLogin` de conta só nasce se o username existir de
    verdade: robô testando 500 nomes inventados criaria 500 linhas inúteis, e
    quem cuida desse caso é o contador por IP (e o fail2ban).
    """
    agora = timezone.now()
    ip = _ip_valido(get_client_ip(request))
    usuario = User.objects.filter(username=username).first() if username else None

    registrar_tentativa(request, username, motivo, sucesso=False, usuario=usuario)
    _log_fail2ban(username, ip, motivo)

    limite = max_tentativas()
    janela = timezone.timedelta(minutes=janela_minutos())
    bloqueio_conta = None

    if usuario is not None:
        bloqueio_conta = _obter_bloqueio(BloqueioLogin.TIPO_CONTA, username, usuario)
        _acumular(bloqueio_conta, agora, janela, ip, limite, minutos_bloqueio())

    if ip:
        bloqueio_ip = _obter_bloqueio(BloqueioLogin.TIPO_IP, ip)
        _acumular(bloqueio_ip, agora, janela, ip, max_tentativas_ip(), minutos_bloqueio_ip())

    if bloqueio_conta is None:
        return None, limite
    if bloqueio_conta.ativo:
        return bloqueio_conta, 0
    return None, max(limite - bloqueio_conta.falhas, 0)


def _acumular(bloqueio, agora, janela, ip, limite, minutos):
    """Incrementa o contador respeitando a janela deslizante e tranca ao
    estourar o limite. Salva sempre — o objeto pode ter acabado de nascer."""
    expirou = bloqueio.ultima_falha_em is None or (agora - bloqueio.ultima_falha_em) > janela
    # Bloqueio que já venceu também reinicia a contagem: quem esperou os 5
    # minutos começa do zero, não do 3º strike.
    venceu = bloqueio.bloqueado_ate is not None and bloqueio.bloqueado_ate <= agora

    if expirou or venceu:
        bloqueio.falhas = 1
        bloqueio.primeira_falha_em = agora
        bloqueio.bloqueado_ate = None
    else:
        bloqueio.falhas = (bloqueio.falhas or 0) + 1

    bloqueio.ultima_falha_em = agora
    if ip:
        bloqueio.ultimo_ip = ip

    if bloqueio.falhas >= limite and not bloqueio.ativo:
        bloqueio.bloqueado_ate = agora + timezone.timedelta(minutes=minutos)
        bloqueio.total_bloqueios = (bloqueio.total_bloqueios or 0) + 1
        bloqueio.desbloqueado_por = None
        bloqueio.desbloqueado_em = None
        logger.warning(
            'Bloqueio de login aplicado: tipo=%s chave=%s falhas=%s até=%s',
            bloqueio.tipo, bloqueio.chave, bloqueio.falhas, bloqueio.bloqueado_ate,
        )

    bloqueio.save()
    return bloqueio


def registrar_sucesso(request, user):
    """Login completo (já passou pelo 2FA, quando houver): zera os contadores
    da conta e do IP. Sem isso, três erros espalhados ao longo de semanas
    somariam com o próximo erro e trancariam quem só digitou errado."""
    registrar_tentativa(
        request, user.username, TentativaLogin.MOTIVO_SUCESSO, sucesso=True, usuario=user,
    )
    ip = _ip_valido(get_client_ip(request))
    chaves = [(BloqueioLogin.TIPO_CONTA, user.username[:150])]
    if ip:
        chaves.append((BloqueioLogin.TIPO_IP, ip))
    for tipo, chave in chaves:
        BloqueioLogin.objects.filter(tipo=tipo, chave=chave).update(
            falhas=0, bloqueado_ate=None, primeira_falha_em=None,
        )


def desbloquear(bloqueio, por_usuario=None, request=None):
    """Libera manualmente (botão do painel). Zera o contador junto — senão a
    próxima falha já trancaria de novo, e o desbloqueio não teria servido pra
    nada."""
    bloqueio.falhas = 0
    bloqueio.bloqueado_ate = None
    bloqueio.primeira_falha_em = None
    bloqueio.desbloqueado_por = por_usuario
    bloqueio.desbloqueado_em = timezone.now()
    bloqueio.save(update_fields=[
        'falhas', 'bloqueado_ate', 'primeira_falha_em',
        'desbloqueado_por', 'desbloqueado_em', 'atualizado_em',
    ])
    AcaoSeguranca.objects.create(
        acao=(AcaoSeguranca.ACAO_DESBLOQUEIO_CONTA if bloqueio.tipo == BloqueioLogin.TIPO_CONTA
              else AcaoSeguranca.ACAO_DESBLOQUEIO_IP),
        alvo=bloqueio.chave,
        usuario=por_usuario,
        ip_origem=_ip_valido(get_client_ip(request)),
    )
    return bloqueio


def registrar_acao(acao, alvo, usuario=None, detalhe='', request=None):
    return AcaoSeguranca.objects.create(
        acao=acao, alvo=alvo[:150], detalhe=detalhe[:300], usuario=usuario,
        ip_origem=_ip_valido(get_client_ip(request)),
    )


def limpar_registros_antigos(dias=None):
    """Poda o log de tentativas. Chamado pela task Celery `seguranca.limpeza`
    — a tabela cresce com o tráfego de robô, que é justamente o que não para
    de bater."""
    from .models import EventoSeguranca

    dias = int(dias or _cfg('SEGURANCA_RETENCAO_DIAS', 90))
    corte = timezone.now() - timezone.timedelta(days=dias)
    apagadas, _ = TentativaLogin.objects.filter(criado_em__lt=corte).delete()
    eventos, _ = EventoSeguranca.objects.filter(criado_em__lt=corte).delete()
    # Chaves sem bloqueio ativo e sem falha recente não têm mais utilidade.
    limpas, _ = BloqueioLogin.objects.filter(
        ultima_falha_em__lt=corte,
    ).filter(bloqueado_ate__isnull=True).delete()
    return {'tentativas': apagadas, 'eventos': eventos, 'bloqueios': limpas}
