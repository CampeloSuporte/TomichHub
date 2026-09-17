"""
Telas e APIs dos documentos de rede (AS-IS). Exclusivo do Administrador.

Páginas usam `clientes.decorators.admin_required` (redireciona); rotas AJAX
usam `_admin_api`, que responde JSON 401/403 — um 302 para o login quebraria
o `response.json()` do editor.
"""
import json
import logging
import uuid
from functools import wraps

from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from clientes.decorators import admin_required
from clientes.models import Acesso, BackupLog, Cliente, TopologiaDiagrama
from usuario import perms

from . import analise, coleta, composicao, exportacao
from .models import DocumentoRede, DocumentoRedeRevisao
from .sanitizar import limpar_html, limpar_texto

logger = logging.getLogger(__name__)

LIMITE_SECOES = 80
LIMITE_HTML_SECAO = 3 * 1024 * 1024
CAMPOS_META_EDITAVEIS = {
    'empresa', 'titulo', 'subtitulo', 'documento', 'versao', 'status', 'asn',
    'data_base', 'responsavel', 'classificacao', 'principio',
}


def _admin_api(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'ok': False, 'erro': 'Sessão expirada. Entre novamente.'}, status=401)
        if not perms.is_admin(request.user):
            return JsonResponse({'ok': False, 'erro': 'Apenas administradores.'}, status=403)
        return view(request, *args, **kwargs)
    return wrapper


def _json(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return None


def _nome_usuario(user):
    return (user.get_full_name() or user.get_username()) if user else ''


def _novo_id():
    return uuid.uuid4().hex[:12]


def _modelo_do_cliente(cliente):
    return analise.montar_modelo(coleta.coletar(cliente))


def _snapshot(doc, motivo, user):
    return DocumentoRedeRevisao.objects.create(
        documento=doc, versao=doc.versao, status=doc.status,
        metadados=doc.metadados, secoes=doc.secoes, motivo=motivo[:255], autor=user,
    )


def _doc_json(doc):
    return {
        'id': doc.id, 'titulo': doc.titulo, 'versao': doc.versao, 'status': doc.status,
        'metadados': doc.metadados, 'secoes': doc.secoes,
        'atualizado_em': doc.atualizado_em.isoformat(),
        'atualizado_por': _nome_usuario(doc.atualizado_por),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Páginas
# ═══════════════════════════════════════════════════════════════════════════

@admin_required
@require_GET
def lista(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    acessos = Acesso.objects.filter(cliente=cliente)
    com_backup = (BackupLog.objects.filter(cliente=cliente, status='SUCESSO')
                  .exclude(arquivo_path='').values('acesso_id').distinct().count())
    documentos = (DocumentoRede.objects.filter(cliente=cliente)
                  .select_related('criado_por', 'atualizado_por')
                  .order_by('-atualizado_em'))
    return render(request, 'projeto_rede/lista.html', {
        'cliente': cliente,
        'documentos': documentos,
        'total_acessos': acessos.count(),
        'total_ssh': acessos.filter(protocolo='SSH').count(),
        'total_com_backup': com_backup,
        'total_mapas': TopologiaDiagrama.objects.filter(cliente=cliente).count(),
        'total_blocos': cliente.blocos_ip.count(),
    })


@admin_required
@require_GET
def editor(request, doc_id):
    doc = get_object_or_404(DocumentoRede.objects.select_related('cliente', 'atualizado_por'), id=doc_id)
    return render(request, 'projeto_rede/editor.html', {
        'doc': doc,
        'cliente': doc.cliente,
        'doc_json': _doc_json(doc),
        'status_opcoes': DocumentoRede.STATUS,
        'campos_meta': composicao.CAMPOS_METADADOS,
        'secoes_auto': [{'chave': k, 'titulo': t} for k, t, _ in composicao.SECOES],
    })


@admin_required
@require_GET
def visualizar(request, doc_id):
    doc = get_object_or_404(DocumentoRede, id=doc_id)
    return HttpResponse(exportacao.html_documento(doc))


@admin_required
@require_GET
def baixar_pdf(request, doc_id):
    doc = get_object_or_404(DocumentoRede, id=doc_id)
    try:
        conteudo = exportacao.gerar_pdf(doc)
    except exportacao.ErroExportacao as e:
        logger.warning('AS-IS: PDF do documento %s falhou: %s', doc.id, e)
        return HttpResponse(f'Não foi possível gerar o PDF: {e}', status=500, content_type='text/plain; charset=utf-8')
    resp = HttpResponse(conteudo, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{exportacao.nome_arquivo(doc, "pdf")}"'
    return resp


@admin_required
@require_GET
def baixar_docx(request, doc_id):
    doc = get_object_or_404(DocumentoRede, id=doc_id)
    try:
        conteudo = exportacao.gerar_docx(doc)
    except Exception:
        logger.exception('AS-IS: DOCX do documento %s falhou', doc.id)
        return HttpResponse('Não foi possível gerar o DOCX.', status=500, content_type='text/plain; charset=utf-8')
    resp = HttpResponse(
        conteudo, content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    resp['Content-Disposition'] = f'attachment; filename="{exportacao.nome_arquivo(doc, "docx")}"'
    return resp


# ═══════════════════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════════════════

@_admin_api
@require_POST
def gerar_asis(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    try:
        modelo = _modelo_do_cliente(cliente)
    except Exception:
        logger.exception('AS-IS: coleta/análise do cliente %s falhou', cliente.id)
        return JsonResponse({'ok': False, 'erro': 'Falha ao analisar os backups. Detalhes no log do servidor.'},
                            status=500)
    if not modelo['total_com_backup']:
        return JsonResponse({'ok': False, 'erro': 'Nenhum backup de configuração disponível para este cliente. '
                                                  'Execute os backups antes de gerar o AS-IS.'}, status=400)
    secoes = composicao.gerar_secoes(modelo)
    for s in secoes:
        s['id'] = _novo_id()
    meta = composicao.metadados_padrao(modelo, _nome_usuario(request.user))
    with transaction.atomic():
        doc = DocumentoRede.objects.create(
            cliente=cliente, tipo=DocumentoRede.TIPO_ASIS,
            titulo=meta['documento'][:255], versao=meta['versao'],
            metadados=meta, secoes=secoes, coleta=composicao.resumo_coleta(modelo),
            criado_por=request.user, atualizado_por=request.user,
        )
        _snapshot(doc, 'Geração automática a partir dos backups', request.user)
    return JsonResponse({
        'ok': True, 'id': doc.id, 'url': reverse('projeto_rede:editor', args=[doc.id]),
        'achados': len(modelo['achados']), 'equipamentos': modelo['total_com_backup'],
    })


@_admin_api
@require_POST
def salvar(request, doc_id):
    doc = get_object_or_404(DocumentoRede, id=doc_id)
    dados = _json(request)
    if not isinstance(dados, dict):
        return JsonResponse({'ok': False, 'erro': 'Requisição inválida.'}, status=400)

    base = dados.get('base')
    if base and base != doc.atualizado_em.isoformat() and not dados.get('forcar'):
        return JsonResponse({
            'ok': False, 'conflito': True,
            'erro': f'O documento foi alterado por {_nome_usuario(doc.atualizado_por) or "outra sessão"} '
                    f'em {timezone.localtime(doc.atualizado_em):%d/%m/%Y %H:%M}.',
        }, status=409)

    secoes_in = dados.get('secoes')
    if not isinstance(secoes_in, list) or len(secoes_in) > LIMITE_SECOES:
        return JsonResponse({'ok': False, 'erro': 'Lista de seções inválida.'}, status=400)
    secoes = []
    chaves_validas = {k for k, _, _ in composicao.SECOES}
    for s in secoes_in:
        if not isinstance(s, dict):
            continue
        html = s.get('html') or ''
        if len(html) > LIMITE_HTML_SECAO:
            return JsonResponse({'ok': False, 'erro': f'A seção "{limpar_texto(s.get("titulo"), 80)}" é grande demais.'},
                                status=400)
        chave = s.get('chave') if s.get('chave') in chaves_validas else ''
        secoes.append({
            'id': limpar_texto(s.get('id'), 40) or _novo_id(),
            'chave': chave,
            'titulo': limpar_texto(s.get('titulo'), 200) or 'Sem título',
            'html': limpar_html(html),
            'auto': bool(chave) and bool(s.get('auto')),
        })

    meta = dict(doc.metadados or {})
    for k, v in (dados.get('metadados') or {}).items():
        if k in CAMPOS_META_EDITAVEIS:
            meta[k] = limpar_texto(v, 1000 if k == 'principio' else 300)

    status = dados.get('status')
    if status in dict(DocumentoRede.STATUS):
        doc.status = status
    doc.versao = limpar_texto(meta.get('versao') or doc.versao, 20) or doc.versao
    doc.titulo = limpar_texto(meta.get('documento') or doc.titulo, 255) or doc.titulo
    doc.metadados = meta
    doc.secoes = secoes
    doc.atualizado_por = request.user
    doc.save()
    return JsonResponse({'ok': True, 'atualizado_em': doc.atualizado_em.isoformat(),
                         'secoes': secoes})


@_admin_api
@require_POST
def regenerar_secao(request, doc_id):
    """Devolve o HTML recalculado de uma seção automática a partir dos backups
    atuais. Não grava — o editor substitui o conteúdo e o usuário salva."""
    doc = get_object_or_404(DocumentoRede.objects.select_related('cliente'), id=doc_id)
    dados = _json(request) or {}
    chave = dados.get('chave')
    if chave not in {k for k, _, _ in composicao.SECOES}:
        return JsonResponse({'ok': False, 'erro': 'Seção sem geração automática.'}, status=400)
    try:
        modelo = _modelo_do_cliente(doc.cliente)
    except Exception:
        logger.exception('AS-IS: regeneração da seção %s do documento %s falhou', chave, doc.id)
        return JsonResponse({'ok': False, 'erro': 'Falha ao analisar os backups.'}, status=500)
    return JsonResponse({'ok': True, **composicao.gerar_secao(modelo, chave)})


@_admin_api
@require_POST
def regenerar_tudo(request, doc_id):
    """Recalcula todas as seções automáticas, preservando as seções criadas à
    mão. Guarda uma revisão antes, para o que foi editado não se perder."""
    doc = get_object_or_404(DocumentoRede.objects.select_related('cliente'), id=doc_id)
    try:
        modelo = _modelo_do_cliente(doc.cliente)
    except Exception:
        logger.exception('AS-IS: regeneração do documento %s falhou', doc.id)
        return JsonResponse({'ok': False, 'erro': 'Falha ao analisar os backups.'}, status=500)
    novas = {s['chave']: s for s in composicao.gerar_secoes(modelo)}
    with transaction.atomic():
        _snapshot(doc, 'Antes de atualizar com os backups mais recentes', request.user)
        secoes = []
        vistas = set()
        for s in doc.secoes or []:
            chave = s.get('chave')
            if chave in novas and chave not in vistas:
                vistas.add(chave)
                secoes.append({**novas[chave], 'id': s.get('id') or _novo_id(),
                               'titulo': s.get('titulo') or novas[chave]['titulo']})
            else:
                secoes.append(s)
        for chave, s in novas.items():
            if chave not in vistas:
                secoes.append({**s, 'id': _novo_id()})
        meta = dict(doc.metadados or {})
        meta['data_base'] = composicao.metadados_padrao(modelo)['data_base']
        doc.metadados = meta
        doc.secoes = secoes
        doc.coleta = composicao.resumo_coleta(modelo)
        doc.atualizado_por = request.user
        doc.save()
    return JsonResponse({'ok': True, 'documento': _doc_json(doc)})


@_admin_api
@require_GET
def revisoes(request, doc_id):
    doc = get_object_or_404(DocumentoRede, id=doc_id)
    itens = [{
        'id': r.id, 'versao': r.versao, 'motivo': r.motivo, 'autor': _nome_usuario(r.autor),
        'criado_em': timezone.localtime(r.criado_em).strftime('%d/%m/%Y %H:%M'),
        'secoes': len(r.secoes or []),
    } for r in doc.revisoes.select_related('autor')[:100]]
    return JsonResponse({'ok': True, 'revisoes': itens})


@_admin_api
@require_POST
def registrar_revisao(request, doc_id):
    doc = get_object_or_404(DocumentoRede, id=doc_id)
    dados = _json(request) or {}
    motivo = limpar_texto(dados.get('motivo'), 255) or 'Revisão registrada'
    rev = _snapshot(doc, motivo, request.user)
    return JsonResponse({'ok': True, 'id': rev.id})


@_admin_api
@require_POST
def restaurar_revisao(request, doc_id, rev_id):
    doc = get_object_or_404(DocumentoRede, id=doc_id)
    rev = get_object_or_404(DocumentoRedeRevisao, id=rev_id, documento=doc)
    with transaction.atomic():
        _snapshot(doc, f'Antes de restaurar a revisão de {timezone.localtime(rev.criado_em):%d/%m/%Y %H:%M}',
                  request.user)
        doc.metadados = rev.metadados
        doc.secoes = rev.secoes
        doc.versao = rev.versao or doc.versao
        if rev.status in dict(DocumentoRede.STATUS):
            doc.status = rev.status
        doc.atualizado_por = request.user
        doc.save()
    return JsonResponse({'ok': True, 'documento': _doc_json(doc)})


@_admin_api
@require_POST
def excluir(request, doc_id):
    doc = get_object_or_404(DocumentoRede, id=doc_id)
    cliente_id = doc.cliente_id
    doc.delete()
    return JsonResponse({'ok': True, 'url': reverse('projeto_rede:lista', args=[cliente_id])})
