"""
Registro dos tipos de documento: seções automáticas, campos da capa e
contexto de geração. As views e a exportação só falam com este módulo.
"""
import os

from django.utils import timezone

from . import analise, coleta, composicao, composicao_hld, composicao_tobe
from . import convencao as cv
from . import tobe
from .models import CenarioTopologia, DocumentoRede

_REGISTRO = {
    DocumentoRede.TIPO_ASIS: (composicao.SECOES, composicao.CAMPOS_METADADOS),
    DocumentoRede.TIPO_HLD: (composicao_hld.SECOES, composicao_hld.CAMPOS_METADADOS),
    DocumentoRede.TIPO_CHANGE_PLAN: (composicao_tobe.SECOES, composicao_tobe.CAMPOS_METADADOS),
}

ROTULO_ATUALIZAR = {
    DocumentoRede.TIPO_ASIS: 'Atualizar com backups',
    DocumentoRede.TIPO_HLD: 'Atualizar pela convenção',
    DocumentoRede.TIPO_CHANGE_PLAN: 'Recalcular o plano',
}

MESES = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro',
         'Outubro', 'Novembro', 'Dezembro']


def secoes(tipo):
    return _REGISTRO[tipo][0]


def campos(tipo):
    return _REGISTRO[tipo][1]


def chaves(tipo):
    return {k for k, _, _ in secoes(tipo)}


def gerar_secao(tipo, ctx, chave):
    for k, titulo, func in secoes(tipo):
        if k == chave:
            return {'chave': k, 'titulo': titulo, 'html': func(ctx), 'auto': True}
    raise KeyError(chave)


def gerar_secoes(tipo, ctx):
    return [gerar_secao(tipo, ctx, k) for k, _, _ in secoes(tipo)]


def data_extenso(quando=None):
    quando = timezone.localtime(quando or timezone.now())
    return f'{MESES[quando.month - 1]} de {quando.year}'


# ═══════════════════════════════════════════════════════════════════════════
# Contextos
# ═══════════════════════════════════════════════════════════════════════════

def modelo_asis(cliente):
    return analise.montar_modelo(coleta.coletar(cliente))


def convencao_do_hld(doc_hld):
    return cv.normalizar((doc_hld.dados or {}).get('convencao') or {})


def hlds_do_cliente(cliente):
    return DocumentoRede.objects.filter(cliente=cliente, tipo=DocumentoRede.TIPO_HLD)


def _obj(model, cliente, pk, **filtros):
    if not pk:
        return None
    return model.objects.filter(cliente=cliente, id=pk, **filtros).first()


def referencias_tobe(doc):
    """HLD, AS-IS e cenário vinculados a um Change Plan (podem faltar)."""
    dados = doc.dados or {}
    hld = _obj(DocumentoRede, doc.cliente, dados.get('hld_id'), tipo=DocumentoRede.TIPO_HLD)
    asis = doc.documento_base if doc.documento_base_id and doc.documento_base.tipo == DocumentoRede.TIPO_ASIS else None
    cenario = _obj(CenarioTopologia, doc.cliente, dados.get('cenario_id'))
    return hld, asis, cenario


def contexto_tobe(cliente, hld=None, asis=None, cenario=None, mapeamentos=None, modelo=None):
    modelo = modelo or modelo_asis(cliente)
    conv = convencao_do_hld(hld) if hld else cv.convencao_padrao(**cv.sugerir_parametros(modelo))
    plano = tobe.gerar_plano(modelo, conv, cenario.dados() if cenario else None, mapeamentos)
    if not hld:
        plano['pendencias'].insert(0, 'Nenhum HLD vinculado: o plano usou o modelo ISP padrão como convenção.')
    documentos = []
    if hld:
        documentos.append(f'{hld.titulo} v{hld.versao}')
    if asis:
        documentos.append(f'{asis.titulo} v{asis.versao}')
    documentos.append(f'Cenário de topologia TO-BE: {cenario.nome}' if cenario
                      else 'Topologia atual do CRM (sem cenário TO-BE)')
    backups = []
    for eq in modelo['equipamentos']:
        b = eq.get('backup') or {}
        if b.get('arquivo_disponivel') and b.get('arquivo'):
            backups.append(os.path.basename(b['arquivo']))
    fontes = [
        f'AS-IS recalculado dos backups de {modelo["total_com_backup"]} equipamento(s) em '
        f'{timezone.localtime().strftime("%d/%m/%Y %H:%M")}.',
        f'Convenção: {hld.titulo} v{hld.versao}.' if hld else 'Convenção: modelo ISP padrão (sem HLD vinculado).',
        f'Topologia alvo: {cenario.nome}.' if cenario else 'Topologia: mapa atual do cliente.',
        'Mapeamentos manuais (VRFs, contratantes, papéis, communities) registrados no próprio documento.',
    ]
    return {'plano': plano, 'conv': conv, 'modelo': modelo,
            'refs': {'documentos': documentos, 'backups': sorted(backups), 'fontes': fontes}}


def contexto(doc):
    if doc.tipo == DocumentoRede.TIPO_ASIS:
        return modelo_asis(doc.cliente)
    if doc.tipo == DocumentoRede.TIPO_HLD:
        return convencao_do_hld(doc)
    hld, asis, cenario = referencias_tobe(doc)
    return contexto_tobe(doc.cliente, hld, asis, cenario, (doc.dados or {}).get('mapeamentos'))
