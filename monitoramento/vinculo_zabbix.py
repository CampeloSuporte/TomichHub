"""
monitoramento/vinculo_zabbix.py

Liga os Acessos do cliente aos hosts do Zabbix dele (ZabbixHostVinculo) e
devolve o status de cada um para a aba Acessos.

Casamento por IP:
- IP do acesso (sem esquema, caminho ou porta) e `host_ipv6` contra todos os
  IPs de interface de cada host do Zabbix.
- 1 host com aquele IP → vínculo automático. Vários acessos no mesmo IP vão
  para o mesmo host (ex.: SSH e Web do mesmo equipamento).
- Vários hosts com o mesmo IP → desempata pelo nome igual ao tipo do acesso;
  sem desempate é ambíguo: não vincula, aparece no resumo para escolher na tela.
- Vínculo manual nunca é tocado pelo sincronismo; `zabbix_hostid=''` manual
  quer dizer "não monitorado, não casar de novo".

A conexão com o Zabbix reaproveita `agent_zabbix.resolver_config`, que já
aplica o túnel SSH do proxy em IP privado e cai para as credenciais do acesso
"Zabbix" quando a configuração de Monitoramento está com senha errada.
"""
import ipaddress
import logging
import re

from django.core.cache import cache
from django.utils import timezone

from . import services
from .agent_zabbix import ZabbixIndisponivel
from .models import ZabbixHostVinculo

logger = logging.getLogger(__name__)

# IPs que o Zabbix usa para o próprio servidor/agent local — casariam com
# qualquer coisa e nunca são o IP de gerência de um equipamento.
_IPS_IGNORADOS = {'', '0.0.0.0', '127.0.0.1', '::1', '::'}

_TTL_STATUS   = 60       # status dos hosts (a aba consulta a cada 60s)
_TTL_INDISP   = 300      # Zabbix fora: não retentar a cada abertura da aba
_TTL_HOSTS    = 300      # lista de hosts para o seletor manual
_TTL_SYNC     = 6 * 3600  # sincronismo automático ao abrir a aba, no máximo a cada 6h


def _ck(nome, cliente_id):
    return f'zbx_vinc_{nome}:{cliente_id}'


def ip_do_acesso(host: str) -> str:
    """'https://10.0.0.1:8443/zabbix' → '10.0.0.1'. Devolve '' se não for IP."""
    h = (host or '').strip()
    if '://' in h:
        h = h.split('://', 1)[1]
    h = h.split('/', 1)[0]
    if h.startswith('['):                       # [2001:db8::1]:443
        h = h[1:].split(']', 1)[0]
    elif h.count(':') == 1:                     # 10.0.0.1:8080
        h = h.split(':', 1)[0]
    try:
        return str(ipaddress.ip_address(h))
    except ValueError:
        return ''


def _ips_do_acesso(acesso) -> set:
    ips = {ip_do_acesso(acesso.host), ip_do_acesso(acesso.host_ipv6 or '')}
    return ips - _IPS_IGNORADOS


def casar(acessos, hosts) -> dict:
    """
    Casamento puro (sem banco): {acesso_id: [hosts que casaram]}.
    Só entram acessos com pelo menos um IP válido.
    """
    por_ip = {}
    for h in hosts:
        for ip in h.get('ips', []):
            try:
                ip = str(ipaddress.ip_address(ip))
            except ValueError:
                continue
            if ip in _IPS_IGNORADOS:
                continue
            por_ip.setdefault(ip, {})[h['hostid']] = h

    resultado = {}
    for a in acessos:
        achados = {}
        for ip in _ips_do_acesso(a):
            achados.update(por_ip.get(ip, {}))
        resultado[a.id] = list(achados.values())
    return resultado


def _nome_normalizado(texto: str) -> str:
    return re.sub(r'[^0-9A-Z]', '', (texto or '').upper())


def desempatar(acesso, achados: list) -> list:
    """Vários hosts no mesmo IP: fica o único cujo nome é igual ao tipo do
    acesso, ignorando pontuação e caixa ('OLT-ITAMARAJU-06-08-14' ×
    'OLT ITAMARAJU 06 08 14'). Nada de semelhança aproximada — 'SW.HU.STMR' ×
    'SW.HW.STMR' continua ambíguo e vai para a escolha manual."""
    alvo = _nome_normalizado(acesso.tipo)
    iguais = [h for h in achados
              if alvo and alvo in (_nome_normalizado(h['name']), _nome_normalizado(h.get('host')))]
    return iguais if len(iguais) == 1 else achados


def tem_zabbix(cliente_id) -> bool:
    """Há alguma configuração plausível de Zabbix (sem tocar na rede)."""
    from .agent_zabbix import _candidatos
    return bool(_candidatos(cliente_id))


def _config(cliente_id):
    """Config pronta (com túnel). Falha recente fica em cache por _TTL_INDISP
    para a aba não segurar um worker do gunicorn tentando todo candidato de
    novo a cada abertura."""
    from .agent_zabbix import resolver_config

    erro = cache.get(_ck('indisp', cliente_id))
    if erro:
        raise ZabbixIndisponivel(erro)
    try:
        return resolver_config(cliente_id)
    except Exception as exc:
        msg = str(exc)
        cache.set(_ck('indisp', cliente_id), msg, _TTL_INDISP)
        raise ZabbixIndisponivel(msg) from exc


def hosts_do_cliente(cliente_id, usar_cache=True) -> list:
    ck = _ck('hosts', cliente_id)
    if usar_cache:
        hosts = cache.get(ck)
        if hosts is not None:
            return hosts
    hosts = services.hosts_para_vinculo(_config(cliente_id))
    cache.set(ck, hosts, _TTL_HOSTS)
    return hosts


def sincronizar(cliente_id) -> dict:
    """Refaz os vínculos automáticos do cliente. Devolve um resumo."""
    from clientes.models import Acesso

    hosts   = hosts_do_cliente(cliente_id, usar_cache=False)
    acessos = list(Acesso.objects.filter(cliente_id=cliente_id).only('id', 'tipo', 'host', 'host_ipv6'))
    casados = casar(acessos, hosts)
    por_id  = {h['hostid']: h for h in hosts}

    existentes = {v.acesso_id: v for v in ZabbixHostVinculo.objects.filter(acesso__cliente_id=cliente_id)}

    resumo = {'hosts': len(hosts), 'acessos': len(acessos), 'vinculados': 0,
              'manuais': 0, 'ambiguos': [], 'manuais_sem_host': []}

    for a in acessos:
        v = existentes.get(a.id)

        if v and v.origem == 'manual':
            resumo['manuais'] += 1
            if v.zabbix_hostid:
                h = por_id.get(v.zabbix_hostid)
                if not h:
                    resumo['manuais_sem_host'].append({'acesso_id': a.id, 'tipo': a.tipo})
                elif h['name'] != v.zabbix_nome:
                    v.zabbix_nome = h['name']
                    v.save(update_fields=['zabbix_nome', 'atualizado_em'])
            continue

        achados = casados.get(a.id, [])
        if len(achados) > 1:
            achados = desempatar(a, achados)
        if len(achados) == 1:
            h = achados[0]
            if not v:
                ZabbixHostVinculo.objects.create(acesso=a, zabbix_hostid=h['hostid'],
                                                 zabbix_nome=h['name'], origem='auto')
            elif (v.zabbix_hostid, v.zabbix_nome) != (h['hostid'], h['name']):
                v.zabbix_hostid, v.zabbix_nome = h['hostid'], h['name']
                v.save(update_fields=['zabbix_hostid', 'zabbix_nome', 'atualizado_em'])
            resumo['vinculados'] += 1
            continue

        if len(achados) > 1:
            resumo['ambiguos'].append({
                'acesso_id': a.id, 'tipo': a.tipo,
                'hosts': [{'hostid': h['hostid'], 'name': h['name']} for h in achados],
            })
            # Mantém o automático anterior se ainda for uma das opções
            if v and any(h['hostid'] == v.zabbix_hostid for h in achados):
                resumo['vinculados'] += 1
                continue

        if v:   # automático que não casa mais (IP mudou, host saiu do Zabbix)
            v.delete()

    cache.set(_ck('sync', cliente_id), timezone.now().isoformat(), _TTL_SYNC)
    cache.delete(_ck('status', cliente_id))
    logger.info('[ZBX-VINC] cliente %s: %s hosts, %s/%s acessos vinculados, %s ambíguos',
                cliente_id, resumo['hosts'], resumo['vinculados'], resumo['acessos'],
                len(resumo['ambiguos']))
    return resumo


def definir_manual(acesso, hostid: str) -> ZabbixHostVinculo:
    """Fixa o host escolhido na tela (hostid='' = não monitorado)."""
    nome = ''
    if hostid:
        h = next((h for h in hosts_do_cliente(acesso.cliente_id) if h['hostid'] == hostid), None)
        if not h:
            raise ValueError('Host não encontrado no Zabbix deste cliente.')
        nome = h['name']
    v, _ = ZabbixHostVinculo.objects.update_or_create(
        acesso=acesso,
        defaults={'zabbix_hostid': hostid, 'zabbix_nome': nome, 'origem': 'manual'},
    )
    cache.delete(_ck('status', acesso.cliente_id))
    return v


def voltar_automatico(acesso):
    """Desfaz a escolha manual; o próximo sincronismo decide pelo IP."""
    ZabbixHostVinculo.objects.filter(acesso=acesso).delete()
    cache.delete(_ck('sync', acesso.cliente_id))
    cache.delete(_ck('status', acesso.cliente_id))


def status_cliente(cliente_id) -> dict:
    """
    {acesso_id: {status, hostid, nome, origem}} para os acessos vinculados.
    status ∈ up | problem | down | unknown | missing (host saiu do Zabbix) | ignorado.
    Sincroniza sozinho se o cliente nunca foi sincronizado nas últimas horas.
    """
    ck = _ck('status', cliente_id)
    em_cache = cache.get(ck)
    if em_cache is not None:
        return em_cache

    if not cache.get(_ck('sync', cliente_id)):
        sincronizar(cliente_id)

    vinculos = list(ZabbixHostVinculo.objects.filter(acesso__cliente_id=cliente_id))
    hostids  = sorted({v.zabbix_hostid for v in vinculos if v.zabbix_hostid})
    estados  = services.status_nodes(_config(cliente_id), hostids) if hostids else {}

    resp = {}
    for v in vinculos:
        if v.zabbix_hostid:
            st = estados.get(v.zabbix_hostid, 'missing')
        else:
            st = 'ignorado'
        resp[str(v.acesso_id)] = {'status': st, 'hostid': v.zabbix_hostid,
                                  'nome': v.zabbix_nome, 'origem': v.origem}
    cache.set(ck, resp, _TTL_STATUS)
    return resp
