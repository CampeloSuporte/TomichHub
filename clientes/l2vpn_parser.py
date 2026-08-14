"""
Parser de serviços L2VPN (VSI/VPLS, VPWS e L2VC/pseudowire) a partir do texto
bruto do backup do equipamento.

Complementa `clientes/backup_parser.py` — que extrai IP/BGP/VLAN e já fazia uma
leitura rasa de `vsi`/`l2vc` para o artigo do wiki. Aqui o objetivo é
*documentar o serviço inteiro*: tipo, id (vsi-id/pw-id/vc-id), nome, peers,
MTU, sinalização, interfaces de acesso e VLAN — e devolver os peers de forma
que a topologia consiga casar cada túnel com o host do outro lado
(`extrair_ips_identidade`, abaixo).

Sintaxes cobertas (todas confirmadas em backups reais do ambiente, exceto
Cisco/Juniper que seguem a doc oficial — ver docs/topologia_l2vpn.md):

- Huawei VRP — `vsi NOME` + `pwsignal ldp` + `vsi-id N` + `peer IP` (VPLS),
  `l2 binding vsi NOME` na sub-interface (interface de acesso do VSI) e
  `mpls l2vc IP VC-ID` na interface/Vlanif (VLL/L2VC ponta a ponta).
- Datacom DmOS — `mpls l2vpn` + `vpws-group|vpls-group NOME` + `vpn NOME` com
  `neighbor IP`/`pw-id`/`pw-mtu` e `access-interface`/`dot1q`. O `vfi` marca
  VPLS; sem ele o `vpn` é VPWS.
- MikroTik RouterOS — `/interface vpls add name=X remote-peer=IP …`, com as
  VLANs/bridges que usam a interface VPLS como interface de acesso.
- Cisco IOS/XR — `xconnect IP VC-ID encapsulation mpls` na interface,
  `l2vpn xconnect group`/`p2p`/`neighbor ipv4 IP pw-id N` e `l2 vfi NOME
  manual` + `vpn id N` + `neighbor IP N encapsulation mpls`.
- Juniper JunOS (`| display set`) — `set protocols l2circuit neighbor IP
  interface X virtual-circuit-id N` e `routing-instances` com
  `instance-type vpls` + `vpls-id`/`neighbor`.

Cada serviço vira um dict normalizado (ver `_novo_servico`); `trecho` guarda o
pedaço cru da config que originou o serviço, para mostrar no modal da
topologia sem precisar abrir o arquivo de backup inteiro.
"""
import re

_IP = r'\d{1,3}(?:\.\d{1,3}){3}'

# Quantas linhas de config crua guardar em `trecho` por serviço — o suficiente
# para o bloco completo de um VSI/vpn e curto o bastante para caber no modal.
_MAX_TRECHO = 60


# ─── Modelo normalizado ───────────────────────────────────────────────────────

def _novo_servico(tipo, tecnologia, nome, **extra):
    """`tipo` é a família (vpls/vpws/l2vc) usada para filtro e cor na UI;
    `tecnologia` é o rótulo do comando que gerou o serviço (VSI, VPLS, VPWS,
    L2VC…) — o operador procura pelo nome que ele digitou no equipamento."""
    servico = {
        'tipo': tipo,
        'tecnologia': tecnologia,
        'nome': nome,
        'id': '',
        'grupo': '',
        'descricao': '',
        'sinalizacao': '',
        'mtu': '',
        'pw_type': '',
        # VLAN do pseudowire (`pw-type vlan N` do DmOS) — separada da VLAN de
        # acesso porque as duas podem ser diferentes no mesmo serviço.
        'pw_vlan': '',
        'encapsulamento': '',
        'flow_label': '',      # both | transmit | receive — balanceamento do pseudowire
        'vlan': '',
        'peers': [],
        'interfaces': [],
        'vendor': '',
        'linha': 0,
        'trecho': '',
    }
    servico.update(extra)
    return servico


def _add_peer(servico, ip, pw_id='', mtu='', flow_label=False):
    """Peer/neighbor do túnel. Idempotente: o mesmo IP citado em dois lugares
    do bloco (ex. `neighbor` do vfi e `pw-load-balance`) vira um peer só,
    acumulando os campos que cada trecho traz."""
    for peer in servico['peers']:
        if peer['ip'] == ip:
            if pw_id and not peer['pw_id']:
                peer['pw_id'] = pw_id
            if mtu and not peer['mtu']:
                peer['mtu'] = mtu
            peer['flow_label'] = peer['flow_label'] or flow_label
            return peer
    peer = {'ip': ip, 'pw_id': pw_id, 'mtu': mtu, 'flow_label': flow_label}
    servico['peers'].append(peer)
    return peer


def _add_interface(servico, nome, vlan='', descricao='', sobrepor_vlan=False):
    """Interface de acesso do serviço. Idempotente por nome.

    `sobrepor_vlan=True` é para a VLAN LIDA NA PRÓPRIA INTERFACE (o `dot1q`
    dela): ela vale mais que a VLAN herdada do serviço, que só entra como
    palpite. Sem isso o palpite chegava primeiro e ficava — no DmOS o
    `access-interface` aparece ANTES do `dot1q` dele, então uma interface com
    `dot1q 86` num serviço com `pw-type vlan 2400` era mostrada (e clonada)
    como VLAN 2400. Caso real deste ambiente, ver docs/topologia_l2vpn.md.
    """
    if not nome:
        return
    for iface in servico['interfaces']:
        if iface['nome'] == nome:
            if vlan and (sobrepor_vlan or not iface['vlan']):
                iface['vlan'] = vlan
            if descricao and not iface['descricao']:
                iface['descricao'] = descricao
            return
    servico['interfaces'].append({'nome': nome, 'vlan': vlan, 'descricao': descricao})


def _trecho(linhas, inicio, fim):
    return '\n'.join(linhas[inicio:min(fim, inicio + _MAX_TRECHO)]).rstrip()


# ─── Huawei VRP ───────────────────────────────────────────────────────────────

def _iter_blocos_vrp(linhas):
    """Itera os blocos da config estilo VRP (Huawei) e ZTE: começam numa linha
    sem indentação e terminam na linha `#` (ou no começo do bloco seguinte,
    quando o `#` não veio no meio). Devolve (índice, cabeçalho, corpo, fim)."""
    total = len(linhas)
    i = 0
    while i < total:
        cabecalho = linhas[i]
        if not cabecalho.strip() or cabecalho.strip() == '#' or cabecalho[0] in ' \t':
            i += 1
            continue
        j = i + 1
        corpo = []
        while j < total:
            linha = linhas[j]
            if linha.strip() == '#':
                break
            if linha.strip() and linha[0] not in ' \t':
                break
            corpo.append(linha)
            j += 1
        yield i, cabecalho.strip(), corpo, j
        i = max(j, i + 1)


def _parse_huawei(conteudo, linhas):
    servicos = []
    por_vsi = {}  # nome do VSI -> serviço, para casar o `l2 binding vsi` depois

    # OLTs Huawei (MA5800) declaram o pseudowire num bloco à parte e o VSI só
    # aponta pro índice (`vsi-pw-binding pwindex N`) — indexa antes para poder
    # resolver o peer na hora de montar o VSI.
    pw_para = {}
    for idx, cabecalho, corpo, fim in _iter_blocos_vrp(linhas):
        m = re.match(r'^pw-para pwindex\s+(\d+)', cabecalho)
        if not m:
            continue
        dados = {'peer': '', 'pwid': '', 'pw_type': ''}
        for linha in corpo:
            mm = re.match(rf'\s*peer-address\s+({_IP})', linha)
            if mm:
                dados['peer'] = mm.group(1)
            mm = re.match(r'\s*pwid\s+(\d+)', linha)
            if mm:
                dados['pwid'] = mm.group(1)
            mm = re.match(r'\s*pw-type\s+(.+?)\s*$', linha)
            if mm:
                dados['pw_type'] = mm.group(1)
        pw_para[m.group(1)] = dados

    for idx, cabecalho, corpo, fim in _iter_blocos_vrp(linhas):
        # ── VSI (VPLS) ────────────────────────────────────────────────────────
        m = re.match(r'^vsi\s+("[^"]+"|\S+)(?:\s+(static|auto))?$', cabecalho)
        if m:
            svc = _novo_servico(
                'vpls', 'VSI', m.group(1).strip('"'),
                vendor='huawei', linha=idx + 1,
                sinalizacao=m.group(2) or '',
                trecho=_trecho(linhas, idx, fim),
            )
            for linha in corpo:
                mm = re.match(r'\s*pwsignal\s+(\S+)', linha)
                if mm:
                    svc['sinalizacao'] = mm.group(1)
                mm = re.match(r'\s*vsi-id\s+(\d+)', linha)
                if mm:
                    svc['id'] = mm.group(1)
                # `peer IP [negotiation-vc-id N]` — o vc-id negociado só
                # aparece quando o vsi-id não é igual dos dois lados.
                mm = re.match(rf'\s*peer\s+({_IP})(?:\s+negotiation-vc-id\s+(\d+))?', linha)
                if mm:
                    _add_peer(svc, mm.group(1), pw_id=mm.group(2) or '')
                mm = re.match(r'\s*mtu\s+(\d+)', linha)
                if mm:
                    svc['mtu'] = mm.group(1)
                mm = re.match(r'\s*encapsulation\s+(\S+)', linha)
                if mm:
                    svc['encapsulamento'] = mm.group(1)
                # `flow-label both` fica dentro do pwsignal, entre o vsi-id e os
                # peers — é o que faz o tráfego do pseudowire balancear entre os
                # caminhos do core, então some junto se não for clonado.
                mm = re.match(r'\s*flow-label\s+(both|transmit|receive)', linha)
                if mm:
                    svc['flow_label'] = mm.group(1)
                # MA5800: peer e VLAN de acesso vêm por referência.
                mm = re.match(r'\s*vsi-pw-binding pwindex\s+(\d+)', linha)
                if mm:
                    pw = pw_para.get(mm.group(1))
                    if pw:
                        svc['pw_type'] = svc['pw_type'] or pw['pw_type']
                        if pw['peer']:
                            _add_peer(svc, pw['peer'], pw_id=pw['pwid'])
                mm = re.match(r'\s*vsi-ac-binding vlan\s+(\d+)', linha)
                if mm:
                    svc['vlan'] = mm.group(1)
                    _add_interface(svc, f'VLAN {mm.group(1)}', vlan=mm.group(1))
            servicos.append(svc)
            por_vsi[svc['nome']] = svc
            continue

        # ── Interfaces: binding de VSI e L2VC ────────────────────────────────
        m = re.match(r'^interface\s+(\S+)', cabecalho)
        if not m:
            continue
        iface = m.group(1)
        desc, vlan, mtu_if = '', '', ''
        # Vlanif2301 → VLAN 2301 (a VLAN está no próprio nome da interface).
        mv = re.match(r'^Vlanif(\d+)$', iface, re.IGNORECASE)
        if mv:
            vlan = mv.group(1)
        for linha in corpo:
            mm = re.match(r'\s*description\s+(.+?)\s*$', linha)
            if mm and not desc:
                desc = mm.group(1)
            mm = re.match(r'\s*vlan-type\s+dot1q\s+(\d+)', linha)
            if mm:
                vlan = mm.group(1)
            mm = re.match(r'\s*mtu\s+(\d+)', linha)
            if mm:
                mtu_if = mm.group(1)

        for linha in corpo:
            mm = re.match(r'\s*l2 binding vsi\s+(\S+)', linha)
            if mm:
                svc = por_vsi.get(mm.group(1))
                if svc is None:
                    # Binding citado antes do bloco `vsi` (ordem do arquivo) —
                    # cria o serviço agora e completa quando o bloco aparecer.
                    svc = _novo_servico('vpls', 'VSI', mm.group(1),
                                        vendor='huawei', linha=idx + 1)
                    servicos.append(svc)
                    por_vsi[mm.group(1)] = svc
                _add_interface(svc, iface, vlan=vlan, descricao=desc)
                # A VLAN do VSI é a da interface onde ele foi amarrado — no
                # bloco `vsi` ela não aparece (só o vsi-id). Sem isso o serviço
                # ficava com VLAN vazia e a clonagem, que aplica o VSI na
                # `Vlanif` da VLAN, não tinha o que preencher: o operador via o
                # número só na linha da interface e o campo "VLAN" em branco.
                if vlan and not svc['vlan']:
                    svc['vlan'] = vlan
                continue
            # `mpls l2vc PEER VC-ID [raw|tagged] [mtu N]` ou, com template,
            # `mpls l2vc PEER pw-template NOME VC-ID`.
            mm = re.match(
                rf'\s*mpls l2vc\s+({_IP})\s+(?:pw-template\s+\S+\s+)?(\d+)(.*)$', linha)
            if mm:
                resto = mm.group(3) or ''
                mtu_pw = ''
                mmtu = re.search(r'\bmtu\s+(\d+)', resto)
                if mmtu:
                    mtu_pw = mmtu.group(1)
                encap = ''
                menc = re.search(r'\b(raw|tagged)\b', resto)
                if menc:
                    encap = menc.group(1)
                svc = _novo_servico(
                    'l2vc', 'L2VC', desc or iface,
                    vendor='huawei', linha=idx + 1, id=mm.group(2),
                    descricao=desc, vlan=vlan, encapsulamento=encap,
                    mtu=mtu_pw or mtu_if, sinalizacao='ldp',
                    trecho=_trecho(linhas, idx, fim),
                )
                _add_peer(svc, mm.group(1), pw_id=mm.group(2), mtu=mtu_pw)
                _add_interface(svc, iface, vlan=vlan, descricao=desc)
                servicos.append(svc)

    return servicos


# ─── Datacom DmOS ─────────────────────────────────────────────────────────────

# Backups de DmOS chegam em dois formatos: o normal (indentado, blocos
# fechados por `!`) e o "achatado" — o coletor às vezes traz o
# `show running-config | nomore` inteiro numa única linha. Um scanner por
# tokens funciona nos dois: dentro da região `mpls l2vpn`, cada `vpn NOME`
# abre um serviço e os tokens seguintes (neighbor/pw-id/access-interface…)
# pertencem a ele até o próximo `vpn`/`*-group`.

# Palavras que só aparecem fora do bloco `mpls l2vpn` e portanto marcam o fim
# da região no formato achatado (no indentado o fim é a coluna 0).
_DMOS_FIM_REGIAO = re.compile(
    r'\b(snmp|hostname|clock|telnet-server|oam|license|vrf|dot1q\s+vlan|'
    r'mac-address-table|layer2-control-protocol|loopback-detection|'
    r'assistant-task|remote-devices|link-aggregation|router\s+(?:ospf|bgp|static)|'
    r'interface\s+(?:gigabit|ten-gigabit|forty|hundred|l3|loopback|mgmt))\b')

_DMOS_KEYWORDS = {
    'vpws-group', 'vpls-group', 'vpn', 'neighbor', 'pw-id', 'pw-mtu', 'pw-type',
    'access-interface', 'dot1q', 'description', 'vfi', 'bridge-domain',
    'pw-load-balance', 'flow-label', 'encapsulation', 'qinq', 'mtu',
    'service-instance', 'split-horizon', 'mac-address-table', 'storm-control',
}


def _regiao_l2vpn_dmos(conteudo):
    """Recorta o trecho do `mpls l2vpn` — no formato indentado termina na
    próxima linha de coluna 0 que não seja `!`; no achatado, na primeira
    palavra-chave de outra seção."""
    m = re.search(r'^[ \t]*mpls l2vpn\b', conteudo, re.MULTILINE)
    if not m:
        return '', 0
    inicio = m.end()
    linha_inicial = conteudo.count('\n', 0, m.start()) + 1
    resto = conteudo[inicio:]

    fim_linha = None
    for mm in re.finditer(r'^(?![ \t!])\S.*$', resto, re.MULTILINE):
        fim_linha = mm.start()
        break
    if fim_linha is not None and fim_linha > 0:
        return resto[:fim_linha], linha_inicial

    # Achatado (sem quebras de linha úteis): corta na próxima seção conhecida.
    mm = _DMOS_FIM_REGIAO.search(resto)
    return (resto[:mm.start()] if mm else resto[:200000]), linha_inicial


def _parse_datacom(conteudo):
    regiao, linha_inicial = _regiao_l2vpn_dmos(conteudo)
    if not regiao.strip():
        return []

    # Guarda o offset de cada token para recuperar depois a linha e o trecho
    # cru de cada `vpn` (a config achatada não tem linha própria por serviço).
    achados = [(m.group(0), m.start()) for m in re.finditer(r'\S+', regiao)]
    tokens = [t for t, _ in achados]
    posicoes = [p for _, p in achados]

    servicos = []
    grupo, tipo_grupo = '', 'vpws'
    svc = None
    svc_ini = 0          # offset, na região, onde o `vpn` do serviço começou
    peer_atual = None
    iface_atual = None

    def _linha_de(pos):
        return linha_inicial + regiao.count('\n', 0, pos)

    def _fechar_trecho(fim_pos):
        """Fecha o `trecho` do serviço aberto com a config entre o `vpn` e o
        começo do próximo serviço/grupo."""
        if svc is None:
            return
        bruto = regiao[svc_ini:fim_pos].rstrip().rstrip('!').rstrip()
        linhas_trecho = [l.rstrip() for l in bruto.split('\n')][:_MAX_TRECHO]
        if len(linhas_trecho) == 1:
            svc['trecho'] = linhas_trecho[0][:600]
            return
        # A 1ª linha vem sem indentação (começa no token `vpn`); tira o recuo
        # comum das demais para o bloco não aparecer torto no modal.
        corpo_trecho = [l for l in linhas_trecho[1:] if l.strip()]
        recuo = min((len(l) - len(l.lstrip()) for l in corpo_trecho), default=0)
        svc['trecho'] = '\n'.join(
            [linhas_trecho[0]] + [l[recuo:] for l in linhas_trecho[1:]])

    def _texto_livre(i):
        """Consome os tokens de um valor de texto livre (ex. `description`)
        até a próxima palavra-chave/terminador. Devolve (texto, novo_i)."""
        partes = []
        while i < len(tokens) and tokens[i] not in _DMOS_KEYWORDS and tokens[i] != '!':
            partes.append(tokens[i])
            i += 1
        return ' '.join(partes), i

    i = 0
    while i < len(tokens):
        tk = tokens[i]

        if tk in ('vpws-group', 'vpls-group') and i + 1 < len(tokens):
            _fechar_trecho(posicoes[i])
            grupo = tokens[i + 1]
            tipo_grupo = 'vpws' if tk == 'vpws-group' else 'vpls'
            svc, peer_atual, iface_atual = None, None, None
            i += 2
            continue

        if tk == 'vpn' and i + 1 < len(tokens):
            _fechar_trecho(posicoes[i])
            svc_ini = posicoes[i]
            svc = _novo_servico(
                tipo_grupo, tipo_grupo.upper(), tokens[i + 1],
                vendor='datacom', grupo=grupo, linha=_linha_de(posicoes[i]),
                sinalizacao='ldp',
            )
            # O id do serviço na DmOS é o pw-id; o nome quase sempre começa
            # com ele (ex. `2620-PPPOE-OLT-02`) — usa como palpite até o
            # `pw-id` explícito aparecer.
            mid = re.match(r'^(\d+)[-_]', tokens[i + 1])
            if mid:
                svc['id'] = mid.group(1)
            servicos.append(svc)
            peer_atual, iface_atual = None, None
            i += 2
            continue

        if svc is None:
            i += 1
            continue

        if tk == 'vfi':
            # `vfi` = encaminhamento multiponto → é VPLS mesmo que o grupo
            # tenha sido declarado como vpws-group.
            svc['tipo'] = 'vpls'
            svc['tecnologia'] = 'VPLS'
            i += 1
            continue

        if tk == 'neighbor' and i + 1 < len(tokens) and re.fullmatch(_IP, tokens[i + 1]):
            peer_atual = _add_peer(svc, tokens[i + 1])
            i += 2
            continue

        if tk == 'pw-id' and i + 1 < len(tokens):
            svc['id'] = tokens[i + 1]
            if peer_atual is not None:
                peer_atual['pw_id'] = tokens[i + 1]
            i += 2
            continue

        if tk == 'pw-mtu' and i + 1 < len(tokens):
            svc['mtu'] = tokens[i + 1]
            if peer_atual is not None:
                peer_atual['mtu'] = tokens[i + 1]
            i += 2
            continue

        if tk == 'pw-type' and i + 1 < len(tokens):
            # `pw-type vlan [VLAN-ID]` — o id opcional é a VLAN DO PSEUDOWIRE
            # (a tag que sai no túnel), que não é necessariamente a VLAN de
            # acesso do cliente: em config real deste ambiente existe
            # `pw-type vlan 2400` com `access-interface ... dot1q 86`. Guarda
            # em `pw_vlan` e só usa como palpite da VLAN de acesso enquanto
            # nenhum `dot1q` explícito tiver aparecido.
            svc['pw_type'] = tokens[i + 1]
            i += 2
            if i < len(tokens) and tokens[i].isdigit():
                svc['pw_vlan'] = tokens[i]
                svc['vlan'] = svc['vlan'] or tokens[i]
                i += 1
            continue

        if tk == 'flow-label':
            valor = tokens[i + 1] if i + 1 < len(tokens) else ''
            if valor in ('both', 'transmit', 'receive'):
                svc['flow_label'] = valor
            for peer in svc['peers']:
                peer['flow_label'] = True
            i += 2 if valor in ('both', 'transmit', 'receive') else 1
            continue

        if tk == 'access-interface' and i + 1 < len(tokens):
            iface_atual = tokens[i + 1]
            # No `bridge-domain` o `dot1q` vem *antes* das interfaces de
            # acesso (o contrário do vpws) — herda a VLAN já lida do serviço.
            _add_interface(svc, iface_atual, vlan=svc['vlan'])
            i += 2
            continue

        if tk in ('dot1q', 'encapsulation') and i + 1 < len(tokens):
            j = i + 1
            if tk == 'encapsulation':
                if tokens[j] != 'dot1q':
                    i += 2
                    continue
                j += 1
            if j < len(tokens) and tokens[j].isdigit():
                # Dentro de uma `access-interface` o dot1q é a VLAN DELA e
                # manda na VLAN do serviço também (o palpite anterior podia ter
                # vindo do `pw-type vlan N`, que é outra coisa). Fora dela
                # (bridge-domain) é a VLAN do serviço mesmo.
                if iface_atual:
                    _add_interface(svc, iface_atual, vlan=tokens[j], sobrepor_vlan=True)
                    if svc.get('pw_vlan') and svc['vlan'] == svc['pw_vlan']:
                        svc['vlan'] = tokens[j]
                svc['vlan'] = svc['vlan'] or tokens[j]
                i = j + 1
                continue
            i = j + 1
            continue

        if tk == 'description':
            texto, i = _texto_livre(i + 1)
            svc['descricao'] = svc['descricao'] or texto
            continue

        if tk == 'qinq':
            svc['encapsulamento'] = 'qinq'
            i += 1
            continue

        i += 1

    _fechar_trecho(len(regiao))

    # Sem interface de acesso e sem peer o "serviço" é ruído de recorte.
    return [s for s in servicos if s['peers'] or s['interfaces']]


# ─── MikroTik RouterOS ────────────────────────────────────────────────────────

def _mk_valor(linha, chave):
    m = re.search(rf'\b{chave}=("([^"]*)"|(\S+))', linha)
    if not m:
        return ''
    return m.group(2) if m.group(2) is not None else m.group(3)


def _parse_mikrotik(conteudo, linhas):
    servicos = []
    por_nome = {}

    for idx, linha in enumerate(linhas):
        if not re.match(r'^/interface vpls\s+(bgp-vpls\s+)?add\b', linha):
            continue
        nome = _mk_valor(linha, 'name')
        if not nome:
            continue
        # O IP do outro lado é `remote-peer=` no RouterOS 6 e `peer=` no 7.
        peer = _mk_valor(linha, 'remote-peer') or _mk_valor(linha, 'peer')
        # Idem o VC-ID: `vpls-id` (RFC 4761), `cisco-style-id`/`cisco-static-id`
        # (RFC 4762/Cisco) — cada versão/estilo grava num atributo diferente.
        vpls_id = (_mk_valor(linha, 'vpls-id') or _mk_valor(linha, 'cisco-style-id')
                   or _mk_valor(linha, 'cisco-static-id'))
        svc = _novo_servico(
            'vpls' if 'bgp-vpls' in linha else 'l2vc', 'VPLS', nome,
            vendor='mikrotik', linha=idx + 1, id=vpls_id,
            descricao=_mk_valor(linha, 'comment'),
            mtu=(_mk_valor(linha, 'pw-l2mtu') or _mk_valor(linha, 'l2mtu')
                 or _mk_valor(linha, 'advertised-l2mtu') or _mk_valor(linha, 'mtu')),
            sinalizacao='bgp' if 'bgp-vpls' in linha else 'ldp',
            pw_type=(_mk_valor(linha, 'pw-type')
                     or ('cisco-style' if _mk_valor(linha, 'cisco-style') == 'yes' else '')),
            trecho=linha.strip(),
        )
        if peer:
            _add_peer(svc, peer, pw_id=vpls_id)
        servicos.append(svc)
        por_nome[nome] = svc

    if not por_nome:
        return []

    # Interfaces de acesso: VLANs criadas *sobre* a interface VPLS e portas de
    # bridge que têm a VPLS como membro — é o que enxerga o cliente do túnel.
    for linha in linhas:
        if re.match(r'^/interface vlan\s+add\b', linha):
            svc = por_nome.get(_mk_valor(linha, 'interface'))
            if svc:
                _add_interface(svc, _mk_valor(linha, 'name'),
                               vlan=_mk_valor(linha, 'vlan-id'))
        elif re.match(r'^/interface bridge port\s+add\b', linha):
            svc = por_nome.get(_mk_valor(linha, 'interface'))
            if svc:
                _add_interface(svc, f"bridge {_mk_valor(linha, 'bridge')}")

    return servicos


# ─── Cisco IOS / IOS-XR ───────────────────────────────────────────────────────

def _iter_blocos_indentados(linhas):
    """Blocos de config estilo Cisco: cabeçalho na coluna 0 e corpo indentado
    (encerra em `!` ou na próxima linha de coluna 0)."""
    total = len(linhas)
    i = 0
    while i < total:
        cabecalho = linhas[i]
        if not cabecalho.strip() or cabecalho.lstrip().startswith('!') or cabecalho[0] in ' \t':
            i += 1
            continue
        j = i + 1
        corpo = []
        while j < total:
            linha = linhas[j]
            if linha.strip() and linha[0] not in ' \t':
                break
            if linha.strip() == '!':
                j += 1
                break
            corpo.append(linha)
            j += 1
        yield i, cabecalho.strip(), corpo, j
        i = max(j, i + 1)


def _parse_cisco(conteudo, linhas):
    servicos = []

    for idx, cabecalho, corpo, fim in _iter_blocos_indentados(linhas):
        # VFI (VPLS): `l2 vfi NOME manual` / `vpn id N` / `neighbor IP N encapsulation mpls`
        m = re.match(r'^l2 vfi\s+(\S+)', cabecalho)
        if m:
            svc = _novo_servico('vpls', 'VFI', m.group(1), vendor='cisco',
                                linha=idx + 1, trecho=_trecho(linhas, idx, fim))
            for linha in corpo:
                mm = re.match(r'\s*vpn id\s+(\d+)', linha)
                if mm:
                    svc['id'] = mm.group(1)
                mm = re.match(rf'\s*neighbor\s+({_IP})(?:\s+(\d+))?', linha)
                if mm:
                    _add_peer(svc, mm.group(1), pw_id=mm.group(2) or '')
            servicos.append(svc)
            continue

        # Interface com xconnect (L2VC ponta a ponta)
        m = re.match(r'^interface\s+(\S+)', cabecalho)
        if m:
            iface = m.group(1)
            desc = ''
            for linha in corpo:
                mm = re.match(r'\s*description\s+(.+?)\s*$', linha)
                if mm and not desc:
                    desc = mm.group(1)
            for linha in corpo:
                mm = re.match(rf'\s*xconnect\s+({_IP})\s+(\d+)', linha)
                if mm:
                    svc = _novo_servico(
                        'l2vc', 'XCONNECT', desc or iface, vendor='cisco',
                        linha=idx + 1, id=mm.group(2), descricao=desc,
                        sinalizacao='ldp', trecho=_trecho(linhas, idx, fim))
                    _add_peer(svc, mm.group(1), pw_id=mm.group(2))
                    _add_interface(svc, iface, descricao=desc)
                    servicos.append(svc)
            continue

    # IOS-XR: `l2vpn` / `xconnect group G` / `p2p NOME` / `neighbor ipv4 IP pw-id N`
    grupo, svc = '', None
    for idx, linha in enumerate(linhas):
        m = re.match(r'\s*xconnect group\s+(\S+)', linha)
        if m:
            grupo, svc = m.group(1), None
            continue
        m = re.match(r'\s*p2p\s+(\S+)', linha)
        if m:
            svc = _novo_servico('vpws', 'VPWS', m.group(1), vendor='cisco',
                                grupo=grupo, linha=idx + 1, sinalizacao='ldp')
            servicos.append(svc)
            continue
        if svc is None:
            continue
        m = re.match(rf'\s*neighbor\s+(?:ipv4\s+)?({_IP})\s+pw-id\s+(\d+)', linha)
        if m:
            svc['id'] = m.group(2)
            _add_peer(svc, m.group(1), pw_id=m.group(2))
            continue
        m = re.match(r'\s*interface\s+(\S+)', linha)
        if m:
            _add_interface(svc, m.group(1))

    return [s for s in servicos if s['peers'] or s['interfaces']]


# ─── Juniper JunOS (| display set) ────────────────────────────────────────────

def _parse_juniper(conteudo, linhas):
    por_chave = {}

    # L2circuit: um pseudowire por (neighbor, interface).
    for idx, linha in enumerate(linhas):
        m = re.match(
            rf'^set protocols l2circuit neighbor\s+({_IP})\s+interface\s+(\S+)\s+(.*)$', linha)
        if not m:
            continue
        peer, iface, resto = m.group(1), m.group(2), m.group(3)
        chave = ('l2circuit', peer, iface)
        svc = por_chave.get(chave)
        if svc is None:
            svc = _novo_servico('l2vc', 'L2CIRCUIT', iface, vendor='juniper',
                                linha=idx + 1, sinalizacao='ldp')
            _add_peer(svc, peer)
            _add_interface(svc, iface)
            por_chave[chave] = svc
        mm = re.match(r'virtual-circuit-id\s+(\d+)', resto)
        if mm:
            svc['id'] = mm.group(1)
            svc['peers'][0]['pw_id'] = mm.group(1)
        mm = re.match(r'mtu\s+(\d+)', resto)
        if mm:
            svc['mtu'] = mm.group(1)
        mm = re.match(r'description\s+(.+?)\s*$', resto)
        if mm:
            svc['descricao'] = mm.group(1).strip('"')

    # VPLS em routing-instance.
    for idx, linha in enumerate(linhas):
        m = re.match(r'^set routing-instances (\S+) (.*)$', linha)
        if not m:
            continue
        nome, resto = m.group(1), m.group(2)
        chave = ('vpls', nome)
        if 'instance-type vpls' in resto:
            por_chave.setdefault(chave, _novo_servico(
                'vpls', 'VPLS', nome, vendor='juniper', linha=idx + 1, sinalizacao='ldp'))
            continue
        svc = por_chave.get(chave)
        if svc is None:
            continue
        mm = re.match(r'protocols vpls vpls-id\s+(\d+)', resto)
        if mm:
            svc['id'] = mm.group(1)
        mm = re.match(rf'protocols vpls neighbor\s+({_IP})', resto)
        if mm:
            _add_peer(svc, mm.group(1))
        mm = re.match(r'interface\s+(\S+)', resto)
        if mm:
            _add_interface(svc, mm.group(1))
        mm = re.match(r'protocols vpls mtu\s+(\d+)', resto)
        if mm:
            svc['mtu'] = mm.group(1)

    return [s for s in por_chave.values() if s['peers'] or s['interfaces']]


# ─── Sessão LDP targeted (o "outro lado" do pseudowire) ───────────────────────

def extrair_ldp(conteudo):
    """O que o equipamento já tem de LDP targeted, lido do backup.

    O pseudowire só sobe se existir sessão LDP com o peer, e ela não faz parte
    do bloco do serviço — por isso clonar um VSI/VPWS para um peer novo não
    fecha o circuito sozinho. Devolve o que é preciso para gerar essa parte:

        {'lsr_id': 'loopback-0',            # DmOS: qual loopback está em uso
         'peers': {'10.1.1.1': 'CORE-01'},  # peers já configurados → nome/rótulo
         'tem_bloco': True}                 # o backup mostra config de LDP

    Sintaxes reais deste ambiente:

        Huawei VRP                     Datacom DmOS
        ─────────────────────────      ────────────────────────────
        mpls ldp remote-peer NOME      mpls ldp
         remote-ip 10.1.1.1             lsr-id loopback-0
        #                                neighbor targeted 21.21.21.21

    No DmOS o `lsr-id` importa: o `neighbor targeted` vive DENTRO dele, e qual
    loopback está em uso varia por equipamento — por isso é lido daqui em vez
    de fixado como `loopback-0`.
    """
    dados = {'lsr_id': '', 'peers': {}, 'tem_bloco': False}

    # Huawei: cada remote-peer é um bloco; o nome do bloco é rótulo livre (nos
    # backups daqui costuma ser o próprio IP) e o `remote-ip` é o que vale.
    for m in re.finditer(
            r'^mpls ldp remote-peer\s+(\S+)[^\n]*\n((?:[ \t][^\n]*\n)*)',
            conteudo, re.MULTILINE):
        dados['tem_bloco'] = True
        nome = m.group(1)
        mm = re.search(rf'^\s*remote-ip\s+({_IP})', m.group(2), re.MULTILINE)
        if mm:
            dados['peers'][mm.group(1)] = nome

    # Datacom: um bloco `mpls ldp` só, com os targeted dentro do `lsr-id`.
    m = re.search(r'^[ \t]*mpls ldp[ \t]*$', conteudo, re.MULTILINE)
    if m:
        resto = conteudo[m.end():]
        fim = re.search(r'^(?![ \t!])\S.*$', resto, re.MULTILINE)
        bloco = resto[:fim.start()] if fim else resto[:20000]
        dados['tem_bloco'] = True
        mm = re.search(r'^\s*lsr-id\s+(\S+)', bloco, re.MULTILINE)
        if mm:
            dados['lsr_id'] = mm.group(1)
        for mp in re.finditer(rf'^\s*neighbor targeted\s+({_IP})', bloco, re.MULTILINE):
            dados['peers'].setdefault(mp.group(1), '')

    # `mpls lsr-id 10.1.1.2` (Huawei) só entra se o DmOS não tiver respondido —
    # é o LSR-ID local, útil como referência quando não há lsr-id nomeado.
    if not dados['lsr_id']:
        mm = re.search(rf'^\s*mpls lsr-id\s+({_IP})', conteudo, re.MULTILINE)
        if mm:
            dados['lsr_id'] = mm.group(1)

    return dados


# ─── IPs de identidade (para casar peer → host) ───────────────────────────────

def extrair_ips_identidade(conteudo):
    """IPs pelos quais *este* equipamento é conhecido pelos vizinhos de MPLS:
    loopbacks, LSR-ID do LDP e router-id. É por eles que os `peer`/`neighbor`
    dos túneis apontam — o IP de gerência do CRM quase nunca é o mesmo.

    Devolve {ip: origem} (origem = de onde o IP saiu, ex. 'LoopBack0',
    'mpls lsr-id'), do mais específico para o mais genérico.
    """
    ips = {}

    def _add(ip, origem):
        if not ip or ip.startswith('0.') or ip.startswith('127.'):
            return
        # Máscara escrita como endereço (255.255.255.255) não é identidade.
        if ip.startswith('255.'):
            return
        ips.setdefault(ip, origem)

    linhas = conteudo.splitlines()

    # LSR-ID / router-id explícitos — a fonte mais confiável.
    for m in re.finditer(rf'^\s*mpls lsr-id\s+({_IP})', conteudo, re.MULTILINE):
        _add(m.group(1), 'mpls lsr-id')
    for m in re.finditer(rf'\blsr-id=({_IP})', conteudo):
        _add(m.group(1), 'mpls ldp lsr-id')
    for m in re.finditer(rf'\btransport-addresses=({_IP})', conteudo):
        _add(m.group(1), 'ldp transport-address')
    for m in re.finditer(rf'^\s*mpls ldp router-id\s+({_IP})', conteudo, re.MULTILINE):
        _add(m.group(1), 'mpls ldp router-id')
    for m in re.finditer(rf'^\s*(?:ospf\s+)?router[- ]id\s+({_IP})', conteudo, re.MULTILINE):
        _add(m.group(1), 'router-id')
    for m in re.finditer(rf'\brouter-id=({_IP})', conteudo):
        _add(m.group(1), 'router-id')
    for m in re.finditer(rf'^set routing-options router-id\s+({_IP})', conteudo, re.MULTILINE):
        _add(m.group(1), 'router-id')

    # Loopbacks — Huawei/Cisco/ZTE (bloco), Datacom (`interface loopback N`).
    for idx, cabecalho, corpo, fim in _iter_blocos_vrp(linhas):
        m = re.match(r'^interface\s+(loopback[\s-]*\d*|lo\d*)\s*$', cabecalho, re.IGNORECASE)
        if not m:
            continue
        nome = m.group(1).strip()
        for linha in corpo:
            mm = re.match(rf'\s*ip(?:v4)?\s+address\s+({_IP})', linha)
            if mm:
                _add(mm.group(1), nome)

    # MikroTik: /ip address add address=X/32 interface=lo…
    for m in re.finditer(
            rf'^/ip address add .*?\baddress=({_IP})/\d+.*?\binterface=("([^"]+)"|(\S+))',
            conteudo, re.MULTILINE):
        nome = m.group(3) or m.group(4) or ''
        if re.match(r'^(lo|loopback)', nome, re.IGNORECASE):
            _add(m.group(1), nome)

    # Juniper: lo0
    for m in re.finditer(
            rf'^set interfaces lo0 unit \d+ family inet address\s+({_IP})', conteudo, re.MULTILINE):
        _add(m.group(1), 'lo0')

    return ips


# ─── Dispatcher ───────────────────────────────────────────────────────────────

def _ordem(servico):
    try:
        id_num = int(servico['id'])
    except (TypeError, ValueError):
        id_num = 10 ** 9
    return (servico['tipo'], id_num, servico['nome'].lower())


def parse_l2vpn(conteudo, fabricante=''):
    """Extrai todos os serviços L2VPN de um backup.

    Roda só os sub-parsers cujas marcas sintáticas aparecem no texto (as
    gramáticas não se confundem entre si) em vez de confiar no `fabricante`
    do template — na prática muitos equipamentos Datacom/ZTE estão cadastrados
    como GENERICO, e um template errado não pode esconder os serviços.
    """
    if not conteudo:
        return []

    linhas = conteudo.replace('\r\n', '\n').split('\n')
    servicos = []

    if re.search(r'^vsi\s+\S', conteudo, re.MULTILINE) or 'mpls l2vc' in conteudo:
        servicos += _parse_huawei(conteudo, linhas)
    if 'mpls l2vpn' in conteudo or 'vpws-group' in conteudo or 'vpls-group' in conteudo:
        servicos += _parse_datacom(conteudo)
    if '/interface vpls' in conteudo:
        servicos += _parse_mikrotik(conteudo, linhas)
    if 'xconnect' in conteudo or re.search(r'^l2 vfi\s', conteudo, re.MULTILINE):
        servicos += _parse_cisco(conteudo, linhas)
    if 'set protocols l2circuit' in conteudo or 'instance-type vpls' in conteudo:
        servicos += _parse_juniper(conteudo, linhas)

    servicos.sort(key=_ordem)
    return servicos


def resumo_por_tipo(servicos):
    """Contagem por família, para o cabeçalho do modal e o artigo do wiki."""
    resumo = {'vpls': 0, 'vpws': 0, 'l2vc': 0}
    for servico in servicos:
        resumo[servico['tipo']] = resumo.get(servico['tipo'], 0) + 1
    resumo['total'] = len(servicos)
    return resumo
