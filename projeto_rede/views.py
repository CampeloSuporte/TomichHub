"""
Telas e APIs dos documentos de arquitetura (HLD, AS-IS, Change Plan TO-BE)
e dos cenários de topologia TO-BE. Exclusivo do Administrador.

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

from . import composicao, composicao_hld, composicao_tobe, documentos, exportacao, ia, tobe
from . import convencao as cv
from .models import CenarioTopologia, DocumentoRede, DocumentoRedeRevisao
from .sanitizar import limpar_html, limpar_texto

logger = logging.getLogger(__name__)

LIMITE_SECOES = 80
LIMITE_HTML_SECAO = 3 * 1024 * 1024
LIMITE_CENARIO = 8 * 1024 * 1024
CAMPOS_META_EDITAVEIS = {
    'empresa', 'titulo', 'subtitulo', 'documento', 'versao', 'status', 'asn', 'data_base', 'responsavel',
    'classificacao', 'principio', 'principio_titulo', 'ambito', 'hld', 'data',
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


def _modelo_do_cliente(cliente, com_ia=False):
    return documentos.modelo_asis(cliente, com_ia=com_ia)


def _snapshot(doc, motivo, user):
    return DocumentoRedeRevisao.objects.create(
        documento=doc, versao=doc.versao, status=doc.status,
        metadados=doc.metadados, secoes=doc.secoes, motivo=motivo[:255], autor=user,
    )


def _com_ids(secoes):
    for s in secoes:
        s['id'] = _novo_id()
    return secoes


def _doc_json(doc):
    return {
        'id': doc.id, 'tipo': doc.tipo, 'titulo': doc.titulo, 'versao': doc.versao, 'status': doc.status,
        'metadados': doc.metadados, 'secoes': doc.secoes,
        'atualizado_em': doc.atualizado_em.isoformat(),
        'atualizado_por': _nome_usuario(doc.atualizado_por),
    }


def _erro(msg, status=400):
    return JsonResponse({'ok': False, 'erro': msg}, status=status)


def _criar_documento(cliente, tipo, meta, secoes, user, motivo, **extra):
    with transaction.atomic():
        doc = DocumentoRede.objects.create(
            cliente=cliente, tipo=tipo, titulo=meta['documento'][:255], versao=meta['versao'],
            metadados=meta, secoes=_com_ids(secoes), criado_por=user, atualizado_por=user, **extra)
        _snapshot(doc, motivo, user)
    return doc


def _recompor(doc, ctx, user, motivo):
    """Recalcula as seções automáticas preservando as escritas à mão."""
    novas = {s['chave']: s for s in documentos.gerar_secoes(doc.tipo, ctx)}
    _snapshot(doc, motivo, user)
    secoes, vistas = [], set()
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
    doc.secoes = secoes
    doc.atualizado_por = user


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
    docs = list(DocumentoRede.objects.filter(cliente=cliente)
                .select_related('criado_por', 'atualizado_por').order_by('-atualizado_em'))
    cenarios = list(CenarioTopologia.objects.filter(cliente=cliente).select_related('origem'))
    for c in cenarios:
        c.info = c.resumo()
    mapas = TopologiaDiagrama.objects.filter(cliente=cliente).order_by('pai_id', 'id')
    return render(request, 'projeto_rede/lista.html', {
        'cliente': cliente,
        'hlds': [d for d in docs if d.tipo == DocumentoRede.TIPO_HLD],
        'asis': [d for d in docs if d.tipo == DocumentoRede.TIPO_ASIS],
        'planos': [d for d in docs if d.tipo == DocumentoRede.TIPO_CHANGE_PLAN],
        'documentos': docs,
        'cenarios': cenarios,
        'mapas': mapas,
        'total_acessos': acessos.count(),
        'total_ssh': acessos.filter(protocolo='SSH').count(),
        'total_com_backup': com_backup,
        'total_mapas': mapas.count(),
        'total_blocos': cliente.blocos_ip.count(),
    })


@admin_required
@require_GET
def editor(request, doc_id):
    doc = get_object_or_404(DocumentoRede.objects.select_related('cliente', 'atualizado_por'), id=doc_id)
    extras = {}
    if doc.tipo == DocumentoRede.TIPO_CHANGE_PLAN:
        hld, asis, cenario = documentos.referencias_tobe(doc)
        extras = {'ref_hld': hld, 'ref_asis': asis, 'ref_cenario': cenario}
    return render(request, 'projeto_rede/editor.html', {
        'doc': doc,
        'cliente': doc.cliente,
        'doc_json': _doc_json(doc),
        'status_opcoes': DocumentoRede.STATUS,
        'campos_meta': documentos.campos(doc.tipo),
        'secoes_auto': [{'chave': k, 'titulo': t} for k, t, _ in documentos.secoes(doc.tipo)],
        'rotulo_atualizar': documentos.ROTULO_ATUALIZAR[doc.tipo],
        **extras,
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
        logger.warning('projeto_rede: PDF do documento %s falhou: %s', doc.id, e)
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
        logger.exception('projeto_rede: DOCX do documento %s falhou', doc.id)
        return HttpResponse('Não foi possível gerar o DOCX.', status=500, content_type='text/plain; charset=utf-8')
    resp = HttpResponse(
        conteudo, content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    resp['Content-Disposition'] = f'attachment; filename="{exportacao.nome_arquivo(doc, "docx")}"'
    return resp


@admin_required
@require_GET
def convencao(request, doc_id):
    doc = get_object_or_404(DocumentoRede.objects.select_related('cliente'), id=doc_id, tipo=DocumentoRede.TIPO_HLD)
    return render(request, 'projeto_rede/convencao.html', {
        'doc': doc, 'cliente': doc.cliente,
        'conv': documentos.convencao_do_hld(doc),
        'campos': cv.CAMPOS, 'tabelas': cv.TABELAS,
        'avisos': cv.validar(documentos.convencao_do_hld(doc)),
    })


@admin_required
@require_GET
def cenario_editor(request, cenario_id):
    cen = get_object_or_404(CenarioTopologia.objects.select_related('cliente'), id=cenario_id)
    return render(request, 'topologia_editor.html', {
        'cliente': cen.cliente,
        'diagrama': None,
        'dados_json': cen.dados_json,
        'diagrama_id': None,
        'cenario': cen,
        'cenario_papeis_json': json.dumps([p for p in tobe.PAPEIS_NO if p]),
        'bng_destinos_json': json.dumps(['BNG01', 'BNG02', 'BNG03']),
    })


@admin_required
@require_GET
def tobe_novo(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    return render(request, 'projeto_rede/tobe_novo.html', {
        'cliente': cliente,
        'hlds': documentos.hlds_do_cliente(cliente),
        'asis': DocumentoRede.objects.filter(cliente=cliente, tipo=DocumentoRede.TIPO_ASIS),
        'cenarios': CenarioTopologia.objects.filter(cliente=cliente),
    })


@admin_required
@require_GET
def tobe_mapeamentos(request, doc_id):
    doc = get_object_or_404(DocumentoRede.objects.select_related('cliente'), id=doc_id,
                            tipo=DocumentoRede.TIPO_CHANGE_PLAN)
    hld, asis, cenario = documentos.referencias_tobe(doc)
    modelo = documentos.modelo_asis(doc.cliente)
    conv = documentos.convencao_do_hld(hld) if hld else cv.convencao_padrao(**cv.sugerir_parametros(modelo))
    mapa = tobe.mesclar_mapeamentos(tobe.mapeamentos_sugeridos(modelo, conv, cenario.dados() if cenario else None),
                                    (doc.dados or {}).get('mapeamentos') or {})
    l2 = []
    for s in modelo['l2vpn']:
        if s.get('pppoe'):
            continue
        chave = tobe.chave_servico_l2(s)
        l2.append({'chave': chave, 'id': s['id'], 'nomes': ', '.join(s['nomes'][:3]),
                   'pontas': ', '.join(s['pontas']), 'contratante': mapa['contratantes'].get(chave, '')})
    equipamentos = []
    for e in modelo['equipamentos']:
        if e.get('vendor') == 'huawei' or 'BNG' in e.get('papeis', []) or 'RR' in e.get('papeis', []):
            equipamentos.append({'acesso_id': str(e['acesso_id']), 'nome': e['nome_exibicao'], 'pop': e['pop'],
                                 'papeis': ', '.join(e.get('papeis', [])),
                                 'alvo': mapa['papeis'].get(str(e['acesso_id']), '')})
    pops_bng = [{'pop': b['pop'], 'nome': b['nome'], 'destino': mapa['bng_destino'].get(b['pop'], '')}
                for b in modelo['bngs_remotos']]
    communities = [{'valor': l['valor'], 'finalidade': l['finalidade'], 'alvo': mapa['communities'].get(l['valor'], '')}
                   for l in modelo['communities']['linhas']]
    pendencias = tobe.gerar_plano(modelo, conv, cenario.dados() if cenario else None,
                                  (doc.dados or {}).get('mapeamentos'))['pendencias']
    if not hld:
        pendencias.insert(0, 'Nenhum HLD vinculado: o plano usa o modelo ISP padrão como convenção.')
    return render(request, 'projeto_rede/tobe_mapeamentos.html', {
        'doc': doc, 'cliente': doc.cliente,
        'hlds': documentos.hlds_do_cliente(doc.cliente),
        'asis_docs': DocumentoRede.objects.filter(cliente=doc.cliente, tipo=DocumentoRede.TIPO_ASIS),
        'cenarios': CenarioTopologia.objects.filter(cliente=doc.cliente),
        'ref_hld': hld, 'ref_asis': asis, 'ref_cenario': cenario,
        'vrfs': [{'nome': k, 'alvo': v} for k, v in mapa['vrfs'].items()],
        'destinos_vrf': [s.get('nome') for s in conv.get('servicos', [])] + [tobe.L3VPN, tobe.ELIMINAR],
        'l2': l2, 'equipamentos': equipamentos, 'pops_bng': pops_bng, 'communities': communities,
        'criticos': mapa.get('criticos', ''),
        'papeis_opcoes': [p for p in tobe.PAPEIS_NO if p],
        'com_cenario': bool(cenario),
        'pendencias': pendencias,
    })


# ═══════════════════════════════════════════════════════════════════════════
# API — documentos
# ═══════════════════════════════════════════════════════════════════════════

@_admin_api
@require_POST
def gerar_asis(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    try:
        modelo = _modelo_do_cliente(cliente, com_ia=True)
    except Exception:
        logger.exception('AS-IS: coleta/análise do cliente %s falhou', cliente.id)
        return _erro('Falha ao analisar os backups. Detalhes no log do servidor.', 500)
    if not modelo['total_com_backup']:
        return _erro('Nenhum backup de configuração disponível para este cliente. '
                     'Execute os backups antes de gerar o AS-IS.')
    meta = composicao.metadados_padrao(modelo, _nome_usuario(request.user))
    doc = _criar_documento(cliente, DocumentoRede.TIPO_ASIS, meta, composicao.gerar_secoes(modelo),
                           request.user, 'Geração automática a partir dos backups',
                           coleta=composicao.resumo_coleta(modelo))
    return JsonResponse({
        'ok': True, 'id': doc.id, 'url': reverse('projeto_rede:editor', args=[doc.id]),
        'achados': len(modelo['achados']), 'equipamentos': modelo['total_com_backup'],
        'ia': modelo.get('ia_status'),
    })


@_admin_api
@require_POST
def gerar_hld(request, cliente_id):
    """HLD a partir do modelo ISP padrão, parametrizado pelo AS-IS quando há
    backups (ASN, /32 IPv6, RRs e BNGs sugeridos)."""
    cliente = get_object_or_404(Cliente, id=cliente_id)
    dados = _json(request) or {}
    params = {}
    try:
        modelo = _modelo_do_cliente(cliente)
        if modelo['total_com_backup']:
            params = cv.sugerir_parametros(modelo)
    except Exception:
        logger.exception('HLD: análise do cliente %s falhou; usando modelo sem parâmetros', cliente.id)
    for chave in ('asn', 'prefixo_v6'):
        valor = limpar_texto(dados.get(chave), 60)
        if valor:
            params[chave] = valor
    conv = cv.convencao_padrao(**params)
    meta = composicao_hld.metadados_padrao(conv, cliente.nome_empresa, _nome_usuario(request.user))
    doc = _criar_documento(cliente, DocumentoRede.TIPO_HLD, meta, documentos.gerar_secoes('hld', conv),
                           request.user, 'Criado a partir do modelo ISP padrão', dados={'convencao': conv})
    return JsonResponse({'ok': True, 'id': doc.id, 'url': reverse('projeto_rede:convencao', args=[doc.id])})


@_admin_api
@require_POST
def salvar_convencao(request, doc_id):
    doc = get_object_or_404(DocumentoRede, id=doc_id, tipo=DocumentoRede.TIPO_HLD)
    dados = _json(request)
    if not isinstance(dados, dict) or not isinstance(dados.get('convencao'), dict):
        return _erro('Requisição inválida.')
    entrada = dados['convencao']
    conv = documentos.convencao_do_hld(doc)
    for chave, _, _ in cv.CAMPOS:
        if chave in entrada:
            conv[chave] = limpar_texto(entrada[chave], 600)
    for chave, _, colunas in cv.TABELAS:
        linhas = entrada.get(chave)
        if not isinstance(linhas, list):
            continue
        conv[chave] = [
            {campo: limpar_texto(l.get(campo), 300) for campo, _ in colunas}
            for l in linhas[:200] if isinstance(l, dict) and any(str(l.get(c, '')).strip() for c, _ in colunas)
        ]
    if isinstance(entrada.get('regras_communities'), list):
        conv['regras_communities'] = [limpar_texto(x, 300) for x in entrada['regras_communities'][:30] if str(x).strip()]
    for chave in ('acesso_regra', 'ix_isolamento'):
        if chave in entrada:
            conv[chave] = limpar_texto(entrada[chave], 600)
    if isinstance(entrada.get('uplinks'), list):
        conv['uplinks'] = [limpar_texto(x, 300) for x in entrada['uplinks'][:30] if str(x).strip()]
    with transaction.atomic():
        doc.dados = {**(doc.dados or {}), 'convencao': conv}
        meta = dict(doc.metadados or {})
        meta['asn'] = conv.get('asn', '')
        doc.metadados = meta
        if dados.get('regerar', True):
            _recompor(doc, conv, request.user, 'Antes de aplicar a convenção editada')
        doc.atualizado_por = request.user
        doc.save()
    return JsonResponse({'ok': True, 'avisos': cv.validar(conv), 'url': reverse('projeto_rede:editor', args=[doc.id])})


@_admin_api
@require_POST
def gerar_tobe(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    dados = _json(request) or {}
    hld = documentos._obj(DocumentoRede, cliente, dados.get('hld_id'), tipo=DocumentoRede.TIPO_HLD)
    asis = documentos._obj(DocumentoRede, cliente, dados.get('asis_id'), tipo=DocumentoRede.TIPO_ASIS)
    cenario = documentos._obj(CenarioTopologia, cliente, dados.get('cenario_id'))
    try:
        ctx = documentos.contexto_tobe(cliente, hld, asis, cenario)
    except Exception:
        logger.exception('TO-BE: geração do plano do cliente %s falhou', cliente.id)
        return _erro('Falha ao montar o plano. Detalhes no log do servidor.', 500)
    meta = composicao_tobe.metadados_padrao(
        ctx['conv'], cliente.nome_empresa, f'{hld.titulo} v{hld.versao}' if hld else 'Modelo ISP padrão',
        _nome_usuario(request.user), documentos.data_extenso())
    doc = _criar_documento(
        cliente, DocumentoRede.TIPO_CHANGE_PLAN, meta, documentos.gerar_secoes('change_plan', ctx), request.user,
        'Geração automática (AS-IS + HLD + cenário)', documento_base=asis,
        dados={'hld_id': hld.id if hld else None, 'cenario_id': cenario.id if cenario else None,
               'mapeamentos': {}},
        coleta={'pendencias': len(ctx['plano']['pendencias']),
                'itens': sum(w['total'] for w in ctx['plano']['waves'])})
    return JsonResponse({'ok': True, 'id': doc.id, 'url': reverse('projeto_rede:tobe_mapeamentos', args=[doc.id]),
                         'pendencias': len(ctx['plano']['pendencias'])})


_MAPA_TEXTO = {'vrfs': 60, 'contratantes': 60, 'papeis': 80, 'bng_destino': 10, 'communities': 30}


@_admin_api
@require_POST
def salvar_mapeamentos(request, doc_id):
    doc = get_object_or_404(DocumentoRede, id=doc_id, tipo=DocumentoRede.TIPO_CHANGE_PLAN)
    dados = _json(request)
    if not isinstance(dados, dict):
        return _erro('Requisição inválida.')
    mapa = {}
    for chave, limite in _MAPA_TEXTO.items():
        valores = dados.get('mapeamentos', {}).get(chave) or {}
        if isinstance(valores, dict):
            mapa[chave] = {limpar_texto(k, 200): limpar_texto(v, limite) for k, v in list(valores.items())[:2000]}
    mapa['criticos'] = limpar_texto(dados.get('mapeamentos', {}).get('criticos'), 300)
    refs = dict(doc.dados or {})
    for campo, model, filtros in (('hld_id', DocumentoRede, {'tipo': DocumentoRede.TIPO_HLD}),
                                  ('cenario_id', CenarioTopologia, {})):
        if campo in dados:
            obj = documentos._obj(model, doc.cliente, dados.get(campo), **filtros)
            refs[campo] = obj.id if obj else None
    if 'asis_id' in dados:
        doc.documento_base = documentos._obj(DocumentoRede, doc.cliente, dados.get('asis_id'),
                                             tipo=DocumentoRede.TIPO_ASIS)
    refs['mapeamentos'] = mapa
    doc.dados = refs
    try:
        ctx = documentos.contexto(doc)
    except Exception:
        logger.exception('TO-BE: recálculo do plano %s falhou', doc.id)
        return _erro('Falha ao recalcular o plano. Detalhes no log do servidor.', 500)
    with transaction.atomic():
        _recompor(doc, ctx, request.user, 'Antes de aplicar mapeamentos')
        hld, _, _ = documentos.referencias_tobe(doc)
        meta = dict(doc.metadados or {})
        meta['hld'] = f'{hld.titulo} v{hld.versao}' if hld else 'Modelo ISP padrão'
        meta['asn'] = ctx['conv'].get('asn', meta.get('asn', ''))
        doc.metadados = meta
        doc.coleta = {'pendencias': len(ctx['plano']['pendencias']),
                      'itens': sum(w['total'] for w in ctx['plano']['waves'])}
        doc.save()
    return JsonResponse({'ok': True, 'url': reverse('projeto_rede:editor', args=[doc.id]),
                         'pendencias': ctx['plano']['pendencias']})


@_admin_api
@require_POST
def salvar(request, doc_id):
    doc = get_object_or_404(DocumentoRede, id=doc_id)
    dados = _json(request)
    if not isinstance(dados, dict):
        return _erro('Requisição inválida.')

    base = dados.get('base')
    if base and base != doc.atualizado_em.isoformat() and not dados.get('forcar'):
        return JsonResponse({
            'ok': False, 'conflito': True,
            'erro': f'O documento foi alterado por {_nome_usuario(doc.atualizado_por) or "outra sessão"} '
                    f'em {timezone.localtime(doc.atualizado_em):%d/%m/%Y %H:%M}.',
        }, status=409)

    secoes_in = dados.get('secoes')
    if not isinstance(secoes_in, list) or len(secoes_in) > LIMITE_SECOES:
        return _erro('Lista de seções inválida.')
    secoes = []
    chaves_validas = documentos.chaves(doc.tipo)
    for s in secoes_in:
        if not isinstance(s, dict):
            continue
        html = s.get('html') or ''
        if len(html) > LIMITE_HTML_SECAO:
            return _erro(f'A seção "{limpar_texto(s.get("titulo"), 80)}" é grande demais.')
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
    return JsonResponse({'ok': True, 'atualizado_em': doc.atualizado_em.isoformat(), 'secoes': secoes})


@_admin_api
@require_POST
def regenerar_secao(request, doc_id):
    """HTML recalculado de uma seção automática. Não grava — o editor
    substitui o conteúdo e o usuário salva."""
    doc = get_object_or_404(DocumentoRede.objects.select_related('cliente'), id=doc_id)
    dados = _json(request) or {}
    chave = dados.get('chave')
    if chave not in documentos.chaves(doc.tipo):
        return _erro('Seção sem geração automática.')
    asis = doc.tipo == DocumentoRede.TIPO_ASIS
    try:
        ctx = documentos.contexto(doc, com_ia=asis and chave in ia.SECOES)
    except Exception:
        logger.exception('projeto_rede: regeneração da seção %s do documento %s falhou', chave, doc.id)
        return _erro('Falha ao recalcular a seção.', 500)
    return JsonResponse({'ok': True, **documentos.gerar_secao(doc.tipo, ctx, chave),
                         'ia': ctx.get('ia_status') if asis else None})


@_admin_api
@require_POST
def regenerar_tudo(request, doc_id):
    """Recalcula todas as seções automáticas, preservando as seções criadas à
    mão. Guarda uma revisão antes."""
    doc = get_object_or_404(DocumentoRede.objects.select_related('cliente'), id=doc_id)
    try:
        ctx = documentos.contexto(doc, com_ia=True)
    except Exception:
        logger.exception('projeto_rede: regeneração do documento %s falhou', doc.id)
        return _erro('Falha ao recalcular o documento.', 500)
    with transaction.atomic():
        _recompor(doc, ctx, request.user, 'Antes de recalcular as seções automáticas')
        if doc.tipo == DocumentoRede.TIPO_ASIS:
            meta = dict(doc.metadados or {})
            meta['data_base'] = composicao.metadados_padrao(ctx)['data_base']
            doc.metadados = meta
            doc.coleta = composicao.resumo_coleta(ctx)
        doc.save()
    return JsonResponse({'ok': True, 'documento': _doc_json(doc),
                         'ia': ctx.get('ia_status') if doc.tipo == DocumentoRede.TIPO_ASIS else None})


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


# ═══════════════════════════════════════════════════════════════════════════
# API — cenários de topologia TO-BE
# ═══════════════════════════════════════════════════════════════════════════

@_admin_api
@require_POST
def cenario_criar(request, cliente_id):
    """Clona um mapa da topologia (o principal, por padrão) num cenário TO-BE.
    Os nós preservam `acesso_id`; `submap_id` sai para o cenário não abrir os
    sub-mapas reais do cliente."""
    cliente = get_object_or_404(Cliente, id=cliente_id)
    dados = _json(request) or {}
    origem = None
    if dados.get('origem_id'):
        origem = TopologiaDiagrama.objects.filter(cliente=cliente, id=dados['origem_id']).first()
    if not origem:
        origem = TopologiaDiagrama.objects.filter(cliente=cliente, pai__isnull=True).first()
    base = {'nodes': [], 'links': []}
    if origem:
        try:
            base = json.loads(origem.dados_json or '{}')
        except ValueError:
            pass
    for n in base.get('nodes', []):
        n.pop('submap_id', None)
        n.setdefault('tobe', {'estado': 'manter'})
    for lk in base.get('links', []):
        lk.setdefault('tobe', {'estado': 'manter', 'papel': 'principal'})
    nome = limpar_texto(dados.get('nome'), 200) or f'TO-BE — {origem.nome if origem else "novo"}'
    cen = CenarioTopologia.objects.create(
        cliente=cliente, nome=nome, origem=origem, criado_por=request.user,
        dados_json=json.dumps({'nodes': base.get('nodes', []), 'links': base.get('links', [])}, ensure_ascii=False))
    return JsonResponse({'ok': True, 'id': cen.id, 'url': reverse('projeto_rede:cenario_editor', args=[cen.id])})


@_admin_api
@require_POST
def cenario_salvar(request, cenario_id):
    cen = get_object_or_404(CenarioTopologia, id=cenario_id)
    if len(request.body) > LIMITE_CENARIO:
        return _erro('Cenário grande demais.')
    dados = _json(request)
    if not isinstance(dados, dict):
        return _erro('Body inválido.')
    if dados.get('nome'):
        cen.nome = limpar_texto(dados['nome'], 255)
    if 'dados_json' in dados:
        v = dados['dados_json']
        try:
            estrutura = json.loads(v) if isinstance(v, str) else v
        except ValueError:
            return _erro('dados_json inválido.')
        if not isinstance(estrutura, dict):
            return _erro('dados_json inválido.')
        cen.dados_json = json.dumps({'nodes': estrutura.get('nodes') or [], 'links': estrutura.get('links') or []},
                                    ensure_ascii=False)
    cen.save()
    return JsonResponse({'ok': True, 'diagrama_id': None, 'nome': cen.nome})


@_admin_api
@require_POST
def cenario_excluir(request, cenario_id):
    cen = get_object_or_404(CenarioTopologia, id=cenario_id)
    cliente_id = cen.cliente_id
    cen.delete()
    return JsonResponse({'ok': True, 'url': reverse('projeto_rede:lista', args=[cliente_id])})
