"""
Grafo de conhecimento do cliente para o Agent NOC (abordagem Graphify)
======================================================================

Inspirado no Graphify (github.com/Graphify-Labs/graphify): em vez de colar a
infraestrutura inteira do cliente no system prompt a cada chamada à IA, o CRM
monta um **grafo de conhecimento** (hosts, interfaces, sessões BGP, enlaces) e o
agente consulta só o **subgrafo** relevante para a pergunta, dentro de um
orçamento de tokens.

Por que não o pacote `graphifyy`: ele é feito para repositórios de código
(tree-sitter em ~25 linguagens) e para documentos via LLM; a infraestrutura do
cliente vive no banco do CRM (Acesso.contexto_backup, topologia), não em
arquivos. Instalar o pacote traria ~30 dependências ao venv de produção sem
usar nenhuma delas. Aqui ficam só as ideias que reduzem tokens — grafo
persistente em memória, busca por palavra-chave que escolhe "sementes",
expansão em largura (BFS) com profundidade limitada, saída cortada por
orçamento e arestas marcadas EXTRACTED/INFERRED — em Python puro, sem
dependência nova. `exportar_graphify_json()` grava no formato node-link do
Graphify (graph.json), então o grafo abre nas ferramentas dele se quiserem.

Fontes do grafo (tudo já existe no CRM; nada é coletado nos equipamentos):
  - Acesso (host): tipo, IP, função, modelo, fabricante, notas
  - Acesso.contexto_backup: hostname, ASN, interfaces (IP/VLAN/descrição),
    peers BGP, protocolos (OSPF/MPLS/VRF/pools...)
  - TopologiaDiagrama.dados_json: enlaces desenhados pelo operador

Arestas:
  - host —tem_interface→ interface                          EXTRACTED
  - host —bgp_peer→ peer BGP                                EXTRACTED
  - peer BGP —e_o_host→ host dono do IP                     EXTRACTED (IP casou)
  - interface —enlace_p2p— interface (mesma sub-rede ≥/29)  INFERRED
  - host —enlace_topologia— host (mapa do operador)         EXTRACTED
  - host —funcao→ grupo de função (OLT, BORDA...)           EXTRACTED

Nada aqui pode derrubar o agente: o motor chama tudo dentro de try/except e,
em qualquer falha, volta ao prompt completo de antes.
"""
from __future__ import annotations

import ipaddress
import json
import math
import re
import threading
import unicodedata
from collections import defaultdict, deque

# Abaixo disso (chars da seção de hosts no formato antigo) o prompt completo
# continua sendo usado — clientes pequenos ficam exatamente como antes.
LIMIAR_PROMPT_COMPACTO = 12000

# Orçamento padrão da resposta de consultar_grafo (~3 chars/token, como no Graphify)
ORCAMENTO_TOKENS_PADRAO = 1500
MAX_ORCAMENTO_TOKENS = 4000
MAX_SEMENTES = 8
MAX_NOS_VISITADOS = 400

_STOPWORDS = {
    'a', 'o', 'as', 'os', 'de', 'do', 'da', 'dos', 'das', 'e', 'em', 'no', 'na',
    'nos', 'nas', 'um', 'uma', 'para', 'pra', 'por', 'com', 'qual', 'quais',
    'que', 'como', 'onde', 'esta', 'está', 'sao', 'são', 'ver', 'veja', 'me',
    'mostra', 'mostre', 'sobre', 'the', 'of', 'to', 'in', 'is', 'host',
}


# ─────────────────────────────────────────────────────────────────
# Normalização de texto (acento/caixa/hífen, como a busca por descrição)
# ─────────────────────────────────────────────────────────────────

def _sem_acento(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', s or '')
                   if not unicodedata.combining(c))


def _norm(s: str) -> str:
    """minúsculo, sem acento, separadores (-_/|,;:()"') viram espaço."""
    s = _sem_acento(s).lower()
    s = re.sub(r'[-_|,;()"\'\[\]]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _colado(s: str) -> str:
    """'NDD P2' e 'ndd-p2' → 'nddp2' (casamento sem separadores)."""
    return re.sub(r'[^a-z0-9./:]', '', _norm(s))


def _termos(pergunta: str) -> list[str]:
    out = []
    for t in _norm(pergunta).split(' '):
        t = t.strip('.:/')
        if len(t) >= 2 and t not in _STOPWORDS and t not in out:
            out.append(t)
    return out[:12]


# ─────────────────────────────────────────────────────────────────
# Parser do contexto_backup (formato gerado em clientes/ipam_views.py)
# ─────────────────────────────────────────────────────────────────

_RE_HEADER = re.compile(r'^Fabricante:\s*([^|\n]*?)(?:\s*\|\s*Hostname:\s*(.+))?\s*$', re.M)
_RE_ASN = re.compile(r'BGP \(ASN local:\s*([^)]*)\)')
_RE_PEER = re.compile(r'^\s+Peer\s+([\d.:a-fA-F]+)\s+AS(\S*)(?:\s+—\s+(.+))?\s*$', re.M)
_RE_PEER_LISTA = re.compile(r'^\s+-\s+(.+?)\s+\|\s+([\d.:a-fA-F]+)\s+\|\s*(\S*)', re.M)
_RE_IFACE_IP = re.compile(
    r'^  (\S.*?)  IP=(\S+)(?:  VLAN=(\S+))?(?:  desc="(.*)")?\s*$', re.M)
_RE_DEMAIS = re.compile(r'Demais interfaces[^:]*:\s*(.+)')
_RE_PROTO = re.compile(r'^(OSPF|MPLS/LDP|VRF|PPPoE/PPP|GPON|IP Pools|Route-Policies)\b[^\n]*', re.M)


def _split_demais(texto: str) -> list[tuple[str, str]]:
    """'A(D1), B, C(D,3) ...+4 mais' → [(A,D1),(B,''),(C,'D,3')] respeitando parênteses."""
    itens, atual, nivel = [], '', 0
    for ch in texto:
        if ch == '(':
            nivel += 1
        elif ch == ')':
            nivel = max(0, nivel - 1)
        if ch == ',' and nivel == 0:
            itens.append(atual)
            atual = ''
        else:
            atual += ch
    itens.append(atual)
    out = []
    for tok in itens:
        tok = re.sub(r'\s*\.\.\.\+\d+ mais$', '', tok.strip()).rstrip('.')
        if not tok or tok.startswith('...'):
            continue
        m = re.match(r'^(.+?)\((.*)\)$', tok)
        out.append((m.group(1).strip(), m.group(2).strip()) if m else (tok, ''))
    return out


def parse_contexto(ctx: str) -> dict:
    """Extrai do contexto_backup o que vira nó/atributo no grafo. Tolerante a lixo."""
    r = {'fabricante': '', 'hostname': '', 'asn': '', 'peers': [], 'ifaces': [], 'protocolos': []}
    if not ctx:
        return r
    m = _RE_HEADER.search(ctx)
    if m:
        r['fabricante'] = (m.group(1) or '').strip()
        r['hostname'] = (m.group(2) or '').strip()
    m = _RE_ASN.search(ctx)
    if m:
        r['asn'] = m.group(1).strip()
    vistos = set()
    for m in _RE_PEER.finditer(ctx):
        ip = m.group(1)
        if ip not in vistos:
            vistos.add(ip)
            r['peers'].append({'ip': ip, 'as': m.group(2) or '', 'desc': (m.group(3) or '').strip()})
    for m in _RE_PEER_LISTA.finditer(ctx):
        ip = m.group(2)
        if ip not in vistos and re.match(r'^[\d.]+$|^[0-9a-fA-F:]+$', ip):
            vistos.add(ip)
            r['peers'].append({'ip': ip, 'as': m.group(3) or '', 'desc': m.group(1).strip()})
    for m in _RE_IFACE_IP.finditer(ctx):
        desc = (m.group(4) or '').strip().strip('"')
        r['ifaces'].append({'nome': m.group(1).strip(), 'ip': m.group(2),
                            'vlan': m.group(3) or '', 'desc': desc})
    m = _RE_DEMAIS.search(ctx)
    if m:
        for nome, desc in _split_demais(m.group(1)):
            r['ifaces'].append({'nome': nome, 'ip': '', 'vlan': '', 'desc': desc})
    for m in _RE_PROTO.finditer(ctx):
        r['protocolos'].append(m.group(0).strip()[:120])
    return r


# ─────────────────────────────────────────────────────────────────
# Grafo
# ─────────────────────────────────────────────────────────────────

class GrafoCliente:
    """Grafo não-direcionado simples (dict de adjacência) — sem networkx."""

    def __init__(self, cliente_id, cliente_nome: str = ''):
        self.cliente_id = cliente_id
        self.cliente_nome = cliente_nome
        self.nos: dict[str, dict] = {}
        self.adj: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
        self.arestas: list[dict] = []
        self._busca: dict[str, tuple[str, str, str]] = {}

    # ── construção ──
    def add_no(self, nid: str, label: str, tipo: str, acesso_id=None, **attrs):
        if nid in self.nos:
            self.nos[nid].update({k: v for k, v in attrs.items() if v})
            return
        self.nos[nid] = {'id': nid, 'label': label, 'tipo': tipo,
                         'acesso_id': acesso_id, **{k: v for k, v in attrs.items() if v}}

    def add_aresta(self, a: str, b: str, rel: str, conf: str = 'EXTRACTED', info: str = ''):
        if a not in self.nos or b not in self.nos or a == b:
            return
        for (viz, r, _c, _i) in self.adj[a]:
            if viz == b and r == rel:
                return
        self.adj[a].append((b, rel, conf, info))
        self.adj[b].append((a, rel, conf, info))
        self.arestas.append({'source': a, 'target': b, 'relation': rel,
                             'confidence': conf, 'info': info})

    def finalizar(self):
        for nid, n in self.nos.items():
            extra = ' '.join(str(v) for k, v in n.items()
                             if k not in ('id', 'tipo', 'acesso_id') and isinstance(v, (str, int)))
            self._busca[nid] = (_norm(n['label']), _norm(extra), _colado(n['label'] + ' ' + extra))

    # ── estatísticas (o "GRAPH_REPORT" resumido que vai no prompt) ──
    def resumo(self) -> str:
        por_tipo = defaultdict(int)
        for n in self.nos.values():
            por_tipo[n['tipo']] += 1
        enlaces = sum(1 for e in self.arestas if e['relation'] in ('enlace_p2p', 'enlace_topologia'))
        hubs = sorted(
            (n for n in self.nos.values() if n['tipo'] == 'host'),
            key=lambda n: -len(self.adj.get(n['id'], [])),
        )[:8]
        partes = [f"{por_tipo.get('host', 0)} hosts", f"{por_tipo.get('interface', 0)} interfaces",
                  f"{por_tipo.get('bgp_peer', 0)} sessões BGP", f"{enlaces} enlaces"]
        txt = 'Grafo: ' + ', '.join(partes) + '.'
        if hubs:
            txt += ' Hosts mais conectados: ' + ', '.join(
                f"{h['label']} (ID {h['acesso_id']})" for h in hubs) + '.'
        return txt

    # ── consulta (sementes por palavra-chave + BFS + orçamento) ──
    def _idf(self, termos: list[str]) -> dict[str, float]:
        """Termo raro ('tangara') pesa mais que termo comum ('olt'), como no Graphify."""
        total = max(1, len(self._busca))
        out = {}
        for t in termos:
            df = sum(1 for lab, extra, _c in self._busca.values() if t in lab or t in extra)
            out[t] = 1.0 + math.log(total / (1 + df))
        return out

    def _pontuar(self, termos: list[str], pergunta: str) -> list[tuple[float, str]]:
        colado_q = _colado(pergunta)
        ips = set(re.findall(r'\d{1,3}(?:\.\d{1,3}){3}', pergunta))
        peso = self._idf(termos)
        res = []
        for nid, (lab, extra, col) in self._busca.items():
            n = self.nos[nid]
            sc = 0.0
            lab_tokens = set(lab.split(' '))
            for t in termos:
                if t in lab_tokens:
                    sc += 5 * peso[t]
                elif t in lab:
                    sc += 3 * peso[t]
                elif t in extra:
                    sc += 1 * peso[t]
            if len(colado_q) >= 4 and colado_q in col:
                sc += 4
            for ip in ips:
                if ip in (n.get('ip') or '') or ip in lab or ip == n.get('host_ip'):
                    sc += 10
            if sc:
                if n['tipo'] == 'host':
                    sc *= 1.3
                elif n['tipo'] == 'grupo':
                    sc *= 0.6
                res.append((sc, nid))
        res.sort(key=lambda x: (-x[0], x[1]))
        return res

    def consultar(self, pergunta: str, profundidade: int = 2,
                  orcamento_tokens: int = ORCAMENTO_TOKENS_PADRAO) -> str:
        profundidade = max(1, min(int(profundidade or 2), 3))
        orcamento_tokens = max(300, min(int(orcamento_tokens or ORCAMENTO_TOKENS_PADRAO), MAX_ORCAMENTO_TOKENS))
        termos = _termos(pergunta)
        ranking = self._pontuar(termos, pergunta)
        if not ranking:
            return (f"Nada no grafo casa com '{pergunta}'. Tente outro termo (nome do site, descrição "
                    f"da interface, IP, nome do peer BGP) ou use list_hosts.")
        topo = ranking[0][0]
        sementes = [nid for sc, nid in ranking if sc >= topo * 0.5][:MAX_SEMENTES]
        scores = dict((nid, sc) for sc, nid in ranking)

        # BFS; nós "grupo" (hubs de função) só expandem se forem semente
        dist = {s: 0 for s in sementes}
        fila = deque(sementes)
        while fila and len(dist) < MAX_NOS_VISITADOS:
            atual = fila.popleft()
            if dist[atual] >= profundidade:
                continue
            if self.nos[atual]['tipo'] == 'grupo' and atual not in sementes:
                continue
            for viz, _r, _c, _i in self.adj.get(atual, []):
                if viz not in dist:
                    dist[viz] = dist[atual] + 1
                    fila.append(viz)

        ordem = sorted(dist, key=lambda n: (dist[n], -scores.get(n, 0), n))
        linhas = [f"Subgrafo para '{pergunta}' (sementes: {len(sementes)}, profundidade {profundidade}):"]
        incluidos = set()
        limite = orcamento_tokens * 3
        tamanho = len(linhas[0])
        cortados = 0
        for nid in ordem:
            n = self.nos[nid]
            if (n['tipo'] == 'interface' and nid not in sementes
                    and not n.get('desc') and not n.get('ip')):
                continue  # porta sem IP nem descrição só gasta orçamento
            if n['tipo'] == 'grupo' and nid not in sementes:
                continue  # lista de função só interessa se a pergunta for sobre ela
            l = self._linha_no(nid)
            if tamanho + len(l) > limite:
                cortados += 1
                continue
            linhas.append(l)
            incluidos.add(nid)
            tamanho += len(l) + 1
        arestas_txt = []
        vistas = set()
        for nid in ordem:
            if nid not in incluidos:
                continue
            for viz, rel, conf, info in self.adj.get(nid, []):
                if viz not in incluidos or rel in ('tem_interface', 'funcao'):
                    continue  # tem_interface/funcao já aparecem no próprio nó
                chave = tuple(sorted((nid, viz))) + (rel,)
                if chave in vistas:
                    continue
                vistas.add(chave)
                e = (f"  {self._nome_curto(nid)} —{rel}— {self._nome_curto(viz)}"
                     f"{' (' + info + ')' if info else ''} [{conf}]")
                if tamanho + len(e) > limite:
                    cortados += 1
                    break
                arestas_txt.append(e)
                tamanho += len(e) + 1
        if arestas_txt:
            linhas.append('Ligações:')
            linhas.extend(arestas_txt)
        if cortados:
            linhas.append(f"(… {cortados} itens fora do orçamento — refine a pergunta ou use detalhe_host)")
        return '\n'.join(linhas)

    def _host_do_no(self, nid: str) -> str:
        n = self.nos[nid]
        if n['tipo'] == 'host' or not n.get('acesso_id'):
            return ''
        h = self.nos.get(f"host:{n['acesso_id']}")
        return h['label'] if h else ''

    def _nome_curto(self, nid: str) -> str:
        n = self.nos[nid]
        if n['tipo'] == 'host':
            return f"{n['label']} (ID {n['acesso_id']})"
        dono = self._host_do_no(nid)
        return f"{dono}:{n['label']}" if dono else n['label']

    def _linha_no(self, nid: str) -> str:
        n = self.nos[nid]
        t = n['tipo']
        if t == 'host':
            p = [f"HOST ID {n['acesso_id']}: {n['label']}", n.get('endereco', '')]
            for k in ('funcao', 'fabricante', 'modelo', 'hostname'):
                if n.get(k):
                    p.append(f"{k}={n[k]}")
            if n.get('asn'):
                p.append(f"AS{n['asn']}")
            if n.get('protocolos'):
                pr = n['protocolos']
                p.append('protocolos: ' + (pr[:160] + '…' if len(pr) > 160 else pr))
            if n.get('notas'):
                p.append('notas: ' + n['notas'])
            return ' | '.join(x for x in p if x)
        if t == 'interface':
            p = [f"  IFACE {n['label']} @ {self._host_do_no(nid)} (ID {n['acesso_id']})"]
            if n.get('desc'):
                p.append(f'desc="{n["desc"]}"')
            if n.get('ip'):
                p.append(f"IP={n['ip']}")
            if n.get('vlan'):
                p.append(f"VLAN={n['vlan']}")
            return ' '.join(p)
        if t == 'bgp_peer':
            return (f"  BGP {n.get('desc') or '(sem descrição)'} = {n['ip']} AS{n.get('as') or '?'}"
                    f" @ {self._host_do_no(nid)} (ID {n['acesso_id']})")
        if t == 'grupo':
            membros = [self.nos[v] for v, r, _c, _i in self.adj.get(nid, []) if r == 'funcao']
            ids = ', '.join(f"{m['label']}={m['acesso_id']}" for m in membros[:30])
            return f"FUNÇÃO {n['label']}: {ids}{' …' if len(membros) > 30 else ''}"
        return f"{t.upper()} {n['label']}"

    # ── detalhe de um host (o que antes ia inteiro no prompt) ──
    def detalhe_host(self, acesso_id: int, contexto_backup: str, max_chars: int = 6000) -> str:
        hid = f'host:{acesso_id}'
        if hid not in self.nos:
            return f"Host ID {acesso_id} não está no grafo deste cliente."
        out = [self._linha_no(hid)]
        vizinhos = []
        for viz, rel, conf, info in self.adj.get(hid, []):
            if rel in ('enlace_topologia',) or (rel == 'e_o_host'):
                vizinhos.append(f"  —{rel}— {self._nome_curto(viz)}{' (' + info + ')' if info else ''}")
        # enlaces p2p ficam nas interfaces: sobe um nível
        for viz, rel, _c, _i in self.adj.get(hid, []):
            if rel != 'tem_interface':
                continue
            for v2, r2, c2, i2 in self.adj.get(viz, []):
                if r2 == 'enlace_p2p':
                    vizinhos.append(f"  {self.nos[viz]['label']} —enlace_p2p— {self._nome_curto(v2)}"
                                    f"{' (' + i2 + ')' if i2 else ''} [{c2}]")
        if vizinhos:
            out.append('Vizinhos:')
            out.extend(vizinhos[:40])
        if contexto_backup:
            ctx = contexto_backup
            if len(ctx) > max_chars:
                ctx = ctx[:max_chars] + f'\n… [cortado — {len(contexto_backup) - max_chars} chars restantes]'
            out.append('[Configuração conhecida do backup]')
            out.append(ctx)
        else:
            out.append('(sem contexto de backup — use fetch_host_config para coletar)')
        return '\n'.join(out)

    # ── exportação no formato do Graphify (graph.json node-link) ──
    def exportar_graphify_json(self) -> dict:
        return {
            'directed': False, 'multigraph': False,
            'graph': {'schema_version': 1, 'graphify_version': 'crm-agent-noc',
                      'cliente': self.cliente_nome},
            'nodes': [{'id': n['id'], 'label': n['label'], 'file_type': n['tipo'],
                       'source_file': f"acesso/{n['acesso_id']}" if n.get('acesso_id') else 'crm',
                       **{k: v for k, v in n.items() if k not in ('id', 'label')}}
                      for n in self.nos.values()],
            'links': [{'source': e['source'], 'target': e['target'], 'relation': e['relation'],
                       'confidence': e['confidence'],
                       'confidence_score': 1.0 if e['confidence'] == 'EXTRACTED' else 0.7,
                       'info': e['info']} for e in self.arestas],
        }


# ─────────────────────────────────────────────────────────────────
# Montagem a partir do banco (síncrono — chame via sync_to_async)
# ─────────────────────────────────────────────────────────────────

def _rede_p2p(ip_cidr: str):
    try:
        itf = ipaddress.ip_interface(ip_cidr)
    except ValueError:
        return None
    lim = 29 if itf.version == 4 else 126
    if itf.network.prefixlen < lim:
        return None
    return str(itf.network)


def construir_grafo(cliente, acessos=None) -> GrafoCliente:
    """Monta o grafo do cliente. `acessos` pode vir já carregado (com modelo/funcao)."""
    from clientes.models import Acesso, TopologiaDiagrama

    if acessos is None:
        acessos = list(Acesso.objects.filter(cliente=cliente).select_related('modelo', 'funcao').only(
            'id', 'tipo', 'host', 'porta', 'protocolo', 'contexto_backup', 'notas',
            'modelo__nome', 'modelo__fabricante', 'funcao__descricao'))
    g = GrafoCliente(cliente.id, cliente.nome_empresa)
    ip_dono: dict[str, str] = {}           # IP sem máscara → nó (interface ou host)
    redes: dict[str, list[str]] = defaultdict(list)
    peers_pend: list[tuple[str, str]] = []

    for a in acessos:
        info = parse_contexto(a.contexto_backup or '')
        fab = (getattr(a.modelo, 'fabricante', '') if a.modelo else '') or info['fabricante']
        funcao = a.funcao.descricao if a.funcao else ''
        hid = f'host:{a.id}'
        g.add_no(hid, a.tipo or f'host {a.id}', 'host', a.id,
                 endereco=f'{a.host}:{a.porta} {a.protocolo}', host_ip=a.host,
                 funcao=funcao, fabricante=fab,
                 modelo=(a.modelo.nome if a.modelo else ''), hostname=info['hostname'],
                 asn=info['asn'], protocolos='; '.join(info['protocolos'])[:300],
                 notas=(a.notas or '').strip()[:300])
        if a.host:
            ip_dono.setdefault(a.host, hid)
        if funcao:
            gid = f'grupo:{_norm(funcao)}'
            g.add_no(gid, funcao, 'grupo')
            g.add_aresta(hid, gid, 'funcao')
        for itf in info['ifaces']:
            iid = f"if:{a.id}:{itf['nome']}"
            g.add_no(iid, itf['nome'], 'interface', a.id, desc=itf['desc'], ip=itf['ip'], vlan=itf['vlan'])
            g.add_aresta(hid, iid, 'tem_interface')
            if itf['ip']:
                ip_dono.setdefault(itf['ip'].split('/')[0], iid)
                rede = _rede_p2p(itf['ip'])
                if rede:
                    redes[rede].append(iid)
        for p in info['peers']:
            pid = f"peer:{a.id}:{p['ip']}"
            g.add_no(pid, f"BGP {p['desc'] or p['ip']}", 'bgp_peer', a.id,
                     ip=p['ip'], desc=p['desc'], **{'as': p['as']})
            g.add_aresta(hid, pid, 'bgp_peer')
            peers_pend.append((pid, p['ip']))

    # Enlaces inferidos: duas interfaces na mesma /30, /31 ou /29
    for rede, membros in redes.items():
        hosts_distintos = {g.nos[m]['acesso_id'] for m in membros}
        if 2 <= len(membros) <= 6 and len(hosts_distintos) >= 2:
            for i, m1 in enumerate(membros):
                for m2 in membros[i + 1:]:
                    if g.nos[m1]['acesso_id'] != g.nos[m2]['acesso_id']:
                        g.add_aresta(m1, m2, 'enlace_p2p', 'INFERRED', rede)

    # Peer BGP → host dono do IP
    for pid, ip in peers_pend:
        dono = ip_dono.get(ip)
        if dono:
            hdono = f"host:{g.nos[dono]['acesso_id']}"
            if hdono != f"host:{g.nos[pid]['acesso_id']}":
                g.add_aresta(pid, hdono, 'e_o_host', 'EXTRACTED', ip)

    # Enlaces desenhados na topologia
    try:
        for d in TopologiaDiagrama.objects.filter(cliente=cliente).only('nome', 'dados_json'):
            try:
                dados = json.loads(d.dados_json or '{}')
            except ValueError:
                continue
            node_acesso = {}
            for n in dados.get('nodes') or []:
                if not isinstance(n, dict):
                    continue
                aid = n.get('acesso_id')
                if not aid and str(n.get('id', '')).startswith('crm_'):
                    aid = str(n['id'])[4:]
                if aid:
                    node_acesso[str(n.get('id'))] = str(aid)
            for l in dados.get('links') or []:
                if not isinstance(l, dict):
                    continue
                a1 = node_acesso.get(str(l.get('src') or l.get('source') or ''))
                a2 = node_acesso.get(str(l.get('tgt') or l.get('target') or ''))
                if not a1 or not a2:
                    continue
                partes = [p for p in (
                    f"{l.get('iface_a') or '?'} ↔ {l.get('iface_b') or '?'}"
                    if (l.get('iface_a') or l.get('iface_b')) else '',
                    f"IP {l.get('ip_local')}" if l.get('ip_local') else '',
                    f"VLAN {l.get('vlan')}" if l.get('vlan') else '',
                    f"mapa {d.nome}",
                ) if p]
                g.add_aresta(f'host:{a1}', f'host:{a2}', 'enlace_topologia', 'EXTRACTED', ', '.join(partes))
    except Exception:
        pass  # topologia é complemento; sem ela o grafo segue útil

    g.finalizar()
    return g


# ─────────────────────────────────────────────────────────────────
# Cache em memória por cliente (invalida quando os dados mudam)
# ─────────────────────────────────────────────────────────────────

_cache: dict = {}
_lock = threading.Lock()


def _assinatura(cliente_id, acessos) -> tuple:
    from clientes.models import TopologiaDiagrama
    from django.db.models import Max, Count
    topo = TopologiaDiagrama.objects.filter(cliente_id=cliente_id).aggregate(m=Max('atualizado_em'), c=Count('id'))
    return (
        len(acessos),
        hash(tuple((a.id, a.tipo, a.host, len(a.contexto_backup or ''), len(a.notas or ''),
                    a.funcao.descricao if a.funcao else '') for a in acessos)),
        topo['c'], str(topo['m']),
    )


def grafo_do_cliente(cliente, acessos=None) -> GrafoCliente:
    """Grafo do cliente com cache por assinatura (nº de hosts, backups, topologia)."""
    from clientes.models import Acesso
    if acessos is None:
        acessos = list(Acesso.objects.filter(cliente=cliente).select_related('modelo', 'funcao').only(
            'id', 'tipo', 'host', 'porta', 'protocolo', 'contexto_backup', 'notas',
            'modelo__nome', 'modelo__fabricante', 'funcao__descricao'))
    ass = _assinatura(cliente.id, acessos)
    with _lock:
        hit = _cache.get(cliente.id)
        if hit and hit[0] == ass:
            return hit[1]
    g = construir_grafo(cliente, acessos)
    with _lock:
        _cache[cliente.id] = (ass, g)
    return g


def linha_host_compacta(a) -> str:
    """Uma linha por host para o prompt compacto (sem o backup inteiro)."""
    fab = (getattr(a.modelo, 'fabricante', '') if a.modelo else '') or ''
    parts = [f'ID {a.id}', a.tipo, f'{a.host}:{a.porta}', a.protocolo]
    if a.funcao:
        parts.append(f'função={a.funcao.descricao}')
    if a.modelo and a.modelo.nome:
        parts.append(f'modelo={a.modelo.nome}')
    if fab:
        parts.append(f'fabricante={fab}')
    parts.append('backup✓' if a.contexto_backup else 'sem backup')
    linha = '  - ' + ' | '.join(str(p) for p in parts if p)
    notas = (getattr(a, 'notas', '') or '').strip()
    if notas:
        # Notas são instruções do operador: vão inteiras, como no prompt completo
        nota_indent = '\n'.join('      ' + l for l in notas.splitlines())
        linha += f'\n    [Notas do operador]\n{nota_indent}'
    return linha
