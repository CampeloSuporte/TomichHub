"""
monitoramento/agent_zabbix.py
Camada que liga o Agent NOC ao Zabbix do cliente.

O Zabbix não é cadastrado num lugar próprio: ele já vive nos **acessos do
cliente** (um `Acesso` com "zabbix" no tipo e protocolo HTTP/HTTPS). Este
módulo descobre esse acesso, monta a URL da API, reaproveita o túnel SSH do
ProxyServer quando o Zabbix está em IP privado (mesma engrenagem da aba
Monitoramento) e expõe funções síncronas que as tools do agent chamam via
`sync_to_async`.

Fluxo típico das tools:
    buscar_itens_texto(cliente_id, host='juina', item='painera sinal')
    historico_itens(cliente_id, ['45231'], periodo='6h')
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from urllib.parse import urlparse

from django.core.cache import cache
from django.utils import timezone

from . import chart, services

logger = logging.getLogger(__name__)

_CACHE_TTL_CFG = 1800   # 30 min — qual acesso/URL respondeu como Zabbix


class ZabbixIndisponivel(Exception):
    """Nenhum Zabbix utilizável para este cliente (não cadastrado ou sem rota)."""


class _Cfg:
    """Config compatível com monitoramento.services (duck typing de ZabbixConfig)."""

    def __init__(self, ident, url, usuario='', senha='', api_token=None, origem=''):
        self.id        = ident
        self.url       = url
        self.usuario   = usuario
        self.senha     = senha
        self.api_token = api_token
        self.ativo     = True
        self.origem    = origem   # texto para mostrar ao operador


# ──────────────────────────────────────────────────────────────
# Descoberta do Zabbix a partir dos acessos do cliente
# ──────────────────────────────────────────────────────────────

def _urls_do_acesso(acesso) -> list[str]:
    """
    Monta as URLs candidatas da API a partir do cadastro do acesso.

    O campo `host` do acesso é escrito de formas variadas na prática:
        187.84.126.249:3032/zabbix · 172.31.100.14/zabbix/ · 45.169.153.145
    Por isso a porta do cadastro só é aplicada quando o host ainda não traz
    uma, e a variante com sufixo `/zabbix` é oferecida quando não há path.
    """
    host = (acesso.host or '').strip()
    if not host:
        return []

    proto  = (acesso.protocolo or '').upper()
    scheme = 'https' if proto == 'HTTPS' else 'http'

    if '://' in host:
        parsed = urlparse(host)
        scheme = parsed.scheme or scheme
        host   = parsed.netloc + parsed.path

    host = host.strip('/')
    if '/' in host:
        hostpart, path = host.split('/', 1)
        path = '/' + path.strip('/')
    else:
        hostpart, path = host, ''

    if ':' not in hostpart:
        porta = acesso.porta
        if porta and int(porta) not in (80, 443):
            hostpart = f'{hostpart}:{int(porta)}'

    base = f'{scheme}://{hostpart}{path}'
    urls = [base]
    if not path:
        urls.append(f'{base}/zabbix')
    return urls


def _candidatos(cliente_id) -> list[_Cfg]:
    """Todas as configurações plausíveis de Zabbix do cliente, em ordem de aposta."""
    from clientes.models import Acesso

    from .models import ZabbixConfig

    cands: list[_Cfg] = []

    zc = ZabbixConfig.objects.filter(cliente_id=cliente_id, ativo=True).first()
    if zc:
        cands.append(_Cfg(f'zbxcfg{zc.id}', zc.url.rstrip('/'), zc.usuario, zc.senha,
                          zc.api_token, origem='configuração de Monitoramento'))

    acessos = list(Acesso.objects.filter(
        cliente_id=cliente_id,
        tipo__icontains='zabbix',
        protocolo__in=['HTTP', 'HTTPS'],
    ))
    # Acesso com path /zabbix explícito tende a ser o front web certo
    acessos.sort(key=lambda a: 0 if 'zabbix' in (a.host or '').lower() else 1)

    for a in acessos:
        for url in _urls_do_acesso(a):
            cands.append(_Cfg(f'acesso{a.id}', url, a.usuario or '', a.senha or '',
                              origem=f'acesso "{a.tipo}" (ID {a.id})'))
    return cands


def _preparar(cfg: _Cfg, cliente_id):
    """Aplica túnel SSH/rota (mesma lógica da aba Monitoramento) e testa a API."""
    from .views import _get_config_com_tunel, _responde_como_zabbix

    cfg_final, _tunel = _get_config_com_tunel(cfg, cliente_id)
    url = cfg_final.url.rstrip('/')

    if not _responde_como_zabbix(url):
        raise ZabbixIndisponivel(f'{url} não respondeu como API Zabbix')

    pronto = _Cfg(cfg.id, url, cfg.usuario, cfg.senha, cfg.api_token, cfg.origem)
    services._get_auth_token(pronto)   # valida credenciais antes de devolver
    return pronto


def resolver_config(cliente_id) -> _Cfg:
    """
    Devolve uma config de Zabbix pronta para uso (já com túnel aplicado, se
    necessário) para o cliente. Levanta ZabbixIndisponivel com o motivo real
    quando não há como chegar em nenhum Zabbix.
    """
    cands = _candidatos(cliente_id)
    if not cands:
        raise ZabbixIndisponivel(
            'Este cliente não tem Zabbix cadastrado. Cadastre um acesso HTTP/HTTPS '
            'com "Zabbix" no tipo (usuário e senha da interface web) na aba Acessos.'
        )

    # Última combinação que funcionou primeiro — evita reprovar todas a cada pergunta
    ck    = f'zbx_agent_cfg:{cliente_id}'
    ident = cache.get(ck)
    if ident:
        cands.sort(key=lambda c: 0 if c.id == ident else 1)

    erros = []
    for cand in cands:
        try:
            pronto = _preparar(cand, cliente_id)
            cache.set(ck, cand.id, _CACHE_TTL_CFG)
            logger.info('[ZBX-AGENT] cliente %s → %s (%s)', cliente_id, pronto.url, pronto.origem)
            return pronto
        except Exception as exc:
            erros.append(f'{cand.url}: {exc}')
            continue

    cache.delete(ck)
    raise ZabbixIndisponivel(
        'Não consegui falar com o Zabbix deste cliente. Tentativas:\n- '
        + '\n- '.join(erros[:6])
    )


# ──────────────────────────────────────────────────────────────
# Período: texto → janela absoluta
# ──────────────────────────────────────────────────────────────

_RE_DUR = re.compile(r'^\s*(\d+)\s*([mhdws])\s*$', re.I)
_FORMATOS = (
    '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M',
    '%Y-%m-%d', '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y', '%d/%m %H:%M',
)


def _parse_datahora(texto: str) -> int | None:
    """'2026-08-19 14:30', '19/08/2026 14:30', '14:30', 'agora' → epoch."""
    t = (texto or '').strip()
    if not t:
        return None
    if t.lower() in ('agora', 'now'):
        return int(time.time())

    tz = timezone.get_current_timezone()
    for fmt in _FORMATOS:
        try:
            dt = datetime.strptime(t, fmt)
        except ValueError:
            continue
        if fmt == '%d/%m %H:%M':
            dt = dt.replace(year=timezone.localtime().year)
        return int(timezone.make_aware(dt, tz).timestamp())

    # Só a hora ("14:30") → hoje
    try:
        hm = datetime.strptime(t, '%H:%M')
        hoje = timezone.localtime()
        dt = hoje.replace(hour=hm.hour, minute=hm.minute, second=0, microsecond=0)
        return int(dt.timestamp())
    except ValueError:
        return None


def parse_janela(periodo: str = '', inicio: str = '', fim: str = '') -> tuple:
    """
    Converte o que o modelo mandou em (ts_from, ts_till).

    periodo : '30m', '6h', '2d', '1w' (padrão: 6h)
    inicio  / fim : data/hora absolutas; se só `inicio` vier, o fim é `inicio + periodo`.
    """
    agora = int(time.time())

    dur = 6 * 3600
    m = _RE_DUR.match(periodo or '')
    if m:
        n = int(m.group(1))
        dur = n * {'m': 60, 'h': 3600, 'd': 86400, 'w': 604800, 's': 1}[m.group(2).lower()]

    ts_ini = _parse_datahora(inicio)
    ts_fim = _parse_datahora(fim)

    if ts_ini and ts_fim:
        pass
    elif ts_ini:
        ts_fim = min(ts_ini + dur, agora)
    elif ts_fim:
        ts_ini = ts_fim - dur
    else:
        ts_fim, ts_ini = agora, agora - dur

    if ts_fim <= ts_ini:
        ts_fim = ts_ini + 3600
    return ts_ini, min(ts_fim, agora)


def _fmt_janela(ts_from: int, ts_till: int) -> str:
    d1 = chart._dt_local(ts_from)
    d2 = chart._dt_local(ts_till)
    if d1.date() == d2.date():
        return f"{d1:%d/%m/%Y %H:%M} → {d2:%H:%M}"
    return f"{d1:%d/%m/%Y %H:%M} → {d2:%d/%m/%Y %H:%M}"


# ──────────────────────────────────────────────────────────────
# Operações usadas pelas tools do agent
# ──────────────────────────────────────────────────────────────

def buscar_itens_texto(cliente_id, host: str = '', item: str = '',
                       limit: int = 25) -> str:
    """Busca itens no Zabbix e devolve texto pronto para o LLM raciocinar."""
    cfg = resolver_config(cliente_id)

    if not host and not item:
        hosts = services.buscar_hosts(cfg, '', limit=60)
        if not hosts:
            return 'Nenhum host monitorado neste Zabbix.'
        linhas = [f"- {h['name']} (hostid {h['hostid']})" for h in hosts]
        return (f"Zabbix: {cfg.url} — {len(hosts)} hosts monitorados:\n"
                + '\n'.join(linhas))

    itens = services.buscar_itens(cfg, host, item, limit=limit)

    if not itens and host:
        # Host não bateu ou o termo do item só existe em outro host — mostra o
        # que existe naquele host para o modelo escolher.
        hosts = services.buscar_hosts(cfg, host, limit=15)
        if hosts:
            amostra = services.buscar_itens(cfg, host, '', limit=40)
            linhas = [f"- itemid {i['itemid']} | {i['hostname']} | {i['name']}"
                      for i in amostra]
            return (
                f"Nenhum item com '{item}' em '{host}'. Hosts que casam com '{host}': "
                + ', '.join(h['name'] for h in hosts)
                + (f"\nAlguns itens desses hosts:\n" + '\n'.join(linhas) if linhas else '')
            )
        return (f"Nenhum host com '{host}' no Zabbix ({cfg.url}). "
                f"Use zabbix_buscar_item sem o parâmetro host para listar os hosts monitorados.")

    if not itens:
        return f"Nenhum item com '{item}' no Zabbix ({cfg.url})."

    linhas = []
    for i in itens:
        ultimo = ''
        if i['lastclock']:
            ultimo = (f" | último: {chart.formatar_valor(_float(i['lastvalue']), i['units'])}"
                      f" em {chart._dt_local(i['lastclock']):%d/%m %H:%M}")
        linhas.append(
            f"- itemid {i['itemid']} | host: {i['hostname']} | {i['name']}"
            f" | key: {i['key_']}{ultimo}"
        )
    return (f"{len(linhas)} itens encontrados no Zabbix ({cfg.url}):\n"
            + '\n'.join(linhas)
            + "\n\nUse zabbix_historico com os itemids relevantes para ver o histórico e o gráfico.")


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def historico_itens(cliente_id, itemids: list, periodo: str = '6h',
                    inicio: str = '', fim: str = '', titulo: str = '',
                    marcador: str = '', grafico: bool = True) -> dict:
    """
    Histórico de 1..4 itens numa janela, com resumo textual e PNG do gráfico.

    Retorna {'texto': str, 'png': bytes|None, 'legenda': str}
    """
    cfg = resolver_config(cliente_id)
    ts_from, ts_till = parse_janela(periodo, inicio, fim)
    marcador_ts = _parse_datahora(marcador) if marcador else None

    itemids = [str(i) for i in (itemids or [])][:4]
    if not itemids:
        return {'texto': 'Nenhum itemid informado.', 'png': None, 'legenda': ''}

    series  = []
    blocos  = []
    unidades = set()

    for itemid in itemids:
        try:
            dados = services.historico_janela(cfg, itemid, ts_from, ts_till,
                                              max_pontos=320)
        except Exception as exc:
            blocos.append(f"❌ item {itemid}: {exc}")
            continue

        # "Interface 100GE0/0/2(LINK-POP-PAINEIRAS): Bits sent" já é longo demais
        # para a legenda do gráfico — o prefixo "Interface " não informa nada.
        nome_curto = re.sub(r'^Interface\s+', '', dados['name'] or '') or f"item {itemid}"
        rotulo = f"{dados['hostname']} — {nome_curto}" if dados['hostname'] else nome_curto

        if not dados['pontos']:
            blocos.append(
                f"⚠️ {rotulo}: sem dados coletados entre {_fmt_janela(ts_from, ts_till)} "
                f"(item pode estar sem coleta nesse período ou o histórico já expirou)."
            )
            continue

        st = dados['stats']
        u  = dados['units']
        unidades.add(u)
        fonte_txt = 'valores brutos' if dados['fonte'] == 'history' else 'médias horárias (trends)'
        blocos.append(
            f"**{rotulo}** (itemid {itemid} — {fonte_txt}, {st['n']} amostras)\n"
            f"  mín: {chart.formatar_valor(st['min'], u)} · "
            f"méd: {chart.formatar_valor(st['avg'], u)} · "
            f"máx: {chart.formatar_valor(st['max'], u)} · "
            f"último: {chart.formatar_valor(st['ultimo'], u)} "
            f"({chart._dt_local(st['ultimo_t']):%d/%m %H:%M})\n"
            f"  amostras: {_amostras_txt(dados['pontos'], u)}"
        )
        series.append({'nome': rotulo, 'pontos': dados['pontos'], 'units': u})

    janela_txt = _fmt_janela(ts_from, ts_till)
    texto = f"Zabbix — janela {janela_txt}\n\n" + '\n\n'.join(blocos)

    png = None
    legenda = ''
    if grafico and series:
        try:
            titulo_final = titulo or (series[0]['nome'] if len(series) == 1
                                      else 'Histórico Zabbix')
            png = chart.render_series_png(
                series,
                titulo=titulo_final,
                subtitulo=janela_txt + (f' · marcador: {marcador}' if marcador_ts else ''),
                units=(list(unidades)[0] if len(unidades) == 1 else ''),
                marcador_ts=marcador_ts,
            )
            legenda = f"📈 {titulo_final} — {janela_txt}"
            texto += "\n\n🖼️ Gráfico gerado e enviado ao usuário (não descreva pixel a pixel; comente os números)."
        except Exception as exc:
            logger.warning('Falha ao renderizar gráfico Zabbix: %s', exc)
            texto += f"\n\n(não foi possível renderizar o gráfico: {exc})"

    return {'texto': texto, 'png': png, 'legenda': legenda}


def _amostras_txt(pontos: list, units: str, n: int = 6) -> str:
    """Algumas amostras espalhadas na janela — dão ao modelo o formato da curva."""
    if not pontos:
        return '—'
    if len(pontos) <= n:
        escolhidos = pontos
    else:
        passo = (len(pontos) - 1) / (n - 1)
        escolhidos = [pontos[int(round(i * passo))] for i in range(n)]
    return ' | '.join(
        f"{chart._dt_local(p['t']):%d/%m %H:%M}={chart.formatar_valor(p['v'], units)}"
        for p in escolhidos
    )
