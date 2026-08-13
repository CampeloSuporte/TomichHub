"""
Clonagem de serviço L2VPN: monta a config de um VSI/VPLS/VPWS/L2VC novo a
partir de um existente (lido do backup por `clientes/l2vpn_parser.py`) e
aplica no equipamento.

Mesmo desenho da automação BGP (`clientes/bgp_actions.py`), de propósito:

- `comandos_clonar` só MONTA a lista de comandos — não toca em nada real,
  seguro de chamar pro preview que a UI mostra num textarea editável;
- `executar` é quem conecta e envia, reaproveitando a conexão Netmiko do
  Painel de Scripts (`script_views._conectar_script`);
- o que não tem sintaxe conhecida e conferida em config real deste ambiente
  é recusado com `L2vpnNaoSuportado` em vez de arriscar um edit errado num
  equipamento de produção.

Fabricantes suportados para CRIAR serviço: Huawei VRP (VSI e L2VC), Datacom
DmOS (VPWS e VPLS) e MikroTik RouterOS (VPLS). Cisco e Juniper são lidos
pelo parser mas ficam de fora aqui — não há uma única ocorrência real de
xconnect/l2circuit nos backups deste ambiente pra conferir a sintaxe gerada.
"""
import ipaddress
import logging
import re

from .script_views import _conectar_script, _fechar_tunel, DEVICE_TYPES

logger = logging.getLogger(__name__)

# Fabricantes com config candidata: precisam de `commit` pra valer. Huawei usa
# o commit do próprio Netmiko (conn.commit()); no DmOS o `commit` vai como
# linha de config mesmo — é o comando certo dentro do modo de configuração e
# evita o prompt de "uncommitted changes" na saída.
_PRECISA_COMMIT_NETMIKO = {'huawei'}
# RouterOS não tem "modo configuração": cada linha é um comando completo.
_COMANDO_A_COMANDO = {'mikrotik'}

VENDORS_SUPORTADOS = {'huawei', 'datacom', 'mikrotik'}

# Teto de sanidade do texto editado à mão no modal antes de ir pro equipamento.
MAX_LINHAS = 80
MAX_COLUNAS = 500


def eh_vsi_huawei(vendor, tipo):
    """VSI Huawei tem um fluxo próprio: o serviço é aplicado na `Vlanif` da
    VLAN designada (1092 dos 1128 bindings reais deste ambiente são em Vlanif),
    e a VLAN chega às portas físicas por fora do bloco do serviço."""
    return vendor == 'huawei' and (tipo or 'vpls') != 'l2vc'


class L2vpnNaoSuportado(Exception):
    """Combinação fabricante+tipo sem sintaxe conferida em config real, ou
    spec inválida (id em uso, VLAN fora de faixa, peer inválido…). Melhor
    recusar explicitamente do que mandar um comando torto pro equipamento."""


# ─── Validação da spec ────────────────────────────────────────────────────────

_NOME_OK = re.compile(r'^[A-Za-z0-9._@:-]{1,63}$')
_IFACE_OK = re.compile(r'^[A-Za-z0-9./_:-]{1,48}$')


def _int_na_faixa(valor, nome, minimo, maximo, obrigatorio=True):
    texto = str(valor or '').strip()
    if not texto:
        if obrigatorio:
            raise L2vpnNaoSuportado(f'{nome} é obrigatório.')
        return None
    if not texto.isdigit():
        raise L2vpnNaoSuportado(f'{nome} precisa ser numérico (recebido: "{texto}").')
    numero = int(texto)
    if not (minimo <= numero <= maximo):
        raise L2vpnNaoSuportado(f'{nome} fora da faixa {minimo}–{maximo} (recebido: {numero}).')
    return numero


def _normalizar_iface(nome, vendor):
    """No DmOS a interface é DECLARADA com espaço (`interface gigabit-ethernet
    1/1/1`) e REFERENCIADA com hífen (`access-interface gigabit-ethernet-1/1/1`).
    A lista do painel vem da declaração (é o que está no backup), então o que o
    operador escolhe precisa virar a forma de referência antes de virar comando.
    """
    nome = (nome or '').strip()
    if vendor == 'datacom':
        return re.sub(r'\s+', '-', nome)
    return nome


def _ip_valido(texto, rotulo):
    texto = str(texto or '').strip()
    try:
        ipaddress.IPv4Address(texto)
    except ValueError:
        raise L2vpnNaoSuportado(f'{rotulo} inválido: "{texto}".')
    return texto


def ids_em_uso(servicos):
    """Ids (vsi-id/pw-id/vc-id) já ocupados no equipamento, pra recusar
    colisão antes de mandar o comando — dois serviços com o mesmo id é erro
    de config que só aparece depois, quando o túnel não sobe."""
    usados = set()
    for servico in servicos:
        if str(servico.get('id') or '').isdigit():
            usados.add(int(servico['id']))
    return usados


def sugerir_id(servicos, base=None):
    """Primeiro id livre a partir do id de origem (ou do maior + 1)."""
    usados = ids_em_uso(servicos)
    if base and str(base).isdigit():
        candidato = int(base) + 1
    else:
        candidato = (max(usados) + 1) if usados else 100
    while candidato in usados and candidato < 65535:
        candidato += 1
    return candidato


def validar_spec(spec, servicos, origem):
    """Normaliza e valida o que veio do formulário de clonagem. Devolve a
    spec limpa; levanta `L2vpnNaoSuportado` com mensagem pronta pra UI."""
    vendor = (origem.get('vendor') or '').lower()
    if vendor not in VENDORS_SUPORTADOS:
        raise L2vpnNaoSuportado(
            f'Clonagem ainda não suportada para "{vendor or "desconhecido"}" — '
            'só Huawei (VSI/L2VC), Datacom (VPWS/VPLS) e MikroTik (VPLS).')

    nome = str(spec.get('nome') or '').strip()
    if not _NOME_OK.match(nome):
        raise L2vpnNaoSuportado(
            'Nome do serviço inválido — use até 63 caracteres entre letras, números e . _ - : @')

    novo_id = _int_na_faixa(spec.get('id'), 'ID do serviço', 1, 4294967295)
    if novo_id in ids_em_uso(servicos):
        origem_id = str(origem.get('id') or '')
        if origem_id.isdigit() and int(origem_id) == novo_id:
            raise L2vpnNaoSuportado(
                f'O id {novo_id} é o do serviço de origem — o clone precisa de um id próprio.')
        raise L2vpnNaoSuportado(f'O id {novo_id} já está em uso neste equipamento — escolha outro.')

    if any(str(s.get('nome', '')).strip().lower() == nome.lower() for s in servicos):
        raise L2vpnNaoSuportado(f'Já existe um serviço chamado "{nome}" neste equipamento.')

    vlan = _int_na_faixa(spec.get('vlan'), 'VLAN', 1, 4094, obrigatorio=False)
    mtu = _int_na_faixa(spec.get('mtu'), 'MTU', 46, 65535, obrigatorio=False)

    peers = [_ip_valido(p, 'Peer') for p in (spec.get('peers') or []) if str(p).strip()]
    if not peers:
        raise L2vpnNaoSuportado('Informe pelo menos um peer (o outro lado do túnel).')
    if len(peers) != len(set(peers)):
        raise L2vpnNaoSuportado('Há peers repetidos na lista.')

    interfaces = []
    for iface in (spec.get('interfaces') or []):
        nome_if = _normalizar_iface(str((iface or {}).get('nome') or '').strip(), vendor)
        if not nome_if:
            continue
        if not _IFACE_OK.match(nome_if):
            raise L2vpnNaoSuportado(f'Nome de interface inválido: "{nome_if}".')
        vlan_if = _int_na_faixa((iface or {}).get('vlan'), f'VLAN de {nome_if}', 1, 4094, obrigatorio=False)
        interfaces.append({'nome': nome_if, 'vlan': vlan_if})

    # Portas físicas onde a VLAN do serviço passa (tagged/untagged). Só o VSI
    # Huawei usa: ele é aplicado na Vlanif, então a VLAN precisa chegar até a
    # porta por fora do bloco do serviço.
    portas = []
    for porta in (spec.get('portas') or []):
        nome_p = _normalizar_iface(str((porta or {}).get('nome') or '').strip(), vendor)
        if not nome_p:
            continue
        if not _IFACE_OK.match(nome_p):
            raise L2vpnNaoSuportado(f'Nome de porta inválido: "{nome_p}".')
        modo = str((porta or {}).get('modo') or 'tagged').strip().lower()
        if modo not in ('tagged', 'untagged'):
            raise L2vpnNaoSuportado(f'Modo inválido para a porta {nome_p}: use tagged ou untagged.')
        portas.append({'nome': nome_p, 'modo': modo})

    if eh_vsi_huawei(vendor, origem.get('tipo')):
        # O VSI é aplicado na Vlanif da VLAN designada — sem VLAN não há onde
        # aplicar. As portas físicas são opcionais: a VLAN pode já estar
        # liberada nos uplinks.
        if not vlan:
            raise L2vpnNaoSuportado(
                'O VSI é aplicado na Vlanif da VLAN — informe a VLAN do serviço.')
    elif not interfaces:
        raise L2vpnNaoSuportado('Informe pelo menos uma interface de acesso.')

    grupo = str(spec.get('grupo') or '').strip()
    if grupo and not _NOME_OK.match(grupo):
        raise L2vpnNaoSuportado('Nome do grupo inválido.')
    if vendor == 'datacom' and not grupo:
        raise L2vpnNaoSuportado('No Datacom o serviço precisa de um grupo (vpws-group/vpls-group).')

    flow_label = str(spec.get('flow_label') or '').strip().lower()
    if flow_label and flow_label not in ('both', 'transmit', 'receive'):
        raise L2vpnNaoSuportado(
            f'flow-label inválido: "{flow_label}" — use both, transmit ou receive.')

    descricao = str(spec.get('descricao') or '').strip()[:200]
    if '\n' in descricao or '\r' in descricao:
        raise L2vpnNaoSuportado('Descrição não pode ter quebra de linha.')

    return {
        'vendor': vendor,
        'tipo': origem.get('tipo') or 'vpls',
        'tecnologia': (origem.get('tecnologia') or '').upper(),
        'nome': nome,
        'id': novo_id,
        'vlan': vlan,
        'mtu': mtu,
        'peers': peers,
        'interfaces': interfaces,
        'portas': portas,
        'grupo': grupo,
        'descricao': descricao,
        'flow_label': flow_label,
        'pw_type': str(origem.get('pw_type') or '').strip(),
        'encapsulamento': str(origem.get('encapsulamento') or '').strip(),
        # A config crua da origem é usada só pra copiar o *dialeto* do
        # RouterOS daquele equipamento (peer= x remote-peer=, cisco-static-id
        # x vpls-id) — ver `_attrs_mikrotik`.
        'origem_trecho': str(origem.get('trecho') or '')[:2000],
    }


# ─── Geração dos comandos ─────────────────────────────────────────────────────

def _subinterface_huawei(nome, vlan):
    """Nome da sub-interface de acesso no VRP: `<porta>.<vlan>`.

    A interface que veio da origem já pode ser a sub-interface
    (`GigabitEthernet0/2/3.3127`, é assim que ela aparece no backup) — nesse
    caso o sufixo antigo é trocado pela VLAN nova, e não concatenado, senão
    o clone com VLAN diferente sairia como `...3127.3200`.
    """
    base = re.sub(r'\.\d+$', '', nome)
    return f'{base}.{vlan}'


def _comandos_huawei_vsi(spec):
    """VSI (VPLS multiponto). O binding da interface de acesso é feito na
    sub-interface, como nos backups reais deste ambiente."""
    cmds = [f'vsi {spec["nome"]}', 'pwsignal ldp', f'vsi-id {spec["id"]}']
    # Ordem da config real: vsi-id, flow-label e só então os peers.
    if spec['flow_label']:
        cmds.append(f'flow-label {spec["flow_label"]}')
    for peer in spec['peers']:
        cmds.append(f'peer {peer}')
    cmds.append('quit')                       # sai do pwsignal
    if spec['mtu']:
        cmds.append(f'mtu {spec["mtu"]}')
    cmds.append('quit')                       # sai do vsi

    # Acesso do VSI: a Vlanif da VLAN designada. Cria a VLAN antes — a Vlanif
    # não existe enquanto a VLAN não existir.
    vlan = spec['vlan']
    cmds.append(f'vlan {vlan}')
    if spec['descricao']:
        cmds.append(f'description {spec["descricao"]}')
    cmds.append('quit')
    cmds.append(f'interface Vlanif{vlan}')
    if spec['descricao']:
        cmds.append(f'description {spec["descricao"]}')
    cmds.append(f'l2 binding vsi {spec["nome"]}')
    cmds.append('quit')

    # Portas físicas onde a VLAN entra. Só a liberação da VLAN é emitida:
    # mudar `port link-type` de uma porta em produção derruba o que já passa
    # por ela, então o tipo do porta continua sendo decisão de quem revisa o
    # preview (que é editável).
    for porta in spec['portas']:
        cmds.append(f'interface {porta["nome"]}')
        if porta['modo'] == 'untagged':
            cmds.append(f'port default vlan {vlan}')
        else:
            cmds.append(f'port trunk allow-pass vlan {vlan}')
        cmds.append('quit')

    # 'commit' aqui é só pro preview/auditoria mostrarem a ação completa — a
    # execução real usa conn.commit() do Netmiko (ver `executar`).
    cmds.append('commit')
    return cmds


def _comandos_huawei_l2vc(spec):
    """L2VC/VLL ponta a ponta: um pseudowire por sub-interface, sem VSI."""
    if len(spec['peers']) > 1:
        raise L2vpnNaoSuportado(
            'L2VC é ponta a ponta: use exatamente um peer (para vários peers, clone um VSI).')
    peer = spec['peers'][0]
    encap = spec['encapsulamento'] if spec['encapsulamento'] in ('raw', 'tagged') else ''

    cmds = []
    for iface in spec['interfaces']:
        vlan = iface['vlan'] or spec['vlan']
        if not vlan:
            raise L2vpnNaoSuportado(
                f'A interface de acesso {iface["nome"]} precisa de uma VLAN (dot1q) no Huawei.')
        cmds.append(f'interface {_subinterface_huawei(iface["nome"], vlan)}')
        cmds.append(f'vlan-type dot1q {vlan}')
        if spec['descricao']:
            cmds.append(f'description {spec["descricao"]}')
        if spec['mtu']:
            cmds.append(f'mtu {spec["mtu"]}')
        linha = f'mpls l2vc {peer} {spec["id"]}'
        if encap:
            linha += f' {encap}'
        cmds.append(linha)
        cmds.append('quit')
    cmds.append('commit')
    return cmds


def _comandos_datacom(spec):
    """DmOS: `vpws-group` (ponto a ponto) ou `vpls-group` + `vfi` (multiponto),
    na mesma ordem/indentação da config real. O `commit` vai como linha: é o
    comando certo dentro do modo de configuração do DmOS."""
    eh_vpls = spec['tipo'] == 'vpls'
    grupo_cmd = 'vpls-group' if eh_vpls else 'vpws-group'
    pw_type = spec['pw_type'] or 'vlan'

    cmds = ['mpls l2vpn', f'{grupo_cmd} {spec["grupo"]}', f'vpn {spec["nome"]}']
    if spec['descricao']:
        cmds.append(f'description {spec["descricao"]}')

    if eh_vpls:
        cmds.append('vfi')
        cmds.append(f'pw-type {pw_type}')
        for peer in spec['peers']:
            cmds.append(f'neighbor {peer}')
            cmds.append(f'pw-id {spec["id"]}')
            if spec['mtu']:
                cmds.append(f'pw-mtu {spec["mtu"]}')
            if spec['flow_label']:
                # No vfi o pw-load-balance vem DEPOIS do pw-id/pw-mtu (no vpws
                # vem antes) — cada um na ordem em que a config real gera.
                cmds.append('pw-load-balance')
                cmds.append(f'flow-label {spec["flow_label"]}')
                cmds.append('exit')      # sai do pw-load-balance
            cmds.append('exit')          # sai do neighbor
        cmds.append('exit')              # sai do vfi
        cmds.append('bridge-domain')
        if spec['vlan']:
            cmds.append(f'dot1q {spec["vlan"]}')
        for iface in spec['interfaces']:
            cmds.append(f'access-interface {iface["nome"]}')
            cmds.append('exit')
        cmds.append('exit')              # sai do bridge-domain
    else:
        if len(spec['peers']) > 1:
            raise L2vpnNaoSuportado(
                'VPWS é ponto a ponto: use um peer só (para vários peers, clone um VPLS).')
        cmds.append(f'neighbor {spec["peers"][0]}')
        cmds.append(f'pw-type {pw_type}')
        if spec['flow_label']:
            cmds.append('pw-load-balance')
            cmds.append(f'flow-label {spec["flow_label"]}')
            cmds.append('exit')          # sai do pw-load-balance
        cmds.append(f'pw-id {spec["id"]}')
        if spec['mtu']:
            cmds.append(f'pw-mtu {spec["mtu"]}')
        cmds.append('exit')              # sai do neighbor
        for iface in spec['interfaces']:
            vlan = iface['vlan'] or spec['vlan']
            cmds.append(f'access-interface {iface["nome"]}')
            if vlan:
                cmds.append(f'dot1q {vlan}')
            cmds.append('exit')

    cmds += ['exit', 'exit', 'exit', 'commit']   # vpn → grupo → mpls l2vpn
    return cmds


def _attrs_mikrotik(trecho):
    """Qual nome de atributo este equipamento usa — `remote-peer=` (RouterOS 6)
    ou `peer=` (v7), `cisco-static-id`/`cisco-style-id`/`vpls-id` — copiado da
    linha de origem em vez de fixado no código: a mesma config escrita no
    dialeto errado é recusada pelo RouterOS."""
    attr_peer = 'remote-peer' if 'remote-peer=' in trecho else 'peer'
    for candidato in ('cisco-static-id', 'cisco-style-id', 'vpls-id'):
        if f'{candidato}=' in trecho:
            return attr_peer, candidato
    return attr_peer, 'cisco-static-id'


def _comandos_mikrotik(spec):
    """RouterOS: a interface VPLS já é o serviço; as interfaces de acesso são
    VLANs criadas sobre ela. Uma linha = um comando completo."""
    if len(spec['peers']) > 1:
        raise L2vpnNaoSuportado('No RouterOS cada interface VPLS tem um peer só — crie uma por peer.')
    peer = spec['peers'][0]
    pw_type = spec['pw_type'] or 'tagged-ethernet'
    attr_peer, attr_id = _attrs_mikrotik(spec.get('origem_trecho', ''))

    linha = (f'/interface vpls add name={spec["nome"]} {attr_peer}={peer} '
             f'{attr_id}={spec["id"]} pw-type={pw_type} disabled=no')
    if spec['mtu']:
        linha += f' pw-l2mtu={spec["mtu"]}'
    if spec['descricao']:
        linha += f' comment="{spec["descricao"]}"'
    cmds = [linha]

    for iface in spec['interfaces']:
        vlan = iface['vlan'] or spec['vlan']
        if not vlan:
            continue
        # A interface de acesso do RouterOS é uma VLAN POR CIMA da VPLS — o
        # `nome` digitado é o nome da VLAN, não uma porta física.
        cmds.append(f'/interface vlan add interface={spec["nome"]} '
                    f'name={iface["nome"]} vlan-id={vlan} disabled=no')
    return cmds


def comandos_clonar(spec):
    """Comandos de criação do serviço clonado, prontos pro preview editável."""
    vendor = spec['vendor']
    if vendor == 'huawei':
        return _comandos_huawei_l2vc(spec) if spec['tipo'] == 'l2vc' else _comandos_huawei_vsi(spec)
    if vendor == 'datacom':
        return _comandos_datacom(spec)
    if vendor == 'mikrotik':
        return _comandos_mikrotik(spec)
    raise L2vpnNaoSuportado(f'Fabricante "{vendor}" não suportado para criar serviço L2VPN.')


def validar_comandos_editados(comandos):
    """O textarea do modal é editável (mesmo padrão da automação BGP) — o que
    volta de lá ainda precisa passar por um teto de sanidade."""
    if not isinstance(comandos, list) or not comandos or not all(isinstance(c, str) for c in comandos):
        raise L2vpnNaoSuportado('Lista de comandos inválida.')
    limpos = [c.strip() for c in comandos if c.strip()]
    if len(limpos) > MAX_LINHAS:
        raise L2vpnNaoSuportado(f'Comando longo demais ({len(limpos)} linhas, máximo {MAX_LINHAS}).')
    if any(len(c) > MAX_COLUNAS for c in limpos):
        raise L2vpnNaoSuportado('Há uma linha longa demais — revise antes de enviar.')
    return limpos


# ─── Execução no equipamento ──────────────────────────────────────────────────

def executar(acesso, vendor, comandos):
    """Conecta e envia os comandos. Devolve (output, status) — 'sucesso' ou
    'erro' — e nunca levanta exceção de conexão.

    Mesma mecânica de `bgp_actions.executar_acao_bgp`, com uma diferença: no
    RouterOS os comandos são enviados UM A UM (criar a VPLS e depois as VLANs
    de acesso são comandos independentes), enquanto a automação BGP só
    precisava mandar um.
    """
    fabricante_conexao = 'cisco' if vendor == 'datacom' else vendor
    if fabricante_conexao not in DEVICE_TYPES:
        return f'Fabricante "{vendor}" sem driver de conexão configurado.', 'erro'

    conn, tunel = None, None
    try:
        conn, tunel = _conectar_script(acesso, fabricante_conexao)
        if vendor in _COMANDO_A_COMANDO:
            partes = []
            for comando in comandos:
                partes.append(f'> {comando}')
                partes.append(conn.send_command(comando))
            output = '\n'.join(partes)
        elif vendor in _PRECISA_COMMIT_NETMIKO:
            # 'commit' está na lista só pro preview/auditoria mostrarem a ação
            # completa — enviar o texto não faz o handshake de confirmação da
            # config candidata; quem faz isso direito é conn.commit().
            config = [c for c in comandos if c.strip().lower() != 'commit']
            output = conn.send_config_set(config, exit_config_mode=False)
            output += '\n' + conn.commit()
        else:
            output = conn.send_config_set(comandos)
        return output, 'sucesso'
    except Exception as e:
        logger.error(f'❌ Erro aplicando L2VPN em {acesso}: {e}')
        return str(e), 'erro'
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass
        if tunel:
            _fechar_tunel(tunel)
