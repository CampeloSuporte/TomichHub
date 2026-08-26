"""
Consultas IRR para a ferramenta de Looking Glass.

Duas frentes:

* ``gerar_filtro()``  — roda o ``bgpq4`` e devolve o prefix-list/filtro pronto
  pro fabricante escolhido, junto com a lista de prefixos em JSON.
* ``consultar_as_set()`` — expande um as-set: membros diretos (inclusive os
  as-sets aninhados), ASNs do fechamento recursivo, o objeto IRR cru de cada
  base (RADB, LACNIC, TC, RIPE…) e a contagem de prefixos v4/v6.

O bgpq4 e as consultas IRRd falam com o mirror do NTT (``rr.ntt.net``), que
espelha praticamente todas as bases (RADB, LACNIC, TC, RIPE, ARIN, ALTDB…).
"""

import json
import re
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

BGPQ4_BIN   = shutil.which('bgpq4') or '/usr/bin/bgpq4'
IRRD_HOST   = 'rr.ntt.net'
IRRD_PORTA  = 43
TIMEOUT_PAD = 75          # gunicorn corta em 120s — sobra folga pro resto

# Servidores IRRd conhecidos (o usuário pode escolher outro mirror)
IRRD_HOSTS = {
    'rr.ntt.net':      'NTT (espelha RADB, LACNIC, TC, RIPE, ARIN…)',
    'whois.radb.net':  'RADB',
    'irr.lacnic.net':  'LACNIC',
    'rr.level3.net':   'Level3',
    'whois.bgp.net.br': 'IRR.br / NIC.br',
}

# fabricante → flags do bgpq4 (vide `man bgpq4`)
VENDORS = {
    'cisco':      {'nome': 'Cisco IOS',              'flags': [],       'ext': 'txt'},
    'cisco-xr':   {'nome': 'Cisco IOS XR',           'flags': ['-X'],   'ext': 'txt'},
    'juniper':    {'nome': 'Juniper Junos',          'flags': ['-J'],   'ext': 'txt'},
    'juniper-rf': {'nome': 'Juniper route-filter-list', 'flags': ['-z'], 'ext': 'txt'},
    'huawei':     {'nome': 'Huawei VRP',             'flags': ['-U'],   'ext': 'txt'},
    'huawei-xpl': {'nome': 'Huawei XPL',             'flags': ['-u'],   'ext': 'txt'},
    'mikrotik7':  {'nome': 'MikroTik RouterOS v7',   'flags': ['-K7'],  'ext': 'rsc'},
    'mikrotik6':  {'nome': 'MikroTik RouterOS v6',   'flags': ['-K'],   'ext': 'rsc'},
    'nokia-md':   {'nome': 'Nokia SR OS (MD-CLI)',   'flags': ['-n'],   'ext': 'txt'},
    'nokia':      {'nome': 'Nokia SR OS (Classic)',  'flags': ['-N'],   'ext': 'txt'},
    'srlinux':    {'nome': 'Nokia SR Linux',         'flags': ['-n2'],  'ext': 'txt'},
    'arista':     {'nome': 'Arista EOS',             'flags': ['-e'],   'ext': 'txt'},
    'bird':       {'nome': 'BIRD',                   'flags': ['-b'],   'ext': 'conf'},
    'openbgpd':   {'nome': 'OpenBGPD',               'flags': ['-B'],   'ext': 'conf'},
    'json':       {'nome': 'JSON',                   'flags': ['-j'],   'ext': 'json'},
    'lista':      {'nome': 'Lista simples (um prefixo por linha)',
                   'flags': ['-F', '%n/%l\n'], 'ext': 'txt'},
}

# AS64512 · AS-CLIENTES · AS64512:AS-CLIENTES · RS-EXEMPLO · AS-X:AS-Y:AS-Z
_RE_ASN    = re.compile(r'^AS\d{1,10}$', re.I)
_RE_SET    = re.compile(r'^(?:AS\d{1,10}|(?:AS|RS)-[A-Z0-9_.\-]{1,60})'
                        r'(?::(?:AS\d{1,10}|(?:AS|RS)-[A-Z0-9_.\-]{1,60}))*$', re.I)
_RE_FONTE  = re.compile(r'^[A-Z0-9,\-]{1,120}$', re.I)
_RE_HOST   = re.compile(r'^[A-Za-z0-9.\-]{1,80}$')
_RE_NOME   = re.compile(r'^[A-Za-z0-9_.\-:]{1,60}$')


class IRRError(Exception):
    """Erro de validação/execução de uma consulta IRR."""


# ─────────────────────────────────────────────────────────────────────────────
# Validação
# ─────────────────────────────────────────────────────────────────────────────
def validar_objeto(texto, exigir_set=False):
    """Normaliza e valida um objeto IRR (ASN ou as-set).

    Recusa qualquer coisa fora do formato RPSL — o valor entra na linha de
    comando do bgpq4 e num socket whois, então nada de espaço, `-` inicial
    ou metacaractere.
    """
    obj = (texto or '').strip().upper()
    if not obj:
        raise IRRError('Informe um ASN ou as-set.')
    if obj.isdigit():
        obj = 'AS' + obj
    if not _RE_SET.match(obj):
        raise IRRError(f'Objeto IRR inválido: {texto!r}. '
                       'Use AS64512, AS-CLIENTES ou AS64512:AS-CLIENTES.')
    if exigir_set and _RE_ASN.match(obj):
        raise IRRError(f'{obj} é um ASN, não um as-set. '
                       'Informe um objeto AS-… ou RS-….')
    return obj


def validar_host(texto):
    host = (texto or '').strip() or IRRD_HOST
    if not _RE_HOST.match(host):
        raise IRRError(f'Servidor IRRd inválido: {texto!r}')
    return host


def validar_fontes(texto):
    fontes = (texto or '').strip().upper()
    if not fontes:
        return ''
    if not _RE_FONTE.match(fontes):
        raise IRRError(f'Lista de fontes inválida: {texto!r}')
    return fontes


def nome_lista_padrao(objeto):
    """Nome do prefix-list a partir do objeto (AS64512:AS-X → AS-X)."""
    base = objeto.split(':')[-1]
    return re.sub(r'[^A-Za-z0-9_\-]', '-', base)


# ─────────────────────────────────────────────────────────────────────────────
# bgpq4
# ─────────────────────────────────────────────────────────────────────────────
def bgpq4_disponivel():
    return bool(BGPQ4_BIN and shutil.which(BGPQ4_BIN))


def versao_bgpq4():
    try:
        p = subprocess.run([BGPQ4_BIN, '-v'], capture_output=True, text=True, timeout=10)
        for linha in (p.stdout or p.stderr).splitlines():
            if linha.lower().startswith('version:'):
                return 'bgpq4 ' + linha.split(':', 1)[1].strip()
        return 'bgpq4'
    except Exception:
        return ''


def _montar_args(objeto, af, *, vendor='cisco', nome=None, host=IRRD_HOST,
                 fontes='', agregar=False, maxlen=None, mais_especificos=None,
                 validar_asn=False):
    args = ['-h', host]
    if fontes:
        args += ['-S', fontes]
    args += ['-6' if int(af) == 6 else '-4']
    args += VENDORS[vendor]['flags']
    if nome and vendor not in ('lista',):
        args += ['-l', nome]
    if agregar:
        args.append('-A')
    if maxlen:
        args += ['-m', str(int(maxlen))]
    if mais_especificos:
        args += ['-R', str(int(mais_especificos))]
    if validar_asn:
        args.append('-w')
    args.append(objeto)
    return args


def _rodar(args, timeout=TIMEOUT_PAD):
    inicio = time.monotonic()
    try:
        p = subprocess.run([BGPQ4_BIN] + args, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise IRRError(f'bgpq4 excedeu {timeout}s — as-set grande demais ou '
                       'servidor IRRd lento. Tente de novo ou restrinja as fontes (-S).')
    except FileNotFoundError:
        raise IRRError('bgpq4 não está instalado no servidor (apt install bgpq4).')
    saida = p.stdout or ''
    erro  = (p.stderr or '').strip()
    return {
        'rc':      p.returncode,
        'saida':   saida,
        'erro':    erro,
        'segundos': round(time.monotonic() - inicio, 2),
        'comando': 'bgpq4 ' + ' '.join(_shq(a) for a in args),
    }


def _shq(arg):
    """Só pra exibir o comando na tela de forma copiável."""
    return arg if re.match(r'^[A-Za-z0-9_.:/=\-]+$', arg) else "'" + arg.replace("'", "'\\''") + "'"


def _prefixos_json(objeto, af, timeout=TIMEOUT_PAD, **kw):
    """Roda o bgpq4 em modo JSON e devolve a lista de prefixos."""
    args = _montar_args(objeto, af, vendor='json', nome='p', **kw)
    r = _rodar(args, timeout=timeout)
    prefixos = []
    if r['rc'] == 0 and r['saida'].strip():
        try:
            prefixos = json.loads(r['saida']).get('p', []) or []
        except (ValueError, AttributeError):
            prefixos = []
    r['prefixos'] = prefixos
    return r


def contar_prefixos(objeto, af, timeout=45, **kw):
    """Só a contagem — evita carregar/parsear o JSON (as-sets grandes passam
    de 30 MB de saída)."""
    args = _montar_args(objeto, af, vendor='lista', **kw)
    r = _rodar(args, timeout=timeout)
    texto = r['saida'].strip()          # o bgpq4 fecha a saída com uma linha extra
    return {
        'total':    (texto.count('\n') + 1 if texto else 0) if r['rc'] == 0 else None,
        'erro':     r['erro'] if r['rc'] != 0 else '',
        'segundos': r['segundos'],
    }


def gerar_filtro(objeto, *, vendor='cisco', afs=(4, 6), nome=None, host=IRRD_HOST,
                 fontes='', agregar=False, maxlen4=None, maxlen6=None,
                 mais_especificos=None, validar_asn=False, limite_prefixos=8000,
                 limite_config=1_500_000):
    """Gera o filtro do fabricante + a lista de prefixos, para cada família.

    As chamadas (config e JSON, v4 e v6) rodam em paralelo — cada uma é um
    processo bgpq4 independente falando com o IRRd.
    """
    if vendor not in VENDORS:
        raise IRRError(f'Fabricante desconhecido: {vendor!r}')
    nome = nome or nome_lista_padrao(objeto)
    if not _RE_NOME.match(nome):
        raise IRRError(f'Nome de lista inválido: {nome!r}')

    comum = dict(host=host, fontes=fontes, agregar=agregar,
                 mais_especificos=mais_especificos, validar_asn=validar_asn)
    tarefas = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for af in afs:
            maxlen = maxlen4 if int(af) == 4 else maxlen6
            args = _montar_args(objeto, af, vendor=vendor, nome=nome,
                                maxlen=maxlen, **comum)
            tarefas[(af, 'cfg')] = ex.submit(_rodar, args)
            if vendor != 'json':
                tarefas[(af, 'json')] = ex.submit(_prefixos_json, objeto, af,
                                                  maxlen=maxlen, **comum)

    resultados = {}
    for af in afs:
        cfg = tarefas[(af, 'cfg')].result()
        if vendor == 'json':
            jsn = dict(cfg)
            try:
                jsn['prefixos'] = json.loads(cfg['saida']).get(nome, []) or []
            except (ValueError, AttributeError):
                jsn['prefixos'] = []
        else:
            jsn = tarefas[(af, 'json')].result()

        prefixos = jsn.get('prefixos', [])
        config   = cfg['saida']
        # Filtro de as-set grande passa de 30 MB — a tela recebe só o começo e
        # o usuário baixa o arquivo inteiro pelo botão.
        corta_cfg = limite_config is not None and len(config) > limite_config
        resultados[f'v{af}'] = {
            'af':          int(af),
            'comando':     cfg['comando'],
            'config':      (config[:limite_config] + '\n! … saída truncada — use o botão Baixar\n'
                            if corta_cfg else config),
            'config_truncado': corta_cfg,
            'erro':        cfg['erro'] if cfg['rc'] != 0 or not cfg['saida'].strip() else '',
            'segundos':    cfg['segundos'],
            'total':       len(prefixos),
            'prefixos':    prefixos[:limite_prefixos] if limite_prefixos else [],
            'truncado':    len(prefixos) > limite_prefixos,
            'bytes_config': len(config),
        }
    return resultados


# ─────────────────────────────────────────────────────────────────────────────
# IRRd (protocolo whois "!" — conexão persistente)
# ─────────────────────────────────────────────────────────────────────────────
class IRRd:
    """Cliente mínimo do protocolo de consulta do IRRd (porta 43).

    Suporta só o que a ferramenta precisa: ``!i<set>`` (membros diretos),
    ``!i<set>,1`` (expansão recursiva em ASNs) e ``!g``/``!6`` (rotas de um ASN).
    """

    def __init__(self, host=IRRD_HOST, porta=IRRD_PORTA, timeout=25):
        self.sock = socket.create_connection((host, porta), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b''
        self._enviar('!!')                     # mantém a conexão aberta
        self._enviar('!nCRM-NOC-LG')
        self._resposta()

    # -- baixo nível -------------------------------------------------------
    def _enviar(self, cmd):
        self.sock.sendall((cmd + '\n').encode())

    def _linha(self):
        while b'\n' not in self._buf:
            ch = self.sock.recv(65536)
            if not ch:
                raise IRRError('Conexão com o servidor IRRd caiu.')
            self._buf += ch
        linha, _, self._buf = self._buf.partition(b'\n')
        return linha.decode('utf-8', 'replace').rstrip('\r')

    def _bytes(self, n):
        while len(self._buf) < n:
            ch = self.sock.recv(65536)
            if not ch:
                raise IRRError('Conexão com o servidor IRRd caiu.')
            self._buf += ch
        dados, self._buf = self._buf[:n], self._buf[n:]
        return dados.decode('utf-8', 'replace')

    def _resposta(self):
        """None = chave não encontrada / erro; str = conteúdo (pode ser '')."""
        linha = self._linha()
        if linha[:1] == 'A':
            try:
                n = int(linha[1:])
            except ValueError:
                return None
            dados = self._bytes(n)
            self._linha()                      # 'C' de fim
            return dados
        if linha[:1] == 'C':
            return ''
        return None

    # -- alto nível --------------------------------------------------------
    def consultar(self, cmd):
        self._enviar(cmd)
        return self._resposta()

    def membros(self, objeto, recursivo=False):
        """`!i<set>` → lista de membros. Recursivo devolve só ASNs."""
        resp = self.consultar(f'!i{objeto}' + (',1' if recursivo else ''))
        if resp is None:
            return None
        return [m for m in resp.split() if m]

    def rotas(self, asn, v6=False):
        resp = self.consultar(('!6' if v6 else '!g') + asn)
        if resp is None:
            return []
        return [p for p in resp.split() if p]

    def fechar(self):
        try:
            self._enviar('!q')
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fechar()


def objeto_rpsl(objeto, host=IRRD_HOST, timeout=20, limite=256 * 1024):
    """Consulta whois "normal" e devolve o(s) objeto(s) RPSL crus."""
    try:
        with socket.create_connection((host, IRRD_PORTA), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(f'{objeto}\r\n'.encode())
            partes, total = [], 0
            while total < limite:
                ch = s.recv(65536)
                if not ch:
                    break
                partes.append(ch)
                total += len(ch)
        return b''.join(partes).decode('utf-8', 'replace')
    except OSError as e:
        raise IRRError(f'Falha ao consultar {host}: {e}')


def _parsear_rpsl(texto):
    """Quebra a saída whois em objetos {chave: valor, ...} por base IRR."""
    objetos, atual, ultima = [], {}, None
    for linha in texto.splitlines():
        if not linha.strip() or linha.lstrip().startswith('%'):
            if atual:
                objetos.append(atual)
                atual, ultima = {}, None
            continue
        if linha[:1] in (' ', '\t') and ultima:          # continuação
            atual[ultima] += ' ' + linha.strip()
            continue
        if ':' not in linha:
            continue
        chave, _, valor = linha.partition(':')
        chave, valor = chave.strip().lower(), valor.strip()
        if chave in atual:
            atual[chave] += ',' + valor
        else:
            atual[chave] = valor
        ultima = chave
    if atual:
        objetos.append(atual)
    return objetos


def nomes_asn(asns, chunk=100, timeout=12):
    """Nome/holder de vários ASNs de uma vez (RIPEstat aceita lista)."""
    import requests

    asns = [a for a in asns if a]
    if not asns:
        return {}

    def _lote(lote):
        try:
            r = requests.get(
                'https://stat.ripe.net/data/as-names/data.json',
                params={'resource': ','.join(f'AS{a}' for a in lote)},
                timeout=timeout, headers={'User-Agent': 'CRM-LG/1.0'},
            )
            return (r.json().get('data', {}).get('names') or {})
        except Exception:
            return {}

    lotes = [asns[i:i + chunk] for i in range(0, len(asns), chunk)]
    nomes = {}
    with ThreadPoolExecutor(max_workers=min(6, len(lotes))) as ex:
        for parcial in ex.map(_lote, lotes):
            nomes.update(parcial)
    return nomes


def consultar_as_set(objeto, *, host=IRRD_HOST, fontes='', limite_nomes=600,
                     com_prefixos=True, limite_lista=2000):
    """Expande um as-set: membros diretos, ASNs recursivos, objeto e prefixos."""
    inicio  = time.monotonic()
    avisos  = []
    e_asn   = bool(_RE_ASN.match(objeto))

    # 1) IRRd: membros diretos + expansão recursiva (uma conexão só)
    diretos = recursivos = None
    if not e_asn:
        try:
            with IRRd(host=host) as irr:
                diretos    = irr.membros(objeto)
                recursivos = irr.membros(objeto, recursivo=True)
        except (IRRError, OSError) as e:
            avisos.append(f'Consulta IRRd em {host} falhou: {e}')
    else:
        diretos = recursivos = [objeto]

    # 2) objeto RPSL cru + contagem de prefixos, em paralelo
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_rpsl = ex.submit(objeto_rpsl, objeto, host)
        f_v4 = f_v6 = None
        if com_prefixos and bgpq4_disponivel():
            f_v4 = ex.submit(contar_prefixos, objeto, 4, host=host, fontes=fontes)
            f_v6 = ex.submit(contar_prefixos, objeto, 6, host=host, fontes=fontes)

        try:
            texto_rpsl = f_rpsl.result()
        except IRRError as e:
            texto_rpsl = ''
            avisos.append(str(e))

        prefixos = {}
        for chave, fut in (('v4', f_v4), ('v6', f_v6)):
            if fut is None:
                continue
            try:
                prefixos[chave] = fut.result()
            except IRRError as e:
                prefixos[chave] = {'total': None, 'erro': str(e)}

    # 3) o objeto em cada base IRR (RADB × LACNIC × TC… divergem com frequência)
    fontes_obj = []
    for obj in _parsear_rpsl(texto_rpsl):
        chave = 'as-set' if 'as-set' in obj else ('aut-num' if 'aut-num' in obj else None)
        if not chave:
            continue
        membros = [m.strip() for m in obj.get('members', '').split(',') if m.strip()]
        fontes_obj.append({
            'tipo':      chave,
            'nome':      obj.get(chave, ''),
            'source':    obj.get('source', '?'),
            'descr':     obj.get('descr', '') or obj.get('as-name', ''),
            'mnt_by':    obj.get('mnt-by', ''),
            'alterado':  obj.get('last-modified', '') or obj.get('changed', ''),
            'membros':   membros,
            'n_membros': len(membros),
        })

    if len({f['source'] for f in fontes_obj}) > 1:
        avisos.append(
            f'{objeto} existe em {len({f["source"] for f in fontes_obj})} bases IRR '
            '— o upstream pode usar uma diferente da sua. Confira as fontes abaixo.'
        )

    # 4) separa membros diretos entre as-sets aninhados e ASNs
    sets_aninhados, asns_diretos = [], []
    for m in (diretos or []):
        (asns_diretos if _RE_ASN.match(m) else sets_aninhados).append(m.upper())

    asns = sorted({m.upper().lstrip('AS') for m in (recursivos or [])
                   if _RE_ASN.match(m)}, key=lambda x: int(x))

    # 5) nomes dos ASNs (limitado — as-set grande tem milhares)
    nomes = {}
    if asns and limite_nomes:
        nomes = nomes_asn(asns[:limite_nomes])
        if len(asns) > limite_nomes:
            avisos.append(f'Nomes carregados só dos {limite_nomes} primeiros ASNs '
                          f'(o set tem {len(asns)}).')

    lista = asns if not limite_lista else asns[:limite_lista]
    if limite_lista and len(asns) > limite_lista:
        avisos.append(f'A tela lista os {limite_lista} primeiros ASNs — '
                      'use "Baixar .txt" para a lista completa.')

    return {
        'objeto':         objeto,
        'e_asn':          e_asn,
        'existe':         bool(fontes_obj) or bool(recursivos),
        'host':           host,
        'fontes':         fontes_obj,
        'sets_aninhados': sorted(set(sets_aninhados)),
        'asns_diretos':   sorted({a.lstrip('AS') for a in asns_diretos}, key=lambda x: int(x)),
        'asns':           [{'asn': a, 'nome': nomes.get(a, '')} for a in lista],
        'total_asns':     len(asns),
        'lista_truncada': bool(limite_lista) and len(asns) > limite_lista,
        'prefixos':       prefixos,
        'avisos':         avisos,
        'segundos':       round(time.monotonic() - inicio, 2),
    }
