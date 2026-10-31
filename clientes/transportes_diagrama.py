"""Diagrama "Transportes: <cliente> x <empresa>" — imagem (PNG) e PDF gerados na
sub-aba Empresas de Documentação de Rede.

Funções puras sobre o que `l2vpn_empresa_detalhe` já monta (serviços L2VPN com
os peers resolvidos + enlaces da topologia): não toca DB nem equipamento. O
layout vira uma lista de primitivas e cada formato só desenha essa lista, então
PNG e PDF saem sempre iguais.
"""
import os
import re
from io import BytesIO

import reportlab
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# A partir de quantos destinos o site vira estrela (um host à esquerda ligado a
# todos); abaixo disso cada par de sites fica no seu próprio bloco.
MIN_DESTINOS_ESTRELA = 4

MARGEM = 30
ICONE_L, ICONE_A = 62, 38
ALTURA_LINHA = 18       # distância entre dois transportes do mesmo par
GAP_RAIA = 7            # distância entre as verticais da estrela
ESPACO_BLOCOS = 62
ALTURA_NOME = 20        # nome do host, abaixo do ícone
MAX_ALTURA_RASTER = 16000

COR_FUNDO = '#121212'
COR_MOLDURA = '#3a3a3a'
COR_TEXTO = '#f5f5f5'
COR_MUDO = '#9ca3af'
COR_ALERTA = '#fbbf24'
ESTILOS = {
    'vpls':   {'linha': '#4ade80', 'fundo': '#14351f', 'borda': '#2f7d4b'},
    'p2p':    {'linha': '#f9a8d4', 'fundo': '#3b2a12', 'borda': '#a16207'},
    'enlace': {'linha': '#60a5fa', 'fundo': '#14294a', 'borda': '#2563eb'},
}

_DIR_FONTES = os.path.join(os.path.dirname(reportlab.__file__), 'fonts')
# Vera vem junto com o reportlab: mesma fonte (e mesma medida) nos dois formatos
# sem depender de fonte instalada no servidor.
_FONTES = {
    False: ('TranspVera', os.path.join(_DIR_FONTES, 'Vera.ttf')),
    True: ('TranspVeraBd', os.path.join(_DIR_FONTES, 'VeraBd.ttf')),
}


def _registrar_fontes():
    for nome, caminho in _FONTES.values():
        if nome not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(nome, caminho))


def _largura(texto, tamanho, negrito=False):
    _registrar_fontes()
    return pdfmetrics.stringWidth(texto, _FONTES[negrito][0], tamanho)


def _ordem_id(valor):
    """IDs numéricos em ordem numérica; o resto depois, em ordem alfabética."""
    achado = re.search(r'\d+', str(valor or ''))
    return (0, int(achado.group()), str(valor)) if achado else (1, 0, str(valor or ''))


# ─── Dados: de transportes para pares de sites ───────────────────────────────

def _rotulo_servico(item):
    tecnologia = item.get('tecnologia') or (item.get('tipo') or '').upper() or 'L2VPN'
    identificacao = ' '.join(filter(None, [tecnologia, str(item.get('servico_id') or '')]))
    return ' - '.join(filter(None, [identificacao, item.get('nome') or '']))


def montar_ligacoes(transportes, enlaces):
    """Devolve `(ligacoes, sem_ponta)`.

    `ligacoes`: uma por par de hosts, `{a, b, nos: {id: nome}, linhas: [...]}`.
    O mesmo serviço marcado nos dois switches (par + ID iguais) vira uma linha
    só, mesmo com o nome diferente em cada ponta. `sem_ponta`: serviços que não
    dá para desenhar — sumiram do backup ou não têm peer."""
    pares, sem_ponta = {}, []

    def ligar(a, nome_a, b, nome_b, chave, rotulo, estilo, ordem):
        par = tuple(sorted((a, b)))
        lig = pares.setdefault(par, {'a': a, 'b': b, 'nos': {}, 'linhas': {}})
        lig['nos'].setdefault(a, nome_a)
        lig['nos'].setdefault(b, nome_b)
        lig['linhas'].setdefault(chave, {'rotulo': rotulo, 'estilo': estilo, 'ordem': ordem})

    def ordem_item(item):
        return (_ordem_id(item.get('servico_id')), item['acesso'].get('nome') or '')

    for item in sorted(transportes, key=ordem_item):
        origem = f"a:{item['acesso']['id']}"
        nome_origem = item['acesso'].get('nome') or item['acesso'].get('host') or '?'
        rotulo = _rotulo_servico(item)
        estilo = 'vpls' if item.get('tipo') == 'vpls' else 'p2p'
        ordem = _ordem_id(item.get('servico_id'))
        chave = f"s:{item.get('servico_id') or item.get('nome')}"
        if not item.get('encontrado'):
            sem_ponta.append((nome_origem, rotulo, 'não está mais no backup atual'))
            continue
        desenhou = False
        for peer in item.get('peers') or []:
            destino = peer.get('destino')
            if destino:
                alvo, nome_alvo = f"a:{destino['acesso_id']}", destino.get('nome') or peer.get('ip')
            else:
                alvo, nome_alvo = f"ip:{peer.get('ip')}", f"{peer.get('ip')} (não identificado)"
            if alvo == origem:
                continue
            ligar(origem, nome_origem, alvo, nome_alvo, chave, rotulo, estilo, ordem)
            desenhou = True
        if not desenhou:
            sem_ponta.append((nome_origem, rotulo, 'sem peer'))

    for enlace in enlaces:
        pontas = []
        for ponta in (enlace['a'], enlace['b']):
            nome = ponta.get('label') or ponta.get('ip_gerencia') or '?'
            pontas.append((f"a:{ponta['acesso_id']}" if ponta.get('acesso_id') else f'n:{nome.upper()}', nome))
        rotulo = ' - '.join(filter(None, [
            ' '.join(filter(None, ['Enlace', enlace.get('label') or enlace.get('iface') or ''])),
            enlace.get('circuito') or '',
        ]))
        ligar(pontas[0][0], pontas[0][1], pontas[1][0], pontas[1][1],
              f"e:{enlace['link_id']}", rotulo, 'enlace', (2, 0, rotulo))

    ligacoes = list(pares.values())
    for lig in ligacoes:
        lig['linhas'] = sorted(lig['linhas'].values(), key=lambda l: l['ordem'])
    return ligacoes, sem_ponta


def agrupar_blocos(ligacoes):
    """Separa em estrelas (site com `MIN_DESTINOS_ESTRELA`+ destinos) e pares
    soltos, na ordem dos IDs. Um site pode repetir em vários blocos — o bloco
    é a leitura de um trecho, não a topologia inteira."""
    restantes = list(ligacoes)
    blocos = []
    while True:
        destinos = {}
        for lig in restantes:
            destinos.setdefault(lig['a'], []).append(lig)
            destinos.setdefault(lig['b'], []).append(lig)
        if not destinos:
            break
        centro, ligs = max(destinos.items(), key=lambda par: (len(par[1]), par[0]))
        if len(ligs) < MIN_DESTINOS_ESTRELA:
            break
        ligs = sorted(ligs, key=lambda l: l['linhas'][0]['ordem'])
        blocos.append({'centro': centro, 'nome': ligs[0]['nos'][centro], 'ligacoes': ligs})
        restantes = [l for l in restantes if l not in ligs]
    blocos.extend({'centro': lig['a'], 'nome': lig['nos'][lig['a']], 'ligacoes': [lig]} for lig in restantes)
    blocos.sort(key=lambda b: b['ligacoes'][0]['linhas'][0]['ordem'])
    return blocos


# ─── Layout ──────────────────────────────────────────────────────────────────

class _Desenho:
    def __init__(self):
        self.ops = []
        self.largura = self.altura = 0

    def linha(self, pontos, cor, largura=1.0, tracejada=False):
        self.ops.append(('linha', pontos, cor, largura, tracejada))

    def texto(self, x, y, texto, cor=COR_TEXTO, tamanho=8, negrito=False, alinhar='c'):
        self.ops.append(('texto', x, y, texto, cor, tamanho, negrito, alinhar))

    def caixa(self, x, y, l, a, fundo=None, borda=None, raio=0):
        self.ops.append(('caixa', x, y, l, a, fundo, borda, raio))

    def elipse(self, cx, cy, rx, ry, fundo=None, borda=None):
        self.ops.append(('elipse', cx, cy, rx, ry, fundo, borda))


def _icone_roteador(d, cx, cy):
    rx, ry = ICONE_L / 2, 10
    topo, base = cy - 8, cy + 8
    d.elipse(cx, base, rx, ry, fundo='#1d6fc4')
    d.caixa(cx - rx, topo, ICONE_L, base - topo, fundo='#2f8fe6')
    d.elipse(cx, topo, rx, ry, fundo='#7cc4f8')
    for sx, sy in ((-1, -1), (1, 1), (-1, 1), (1, -1)):       # setas de roteador sobre o tampo
        d.linha([(cx + sx * 6, topo + sy * 2), (cx + sx * 20, topo + sy * 6)], '#ffffff', 1.8)


def _no(d, cx, cy, nome):
    _icone_roteador(d, cx, cy)
    d.texto(cx, cy + ICONE_A / 2 + 12, nome, negrito=True)


def _rotulo(d, x1, x2, y, linha):
    estilo = ESTILOS[linha['estilo']]
    largura = _largura(linha['rotulo'], 7, True) + 10
    cx = (x1 + x2) / 2
    d.caixa(cx - largura / 2, y - 6.5, largura, 13, fundo=estilo['fundo'], borda=estilo['borda'], raio=1.5)
    d.texto(cx, y, linha['rotulo'], tamanho=7, negrito=True)


def _ponta(d, x, y, cor):
    d.elipse(x, y, 2.4, 2.4, fundo=COR_FUNDO, borda=cor)


def _altura_par(lig):
    return max(ICONE_A, ALTURA_LINHA * len(lig['linhas'])) + ALTURA_NOME


def _desenhar_par(d, lig, y, x_esq, x_dir):
    n = len(lig['linhas'])
    cy = y + max(ICONE_A, ALTURA_LINHA * n) / 2
    _no(d, x_esq, cy, lig['nos'][lig['a']])
    _no(d, x_dir, cy, lig['nos'][lig['b']])
    for i, linha in enumerate(lig['linhas']):
        ly = cy + (i - (n - 1) / 2) * ALTURA_LINHA
        x1, x2 = x_esq + ICONE_L / 2 + 2, x_dir - ICONE_L / 2 - 2
        cor = ESTILOS[linha['estilo']]['linha']
        d.linha([(x1, ly), (x2, ly)], cor, tracejada=True)
        _ponta(d, x2, ly, cor)
        _rotulo(d, x1, x2, ly, linha)
    return _altura_par(lig)


def _destinos_estrela(bloco):
    """Destinos da estrela de cima para baixo, com as linhas de cada um."""
    return [(lig['nos'][lig['b'] if lig['a'] == bloco['centro'] else lig['a']], lig['linhas'])
            for lig in bloco['ligacoes']]


def _altura_estrela(bloco):
    return sum(max(ICONE_A, ALTURA_LINHA * len(linhas)) + ALTURA_NOME + 14
               for _nome, linhas in _destinos_estrela(bloco)) - 14


def _raias_estrela(bloco):
    return sum(len(linhas) for _nome, linhas in _destinos_estrela(bloco))


def _desenhar_estrela(d, bloco, y, x_esq, x_dir):
    """Host à esquerda, destinos empilhados à direita. Cada linha tem uma
    vertical própria; as que sobem (ou descem) para mais longe ficam mais perto
    do host, senão a saída de uma cruzaria a vertical da outra."""
    altura = _altura_estrela(bloco)
    cy_centro = y + altura / 2 - ALTURA_NOME / 2
    trajetos = []
    cursor = y
    for nome, linhas in _destinos_estrela(bloco):
        corpo = max(ICONE_A, ALTURA_LINHA * len(linhas))
        cy = cursor + corpo / 2
        _no(d, x_dir, cy, nome)
        for i, linha in enumerate(linhas):
            trajetos.append((cy + (i - (len(linhas) - 1) / 2) * ALTURA_LINHA, linha))
        cursor += corpo + ALTURA_NOME + 14
    _no(d, x_esq, cy_centro, bloco['nome'])

    n = len(trajetos)
    passo = min(4.0, (ICONE_A - 8) / max(n - 1, 1))
    saidas = [cy_centro + (i - (n - 1) / 2) * passo for i in range(n)]
    x0 = x_esq + ICONE_L / 2 + 2
    sobem = [i for i in range(n) if trajetos[i][0] < saidas[i]]
    descem = [i for i in reversed(range(n)) if i not in sobem]
    raia = {}
    for grupo in (sobem, descem):
        for posicao, i in enumerate(grupo):
            raia[i] = x0 + 10 + posicao * GAP_RAIA
    fim_raias = x0 + 10 + max(len(sobem), len(descem)) * GAP_RAIA
    x2 = x_dir - ICONE_L / 2 - 2
    for i, (ly, linha) in enumerate(trajetos):
        cor = ESTILOS[linha['estilo']]['linha']
        d.linha([(x0, saidas[i]), (raia[i], saidas[i]), (raia[i], ly), (x2, ly)], cor, tracejada=True)
        _ponta(d, x2, ly, cor)
        _rotulo(d, fim_raias, x2, ly, linha)
    return altura


def montar_desenho(titulo, transportes, enlaces, rodape=''):
    ligacoes, sem_ponta = montar_ligacoes(transportes, enlaces)
    blocos = agrupar_blocos(ligacoes)

    nomes, rotulos, raias = [], [], 0
    for bloco in blocos:
        for lig in bloco['ligacoes']:
            nomes.extend(lig['nos'].values())
            rotulos.extend(linha['rotulo'] for linha in lig['linhas'])
        if len(bloco['ligacoes']) >= MIN_DESTINOS_ESTRELA:
            raias = max(raias, _raias_estrela(bloco))
    col_no = max([ICONE_L] + [_largura(n, 8, True) + 8 for n in nomes])
    miolo = max([190] + [_largura(r, 7, True) + 50 for r in rotulos]) + (raias // 2 + 2) * GAP_RAIA
    largura = MARGEM * 2 + col_no * 2 + miolo
    largura = max(largura, _largura(titulo, 15, True) + MARGEM * 2,
                  *[MARGEM * 2 + _largura(f'{n} — {r}: {m}', 7.5) for n, r, m in sem_ponta] or [0])
    x_esq = MARGEM + col_no / 2
    x_dir = largura - MARGEM - col_no / 2

    d = _Desenho()
    corpo = _Desenho()      # desenhado antes para saber a altura da moldura
    y = 72
    for bloco in blocos:
        if len(bloco['ligacoes']) >= MIN_DESTINOS_ESTRELA:
            y += _desenhar_estrela(corpo, bloco, y, x_esq, x_dir)
        else:
            y += _desenhar_par(corpo, bloco['ligacoes'][0], y, x_esq, x_dir)
        y += ESPACO_BLOCOS
    if not blocos and not sem_ponta:
        corpo.texto(largura / 2, y, 'Nenhum transporte associado a esta empresa.', cor=COR_MUDO, tamanho=9)
        y += 40
    if sem_ponta:
        corpo.texto(MARGEM, y, 'Sem a outra ponta (fora do desenho)', cor=COR_ALERTA, tamanho=8, negrito=True, alinhar='e')
        y += 15
        for nome, rotulo, motivo in sem_ponta:
            corpo.texto(MARGEM, y, f'{nome} — {rotulo}: {motivo}', cor=COR_MUDO, tamanho=7.5, alinhar='e')
            y += 13
        y += 14
    altura = y + 14

    d.largura, d.altura = largura, altura
    d.caixa(0, 0, largura, altura, fundo=COR_FUNDO)
    d.caixa(3, 3, largura - 6, altura - 6, borda=COR_MOLDURA, raio=8)
    d.texto(largura / 2, 30, titulo, tamanho=15, negrito=True)
    d.ops.extend(corpo.ops)
    if rodape:
        d.texto(largura / 2, altura - 14, rodape, cor=COR_MUDO, tamanho=6.5)
    return d


# ─── Saída ───────────────────────────────────────────────────────────────────

def _desenhar_pil(desenho):
    # Desenha maior e reduz no fim: o ImageDraw não suaviza linha nem elipse.
    final = min(2.0, MAX_ALTURA_RASTER / desenho.altura)
    e = final * 2
    img = Image.new('RGB', (round(desenho.largura * e), round(desenho.altura * e)), COR_FUNDO)
    tela = ImageDraw.Draw(img)
    fontes = {}

    def fonte(tamanho, negrito):
        chave = (tamanho, negrito)
        if chave not in fontes:
            fontes[chave] = ImageFont.truetype(_FONTES[negrito][1], tamanho * e)
        return fontes[chave]

    for op in desenho.ops:
        tipo = op[0]
        if tipo == 'linha':
            _, pontos, cor, largura, tracejada = op
            espessura = max(1, round(largura * e))
            for (x1, y1), (x2, y2) in zip(pontos, pontos[1:]):
                if tracejada:
                    comprimento = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** .5
                    passos = int(comprimento // 6)
                    for k in range(passos + 1):
                        t1, t2 = k * 6 / (comprimento or 1), min(1, (k * 6 + 4) / (comprimento or 1))
                        tela.line([(x1 + (x2 - x1) * t1) * e, (y1 + (y2 - y1) * t1) * e,
                                   (x1 + (x2 - x1) * t2) * e, (y1 + (y2 - y1) * t2) * e], fill=cor, width=espessura)
                else:
                    tela.line([x1 * e, y1 * e, x2 * e, y2 * e], fill=cor, width=espessura)
        elif tipo == 'texto':
            _, x, y, texto, cor, tamanho, negrito, alinhar = op
            ancora = 'mm' if alinhar == 'c' else 'lm'
            tela.text((x * e, y * e), texto, fill=cor, font=fonte(tamanho, negrito), anchor=ancora)
        elif tipo == 'caixa':
            _, x, y, l, a, fundo, borda, raio = op
            tela.rounded_rectangle([x * e, y * e, (x + l) * e, (y + a) * e], radius=raio * e,
                                   fill=fundo, outline=borda, width=max(1, round(e * .7)))
        elif tipo == 'elipse':
            _, cx, cy, rx, ry, fundo, borda = op
            tela.ellipse([(cx - rx) * e, (cy - ry) * e, (cx + rx) * e, (cy + ry) * e],
                         fill=fundo, outline=borda, width=max(1, round(e * .7)))
    return img.resize((round(desenho.largura * final), round(desenho.altura * final)), Image.LANCZOS)


def gerar_png(titulo, transportes, enlaces, rodape=''):
    img = _desenhar_pil(montar_desenho(titulo, transportes, enlaces, rodape))
    saida = BytesIO()
    img.save(saida, format='PNG', optimize=True)
    return saida.getvalue()


def gerar_pdf(titulo, transportes, enlaces, rodape=''):
    """Uma página só, do tamanho do desenho, como a imagem. Texto continua
    selecionável (dá para copiar o ID do serviço)."""
    _registrar_fontes()
    desenho = montar_desenho(titulo, transportes, enlaces, rodape)
    saida = BytesIO()
    pdf = canvas.Canvas(saida, pagesize=(desenho.largura, desenho.altura))
    pdf.setTitle(titulo)
    H = desenho.altura

    for op in desenho.ops:
        tipo = op[0]
        if tipo == 'linha':
            _, pontos, cor, largura, tracejada = op
            pdf.setStrokeColor(cor)
            pdf.setLineWidth(largura)
            pdf.setDash(4, 2) if tracejada else pdf.setDash()
            caminho = pdf.beginPath()
            caminho.moveTo(pontos[0][0], H - pontos[0][1])
            for x, y in pontos[1:]:
                caminho.lineTo(x, H - y)
            pdf.drawPath(caminho, stroke=1, fill=0)
            pdf.setDash()
        elif tipo == 'texto':
            _, x, y, texto, cor, tamanho, negrito, alinhar = op
            pdf.setFillColor(cor)
            pdf.setFont(_FONTES[negrito][0], tamanho)
            base = H - y - tamanho * .35
            pdf.drawCentredString(x, base, texto) if alinhar == 'c' else pdf.drawString(x, base, texto)
        elif tipo == 'caixa':
            _, x, y, l, a, fundo, borda, raio = op
            if fundo:
                pdf.setFillColor(fundo)
            if borda:
                pdf.setStrokeColor(borda)
                pdf.setLineWidth(.7)
            pdf.roundRect(x, H - y - a, l, a, raio, stroke=1 if borda else 0, fill=1 if fundo else 0)
        elif tipo == 'elipse':
            _, cx, cy, rx, ry, fundo, borda = op
            if fundo:
                pdf.setFillColor(fundo)
            if borda:
                pdf.setStrokeColor(borda)
                pdf.setLineWidth(.7)
            pdf.ellipse(cx - rx, H - cy - ry, cx + rx, H - cy + ry, stroke=1 if borda else 0, fill=1 if fundo else 0)
    pdf.showPage()
    pdf.save()
    return saida.getvalue()
