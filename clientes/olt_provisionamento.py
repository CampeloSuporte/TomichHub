"""
Provisionamento de ONU/ONT em OLT Huawei (MA5600T/MA5800), individual ou em
massa, a partir do painel ⚡ Scripts do terminal.

Mesmo desenho de `olt_pon.py` (de onde vem a conexão e a detecção de recusa):

- `parse_autofind` só LÊ a saída do `display ont autofind all` — as ONUs que
  acenderam na PON e ainda não têm cadastro;
- `parse_backup` só LÊ o backup: line/srv profiles, VLANs, e como cada PON já
  está provisionada (`ont add`, `native-vlan`, `service-port`). É daí que sai a
  sugestão de VLAN, perfis e gemport de cada ONU nova — a OLT da WTD (SENTO SE)
  usa uma VLAN por PON (0/1/0 → 300, 0/1/1 → 301...) e srv-profile 1001
  "adaptive" só nas Huawei; nada disso é fixo, é o backup que diz;
- `comandos_onu` só MONTA os comandos (preview);
- `provisionar` conecta e envia, ONU por ONU, numa única sessão.

Os dois modelos de comando vieram do operador:

    genérico (ONU + ONT)                         só ONT
    interface gpon 0/<slot>                      interface gpon 0/<slot>
    ont add <pon> sn-auth <sn> omci              ont add <pon> sn-auth <sn> omci
        ont-lineprofile-id <l> ont-srvprofile-id <s>   (idem)
    ont port native-vlan <pon> <id> eth 1        quit
        vlan <vlan> priority 0                   service-port vlan <vlan> gpon ...
    quit
    service-port vlan <vlan> gpon 0/<slot>/<pon> ont <id> gemport <g>
        multi-service user-vlan <vlan> tag-transform translate

O `ont add` vai SEM o ONT-ID de propósito: a OLT escolhe o primeiro livre e
devolve na resposta (`ONTID :44`). Calcular o ID pelo backup erraria toda vez
que alguém provisionou depois do último backup — e um ID ocupado faz o
`ont add` falhar. Por isso a execução é por etapas: o `native-vlan` e o
`service-port` só são montados depois de ler o ID que a OLT respondeu.
"""
import logging
import re
import time
from collections import Counter

from .olt_pon import PREAMBULO, detectar_erro_cli

logger = logging.getLogger(__name__)


class ProvisionamentoInvalido(Exception):
    """Pedido recusado antes de tocar no equipamento: placa/porta que não
    existe, SN malformado, VLAN fora da faixa..."""


MODOS = {
    'onu': {'label': 'Genérico ONU + ONT (com native-vlan)', 'native_vlan': True},
    'ont': {'label': 'Só ONT (sem native-vlan)', 'native_vlan': False},
    # Um service-port por GEM do line profile, traduzindo a user-vlan da ONT
    # para a VLAN da OLT (ex.: GEM 101 / user-vlan 101 → VLAN 3304).
    'translate': {'label': 'VLAN translate (service-port por GEM)', 'native_vlan': False},
}

# Teto do lote: cada ONU são ~5 comandos numa sessão só. 64 já é uma PON
# inteira de ONUs novas; acima disso é melhor dividir e conferir no meio.
MAX_ONUS_LOTE = 64

_SN_RE = re.compile(r'^[0-9A-F]{16}$')
_SLOT_RE = re.compile(r'^\d{1,2}/\d{1,2}$')


# ─── Saída do `display ont autofind all` ─────────────────────────────────────

_AF_CAMPO_RE = re.compile(r'^\s*([A-Za-z][A-Za-z /]*?)\s*:\s?(.*?)\s*$')
_AF_SN_RE = re.compile(r'^([0-9A-Fa-f]{16})(?:\s*\(([^)]*)\))?')
_AF_FSP_RE = re.compile(r'^(\d+)/(\d+)/(\d+)$')


def parse_autofind(texto):
    """ONUs pendentes de provisionamento, na ordem em que a OLT listou.

    Cada bloco começa em `Number :`; um SN repetido (a mesma ONU vista em
    duas PONs, ou duas leituras coladas) entra uma vez só.
    """
    onus, atual, vistos = [], None, set()

    def _fechar():
        if atual and atual.get('sn') and atual['sn'] not in vistos:
            vistos.add(atual['sn'])
            onus.append(atual)

    for linha in (texto or '').splitlines():
        m = _AF_CAMPO_RE.match(linha)
        if not m:
            continue
        chave, valor = m.group(1).strip().lower(), m.group(2).strip()
        if chave == 'number':
            _fechar()
            atual = {'numero': valor}
            continue
        if atual is None:
            continue
        if chave == 'f/s/p':
            mf = _AF_FSP_RE.match(valor)
            if mf:
                atual['slot'] = f'{mf.group(1)}/{mf.group(2)}'
                atual['porta'] = int(mf.group(3))
                atual['fsp'] = valor
        elif chave == 'ont sn':
            ms = _AF_SN_RE.match(valor)
            if ms:
                atual['sn'] = ms.group(1).upper()
                atual['sn_legivel'] = ms.group(2) or ''
        elif chave == 'vendorid':
            atual['vendor'] = valor
        elif chave == 'ont version':
            atual['versao'] = valor
        elif chave == 'ont softwareversion':
            atual['software'] = valor
        elif chave == 'ont equipmentid':
            atual['modelo'] = valor
        elif chave == 'loid':
            atual['loid'] = valor
        elif chave == 'ont autofind time':
            atual['visto_em'] = valor
    _fechar()
    # Sem F/S/P não há onde provisionar.
    return [o for o in onus if 'slot' in o]


def vendor_do_sn(sn):
    """Os 8 primeiros dígitos do SN são o vendor em ASCII: 48575443 → HWTC."""
    try:
        return bytes.fromhex((sn or '')[:8]).decode('ascii')
    except (ValueError, UnicodeDecodeError):
        return ''


# ─── Backup: perfis e como cada PON já está provisionada ─────────────────────

_LINE_RE = re.compile(r'^\s*ont-lineprofile gpon profile-id (\d+) profile-name "([^"]*)"')
_SRV_RE = re.compile(r'^\s*ont-srvprofile gpon profile-id (\d+) profile-name "([^"]*)"')
_GEM_ADD_RE = re.compile(r'^\s*gem add (\d+) eth tcont (\d+)')
_GEM_MAP_RE = re.compile(r'^\s*gem mapping (\d+) \d+ vlan (\d+)')
_SRV_VLAN_RE = re.compile(r'^\s*port vlan eth \d+ translation (\d+)')
_INTERFACE_GPON_RE = re.compile(r'^\s*interface gpon (\d+/\d+)\s*$')
_ONT_ADD_RE = re.compile(
    r'^\s*ont add (\d+) (\d+) sn-auth "?([0-9A-Fa-f]{16})"?.*?'
    r'ont-lineprofile-id (\d+) ont-srvprofile-id (\d+)')
_NATIVE_RE = re.compile(r'^\s*ont port native-vlan (\d+) (\d+) eth \d+ vlan (\d+)')
_SERVICE_PORT_RE = re.compile(
    r'^\s*service-port \d+ vlan (\d+) gpon (\d+/\d+)/(\d+) ont (\d+) gemport (\d+)')
_DBA_RE = re.compile(r'^\s*dba-profile add profile-id (\d+) profile-name "([^"]*)"')
_VLAN_RE = re.compile(r'^\s*vlan (\d+)(?: to (\d+))? ')


def parse_backup(conteudo):
    """Tudo que o provisionamento precisa saber do backup, sem conectar.

    `onts[(slot, porta)]` = {ont_id: {...}} junta as três linhas que formam
    um assinante (ont add + native-vlan + service-port).
    """
    lines, srvs, vlans, dbas = {}, {}, set(), {}
    onts, sns = {}, {}
    perfil_atual, slot_atual = None, None

    for linha in (conteudo or '').splitlines():
        # O corpo de um perfil vem com dois espaços de recuo; qualquer linha
        # menos recuada (outro perfil, `#`, outra seção) fecha o bloco.
        if perfil_atual is not None and not linha.startswith('  '):
            perfil_atual = None
        m = _LINE_RE.match(linha)
        if m:
            perfil_atual = lines.setdefault(int(m.group(1)), {
                'id': int(m.group(1)), 'nome': m.group(2).strip(), 'gems': [], 'vlans': [],
                'mapas': []})
            continue
        m = _SRV_RE.match(linha)
        if m:
            perfil_atual = srvs.setdefault(int(m.group(1)), {
                'id': int(m.group(1)), 'nome': m.group(2).strip(), 'vlans': []})
            continue
        if perfil_atual is not None:
            m = _GEM_ADD_RE.match(linha)
            if m and 'gems' in perfil_atual:
                perfil_atual['gems'].append(int(m.group(1)))
                continue
            m = _GEM_MAP_RE.match(linha)
            if m and 'mapas' in perfil_atual:
                perfil_atual['mapas'].append([int(m.group(1)), int(m.group(2))])
            m = m or _SRV_VLAN_RE.match(linha)
            if m:
                vlan = int(m.groups()[-1])
                if vlan not in perfil_atual['vlans']:
                    perfil_atual['vlans'].append(vlan)
            continue

        m = _DBA_RE.match(linha)
        if m:
            dbas[int(m.group(1))] = m.group(2).strip()
            continue

        m = _VLAN_RE.match(linha)
        if m and linha.rstrip().endswith(('smart', 'standard', 'mux', 'super')):
            ini = int(m.group(1))
            fim = int(m.group(2) or ini)
            vlans.update(range(ini, fim + 1))
            continue

        m = _INTERFACE_GPON_RE.match(linha)
        if m:
            slot_atual = m.group(1)
            continue
        if linha.startswith('#'):
            slot_atual = None
            continue
        if slot_atual:
            m = _ONT_ADD_RE.match(linha)
            if m:
                porta, ont_id = int(m.group(1)), int(m.group(2))
                sn = m.group(3).upper()
                onts.setdefault((slot_atual, porta), {})[ont_id] = {
                    'sn': sn, 'line': int(m.group(4)), 'srv': int(m.group(5)),
                    'native_vlan': None, 'vlan': None, 'gemport': None}
                sns[sn] = f'{slot_atual}/{porta} ONT {ont_id}'
                continue
            m = _NATIVE_RE.match(linha)
            if m:
                ont = onts.get((slot_atual, int(m.group(1))), {}).get(int(m.group(2)))
                if ont:
                    ont['native_vlan'] = int(m.group(3))
                continue

        m = _SERVICE_PORT_RE.match(linha)
        if m:
            ont = onts.get((m.group(2), int(m.group(3))), {}).get(int(m.group(4)))
            if ont and ont['vlan'] is None:
                ont['vlan'] = int(m.group(1))
                ont['gemport'] = int(m.group(5))

    return {'lines': lines, 'srvs': srvs, 'vlans': vlans, 'onts': onts, 'sns': sns, 'dbas': dbas}


def _mais_comum(valores):
    valores = [v for v in valores if v is not None]
    return Counter(valores).most_common(1)[0][0] if valores else None


def sugerir(backup, slot, porta, sn=''):
    """VLAN, perfis, gemport e modo para uma ONU nova nesta PON, copiando a
    maioria das ONTs que já estão nela.

    O srv-profile olha primeiro as ONTs do MESMO vendor: na SENTO SE as
    Huawei (HWTC) usam o 1001 "adaptive" e as demais o perfil da VLAN — a
    maioria da PON escolheria errado para metade delas.
    """
    na_pon = list(backup['onts'].get((slot, porta), {}).values())
    vendor = vendor_do_sn(sn)
    mesmo_vendor = [o for o in na_pon if vendor and vendor_do_sn(o['sn']) == vendor]

    vlan = _mais_comum([o['vlan'] or o['native_vlan'] for o in na_pon])
    line = _mais_comum([o['line'] for o in na_pon])
    srv = _mais_comum([o['srv'] for o in mesmo_vendor]) or _mais_comum([o['srv'] for o in na_pon])
    gemport = _mais_comum([o['gemport'] for o in na_pon])
    com_native = sum(1 for o in na_pon if o['native_vlan'] is not None)

    if na_pon:
        origem = f'maioria das {len(na_pon)} ONTs da PON {slot}/{porta} no backup'
        if mesmo_vendor and srv == _mais_comum([o['srv'] for o in mesmo_vendor]):
            origem += f' (srv-profile das {len(mesmo_vendor)} {vendor})'
    else:
        origem = f'PON {slot}/{porta} sem ONTs no backup — confira a VLAN'

    # PON vazia: perfis com o mesmo número da VLAN, que é a convenção dos
    # modelos (`ont-lineprofile-id <vlan> ont-srvprofile-id <vlan>`).
    if vlan is not None:
        if line is None and vlan in backup['lines']:
            line = vlan
        if srv is None and vlan in backup['srvs']:
            srv = vlan
    if gemport is None and line in backup['lines'] and backup['lines'][line]['gems']:
        gemport = backup['lines'][line]['gems'][0]

    return {
        'vlan': vlan, 'line': line, 'srv': srv, 'gemport': gemport or 1,
        'modo': 'ont' if na_pon and com_native * 2 < len(na_pon) else 'onu',
        'origem': origem,
    }


# ─── Validação e geração dos comandos ────────────────────────────────────────

def _inteiro(valor, nome, minimo, maximo):
    texto = str(valor if valor is not None else '').strip()
    if not texto.isdigit() or not (minimo <= int(texto) <= maximo):
        raise ProvisionamentoInvalido(f'{nome} inválido: "{texto}" (use {minimo}–{maximo}).')
    return int(texto)


def _desc_limpa(desc):
    """Aspas fecham o `desc "..."` no meio e o resto vira parâmetro."""
    return re.sub(r'["\r\n\t]', '', str(desc or '')).strip()[:64]


def validar_item(item, inventario, backup):
    """Normaliza uma ONU do pedido. Devolve (item, avisos).

    Recusa o que a OLT executaria em contexto errado (placa/porta fora do
    inventário — ver `olt_pon.validar_alvo`) e avisa, sem recusar, o que o
    backup não confirma (perfil/VLAN criados depois do último backup).
    """
    sn = str(item.get('sn') or '').strip().upper()
    if not _SN_RE.match(sn):
        raise ProvisionamentoInvalido(f'SN inválido: "{sn}" — use os 16 dígitos hexadecimais.')

    slot = str(item.get('slot') or '').strip()
    if not _SLOT_RE.match(slot):
        raise ProvisionamentoInvalido(f'{sn}: placa inválida "{slot}".')
    placa = next((p for p in inventario.get('placas', []) if p['slot'] == slot), None)
    if placa is None:
        raise ProvisionamentoInvalido(f'{sn}: a placa {slot} não é uma placa PON deste equipamento.')
    porta = _inteiro(item.get('porta'), f'{sn}: PON', 0, 63)
    if porta >= placa['portas_total']:
        raise ProvisionamentoInvalido(
            f'{sn}: a placa {slot} tem {placa["portas_total"]} portas — a PON {porta} não existe.')

    modo = item.get('modo') or 'onu'
    if modo not in MODOS:
        raise ProvisionamentoInvalido(f'{sn}: modo desconhecido "{modo}".')
    # No translate a VLAN vem de cada service-port (preenchida mais abaixo).
    vlan = None if modo == 'translate' else _inteiro(item.get('vlan'), f'{sn}: VLAN', 1, 4094)
    line = _inteiro(item.get('line'), f'{sn}: line-profile', 0, 8192)
    srv = _inteiro(item.get('srv'), f'{sn}: srv-profile', 0, 8192)
    gemport = _inteiro(item.get('gemport') or 1, f'{sn}: gemport', 0, 1023)

    avisos = []
    if line not in backup['lines']:
        avisos.append(f'{sn}: line-profile {line} não está no backup.')
    elif modo != 'translate' and backup['lines'][line]['gems'] and gemport not in backup['lines'][line]['gems']:
        avisos.append(f'{sn}: o line-profile {line} não tem o gemport {gemport} '
                      f'(tem {", ".join(map(str, backup["lines"][line]["gems"]))}).')
    if srv not in backup['srvs']:
        avisos.append(f'{sn}: srv-profile {srv} não está no backup.')
    if vlan is not None and backup['vlans'] and vlan not in backup['vlans']:
        avisos.append(f'{sn}: VLAN {vlan} não está criada no backup.')
    if sn in backup['sns']:
        avisos.append(f'{sn}: já aparece no backup em {backup["sns"][sn]} — a OLT vai recusar se ainda estiver lá.')

    servicos = []
    if modo == 'translate':
        brutos = item.get('servicos')
        if not isinstance(brutos, list) or not brutos:
            raise ProvisionamentoInvalido(f'{sn}: no modo translate marque pelo menos um GEM com VLAN da OLT.')
        if len(brutos) > MAX_GEMS_PERFIL:
            raise ProvisionamentoInvalido(f'{sn}: no máximo {MAX_GEMS_PERFIL} service-ports por ONU.')
        gems_line = backup['lines'].get(line, {}).get('gems') or []
        for sv in brutos:
            if not isinstance(sv, dict):
                raise ProvisionamentoInvalido(f'{sn}: serviço inválido.')
            g = _inteiro(sv.get('gem'), f'{sn}: GEM', 0, MAX_GEM_INDEX)
            servicos.append({'gem': g,
                             'user_vlan': _inteiro(sv.get('user_vlan'), f'{sn}: user-vlan do GEM {g}', 1, 4094),
                             'vlan': _inteiro(sv.get('vlan'), f'{sn}: VLAN da OLT do GEM {g}', 1, 4094)})
            if gems_line and g not in gems_line:
                avisos.append(f'{sn}: o line-profile {line} não tem o GEM {g}.')
            if backup['vlans'] and servicos[-1]['vlan'] not in backup['vlans']:
                avisos.append(f'{sn}: VLAN {servicos[-1]["vlan"]} não está criada no backup.')
        if len({sv['gem'] for sv in servicos}) != len(servicos):
            raise ProvisionamentoInvalido(f'{sn}: GEM repetido nos serviços.')
        vlan = servicos[0]['vlan']

    return {'sn': sn, 'slot': slot, 'porta': porta, 'vlan': vlan, 'line': line,
            'srv': srv, 'gemport': gemport, 'modo': modo, 'servicos': servicos,
            'desc': _desc_limpa(item.get('desc'))}, avisos


ONT_ID = '{ONT_ID}'


def comando_ont_add(it):
    cmd = (f'ont add {it["porta"]} sn-auth {it["sn"]} omci '
           f'ont-lineprofile-id {it["line"]} ont-srvprofile-id {it["srv"]}')
    if it['desc']:
        cmd += f' desc "{it["desc"]}"'
    return cmd


def comandos_pos_add(it, ont_id):
    """(dentro da interface gpon, depois do quit) — o que depende do ID."""
    dentro = []
    if MODOS[it['modo']]['native_vlan']:
        dentro.append(f'ont port native-vlan {it["porta"]} {ont_id} eth 1 vlan {it["vlan"]} priority 0')
    servicos = it.get('servicos') or [
        {'gem': it['gemport'], 'user_vlan': it['vlan'], 'vlan': it['vlan']}]
    fora = [f'service-port vlan {sv["vlan"]} gpon {it["slot"]}/{it["porta"]} ont {ont_id} '
            f'gemport {sv["gem"]} multi-service user-vlan {sv["user_vlan"]} tag-transform translate'
            for sv in servicos]
    return dentro, fora


def comandos_onu(it, ont_id=ONT_ID):
    """Bloco completo de uma ONU, como vai pro equipamento. No preview o ID
    fica como `{ONT_ID}` — quem preenche é a resposta do `ont add`."""
    dentro, fora = comandos_pos_add(it, ont_id)
    return [f'interface gpon {it["slot"]}', comando_ont_add(it), *dentro, 'quit', *fora]


# ─── Criação de srv/line profile para VLAN translate ─────────────────────────

# Teto de GEMs num line profile criado aqui. O índice vai até 1023 na MA5800,
# mas o limite prático é o da ONT: as Huawei HG/EG suportam 32 GEM ports. Nos
# 34 backups Huawei deste ambiente, o maior perfil tem 5 GEMs. Se a OLT
# aceitar menos, ela recusa, e a recusa é detectada.
MAX_GEMS_PERFIL = 32
MAX_GEM_INDEX = 1023
_NOME_PERFIL_RE = re.compile(r'^[A-Za-z0-9_.\- ]{1,32}$')


def proximo_id_livre(usados, inicio=1, fim=8192):
    for i in range(inicio, fim + 1):
        if i not in usados:
            return i
    return None


def _alocar_gems(vlans, gem_igual_vlan):
    """[(gem, vlan)]: GEM com o mesmo número da VLAN quando cabe, como no
    modelo do operador (gem 101 ↔ vlan 101); senão, o próximo índice livre."""
    usados, pares = set(), []
    for v in vlans:
        if gem_igual_vlan and v <= MAX_GEM_INDEX and v not in usados:
            g = v
        else:
            g = proximo_id_livre(usados | {v for v in vlans if v <= MAX_GEM_INDEX and gem_igual_vlan},
                                 1, MAX_GEM_INDEX)
        usados.add(g)
        pares.append((g, v))
    return pares


def validar_perfis(spec, backup):
    """Normaliza o pedido de criação. Devolve (spec, avisos).

    Recusa ID que já existe no backup: entrar em `ont-lineprofile gpon
    profile-id 300` EDITA o perfil existente, e um `gem add` ali muda as 96
    ONTs ligadas nele. Como o backup pode estar velho, a execução ainda confere
    ao vivo (`checagens_perfis`) antes de criar.
    """
    if not isinstance(spec, dict):
        raise ProvisionamentoInvalido('Pedido inválido.')
    srv, line = spec.get('srv') or {}, spec.get('line') or {}
    criar_srv, criar_line = bool(srv.get('criar')), bool(line.get('criar'))
    if not (criar_srv or criar_line):
        raise ProvisionamentoInvalido('Marque o srv profile, o line profile ou os dois.')

    def _nome(valor, tipo):
        nome = str(valor or '').strip()
        if not _NOME_PERFIL_RE.match(nome):
            raise ProvisionamentoInvalido(
                f'Nome do {tipo} inválido: "{nome}" (até 32 letras, números, espaço, _ . -).')
        return nome

    avisos, norm = [], {'srv': None, 'line': None}
    if criar_srv:
        sid = _inteiro(srv.get('id'), 'ID do srv profile', 0, 8192)
        if sid in backup['srvs']:
            raise ProvisionamentoInvalido(
                f'O srv profile {sid} já existe ("{backup["srvs"][sid]["nome"]}") — escolha outro ID.')
        norm['srv'] = {'id': sid, 'nome': _nome(srv.get('nome'), 'srv profile'),
                       'eth': _inteiro(srv.get('eth') or 8, 'portas ETH adaptive', 1, 24)}
    if criar_line:
        lid = _inteiro(line.get('id'), 'ID do line profile', 0, 8192)
        if lid in backup['lines']:
            raise ProvisionamentoInvalido(
                f'O line profile {lid} já existe ("{backup["lines"][lid]["nome"]}") — escolha outro ID.')
        vlans_brutas = spec.get('vlans')
        if not isinstance(vlans_brutas, list) or not vlans_brutas:
            raise ProvisionamentoInvalido('Adicione pelo menos uma VLAN ao line profile.')
        vlans = []
        for v in vlans_brutas:
            v = _inteiro(v, 'VLAN', 1, 4094)
            if v not in vlans:
                vlans.append(v)
        if len(vlans) > MAX_GEMS_PERFIL:
            raise ProvisionamentoInvalido(
                f'{len(vlans)} VLANs — o line profile comporta no máximo {MAX_GEMS_PERFIL} GEMs.')
        dba = _inteiro(line.get('dba'), 'DBA profile', 0, 1024)
        if backup.get('dbas') and dba not in backup['dbas']:
            avisos.append(f'DBA profile {dba} não está no backup.')
        norm['line'] = {'id': lid, 'nome': _nome(line.get('nome'), 'line profile'),
                        'tcont': _inteiro(line.get('tcont') or 1, 'T-CONT', 1, 7), 'dba': dba,
                        'gems': _alocar_gems(vlans, spec.get('gem_igual_vlan', True) is not False)}
    return norm, avisos


def comandos_perfis(norm):
    """Os blocos de configuração, no formato do modelo do operador."""
    cmds = []
    if norm['srv']:
        s = norm['srv']
        cmds += [f'ont-srvprofile gpon profile-id {s["id"]} profile-name "{s["nome"]}"',
                 f'ont-port eth adaptive {s["eth"]}', 'native-vlan unconcern', 'commit', 'quit']
    if norm['line']:
        ln = norm['line']
        cmds += [f'ont-lineprofile gpon profile-id {ln["id"]} profile-name "{ln["nome"]}"',
                 f'tcont {ln["tcont"]} dba-profile-id {ln["dba"]}']
        cmds += [f'gem add {g} eth tcont {ln["tcont"]}' for g, _ in ln['gems']]
        cmds += [f'gem mapping {g} 1 vlan {v}' for g, v in ln['gems']]
        cmds += ['commit', 'quit']
    return cmds


def checagens_perfis(norm):
    """Leituras feitas antes de criar: o perfil não pode existir na OLT."""
    out = []
    if norm['srv']:
        out.append(f'display ont-srvprofile gpon profile-id {norm["srv"]["id"]}')
    if norm['line']:
        out.append(f'display ont-lineprofile gpon profile-id {norm["line"]["id"]}')
    return out


def perfil_existe(saida):
    """True/False pela resposta do `display ont-*profile` (conferida na
    SENTO SE: "Failure: The line profile does not exist" / "Profile-ID :300").
    None quando a saída não diz nem uma coisa nem outra: aí nada é criado."""
    if re.search(r'profile does not exist', saida or '', re.IGNORECASE):
        return False
    if re.search(r'Profile-ID\s*:\s*\d+', saida or '', re.IGNORECASE):
        return True
    return None


def criar_perfis(shell, norm):
    """Confere e cria numa sessão já em (config)#. Devolve (status, log, msg)."""
    log = []
    for cmd in checagens_perfis(norm):
        saida = shell.enviar(cmd)
        log.append(f'{cmd}\n{saida}')
        existe = perfil_existe(saida)
        if existe is not False:
            motivo = 'já existe na OLT' if existe else 'resposta inesperada da OLT'
            return 'erro', '\n'.join(log), f'{cmd[len("display "):]}: {motivo}. Nada foi criado.'
    for cmd in comandos_perfis(norm):
        saida = shell.enviar(cmd)
        log.append(f'{cmd}\n{saida}')
        recusa = detectar_erro_cli(saida)
        if recusa:
            # Sai do modo de edição do perfil para a sessão não ficar presa nele.
            shell.enviar('quit')
            return 'erro', '\n'.join(log), f'{cmd}: {recusa}'
    return 'sucesso', '\n'.join(log), 'perfis criados'


# ─── Execução ────────────────────────────────────────────────────────────────

_PROMPT_FIM_RE = re.compile(r'(?:^|\n)[^\n]*?[A-Za-z0-9_.\-]+(?:\([^)\n]*\))?[#>]\s*$')
_CONTINUA_RE = re.compile(r'\{\s*<cr>[^}]*\}\s*:\s*$')
_MORE_RE = re.compile(r'-+\s*More\s*\(?[^\n]*$', re.IGNORECASE)
_ONT_ID_RESP_RE = re.compile(r'ONTID\s*:\s*(\d+)', re.IGNORECASE)
_ONT_ID_INFO_RE = re.compile(r'ONT-ID\s*:\s*(\d+)', re.IGNORECASE)


class ShellOlt:
    """Shell interativo numa OLT Huawei que lê até o PROMPT voltar, em vez
    do silêncio de 3 s do backup — 30 ONUs × 5 comandos esperando 3 s cada
    seriam 7 minutos parados.

    Responde sozinho o `{ <cr>|... }:` (parâmetro opcional) e o `---- More`.
    """

    def __init__(self, client):
        from .views import limpar_ansi, _ler_ate_silencio
        self._limpar = limpar_ansi
        self.channel = client.invoke_shell(term='vt100', width=10000, height=50)
        self.channel.settimeout(60)
        time.sleep(1.5)
        _ler_ate_silencio(self.channel, silencio=1.5, max_wait=15)

    def enviar(self, comando, max_wait=60):
        self.channel.send(comando + '\n')
        bruto, inicio = '', time.time()
        while time.time() - inicio < max_wait:
            if self.channel.recv_ready():
                bruto += self.channel.recv(65536).decode('utf-8', errors='replace')
                cauda = bruto[-300:]
                if _CONTINUA_RE.search(cauda):
                    self.channel.send('\n')
                    bruto = _CONTINUA_RE.sub('', bruto)
                    continue
                if _MORE_RE.search(cauda):
                    self.channel.send(' ')
                    continue
                if _PROMPT_FIM_RE.search(cauda):
                    # Prompt pode chegar antes do resto (alarme assíncrono);
                    # um respiro curto pega a cauda.
                    time.sleep(0.15)
                    if not self.channel.recv_ready():
                        break
            else:
                time.sleep(0.05)
        texto = self._limpar(bruto)
        linhas = texto.split('\n')
        if linhas and comando.strip() and comando.strip() in linhas[0]:
            linhas = linhas[1:]
        return '\n'.join(linhas).strip('\n')

    def fechar(self):
        """Logout de verdade: `quit` até a OLT encerrar a sessão.

        Um `quit` só, no modo config, só volta um nível — e fechar o canal SSH
        assim deixa a VTY presa na MA5800 até o idle-timeout. Em 29/10/2026 os
        testes do dia encheram as VTYs da SENTO SE e toda conexão nova passou
        a receber `ChannelException(4, 'Resource shortage')`.
        """
        try:
            for _ in range(5):
                if self.channel.closed or self.channel.exit_status_ready():
                    break
                self.channel.send('quit\n')
                time.sleep(0.6)
                saida = ''
                while self.channel.recv_ready():
                    saida += self.channel.recv(65536).decode('utf-8', errors='replace')
                if re.search(r'\(y/n\)', saida, re.IGNORECASE):
                    self.channel.send('y\n')
                    time.sleep(0.6)
        except Exception:
            pass
        finally:
            try:
                self.channel.close()
            except Exception:
                pass


def mensagem_de_falha(erro):
    """Texto para o operador a partir da exceção de conexão."""
    texto = str(erro)
    if 'Resource shortage' in texto:
        return ('A OLT aceitou o login mas recusou abrir a sessão (Resource shortage): '
                'as VTYs estão todas ocupadas. Feche sessões esquecidas (terminal, outros '
                'usuários) ou aguarde o idle-timeout da OLT e tente de novo.')
    return texto


def _id_da_resposta(shell, it, resposta):
    m = _ONT_ID_RESP_RE.search(resposta)
    if m:
        return int(m.group(1)), ''
    # Firmware que não ecoa o ID: pergunta pela SN. Ainda dentro da
    # interface gpon, onde o `display ont info by-sn` também existe.
    cmd = f'display ont info by-sn {it["sn"]}'
    saida = shell.enviar(cmd)
    m = _ONT_ID_INFO_RE.search(saida)
    return (int(m.group(1)) if m else None), f'\n{cmd}\n{saida}'


def provisionar(acesso, itens):
    """Gerador de eventos `(tipo, payload)` para o SSE do painel.

    Uma sessão para o lote inteiro. Antes de qualquer `ont add` relê o
    autofind: ONU que já foi provisionada (por outra pessoa, ou na aba
    esquecida aberta desde cedo) sai do lote em vez de virar erro.

    Uma ONU que falha não para o lote. A que entrou mas falhou no
    service-port é marcada `parcial`, com o ID, porque está cadastrada sem
    serviço — é o caso que o operador precisa corrigir à mão.
    """
    from .olt_pon import conectar, desconectar

    client = tunel = shell = None
    try:
        yield 'info', {'msg': f'Conectando a {acesso.host}...'}
        client, tunel = conectar(acesso)
        shell = ShellOlt(client)
        for cmd in PREAMBULO:
            shell.enviar(cmd)
        yield 'info', {'msg': 'Conectado. Relendo as ONUs pendentes (display ont autofind all)...'}

        pendentes = {o['sn']: o for o in parse_autofind(shell.enviar('display ont autofind all', max_wait=90))}

        total = len(itens)
        for idx, it in enumerate(itens, 1):
            base = {'idx': idx, 'total': total, 'sn': it['sn']}
            achada = pendentes.get(it['sn'])
            if achada is None:
                yield 'onu', {**base, 'status': 'ignorada',
                              'msg': 'não está mais pendente no autofind (já provisionada?)'}
                continue
            if (achada['slot'], achada['porta']) != (it['slot'], it['porta']):
                yield 'onu', {**base, 'status': 'ignorada',
                              'msg': f'está na PON {achada["fsp"]}, não em {it["slot"]}/{it["porta"]}'}
                continue

            yield 'progresso', {**base, 'msg': f'{it["sn"]} → {it["slot"]}/{it["porta"]} VLAN {it["vlan"]}'}
            log = []

            def _env(cmd):
                saida = shell.enviar(cmd)
                log.append(f'{cmd}\n{saida}')
                return saida

            _env(f'interface gpon {it["slot"]}')
            resposta = _env(comando_ont_add(it))
            recusa = detectar_erro_cli(resposta)
            ont_id = None
            if not recusa:
                ont_id, extra = _id_da_resposta(shell, it, resposta)
                if extra:
                    log.append(extra.strip())
            if recusa or ont_id is None:
                _env('quit')
                yield 'onu', {**base, 'status': 'erro', 'comandos': '\n'.join(log),
                              'msg': recusa or 'a OLT não devolveu o ONT-ID do ont add'}
                continue

            dentro, fora = comandos_pos_add(it, ont_id)
            falhas = []
            for cmd in dentro:
                falha = detectar_erro_cli(_env(cmd))
                if falha:
                    falhas.append(falha)
            _env('quit')
            for cmd in fora:
                falha = detectar_erro_cli(_env(cmd))
                if falha:
                    falhas.append(falha)

            yield 'onu', {**base, 'ont_id': ont_id, 'comandos': '\n'.join(log),
                          'status': 'parcial' if falhas else 'sucesso',
                          'msg': ('; '.join(falhas) + f' — ONT {ont_id} ficou cadastrada, confira o serviço')
                          if falhas else f'ONT {ont_id} provisionada'}
    except Exception as e:
        logger.exception(f'Provisionamento de ONU em {acesso}')
        yield 'erro', {'erro': mensagem_de_falha(e)}
    finally:
        if shell:
            shell.fechar()
        desconectar(client, tunel)
