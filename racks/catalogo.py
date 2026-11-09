"""
Catálogo do rack: o que pode ser montado e que cabo liga o quê.

É a única fonte dessas tabelas — o backend valida com ela e a tela recebe
tudo pronto em `catalogo_para_tela()`, então um tipo novo entra aqui e
aparece na paleta sem mexer no JS.
"""
import re
import unicodedata

# tipo -> label, altura padrão (U), cor, ícone Font Awesome, portas desenhadas
# no espelho, se ocupa a profundidade inteira do rack e a face padrão.
# Passivos rasos (patch panel, DIO, organizador, tampa) ficam só na frente e
# deixam a traseira do mesmo U livre — é o que permite uma PDU atrás deles.
TIPOS = {
    'router':        {'label': 'Roteador',          'altura': 1, 'cor': '#00d9ff', 'icone': 'fa-route',          'portas': 8,  'profundidade_total': True,  'face': 'frente'},
    'switch':        {'label': 'Switch',            'altura': 1, 'cor': '#3fb950', 'icone': 'fa-network-wired',  'portas': 24, 'profundidade_total': True,  'face': 'frente'},
    'olt':           {'label': 'OLT',               'altura': 2, 'cor': '#e3b341', 'icone': 'fa-sitemap',        'portas': 16, 'profundidade_total': True,  'face': 'frente'},
    'firewall':      {'label': 'Firewall',          'altura': 1, 'cor': '#f85149', 'icone': 'fa-shield-halved',  'portas': 8,  'profundidade_total': True,  'face': 'frente'},
    'cgnat':         {'label': 'CGNAT',             'altura': 1, 'cor': '#ff6b35', 'icone': 'fa-shuffle',        'portas': 4,  'profundidade_total': True,  'face': 'frente'},
    'dwdm':          {'label': 'DWDM',              'altura': 2, 'cor': '#bc8cff', 'icone': 'fa-wave-square',    'portas': 8,  'profundidade_total': True,  'face': 'frente'},
    'servidor':      {'label': 'Servidor',          'altura': 2, 'cor': '#8b949e', 'icone': 'fa-server',         'portas': 4,  'profundidade_total': True,  'face': 'frente'},
    'radio':         {'label': 'Rádio / IDU',       'altura': 1, 'cor': '#ffa657', 'icone': 'fa-tower-broadcast','portas': 4,  'profundidade_total': True,  'face': 'frente'},
    'patch_panel':   {'label': 'Patch panel',       'altura': 1, 'cor': '#79c0ff', 'icone': 'fa-grip',           'portas': 24, 'profundidade_total': False, 'face': 'frente'},
    'dio':           {'label': 'DIO (fibra)',       'altura': 1, 'cor': '#2dd4bf', 'icone': 'fa-grip-lines',     'portas': 24, 'profundidade_total': False, 'face': 'frente'},
    'organizador':   {'label': 'Organizador',       'altura': 1, 'cor': '#57606a', 'icone': 'fa-bars-staggered', 'portas': 0,  'profundidade_total': False, 'face': 'frente'},
    'bandeja':       {'label': 'Bandeja',           'altura': 1, 'cor': '#6e7781', 'icone': 'fa-layer-group',    'portas': 0,  'profundidade_total': True,  'face': 'frente'},
    'pdu':           {'label': 'PDU / Régua',       'altura': 1, 'cor': '#d29922', 'icone': 'fa-plug',           'portas': 8,  'profundidade_total': False, 'face': 'traseira'},
    'fonte':         {'label': 'Fonte retificadora','altura': 2, 'cor': '#f0883e', 'icone': 'fa-bolt',           'portas': 0,  'profundidade_total': True,  'face': 'frente', 'modulos': 4},
    'nobreak':       {'label': 'Nobreak',           'altura': 2, 'cor': '#a371f7', 'icone': 'fa-car-battery',    'portas': 0,  'profundidade_total': True,  'face': 'frente'},
    'tampa':         {'label': 'Tampa cega',        'altura': 1, 'cor': '#30363d', 'icone': 'fa-square',         'portas': 0,  'profundidade_total': False, 'face': 'frente'},
    'outro':         {'label': 'Outro',             'altura': 1, 'cor': '#8b98a5', 'icone': 'fa-cube',           'portas': 0,  'profundidade_total': True,  'face': 'frente'},
}

# Ordem e agrupamento da paleta da tela.
GRUPOS = [
    ('Ativos de rede', ['router', 'switch', 'olt', 'firewall', 'cgnat', 'dwdm', 'radio']),
    ('Servidores', ['servidor']),
    ('Cabeamento', ['patch_panel', 'dio', 'organizador']),
    ('Energia e acessórios', ['fonte', 'pdu', 'nobreak', 'bandeja', 'tampa', 'outro']),
]

FACES = [('frente', 'Frente'), ('traseira', 'Traseira')]

# Tipo do node da topologia (TOPO_DEVICES em static/js/topo_engine.js) ->
# tipo do rack. Tipo ausente aqui não é equipamento de rack: Internet, IX,
# nuvem e VM são lógicos; texto/área/grupo são anotação do desenho.
TIPO_DA_TOPOLOGIA = {
    'router': 'router',
    'switch_l3': 'switch',
    'switch_l2': 'switch',
    'firewall': 'firewall',
    'cgnat': 'cgnat',
    'dwdm': 'dwdm',
    'olt': 'olt',
    'splitter': 'dio',
    'onu': 'outro',
    'cpe': 'outro',
    'radio': 'radio',
    'ap': 'outro',
    'server': 'servidor',
    'host': 'outro',
}

# Meio físico do cabo.
MEIOS = {
    'fibra_sm': {'label': 'Fibra monomodo', 'cor': '#e3b341'},
    'fibra_mm': {'label': 'Fibra multimodo', 'cor': '#2dd4bf'},
    'utp':      {'label': 'UTP (cobre)',     'cor': '#58a6ff'},
    'dac':      {'label': 'DAC (twinax)',    'cor': '#8b949e'},
    'aoc':      {'label': 'AOC',             'cor': '#bc8cff'},
    'coaxial':  {'label': 'Coaxial',         'cor': '#ffa657'},
    'energia':  {'label': 'Energia',         'cor': '#f85149'},
    'outro':    {'label': 'Outro',           'cor': '#8b98a5'},
}

CONECTORES = ['LC', 'SC', 'MPO', 'RJ45', 'SFP', 'SFP+', 'QSFP', 'F', 'Outro']

# Velocidade do link da topologia (TOPO_IFACES) -> cabo mais provável. É só o
# ponto de partida da conexão física: a tela abre com isso preenchido e a
# pessoa troca se o enlace for, por exemplo, um DAC de 10G.
MEIO_DA_IFACE = {
    '100m': ('utp', 'RJ45'),
    '1g': ('utp', 'RJ45'),
    '10g': ('fibra_sm', 'LC'),
    '20g': ('fibra_sm', 'LC'),
    '30g': ('fibra_sm', 'LC'),
    '40g': ('fibra_sm', 'LC'),
    '50g': ('fibra_sm', 'LC'),
    '100g': ('fibra_sm', 'LC'),
    'sfp': ('fibra_sm', 'LC'),
    'sfp+': ('fibra_sm', 'LC'),
    'gpon': ('fibra_sm', 'SC'),
    'xpon': ('fibra_sm', 'SC'),
    'mw': ('coaxial', 'F'),
}

# Fabricante -> nome como aparece na placa do equipamento, cor da marca e
# padrões (regex, sobre "fabricante modelo nome" em maiúsculas) que o
# reconhecem quando ninguém preencheu o fabricante: "S5730" é Huawei,
# "DM4370" é Datacom. A ordem importa — o primeiro que casar vence.
FABRICANTES = {
    'huawei':    {'label': 'HUAWEI',    'cor': '#e60012', 'padroes': [r'HUAWEI', r'\bS(2|3|5|6|7|12)7\d\d', r'\bNE ?\d{2,4}', r'\bCE ?\d{4}', r'\bMA ?56\d\d', r'\bMA ?58\d\d', r'\bEA ?58\d\d', r'\bETP ?\d', r'\bAR ?\d{3,4}\b']},
    'datacom':   {'label': 'DATACOM',   'cor': '#0b6fd3', 'padroes': [r'DATACOM', r'\bDM ?\d{3,4}']},
    'mikrotik':  {'label': 'MikroTik',  'cor': '#5f6f7a', 'padroes': [r'MIKROTIK', r'\bCCR ?\d', r'\bCRS ?\d', r'\bCSS ?\d', r'\bRB ?\d']},
    'juniper':   {'label': 'Juniper',   'cor': '#6b9a1f', 'padroes': [r'JUNIPER', r'\bMX ?\d', r'\bEX ?\d{4}', r'\bQFX ?\d', r'\bSRX ?\d', r'\bACX ?\d']},
    'cisco':     {'label': 'CISCO',     'cor': '#049fd9', 'padroes': [r'CISCO', r'\bASR ?\d', r'NEXUS', r'CATALYST', r'\bWS-C', r'\bISR ?\d']},
    'zte':       {'label': 'ZTE',       'cor': '#0068b7', 'padroes': [r'\bZTE\b', r'ZXA10', r'\bC(300|320|600|610|620|650)\b']},
    'fiberhome': {'label': 'FiberHome', 'cor': '#d4145a', 'padroes': [r'FIBER ?HOME', r'\bAN ?5516', r'\bAN ?6000']},
    'nokia':     {'label': 'NOKIA',     'cor': '#2f5fd0', 'padroes': [r'NOKIA', r'\bISAM\b', r'\b7750\b']},
    'intelbras': {'label': 'intelbras', 'cor': '#00a335', 'padroes': [r'INTELBRAS']},
    'parks':     {'label': 'Parks',     'cor': '#e07b00', 'padroes': [r'PARKS', r'FIBERLINK']},
    'vsol':      {'label': 'V-SOL',     'cor': '#e8590c', 'padroes': [r'\bV-?SOL\b']},
    'raisecom':  {'label': 'RAISECOM',  'cor': '#c8102e', 'padroes': [r'RAISECOM', r'\bRAX ?\d']},
    'tplink':    {'label': 'tp-link',   'cor': '#1aa5b8', 'padroes': [r'TP-?LINK', r'\bT(1600|1700|2600)G']},
    'ubiquiti':  {'label': 'UBIQUITI',  'cor': '#0559c9', 'padroes': [r'UBIQUITI?', r'EDGE ?SWITCH', r'EDGE ?ROUTER', r'UNIFI']},
    'cdata':     {'label': 'C-DATA',    'cor': '#0094d9', 'padroes': [r'\bC-?DATA\b']},
    'dell':      {'label': 'DELL',      'cor': '#007db8', 'padroes': [r'\bDELL\b', r'POWEREDGE']},
    'hpe':       {'label': 'HPE',       'cor': '#01a982', 'padroes': [r'\bHPE?\b', r'PROLIANT', r'ARUBA']},
    'a10':       {'label': 'A10',       'cor': '#5a9e2f', 'padroes': [r'\bA10\b', r'THUNDER']},
    'hillstone': {'label': 'Hillstone', 'cor': '#e4572e', 'padroes': [r'HILLSTONE']},
    'fortinet':  {'label': 'FORTINET',  'cor': '#da291c', 'padroes': [r'FORTINET', r'FORTIGATE']},
    'mimosa':    {'label': 'MIMOSA',    'cor': '#f7941d', 'padroes': [r'MIMOSA']},
    'eltek':     {'label': 'ELTEK',     'cor': '#d0021b', 'padroes': [r'ELTEK', r'FLATPACK']},
    'delta':     {'label': 'DELTA',     'cor': '#0060a9', 'padroes': [r'\bDELTA\b']},
    'vertiv':    {'label': 'VERTIV',    'cor': '#f26522', 'padroes': [r'VERTIV', r'EMERSON', r'NETSURE']},
    'furukawa':  {'label': 'Furukawa',  'cor': '#0057a8', 'padroes': [r'FURUKAWA']},
}

ALTURA_RACK_PADRAO = 42
ALTURA_RACK_MAX = 60
ALTURA_EQUIPAMENTO_MAX = 20


def tipo_do_node(tipo_topologia):
    """Tipo do rack para um node da topologia, ou None se não se monta."""
    return TIPO_DA_TOPOLOGIA.get(tipo_topologia)


def meio_da_iface(iface):
    return MEIO_DA_IFACE.get((iface or '').lower(), ('utp', 'RJ45'))


_FABRICANTES_RE = [(chave, [re.compile(p) for p in d['padroes']]) for chave, d in FABRICANTES.items()]


def _sem_acento(texto):
    return unicodedata.normalize('NFD', texto or '').encode('ascii', 'ignore').decode().upper()


def marca(fabricante='', *textos):
    """Placa da marca de um equipamento: {'chave', 'label', 'cor'} ou None.

    `fabricante` digitado vale primeiro (se for uma marca conhecida, ganha a
    grafia e a cor dela; senão aparece como foi escrito, em cinza). Sem
    fabricante, procura a marca pelos `textos` (modelo, nome)."""
    fab = (fabricante or '').strip()
    for alvo in ([fab] if fab else []) + [' '.join(t or '' for t in textos)]:
        texto = _sem_acento(alvo)
        if not texto.strip():
            continue
        for chave, padroes in _FABRICANTES_RE:
            if any(p.search(texto) for p in padroes):
                d = FABRICANTES[chave]
                return {'chave': chave, 'label': d['label'], 'cor': d['cor']}
        if alvo is fab:
            return {'chave': '', 'label': fab[:24], 'cor': '#6e7781'}
    return None


def catalogo_para_tela():
    return {
        'tipos': TIPOS,
        'grupos': [{'nome': nome, 'tipos': tipos} for nome, tipos in GRUPOS],
        'faces': dict(FACES),
        'meios': MEIOS,
        'conectores': CONECTORES,
        'fabricantes': [d['label'] for d in FABRICANTES.values()],
        'tipo_da_topologia': TIPO_DA_TOPOLOGIA,
        'meio_da_iface': {k: {'meio': m, 'conector': c} for k, (m, c) in MEIO_DA_IFACE.items()},
        'altura_rack_padrao': ALTURA_RACK_PADRAO,
        'altura_rack_max': ALTURA_RACK_MAX,
        'altura_equipamento_max': ALTURA_EQUIPAMENTO_MAX,
    }
