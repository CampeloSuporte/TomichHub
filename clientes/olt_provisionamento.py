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
    # Uma ou mais VLANs tagueadas passando direto (user-vlan = VLAN da OLT,
    # `tag-transform transparent`), uma service-port por VLAN do line profile.
    'transparente': {'label': 'Passagem de VLANs (transparent, 1 ou mais)', 'native_vlan': False},
}

# Modos cujo service-port sai de `servicos` (um por GEM/VLAN do line profile).
MODOS_SERVICOS = ('translate', 'transparente')

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
# `port vlan eth 1 translation 3087 user-vlan 3087` ou `port vlan eth 1 128`
# (tag fixa sem translation). `transparent` não tem VLAN.
_SRV_VLAN_RE = re.compile(r'^\s*port vlan eth \d+ (?:translation )?(\d+)\b')
_INTERFACE_GPON_RE = re.compile(r'^\s*interface gpon (\d+/\d+)\s*$')
_ONT_ADD_RE = re.compile(
    r'^\s*ont add (\d+) (\d+) sn-auth "?([0-9A-Fa-f]{16})"?.*?'
    r'ont-lineprofile-id (\d+) ont-srvprofile-id (\d+)')
_NATIVE_RE = re.compile(r'^\s*ont port native-vlan (\d+) (\d+) eth \d+ vlan (\d+)')
_SERVICE_PORT_RE = re.compile(
    r'^\s*service-port \d+ vlan (\d+) gpon (\d+/\d+)/(\d+) ont (\d+) gemport (\d+)'
    r'(?:.*?\buser-vlan (\d+))?(?:.*?\btag-transform (\w+))?')
_DBA_RE = re.compile(r'^\s*dba-profile add profile-id (\d+) profile-name "([^"]*)"')
_VLAN_RE = re.compile(r'^\s*vlan (\d+)(?: to (\d+))? ')
# `port vlan 300 to 330 0/8 0` (VLANs na uplink). Só no nível de config: o
# `port vlan eth 1 translation ...` do srv profile tem outro formato.
_PORT_VLAN_RE = re.compile(r'^\s?port vlan (\d+)(?: to (\d+))? (\d+/\d+) (\d+)\s*$')


def parse_backup(conteudo):
    """Tudo que o provisionamento precisa saber do backup, sem conectar.

    `onts[(slot, porta)]` = {ont_id: {...}} junta as três linhas que formam
    um assinante (ont add + native-vlan + service-port).
    """
    lines, srvs, vlans, dbas, uplinks = {}, {}, set(), {}, {}
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
                'mapas': [], 'texto': [linha.strip()]})
            continue
        m = _SRV_RE.match(linha)
        if m:
            perfil_atual = srvs.setdefault(int(m.group(1)), {
                'id': int(m.group(1)), 'nome': m.group(2).strip(), 'vlans': [],
                'texto': [linha.strip()]})
            continue
        if perfil_atual is not None:
            # O bloco como está no backup: é o que a 🔍 do provisionamento mostra.
            if linha.strip():
                perfil_atual['texto'].append('  ' + linha.strip())
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

        m = _PORT_VLAN_RE.match(linha)
        if m:
            ini = int(m.group(1))
            fim = int(m.group(2) or ini)
            uplinks.setdefault((m.group(3), int(m.group(4))), set()).update(range(ini, fim + 1))
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
                    'native_vlan': None, 'vlan': None, 'gemport': None, 'user_vlan': None,
                    'tag': ''}
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
                ont['user_vlan'] = int(m.group(6)) if m.group(6) else None
                ont['tag'] = m.group(7) or ''

    return {'lines': lines, 'srvs': srvs, 'vlans': vlans, 'onts': onts, 'sns': sns, 'dbas': dbas,
            'uplinks': uplinks}


def uplinks_do_backup(backup):
    """[{'porta': '0/8 0', 'vlans': N}] das portas que recebem `port vlan`,
    a que carrega mais VLANs primeiro. `padrao` marca as que levam pelo menos
    metade do que a principal leva: é onde uma VLAN nova normalmente passa
    (0/8 0 na SENTO SE; 0/8 0 e 0/8 1 na DS TECH, que divide a carga)."""
    itens = sorted(backup.get('uplinks', {}).items(), key=lambda kv: (-len(kv[1]), kv[0]))
    topo = len(itens[0][1]) if itens else 0
    return [{'porta': f'{slot} {porta}', 'vlans': len(vs), 'padrao': len(vs) * 2 >= topo}
            for (slot, porta), vs in itens]


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
    # Translate de verdade: o service-port troca a VLAN (user-vlan 1 → vlan
    # 101, modelo "VLAN fixa" da DKY). Nas PONs da SENTO SE user-vlan == vlan.
    traduz = sum(1 for o in na_pon if o.get('user_vlan') not in (None, o['vlan']))
    transparente = sum(1 for o in na_pon if o.get('tag') == 'transparent')

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
        'modo': ('transparente' if na_pon and transparente * 2 > len(na_pon) else
                 'translate' if na_pon and traduz * 2 > len(na_pon) else
                 'ont' if na_pon and com_native * 2 < len(na_pon) else 'onu'),
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
    vlan = None if modo in MODOS_SERVICOS else _inteiro(item.get('vlan'), f'{sn}: VLAN', 1, 4094)
    line = _inteiro(item.get('line'), f'{sn}: line-profile', 0, 8192)
    srv = _inteiro(item.get('srv'), f'{sn}: srv-profile', 0, 8192)
    gemport = _inteiro(item.get('gemport') or 1, f'{sn}: gemport', 0, 1023)

    avisos = []
    if line not in backup['lines']:
        avisos.append(f'{sn}: line-profile {line} não está no backup.')
    elif modo not in MODOS_SERVICOS and backup['lines'][line]['gems'] and gemport not in backup['lines'][line]['gems']:
        avisos.append(f'{sn}: o line-profile {line} não tem o gemport {gemport} '
                      f'(tem {", ".join(map(str, backup["lines"][line]["gems"]))}).')
    if srv not in backup['srvs']:
        avisos.append(f'{sn}: srv-profile {srv} não está no backup.')
    if vlan is not None and backup['vlans'] and vlan not in backup['vlans']:
        avisos.append(f'{sn}: VLAN {vlan} não está criada no backup.')
    if sn in backup['sns']:
        avisos.append(f'{sn}: já aparece no backup em {backup["sns"][sn]} — a OLT vai recusar se ainda estiver lá.')

    servicos = []
    if modo in MODOS_SERVICOS:
        brutos = item.get('servicos')
        if not isinstance(brutos, list) or not brutos:
            raise ProvisionamentoInvalido(f'{sn}: no modo {modo} marque pelo menos uma VLAN do line profile.')
        if len(brutos) > MAX_GEMS_PERFIL:
            raise ProvisionamentoInvalido(f'{sn}: no máximo {MAX_GEMS_PERFIL} service-ports por ONU.')
        gems_line = backup['lines'].get(line, {}).get('gems') or []
        for sv in brutos:
            if not isinstance(sv, dict):
                raise ProvisionamentoInvalido(f'{sn}: serviço inválido.')
            g = _inteiro(sv.get('gem'), f'{sn}: GEM', 0, MAX_GEM_INDEX)
            uv = _inteiro(sv.get('user_vlan'), f'{sn}: user-vlan do GEM {g}', 1, 4094)
            # Transparente: a VLAN passa como está — a da OLT é a própria user-vlan.
            vo = uv if modo == 'transparente' else _inteiro(
                sv.get('vlan'), f'{sn}: VLAN da OLT do GEM {g}', 1, 4094)
            servicos.append({'gem': g, 'user_vlan': uv, 'vlan': vo})
            if gems_line and g not in gems_line:
                avisos.append(f'{sn}: o line-profile {line} não tem o GEM {g}.')
            if backup['vlans'] and servicos[-1]['vlan'] not in backup['vlans']:
                avisos.append(f'{sn}: VLAN {servicos[-1]["vlan"]} não está criada no backup.')
        # Um GEM pode levar várias VLANs (gem mapping 1 1 vlan 3998 / 1 2 vlan 3084).
        if len({(sv['gem'], sv['user_vlan']) for sv in servicos}) != len(servicos):
            raise ProvisionamentoInvalido(f'{sn}: VLAN repetida no mesmo GEM.')
        vlan = servicos[0]['vlan']

    return {'sn': sn, 'slot': slot, 'porta': porta, 'vlan': vlan, 'line': line,
            'srv': srv, 'gemport': gemport, 'modo': modo, 'servicos': servicos,
            'desc': _desc_limpa(item.get('desc'))}, avisos


ONT_ID = '{ONT_ID}'

_PORTA_UPLINK_RE = re.compile(r'^(\d+/\d+)\s+(\d+)$')


def validar_uplink(spec, backup, vlans_padrao=()):
    """{criar, portas: ["0/8 0", ...], vlans: [...]} → (norm|None, avisos).

    Toda VLAN que um script pede precisa existir na OLT (`vlan V smart`) e
    passar na uplink (`port vlan V 0/8 0`); sem isso o service-port até entra,
    mas o tráfego não sai da OLT. As portas vêm do backup
    (`uplinks_do_backup`); uma porta fora dele entra com aviso.
    """
    spec = spec if isinstance(spec, dict) else {}
    if not spec.get('criar'):
        return None, []
    brutas = spec.get('vlans') if spec.get('vlans') not in (None, []) else list(vlans_padrao)
    vlans = []
    for v in brutas or []:
        v = _inteiro(v, 'VLAN da uplink', 1, 4094)
        if v not in vlans:
            vlans.append(v)
    if not vlans:
        raise ProvisionamentoInvalido('Informe as VLANs que vão para a uplink.')
    portas, avisos = [], []
    for p in spec.get('portas') or []:
        m = _PORTA_UPLINK_RE.match(str(p).strip())
        if not m:
            raise ProvisionamentoInvalido(f'Porta de uplink inválida: "{p}" (use frame/slot porta, ex. 0/8 0).')
        chave = (m.group(1), int(m.group(2)))
        if chave not in portas:
            portas.append(chave)
        if backup.get('uplinks') and chave not in backup['uplinks']:
            avisos.append(f'A porta {chave[0]} {chave[1]} não recebe nenhuma VLAN no backup — confira se é uplink.')
    if not portas:
        raise ProvisionamentoInvalido('Escolha pelo menos uma porta de uplink.')
    return {'vlans': vlans, 'portas': portas}, avisos


def comandos_vlans_uplink(up, backup):
    """Só o que falta pelo backup: `vlan V smart` para VLAN que não existe e
    `port vlan V F/S P` para porta que ainda não leva a VLAN."""
    if not up:
        return []
    cmds = [f'vlan {v} smart' for v in up['vlans'] if v not in backup.get('vlans', set())]
    for v in up['vlans']:
        for slot, porta in up['portas']:
            if v not in backup.get('uplinks', {}).get((slot, porta), set()):
                cmds.append(f'port vlan {v} {slot} {porta}')
    return cmds


# "Já existe" não é falha para VLAN/uplink: o backup pode estar velho, e o
# objetivo (a VLAN existir e passar na porta) já foi cumprido.
_JA_EXISTE_RE = re.compile(r'already|exist', re.IGNORECASE)


def falha_vlan_uplink(saida):
    recusa = detectar_erro_cli(saida)
    return '' if (recusa and _JA_EXISTE_RE.search(saida)) else recusa


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
    tag = 'transparent' if it['modo'] == 'transparente' else 'translate'
    fora = [f'service-port vlan {sv["vlan"]} gpon {it["slot"]}/{it["porta"]} ont {ont_id} '
            f'gemport {sv["gem"]} multi-service user-vlan {sv["user_vlan"]} tag-transform {tag}'
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
# DBA profiles 0–9 são os padrão do MA5800: não aparecem no backup (o line
# profile 0 da SENTO SE usa os DBA 0 e 2), mas existem. Criar começa no 10.
DBA_PRIMEIRO_LIVRE = 10
DBA_TIPOS = {
    'type1': ('fix',),
    'type2': ('assure',),
    'type3': ('assure', 'max'),
    'type4': ('max',),
}
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


# Modelos de perfil, ambos vindos de config real passada pelo operador:
# - translate: várias VLANs, um GEM por VLAN, srv "eth adaptive" + "native-vlan
#   unconcern" (a tradução fica no service-port);
# - vlan_fixa: a ONU sai com UMA VLAN setada nas portas (ex.: VLAN 1), srv com
#   `port vlan eth N translation V user-vlan V`, GEM 1 com encrypt (perfil
#   "SRV_DKY_100"/"VLAN_1" da OLT da DKY). O service-port traduz user-vlan 1 para
#   a VLAN da OLT (ex.: 101), igual ao modo translate do provisionamento.
# - multi_vlan: duas ou mais VLANs tagueadas passando pela ONU/ONT num GEM só
#   (perfil "FULLCAM": `port vlan eth 1 translation V user-vlan V` por VLAN e
#   `gem mapping 1 <idx> vlan V`), provisionado no modo transparente.
# - roteada: ONT roteadora (PPPoE/WAN configurada na própria ONT ou por TR-069):
#   o srv só declara as portas (perfil "THINK": `ont-port eth 4 catv 1`) e a
#   VLAN fica só no line (perfil "PPPOE": `gem mapping 1 0 vlan 128`).
#
# vlan_fixa e roteada aceitam as portas da ONU por tipo (`portas`: pots/eth/
# catv, cada uma "adaptive", "adaptive 8" ou "4"), `ring check enable`, o GEM e
# o índice do mapping, e os extras do line profile — os exemplos reais:
#   ARCH (1 GE + CATV):  ont-port eth 1 catv adaptive 8 / ring check enable;
#                        line com fec-upstream, tr069-management, qos-mode
#                        gem-car, mapping-mode priority, gem mapping 1 1 vlan 3087
#   ID = VLAN (110):     ont-port pots adaptive 2 eth adaptive 4, port vlan eth
#                        1..4, tcont 5 + gem 5 (gem mapping 5 0 vlan 110), DBA 300
MODELOS_PERFIL = ('translate', 'vlan_fixa', 'multi_vlan', 'roteada')

# Ordem em que a CLI espera os tipos de porta no `ont-port`.
TIPOS_PORTA = ('pots', 'eth', 'catv')
_PORTA_QTD_RE = re.compile(r'^(?:adaptive(?: (\d{1,3}))?|(\d{1,3}))$')
MAPPING_MODES = ('priority', 'vlan-priority', 'port', 'port-vlan')


def _porta_spec(valor, tipo):
    """'' (sem a porta), 'adaptive', 'adaptive 8' ou '4' → texto normalizado."""
    texto = ' '.join(str(valor if valor is not None else '').lower().split())
    if not texto:
        return ''
    m = _PORTA_QTD_RE.match(texto)
    n = m and (m.group(1) or m.group(2))
    if not m or (n is not None and not 0 < int(n) <= 64):
        raise ProvisionamentoInvalido(
            f'Portas {tipo.upper()} inválidas: "{texto}" (use adaptive, adaptive 8 ou um número).')
    return texto


def _max_portas(spec):
    """Quantas portas o `ont-port` declara (None = adaptive sem teto)."""
    m = _PORTA_QTD_RE.match(spec or '')
    if not m:
        return 0
    n = m.group(1) or m.group(2)
    return int(n) if n else None


def ont_port(portas):
    partes = [f'{t} {portas[t]}' for t in TIPOS_PORTA if portas.get(t)]
    return 'ont-port ' + ' '.join(partes)

# O `gem mapping` tem índice 0–7: no máximo 8 VLANs num mesmo GEM. O modelo do
# operador começa no 1 (1, 2, ...), então o 0 fica para a 8ª.
MAX_VLANS_GEM = 8
_INDICES_MAPPING = [1, 2, 3, 4, 5, 6, 7, 0]


def validar_perfis(spec, backup):
    """Normaliza o pedido de criação. Devolve (spec, avisos).

    Recusa ID que já existe no backup: entrar em `ont-lineprofile gpon
    profile-id 300` EDITA o perfil existente, e um `gem add` ali muda as 96
    ONTs ligadas nele. Como o backup pode estar velho, a execução ainda confere
    ao vivo (`checagens_perfis`) antes de criar.
    """
    if not isinstance(spec, dict):
        raise ProvisionamentoInvalido('Pedido inválido.')
    srv, line, dba = spec.get('srv') or {}, spec.get('line') or {}, spec.get('dba') or {}
    criar_srv, criar_line, criar_dba = bool(srv.get('criar')), bool(line.get('criar')), bool(dba.get('criar'))
    if not (criar_srv or criar_line or criar_dba):
        raise ProvisionamentoInvalido('Marque o que criar: DBA, srv profile e/ou line profile.')

    def _nome(valor, tipo):
        nome = str(valor or '').strip()
        if not _NOME_PERFIL_RE.match(nome):
            raise ProvisionamentoInvalido(
                f'Nome do {tipo} inválido: "{nome}" (até 32 letras, números, espaço, _ . -).')
        return nome

    modelo = spec.get('modelo') or 'translate'
    if modelo not in MODELOS_PERFIL:
        raise ProvisionamentoInvalido(f'Modelo de perfil desconhecido: "{modelo}".')
    uma_vlan = modelo in ('vlan_fixa', 'roteada')
    vlan_fixa = (_inteiro(spec.get('vlan_fixa') or 1,
                          'VLAN setada na ONU' if modelo == 'vlan_fixa' else 'VLAN do line profile', 1, 4094)
                 if uma_vlan else None)
    avisos, norm = [], {'srv': None, 'line': None, 'dba': None, 'modelo': modelo}
    if criar_dba:
        did = _inteiro(dba.get('id'), 'ID do DBA profile', DBA_PRIMEIRO_LIVRE, 1024)
        if did in backup.get('dbas', {}):
            raise ProvisionamentoInvalido(
                f'O DBA profile {did} já existe ("{backup["dbas"][did]}") — escolha-o na lista ou outro ID.')
        tipo = dba.get('tipo') or 'type4'
        if tipo not in DBA_TIPOS:
            raise ProvisionamentoInvalido(f'Tipo de DBA desconhecido: "{tipo}".')
        banda = {k: _inteiro(dba.get(k), f'DBA {k} (kbps)', 128, 10_000_000) for k in DBA_TIPOS[tipo]}
        if tipo == 'type3' and banda['max'] < banda['assure']:
            raise ProvisionamentoInvalido('No DBA type3 o max precisa ser maior ou igual ao assure.')
        norm['dba'] = {'id': did, 'nome': _nome(dba.get('nome'), 'DBA profile'), 'tipo': tipo, **banda}
    if criar_srv:
        sid = _inteiro(srv.get('id'), 'ID do srv profile', 0, 8192)
        if sid in backup['srvs']:
            raise ProvisionamentoInvalido(
                f'O srv profile {sid} já existe ("{backup["srvs"][sid]["nome"]}") — escolha outro ID.')
        norm['srv'] = {'id': sid, 'nome': _nome(srv.get('nome'), 'srv profile'),
                       'eth': _inteiro(srv.get('eth') or 8, 'portas ETH adaptive', 1, 24)}
        if uma_vlan:
            if isinstance(srv.get('portas'), dict):
                portas = {t: _porta_spec(srv['portas'].get(t), t) for t in TIPOS_PORTA}
            else:
                # Pedido antigo (só números): pots/eth adaptive N.
                pots = _inteiro(srv.get('pots') if srv.get('pots') not in (None, '') else 32,
                                'POTS adaptive', 0, 32)
                portas = {'pots': f'adaptive {pots}' if pots else '', 'eth': f'adaptive {norm["srv"]["eth"]}',
                          'catv': ''}
            if not portas['eth']:
                raise ProvisionamentoInvalido('Informe as portas ETH da ONU (ex.: adaptive 4 ou 1).')
            norm['srv'].update({'portas': portas,
                                'ring_check': bool(srv.get('ring_check'))})
        if modelo == 'vlan_fixa':
            eth_vlan = _inteiro(srv.get('eth_vlan') or 4, 'portas ETH com a VLAN', 1, 8)
            teto = _max_portas(norm['srv']['portas']['eth'])
            if teto is not None and eth_vlan > teto:
                raise ProvisionamentoInvalido(
                    f'{eth_vlan} portas ETH com a VLAN, mas a ONU declara só {teto} ETH.')
            norm['srv'].update({'vlan': vlan_fixa, 'eth_vlan': eth_vlan})
    if criar_line:
        lid = _inteiro(line.get('id'), 'ID do line profile', 0, 8192)
        if lid in backup['lines']:
            raise ProvisionamentoInvalido(
                f'O line profile {lid} já existe ("{backup["lines"][lid]["nome"]}") — escolha outro ID.')
        vlans_brutas = [vlan_fixa] if vlan_fixa is not None else spec.get('vlans')
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
        if modelo == 'multi_vlan' and len(vlans) > MAX_VLANS_GEM:
            raise ProvisionamentoInvalido(
                f'{len(vlans)} VLANs num GEM só — o gem mapping vai até {MAX_VLANS_GEM} (índices 0–7).')
        if norm['dba'] and str(line.get('dba')) in ('novo', ''):
            dba_id = norm['dba']['id']
        else:
            dba_id = _inteiro(line.get('dba'), 'DBA profile', 0, 1024)
            if dba_id >= DBA_PRIMEIRO_LIVRE and dba_id not in backup.get('dbas', {}) and not (
                    norm['dba'] and norm['dba']['id'] == dba_id):
                avisos.append(f'DBA profile {dba_id} não está no backup.')
        gem_unico = (_inteiro(line.get('gem') if line.get('gem') not in (None, '') else 1, 'GEM', 0, MAX_GEM_INDEX)
                     if uma_vlan else None)
        norm['line'] = {'id': lid, 'nome': _nome(line.get('nome'), 'line profile'),
                        'tcont': _inteiro(line.get('tcont') or (4 if modelo == 'vlan_fixa' else 1), 'T-CONT', 1, 7),
                        'dba': dba_id,
                        # VLAN fixa: GEM 1, como no perfil "VLAN_1" (gem 1 ↔ vlan 1).
                        # Multi-VLAN: todas no GEM 1 (perfil "FULLCAM").
                        'gems': ([(gem_unico, vlan_fixa)] if uma_vlan else
                                 [(1, v) for v in vlans] if modelo == 'multi_vlan' else
                                 _alocar_gems(vlans, spec.get('gem_igual_vlan', True) is not False))}
        if modelo == 'multi_vlan':
            norm['line']['tcont'] = _inteiro(line.get('tcont') or 4, 'T-CONT', 1, 7)
        if uma_vlan:
            mm = str(line.get('mapping_mode') or '').strip()
            if mm and mm not in MAPPING_MODES:
                raise ProvisionamentoInvalido(f'mapping-mode desconhecido: "{mm}".')
            norm['line'].update({
                # mapping-index 0 nos perfis "VLAN_1"/"PPPOE"/110; 1 no ARCH.
                'mapping_idx': _inteiro(line.get('mapping_idx') if line.get('mapping_idx') not in (None, '') else 0,
                                        'índice do gem mapping', 0, 7),
                'fec': bool(line.get('fec')), 'tr069': bool(line.get('tr069')),
                'gem_car': bool(line.get('gem_car')), 'mapping_mode': mm,
            })
    if modelo == 'multi_vlan' and norm['srv']:
        vlans_srv = []
        for v in spec.get('vlans') or []:
            v = _inteiro(v, 'VLAN', 1, 4094)
            if v not in vlans_srv:
                vlans_srv.append(v)
        if not vlans_srv:
            raise ProvisionamentoInvalido('Adicione pelo menos uma VLAN.')
        if len(vlans_srv) > MAX_VLANS_GEM:
            raise ProvisionamentoInvalido(f'No máximo {MAX_VLANS_GEM} VLANs neste modelo.')
        norm['srv'].update({'vlans': vlans_srv,
                            'eth_vlan': _inteiro(srv.get('eth_vlan') or 1, 'portas ETH com as VLANs', 1, 8)})

    # VLANs na OLT + uplink. Padrão: as VLANs que de fato saem da OLT com a
    # mesma tag — as do perfil no multi-VLAN; no translate e na VLAN fixa a
    # VLAN da OLT é outra (escolhida no service-port), então vem do pedido.
    padrao = ([v for _, v in (norm['line'] or {}).get('gems', [])] if modelo == 'multi_vlan' else
              [vlan_fixa] if modelo == 'roteada' else [])
    norm['uplink'], av = validar_uplink(spec.get('uplink'), backup, padrao)
    avisos += av
    return norm, avisos


def comandos_perfis(norm):
    """Os blocos de configuração, no formato do modelo do operador."""
    cmds = []
    if norm.get('dba'):
        d = norm['dba']
        banda = ' '.join(f'{k} {d[k]}' for k in DBA_TIPOS[d['tipo']])
        # Formato do backup: dba-profile add profile-id 10 profile-name "DBA_PROFILE" type4 max 1000000
        cmds.append(f'dba-profile add profile-id {d["id"]} profile-name "{d["nome"]}" {d["tipo"]} {banda}')
    fixa = norm.get('modelo') == 'vlan_fixa'
    multi = norm.get('modelo') == 'multi_vlan'
    roteada = norm.get('modelo') == 'roteada'
    if norm['srv']:
        s = norm['srv']
        cmds.append(f'ont-srvprofile gpon profile-id {s["id"]} profile-name "{s["nome"]}"')
        if multi:
            cmds.append('ont-port pots adaptive eth adaptive')
            cmds += [f'port vlan eth {n} translation {v} user-vlan {v}'
                     for v in s['vlans'] for n in range(1, s['eth_vlan'] + 1)]
        elif fixa or roteada:
            cmds.append(ont_port(s['portas']))
            if s.get('ring_check'):
                cmds.append('ring check enable')
            if fixa:
                cmds += [f'port vlan eth {n} translation {s["vlan"]} user-vlan {s["vlan"]}'
                         for n in range(1, s['eth_vlan'] + 1)]
        else:
            cmds += [f'ont-port eth adaptive {s["eth"]}', 'native-vlan unconcern']
        cmds += ['commit', 'quit']
    if norm['line']:
        ln = norm['line']
        cmds.append(f'ont-lineprofile gpon profile-id {ln["id"]} profile-name "{ln["nome"]}"')
        if fixa or roteada:
            # Mesma ordem do backup do perfil ARCH.
            if ln.get('fec'):
                cmds.append('fec-upstream enable')
            cmds.append('omcc encrypt on')
            if ln.get('tr069'):
                cmds.append('tr069-management enable')
            if ln.get('gem_car'):
                cmds.append('qos-mode gem-car')
            if ln.get('mapping_mode'):
                cmds.append(f'mapping-mode {ln["mapping_mode"]}')
        cmds.append(f'tcont {ln["tcont"]} dba-profile-id {ln["dba"]}')
        cifra = ' encrypt on' if fixa or roteada else ''
        gems_unicos = list(dict.fromkeys(g for g, _ in ln['gems']))
        cmds += [f'gem add {g} eth tcont {ln["tcont"]}{cifra}' for g in gems_unicos]
        if multi:
            # Todas no GEM 1, um índice de mapping por VLAN (1, 2, ...).
            cmds += [f'gem mapping {g} {_INDICES_MAPPING[k]} vlan {v}' for k, (g, v) in enumerate(ln['gems'])]
        else:
            # mapping-index 0 no modelo VLAN fixa ("gem mapping 1 0 vlan 1"), 1 no translate.
            idx = ln.get('mapping_idx', 0) if fixa or roteada else 1
            cmds += [f'gem mapping {g} {idx} vlan {v}' for g, v in ln['gems']]
        cmds += ['commit', 'quit']
    return cmds


def checagens_perfis(norm):
    """Leituras feitas antes de criar: o perfil não pode existir na OLT."""
    out = []
    if norm.get('dba'):
        out.append(f'display dba-profile profile-id {norm["dba"]["id"]}')
    if norm['srv']:
        out.append(f'display ont-srvprofile gpon profile-id {norm["srv"]["id"]}')
    if norm['line']:
        out.append(f'display ont-lineprofile gpon profile-id {norm["line"]["id"]}')
    return out


def perfil_existe(saida):
    """True/False pela resposta do `display ont-*profile` (conferida na
    SENTO SE: "Failure: The line profile does not exist" / "Profile-ID :300").
    None quando a saída não diz nem uma coisa nem outra: aí nada é criado."""
    if re.search(r'does not exist', saida or '', re.IGNORECASE):
        return False
    if re.search(r'Profile-ID\s*:\s*\d+', saida or '', re.IGNORECASE):
        return True
    return None


def criar_perfis(shell, norm, backup=None):
    """Confere e cria numa sessão já em (config)#. Devolve (status, log, msg).
    As VLANs/uplink vão por último: só depois de os perfis existirem."""
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
    for cmd in comandos_vlans_uplink(norm.get('uplink'), backup or {}):
        saida = shell.enviar(cmd)
        log.append(f'{cmd}\n{saida}')
        recusa = falha_vlan_uplink(saida)
        if recusa:
            return 'erro', '\n'.join(log), f'perfis criados, mas {cmd}: {recusa}'
    return 'sucesso', '\n'.join(log), 'perfis criados'


# ─── Service-ports criados e validação de MAC ────────────────────────────────

# Linha do `display service-port port F/S/P ont N` (lida na SENTO SE):
#     3378  300 common   gpon 0/1 /0  76   1     vlan  300        -    -    up
# INDEX VLAN ATTR PORT-TYPE F/S/P (com espaço antes da barra) VPI=ONT VCI=GEM
_SP_LINHA_RE = re.compile(
    r'^\s*(\d+)\s+(\d+)\s+\S+\s+gpon\s+(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\s+(\d+)\s+(\d+)\b', re.MULTILINE)
_MAC_RE = re.compile(r'\b([0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4})\b', re.IGNORECASE)
_MAC_TIPO_RE = re.compile(r'\b(dynamic|static|bound|learning)\b', re.IGNORECASE)


def comando_listar_service_ports(it, ont_id):
    return f'display service-port port {it["slot"]}/{it["porta"]} ont {ont_id}'


def parse_service_ports(saida):
    return [{'index': int(m.group(1)), 'vlan': int(m.group(2)),
             'fsp': f'{m.group(3)}/{m.group(4)}/{m.group(5)}',
             'ont': int(m.group(6)), 'gem': int(m.group(7))}
            for m in _SP_LINHA_RE.finditer(saida or '')]


def comando_mac(sp):
    """Modelo passado pelo operador: `mac-address vlan 301 | include 206`
    (206 = índice do service-port), no `display` do modo config."""
    return f'display mac-address vlan {int(sp["vlan"])} | include {int(sp["index"])}'


def service_ports_com_mac(sps):
    return [{**sp, 'cmd_mac': comando_mac(sp)} for sp in sps]


def parse_macs(saida, index):
    """MACs aprendidos no service-port `index`. O `include 206` também casa
    "206" dentro de um MAC ou de outra coluna, então só vale a linha cujo
    PRIMEIRO número é o índice (coluna SRV-P INDEX)."""
    macs = []
    for linha in (saida or '').splitlines():
        campos = linha.split()
        if not campos or campos[0] != str(index):
            continue
        m = _MAC_RE.search(linha)
        if m:
            t = _MAC_TIPO_RE.search(linha)
            macs.append({'mac': m.group(1).lower(), 'tipo': t.group(1).lower() if t else '',
                         'linha': linha.strip()})
    return macs


# ─── Remover a ONU recém-provisionada ────────────────────────────────────────

_INFO_FSP_RE = re.compile(r'F/S/P\s*:\s*(\d+/\d+/\d+)')


def confere_ont(saida, sn, fsp, ont_id):
    """'' se o `display ont info by-sn` confirma que o SN está em `fsp` com
    esse ONT-ID; senão, o motivo. Remover pelo ID guardado sem conferir
    poderia apagar OUTRA ONT, se essa já tivesse saído e o ID fosse reusado."""
    if re.search(r'does not exist|not exist', saida or '', re.IGNORECASE):
        return f'{sn} não está mais cadastrada na OLT.'
    m_fsp, m_id = _INFO_FSP_RE.search(saida or ''), _ONT_ID_INFO_RE.search(saida or '')
    if not (m_fsp and m_id):
        return f'Não consegui confirmar onde está a {sn} (resposta inesperada da OLT).'
    if m_fsp.group(1) != fsp or int(m_id.group(1)) != int(ont_id):
        return (f'{sn} está em {m_fsp.group(1)} ONT {m_id.group(1)}, não em {fsp} ONT {ont_id} — '
                'nada foi removido.')
    return ''


def comandos_remover(slot, porta, ont_id, service_ports):
    """Mesma ordem usada ao vivo na SENTO SE (ONT 76): service-port antes,
    porque o `ont delete` recusa ONT com service-port ligado."""
    cmds = [f'undo service-port {int(sp["index"])}' for sp in service_ports]
    return cmds + [f'interface gpon {slot}', f'ont delete {int(porta)} {int(ont_id)}', 'quit']


def remover_onu(shell, sn, slot, porta, ont_id, service_ports):
    """Numa sessão em (config)#. Devolve (status, log, msg)."""
    log = []
    cmd = f'display ont info by-sn {sn}'
    saida = shell.enviar(cmd)
    log.append(f'{cmd}\n{saida}')
    motivo = confere_ont(saida, sn, f'{slot}/{porta}', ont_id)
    if motivo:
        return 'erro', '\n'.join(log), motivo
    for cmd in comandos_remover(slot, porta, ont_id, service_ports):
        saida = shell.enviar(cmd)
        log.append(f'{cmd}\n{saida}')
        recusa = detectar_erro_cli(saida)
        if recusa:
            if cmd.startswith('ont delete'):
                shell.enviar('quit')
            return 'erro', '\n'.join(log), f'{cmd}: {recusa}'
    return 'sucesso', '\n'.join(log), f'ONT {ont_id} removida de {slot}/{porta}'


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


def provisionar(acesso, itens, preparo=()):
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

        if preparo:
            yield 'info', {'msg': f'Criando VLANs e passando na uplink ({len(preparo)} comando(s))...'}
            for cmd in preparo:
                recusa = falha_vlan_uplink(shell.enviar(cmd))
                if recusa:
                    yield 'erro', {'erro': f'{cmd}: {recusa} — lote não iniciado.'}
                    return
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

            # Índices que a OLT deu aos service-ports: é por eles que a
            # validação de MAC filtra a tabela depois.
            sps = service_ports_com_mac(parse_service_ports(_env(comando_listar_service_ports(it, ont_id))))
            yield 'onu', {**base, 'ont_id': ont_id, 'comandos': '\n'.join(log), 'service_ports': sps,
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
