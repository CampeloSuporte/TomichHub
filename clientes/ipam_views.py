"""
ipam_views.py — CRUD e importação do IPAM nativo
"""
import csv
import io
import ipaddress
import json
import logging
import os
import re
import subprocess

import paramiko
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (
    Cliente, Acesso, ProxyServer,
    IPAMVlan, IPAMPrefixo, IPAMSubRede, IPAMEndereco, IPAMVpnDoc,
    IPAMScanResultado, IPAMAuditLog,
)
from .consumers import _proxy_pool
from .decorators import ferramenta_instancia_required
from usuario.perms import pode_acessar_cliente as _perms_pode_acessar_cliente

logger = logging.getLogger(__name__)

SCAN_MAX_HOSTS  = 1024
SCAN_TIMEOUT_SEC = 60


def _build_scan_cmd(hosts):
    """Ping em lote: dispara todos em paralelo (&) e espera (wait) — um único
    round-trip em vez de um exec/subprocess por IP, essencial pra varrer uma
    /24 sem levar minutos. `hosts` só contém strings vindas de ipaddress.ip_network,
    nunca input de usuário, então a interpolação direta no shell é segura."""
    alvo = ' '.join(hosts)
    return (
        f'for ip in {alvo}; do '
        f'( ping -c1 -W1 "$ip" >/dev/null 2>&1 && echo "$ip:1" || echo "$ip:0" ) & '
        f'done; wait'
    )


def _parse_scan_output(output):
    resultados = {}
    for linha in output.splitlines():
        linha = linha.strip()
        if ':' not in linha:
            continue
        ip, flag = linha.rsplit(':', 1)
        resultados[ip.strip()] = (flag.strip() == '1')
    return resultados


def _scan_subrede_hosts(subrede):
    """
    Ping em lote de todos os hosts de uma sub-rede, gravando o resultado em
    IPAMScanResultado (existe mesmo sem IPAMEndereco cadastrado — permite
    achar hosts respondendo mas não documentados). IP privado: via proxy SSH
    do pool (_proxy_pool, mesmo usado pelos terminais — já tem keepalive e
    health-check). IP público: ping local direto do servidor CRM.
    """
    net = ipaddress.ip_network(subrede.rede, strict=False)
    hosts = [str(h) for h in net.hosts()] if net.num_addresses > 2 else [str(net.network_address)]
    if not hosts:
        return {'online': 0, 'offline': 0, 'total': 0}
    if len(hosts) > SCAN_MAX_HOSTS:
        raise ValueError(
            f'Sub-rede grande demais para scan ({len(hosts)} hosts, limite {SCAN_MAX_HOSTS}). '
            f'Divida em blocos menores primeiro.'
        )

    cmd = _build_scan_cmd(hosts)
    is_private = ipaddress.ip_address(hosts[0]).is_private

    if is_private:
        proxy = ProxyServer.objects.filter(cliente=subrede.cliente, ativo=True).first()
        if not proxy:
            raise ValueError('Sub-rede privada sem proxy SSH ativo configurado para este cliente.')

        client = _proxy_pool.get(proxy)
        if client is None:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=proxy.host, port=int(proxy.porta),
                username=proxy.usuario, password=proxy.senha,
                timeout=10, look_for_keys=False, allow_agent=False,
                banner_timeout=10,
            )
            _proxy_pool.put(proxy, client)

        try:
            _stdin, stdout, _stderr = client.exec_command(cmd, timeout=SCAN_TIMEOUT_SEC)
            output = stdout.read().decode('utf-8', errors='ignore')
        except Exception:
            _proxy_pool.remove(proxy)
            raise
    else:
        proc = subprocess.run(['bash', '-c', cmd], capture_output=True, text=True,
                               timeout=SCAN_TIMEOUT_SEC)
        output = proc.stdout

    resultados = _parse_scan_output(output)
    online_count = 0
    for ip, online in resultados.items():
        IPAMScanResultado.objects.update_or_create(
            cliente=subrede.cliente, ip=ip, defaults={'online': online},
        )
        if online:
            online_count += 1

    subrede.ultimo_scan = timezone.now()
    subrede.save(update_fields=['ultimo_scan'])

    return {'online': online_count, 'offline': len(resultados) - online_count, 'total': len(resultados)}


def _cliente(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    if not _perms_pode_acessar_cliente(request.user, cliente):
        raise Http404('Cliente não encontrado ou sem permissão.')
    return cliente


def _checar_obj_cliente(request, obj):
    """Levanta Http404 se o usuário não pode acessar o cliente dono de `obj`
    (VLAN/Prefixo/SubRede/Endereço/VPN) — usado nos endpoints que resolvem o
    objeto direto pelo próprio id (vlan_id/prefixo_id/subrede_id/ip_id/vpn_id),
    sem passar por `_cliente(request, cliente_id)`."""
    if not _perms_pode_acessar_cliente(request.user, obj.cliente):
        raise Http404('Objeto não encontrado ou sem permissão.')


def _json(request):
    try:
        return json.loads(request.body)
    except Exception:
        return {}


def _computar_pai_id(alvo_net, candidatos, excluir_id=None):
    """
    Acha o prefixo mais específico, dentre `candidatos`, que contém `alvo_net`.
    candidatos: iterável de (id, cidr_str) do mesmo cliente.
    Pure-python (só usa ipaddress) — importável tanto daqui quanto da migration
    de backfill (0075) sem acoplar a migration ao estado atual dos models.
    """
    melhor_id, melhor_pl = None, -1
    for cid, cidr_str in candidatos:
        if excluir_id is not None and cid == excluir_id:
            continue
        try:
            net = ipaddress.ip_network(cidr_str, strict=False)
        except ValueError:
            continue
        if net.version != alvo_net.version or net.prefixlen >= alvo_net.prefixlen:
            continue
        if alvo_net.subnet_of(net) and net.prefixlen > melhor_pl:
            melhor_id, melhor_pl = cid, net.prefixlen
    return melhor_id


def _rede_contida_em(rede_str, alvo_net):
    """True se `rede_str` (CIDR de uma IPAMSubRede) está contida em `alvo_net`
    (inclui o caso de ser igual a `alvo_net`)."""
    try:
        net = ipaddress.ip_network(rede_str, strict=False)
    except ValueError:
        return False
    return net.version == alvo_net.version and net.subnet_of(alvo_net)


def _sync_prefixo_pool_cheia(prefixo_id):
    """
    Recalcula automaticamente o pool_cheia de um IPAMPrefixo tipo 'pool' a
    partir das sub-redes vinculadas a ele (FK IPAMSubRede.prefixo).
    Um prefixo pool fica "cheio" quando tem ao menos uma sub-rede vinculada
    e TODAS elas estão marcadas como pool_cheia. Sem sub-redes vinculadas,
    o flag não é mexido (fica sob controle manual do toggle na aba Prefixos).
    Chamado após criar/editar/excluir uma sub-rede ou alternar seu pool_cheia,
    pra que a badge "Pool Cheia" do prefixo pai fique sempre coerente.
    """
    if not prefixo_id:
        return
    try:
        pobj = IPAMPrefixo.objects.get(id=prefixo_id)
    except IPAMPrefixo.DoesNotExist:
        return
    if pobj.tipo != 'pool':
        return
    flags = list(IPAMSubRede.objects.filter(prefixo_id=prefixo_id).values_list('pool_cheia', flat=True))
    novo = bool(flags) and all(flags)
    if novo != pobj.pool_cheia:
        pobj.pool_cheia = novo
        pobj.save(update_fields=['pool_cheia'])


# ─────────────────────────────────────────────────────────────────────────────
# Log de auditoria — quem mudou o quê
# ─────────────────────────────────────────────────────────────────────────────

_LOG_CAMPOS = {
    'vlan':    ['numero', 'nome', 'descricao', 'status'],
    'prefixo': ['prefixo', 'tipo', 'status', 'descricao', 'local', 'pool_cheia'],
    'subrede': ['rede', 'gateway', 'descricao', 'local', 'status', 'pool_cheia', 'prefixo_id', 'vlan_id'],
    'ip':      ['ip', 'tipo', 'status', 'hostname', 'descricao', 'mac_address', 'subrede_id'],
    'vpn':     ['nome', 'tipo', 'endpoint_local', 'endpoint_remoto', 'rede_local', 'rede_remota',
                'as_local', 'as_remoto', 'descricao', 'status'],
}


def _ipam_snapshot(modelo, obj):
    """Dict raso dos campos relevantes do objeto — usado tanto pra capturar
    o estado 'antes' quanto pra montar o snapshot de created/deleted."""
    campos = _LOG_CAMPOS.get(modelo, [])
    return {c: getattr(obj, c, None) for c in campos}


def _ipam_log(request, cliente, modelo, obj, acao, antes=None):
    """
    Registra uma entrada de IPAMAuditLog. Em 'updated', só grava os campos
    que de fato mudaram (diff entre `antes`, capturado pelo caller ANTES de
    salvar, e o estado atual do objeto) — se nada mudou, não grava nada.
    Nunca deve derrubar a operação principal se falhar (log é best-effort).
    """
    try:
        if acao == 'updated' and antes is not None:
            depois = _ipam_snapshot(modelo, obj)
            mudancas = {
                campo: {'antes': valor_antigo, 'depois': depois.get(campo)}
                for campo, valor_antigo in antes.items()
                if valor_antigo != depois.get(campo)
            }
            if not mudancas:
                return
        else:
            mudancas = _ipam_snapshot(modelo, obj)

        IPAMAuditLog.objects.create(
            cliente=cliente, modelo=modelo, objeto_id=obj.id,
            objeto_repr=str(obj)[:255], acao=acao, mudancas=mudancas,
            usuario=request.user if request.user.is_authenticated else None,
        )
    except Exception as e:
        logger.warning(f'_ipam_log falhou ({modelo}/{acao} #{getattr(obj, "id", "?")}): {e}')


# ─────────────────────────────────────────────────────────────────────────────
# VLANs
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@ferramenta_instancia_required('ipam')
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
@ferramenta_instancia_required('ipam')
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
            antes = _ipam_snapshot('vlan', obj)
        else:
            obj = IPAMVlan(cliente=c)
            antes = None
        obj.numero    = numero
        obj.nome      = body.get('nome', '').strip() or f'VLAN {numero}'
        obj.descricao = body.get('descricao', '').strip()
        obj.status    = body.get('status', 'ativo')
        obj.save()
        _ipam_log(request, c, 'vlan', obj, 'updated' if vid else 'created', antes)
        return JsonResponse({'ok': True, 'id': obj.id})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
def ipam_vlan_deletar(request, vlan_id):
    obj = get_object_or_404(IPAMVlan, id=vlan_id)
    _checar_obj_cliente(request, obj)
    _ipam_log(request, obj.cliente, 'vlan', obj, 'deleted')
    obj.delete()
    return JsonResponse({'ok': True})


# ─────────────────────────────────────────────────────────────────────────────
# Prefixos
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@ferramenta_instancia_required('ipam')
def ipam_prefixos_listar(request, cliente_id):
    c  = _cliente(request, cliente_id)
    qs = IPAMPrefixo.objects.filter(cliente=c)
    by_id = {p.id: p for p in qs}

    def _nivel(p):
        n, cursor, visto = 0, p.pai_id, {p.id}
        while cursor and cursor in by_id and cursor not in visto:
            n += 1
            visto.add(cursor)
            cursor = by_id[cursor].pai_id
        return n

    data = []
    for p in by_id.values():
        sub_count = p.subredes.count()
        data.append({
            'id': p.id, 'prefixo': p.prefixo, 'tipo': p.tipo,
            'status': p.status, 'descricao': p.descricao,
            'local': p.local, 'subredes': sub_count, 'pool_cheia': p.pool_cheia,
            'pai_id': p.pai_id, 'nivel': _nivel(p),
        })
    data.sort(key=lambda x: (ipaddress.ip_network(x['prefixo'], strict=False).version,
                              ipaddress.ip_network(x['prefixo'], strict=False)))
    return JsonResponse({'ok': True, 'prefixos': data})


@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
def ipam_prefixo_salvar(request, cliente_id):
    c    = _cliente(request, cliente_id)
    body = _json(request)
    pid  = body.get('id')
    try:
        prefixo_str = body.get('prefixo', '').strip()
        alvo_net = ipaddress.ip_network(prefixo_str, strict=False)  # valida CIDR
        if pid:
            obj = get_object_or_404(IPAMPrefixo, id=pid, cliente=c)
            antes = _ipam_snapshot('prefixo', obj)
        else:
            obj = IPAMPrefixo(cliente=c)
            antes = None
        obj.prefixo   = prefixo_str
        obj.tipo      = body.get('tipo', 'rede')
        obj.status    = body.get('status', 'ativo')
        obj.descricao = body.get('descricao', '').strip()
        obj.local     = body.get('local', '').strip()

        candidatos = IPAMPrefixo.objects.filter(cliente=c).exclude(id=obj.id).values_list('id', 'prefixo')
        obj.pai_id = _computar_pai_id(alvo_net, candidatos)
        obj.save()

        # Prefixos que antes tinham outro pai podem agora ficar mais bem
        # posicionados sob o que acabou de ser salvo (ex: criar um /16 depois
        # de já existir um /24 dentro dele) — reancorar os filhos diretos.
        outros = IPAMPrefixo.objects.filter(cliente=c).exclude(id=obj.id).values_list('id', 'prefixo')
        for outro_id, outro_cidr in outros:
            try:
                outro_net = ipaddress.ip_network(outro_cidr, strict=False)
            except ValueError:
                continue
            if outro_net.version == alvo_net.version and outro_net.prefixlen > alvo_net.prefixlen \
                    and outro_net.subnet_of(alvo_net):
                novo_pai = _computar_pai_id(outro_net, IPAMPrefixo.objects.filter(cliente=c)
                                             .exclude(id=outro_id).values_list('id', 'prefixo'))
                IPAMPrefixo.objects.filter(id=outro_id).exclude(pai_id=novo_pai).update(pai_id=novo_pai)

        _ipam_log(request, c, 'prefixo', obj, 'updated' if pid else 'created', antes)
        return JsonResponse({'ok': True, 'id': obj.id})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
def ipam_prefixo_deletar(request, prefixo_id):
    obj = get_object_or_404(IPAMPrefixo, id=prefixo_id)
    _checar_obj_cliente(request, obj)
    _ipam_log(request, obj.cliente, 'prefixo', obj, 'deleted')
    obj.delete()
    return JsonResponse({'ok': True})


# Quantos registros uma divisão pode criar de uma vez. Existe por causa do
# IPv6: dividir um /32 em /48 são 65.536 INSERTs num clique — e em /64,
# 4 bilhões. Em IPv4 o pior caso realista (/16 em /30) são 16.384.
DIVIDIR_LIMITE = 4096


def _checar_limite_divisao(parent_net, target_pl, apenas_um):
    """Mensagem de erro se a divisão não for permitida/razoável, senão None."""
    # Mesmo teto do seletor de blocos: em IPv6 o automático para no /64.
    if parent_net.version == 6 and target_pl > V6_MASCARA_MINIMA:
        return (f'Em IPv6 a divisão automática vai até /{V6_MASCARA_MINIMA}. Pra p2p ou '
                f'loopback (/126, /127, /128), cadastre a sub-rede digitando o CIDR '
                f'em "Nova Sub-rede".')
    if apenas_um:
        return None
    n = 2 ** (target_pl - parent_net.prefixlen)
    if n > DIVIDIR_LIMITE:
        return (f'Dividir {parent_net} em /{target_pl} criaria {_num_curto(n)} sub-redes '
                f'(limite {_num_curto(DIVIDIR_LIMITE)}). Use o "+" pra criar só o bloco que '
                f'precisa, ou marque "criar apenas o primeiro bloco".')
    return None


@login_required
@require_http_methods(["POST"])
@ferramenta_instancia_required('ipam')
def ipam_prefixo_dividir(request, prefixo_id):
    """
    Divide um prefixo em N sub-redes iguais do tamanho prefixlen.
    Cria registros em IPAMSubRede para cada bloco.
    """
    pobj = get_object_or_404(IPAMPrefixo, id=prefixo_id)
    _checar_obj_cliente(request, pobj)
    try:
        body = _json(request)
        target_pl   = int(body.get('prefixlen', 0))
        descricao   = body.get('descricao', '').strip()
        apenas_um   = body.get('apenas_um', False)   # criar só o primeiro bloco

        prefixo_net = ipaddress.ip_network(pobj.prefixo, strict=False)
        max_pl      = 32 if prefixo_net.version == 4 else 128

        if target_pl <= prefixo_net.prefixlen or target_pl > max_pl:
            return JsonResponse({'ok': False, 'erro': f'prefixlen deve ser entre /{prefixo_net.prefixlen+1} e /{max_pl}'}, status=400)

        erro_qtd = _checar_limite_divisao(prefixo_net, target_pl, apenas_um)
        if erro_qtd:
            return JsonResponse({'ok': False, 'erro': erro_qtd}, status=400)

        subnets = [next(prefixo_net.subnets(new_prefix=target_pl))] if apenas_um \
                  else list(prefixo_net.subnets(new_prefix=target_pl))

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

        _sync_prefixo_pool_cheia(pobj.id)
        return JsonResponse({'ok': True, 'criados': len(criados), 'pulados': len(pulados), 'subredes': criados})
    except Exception as e:
        logger.error(f'ipam_prefixo_dividir: {e}')
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


SUBDIVISOES_LIMITE = 4096
# Teto de blocos somados quando se pede TODOS os tamanhos de uma vez
# (num /24 a divisão completa /25../32 dá 510 blocos — cabe folgado).
SUBDIVISOES_TODOS_LIMITE = 3000
# Acima disto a máscara vira AMOSTRA em vez de lista completa. Em IPv6 é o
# caso normal: um /32 tem 65.536 /48 e 4 bilhões de /64 — listar tudo é
# impossível, e listar "os primeiros N" esconderia as alocações reais, que
# num /32 costumam estar lá no fim (ex: 2804:57b0:e000::/40).
SUBDIVISOES_LISTA_CHEIA   = 512
SUBDIVISOES_AMOSTRA_CABECA = 32   # início do bloco, sempre listado
SUBDIVISOES_AMOSTRA_MAX    = 128  # teto de blocos por máscara na amostra
# Menor bloco que o IPAM quebra sozinho em IPv6. /64 é o tamanho de LAN: abaixo
# disso (p2p /126, loopback /128) o endereçamento é escolhido a dedo, então
# esses blocos entram digitando o CIDR na mão em "Nova Sub-rede", não por
# subdivisão automática — que geraria listas de 2^62 blocos sem serventia.
V6_MASCARA_MINIMA = 64


def _mascaras_candidatas(parent_net, limite_pl):
    """
    Máscaras que fazem sentido oferecer dentro de `parent_net`.

    IPv4 vai de /pai+1 até /pai+9. IPv6 anda de nibble em nibble e PARA no /64
    (ver `V6_MASCARA_MINIMA`) — ninguém subdivide um /32 em /33, e nada abaixo
    de /64 é escolhido por lista.
    """
    inicio = parent_net.prefixlen + 1
    if parent_net.version == 4:
        return [pl for pl in range(inicio, min(limite_pl, parent_net.prefixlen + 9) + 1)]

    teto = min(limite_pl, V6_MASCARA_MINIMA)
    return [pl for pl in range(inicio, teto + 1) if pl % 4 == 0]


def _alocadas_no_bloco(cliente, parent_net, excluir_pai=False):
    """
    Sub-redes já cadastradas do cliente que caem dentro de `parent_net`.

    `excluir_pai` existe porque a sub-rede pai *ela mesma* está cadastrada em
    IPAMSubRede: sem isso ela apareceria como ocupante de todos os blocos
    filhos, marcando o /24 inteiro como "parcial" contra si próprio.
    """
    alocadas = []
    for s in IPAMSubRede.objects.filter(cliente=cliente):
        try:
            snet = ipaddress.ip_network(s.rede, strict=False)
        except ValueError:
            continue
        if snet.version != parent_net.version or not snet.subnet_of(parent_net):
            continue
        if excluir_pai and snet == parent_net:
            continue
        alocadas.append((snet, s))
    return alocadas


def _indices_amostra(parent_net, target_pl, alocadas, total):
    """
    Índices dos blocos a listar quando a máscara gera blocos demais.

    Pega o começo do bloco (onde as alocações normalmente começam) MAIS o
    índice de cada sub-rede já cadastrada e o vizinho seguinte dela — que é
    justamente o próximo bloco livre a usar. Sem isso, num /32 IPv6 a lista
    mostraria só `2804:57b0:0::/40 …` e esconderia os /40 reais em `e000::`.
    """
    bits   = 128 if parent_net.version == 6 else 32
    desloc = bits - target_pl
    base   = int(parent_net.network_address)

    def _idx(snet):
        i = (int(snet.network_address) - base) >> desloc
        return i if 0 <= i < total else None

    # Ordem de prioridade dentro do orçamento: alocação exata nesta máscara,
    # depois o vizinho dela (o próximo bloco livre a usar), depois alocações
    # MAIS específicas (que deixam o bloco "parcial"), e só então o começo.
    #
    # Alocações mais AMPLAS que a máscara não viram âncora de propósito: se um
    # /36 está alocado e estou listando /64, ancorar nele só produziria fileiras
    # de "parcial" espalhadas, cada uma com um "⋯ 224 omitidos" no meio. A
    # região inteira já está tomada — o que interessa ali é o começo.
    exatas      = [s for s, _ in alocadas if s.prefixlen == target_pl]
    especificas = [s for s, _ in alocadas if s.prefixlen > target_pl]
    idx = set()

    def _juntar(valores):
        for v in valores:
            if len(idx) >= SUBDIVISOES_AMOSTRA_MAX:
                return
            if v is not None:
                idx.add(v)

    _juntar(_idx(s) for s in exatas)
    _juntar((_idx(s) or 0) + 1 for s in exatas if (_idx(s) or 0) + 1 < total)
    _juntar(_idx(s) for s in especificas)
    _juntar(range(min(SUBDIVISOES_AMOSTRA_CABECA, total)))
    return sorted(idx)[:SUBDIVISOES_AMOSTRA_MAX]


def _marcar_blocos(parent_net, target_pl, alocadas, indices=None):
    """
    Divide parent_net em /target_pl e marca cada bloco: livre / em uso / cheia / parcial.

    Com `indices`, monta só os blocos daquelas posições (aritmética direta, sem
    percorrer o gerador — um /64 dentro de um /32 são 4 bilhões de iterações) e
    marca `salto` no primeiro bloco depois de cada intervalo pulado, pra UI
    poder dizer que ali no meio ficou coisa de fora.
    """
    if indices is None:
        fonte = parent_net.subnets(new_prefix=target_pl)
        saltos = {}
    else:
        bits   = 128 if parent_net.version == 6 else 32
        desloc = bits - target_pl
        base   = int(parent_net.network_address)
        fonte  = (ipaddress.ip_network((base + (i << desloc), target_pl)) for i in indices)
        saltos = {pos: indices[pos] - indices[pos - 1] - 1
                  for pos in range(1, len(indices)) if indices[pos] - indices[pos - 1] > 1}

    blocos = []
    for subnet in fonte:
        exato = next((s for snet, s in alocadas if snet == subnet), None)
        ocupante_parcial = None if exato else next(
            (s for snet, s in alocadas if snet.subnet_of(subnet) or subnet.subnet_of(snet)), None)

        if exato:
            status = 'cheia' if exato.pool_cheia else 'em_uso'
        elif ocupante_parcial:
            status = 'parcial'
        else:
            status = 'livre'

        bloco = {
            'rede':        str(subnet),
            'status':      status,
            'subrede_id':  exato.id if exato else (ocupante_parcial.id if ocupante_parcial else None),
            'descricao':   exato.descricao if exato else (ocupante_parcial.descricao if ocupante_parcial else ''),
            'ocupado_por': ocupante_parcial.rede if ocupante_parcial else None,
        }
        pulados = saltos.get(len(blocos))
        if pulados:
            bloco['salto'] = pulados
        blocos.append(bloco)
    return blocos


def _subdivisoes_payload(cliente, parent_net, prefixlen_raw,
                         excluir_pai=False, limite_pl=None):
    """
    Monta os blocos possíveis dentro de um bloco pai, marcando cada um como
    livre / em uso / cheio / parcial. Compartilhado pelas duas entradas —
    prefixo (container) e sub-rede (ver `_alocadas_no_bloco`).

    `prefixlen_raw`:
      - um prefixlen (ex: `26`) → devolve a chave `blocos` só daquele tamanho;
      - `'todos'` (ou vazio)    → devolve `grupos`, com a divisão COMPLETA:
        num /24 são os 2 /25, os 4 /26, … até os 256 /32, tudo de uma vez, pra
        escolher o bloco direto sem ter que trocar de máscara antes.

    Em IPv6 "completa" é impossível (um /32 tem 4 bilhões de /64), então cada
    máscara acima de `SUBDIVISOES_LISTA_CHEIA` vira amostra — ver
    `_indices_amostra`, que garante as alocações existentes na lista.

    Retorna `(payload, None)` em caso de sucesso, `(None, erro)` se inválido.
    """
    v6        = parent_net.version == 6
    max_pl    = 128 if v6 else 32
    limite_pl = max_pl if limite_pl is None else min(limite_pl, max_pl)
    if v6:
        limite_pl = min(limite_pl, V6_MASCARA_MINIMA)
    opcoes = _mascaras_candidatas(parent_net, limite_pl)
    if not opcoes:
        if v6:
            return None, (f'Em IPv6 a subdivisão automática vai até /{V6_MASCARA_MINIMA} — '
                          f'/{parent_net.prefixlen} já é igual ou menor que isso. Blocos menores '
                          f'(p2p, loopback) entram digitando o CIDR em "Nova Sub-rede".')
        return None, f'/{parent_net.prefixlen} não tem como ser subdividido (limite /{limite_pl})'

    todos = str(prefixlen_raw or 'todos').lower() == 'todos'
    if not todos:
        try:
            target_pl = int(prefixlen_raw)
        except (TypeError, ValueError):
            return None, 'prefixlen inválido'
        if target_pl <= parent_net.prefixlen or target_pl > limite_pl:
            return None, f'prefixlen deve ser entre /{parent_net.prefixlen+1} e /{limite_pl}'

    alocadas = _alocadas_no_bloco(cliente, parent_net, excluir_pai)

    def _grupo(pl, teto_lista):
        """Monta o grupo de uma máscara, em lista cheia ou em amostra."""
        n = 2 ** (pl - parent_net.prefixlen)
        if n <= teto_lista:
            blocos = _marcar_blocos(parent_net, pl, alocadas)
        else:
            blocos = _marcar_blocos(parent_net, pl, alocadas,
                                    indices=_indices_amostra(parent_net, pl, alocadas, n))
        return {
            'prefixlen':   pl,
            'label':       _prefixlen_label(pl, parent_net.version),
            'total':       n if n <= SUBDIVISOES_LIMITE else None,   # JSON não gosta de 2^96
            'total_label': _num_curto(n),
            'mostrando':   len(blocos),
            'amostra':     len(blocos) < n,
            'blocos':      blocos,
        }

    base = {
        'ok':            True,
        'prefixo':       str(parent_net),
        'prefixlen_pai': parent_net.prefixlen,
        'versao':        parent_net.version,
        'opcoes':        [{'prefixlen': pl, 'label': _prefixlen_label(pl, parent_net.version),
                           'total_label': _num_curto(2 ** (pl - parent_net.prefixlen))}
                          for pl in opcoes],
    }

    if not todos:
        # Máscara pedida na mão: lista cheia até o limite duro, senão amostra
        # maior que a do modo "todos" (o usuário está focado nela).
        g = _grupo(target_pl, SUBDIVISOES_LIMITE)
        base.update({'prefixlen': target_pl, 'total': g['total'],
                     'total_label': g['total_label'], 'mostrando': g['mostrando'],
                     'amostra': g['amostra'], 'blocos': g['blocos']})
        return base, None

    grupos, somados = [], 0
    for pl in opcoes:
        if somados >= SUBDIVISOES_TODOS_LIMITE:
            break
        g = _grupo(pl, SUBDIVISOES_LISTA_CHEIA)
        grupos.append(g)
        somados += g['mostrando']

    if not grupos:
        return None, (f'/{parent_net.prefixlen} é grande demais pra listar a divisão '
                      f'completa — escolha um tamanho específico.')

    base.update({'prefixlen': 'todos', 'total': somados, 'grupos': grupos,
                 'truncado': len(grupos) < len(opcoes)})
    return base, None


@login_required
@ferramenta_instancia_required('ipam')
def ipam_prefixo_subdivisoes(request, prefixo_id):
    """
    Lista TODOS os blocos possíveis de um determinado prefixlen dentro do
    prefixo (ex: um /24 dividido em todos os /28 possíveis), marcando cada
    bloco como livre, em uso, cheio ou parcialmente ocupado — pra o usuário
    escolher visualmente qual bloco específico usar (estilo "split network"
    do phpIPAM), em vez de só dividir tudo de uma vez.
    """
    pobj = get_object_or_404(IPAMPrefixo, id=prefixo_id)
    _checar_obj_cliente(request, pobj)
    try:
        prefixo_net = ipaddress.ip_network(pobj.prefixo, strict=False)
    except ValueError as e:
        return JsonResponse({'ok': False, 'erro': f'Prefixo inválido: {e}'}, status=400)

    payload, erro = _subdivisoes_payload(pobj.cliente, prefixo_net,
                                         request.GET.get('prefixlen'))
    if erro:
        return JsonResponse({'ok': False, 'erro': erro}, status=400)
    payload['prefixo_id'] = pobj.id
    return JsonResponse(payload)


@login_required
@ferramenta_instancia_required('ipam')
def ipam_subrede_subdivisoes(request, subrede_id):
    """
    Mesma ideia de `ipam_prefixo_subdivisoes`, mas usando uma sub-rede já
    cadastrada como bloco pai: pega um /24 e lista todos os /25../32 possíveis
    dentro dele, pra criar uma sub-rede filha escolhendo o bloco na mão
    (botão "+" da linha da sub-rede).

    O teto vai até /32 (e /128 em IPv6): na teoria isso é host e viraria
    IPAMEndereco, mas o cadastro deste CRM usa host como sub-rede em massa
    (1.170 /32 e 22 /128 — loopbacks de equipamento), então cortar em /31
    impediria justamente o caso mais comum.
    """
    sobj = get_object_or_404(IPAMSubRede, id=subrede_id)
    _checar_obj_cliente(request, sobj)
    try:
        parent_net = ipaddress.ip_network(sobj.rede, strict=False)
    except ValueError as e:
        return JsonResponse({'ok': False, 'erro': f'Sub-rede inválida: {e}'}, status=400)

    payload, erro = _subdivisoes_payload(sobj.cliente, parent_net,
                                         request.GET.get('prefixlen'),
                                         excluir_pai=True)
    if erro:
        return JsonResponse({'ok': False, 'erro': erro}, status=400)
    # O modal de criação precisa do prefixo dono pra já vir pré-selecionado
    payload['prefixo_id'] = sobj.prefixo_id
    return JsonResponse(payload)


DISPONIVEIS_PAGINA     = 240    # blocos por página quando o tamanho é escolhido
DISPONIVEIS_GAPS_MAX   = 500    # tetos de "buracos" livres inteiros devolvidos
DISPONIVEIS_BUSCA_SCAN = 20000  # blocos varridos no máximo quando há busca


def _mascaras_disponiveis(parent_net):
    """
    Máscaras oferecidas no seletor de blocos livres.

    Diferente de `_mascaras_candidatas` (subdivisão, que para em pai+9), aqui
    vai até o fim da família: dentro de um /16 o operador precisa poder pedir
    um /30 de p2p ou um /32 de loopback sem digitar o CIDR na mão. A versão
    antiga só oferecia pai+1..pai+6, então num /16 a lista ia de /17 a /22 e
    um /24 simplesmente não existia como opção.
    """
    inicio = parent_net.prefixlen + 1
    if parent_net.version == 4:
        return list(range(inicio, 33))
    # IPv6 anda de nibble em nibble até /64; abaixo disso só as máscaras que
    # aparecem de verdade no cadastro (p2p /126-/127 e loopback /128).
    pls = {pl for pl in range(inicio, V6_MASCARA_MINIMA + 1) if pl % 4 == 0}
    pls |= {pl for pl in (112, 126, 127, 128) if pl >= inicio}
    return sorted(pls)


def _num_blocos(n):
    """
    Contagem de blocos livres: número cheio até o bilhão, potência de 2 acima.

    `_num_curto` corta em 65.536, o que em IPv4 é cedo demais — "2^20" no lugar
    de "1.048.576 blocos /30" não ajuda ninguém a decidir.
    """
    if n <= 10 ** 9:
        return f'{n:,}'.replace(',', '.')
    bits = n.bit_length() - 1
    return ('' if n == 1 << bits else '~') + f'2^{bits}'


def _livres_contagem(gaps, pl):
    """Quantos blocos /pl cabem no espaço livre — soma exata, sem enumerar."""
    return sum(1 << (pl - g.prefixlen) for g in gaps if g.prefixlen <= pl)


def _livres_pagina(gaps, pl, version, offset=0, limite=DISPONIVEIS_PAGINA):
    """
    Uma fatia da lista de blocos /pl livres, sem materializar o resto.

    O salto até o `offset` é aritmético (base + i * passo), então pedir a
    página 40 de um /16 dividido em /30 custa o mesmo que pedir a primeira —
    era enumerar tudo de uma vez que travava o menu.
    """
    passo    = 1 << ((128 if version == 6 else 32) - pl)
    blocos   = []
    restante = max(0, offset)

    for gap in gaps:
        if len(blocos) >= limite:
            break
        if gap.prefixlen > pl:
            continue
        n = 1 << (pl - gap.prefixlen)
        if restante >= n:
            restante -= n
            continue
        base = int(gap.network_address)
        i, restante = restante, 0
        while i < n and len(blocos) < limite:
            blocos.append(f'{ipaddress.ip_address(base + i * passo)}/{pl}')
            i += 1
    return blocos


def _livres_busca(gaps, pl, version, termo, limite=DISPONIVEIS_PAGINA):
    """
    Blocos /pl livres cujo CIDR contém `termo`.

    Aqui não dá pra pular por aritmética (o filtro é textual), então a varredura
    tem teto: devolve `(blocos, truncou)` e a UI avisa quando parou no meio em
    vez de fingir que aquilo é a lista inteira.
    """
    passo    = 1 << ((128 if version == 6 else 32) - pl)
    termo    = termo.strip().lower()
    achados  = []
    varridos = 0

    for gap in gaps:
        if gap.prefixlen > pl:
            continue
        base = int(gap.network_address)
        for i in range(1 << (pl - gap.prefixlen)):
            if len(achados) >= limite:
                return achados, False
            if varridos >= DISPONIVEIS_BUSCA_SCAN:
                return achados, True
            varridos += 1
            cidr = f'{ipaddress.ip_address(base + i * passo)}/{pl}'
            if termo in cidr:
                achados.append(cidr)
    return achados, False


def _ocupadas_no_prefixo(pobj, prefixo_net):
    """
    Tudo que já consome espaço dentro do prefixo: as sub-redes cadastradas e
    também os prefixos FILHOS.

    Os filhos entravam de fora antes, e um /24 registrado como prefixo dentro
    de um /16 aparecia como "bloco livre" do /16 — livre ele não estava.
    """
    version  = prefixo_net.version
    ocupadas = []

    def _add(cidr):
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            return
        if net.version == version and net.subnet_of(prefixo_net):
            ocupadas.append(net)

    for s in IPAMSubRede.objects.filter(cliente=pobj.cliente).only('rede'):
        _add(s.rede)
    for p in IPAMPrefixo.objects.filter(cliente=pobj.cliente).exclude(id=pobj.id).only('prefixo'):
        try:
            net = ipaddress.ip_network(p.prefixo, strict=False)
        except ValueError:
            continue
        # `subnet_of` inclui igualdade: um prefixo duplicado com o mesmo CIDR
        # zeraria o espaço livre do pai, então só entra quem é mais específico.
        if net.version == version and net != prefixo_net and net.subnet_of(prefixo_net):
            ocupadas.append(net)
    return ocupadas


@login_required
@ferramenta_instancia_required('ipam')
def ipam_prefixo_disponiveis(request, prefixo_id):
    """
    Seletor de blocos LIVRES dentro de um prefixo — o que o botão "Livres" do
    modal de sub-rede abre. Estilo phpIPAM "Add subnet": em vez de calcular o
    CIDR na mão, o operador escolhe da lista.

    Dois modos, controlados pelo parâmetro `prefixlen`:

      - sem `prefixlen`: devolve os *gaps* (os maiores blocos livres alinhados,
        já é a resposta exata e curta) e a contagem de blocos livres por
        máscara, pra UI montar os chips de tamanho;
      - com `prefixlen`: devolve uma PÁGINA (`offset` + `limite`) dos blocos
        daquela máscara. Um /16 tem 16.384 /30 — mandar tudo de uma vez era o
        que deixava o menu inutilizável.

    `q` filtra por pedaço do CIDR, com varredura limitada (ver `_livres_busca`).
    """
    pobj = get_object_or_404(IPAMPrefixo, id=prefixo_id)
    _checar_obj_cliente(request, pobj)
    try:
        prefixo_net = ipaddress.ip_network(pobj.prefixo, strict=False)
    except ValueError as e:
        return JsonResponse({'ok': False, 'erro': f'Prefixo inválido: {e}'}, status=400)

    version   = prefixo_net.version
    gaps      = _gaps_livres(prefixo_net, _ocupadas_no_prefixo(pobj, prefixo_net))
    mascaras  = _mascaras_disponiveis(prefixo_net)
    livre_end = sum(g.num_addresses for g in gaps)

    resp = {
        'ok':            True,
        'prefixo':       str(prefixo_net),
        'versao':        version,
        'prefixlen_pai': prefixo_net.prefixlen,
        'livre_total':   _num_blocos(livre_end),
        'gaps': [{'rede': str(g), 'label': _prefixlen_label(g.prefixlen, version)}
                 for g in gaps[:DISPONIVEIS_GAPS_MAX]],
        'gaps_total': len(gaps),
        'tamanhos': [],
    }
    for pl in mascaras:
        n = _livres_contagem(gaps, pl)
        if n:
            resp['tamanhos'].append({'prefixlen': pl, 'total_label': _num_blocos(n),
                                     'label': _prefixlen_label(pl, version)})

    bruto = (request.GET.get('prefixlen') or '').strip()
    if not bruto:
        return JsonResponse(resp)

    try:
        pl = int(bruto)
    except ValueError:
        return JsonResponse({'ok': False, 'erro': 'prefixlen inválido'}, status=400)
    if pl not in mascaras:
        return JsonResponse({'ok': False,
                             'erro': f'/{pl} não é um tamanho válido dentro de {prefixo_net}'},
                            status=400)

    try:
        offset = max(0, int(request.GET.get('offset') or 0))
    except ValueError:
        offset = 0

    total = _livres_contagem(gaps, pl)
    busca = (request.GET.get('q') or '').strip()
    if busca:
        blocos, truncou = _livres_busca(gaps, pl, version, busca)
        resp.update({'blocos': blocos, 'busca': busca, 'busca_truncada': truncou,
                     'offset': 0, 'tem_mais': False})
    else:
        blocos = _livres_pagina(gaps, pl, version, offset)
        resp.update({'blocos': blocos, 'offset': offset,
                     'tem_mais': offset + len(blocos) < total})

    # `total` cru só vai quando cabe em número JS exato (IPv6 estoura fácil);
    # quem precisa mostrar usa sempre `total_label`.
    resp.update({'prefixlen': pl, 'total_label': _num_blocos(total),
                 'total': total if total <= 2 ** 53 else None})
    return JsonResponse(resp)


@login_required
@require_http_methods(["POST"])
@ferramenta_instancia_required('ipam')
def ipam_subrede_dividir(request, subrede_id):
    """Divide uma sub-rede em N sub-redes menores (herda prefixo pai)."""
    sobj = get_object_or_404(IPAMSubRede, id=subrede_id)
    _checar_obj_cliente(request, sobj)
    try:
        body      = _json(request)
        target_pl = int(body.get('prefixlen', 0))
        descricao = body.get('descricao', '').strip()
        apenas_um = body.get('apenas_um', False)

        parent_net = ipaddress.ip_network(sobj.rede, strict=False)
        max_pl     = 32 if parent_net.version == 4 else 128

        if target_pl <= parent_net.prefixlen or target_pl > max_pl:
            return JsonResponse({'ok': False, 'erro': f'prefixlen deve ser maior que /{parent_net.prefixlen}'}, status=400)

        erro_qtd = _checar_limite_divisao(parent_net, target_pl, apenas_um)
        if erro_qtd:
            return JsonResponse({'ok': False, 'erro': erro_qtd}, status=400)

        subnets = [next(parent_net.subnets(new_prefix=target_pl))] if apenas_um \
                  else list(parent_net.subnets(new_prefix=target_pl))

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

        _sync_prefixo_pool_cheia(sobj.prefixo_id)
        return JsonResponse({'ok': True, 'criados': len(criados), 'pulados': len(pulados), 'subredes': criados})
    except Exception as e:
        logger.error(f'ipam_subrede_dividir: {e}')
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
@ferramenta_instancia_required('ipam')
def ipam_prefixo_marcar_em_uso(request, prefixo_id):
    """Cria uma sub-rede única que cobre todo o prefixo (100% em uso)."""
    pobj = get_object_or_404(IPAMPrefixo, id=prefixo_id)
    _checar_obj_cliente(request, pobj)
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
        _sync_prefixo_pool_cheia(pobj.id)
        return JsonResponse({'ok': True, 'subrede_id': sr.id})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
def ipam_prefixo_pool_cheia(request, prefixo_id):
    """Alterna o flag pool_cheia do prefixo."""
    pobj = get_object_or_404(IPAMPrefixo, id=prefixo_id)
    _checar_obj_cliente(request, pobj)
    pobj.pool_cheia = not pobj.pool_cheia
    pobj.save(update_fields=['pool_cheia'])
    return JsonResponse({'ok': True, 'pool_cheia': pobj.pool_cheia})


@login_required
@ferramenta_instancia_required('ipam')
def ipam_prefixo_breakdown(request, prefixo_id):
    """
    Retorna o breakdown de um prefixo:
    - sub-redes já alocadas dentro dele
    - espaço livre (gaps) como CIDRs
    - sugestões de tamanhos para o espaço livre
    """
    pobj = get_object_or_404(IPAMPrefixo, id=prefixo_id)
    _checar_obj_cliente(request, pobj)
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
                    'pool_cheia': s.pool_cheia,
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


def _gaps_livres(prefixo_net, alocadas_nets):
    """
    Encontra os gaps (espaço não alocado) dentro de prefixo_net, como uma
    lista de ip_network já otimizada (maiores blocos possíveis alinhados).
    Usado tanto por _calcular_livres (breakdown) quanto por
    ipam_prefixo_disponiveis (lista de blocos livres agrupados por tamanho).
    """
    p_start = int(prefixo_net.network_address)
    p_end   = int(prefixo_net.broadcast_address)

    try:
        collapsed = list(ipaddress.collapse_addresses(alocadas_nets))
    except Exception:
        collapsed = sorted(alocadas_nets, key=lambda n: n.network_address)

    gaps = []
    cursor = p_start

    for net in sorted(collapsed, key=lambda n: int(n.network_address)):
        net_start = int(net.network_address)
        net_end   = int(net.broadcast_address)

        if net_start > cursor:
            gap_start = ipaddress.ip_address(cursor)
            gap_end   = ipaddress.ip_address(net_start - 1)
            try:
                gaps.extend(ipaddress.summarize_address_range(gap_start, gap_end))
            except Exception:
                pass

        cursor = max(cursor, net_end + 1)

    if cursor <= p_end:
        gap_start = ipaddress.ip_address(cursor)
        gap_end   = ipaddress.ip_address(p_end)
        try:
            gaps.extend(ipaddress.summarize_address_range(gap_start, gap_end))
        except Exception:
            pass

    return gaps


def _calcular_livres(prefixo_net, alocadas_nets, version):
    """
    Encontra os gaps (espaço não alocado) dentro de prefixo_net.
    Retorna lista de dicts com rede CIDR, tamanho e sugestões de subdivisão.
    """
    return [_livre_dict(g) for g in _gaps_livres(prefixo_net, alocadas_nets)]


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


def _num_curto(n):
    """2^64 em vez de 18.446.744.073.709.551.616 — número gigante não informa nada."""
    return f'{n:,}'.replace(',', '.') if n <= 65536 else f'2^{n.bit_length() - 1}'


def _prefixlen_label(prefixlen, version=4):
    max_prefix = 32 if version == 4 else 128
    n = 2 ** (max_prefix - prefixlen)
    if n == 1:
        return f'/{prefixlen} (1 endereço)'
    if version == 4:
        if prefixlen <= 16:
            return f'/{prefixlen} ({n:,} IPs)'
        # /31 é ponto-a-ponto (RFC 3021): os 2 endereços são usáveis, então
        # "0 hosts" da conta clássica (n-2) mentiria.
        if prefixlen >= 31:
            return f'/{prefixlen} ({n} endereços)'
        return f'/{prefixlen} ({n - 2} hosts)'
    return f'/{prefixlen} ({_num_curto(n)} endereços)'


# ─────────────────────────────────────────────────────────────────────────────
# Sub-redes
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@ferramenta_instancia_required('ipam')
def ipam_subredes_listar(request, cliente_id):
    c       = _cliente(request, cliente_id)
    filtro  = request.GET.get('prefixo_id')
    qs      = IPAMSubRede.objects.filter(cliente=c).select_related('prefixo', 'vlan')
    if filtro:
        # Filtra por CONTAINMENT real do CIDR, não pelo FK prefixo_id exato.
        # A maioria das sub-redes do sistema nunca teve prefixo_id preenchido
        # (import CSV não seta, análise de backup só vincula ao container /24
        # mais específico) — filtrar só por FK igual escondia a imensa maioria
        # das redes que visualmente pertencem à faixa selecionada.
        pobj = IPAMPrefixo.objects.filter(id=filtro, cliente=c).first()
        alvo_net = None
        if pobj:
            try:
                alvo_net = ipaddress.ip_network(pobj.prefixo, strict=False)
            except ValueError:
                alvo_net = None
        if alvo_net is not None:
            # values_list em vez de qs.only(): só() nos mesmos campos do
            # select_related já aplicado em `qs` acima quebra com FieldError
            # ("cannot be both deferred and traversed using select_related").
            candidatos = IPAMSubRede.objects.filter(cliente=c).values_list('id', 'rede')
            ids = [cid for cid, rede in candidatos if _rede_contida_em(rede, alvo_net)]
            qs = qs.filter(id__in=ids)
        else:
            qs = qs.filter(prefixo_id=filtro)

    data = []
    for s in qs:
        total = s.total_hosts()
        used  = s.usados()
        pct   = round(used / total * 100, 1) if total else 0
        # Hostnames distintos dos IPs nesta sub-rede (apenas os preenchidos)
        hostnames = list(
            s.ips.exclude(hostname='')
             .values_list('hostname', flat=True)
             .distinct()[:5]
        )
        data.append({
            'id': s.id, 'rede': s.rede, 'gateway': s.gateway,
            'descricao': s.descricao, 'local': s.local, 'status': s.status,
            'prefixo_id': s.prefixo_id,
            'prefixo':    s.prefixo.prefixo if s.prefixo else '',
            'vlan_id':    s.vlan_id,
            'vlan':       str(s.vlan) if s.vlan else '',
            'total_hosts': total, 'usados': used, 'utilizacao_pct': pct,
            'pool_cheia': s.pool_cheia,
            'hostnames':  hostnames,
        })
    data.sort(key=lambda x: (ipaddress.ip_network(x['rede'], strict=False).version,
                              ipaddress.ip_network(x['rede'], strict=False)))
    return JsonResponse({'ok': True, 'subredes': data})


@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
def ipam_subrede_salvar(request, cliente_id):
    c    = _cliente(request, cliente_id)
    body = _json(request)
    sid  = body.get('id')
    try:
        rede_str = body.get('rede', '').strip()
        ipaddress.ip_network(rede_str, strict=False)
        if sid:
            obj = get_object_or_404(IPAMSubRede, id=sid, cliente=c)
            antes = _ipam_snapshot('subrede', obj)
        else:
            obj = IPAMSubRede(cliente=c)
            antes = None
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
        # prefixo anterior (se estava editando) — precisa ressincronizar também
        # caso a sub-rede tenha sido desvinculada/movida para outro prefixo
        prefixo_id_antes = antes.get('prefixo_id') if antes else None
        obj.save()
        _ipam_log(request, c, 'subrede', obj, 'updated' if sid else 'created', antes)
        _sync_prefixo_pool_cheia(obj.prefixo_id)
        if prefixo_id_antes and prefixo_id_antes != obj.prefixo_id:
            _sync_prefixo_pool_cheia(prefixo_id_antes)
        return JsonResponse({'ok': True, 'id': obj.id})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
def ipam_subrede_deletar(request, subrede_id):
    obj = get_object_or_404(IPAMSubRede, id=subrede_id)
    _checar_obj_cliente(request, obj)
    prefixo_id = obj.prefixo_id
    _ipam_log(request, obj.cliente, 'subrede', obj, 'deleted')
    obj.delete()
    _sync_prefixo_pool_cheia(prefixo_id)
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
def ipam_subrede_pool_cheia(request, subrede_id):
    """Alterna o flag pool_cheia da sub-rede e sincroniza o prefixo pai."""
    obj = get_object_or_404(IPAMSubRede, id=subrede_id)
    _checar_obj_cliente(request, obj)
    obj.pool_cheia = not obj.pool_cheia
    obj.save(update_fields=['pool_cheia'])
    _sync_prefixo_pool_cheia(obj.prefixo_id)
    return JsonResponse({'ok': True, 'pool_cheia': obj.pool_cheia})


@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
def ipam_subrede_scan(request, subrede_id):
    """Dispara um scan (ping em lote) imediato da sub-rede."""
    s = get_object_or_404(IPAMSubRede, id=subrede_id)
    _checar_obj_cliente(request, s)
    try:
        resultado = _scan_subrede_hosts(s)
        return JsonResponse({'ok': True, **resultado})
    except Exception as e:
        logger.error(f'ipam_subrede_scan: {e}')
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
def ipam_subrede_scan_toggle(request, subrede_id):
    """Liga/desliga o scan automático periódico (Celery) desta sub-rede."""
    obj = get_object_or_404(IPAMSubRede, id=subrede_id)
    _checar_obj_cliente(request, obj)
    obj.scan_automatico = not obj.scan_automatico
    obj.save(update_fields=['scan_automatico'])
    return JsonResponse({'ok': True, 'scan_automatico': obj.scan_automatico})


@login_required
@ferramenta_instancia_required('ipam')
def ipam_subrede_ips(request, subrede_id):
    """Lista IPs de uma sub-rede específica."""
    s  = get_object_or_404(IPAMSubRede, id=subrede_id)
    _checar_obj_cliente(request, s)
    qs = IPAMEndereco.objects.filter(subrede=s).select_related('acesso')
    data = [_ip_dict(e) for e in qs]
    return JsonResponse({'ok': True, 'ips': data, 'subrede': s.rede})


# Grade visual = uma célula por endereço. O teto é o /24 (256 endereços) e o
# equivalente IPv6, /120 — acima disso vira uma malha de milhares de
# quadradinhos que não se lê. A UI já esconde o botão nesse caso
# (`tetoGrade` em _renderSrLeafRow); aqui é a mesma regra do lado do servidor.
GRADE_MAX_ENDERECOS = 256


@login_required
@ferramenta_instancia_required('ipam')
def ipam_subrede_grade(request, subrede_id):
    """
    Grade completa dos endereços de uma sub-rede (estilo phpIPAM): gera todo
    o range CIDR e cruza com IPAMEndereco (documentado) + IPAMScanResultado
    (online/offline do último scan) — mostra inclusive hosts que respondem
    ping mas nunca foram cadastrados (achado de descoberta).
    """
    s = get_object_or_404(IPAMSubRede, id=subrede_id)
    _checar_obj_cliente(request, s)
    try:
        net = ipaddress.ip_network(s.rede, strict=False)
    except ValueError as e:
        return JsonResponse({'ok': False, 'erro': f'Rede inválida: {e}'}, status=400)

    if net.num_addresses > GRADE_MAX_ENDERECOS:
        return JsonResponse({
            'ok': False,
            'erro': (f'Grade visual vai até /24 ({GRADE_MAX_ENDERECOS} endereços); '
                     f'{s.rede} tem {net.num_addresses:,}'.replace(',', '.')
                     + '. Abra a grade num bloco menor.'),
        }, status=400)

    enderecos = {e.ip: e for e in IPAMEndereco.objects.filter(subrede=s).select_related('acesso')}
    scans     = {r.ip: r for r in IPAMScanResultado.objects.filter(cliente=s.cliente)}
    gateway_ip = (s.gateway or '').split('/')[0].strip()
    tem_rede_broadcast = (net.version == 4 and net.num_addresses > 2)

    grade = []
    for host in net:
        ip_str = str(host)
        especial = None
        if tem_rede_broadcast:
            if host == net.network_address:
                especial = 'rede'
            elif host == net.broadcast_address:
                especial = 'broadcast'
        if especial is None and gateway_ip and ip_str == gateway_ip:
            especial = 'gateway'

        e = enderecos.get(ip_str)
        r = scans.get(ip_str)
        grade.append({
            'ip': ip_str,
            'especial': especial,
            'endereco_id': e.id if e else None,
            'tipo': e.tipo if e else None,
            'status': e.status if e else None,
            'hostname': e.hostname if e else '',
            'descricao': e.descricao if e else '',
            'mac_address': e.mac_address if e else '',
            'online': r.online if r else None,
            'checado_em': r.checado_em.isoformat() if r else None,
        })

    return JsonResponse({
        'ok': True,
        'rede': str(net),
        'gateway': gateway_ip,
        'total': len(grade),
        'grade': grade,
        'ultimo_scan': s.ultimo_scan.isoformat() if s.ultimo_scan else None,
        'scan_automatico': s.scan_automatico,
    })


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
@ferramenta_instancia_required('ipam')
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
@ferramenta_instancia_required('ipam')
def ipam_ip_salvar(request, cliente_id):
    c    = _cliente(request, cliente_id)
    body = _json(request)
    eid  = body.get('id')
    try:
        ip_str = body.get('ip', '').strip()
        ipaddress.ip_address(ip_str)
        if eid:
            obj = get_object_or_404(IPAMEndereco, id=eid, cliente=c)
            antes = _ipam_snapshot('ip', obj)
        else:
            obj = IPAMEndereco(cliente=c)
            antes = None
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
        _ipam_log(request, c, 'ip', obj, 'updated' if eid else 'created', antes)
        return JsonResponse({'ok': True, 'id': obj.id})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
def ipam_ip_deletar(request, ip_id):
    obj = get_object_or_404(IPAMEndereco, id=ip_id)
    _checar_obj_cliente(request, obj)
    _ipam_log(request, obj.cliente, 'ip', obj, 'deleted')
    obj.delete()
    return JsonResponse({'ok': True})


# ─────────────────────────────────────────────────────────────────────────────
# VPNs documentadas
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@ferramenta_instancia_required('ipam')
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
@ferramenta_instancia_required('ipam')
def ipam_vpn_salvar(request, cliente_id):
    c    = _cliente(request, cliente_id)
    body = _json(request)
    vid  = body.get('id')
    try:
        if vid:
            obj = get_object_or_404(IPAMVpnDoc, id=vid, cliente=c)
            antes = _ipam_snapshot('vpn', obj)
        else:
            obj = IPAMVpnDoc(cliente=c)
            antes = None
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
        _ipam_log(request, c, 'vpn', obj, 'updated' if vid else 'created', antes)
        return JsonResponse({'ok': True, 'id': obj.id})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
def ipam_vpn_deletar(request, vpn_id):
    obj = get_object_or_404(IPAMVpnDoc, id=vpn_id)
    _checar_obj_cliente(request, obj)
    _ipam_log(request, obj.cliente, 'vpn', obj, 'deleted')
    obj.delete()
    return JsonResponse({'ok': True})


# ─────────────────────────────────────────────────────────────────────────────
# Importação phpIPAM (CSV)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
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


# ─────────────────────────────────────────────────────────────────────────────
# Análise automática de backups → documentação IPAM
# ─────────────────────────────────────────────────────────────────────────────

def _detect_vendor(content):
    """Detecta o fabricante pelo conteúdo do backup."""
    if '/ip address add' in content or '/interface vlan add' in content:
        return 'mikrotik'
    if 'vlan-type dot1q' in content or (
        re.search(r'^interface \S', content, re.MULTILINE) and
        re.search(r'^\s+ip address \d', content, re.MULTILINE)
    ):
        return 'huawei'
    return 'generic'


def _parse_mikrotik(content):
    """
    Parseia backup MikroTik.
    Extrai VLANs de  /interface vlan add ... vlan-id=NN name="..."
    Extrai IPs de    /ip address add address=X/XX comment="DESC" interface="..."
             e       /ipv6 address add address=X::X/XX comment="DESC" interface="..."
    """
    vlans = {}
    ips   = []

    for m in re.finditer(r'/interface vlan add\b[^\n]+', content, re.IGNORECASE):
        line = m.group(0)
        vid  = re.search(r'vlan-id=(\d+)', line)
        if not vid:
            continue
        vlan_id = int(vid.group(1))
        nm = re.search(r'name="([^"]+)"', line)
        nome = nm.group(1) if nm else f'VLAN {vlan_id}'
        vlans.setdefault(vlan_id, nome)

    def _extrai_enderecos(padrao_secao, padrao_endereco):
        for m in re.finditer(padrao_secao, content, re.IGNORECASE):
            line    = m.group(0)
            ip_m    = re.search(padrao_endereco, line)
            if not ip_m:
                continue
            ip_cidr = ip_m.group(1)
            cm      = re.search(r'comment="([^"]+)"', line)
            desc    = cm.group(1) if cm else ''
            ifm     = re.search(r'interface="([^"]+)"', line)
            iface   = ifm.group(1) if ifm else ''
            vlan_num = None
            vm = re.search(r'[Vv][Ll][Aa][Nn][-_]?(\d+)', iface)
            if vm:
                vlan_num = int(vm.group(1))
            ips.append((ip_cidr, desc, vlan_num))

    _extrai_enderecos(r'/ip address add\b[^\n]+', r'\baddress=([\d./]+)')
    _extrai_enderecos(r'/ipv6 address add\b[^\n]+', r'\baddress=([0-9a-fA-F:]+/\d+)')

    return {
        'vlans': [{'numero': n, 'nome': v} for n, v in vlans.items()],
        'ips': ips,
    }


def _parse_huawei(content):
    """
    Parseia backup Huawei VRP.
    Blocos de interface: description, ip address X.X.X.X MASK, ipv6 address X::X/XX,
    vlan-type dot1q NN
    """
    vlans = {}
    ips   = []

    blocks = re.split(r'\n(?=interface )', content)

    for block in blocks:
        lines = block.splitlines()
        if not lines or not lines[0].startswith('interface'):
            continue

        iface_name = lines[0][len('interface'):].strip()
        desc       = ''
        # Lista, não valor único — uma interface dual-stack tem IPv4 E IPv6
        # (e às vezes IPv4 secundário) ao mesmo tempo.
        ip_cidrs   = []
        vlan_num   = None

        for line in lines[1:]:
            ls = line.strip()
            if not ls or ls == '#':
                continue
            if ls.startswith('interface '):
                break

            m = re.match(r'description\s+(.+)', ls, re.IGNORECASE)
            if m:
                desc = m.group(1).strip()

            m = re.match(r'ip address\s+([\d.]+)\s+([\d.]+)', ls, re.IGNORECASE)
            if m:
                try:
                    net = ipaddress.ip_network(f'{m.group(1)}/{m.group(2)}', strict=False)
                    ip_cidrs.append(f'{m.group(1)}/{net.prefixlen}')
                except Exception:
                    pass

            m = re.match(r'ipv6 address\s+([0-9a-fA-F:]+/\d+)', ls, re.IGNORECASE)
            if m:
                ip_cidrs.append(m.group(1))

            m = re.match(r'vlan-type dot1q\s+(\d+)', ls, re.IGNORECASE)
            if m:
                vlan_num = int(m.group(1))
                vlans.setdefault(vlan_num, f'VLAN {vlan_num}')

        for ip_cidr in ip_cidrs:
            ips.append((ip_cidr, desc or iface_name, vlan_num))

    return {
        'vlans': [{'numero': n, 'nome': v} for n, v in vlans.items()],
        'ips': ips,
    }


def _parse_generic(content):
    """
    Parser genérico (Parks, Cisco IOS, etc.).
    Procura blocos interface com ip address X/XX, ip address X M ou
    ipv6 address X::X/XX.
    """
    vlans = {}
    ips   = []

    blocks = re.split(r'\n(?=interface\s)', content, flags=re.IGNORECASE)

    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        first = lines[0].strip()
        if not re.match(r'interface\s+\S', first, re.IGNORECASE):
            continue

        iface_name = re.sub(r'^interface\s+', '', first, flags=re.IGNORECASE).strip()
        desc       = ''
        # Lista, não valor único — uma interface dual-stack tem IPv4 E IPv6
        # (e às vezes IPv4 secundário) ao mesmo tempo.
        ip_cidrs   = []
        vlan_num   = None

        for line in lines[1:]:
            ls = line.strip()
            if not ls or ls in ('!', '#'):
                break

            m = re.match(r'description\s+(.+)', ls, re.IGNORECASE)
            if m:
                desc = m.group(1).strip()

            m = re.match(r'ip(?:v4)? address\s+([\d]+\.[\d]+\.[\d]+\.[\d]+/\d+)', ls, re.IGNORECASE)
            if m:
                ip_cidrs.append(m.group(1))
            else:
                m = re.match(r'ip(?:v4)? address\s+([\d.]+)\s+([\d.]+)', ls, re.IGNORECASE)
                if m:
                    try:
                        net = ipaddress.ip_network(f'{m.group(1)}/{m.group(2)}', strict=False)
                        ip_cidrs.append(f'{m.group(1)}/{net.prefixlen}')
                    except Exception:
                        pass

            m = re.match(r'ipv6 address\s+([0-9a-fA-F:]+/\d+)', ls, re.IGNORECASE)
            if m:
                ip_cidrs.append(m.group(1))

            m = re.search(r'dot1[qQ]\s+(\d+)', ls)
            if m:
                vlan_num = int(m.group(1))
                vlans.setdefault(vlan_num, f'VLAN {vlan_num}')

        for ip_cidr in ip_cidrs:
            ips.append((ip_cidr, desc or iface_name, vlan_num))

    return {
        'vlans': [{'numero': n, 'nome': v} for n, v in vlans.items()],
        'ips': ips,
    }




# ─────────────────────────────────────────────────────────────────────────────
# Contexto rico para o Agent NOC — interfaces + todos os protocolos
# ─────────────────────────────────────────────────────────────────────────────


def _full_parse_huawei(content):
    hostname = ''
    m = re.search(r'^sysname\s+(\S+)', content, re.MULTILINE)
    if m:
        hostname = m.group(1)
    interfaces = []
    for block in re.split(r'\n(?=interface )', content):
        lines = block.splitlines()
        if not lines or not lines[0].startswith('interface'):
            continue
        nome = lines[0][len('interface'):].strip()
        desc = ip_cidr = vlan = None
        shutdown = False
        for line in lines[1:]:
            ls = line.strip()
            if not ls or ls == '#' or ls.startswith('interface '):
                break
            if re.match(r'description\s+', ls, re.I):
                desc = ls.split(None, 1)[1].strip()
            m2 = re.match(r'ip address\s+([\d.]+)\s+([\d.]+)', ls, re.I)
            if m2:
                try:
                    net = ipaddress.ip_network(f'{m2.group(1)}/{m2.group(2)}', strict=False)
                    ip_cidr = f'{m2.group(1)}/{net.prefixlen}'
                except Exception:
                    pass
            m2 = re.match(r'vlan-type dot1q\s+(\d+)', ls, re.I)
            if m2:
                vlan = int(m2.group(1))
            if ls == 'shutdown':
                shutdown = True
        interfaces.append({'nome': nome, 'desc': desc or '', 'ip': ip_cidr,
                           'vlan': vlan, 'shutdown': shutdown})
    rotas = []
    for m2 in re.finditer(r'ip route-static\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)', content, re.I):
        try:
            net = ipaddress.ip_network(f'{m2.group(1)}/{m2.group(2)}', strict=False)
            rotas.append(f'{net} via {m2.group(3)}')
        except Exception:
            pass
    return {'hostname': hostname, 'interfaces': interfaces, 'rotas': rotas}


def _full_parse_mikrotik(content):
    hostname = ''
    m = re.search(r'/system identity set name="?([^"\n]+)"?', content, re.I)
    if m:
        hostname = m.group(1).strip()
    iface_map = {}
    for pattern in [r'/interface ethernet\b[^\n]+', r'/interface vlan add\b[^\n]+',
                    r'/interface bridge add\b[^\n]+', r'/interface bonding add\b[^\n]+']:
        for m2 in re.finditer(pattern, content, re.I):
            line = m2.group(0)
            nm = re.search(r'name="([^"]+)"', line)
            cm = re.search(r'comment="([^"]+)"', line)
            vid = re.search(r'vlan-id=(\d+)', line)
            if nm:
                iface_map[nm.group(1)] = {'desc': cm.group(1) if cm else '',
                                           'vlan': int(vid.group(1)) if vid else None}
    ip_map = {}
    for m2 in re.finditer(r'/ip address add\b[^\n]+', content, re.I):
        line = m2.group(0)
        addr_m = re.search(r'\baddress=([\d./]+)', line)
        if_m   = re.search(r'interface="([^"]+)"', line)
        cm     = re.search(r'comment="([^"]+)"', line)
        if addr_m and if_m:
            ip_map.setdefault(if_m.group(1), []).append(
                (addr_m.group(1), cm.group(1) if cm else ''))
    interfaces = []
    for nome in sorted(set(iface_map) | set(ip_map)):
        info = iface_map.get(nome, {'desc': '', 'vlan': None})
        for ip_cidr, ip_desc in ip_map.get(nome, [(None, '')]):
            interfaces.append({'nome': nome, 'desc': ip_desc or info['desc'],
                               'ip': ip_cidr, 'vlan': info['vlan'], 'shutdown': False})
    rotas = []
    for m2 in re.finditer(r'/ip route add\b[^\n]+dst-address=([\d./]+)[^\n]+gateway=([\d.]+)', content, re.I):
        rotas.append(f'{m2.group(1)} via {m2.group(2)}')
    return {'hostname': hostname, 'interfaces': interfaces, 'rotas': list(dict.fromkeys(rotas))}


def _full_parse_generic(content):
    hostname = ''
    m = re.search(r'^hostname\s+(\S+)', content, re.MULTILINE | re.I)
    if m:
        hostname = m.group(1)
    interfaces = []
    for block in re.split(r'\n(?=interface\s)', content, flags=re.I):
        lines = block.splitlines()
        if not lines or not re.match(r'interface\s+\S', lines[0].strip(), re.I):
            continue
        nome = re.sub(r'^interface\s+', '', lines[0].strip(), flags=re.I).strip()
        desc = ip_cidr = vlan = None
        shutdown = False
        for line in lines[1:]:
            ls = line.strip()
            if not ls or ls in ('!', '#'):
                break
            if re.match(r'description\s+', ls, re.I):
                desc = ls.split(None, 1)[1].strip()
            m2 = re.match(r'ip(?:v4)? address\s+([\d.]+/\d+)', ls, re.I)
            if m2:
                ip_cidr = m2.group(1)
            if not ip_cidr:
                m2 = re.match(r'ip(?:v4)? address\s+([\d.]+)\s+([\d.]+)', ls, re.I)
                if m2:
                    try:
                        net = ipaddress.ip_network(f'{m2.group(1)}/{m2.group(2)}', strict=False)
                        ip_cidr = f'{m2.group(1)}/{net.prefixlen}'
                    except Exception:
                        pass
            m2 = re.search(r'dot1[qQ]\s+(\d+)', ls)
            if m2:
                vlan = int(m2.group(1))
            if re.match(r'shutdown', ls, re.I):
                shutdown = True
        interfaces.append({'nome': nome, 'desc': desc or '', 'ip': ip_cidr,
                           'vlan': vlan, 'shutdown': shutdown})
    rotas = []
    for m2 in re.finditer(r'ip route\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)', content, re.I):
        try:
            net = ipaddress.ip_network(f'{m2.group(1)}/{m2.group(2)}', strict=False)
            rotas.append(f'{net} via {m2.group(3)}')
        except Exception:
            pass
    return {'hostname': hostname, 'interfaces': interfaces, 'rotas': rotas}

def _proto_mikrotik(content):
    """Extrai resumo de todos os protocolos de um backup MikroTik."""
    proto = {}

    # ── BGP ──────────────────────────────────────────────────────────────────
    bgp_instance = re.search(r'/routing bgp instance[^\n]*\bas=(\d+)', content, re.I)
    if not bgp_instance:
        bgp_instance = re.search(r'/routing bgp template[^\n]*\bas=(\d+)', content, re.I)
    bgp_asn = bgp_instance.group(1) if bgp_instance else None

    bgp_peers = []
    for m in re.finditer(r'/routing bgp peer add\b[^\n]+', content, re.I):
        line = m.group(0)
        addr = re.search(r'remote-address=([\d.:a-fA-F/]+)', line)
        asn  = re.search(r'remote-as=(\d+)', line)
        name = re.search(r'name="([^"]+)"', line)
        if addr:
            bgp_peers.append({
                'ip': addr.group(1),
                'as': asn.group(1) if asn else '?',
                'desc': name.group(1) if name else '',
            })
    bgp_nets = re.findall(r'/routing bgp network add[^\n]*network=([\d./]+)', content, re.I)

    if bgp_asn or bgp_peers:
        proto['bgp'] = {'asn_local': bgp_asn, 'peers': bgp_peers, 'redes': bgp_nets}

    # ── OSPF ─────────────────────────────────────────────────────────────────
    ospf_inst = re.findall(r'/routing ospf instance add[^\n]+', content, re.I)
    ospf_areas = re.findall(r'/routing ospf area add[^\n]+', content, re.I)
    ospf_ifaces = re.findall(r'/routing ospf interface add[^\n]+', content, re.I)
    if ospf_inst:
        router_ids = [re.search(r'router-id=([\d.]+)', l) for l in ospf_inst]
        proto['ospf'] = {
            'router_ids': [m.group(1) for m in router_ids if m],
            'areas': len(ospf_areas),
            'interfaces': len(ospf_ifaces),
        }

    # ── PPPoE / PPP ───────────────────────────────────────────────────────────
    pppoe_servers = re.findall(r'/interface pppoe-server server[^\n]*interface=([\S]+)', content, re.I)
    ppp_profiles = []
    for m in re.finditer(r'/ppp profile add\b[^\n]+', content, re.I):
        line = m.group(0)
        nm   = re.search(r'name=(\S+)', line)
        rl   = re.search(r'rate-limit=([\S]+)', line)
        la   = re.search(r'local-address=([\S]+)', line)
        ra   = re.search(r'remote-address=([\S]+)', line)
        if nm:
            ppp_profiles.append({
                'nome': nm.group(1).strip('"'),
                'rate_limit': rl.group(1) if rl else '',
                'local': la.group(1) if la else '',
                'pool': ra.group(1) if ra else '',
            })
    ip_pools = []
    for m in re.finditer(r'/ip pool add\b[^\n]+', content, re.I):
        line = m.group(0)
        nm   = re.search(r'name=([\S]+)', line)
        rng  = re.search(r'ranges=([\S]+)', line)
        if nm:
            ip_pools.append({'nome': nm.group(1).strip('"'),
                             'ranges': rng.group(1) if rng else ''})
    if pppoe_servers or ppp_profiles:
        proto['pppoe'] = {
            'servidores': pppoe_servers,
            'perfis': ppp_profiles,
            'pools': ip_pools,
        }

    # ── DHCP ─────────────────────────────────────────────────────────────────
    dhcp_servers = []
    for m in re.finditer(r'/ip dhcp-server add\b[^\n]+', content, re.I):
        line = m.group(0)
        nm   = re.search(r'name=([\S]+)', line)
        ifc  = re.search(r'interface=([\S]+)', line)
        pool = re.search(r'address-pool=([\S]+)', line)
        if nm:
            dhcp_servers.append({
                'nome': nm.group(1).strip('"'),
                'interface': ifc.group(1).strip('"') if ifc else '',
                'pool': pool.group(1).strip('"') if pool else '',
            })
    if dhcp_servers:
        proto['dhcp'] = {'servidores': dhcp_servers, 'pools': ip_pools}

    # ── Firewall ──────────────────────────────────────────────────────────────
    fw_filter = len(re.findall(r'/ip firewall filter add\b', content, re.I))
    fw_nat    = len(re.findall(r'/ip firewall nat add\b', content, re.I))
    fw_mangle = len(re.findall(r'/ip firewall mangle add\b', content, re.I))
    nat_rules = []
    for m in re.finditer(r'/ip firewall nat add\b[^\n]+', content, re.I):
        line   = m.group(0)
        action = re.search(r'action=(\S+)', line)
        chain  = re.search(r'chain=(\S+)', line)
        src    = re.search(r'src-address=([\S]+)', line)
        to     = re.search(r'to-addresses=([\S]+)', line)
        comment = re.search(r'comment="([^"]+)"', line)
        if action:
            nat_rules.append({
                'action': action.group(1),
                'chain': chain.group(1) if chain else '',
                'src': src.group(1) if src else '',
                'to': to.group(1) if to else '',
                'comment': comment.group(1) if comment else '',
            })
    if fw_filter or fw_nat:
        proto['firewall'] = {
            'filter_rules': fw_filter,
            'nat_rules': fw_nat,
            'mangle_rules': fw_mangle,
            'nat_detalhes': nat_rules[:10],
        }

    # ── Queue / QoS ───────────────────────────────────────────────────────────
    q_tree   = len(re.findall(r'/queue tree add\b', content, re.I))
    q_simple = len(re.findall(r'/queue simple add\b', content, re.I))
    if q_tree or q_simple:
        proto['qos'] = {'queue_tree': q_tree, 'queue_simple': q_simple}

    # ── MPLS ─────────────────────────────────────────────────────────────────
    if '/mpls' in content.lower():
        ldp_ifaces = re.findall(r'/mpls ldp interface add[^\n]*interface=([\S]+)', content, re.I)
        proto['mpls'] = {'ldp_interfaces': [i.strip('"') for i in ldp_ifaces]}

    # ── L2TP / OpenVPN / SSTP ────────────────────────────────────────────────
    vpn = {}
    if re.search(r'/interface l2tp-server server set[^\n]*enabled=yes', content, re.I):
        vpn['l2tp'] = 'ativo'
    if re.search(r'/interface ovpn-server server add[^\n]*disabled=no', content, re.I):
        ovpn_ports = re.findall(r'/interface ovpn-server server add[^\n]*port=(\d+)', content, re.I)
        vpn['openvpn'] = f"portas: {', '.join(ovpn_ports)}" if ovpn_ports else 'ativo'
    if re.search(r'/interface sstp-server server set[^\n]*enabled=yes', content, re.I):
        vpn['sstp'] = 'ativo'
    if vpn:
        proto['vpn_servers'] = vpn

    # ── SNMP ─────────────────────────────────────────────────────────────────
    if '/snmp set' in content.lower():
        snmp_comm = re.findall(r'/snmp community set[^\n]*name=([\S]+)', content, re.I)
        proto['snmp'] = {'communities': [c.strip('"') for c in snmp_comm]}

    # ── Hotspot ───────────────────────────────────────────────────────────────
    if '/ip hotspot add' in content.lower():
        hs_ifaces = re.findall(r'/ip hotspot add[^\n]*interface=([\S]+)', content, re.I)
        proto['hotspot'] = {'interfaces': [i.strip('"') for i in hs_ifaces]}

    return proto


def _proto_huawei(content):
    """Extrai resumo de todos os protocolos de um backup Huawei VRP."""
    proto = {}

    # ── BGP ──────────────────────────────────────────────────────────────────
    bgp_m = re.search(r'^\s*bgp\s+(\d+)', content, re.MULTILINE)
    if bgp_m:
        asn = bgp_m.group(1)
        idx = bgp_m.start()
        bloco = content[idx:idx+30000]  # aumentado para capturar todos os peers
        peers = []
        seen_ips = set()
        for m in re.finditer(r'peer\s+([\d.:a-fA-F]+)\s+as-number\s+(\d+)', bloco, re.I):
            ip_p, asn_p = m.group(1), m.group(2)
            if ip_p in seen_ips:
                continue
            seen_ips.add(ip_p)
            desc_m = re.search(
                rf'peer\s+{re.escape(ip_p)}\s+description\s+(.+)', bloco, re.I)
            peers.append({
                'ip': ip_p, 'as': asn_p,
                'desc': desc_m.group(1).strip() if desc_m else '',
            })
        nets = re.findall(r'network\s+([\d.:/a-fA-F]+)', bloco, re.I)
        proto['bgp'] = {'asn_local': asn, 'peers': peers, 'redes': nets[:20]}

    # ── OSPF ─────────────────────────────────────────────────────────────────
    ospf_m = re.search(r'^\s*ospf\s+(\d+)', content, re.MULTILINE)
    if ospf_m:
        idx   = ospf_m.start()
        bloco = content[idx:idx+2000]
        rid_m = re.search(r'router-id\s+([\d.]+)', bloco, re.I)
        areas = re.findall(r'area\s+([\d.]+)', bloco, re.I)
        proto['ospf'] = {
            'instancia': ospf_m.group(1),
            'router_id': rid_m.group(1) if rid_m else '',
            'areas': list(dict.fromkeys(areas)),
        }

    # ── MPLS / LDP ───────────────────────────────────────────────────────────
    mpls_lsr = re.search(r'mpls lsr-id\s+([\d.]+)', content, re.I)
    if mpls_lsr:
        ldp_ifaces = re.findall(r'mpls ldp\s*\n.*?interface\s+(\S+)', content, re.I | re.DOTALL)
        proto['mpls'] = {
            'lsr_id': mpls_lsr.group(1),
            'ldp_interfaces': ldp_ifaces[:10],
        }

    # ── VRF ──────────────────────────────────────────────────────────────────
    vrfs = re.findall(r'ip vpn-instance\s+(\S+)', content, re.I)
    if vrfs:
        proto['vrf'] = list(dict.fromkeys(vrfs))

    # ── BAS / PPPoE / AAA ────────────────────────────────────────────────────
    domains = re.findall(r'^domain\s+(\S+)', content, re.MULTILINE | re.I)
    aaa_schemes = re.findall(r'radius-server\s+template\s+(\S+)', content, re.I)
    if domains:
        proto['bas_aaa'] = {
            'domains': domains[:15],
            'radius_templates': list(dict.fromkeys(aaa_schemes))[:5],
        }

    # ── IP Pools (BRAS) ───────────────────────────────────────────────────────
    pools = []
    for m in re.finditer(r'ip pool\s+(\S+)[^\n]*\n((?:[ \t]+[^\n]+\n)*)', content, re.I):
        nome  = m.group(1)
        corpo = m.group(2)
        secs  = re.findall(r'section\s+\d+\s+([\d.]+)\s+([\d.]+)', corpo)
        pools.append({'nome': nome, 'ranges': [f'{s[0]}-{s[1]}' for s in secs]})
    if pools:
        proto['ip_pools'] = pools[:10]

    # ── Route-Policy ─────────────────────────────────────────────────────────
    policies = list(dict.fromkeys(re.findall(r'^route-policy\s+(\S+)', content, re.MULTILINE)))
    if policies:
        proto['route_policies'] = policies[:20]

    # ── SNMP ─────────────────────────────────────────────────────────────────
    snmp_comm = re.findall(r'snmp-agent community\s+\S+\s+(\S+)', content, re.I)
    snmp_trap = re.findall(r'snmp-agent trap enable\s+(.+)', content, re.I)
    if snmp_comm:
        proto['snmp'] = {'communities': snmp_comm[:5]}

    # ── NTP ──────────────────────────────────────────────────────────────────
    ntp_servers = re.findall(r'ntp-service unicast-server\s+([\d.]+)', content, re.I)
    if ntp_servers:
        proto['ntp'] = ntp_servers

    # ── GPON / OLT ───────────────────────────────────────────────────────────
    if 'gpon' in content.lower():
        gpon_profiles = list(dict.fromkeys(
            re.findall(r'gpon profile\s+\S+\s+(\S+)', content, re.I)
        ))
        gpon_onus = len(re.findall(r'^\s*onu\s+\d+', content, re.MULTILINE))
        proto['gpon'] = {'profiles': gpon_profiles[:10], 'total_onus': gpon_onus}

    return proto


def _proto_generic(content):
    """Extrai protocolos de backup genérico (Cisco IOS, Parks, Datacom, etc.)."""
    proto = {}

    # ── BGP ──────────────────────────────────────────────────────────────────
    bgp_m = re.search(r'^router bgp\s+(\d+)', content, re.MULTILINE | re.I)
    if bgp_m:
        idx   = bgp_m.start()
        bloco = content[idx:idx+3000]
        peers = re.findall(r'neighbor\s+([\d.]+)\s+remote-as\s+(\d+)', bloco, re.I)
        nets  = re.findall(r'network\s+([\d./]+)', bloco, re.I)
        proto['bgp'] = {
            'asn_local': bgp_m.group(1),
            'peers': [{'ip': p[0], 'as': p[1], 'desc': ''} for p in peers],
            'redes': nets[:20],
        }

    # ── OSPF ─────────────────────────────────────────────────────────────────
    ospf_m = re.search(r'^router ospf\s+(\d+)', content, re.MULTILINE | re.I)
    if ospf_m:
        idx   = ospf_m.start()
        bloco = content[idx:idx+1000]
        rid_m = re.search(r'router-id\s+([\d.]+)', bloco, re.I)
        nets  = re.findall(r'network\s+([\d.]+)\s+([\d.]+)\s+area\s+([\d.]+)', bloco, re.I)
        proto['ospf'] = {
            'instancia': ospf_m.group(1),
            'router_id': rid_m.group(1) if rid_m else '',
            'networks': [f'{n[0]}/{n[1]} area {n[2]}' for n in nets],
        }

    # ── VRF ──────────────────────────────────────────────────────────────────
    vrfs = re.findall(r'^ip vrf\s+(\S+)', content, re.MULTILINE | re.I)
    if vrfs:
        proto['vrf'] = vrfs

    # ── GPON / OLT genérico ───────────────────────────────────────────────────
    if 'gpon' in content.lower():
        gpon_profiles = list(dict.fromkeys(
            re.findall(r'gpon profile\s+\S+\s+(\S+)', content, re.I)
        ))
        vlans_gpon = list(dict.fromkeys(re.findall(r'vlan\s+(\d+)\s+service', content, re.I)))
        gpon_onus = len(re.findall(r'serial-number\s+[A-Z0-9]{4,}', content))
        proto['gpon'] = {
            'profiles': gpon_profiles[:10],
            'vlans_servico': vlans_gpon[:20],
            'total_onus': gpon_onus,
        }

    # ── SNMP ─────────────────────────────────────────────────────────────────
    snmp_comm = re.findall(r'snmp(?:-server)?\s+community\s+(\S+)', content, re.I)
    if snmp_comm:
        proto['snmp'] = {'communities': list(dict.fromkeys(snmp_comm))[:5]}

    # ── AAA ──────────────────────────────────────────────────────────────────
    radius = re.findall(r'radius-server\s+host\s+([\d.]+)', content, re.I)
    if radius:
        proto['radius'] = list(dict.fromkeys(radius))

    # ── VLANs (OLT style) ────────────────────────────────────────────────────
    vlans_db = re.findall(r'vlan\s+([\d,\-]+)', content[:3000], re.I)
    if vlans_db:
        proto['vlans_configuradas'] = vlans_db[:5]

    return proto


def _cmds_interface(vendor, iface_nome):
    n = iface_nome
    if vendor == 'huawei':
        return [f'display interface {n}', f'display ip interface {n}']
    if vendor == 'mikrotik':
        return [f'/interface print where name="{n}"',
                f'/ip address print where interface="{n}"']
    return [f'show interface {n}']


def _cmds_proto(vendor):
    """Retorna comandos de verificação por protocolo para cada fabricante."""
    if vendor == 'mikrotik':
        return {
            'bgp':      ['/routing bgp peer print', '/routing bgp advertisements print peer=<PEER>'],
            'ospf':     ['/routing ospf neighbor print', '/routing ospf route print'],
            'pppoe':    ['/ppp active print', '/interface pppoe-server print'],
            'dhcp':     ['/ip dhcp-server lease print', '/ip dhcp-server print'],
            'firewall': ['/ip firewall filter print', '/ip firewall nat print', '/ip firewall mangle print'],
            'qos':      ['/queue tree print', '/queue simple print'],
            'mpls':     ['/mpls forwarding-table print', '/mpls ldp neighbor print'],
            'vpn':      ['/interface l2tp-server print', '/ppp active print where service=l2tp'],
            'snmp':     ['/snmp print', '/snmp community print'],
            'hotspot':  ['/ip hotspot active print', '/ip hotspot host print'],
            'rotas':    ['/ip route print', '/ip route print where active=yes'],
        }
    if vendor == 'huawei':
        return {
            'bgp':       ['display bgp peer', 'display bgp routing-table', 'display bgp peer verbose'],
            'ospf':      ['display ospf peer', 'display ospf routing', 'display ospf lsdb'],
            'mpls':      ['display mpls ldp session', 'display mpls forwarding-table', 'display mpls lsp'],
            'vrf':       ['display ip vpn-instance', 'display ip routing-table vpn-instance <VRF>'],
            'bas_aaa':   ['display access-user', 'display domain', 'display radius-server'],
            'ip_pools':  ['display ip pool name <POOL>', 'display ip pool'],
            'gpon':      ['display ont info summary <SLOT> <PON>', 'display ont alarm-state all'],
            'snmp':      ['display snmp-agent community', 'display snmp-agent trap enable'],
            'ntp':       ['display ntp-service status', 'display ntp-service sessions'],
            'rotas':     ['display ip routing-table', 'display ip routing-table statistics'],
        }
    # generic/cisco/parks/datacom
    return {
        'bgp':   ['show bgp summary', 'show bgp neighbors', 'show bgp'],
        'ospf':  ['show ip ospf neighbor', 'show ip ospf database', 'show ip ospf'],
        'vrf':   ['show ip vrf', 'show ip route vrf <VRF>'],
        'snmp':  ['show snmp community', 'show snmp'],
        'gpon':  ['show gpon onu state', 'show gpon onu detail-info <SLOT> <PON> <ID>'],
        'rotas': ['show ip route', 'show ip route summary'],
    }


def _build_contexto_backup(vendor, content):
    """
    Gera contexto completo para o Agent NOC:
    hostname, todas interfaces + IPs, VLANs, e resumo de TODOS os protocolos
    configurados com os comandos exatos para inspecioná-los.
    """
    if vendor == 'huawei':
        iface_data = _full_parse_huawei(content)
        proto_data = _proto_huawei(content)
    elif vendor == 'mikrotik':
        iface_data = _full_parse_mikrotik(content)
        proto_data = _proto_mikrotik(content)
    else:
        iface_data = _full_parse_generic(content)
        proto_data = _proto_generic(content)

    cmds = _cmds_proto(vendor)
    ifaces   = iface_data['interfaces']
    hostname = iface_data.get('hostname', '')
    rotas    = iface_data.get('rotas', [])

    com_ip = [i for i in ifaces if i['ip']]
    sem_ip = [i for i in ifaces if not i['ip'] and not i['shutdown']]

    linhas = []
    header = f'Fabricante: {vendor}'
    if hostname:
        header += f' | Hostname: {hostname}'
    linhas.append(header)

    # ── Protocolos (primeiro — nunca truncado) ────────────────────────────────
    if proto_data:
        linhas.append('\nProtocolos configurados:')

    if 'bgp' in proto_data:
        b = proto_data['bgp']
        linhas.append(f"\nBGP (ASN local: {b.get('asn_local','?')}):")
        linhas.append(f"  Mapeamento de sessões (descrição → IP → comando de verificação):")
        for p in b.get('peers', []):
            label = p['desc'] if p['desc'] else p['ip']
            desc_txt = f" — {p['desc']}" if p['desc'] else ''
            linhas.append(f"  Peer {p['ip']} AS{p['as']}{desc_txt}")
            # Comando específico por peer para checar status da sessão
            if vendor == 'huawei':
                linhas.append(f"    → display bgp peer {p['ip']}  [verificar: {label}]")
            elif vendor == 'mikrotik':
                if p['desc']:
                    linhas.append(f"    → /routing bgp peer print where name=\"{p['desc']}\"  [por descrição]")
                linhas.append(f"    → /routing bgp peer print where remote-address={p['ip']}  [por IP]")
            else:
                linhas.append(f"    → show bgp neighbors {p['ip']}  [verificar: {label}]")
        if b.get('redes'):
            linhas.append(f"  Redes anunciadas: {', '.join(b['redes'][:8])}")
        if 'bgp' in cmds:
            for c in cmds['bgp'][:2]:
                linhas.append(f'  → {c}')

    if 'ospf' in proto_data:
        o = proto_data['ospf']
        rid_list = o.get('router_ids', []); rid = o.get('router_id') or (rid_list[0] if rid_list else '')
        areas = o.get('areas', [])
        areas_str = ', '.join(areas) if isinstance(areas, list) else str(areas)
        linhas.append(f"\nOSPF (instância {o.get('instancia','1')} | router-id: {rid} | áreas: {areas_str}):")
        if 'ospf' in cmds:
            for c in cmds['ospf'][:2]:
                linhas.append(f'  → {c}')

    if 'mpls' in proto_data:
        m = proto_data['mpls']
        lsr = m.get('lsr_id', '')
        linhas.append(f"\nMPLS/LDP (LSR-ID: {lsr}):")
        if m.get('ldp_interfaces'):
            linhas.append(f"  Interfaces LDP: {', '.join(m['ldp_interfaces'][:5])}")
        if 'mpls' in cmds:
            for c in cmds['mpls'][:2]:
                linhas.append(f'  → {c}')

    if 'vrf' in proto_data:
        vrfs = proto_data['vrf']
        linhas.append(f"\nVRF ({len(vrfs)} instâncias): {', '.join(vrfs[:10])}")
        if 'vrf' in cmds:
            linhas.append(f"  → {cmds['vrf'][0]}")

    if 'pppoe' in proto_data:
        p = proto_data['pppoe']
        serv = p.get('servidores', [])
        linhas.append(f"\nPPPoE/PPP:")
        if serv:
            linhas.append(f"  Servidores nas interfaces: {', '.join(serv)}")
        for pf in p.get('perfis', []):
            rl = f" rate-limit={pf['rate_limit']}" if pf['rate_limit'] else ''
            pool = f" pool={pf['pool']}" if pf['pool'] else ''
            linhas.append(f"  Perfil: {pf['nome']}{rl}{pool}")
        for pool in p.get('pools', []):
            linhas.append(f"  Pool: {pool['nome']} ranges={pool['ranges']}")
        if 'pppoe' in cmds:
            for c in cmds['pppoe'][:2]:
                linhas.append(f'  → {c}')

    if 'bas_aaa' in proto_data:
        b = proto_data['bas_aaa']
        doms = b.get('domains', [])
        linhas.append(f"\nBAS/AAA ({len(doms)} domínios): {', '.join(doms[:8])}")
        if b.get('radius_templates'):
            linhas.append(f"  Radius templates: {', '.join(b['radius_templates'])}")
        if 'bas_aaa' in cmds:
            for c in cmds['bas_aaa'][:2]:
                linhas.append(f'  → {c}')

    if 'ip_pools' in proto_data:
        linhas.append(f"\nIP Pools (BRAS):")
        for pool in proto_data['ip_pools'][:5]:
            rng = ', '.join(pool.get('ranges', [])[:3])
            linhas.append(f"  {pool['nome']}: {rng}")
        if 'ip_pools' in cmds:
            linhas.append(f"  → {cmds['ip_pools'][0]}")

    if 'dhcp' in proto_data:
        d = proto_data['dhcp']
        linhas.append(f"\nDHCP ({len(d.get('servidores',[]))} servidores):")
        for s in d.get('servidores', []):
            linhas.append(f"  {s['nome']} — interface={s['interface']} pool={s['pool']}")
        if 'dhcp' in cmds:
            for c in cmds['dhcp'][:2]:
                linhas.append(f'  → {c}')

    if 'firewall' in proto_data:
        fw = proto_data['firewall']
        linhas.append(f"\nFirewall: {fw['filter_rules']} filter | {fw['nat_rules']} NAT | {fw.get('mangle_rules',0)} mangle")
        for nr in fw.get('nat_detalhes', [])[:5]:
            cmt = f" [{nr['comment']}]" if nr['comment'] else ''
            src = f" src={nr['src']}" if nr['src'] else ''
            to  = f" to={nr['to']}" if nr['to'] else ''
            linhas.append(f"  NAT {nr['chain']} {nr['action']}{src}{to}{cmt}")
        if 'firewall' in cmds:
            for c in cmds['firewall'][:2]:
                linhas.append(f'  → {c}')

    if 'qos' in proto_data:
        q = proto_data['qos']
        linhas.append(f"\nQoS: {q.get('queue_tree',0)} queue-tree | {q.get('queue_simple',0)} queue-simple")
        if 'qos' in cmds:
            linhas.append(f"  → {cmds['qos'][0]}")

    if 'gpon' in proto_data:
        g = proto_data['gpon']
        linhas.append(f"\nGPON ({g.get('total_onus',0)} ONUs):")
        if g.get('profiles'):
            linhas.append(f"  Profiles: {', '.join(g['profiles'][:8])}")
        if g.get('vlans_servico'):
            linhas.append(f"  VLANs de serviço: {', '.join(g['vlans_servico'][:15])}")
        if 'gpon' in cmds:
            for c in cmds['gpon'][:2]:
                linhas.append(f'  → {c}')

    if 'vpn_servers' in proto_data:
        v = proto_data['vpn_servers']
        linhas.append(f"\nVPN Servers: {', '.join(f'{k}={v2}' for k,v2 in v.items())}")
        if 'vpn' in cmds:
            for c in cmds['vpn'][:2]:
                linhas.append(f'  → {c}')

    if 'snmp' in proto_data:
        s = proto_data['snmp']
        linhas.append(f"\nSNMP communities: {', '.join(s.get('communities',[]))}")

    if 'hotspot' in proto_data:
        h = proto_data['hotspot']
        linhas.append(f"\nHotspot interfaces: {', '.join(h.get('interfaces',[]))}")
        if 'hotspot' in cmds:
            for c in cmds['hotspot'][:2]:
                linhas.append(f'  → {c}')

    if 'route_policies' in proto_data:
        rp = proto_data['route_policies']
        linhas.append(f"\nRoute-Policies ({len(rp)}): {', '.join(rp[:10])}")

    if 'ntp' in proto_data:
        linhas.append(f"\nNTP servers: {', '.join(proto_data['ntp'][:3])}")

    # ── Interfaces com IP (após protocolos) ───────────────────────────────────
    if com_ip:
        linhas.append('\nInterfaces com endereço IP:')
        for i in com_ip:
            l = f"  {i['nome']}  IP={i['ip']}"
            if i['vlan']:
                l += f"  VLAN={i['vlan']}"
            if i['desc']:
                l += f'  desc="{i["desc"]}"'
            linhas.append(l)
            for c in _cmds_interface(vendor, i['nome']):
                linhas.append(f'    → {c}')

    # ── Interfaces sem IP ─────────────────────────────────────────────────────
    if sem_ip:
        nomes = ', '.join(
            (f"{i['nome']}({i['desc']})" if i['desc'] else i['nome'])
            for i in sem_ip[:25]
        )
        if len(sem_ip) > 25:
            nomes += f' ...+{len(sem_ip)-25} mais'
        linhas.append(f'\nDemais interfaces (sem IP): {nomes}')

    # ── Rotas ─────────────────────────────────────────────────────────────────
    if rotas:
        linhas.append('\nRotas estáticas:')
        for r in rotas[:10]:
            linhas.append(f'  {r}')
        if len(rotas) > 10:
            linhas.append(f'  ...+{len(rotas)-10} rotas')
        if 'rotas' in cmds:
            linhas.append(f'  → Verificar: {cmds["rotas"][0]}')

    if not com_ip and not sem_ip and not proto_data:
        return ''

    return '\n'.join(linhas)



@login_required
@require_http_methods(['POST'])
@ferramenta_instancia_required('ipam')
def ipam_analisar_backups(request, cliente_id):
    """
    Analisa os backups mais recentes dos acessos do cliente e auto-documenta
    IPs, descrições de interface e VLANs no IPAM nativo.
    """
    from django.conf import settings
    from .models import BackupLog

    c       = _cliente(request, cliente_id)
    acessos = Acesso.objects.filter(cliente=c)

    criados_vlans    = 0
    criados_subredes = 0
    criados_ips      = 0
    atualizados_ips  = 0
    processados      = 0
    erros            = []

    for acesso in acessos:
        # Label do equipamento para preencher o hostname do IP
        host_label = f"{acesso.tipo} ({acesso.host})" if acesso.tipo else acesso.host

        backup = (
            BackupLog.objects
            .filter(acesso=acesso, status='SUCESSO')
            .exclude(arquivo_path='')
            .order_by('-data_backup')
            .first()
        )
        if not backup:
            continue

        caminho = os.path.join(settings.MEDIA_ROOT, backup.arquivo_path)
        if not os.path.exists(caminho):
            erros.append(f'Acesso {acesso.id} ({acesso.host}): arquivo não encontrado')
            continue

        try:
            with open(caminho, 'r', encoding='utf-8', errors='replace') as fh:
                content = fh.read()
        except Exception as e:
            erros.append(f'Acesso {acesso.id}: erro ao ler arquivo: {e}')
            continue

        vendor = _detect_vendor(content)
        if vendor == 'mikrotik':
            parsed = _parse_mikrotik(content)
        elif vendor == 'huawei':
            parsed = _parse_huawei(content)
        else:
            parsed = _parse_generic(content)

        if not parsed['ips']:
            continue

        processados += 1

        # Salvar contexto no Acesso para o Agent NOC
        contexto = _build_contexto_backup(vendor, content)
        if contexto:
            from django.utils import timezone as _tz
            acesso.contexto_backup = contexto
            acesso.contexto_backup_em = _tz.now()
            acesso.save(update_fields=['contexto_backup', 'contexto_backup_em'])

        # Criar/atualizar VLANs
        vlan_map = {}
        for v in parsed['vlans']:
            obj, created = IPAMVlan.objects.get_or_create(
                cliente=c, numero=v['numero'],
                defaults={'nome': v['nome']}
            )
            if created:
                criados_vlans += 1
            vlan_map[v['numero']] = obj

        # Criar/atualizar SubRedes e IPs
        prefixo_cache = {}  # cidr_/24 → IPAMPrefixo

        def _get_or_create_prefixo_pai(net):
            """Retorna (IPAMPrefixo, created) para a rede /24 pai do endereço.
            Também garante que exista um IPAMSubRede correspondente ao bloco /24."""
            # Para IPv4: agrupa em /24. Para IPv6: agrupa em /48.
            if isinstance(net, ipaddress.IPv6Network):
                preflen = min(net.prefixlen, 48)
            else:
                preflen = min(net.prefixlen, 24)
            # Superrede pai com prefixlen mínimo
            pai_net = net.supernet(new_prefix=preflen) if net.prefixlen > preflen else net
            cidr_pai = str(pai_net)
            if cidr_pai in prefixo_cache:
                return prefixo_cache[cidr_pai], False
            obj, created = IPAMPrefixo.objects.get_or_create(
                cliente=c, prefixo=cidr_pai,
                defaults={'tipo': 'container', 'descricao': f'Bloco {cidr_pai} (auto)'}
            )
            prefixo_cache[cidr_pai] = obj
            # Garante que o bloco /24 também apareça como IPAMSubRede
            sub_pai, sub_criada = IPAMSubRede.objects.get_or_create(
                cliente=c, rede=cidr_pai,
                defaults={
                    'prefixo': obj,
                    'descricao': f'Bloco {cidr_pai} (auto)',
                    'status': 'reservado',
                }
            )
            if sub_criada:
                nonlocal criados_subredes
                criados_subredes += 1
            elif not sub_pai.prefixo:
                sub_pai.prefixo = obj
                sub_pai.save(update_fields=['prefixo'])
            return obj, created

        for ip_cidr, desc, vlan_num in parsed['ips']:
            try:
                net     = ipaddress.ip_network(ip_cidr, strict=False)
                ip_host = ip_cidr.split('/')[0]
                ipaddress.ip_address(ip_host)
                cidr_rede = str(net)
            except Exception:
                erros.append(f'Acesso {acesso.id}: CIDR inválido "{ip_cidr}"')
                continue

            # Resolver VLAN
            vlan_obj = None
            if vlan_num is not None:
                if vlan_num in vlan_map:
                    vlan_obj = vlan_map[vlan_num]
                else:
                    obj, created = IPAMVlan.objects.get_or_create(
                        cliente=c, numero=vlan_num,
                        defaults={'nome': f'VLAN {vlan_num}'}
                    )
                    if created:
                        criados_vlans += 1
                    vlan_map[vlan_num] = obj
                    vlan_obj = obj

            # Prefixo pai /24 (agrupador)
            prefixo_pai, _ = _get_or_create_prefixo_pai(net)

            # Sub-rede com CIDR real (ex: /30), vinculada ao prefixo pai
            sub, sub_created = IPAMSubRede.objects.get_or_create(
                cliente=c, rede=cidr_rede,
                defaults={
                    'vlan': vlan_obj,
                    'descricao': desc,
                    'prefixo': prefixo_pai,
                }
            )
            if sub_created:
                criados_subredes += 1
            else:
                fields = []
                if vlan_obj and not sub.vlan:
                    sub.vlan = vlan_obj
                    fields.append('vlan')
                if not sub.prefixo:
                    sub.prefixo = prefixo_pai
                    fields.append('prefixo')
                if fields:
                    sub.save(update_fields=fields)

            # IP
            ip_obj, ip_created = IPAMEndereco.objects.get_or_create(
                cliente=c, ip=ip_host,
                defaults={
                    'descricao': desc,
                    'hostname': host_label,
                    'subrede': sub,
                    'acesso': acesso,
                    'status': 'ativo',
                }
            )
            if ip_created:
                criados_ips += 1
            else:
                changed = False
                if not ip_obj.descricao and desc:
                    ip_obj.descricao = desc
                    changed = True
                if not ip_obj.hostname and host_label:
                    ip_obj.hostname = host_label
                    changed = True
                if not ip_obj.subrede:
                    ip_obj.subrede = sub
                    changed = True
                if not ip_obj.acesso:
                    ip_obj.acesso = acesso
                    changed = True
                if changed:
                    ip_obj.save()
                    atualizados_ips += 1

    return JsonResponse({
        'ok': True,
        'processados': processados,
        'criados_vlans': criados_vlans,
        'criados_subredes': criados_subredes,
        'criados_ips': criados_ips,
        'atualizados_ips': atualizados_ips,
        'erros': erros[:20],
        'total_erros': len(erros),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Histórico / auditoria
# ─────────────────────────────────────────────────────────────────────────────

HISTORICO_PAGE_SIZE = 50


@login_required
@ferramenta_instancia_required('ipam')
def ipam_historico_listar(request, cliente_id):
    c = _cliente(request, cliente_id)
    qs = IPAMAuditLog.objects.filter(cliente=c).select_related('usuario')

    modelo = request.GET.get('modelo', '').strip()
    if modelo:
        qs = qs.filter(modelo=modelo)

    try:
        pagina = max(1, int(request.GET.get('pagina', 1)))
    except (TypeError, ValueError):
        pagina = 1
    offset = (pagina - 1) * HISTORICO_PAGE_SIZE

    total = qs.count()
    itens = qs[offset:offset + HISTORICO_PAGE_SIZE]

    data = [{
        'id': log.id,
        'criado_em': log.criado_em.isoformat(),
        'modelo': log.modelo, 'modelo_label': log.get_modelo_display(),
        'acao': log.acao, 'acao_label': log.get_acao_display(),
        'objeto_id': log.objeto_id, 'objeto_repr': log.objeto_repr,
        'mudancas': log.mudancas,
        'usuario': log.usuario.get_username() if log.usuario else '—',
    } for log in itens]

    return JsonResponse({
        'ok': True, 'historico': data, 'total': total,
        'pagina': pagina, 'total_paginas': max(1, -(-total // HISTORICO_PAGE_SIZE)),
    })
