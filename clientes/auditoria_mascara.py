"""Mascara senhas na auditoria de terminal (AcessoComando e transcript).

Três fontes de segredo, porque nenhuma sozinha cobre o terminal real:

1. Resposta a um prompt de senha ("Password:", "Please enter old password:",
   "Enter password:" do stelnet...). O equipamento não ecoa o que é digitado
   ali, então só dá para saber que era senha olhando o fim da saída na hora
   do Enter.
2. Senha inline num comando de configuração (`local-user x password
   irreversible-cipher X`, `username x secret 5 X`, `/user set password=X`).
   Na digitação crua ela costuma vir abreviada com Tab (`pa\\ti i\\tX`), por
   isso a palavra-chave é procurada no eco do equipamento (já expandido) e
   o valor encontrado vira segredo conhecido da sessão.
3. Senhas cadastradas nos acessos do cliente, digitadas em qualquer lugar
   (ex.: stelnet de um roteador para outro).

Todo segredo conhecido é trocado por MASCARA onde aparecer, nos comandos e
no transcript.
"""
import re

MASCARA = '******'

# Segredo de cadastro ou aprendido curto demais (ex.: "admin", "1234")
# apagaria palavras comuns da saída inteira. Abaixo disso só se mascara pelo
# contexto (prompt/palavra-chave), nunca por substituição solta.
TAMANHO_MIN_SEGREDO = 6

_PROMPT_SENHA_RE = re.compile(
    r'(?i)(?:password|passwd|senha|secret|passphrase|pass phrase|pin)[^\n:]{0,40}:\s*$'
)

# Precedido de espaço/início e não colado em '-' ou letra: "user-password" e
# "password-force-change" não são a palavra-chave. "community" sozinho fica de
# fora: na maioria das vezes é community BGP ("community no-export",
# "65109:50101"); a SNMP tem regra própria (_SNMP_RE).
_CHAVE = (
    r'(?:password|passwd|secret|senha|encrypted-password|pre-shared-key|'
    r'key-string|authentication-key|auth-key)'
)
_MODIFICADOR = (
    r'(?:cipher|irreversible-cipher|simple|plain|plain-text|encrypted|hash|'
    r'sha256|sha512|md5|[0-9])'
)
# Palavra que vem depois de "password"/"secret" em mensagem do equipamento
# ("password has been changed", "password needs to be changed") ou em
# subcomando ("/ppp secret add", "user password security mode"), não um
# valor. Não é mascarada nem vira segredo.
_NAO_VALOR = {
    'has', 'have', 'is', 'was', 'are', 'for', 'of', 'the', 'to', 'be', 'been',
    'will', 'must', 'and', 'or', 'in', 'on', 'not', 'no', 'you', 'policy',
    'expired', 'expires', 'expire', 'changed', 'length', 'complexity', 'error',
    'failed', 'incorrect', 'required', 'again', 'cannot', 'can', 'should',
    'encryption', 'recovery', 'control', 'history', 'alert', 'lock', 'too',
    'add', 'set', 'print', 'remove', 'export', 'enable', 'disable', 'edit',
    'find', 'get', 'show', 'display', 'undo', 'reset', 'modify', 'change',
    'mismatch', 'invalid', 'wrong', 'does', 'do', 'did', 'with', 'needs',
    'poses', 'security', 'mode', 'name', 'level', 'aging', 'prompt', 'string',
    'configuration', 'specify', 'type', 'format', 'mask', 'all', 'local',
}
# Hash do VRP tem aspas no meio ($1c$D'Y{U5...): sem aspas no começo, o
# valor vai até o próximo espaço.
_VALOR = r'(?:"[^"\n]*"|\'[^\'\n]*\'|\S+)'

_INLINE_RE = re.compile(
    r'(?i)(?<![\w-])(' + _CHAVE + r')((?:[ \t]+' + _MODIFICADOR + r'(?![\w-]))*)[ \t]+(' + _VALOR + r')'
)
_ATRIB_RE = re.compile(r'(?i)(?<![\w-])(' + _CHAVE + r')=(' + _VALOR + r')')

# "snmp-agent community read cipher X" (VRP, aceita abreviação: "read s X")
# e "snmp-server community X ro" (Cisco/Datacom). O valor é o primeiro
# token que não é modificador nem abreviação de um.
_SNMP_RE = re.compile(r'(?i)(?<![\w-])(snmp-(?:agent|server)[ \t]+community)([^\n\r]*)')
_SNMP_MODIFICADORES = ('read', 'write', 'cipher', 'simple', 'complexity-check')


def _valor_snmp(resto):
    """(início, fim) do valor em `resto` (o que vem depois de "community")."""
    for m in re.finditer(r'\S+', resto):
        tok = m.group(0).lower()
        if any(mod.startswith(tok) for mod in _SNMP_MODIFICADORES):
            continue
        if _eh_valor(tok, subcomando_possivel=False):
            return m.span()
        return None
    return None


def _sem_aspas(v):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
        return v[1:-1]
    return v


def _eh_valor(v, subcomando_possivel=True):
    v = _sem_aspas(v)
    if not re.search(r'[A-Za-z0-9]', v):        # "?", ":" da ajuda contextual
        return False
    # Subcomando (security-length, force-change). Community SNMP costuma ser
    # assim ("dstech-ro"), então lá a regra não vale.
    if subcomando_possivel and re.fullmatch(r'[a-z]+(?:-[a-z]+)+', v):
        return False
    return v.lower().rstrip('.,;:!') not in _NAO_VALOR


def extrair_segredos(texto):
    """Valores que aparecem depois de uma palavra-chave de senha."""
    achados = set()
    for m in _INLINE_RE.finditer(texto or ''):
        if _eh_valor(m.group(3)):
            achados.add(_sem_aspas(m.group(3)))
    for m in _ATRIB_RE.finditer(texto or ''):
        if _eh_valor(m.group(2)):
            achados.add(_sem_aspas(m.group(2)))
    for m in _SNMP_RE.finditer(texto or ''):
        span = _valor_snmp(m.group(2))
        if span:
            achados.add(_sem_aspas(m.group(2)[span[0]:span[1]]))
    return {s for s in achados if s and s != MASCARA}


def eh_prompt_senha(saida):
    """True se a última linha da saída é um pedido de senha."""
    if not saida:
        return False
    ultima = saida.replace('\r', '\n').rstrip(' ').split('\n')[-1]
    return bool(_PROMPT_SENHA_RE.search(ultima))


def segredos_de_acessos(acessos):
    """senha/senha_adm de uma lista de Acesso (ou dicts/tuplas) prontos para
    substituição solta."""
    out = set()
    for a in acessos:
        if isinstance(a, (tuple, list)):
            valores = a
        else:
            valores = (getattr(a, 'senha', ''), getattr(a, 'senha_adm', ''))
        for v in valores:
            v = (v or '').strip()
            if len(v) >= TAMANHO_MIN_SEGREDO:
                out.add(v)
    return out


def _substituivel(s):
    """Segredo que pode ser trocado em qualquer lugar do texto, fora do
    contexto onde apareceu. Palavra comum ("authentication", community
    "parks"), número, IP/IPv6 e community BGP (65109:50101) apagariam
    configuração legítima da sessão inteira; esses só saem pelo contexto."""
    if not s or len(s) < TAMANHO_MIN_SEGREDO:
        return False
    if re.fullmatch(r'[0-9a-fA-F:./-]+', s) or re.fullmatch(r'[a-z]+(?:[-_.][a-z]+)*', s):
        return False
    classes = sum(bool(re.search(p, s)) for p in (r'[a-z]', r'[A-Z]', r'[0-9]', r'[^A-Za-z0-9]'))
    return classes >= 2


def mascarar(texto, segredos=()):
    """Aplica as regras inline e troca cada segredo conhecido por MASCARA."""
    if not texto:
        return texto
    texto = _INLINE_RE.sub(
        lambda m: (m.group(0)[:m.start(3) - m.start(0)] + MASCARA) if _eh_valor(m.group(3)) else m.group(0),
        texto,
    )
    texto = _ATRIB_RE.sub(lambda m: f'{m.group(1)}={MASCARA}' if _eh_valor(m.group(2)) else m.group(0), texto)

    def _snmp(m):
        resto = m.group(2)
        span = _valor_snmp(resto)
        if not span:
            return m.group(0)
        return m.group(1) + resto[:span[0]] + MASCARA + resto[span[1]:]
    texto = _SNMP_RE.sub(_snmp, texto)
    # Maior primeiro: um segredo que contém outro não fica meio trocado.
    for s in sorted({s for s in segredos if _substituivel(s)}, key=len, reverse=True):
        texto = texto.replace(s, MASCARA)
    return texto


def mascarar_comando(comando, saida_antes, segredos=()):
    """Versão gravada de um comando digitado. `saida_antes` é a saída do
    equipamento até o Enter (o transcript acumulado)."""
    if eh_prompt_senha(saida_antes):
        return MASCARA
    return mascarar(comando, segredos)


# ── Registros antigos ───────────────────────────────────────────────────────
# Sem a saída do momento de cada Enter, a resposta a prompt de senha é
# deduzida: se a sessão teve prompt de senha, um comando de uma palavra só
# que nunca aparece no transcript foi digitado sem eco, ou seja, era senha.
# Comando normal sempre volta ecoado pelo equipamento.

def _tem_prompt_senha(transcript):
    return any(_PROMPT_SENHA_RE.search(l) for l in (transcript or '').replace('\r', '\n').split('\n'))


def segredos_sem_eco(comandos, transcript):
    # Transcript truncado perdeu o eco do começo da sessão: ausência dele
    # não prova nada.
    if not _tem_prompt_senha(transcript) or transcript.startswith('[...transcript truncado'):
        return set()
    out = set()
    for c in comandos:
        c = (c or '').strip()
        # Tecla de paginador ("q") também não volta ecoada; senha curta assim
        # não dá para separar dela.
        if len(c) < TAMANHO_MIN_SEGREDO or c == MASCARA or re.search(r'\s', c):
            continue
        # Comando corrigido com backspace também some do eco ("displya"→
        # "display"); só tudo minúsculo é comando, senha quase sempre tem
        # número, maiúscula ou símbolo.
        if re.fullmatch(r'[a-z-]+', c):
            continue
        if c not in transcript:
            out.add(c)
    return out
