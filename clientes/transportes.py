"""Enlaces de transporte da topologia — sub-aba 'Transportes' em Documentação
de Rede.

Um enlace vira transporte pelo checkbox "Transporte" do painel do enlace no
editor (`link.transporte = true` no `dados_json`). Aqui só lemos os mapas
salvos e cruzamos com `TransporteEnlace`, que guarda a empresa que faz o
transporte. O mapa continua sendo a fonte: nada é copiado do link para o banco.
"""
import json

from .models import TopologiaDiagrama, TransporteEnlace


def _diagramas(cliente):
    """(diagrama, dados) de todos os mapas do cliente, mapa raiz primeiro —
    o enlace de um host agrupado também aparece no sub-mapa, e o original é
    o do mapa de cima (mesma ordem de `racks.services._diagramas`)."""
    resultado = []
    for d in TopologiaDiagrama.objects.filter(cliente=cliente).order_by('pai_id', 'id'):
        try:
            dados = json.loads(d.dados_json or '{}')
        except ValueError:
            continue
        if isinstance(dados, dict):
            resultado.append((d, dados))
    return sorted(resultado, key=lambda par: (par[0].pai_id is not None, par[0].id))


def _acesso_do_node(node):
    acesso_id = node.get('acesso_id')
    if not acesso_id and str(node.get('id', '')).startswith('crm_'):
        acesso_id = str(node['id'])[4:]
    try:
        return int(acesso_id) if acesso_id else None
    except (TypeError, ValueError):
        return None


def _ponta(node, porta, ip):
    node = node or {}
    return {
        'node_id': str(node.get('id') or ''),
        'label': node.get('label') or '',
        'ip_gerencia': node.get('ip') or '',
        'acesso_id': _acesso_do_node(node),
        'porta': str(porta or '').strip(),
        'ip': str(ip or '').strip(),
    }


def links_transporte(cliente):
    """Enlaces marcados como transporte em qualquer mapa do cliente, cada um
    uma vez só, já com a empresa/circuito/observação de `TransporteEnlace`."""
    registros = {t.topologia_link_id: t for t in
                 TransporteEnlace.objects.filter(cliente=cliente).select_related('empresa')}
    vistos, lista = set(), []
    for diagrama, dados in _diagramas(cliente):
        nodes = {str(n.get('id')): n for n in dados.get('nodes') or [] if isinstance(n, dict)}
        for link in dados.get('links') or []:
            if not isinstance(link, dict) or not link.get('transporte'):
                continue
            lid = str(link.get('id') or '')
            if not lid or lid in vistos:
                continue
            vistos.add(lid)
            reg = registros.get(lid)
            lista.append({
                'link_id': lid,
                'diagrama_id': diagrama.id,
                'diagrama': diagrama.nome,
                'label': link.get('label') or '',
                'iface': link.get('iface') or '',
                'vlan': link.get('vlan') or '',
                'a': _ponta(nodes.get(str(link.get('src'))), link.get('iface_a'), link.get('ip_local')),
                'b': _ponta(nodes.get(str(link.get('tgt'))), link.get('iface_b'), link.get('ip_remote')),
                'empresa_id': reg.empresa_id if reg else None,
                'empresa_nome': reg.empresa.nome if reg and reg.empresa else '',
                'circuito': reg.circuito if reg else '',
                'observacao': reg.observacao if reg else '',
            })
    lista.sort(key=lambda t: (t['empresa_nome'] == '', t['empresa_nome'].lower(), t['label'].lower()))
    return lista
