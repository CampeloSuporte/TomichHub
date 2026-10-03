"""
monitoramento/zabbix_cadastro.py

Cadastra no Zabbix do cliente um acesso que não casou com nenhum host pelo IP
(botão "Cadastrar no Zabbix" do card). Fluxo em dois passos:

1. `previa(acesso)` monta a sugestão — nada é escrito no Zabbix.
2. `criar(acesso, dados, user)` faz o `host.create` com o que a tela confirmou
   e grava o vínculo como manual.

De onde vem cada campo da sugestão, em ordem de preferência:

- **Templates, grupos, SNMP e proxy** copiados dos hosts que o cliente JÁ tem
  no Zabbix vinculados a acessos do mesmo modelo; depois, do mesmo fabricante.
  Cada cliente tem seu padrão (ex.: MikroTik = template oficial do modelo +
  "Template Sinal SFP Mikrotik - BEE"), e um conjunto de templates que já roda
  num host vizinho não tem conflito de chave entre si.
- Sem vizinho do mesmo modelo: template oficial do modelo ("MikroTik
  CRS309-1G-8S+IN by SNMP"), lendo o modelo do backup (`# model = ...` no
  RouterOS), mais os complementos que a maioria dos vizinhos do fabricante usa.
- Sem nada disso: template genérico do fabricante, e por fim o genérico SNMP.
- **Community**: a do backup do equipamento quando dá para ler (Huawei guarda
  cifrada); senão a dos vizinhos.

A API muda entre versões (5.0 → 7.4): grupos via `selectHostGroups` a partir da
6.2, proxy via `monitored_by`/`proxyid` a partir da 7.0 (antes `proxy_hostid`),
nome do proxy em `name` (7.0+) ou `host`.
"""
import logging
import os
import re
from collections import Counter

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from . import services
from .models import ZabbixHostVinculo

logger = logging.getLogger(__name__)

_TTL_VIZINHOS = 600
_TTL_OPCOES   = 600


def _norm(texto: str) -> str:
    return re.sub(r'[^0-9a-z]', '', (texto or '').lower())


# ──────────────────────────────────────────────────────────────
# Backup do equipamento: modelo, fabricante e community SNMP
# ──────────────────────────────────────────────────────────────

def _conteudo_backup(acesso, limite=2_000_000) -> str:
    from clientes.models import BackupLog

    ultimo = (BackupLog.objects.filter(acesso=acesso, status='SUCESSO')
              .exclude(arquivo_path='').exclude(arquivo_path__isnull=True)
              .order_by('-data_backup').first())
    if not ultimo:
        return ''
    caminho = os.path.join(getattr(settings, 'MEDIA_ROOT', '/opt/crm/media'), ultimo.arquivo_path)
    try:
        with open(caminho, errors='replace') as f:
            return f.read(limite)
    except OSError:
        return ''


def fabricante_do_backup(conteudo: str) -> str:
    if re.search(r'^#\s+model\s*=|^/system identity|RouterOS', conteudo, re.M):
        return 'Mikrotik'
    if re.search(r'\bVRP\b|HUAWEI|^\s*sysname\s', conteudo, re.M):
        return 'Huawei'
    if re.search(r'Cisco IOS|^hostname\s.*\n.*^!\s*$', conteudo, re.M | re.I):
        return 'Cisco'
    if re.search(r'DmOS|Datacom', conteudo, re.I):
        return 'Datacom'
    if re.search(r'^set\s+system\s+host-name|JUNOS', conteudo, re.M):
        return 'Juniper'
    return ''


def _linhas_routeros(conteudo: str, secao: str) -> list:
    """Comandos de uma seção do export do RouterOS nos dois formatos:
    terse ("/snmp community set ... name=X", um por linha — o dos backups do
    CRM) e em bloco ("/snmp community" seguido de "set ..."/"add ...")."""
    linhas = [m.group(1) for m in re.finditer(rf'^{re.escape(secao)} ((?:set|add)\b.*)$', conteudo, re.M)]
    bloco = re.search(rf'^{re.escape(secao)}\s*\n((?:(?!^/).*\n?)*)', conteudo, re.M)
    if bloco:
        linhas += [l.strip() for l in bloco.group(1).splitlines() if re.match(r'\s*(set|add)\b', l)]
    return linhas


def snmp_do_backup(conteudo: str) -> dict:
    """{'communities': [...], 'restricoes': {community: 'addresses'},
    'cifrada': bool, 'desabilitado': bool|None}. `desabilitado=None` = não
    deu para saber pelo backup."""
    comms, restricoes, cifrada, desabilitado = [], {}, False, None

    # MikroTik RouterOS
    for l in _linhas_routeros(conteudo, '/snmp community'):
        if re.search(r'\bdisabled=yes', l):
            continue
        nome = re.search(r'\bname=("[^"]+"|\S+)', l)
        if not nome:
            continue
        c = nome.group(1).strip('"')
        comms.append(c)
        end = re.search(r'\baddresses=(\S+)', l)
        if end and end.group(1) not in ('0.0.0.0/0', '::/0'):
            restricoes[c] = end.group(1)
    if re.search(r'^#\s+model\s*=|^/system ', conteudo, re.M):
        snmp = _linhas_routeros(conteudo, '/snmp')
        desabilitado = not any(re.search(r'\benabled=yes', l) for l in snmp)

    # Huawei VRP: "snmp-agent community read [cipher] X"
    for m in re.finditer(r'^\s*snmp-agent community (read|write)\s+(cipher\s+)?(\S+)', conteudo, re.M):
        if m.group(2):
            cifrada = True
        elif m.group(1) == 'read':
            comms.append(m.group(3))

    # Cisco / Datacom / genéricos
    for m in re.finditer(r'^\s*snmp(?:-server)?\s+community\s+(\S+)', conteudo, re.M):
        if m.group(1) not in ('cipher', 'read', 'write'):
            comms.append(m.group(1).strip('"'))

    return {'communities': list(dict.fromkeys(c for c in comms if c)), 'restricoes': restricoes,
            'cifrada': cifrada, 'desabilitado': desabilitado}


# ──────────────────────────────────────────────────────────────
# Zabbix: opções e vizinhos
# ──────────────────────────────────────────────────────────────

def _ver(cfg):
    return services._get_zabbix_version(cfg.url)


def _sel_grupos(cfg):
    return 'selectHostGroups' if _ver(cfg) >= (6, 2, 0) else 'selectGroups'


def opcoes(cfg, cliente_id) -> dict:
    """Templates, grupos e proxies do Zabbix do cliente (para a tela)."""
    ck = f'zbx_cad_opcoes:{cliente_id}'
    em_cache = cache.get(ck)
    if em_cache is not None:
        return em_cache
    auth = services._get_auth_token(cfg)
    templates = services._rpc(cfg.url, 'template.get', {'output': ['templateid', 'name']}, auth, config=cfg)
    grupos = services._rpc(cfg.url, 'hostgroup.get', {'output': ['groupid', 'name']}, auth, config=cfg)
    campo_proxy = 'name' if _ver(cfg) >= (7, 0, 0) else 'host'
    proxies = services._rpc(cfg.url, 'proxy.get', {'output': ['proxyid', campo_proxy]}, auth, config=cfg)
    macros = services._rpc(cfg.url, 'usermacro.get', {'globalmacro': True, 'output': ['macro', 'value', 'type']},
                           auth, config=cfg)
    dados = {
        # Macro secreta (type 1) não devolve valor: fica de fora, valor desconhecido
        'macros': {m['macro']: m.get('value', '') for m in macros if str(m.get('type', '0')) == '0'},
        'templates': sorted(({'id': t['templateid'], 'nome': t['name']} for t in templates), key=lambda t: t['nome'].lower()),
        'grupos': sorted(({'id': g['groupid'], 'nome': g['name']} for g in grupos), key=lambda g: g['nome'].lower()),
        'proxies': sorted(({'id': p['proxyid'], 'nome': p.get(campo_proxy, '')} for p in proxies), key=lambda p: p['nome'].lower()),
    }
    cache.set(ck, dados, _TTL_OPCOES)
    return dados


def vizinhos(cfg, cliente_id) -> list:
    """Hosts do Zabbix já vinculados a acessos do cliente, com o que dá para
    copiar deles: templates, grupos, interface SNMP e proxy."""
    ck = f'zbx_cad_vizinhos:{cliente_id}'
    em_cache = cache.get(ck)
    if em_cache is not None:
        return em_cache

    vincs = (ZabbixHostVinculo.objects.filter(acesso__cliente_id=cliente_id).exclude(zabbix_hostid='')
             .select_related('acesso__modelo'))
    por_host = {}
    for v in vincs:
        por_host.setdefault(v.zabbix_hostid, v.acesso)
    if not por_host:
        return []

    auth = services._get_auth_token(cfg)
    sel_g = _sel_grupos(cfg)
    campos_proxy = ['proxyid', 'monitored_by'] if _ver(cfg) >= (7, 0, 0) else ['proxy_hostid']
    hosts = services._rpc(cfg.url, 'host.get', {
        'hostids': list(por_host),
        'output': ['hostid', 'name'] + campos_proxy,
        'selectParentTemplates': ['templateid'],
        sel_g: ['groupid'],
        'selectInterfaces': ['type', 'details'],
    }, auth, config=cfg)

    saida = []
    for h in hosts:
        a = por_host.get(h['hostid'])
        snmp = next((i.get('details') or {} for i in h.get('interfaces', []) if str(i.get('type')) == '2'), None)
        if _ver(cfg) >= (7, 0, 0):
            proxy = h.get('proxyid') if str(h.get('monitored_by')) == '1' else ''
        else:
            proxy = h.get('proxy_hostid') if h.get('proxy_hostid') not in (None, '0') else ''
        saida.append({
            'hostid': h['hostid'], 'nome': h.get('name', ''),
            'modelo_id': a.modelo_id if a else None,
            'funcao_id': a.funcao_id if a else None,
            'fabricante': _norm(a.modelo.fabricante) if a and a.modelo else '',
            'templates': sorted(t['templateid'] for t in h.get('parentTemplates', [])),
            'grupos': sorted(g['groupid'] for g in h.get('hostgroups', h.get('groups', []))),
            'snmp': snmp, 'proxy': proxy or '',
        })
    cache.set(ck, saida, _TTL_VIZINHOS)
    return saida


# ──────────────────────────────────────────────────────────────
# Sugestão
# ──────────────────────────────────────────────────────────────

_GENERICOS = ['generic by snmp', 'network generic device by snmp',
              'template net network generic device snmp', 'icmp ping', 'template module icmp ping']


def _mais_comum(listas):
    listas = [tuple(x) for x in listas if x]
    if not listas:
        return []
    return list(Counter(listas).most_common(1)[0][0])


def template_do_modelo(modelo: str, fabricante: str, templates: list):
    """Template oficial do modelo (ex.: 'MikroTik CCR2004-1G-12S+2XS by SNMP')."""
    m = _norm(modelo)
    if len(m) < 5:
        return None
    candidatos = [t for t in templates if m in _norm(t['nome'])
                  and (not fabricante or _norm(fabricante)[:5] in _norm(t['nome']))]
    # O mais curto é o mais específico ("... by SNMP" e não "... by SNMP - cópia")
    return min(candidatos, key=lambda t: len(t['nome'])) if candidatos else None


def template_do_fabricante(fabricante: str, templates: list):
    f = _norm(fabricante)
    if len(f) < 3:
        return None
    candidatos = [t for t in templates if f in _norm(t['nome']) and 'snmp' in t['nome'].lower()]
    oficiais = [t for t in candidatos if t['nome'].lower().endswith('by snmp')]
    lista = oficiais or candidatos
    return min(lista, key=lambda t: len(t['nome'])) if lista else None


def _eh_oficial_de_modelo(nome: str, fabricante: str) -> bool:
    """'MikroTik CRS309-1G-8S+IN by SNMP' sim; 'Mikrotik by SNMP' (genérico) não."""
    m = re.match(r'^(\S+)\s+(.+?)\s+by snmp$', nome.strip(), re.I)
    return bool(m and _norm(fabricante)[:5] and _norm(m.group(1)).startswith(_norm(fabricante)[:5]))


def sugerir(acesso, modelo_str: str, fabricante: str, viz: list, ops: dict) -> dict:
    """Sugestão pura (sem rede): templates, grupos, SNMP e proxy + de onde veio.

    Ordem dos vizinhos: mesmo modelo → mesmo fabricante e mesma função (BRAS
    com BRAS, switch com switch) → mesmo fabricante."""
    nomes_t = {t['id']: t['nome'] for t in ops['templates']}
    f = _norm(fabricante)
    mesmo_modelo = [v for v in viz if acesso.modelo_id and v['modelo_id'] == acesso.modelo_id]
    mesmo_fab = [v for v in viz if f and v['fabricante'] and (v['fabricante'] in f or f in v['fabricante'])]
    fab_funcao = [v for v in mesmo_fab if acesso.funcao_id and v.get('funcao_id') == acesso.funcao_id]
    oficial = template_do_modelo(modelo_str, fabricante, ops['templates'])

    templates, base = [], ''
    comum = lambda grupo: _mais_comum(v['templates'] for v in grupo)
    if mesmo_modelo and comum(mesmo_modelo):
        templates = comum(mesmo_modelo)
        base = f'igual a {len(mesmo_modelo)} host(s) do mesmo modelo já no Zabbix'
    elif fab_funcao and comum(fab_funcao):
        templates = comum(fab_funcao)
        base = f'igual a {len(fab_funcao)} host(s) {fabricante} da mesma função já no Zabbix'
    elif oficial:
        templates = [oficial['id']]
        # Complementos que a maioria dos vizinhos do fabricante usa (ex.:
        # sinal SFP), fora os oficiais "by SNMP" que dariam chave duplicada
        if mesmo_fab:
            uso = Counter(t for v in mesmo_fab for t in v['templates'])
            for tid, n in uso.items():
                nome = nomes_t.get(tid, '')
                if n * 2 >= len(mesmo_fab) and 'by snmp' not in nome.lower() and tid not in templates:
                    templates.append(tid)
        base = f'template oficial do modelo {modelo_str}'
        if len(templates) > 1:
            base += ' + complementos usados nos outros equipamentos do fabricante'
    elif (t_fab := template_do_fabricante(fabricante, ops['templates'])) and t_fab['nome'].lower().endswith('by snmp'):
        # Vizinhos do fabricante mas de outra função (switch × BRAS) trariam
        # templates específicos do outro equipamento: o oficial genérico é mais seguro
        templates, base = [t_fab['id']], f'template oficial genérico {fabricante}'
    elif mesmo_fab and comum(mesmo_fab):
        templates = comum(mesmo_fab)
        base = f'igual aos {len(mesmo_fab)} host(s) {fabricante} já no Zabbix (outra função)'
    else:
        if t_fab:
            templates, base = [t_fab['id']], f'template genérico {fabricante}'
        else:
            por_nome = {t['nome'].lower(): t['id'] for t in ops['templates']}
            g = next((por_nome[n] for n in _GENERICOS if n in por_nome), None)
            if g:
                templates, base = [g], 'template genérico SNMP (fabricante sem template conhecido)'

    # Vizinho usa o template oficial de OUTRO modelo (ex.: RB3011 num RB750Gr3):
    # troca pelo do modelo deste equipamento
    if oficial and oficial['id'] not in templates:
        trocados = [t for t in templates if _eh_oficial_de_modelo(nomes_t.get(t, ''), fabricante)]
        if trocados:
            templates = [oficial['id'] if t == trocados[0] else t for t in templates if t == trocados[0] or t not in trocados]
            base += f' (com o template oficial do {modelo_str})'

    ordem = [g for g in (mesmo_modelo, fab_funcao, mesmo_fab) if g]
    grupos = next((_mais_comum(v['grupos'] for v in g) for g in ordem), [])
    if not grupos and len(f) >= 3:
        # Sem vizinho do fabricante: grupo com o nome do fabricante, se houver
        grupos = [g['id'] for g in ops['grupos'] if f in _norm(g['nome'])][:1]
    ordem_rede = ordem + [viz]   # proxy e community valem para a rede inteira do cliente
    proxy = next((Counter(v['proxy'] for v in g).most_common(1)[0][0] for g in ordem_rede if g), '')
    snmp_viz = next(([v for v in g if v['snmp']] for g in ordem_rede if any(v['snmp'] for v in g)), [])
    snmp_ref = None
    if snmp_viz:
        chave = Counter((str(v['snmp'].get('version')), v['snmp'].get('community', '')) for v in snmp_viz).most_common(1)[0][0]
        snmp_ref = next(v for v in snmp_viz if (str(v['snmp'].get('version')), v['snmp'].get('community', '')) == chave)

    return {'templates': [t for t in templates if t in nomes_t], 'base_templates': base,
            'grupos': grupos, 'proxy': proxy, 'snmp_ref': snmp_ref}


def _nome_tecnico(texto: str) -> str:
    """Zabbix aceita só letras, números, espaço, ponto, hífen e sublinhado."""
    return re.sub(r'[^0-9A-Za-z ._\-]', '_', (texto or '').strip())[:128] or 'host'


def previa(acesso) -> dict:
    from .vinculo_zabbix import _config, ip_do_acesso

    cfg = _config(acesso.cliente_id)
    ops = opcoes(cfg, acesso.cliente_id)
    viz = vizinhos(cfg, acesso.cliente_id)

    conteudo = _conteudo_backup(acesso)
    from clientes.tasks import _extrair_modelo_do_backup
    modelo_str = (_extrair_modelo_do_backup(conteudo[:8000]) if conteudo else None) or (acesso.modelo.nome if acesso.modelo else '')
    fabricante = (acesso.modelo.fabricante if acesso.modelo else '') or fabricante_do_backup(conteudo)
    snmp_bkp = snmp_do_backup(conteudo) if conteudo else {'communities': [], 'restricoes': {}, 'cifrada': False, 'desabilitado': None}

    s = sugerir(acesso, modelo_str, fabricante, viz, ops)
    avisos = []

    # Versão/community: SNMP dos vizinhos, community do backup quando legível
    versao, community, v3_de = '2', '', None
    ref = s['snmp_ref']
    if ref:
        versao = str(ref['snmp'].get('version') or '2')
        community = ref['snmp'].get('community', '')
        if versao == '3':
            v3_de = {'hostid': ref['hostid'], 'nome': ref['nome']}
    if versao != '3' and snmp_bkp['communities']:
        # Macro global ({$SNMP_COMMUNITY}) vale se o valor dela for uma das
        # communities do equipamento; senão o equipamento não responderia
        valor = ops.get('macros', {}).get(community, community) if community.startswith('{$') else community
        if valor not in snmp_bkp['communities']:
            if community.startswith('{$') and community in ops.get('macros', {}):
                avisos.append(f'A macro {community} do Zabbix vale "{valor}", mas o equipamento usa '
                              f'"{snmp_bkp["communities"][0]}" (backup): usei a do equipamento.')
            community = snmp_bkp['communities'][0]
    if not community and versao != '3':
        community = '{$SNMP_COMMUNITY}'
        avisos.append('Não achei a community nem no backup nem em outro host: ficou a macro {$SNMP_COMMUNITY}.')
    if snmp_bkp['cifrada'] and not snmp_bkp['communities']:
        avisos.append('A community no backup está cifrada; usei a dos outros hosts do cliente.')
    if versao != '3' and community in snmp_bkp.get('restricoes', {}):
        avisos.append(f'No equipamento, a community "{community}" só responde para {snmp_bkp["restricoes"][community]} '
                      '— o IP do Zabbix (ou do proxy) precisa estar nessa lista.')
    if snmp_bkp['desabilitado']:
        avisos.append('Pelo último backup, o SNMP está desabilitado no equipamento — o Zabbix não vai coletar até habilitar.')
    if not conteudo:
        avisos.append('Acesso sem backup: modelo e community não puderam ser conferidos no equipamento.')
    if not s['templates']:
        avisos.append('Nenhum template sugerido: escolha na lista.')
    if not s['grupos']:
        avisos.append('Nenhum grupo sugerido: escolha na lista.')

    ip = ip_do_acesso(acesso.host)
    mesmo_ip = []
    if ip:
        from .vinculo_zabbix import hosts_do_cliente
        mesmo_ip = [{'hostid': h['hostid'], 'nome': h['name']} for h in hosts_do_cliente(acesso.cliente_id) if ip in h['ips']]
        if mesmo_ip:
            avisos.append('Já existe host com este IP no Zabbix — talvez seja só vincular.')

    return {
        'acesso': {'id': acesso.id, 'tipo': acesso.tipo},
        'modelo': modelo_str or '', 'fabricante': fabricante or '',
        'sugestao': {
            'host': _nome_tecnico(acesso.tipo), 'nome': acesso.tipo[:128], 'ip': ip, 'porta': '161',
            'snmp_versao': versao, 'community': community if versao != '3' else '',
            'snmp_v3_de': v3_de, 'templates': s['templates'], 'grupos': s['grupos'], 'proxy': s['proxy'],
        },
        'base_templates': s['base_templates'],
        'communities_backup': snmp_bkp['communities'],
        'mesmo_ip': mesmo_ip, 'avisos': avisos,
        'opcoes': {k: v for k, v in ops.items() if k != 'macros'},
    }


# ──────────────────────────────────────────────────────────────
# Criação
# ──────────────────────────────────────────────────────────────

def montar_parametros(dados: dict, versao_zbx: tuple, detalhes_v3: dict = None) -> dict:
    """Parâmetros do host.create conforme a versão do Zabbix (puro, testável)."""
    if str(dados.get('snmp_versao')) == '3':
        if not detalhes_v3:
            raise ValueError('SNMPv3 só pode ser copiado de um host existente.')
        detalhes = dict(detalhes_v3)
    else:
        community = (dados.get('community') or '').strip()
        if not community:
            raise ValueError('Informe a community SNMP.')
        detalhes = {'version': int(dados.get('snmp_versao') or 2), 'bulk': 1, 'community': community}

    ip = (dados.get('ip') or '').strip()
    import ipaddress
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise ValueError('IP inválido.')
    porta = str(dados.get('porta') or '161').strip()
    if not porta.isdigit() or not (0 < int(porta) < 65536):
        raise ValueError('Porta inválida.')
    if not dados.get('grupos'):
        raise ValueError('Escolha pelo menos um grupo.')

    params = {
        'host': _nome_tecnico(dados.get('host')),
        'name': (dados.get('nome') or dados.get('host') or '')[:128],
        'interfaces': [{'type': 2, 'main': 1, 'useip': 1, 'ip': ip, 'dns': '', 'port': porta, 'details': detalhes}],
        'groups': [{'groupid': str(g)} for g in dados['grupos']],
        'templates': [{'templateid': str(t)} for t in dados.get('templates') or []],
        'tags': [{'tag': 'crm_acesso_id', 'value': str(dados['acesso_id'])}],
    }
    proxy = str(dados.get('proxy') or '')
    if proxy:
        if versao_zbx >= (7, 0, 0):
            params.update(monitored_by=1, proxyid=proxy)
        else:
            params['proxy_hostid'] = proxy
    return params


def criar(acesso, dados: dict, user=None) -> dict:
    from .vinculo_zabbix import _ck, _config

    cfg = _config(acesso.cliente_id)
    auth = services._get_auth_token(cfg)
    dados = dict(dados, acesso_id=acesso.id)

    detalhes_v3 = None
    if str(dados.get('snmp_versao')) == '3':
        # Credenciais v3 nunca passam pelo navegador: relidas do host de referência
        ref = next((v for v in vizinhos(cfg, acesso.cliente_id) if v['hostid'] == str(dados.get('snmp_v3_de'))), None)
        detalhes_v3 = ref['snmp'] if ref and str((ref['snmp'] or {}).get('version')) == '3' else None

    params = montar_parametros(dados, _ver(cfg), detalhes_v3)

    existe = services._rpc(cfg.url, 'host.get', {'filter': {'host': [params['host']]}, 'output': ['hostid']},
                           auth, config=cfg)
    if existe:
        raise ValueError(f'Já existe um host com o nome técnico "{params["host"]}" no Zabbix.')

    try:
        r = services._rpc(cfg.url, 'host.create', params, auth, config=cfg)
    except Exception as exc:
        msg = str(exc)
        if 'permission' in msg.lower() or 'no permissions' in msg.lower():
            msg = ('O usuário da API do Zabbix deste cliente não tem permissão de escrita. '
                   'Dê acesso de leitura e escrita ao grupo de hosts escolhido. Detalhe: ' + msg)
        raise ValueError(msg) from exc

    hostid = str(r['hostids'][0])
    ZabbixHostVinculo.objects.update_or_create(
        acesso=acesso,
        defaults={'zabbix_hostid': hostid, 'zabbix_nome': params['name'], 'origem': 'manual',
                  'criado_no_zabbix_por': user if getattr(user, 'is_authenticated', False) else None,
                  'criado_no_zabbix_em': timezone.now()},
    )
    for nome in ('hosts', 'status', 'metricas'):
        cache.delete(_ck(nome, acesso.cliente_id))
    cache.delete(f'zbx_cad_vizinhos:{acesso.cliente_id}')
    logger.info('[ZBX-CAD] acesso %s (%s) criado no Zabbix do cliente %s como host %s por %s; templates=%s',
                acesso.id, acesso.tipo, acesso.cliente_id, hostid, getattr(user, 'username', '?'),
                [t['templateid'] for t in params['templates']])
    return {'hostid': hostid, 'nome': params['name']}
