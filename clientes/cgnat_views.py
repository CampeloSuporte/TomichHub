"""
clientes/cgnat_views.py
Gerador de CGNAT MikroTik (v6/v7): tela própria, API usada pela tela e pela
aba CGNAT do painel ⚡ Scripts do terminal, e download do .rsc/.csv.
Ver docs/cgnat_mikrotik.md.
"""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import cgnat
from .decorators import ferramenta_instancia_required
from .models import Acesso, Cliente, Documento
from usuario.perms import pode_acessar_acesso, pode_acessar_cliente

logger = logging.getLogger(__name__)

# Linhas da tabela de mapeamento que vão na resposta para pré-visualizar;
# a tabela inteira sai no CSV (download ou Documentos).
PREVIEW_LINHAS = 2048


def _bool(v, padrao=True):
    if v in (None, ''):
        return padrao
    return str(v).lower() in ('1', 'true', 'on', 'sim', 'yes')


def _planejar(dados):
    plano = cgnat.planejar(
        dados.get('publico'), dados.get('privado'),
        porta_inicial=dados.get('porta_inicial'),
        porta_final=dados.get('porta_final'),
        portas_por_cliente=dados.get('portas_por_cliente'),
    )
    versao = dados.get('versao') or 'v6'
    script = cgnat.gerar_script(plano, versao,
                                icmp=_bool(dados.get('icmp')),
                                fallback=_bool(dados.get('fallback')))
    return plano, versao, script


def _cliente_alvo(request, dados):
    """Cliente onde salvar os Documentos (por cliente_id ou pelo acesso do terminal)."""
    acesso_id = dados.get('acesso_id')
    if acesso_id:
        acesso = Acesso.objects.select_related('cliente').filter(id=acesso_id).first()
        if not acesso or not pode_acessar_acesso(request.user, acesso):
            return None
        return acesso.cliente
    cliente_id = dados.get('cliente_id')
    if cliente_id:
        cliente = Cliente.objects.filter(id=cliente_id).first()
        if cliente and pode_acessar_cliente(request.user, cliente):
            return cliente
    return None


def salvar_documentos(cliente, plano, versao, script):
    """Grava o .rsc e o .csv de mapeamento nos Documentos do cliente."""
    base = f'{cgnat.nome_base(plano, versao)}_{timezone.localtime():%Y%m%d_%H%M}'
    docs = []
    for ext, conteudo in (('rsc', script), ('csv', cgnat.gerar_csv(plano))):
        nome = f'{base}.{ext}'
        doc = Documento(cliente=cliente, nome=nome)
        doc.arquivo.save(nome, ContentFile(conteudo.encode('utf-8')), save=True)
        docs.append({'id': doc.id, 'nome': doc.nome, 'url': doc.arquivo.url})
    return docs


@login_required(login_url='login')
@ferramenta_instancia_required('scripts')
def gerador_cgnat(request):
    """GET /clientes/cgnat/ — tela do gerador."""
    clientes = Cliente.objects.visiveis_para(request.user).order_by('nome_empresa')
    return render(request, 'cgnat/gerador.html', {
        'clientes': clientes.only('id', 'nome_empresa'),
        'cliente_sel': request.GET.get('cliente', ''),
    })


@login_required(login_url='login')
@require_http_methods(['POST'])
@ferramenta_instancia_required('scripts')
def gerar_cgnat(request):
    """POST /clientes/cgnat/gerar/ (JSON)
    { publico, privado, versao: v6|v7, porta_inicial, porta_final,
      portas_por_cliente, icmp, fallback, salvar, cliente_id | acesso_id }
    """
    try:
        dados = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'ok': False, 'erro': 'JSON inválido.'}, status=400)
    try:
        plano, versao, script = _planejar(dados)
    except cgnat.CGNATErro as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)

    resp = {
        'ok': True,
        'versao': versao,
        'script': script,
        'resumo': plano.resumo(),
        'avisos': plano.avisos,
        'fatias': [{'privado': str(f.privado), 'portas': f.portas,
                    'porta_ini': f.porta_ini, 'porta_fim': f.porta_fim} for f in plano.fatias],
        'mapeamento': plano.mapeamento()[:PREVIEW_LINHAS],
        'mapeamento_total': plano.ips_privados,
        'documentos': [],
    }

    if _bool(dados.get('salvar'), padrao=False):
        cliente = _cliente_alvo(request, dados)
        if cliente is None:
            resp['avisos'] = resp['avisos'] + ['Não salvo nos Documentos: cliente não informado ou sem permissão.']
        else:
            resp['documentos'] = salvar_documentos(cliente, plano, versao, script)
            resp['cliente'] = {'id': cliente.id, 'nome': cliente.nome_empresa}
            logger.info('[CGNAT] %s gerou %s <= %s (%s) para cliente %s', request.user,
                        plano.publico, plano.privado, versao, cliente.id)
    return JsonResponse(resp)


@login_required(login_url='login')
@ferramenta_instancia_required('scripts')
def baixar_cgnat(request):
    """GET /clientes/cgnat/baixar/?formato=rsc|csv&publico=..&privado=..&versao=.."""
    try:
        plano, versao, script = _planejar(request.GET)
    except cgnat.CGNATErro as e:
        return HttpResponse(str(e), status=400, content_type='text/plain; charset=utf-8')
    if request.GET.get('formato') == 'csv':
        conteudo, ext, ctype = cgnat.gerar_csv(plano), 'csv', 'text/csv; charset=utf-8'
    else:
        conteudo, ext, ctype = script, 'rsc', 'text/plain; charset=utf-8'
    resp = HttpResponse(conteudo, content_type=ctype)
    resp['Content-Disposition'] = f'attachment; filename="{cgnat.nome_base(plano, versao)}.{ext}"'
    return resp
