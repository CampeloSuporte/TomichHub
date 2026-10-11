"""
Ferramentas de rede do dashboard do cliente (Ping, Traceroute, MTR, Teste de
Portas, Teste de Qualidade e Calculadora IP).

Os testes rodam a partir do proxy SSH do cliente — a origem é a rede dele, não
o servidor do CRM. A calculadora é só conta e roda aqui mesmo.

O destino e as portas chegam do navegador do cliente e viram comando de shell
no proxy: tudo passa por validar_destino/validar_portas (whitelist) e ainda é
citado com shlex.quote. Não montar comando com texto cru do request.
"""
import ipaddress
import re
import shlex
import statistics

FERRAMENTAS_PROXY = ('ping', 'traceroute', 'mtr', 'portas', 'qualidade')

_HOSTNAME_RE = re.compile(
    r'^(?=.{1,253}$)(?!-)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
    r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$'
)

MAX_PORTAS = 20


class ErroValidacao(ValueError):
    pass


def validar_destino(valor):
    """IP (v4/v6) ou hostname. Devolve o destino normalizado ou levanta ErroValidacao."""
    destino = (valor or '').strip()
    if not destino:
        raise ErroValidacao('Informe o destino (IP ou domínio).')
    try:
        return str(ipaddress.ip_address(destino))
    except ValueError:
        pass
    if not _HOSTNAME_RE.match(destino):
        raise ErroValidacao('Destino inválido: use um IP ou um domínio (ex.: google.com).')
    return destino.rstrip('.').lower()


def validar_portas(valor):
    """'22, 80, 443' ou '8000-8005' → lista de ints (máx. MAX_PORTAS)."""
    texto = str(valor or '').strip()
    if not texto:
        raise ErroValidacao('Informe ao menos uma porta.')
    portas = []
    for parte in re.split(r'[\s,;]+', texto):
        if not parte:
            continue
        m = re.fullmatch(r'(\d{1,5})(?:-(\d{1,5}))?', parte)
        if not m:
            raise ErroValidacao(f'Porta inválida: {parte}')
        ini = int(m.group(1))
        fim = int(m.group(2) or ini)
        if not (1 <= ini <= 65535 and 1 <= fim <= 65535) or fim < ini:
            raise ErroValidacao(f'Porta fora do intervalo 1-65535: {parte}')
        if fim - ini + 1 > MAX_PORTAS:
            raise ErroValidacao(f'No máximo {MAX_PORTAS} portas por teste.')
        for p in range(ini, fim + 1):
            if p not in portas:
                portas.append(p)
    if len(portas) > MAX_PORTAS:
        raise ErroValidacao(f'No máximo {MAX_PORTAS} portas por teste.')
    return portas


def _flag_ip(destino, ipv):
    """-4/-6: IP literal manda; hostname segue a escolha do usuário."""
    try:
        return '-6' if ipaddress.ip_address(destino).version == 6 else '-4'
    except ValueError:
        return '-6' if ipv == 'v6' else '-4'


def _int(valor, padrao, minimo, maximo):
    try:
        return max(minimo, min(maximo, int(valor)))
    except (TypeError, ValueError):
        return padrao


# ── Comandos ─────────────────────────────────────────────────────────────

def cmd_ping(destino, ipv, pacotes):
    return f'ping {_flag_ip(destino, ipv)} -c {pacotes} -W 2 {shlex.quote(destino)} 2>&1'


def cmd_traceroute(destino, ipv):
    d, f = shlex.quote(destino), _flag_ip(destino, ipv)
    return (
        f'if command -v traceroute >/dev/null 2>&1; then traceroute -n -w 2 -q 1 -m 30 {f} {d} 2>&1; '
        f'elif command -v tracepath >/dev/null 2>&1; then tracepath -n {f} -m 30 {d} 2>&1; '
        f'else echo "SEM_FERRAMENTA traceroute"; fi'
    )


def cmd_mtr(destino, ipv, ciclos):
    d, f = shlex.quote(destino), _flag_ip(destino, ipv)
    return (
        f'if command -v mtr >/dev/null 2>&1; then '
        f'mtr --report --report-wide --report-cycles {ciclos} --no-dns {f} {d} 2>&1; '
        f'elif command -v traceroute >/dev/null 2>&1; then '
        f'echo "AVISO mtr não instalado no proxy; usando traceroute"; traceroute -n -w 2 -q 3 -m 30 {f} {d} 2>&1; '
        f'else echo "SEM_FERRAMENTA mtr"; fi'
    )


def cmd_portas(destino, portas):
    # bash /dev/tcp distingue recusada (rc 1) de filtrada (timeout → 124);
    # sem bash, cai para nc -z (só aberta/fechada).
    d = shlex.quote(destino)
    lista = ' '.join(str(p) for p in portas)
    return (
        f'H={d}; for P in {lista}; do '
        f'if command -v bash >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then '
        f'timeout 3 bash -c "exec 3<>/dev/tcp/$H/$P" >/dev/null 2>&1; RC=$?; '
        f'if [ $RC -eq 0 ]; then echo "PORTA $P aberta"; '
        f'elif [ $RC -eq 124 ]; then echo "PORTA $P filtrada"; '
        f'else echo "PORTA $P fechada"; fi; '
        f'elif command -v nc >/dev/null 2>&1; then '
        f'if nc -z -w 3 "$H" "$P" >/dev/null 2>&1; then echo "PORTA $P aberta"; else echo "PORTA $P fechada"; fi; '
        f'else echo "SEM_FERRAMENTA portas"; break; fi; done'
    )


def cmd_qualidade(destino, ipv, pacotes):
    return f'ping {_flag_ip(destino, ipv)} -c {pacotes} -i 0.2 -W 2 {shlex.quote(destino)} 2>&1'


# ── Leitura das saídas ───────────────────────────────────────────────────

def _sem_ferramenta(saida):
    m = re.search(r'SEM_FERRAMENTA (\w+)', saida or '')
    return m.group(1) if m else None


def parse_ping(saida):
    r = {'enviados': 0, 'recebidos': 0, 'perda_pct': 100,
         'rtt_min': None, 'rtt_avg': None, 'rtt_max': None, 'rtt_mdev': None,
         'tempos': [float(t) for t in re.findall(r'time[=<]\s*([\d.]+)\s*ms', saida or '')]}
    m = re.search(r'(\d+)\s+packets? transmitted,\s*(\d+)\s+(?:packets? )?received', saida or '')
    if m:
        r['enviados'], r['recebidos'] = int(m.group(1)), int(m.group(2))
        r['perda_pct'] = round((r['enviados'] - r['recebidos']) / max(r['enviados'], 1) * 100, 1)
    m = re.search(r'min/avg/max(?:/m?dev)?[^=]*=\s*([\d.]+)/([\d.]+)/([\d.]+)(?:/([\d.]+))?', saida or '')
    if m:
        r['rtt_min'], r['rtt_avg'], r['rtt_max'] = float(m.group(1)), float(m.group(2)), float(m.group(3))
        r['rtt_mdev'] = float(m.group(4)) if m.group(4) else None
    elif r['tempos']:
        t = r['tempos']
        r['rtt_min'], r['rtt_avg'], r['rtt_max'] = min(t), round(sum(t) / len(t), 3), max(t)
    r['alcancavel'] = r['recebidos'] > 0 or bool(r['tempos'])
    return r


def parse_traceroute(saida):
    saltos = []
    for linha in (saida or '').splitlines():
        m = re.match(r'\s*(\d+)[:?]?\s+(.*)$', linha)
        if not m:
            continue
        resto = m.group(2)
        ip = re.search(r'(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F]*:[0-9a-fA-F:]+)', resto)
        tempos = [float(t) for t in re.findall(r'([\d.]+)\s*ms', resto)]
        perdidos = resto.count('*')
        saltos.append({
            'salto': int(m.group(1)),
            'ip': ip.group(1) if ip else None,
            'rtt': round(sum(tempos) / len(tempos), 2) if tempos else None,
            'perda_pct': round(perdidos / max(perdidos + len(tempos), 1) * 100),
        })
    return saltos


def parse_mtr(saida):
    saltos = []
    for linha in (saida or '').splitlines():
        m = re.match(r'\s*(\d+)\.\|--\s+(\S+)\s+([\d.]+)%?\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)', linha)
        if m:
            saltos.append({
                'salto': int(m.group(1)), 'host': m.group(2), 'perda_pct': float(m.group(3)),
                'enviados': int(m.group(4)), 'ultimo': float(m.group(5)), 'media': float(m.group(6)),
                'melhor': float(m.group(7)), 'pior': float(m.group(8)), 'desvio': float(m.group(9)),
            })
    return saltos


def parse_portas(saida):
    return [{'porta': int(p), 'estado': e}
            for p, e in re.findall(r'PORTA (\d+) (aberta|fechada|filtrada)', saida or '')]


def avaliar_qualidade(ping):
    """Latência, jitter (média da variação entre respostas seguidas) e perda → nota."""
    t = ping['tempos']
    jitter = round(statistics.mean(abs(b - a) for a, b in zip(t, t[1:])), 2) if len(t) > 1 else None
    lat = ping['rtt_avg']
    perda = ping['perda_pct']
    if not ping['alcancavel'] or lat is None:
        nota, texto = 'ruim', 'Destino não respondeu'
    elif perda >= 5 or lat > 150 or (jitter or 0) > 30:
        nota, texto = 'ruim', 'Conexão instável — perda, latência ou jitter alto'
    elif perda > 0 or lat > 80 or (jitter or 0) > 10:
        nota, texto = 'regular', 'Aceitável, mas pode afetar voz/vídeo e jogos'
    else:
        nota, texto = 'boa', 'Conexão estável, boa para voz/vídeo'
    return {'latencia_media': lat, 'latencia_min': ping['rtt_min'], 'latencia_max': ping['rtt_max'],
            'jitter': jitter, 'perda_pct': perda, 'nota': nota, 'resumo': texto}


# ── Calculadora (local, sem proxy) ───────────────────────────────────────

def calcular_ip(valor):
    texto = (valor or '').strip()
    if not texto:
        raise ErroValidacao('Informe um IP com máscara (ex.: 192.168.10.0/24).')
    try:
        if '/' not in texto and ' ' in texto:   # "192.168.0.1 255.255.255.0"
            ip, mascara = texto.split(None, 1)
            texto = f'{ip}/{mascara.strip()}'
        iface = ipaddress.ip_interface(texto if '/' in texto else texto + ('/128' if ':' in texto else '/32'))
    except ValueError:
        raise ErroValidacao('Formato inválido. Use 192.168.10.0/24, 10.0.0.1 255.255.255.0 ou 2001:db8::/48.')
    rede = iface.network
    r = {
        'versao': rede.version,
        'ip': str(iface.ip),
        'rede': str(rede.network_address),
        'prefixo': rede.prefixlen,
        'cidr': str(rede),
        'total_enderecos': str(rede.num_addresses),
        'privado': iface.ip.is_private,
    }
    if rede.version == 4:
        n = rede.num_addresses
        uteis = n - 2 if rede.prefixlen < 31 else n
        hosts_ini = rede.network_address + (1 if rede.prefixlen < 31 else 0)
        hosts_fim = rede.broadcast_address - (1 if rede.prefixlen < 31 else 0)
        r.update({
            'mascara': str(rede.netmask),
            'wildcard': str(rede.hostmask),
            'broadcast': str(rede.broadcast_address) if rede.prefixlen < 31 else None,
            'primeiro_host': str(hosts_ini),
            'ultimo_host': str(hosts_fim),
            'hosts_uteis': str(uteis),
        })
    else:
        r.update({
            'primeiro_host': str(rede.network_address),
            'ultimo_host': str(rede.broadcast_address),
            'hosts_uteis': str(rede.num_addresses),
            'redes_64': str(2 ** (64 - rede.prefixlen)) if rede.prefixlen <= 64 else None,
        })
    return r


# ── Execução ─────────────────────────────────────────────────────────────

def executar(ferramenta, params, executor):
    """
    Roda a ferramenta. `executor(cmd, timeout) -> (stdout, stderr, rc)` executa
    no proxy do cliente. Devolve dict serializável; ErroValidacao para entrada ruim.
    """
    if ferramenta == 'calculadora':
        return {'ferramenta': 'calculadora', 'resultado': calcular_ip(params.get('entrada'))}
    if ferramenta not in FERRAMENTAS_PROXY:
        raise ErroValidacao('Ferramenta desconhecida.')

    destino = validar_destino(params.get('destino'))
    ipv = 'v6' if params.get('ipv') == 'v6' else 'v4'
    base = {'ferramenta': ferramenta, 'destino': destino}

    if ferramenta == 'ping':
        pacotes = _int(params.get('pacotes'), 5, 1, 20)
        out, err, _ = executor(cmd_ping(destino, ipv, pacotes), timeout=pacotes * 3 + 15)
        saida = (out + err).strip()
        return {**base, 'resultado': parse_ping(saida), 'saida': saida[:6000]}

    if ferramenta == 'traceroute':
        out, err, _ = executor(cmd_traceroute(destino, ipv), timeout=80)
        saida = (out + err).strip()
        if _sem_ferramenta(saida):
            return {**base, 'erro': 'O proxy não tem traceroute/tracepath instalado.', 'saida': saida}
        return {**base, 'resultado': parse_traceroute(saida), 'saida': saida[:8000]}

    if ferramenta == 'mtr':
        ciclos = _int(params.get('ciclos'), 10, 3, 30)
        out, err, _ = executor(cmd_mtr(destino, ipv, ciclos), timeout=ciclos * 2 + 50)
        saida = (out + err).strip()
        if _sem_ferramenta(saida):
            return {**base, 'erro': 'O proxy não tem mtr nem traceroute instalado.', 'saida': saida}
        if 'AVISO mtr' in saida:
            # Proxy sem mtr: traceroute com 3 sondas por salto, mostrado como traceroute.
            return {**base, 'modo': 'traceroute', 'aviso': 'O proxy não tem mtr instalado; resultado do traceroute (3 sondas por salto).',
                    'resultado': parse_traceroute(saida), 'saida': saida[:8000]}
        return {**base, 'resultado': parse_mtr(saida), 'saida': saida[:8000]}

    if ferramenta == 'portas':
        portas = validar_portas(params.get('portas'))
        out, err, _ = executor(cmd_portas(destino, portas), timeout=len(portas) * 4 + 15)
        saida = (out + err).strip()
        if _sem_ferramenta(saida):
            return {**base, 'erro': 'O proxy não tem bash nem nc para testar portas.', 'saida': saida}
        return {**base, 'resultado': parse_portas(saida), 'saida': saida[:4000]}

    # qualidade
    pacotes = _int(params.get('pacotes'), 30, 10, 100)
    out, err, _ = executor(cmd_qualidade(destino, ipv, pacotes), timeout=pacotes // 5 + 20)
    saida = (out + err).strip()
    ping = parse_ping(saida)
    return {**base, 'resultado': {**avaliar_qualidade(ping), 'tempos': ping['tempos'],
                                  'enviados': ping['enviados'], 'recebidos': ping['recebidos']},
            'saida': saida[:6000]}
