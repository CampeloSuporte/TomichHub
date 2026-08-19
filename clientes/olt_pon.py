"""
Diagnóstico e operação das portas PON de OLT Huawei (MA5600T/MA5800) a partir
da topologia.

Mesmo desenho das automações de BGP (`bgp_actions.py`) e L2VPN
(`l2vpn_actions.py`), de propósito:

- `parse_pon` só LÊ o backup e monta o inventário de placas/portas — é o que
  o painel mostra antes de qualquer conexão, sem tocar no equipamento;
- `comandos_*` só MONTAM a lista de comandos, seguro de chamar pro preview
  editável;
- `executar` é quem conecta e envia;
- o que não tem sintaxe conferida em config real deste ambiente é recusado com
  `OltPonNaoSuportado` em vez de arriscar um comando errado numa OLT com
  centenas de assinantes pendurados.

Duas famílias de comando, com pesos MUITO diferentes:

    display port info <porta>    ← leitura: módulo óptico, potência, distância
    display port state <porta>   ← leitura: estado operacional da porta
    port <porta> laser-switch off/on   ← ESCRITA: apaga o laser da porta

O `laser-switch off` derruba **todas** as ONTs daquela porta. Por isso o
inventário conta as ONTs por porta a partir do backup (`ont add <porta> ...`) e
o endpoint devolve esse número junto: quem confirma a ação vê antes quantos
assinantes caem.
"""
import logging
import re

logger = logging.getLogger(__name__)


class OltPonNaoSuportado(Exception):
    """Equipamento fora do escopo (não é OLT Huawei), alvo inválido (placa/porta
    que não existe no backup) ou ação desconhecida."""


# ─── Inventário lido do backup ────────────────────────────────────────────────

# Quantas portas cada família de placa tem. Levantado dos backups reais deste
# ambiente (18 OLTs Huawei): as placas GPBD/GPBH do MA5600T têm 8 portas, e as
# GPSF/GPLF/GPHF/GPUF/CGHF/FLSF do MA5800 têm 16. Só um palpite de exibição —
# uma porta que o backup mostra além do previsto entra do mesmo jeito
# (`max(indice)+1` manda), então placa nova não fica capada.
_PORTAS_POR_FAMILIA = {
    'GPBD': 8, 'GPBH': 8, 'GPFD': 16, 'GPSF': 16, 'GPLF': 16,
    'GPHF': 16, 'GPUF': 16, 'CGHF': 16, 'FLSF': 16,
}

# Nome da placa no `board add`: H903GPSF → família GPSF.
_FAMILIA_RE = re.compile(r'^[A-Z]*\d*([A-Z]{4})$')

# `interface gpon 0/1` abre o bloco; o `#` na coluna 0 (ou outro `interface`)
# fecha, como em qualquer config VRP.
_BLOCO_GPON_RE = re.compile(
    r'^[ \t]*interface gpon\s+(\d+/\d+)[ \t]*$(.*?)(?=^[ \t]*interface\s|^#|\Z)',
    re.MULTILINE | re.DOTALL)

_BOARD_RE = re.compile(r'^[ \t]*board add\s+(\d+/\d+)\s+(\S+)', re.MULTILINE)
_PORT_CFG_RE = re.compile(r'^[ \t]*port\s+(\d+)\s+(.+?)[ \t]*$', re.MULTILINE)
_ONT_ADD_RE = re.compile(
    r'^[ \t]*ont add\s+(\d+)\s+(\d+)\s+(.*?)[ \t]*$', re.MULTILINE)
_DESC_RE = re.compile(r'\bdesc\s+"([^"]*)"')
_SN_RE = re.compile(r'\bsn-auth\s+"?([0-9A-Za-z]+)"?')

# Quantos nomes de assinante devolver por porta. É pra dar rosto ao número
# ("quem cai se eu apagar o laser"), não pra listar a base inteira — uma porta
# passa de 100 ONTs e o JSON do painel ficaria grande à toa.
MAX_CLIENTES_AMOSTRA = 12

# Modelo no cabeçalho do backup ("Modelo: OLT HUAWEI MA5800X7") ou no prompt
# ("MA5800-X7(config)#"). O primeiro é o que o CRM tem cadastrado; o segundo é
# o que o equipamento responde — o prompt ganha quando os dois aparecem.
_MODELO_CABECALHO_RE = re.compile(r'^Modelo:[ \t]*(.+?)[ \t]*$', re.MULTILINE)
_PROMPT_RE = re.compile(r'^(MA\d{4}[A-Za-z0-9-]*)[(#>]', re.MULTILINE)


def _familia(tipo_placa):
    m = _FAMILIA_RE.match((tipo_placa or '').upper())
    return m.group(1) if m else ''


def _portas_da_familia(tipo_placa):
    """Quantas portas a placa tem, ou None quando o tipo é desconhecido.

    Devolver None (em vez de um padrão de 16) é o ponto: nem toda placa PON
    aparece no `board add` do backup — placa confirmada em campo
    (`board confirm`) não entra no bloco `[pre-config]`. Assumir 16 nesses
    casos INVENTA portas: aconteceu na OLT-HU-LEAL, cujo backup mostra
    `port 0..7` (placa de 8), e o painel ofereceu a porta 8 — o equipamento
    respondeu `% Parameter error`.
    """
    return _PORTAS_POR_FAMILIA.get(_familia(tipo_placa))


def eh_olt_huawei(conteudo):
    """Só entra no fluxo o que é OLT Huawei de verdade. ZTE, Datacom e Parks
    também têm `interface gpon` no backup (69 acessos deste ambiente), com
    sintaxe completamente diferente — por isso a detecção exige a assinatura
    Huawei (`board add`/prompt MA5xxx) além do bloco GPON."""
    if not conteudo:
        return False
    tem_gpon = bool(_BLOCO_GPON_RE.search(conteudo))
    tem_assinatura = bool(_PROMPT_RE.search(conteudo)) or bool(_BOARD_RE.search(conteudo))
    return tem_gpon and tem_assinatura


def _modelo(conteudo):
    m = _PROMPT_RE.search(conteudo or '')
    if m:
        return m.group(1)
    m = _MODELO_CABECALHO_RE.search(conteudo or '')
    return m.group(1) if m else ''


def parse_pon(conteudo):
    """Inventário das placas e portas PON lido do backup.

    Devolve `{'suportado', 'modelo', 'placas': [...], 'total_onts', ...}`.
    Cada porta traz quantas ONTs estão cadastradas nela e uma amostra dos
    nomes — é o "raio de alcance" de um `laser-switch off`.
    """
    conteudo = conteudo or ''
    if not eh_olt_huawei(conteudo):
        return {'suportado': False, 'modelo': _modelo(conteudo), 'placas': [],
                'total_onts': 0, 'total_portas': 0}

    tipos = {slot: tipo for slot, tipo in _BOARD_RE.findall(conteudo)}

    placas = []
    total_onts = 0
    for m in _BLOCO_GPON_RE.finditer(conteudo):
        slot, corpo = m.group(1), m.group(2)
        tipo = tipos.get(slot, '')

        # Config por porta: guarda o que dá contexto operacional. O
        # `ont-password-renew` é ruído pro painel e fica de fora de propósito.
        auto_find, distancia = set(), {}
        for porta, resto in _PORT_CFG_RE.findall(corpo):
            if 'ont-auto-find enable' in resto:
                auto_find.add(int(porta))
            md = re.search(r'range min-distance\s+(\d+)\s+max-distance\s+(\d+)', resto)
            if md:
                distancia[int(porta)] = f'{md.group(1)}–{md.group(2)} km'

        onts_por_porta = {}
        for porta, ont_id, resto in _ONT_ADD_RE.findall(corpo):
            porta = int(porta)
            info = onts_por_porta.setdefault(porta, {'total': 0, 'clientes': []})
            info['total'] += 1
            total_onts += 1
            if len(info['clientes']) < MAX_CLIENTES_AMOSTRA:
                m_desc = _DESC_RE.search(resto)
                m_sn = _SN_RE.search(resto)
                info['clientes'].append({
                    'ont': ont_id,
                    'desc': (m_desc.group(1).strip() if m_desc else ''),
                    'sn': (m_sn.group(1) if m_sn else ''),
                })

        vistas = set(auto_find) | set(distancia) | set(onts_por_porta)
        vistas_max = (max(vistas) + 1) if vistas else 0
        da_familia = _portas_da_familia(tipo)
        if da_familia:
            # Tipo conhecido: a família manda (uma GPBD tem 8 portas mesmo com
            # só 3 configuradas), mas nunca menos do que o backup mostra.
            total_portas = max(da_familia, vistas_max)
            inferidas = False
        else:
            # Tipo desconhecido: vale só o que o backup prova. Melhor esconder
            # uma porta que existe do que oferecer uma que não existe — a
            # segunda opção manda comando pra porta inexistente.
            total_portas = vistas_max
            inferidas = True

        portas = []
        for idx in range(total_portas):
            info = onts_por_porta.get(idx, {'total': 0, 'clientes': []})
            portas.append({
                'porta': idx,
                'onts': info['total'],
                'clientes': info['clientes'],
                'auto_find': idx in auto_find,
                'distancia': distancia.get(idx, ''),
                # Porta que não aparece em nenhuma linha do backup: existe na
                # placa mas está sem nada configurado. A UI mostra apagada.
                'configurada': idx in vistas,
            })

        placas.append({
            'slot': slot,
            'tipo': tipo,
            'familia': _familia(tipo),
            'portas_total': total_portas,
            # True = a placa não tem `board add` no backup e o número de portas
            # saiu do que está configurado. A UI avisa: se a placa física tiver
            # mais portas, elas não aparecem aqui.
            'portas_inferidas': inferidas,
            'portas': portas,
            'onts': sum(p['onts'] for p in portas),
        })

    placas.sort(key=lambda p: [int(x) for x in p['slot'].split('/')])
    return {
        'suportado': bool(placas),
        'modelo': _modelo(conteudo),
        'placas': placas,
        'total_onts': total_onts,
        'total_portas': sum(p['portas_total'] for p in placas),
    }


# ─── Geração dos comandos ─────────────────────────────────────────────────────

# Preâmbulo idêntico ao do template "backup olt huawei" que roda todo dia
# nesses mesmos equipamentos: `undo interactive` tira os prompts de
# confirmação (é o que faz o laser-switch não parar esperando um "y"),
# `undo smart` desliga o autocomplete e `scroll` desliga a paginação
# (`---- More ----` truncaria a saída do display no meio).
PREAMBULO = ['enable', 'config', 'undo interactive', 'undo smart', 'scroll']

ACOES = {
    'info':  {'label': 'Informações da porta', 'comando': 'display port info {porta}', 'escreve': False},
    'state': {'label': 'Estado da porta',      'comando': 'display port state {porta}', 'escreve': False},
    'laser_off': {'label': 'Desativar porta (laser off)',
                  'comando': 'port {porta} laser-switch off', 'escreve': True},
    'laser_on':  {'label': 'Ativar porta (laser on)',
                  'comando': 'port {porta} laser-switch on',  'escreve': True},
}

_SLOT_RE = re.compile(r'^\d{1,2}/\d{1,2}$')


def acao_escreve(acao):
    return bool(ACOES.get(acao, {}).get('escreve'))


def validar_alvo(inventario, slot, portas, acao):
    """Confere que a placa e as portas existem no inventário do backup e que a
    ação é conhecida. Devolve (slot, [portas]) normalizado.

    Recusar aqui é de propósito: `interface gpon 0/9` numa OLT que só tem
    placa até 0/5 entra no modo de configuração de um slot vazio e o comando
    seguinte (inclusive um `laser-switch`) executa em contexto errado.
    """
    if acao not in ACOES:
        raise OltPonNaoSuportado(f'Ação desconhecida: "{acao}".')

    slot = str(slot or '').strip()
    if not _SLOT_RE.match(slot):
        raise OltPonNaoSuportado(f'Placa inválida: "{slot}" — use o formato frame/slot, ex. 0/1.')

    placa = next((p for p in inventario.get('placas', []) if p['slot'] == slot), None)
    if placa is None:
        disponiveis = ', '.join(p['slot'] for p in inventario.get('placas', [])) or 'nenhuma'
        raise OltPonNaoSuportado(
            f'A placa {slot} não é uma placa PON deste equipamento (placas: {disponiveis}).')

    if isinstance(portas, (str, int)):
        portas = [portas]
    normalizadas = []
    for porta in (portas or []):
        texto = str(porta).strip()
        if not texto.isdigit():
            raise OltPonNaoSuportado(f'Porta inválida: "{texto}".')
        numero = int(texto)
        if numero >= placa['portas_total']:
            raise OltPonNaoSuportado(
                f'A placa {slot} ({placa["tipo"] or "tipo desconhecido"}) tem '
                f'{placa["portas_total"]} portas (0–{placa["portas_total"] - 1}) — '
                f'a porta {numero} não existe.')
        if numero not in normalizadas:
            normalizadas.append(numero)

    if not normalizadas:
        raise OltPonNaoSuportado('Escolha pelo menos uma porta.')

    # Escrita é sempre uma porta por vez: apagar o laser de uma placa inteira
    # num clique é o tipo de ação que ninguém deveria conseguir fazer sem
    # querer — e o preview de 16 comandos destrutivos não ajuda a revisar.
    if acao_escreve(acao) and len(normalizadas) > 1:
        raise OltPonNaoSuportado(
            'Ligar/desligar laser é uma porta por vez — selecione só uma.')

    return slot, normalizadas


def comandos_pon(slot, portas, acao):
    """Comandos prontos pro preview editável. Sempre com o preâmbulo, porque a
    sessão nasce zerada: sem `config` + `interface gpon` o `display port` não
    existe no contexto, e sem `scroll` a saída vem paginada."""
    modelo = ACOES[acao]['comando']
    cmds = list(PREAMBULO)
    cmds.append(f'interface gpon {slot}')
    for porta in portas:
        cmds.append(modelo.format(porta=porta))
    cmds.append('quit')
    return cmds


MAX_LINHAS = 40
MAX_COLUNAS = 200


def validar_comandos_editados(comandos):
    """O textarea do modal é editável (mesmo padrão do BGP e do L2VPN) — o que
    volta de lá ainda passa por um teto de sanidade."""
    if not isinstance(comandos, list) or not comandos or not all(isinstance(c, str) for c in comandos):
        raise OltPonNaoSuportado('Lista de comandos inválida.')
    limpos = [c.strip() for c in comandos if c.strip()]
    if len(limpos) > MAX_LINHAS:
        raise OltPonNaoSuportado(f'Comando longo demais ({len(limpos)} linhas, máximo {MAX_LINHAS}).')
    if any(len(c) > MAX_COLUNAS for c in limpos):
        raise OltPonNaoSuportado('Há uma linha longa demais — revise antes de enviar.')
    return limpos


# ─── Execução no equipamento ──────────────────────────────────────────────────

# Recusa da CLI. Conectar e enviar sem estourar exceção NÃO quer dizer que o
# comando valeu: o VRP responde o erro no texto e segue no prompt. Sem olhar a
# saída, um `laser-switch` recusado era gravado como sucesso e o operador saía
# achando que a porta estava desativada — foi o que aconteceu na OLT-HU-LEAL
# (porta 8 inexistente, `% Parameter error`, auditoria dizendo "sucesso").
#
# O `%` sozinho não serve de gatilho (aparece em percentual dentro de saída
# legítima), então cada padrão exige a palavra do erro junto.
_ERRO_CLI_RE = re.compile(
    r'%\s*(?:parameter error|unknown command|wrong parameter|incomplete command|'
    r'too many parameters|invalid input|invalid parameter|command not found|error:)'
    r'|^\s*Failure:'
    r'|\bcommand is not supported\b',
    re.IGNORECASE | re.MULTILINE)


def detectar_erro_cli(output):
    """Primeira recusa encontrada na saída, ou '' se o equipamento aceitou.

    Devolve a linha inteira (e não só o trecho casado) porque é ela que a UI
    mostra — `% Parameter error, the error locates at '^'` diz muito mais que
    "parameter error".
    """
    m = _ERRO_CLI_RE.search(output or '')
    if not m:
        return ''
    inicio = (output.rfind('\n', 0, m.start()) + 1)
    fim = output.find('\n', m.end())
    linha = output[inicio:(fim if fim != -1 else len(output))].strip()
    return linha or m.group(0).strip()


def executar(acesso, comandos):
    """Conecta e envia os comandos, devolvendo (output, status).

    Reaproveita a MESMA mecânica do backup diário destas OLTs
    (`views._executar_comandos_huawei`, shell Paramiko com terminal largo e
    detecção de silêncio) em vez do Netmiko usado nas outras automações: o
    driver `huawei_vrpv8` briga com o prompt do MA5800 (que troca de
    `MA5800-X7#` pra `MA5800-X7(config-if-gpon-0/1)#` conforme o modo), e é
    justamente essa troca de contexto que o `interface gpon` provoca. O shell
    cru já roda todo dia nestes 18 equipamentos.

    Nunca levanta exceção de conexão — devolve o erro como output.
    """
    # Import tardio: `views` importa este módulo, então importar no topo daria
    # ciclo (mesmo motivo do import de `vpn_cobre_ip` dentro de
    # `script_views._conectar_script`).
    from .views import (
        is_private_ip, criar_ssh_tunnel, vpn_cobre_ip, _executar_comandos_huawei,
    )
    from .models import ProxyServer

    import paramiko

    tunel = None
    client = None
    try:
        host = acesso.host
        porta = int(acesso.porta or 22)

        if is_private_ip(host):
            proxy = ProxyServer.objects.filter(cliente=acesso.cliente, ativo=True).first()
            if proxy:
                tunel = criar_ssh_tunnel(
                    {'host': proxy.host, 'porta': proxy.porta,
                     'usuario': proxy.usuario, 'senha': proxy.senha},
                    host, porta)
                host, porta = tunel['local_host'], tunel['local_port']
            elif not vpn_cobre_ip(acesso.cliente, acesso.host):
                return ('IP privado sem proxy SSH ativo e sem túnel OpenVPN cobrindo o host — '
                        'configure um dos dois na aba "Túneis".'), 'erro'

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=host, port=porta, username=acesso.usuario,
                       password=acesso.senha, timeout=30, look_for_keys=False,
                       allow_agent=False, banner_timeout=30)
        client.get_transport().set_keepalive(10)

        output = _executar_comandos_huawei(client, comandos)
        # A conexão funcionou, mas a CLI pode ter recusado o comando — quem
        # decide o status é a saída, não a ausência de exceção.
        recusa = detectar_erro_cli(output)
        if recusa:
            logger.warning(f'⚠️ OLT {acesso} recusou o comando PON: {recusa}')
            return output, 'erro'
        return output, 'sucesso'
    except Exception as e:
        logger.error(f'❌ Erro no diagnóstico PON em {acesso}: {e}')
        return str(e), 'erro'
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
        if tunel:
            # Mesmo teardown de `realizar_backup`: o túnel é um dict com o
            # client do proxy e o socket de forwarding, não um objeto com stop().
            for chave in ('ssh_client', 'server_socket'):
                try:
                    if chave in tunel:
                        tunel[chave].close()
                except Exception:
                    pass
