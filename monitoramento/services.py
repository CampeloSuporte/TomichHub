"""
monitoramento/services.py
Camada de acesso à API Zabbix (JSON-RPC 2.0).

Compatibilidade:
  - Zabbix < 6.4 : auth no payload JSON
  - Zabbix 7.0+  : Bearer header (auth no payload foi removido)
"""
import logging
import time
import requests
from django.core.cache import cache

requests.packages.urllib3.disable_warnings(
    requests.packages.urllib3.exceptions.InsecureRequestWarning
)

logger = logging.getLogger(__name__)

_version_cache: dict = {}
_AUTH_TOKEN_TTL = 600   # 10 min — evita relogar no Zabbix a cada poll de 15s
_ITEM_META_TTL  = 300   # 5 min — value_type/units de um item raramente mudam


# ──────────────────────────────────────────────────────────────
# VERSÃO
# ──────────────────────────────────────────────────────────────

def _get_zabbix_version(url: str) -> tuple:
    if url in _version_cache:
        return _version_cache[url]
    try:
        resp = requests.post(
            f"{url.rstrip('/')}/api_jsonrpc.php",
            json={"jsonrpc": "2.0", "method": "apiinfo.version", "params": {}, "id": 1},
            headers={"Content-Type": "application/json"},
            timeout=10,
            verify=False,
        )
        version_str = resp.json().get("result", "0.0.0")
        parts = version_str.split(".")
        version = tuple(int(p) for p in parts[:3])
    except Exception:
        version = (7, 0, 0)
    _version_cache[url] = version
    return version


# ──────────────────────────────────────────────────────────────
# HTTP / RPC
# ──────────────────────────────────────────────────────────────

_AUTH_ERROR_MARKERS = ('re-login', 'not authorized', 'session terminated', 'expected another format')


def _rpc(url: str, method: str, params: dict, auth: str = None, config=None) -> dict:
    endpoint = f"{url.rstrip('/')}/api_jsonrpc.php"
    payload  = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    headers  = {"Content-Type": "application/json"}

    if auth:
        version = _get_zabbix_version(url)
        if version >= (7, 0, 0):
            headers["Authorization"] = f"Bearer {auth}"
        else:
            payload["auth"] = auth

    resp = requests.post(endpoint, json=payload, headers=headers, timeout=15, verify=False)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        msg = data["error"].get("data") or data["error"].get("message", "Zabbix API Error")
        # Token cacheado expirou do lado do Zabbix antes do nosso TTL (ex:
        # sessão derrubada manualmente) — invalida o cache e refaz login uma
        # vez, em vez de estourar erro pro usuário no meio de um poll de 15s.
        if config is not None and any(m in msg.lower() for m in _AUTH_ERROR_MARKERS):
            novo_auth = _get_auth_token(config, force_refresh=True)
            return _rpc(url, method, params, auth=novo_auth)
        raise Exception(msg)

    return data.get("result")


# ──────────────────────────────────────────────────────────────
# AUTENTICAÇÃO
# ──────────────────────────────────────────────────────────────

def _get_auth_token(config, force_refresh=False):
    if getattr(config, 'api_token', None):
        return config.api_token

    cache_key = f"zbx_auth_token:{getattr(config, 'id', config.url)}"
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached:
            return cached

    url = config.url.rstrip('/')
    for user_param in ("username", "user"):
        payload = {
            "jsonrpc": "2.0",
            "method":  "user.login",
            "params":  {user_param: config.usuario, "password": config.senha},
            "id":      1,
        }
        resp = requests.post(
            f"{url}/api_jsonrpc.php",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()

        if "result" in data:
            token = data["result"]
            cache.set(cache_key, token, _AUTH_TOKEN_TTL)
            return token

        error_data = data.get("error", {}).get("data", "")
        if 'unexpected parameter "user"' in error_data:
            continue
        if 'unexpected parameter "username"' in error_data:
            continue
        raise Exception(error_data or data["error"].get("message", "Erro de autenticação"))

    raise Exception("Falha ao autenticar na API Zabbix")


def testar_conexao(config) -> str:
    version = _rpc(config.url, "apiinfo.version", {})
    _get_auth_token(config)
    return version


# ──────────────────────────────────────────────────────────────
# HOSTS
# ──────────────────────────────────────────────────────────────

def listar_hosts(config, busca: str = "") -> list:
    auth   = _get_auth_token(config)
    params = {
        "output":           ["hostid", "host", "name", "status"],
        "selectInterfaces": ["ip", "port", "type", "available"],
        "limit":            300,
    }
    if busca:
        params["search"]      = {"name": busca, "host": busca}
        params["searchByAny"] = True

    hosts = _rpc(config.url, "host.get", params, auth)

    result = []
    for h in hosts:
        ip        = ""
        available = "unknown"
        ifaces    = h.get("interfaces", [])
        if ifaces:
            ip = ifaces[0].get("ip", "")
            avail_vals = [i.get("available", "0") for i in ifaces]
            if "1" in avail_vals:
                available = "up"
            elif all(v == "2" for v in avail_vals):
                available = "down"

        result.append({
            "hostid":    h["hostid"],
            "host":      h["host"],
            "name":      h.get("name", h["host"]),
            "status":    "enabled" if h.get("status") == "0" else "disabled",
            "available": available,
            "ip":        ip,
        })
    return result


# ──────────────────────────────────────────────────────────────
# INTERFACES / ITENS
# ──────────────────────────────────────────────────────────────

def listar_interfaces(config, host_id: str) -> list:
    """
    Retorna TODOS os itens ativos do host agrupados por nome de interface.
    Sem filtro de keyword — o usuário pesquisa o que quiser no frontend.

    Ordenação: grupos com in+out primeiro, depois só um, depois nenhum.
    """
    auth = _get_auth_token(config)

    try:
        all_items = _rpc(config.url, "item.get", {
            "output":  ["itemid", "name", "key_", "lastvalue", "units"],
            "hostids": host_id,
            "filter":  {"status": "0"},
            "limit":   3000,
        }, auth)
    except Exception as e:
        logger.error("item.get error: %s", e)
        return []

    if not all_items:
        return []

    groups: dict = {}  # group_name → {name, in_id, out_id}

    for item in all_items:
        name = item["name"]
        key  = item["key_"]

        if ":" in name:
            group_name = name.split(":")[0].strip()
            for prefix in ("Interface ", "Port ", "Iface ", "Link "):
                group_name = group_name.replace(prefix, "").strip()
        else:
            group_name = key.split("[")[0] if "[" in key else key

        if group_name not in groups:
            groups[group_name] = {
                "name":   group_name,
                "in_id":  None,
                "out_id": None,
            }

        g         = groups[group_name]
        key_lower  = key.lower()
        name_lower = name.lower()

        is_in = (
            any(k in key_lower for k in (
                "hcinoctets", "ifinoctets", "net.if.in", "if.in[",
            )) or
            any(k in name_lower for k in (
                "bits received", "traffic in", "in octets",
                "received bits", "entrada",
            ))
        )

        is_out = (
            any(k in key_lower for k in (
                "hcoutoctets", "ifoutoctets", "net.if.out", "if.out[",
            )) or
            any(k in name_lower for k in (
                "bits sent", "traffic out", "out octets",
                "sent bits", "saída",
            ))
        )

        if is_in and not g["in_id"]:
            g["in_id"] = item["itemid"]
        if is_out and not g["out_id"]:
            g["out_id"] = item["itemid"]

    result = list(groups.values())

    def _sort(g):
        if g["in_id"] and g["out_id"]:
            return (0, g["name"])
        if g["in_id"] or g["out_id"]:
            return (1, g["name"])
        return (2, g["name"])

    result.sort(key=_sort)
    return result


# ──────────────────────────────────────────────────────────────
# HISTÓRICO DE ITENS
# ──────────────────────────────────────────────────────────────

# Janelas maiores que isso saem do `trends` (médias horárias): o `history` do
# Zabbix costuma ser expurgado em poucos dias e não cobre 7/30 dias.
_JANELA_TRENDS = 3 * 86400
# Teto de pontos brutos puxados do Zabbix antes do downsample.
_MAX_BRUTO     = 20000


def historico_item(config, item_id, hours=1, limit=300):
    """Histórico das últimas `hours` horas de um item, já reamostrado para no
    máximo `limit` pontos.

    O período inteiro é sempre coberto: antes essa função pedia ao Zabbix os
    `limit` pontos mais recentes (sortorder DESC), então 12h e 24h devolviam a
    mesma fatia curta do fim da janela. Agora busca a janela toda em ordem
    cronológica e reduz aqui, com fallback para `trends` nas janelas longas.
    """
    auth      = _get_auth_token(config)
    agora     = int(time.time())
    time_from = agora - int(hours * 3600)

    # Metadados do item (value_type/units) raramente mudam — cacheados
    # separado do histórico em si, que precisa ser sempre fresco.
    meta_key = f"zbx_item_meta:{getattr(config, 'id', config.url)}:{item_id}"
    meta = cache.get(meta_key)
    if meta is None:
        item_info = _rpc(config.url, 'item.get', {
            'itemids': [item_id],
            'output': ['value_type', 'units', 'name', 'lastvalue', 'lastclock'],
        }, auth, config=config)
        meta = {'value_type': 3, 'units': ''}
        if item_info:
            meta = {
                'value_type': int(item_info[0].get('value_type', 3)),
                'units': item_info[0].get('units', ''),
            }
        cache.set(meta_key, meta, _ITEM_META_TTL)

    value_type = meta['value_type']
    units = meta['units']

    janela = agora - time_from
    fontes = ['trends', 'history'] if janela > _JANELA_TRENDS else ['history', 'trends']

    pontos = []
    for fonte in fontes:
        try:
            if fonte == 'history':
                bruto = _rpc(config.url, 'history.get', {
                    'itemids':   [item_id],
                    'history':   value_type,
                    'time_from': time_from,
                    'time_till': agora,
                    'sortfield': 'clock',
                    'sortorder': 'ASC',
                    'limit':     _MAX_BRUTO,
                }, auth, config=config)
                pontos = [{'t': int(p['clock']), 'v': float(p['value'])} for p in bruto]
            else:
                bruto = _rpc(config.url, 'trend.get', {
                    'itemids':   [item_id],
                    'time_from': time_from,
                    'time_till': agora,
                    'limit':     _MAX_BRUTO,
                }, auth, config=config)
                pontos = [{'t': int(p['clock']), 'v': float(p['value_avg'])} for p in bruto]
                pontos.sort(key=lambda p: p['t'])
        except Exception as exc:
            logger.warning('%s.get do item %s falhou: %s', fonte, item_id, exc)
            pontos = []
        if pontos:
            break

    if not pontos:
        return []

    units_lower = units.lower()

    # ✅ Já é taxa (bps, pps, etc.) — não calcula delta
    if not any(u in units_lower for u in ('bps', 'pps', 'b/s', 'bit')):
        # ✅ Contador acumulativo de bytes — calcula delta e converte para bps
        if value_type == 3 and len(pontos) >= 2:
            taxa = []
            for i in range(1, len(pontos)):
                dt = pontos[i]['t'] - pontos[i-1]['t']
                if dt <= 0:
                    continue
                delta = pontos[i]['v'] - pontos[i-1]['v']
                if delta < 0:
                    # Contador reiniciou (reboot do equipamento)
                    continue
                # Bytes → bits
                bps = (delta * 8) / dt
                taxa.append({'t': pontos[i]['t'], 'v': bps})
            pontos = taxa

    return _downsample(pontos, limit)


# ──────────────────────────────────────────────────────────────
# STATUS EM TEMPO REAL
# ──────────────────────────────────────────────────────────────

def status_nodes(config, host_ids: list) -> dict:
    if not host_ids:
        return {}

    auth      = _get_auth_token(config)
    hosts_raw = _rpc(config.url, "host.get", {
        "output":           ["hostid"],
        "selectInterfaces": ["available"],
        "hostids":          host_ids,
    }, auth)

    status: dict = {}
    for h in hosts_raw:
        hid        = h["hostid"]
        avail_vals = [i.get("available", "0") for i in h.get("interfaces", [])]
        if "1" in avail_vals:
            status[hid] = "up"
        elif avail_vals and all(v == "2" for v in avail_vals):
            status[hid] = "down"
        else:
            status[hid] = "unknown"

    try:
        triggered = _rpc(config.url, "trigger.get", {
            "output":      ["triggerid"],
            "selectHosts": ["hostid"],
            "hostids":     host_ids,
            "only_true":   True,
            "filter":      {"status": "0"},
        }, auth)
        for t in triggered:
            for h in t.get("hosts", []):
                hid = h["hostid"]
                if status.get(hid) == "up":
                    status[hid] = "problem"
    except Exception:
        pass

    return status


def status_items(config, item_ids: list) -> dict:
    if not item_ids:
        return {}

    auth  = _get_auth_token(config)
    items = _rpc(config.url, "item.get", {
        "output":  ["itemid", "lastvalue", "lastclock", "units"],
        "itemids": item_ids,
    }, auth)

    return {
        it["itemid"]: {
            "value": it.get("lastvalue", "0"),
            "units": it.get("units", "bps"),
            "clock": it.get("lastclock", 0),
        }
        for it in items
    }


# ──────────────────────────────────────────────────────────────
# UTILITÁRIOS
# ──────────────────────────────────────────────────────────────

def format_bps(value_str) -> str:
    try:
        v = float(value_str)
    except (TypeError, ValueError):
        return "0 bps"
    if v >= 1_000_000_000: return f"{v/1_000_000_000:.1f} Gbps"
    if v >= 1_000_000:     return f"{v/1_000_000:.1f} Mbps"
    if v >= 1_000:         return f"{v/1_000:.1f} Kbps"
    return f"{v:.0f} bps"


def listar_itens(config, host_id: str, busca: str = '') -> list:
    """
    Retorna todos os itens numéricos ativos do host.
    O usuário seleciona In e Out separadamente.
    """
    auth = _get_auth_token(config)

    params = {
        "output":  ["itemid", "name", "key_", "lastvalue", "units", "value_type"],
        "hostids": host_id,
        "filter":  {"status": "0"},
        # Apenas tipos numéricos: 0=float, 3=unsigned int
        "search":  {"name": busca} if busca else {},
        "limit":   2000,
    }

    try:
        items = _rpc(config.url, "item.get", params, auth)
    except Exception as e:
        logger.error("item.get error: %s", e)
        return []

    # Filtrar apenas tipos numéricos
    result = []
    for it in items:
        vt = int(it.get("value_type", 99))
        if vt not in (0, 3):  # 0=float, 3=unsigned
            continue
        result.append({
            "itemid":     it["itemid"],
            "name":       it["name"],
            "key":        it["key_"],
            "lastvalue":  it.get("lastvalue", ""),
            "units":      it.get("units", ""),
            "value_type": vt,
        })

    # Ordenar: itens com "bit" ou "bps" no nome/units primeiro
    def _sort(i):
        n = i["name"].lower()
        u = i["units"].lower()
        if any(k in n for k in ("bits received", "bits sent", "bit receiv", "bit sent")):
            return 0
        if any(k in u for k in ("bps", "bit")):
            return 1
        return 2

    result.sort(key=_sort)
    return result

# ──────────────────────────────────────────────────────────────
# BUSCA DE ITENS (usada pelo Agent NOC)
# ──────────────────────────────────────────────────────────────

def _rate_de_contador(pontos: list, value_type: int, units: str) -> tuple:
    """Converte contador acumulativo (bytes) em taxa (bps). Mesma regra do
    gráfico da aba Monitoramento — ver historico_item().
    Retorna (pontos, convertido)."""
    units_lower = (units or '').lower()
    if any(u in units_lower for u in ('bps', 'pps', 'b/s', 'bit')):
        return pontos, False
    if value_type == 3 and units_lower in ('b', 'bytes') and len(pontos) >= 2:
        taxa = []
        for i in range(1, len(pontos)):
            dt = pontos[i]['t'] - pontos[i - 1]['t']
            if dt <= 0:
                continue
            delta = pontos[i]['v'] - pontos[i - 1]['v']
            if delta < 0:      # contador reiniciou (reboot)
                continue
            taxa.append({'t': pontos[i]['t'], 'v': (delta * 8) / dt})
        return taxa, True
    return pontos, False


def buscar_hosts(config, busca: str = '', limit: int = 30) -> list:
    """Hosts monitorados que casam com `busca` (nome visível ou host técnico)."""
    auth   = _get_auth_token(config)
    params = {'output': ['hostid', 'host', 'name', 'status'], 'limit': limit}
    if busca:
        params['search']      = {'name': busca, 'host': busca}
        params['searchByAny'] = True
    hosts = _rpc(config.url, 'host.get', params, auth, config=config)
    return [
        {'hostid': h['hostid'], 'host': h['host'], 'name': h.get('name', h['host'])}
        for h in hosts
    ]


def buscar_itens(config, host_busca: str = '', item_busca: str = '',
                 limit: int = 40) -> list:
    """
    Procura itens do Zabbix por texto livre.

    host_busca : filtra os hosts (ex: 'juina', 'switch painera'). Vazio = todos.
    item_busca : termo(s) do item — casa em `name` ou `key_` (ex: 'painera',
                 'tráfego', 'rx power'). Múltiplos termos: o primeiro vai para
                 a API, os demais filtram em Python (a API não faz AND entre
                 campos diferentes).

    Retorna [{'itemid', 'name', 'key_', 'units', 'value_type', 'lastvalue',
              'lastclock', 'hostid', 'hostname'}]
    """
    auth = _get_auth_token(config)

    hostids = None
    hostmap = {}
    if host_busca:
        hosts = buscar_hosts(config, host_busca, limit=30)
        if not hosts:
            return []
        hostids = [h['hostid'] for h in hosts]
        hostmap = {h['hostid']: h['name'] for h in hosts}

    termos = [t for t in (item_busca or '').split() if len(t) >= 2]
    params = {
        'output':       ['itemid', 'name', 'key_', 'units', 'value_type',
                         'lastvalue', 'lastclock'],
        'filter':       {'status': '0'},
        'selectHosts':  ['hostid', 'name'],
        'limit':        2000 if termos else 300,
    }
    if hostids:
        params['hostids'] = hostids

    itens = []
    if termos:
        for campo in ('name', 'key_'):
            p = dict(params)
            p['search'] = {campo: termos[0]}
            try:
                itens = _rpc(config.url, 'item.get', p, auth, config=config)
            except Exception as exc:
                logger.warning('item.get (%s) falhou: %s', campo, exc)
                itens = []
            if itens:
                break
    else:
        itens = _rpc(config.url, 'item.get', params, auth, config=config)

    # Termos restantes filtram em Python (AND), casando em name OU key_
    for termo in termos[1:]:
        t = termo.lower()
        itens = [
            i for i in itens
            if t in i.get('name', '').lower() or t in i.get('key_', '').lower()
        ]

    resultado = []
    for i in itens:
        hosts_do_item = i.get('hosts') or []
        hid  = hosts_do_item[0]['hostid'] if hosts_do_item else ''
        hnom = hosts_do_item[0].get('name', '') if hosts_do_item else hostmap.get(hid, '')
        resultado.append({
            'itemid':     i['itemid'],
            'name':       i.get('name', ''),
            'key_':       i.get('key_', ''),
            'units':      i.get('units', ''),
            'value_type': int(i.get('value_type', 3)),
            'lastvalue':  i.get('lastvalue', ''),
            'lastclock':  int(i.get('lastclock') or 0),
            'hostid':     hid,
            'hostname':   hnom,
        })

    # Ranking: item coletando > item com descrição de interface preenchida >
    # item com valor diferente de zero > nome curto (mais específico).
    def _rank(r):
        tem_desc = 1 if '()' in r['name'] else 0
        try:
            zero = 0 if float(r['lastvalue']) != 0 else 1
        except (TypeError, ValueError):
            zero = 0
        return (0 if r['lastclock'] else 1, tem_desc, zero, len(r['name']))

    resultado.sort(key=_rank)
    return resultado[:limit]


# ──────────────────────────────────────────────────────────────
# HISTÓRICO EM JANELA ABSOLUTA (history + fallback trends)
# ──────────────────────────────────────────────────────────────

def _downsample(pontos: list, max_pontos: int) -> list:
    if len(pontos) <= max_pontos or max_pontos < 2:
        return pontos
    bucket = len(pontos) / max_pontos
    saida  = []
    for i in range(max_pontos):
        ini = int(i * bucket)
        fim = max(int((i + 1) * bucket), ini + 1)
        fatia = pontos[ini:fim]
        if not fatia:
            continue
        saida.append({
            't': fatia[len(fatia) // 2]['t'],
            'v': sum(p['v'] for p in fatia) / len(fatia),
        })
    return saida


def historico_janela(config, item_id, ts_from: int, ts_till: int,
                     max_pontos: int = 300) -> dict:
    """
    Histórico de um item numa janela absoluta (epoch → epoch).

    Diferente de historico_item(), aceita início/fim arbitrários e cai
    automaticamente em `trends` (médias horárias, retidas por muito mais tempo
    no Zabbix) quando o `history` já expirou — que é o caso de perguntas do
    tipo "como estava o sinal antes do rompimento de ontem".

    Retorna {'itemid', 'name', 'units', 'value_type', 'fonte', 'pontos',
             'stats': {'min','avg','max','ultimo','ultimo_t','n'}}
    """
    auth = _get_auth_token(config)

    info = _rpc(config.url, 'item.get', {
        'itemids': [item_id],
        'output':  ['name', 'key_', 'units', 'value_type'],
        'selectHosts': ['name'],
    }, auth, config=config)
    if not info:
        raise Exception(f'Item {item_id} não encontrado no Zabbix')

    meta       = info[0]
    value_type = int(meta.get('value_type', 3))
    units      = meta.get('units', '')
    nome       = meta.get('name', '')
    hosts_meta = meta.get('hosts') or []
    hostname   = hosts_meta[0].get('name', '') if hosts_meta else ''

    if value_type not in (0, 3):
        raise Exception(
            f'Item "{nome}" é do tipo texto/log — não tem histórico numérico para gráfico.'
        )

    janela   = max(int(ts_till) - int(ts_from), 60)
    # Janela longa: trends primeiro (mais barato e sobrevive à expiração do history)
    preferir_trends = janela > 3 * 86400
    fontes = ['trends', 'history'] if preferir_trends else ['history', 'trends']

    pontos = []
    fonte  = ''
    for f in fontes:
        try:
            if f == 'history':
                bruto = _rpc(config.url, 'history.get', {
                    'itemids':   [item_id],
                    'history':   value_type,
                    'time_from': int(ts_from),
                    'time_till': int(ts_till),
                    'sortfield': 'clock',
                    'sortorder': 'ASC',
                    'limit':     20000,
                }, auth, config=config)
                pontos = [{'t': int(p['clock']), 'v': float(p['value'])} for p in bruto]
            else:
                bruto = _rpc(config.url, 'trend.get', {
                    'itemids':   [item_id],
                    'time_from': int(ts_from),
                    'time_till': int(ts_till),
                    'limit':     20000,
                }, auth, config=config)
                pontos = [
                    {'t': int(p['clock']), 'v': float(p['value_avg']),
                     'min': float(p['value_min']), 'max': float(p['value_max'])}
                    for p in bruto
                ]
                pontos.sort(key=lambda p: p['t'])
        except Exception as exc:
            logger.warning('%s.get do item %s falhou: %s', f, item_id, exc)
            pontos = []
        if pontos:
            fonte = f
            break

    if not pontos:
        return {
            'itemid': str(item_id), 'name': nome, 'hostname': hostname,
            'units': units, 'value_type': value_type, 'fonte': '',
            'pontos': [], 'stats': {},
        }

    pontos, convertido = _rate_de_contador(pontos, value_type, units)
    if convertido:
        units = 'bps'
    if not pontos:
        return {
            'itemid': str(item_id), 'name': nome, 'hostname': hostname,
            'units': units, 'value_type': value_type, 'fonte': fonte,
            'pontos': [], 'stats': {},
        }
    if pontos and any('min' in p for p in pontos):
        v_min = min(p.get('min', p['v']) for p in pontos)
        v_max = max(p.get('max', p['v']) for p in pontos)
    else:
        v_min = min(p['v'] for p in pontos)
        v_max = max(p['v'] for p in pontos)
    v_avg   = sum(p['v'] for p in pontos) / len(pontos)
    ultimo  = pontos[-1]
    total_n = len(pontos)

    pontos = _downsample([{'t': p['t'], 'v': p['v']} for p in pontos], max_pontos)

    return {
        'itemid':     str(item_id),
        'name':       nome,
        'hostname':   hostname,
        'units':      units,
        'value_type': value_type,
        'fonte':      fonte,
        'pontos':     pontos,
        'stats': {
            'min': v_min, 'avg': v_avg, 'max': v_max,
            'ultimo': ultimo['v'], 'ultimo_t': ultimo['t'], 'n': total_n,
        },
    }
