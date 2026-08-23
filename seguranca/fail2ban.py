"""
Ponte com o fail2ban — quem realmente bane o IP no firewall.

O CRM não guarda espelho da blacklist: a fonte da verdade é o
`fail2ban-client`, porque é ele que fala com o iptables. Espelhar em tabela
daria um painel que mente sempre que alguém mexer no fail2ban por fora (e um
IP "liberado" no CRM continuando banido no firewall é o pior tipo de bug de
segurança: o operador acha que resolveu).

O gunicorn/daphne rodam como www-data, então todo comando vai por
`sudo -n` — a regra vive em /etc/sudoers.d/crm-fail2ban e libera SÓ o
binário do fail2ban-client.
"""
import logging
import os
import re
import shutil
import subprocess

from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT = 10
CAMINHO_LOG = '/var/log/fail2ban.log'

# `crm-login` é a jail alimentada pelo próprio CRM (SEGURANCA_AUTH_LOG);
# `sshd` é a do SSH do servidor.
JAIL_SSH = 'sshd'
JAIL_CRM = 'crm-login'


def _binario():
    return (
        getattr(settings, 'FAIL2BAN_CLIENT', None)
        or shutil.which('fail2ban-client')
        or '/usr/bin/fail2ban-client'
    )


def _comando(args):
    """Prefixa com sudo quando não estamos rodando como root (caso normal:
    www-data)."""
    base = [_binario()] + list(args)
    if os.geteuid() != 0:
        return ['sudo', '-n'] + base
    return base


def _executar(args):
    """Roda o fail2ban-client. Devolve `(ok, saida)`; nunca levanta exceção —
    o painel precisa abrir mesmo com o fail2ban parado, mostrando o motivo."""
    try:
        proc = subprocess.run(
            _comando(args), capture_output=True, text=True, timeout=TIMEOUT,
        )
    except FileNotFoundError:
        return False, 'fail2ban-client não encontrado neste servidor.'
    except subprocess.TimeoutExpired:
        return False, 'fail2ban-client não respondeu no tempo esperado.'
    except Exception as e:
        logger.exception('Erro ao executar fail2ban-client')
        return False, str(e)

    saida = (proc.stdout or '').strip()
    erro = (proc.stderr or '').strip()
    if proc.returncode != 0:
        return False, erro or saida or f'fail2ban-client retornou {proc.returncode}'
    return True, saida


def disponivel():
    """True se o fail2ban está instalado, rodando e alcançável por este
    processo (inclui a permissão do sudo)."""
    ok, _ = _executar(['ping'])
    return ok


def diagnostico():
    """Mensagem pronta pro painel quando algo impede o acesso ao fail2ban —
    instalado?, serviço no ar?, sudo liberado? Cada caso pede uma correção
    diferente, então vale distinguir."""
    if not shutil.which(_binario()) and not os.path.exists(_binario()):
        return 'fail2ban não está instalado (apt install fail2ban).'
    ok, saida = _executar(['ping'])
    if ok:
        return ''
    if 'sudo' in saida.lower() or 'password' in saida.lower():
        return 'Sem permissão de sudo para o fail2ban-client (ver /etc/sudoers.d/crm-fail2ban).'
    if 'socket' in saida.lower() or 'could not' in saida.lower():
        return 'Serviço fail2ban parado (systemctl start fail2ban).'
    return saida or 'fail2ban indisponível.'


def listar_jails():
    ok, saida = _executar(['status'])
    if not ok:
        return []
    m = re.search(r'Jail list:\s*(.*)', saida)
    if not m:
        return []
    return [j.strip() for j in m.group(1).split(',') if j.strip()]


def _parse_status_jail(saida):
    def _int(padrao):
        m = re.search(padrao, saida)
        return int(m.group(1)) if m else 0

    banidos_m = re.search(r'Banned IP list:\s*(.*)', saida)
    banidos = [ip for ip in (banidos_m.group(1).split() if banidos_m else []) if ip]
    arquivos_m = re.search(r'File list:\s*(.*)', saida)
    return {
        'falhas_atuais': _int(r'Currently failed:\s*(\d+)'),
        'falhas_total': _int(r'Total failed:\s*(\d+)'),
        'banidos_atuais': _int(r'Currently banned:\s*(\d+)'),
        'banidos_total': _int(r'Total banned:\s*(\d+)'),
        'ips': banidos,
        'arquivos': (arquivos_m.group(1).strip() if arquivos_m else ''),
    }


def status_jail(jail):
    ok, saida = _executar(['status', jail])
    if not ok:
        return {'jail': jail, 'erro': saida, 'ips': [], 'banidos_atuais': 0,
                'banidos_total': 0, 'falhas_atuais': 0, 'falhas_total': 0, 'arquivos': ''}
    dados = _parse_status_jail(saida)
    dados['jail'] = jail
    dados['erro'] = ''
    return dados


def resumo():
    """Status de todas as jails, pro painel. Lista vazia = fail2ban fora."""
    return [status_jail(j) for j in listar_jails()]


def banidos_por_ip():
    """{ip: [jails...]} — o mesmo IP costuma estar banido em mais de uma jail
    (ex.: bateu no SSH e no login do CRM)."""
    mapa = {}
    for jail in resumo():
        for ip in jail['ips']:
            mapa.setdefault(ip, []).append(jail['jail'])
    return mapa


def desbanir(ip, jail=None):
    """Tira o IP do banimento. Sem `jail`, usa o `unban` global (remove de
    todas as jails de uma vez)."""
    args = ['set', jail, 'unbanip', ip] if jail else ['unban', ip]
    ok, saida = _executar(args)
    if not ok:
        return False, saida
    return True, saida or f'{ip} liberado.'


def banir(ip, jail=JAIL_SSH):
    ok, saida = _executar(['set', jail, 'banip', ip])
    if not ok:
        return False, saida
    return True, saida or f'{ip} banido em {jail}.'


_RE_LOG = re.compile(
    r'^(?P<data>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+'
    r'fail2ban\.\w+\s*\[?\d*\]?:\s*(?P<nivel>\w+)\s+'
    r'\[(?P<jail>[^\]]+)\]\s+(?P<acao>Ban|Unban|Restore Ban|Found)\s+(?P<ip>\S+)'
)


def historico(limite=200, incluir_found=False):
    """Últimos Ban/Unban lidos de /var/log/fail2ban.log.

    Ler o arquivo (em vez de manter tabela) mantém o painel honesto: mostra
    inclusive banimento feito por outra pessoa direto no terminal. `Found` (a
    falha individual, antes do ban) fica de fora por padrão — é ruidoso.
    """
    caminho = getattr(settings, 'FAIL2BAN_LOG', CAMINHO_LOG)
    if not os.path.exists(caminho):
        return []
    eventos = []
    try:
        with open(caminho, 'r', errors='replace') as fh:
            linhas = fh.readlines()[-8000:]
    except PermissionError:
        return []
    except Exception:
        logger.exception('Falha ao ler %s', caminho)
        return []

    for linha in reversed(linhas):
        m = _RE_LOG.match(linha)
        if not m:
            continue
        acao = m.group('acao')
        if acao == 'Found' and not incluir_found:
            continue
        eventos.append({
            'data': m.group('data'),
            'jail': m.group('jail'),
            'acao': acao,
            'ip': m.group('ip'),
        })
        if len(eventos) >= limite:
            break
    return eventos
