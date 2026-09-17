"""
Sanitização do HTML editado no navegador.

Lista de permissões (não de bloqueio): só sobrevivem as tags e classes que o
editor produz e que a exportação DOCX sabe converter. Qualquer outra tag é
descartada mantendo o texto; `script`/`style` são descartadas com o conteúdo.
Atributos de evento, `style` e URLs que não sejam http(s)/mailto somem.
"""
from html import escape
from html.parser import HTMLParser

TAGS = {
    'p', 'br', 'strong', 'em', 'u', 's', 'h3', 'h4', 'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tr', 'th', 'td', 'code', 'pre', 'blockquote',
    'div', 'span', 'a', 'sup', 'sub',
}
VAZIAS = {'br'}
DESCARTAR_COM_CONTEUDO = {'script', 'style', 'iframe', 'object', 'embed', 'template', 'noscript', 'svg', 'math'}
RENOMEAR = {'b': 'strong', 'i': 'em', 'strike': 's', 'del': 's', 'h1': 'h3', 'h2': 'h3', 'h5': 'h4', 'h6': 'h4'}
CLASSES = {
    'div': {'callout', 'callout-risco', 'callout-status'},
    'span': {'sev-critica', 'sev-alta', 'sev-media', 'sev-baixa', 'sev-info', 'marca'},
    'p': {'nota'},
    'table': {'compacta'},
}
ATRIBUTOS = {
    'a': {'href'},
    'td': {'colspan', 'rowspan'},
    'th': {'colspan', 'rowspan'},
}


class _Limpador(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.saida = []
        self.pilha = []
        self.descartando = 0

    def _abre(self, tag, attrs, fechada=False):
        tag = RENOMEAR.get(tag, tag)
        if tag in DESCARTAR_COM_CONTEUDO:
            if not fechada:
                self.descartando += 1
            return
        if self.descartando or tag not in TAGS:
            return
        partes = [tag]
        for nome, valor in attrs:
            nome = (nome or '').lower()
            valor = valor or ''
            if nome == 'class':
                ok = [c for c in valor.split() if c in CLASSES.get(tag, set())]
                if ok:
                    partes.append(f'class="{escape(" ".join(ok))}"')
            elif nome in ATRIBUTOS.get(tag, set()):
                if nome == 'href':
                    v = valor.strip()
                    if not v.lower().startswith(('http://', 'https://', 'mailto:')):
                        continue
                    partes.append(f'href="{escape(v)}" rel="noopener noreferrer"')
                elif nome in ('colspan', 'rowspan') and valor.isdigit() and 0 < int(valor) <= 50:
                    partes.append(f'{nome}="{valor}"')
        if tag == 'div' and len(partes) == 1:
            tag = 'p'          # div sem classe vira parágrafo (Enter do contenteditable)
            partes[0] = 'p'
        self.saida.append('<' + ' '.join(partes) + '>')
        if tag not in VAZIAS and not fechada:
            self.pilha.append(tag)

    def handle_starttag(self, tag, attrs):
        self._abre(tag.lower(), attrs)

    def handle_startendtag(self, tag, attrs):
        self._abre(tag.lower(), attrs, fechada=True)

    def handle_endtag(self, tag):
        tag = RENOMEAR.get(tag.lower(), tag.lower())
        if tag in DESCARTAR_COM_CONTEUDO:
            self.descartando = max(0, self.descartando - 1)
            return
        if self.descartando or tag in VAZIAS:
            return
        alvo = tag
        if alvo == 'div' and alvo not in self.pilha and 'p' in self.pilha:
            alvo = 'p'
        if alvo not in self.pilha:
            return
        while self.pilha:
            t = self.pilha.pop()
            self.saida.append(f'</{t}>')
            if t == alvo:
                break

    def handle_data(self, data):
        if not self.descartando:
            self.saida.append(escape(data, quote=False))

    def resultado(self):
        while self.pilha:
            self.saida.append(f'</{self.pilha.pop()}>')
        return ''.join(self.saida)


def limpar_html(html):
    if not html:
        return ''
    p = _Limpador()
    p.feed(str(html))
    p.close()
    return p.resultado()


def limpar_texto(valor, limite=500):
    return ' '.join(str(valor or '').split())[:limite]
