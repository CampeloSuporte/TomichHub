"""
Middleware de proteção contra injeção (SQL injection, path traversal, XSS
refletido) na borda da aplicação.

Por que existe, se o Django já é seguro por padrão? Porque "por padrão" tem
exceções e o painel de segurança precisa ENXERGAR a tentativa:

* O ORM parametriza tudo, mas qualquer `cursor.execute` com f-string futuro,
  ou um `filter(**request.GET)` desavisado, abre o buraco. Esta camada é o
  cinto de segurança pra esse tipo de regressão.
* Sem ela, uma varredura de sqlmap contra o CRM não deixa rastro nenhum: o
  Django devolve 404/200 e a tentativa some. Aqui ela vira `EventoSeguranca`
  e aparece no painel.

Cuidado deliberado com falso positivo: o CRM manipula texto que PARECE
ataque (scripts de automação, artigos da wiki, mensagens do atendimento,
terminal SSH). Por isso:

* as assinaturas são específicas (`union select`, `information_schema`,
  `sleep(`), não genéricas (a palavra "select" sozinha não dispara);
* caminhos e campos de conteúdo livre entram em lista de isenção
  (`SEGURANCA_INJECAO_ISENTAS` / `SEGURANCA_INJECAO_CAMPOS_LIVRES`);
* dá pra rodar em modo observação (`SEGURANCA_INJECAO_BLOQUEAR = False`):
  registra o evento e deixa passar.
"""
import logging
import re

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render

logger = logging.getLogger(__name__)

# ── Assinaturas ───────────────────────────────────────────────────────────
# (nome, tipo, regex). O nome vai pro painel — é o que explica ao operador
# POR QUE aquela requisição foi barrada.
ASSINATURAS_SQL = [
    ('union_select',      r'\bunion\b[\s\S]{0,40}?\bselect\b'),
    ('information_schema', r'\binformation_schema\b'),
    ('tabela_sistema',    r'\b(pg_catalog|pg_shadow|pg_user|mysql\.user|sysobjects|sqlite_master)\b'),
    ('tautologia',        r'(\'|")\s*(or|and)\s*(\'|")?\s*\d+\s*(\'|")?\s*=\s*(\'|")?\s*\d+'),
    ('tautologia_texto',  r'(\'|")\s*(or|and)\s+(\'|")[^\'"]*(\'|")\s*=\s*(\'|")'),
    ('comentario_stack',  r';\s*(drop|alter|truncate|create|rename)\s+(table|database|schema|user)\b'),
    ('dml_stack',         r';\s*(insert|update|delete)\s+(into|from|\w+\s+set)\b'),
    ('time_based',        r'\b(sleep|pg_sleep|benchmark|waitfor\s+delay|dbms_pipe\.receive_message)\s*\('),
    ('arquivo',           r'\b(load_file|into\s+(out|dump)file|copy\s+\w+\s+from\s+program)\b'),
    ('exec_shell',        r'\b(xp_cmdshell|sp_executesql|exec\s*\(\s*@)'),
    ('comentario_mysql',  r'/\*!\d{5}'),
    ('hex_concat',        r'\bconcat\s*\(\s*0x[0-9a-f]{4,}'),
    ('select_from_users', r'\bselect\b[\s\S]{0,80}?\bfrom\b[\s\S]{0,40}?\b(auth_user|users|usuarios|pg_\w+)\b'),
]

ASSINATURAS_PATH = [
    # `../` codificado ou cru, três níveis ou mais — dois níveis aparecem em
    # URL relativa legítima.
    ('traversal', r'(\.\.[\\/]){2,}|(%2e%2e[\\/%])'),
    ('arquivo_sensivel', r'/etc/(passwd|shadow|sudoers)|\bproc/self/environ\b'),
]

ASSINATURAS_XSS = [
    ('script_tag', r'<\s*script[\s>]'),
    ('js_handler', r'\bon(error|load|click|mouseover)\s*=\s*[\'"]?\s*(javascript:|alert\(|eval\()'),
    ('js_uri', r'javascript:\s*(alert|eval|document\.cookie)'),
]

_COMPILADAS = {
    'sql_injection': [(nome, re.compile(p, re.IGNORECASE)) for nome, p in ASSINATURAS_SQL],
    'path_traversal': [(nome, re.compile(p, re.IGNORECASE)) for nome, p in ASSINATURAS_PATH],
    'xss': [(nome, re.compile(p, re.IGNORECASE)) for nome, p in ASSINATURAS_XSS],
}

# Prefixos de URL isentos: conteúdo livre de verdade, onde texto parecido com
# ataque é o trabalho normal do usuário (script de automação com SQL, artigo
# da wiki explicando injeção, mensagem de chamado colando um log).
ISENTAS_PADRAO = (
    '/admin/',                    # o próprio Django admin já é restrito ao Administrador
    '/clientes/scripts/',         # scripts de automação: texto de comando cru
    '/clientes/terminal/',        # terminal SSH web
    '/wiki/',                     # artigos podem falar sobre SQL injection
    '/atendimento/',              # mensagens de chamado colam logs inteiros
    '/monitoramento/webhook',     # payload de terceiros
    '/static/', '/media/',
)

# Campos de texto longo isentos em qualquer rota — descrição, observação,
# corpo de artigo. Comparação por sufixo/contém, minúsculo.
CAMPOS_LIVRES_PADRAO = (
    'conteudo', 'descricao', 'observacao', 'observacoes', 'mensagem', 'comentario',
    'texto', 'corpo', 'script', 'comandos', 'saida', 'output', 'log', 'payload',
    'dados_json', 'drawio_xml', 'config', 'configuracao', 'prompt', 'anotacoes',
)

TAMANHO_MAX_VALOR = 4000   # acima disso é anexo/base64, não parâmetro de query
TRECHO_PAYLOAD = 400


class ProtecaoInjecaoMiddleware:
    """Inspeciona query string, corpo do POST e caminho de cada requisição."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.ativo = getattr(settings, 'SEGURANCA_INJECAO_ATIVO', True)
        self.bloquear = getattr(settings, 'SEGURANCA_INJECAO_BLOQUEAR', True)
        self.isentas = tuple(getattr(settings, 'SEGURANCA_INJECAO_ISENTAS', ISENTAS_PADRAO))
        self.campos_livres = tuple(
            c.lower() for c in getattr(settings, 'SEGURANCA_INJECAO_CAMPOS_LIVRES', CAMPOS_LIVRES_PADRAO)
        )

    def __call__(self, request):
        deteccao = self._inspecionar(request) if self.ativo else None
        if deteccao:
            self._registrar(request, deteccao)
            if self.bloquear:
                return self._resposta_bloqueio(request)
        return self.get_response(request)

    # ── inspeção ─────────────────────────────────────────────────────────
    def _isenta(self, request):
        caminho = request.path or ''
        return any(caminho.startswith(prefixo) for prefixo in self.isentas)

    def _campo_livre(self, nome):
        nome = (nome or '').lower()
        return any(livre in nome for livre in self.campos_livres)

    def _inspecionar(self, request):
        if self._isenta(request):
            return None

        achado = self._checar_valor(request.path, tipos=('path_traversal',))
        if achado:
            return dict(achado, origem='path', campo='')

        for chave, valor in request.GET.items():
            achado = self._checar_campo(chave, valor)
            if achado:
                return dict(achado, origem='querystring', campo=chave[:120])

        if request.method in ('POST', 'PUT', 'PATCH'):
            for chave, valor in self._itens_post(request):
                achado = self._checar_campo(chave, valor)
                if achado:
                    return dict(achado, origem='post', campo=chave[:120])
        return None

    def _itens_post(self, request):
        """Só formulário urlencoded. JSON e multipart ficam de fora de propósito:

        * ler `request.body`/`request.POST` aqui CONSOME o corpo antes da view.
          Pra multipart (upload de backup, firmware, anexo de chamado) isso
          significa processar 100 MB no middleware e impedir qualquer view de
          trocar os upload handlers depois;
        * as rotas que recebem JSON no CRM são internas, autenticadas e passam
          por ORM.

        A superfície que sobra — querystring e formulário comum — é onde uma
        injeção refletida realmente aparece.
        """
        tipo = (request.META.get('CONTENT_TYPE') or '').lower()
        if 'application/x-www-form-urlencoded' not in tipo:
            return []
        try:
            return list(request.POST.items())
        except Exception:
            return []

    def _checar_campo(self, chave, valor):
        if self._campo_livre(chave):
            return None
        return self._checar_valor(valor)

    def _checar_valor(self, valor, tipos=('sql_injection', 'path_traversal', 'xss')):
        if not isinstance(valor, str) or not valor:
            return None
        if len(valor) > TAMANHO_MAX_VALOR:
            return None
        for tipo in tipos:
            for nome, regex in _COMPILADAS[tipo]:
                m = regex.search(valor)
                if m:
                    return {'tipo': tipo, 'assinatura': nome, 'payload': valor[:TRECHO_PAYLOAD]}
        return None

    # ── resposta e registro ──────────────────────────────────────────────
    def _registrar(self, request, deteccao):
        from .models import EventoSeguranca
        from .services import _ip_valido, get_client_ip

        usuario = getattr(request, 'user', None)
        if usuario is not None and not getattr(usuario, 'is_authenticated', False):
            usuario = None
        try:
            EventoSeguranca.objects.create(
                tipo=deteccao['tipo'],
                assinatura=deteccao['assinatura'],
                origem=deteccao.get('origem', 'querystring'),
                campo=deteccao.get('campo', ''),
                payload=deteccao.get('payload', ''),
                caminho=(request.path or '')[:500],
                metodo=request.method or '',
                ip=_ip_valido(get_client_ip(request)),
                user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:300],
                usuario=usuario,
                bloqueado=self.bloquear,
            )
        except Exception:
            logger.exception('Falha ao registrar evento de segurança')
        logger.warning(
            'Injeção detectada (%s/%s) em %s %s campo=%s ip=%s',
            deteccao['tipo'], deteccao['assinatura'], request.method, request.path,
            deteccao.get('campo'), get_client_ip(request),
        )

    def _resposta_bloqueio(self, request):
        """403 em HTML pra navegação, JSON pra chamada AJAX — uma página de
        erro dentro de um `fetch()` viraria "Erro ao carregar" genérico."""
        aceita_json = 'application/json' in (request.META.get('HTTP_ACCEPT') or '')
        ajax = request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest'
        if aceita_json or ajax:
            return JsonResponse(
                {'erro': 'Requisição bloqueada pelo filtro de segurança.'}, status=403,
            )
        try:
            return render(request, '403.html', status=403)
        except Exception:
            return HttpResponseForbidden('Requisição bloqueada pelo filtro de segurança.')
