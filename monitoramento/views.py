"""
monitoramento/views.py
Views da aba de Monitoramento de Rede.
"""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from .models import MonitorLink, MonitorNode, MonitorTopology, ZabbixConfig
from . import services

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# HELPERS DE PERMISSÃO
# ──────────────────────────────────────────────────────────────

def _pode_acessar_cliente(request, cliente_id: str) -> bool:
    if request.user.is_staff or request.user.is_superuser:
        return True
    try:
        from clientes.models import Cliente
        c = Cliente.objects.get(usuario=request.user)
        return str(c.id) == str(cliente_id)
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────
# CONFIGURAÇÃO ZABBIX
# ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
@require_http_methods(["POST"])
def salvar_zabbix_config(request):
    cliente_id = request.POST.get('cliente')
    url        = request.POST.get('url', '').strip().rstrip('/')
    usuario    = request.POST.get('usuario', '').strip()
    senha      = request.POST.get('senha', '').strip()
    api_token  = request.POST.get('api_token', '').strip()

    if not cliente_id or not url:
        return JsonResponse({'error': 'URL e cliente são obrigatórios'}, status=400)

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    config, _ = ZabbixConfig.objects.update_or_create(
        cliente_id=cliente_id,
        defaults={
            'url':       url,
            'usuario':   usuario,
            'senha':     senha,
            'api_token': api_token or None,
            'ativo':     True,
        },
    )

    try:
        version = services.testar_conexao(config)
        return JsonResponse({'success': True, 'message': f'Conectado! Zabbix v{version}'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'Configurado, mas teste falhou: {e}'})


@login_required(login_url='login')
def buscar_zabbix_config(request):
    cliente_id = request.GET.get('id')
    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    try:
        config = ZabbixConfig.objects.get(cliente_id=cliente_id)
        return JsonResponse({
            'existe':    True,
            'url':       config.url,
            'usuario':   config.usuario,
            'tem_token': bool(config.api_token),
            'ativo':     config.ativo,
        })
    except ZabbixConfig.DoesNotExist:
        return JsonResponse({'existe': False})


@login_required(login_url='login')
def testar_zabbix_conexao(request):
    cliente_id = request.GET.get('id')
    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    try:
        config  = ZabbixConfig.objects.get(cliente_id=cliente_id, ativo=True)
        version = services.testar_conexao(config)
        return JsonResponse({'success': True, 'message': f'Zabbix v{version} — conexão OK'})
    except ZabbixConfig.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Zabbix não configurado para este cliente'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})


# ──────────────────────────────────────────────────────────────
# HOSTS E INTERFACES ZABBIX
# ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
def listar_hosts_zabbix(request):
    cliente_id = request.GET.get('id')
    busca      = request.GET.get('q', '')

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    try:
        config = ZabbixConfig.objects.get(cliente_id=cliente_id, ativo=True)
        hosts  = services.listar_hosts(config, busca)
        return JsonResponse({'hosts': hosts})
    except ZabbixConfig.DoesNotExist:
        return JsonResponse({'error': 'Zabbix não configurado para este cliente'}, status=400)
    except Exception as e:
        logger.exception("Erro ao listar hosts Zabbix")
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url='login')
def listar_interfaces_zabbix(request):
    cliente_id = request.GET.get('cliente_id')
    host_id    = request.GET.get('host_id')

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    try:
        config     = ZabbixConfig.objects.get(cliente_id=cliente_id, ativo=True)
        interfaces = services.listar_interfaces(config, host_id)
        return JsonResponse({'interfaces': interfaces})
    except ZabbixConfig.DoesNotExist:
        return JsonResponse({'error': 'Zabbix não configurado'}, status=400)
    except Exception as e:
        logger.exception("Erro ao listar interfaces")
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url='login')
def historico_item_zabbix(request):
    cliente_id = request.GET.get('cliente_id')
    item_id    = request.GET.get('item_id')
    hours      = int(request.GET.get('hours', 1))   # ← NOVO

    # Garante valor válido
    if hours not in (1, 3, 6, 12, 24):
        hours = 1

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    try:
        config  = ZabbixConfig.objects.get(cliente_id=cliente_id, ativo=True)
        history = services.historico_item(config, item_id, hours=hours)   # ← passa hours
        return JsonResponse({'history': history})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ──────────────────────────────────────────────────────────────
# CRUD DE TOPOLOGIAS
# ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
def listar_topologias(request):
    cliente_id = request.GET.get('id')
    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    topos = MonitorTopology.objects.filter(
        cliente_id=cliente_id
    ).prefetch_related('nodes', 'links').order_by('-data_atualizacao')

    return JsonResponse({
        'topologias': [{
            'id':               t.id,
            'nome':             t.nome,
            'descricao':        t.descricao,
            'data_atualizacao': t.data_atualizacao.strftime('%d/%m/%Y %H:%M'),
            'total_nodes':      t.nodes.count(),
            'total_links':      t.links.count(),
        } for t in topos]
    })


@login_required(login_url='login')
@require_http_methods(["POST"])
def criar_topologia(request):
    try:
        data       = json.loads(request.body)
        cliente_id = data.get('cliente_id')
        nome       = data.get('nome', '').strip()

        if not cliente_id or not nome:
            return JsonResponse({'error': 'cliente_id e nome são obrigatórios'}, status=400)

        if not _pode_acessar_cliente(request, cliente_id):
            return JsonResponse({'error': 'Sem permissão'}, status=403)

        topo = MonitorTopology.objects.create(
            cliente_id=cliente_id,
            nome=nome,
            descricao=data.get('descricao', ''),
        )
        return JsonResponse({'id': topo.id, 'nome': topo.nome})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url='login')
@require_http_methods(["POST"])
def salvar_topologia(request):
    try:
        data       = json.loads(request.body)
        topo_id    = data.get('topo_id')
        cliente_id = data.get('cliente_id')

        if not _pode_acessar_cliente(request, cliente_id):
            return JsonResponse({'error': 'Sem permissão'}, status=403)

        topo = get_object_or_404(MonitorTopology, id=topo_id, cliente_id=cliente_id)

        topo.nodes.all().delete()
        topo.links.all().delete()

        node_map: dict = {}
        for nd in data.get('nodes', []):
            node = MonitorNode.objects.create(
                topologia       = topo,
                tipo            = nd.get('tipo', 'switch'),
                label           = nd.get('label', 'Node'),
                zabbix_hostid   = nd.get('zabbix_hostid')   or None,
                zabbix_hostname = nd.get('zabbix_hostname') or None,
                pos_x           = nd.get('x', 200),
                pos_y           = nd.get('y', 200),
            )
            node_map[str(nd['id'])] = node

        for lk in data.get('links', []):
            src = node_map.get(str(lk['source']))
            dst = node_map.get(str(lk['target']))
            if src and dst:
                MonitorLink.objects.create(
                    topologia            = topo,
                    node_origem          = src,
                    node_destino         = dst,
                    label                = lk.get('label', ''),
                    zabbix_itemid_in     = lk.get('itemid_in')     or None,
                    zabbix_itemid_out    = lk.get('itemid_out')    or None,
                    zabbix_itemid_status = lk.get('itemid_status') or None,
                )

        topo.save()
        return JsonResponse({'success': True})

    except Exception as e:
        logger.exception("Erro ao salvar topologia")
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url='login')
def carregar_topologia(request):
    topo_id    = request.GET.get('topo_id')
    cliente_id = request.GET.get('cliente_id')

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    topo  = get_object_or_404(MonitorTopology, id=topo_id, cliente_id=cliente_id)
    nodes = MonitorNode.objects.filter(topologia=topo)
    links = MonitorLink.objects.filter(topologia=topo)

    return JsonResponse({
        'id':    topo.id,
        'nome':  topo.nome,
        'nodes': [{
            'id':              n.id,
            'tipo':            n.tipo,
            'label':           n.label,
            'zabbix_hostid':   n.zabbix_hostid,
            'zabbix_hostname': n.zabbix_hostname,
            'x':               n.pos_x,
            'y':               n.pos_y,
        } for n in nodes],
        'links': [{
            'id':            lk.id,
            'source':        lk.node_origem_id,
            'target':        lk.node_destino_id,
            'label':         lk.label,
            'itemid_in':     lk.zabbix_itemid_in,
            'itemid_out':    lk.zabbix_itemid_out,
            'itemid_status': lk.zabbix_itemid_status,
        } for lk in links],
    })


@login_required(login_url='login')
@require_http_methods(["POST"])
def deletar_topologia(request, topo_id):
    topo       = get_object_or_404(MonitorTopology, id=topo_id)
    cliente_id = topo.cliente_id

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    topo.delete()
    return JsonResponse({'success': True, 'cliente_id': cliente_id})


# ──────────────────────────────────────────────────────────────
# STATUS EM TEMPO REAL
# ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
def status_topologia(request):
    topo_id    = request.GET.get('topo_id')
    cliente_id = request.GET.get('cliente_id')

    if not _pode_acessar_cliente(request, cliente_id):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    try:
        config    = ZabbixConfig.objects.get(cliente_id=cliente_id, ativo=True)
        topologia = get_object_or_404(MonitorTopology, id=topo_id, cliente_id=cliente_id)

        nodes = list(MonitorNode.objects.filter(topologia=topologia))
        links = list(MonitorLink.objects.filter(topologia=topologia))

        host_ids = list({n.zabbix_hostid for n in nodes if n.zabbix_hostid})
        item_ids = []
        for lk in links:
            for fid in [lk.zabbix_itemid_in, lk.zabbix_itemid_out, lk.zabbix_itemid_status]:
                if fid:
                    item_ids.append(fid)

        host_status = services.status_nodes(config, host_ids)
        item_values = services.status_items(config, item_ids)

        # ── Nodes ─────────────────────────────────────────
        nodes_resp = {}
        for n in nodes:
            nodes_resp[n.id] = {
                'status': host_status.get(n.zabbix_hostid, 'unknown')
                          if n.zabbix_hostid else 'unconfigured',
                'hostid': n.zabbix_hostid,
            }

        # ── Links ─────────────────────────────────────────
        links_resp = {}
        for lk in links:
            val_in     = item_values.get(lk.zabbix_itemid_in,     {}) if lk.zabbix_itemid_in     else {}
            val_out    = item_values.get(lk.zabbix_itemid_out,    {}) if lk.zabbix_itemid_out    else {}
            val_status = item_values.get(lk.zabbix_itemid_status, {}) if lk.zabbix_itemid_status else {}

            # Define tráfego ANTES de usar no status
            traffic_in  = val_in.get('value')  if val_in  else None
            traffic_out = val_out.get('value')  if val_out else None

            # Status: usa item dedicado, senão infere pelo tráfego
            raw_status = val_status.get('value') if val_status else None
            if raw_status is not None:
                link_up = str(raw_status).strip() == '1'
            elif traffic_in is not None or traffic_out is not None:
                total   = float(traffic_in or 0) + float(traffic_out or 0)
                link_up = total > 0
            else:
                link_up = None

            links_resp[lk.id] = {
                'status':      'up'   if link_up is True
                          else 'down' if link_up is False
                          else 'unknown',
                'traffic_in':  services.format_bps(traffic_in)  if traffic_in  is not None else None,
                'traffic_out': services.format_bps(traffic_out) if traffic_out is not None else None,
                'raw_in':      traffic_in,
                'raw_out':     traffic_out,
            }

        return JsonResponse({'success': True, 'nodes': nodes_resp, 'links': links_resp})

    except ZabbixConfig.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Zabbix não configurado'}, status=400)
    except Exception as e:
        logger.exception("Erro ao buscar status da topologia")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
