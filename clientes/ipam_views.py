"""
ipam_views.py — CRUD e importação do IPAM nativo
"""
import csv
import io
import ipaddress
import json
import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models import (
    Cliente, Acesso,
    IPAMVlan, IPAMPrefixo, IPAMSubRede, IPAMEndereco, IPAMVpnDoc,
)

logger = logging.getLogger(__name__)


def _cliente(request, cliente_id):
    return get_object_or_404(Cliente, id=cliente_id)


def _json(request):
    try:
        return json.loads(request.body)
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# VLANs
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def ipam_vlans_listar(request, cliente_id):
    c = _cliente(request, cliente_id)
    qs = IPAMVlan.objects.filter(cliente=c)
    data = [
        {'id': v.id, 'numero': v.numero, 'nome': v.nome,
         'descricao': v.descricao, 'status': v.status,
         'subredes': v.subredes.count()}
        for v in qs
    ]
    return JsonResponse({'ok': True, 'vlans': data})


@login_required
@require_http_methods(['POST'])
def ipam_vlan_salvar(request, cliente_id):
    c   = _cliente(request, cliente_id)
    body = _json(request)
    vid = body.get('id')
    try:
        numero = int(body['numero'])
        if not 1 <= numero <= 4094:
            raise ValueError('VLAN fora do range 1-4094')
        if vid:
            obj = get_object_or_404(IPAMVlan, id=vid, cliente=c)
        else:
            obj = IPAMVlan(cliente=c)
        obj.numero    = numero
        obj.nome      = body.get('nome', '').strip() or f'VLAN {numero}'
        obj.descricao = body.get('descricao', '').strip()
        obj.status    = body.get('status', 'ativo')
        obj.save()
        return JsonResponse({'ok': True, 'id': obj.id})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def ipam_vlan_deletar(request, vlan_id):
    obj = get_object_or_404(IPAMVlan, id=vlan_id)
    obj.delete()
    return JsonResponse({'ok': True})


# ─────────────────────────────────────────────────────────────────────────────
# Prefixos
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def ipam_prefixos_listar(request, cliente_id):
    c  = _cliente(request, cliente_id)
    qs = IPAMPrefixo.objects.filter(cliente=c)
    data = []
    for p in qs:
        sub_count = p.subredes.count()
        data.append({
            'id': p.id, 'prefixo': p.prefixo, 'tipo': p.tipo,
            'status': p.status, 'descricao': p.descricao,
            'local': p.local, 'subredes': sub_count, 'pool_cheia': p.pool_cheia,
        })
    data.sort(key=lambda x: (ipaddress.ip_network(x['prefixo'], strict=False).version,
                              ipaddress.ip_network(x['prefixo'], strict=False)))
    return JsonResponse({'ok': True, 'prefixos': data})


@login_required
@require_http_methods(['POST'])
def ipam_prefixo_salvar(request, cliente_id):
    c    = _cliente(request, cliente_id)
    body = _json(request)
    pid  = body.get('id')
    try:
        prefixo_str = body.get('prefixo', '').strip()
        ipaddress.ip_network(prefixo_str, strict=False)  # valida CIDR
        if pid:
            obj = get_object_or_404(IPAMPrefixo, id=pid, cliente=c)
        else:
            obj = IPAMPrefixo(cliente=c)
        obj.prefixo   = prefixo_str
        obj.tipo      = body.get('tipo', 'rede')
        obj.status    = body.get('status', 'ativo')
        obj.descricao = body.get('descricao', '').strip()
        obj.local     = body.get('local', '').strip()
        obj.save()
        return JsonResponse({'ok': True, 'id': obj.id})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def ipam_prefixo_deletar(request, prefixo_id):
    obj = get_object_or_404(IPAMPrefixo, id=prefixo_id)
    obj.delete()
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(["POST"])
def ipam_prefixo_dividir(request, prefixo_id):
    """
    Divide um prefixo em N sub-redes iguais do tamanho prefixlen.
    Cria registros em IPAMSubRede para cada bloco.
    """
    pobj = get_object_or_404(IPAMPrefixo, id=prefixo_id)
    try:
        body = _json(request)
        target_pl   = int(body.get('prefixlen', 0))
        descricao   = body.get('descricao', '').strip()
        apenas_um   = body.get('apenas_um', False)   # criar só o primeiro bloco

        prefixo_net = ipaddress.ip_network(pobj.prefixo, strict=False)
        max_pl      = 32 if prefixo_net.version == 4 else 128

        if target_pl <= prefixo_net.prefixlen or target_pl > max_pl:
            return JsonResponse({'ok': False, 'erro': f'prefixlen deve ser entre /{prefixo_net.prefixlen+1} e /{max_pl}'}, status=400)

        subnets = list(prefixo_net.subnets(new_prefix=target_pl))
        if apenas_um:
            subnets = subnets[:1]

        # Não duplicar redes já existentes
        existentes = set(IPAMSubRede.objects.filter(cliente=pobj.cliente)
                         .values_list('rede', flat=True))
        criados, pulados = [], []
        for subnet in subnets:
            rede_str = str(subnet)
            if rede_str in existentes:
                pulados.append(rede_str)
                continue
            IPAMSubRede.objects.create(
                cliente=pobj.cliente,
                prefixo=pobj,
                rede=rede_str,
                status='ativo',
                descricao=descricao,
            )
            criados.append(rede_str)

        return JsonResponse({'ok': True, 'criados': len(criados), 'pulados': len(pulados), 'subredes': criados})
    except Exception as e:
        logger.error(f'ipam_prefixo_dividir: {e}')
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def ipam_subrede_dividir(request, subrede_id):
    """Divide uma sub-rede em N sub-redes menores (herda prefixo pai)."""
    sobj = get_object_or_404(IPAMSubRede, id=subrede_id)
    try:
        body      = _json(request)
        target_pl = int(body.get('prefixlen', 0))
        descricao = body.get('descricao', '').strip()
        apenas_um = body.get('apenas_um', False)

        parent_net = ipaddress.ip_network(sobj.rede, strict=False)
        max_pl     = 32 if parent_net.version == 4 else 128

        if target_pl <= parent_net.prefixlen or target_pl > max_pl:
            return JsonResponse({'ok': False, 'erro': f'prefixlen deve ser maior que /{parent_net.prefixlen}'}, status=400)

        subnets = list(parent_net.subnets(new_prefix=target_pl))
        if apenas_um:
            subnets = subnets[:1]

        existentes = set(IPAMSubRede.objects.filter(cliente=sobj.cliente).values_list('rede', flat=True))
        criados, pulados = [], []
        for subnet in subnets:
            rede_str = str(subnet)
            if rede_str in existentes:
                pulados.append(rede_str)
                continue
            IPAMSubRede.objects.create(
                cliente=sobj.cliente,
                prefixo=sobj.prefixo,
                rede=rede_str,
                status='ativo',
                descricao=descricao,
            )
            criados.append(rede_str)

        return JsonResponse({'ok': True, 'criados': len(criados), 'pulados': len(pulados), 'subredes': criados})
    except Exception as e:
        logger.error(f'ipam_subrede_dividir: {e}')
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def ipam_prefixo_marcar_em_uso(request, prefixo_id):
    """Cria uma sub-rede única que cobre todo o prefixo (100% em uso)."""
    pobj = get_object_or_404(IPAMPrefixo, id=prefixo_id)
    try:
        body = _json(request)
        descricao = body.get('descricao', '').strip() or 'Em uso'
        if IPAMSubRede.objects.filter(cliente=pobj.cliente, rede=pobj.prefixo).exists():
            return JsonResponse({'ok': False, 'erro': 'Já existe sub-rede com este CIDR'}, status=400)
        sr = IPAMSubRede.objects.create(
            cliente=pobj.cliente,
            prefixo=pobj,
            rede=pobj.prefixo,
            status='ativo',
            descricao=descricao,
        )
        return JsonResponse({'ok': True, 'subrede_id': sr.id})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def ipam_prefixo_pool_cheia(request, prefixo_id):
    """Alterna o flag pool_cheia do prefixo."""
    pobj = get_object_or_404(IPAMPrefixo, id=prefixo_id)
    pobj.pool_cheia = not pobj.pool_cheia
    pobj.save(update_fields=['pool_cheia'])
    return JsonResponse({'ok': True, 'pool_cheia': pobj.pool_cheia})


@login_required
def ipam_prefixo_breakdown(request, prefixo_id):
    """
    Retorna o breakdown de um prefixo:
    - sub-redes já alocadas dentro dele
    - espaço livre (gaps) como CIDRs
    - sugestões de tamanhos para o espaço livre
    """
    pobj = get_object_or_404(IPAMPrefixo, id=prefixo_id)
    try:
        prefixo_net = ipaddress.ip_network(pobj.prefixo, strict=False)
    except ValueError as e:
        return JsonResponse({'ok': False, 'erro': f'Prefixo inválido: {e}'}, status=400)

    version = prefixo_net.version

    # Coletar sub-redes do cliente que estão dentro deste prefixo
    alocadas = []
    for s in IPAMSubRede.objects.filter(cliente=pobj.cliente).select_related('vlan'):
        try:
            snet = ipaddress.ip_network(s.rede, strict=False)
            if snet.version == version and snet.subnet_of(prefixo_net):
                total = snet.num_addresses
                used  = s.ips.count()
                pct   = round(used / total * 100, 1) if total else 0
                alocadas.append({
                    'id':       s.id,
                    'rede':     str(snet),
                    'prefixlen': snet.prefixlen,
                    'gateway':  s.gateway,
                    'descricao': s.descricao,
                    'local':    s.local,
                    'status':   s.status,
                    'vlan':     str(s.vlan) if s.vlan else '',
                    'vlan_id':  s.vlan_id,
                    'total_hosts': total,
                    'usados':   used,
                    'pct':      pct,
                    '_net':     snet,   # usado internamente — removido antes de serializar
                })
        except Exception:
            pass

    # Ordenar por endereço de rede
    alocadas.sort(key=lambda x: x['_net'].network_address)

    # Calcular espaço livre (gaps)
    livres = _calcular_livres(prefixo_net, [a['_net'] for a in alocadas], version)

    # Remover campo interno antes de serializar
    for a in alocadas:
        del a['_net']

    # Estatísticas gerais
    total_prefixo = prefixo_net.num_addresses
    total_alocado = sum(
        ipaddress.ip_network(a['rede'], strict=False).num_addresses for a in alocadas
    )
    pct_alocado = round(total_alocado / total_prefixo * 100, 2) if total_prefixo else 0

    # IPv6 counts excedem JS Number (max ~9×10^15) — enviar como string
    if version == 6:
        for a in alocadas:
            a['total_hosts'] = str(a['total_hosts'])
        total_ips_json    = str(total_prefixo)
        total_alocado_json = str(total_alocado)
    else:
        total_ips_json    = total_prefixo
        total_alocado_json = total_alocado

    return JsonResponse({
        'ok':          True,
        'prefixo':     str(prefixo_net),
        'prefixlen':   prefixo_net.prefixlen,
        'version':     version,
        'descricao':   pobj.descricao,
        'tipo':        pobj.tipo,
        'status':      pobj.status,
        'total_ips':   total_ips_json,
        'total_alocado': total_alocado_json,
        'pct_alocado': pct_alocado,
        'alocadas':    alocadas,
        'livres':      livres,
    })


def _calcular_livres(prefixo_net, alocadas_nets, version):
    """
    Encontra os gaps (espaço não alocado) dentro de prefixo_net.
    Retorna lista de dicts com rede CIDR, tamanho e sugestões de subdivisão.
    """
    p_start = int(prefixo_net.network_address)
    p_end   = int(prefixo_net.broadcast_address)

    # Colapsar sobreposições nas alocadas
    try:
        collapsed = list(ipaddress.collapse_addresses(alocadas_nets))
    except Exception:
        collapsed = sorted(alocadas_nets, key=lambda n: n.network_address)

    livres = []
    cursor = p_start

    for net in sorted(collapsed, key=lambda n: int(n.network_address)):
        net_start = int(net.network_address)
        net_end   = int(net.broadcast_address)

        if net_start > cursor:
            # Há um gap entre cursor e net_start-1
            gap_start = ipaddress.ip_address(cursor)
            gap_end   = ipaddress.ip_address(net_start - 1)
            try:
                cidrs = list(ipaddress.summarize_address_range(gap_start, gap_end))
                for c in cidrs:
                    livres.append(_livre_dict(c))
            except Exception:
                pass

        cursor = max(cursor, net_end + 1)

    # Gap final
    if cursor <= p_end:
        gap_start = ipaddress.ip_address(cursor)
        gap_end   = ipaddress.ip_address(p_end)
        try:
            cidrs = list(ipaddress.summarize_address_range(gap_start, gap_end))
            for c in cidrs:
                livres.append(_livre_dict(c))
        except Exception:
            pass

    return livres


def _livre_dict(net):
    """Gera dict para um bloco de espaço livre, incluindo sugestões de subdivisão."""
    num = net.num_addresses
    prefixlen = net.prefixlen
    version   = net.version

    sugestoes = []
    max_prefix = 32 if version == 4 else 128

    for pl in range(prefixlen + 1, min(max_prefix + 1, prefixlen + 9)):
        tamanho = 2 ** (max_prefix - pl)
        if tamanho <= num:
            label = _prefixlen_label(pl, version)
            # IPv6 counts são grandes demais para JS Number — enviar como string
            sugestoes.append({'prefixlen': pl, 'tamanho': tamanho if version == 4 else str(tamanho), 'label': label})
        if len(sugestoes) >= 6:
            break

    return {
        'rede':       str(net),
        'prefixlen':  prefixlen,
        'tamanho':    num if version == 4 else str(num),
        'sugestoes':  sugestoes,
    }


def _prefixlen_label(prefixlen, version=4):
    max_prefix = 32 if version == 4 else 128
    n = 2 ** (max_prefix - prefixlen)
    if version == 4:
        if prefixlen <= 16:
            return f'/{prefixlen} ({n:,} IPs)'
        hosts = max(0, n - 2)
        return f'/{prefixlen} ({hosts} hosts)'
    return f'/{prefixlen} ({n:,} endereços)'


# ─────────────────────────────────────────────────────────────────────────────
# Sub-redes
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def ipam_subredes_listar(request, cliente_id):
    c       = _cliente(request, cliente_id)
    filtro  = request.GET.get('prefixo_id')
    qs      = IPAMSubRede.objects.filter(cliente=c).select_related('prefixo', 'vlan')
    if filtro:
        qs = qs.filter(prefixo_id=filtro)

    data = []
    for s in qs:
        total = s.total_hosts()
        used  = s.usados()
        pct   = round(used / total * 100, 1) if total else 0
        data.append({
            'id': s.id, 'rede': s.rede, 'gateway': s.gateway,
            'descricao': s.descricao, 'local': s.local, 'status': s.status,
            'prefixo_id': s.prefixo_id,
            'prefixo':    s.prefixo.prefixo if s.prefixo else '',
            'vlan_id':    s.vlan_id,
            'vlan':       str(s.vlan) if s.vlan else '',
            'total_hosts': total, 'usados': used, 'utilizacao_pct': pct,
        })
    data.sort(key=lambda x: (ipaddress.ip_network(x['rede'], strict=False).version,
                              ipaddress.ip_network(x['rede'], strict=False)))
    return JsonResponse({'ok': True, 'subredes': data})


@login_required
@require_http_methods(['POST'])
def ipam_subrede_salvar(request, cliente_id):
    c    = _cliente(request, cliente_id)
    body = _json(request)
    sid  = body.get('id')
    try:
        rede_str = body.get('rede', '').strip()
        ipaddress.ip_network(rede_str, strict=False)
        if sid:
            obj = get_object_or_404(IPAMSubRede, id=sid, cliente=c)
        else:
            obj = IPAMSubRede(cliente=c)
        obj.rede      = rede_str
        obj.gateway   = body.get('gateway', '').strip()
        obj.descricao = body.get('descricao', '').strip()
        obj.local     = body.get('local', '').strip()
        obj.status    = body.get('status', 'ativo')
        # FK opcionais
        pid = body.get('prefixo_id')
        obj.prefixo = IPAMPrefixo.objects.filter(id=pid, cliente=c).first() if pid else None
        vid = body.get('vlan_id')
        obj.vlan = IPAMVlan.objects.filter(id=vid, cliente=c).first() if vid else None
        obj.save()
        return JsonResponse({'ok': True, 'id': obj.id})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def ipam_subrede_deletar(request, subrede_id):
    obj = get_object_or_404(IPAMSubRede, id=subrede_id)
    obj.delete()
    return JsonResponse({'ok': True})


@login_required
def ipam_subrede_ips(request, subrede_id):
    """Lista IPs de uma sub-rede específica."""
    s  = get_object_or_404(IPAMSubRede, id=subrede_id)
    qs = IPAMEndereco.objects.filter(subrede=s).select_related('acesso')
    data = [_ip_dict(e) for e in qs]
    return JsonResponse({'ok': True, 'ips': data, 'subrede': s.rede})


# ─────────────────────────────────────────────────────────────────────────────
# Endereços IP
# ─────────────────────────────────────────────────────────────────────────────

def _ip_dict(e):
    return {
        'id': e.id, 'ip': e.ip, 'tipo': e.tipo, 'status': e.status,
        'hostname': e.hostname, 'descricao': e.descricao,
        'mac_address': e.mac_address,
        'subrede_id': e.subrede_id,
        'subrede':    e.subrede.rede if e.subrede else '',
        'acesso_id':  e.acesso_id,
        'acesso':     str(e.acesso) if e.acesso else '',
    }


@login_required
def ipam_ips_listar(request, cliente_id):
    c       = _cliente(request, cliente_id)
    filtro_sub = request.GET.get('subrede_id')
    busca      = request.GET.get('q', '').strip()
    qs         = IPAMEndereco.objects.filter(cliente=c).select_related('subrede', 'acesso')
    if filtro_sub:
        qs = qs.filter(subrede_id=filtro_sub)
    if busca:
        from django.db.models import Q
        qs = qs.filter(
            Q(ip__icontains=busca) | Q(hostname__icontains=busca) |
            Q(descricao__icontains=busca) | Q(mac_address__icontains=busca)
        )
    data = [_ip_dict(e) for e in qs[:500]]
    return JsonResponse({'ok': True, 'ips': data, 'total': qs.count()})


@login_required
@require_http_methods(['POST'])
def ipam_ip_salvar(request, cliente_id):
    c    = _cliente(request, cliente_id)
    body = _json(request)
    eid  = body.get('id')
    try:
        ip_str = body.get('ip', '').strip()
        ipaddress.ip_address(ip_str)
        if eid:
            obj = get_object_or_404(IPAMEndereco, id=eid, cliente=c)
        else:
            obj = IPAMEndereco(cliente=c)
        obj.ip          = ip_str
        obj.tipo        = body.get('tipo', 'fixo')
        obj.status      = body.get('status', 'ativo')
        obj.hostname    = body.get('hostname', '').strip()
        obj.descricao   = body.get('descricao', '').strip()
        obj.mac_address = body.get('mac_address', '').strip()
        sid = body.get('subrede_id')
        obj.subrede = IPAMSubRede.objects.filter(id=sid, cliente=c).first() if sid else None
        aid = body.get('acesso_id')
        obj.acesso = Acesso.objects.filter(id=aid, cliente=c).first() if aid else None
        obj.save()
        return JsonResponse({'ok': True, 'id': obj.id})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def ipam_ip_deletar(request, ip_id):
    obj = get_object_or_404(IPAMEndereco, id=ip_id)
    obj.delete()
    return JsonResponse({'ok': True})


# ─────────────────────────────────────────────────────────────────────────────
# VPNs documentadas
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def ipam_vpns_listar(request, cliente_id):
    c  = _cliente(request, cliente_id)
    qs = IPAMVpnDoc.objects.filter(cliente=c)
    data = [
        {'id': v.id, 'nome': v.nome, 'tipo': v.tipo,
         'tipo_display': v.get_tipo_display(),
         'endpoint_local': v.endpoint_local, 'endpoint_remoto': v.endpoint_remoto,
         'rede_local': v.rede_local, 'rede_remota': v.rede_remota,
         'as_local': v.as_local, 'as_remoto': v.as_remoto,
         'descricao': v.descricao, 'status': v.status}
        for v in qs
    ]
    return JsonResponse({'ok': True, 'vpns': data})


@login_required
@require_http_methods(['POST'])
def ipam_vpn_salvar(request, cliente_id):
    c    = _cliente(request, cliente_id)
    body = _json(request)
    vid  = body.get('id')
    try:
        if vid:
            obj = get_object_or_404(IPAMVpnDoc, id=vid, cliente=c)
        else:
            obj = IPAMVpnDoc(cliente=c)
        obj.nome            = body.get('nome', '').strip()
        obj.tipo            = body.get('tipo', 'ipsec')
        obj.endpoint_local  = body.get('endpoint_local', '').strip()
        obj.endpoint_remoto = body.get('endpoint_remoto', '').strip()
        obj.rede_local      = body.get('rede_local', '').strip()
        obj.rede_remota     = body.get('rede_remota', '').strip()
        obj.as_local        = body.get('as_local', '').strip()
        obj.as_remoto       = body.get('as_remoto', '').strip()
        obj.descricao       = body.get('descricao', '').strip()
        obj.status          = body.get('status', 'ativo')
        obj.save()
        return JsonResponse({'ok': True, 'id': obj.id})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def ipam_vpn_deletar(request, vpn_id):
    obj = get_object_or_404(IPAMVpnDoc, id=vpn_id)
    obj.delete()
    return JsonResponse({'ok': True})


# ─────────────────────────────────────────────────────────────────────────────
# Importação phpIPAM (CSV)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def ipam_importar(request, cliente_id):
    """
    Importa CSV exportado do phpIPAM.
    Aceita dois tipos (detectados automaticamente):
      • Subnets CSV  — colunas: subnet / mask / description / VLAN name / location / ...
      • IPs CSV      — colunas: ip_addr / hostname / description / mac / state / ...
    """
    c    = _cliente(request, cliente_id)
    f    = request.FILES.get('arquivo')
    modo = request.POST.get('modo', 'auto')   # 'subnets' | 'ips' | 'auto'

    if not f:
        return JsonResponse({'ok': False, 'erro': 'Nenhum arquivo enviado.'}, status=400)

    try:
        raw = f.read().decode('utf-8-sig', errors='replace')
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': f'Erro ao ler arquivo: {e}'}, status=400)

    reader   = csv.DictReader(io.StringIO(raw))
    headers  = [h.lower().strip() for h in (reader.fieldnames or [])]

    # Auto-detectar tipo
    if modo == 'auto':
        if 'ip_addr' in headers or 'ipaddress' in headers:
            modo = 'ips'
        elif 'subnet' in headers or 'network address' in headers:
            modo = 'subnets'
        elif 'subnetaddress' in headers:
            modo = 'subnets'
        else:
            modo = 'subnets'  # fallback

    criados = 0
    erros   = []

    if modo == 'subnets':
        criados, erros = _importar_subnets(c, reader, headers)
    else:
        criados, erros = _importar_ips(c, reader, headers)

    return JsonResponse({
        'ok': True, 'modo': modo,
        'criados': criados,
        'erros': erros[:20],
        'total_erros': len(erros),
    })


def _norm(row, *keys):
    """Lê o primeiro campo existente no row (case-insensitive)."""
    for k in keys:
        for rk, rv in row.items():
            if rk.lower().strip() == k.lower():
                return (rv or '').strip()
    return ''


def _importar_subnets(cliente, reader, headers):
    criados = 0
    erros   = []
    for i, row in enumerate(reader, 1):
        try:
            # Tenta diferentes nomes de coluna do phpIPAM
            subnet_str = _norm(row, 'subnet', 'network address', 'subnetaddress', 'prefix')
            mask_str   = _norm(row, 'mask', 'cidr', 'subnetmask')
            desc       = _norm(row, 'description', 'subnetdescription', 'name')
            local      = _norm(row, 'location', 'section', 'sectionname')
            vlan_num   = _norm(row, 'vlan', 'vlanid', 'vlan id', 'vlan number')
            gateway    = _norm(row, 'gateway', 'defaultgateway')

            if not subnet_str:
                continue

            # Montar CIDR
            if '/' in subnet_str:
                cidr = subnet_str
            elif mask_str:
                try:
                    net = ipaddress.ip_network(f'{subnet_str}/{mask_str}', strict=False)
                    cidr = str(net)
                except Exception:
                    cidr = subnet_str
            else:
                continue

            # Validar
            try:
                ipaddress.ip_network(cidr, strict=False)
            except Exception:
                erros.append(f'Linha {i}: CIDR inválido "{cidr}"')
                continue

            # Resolver VLAN
            vlan_obj = None
            if vlan_num:
                try:
                    vlan_num_int = int(vlan_num)
                    vlan_obj, _ = IPAMVlan.objects.get_or_create(
                        cliente=cliente, numero=vlan_num_int,
                        defaults={'nome': f'VLAN {vlan_num_int}'}
                    )
                except Exception:
                    pass

            # Criar sub-rede (evitar duplicata)
            obj, created = IPAMSubRede.objects.get_or_create(
                cliente=cliente, rede=cidr,
                defaults={
                    'gateway': gateway, 'descricao': desc,
                    'local': local, 'vlan': vlan_obj,
                }
            )
            if not created:
                # Atualizar campos vazios
                changed = False
                if not obj.descricao and desc:
                    obj.descricao = desc; changed = True
                if not obj.gateway and gateway:
                    obj.gateway = gateway; changed = True
                if not obj.vlan and vlan_obj:
                    obj.vlan = vlan_obj; changed = True
                if changed:
                    obj.save()
            else:
                criados += 1

        except Exception as e:
            erros.append(f'Linha {i}: {e}')

    return criados, erros


def _importar_ips(cliente, reader, headers):
    criados = 0
    erros   = []
    for i, row in enumerate(reader, 1):
        try:
            ip_str   = _norm(row, 'ip_addr', 'ipaddress', 'ip address', 'ip')
            hostname = _norm(row, 'hostname', 'dnsname', 'dns name', 'fqdn')
            desc     = _norm(row, 'description', 'note', 'notes')
            mac      = _norm(row, 'mac', 'macaddr', 'mac address', 'mac_addr')
            state    = _norm(row, 'state', 'status', 'ipstate')
            subnet_s = _norm(row, 'subnet', 'subnetid', 'subnet id', 'subnetcidr')

            if not ip_str:
                continue

            try:
                ipaddress.ip_address(ip_str)
            except Exception:
                erros.append(f'Linha {i}: IP inválido "{ip_str}"')
                continue

            # Mapear status phpIPAM → interno
            status_map = {'1': 'ativo', '2': 'reservado', '3': 'inativo',
                          'used': 'ativo', 'reserved': 'reservado',
                          'offline': 'inativo', 'dhcp': 'ativo'}
            status = status_map.get(state.lower(), 'ativo')

            # Tentar vincular sub-rede
            subrede_obj = None
            if subnet_s:
                subrede_obj = IPAMSubRede.objects.filter(
                    cliente=cliente, rede=subnet_s
                ).first()
            if not subrede_obj:
                # Encontrar sub-rede que contém este IP
                ip_obj = ipaddress.ip_address(ip_str)
                for s in IPAMSubRede.objects.filter(cliente=cliente):
                    try:
                        if ip_obj in ipaddress.ip_network(s.rede, strict=False):
                            subrede_obj = s
                            break
                    except Exception:
                        pass

            obj, created = IPAMEndereco.objects.get_or_create(
                cliente=cliente, ip=ip_str,
                defaults={
                    'hostname': hostname, 'descricao': desc,
                    'mac_address': mac, 'status': status,
                    'subrede': subrede_obj,
                }
            )
            if not created:
                changed = False
                if not obj.hostname and hostname:
                    obj.hostname = hostname; changed = True
                if not obj.mac_address and mac:
                    obj.mac_address = mac; changed = True
                if not obj.subrede and subrede_obj:
                    obj.subrede = subrede_obj; changed = True
                if changed:
                    obj.save()
            else:
                criados += 1

        except Exception as e:
            erros.append(f'Linha {i}: {e}')

    return criados, erros
