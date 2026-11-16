"""
monitoramento/services.py
Camada de acesso à API Zabbix (JSON-RPC 2.0).

Compatibilidade:
  - Zabbix < 6.4 : auth no payload JSON
  - Zabbix 7.0+  : Bearer header (auth no payload foi removido)
"""
import logging
import re
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


# Disjuntor por URL: depois de um timeout/erro de conexão, a mesma URL falha
# na hora por _ZBX_FORA_TTL s. Sem isso, um Zabbix de cliente oscilando prendia
# uma thread do Gunicorn por 15 s por chamada (30 s com o fallback
# history→trends), e uma aba com 6–8 gráficos tomava a maioria das 12 threads
# — o CRM inteiro ficava lento para todo mundo (04/11/2026).
_ZBX_FORA_TTL = 30


class ZabbixForaDoAr(requests.ConnectionError):
    """Zabbix não respondeu há pouco; nem tentamos de novo (disjuntor)."""


def _chave_fora(url: str) -> str:
    return f"zbx_fora:{url.rstrip('/')}"


def _rpc(url: str, method: str, params: dict, auth: str = None, config=None) -> dict:
    endpoint = f"{url.rstrip('/')}/api_jsonrpc.php"
    if cache.get(_chave_fora(url)):
        raise ZabbixForaDoAr(f'{url} não respondeu há pouco (nova tentativa em até {_ZBX_FORA_TTL}s)')
    payload  = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    headers  = {"Content-Type": "application/json"}

    if auth:
        version = _get_zabbix_version(url)
        if version >= (7, 0, 0):
            headers["Authorization"] = f"Bearer {auth}"
        else:
            payload["auth"] = auth

    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=15, verify=False)
    except (requests.Timeout, requests.ConnectionError):
        cache.set(_chave_fora(url), 1, _ZBX_FORA_TTL)
        raise
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
    cache.delete(_chave_fora(config.url))   # teste manual sempre tenta de verdade
    version = _rpc(config.url, "apiinfo.version", {})
    _get_auth_token(config)
    return version


# ──────────────────────────────────────────────────────────────
# HOSTS
# ──────────────────────────────────────────────────────────────

# Até o Zabbix 5.2 a disponibilidade fica no HOST (um campo por tipo de
# interface); da 5.4 em diante, em cada interface. Pedir "available" na
# interface de um 5.2 devolve `interfaces: [[]]` (caso real: Sartor 5.2.7 e
# cliente 37 5.0 — status do card e da topologia quebravam).
_AVAIL_HOST = ["available", "snmp_available", "ipmi_available", "jmx_available"]


def _avail_no_host(config) -> bool:
    return _get_zabbix_version(config.url) < (5, 4, 0)


def _disponibilidade(h) -> list:
    """Valores de disponibilidade do host ("1" ok, "2" fora; "0" = desconhecido
    ou tipo de interface não usado, ignorado), nas duas formas da API."""
    vals = [i.get("available") for i in h.get("interfaces", [])
            if isinstance(i, dict) and i.get("available") not in (None, "0")]
    if not vals:
        vals = [h.get(k) for k in _AVAIL_HOST if h.get(k) not in (None, "0")]
    return vals


def listar_hosts(config, busca: str = "") -> list:
    auth   = _get_auth_token(config)
    params = {
        "output":           ["hostid", "host", "name", "status"] + (_AVAIL_HOST if _avail_no_host(config) else []),
        "selectInterfaces": ["ip", "port", "type", "available"],
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
        if ifaces and isinstance(ifaces[0], dict):
            ip = ifaces[0].get("ip", "")
        avail_vals = _disponibilidade(h)
        if "1" in avail_vals:
            available = "up"
        elif avail_vals and all(v == "2" for v in avail_vals):
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


def hosts_para_vinculo(config) -> list:
    """Todos os hosts (sem limite) com todos os IPs de interface — base do
    casamento Acesso ↔ host em monitoramento/vinculo_zabbix.py. `listar_hosts`
    só guarda o IP da primeira interface, e host com SNMP + agent costuma ter
    IPs diferentes em cada uma."""
    auth  = _get_auth_token(config)
    hosts = _rpc(config.url, "host.get", {
        "output":           ["hostid", "host", "name", "status"],
        "selectInterfaces": ["ip"],
    }, auth, config=config)
    return [
        {
            "hostid": h["hostid"],
            "host":   h["host"],
            "name":   h.get("name") or h["host"],
            "ips":    sorted({i.get("ip", "") for i in h.get("interfaces", []) if i.get("ip")}),
        }
        for h in hosts
    ]


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
    máximo `limit` pontos. Atalho de historico_itens() para um item só."""
    return historico_itens(config, [item_id], hours=hours, limit=limit).get(str(item_id), [])


def _meta_itens(config, item_ids, auth) -> dict:
    """{itemid: {'value_type', 'units'}} — cacheado por item (raramente muda);
    os que faltam no cache saem numa única chamada item.get."""
    base = getattr(config, 'id', config.url)
    metas, faltam = {}, []
    for iid in item_ids:
        m = cache.get(f"zbx_item_meta:{base}:{iid}")
        if m is None:
            faltam.append(iid)
        else:
            metas[iid] = m
    if faltam:
        info = _rpc(config.url, 'item.get', {
            'itemids': faltam,
            'output': ['itemid', 'value_type', 'units'],
        }, auth, config=config) or []
        achados = {str(i['itemid']): i for i in info}
        for iid in faltam:
            i = achados.get(iid)
            m = {'value_type': int(i.get('value_type', 3)), 'units': i.get('units', '')} if i \
                else {'value_type': 3, 'units': ''}
            cache.set(f"zbx_item_meta:{base}:{iid}", m, _ITEM_META_TTL)
            metas[iid] = m
    return metas


def _buscar_bruto(config, auth, fonte, item_ids, value_type, time_from, agora) -> dict:
    """{itemid: [{'t','v'}]} em ordem cronológica, numa chamada só para todos
    os itens (history.get/trend.get aceitam vários itemids)."""
    if fonte == 'history':
        bruto = _rpc(config.url, 'history.get', {
            'itemids':   item_ids,
            'history':   value_type,
            'time_from': time_from,
            'time_till': agora,
            'sortfield': 'clock',
            'sortorder': 'ASC',
            'limit':     _MAX_BRUTO * len(item_ids),
        }, auth, config=config) or []
        campo = 'value'
    else:
        bruto = _rpc(config.url, 'trend.get', {
            'itemids':   item_ids,
            'time_from': time_from,
            'time_till': agora,
            'limit':     _MAX_BRUTO * len(item_ids),
        }, auth, config=config) or []
        campo = 'value_avg'
    por_item = {iid: [] for iid in item_ids}
    for p in bruto:
        lista = por_item.get(str(p.get('itemid')))
        if lista is not None:
            lista.append({'t': int(p['clock']), 'v': float(p[campo])})
    for lista in por_item.values():
        lista.sort(key=lambda p: p['t'])
    return por_item


# Pontos brutos do `history` guardados por item: o poll seguinte pede ao
# Zabbix só o trecho novo, não a janela inteira de novo. No Zabbix do cliente
# 90 o history.get de 24h levava 2–12 s (e piorava a cada poll seguido);
# o incremental custa o mesmo de uma janela de 1–2 min.
_RAW_TTL      = 900      # sem poll por 15 min → próxima vez busca tudo
_RAW_MARGEM   = 180      # re-pede os últimos 3 min (valor que chega atrasado)
_RAW_MAX_PTS  = 60000    # item muito denso não vai pro cache


def _chave_raw(config, iid, value_type):
    return f"zbx_raw:{getattr(config, 'id', config.url)}:{iid}:{value_type}"


def _history_incremental(config, auth, item_ids, value_type, time_from, agora) -> dict:
    """Como _buscar_bruto('history'), mas reaproveita o que já foi baixado.
    Um item cujo cache cobre o início da janela só busca de `fim - margem`
    em diante; os demais buscam a janela toda (numa chamada só)."""
    saida, completos, delta, desde = {}, [], [], agora
    caches = {}
    for iid in item_ids:
        c = cache.get(_chave_raw(config, iid, value_type))
        if c and c['ini'] <= time_from and c['fim'] > time_from:
            caches[iid] = c
            delta.append(iid)
            desde = min(desde, c['fim'] - _RAW_MARGEM)
        else:
            completos.append(iid)

    # Guardado: tudo desde o início coberto (até _JANELA_TRENDS atrás),
    # mesmo que este pedido seja de uma janela menor — outro gráfico do
    # mesmo item pode estar em 24h enquanto este está em 1h.
    novos = {}
    if completos:
        novos.update(_buscar_bruto(config, auth, 'history', completos, value_type, time_from, agora))
    if delta:
        novos_delta = _buscar_bruto(config, auth, 'history', delta, value_type, max(desde, time_from), agora)
        for iid in delta:
            c = caches[iid]
            corte = novos_delta[iid][0]['t'] if novos_delta[iid] else agora + 1
            novos[iid] = [{'t': t, 'v': v} for t, v in c['p'] if t < corte] + novos_delta[iid]

    limite_antigo = agora - _JANELA_TRENDS
    for iid in item_ids:
        pts = novos.get(iid) or []
        if pts and len(pts) <= _RAW_MAX_PTS:
            ini_cob = time_from if iid in completos else caches[iid]['ini']
            if ini_cob < limite_antigo:
                ini_cob = limite_antigo
                pts = [p for p in pts if p['t'] >= limite_antigo]
            cache.set(_chave_raw(config, iid, value_type),
                      {'ini': ini_cob, 'fim': agora, 'p': [[p['t'], p['v']] for p in pts]},
                      _RAW_TTL)
        saida[iid] = [p for p in pts if p['t'] >= time_from]
    return saida


def _pos_processar(pontos, fonte, meta, limit):
    """Contador de bytes → bps, correção do serrilhado e downsample."""
    if not pontos:
        return []
    value_type  = meta['value_type']
    units_lower = (meta['units'] or '').lower()

    # Já é taxa (bps, pps, etc.) — não calcula delta
    if not any(u in units_lower for u in ('bps', 'pps', 'b/s', 'bit')):
        # Contador acumulativo de bytes — calcula delta e converte para bps
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
                taxa.append({'t': pontos[i]['t'], 'v': (delta * 8) / dt})
            pontos = taxa
            units_lower = 'bps'

    if fonte == 'history' and _eh_trafego(units_lower):
        pontos = _corrigir_serrilhado(pontos)

    return _downsample(pontos, limit)


def historico_itens(config, item_ids, hours=1, limit=300) -> dict:
    """{itemid: pontos} das últimas `hours` horas de vários itens de uma vez.

    O gráfico da aba Monitoramento pede Download e Upload juntos: antes eram
    duas requisições HTTP (cada uma com túnel, auth e item.get próprios) e duas
    idas ao Zabbix; agora é uma requisição e um history.get por value_type.

    O período inteiro é sempre coberto (busca a janela toda em ordem
    cronológica e reduz aqui), com fallback para `trends` nas janelas longas
    ou quando o `history` não tem o item.
    """
    item_ids = [str(i) for i in dict.fromkeys(item_ids) if i]
    if not item_ids:
        return {}
    auth      = _get_auth_token(config)
    agora     = int(time.time())
    time_from = agora - int(hours * 3600)
    metas     = _meta_itens(config, item_ids, auth)

    janela = agora - time_from
    fontes = ['trends', 'history'] if janela > _JANELA_TRENDS else ['history', 'trends']

    grupos = {}
    for iid in item_ids:
        grupos.setdefault(metas[iid]['value_type'], []).append(iid)

    saida = {}
    for value_type, ids in grupos.items():
        pendentes = list(ids)
        for fonte in fontes:
            if not pendentes:
                break
            try:
                if fonte == 'history':
                    bruto = _history_incremental(config, auth, list(pendentes), value_type, time_from, agora)
                else:
                    bruto = _buscar_bruto(config, auth, fonte, list(pendentes), value_type, time_from, agora)
            except Exception as exc:
                if isinstance(exc, ZabbixForaDoAr) or isinstance(exc, requests.ConnectionError):
                    raise
                logger.warning('%s.get dos itens %s falhou: %s', fonte, pendentes, exc)
                continue
            for iid in list(pendentes):
                if bruto.get(iid):
                    saida[iid] = _pos_processar(bruto[iid], fonte, metas[iid], limit)
                    pendentes.remove(iid)
        for iid in pendentes:
            saida[iid] = []
    return saida


# ──────────────────────────────────────────────────────────────
# STATUS EM TEMPO REAL
# ──────────────────────────────────────────────────────────────

def status_nodes(config, host_ids: list) -> dict:
    if not host_ids:
        return {}

    auth      = _get_auth_token(config)
    no_host   = _avail_no_host(config)
    params    = {"output": ["hostid"] + (_AVAIL_HOST if no_host else []), "hostids": host_ids}
    if not no_host:
        params["selectInterfaces"] = ["available"]
    hosts_raw = _rpc(config.url, "host.get", params, auth)

    status: dict = {}
    for h in hosts_raw:
        hid        = h["hostid"]
        avail_vals = _disponibilidade(h)
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
    parear_trafego(result)
    return result


# Sentido do tráfego pela chave (net.if.in[...] / ifHCInOctets) ou pelo nome
# ("Bits received" / "Traffic in" / "Entrada"). Erros/descartes ficam de fora:
# net.if.in.errors[ não casa com net.if.in[.
_SENTIDO_KEY = re.compile(r'^net\.if\.(in|out)\[', re.I)
_SENTIDO_OID = re.compile(r'if(?:hc)?(in|out)octets', re.I)
_SENTIDO_NOME = [
    ('in',  re.compile(r'bits? receiv|traffic in\b|\bentrada\b|\bdownload\b|\brx bits\b', re.I)),
    ('out', re.compile(r'bits? sent|traffic out\b|\bsa[ií]da\b|\bupload\b|\btx bits\b', re.I)),
]
# Pares de troca in↔out para achar o item gêmeo pela chave/nome
_TROCAS = [
    (re.compile(r'(?<=net\.if\.)in(?=\[)', re.I), 'out'),
    (re.compile(r'(?<=if)(hc)?in(?=octets)', re.I), r'\1out'),
    (re.compile(r'received', re.I), 'sent'),
    (re.compile(r'receive\b', re.I), 'sent'),
    (re.compile(r'traffic in\b', re.I), 'traffic out'),
    (re.compile(r'\bentrada\b', re.I), 'saída'),
    (re.compile(r'\bdownload\b', re.I), 'upload'),
    (re.compile(r'\brx\b', re.I), 'tx'),
]


def _sentido_trafego(it):
    key = it.get('key') or ''
    m = _SENTIDO_KEY.match(key)
    if m:
        return m.group(1).lower()
    nome = it.get('name') or ''
    for sentido, rx in _SENTIDO_NOME:
        if rx.search(nome):
            return sentido
    m = _SENTIDO_OID.search(key)
    if m and '.errors' not in key and '.discards' not in key:
        return m.group(1).lower()
    return None


def _nome_iface(nome):
    """'Interface ge-0/0/1(UPLINK): Bits received' → 'ge-0/0/1(UPLINK)'."""
    n = (nome or '').split(':', 1)[0].strip() if ':' in (nome or '') else (nome or '')
    n = re.sub(r'^interface\s+', '', n, flags=re.I)
    for _s, rx in _SENTIDO_NOME:
        n = rx.sub('', n)
    return re.sub(r'\s{2,}', ' ', n).strip(' -–:') or (nome or '')


def _trocar(texto, para_out):
    """Versão do sentido oposto de uma chave/nome (in→out ou out→in)."""
    if para_out:
        out = texto
        for rx, rep in _TROCAS:
            out = rx.sub(rep, out)
        return out
    inv = [
        (re.compile(r'(?<=net\.if\.)out(?=\[)', re.I), 'in'),
        (re.compile(r'(?<=if)(hc)?out(?=octets)', re.I), r'\1in'),
        (re.compile(r'\bsent\b', re.I), 'received'),
        (re.compile(r'traffic out\b', re.I), 'traffic in'),
        (re.compile(r'\bsa[ií]da\b', re.I), 'entrada'),
        (re.compile(r'\bupload\b', re.I), 'download'),
        (re.compile(r'\btx\b', re.I), 'rx'),
    ]
    out = texto
    for rx, rep in inv:
        out = rx.sub(rep, out)
    return out


def parear_trafego(itens: list) -> list:
    """Marca em cada item de tráfego `sentido` ('in'/'out'), `iface` (nome da
    interface) e `par` (itemid do gêmeo do outro sentido), para a aba
    Monitoramento preencher o Upload sozinho ao escolher o Download (e
    vice-versa). Casa primeiro pela chave trocada (net.if.in[X] ↔
    net.if.out[X], ifHCInOctets.N ↔ ifHCOutOctets.N), depois pelo nome trocado
    ("Bits received" ↔ "Bits sent") e por último pelo nome da interface.
    Altera e devolve a própria lista."""
    trafego = []
    for it in itens:
        sentido = _sentido_trafego(it)
        it['sentido'] = sentido
        it['iface'] = _nome_iface(it.get('name')) if sentido else ''
        it.setdefault('key', it.get('key_', ''))
        it['par'] = None
        if sentido:
            trafego.append(it)

    por_key  = {(it['sentido'], it['key'].lower()): it for it in trafego}
    por_nome = {(it['sentido'], it['name'].lower()): it for it in trafego}
    por_if   = {}
    for it in trafego:
        por_if.setdefault((it['sentido'], it['iface'].lower()), []).append(it)

    for it in trafego:
        if it['par']:
            continue
        outro = 'out' if it['sentido'] == 'in' else 'in'
        para_out = it['sentido'] == 'in'
        gemeo = por_key.get((outro, _trocar(it['key'], para_out).lower()))
        if gemeo is None:
            gemeo = por_nome.get((outro, _trocar(it['name'], para_out).lower()))
        if gemeo is None:
            cands = por_if.get((outro, it['iface'].lower()), [])
            if len(cands) == 1:
                gemeo = cands[0]
        if gemeo is not None and gemeo['itemid'] != it['itemid'] and not gemeo.get('par'):
            it['par'] = gemeo['itemid']
            gemeo['par'] = it['itemid']
    return itens

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


def _eh_trafego(units: str) -> bool:
    u = (units or '').lower()
    return any(x in u for x in ('bps', 'b/s', 'bit'))


def _corrigir_serrilhado(pontos: list, limiar: float = 0.08) -> list:
    """Desfaz o zigue-zague de contador em séries de tráfego.

    Quando o equipamento atualiza o contador de octetos num ritmo diferente da
    coleta do Zabbix, um intervalo recebe bytes "a mais" e um vizinho "a menos":
    a série vira 22.7 → 14.5 → 23.3 → 14.5 Gbps com tráfego real estável em
    ~18.7. No gráfico isso aparece como queda que não aconteceu (falso
    positivo).

    1. Compensação: pontos fora da mediana local com sinais alternados, a até
       2 coletas um do outro (o pico às vezes vem uma coleta depois), viram a
       média do trecho — o volume trafegado é preservado.
    2. Suavização 1-2-1 (também preserva volume) para o resíduo do serrilhado.
       Queda profunda (< 20% da mediana local, ex.: link caído) fica fora da
       suavização e aparece crua, assim como os vizinhos não a "puxam".
    """
    n = len(pontos)
    if n < 4:
        return pontos
    vals = [p['v'] for p in pontos]

    def _bases(vs):
        out = []
        for i in range(n):
            janela = sorted(vs[max(0, i - 3):i + 4])
            out.append(janela[len(janela) // 2])
        return out

    base = _bases(vals)
    desvio = [
        (vals[i] - base[i]) if base[i] > 0 and abs(vals[i] - base[i]) > limiar * base[i] else 0.0
        for i in range(n)
    ]

    corr = list(vals)
    sig = [i for i in range(n) if desvio[i]]
    k = 0
    while k < len(sig):
        m = k
        while (m + 1 < len(sig) and sig[m + 1] - sig[m] <= 2
               and (desvio[sig[m]] > 0) != (desvio[sig[m + 1]] > 0)):
            m += 1
        if m > k:
            i, j = sig[k], sig[m]
            media = sum(vals[i:j + 1]) / (j - i + 1)
            for x in range(i, j + 1):
                corr[x] = media
        k = m + 1

    base = _bases(corr)
    queda = [b > 0 and v < 0.2 * b for v, b in zip(corr, base)]
    saida = [dict(p) for p in pontos]
    for i in range(n):
        if queda[i]:
            saida[i]['v'] = corr[i]
            continue
        soma, peso = 2 * corr[i], 2
        for j in (i - 1, i + 1):
            if 0 <= j < n and not queda[j]:
                soma += corr[j]
                peso += 1
        saida[i]['v'] = soma / peso
    return saida


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
    if fonte == 'history' and _eh_trafego(units):
        pontos = _corrigir_serrilhado(pontos)
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
