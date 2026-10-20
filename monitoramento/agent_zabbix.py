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
import statistics
import time
from collections import Counter
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


# ──────────────────────────────────────────────────────────────
# Sinal óptico antes × depois do rompimento
# ──────────────────────────────────────────────────────────────
#
# Reproduz o relatório que o NOC fazia à mão depois de um reparo de fibra:
# para cada ramo (lane) do transceptor, a leitura de RX antes do rompimento e a
# leitura atual, e quanto mudou. O rompimento é achado no próprio histórico —
# transceptor sem luz reporta -40 dBm (piso do DDM), então a queda é o trecho
# em que o RX fica nesse piso.

_PISO_SEM_LUZ    = -38.0   # abaixo disso o DDM está no piso (-40 dBm / -inf) = sem luz
_QUEDA_RELATIVA  = 10.0    # ou 10 dB abaixo da referência da janela (enlaces de sinal alto)
_JANELA_ANTES    = 6 * 3600   # patamar "antes": leituras das 6h anteriores à queda…
_IGNORAR_ANTES   = 1800       # …menos os 30 min finais (a fibra costuma degradar antes de romper)
_N_DEPOIS        = 12         # patamar "depois": ~1h de leituras mais recentes (coleta de 5 min)
_MIN_LEITURAS_QUEDA = 2     # queda de 1 leitura só é soluço do DDM
_UNIR_QUEDAS     = 1800    # quedas a menos de 30 min uma da outra = um evento (emenda/flap)
_OSCILACAO_DB    = 0.6     # até isso é oscilação da leitura (DDM Huawei anda em degraus de ~0,5 dB)
_PERDA_RELEVANTE = 1.0     # perda acima disso após o reparo merece verificar a fusão

_RE_RAMO  = re.compile(r'\s*-?\s*Ramo\s*(\d+)\s*$', re.I)
_RE_IFACE = re.compile(r'Interface\s*-?\s*(.+)$', re.I)


def _serie_bruta(cfg, itemid, ts_from: int, ts_till: int) -> dict:
    """
    Série crua (sem reamostrar) de um item: o rompimento precisa da leitura
    exata de cada coleta, e historico_janela() reamostra e prefere trends em
    janelas longas. Cai em trends (médias horárias) se o history expirou.
    """
    auth = services._get_auth_token(cfg)
    info = services._rpc(cfg.url, 'item.get', {
        'itemids': [str(itemid)],
        'output':  ['name', 'units', 'value_type'],
        'selectHosts': ['name'],
    }, auth, config=cfg)
    if not info:
        raise Exception(f'item {itemid} não encontrado no Zabbix')
    meta = info[0]
    vt   = int(meta.get('value_type', 0))
    if vt not in (0, 3):
        raise Exception(f'item "{meta.get("name")}" não é numérico')

    # DESC + reverse: se o limite cortar, perde-se o começo da janela, não o "agora"
    bruto = services._rpc(cfg.url, 'history.get', {
        'itemids': [str(itemid)], 'history': vt,
        'time_from': int(ts_from), 'time_till': int(ts_till),
        'sortfield': 'clock', 'sortorder': 'DESC', 'limit': 20000,
    }, auth, config=cfg)
    pontos = [(int(p['clock']), float(p['value'])) for p in reversed(bruto)]
    fonte  = 'history'
    if not pontos:
        bruto = services._rpc(cfg.url, 'trend.get', {
            'itemids': [str(itemid)],
            'time_from': int(ts_from), 'time_till': int(ts_till), 'limit': 20000,
        }, auth, config=cfg)
        pontos = sorted((int(p['clock']), float(p['value_avg'])) for p in bruto)
        fonte  = 'trends'

    hosts = meta.get('hosts') or []
    return {
        'itemid':   str(itemid),
        'name':     meta.get('name', ''),
        'units':    meta.get('units', ''),
        'hostname': hosts[0].get('name', '') if hosts else '',
        'pontos':   pontos,
        'fonte':    fonte,
    }


def _corte_sem_luz(pontos: list) -> float:
    """Valor a partir do qual a leitura conta como sem luz para esta série."""
    vals = sorted(v for _, v in pontos)
    ref  = vals[int(len(vals) * 0.75)] if vals else _PISO_SEM_LUZ
    return max(_PISO_SEM_LUZ, ref - _QUEDA_RELATIVA)


def _quedas(pontos: list, corte: float) -> list:
    """
    Trechos sem luz: [{'ini', 'retorno'}] — `ini` é a 1ª coleta sem luz,
    `retorno` a 1ª coleta com luz depois (None = continua sem luz).
    Trechos muito próximos são unidos (a emenda costuma piscar).
    """
    trechos = []
    atual = None
    for t, v in pontos:
        if v <= corte:
            if atual is None:
                atual = {'ini': t, 'retorno': None, 'n': 0}
                trechos.append(atual)
            atual['n'] += 1
        elif atual is not None:
            atual['retorno'] = t
            atual = None

    # Uma leitura isolada em -40 no meio de leituras boas é soluço do DDM, não
    # rompimento (visto em Guarantã: 1 coleta em -40 às 13:42, o rompimento
    # real foi 13:00→13:25). Exceção: a última leitura — o link pode estar caindo agora.
    trechos = [tr for tr in trechos if tr['n'] >= _MIN_LEITURAS_QUEDA or tr['retorno'] is None]

    unidos = []
    for tr in trechos:
        if unidos and unidos[-1]['retorno'] and tr['ini'] - unidos[-1]['retorno'] < _UNIR_QUEDAS:
            unidos[-1]['retorno'] = tr['retorno']
        else:
            unidos.append(dict(tr))
    return unidos


def _lacuna(pontos: list) -> dict | None:
    """Maior buraco de coleta da janela (equipamento inalcançável na queda)."""
    if len(pontos) < 3:
        return None
    passos = sorted(b[0] - a[0] for a, b in zip(pontos, pontos[1:]))
    minimo = max(_UNIR_QUEDAS, passos[len(passos) // 2] * 6)
    maior  = None
    for a, b in zip(pontos, pontos[1:]):
        if b[0] - a[0] >= minimo and (maior is None or b[0] - a[0] > maior['retorno'] - maior['ini']):
            maior = {'ini': a[0] + 1, 'retorno': b[0], 'sem_coleta': True}
    return maior


def _leitura_tipica(vals: list):
    """
    Patamar de uma sequência de leituras do DDM: a leitura mais frequente (o DDM
    é quantizado e alterna entre 2-3 valores). Empate → a pior (mais baixa).
    Sem repetição (médias de trends) → mediana.
    """
    if not vals:
        return None
    cont = Counter(round(v, 2) for v in vals)
    maior = max(cont.values())
    if maior == 1:
        return statistics.median_low(vals)
    return min(v for v, n in cont.items() if n == maior)


def _fmt_dur(seg: int) -> str:
    seg = max(int(seg), 0)
    d, r = divmod(seg, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d}d {h:02d}h{m:02d}"
    if h:
        return f"{h}h{m:02d}"
    return f"{m}min"


def _fmt_dbm(v) -> str:
    if v is None:
        return 'sem leitura'
    if v <= _PISO_SEM_LUZ:
        return f"{v:.2f} (sem luz)"
    return f"{v:.2f}"


def _rotulos(serie: dict) -> tuple:
    """('SWITCH-HUAWEI-JURUENA', '100GE0/0/2 - SW-DT-REG-JUINA', 'Ramo 1')"""
    host = (serie['hostname'] or '').split(' - ')[0].strip() or serie['hostname']
    nome = serie['name'] or f"item {serie['itemid']}"
    m = _RE_RAMO.search(nome)
    ramo = f"Ramo {m.group(1)}" if m else ''
    sem_ramo = _RE_RAMO.sub('', nome).strip(' -')
    mi = _RE_IFACE.search(sem_ramo)
    iface = mi.group(1).strip(' -') if mi else sem_ramo
    return host, iface, ramo or iface


# Palavras que não identificam o site no nome do host ("sw-hu-juruena" × "SWITCH-HUAWEI-JURUENA")
_HOST_GENERICO = {'sw', 'switch', 'hu', 'huawei', 'dt', 'datacom', 'mk', 'mikrotik', 'cisco',
                  'juniper', 'olt', 'rb', 'ccr', 'router', 'roteador', 'bng', 'bras', 'reg', 'pop'}


def _hosts_do_pedido(cfg, host: str) -> list:
    """
    Hosts do Zabbix para o nome que o modelo mandou. Tenta o nome como veio
    (e com hífens no lugar de espaços); depois os hosts que contêm TODAS as
    palavras que identificam o site; só então uma palavra sozinha.
    """
    for h in dict.fromkeys([host, re.sub(r'\s+', '-', host.strip())]):
        achados = services.buscar_hosts(cfg, h, limit=10)
        if achados:
            return achados
    palavras = [w for w in re.split(r'[\s\-_.·/]+', host.lower())
                if len(w) >= 3 and w not in _HOST_GENERICO]
    por_palavra = {w: services.buscar_hosts(cfg, w, limit=50) for w in palavras}
    for achados in por_palavra.values():
        todos = [x for x in achados if all(w in x['name'].lower() for w in palavras)]
        if todos:
            return todos
    for achados in por_palavra.values():
        if achados:
            return achados[:10]
    return []


# Palavras do pedido que não ajudam a achar a porta ("sinal da porta nova santa")
_ITEM_GENERICO = {'rx', 'tx', 'sinal', 'porta', 'interface', 'potencia', 'potência', 'link',
                  'enlace', 'de', 'do', 'da', 'para', 'com'}


def _itens_dbm(cfg, host: str) -> list:
    """Todos os itens em dBm do host — filtro pela unidade, não pelo texto do nome."""
    auth = services._get_auth_token(cfg)
    hosts = _hosts_do_pedido(cfg, host)
    if hosts:
        itens = services._rpc(cfg.url, 'item.get', {
            'hostids': [x['hostid'] for x in hosts],
            'output':  ['itemid', 'name', 'key_', 'lastvalue'],
            'filter':  {'status': '0', 'units': ['dBm', 'dbm', 'DBm', 'DBM']},
            'limit':   2000,
        }, auth, config=cfg)
        return [i for i in itens if not i.get('name', '').lower().startswith('limite')]
    return []


def itens_de_sinal(cfg, host: str, item: str = '') -> tuple:
    """
    (itemids, portas_do_host) de potência óptica de um host — RX por padrão, TX
    só se pedido. A porta casa por palavras da descrição; se nem todas casam
    ("nova santa helena" × "P2P-SW-NOVA-SANTA"), as do fim vão sendo largadas.
    """
    quer_tx = bool(re.search(r'\btx\b|transmit', item or '', re.I))
    lado = [i for i in _itens_dbm(cfg, host)
            if bool(re.search(r'recebida|\brx|rxpower', i['name'] + ' ' + i.get('key_', ''), re.I))
            != quer_tx]
    portas = sorted({_RE_RAMO.sub('', i['name']).split('Interface')[-1].strip(' -') for i in lado})

    termos = [t for t in re.split(r'[\s\-_]+', (item or '').lower())
              if len(t) >= 2 and t not in _ITEM_GENERICO]
    escolhidos = lado if not termos else []
    for n in range(len(termos), 0, -1):
        escolhidos = [i for i in lado
                      if all(t in (i['name'] + ' ' + i.get('key_', '')).lower() for t in termos[:n])]
        if escolhidos:
            break
    return [i['itemid'] for i in sorted(escolhidos, key=lambda i: i['name'])], portas


def comparar_sinal(cliente_id, itemids: list | None = None, buscas: list | None = None,
                   rompimento: str = '', periodo: str = '3d') -> str:
    """
    Sinal antes do rompimento × sinal atual, por ramo, no formato do relatório
    manual do NOC (SINAL DE RECEPÇÃO ANTES / DEPOIS + diferença e resumo).

    itemids    : itens de potência (dBm) já conhecidos
    buscas     : [{'host': 'juruena', 'item': 'reg-juina'}, ...] — acha os itens de RX
    rompimento : hora aproximada do rompimento (opcional — sem ela é detectado)
    periodo    : quanto olhar para trás procurando a queda (padrão 3d, máx 30d)
    """
    cfg = resolver_config(cliente_id)

    ids = [str(i).strip() for i in (itemids or []) if str(i).strip()]
    avisos = []
    for b in (buscas or []):
        b = b if isinstance(b, dict) else {'host': str(b)}
        h, it = (b.get('host') or '').strip(), (b.get('item') or '').strip()
        if not h:
            continue
        achados, portas = itens_de_sinal(cfg, h, it)
        if not achados and portas:
            avisos.append(
                f"⚠️ host '{h}' TEM sinal óptico monitorado, mas nenhuma porta casou com '{it}'. "
                "Portas com RX nesse host: " + '; '.join(portas[:30])
                + ". Chame zabbix_comparar_sinal de novo com o item = descrição da porta certa "
                  "antes de responder."
            )
        elif not achados:
            avisos.append(
                f"⚠️ nenhum item de sinal (dBm) achado com host '{h}'" + (f" e item '{it}'" if it else '')
                + " — isso NÃO quer dizer que não é monitorado: confira o nome do host com "
                  "zabbix_buscar_item e chame de novo com o nome certo antes de responder."
            )
        ids.extend(achados)
    ids = list(dict.fromkeys(ids))[:16]
    if not ids:
        return '\n'.join(avisos) or 'Nenhum item informado (passe itemids ou buscas com host/item).'

    m = _RE_DUR.match(periodo or '')
    dur = 3 * 86400
    if m:
        dur = int(m.group(1)) * {'m': 60, 'h': 3600, 'd': 86400, 'w': 604800, 's': 1}[m.group(2).lower()]
    dur = min(max(dur, 3600), 30 * 86400)
    agora = int(time.time())
    ts_romp = _parse_datahora(rompimento) if rompimento else None
    ts_from = min(agora - dur, (ts_romp or agora) - 6 * 3600)

    series = []
    for iid in ids:
        try:
            s = _serie_bruta(cfg, iid, ts_from, agora)
        except Exception as exc:
            avisos.append(f"❌ item {iid}: {exc}")
            continue
        if not s['pontos']:
            avisos.append(f"⚠️ {s['hostname']} — {s['name']}: sem coletas na janela")
            continue
        s['corte'] = _corte_sem_luz(s['pontos'])
        s['quedas'] = _quedas(s['pontos'], s['corte'])
        s['host'], s['iface'], s['ramo'] = _rotulos(s)
        series.append(s)
    if not series:
        return '\n'.join(avisos)

    # Agrupa por porta (host + interface): os ramos de uma porta rompem juntos
    grupos = {}
    for s in series:
        grupos.setdefault((s['host'], s['iface']), []).append(s)

    def _evento(ss: list) -> dict | None:
        """Queda da porta: a última (ou a que casa com `rompimento`), unindo os ramos."""
        cands = []
        for s in ss:
            qs = s['quedas']
            if ts_romp:
                qs = [q for q in qs if (q['retorno'] or agora) >= ts_romp - 3600]
                q = qs[0] if qs else None
            else:
                q = qs[-1] if qs else None
            if q:
                cands.append(q)
        if not cands:
            return None
        ini = min(q['ini'] for q in cands)
        rets = [q['retorno'] for q in cands]
        return {'ini': ini, 'retorno': None if None in rets else max(rets)}

    eventos = {k: _evento(v) for k, v in grupos.items()}
    # Porta sem queda própria (ex.: o equipamento ficou sem coleta) herda a do enlace
    comuns = [e for e in eventos.values() if e]
    for k, ss in grupos.items():
        if eventos[k]:
            continue
        if comuns:
            eventos[k] = {'ini': min(e['ini'] for e in comuns),
                          'retorno': max((e['retorno'] or agora) for e in comuns), 'herdado': True}
        else:
            lac = _lacuna(ss[0]['pontos'])
            if lac and (not ts_romp or lac['retorno'] >= ts_romp - 3600):
                eventos[k] = lac
            elif ts_romp:
                eventos[k] = {'ini': ts_romp, 'retorno': ts_romp, 'informado': True}

    blocos = []
    for (host, iface), ss in grupos.items():
        ev = eventos[(host, iface)]
        ss.sort(key=lambda s: (s['ramo'] if s['ramo'].startswith('Ramo') else 'z' + s['ramo']))
        cab = f"(SINAL)\n{host} · {iface}"
        if not ev:
            blocos.append(
                cab + f"\nNenhum rompimento (sinal em -40 dBm / sem luz) nos últimos {_fmt_dur(dur)}."
                "\nInforme a hora do rompimento (parâmetro `rompimento`) ou aumente o `periodo`."
                + "\nAtual: " + ' · '.join(f"{s['ramo']}: {_fmt_dbm(s['pontos'][-1][1])}" for s in ss)
            )
            continue

        linhas_antes, linhas_depois, linhas_dif, deltas = [], [], [], []
        ainda_fora = False
        for s in ss:
            ok = [(t, v) for t, v in s['pontos'] if v > s['corte']]
            antes = [v for t, v in ok
                     if ev['ini'] - _JANELA_ANTES <= t < ev['ini'] - _IGNORAR_ANTES]
            if not antes:   # queda logo no começo da janela / coleta esparsa
                antes = [v for t, v in ok if t < ev['ini']][-5:]
            v_antes = _leitura_tipica(antes)
            ultimo_t, ultimo_v = s['pontos'][-1]
            if ultimo_v <= s['corte']:
                v_depois = ultimo_v
                ainda_fora = True
            else:
                depois = [v for t, v in ok if t >= (ev['retorno'] or agora)][-_N_DEPOIS:]
                v_depois = _leitura_tipica(depois) if depois else ultimo_v
            linhas_antes.append(f"{s['ramo']}: {_fmt_dbm(v_antes)}")
            linhas_depois.append(f"{s['ramo']}: {_fmt_dbm(v_depois)}")
            if v_antes is None or v_depois <= s['corte']:
                linhas_dif.append(f"{s['ramo']}: —")
                continue
            d = round(v_depois - v_antes, 2)
            deltas.append((d, s['ramo']))
            if abs(d) < 0.01:
                linhas_dif.append(f"{s['ramo']}: 0.00 dB = igual")
            elif d > 0:
                linhas_dif.append(f"{s['ramo']}: +{d:.2f} dB ↑ aumentou")
            else:
                linhas_dif.append(f"{s['ramo']}: {d:.2f} dB ↓ diminuiu")

        d_ini = chart._dt_local(ev['ini'])
        if ev.get('informado'):
            quando = f"Rompimento informado: {d_ini:%d/%m %H:%M}"
        elif ainda_fora or not ev.get('retorno'):
            quando = f"Rompimento: {d_ini:%d/%m %H:%M} → ainda sem sinal ({_fmt_dur(agora - ev['ini'])})"
        else:
            d_ret = chart._dt_local(ev['retorno'])
            tipo = ' (sem coleta no período)' if ev.get('sem_coleta') else ''
            quando = (f"Rompimento: {d_ini:%d/%m %H:%M} → normalizou {d_ret:%d/%m %H:%M} "
                      f"({_fmt_dur(ev['retorno'] - ev['ini'])} fora){tipo}")
        if ev.get('herdado'):
            quando += ' — horário do outro lado do enlace'

        if ainda_fora:
            resumo = "⛔ Ainda sem sinal — refaça a comparação depois da normalização."
        elif not deltas:
            resumo = "Sem leituras antes do rompimento para comparar (histórico expirado?)."
        else:
            sub = sum(1 for d, _ in deltas if d >= 0.01)
            caiu = sum(1 for d, _ in deltas if d <= -0.01)
            igual = len(deltas) - sub - caiu
            pior_d, pior_r = min(deltas)
            maior = max(abs(d) for d, _ in deltas)
            partes = [f"{n} {um if n == 1 else varios}" for n, um, varios in (
                (sub, 'aumentou', 'aumentaram'), (caiu, 'diminuiu', 'diminuíram'),
                (igual, 'igual', 'iguais')) if n]
            resumo = ', '.join(partes) + '.'
            if pior_d <= -_PERDA_RELEVANTE:
                resumo += (f" ⚠️ Perda adicional de {abs(pior_d):.2f} dB no {pior_r} após o reparo"
                           " — verificar fusões/emendas.")
            elif maior <= _OSCILACAO_DB:
                resumo += (f" ✅ Variação até {maior:.2f} dB, dentro da oscilação normal da leitura —"
                           " sinal voltou ao patamar de antes.")
            elif pior_d > -0.01:
                resumo += f" ✅ Sinal melhorou (até +{maior:.2f} dB)."
            else:
                resumo += f" ℹ️ Variação pequena (pior: {pior_r} {pior_d:.2f} dB)."

        blocos.append(
            cab + f"\n{quando}\n"
            "SINAL DE RECEPÇÃO ANTES:\n" + '\n'.join(linhas_antes)
            + "\n\nSINAL DE RECEPÇÃO DEPOIS:\n" + '\n'.join(linhas_depois)
            + "\n\nDIFERENÇA:\n" + '\n'.join(linhas_dif)
            + f"\nResumo: {resumo}"
        )

    fonte = {s['fonte'] for s in series}
    rodape = ("\n\n(ANTES = leitura mais frequente nas 6h anteriores à queda, sem os 30 min finais; "
              "DEPOIS = leitura mais frequente na última hora"
              + ('; fonte: médias horárias (trends) — history expirado' if 'trends' in fonte else '')
              + ')')
    instrucao = (
        "[INSTRUÇÃO DO CRM — não repasse esta linha] Responda ao operador com os blocos abaixo "
        "EXATAMENTE neste formato de texto (é o padrão do relatório do NOC): sem tabela, sem "
        "títulos markdown, sem reescrever números. Pode só trocar o nome do host pelo nome curto "
        "do site (ex: JURUENA, REG.JUINA) e acrescentar no fim uma conclusão de 1-2 linhas.\n\n"
    )
    return (instrucao + '\n\n\n'.join(blocos)
            + ('\n\n' + '\n'.join(avisos) if avisos else '') + rodape)
