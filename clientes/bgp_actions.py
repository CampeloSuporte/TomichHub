"""
Geração e execução de comandos de automação BGP — ativar/desativar sessão,
prepend, parar de anunciar — por fabricante.

Cada `comandos_*` só MONTA a lista de comandos (sem tocar em nada real —
seguro de chamar pra preview). `executar_acao_bgp` é quem de fato conecta e
envia, reaproveitando a conexão Netmiko já usada pelo Painel de Scripts
(`clientes/script_views.py::_conectar_script`/`_fechar_tunel`).

Cada fabricante usa o mecanismo mais natural e reversível da própria CLI —
não força um padrão único onde a sintaxe não suporta de forma limpa (ver
docs/bgp_automacao.md para a tabela comparativa completa).
"""
import ipaddress
import logging
import re

from .bgp_matcher import entrada_que_bate
from .script_views import _conectar_script, _fechar_tunel, DEVICE_TYPES

logger = logging.getLogger(__name__)


class AcaoBgpNaoSuportada(Exception):
    """Ação sem um comando seguro/conhecido pra aquele fabricante+situação
    (ex: Huawei "parar de anunciar" quando o prefixo não vem nem de um node
    de route-policy nem de um `network` statement) — melhor recusar
    explicitamente do que arriscar um edit incorreto num equipamento de
    borda em produção."""


def _sessao_por_nome(dados, nome_sessao):
    for s in dados.get('sessoes', []):
        if s.get('nome') == nome_sessao:
            return s
    raise AcaoBgpNaoSuportada(f'Sessão "{nome_sessao}" não encontrada no snapshot.')


def _rede_correspondente(dados, prefixo):
    for rede in dados.get('networks', []):
        if rede.get('prefixo') == prefixo:
            return rede
    return None


def _termo_e_entrada_responsaveis(dados, policy_nome, prefixo):
    """Caminha pelas policies (mesma ordem/semântica do bgp_matcher) e
    devolve (termo, entrada_da_prefix_list) do PRIMEIRO termo que bate com
    `prefixo` — é o termo que precisa ser editado pra mudar o que acontece
    com esse prefixo especificamente."""
    termos = sorted(dados.get('policies', {}).get(policy_nome or '', []), key=lambda t: t.get('ordem', 0))
    prefix_lists = dados.get('prefix_lists', {})
    for termo in termos:
        nomes_pl = termo.get('prefix_lists') or []
        if not nomes_pl:
            return termo, None   # catch-all — não há uma entrada específica de prefix-list
        for nome_pl in nomes_pl:
            entrada = entrada_que_bate(prefixo, prefix_lists.get(nome_pl, []))
            if entrada:
                return termo, entrada
    return None, None


# ═══════════════════════════════════════════════════════════════════════
# Ativar / desativar sessão
# ═══════════════════════════════════════════════════════════════════════

def comandos_toggle_sessao(vendor, dados, nome_sessao, ativar):
    sessao = _sessao_por_nome(dados, nome_sessao)
    nome = sessao['nome']

    if vendor == 'mikrotik':
        versao = dados.get('versao_routeros', 6)
        objeto = 'connection' if versao == 7 else 'peer'
        acao = 'enable' if ativar else 'disable'
        return [f'/routing bgp {objeto} {acao} [find name="{nome}"]']

    if vendor == 'huawei':
        asn = sessao.get('as_local')
        if not asn:
            raise AcaoBgpNaoSuportada('AS local não identificado no snapshot — não dá pra montar o comando.')
        linha = f'undo peer {nome} ignore' if ativar else f'peer {nome} ignore'
        # 'commit' aqui é só pro preview/auditoria mostrarem a ação completa —
        # a execução de verdade usa conn.commit() do Netmiko (ver
        # executar_acao_bgp/_PRECISA_COMMIT), não este texto literal.
        return [f'bgp {asn}', linha, 'commit']

    if vendor in ('cisco', 'datacom'):
        asn = sessao.get('as_local')
        if not asn:
            raise AcaoBgpNaoSuportada('AS local não identificado no snapshot — não dá pra montar o comando.')
        linha = f'no neighbor {nome} shutdown' if ativar else f'neighbor {nome} shutdown'
        return [f'router bgp {asn}', linha]

    if vendor == 'juniper':
        grupo = (sessao.get('extra') or {}).get('grupo', '')
        if not grupo:
            raise AcaoBgpNaoSuportada('Grupo BGP não identificado no snapshot — não dá pra montar o comando.')
        acao = 'activate' if ativar else 'deactivate'
        return [f'{acao} protocols bgp group {grupo} neighbor {nome}', 'commit']

    raise AcaoBgpNaoSuportada(f'Fabricante "{vendor}" não suportado para ativar/desativar sessão.')


# ═══════════════════════════════════════════════════════════════════════
# Prepend
# ═══════════════════════════════════════════════════════════════════════

def _reescrever_prepend_v7(rule_raw, novo_valor):
    """Best-effort: insere/substitui `set bgp-path-prepend=N;` logo após o
    `{` de abertura do bloco de ação da regra v7. Não confirmado em nenhum
    backup real do ambiente (todo prepend real visto em produção é
    RouterOS 6) — ver docs/bgp_automacao.md."""
    sem_prepend = re.sub(r'set\s+bgp-path-prepend=\d+\s*;?\s*', '', rule_raw)
    if novo_valor <= 0:
        return sem_prepend
    return re.sub(r'\{\s*', '{ set bgp-path-prepend=%d; ' % novo_valor, sem_prepend, count=1)


def comandos_prepend(vendor, dados, nome_sessao, prefixo, delta=1):
    sessao = _sessao_por_nome(dados, nome_sessao)
    policy_nome = sessao.get('policy_out')
    termo, _entrada = _termo_e_entrada_responsaveis(dados, policy_nome, prefixo)
    if not termo or termo.get('acao') != 'accept':
        raise AcaoBgpNaoSuportada(
            f'Não encontrei uma regra de anúncio ativa para {prefixo} em "{policy_nome}".'
        )
    novo_valor = max(0, int(termo.get('prepend', 0)) + delta)
    # `prepend_as` só existe quando o parser achou um `fake-as` configurado
    # nessa sessão (hoje só Huawei) — o peer enxerga o AS_PATH com o
    # fake-as, não o AS real do roteador, então o prepend precisa repetir
    # o número que o peer efetivamente vê. Nos demais casos cai no AS real.
    asn = sessao.get('prepend_as') or sessao.get('as_local')
    extra = termo.get('extra', {})

    if vendor == 'mikrotik':
        chain = extra.get('chain')
        if dados.get('versao_routeros', 6) == 6:
            return [f'/routing filter set [find chain="{chain}" prefix="{prefixo}"] set-bgp-prepend={novo_valor}']
        rule_raw = extra.get('rule_raw', '')
        novo_rule = _reescrever_prepend_v7(rule_raw, novo_valor)
        return [f'/routing filter rule set [find chain="{chain}" rule~"{re.escape(prefixo)}"] rule="{novo_rule}"']

    if vendor == 'huawei':
        if not asn:
            raise AcaoBgpNaoSuportada('AS local não identificado no snapshot.')
        node = extra.get('node')
        cabecalho = [f'route-policy {policy_nome} permit node {node}']
        if novo_valor == 0:
            return cabecalho + ['undo apply as-path', 'commit']
        asns = ' '.join([str(asn)] * novo_valor)
        return cabecalho + [f'apply as-path {asns} additive', 'commit']

    if vendor in ('cisco', 'datacom'):
        if not asn:
            raise AcaoBgpNaoSuportada('AS local não identificado no snapshot.')
        seq = extra.get('seq')
        cabecalho = [f'route-map {policy_nome} permit {seq}']
        if novo_valor == 0:
            return cabecalho + ['no set as-path prepend']
        asns = ' '.join([str(asn)] * novo_valor)
        return cabecalho + [f'set as-path prepend {asns}']

    if vendor == 'juniper':
        if not asn:
            raise AcaoBgpNaoSuportada('AS local não identificado no snapshot.')
        term = extra.get('term')
        if novo_valor == 0:
            return [f'delete policy-options policy-statement {policy_nome} term {term} then as-path-prepend', 'commit']
        asns = ' '.join([str(asn)] * novo_valor)
        return [f'set policy-options policy-statement {policy_nome} term {term} then as-path-prepend "{asns}"', 'commit']

    raise AcaoBgpNaoSuportada(f'Fabricante "{vendor}" não suportado para prepend.')


# ═══════════════════════════════════════════════════════════════════════
# Aplicar community
# ═══════════════════════════════════════════════════════════════════════

def _slug(texto):
    """Vira um identificador seguro pra objeto nomeado (ex: community-set do
    Junos, que não aceita espaço/dois-pontos): minúsculo, tudo que não é
    [a-z0-9] vira hífen, hífens nas pontas removidos. Pode devolver ''."""
    return re.sub(r'[^a-z0-9]+', '-', (texto or '').lower()).strip('-')


def comandos_aplicar_community(vendor, dados, nome_sessao, prefixo, valor, label=''):
    """`valor` é o texto da community (`61663:666`, ou vários separados por
    espaço tipo `53181:1607 53181:3710` — Huawei aceita múltiplos numa linha
    só). `label` é o rótulo amigável cadastrado pelo usuário (`BgpCommunity.
    label`) — só usado pra nomear o community-set no Juniper, que não aceita
    valor literal inline (sempre referencia um nome definido à parte)."""
    sessao = _sessao_por_nome(dados, nome_sessao)
    policy_nome = sessao.get('policy_out')
    termo, _entrada = _termo_e_entrada_responsaveis(dados, policy_nome, prefixo)
    if not termo or termo.get('acao') != 'accept':
        raise AcaoBgpNaoSuportada(
            f'Não encontrei uma regra de anúncio ativa para {prefixo} em "{policy_nome}".'
        )
    valor = (valor or '').strip()
    if not valor:
        raise AcaoBgpNaoSuportada('Informe o valor da community (ex: 61663:666).')
    extra = termo.get('extra', {})

    if vendor == 'mikrotik':
        if dados.get('versao_routeros', 6) != 6:
            raise AcaoBgpNaoSuportada(
                'Aplicar community não é suportado no RouterOS 7 — não há evidência confiável de '
                'como fazer isso no dialeto de script (rule="...") em backups reais deste ambiente '
                '(só encontramos MATCH de community nesse dialeto, não SET).'
            )
        chain = extra.get('chain')
        # append (não set) — soma à(s) community(ies) que a regra já tiver
        # (ex: set-pref-src, set-bgp-prepend) em vez de arriscar substituir.
        return [f'/routing filter set [find chain="{chain}" prefix="{prefixo}"] append-bgp-communities={valor}']

    if vendor == 'huawei':
        node = extra.get('node')
        return [f'route-policy {policy_nome} permit node {node}', f'apply community {valor} additive', 'commit']

    if vendor in ('cisco', 'datacom'):
        # Best-effort: `set community <valor> additive` é a sintaxe IOS
        # padrão-de-mercado, mas NÃO há uma única ocorrência real de `set
        # community` em nenhum dos 38 backups Cisco deste ambiente (só
        # existe `neighbor X send-community both`, que é outra coisa) —
        # revise antes de confirmar.
        seq = extra.get('seq')
        return [f'route-map {policy_nome} permit {seq}', f'set community {valor} additive']

    if vendor == 'juniper':
        term = extra.get('term')
        nome_community = _slug(label) or _slug(valor) or 'community'
        # `set` de definição é idempotente — reemitir os mesmos `members`
        # não duplica nem dá erro, então não precisa checar se a
        # community-set já existe no equipamento antes de reenviar.
        return [
            f'set policy-options community {nome_community} members {valor}',
            f'set policy-options policy-statement {policy_nome} term {term} then community add {nome_community}',
            'commit',
        ]

    raise AcaoBgpNaoSuportada(f'Fabricante "{vendor}" não suportado para aplicar community.')


# ═══════════════════════════════════════════════════════════════════════
# Parar de anunciar
# ═══════════════════════════════════════════════════════════════════════

def comandos_parar_anuncio(vendor, dados, nome_sessao, prefixo):
    sessao = _sessao_por_nome(dados, nome_sessao)
    policy_nome = sessao.get('policy_out')

    if vendor == 'juniper':
        termo, _entrada = _termo_e_entrada_responsaveis(dados, policy_nome, prefixo)
        if not termo:
            raise AcaoBgpNaoSuportada(f'Não encontrei o term que anuncia {prefixo}.')
        term = termo['extra'].get('term')
        return [f'deactivate policy-options policy-statement {policy_nome} term {term}', 'commit']

    if vendor == 'mikrotik':
        rede = _rede_correspondente(dados, prefixo)
        if rede and rede.get('origem') == 'bgp_network':
            return [f'/routing bgp network disable [find network="{prefixo}"]']
        if rede and rede.get('origem') == 'address_list':
            return [f'/ip firewall address-list disable [find address="{prefixo}" list="{rede["lista"]}"]']
        if dados.get('versao_routeros', 6) != 6:
            raise AcaoBgpNaoSuportada(
                f'{prefixo} não é uma network/address-list conhecida — no RouterOS 7 só dá pra '
                'parar de anunciar prefixos que vêm de uma address-list (.network=).'
            )
        termo, _entrada = _termo_e_entrada_responsaveis(dados, policy_nome, prefixo)
        if not termo:
            raise AcaoBgpNaoSuportada(f'Não encontrei a regra de filtro que anuncia {prefixo}.')
        chain = termo['extra'].get('chain')
        return [f'/routing filter disable [find chain="{chain}" prefix="{prefixo}"]']

    if vendor == 'huawei':
        # Preferência: trocar o modo do NODE de permit pra deny dentro do
        # route-policy de export DESSA sessão, mantendo o mesmo número de
        # node (if-match/apply continuam intactos, só o permit/deny muda) —
        # é escopado ao peer porque cada sessão tem seu próprio route-policy
        # de export. NÃO editar a prefix-list em vez disso: ela é um objeto
        # nomeado à parte que pode estar referenciada por outro node/route-
        # policy (de outra sessão, ou até a mesma sessão em outro node) —
        # mexer nela vazaria o efeito pra fora desta sessão. `undo network`
        # (global, afeta TODAS as sessões que originam essa rede) só entra
        # como último recurso, quando não há controle via policy.
        termo, entrada = _termo_e_entrada_responsaveis(dados, policy_nome, prefixo)
        if termo and entrada and termo.get('acao') == 'accept':
            node = termo.get('extra', {}).get('node')
            if node is not None:
                return [f'route-policy {policy_nome} deny node {node}', 'commit']

        rede = _rede_correspondente(dados, prefixo)
        if not rede:
            raise AcaoBgpNaoSuportada(
                f'Não encontrei nem um node de route-policy nem um `network` statement pra '
                f'{prefixo} em "{policy_nome}" — parar de anunciar precisa de edição manual aqui.'
            )
        asn = sessao.get('as_local')
        if not asn:
            raise AcaoBgpNaoSuportada('AS local não identificado no snapshot.')
        ip, tam = prefixo.split('/')
        if ':' in ip:
            familia, mask_arg = 'ipv6-family', tam
        else:
            familia = 'ipv4-family'
            mask_arg = str(ipaddress.IPv4Network(prefixo, strict=False).netmask)
        # AVISO: isso desliga a origem BGP dessa rede pra TODAS as sessões
        # do equipamento, não só esta — só chega aqui quando não há uma
        # entrada de ip-prefix na policy pra remover em vez disso.
        return [f'bgp {asn}', f'{familia} unicast', f'undo network {ip} {mask_arg}', 'commit']

    if vendor in ('cisco', 'datacom'):
        # Mesmo princípio da correção do Huawei: NÃO editar a prefix-list
        # (`ip prefix-list PL seq N deny ...`) — é um objeto nomeado à
        # parte, frequentemente reaproveitado por VÁRIOS route-maps/peers
        # ao mesmo tempo (confirmado em backup real: `PL-ORIGIN-*` e
        # `PL-MY-PREFIX-V6-*` — o prefixo próprio do cliente, anunciado
        # pra mais de um upstream — aparecem referenciados por 2-3
        # route-maps OUT diferentes no mesmo equipamento). Editar a lista
        # pararia de anunciar em TODOS os peers que a referenciam, não só
        # o selecionado. Em vez disso, insere um `deny` novo dentro do
        # ROUTE-MAP de export DESSA sessão (mesma prefix-list como match,
        # mas escopado a esse route-map só), num seq menor que o da
        # entrada `permit` existente — só afeta este peer.
        termo, entrada = _termo_e_entrada_responsaveis(dados, policy_nome, prefixo)
        if not termo or not entrada:
            raise AcaoBgpNaoSuportada(
                f'A regra que permite {prefixo} não usa uma prefix-list nomeada com uma entrada '
                'específica — não dá pra inserir um `deny` acima com segurança.'
            )
        nomes_pl = termo.get('prefix_lists') or []
        pl_nome = nomes_pl[0] if nomes_pl else None
        seq_rm_existente = termo.get('extra', {}).get('seq')
        if not pl_nome or seq_rm_existente is None:
            raise AcaoBgpNaoSuportada('Não encontrei o seq do route-map correspondente.')
        if seq_rm_existente <= 1:
            raise AcaoBgpNaoSuportada(
                f'A entrada seq {seq_rm_existente} do route-map "{policy_nome}" não tem seq livre '
                'abaixo dela — renumere o route-map manualmente antes de tentar esta ação.'
            )
        novo_seq_rm = seq_rm_existente - 1
        seqs_existentes = {
            t.get('extra', {}).get('seq') for t in dados.get('policies', {}).get(policy_nome, [])
        }
        if novo_seq_rm in seqs_existentes:
            raise AcaoBgpNaoSuportada(
                f'Já existe uma entrada no route-map "{policy_nome}" com seq {novo_seq_rm} — '
                'renumere manualmente antes de tentar esta ação.'
            )
        cmd_match = 'match ipv6 address prefix-list' if ':' in prefixo else 'match ip address prefix-list'
        return [
            f'route-map {policy_nome} deny {novo_seq_rm}',
            f'{cmd_match} {pl_nome}',
        ]

    raise AcaoBgpNaoSuportada(f'Fabricante "{vendor}" não suportado para parar de anunciar.')


# ═══════════════════════════════════════════════════════════════════════
# Anunciar prefixo novo (clientes/bgp_matcher.py::listar_prefix_lists)
# ═══════════════════════════════════════════════════════════════════════

def _proximo_indice_antes_do_catchall(termos, campo, incremento):
    """Calcula o próximo valor de `campo` (node/seq) livre pra inserir um
    termo PERMIT novo no fim da lista de termos "normais" — mas garantindo
    que fique ANTES de qualquer termo catch-all já existente (sem
    `prefix_lists` — bate com tudo, geralmente um deny/reject final), senão
    o termo novo nunca seria alcançado. Devolve None se não sobrar espaço
    livre entre o último termo normal e o catch-all (precisa renumerar
    manualmente nesse caso)."""
    normais = [
        t['extra'].get(campo) for t in termos
        if t.get('prefix_lists') and t['extra'].get(campo) is not None
    ]
    catchall = [
        t['extra'].get(campo) for t in termos
        if not t.get('prefix_lists') and t['extra'].get(campo) is not None
    ]
    novo = (max(normais) if normais else 0) + incremento
    if catchall and novo >= min(catchall):
        return None
    return novo


def comandos_novo_anuncio(vendor, dados, nome_sessao, lista_escolhida=None, prefixo_novo=None):
    """`lista_escolhida` é o nome de uma das `candidatas` devolvidas por
    `bgp_matcher.py::listar_prefix_lists` — uma prefix-list JÁ EXISTENTE no
    equipamento (de outra sessão, ou cadastrada por outro motivo) que o
    usuário quer passar a anunciar também nesta sessão. A ação NUNCA edita
    essa prefix-list (mesmo cuidado do "parar de anunciar": é um objeto
    compartilhável) — em vez disso cria um NODE/termo/entrada de route-map
    NOVO, exclusivo da export policy DESSA sessão, que só faz `if-match`/
    `match` nela. `prefixo_novo` é usado só pelo Mikrotik, que não tem
    prefix-list nomeada separada (ver abaixo) — os outros fabricantes
    ignoram esse parâmetro."""
    sessao = _sessao_por_nome(dados, nome_sessao)
    policy_nome = sessao.get('policy_out')
    if not policy_nome:
        raise AcaoBgpNaoSuportada('Esta sessão não tem export policy/filter identificada.')

    if vendor == 'mikrotik':
        if dados.get('versao_routeros', 6) != 6:
            raise AcaoBgpNaoSuportada(
                'Anunciar prefixo novo não é suportado no RouterOS 7 — sem evidência real confiável '
                'de como inserir uma regra nova preservando a ordem no dialeto de script deste ambiente.'
            )
        try:
            ipaddress.ip_network(prefixo_novo, strict=False)
        except (ValueError, TypeError):
            raise AcaoBgpNaoSuportada(f'Prefixo inválido: "{prefixo_novo}" (esperado formato X.X.X.X/Y).')
        # Mikrotik v6 não tem prefix-list nomeada separada (cada "prefix-list"
        # no nosso parser é sintética, 1:1 com uma regra de filter) — o
        # equivalente é inserir uma regra `accept` nova na própria chain de
        # export, ANTES do discard/catch-all final (senão a regra nunca
        # seria alcançada nessa cadeia sequencial).
        return [
            f'/routing filter add action=accept chain="{policy_nome}" prefix="{prefixo_novo}" '
            f'place-before=[find chain="{policy_nome}" action=discard]'
        ]

    if not lista_escolhida:
        raise AcaoBgpNaoSuportada('Escolha uma prefix-list já existente no equipamento pra anunciar.')
    if lista_escolhida not in dados.get('prefix_lists', {}):
        raise AcaoBgpNaoSuportada(f'Prefix-list "{lista_escolhida}" não encontrada no snapshot.')
    if '#' in lista_escolhida or lista_escolhida.startswith('__'):
        raise AcaoBgpNaoSuportada(
            f'"{lista_escolhida}" é sintética/interna (route-filter embutido ou união de `network` '
            'statements pra simulação) — não é um objeto nomeado real que dê pra referenciar aqui.'
        )
    termos = dados.get('policies', {}).get(policy_nome, [])

    if vendor == 'huawei':
        novo_node = _proximo_indice_antes_do_catchall(termos, 'node', 10)
        if novo_node is None:
            raise AcaoBgpNaoSuportada(
                f'Não sobrou node livre antes do bloqueio final da policy "{policy_nome}" — '
                'renumere manualmente antes de tentar esta ação.'
            )
        return [
            f'route-policy {policy_nome} permit node {novo_node}',
            f'if-match ip-prefix {lista_escolhida}',
            'commit',
        ]

    if vendor in ('cisco', 'datacom'):
        novo_seq = _proximo_indice_antes_do_catchall(termos, 'seq', 10)
        if novo_seq is None:
            raise AcaoBgpNaoSuportada(
                f'Não sobrou seq livre antes do bloqueio final do route-map "{policy_nome}" — '
                'renumere manualmente antes de tentar esta ação.'
            )
        entradas = dados['prefix_lists'][lista_escolhida]
        eh_v6 = bool(entradas) and ':' in entradas[0]['prefixo']
        cmd_match = 'match ipv6 address prefix-list' if eh_v6 else 'match ip address prefix-list'
        return [
            f'route-map {policy_nome} permit {novo_seq}',
            f'{cmd_match} {lista_escolhida}',
        ]

    if vendor == 'juniper':
        # Junos avalia terms na ORDEM DE DEFINIÇÃO no arquivo, não pelo
        # nome/número do term (por isso o parser precisa do pré-passo
        # `ordem_vista` — ver backup_parser.py::parse_juniper) — um `set`
        # novo entra no FIM da policy por padrão, depois de um eventual
        # term catch-all de reject (nunca seria alcançado). Por isso usa
        # `insert ... before term X` pra garantir a posição — mecanismo
        # padrão do Junos pra isso, mesmo sem evidência em backup real
        # deste ambiente (nenhum backup mostra HISTÓRICO de edições, só o
        # estado final).
        #
        # `extra.term` é STRING (ex: "10", "default-route") — nem todo
        # term segue convenção numérica; só usamos os numéricos pra
        # escolher um nome de term novo que não colida com nenhum existente.
        nomes_existentes = {t['extra']['term'] for t in termos}
        numericos = [int(t['extra']['term']) for t in termos if str(t['extra'].get('term', '')).isdigit()]
        novo_term = (max(numericos) if numericos else 0) + 10
        while str(novo_term) in nomes_existentes:
            novo_term += 10
        novo_term = str(novo_term)

        termo_catchall = next(
            (t['extra']['term'] for t in termos if not t.get('prefix_lists') and t.get('acao') == 'reject'),
            None,
        )
        cmds = [
            f'set policy-options policy-statement {policy_nome} term {novo_term} from prefix-list {lista_escolhida}',
            f'set policy-options policy-statement {policy_nome} term {novo_term} then accept',
        ]
        if termo_catchall:
            cmds.append(
                f'insert policy-options policy-statement {policy_nome} term {novo_term} before term {termo_catchall}'
            )
        cmds.append('commit')
        return cmds

    raise AcaoBgpNaoSuportada(f'Fabricante "{vendor}" não suportado para anunciar prefixo novo.')


# ═══════════════════════════════════════════════════════════════════════
# Execução real (conecta e envia)
# ═══════════════════════════════════════════════════════════════════════

# Fabricantes cuja conexão exige `commit()` explícito depois do
# `send_config_set` — sem isso a mudança fica só na config candidata,
# nunca aplicada de verdade:
# - Juniper: o driver descarta (discard) qualquer mudança não commitada
#   ao sair do modo config.
# - Huawei (driver `huawei_vrpv8`, usado por TODO equipamento Huawei deste
#   projeto — ver DEVICE_TYPES): VRP8 tem o mesmo modelo de config
#   candidata/commit do Juniper. `send_config_set` nem sai do modo config
#   sozinho (o driver força `exit_config_mode=False`) — sem o `commit()`
#   explícito o prompt fica em `[*...]` (mudança pendente) para sempre e
#   nada é aplicado no equipamento real, mesmo a conexão "funcionando" sem
#   erro nenhum. Bug real encontrado em produção — ver AcaoBgp/histórico.
_PRECISA_COMMIT = {'juniper', 'huawei'}
# Fabricantes cujo comando é enviado direto (sem "modo configuração" —
# RouterOS não tem esse conceito, cada comando já é completo por si só).
_COMANDO_UNICO = {'mikrotik'}


def executar_acao_bgp(acesso, vendor, comandos):
    """Conecta de verdade e envia os comandos. Retorna (output, status) —
    status é 'sucesso' ou 'erro', nunca levanta exceção de conexão (só
    `AcaoBgpNaoSuportada`, que é responsabilidade do chamador verificar
    antes de chegar aqui)."""
    fabricante_conexao = 'cisco' if vendor == 'datacom' else vendor
    if fabricante_conexao not in DEVICE_TYPES:
        return f'Fabricante "{vendor}" sem driver de conexão configurado.', 'erro'

    conn, tunel = None, None
    try:
        conn, tunel = _conectar_script(acesso, fabricante_conexao)
        if vendor in _COMANDO_UNICO:
            output = conn.send_command(comandos[0])
        elif vendor in _PRECISA_COMMIT:
            # 'commit' pode aparecer como último item de `comandos` (só pro
            # preview/auditoria mostrarem a ação completa) — não manda como
            # texto pro send_config_set, senão o commit sai duplicado: uma
            # vez como linha de config comum, outra pela chamada real
            # conn.commit() (que faz o handshake certo de confirmação/erro
            # da config candidata, diferente de só mandar o texto "commit").
            comandos_config = [c for c in comandos if c != 'commit']
            output = conn.send_config_set(comandos_config, exit_config_mode=False)
            output += '\n' + conn.commit()
        else:
            output = conn.send_config_set(comandos)
        return output, 'sucesso'
    except Exception as e:
        logger.error(f'❌ Erro executando ação BGP em {acesso}: {e}')
        return str(e), 'erro'
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass
        if tunel:
            _fechar_tunel(tunel)
