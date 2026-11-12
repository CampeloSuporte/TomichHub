"""
Provisionamento de ONU em OLT Datacom DmOS (DM4610 / DM4615) — a contraparte
Datacom de `olt_provisionamento.py` (Huawei). Endpoints em
`onu_prov_datacom_views.py`, aba "ONU Datacom" do painel ⚡ Scripts.

O DmOS é outra CLI (estilo ConfD, com `commit`), e muda três coisas em relação
ao Huawei:

- **O ID da ONU e o do service-port são escolhidos por nós.** Não existe
  "ont add sem ID". E entrar em `onu 5` ou `service-port 3181` que já existem
  EDITA o cadastro de um assinante. Por isso cada ID livre sai do backup e é
  conferido ao vivo antes de gravar: `show running-config ... onu N` precisa
  responder `syntax error: element does not exist` (texto visto ao vivo na
  OLT03 do G5 em 12/11/2026).
- **Cada ONU é um `commit` só.** Ou entra tudo (ONU + service-port), ou nada
  entra. Não há o status "parcial" do Huawei. Uma recusa no meio faz `abort`
  (sai do config descartando o que não foi gravado).
- **O backup é hierárquico** (`show running-config`): `interface gpon 1/1/1`
  → ` onu 5` → `  serial-number ...`, com `!` fechando cada bloco.

Formatos conferidos nos backups das 5 OLTs Datacom do G5 e no guia rápido
DM4610 (ONU Bridge e ONU Router, rev 1.4).
"""
import re
import time
from collections import Counter

MAX_ONUS_LOTE = 64
MAX_ONU_ID = 127            # DM4610/DM4615: 128 ONUs por PON (0..127)
MAX_SERVICE_PORT = 65535
MODOS = {
    'bridge': 'Bridge (ethernet 1 com native VLAN)',
    'router': 'Router (veip 1)',
}
SERVICE_VLAN_TIPOS = ('n:1', 'tls', '1:1')
BW_TIPOS = {
    'type-1': 'fixed-bw',
    'type-4': 'max-bw',
}


class ProvisionamentoInvalido(Exception):
    """Pedido recusado antes de conectar: a mensagem vai para o operador."""


_PON_RE = re.compile(r'^\d+/\d+/\d+$')
_SN_RE = re.compile(r'^[A-Z0-9]{4}[0-9A-F]{8}$')
_NOME_RE = re.compile(r'^[A-Za-z0-9_.\-]{1,32}$')
_NOME_ONU_RE = re.compile(r'[^A-Za-z0-9_.\-@]+')


# ─── Backup (show running-config) ────────────────────────────────────────────

_IF_GPON_RE = re.compile(r'^interface gpon (\d+/\d+/\d+)\s*$')
_ONU_RE = re.compile(r'^ onu (\d+)\s*$')
_LINE_RE = re.compile(r'^profile gpon line-profile (\S+)\s*$')
_BW_RE = re.compile(r'^profile gpon bandwidth-profile (\S+)\s*$')
_ONUPROF_RE = re.compile(r'^profile gpon onu-profile (\S+)\s*$')
_SRVPROF_RE = re.compile(r'^profile gpon service-profile (\S+)\s*$')
_SVLAN_RE = re.compile(r'^service vlan (\d+)\s*$')
_SP_RE = re.compile(r'^service-port (\d+)\s*(.*)$')
_SP_CORPO_RE = re.compile(
    r'gpon (\d+/\d+/\d+) onu (\d+) gem (\d+) match vlan vlan-id (\d+) '
    r'action vlan (replace|add) vlan-id (\d+)')
_MAP_ALVO_RE = re.compile(r'^(ethernet \S+|veip \d+|iphost)\s+vlan (\S+)')


def eh_olt_datacom(conteudo):
    """OLT Datacom de verdade: tem ramo GPON e profile gpon no running-config
    (um switch DM4370 também é DmOS, mas não tem nada disso)."""
    texto = conteudo or ''
    return bool(re.search(r'^interface gpon \d+/\d+/\d+\s*$', texto, re.M)
                and re.search(r'^profile gpon ', texto, re.M))


def _blocos(conteudo):
    """Quebra o running-config em blocos de nível 0: (cabeçalho, [linhas])."""
    bloco = None
    for bruta in (conteudo or '').splitlines():
        linha = bruta.rstrip('\r').rstrip()
        if not linha:
            continue
        if not linha.startswith(' '):
            if bloco:
                yield bloco
            bloco = None if linha == '!' else (linha, [])
            continue
        if bloco:
            bloco[1].append(linha)
    if bloco:
        yield bloco


def _parse_onus(linhas):
    onus, atual = {}, None
    for l in linhas:
        nivel = len(l) - len(l.lstrip(' '))
        s = l.strip()
        if nivel == 1:
            m = _ONU_RE.match(l)
            atual = None
            if m:
                atual = {'id': int(m.group(1)), 'nome': '', 'sn': '', 'service': '', 'line': '',
                         'modo': 'bridge', 'native_vlan': None}
                onus[atual['id']] = atual
            continue
        if atual is None:
            continue
        if nivel == 2:
            if s.startswith('name '):
                atual['nome'] = s[5:].strip()
            elif s.startswith('serial-number '):
                atual['sn'] = s.split()[1].upper()
            elif s.startswith('service-profile ') or s.startswith('line-profile '):
                p = s.split()
                for i in range(0, len(p) - 1, 2):
                    if p[i] == 'service-profile':
                        atual['service'] = p[i + 1]
                    elif p[i] == 'line-profile':
                        atual['line'] = p[i + 1]
            elif s.startswith('veip '):
                atual['modo'] = 'router'
        else:
            m = re.match(r'native vlan vlan-id (\d+)', s)
            if m and atual['native_vlan'] is None:
                atual['native_vlan'] = int(m.group(1))
    return onus


def _parse_line(nome, linhas):
    tconts, gems, atual_gem = {}, [], None
    for l in linhas:
        s = l.strip()
        nivel = len(l) - len(l.lstrip(' '))
        m = re.match(r'tcont (\d+) bandwidth-profile (\S+)', s)
        if nivel == 1 and m:
            tconts[int(m.group(1))] = m.group(2)
            continue
        m = re.match(r'gem (\d+)$', s)
        if nivel == 1 and m:
            atual_gem = {'gem': int(m.group(1)), 'tcont': None, 'maps': []}
            gems.append(atual_gem)
            continue
        if atual_gem is None:
            continue
        m = re.match(r'tcont (\d+)', s)
        if nivel == 2 and m:
            atual_gem['tcont'] = int(m.group(1))
            continue
        m = re.match(r'map (\S+)$', s)
        if nivel == 2 and m:
            atual_gem['maps'].append({'nome': m.group(1), 'alvo': '', 'vlan': None})
            continue
        m = _MAP_ALVO_RE.match(s)
        if nivel == 3 and m and atual_gem['maps']:
            mp = atual_gem['maps'][-1]
            mp['alvo'] = m.group(1)
            mp['vlan'] = int(m.group(2)) if m.group(2).isdigit() else None
    alvos = [mp['alvo'] for g in gems for mp in g['maps']]
    modo = 'router' if any(a.startswith('veip') for a in alvos) else 'bridge'
    vlans = sorted({mp['vlan'] for g in gems for mp in g['maps'] if mp['vlan']})
    return {'nome': nome, 'tconts': tconts, 'gems': gems, 'modo': modo, 'vlans': vlans,
            'texto': [f'profile gpon line-profile {nome}'] + linhas}


def parse_backup(conteudo):
    """Tudo que o provisionamento precisa, lido do `show running-config`."""
    r = {'hostname': '', 'pons': {}, 'lines': {}, 'bandwidths': {}, 'onu_profiles': {},
         'service_profiles': {}, 'service_vlans': {}, 'vlans': {}, 'service_ports': {},
         'sns': {}}
    for cab, linhas in _blocos(conteudo):
        if cab.startswith('hostname '):
            r['hostname'] = cab.split(None, 1)[1].strip()
            continue
        m = _IF_GPON_RE.match(cab)
        if m:
            pon = m.group(1)
            r['pons'][pon] = {'onus': _parse_onus(linhas),
                              'shutdown': ' shutdown' in linhas}
            continue
        m = _LINE_RE.match(cab)
        if m:
            r['lines'][m.group(1)] = _parse_line(m.group(1), linhas)
            continue
        m = _BW_RE.match(cab)
        if m:
            r['bandwidths'][m.group(1)] = {'nome': m.group(1), 'texto': [cab] + linhas,
                                          'traffic': ' '.join(l.strip() for l in linhas)}
            continue
        m = _ONUPROF_RE.match(cab)
        if m:
            r['onu_profiles'][m.group(1)] = {'nome': m.group(1), 'texto': [cab] + linhas,
                                            'veip': any(l.strip().startswith('veip') for l in linhas)}
            continue
        m = _SRVPROF_RE.match(cab)
        if m:
            op = next((l.split()[1] for l in linhas if l.strip().startswith('onu-profile ')), '')
            r['service_profiles'][m.group(1)] = {'nome': m.group(1), 'onu_profile': op,
                                                'texto': [cab] + linhas}
            continue
        m = _SVLAN_RE.match(cab)
        if m:
            tipo = next((l.split()[1] for l in linhas if l.strip().startswith('type ')), '')
            r['service_vlans'][int(m.group(1))] = tipo
            continue
        m = _SP_RE.match(cab)
        if m:
            corpo = (m.group(2) + ' ' + ' '.join(l.strip() for l in linhas)).strip()
            mc = _SP_CORPO_RE.search(corpo)
            sp = {'id': int(m.group(1))}
            if mc:
                sp.update(pon=mc.group(1), onu=int(mc.group(2)), gem=int(mc.group(3)),
                          vlan=int(mc.group(4)), acao=mc.group(5), vlan_olt=int(mc.group(6)))
            r['service_ports'][sp['id']] = sp
            continue
        if cab == 'dot1q':
            atual = None
            for l in linhas:
                s = l.strip()
                mv = re.match(r'vlan (\d+)$', s)
                if l.startswith(' vlan ') and mv:
                    atual = {'id': int(mv.group(1)), 'nome': '', 'interfaces': []}
                    r['vlans'][atual['id']] = atual
                elif atual and l.startswith('  name '):
                    atual['nome'] = s[5:].strip()
                elif atual and l.startswith('  interface '):
                    atual['interfaces'].append(s.split()[1])
    for pon, dados in r['pons'].items():
        for o in dados['onus'].values():
            if o['sn']:
                r['sns'][o['sn']] = f'{pon} onu {o["id"]}'
    # Service-ports de cada ONU, para a sugestão (VLAN e GEM reais).
    for sp in r['service_ports'].values():
        o = r['pons'].get(sp.get('pon'), {}).get('onus', {}).get(sp.get('onu'))
        if o is not None:
            o.setdefault('sps', []).append(sp)
    return r


def uplinks_do_backup(backup):
    """Interfaces que mais levam VLANs no `dot1q` (a uplink), da maior para a
    menor. No G5 é a ten-gigabit-ethernet-1/1/1."""
    c = Counter(i for v in backup['vlans'].values() for i in v['interfaces'])
    return [{'interface': i, 'vlans': n} for i, n in c.most_common()]


def resumo_inventario(backup):
    """Contagem de ONUs por PON, para a tela."""
    return [{'pon': p, 'onus': len(d['onus'])}
            for p, d in sorted(backup['pons'].items(), key=lambda x: [int(n) for n in x[0].split('/')])]


# ─── Discovered (ONUs pendentes) ─────────────────────────────────────────────

_DISC_RE = re.compile(r'^\s*(\d+/\d+/\d+)\s+([A-Za-z0-9]{4}[0-9A-Fa-f]{8})\b(.*)$')


def parse_discovered(texto):
    """`show interface gpon discovered-onus`, visto ao vivo na OLT03 do G5:

         Chassis / Slot / Port  Serial Number
         ---------------------  -------------
               1/1/12           VSOL007B1566
    """
    vistos, onus = set(), []
    for linha in (texto or '').splitlines():
        m = _DISC_RE.match(linha)
        if not m:
            continue
        sn = m.group(2).upper()
        if sn in vistos:
            continue
        vistos.add(sn)
        onus.append({'pon': m.group(1), 'sn': sn, 'extra': m.group(3).strip()})
    return onus


def vendor_do_sn(sn):
    return (sn or '')[:4].upper()


# ─── Sugestão (maioria da PON) ───────────────────────────────────────────────

def _receita(o):
    sp = (o.get('sps') or [None])[0]
    vlan = sp['vlan'] if sp and 'vlan' in sp else o.get('native_vlan')
    return (o['modo'], o['service'], o['line'], sp.get('gem') if sp else None, vlan,
            sp.get('vlan_olt') if sp else vlan)


def ids_onu_usados(backup, pon):
    return set(backup['pons'].get(pon, {}).get('onus', {}))


def proximo_livre(usados, inicio=0, fim=None):
    fim = MAX_ONU_ID if fim is None else fim
    for i in range(inicio, fim + 1):
        if i not in usados:
            return i
    return None


def sugerir(backup, pon, sn=''):
    """Modo, perfis, GEM e VLAN mais usados pelas ONUs que já estão na PON.
    Primeiro entre as do mesmo fabricante (4 letras do SN), depois a PON
    toda e, numa PON vazia, a OLT toda."""
    onus_pon = list(backup['pons'].get(pon, {}).get('onus', {}).values())
    todas = [o for d in backup['pons'].values() for o in d['onus'].values()]
    vend = vendor_do_sn(sn)
    escolha, origem = None, ''
    for grupo, nome in (([o for o in onus_pon if vendor_do_sn(o['sn']) == vend], 'PON, mesmo fabricante'),
                        (onus_pon, 'PON'),
                        ([o for o in todas if vendor_do_sn(o['sn']) == vend], 'OLT, mesmo fabricante'),
                        (todas, 'OLT')):
        receitas = [_receita(o) for o in grupo if o['line'] and o['service']]
        if receitas:
            escolha, n = Counter(receitas).most_common(1)[0]
            origem = f'{n} de {len(receitas)} ONUs ({nome})'
            break
    sug = {'modo': 'bridge', 'service': '', 'line': '', 'gem': None, 'vlan': None,
           'vlan_olt': None, 'origem': origem or 'backup sem ONUs'}
    if escolha:
        sug.update(zip(('modo', 'service', 'line', 'gem', 'vlan', 'vlan_olt'), escolha))
    if sug['gem'] is None and sug['line'] in backup['lines']:
        gems = backup['lines'][sug['line']]['gems']
        sug['gem'] = gems[0]['gem'] if gems else None
    sug['onu_id'] = proximo_livre(ids_onu_usados(backup, pon))
    return sug


# ─── Validação e comandos ────────────────────────────────────────────────────

def _inteiro(valor, nome, minimo, maximo, obrigatorio=True):
    if valor in (None, '') and not obrigatorio:
        return None
    try:
        v = int(valor)
    except (TypeError, ValueError):
        raise ProvisionamentoInvalido(f'{nome} inválido: {valor!r}')
    if not minimo <= v <= maximo:
        raise ProvisionamentoInvalido(f'{nome} fora da faixa {minimo}–{maximo}: {v}')
    return v


def _perfil(nome, rotulo):
    nome = str(nome or '').strip()
    if not _NOME_RE.match(nome):
        raise ProvisionamentoInvalido(f'{rotulo} inválido: {nome!r} (letras, números, _ . -; até 32)')
    return nome


def nome_onu(texto):
    """O DmOS aceita o `name` sem espaço (os backups usam _)."""
    return _NOME_ONU_RE.sub('_', str(texto or '').strip()).strip('_')[:48]


def validar_item(item, backup):
    """Normaliza um cartão; devolve (item, avisos). Não escolhe IDs."""
    pon = str(item.get('pon') or '').strip()
    if not _PON_RE.match(pon) or pon not in backup['pons']:
        raise ProvisionamentoInvalido(f'PON {pon!r} não existe no backup desta OLT.')
    sn = str(item.get('sn') or '').strip().upper()
    if not _SN_RE.match(sn):
        raise ProvisionamentoInvalido(f'Serial {sn!r} inválido (4 letras/números + 8 hex, ex.: DACM91EC965B).')
    modo = item.get('modo') or 'bridge'
    if modo not in MODOS:
        raise ProvisionamentoInvalido(f'Modo {modo!r} inválido.')
    it = {
        'pon': pon, 'sn': sn, 'modo': modo,
        'service': _perfil(item.get('service'), 'Service profile'),
        'line': _perfil(item.get('line'), 'Line profile'),
        'gem': _inteiro(item.get('gem'), 'GEM', 1, 1023),
        'vlan': _inteiro(item.get('vlan'), 'VLAN', 1, 4094),
        'nome': nome_onu(item.get('nome')),
        'onu_id': _inteiro(item.get('onu_id'), 'ID da ONU', 0, MAX_ONU_ID, obrigatorio=False),
    }
    it['vlan_olt'] = _inteiro(item.get('vlan_olt') or it['vlan'], 'VLAN da OLT', 1, 4094)
    avisos = []
    if it['line'] not in backup['lines']:
        avisos.append(f'{sn}: line-profile {it["line"]} não está no backup.')
    else:
        lp = backup['lines'][it['line']]
        gems = [g['gem'] for g in lp['gems']]
        if gems and it['gem'] not in gems:
            avisos.append(f'{sn}: o line-profile {it["line"]} não tem o GEM {it["gem"]} (tem {gems}).')
        if lp['modo'] != modo:
            avisos.append(f'{sn}: o line-profile {it["line"]} é de ONU {lp["modo"]}, e o cartão está em {modo}.')
    if it['service'] not in backup['service_profiles']:
        avisos.append(f'{sn}: service-profile {it["service"]} não está no backup.')
    if backup['vlans'] and it['vlan_olt'] not in backup['vlans']:
        avisos.append(f'{sn}: a VLAN {it["vlan_olt"]} não está no dot1q do backup (não sai pela uplink).')
    if it['vlan_olt'] not in backup['service_vlans']:
        avisos.append(f'{sn}: a VLAN {it["vlan_olt"]} não tem service vlan no backup.')
    if sn in backup['sns']:
        avisos.append(f'{sn}: o serial já consta no backup em {backup["sns"][sn]}.')
    if it['onu_id'] is not None and it['onu_id'] in ids_onu_usados(backup, pon):
        raise ProvisionamentoInvalido(f'{sn}: a ONU {it["onu_id"]} já existe em {pon} no backup.')
    return it, avisos


def alocar_ids(itens, backup, ocupados_onu=None, ocupados_sp=None):
    """Escolhe o ID da ONU (por PON) e o do service-port de cada item, sem
    repetir dentro do lote. `ocupados_*`: IDs que a OLT disse que existem
    (backup velho)."""
    ocupados_onu = ocupados_onu or {}
    usados_sp = set(backup['service_ports']) | set(ocupados_sp or ())
    por_pon = {}
    for it in itens:
        usados = por_pon.setdefault(it['pon'], ids_onu_usados(backup, it['pon'])
                                    | set(ocupados_onu.get(it['pon'], ())))
        if it.get('onu_id') is None or it['onu_id'] in usados:
            it['onu_id'] = proximo_livre(usados)
            if it['onu_id'] is None:
                raise ProvisionamentoInvalido(f'A PON {it["pon"]} não tem ID de ONU livre (0–{MAX_ONU_ID}).')
        usados.add(it['onu_id'])
        it['sp_id'] = proximo_livre(usados_sp, 1, MAX_SERVICE_PORT)
        usados_sp.add(it['sp_id'])
    return itens


def linha_service_port(it):
    return (f'service-port {it["sp_id"]} gpon {it["pon"]} onu {it["onu_id"]} gem {it["gem"]} '
            f'match vlan vlan-id {it["vlan"]} action vlan replace vlan-id {it["vlan_olt"]}')


def comandos_onu(it):
    """Os comandos de config de uma ONU, sem o `config`/`commit` em volta.
    Mesma forma do guia DM4610 e do running-config do G5."""
    cmds = [f'interface gpon {it["pon"]}', f'onu {it["onu_id"]}']
    if it['nome']:
        cmds.append(f'name {it["nome"]}')
    cmds += [f'serial-number {it["sn"]}',
             f'service-profile {it["service"]}',
             f'line-profile {it["line"]}']
    if it['modo'] == 'router':
        cmds.append('veip 1')
    else:
        cmds += ['ethernet 1', 'negotiation', 'no shutdown', f'native vlan vlan-id {it["vlan"]}']
    cmds += ['top', linha_service_port(it)]
    return cmds


def checagens_onu(it):
    """Leituras que precisam responder 'element does not exist' antes de
    gravar: entrar num ID existente editaria o cadastro de outro assinante."""
    return [f'show running-config interface gpon {it["pon"]} onu {it["onu_id"]}',
            f'show running-config service-port {it["sp_id"]}']


def roteiro(comandos):
    return ['config'] + comandos + ['commit', 'end']


# ─── Remoção da ONU recém-provisionada ───────────────────────────────────────

def comandos_remover(pon, onu_id, sp_ids):
    cmds = [f'no service-port {s}' for s in sp_ids]
    cmds += [f'interface gpon {pon}', f'no onu {onu_id}', 'top']
    return cmds


def confere_onu(saida, sn):
    """`show running-config interface gpon P onu N` mostra a mesma SN?"""
    return bool(re.search(rf'serial-number\s+{re.escape(sn)}\b', saida or '', re.IGNORECASE))


# ─── Perfis (bandwidth / line / onu / service) e VLAN ────────────────────────

def validar_perfis(spec, backup):
    """Normaliza o pedido de criação de perfis. Nome que já existe no backup
    é recusado: no DmOS entrar num perfil existente EDITA o perfil, e todas
    as ONUs dele mudariam."""
    tipo = spec.get('tipo') or 'bridge'
    if tipo not in MODOS:
        raise ProvisionamentoInvalido('Tipo inválido.')
    vlan = _inteiro(spec.get('vlan'), 'VLAN', 1, 4094)
    vlan_map = str(spec.get('vlan_map') or vlan).strip().lower()
    if vlan_map != 'any':
        vlan_map = str(_inteiro(vlan_map, 'VLAN do map', 1, 4094))
    norm = {'tipo': tipo, 'vlan': vlan, 'vlan_map': vlan_map, 'avisos': []}

    bw = spec.get('bandwidth') or {}
    if bw.get('novo'):
        norm['bw_novo'] = True
        norm['bw'] = _perfil(bw.get('nome'), 'Nome do bandwidth-profile')
        if norm['bw'] in backup['bandwidths']:
            raise ProvisionamentoInvalido(f'O bandwidth-profile {norm["bw"]} já existe — escolha-o na lista.')
        bt = bw.get('tipo') or 'type-4'
        if bt not in BW_TIPOS:
            raise ProvisionamentoInvalido('Tipo de banda inválido.')
        norm['bw_tipo'] = bt
        norm['bw_kbps'] = _inteiro(bw.get('kbps'), 'Banda (kbps)', 64, 10_000_000)
    else:
        norm['bw_novo'] = False
        norm['bw'] = _perfil(bw.get('nome'), 'Bandwidth-profile')
        if norm['bw'] not in backup['bandwidths']:
            norm['avisos'].append(f'O bandwidth-profile {norm["bw"]} não está no backup.')

    norm['line'] = _perfil(spec.get('line'), 'Nome do line-profile')
    if norm['line'] in backup['lines']:
        raise ProvisionamentoInvalido(f'O line-profile {norm["line"]} já existe na OLT (backup). '
                                      'Entrar nele editaria o perfil de todas as ONUs que o usam.')
    norm['tcont'] = _inteiro(spec.get('tcont') or 1, 'T-CONT', 1, 7)
    norm['gem'] = _inteiro(spec.get('gem') or 1, 'GEM', 1, 1023)
    norm['map'] = _perfil(spec.get('map') or 'pppoe', 'Nome do map')
    norm['fec'] = bool(spec.get('fec', True))

    sp = spec.get('service') or {}
    if sp.get('novo'):
        norm['srv_novo'] = True
        norm['srv'] = _perfil(sp.get('nome'), 'Nome do service-profile')
        if norm['srv'] in backup['service_profiles']:
            raise ProvisionamentoInvalido(f'O service-profile {norm["srv"]} já existe — escolha-o na lista.')
        onu = sp.get('onu_profile') or {}
        if onu.get('novo'):
            norm['onu_novo'] = True
            norm['onu_profile'] = _perfil(onu.get('nome'), 'Nome do onu-profile')
            if norm['onu_profile'] in backup['onu_profiles']:
                raise ProvisionamentoInvalido(f'O onu-profile {norm["onu_profile"]} já existe — escolha-o na lista.')
            norm['onu_pots'] = bool(onu.get('pots'))
        else:
            norm['onu_novo'] = False
            norm['onu_profile'] = _perfil(onu.get('nome'), 'Onu-profile')
    else:
        norm['srv_novo'] = False
        norm['srv'] = _perfil(sp.get('nome'), 'Service-profile')

    # VLAN na OLT: dot1q (com a uplink) e service vlan, só o que falta.
    interfaces = [str(i).strip() for i in (spec.get('uplinks') or []) if str(i).strip()]
    for i in interfaces:
        if not re.match(r'^(gigabit|ten-gigabit|forty-gigabit|hundred-gigabit)-ethernet-\d+/\d+/\d+$|^lag-\d+$', i):
            raise ProvisionamentoInvalido(f'Interface de uplink inválida: {i!r} (ex.: ten-gigabit-ethernet-1/1/1)')
    existente = backup['vlans'].get(vlan)
    norm['vlan_nova'] = existente is None
    norm['vlan_nome'] = _perfil(spec.get('vlan_nome') or f'VLAN-{vlan}', 'Nome da VLAN') if norm['vlan_nova'] else ''
    norm['uplinks'] = [i for i in interfaces if not existente or i not in existente['interfaces']]
    svt = spec.get('service_vlan') or 'n:1'
    if svt not in SERVICE_VLAN_TIPOS:
        raise ProvisionamentoInvalido('Tipo de service vlan inválido.')
    norm['svlan'] = '' if vlan in backup['service_vlans'] else svt
    if norm['vlan_nova'] and not norm['uplinks']:
        norm['avisos'].append(f'A VLAN {vlan} vai ser criada sem nenhuma uplink: o tráfego não sai da OLT.')
    return norm


def comandos_perfis(n):
    cmds = []
    if n['bw_novo']:
        cmds += [f'profile gpon bandwidth-profile {n["bw"]}',
                 f'traffic {n["bw_tipo"]} {BW_TIPOS[n["bw_tipo"]]} {n["bw_kbps"]}', 'top']
    alvo = 'veip 1' if n['tipo'] == 'router' else 'ethernet any'
    cmds += [f'profile gpon line-profile {n["line"]}']
    if n['fec']:
        cmds.append('upstream-fec')
    cmds += [f'tcont {n["tcont"]} bandwidth-profile {n["bw"]}',
             f'gem {n["gem"]}', f'tcont {n["tcont"]} priority 0',
             f'map {n["map"]}', f'{alvo} vlan {n["vlan_map"]} cos any', 'top']
    if n['srv_novo']:
        if n.get('onu_novo'):
            cmds.append(f'profile gpon onu-profile {n["onu_profile"]}')
            if n['tipo'] == 'router':
                if n.get('onu_pots'):
                    cmds.append('pots adaptive')
                cmds.append('veip 1')
            else:
                cmds.append('ethernet adaptive')
            cmds.append('top')
        cmds += [f'profile gpon service-profile {n["srv"]}', f'onu-profile {n["onu_profile"]}', 'top']
    if n['vlan_nova'] or n['uplinks']:
        cmds += ['dot1q', f'vlan {n["vlan"]}']
        if n['vlan_nova']:
            cmds.append(f'name {n["vlan_nome"]}')
        for i in n['uplinks']:
            cmds.append(f'interface {i}')
        cmds.append('top')
    if n['svlan']:
        cmds += [f'service vlan {n["vlan"]}', f'type {n["svlan"]}', 'top']
    return cmds


def checagens_perfis(n):
    """Nomes novos precisam não existir na OLT (o backup pode estar velho)."""
    c = [f'show running-config profile gpon line-profile {n["line"]}']
    if n['bw_novo']:
        c.append(f'show running-config profile gpon bandwidth-profile {n["bw"]}')
    if n['srv_novo']:
        c.append(f'show running-config profile gpon service-profile {n["srv"]}')
        if n.get('onu_novo'):
            c.append(f'show running-config profile gpon onu-profile {n["onu_profile"]}')
    return c


# ─── Respostas da CLI ────────────────────────────────────────────────────────

_NAO_EXISTE_RE = re.compile(r'element does not exist|No entries found', re.IGNORECASE)
_ERRO_RE = re.compile(
    r'syntax error|Aborted:|^\s*Error:|% ?Invalid|unknown (command|element|argument)|'
    r'incomplete (command|path)|Commit failed|missing (element|argument)|'
    r'is not (allowed|valid)|validation failed', re.IGNORECASE | re.M)
_COMMIT_OK_RE = re.compile(r'Commit complete', re.IGNORECASE)


def nao_existe(saida):
    """True: a OLT disse que o elemento não existe (livre). False: mostrou o
    elemento. None: resposta que não dá para afirmar nada — aborta."""
    if _NAO_EXISTE_RE.search(saida or ''):
        return True
    if re.search(r'^\s*(interface gpon|service-port|profile gpon)\b', saida or '', re.M):
        return False
    return None


def erro_cli(saida):
    m = _ERRO_RE.search(saida or '')
    if not m:
        return ''
    ini = saida.rfind('\n', 0, m.start()) + 1
    fim = saida.find('\n', m.end())
    return saida[ini:(fim if fim != -1 else len(saida))].strip() or m.group(0)


def commit_ok(saida):
    return bool(_COMMIT_OK_RE.search(saida or '')) and not erro_cli(saida)


# ─── Sessão (modo servidor) ──────────────────────────────────────────────────

_PROMPT_RE = re.compile(r'(?:^|\n)[^\n]*?[A-Za-z0-9_.\-]+(?:\([^)\n]*\))?#\s*$')
_MORE_RE = re.compile(r'--\s*More\s*--|\(more\)|-+\s*More', re.IGNORECASE)
_PERGUNTA_RE = re.compile(r'\[yes,no(?:,CANCEL)?\]|\[yes/no(?:/CANCEL)?\]', re.IGNORECASE)


class ShellDatacom:
    """Shell DmOS que lê até o prompt (`OLT03#`, `OLT03(config)#`) voltar."""

    def __init__(self, client):
        from .views import limpar_ansi, _ler_ate_silencio
        self._limpar = limpar_ansi
        self.channel = client.invoke_shell(term='vt100', width=500, height=50)
        self.channel.settimeout(60)
        time.sleep(1.0)
        _ler_ate_silencio(self.channel, silencio=1.0, max_wait=10)

    def enviar(self, comando, max_wait=60, responder='no'):
        self.channel.send(comando + '\n')
        bruto, inicio = '', time.time()
        while time.time() - inicio < max_wait:
            if self.channel.recv_ready():
                bruto += self.channel.recv(65536).decode('utf-8', errors='replace')
                cauda = bruto[-300:]
                if _MORE_RE.search(cauda[-40:]):
                    self.channel.send(' ')
                    continue
                if _PERGUNTA_RE.search(cauda[-80:]):
                    # "Uncommitted changes found, commit them?" — nunca grava
                    # sem querer: a resposta é sempre a do chamador.
                    self.channel.send(responder + '\n')
                    continue
                if _PROMPT_RE.search(cauda):
                    time.sleep(0.15)
                    if not self.channel.recv_ready():
                        break
            else:
                time.sleep(0.05)
        linhas = self._limpar(bruto).split('\n')
        if linhas and comando.strip() and comando.strip() in linhas[0]:
            linhas = linhas[1:]
        return '\n'.join(linhas).strip('\n')

    def prompt(self):
        saida = self.enviar('')
        linhas = [l for l in saida.strip().split('\n') if l.strip()]
        return linhas[-1].strip() if linhas else ''

    def descartar(self):
        """Sai do config sem gravar o que ficou pendente."""
        self.enviar('abort')
        if '(config' in self.prompt():
            self.enviar('end', responder='no')

    def fechar(self):
        try:
            self.channel.send('exit\n')
            time.sleep(0.4)
        except Exception:
            pass
        try:
            self.channel.close()
        except Exception:
            pass


def aplicar(shell, comandos):
    """`config` + comandos + `commit`. Devolve (ok, log, erro). Na primeira
    recusa descarta tudo (nada entra pela metade)."""
    log = []
    saida = shell.enviar('config')
    log.append(f'config\n{saida}')
    if '(config' not in saida and '(config' not in shell.prompt():
        return False, '\n'.join(log), 'não entrou no modo config'
    for cmd in comandos:
        saida = shell.enviar(cmd)
        log.append(f'{cmd}\n{saida}' if saida.strip() else cmd)
        err = erro_cli(saida)
        if err:
            shell.descartar()
            log.append('abort  (descartado, nada gravado)')
            return False, '\n'.join(log), f'{cmd} → {err}'
    saida = shell.enviar('commit', max_wait=90)
    log.append(f'commit\n{saida}')
    if not commit_ok(saida):
        shell.descartar()
        log.append('abort  (descartado, nada gravado)')
        return False, '\n'.join(log), erro_cli(saida) or f'commit sem "Commit complete": {saida.strip()[-200:]}'
    shell.enviar('end')
    log.append('end')
    return True, '\n'.join(log), ''


def _livre(shell, cmd, log):
    saida = shell.enviar(cmd)
    log.append(f'{cmd}\n{saida}')
    return nao_existe(saida)


def provisionar(acesso, itens, backup):
    """Gera ('info'|'onu', payload). Cada ONU: confere que segue pendente,
    que o ID da ONU e o do service-port estão livres ao vivo, e grava num
    commit só."""
    from .olt_pon import conectar, desconectar
    client = tunel = shell = None
    try:
        try:
            client, tunel = conectar(acesso)
            shell = ShellDatacom(client)
        except Exception as e:
            for it in itens:
                yield 'onu', {'sn': it['sn'], 'status': 'erro', 'msg': f'Falha ao conectar: {e}'}
            return
        disc = shell.enviar('show interface gpon discovered-onus', max_wait=60)
        pendentes = {o['sn']: o['pon'] for o in parse_discovered(disc)}
        yield 'info', {'msg': f'{len(pendentes)} ONU(s) pendente(s) agora na OLT.'}
        ocupados_onu, ocupados_sp = {}, set()
        for idx, it in enumerate(itens):
            if pendentes.get(it['sn']) != it['pon']:
                onde = pendentes.get(it['sn'])
                yield 'onu', {'sn': it['sn'], 'status': 'ignorada',
                              'msg': f'não está mais pendente' + (f' (agora em {onde})' if onde else '')}
                continue
            log, livre, motivo = [], False, ('não consegui confirmar ID de ONU/service-port livre na OLT '
                                         '(vários IDs do backup já estão em uso: rode um backup e tente de novo)')
            for _ in range(10):
                try:
                    alocar_ids([it], backup, ocupados_onu, ocupados_sp)
                except ProvisionamentoInvalido as e:
                    motivo = str(e)
                    break
                c_onu, c_sp = checagens_onu(it)
                r_onu = _livre(shell, c_onu, log)
                r_sp = _livre(shell, c_sp, log) if r_onu else None
                if r_onu is None or (r_onu and r_sp is None):
                    break
                if not r_onu:
                    ocupados_onu.setdefault(it['pon'], set()).add(it['onu_id'])
                    it['onu_id'] = None
                    continue
                if not r_sp:
                    ocupados_sp.add(it['sp_id'])
                    continue
                livre = True
                break
            if not livre:
                yield 'onu', {'sn': it['sn'], 'status': 'erro', 'comandos': '\n'.join(log),
                              'msg': motivo}
                continue
            ok, saida, err = aplicar(shell, comandos_onu(it))
            log.append(saida)
            # Os próximos do lote não podem pegar estes IDs.
            ocupados_onu.setdefault(it['pon'], set()).add(it['onu_id'])
            ocupados_sp.add(it['sp_id'])
            yield 'onu', {'sn': it['sn'], 'status': 'sucesso' if ok else 'erro',
                          'onu_id': it['onu_id'], 'sp_id': it['sp_id'], 'pon': it['pon'],
                          'comandos': '\n'.join(log),
                          'msg': f'ONU {it["onu_id"]} em {it["pon"]} · service-port {it["sp_id"]}'
                                 if ok else err}
    finally:
        if shell:
            shell.fechar()
        desconectar(client, tunel)


def criar_perfis(acesso, norm):
    """Confere os nomes ao vivo e grava tudo num commit só."""
    from .olt_pon import conectar, desconectar
    client = tunel = shell = None
    log = []
    try:
        client, tunel = conectar(acesso)
        shell = ShellDatacom(client)
        for cmd in checagens_perfis(norm):
            r = _livre(shell, cmd, log)
            if r is not True:
                return 'erro', '\n'.join(log), (f'{cmd.split()[-1]} já existe na OLT — nada foi criado.'
                                                if r is False else f'resposta inesperada em "{cmd}" — nada foi criado.')
        ok, saida, err = aplicar(shell, comandos_perfis(norm))
        log.append(saida)
        return ('sucesso' if ok else 'erro'), '\n'.join(log), ('Perfis criados.' if ok else err)
    finally:
        if shell:
            shell.fechar()
        desconectar(client, tunel)


def remover_onu(acesso, sn, pon, onu_id, sp_ids):
    from .olt_pon import conectar, desconectar
    client = tunel = shell = None
    log = []
    try:
        client, tunel = conectar(acesso)
        shell = ShellDatacom(client)
        cmd = f'show running-config interface gpon {pon} onu {onu_id}'
        saida = shell.enviar(cmd)
        log.append(f'{cmd}\n{saida}')
        if not confere_onu(saida, sn):
            return 'erro', '\n'.join(log), f'a ONU {onu_id} de {pon} não é mais a {sn} — nada removido.'
        ok, saida, err = aplicar(shell, comandos_remover(pon, onu_id, sp_ids))
        log.append(saida)
        return ('sucesso' if ok else 'erro'), '\n'.join(log), (f'ONU {onu_id} removida de {pon}.' if ok else err)
    finally:
        if shell:
            shell.fechar()
        desconectar(client, tunel)
