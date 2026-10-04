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


_SENTIDO_EXPLICITO = [
    ('rx', re.compile(r'rx\s*_?(power|pwr)|\(rx\)|recebida|receive|recep[cç]', re.I)),
    ('tx', re.compile(r'tx\s*_?(power|pwr)|\(tx\)|transmitida|transmit', re.I)),
]
_LANE = re.compile(r'[\s:,\-–]*\b(lane|ramo|canal|ch)\s*([\d][\d\-]*)', re.I)


def _opt_sentido(nome, key=''):
    """rx/tx pelo nome ("Potência Recebida (RX)", "Rx Power", "RxPower"); só
    depois pela chave e por RX/TX soltos."""
    for texto in (nome, key):
        for sentido, rx in _SENTIDO_EXPLICITO:
            if rx.search(texto):
                return sentido
    for texto in (nome, key):
        m = re.search(r'(?<![A-Za-z0-9_])(rx|tx)(?![A-Za-z0-9_])', texto, re.I)
        if m:
            return m.group(1).lower()
    # "Sinal Base" das OLTs: leitura de sinal sem sentido no nome — recebido
    if re.search(r'\bsinal\b|\bsignal\b', nome, re.I):
        return 'rx'
    return ''


def _opt_interface(nome):
    """(interface, lane) a partir do nome do item, sem o sentido nem o ramo —
    RX e TX da mesma porta precisam dar a mesma interface. Formatos reais:
    'Optical Module Interface 100GE0/0/1: RxPower Lane2',
    'Potência Recebida (RX) na Interface hundred-gigabit-ethernet-1/1/1 - SW-X - Ramo 1',
    'SFP GE1/1/5 RX Power', 'XGigabitEthernet0/0/1: Tx Power'."""
    n = nome
    lane = ''
    m = _LANE.search(n)
    if m:
        lane = m.group(2)
        n = n[:m.start()] + n[m.end():]
    n = re.sub(r'pot[eê]ncia\s+(recebida|transmitida)\s*(\((rx|tx)\))?', ' ', n, flags=re.I)
    n = re.sub(r'(rx|tx)\s*_?(power|pwr)', ' ', n, flags=re.I)
    n = re.sub(r'\((rx|tx)\)|(?<![A-Za-z0-9])(rx|tx)(?![A-Za-z0-9_])', ' ', n, flags=re.I)
    n = re.sub(r'\b(avg|m[eé]dia)\b', ' ', n, flags=re.I)
    n = re.sub(r'^\s*(optical module interface|na interface|interface|sfp|transceiver)\b', ' ', n.strip(), flags=re.I)
    n = re.sub(r'^\s*(na interface|interface)\b', ' ', n.strip(), flags=re.I)
    n = re.sub(r'\s{2,}', ' ', n).strip(' :-–,')
    return (n or nome), lane


def com_descricao(opticos_lista, ifaces):
    """Põe a descrição da porta no nome do sinal óptico. O template de tráfego
    traz "XGigabitEthernet0/0/1(HUAWEI NE8000)"; o de módulo óptico, muitas
    vezes só "XGigabitEthernet0/0/1"."""
    desc = {}
    for i in ifaces:
        m = re.match(r'^(.*?)\((.+)\)\s*$', i['nome'])
        if m:
            desc.setdefault(m.group(1).strip().lower(), i['nome'])
    for g in opticos_lista:
        completo = desc.get(g['interface'].strip().lower())
        if completo:
            g['interface'] = completo
    return opticos_lista


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
        # Limiares do módulo ("100GE0/3/0: High Threshold") não são leitura de sinal
        if re.search(r'threshold|warning|alarm|limite|thold|\bhigh\b|\blow\b', nome, re.I):
            continue
        sentido = _opt_sentido(nome, it.get('key_', ''))
        if not sentido:
            continue
        iface, lane = _opt_interface(nome)
        grupos.setdefault(iface, []).append({
            'item': it, 'sentido': sentido,
            'media': bool(re.search(r'\bavg\b|m[eé]dia', nome, re.I)),
            'lane': lane,
        })

    saida = []
    for iface, membros in grupos.items():
        # Multilane com média calculada: o gráfico usa só a média de RX e TX
        medias = [m for m in membros if m['media']]
        principais = medias if medias else membros
        valores = [para_dbm(m['item'].get('lastvalue'), m['item'].get('units')) for m in principais]
        # Sem módulo/leitura: 0 (Datacom, Huawei multilane) ou -0,01 (Huawei S67xx)
        vazio = all(v is None or abs(v) < 0.05 for v in valores)
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


# ── Temperatura do equipamento ───────────────────────────────

_TEMP_SIM = re.compile(r'temp', re.I)
# Módulo óptico/porta/PON tem temperatura própria; limites, walks, JSON e
# "Tempo de resposta" não são leitura de temperatura
_TEMP_NAO = re.compile(
    r'sfp|xfp|qsfp|optic|[oó]ptic|ddm|transceiver|\btcv\b|gpon|epon|\bpon\b|interface|module|m[oó]dulo|'
    r'lane|thold|threshold|limite|alarm|walk|status|json|sensores|tempo|resposta|benchmark|discovery|'
    r'icmp|\bport\b|porta|\d+/\d+/\d+',
    re.I,
)
_TEMP_UNIDADES = {'c', '°c', 'ºc', 'cº', 'c°', 'â°c', ''}


def celsius(valor):
    """Leitura plausível de temperatura de equipamento, ou None (0 = sensor
    sem suporte; 214748 = lixo de OID de OLT)."""
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    return v if v != 0 and -20 < v < 150 else None


def _temp_score(it):
    key, nome = it.get('key_', ''), it.get('name', '')
    texto = f'{key} {nome}'
    if not _TEMP_SIM.search(texto) or _TEMP_NAO.search(texto):
        return 0
    if (it.get('units') or '').strip().lower() not in _TEMP_UNIDADES:
        return 0
    if int(it.get('value_type', 3)) not in (0, 3):
        return 0
    if re.search(r'cpu|processor|mpu|board|placa|device|system|sistema|chassi|slot|ipu|switch|\bolt\b|sensor', texto, re.I):
        return 90
    if re.search(r'psu|power supply|fonte|fan|ventoinha', texto, re.I):
        return 50
    return 70


def escolher_temperatura(itens):
    """Itens de temperatura do equipamento do melhor tipo (várias placas ou
    sensores = vários itens; o card mostra o mais quente)."""
    pontuados = [(s, it) for it in itens if (s := _temp_score(it))]
    if not pontuados:
        return []
    melhor = max(s for s, _ in pontuados)
    return [it for s, it in pontuados if s == melhor]


# ── Estado operacional das portas (up/down) ──────────────────

_OPER_KEY = re.compile(r'^(net\.if\.status|ifoperstatus|[\w.]*\.if\.status|[\w.]*ifoperstatus)\[', re.I)
_OPER_NOME = re.compile(r'operational status|status operacional|oper(ational)?\s*state|^status da interface', re.I)
_OPER_NAO = re.compile(r'admin|onu|\bpon\b|gpon|epon|bgp|peer|ospf|fan|ventoinha|fonte|power|corrente|shutdown|'
                       r'status (rx|tx)|duplex', re.I)
# ifOperStatus (RFC 2863): 1 up, 2 down, 3 testing, 5 dormant, 6 notPresent, 7 lowerLayerDown
_OPER_VALOR = {'1': 'up', '2': 'down', '3': 'testing', '5': 'dormant', '6': 'down', '7': 'down'}


def porta_base(nome: str) -> str:
    """Só o nome da porta, sem descrição: 'XGigabitEthernet0/0/1(HUAWEI)',
    'XGigabitEthernet0/0/1 - P2P-X', 'Interface ge1: Bits' → 'xgigabitethernet0/0/1'."""
    n = re.sub(r'^\s*(status da interface|interface)\s+', '', nome or '', flags=re.I)
    return re.split(r'[\s(:]', n.strip(), maxsplit=1)[0].lower()


def status_portas(itens) -> dict:
    """{porta_base: 'up'|'down'|...} pelos itens de estado operacional."""
    saida = {}
    for it in itens:
        key, nome = it.get('key_', ''), it.get('name', '')
        if not (_OPER_KEY.match(key) or _OPER_NOME.search(nome)) or _OPER_NAO.search(f'{key} {nome}'):
            continue
        valor = str(it.get('lastvalue') or '').split('.')[0]
        estado = _OPER_VALOR.get(valor)
        if not estado:
            continue
        # A porta vem do parâmetro da chave quando ele é um nome (ifOperStatus[XGigabitEthernet0/0/1]);
        # se é índice SNMP (net.if.status[ifOperStatus.12]), vem do nome do item
        param = re.search(r'\[([^\]]+)\]', key)
        base = param.group(1).lower() if param and re.search(r'[a-z].*\d', param.group(1), re.I) \
            and not param.group(1).lower().startswith('ifoperstatus') else porta_base(nome)
        if base:
            saida[base] = estado
    return saida


def sem_luz(membros) -> bool:
    """RX ou TX sem luz: −40 dBm é o piso do Huawei/Datacom para "nada"."""
    for m in membros:
        v = para_dbm(m['item'].get('lastvalue'), m['item'].get('units'))
        if v is not None and v <= -39.9:
            return True
    return False
