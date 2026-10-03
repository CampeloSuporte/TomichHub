"""
monitoramento/zabbix_metricas.py

Acha, entre os itens de um host do Zabbix, os que interessam ao card do
acesso: CPU, uptime, tráfego das interfaces e sinal óptico.

Os templates variam muito entre clientes (chave padrão `system.cpu.util[...]`,
OID cru `1.3.6.1.4.1.34592...`, templates próprios `huawei.sfp.rx[...]`,
`datacom.cpu.util`, `raisecom.transceiver.rx.power[...]` em µW...), então a
escolha é por chave + nome + unidade, nunca por uma chave fixa. Funções puras:
recebem a lista do `item.get` e devolvem o que escolheram.
"""
import math
import re

# ── CPU ──────────────────────────────────────────────────────

_CPU_SIM = re.compile(r'cpu|processador|processor|hwavgduty|cpudevduty', re.I)
# Itens "de CPU" que não são a utilização: tempos parciais do Linux,
# temperatura, contadores, descoberta.
_CPU_NAO = re.compile(
    r'temp|guest|steal|idle|iowait|nice|interrupt|softirq|system time|user time|'
    r'switch|load average|system\.cpu\.load|privileged|number|\.num\b|walk|max cpu|'
    r'memor|threshold|limite|alarm|fan',
    re.I,
)


def _cpu_score(it):
    texto = f"{it.get('key_', '')} {it.get('name', '')}"
    if not _CPU_SIM.search(texto) or _CPU_NAO.search(texto):
        return 0
    units = (it.get('units') or '').strip()
    if units not in ('%', ''):
        return 0
    if units == '' and not re.search(r'util|usage|uso|duty|load', texto, re.I):
        return 0
    if int(it.get('value_type', 3)) not in (0, 3):
        return 0
    nome, key = it.get('name', ''), it.get('key_', '')
    if key.startswith('system.cpu.util') and re.search(r'utiliz', nome, re.I):
        return 100
    if re.search(r'5\s*min', texto, re.I):
        return 90
    if re.search(r'1\s*min', texto, re.I):
        return 85
    if re.search(r'5\s*s(ec|eg)?\b', texto, re.I):
        return 60
    return 70


def escolher_cpu(itens):
    """Itens de utilização de CPU do melhor tipo encontrado (vários = várias
    placas/CPUs; o card mostra o maior)."""
    pontuados = [(s, it) for it in itens if (s := _cpu_score(it))]
    if not pontuados:
        return []
    melhor = max(s for s, _ in pontuados)
    return [it for s, it in pontuados if s == melhor]


# ── Uptime ───────────────────────────────────────────────────

_UP_SIM = re.compile(r'uptime|1\.3\.6\.1\.2\.1\.1\.3\.0|sysuptime|hrsystemuptime', re.I)
_UP_NAO = re.compile(r'ldp|bgp|ospf|peer|session|sess[aã]o|lsp|tunnel|t[uú]nel|grafana|interface|vm ', re.I)


def _uptime_score(it):
    texto = f"{it.get('key_', '')} {it.get('name', '')}"
    if not _UP_SIM.search(texto) or _UP_NAO.search(texto):
        return 0
    # Sem unidade não dá para saber se é segundos, centésimos ou texto
    if (it.get('units') or '').strip() not in ('uptime', 's'):
        return 0
    if int(it.get('value_type', 3)) not in (0, 3):
        return 0
    key = it.get('key_', '')
    if key.startswith('system.hw.uptime'):
        return 100
    if key.startswith('system.uptime'):
        return 95
    if key.startswith('system.net.uptime') or '1.3.6.1.2.1.1.3.0' in key or 'sysuptime' in key.lower():
        return 90
    return 70


def _positivo(it):
    try:
        return float(it.get('lastvalue') or 0) > 0
    except ValueError:
        return False


def escolher_uptime(itens):
    """Melhor item de uptime com valor. Huawei VRP devolve 0 no
    hrSystemUptime (system.hw.uptime) e o sysUpTime é que vale."""
    pontuados = [(s, it) for it in itens if (s := _uptime_score(it))]
    if not pontuados:
        return None
    return max(pontuados, key=lambda p: (_positivo(p[1]), p[0]))[1]


# ── Interfaces (tráfego) ─────────────────────────────────────

_IF_KEY = re.compile(r'^net\.if\.(in|out)\[')


def _nome_interface(nome_item):
    n = nome_item.split(':', 1)[0].strip()
    return re.sub(r'^interface\s+', '', n, flags=re.I)


def interfaces(itens):
    """[{nome, in, out}] — in/out são os itens bps de cada sentido."""
    grupos = {}
    for it in itens:
        m = _IF_KEY.match(it.get('key_', ''))
        if not m or (it.get('units') or '') != 'bps':
            continue
        g = grupos.setdefault(_nome_interface(it.get('name', '')), {})
        g[m.group(1)] = it
    saida = [{'nome': nome, 'in': g.get('in'), 'out': g.get('out')} for nome, g in grupos.items()]
    return sorted(saida, key=lambda g: g['nome'].lower())


# ── Sinal óptico ─────────────────────────────────────────────

_OPT_UNIDADES = {'dbm', '!dbm', 'uw', '!uw', 'mw', '!mw'}
_OPT_NOME = re.compile(r'(rx|tx)\s*_?power|(rx|tx)\s*pwr|pot[eê]ncia', re.I)


def _opt_sentido(texto):
    t = texto.lower()
    if re.search(r'\brx|rxpower|rx\s*power|receb|recep', t):
        return 'rx'
    if re.search(r'\btx|txpower|tx\s*power|transm', t):
        return 'tx'
    return ''


def _opt_interface(nome):
    n = re.split(r'[:\-–]?\s*(rx|tx)\s*_?(power|pwr)', nome, maxsplit=1, flags=re.I)[0]
    n = re.sub(r'^(optical module interface|interface|sfp|transceiver)\s+', '', n.strip(), flags=re.I)
    return n.strip(' :-–') or nome


def para_dbm(valor, units):
    """Converte para dBm (µW e mW → dBm). None se não der."""
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    u = (units or '').lower().lstrip('!')
    if u == 'uw':
        return 10 * math.log10(v / 1000) if v > 0 else None
    if u == 'mw':
        return 10 * math.log10(v) if v > 0 else None
    return v


def opticos(itens):
    """[{interface, itens: [{item, sentido, lane}], vazio}] — `vazio` quando
    toda leitura é 0/ausente (porta sem módulo no Huawei devolve 0 dBm)."""
    grupos = {}
    for it in itens:
        units = (it.get('units') or '').strip().lower()
        nome = it.get('name', '')
        texto = f"{it.get('key_', '')} {nome}"
        if units not in _OPT_UNIDADES and not (_OPT_NOME.search(nome) and units in ('', 'dbm')):
            continue
        if int(it.get('value_type', 3)) not in (0, 3):
            continue
        sentido = _opt_sentido(texto)
        if not sentido:
            continue
        lane = re.search(r'lane\s*([\d\-]+)|\bch\s*(\d+)', nome, re.I)
        grupos.setdefault(_opt_interface(nome), []).append({
            'item': it, 'sentido': sentido,
            'media': bool(re.search(r'\bavg\b|m[eé]dia', nome, re.I)),
            'lane': (lane.group(1) or lane.group(2)) if lane else '',
        })

    saida = []
    for iface, membros in grupos.items():
        # Multilane com média calculada: o gráfico usa só a média de RX e TX
        medias = [m for m in membros if m['media']]
        principais = medias if medias else membros
        valores = [para_dbm(m['item'].get('lastvalue'), m['item'].get('units')) for m in principais]
        vazio = all(v is None or v == 0 for v in valores)
        saida.append({'interface': iface, 'itens': principais, 'todos': membros, 'vazio': vazio})
    return sorted(saida, key=lambda g: (g['vazio'], g['interface'].lower()))


# ── Voltagem do equipamento ──────────────────────────────────

# Chave do item que o CRM cria em MikroTik (os templates do Zabbix não têm)
CHAVE_VOLTAGEM_CRM = 'crm.voltagem[mtxrHlVoltage.0]'

_VOLT_SIM = re.compile(r'volt|tens[aã]o', re.I)
# Tensão de módulo óptico/porta PON não é a do equipamento; status/limites
# são enumerações ou thresholds
_VOLT_NAO = re.compile(
    r'sfp|xfp|qsfp|optic|[oó]ptic|transceiver|lane|\bpon\b|gpon|epon|module|m[oó]dulo|'
    r'threshold|limite|alarm|status|current|corrente|\bport\b|porta|interface',
    re.I,
)


def _volt_score(it):
    key, nome = it.get('key_', ''), it.get('name', '')
    if key == CHAVE_VOLTAGEM_CRM:
        return 100
    texto = f'{key} {nome}'
    if not _VOLT_SIM.search(texto) or _VOLT_NAO.search(texto):
        return 0
    if (it.get('units') or '').strip() not in ('V', 'v', 'mV'):
        return 0
    if int(it.get('value_type', 3)) not in (0, 3):
        return 0
    if re.search(r'input|entrada|psu|power supply|fonte|system|board|main', texto, re.I):
        return 80
    return 60


def volts(valor, units):
    """Valor em volts, ou None se não for uma leitura plausível de equipamento
    (0 a 300 V — template de OLT Parks devolve 1195 para 11,95 V em 'V')."""
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    if (units or '').strip() == 'mV':
        v /= 1000
    return v if 0 < v <= 300 else None


def escolher_voltagem(itens):
    """Itens de voltagem do equipamento, melhor tipo primeiro (várias fontes =
    vários itens)."""
    pontuados = [(s, it) for it in itens if (s := _volt_score(it))]
    if not pontuados:
        return []
    melhor = max(s for s, _ in pontuados)
    return [it for s, it in pontuados if s == melhor]
