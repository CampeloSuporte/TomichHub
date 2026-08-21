"""
monitoramento/chart.py
Renderização de gráficos de série temporal em PNG usando apenas Pillow.

Usado pelo Agent NOC para responder pedidos do tipo "me traga o histórico do
tráfego do link X" com uma imagem, tanto no WhatsApp (sendMedia) quanto no
chat do terminal web (data URI).

Não usa matplotlib de propósito: o projeto já tem Pillow instalado e o gráfico
aqui é simples (linhas + eixos + legenda), não vale puxar numpy/matplotlib só
para isso.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone as _dt_timezone

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Paleta (fundo claro — legível no WhatsApp e no chat do terminal)
_BG        = (255, 255, 255)
_FG        = (33, 37, 41)
_MUTED     = (120, 128, 138)
_GRID      = (228, 232, 237)
_EIXO      = (170, 178, 188)
_MARCADOR  = (220, 53, 69)

_CORES = [
    (13, 110, 253),   # azul   — normalmente "entrada"
    (25, 135, 84),    # verde  — normalmente "saída"
    (255, 143, 0),    # laranja
    (111, 66, 193),   # roxo
    (13, 202, 240),   # ciano
    (214, 51, 132),   # rosa
]

_FONT_DIRS = (
    '/usr/share/fonts/truetype/dejavu',
    '/usr/share/fonts/truetype/liberation',
)


def _font(tamanho: int, bold: bool = False):
    nomes = (
        ('DejaVuSans-Bold.ttf', 'LiberationSans-Bold.ttf') if bold
        else ('DejaVuSans.ttf', 'LiberationSans-Regular.ttf')
    )
    for d in _FONT_DIRS:
        for n in nomes:
            try:
                return ImageFont.truetype(f'{d}/{n}', tamanho)
            except Exception:
                continue
    return ImageFont.load_default()


# ──────────────────────────────────────────────────────────────
# Formatação de valores
# ──────────────────────────────────────────────────────────────

def formatar_valor(valor: float, units: str = '') -> str:
    """Formata um valor com a unidade do item Zabbix (bps, dBm, %, °C...)."""
    u = (units or '').strip()
    ul = u.lower()

    if valor is None:
        return '—'

    # Taxas e volumes — escala binária/decimal com prefixo
    if ul in ('bps', 'b/s', 'bits/s', 'bits/sec') or ul.endswith('bps'):
        return _escala(valor, 'bps', 1000)
    if ul in ('b', 'bytes'):
        return _escala(valor, 'B', 1024)
    if ul in ('pps', 'p/s'):
        return _escala(valor, 'pps', 1000)

    if ul in ('dbm', 'db'):
        return f'{valor:.2f} {u}'
    if ul == '%':
        return f'{valor:.2f}%'
    if ul:
        if abs(valor) >= 1000:
            return f'{_escala(valor, "", 1000)} {u}'.replace('  ', ' ')
        return f'{valor:.2f} {u}'

    if abs(valor) >= 1000:
        return _escala(valor, '', 1000)
    if abs(valor) >= 10:
        return f'{valor:.1f}'
    return f'{valor:.3f}'.rstrip('0').rstrip('.')


def _escala(valor: float, sufixo: str, base: int) -> str:
    prefixos = ['', 'k', 'M', 'G', 'T', 'P'] if base == 1000 else ['', 'Ki', 'Mi', 'Gi', 'Ti']
    v = float(valor)
    neg = v < 0
    v = abs(v)
    i = 0
    while v >= base and i < len(prefixos) - 1:
        v /= base
        i += 1
    if neg:
        v = -v
    txt = f'{v:.2f}'.rstrip('0').rstrip('.') if v < 100 else f'{v:.0f}'
    return f'{txt} {prefixos[i]}{sufixo}'.strip()


def _dt_local(ts: int) -> datetime:
    """Epoch → datetime no fuso do Django (America/Sao_Paulo), independente do
    fuso do sistema onde o daphne está rodando."""
    dt = datetime.fromtimestamp(ts, tz=_dt_timezone.utc)
    try:
        from django.utils import timezone as dj_tz
        return dj_tz.localtime(dt)
    except Exception:
        return dt.astimezone()


def _fmt_hora(ts: int, span_seg: int) -> str:
    dt = _dt_local(ts)
    if span_seg <= 86400:
        return dt.strftime('%H:%M')
    if span_seg <= 7 * 86400:
        return dt.strftime('%d/%m %Hh')
    return dt.strftime('%d/%m')


def _texto_largura(draw, texto, fonte) -> int:
    try:
        box = draw.textbbox((0, 0), texto, font=fonte)
        return box[2] - box[0]
    except Exception:
        return len(texto) * 6


# ──────────────────────────────────────────────────────────────
# Renderização
# ──────────────────────────────────────────────────────────────

def render_series_png(series: list[dict], titulo: str = '', subtitulo: str = '',
                      units: str = '', marcador_ts: int | None = None,
                      largura: int = 960, altura: int = 480) -> bytes:
    """
    Renderiza um gráfico de linhas em PNG.

    series      : [{'nome': str, 'pontos': [{'t': epoch, 'v': float}, ...], 'units': str}]
    titulo      : título no topo
    subtitulo   : linha menor abaixo do título (ex: período consultado)
    units       : unidade dominante (usada no eixo Y quando as séries compartilham)
    marcador_ts : timestamp epoch para desenhar uma linha vertical tracejada
                  (ex: momento do rompimento) — opcional
    """
    series = [s for s in (series or []) if s.get('pontos')]
    if not series:
        raise ValueError('Nenhuma série com pontos para plotar')

    f_titulo = _font(18, bold=True)
    f_sub    = _font(12)
    f_eixo   = _font(11)
    f_leg    = _font(12)

    m_esq, m_dir = 92, 24
    m_topo = 62 if titulo else 20
    if subtitulo:
        m_topo += 6

    # Legenda: monta e quebra em linhas ANTES de fixar a altura da imagem —
    # senão a segunda linha de legenda fica cortada fora do PNG.
    medidor = ImageDraw.Draw(Image.new('RGB', (10, 10)))
    legendas = []
    for idx, s_ in enumerate(series):
        vals = [p['v'] for p in s_['pontos']]
        u    = s_.get('units', units)
        txt  = (
            f"{s_.get('nome', 'série')}  "
            f"min {formatar_valor(min(vals), u)} · "
            f"méd {formatar_valor(sum(vals) / len(vals), u)} · "
            f"máx {formatar_valor(max(vals), u)}"
        )
        legendas.append({
            'texto': txt,
            'cor':   s_.get('cor') or _CORES[idx % len(_CORES)],
            'largura': _texto_largura(medidor, txt, f_leg) + 30,
        })

    linhas_legenda = []
    atual, usado = [], 0
    disponivel = largura - m_esq - 8
    for leg in legendas:
        if atual and usado + leg['largura'] > disponivel:
            linhas_legenda.append(atual)
            atual, usado = [], 0
        atual.append(leg)
        usado += leg['largura']
    if atual:
        linhas_legenda.append(atual)

    m_baixo = 40 + 20 * len(linhas_legenda)
    altura  = max(altura, m_topo + 180 + m_baixo)

    img  = Image.new('RGB', (largura, altura), _BG)
    draw = ImageDraw.Draw(img)

    x0, y0 = m_esq, m_topo
    x1, y1 = largura - m_dir, altura - m_baixo

    if titulo:
        draw.text((m_esq, 16), titulo[:110], font=f_titulo, fill=_FG)
    if subtitulo:
        draw.text((m_esq, 40), subtitulo[:140], font=f_sub, fill=_MUTED)

    # Domínio
    t_min = min(p['t'] for s in series for p in s['pontos'])
    t_max = max(p['t'] for s in series for p in s['pontos'])
    if t_max == t_min:
        t_max = t_min + 1
    v_min = min(p['v'] for s in series for p in s['pontos'])
    v_max = max(p['v'] for s in series for p in s['pontos'])

    # Margem vertical: 8% de folga; escala parte do zero em métricas positivas
    # (tráfego), mas respeita valores negativos (dBm, temperatura).
    if v_max == v_min:
        delta = abs(v_max) * 0.1 or 1.0
        v_min, v_max = v_min - delta, v_max + delta
    else:
        folga = (v_max - v_min) * 0.08
        v_max += folga
        v_min = 0.0 if v_min >= 0 and v_min < (v_max - v_min) * 0.35 else v_min - folga

    span_seg = t_max - t_min

    def px(t):
        return x0 + (t - t_min) / (t_max - t_min) * (x1 - x0)

    def py(v):
        return y1 - (v - v_min) / (v_max - v_min) * (y1 - y0)

    # Grade horizontal + rótulos do eixo Y
    linhas_y = 5
    for i in range(linhas_y + 1):
        v = v_min + (v_max - v_min) * i / linhas_y
        y = py(v)
        draw.line([(x0, y), (x1, y)], fill=_GRID, width=1)
        rot = formatar_valor(v, units)
        draw.text((x0 - 10 - _texto_largura(draw, rot, f_eixo), y - 7),
                  rot, font=f_eixo, fill=_MUTED)

    # Grade vertical + rótulos do eixo X
    linhas_x = 6
    for i in range(linhas_x + 1):
        t = t_min + (t_max - t_min) * i / linhas_x
        x = px(t)
        if i not in (0, linhas_x):
            draw.line([(x, y0), (x, y1)], fill=_GRID, width=1)
        rot = _fmt_hora(int(t), span_seg)
        lw  = _texto_largura(draw, rot, f_eixo)
        draw.text((min(max(x - lw / 2, 2), largura - lw - 2), y1 + 8),
                  rot, font=f_eixo, fill=_MUTED)

    # Eixos
    draw.line([(x0, y0), (x0, y1)], fill=_EIXO, width=1)
    draw.line([(x0, y1), (x1, y1)], fill=_EIXO, width=1)

    # Marcador vertical (ex: momento do rompimento)
    if marcador_ts and t_min <= marcador_ts <= t_max:
        xm = px(marcador_ts)
        y = y0
        while y < y1:
            draw.line([(xm, y), (xm, min(y + 6, y1))], fill=_MARCADOR, width=2)
            y += 12
        rot = _fmt_hora(int(marcador_ts), span_seg)
        draw.text((min(xm + 4, x1 - 40), y0 + 2), rot, font=f_eixo, fill=_MARCADOR)

    # Séries
    for idx, s in enumerate(series):
        cor = s.get('cor') or _CORES[idx % len(_CORES)]
        pts = [(px(p['t']), py(p['v'])) for p in s['pontos']]
        if len(pts) == 1:
            x, y = pts[0]
            draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=cor)
        else:
            draw.line(pts, fill=cor, width=2, joint='curve')

    # Legenda (linhas já quebradas antes de fixar a altura)
    ly = y1 + 28
    for linha in linhas_legenda:
        lx = x0
        for leg in linha:
            draw.rectangle([lx, ly + 3, lx + 12, ly + 12], fill=leg['cor'])
            draw.text((lx + 18, ly), leg['texto'], font=f_leg, fill=_FG)
            lx += leg['largura']
        ly += 20

    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()
