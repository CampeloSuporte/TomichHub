"""
Motor do Change Plan TO-BE.

Entradas:
  - modelo AS-IS (`analise.montar_modelo`, recalculado dos backups);
  - convenção (HLD estruturado, `convencao.py`);
  - cenário TO-BE (opcional): mapa da topologia clonado e editado, com
    `node.tobe = {papeis: [...], estado, loopback, bng_destino}` e
    `link.tobe = {estado, papel, mtu_alvo, lote, custo}`;
  - mapeamentos manuais (VRF legada → serviço, contratante de L2VPN,
    papéis e BNG de destino quando não há cenário, communities).

Saída: `plano` com as waves 0–9, cada uma com itens concretos
(objeto, equipamentos, atual → alvo, ação, origem da regra), lotes e as
decisões pendentes. Nada aqui toca banco ou disco.
"""
import ipaddress
import re
from collections import Counter, OrderedDict, defaultdict

from . import convencao as cv
from .extratores import eh_asn_privado

PAPEIS_NO = ['', 'RR01', 'RR02', 'PE', 'P', 'BNG01', 'BNG02', 'BNG03', 'BRAS-POP', 'CGNAT-EXTERNO',
             'BORDA', 'CE', 'SERVIDOR', 'OUTRO']
ESTADOS = ['manter', 'novo', 'remover']
PAPEIS_ENLACE = ['principal', 'alternativo', 'ultimo_recurso']
PAPEIS_BACKBONE = {'RR01', 'RR02', 'PE', 'P', 'BNG01', 'BNG02', 'BNG03', 'BORDA'}
ELIMINAR = 'ELIMINAR'
L3VPN = 'L3VPN'

WAVES = [
    {'numero': 0, 'nome': 'Baseline, inventário e preparação', 'natureza': 'Preparação',
     'gate': 'Inventário aprovado, LLD e MOP da Wave 1',
     'objetivo': 'Construir a linha de base técnica e operacional necessária para alterar a rede com '
                 'rastreabilidade e rollback seguro.'},
    {'numero': 1, 'nome': 'Correção e normalização de MTU', 'natureza': 'Crítica',
     'gate': 'MTU validada ponta a ponta em todo o backbone',
     'objetivo': 'Normalizar a capacidade de transporte de quadros em todas as camadas do backbone, eliminando '
                 'incompatibilidades entre meios físicos, interfaces IP, MPLS e serviços encapsulados.'},
    {'numero': 2, 'nome': 'Underlay OSPF/MPLS/LDP', 'natureza': 'Fundação',
     'gate': 'Underlay padronizado e estável',
     'objetivo': 'Padronizar o IGP, a distribuição de labels, o BFD e o endereçamento de infraestrutura '
                 'conforme o HLD. Esta wave inicia somente após o gate de MTU.'},
    {'numero': 3, 'nome': 'Route Reflectors e MP-BGP', 'natureza': 'Controle',
     'gate': 'Dual-homing de todos os PEs/BNGs aos dois RRs',
     'objetivo': 'Consolidar o plano de controle em dois route reflectors, com cada PE e BNG conectado aos dois.'},
    {'numero': 4, 'nome': 'VRFs, RD, RT e communities', 'natureza': 'Serviços',
     'gate': 'Domínios lógicos e políticas padronizados',
     'objetivo': 'Levar VRFs, RDs, RTs, communities e nomenclatura de políticas ao catálogo do HLD.'},
    {'numero': 5, 'nome': 'L2VPN, L3VPN e serviços críticos', 'natureza': 'Serviços',
     'gate': 'Transportes migrados por prioridade',
     'objetivo': 'Recriar os transportes no padrão TR-<CONTRATANTE>, um contrato por vez, preservando MTU, '
                 'QoS e caminho de retorno.'},
    {'numero': 6, 'nome': 'BNG centralizado', 'natureza': 'Assinantes',
     'gate': 'Acesso migrado POP a POP',
     'objetivo': 'Terminar PPPoE e IPoE, B2C ou B2B, nos BNGs centrais, diretamente na VRF-ACCESS.'},
    {'numero': 7, 'nome': 'CGNAT integrado aos BNGs', 'natureza': 'Assinantes',
     'gate': 'CGNAT nas cards e retirada dos appliances externos',
     'objetivo': 'Executar o CGNAT nos BNGs centrais e retirar os elementos externos da arquitetura permanente.'},
    {'numero': 8, 'nome': 'Uplinks, Content, IX e downstreams', 'natureza': 'Borda',
     'gate': 'Políticas de borda aderentes ao HLD',
     'objetivo': 'Aplicar identidades lógicas, classes de LP, communities e filtros de segurança em toda a borda.'},
    {'numero': 9, 'nome': 'Desativação do legado e estabilização', 'natureza': 'Encerramento',
     'gate': 'Objetos legados removidos e documentação finalizada',
     'objetivo': 'Remover o que foi substituído, somente com evidência técnica e aceite.'},
]


# ═══════════════════════════════════════════════════════════════════════════
# Utilitários
# ═══════════════════════════════════════════════════════════════════════════

def _ip(txt):
    try:
        return ipaddress.ip_address(str(txt or '').split('/')[0].strip())
    except ValueError:
        return None


def _rede(txt):
    try:
        return ipaddress.ip_network(str(txt).strip(), strict=False)
    except ValueError:
        return None


def _token(txt):
    s = re.sub(r'[^A-Za-z0-9]+', '-', str(txt or '').upper()).strip('-')
    return s or 'A-DEFINIR'


class _Contador:
    def __init__(self, prefixo):
        self.prefixo = prefixo
        self.n = 0

    def __call__(self):
        self.n += 1
        return f'{self.prefixo}-{self.n:03d}'


def _item(cont, objeto, acao, atual='', alvo='', equipamentos=(), origem='', lote='', prioridade=''):
    return {
        'id': cont(), 'objeto': objeto, 'acao': acao, 'atual': atual, 'alvo': alvo,
        'equipamentos': [e for e in equipamentos if e], 'origem': origem,
        'lote': lote, 'prioridade': prioridade, 'status': 'pendente',
    }


# ═══════════════════════════════════════════════════════════════════════════
# Sugestões para os mapeamentos manuais
# ═══════════════════════════════════════════════════════════════════════════

_SUG_VRF = [
    (r'BLOQ|BLOCK|PORTAL|AVISO|ATRASO|SUSPEN', ELIMINAR),
    (r'^INTERNET$|^INET$|^GLOBAL$', 'VRF-INTERNET'),
    (r'B2C|ACCESS|ACESSO|PPPOE|ASSINANTE|SUBSCRIBER', 'VRF-ACCESS'),
    (r'B2B|BUSINESS|CORP|EMPRESA', 'VRF-BUSINESS'),
    (r'TRANSIT|TRANSITO', 'VRF-TRANSIT'),
    (r'\bIX\b|PTT', 'VRF-IX'),
    (r'CDN|CONTENT|CACHE', 'VRF-CONTENT'),
    (r'CGN', 'VRF-CGNAT'),
    (r'GERENCIA|MGMT|MANAGEMENT|INFRA|OAM|CAMERA|CFTV|OLT', 'VRF-INFRA'),
    (r'DNS|RADIUS|SERVIC', 'VRF-SERVICES'),
]


def sugerir_vrf(nome, conv):
    nomes_hld = {s.get('nome') for s in conv.get('servicos', [])}
    if nome in nomes_hld:
        return nome
    for padrao, alvo in _SUG_VRF:
        if re.search(padrao, nome or '', re.I) and (alvo == ELIMINAR or alvo in nomes_hld):
            return alvo
    return L3VPN


_LIMPA_L2 = [
    r'^TR[-_.]?VLAN\d+[-_.]?', r'^TRV?[-_.]?VLAN\d+[-_.]?', r'^TRANSPORT[E]?[-_.]?VLAN\d+[-_.]?',
    r'^L2L[-_.]?', r'^TR[-_.]', r'[:._-]\d+$', r'\bCLIENTE[-_]?', r'\bTRANSPORTE[-_]?', r'^VLANIF\d+$',
    r'^VLAN\d+$', r'[-_.]?(PPPOE|ACESSO)$', r'^\d+$',
    r'[-_.]?(X?GE|GI|100GE|25GE|10GE|ETH-TRUNK|ETH)\d+(/\d+)*[-_.]?', r'^P2P[-_.]?',
]


def _base_contratante(servico):
    textos = list(servico.get('nomes', [])) + list(servico.get('descricoes', []))
    for t in textos:
        s = str(t).upper()
        for padrao in _LIMPA_L2:
            s = re.sub(padrao, '', s)
        s = s.strip('-_. ')
        if s and re.search(r'[A-Z]{3}', s) and s not in ('ACESSO', 'PPPOE'):
            return '-'.join(_token(s).split('-')[:3])
    return ''


def sugerir_contratantes(servicos):
    """Contratante sugerido por serviço L2: a palavra inicial que se repete
    em vários serviços (ex.: INVINET) ganha de nomes compostos."""
    bases = {s['chave']: _base_contratante(s) for s in servicos}
    primeiras = Counter(b.split('-')[0] for b in bases.values() if b)
    saida = {}
    for chave, base in bases.items():
        if not base:
            saida[chave] = 'A-DEFINIR'
            continue
        primeira = base.split('-')[0]
        saida[chave] = primeira if primeiras[primeira] >= 2 else base
    return saida


def chave_servico_l2(s):
    return f'{s.get("tipo", "")}|{",".join(s.get("sinalizacao", []))}|{s.get("id", "")}'


def sugerir_community(linha):
    fin = (linha.get('finalidade') or '').upper()
    if 'BLACKHOLE' in fin:
        return '666'
    if 'INTERNA' in fin:
        return '21000'
    if 'CLIENTE' in fin:
        return '21001'
    if 'PREFERENCIAL' in fin:
        return '22000-22090'
    if 'CDN' in fin and 'PREPEND' in fin:
        return '30300-30399'
    if 'CDN' in fin:
        return '20100-20190'
    if 'PREPEND' in fin:
        return '30200-30299'
    if 'NÃO ANUNCIAR' in fin or 'NO-' in fin:
        return '30100-30199'
    return ''


def _ordem_ip(txt):
    ip = _ip(txt)
    return (0, int(ip)) if ip else (1, 0)


def rrs_e_bngs_sugeridos(modelo):
    """Nomes sugeridos para RR01/RR02 e BNG01.. — RR: quem mais reflete
    clientes (NE antes de switch); BNG: centrais que também são RR primeiro."""
    clientes = {s['rr']: s['clientes'] for s in modelo.get('sessoes_rr', [])}
    lsr = {e['nome_exibicao']: ((e.get('extr') or {}).get('lsr_id')) for e in modelo.get('equipamentos', [])}
    rrs = sorted(modelo.get('rrs', []), key=lambda r: ('NE' not in (r.get('modelo') or '').upper(),
                                                      -clientes.get(r['nome'], 0), _ordem_ip(r.get('lsr_id'))))
    nomes_rr = [r['nome'] for r in rrs]
    top = nomes_rr[:2]
    bngs = sorted(modelo.get('bngs_centrais', []),
                  key=lambda b: (b['nome'] not in top, top.index(b['nome']) if b['nome'] in top else 0,
                                 _ordem_ip(lsr.get(b['nome']))))
    return [n for n in nomes_rr if n in lsr], [b['nome'] for b in bngs if b['nome'] in lsr]


def mapeamentos_sugeridos(modelo, conv, cenario=None):
    vrfs = OrderedDict((v['nome'], sugerir_vrf(v['nome'], conv)) for v in modelo.get('vrfs', []))
    l2 = [dict(s, chave=chave_servico_l2(s)) for s in modelo.get('l2vpn', []) if not s.get('pppoe')]
    contratantes = sugerir_contratantes(l2)
    papeis = defaultdict(list)
    por_nome = {e['nome_exibicao']: e for e in modelo['equipamentos']}
    rrs, bngs = rrs_e_bngs_sugeridos(modelo)
    for i, nome in enumerate(rrs[:2]):
        papeis[str(por_nome[nome]['acesso_id'])].append(f'RR0{i + 1}')
    for i, nome in enumerate(bngs[:2]):
        papeis[str(por_nome[nome]['acesso_id'])].append(f'BNG0{i + 1}')
    papeis = OrderedDict((k, ', '.join(v)) for k, v in papeis.items())
    communities = OrderedDict((l['valor'], sugerir_community(l)) for l in modelo.get('communities', {}).get('linhas', []))
    return {
        'vrfs': vrfs,
        'contratantes': contratantes,
        'papeis': papeis if not cenario else {},
        'bng_destino': {},
        'communities': communities,
        'criticos': 'INVINET, SICREDI',
    }


def mesclar_mapeamentos(sugeridos, salvos):
    saida = {}
    for k, v in sugeridos.items():
        atual = (salvos or {}).get(k)
        if isinstance(v, dict):
            m = OrderedDict(v)
            # o que foi salvo vence a sugestão, inclusive vazio (ex.: tirar um papel sugerido)
            for kk, vv in (atual or {}).items():
                if vv is not None:
                    m[kk] = vv
            saida[k] = m
        else:
            saida[k] = atual if atual not in (None, '') else v
    return saida


# ═══════════════════════════════════════════════════════════════════════════
# Contexto: equipamentos, papéis e enlaces
# ═══════════════════════════════════════════════════════════════════════════

class _Contexto:
    def __init__(self, modelo, conv, cenario, mapeamentos):
        self.m = modelo
        self.conv = conv
        self.cenario = cenario
        self.map = mapeamentos
        self.por_acesso = {e['acesso_id']: e for e in modelo['equipamentos']}
        self.nos = {}
        self.papel = {}
        self.estado = {}
        self.loopback_alvo = {}
        self.bng_destino = {}
        if cenario:
            for n in cenario.get('nodes', []):
                if n.get('type') in ('area', 'text_box') or n.get('grupo'):
                    continue
                self.nos[n['id']] = n
                t = n.get('tobe') or {}
                chave = n.get('acesso_id') or f'no:{n["id"]}'
                papeis = set(_lista_papeis(t.get('papeis') or t.get('papel')))
                if papeis:
                    self.papel[chave] = papeis
                self.estado[chave] = t.get('estado') or 'manter'
                if t.get('loopback'):
                    self.loopback_alvo[chave] = t['loopback']
                if t.get('bng_destino'):
                    self.bng_destino[chave] = t['bng_destino']
        for aid, papel in (mapeamentos.get('papeis') or {}).items():
            try:
                chave = int(aid)
            except (TypeError, ValueError):
                continue
            papeis = set(_lista_papeis(papel))
            if papeis and chave not in self.papel:
                self.papel[chave] = papeis
        # sem papel explícito: PE/P pelo AS-IS (marcado como inferido)
        self.inferidos = set()
        for e in modelo['equipamentos']:
            if e['acesso_id'] not in self.papel:
                self.inferidos.add(e['acesso_id'])
                if 'PE' in e.get('papeis', []):
                    self.papel[e['acesso_id']] = {'PE'}
                elif 'P' in e.get('papeis', []):
                    self.papel[e['acesso_id']] = {'P'}
                elif 'BNG' in e.get('papeis', []) and e.get('vendor') != 'huawei':
                    self.papel[e['acesso_id']] = {'BRAS-POP'}
                elif 'CGNAT' in e.get('papeis', []) and 'BNG' not in e.get('papeis', []):
                    self.papel[e['acesso_id']] = {'CGNAT-EXTERNO'}
            elif self.papel[e['acesso_id']] & {'RR01', 'RR02', 'BNG01', 'BNG02', 'BNG03'} \
                    and 'PE' in e.get('papeis', []):
                self.papel[e['acesso_id']].add('PE')
        self.enlaces = self._enlaces()

    def tem(self, chave, *papeis):
        return bool(self.papel.get(chave, set()) & set(papeis))

    def papeis_txt(self, chave):
        txt = ' + '.join(sorted(self.papel.get(chave, set()))) or 'papel a definir'
        return f'{txt} (inferido do AS-IS)' if chave in self.inferidos and self.papel.get(chave) else txt

    def explicito(self, chave, papel):
        return papel in self.papel.get(chave, set()) and chave not in self.inferidos

    def eq_do_papel(self, papel):
        for chave, p in self.papel.items():
            if papel in p and isinstance(chave, int) and chave in self.por_acesso:
                return self.por_acesso[chave]
        return None

    def rotulo_do_papel(self, papel):
        eq = self.eq_do_papel(papel)
        if eq:
            return f'{eq["nome_exibicao"]} ({eq["pop"]})'
        for chave, p in self.papel.items():
            if papel in p and isinstance(chave, str) and chave.startswith('no:'):
                no = self.nos.get(chave[3:])
                if no:
                    return f'{no.get("label") or "novo equipamento"} (novo)'
        return ''

    def nome(self, chave):
        if isinstance(chave, int) and chave in self.por_acesso:
            return self.por_acesso[chave]['nome_exibicao']
        if isinstance(chave, str) and chave.startswith('no:'):
            return (self.nos.get(chave[3:]) or {}).get('label') or 'novo equipamento'
        return str(chave)

    def _enlaces(self):
        saida = []
        if self.cenario:
            for lk in self.cenario.get('links', []):
                a, b = self.nos.get(lk.get('src')), self.nos.get(lk.get('tgt'))
                if not a or not b:
                    continue
                t = lk.get('tobe') or {}
                saida.append({
                    'id': lk.get('id'),
                    'a': a.get('acesso_id') or f'no:{a["id"]}', 'b': b.get('acesso_id') or f'no:{b["id"]}',
                    'rotulo_a': a.get('label', ''), 'rotulo_b': b.get('label', ''),
                    'iface_a': lk.get('iface_a', ''), 'iface_b': lk.get('iface_b', ''),
                    'ip_a': lk.get('ip_local', ''), 'ip_b': lk.get('ip_remote', ''),
                    'vlan': lk.get('vlan', ''), 'capacidade': lk.get('iface', ''),
                    'estado': t.get('estado') or 'manter', 'papel': t.get('papel') or 'principal',
                    'mtu_alvo': str(t.get('mtu_alvo') or ''), 'lote': str(t.get('lote') or ''),
                    'custo': str(t.get('custo') or ''),
                })
        else:
            for lk in self.m['topologia'].get('enlaces', []):
                if not lk.get('a_acesso') or not lk.get('b_acesso'):
                    continue
                saida.append({
                    'id': '', 'a': lk['a_acesso'], 'b': lk['b_acesso'],
                    'rotulo_a': lk['a'], 'rotulo_b': lk['b'],
                    'iface_a': lk['iface_a'], 'iface_b': lk['iface_b'],
                    'ip_a': lk['ip_a'], 'ip_b': lk['ip_b'], 'vlan': lk['vlan'], 'capacidade': lk['capacidade'],
                    'estado': 'manter', 'papel': 'principal', 'mtu_alvo': '', 'lote': '', 'custo': '',
                })
        return [e for e in saida if self.eh_backbone(e['a']) and self.eh_backbone(e['b'])]

    def eh_backbone(self, chave):
        if self.papel.get(chave, set()) & PAPEIS_BACKBONE:
            return True
        eq = self.por_acesso.get(chave) if isinstance(chave, int) else None
        x = (eq or {}).get('extr') or {}
        return bool(eq and eq.get('vendor') == 'huawei' and (x.get('mpls') or x.get('ospf')))


def _interface(eq, iface, ip, vlan):
    x = (eq or {}).get('extr') or {}
    itfs = x.get('interfaces') or []
    if not itfs:
        return None, None
    por_nome = {i['nome']: i for i in itfs}
    alvo = None
    ipa = _ip(ip)
    if ipa:
        alvo = next((i for i in itfs if any(_ip(p) == ipa for p in i['ips'])), None)
    if not alvo and iface:
        for nome in (f'{iface}.{vlan}' if vlan else '', iface, f'Vlanif{vlan}' if vlan else ''):
            if nome and nome in por_nome:
                alvo = por_nome[nome]
                break
    if not alvo:
        return None, None
    fisica = por_nome.get(iface) if iface in por_nome else (
        por_nome.get(alvo['nome'].split('.')[0]) if '.' in alvo['nome'] else None)
    return alvo, fisica


def _mtu_l3(itf, fisica):
    """Subinterface herda a MTU da porta; Vlanif e interface roteada não."""
    if not itf:
        return None
    herda = fisica if (fisica and '.' in itf['nome'] and itf['nome'].startswith(fisica['nome'])) else None
    return _mtu(itf, herda)


def _desc_mtu(itf, fisica, iface):
    if not itf:
        return f'? ({iface or "interface não identificada"})'
    txt = f'{_mtu_l3(itf, fisica)} ({itf["nome"]})'
    if fisica and fisica is not itf:
        porta = fisica.get('jumbo') or fisica.get('mtu')
        txt += f', porta {fisica["nome"]} ' + (f'jumbo {porta}' if fisica.get('jumbo') else f'MTU {porta or 1500}')
    return txt


def _mtu(itf, pai=None):
    if not itf:
        return None
    return itf.get('mtu') or (pai or {}).get('mtu') or 1500


# ═══════════════════════════════════════════════════════════════════════════
# Waves
# ═══════════════════════════════════════════════════════════════════════════

def _wave0(ctx, pend):
    c = _Contador('W0')
    itens = []
    for e in ctx.m['sem_backup']:
        itens.append(_item(c, e['nome'], 'Regularizar o backup de configuração antes do LLD',
                           atual='Sem backup analisado', alvo='Backup diário com checksum',
                           equipamentos=[e['nome']], origem='AS-IS'))
    for e in ctx.m['backups_antigos']:
        itens.append(_item(c, e['nome'], 'Atualizar o backup (coleta com mais de 7 dias)',
                           equipamentos=[e['nome']], origem='AS-IS'))
    for lk in ctx.enlaces:
        faltando = [n for n, v in (('interface A', lk['iface_a']), ('interface B', lk['iface_b']),
                                   ('IP A', lk['ip_a']), ('IP B', lk['ip_b'])) if not v]
        if faltando:
            itens.append(_item(c, f'{lk["rotulo_a"]} ↔ {lk["rotulo_b"]}',
                               'Completar o inventário do enlace na topologia',
                               atual='Sem ' + ', '.join(faltando), alvo='Ponta A e ponta B identificadas',
                               equipamentos=[lk['rotulo_a'], lk['rotulo_b']], origem='Topologia'))
    for chave, estado in ctx.estado.items():
        if estado == 'novo':
            itens.append(_item(c, ctx.nome(chave), 'Especificar e instalar o novo equipamento (LLD)',
                               alvo=ctx.papeis_txt(chave), equipamentos=[ctx.nome(chave)],
                               origem='Cenário TO-BE'))
    if not ctx.enlaces:
        pend.append('Nenhum enlace de backbone documentado: desenhe a topologia (ou o cenário TO-BE) com '
                    'ponta A/B, interfaces e IPs antes de detalhar a Wave 1.')
    return itens


def _wave1(ctx, pend):
    c = _Contador('W1')
    conv = ctx.conv
    alvo_global = str(conv.get('mtu_alvo') or '')
    itens, observados = [], []

    # inventário de valores observados por camada
    camadas = defaultdict(lambda: defaultdict(set))
    for e in ctx.m['huaweis']:
        for i in e['extr']['interfaces']:
            nome = i['nome']
            if nome.startswith('Virtual-Template'):
                valor = i['mtu'] or 1500
                camadas['PPP (Virtual-Template)'][f'MTU {valor}' + (f' / MRU {i["ppp_mru"]}' if i.get('ppp_mru') else '')
                                                   + (f' / MSS {i["mss"]}' if i.get('mss') else '')].add(e['pop'])
            elif (i['ospf'] or i['mpls']) and not nome.startswith(('LoopBack', 'NULL', 'Tunnel')):
                camadas['Interface de backbone'][str(_mtu(i))].add(e['pop'])
                if i['mpls_mtu']:
                    camadas['MPLS MTU'][str(i['mpls_mtu'])].add(e['pop'])
    for s in ctx.m['l2vpn']:
        for mt in s['mtus']:
            rotulo = 'VSI/PW PPPoE' if s['pppoe'] else 'VSI/PW/L2VC'
            camadas[rotulo][mt].add(', '.join(s['nomes'][:1]))
    tratamento = {
        'Interface de backbone': 'Validar a capacidade ponta a ponta e igualar as duas pontas',
        'MPLS MTU': 'Recalcular conforme a pilha máxima de labels',
        'VSI/PW/L2VC': 'Preservar o serviço durante a normalização; igualar as duas pontas do PW',
        'VSI/PW PPPoE': 'Validar PW, PPPoE e frame máximo',
        'PPP (Virtual-Template)': 'Validar MRU, MSS e payload PPPoE',
    }
    for camada, valores in camadas.items():
        for valor, onde in sorted(valores.items()):
            observados.append({'camada': camada, 'valor': valor,
                               'ocorrencia': ', '.join(sorted(onde))[:160], 'tratamento': tratamento[camada]})

    if not alvo_global:
        pend.append('MTU alvo do backbone não definida na convenção: o valor deve ser aprovado no LLD '
                    'considerando Ethernet, VLAN/QinQ, MPLS, labels, RSVP-TE legado, L2VPN e PPPoE.')

    lotes = _lotes(ctx)
    for lk in ctx.enlaces:
        if lk['estado'] == 'remover':
            continue
        eq_a = ctx.por_acesso.get(lk['a']) if isinstance(lk['a'], int) else None
        eq_b = ctx.por_acesso.get(lk['b']) if isinstance(lk['b'], int) else None
        ia, fa = _interface(eq_a, lk['iface_a'], lk['ip_a'], lk['vlan'])
        ib, fb = _interface(eq_b, lk['iface_b'], lk['ip_b'], lk['vlan'])
        mtu_a, mtu_b = _mtu_l3(ia, fa), _mtu_l3(ib, fb)
        alvo = lk['mtu_alvo'] or alvo_global
        atual = f'A: {_desc_mtu(ia, fa, lk["iface_a"])} · B: {_desc_mtu(ib, fb, lk["iface_b"])}'
        mpls = sorted({x for x in ((ia or {}).get('mpls_mtu'), (ib or {}).get('mpls_mtu')) if x})
        if mpls:
            atual += f' · MPLS MTU {", ".join(map(str, mpls))}'
        divergente = mtu_a and mtu_b and mtu_a != mtu_b
        fora_alvo = alvo and any(v and str(v) != alvo for v in (mtu_a, mtu_b))
        if lk['estado'] == 'novo':
            acao = 'Ativar o enlace novo já com a MTU alvo nas duas pontas e no meio'
        elif divergente:
            acao = 'Igualar a MTU das duas pontas e do meio de transporte'
        elif fora_alvo:
            acao = 'Ajustar ponta A, meio e ponta B para a MTU alvo'
        elif not (mtu_a and mtu_b):
            acao = 'Levantar a MTU das duas pontas (interface não identificada no backup)'
        else:
            acao = 'Validar a MTU ponta a ponta (payload máximo com DF) e registrar evidência'
        itens.append(_item(c, f'{lk["rotulo_a"]} ↔ {lk["rotulo_b"]}', acao, atual=atual,
                           alvo=alvo or 'Definir no LLD', equipamentos=[lk['rotulo_a'], lk['rotulo_b']],
                           origem='Cenário TO-BE' if ctx.cenario else 'Topologia/AS-IS',
                           lote=lotes.get(id(lk), ''),
                           prioridade='Alta' if divergente else ''))
    grupos = defaultdict(list)
    for it in itens:
        grupos[it['lote']].append(it)
    tabela_lotes = []
    for lote in sorted(grupos, key=lambda x: (x == '', str(x).zfill(4))):
        its = grupos[lote]
        pops = []
        for it in its:
            for eqn in it['equipamentos']:
                if eqn not in pops:
                    pops.append(eqn)
        alt = 'alternativo' in str(lote)
        tabela_lotes.append({
            'lote': lote or '—', 'dominio': ', '.join(pops[:6]) + ('…' if len(pops) > 6 else ''),
            'enlaces': len(its),
            'objetivo': 'Fechar a validação de contingência' if alt else
                        ('Validar o corredor inicial a partir do núcleo' if str(lote) == '1' else 'Normalizar o corredor'),
        })
    return itens, observados, tabela_lotes


def _lotes(ctx):
    """Lote de cada enlace: o definido no cenário; senão, a distância (em
    saltos) da ponta mais próxima do núcleo (RR01/RR02). Enlaces
    alternativos e de último recurso ficam no lote final."""
    saida = {}
    raizes = [k for k in ctx.papel if ctx.tem(k, 'RR01', 'RR02')]
    viz = defaultdict(set)
    for lk in ctx.enlaces:
        viz[lk['a']].add(lk['b'])
        viz[lk['b']].add(lk['a'])
    dist = {r: 0 for r in raizes if r in viz}
    fila = list(dist)
    while fila:
        n = fila.pop(0)
        for v in viz[n]:
            if v not in dist:
                dist[v] = dist[n] + 1
                fila.append(v)
    maior = max(dist.values(), default=0)
    for lk in ctx.enlaces:
        if lk['lote']:
            saida[id(lk)] = lk['lote']
        elif lk['papel'] in ('alternativo', 'ultimo_recurso'):
            saida[id(lk)] = f'{maior + 2} (alternativos)'
        elif dist:
            d = max(dist.get(lk['a'], maior + 1), dist.get(lk['b'], maior + 1))
            saida[id(lk)] = str(max(d, 1))
        else:
            saida[id(lk)] = ''
    return saida


def _wave2(ctx, pend):
    c = _Contador('W2')
    conv = ctx.conv
    itens = []
    proc_alvo = str(conv.get('ospf_processo') or '')
    area_alvo = str(conv.get('ospf_area') or '')
    p2p_v4 = str(conv.get('p2p_v4') or '')
    timers_alvo = f'{conv.get("bfd_tx")}/{conv.get("bfd_rx")}/{conv.get("bfd_mult")}'
    exigir_sync = 'obrig' in str(conv.get('ldp_sync', '')).lower()
    bloco_lo = next((_rede(l['bloco']) for l in conv.get('ipv4', [])
                     if 'loopback' in l.get('uso', '').lower() and 'core' in l.get('uso', '').lower()), None)
    for e in ctx.m['huaweis']:
        x = e['extr']
        if not (x['mpls'] or x['ospf']) or ctx.estado.get(e['acesso_id']) == 'remover':
            continue
        nome = e['nome_exibicao']
        principais = [o for o in x['ospf'] if not o['vrf']]
        for o in principais:
            if proc_alvo and o['processo'] != proc_alvo:
                itens.append(_item(c, nome, 'Migrar o OSPF de infraestrutura para o processo padrão',
                                   atual=f'processo {o["processo"]}', alvo=f'processo {proc_alvo}',
                                   equipamentos=[nome], origem='HLD — Underlay'))
            outras = [a for a in o['areas'] if a != area_alvo]
            if area_alvo and outras:
                itens.append(_item(c, nome, 'Consolidar as áreas OSPF', atual='áreas ' + ', '.join(o['areas']),
                                   alvo=f'área {area_alvo}', equipamentos=[nome], origem='HLD — Underlay'))
            if o['router_id'] and x['lsr_id'] and o['router_id'] != x['lsr_id']:
                itens.append(_item(c, nome, 'Igualar o router-id OSPF ao MPLS LSR-ID',
                                   atual=o['router_id'], alvo=x['lsr_id'], equipamentos=[nome], origem='HLD — Underlay'))
            if o.get('import_route'):
                itens.append(_item(c, nome, 'Remover a redistribuição do IGP (somente infraestrutura)',
                                   atual='import-route ' + ', '.join(o['import_route']), alvo='sem redistribuição genérica',
                                   equipamentos=[nome], origem='HLD — Underlay'))
            if o.get('bfd_timers') and o['bfd_timers'] != timers_alvo:
                itens.append(_item(c, nome, 'Padronizar os timers de BFD do OSPF',
                                   atual=o['bfd_timers'], alvo=timers_alvo, equipamentos=[nome], origem='HLD — Underlay'))
        ifs = [i for i in x['interfaces'] if i['ospf'] and not i['nome'].startswith(('LoopBack', 'NULL', 'Tunnel'))]
        bfd_global = any(o['bfd'] for o in principais)
        sem_sync = [i['nome'] for i in ifs if i['mpls'] and not i['ldp_sync']]
        if exigir_sync and sem_sync:
            itens.append(_item(c, nome, 'Habilitar LDP/IGP synchronization',
                               atual=f'{len(sem_sync)} interface(s) sem sync: ' + ', '.join(sem_sync[:4]),
                               alvo='ospf ldp-sync em todas', equipamentos=[nome], origem='HLD — Underlay'))
        sem_bfd = [i['nome'] for i in ifs if not i['bfd']]
        if sem_bfd and not bfd_global:
            itens.append(_item(c, nome, 'Habilitar BFD no OSPF',
                               atual=f'{len(sem_bfd)} interface(s) sem BFD', alvo=timers_alvo,
                               equipamentos=[nome], origem='HLD — Underlay'))
        timers = sorted({i['bfd_timers'] for i in ifs if i['bfd_timers'] and i['bfd_timers'] != timers_alvo})
        if timers:
            itens.append(_item(c, nome, 'Padronizar os timers de BFD das interfaces',
                               atual=', '.join(timers), alvo=timers_alvo, equipamentos=[nome], origem='HLD — Underlay'))
        if p2p_v4.isdigit():
            fora = [f'{i["nome"]} {p}' for i in ifs for p in i['ips']
                    if '/' in p and p.split('/')[1] != p2p_v4 and not i['nome'].startswith('Vlanif')]
            if fora:
                itens.append(_item(c, nome, f'Renumerar enlaces P2P para /{p2p_v4}',
                                   atual=f'{len(fora)} interface(s): ' + ', '.join(fora[:3]), alvo=f'IPv4 /{p2p_v4}',
                                   equipamentos=[nome], origem='HLD — Endereçamento'))
        lo_alvo = ctx.loopback_alvo.get(e['acesso_id'])
        if lo_alvo and lo_alvo.split('/')[0] != x['lsr_id']:
            itens.append(_item(c, nome, 'Implantar a loopback aprovada e migrar LSR-ID/router-id',
                               atual=x['lsr_id'] or '—', alvo=lo_alvo, equipamentos=[nome], origem='Cenário TO-BE'))
        elif bloco_lo and _ip(x['lsr_id']) and _ip(x['lsr_id']) not in bloco_lo and not lo_alvo:
            itens.append(_item(c, nome, 'Planejar a loopback no bloco de loopbacks core',
                               atual=x['lsr_id'], alvo=f'/32 em {bloco_lo}', equipamentos=[nome],
                               origem='HLD — Endereçamento'))
        if x['rsvp_te']:
            tuneis = ', '.join(f'{t["nome"]}→{t["destino"]}' for t in x['tuneis_te']) or 'nenhum túnel explícito'
            itens.append(_item(c, nome, 'Inventariar e justificar o RSVP-TE remanescente (legado controlado)',
                               atual=tuneis, alvo=str(conv.get('rsvp') or 'Legado controlado'),
                               equipamentos=[nome], origem='HLD — Underlay'))
    for lk in ctx.enlaces:
        if lk['estado'] == 'novo':
            itens.append(_item(c, f'{lk["rotulo_a"]} ↔ {lk["rotulo_b"]}', 'Ativar o enlace novo no underlay',
                               alvo=f'OSPF {proc_alvo}/{area_alvo}, LDP + sync, BFD {timers_alvo}'
                                    + (f', custo {lk["custo"]}' if lk['custo'] else ''),
                               equipamentos=[lk['rotulo_a'], lk['rotulo_b']], origem='Cenário TO-BE'))
        elif lk['custo']:
            itens.append(_item(c, f'{lk["rotulo_a"]} ↔ {lk["rotulo_b"]}', 'Aplicar o custo OSPF do LLD de topologia',
                               alvo=f'custo {lk["custo"]}', equipamentos=[lk['rotulo_a'], lk['rotulo_b']],
                               origem='Cenário TO-BE'))
    return itens


def _peers_ibgp(eq):
    bgp = ((eq or {}).get('extr') or {}).get('bgp') or {}
    return {p['ip']: p for p in bgp.get('peers', []) if p['tipo'] == 'ibgp'}


def _wave3(ctx, pend):
    c = _Contador('W3')
    itens = []
    rrs = {}
    for papel in ('RR01', 'RR02'):
        eq = ctx.eq_do_papel(papel)
        rotulo = ctx.rotulo_do_papel(papel)
        if not rotulo:
            pend.append(f'{papel} não definido: marque o papel no cenário TO-BE ou nos mapeamentos.')
            continue
        rrs[papel] = eq
    familias = [f.strip() for f in str(ctx.conv.get('familias_mpbgp', '')).split(',') if f.strip()]
    af_de = {'VPNV4': 'vpnv4', 'VPNV6': 'vpnv6', 'L2VPN': 'l2vpn-ad-family'}
    exigidas = [af_de[f.upper()] for f in familias if f.upper() in af_de]
    lsr_rr = {p: ((e or {}).get('extr') or {}).get('lsr_id') for p, e in rrs.items() if e}
    if len(lsr_rr) == 2:
        a, b = rrs['RR01'], rrs['RR02']
        if lsr_rr['RR02'] not in _peers_ibgp(a) or lsr_rr['RR01'] not in _peers_ibgp(b):
            itens.append(_item(c, 'RR01 ↔ RR02', 'Estabelecer iBGP normal entre os RRs (mesmo cluster ID)',
                               equipamentos=[a['nome_exibicao'], b['nome_exibicao']], origem='HLD — MP-BGP'))
        else:
            itens.append(_item(c, 'RR01 ↔ RR02', 'Validar cluster ID comum e router IDs individuais',
                               equipamentos=[a['nome_exibicao'], b['nome_exibicao']], origem='HLD — MP-BGP'))
    # P só transporta labels: não fecha MP-BGP
    clientes = [k for k in ctx.papel if ctx.tem(k, 'PE', 'BNG01', 'BNG02', 'BNG03', 'BORDA')
                and ctx.estado.get(k) != 'remover']
    for chave in clientes:
        eq = ctx.por_acesso.get(chave) if isinstance(chave, int) else None
        nome = ctx.nome(chave)
        if not eq or not eq.get('extr'):
            itens.append(_item(c, nome, 'Configurar sessões com RR01 e RR02 (sem backup para comparar)',
                               alvo=' + '.join(filter(None, [ctx.rotulo_do_papel('RR01'), ctx.rotulo_do_papel('RR02')])),
                               equipamentos=[nome], origem='HLD — MP-BGP'))
            continue
        if eq.get('vendor') != 'huawei':
            continue
        peers = _peers_ibgp(eq)
        faltam, fam_faltando = [], []
        for papel, lsr in lsr_rr.items():
            if not lsr or rrs.get(papel) is eq:
                continue
            p = peers.get(lsr)
            if not p or not p['ativa']:
                faltam.append(papel)
            else:
                ausentes = [af for af in exigidas if af not in p['afs']]
                if ausentes:
                    fam_faltando.append(f'{papel}: {", ".join(ausentes)}')
        if faltam:
            itens.append(_item(c, nome, 'Ativar sessão MP-BGP com ' + ' e '.join(faltam),
                               atual='sem sessão ativa com ' + ', '.join(faltam),
                               alvo='sessões com RR01 e RR02 (' + ', '.join(familias) + ')',
                               equipamentos=[nome], origem='HLD — MP-BGP', prioridade='Alta'))
        if fam_faltando:
            itens.append(_item(c, nome, 'Habilitar as famílias do padrão nas sessões com os RRs',
                               atual='; '.join(fam_faltando), alvo=', '.join(familias),
                               equipamentos=[nome], origem='HLD — MP-BGP'))
        eh_rr = any(rrs.get(pp) is eq for pp in rrs)
        if not eh_rr:
            lsrs = set(lsr_rr.values())
            pe_pe = [ip for ip, p in peers.items() if ip not in lsrs and p['ativa']]
            if pe_pe:
                itens.append(_item(c, nome, 'Retirar sessões iBGP fora do modelo dual-RR (após redundância comprovada)',
                                   atual=', '.join(pe_pe[:5]) + ('…' if len(pe_pe) > 5 else ''),
                                   alvo='somente RR01 e RR02', equipamentos=[nome], origem='HLD — MP-BGP'))
    papeis_rr = {id(e) for e in rrs.values() if e}
    for r in ctx.m['rrs']:
        eq = next((e for e in ctx.m['equipamentos'] if e['nome_exibicao'] == r['nome']), None)
        if eq and id(eq) not in papeis_rr:
            itens.append(_item(c, r['nome'], 'Retirar a função de route reflector (fora do par RR01/RR02)',
                               atual='reflect-client ativo', alvo='cliente de RR01 e RR02',
                               equipamentos=[r['nome']], origem='HLD — MP-BGP'))
    for p in ctx.m['peers_ibgp_desconhecidos']:
        itens.append(_item(c, p['equipamento'], f'Remover peer iBGP histórico {p["peer"]}',
                           atual=p['descricao'] or 'sem descrição', alvo='removido',
                           equipamentos=[p['equipamento']], origem='AS-IS'))
    return itens, rrs


def _rd(lsr, sid):
    return f'{lsr or "<LSR-ID>"}:{sid}'


def _wave4(ctx, pend):
    c = _Contador('W4')
    conv = ctx.conv
    itens = []
    mapa = ctx.map.get('vrfs') or {}
    l3vpns = OrderedDict()
    for v in ctx.m['vrfs']:
        alvo = mapa.get(v['nome']) or L3VPN
        if alvo == ELIMINAR:
            itens.append(_item(c, f'VRF {v["nome"]}', 'Eliminar a VRF; tratar o estado do assinante como '
                               'RESTRICTED/PENDING na VRF-ACCESS (walled garden)',
                               atual=f'RD {", ".join(v["rds"]) or "—"} em {", ".join(v["pes"])}',
                               alvo='removida', equipamentos=v['pes'], origem='HLD — BNG e acesso'))
            continue
        if alvo == L3VPN:
            l3vpns[v['nome']] = v
            continue
        serv = cv.servico_por_nome(conv, alvo)
        if not serv:
            pend.append(f'VRF {v["nome"]} mapeada para "{alvo}", que não existe no catálogo do HLD.')
            continue
        rt_alvo = cv.rt(conv, serv['rt'])
        ok_nome = v['nome'] == alvo
        ok_rt = set(v['rts']) == {rt_alvo}
        for pop in v['pes']:
            eq = next((e for e in ctx.m['huaweis'] if e['pop'] == pop), None)
            lsr = (eq or {}).get('extr', {}).get('lsr_id') if eq else ''
            rd_alvo = _rd(lsr, serv['id'])
            ok_rd = rd_alvo in v['rds'] and len(v['rds']) == 1
            if ok_nome and ok_rt and ok_rd:
                continue
            itens.append(_item(c, f'{v["nome"]} → {alvo}',
                               'Criar a VRF padrão em paralelo e migrar interfaces e sessões'
                               if not ok_nome else 'Ajustar RD/RT ao padrão',
                               atual=f'RD {", ".join(v["rds"]) or "—"} · RT {", ".join(v["rts"][:3]) or "—"}',
                               alvo=f'RD {rd_alvo} · RT {rt_alvo}',
                               equipamentos=[eq['nome_exibicao'] if eq else pop], origem='HLD — VRFs'))
    faixa = next((t for t in conv.get('transportes', []) if t.get('tipo') == 'L3VPN'), {})
    seq = _inicio_faixa(faixa.get('ids'), 6001)
    seq_rt = _inicio_faixa(faixa.get('rts'), 16001)
    l3_saida = []
    for i, (nome, v) in enumerate(l3vpns.items()):
        contratante = _token(nome)
        sid = seq + i
        l3_saida.append({'legado': nome, 'nome': f'TR-{contratante}-L3-{i + 1:03d}', 'id': sid,
                         'rt': cv.rt(conv, seq_rt + i), 'pes': v['pes'], 'rds': v['rds'], 'rts': v['rts']})
    # communities
    comm_map = ctx.map.get('communities') or {}
    comm = []
    for linha in ctx.m['communities']['linhas']:
        alvo = comm_map.get(linha['valor'], '')
        comm.append({'legado': linha['valor'], 'finalidade': linha['finalidade'],
                     'alvo': cv.faixa_community(conv, alvo) if alvo else 'A definir'})
    if any(x['alvo'] == 'A definir' for x in comm):
        pend.append(f'{sum(1 for x in comm if x["alvo"] == "A definir")} community(ies) legada(s) sem destino '
                    'definido nos mapeamentos.')
    if comm:
        itens.append(_item(c, 'Communities', 'Implantar as communities do HLD em paralelo, remarcar rotas e só '
                           'então remover as legadas', atual=f'{len(comm)} valor(es)/faixa(s) legados',
                           alvo=f'formato {"large (RFC 8092)" if cv.formato_community(conv.get("asn")) == cv.FORMATO_LARGE else "padrão"}',
                           origem='HLD — Communities'))
    padrao_rp = re.compile(r'^RP-AS\d+-[A-Z0-9-]+-V[46]-(IN|OUT)$')
    for e in ctx.m['huaweis']:
        fora = [n for n in e['extr']['route_policies'] if not padrao_rp.match(n)]
        if fora:
            itens.append(_item(c, e['nome_exibicao'], 'Renomear route-policies para RP-AS<ASN>-<NOME>-V4/V6-IN/OUT',
                               atual=f'{len(fora)} policy(ies) fora do padrão (ex.: {", ".join(fora[:3])})',
                               alvo='nomenclatura do HLD', equipamentos=[e['nome_exibicao']],
                               origem='HLD — Nomenclatura'))
    return itens, l3_saida, comm


def _inicio_faixa(txt, padrao):
    m = re.match(r'\s*(\d+)', str(txt or ''))
    return int(m.group(1)) if m else padrao


def _wave5(ctx, pend, l3vpns):
    c = _Contador('W5')
    conv = ctx.conv
    itens = []
    contratantes = ctx.map.get('contratantes') or {}
    criticos = [t.strip().upper() for t in str(ctx.map.get('criticos') or '').split(',') if t.strip()]
    faixa = next((t for t in conv.get('transportes', []) if t.get('tipo') == 'L2VPN'), {})
    seq = _inicio_faixa(faixa.get('ids'), 7001)
    seq_rt = _inicio_faixa(faixa.get('rts'), 17001)
    servicos = []
    for s in ctx.m['l2vpn']:
        if s.get('pppoe'):
            continue
        chave = chave_servico_l2(s)
        contratante = _token(contratantes.get(chave) or 'A-DEFINIR')
        texto = ' '.join(s['nomes'] + s['descricoes']).upper()
        if any(k and k in (contratante + ' ' + texto) for k in criticos):
            prio = 4
        elif re.search(r'\bOLTS?\b|GERENCIA|MGMT', texto):
            prio = 1
        elif s['uma_ponta'] or s['peers_desconhecidos']:
            prio = 3
        else:
            prio = 2
        servicos.append((prio, contratante, s, chave))
    servicos.sort(key=lambda x: (x[0], x[1], str(x[2]['id']).zfill(6)))
    seq_por_contratante = Counter()
    grupos = {1: 'Gerência de OLTs e serviços de baixa criticidade', 2: 'Transportes empresariais sem acesso em massa',
              3: 'L2VPNs com dependências a confirmar', 4: 'Serviços críticos', 5: 'PPPoE dos POPs (com a Wave 6)'}
    sem_contratante = 0
    for n, (prio, contratante, s, chave) in enumerate(servicos):
        seq_por_contratante[contratante] += 1
        nome = f'TR-{contratante}-L2-{seq_por_contratante[contratante]:03d}'
        if contratante == 'A-DEFINIR':
            sem_contratante += 1
        itens.append(_item(c, f'{s["tecnologias"][0] if s["tecnologias"] else "L2"} {s["id"]} '
                              f'({", ".join(s["nomes"][:2])})',
                           'Recriar com sinalização BGP e migrar endpoint por endpoint; rollback para o serviço legado',
                           atual=f'pontas {", ".join(s["pontas"])} · {", ".join(s["sinalizacao"]) or "?"}'
                                 + (f' · MTU {", ".join(s["mtus"])}' if s['mtus'] else ''),
                           alvo=f'{nome} · ID {seq + n} · RT {cv.rt(conv, seq_rt + n)}',
                           equipamentos=s['pontas'], origem='HLD — Transportes',
                           prioridade=f'{prio} — {grupos[prio]}'))
    for i, l3 in enumerate(l3vpns):
        itens.append(_item(c, f'VRF {l3["legado"]}', 'Recriar como L3VPN fechada e migrar PE por PE',
                           atual=f'RD {", ".join(l3["rds"]) or "—"} · RT {", ".join(l3["rts"][:2]) or "—"}',
                           alvo=f'{l3["nome"]} · ID {l3["id"]} · RT {l3["rt"]} · RD <LSR-ID>:{l3["id"]}',
                           equipamentos=l3['pes'], origem='HLD — Transportes',
                           prioridade='4 — Serviços críticos' if any(k in l3['legado'].upper() for k in criticos)
                           else '2 — Transportes empresariais'))
    if sem_contratante:
        pend.append(f'{sem_contratante} serviço(s) L2 sem contratante identificado: preencher nos mapeamentos.')
    itens.sort(key=lambda it: it['prioridade'])
    return itens


def _lista_papeis(valor):
    if isinstance(valor, (list, tuple, set)):
        itens = valor
    else:
        itens = re.split(r'[,+;\s]+', str(valor or ''))
    return [p.strip().upper() for p in itens if p and p.strip().upper() in PAPEIS_NO]


def _bng_numero(papel):
    m = re.match(r'BNG0?(\d)', papel or '')
    return int(m.group(1)) if m else None


def _wave6(ctx, pend):
    c = _Contador('W6')
    conv = ctx.conv
    itens = []
    bngs = {p: ctx.rotulo_do_papel(p) for p in ('BNG01', 'BNG02', 'BNG03') if ctx.rotulo_do_papel(p)}
    if not bngs:
        pend.append('Nenhum BNG central definido (papel BNG01/BNG02 no cenário ou nos mapeamentos).')
    destino_map = ctx.map.get('bng_destino') or {}
    sem_destino = []
    for b in ctx.m['bngs_remotos']:
        eq = next((e for e in ctx.m['equipamentos'] if e['nome_exibicao'] == b['nome']), None)
        chave = eq['acesso_id'] if eq else None
        destino = ctx.bng_destino.get(chave) or destino_map.get(b['pop']) or destino_map.get(str(chave)) or ''
        if ctx.estado.get(chave, 'manter') == 'manter' and ctx.explicito(chave, 'BRAS-POP') and not destino:
            acao = 'Manter como BRAS de POP pequeno (pool 100.124.0.0/14 do HLD)'
        else:
            acao = 'Transportar o PPPoE do POP até o BNG central e migrar os assinantes'
        itens.append(_item(c, f'POP {b["pop"]}', acao,
                           atual=f'{b["nome"]} ({b["modelo"]}) · {b["pppoe"]} servidor(es) PPPoE'
                                 + (' · NAT embarcado' if b['nat_embarcado'] else ''),
                           alvo=(f'{destino} — {bngs.get(destino, "")}' if destino else 'BNG de destino a definir'),
                           equipamentos=[b['nome']], origem='HLD — BNG'))
        if not destino and 'Transportar' in acao:
            sem_destino.append(b['pop'])
    if sem_destino:
        pend.append(f'BNG de destino não definido para {len(sem_destino)} POP(s) com BNG remoto: '
                    + ', '.join(sem_destino) + '.')
    for x in ctx.m['pppoe']:
        itens.append(_item(c, f'PPPoE {x["servico"]}', 'Terminar diretamente na VRF-ACCESS do BNG central',
                           atual=f'{x["vsi"]} (VLAN {x["vlan"]}) em {x["pop_bng"]}',
                           alvo='VRF-ACCESS · validar PPPoE, AAA, accounting, CoA, IPv4, IPv6 PD e retorno',
                           equipamentos=[x['bng']], origem='HLD — BNG'))
    pools_hld = {}
    for p in conv.get('pools_cgnat', []):
        n = _bng_numero(p.get('uso'))
        if n:
            pools_hld[n] = _rede(p.get('bloco'))
    for b in ctx.m['bngs_centrais']:
        eq = next((e for e in ctx.m['equipamentos'] if e['nome_exibicao'] == b['nome']), None)
        papel = next((p for p in sorted(ctx.papel.get(eq['acesso_id'], set())) if p.startswith('BNG')), '') if eq else ''
        n = _bng_numero(papel)
        bloco = pools_hld.get(n) if n else None
        for pool in b['pools']:
            rede = _rede(pool['rede'])
            if not rede or rede.version != 4 or not rede.subnet_of(ipaddress.ip_network('100.64.0.0/10')):
                continue   # só pools de CGNAT (RFC 6598)
            if bloco and not rede.subnet_of(bloco):
                itens.append(_item(c, f'{b["nome"]} pool {pool["nome"]}', 'Renumerar o pool CGNAT para o bloco do BNG',
                                   atual=pool['rede'], alvo=f'{bloco} ({papel})', equipamentos=[b['nome']],
                                   origem='HLD — CGNAT', prioridade='Alta'))
        pd = [x for x in b['pools_v6'] if x.get('tamanho_delegado')]
        tam = next((t['prefixo'].strip('/') for t in conv.get('ipv6_tamanhos', []) if 'PD padrão' in t.get('tipo', '')), '')
        for x in pd:
            if tam and x['tamanho_delegado'] != tam:
                itens.append(_item(c, f'{b["nome"]} PD {x["nome"]}', 'Ajustar o tamanho do prefixo delegado',
                                   atual=f'/{x["tamanho_delegado"]}', alvo=f'/{tam}', equipamentos=[b['nome']],
                                   origem='HLD — IPv6'))
        if b['vrfs'] and 'VRF-ACCESS' not in b['vrfs']:
            itens.append(_item(c, b['nome'], 'Mover pools e terminação dos assinantes para a VRF-ACCESS',
                               atual='VRF ' + ', '.join(b['vrfs']), alvo='VRF-ACCESS', equipamentos=[b['nome']],
                               origem='HLD — BNG'))
    return itens


def _wave7(ctx, pend):
    c = _Contador('W7')
    itens = []
    bngs = [p for p in ('BNG01', 'BNG02', 'BNG03') if ctx.rotulo_do_papel(p)]
    for p in bngs:
        eq = ctx.eq_do_papel(p)
        x = (eq or {}).get('extr') or {}
        tem_card = x.get('cgnat_servico')
        itens.append(_item(c, f'{p} — {ctx.rotulo_do_papel(p)}',
                           'Validar hardware, licença e capacidade da card de serviço; criar VRF-CGNAT e domínios '
                           'inside/outside',
                           atual='service-instance-group CGNAT já configurado' if tem_card else 'card não identificada no backup',
                           alvo='CGNAT ativo na card', equipamentos=[ctx.rotulo_do_papel(p)], origem='HLD — CGNAT'))
    for cg in ctx.m['cgnats']:
        eq = next((e for e in ctx.m['equipamentos'] if e['nome_exibicao'] == cg['nome']), None)
        destino = next((p for p in bngs if (ctx.eq_do_papel(p) or {}).get('pop') == cg['pop']), '')
        itens.append(_item(c, cg['nome'], 'Reproduzir pools e regras na card, executar piloto e migrar blocos em lotes',
                           atual=f'{cg["modelo"]} · privado {cg["pools_privados"] or "—"} · público {cg["publicos"] or "—"}'
                                 + (' · determinístico' if cg['deterministico'] else ''),
                           alvo=f'card do {destino}' if destino else 'BNG de destino a definir',
                           equipamentos=[cg['nome']], origem='HLD — CGNAT'))
        if not destino:
            pend.append(f'CGNAT externo {cg["nome"]} ({cg["pop"]}) sem BNG de destino no mesmo POP.')
        if eq:
            ctx.estado.setdefault(eq['acesso_id'], 'remover')
    return itens


def _wave8(ctx, pend):
    c = _Contador('W8')
    conv = ctx.conv
    asn = conv.get('asn', '')
    itens = []
    ids = {}

    def identidade(prefixo, chave, base):
        grupo = ids.setdefault(prefixo, OrderedDict())
        if chave not in grupo:
            grupo[chave] = len(grupo) + 1
        n = grupo[chave]
        return f'{prefixo}{n:02d}', cv.community(conv, base + (n - 1) * 10)

    for s in ctx.m['ebgp']:
        if s['vendor'] != 'huawei' or not s['ativa'] or not s['vrf_internet']:
            continue
        classe = s['classe']
        if classe.startswith('Upstream'):
            ident, com = identidade('UPLINK', s['asn'], 20000)
            lp = cv.lp_da_classe(conv, 'UPSTREAM')
            papel_com = cv.community(conv, 21003)
        elif classe == 'Parceiro de conteúdo / CDN':
            ident, com = identidade('CONTENT', s['asn'], 20100)
            lp = cv.lp_da_classe(conv, 'CONTENT')
            papel_com = ''
        elif classe in ('IX / PTT', 'Peering / troca de tráfego'):
            ident, com = identidade('IX', s['asn'], 20200)
            lp = cv.lp_da_classe(conv, 'IX')
            papel_com = cv.community(conv, 21004)
        elif classe == 'ISP downstream':
            ident, com = 'DOWNSTREAM', cv.community(conv, 21002)
            lp = cv.lp_da_classe(conv, 'CUSTOMER')
            papel_com = ''
        else:
            continue
        nome_rp = _token(s['cliente']).replace('A-DEFINIR', f'AS{s["asn"]}')
        fam = 'V6' if s['familia'] == 'v6' else 'V4'
        alvo_pol = f'RP-AS{s["asn"]}-{nome_rp}-{fam}-IN / -OUT'
        faltas = []
        if s['lp'] and lp and str(lp) not in map(str, s['lp']):
            faltas.append(f'LP {", ".join(map(str, s["lp"]))}→{lp}')
        elif not s['lp'] and lp:
            faltas.append(f'LP padrão→{lp}')
        if not s['filtra_bogons'] and not classe.startswith('ISP downstream'):
            faltas.append('sem filtro de bogons')
        if s['sem_policy_in'] or s['sem_policy_out']:
            faltas.append('sem policy de ' + ('entrada' if s['sem_policy_in'] else 'saída'))
        eq = next((e for e in ctx.m['huaweis'] if e['nome_exibicao'] == s['equipamento']), None)
        peer = next((p for p in ((eq or {}).get('extr', {}).get('bgp') or {}).get('peers', [])
                     if p['ip'] == s['peer']), {})
        if not peer.get('route_limit'):
            faltas.append('sem max-prefix')
        itens.append(_item(c, f'{s["descricao"] or s["cliente"]} (AS{s["asn"]}, {s["familia"].upper()})',
                           'Aplicar identidade lógica, classe de LP, communities, RPKI (INVALID rejeitado) e max-prefix',
                           atual=f'{classe} · {s["policy_in"] or "—"} / {s["policy_out"] or "—"}'
                                 + (f' · {"; ".join(faltas)}' if faltas else ''),
                           alvo=f'{ident} · {com} {papel_com}'.strip() + f' · LP {lp} · {alvo_pol}',
                           equipamentos=[s['equipamento']], origem='HLD — Borda',
                           prioridade='Alta' if any('sem' in f for f in faltas) else ''))
    identidades = {k: list(v.items()) for k, v in ids.items()}
    return itens, identidades


def _wave9(ctx, pend, rrs):
    c = _Contador('W9')
    itens = []
    for cg in ctx.m['cgnats']:
        itens.append(_item(c, cg['nome'], 'Retirar o CGNAT externo após aceite e estabilidade',
                           equipamentos=[cg['nome']], origem='Wave 7'))
    for b in ctx.m['bngs_remotos']:
        eq = next((e for e in ctx.m['equipamentos'] if e['nome_exibicao'] == b['nome']), None)
        if eq and ctx.explicito(eq['acesso_id'], 'BRAS-POP') \
                and ctx.estado.get(eq['acesso_id'], 'manter') != 'remover':
            continue
        itens.append(_item(c, b['nome'], 'Desativar o BNG remoto já migrado', equipamentos=[b['nome']], origem='Wave 6'))
    for v, alvo in (ctx.map.get('vrfs') or {}).items():
        if alvo and alvo != v:
            itens.append(_item(c, f'VRF {v}', 'Remover a VRF legada substituída', alvo=alvo if alvo != ELIMINAR else '—',
                               origem='Wave 4'))
    total_rp = sum(len(e['extr']['ldp_remote_peers']) for e in ctx.m['huaweis'])
    if total_rp:
        itens.append(_item(c, 'LDP remote-peers', 'Remover remote-peers sem função remanescente',
                           atual=f'{total_rp} remote-peer(s)', origem='AS-IS'))
    for e in ctx.m['huaweis']:
        for t in e['extr']['tuneis_te']:
            itens.append(_item(c, f'{t["nome"]} ({e["nome_exibicao"]})', 'Remover o túnel RSVP-TE sem dependência',
                               atual=f'destino {t["destino"]}, caminho {t["caminho"] or "—"}',
                               equipamentos=[e['nome_exibicao']], origem='Wave 2'))
    for chave, estado in ctx.estado.items():
        if estado == 'remover' and not ctx.tem(chave, 'CGNAT-EXTERNO'):
            nome = ctx.nome(chave)
            if not any(nome in it['equipamentos'] for it in itens):
                itens.append(_item(c, nome, 'Desativar o equipamento marcado para remoção no cenário',
                                   equipamentos=[nome], origem='Cenário TO-BE'))
    for lk in ctx.enlaces:
        if lk['estado'] == 'remover':
            itens.append(_item(c, f'{lk["rotulo_a"]} ↔ {lk["rotulo_b"]}', 'Desativar o enlace após migrar o tráfego',
                               equipamentos=[lk['rotulo_a'], lk['rotulo_b']], origem='Cenário TO-BE'))
    if ctx.m['communities']['linhas']:
        itens.append(_item(c, 'Communities legadas', 'Remover filtros e marcações legados já substituídos',
                           atual=f'{len(ctx.m["communities"]["linhas"])} valor(es)/faixa(s)', origem='Wave 4'))
    return itens


# ═══════════════════════════════════════════════════════════════════════════
# Plano
# ═══════════════════════════════════════════════════════════════════════════

def gerar_plano(modelo, conv, cenario=None, mapeamentos=None):
    conv = cv.normalizar(conv or {})
    mapeamentos = mesclar_mapeamentos(mapeamentos_sugeridos(modelo, conv, cenario), mapeamentos or {})
    ctx = _Contexto(modelo, conv, cenario, mapeamentos)
    pend = []
    w0 = _wave0(ctx, pend)
    w1, observados, lotes = _wave1(ctx, pend)
    w2 = _wave2(ctx, pend)
    w3, rrs = _wave3(ctx, pend)
    w4, l3vpns, communities = _wave4(ctx, pend)
    w5 = _wave5(ctx, pend, l3vpns)
    w6 = _wave6(ctx, pend)
    w7 = _wave7(ctx, pend)
    w8, identidades = _wave8(ctx, pend)
    w9 = _wave9(ctx, pend, rrs)
    for aviso in cv.validar(conv):
        pend.append(aviso)
    itens = [w0, w1, w2, w3, w4, w5, w6, w7, w8, w9]
    waves = []
    for base, its in zip(WAVES, itens):
        waves.append(dict(base, itens=its, total=len(its)))
    papeis = []
    for chave in ctx.papel:
        if ctx.tem(chave, 'RR01', 'RR02', 'BNG01', 'BNG02', 'BNG03', 'BRAS-POP', 'CGNAT-EXTERNO') or \
                ctx.estado.get(chave) in ('novo', 'remover'):
            papeis.append({'equipamento': ctx.nome(chave), 'papel': ctx.papeis_txt(chave),
                           'estado': ctx.estado.get(chave, 'manter'), 'inferido': chave in ctx.inferidos})
    papeis.sort(key=lambda p: (p['inferido'], p['papel'], p['equipamento']))
    return {
        'waves': waves,
        'mtu_observado': observados,
        'lotes_mtu': lotes,
        'l3vpns': l3vpns,
        'communities': communities,
        'identidades': identidades,
        'papeis': papeis,
        'pendencias': list(OrderedDict.fromkeys(pend)),
        'mapeamentos': mapeamentos,
        'rr01': ctx.rotulo_do_papel('RR01'), 'rr02': ctx.rotulo_do_papel('RR02'),
        'bng01': ctx.rotulo_do_papel('BNG01'), 'bng02': ctx.rotulo_do_papel('BNG02'),
        'enlaces': len(ctx.enlaces),
        'com_cenario': bool(cenario),
        'asn': conv.get('asn', ''),
    }
