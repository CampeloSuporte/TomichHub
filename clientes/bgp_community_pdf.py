"""
clientes/bgp_community_pdf.py
PDF "Padrão de communities" da automação BGP: a partir do mapa descoberto por
`bgp_community_auto.montar_mapa`, lista cada community da caixa dizendo a QUEM
ela pertence (circuito / grupo global) e o que ela APLICA (anunciar, prepend,
no-export, blackhole, bloquear, origem), mais quais communities cada prefixo
originado carrega hoje.

Função pura sobre o mapa — não toca DB nem equipamento; quem busca o snapshot
e devolve a resposta HTTP é `bgp_views.bgp_community_pdf`.
"""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from .bgp_community_auto import ACOES, ACOES_POR_CHAVE, TIPOS_CIRCUITO

COR_ACCENT = colors.HexColor('#1d4ed8')
COR_LINHA_PAR = colors.HexColor('#f1f5f9')
COR_GRADE = colors.HexColor('#e2e8f0')
COR_ALERTA = colors.HexColor('#b45309')

_ORDEM_ACAO = {a['chave']: i for i, a in enumerate(ACOES)}
_ORDEM_TIPO = {t['chave']: i for i, t in enumerate(TIPOS_CIRCUITO)}


def _e(valor):
    return escape(str(valor if valor not in (None, '') else '—'))


def _o_que_aplica(chave, prepend_as=''):
    """Efeito da ação na policy de saída do circuito, em português."""
    acao = ACOES_POR_CHAVE.get(chave) or {}
    n = acao.get('prepend') or 0
    if n:
        as_txt = f' do AS {prepend_as}' if prepend_as else ''
        return f'Anuncia com {n} prepend{"s" if n > 1 else ""}{as_txt} (+{n} AS no as-path)'
    return {
        'export': 'Anuncia o prefixo normalmente',
        'export-ne': 'Anuncia com no-export — o peer recebe, mas não repassa adiante',
        'export-df': 'Anuncia (default/específico) — ação legada, não gerada em config nova',
        'export-bh': 'Blackhole — sinaliza ao circuito para descartar o tráfego destinado ao prefixo',
        'export-bl': 'Bloqueia o anúncio do prefixo neste circuito (deny)',
        'import-rr': 'Carimbada pela policy IN nas rotas RECEBIDAS deste circuito (marca a origem)',
    }.get(chave, acao.get('rotulo', chave))


def _o_que_aplica_global(a):
    """Efeito de uma ação de grupo global, a partir do alcance real descoberto."""
    if a.get('deny'):
        base = 'Bloqueia o anúncio'
    elif a.get('no_export'):
        base = 'Anuncia com no-export'
    else:
        base = 'Anuncia'
    prepends = sorted(set(a.get('prepends') or []))
    if prepends and prepends != [0]:
        base += ' com ' + '/'.join(str(p) for p in prepends) + ' prepend(s)'
    circ = a.get('circuitos') or []
    if circ:
        return f'{base} para {len(circ)} circuito(s): {", ".join(circ)}'
    return f'{base} — nenhuma policy de saída casa esta community hoje (sem efeito)'


def _estilos():
    s = getSampleStyleSheet()
    return {
        'titulo': ParagraphStyle('t', parent=s['Title'], fontSize=16,
                                 textColor=colors.HexColor('#0d1829'), spaceAfter=4),
        'sub': ParagraphStyle('s', parent=s['Normal'], fontSize=9,
                              textColor=colors.HexColor('#64748b'), spaceAfter=10),
        'h2': ParagraphStyle('h2', parent=s['Heading2'], fontSize=12,
                             textColor=COR_ACCENT, spaceBefore=10, spaceAfter=4),
        'h3': ParagraphStyle('h3', parent=s['Heading3'], fontSize=10,
                             textColor=colors.HexColor('#0d1829'), spaceBefore=8, spaceAfter=2),
        'txt': ParagraphStyle('x', parent=s['Normal'], fontSize=8.5, leading=11),
        'cell': ParagraphStyle('c', parent=s['Normal'], fontSize=8, leading=10),
        'mono': ParagraphStyle('m', parent=s['Normal'], fontSize=8, leading=10, fontName='Courier'),
        'alerta': ParagraphStyle('a', parent=s['Normal'], fontSize=8, leading=10, textColor=COR_ALERTA),
    }


def _tabela(dados, larguras):
    t = Table(dados, colWidths=larguras, repeatRows=1, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND',     (0, 0), (-1, 0), COR_ACCENT),
        ('TEXTCOLOR',      (0, 0), (-1, 0), colors.white),
        ('FONTNAME',       (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',       (0, 0), (-1, -1), 8),
        ('VALIGN',         (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',     (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING',  (0, 0), (-1, -1), 3),
        ('LEFTPADDING',    (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',   (0, 0), (-1, -1), 4),
        ('GRID',           (0, 0), (-1, -1), 0.4, COR_GRADE),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, COR_LINHA_PAR]),
    ]))
    return t


def _nome_circuito(c):
    partes = [c.get('nome') or '']
    if c.get('peer_as'):
        partes.append(f'AS{c["peer_as"]}')
    return ' — '.join(p for p in partes if p) or 'sem sessão configurada'


def _ordenar_circuitos(circuitos):
    return sorted(circuitos.values(),
                  key=lambda c: (_ORDEM_TIPO.get(c.get('tipo'), 9), c['id'].split('-')[0],
                                 int(c.get('numero') or 0)))


def gerar_pdf_communities(mapa, titulo_host, gerado_em):
    """Monta o PDF e devolve os bytes. `mapa` é a saída de `montar_mapa`."""
    st = _estilos()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), title='Padrão de communities BGP',
                            leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    el = []
    circuitos = mapa.get('circuitos') or {}
    globais = mapa.get('globais') or {}
    asn = (mapa.get('padroes') or {}).get('asn_community') or ''

    # ── Cabeçalho ─────────────────────────────────────────────────────────────
    el.append(Paragraph('Padrão de communities BGP', st['titulo']))
    el.append(Paragraph(
        f'{_e(titulo_host)} &nbsp;|&nbsp; AS local: {_e(mapa.get("as_local"))} &nbsp;|&nbsp; '
        f'ASN de community: {_e(asn)} &nbsp;|&nbsp; Gerado em: {gerado_em:%d/%m/%Y %H:%M}',
        st['sub']))

    # ── Como ler ──────────────────────────────────────────────────────────────
    el.append(Paragraph('Como ler uma community', st['h2']))
    el.append(Paragraph(
        'Formato <font name="Courier">&lt;ASN&gt;:&lt;grupo&gt;&lt;ação&gt;</font> — ex.: '
        f'<font name="Courier">{_e(asn or "65100")}:50203</font> = grupo <b>502</b> (circuito c-02) + '
        'ação <b>03</b> (anunciar com 2 prepends). A route-policy LOCAL do prefixo carimba a '
        'community; a route-policy de SAÍDA de cada circuito traduz a community no comportamento.',
        st['txt']))
    el.append(Spacer(1, 0.2 * cm))

    linhas = [['Faixa de grupo', 'Tipo de circuito', 'Prefixo do filtro', 'Global "para todos"']]
    for t in TIPOS_CIRCUITO:
        linhas.append([
            f'{t["base_grupo"] + 1}–{t["base_grupo"] + t["slots"]}', t['rotulo'],
            f'{t["prefixo"]}-NN', f'glob-{t["glob_slug"]} ({t["glob_community"]})',
        ])
    el.append(_tabela(linhas, [4 * cm, 6 * cm, 4 * cm, 8 * cm]))
    el.append(Spacer(1, 0.3 * cm))

    linhas = [['Sufixo', 'Ação', 'O que aplica']]
    for a in ACOES:
        linhas.append([a['sufixo'], Paragraph(_e(a['rotulo']), st['cell']),
                       Paragraph(_e(_o_que_aplica(a['chave'])), st['cell'])])
    el.append(_tabela(linhas, [2 * cm, 6 * cm, 18.7 * cm]))

    # ── Circuitos ─────────────────────────────────────────────────────────────
    el.append(PageBreak())
    el.append(Paragraph(f'Communities por circuito ({len(circuitos)})', st['h2']))
    for c in _ordenar_circuitos(circuitos):
        cab = [Paragraph(
            f'{_e(c["id"])} · {_e(c.get("tipo_rotulo"))} · grupo {_e(c.get("grupo"))} · '
            f'{_e(_nome_circuito(c))}', st['h3'])]
        sessoes = c.get('sessoes') or []
        if sessoes:
            cab.append(Paragraph('Sessões: ' + ', '.join(
                f'{_e(s.get("peer_ip"))} ({_e(s.get("descricao") or s.get("familia"))}'
                f'{"" if s.get("habilitada", True) else ", desativada"})' for s in sessoes),
                st['cell']))
        linhas = [['Community', 'Ação', 'O que aplica', 'Community-filter', 'Situação']]
        for chave in sorted(c.get('acoes') or {}, key=lambda k: _ORDEM_ACAO.get(k, 99)):
            a = c['acoes'][chave]
            comms = [a.get('community')] + list(a.get('multiplos') or [])
            if a.get('conforme', True):
                situacao = Paragraph('OK', st['cell'])
            else:
                situacao = Paragraph(f'Fora do padrão (esperado {_e(a.get("esperada"))})', st['alerta'])
            linhas.append([
                Paragraph(_e(' '.join(x for x in comms if x)), st['mono']),
                Paragraph(_e((ACOES_POR_CHAVE.get(chave) or {}).get('rotulo', chave)), st['cell']),
                Paragraph(_e(_o_que_aplica(chave, c.get('prepend_as'))), st['cell']),
                Paragraph(_e(a.get('filtro')), st['mono']),
                situacao,
            ])
        if c.get('faltando'):
            cab.append(Paragraph('Ações do padrão ausentes: ' + _e(', '.join(c['faltando'])), st['alerta']))
        el.append(KeepTogether(cab + [Spacer(1, 0.1 * cm),
                                      _tabela(linhas, [3.6 * cm, 4.2 * cm, 10.3 * cm, 4.6 * cm, 4 * cm])]))

    # ── Globais ───────────────────────────────────────────────────────────────
    if globais:
        el.append(PageBreak())
        el.append(Paragraph(f'Grupos globais — "anunciar para todos" ({len(globais)})', st['h2']))
        linhas = [['Community', 'Grupo', 'Ação', 'O que aplica', 'Community-filter']]
        for g in sorted(globais.values(), key=lambda g: g['id']):
            for chave in sorted(g.get('acoes') or {}, key=lambda k: _ORDEM_ACAO.get(k, 99)):
                a = g['acoes'][chave]
                comms = [a.get('community')] + list(a.get('multiplos') or [])
                linhas.append([
                    Paragraph(_e(' '.join(x for x in comms if x)), st['mono']),
                    Paragraph(_e(g.get('rotulo') or g['id']), st['cell']),
                    Paragraph(_e((ACOES_POR_CHAVE.get(chave) or {}).get('rotulo', chave)), st['cell']),
                    Paragraph(_e(_o_que_aplica_global(a)), st['cell']),
                    Paragraph(_e(a.get('filtro')), st['mono']),
                ])
        el.append(_tabela(linhas, [3.8 * cm, 4.2 * cm, 4 * cm, 10.2 * cm, 4.5 * cm]))

    # ── Prefixos ──────────────────────────────────────────────────────────────
    anuncios = [a for a in (mapa.get('anuncios') or [])
                if a.get('destinos') or a.get('globais') or a.get('communities_orfas')]
    if anuncios:
        el.append(PageBreak())
        el.append(Paragraph(f'Communities aplicadas por prefixo ({len(anuncios)})', st['h2']))
        linhas = [['Prefixo', 'Route-policy local', 'Community', 'Pertence a', 'Aplica']]
        for an in anuncios:
            itens = []
            for dest, chave in sorted((an.get('destinos') or {}).items()):
                c = circuitos.get(dest) or {}
                comm = ((c.get('acoes') or {}).get(chave) or {}).get('community', '')
                itens.append((comm, f'{dest} ({_nome_circuito(c)})' if c else dest,
                               _o_que_aplica(chave, c.get('prepend_as')), False))
            for dest, chave in sorted((an.get('globais') or {}).items()):
                g = globais.get(dest) or {}
                a = (g.get('acoes') or {}).get(chave) or {}
                itens.append((a.get('community', ''), g.get('rotulo') or dest,
                              _o_que_aplica_global(a) if a else chave, False))
            for o in an.get('communities_orfas') or []:
                motivo = (f'ASN diferente do filtro — provável {o["sugestao"]} ({o["destino"]})'
                          if o.get('tipo') == 'asn' else 'Não casa nenhum community-filter')
                itens.append((o.get('valor', ''), '—', f'Sem efeito: {motivo}', True))
            for i, (comm, dono, aplica, alerta) in enumerate(itens):
                linhas.append([
                    Paragraph(_e(an['prefixo']) if i == 0 else '', st['mono']),
                    Paragraph(_e(an.get('route_policy')) if i == 0 else '', st['mono']),
                    Paragraph(_e(comm), st['mono']),
                    Paragraph(_e(dono), st['cell']),
                    Paragraph(_e(aplica), st['alerta' if alerta else 'cell']),
                ])
        el.append(_tabela(linhas, [3.8 * cm, 4.4 * cm, 3.4 * cm, 6.3 * cm, 8.8 * cm]))

    def _rodape(canvas, d):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#64748b'))
        canvas.drawString(1.5 * cm, 0.8 * cm, f'Padrão de communities BGP — {titulo_host}')
        canvas.drawRightString(d.pagesize[0] - 1.5 * cm, 0.8 * cm, f'Página {d.page}')
        canvas.restoreState()

    doc.build(el, onFirstPage=_rodape, onLaterPages=_rodape)
    return buf.getvalue()
