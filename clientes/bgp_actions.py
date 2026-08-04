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
# Configurar nova sessão (Cisco/Datacom apenas por enquanto — ver
# docs/superpowers/specs/2026-08-04-bgp-nova-sessao-design.md)
# ═══════════════════════════════════════════════════════════════════════

def _cidr_para_nome(cidr):
    return cidr.replace('/', '_')


def _nome_prefix_list_nova(tipo_peer, direcao, sufixo, cidr):
    """Convenção observada no exemplo real fornecido: uma prefix-list
    nova sempre reflete O QUE ELA CONTÉM — `PL-ORIGIN-*` é sempre um
    prefixo NOSSO (anunciado a um upstream OU entregue a um downstream),
    independente de quem é o peer. `PL-UPSTREAM-*`/`PL-CLIENTE-*` levam
    o sufixo da sessão porque representam algo que só faz sentido no
    contexto DAQUELE peer especificamente (uma rota aceita de um
    upstream específico / o prefixo próprio de um cliente específico)."""
    cidr_nome = _cidr_para_nome(cidr)
    if direcao == 'out':
        return f'PL-ORIGIN-{cidr_nome}'
    if tipo_peer == 'upstream':
        return f'PL-UPSTREAM-{sufixo}-{cidr_nome}'
    return f'PL-CLIENTE-{sufixo}-{cidr_nome}'


def _validar_cidr(cidr, af):
    try:
        rede = ipaddress.ip_network(cidr, strict=True)
    except ValueError as e:
        raise AcaoBgpNaoSuportada(f'CIDR inválido "{cidr}": {e}')
    esperado = 6 if af == 'ipv6' else 4
    if rede.version != esperado:
        raise AcaoBgpNaoSuportada(
            f'CIDR "{cidr}" não é {"IPv6" if af == "ipv6" else "IPv4"} (address-family escolhida: {af}).'
        )
    return cidr


def _nomes_em_uso(dados):
    """Peers/route-maps/prefix-lists já conhecidos em `dados` (snapshot
    ou leitura ao vivo — o chamador decide qual passar) — usado pra
    recusar colisão de nome ANTES de gerar qualquer comando."""
    peers = {s.get('peer_ip') for s in dados.get('sessoes', [])}
    route_maps = set(dados.get('policies', {}).keys())
    prefix_lists = set(dados.get('prefix_lists', {}).keys())
    return peers, route_maps, prefix_lists


def comandos_criar_sessao(vendor, dados, params):
    """`params`: {"tipo_peer": "upstream"|"downstream", "sufixo": str,
    "afs": [{"af": "ipv4"|"ipv6", "peer_ip": str, "remote_as": str,
              "pl_in":  {"modo": "existente", "nome": str} | {"modo": "nova", "cidr": str},
              "pl_out": mesmo formato}, ...]}.

    Gera a config completa de UMA sessão nova, cobrindo IPv4 e/ou IPv6 do
    MESMO peer no mesmo bloco — igual a como um `router bgp` real
    costuma ser editado numa tacada só. Nunca edita uma prefix-list já
    existente (mesmo princípio de `comandos_novo_anuncio`/`comandos_
    parar_anuncio`: objeto compartilhável, editar vazaria efeito pra
    fora desta sessão); quando `pl_in`/`pl_out` escolhe "nova", cria uma
    prefix-list EXCLUSIVA dessa sessão com 1 entrada, seq 10.

    Ordem dos comandos gerados: 1) `router bgp`+`neighbor`s (definição
    do(s) peer(s)), 2) prefix-lists/route-maps NOVOS (só os que o
    operador escolheu criar), 3) blocos `address-family` (ativação +
    anexação dos route-maps). Prefix-lists/route-maps são definidos
    ANTES de serem referenciados na ativação — o exemplo original (só
    ilustrativo da estrutura, não um roteiro literal de comandos) mistura
    a ordem, mas definir-antes-de-referenciar evita a sessão subir
    momentaneamente sem filtro (ou bloqueada, dependendo da versão do
    IOS) enquanto o route-map ainda não existe."""
    if vendor not in ('cisco', 'datacom'):
        raise AcaoBgpNaoSuportada(f'Configurar nova sessão ainda não é suportado para "{vendor}".')

    tipo_peer = params.get('tipo_peer')
    if tipo_peer not in ('upstream', 'downstream'):
        raise AcaoBgpNaoSuportada('Informe se a sessão é upstream ou downstream.')
    sufixo = (params.get('sufixo') or '').strip().upper().replace(' ', '-')
    if not sufixo:
        raise AcaoBgpNaoSuportada('Informe o sufixo da descrição.')
    afs = params.get('afs') or []
    if not afs:
        raise AcaoBgpNaoSuportada('Marque pelo menos IPv4 ou IPv6.')

    as_local = next((s.get('as_local') for s in dados.get('sessoes', []) if s.get('as_local')), None)
    if not as_local:
        raise AcaoBgpNaoSuportada('AS local não identificado no snapshot — não dá pra montar "router bgp".')

    peers_em_uso, route_maps_em_uso, prefix_lists_em_uso = _nomes_em_uso(dados)

    comandos_peers = [f'router bgp {as_local}']
    comandos_pl_rm = []
    comandos_af = []

    for entrada_af in afs:
        af = entrada_af.get('af')
        if af not in ('ipv4', 'ipv6'):
            raise AcaoBgpNaoSuportada(f'Address-family inválida: "{af}".')
        peer_ip = (entrada_af.get('peer_ip') or '').strip()
        remote_as = (entrada_af.get('remote_as') or '').strip()
        if not peer_ip or not remote_as:
            raise AcaoBgpNaoSuportada(f'Peer IP e AS remoto são obrigatórios ({af}).')
        try:
            ip_parseado = ipaddress.ip_address(peer_ip)
        except ValueError:
            raise AcaoBgpNaoSuportada(f'Peer IP inválido: "{peer_ip}".')
        esperado = 6 if af == 'ipv6' else 4
        if ip_parseado.version != esperado:
            raise AcaoBgpNaoSuportada(f'Peer IP "{peer_ip}" não é {"IPv6" if af == "ipv6" else "IPv4"}.')
        if peer_ip in peers_em_uso:
            raise AcaoBgpNaoSuportada(f'Já existe uma sessão com peer "{peer_ip}" no snapshot.')

        af_tag = 'V6' if af == 'ipv6' else 'V4'
        descricao = f'{tipo_peer.upper()}-{sufixo}-{af_tag}'
        rm_in = f'RM-PEER-{sufixo}-{af_tag}-IN'
        rm_out = f'RM-PEER-{sufixo}-{af_tag}-OUT'
        for rm in (rm_in, rm_out):
            if rm in route_maps_em_uso:
                raise AcaoBgpNaoSuportada(f'Já existe um route-map chamado "{rm}" — escolha outro sufixo.')

        comandos_peers.append(f'neighbor {peer_ip} remote-as {remote_as}')
        comandos_peers.append(f'neighbor {peer_ip} description {descricao}')

        cmd_pl = 'ipv6 prefix-list' if af == 'ipv6' else 'ip prefix-list'
        cmd_match = 'match ipv6 address prefix-list' if af == 'ipv6' else 'match ip address prefix-list'

        for direcao, rm_nome, escolha in (('in', rm_in, entrada_af.get('pl_in')), ('out', rm_out, entrada_af.get('pl_out'))):
            if not escolha or escolha.get('modo') not in ('existente', 'nova'):
                raise AcaoBgpNaoSuportada(f'Escolha uma prefix-list (existente ou nova) pro {direcao.upper()} de {af}.')
            if escolha['modo'] == 'existente':
                nome_pl = (escolha.get('nome') or '').strip()
                if not nome_pl or nome_pl not in dados.get('prefix_lists', {}):
                    raise AcaoBgpNaoSuportada(
                        f'Prefix-list "{nome_pl}" não encontrada — atualize a busca antes de confirmar.'
                    )
            else:
                cidr = _validar_cidr((escolha.get('cidr') or '').strip(), af)
                nome_pl = _nome_prefix_list_nova(tipo_peer, direcao, sufixo, cidr)
                if nome_pl in prefix_lists_em_uso:
                    raise AcaoBgpNaoSuportada(f'Já existe uma prefix-list chamada "{nome_pl}".')
                prefix_lists_em_uso.add(nome_pl)
                comandos_pl_rm.append(f'{cmd_pl} {nome_pl} seq 10 permit {cidr}')
            comandos_pl_rm.append(f'route-map {rm_nome} permit 10')
            comandos_pl_rm.append(f' {cmd_match} {nome_pl}')

        route_maps_em_uso.add(rm_in)
        route_maps_em_uso.add(rm_out)
        peers_em_uso.add(peer_ip)

        comandos_af.append(f'address-family {af}')
        comandos_af.append(f' neighbor {peer_ip} activate')
        comandos_af.append(f' neighbor {peer_ip} send-community both')
        comandos_af.append(f' neighbor {peer_ip} route-map {rm_in} in')
        comandos_af.append(f' neighbor {peer_ip} route-map {rm_out} out')
        comandos_af.append('exit-address-family')

    return comandos_peers + comandos_pl_rm + comandos_af


def buscar_prefix_lists_ao_vivo(acesso):
    """Conecta AO VIVO (Cisco/Datacom) e roda `show running-config |
    section prefix-list`/`section route-map` — mesma sintaxe de
    configuração usada em qualquer backup, então reaproveita a MESMA
    regex do parser de backup (`backup_parser._extrair_prefix_lists_e_
    policies_cisco`) em vez de escrever um parser novo pra saída "ao
    vivo". Usado só pelo botão "🔄 atualizar" do modal de "Configurar
    nova sessão" — o resto da automação BGP usa o snapshot (backup em
    disco), que é mais barato. Nunca escreve nada (leitura pura); fecha a
    conexão antes de devolver — não fica aberta enquanto o operador
    preenche o resto do formulário. Propaga qualquer exceção de conexão
    pro chamador (a view decide como reportar o erro)."""
    from .backup_parser import _extrair_prefix_lists_e_policies_cisco

    conn, tunel = None, None
    try:
        conn, tunel = _conectar_script(acesso, 'cisco')
        saida_pl = conn.send_command('show running-config | section prefix-list', read_timeout=25)
        saida_rm = conn.send_command('show running-config | section route-map', read_timeout=25)
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass
        if tunel:
            _fechar_tunel(tunel)

    prefix_lists, policies = _extrair_prefix_lists_e_policies_cisco(saida_pl + '\n' + saida_rm)
    return {'prefix_lists': prefix_lists, 'policies': policies}


def _aplicar_criar_sessao_localmente(dados, params):
    """Insere a(s) sessão(ões) nova(s) — e as prefix-lists/route-maps
    novas criadas junto — direto em `dados` (o MESMO dict salvo em
    BgpSnapshot.dados), pro painel refletir a ação sem esperar o próximo
    backup. Reconstrói o MESMO cálculo de nomes de `comandos_criar_
    sessao` (`_nome_prefix_list_nova`) — se os dois divergirem um dia, o
    painel mostraria um nome diferente do que foi realmente configurado
    no equipamento; mantenha as duas funções em sincronia."""
    from .bgp_matcher import simular_anuncios

    tipo_peer = params.get('tipo_peer', 'upstream')
    sufixo = (params.get('sufixo') or '').strip().upper().replace(' ', '-')
    as_local = next((s.get('as_local') for s in dados.get('sessoes', []) if s.get('as_local')), None)

    for entrada_af in params.get('afs', []):
        af = entrada_af.get('af')
        af_tag = 'V6' if af == 'ipv6' else 'V4'
        peer_ip = entrada_af.get('peer_ip')
        remote_as = entrada_af.get('remote_as')
        descricao = f'{tipo_peer.upper()}-{sufixo}-{af_tag}'
        rm_in = f'RM-PEER-{sufixo}-{af_tag}-IN'
        rm_out = f'RM-PEER-{sufixo}-{af_tag}-OUT'

        dados.setdefault('sessoes', []).append({
            'peer_ip': peer_ip, 'peer_as': remote_as, 'as_local': as_local,
            'nome': peer_ip, 'descricao': descricao, 'habilitada': True,
            'policy_in': rm_in, 'policy_out': rm_out,
        })

        for direcao, rm_nome, escolha in (('in', rm_in, entrada_af.get('pl_in')), ('out', rm_out, entrada_af.get('pl_out'))):
            if not escolha:
                continue
            if escolha.get('modo') == 'nova':
                cidr = escolha.get('cidr')
                nome_pl = _nome_prefix_list_nova(tipo_peer, direcao, sufixo, cidr)
                dados.setdefault('prefix_lists', {})[nome_pl] = [
                    {'acao': 'permit', 'prefixo': cidr, 'len_min': None, 'len_max': None, 'seq': 10}
                ]
            else:
                nome_pl = escolha.get('nome')
            dados.setdefault('policies', {}).setdefault(rm_nome, []).append({
                'ordem': 10, 'prefix_lists': [nome_pl], 'acao': 'accept',
                'prepend': 0, 'extra': {'route_map': rm_nome, 'seq': 10, 'nao_suportado': False},
            })

        dados.setdefault('anuncios', {})[peer_ip] = simular_anuncios(
            dados.get('prefix_lists', {}), dados.get('policies', {}), rm_out
        )


# ═══════════════════════════════════════════════════════════════════════
# Atualização otimista do snapshot local (depois de uma ação real bem-
# sucedida no equipamento)
# ═══════════════════════════════════════════════════════════════════════

def aplicar_efeito_localmente(vendor, dados, tipo, nome_sessao, alvo, params):
    """Depois de uma ação real (`preview=false`) bem-sucedida no
    equipamento, atualiza `dados` (o MESMO dict que é salvo como
    `BgpSnapshot.dados`) pra refletir o efeito esperado — sem esperar o
    próximo backup real do equipamento (rotina noturna ou "Atualizar
    agora"). Evita o painel continuar mostrando um prefixo como anunciado
    depois que o operador já rodou "Parar de anunciar" nele, por exemplo.

    É uma aproximação OTIMISTA: assume que o comando fez exatamente o que
    a mesma lógica usada pra montá-lo (`comandos_*`/`_termo_e_entrada_
    responsaveis`) previu. Se o comando real teve algum efeito colateral
    inesperado no equipamento, só o próximo backup de verdade corrige —
    mas isso já era verdade antes desta função existir (o snapshot sempre
    foi uma cópia ponto-no-tempo do backup, nunca a config viva).

    Nunca levanta exceção — falha em aplicar localmente não deve impedir
    a ação real (já executada) de ser reportada como sucesso ao usuário."""
    if tipo == 'criar_sessao':
        # Sessão nova por definição não existe em `dados` ainda —
        # `_sessao_por_nome` abaixo nunca acharia — trata à parte.
        try:
            _aplicar_criar_sessao_localmente(dados, params)
        except Exception as e:
            logger.warning(f'aplicar_efeito_localmente (criar_sessao) falhou (não crítico): {e}')
        return
    try:
        sessao = _sessao_por_nome(dados, nome_sessao)
    except AcaoBgpNaoSuportada:
        return
    policy_nome = sessao.get('policy_out')

    if tipo == 'ativar_sessao':
        sessao['habilitada'] = True
    elif tipo == 'desativar_sessao':
        sessao['habilitada'] = False
    elif tipo == 'prepend':
        termo, entrada = _termo_e_entrada_responsaveis(dados, policy_nome, alvo)
        if termo and entrada:
            termo['prepend'] = max(0, int(termo.get('prepend', 0)) + int(params.get('delta', 1)))
    elif tipo == 'parar_anuncio':
        # Mecanismo real varia por fabricante (node vira deny no Huawei,
        # deny novo inserido acima no Cisco, term desativado no Juniper,
        # regra/network desabilitada no Mikrotik) — mas o efeito
        # observável pro prefixo alvo é sempre o mesmo: o termo que
        # decidia o match dele deixa de permitir. Simular isso como
        # "esse termo agora rejeita" reproduz o resultado corretamente
        # pra fins de exibição, sem precisar replicar cada mecanismo.
        termo, entrada = _termo_e_entrada_responsaveis(dados, policy_nome, alvo)
        if termo and entrada:
            termo['acao'] = 'reject'
    elif tipo == 'novo_anuncio':
        lista = params.get('lista')
        prefixo_novo = params.get('prefixo')
        if policy_nome and (lista or prefixo_novo):
            termos = dados.setdefault('policies', {}).setdefault(policy_nome, [])
            # `ordem` decide a prioridade na simulação (`simular_anuncios`
            # ordena por ela) — semântica varia por fabricante (node number
            # no Huawei, posição no arquivo no Juniper/Mikrotik), mas em
            # TODOS os casos um catch-all (termo sem prefix_lists — bate
            # com tudo) tem que continuar sendo avaliado por ÚLTIMO. Só
            # apendar com `max(ordem)+1` colocaria o termo novo DEPOIS de
            # um catch-all que já tenha a maior ordem (bug real: testado
            # com backup do Huawei, node 2000 de deny final tem ordem=2000,
            # apendar com +1 o colocava depois dele e o novo anúncio nunca
            # era alcançado na simulação).
            ordens_catchall = [t.get('ordem', 0) for t in termos if not t.get('prefix_lists')]
            if ordens_catchall:
                nova_ordem = min(ordens_catchall) - 0.5
            else:
                ordens_normais = [t.get('ordem', 0) for t in termos]
                nova_ordem = (max(ordens_normais) if ordens_normais else -1) + 1

            if lista and lista in dados.get('prefix_lists', {}):
                nome_lista = lista
            else:
                # Mikrotik: sem prefix-list nomeada — registra uma entrada
                # sintética só pra simulação local refletir o prefixo novo.
                nome_lista = f'__local_novo_anuncio__{nome_sessao}__{prefixo_novo}'
                dados.setdefault('prefix_lists', {})[nome_lista] = [
                    {'acao': 'permit', 'prefixo': prefixo_novo, 'len_min': None, 'len_max': None}
                ]
            termos.append({
                'ordem': nova_ordem, 'prefix_lists': [nome_lista], 'acao': 'accept',
                'prepend': 0, 'extra': {},
            })
    # 'community' não muda o que é simulado como anunciado (é um atributo
    # extra aplicado ao anúncio, não afeta permit/deny) — nada a fazer.

    if policy_nome:
        from .bgp_matcher import simular_anuncios
        dados.setdefault('anuncios', {})[nome_sessao] = simular_anuncios(
            dados.get('prefix_lists', {}), dados.get('policies', {}), policy_nome
        )


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

# Fabricantes com mecanismo de "commit temporário" (aplica e reverte
# sozinho depois de N segundos, a menos que seja confirmado antes) —
# Huawei `commit trial N` e Junos `commit confirmed N` (minutos) operam
# sobre a MESMA config candidata do commit normal, risco contido à sessão/
# policy sendo editada.
#
# Cisco/Datacom e Mikrotik ficam de fora por decisão explícita (perguntado
# ao usuário): IOS clássico não tem candidate-config/commit — comandos
# aplicam na hora — e o equivalente mais próximo de rollback temporizado
# seria `reload in N` (reagenda um REBOOT do equipamento inteiro se
# ninguém confirmar), risco muito maior que o rollback restrito do
# Huawei/Juniper — decidiu não usar. RouterOS só tem "safe mode"
# (reverte no DISCONNECT da sessão, não por tempo), incompatível com o
# modelo conecta→executa→desconecta desta automação.
_TRIAL_SUPORTADO = {'huawei', 'juniper'}


def validar_trial_suportado(vendor):
    """Levanta `AcaoBgpNaoSuportada` se `vendor` não tem um mecanismo de
    rollback temporizado compatível com esta automação — ver comentário
    de `_TRIAL_SUPORTADO`. Chamada pela view ANTES de executar de verdade,
    pra recusar cedo (mesmo padrão de `comandos_*` recusando ações sem um
    comando seguro conhecido)."""
    if vendor not in _TRIAL_SUPORTADO:
        motivo = (
            'route-map/comando aplica na hora, sem candidate-config — o único jeito de reverter '
            'por tempo seria agendar reload do equipamento inteiro, risco desproporcional'
            if vendor in ('cisco', 'datacom') else
            'RouterOS só tem "safe mode" (reverte no disconnect da sessão, não por tempo) — '
            'incompatível com o modelo conecta→executa→desconecta desta automação'
        )
        raise AcaoBgpNaoSuportada(f'Execução em modo trial não é suportada em {vendor}: {motivo}.')


def _comando_commit_trial(vendor, trial_segundos):
    """Comando de commit temporário nativo de cada fabricante suportado —
    ver `_TRIAL_SUPORTADO`. `trial_segundos` já validado pelo chamador
    (`bgp_views.py`) como inteiro positivo razoável."""
    if vendor == 'huawei':
        # VRP aceita de 5 a 65534 segundos.
        segundos = max(5, min(65534, int(trial_segundos)))
        return f'commit trial {segundos}'
    if vendor == 'juniper':
        # Junos "commit confirmed" é em MINUTOS, não segundos (mín. 1).
        minutos = max(1, -(-int(trial_segundos) // 60))  # arredonda pra cima
        return f'commit confirmed {minutos}'
    raise AcaoBgpNaoSuportada(f'Trial não suportado para {vendor}.')


def executar_acao_bgp(acesso, vendor, comandos, trial=False, trial_segundos=60):
    """Conecta de verdade e envia os comandos. Retorna (output, status) —
    status é 'sucesso' ou 'erro', nunca levanta exceção de conexão (só
    `AcaoBgpNaoSuportada`, que é responsabilidade do chamador verificar
    antes de chegar aqui — inclusive `validar_trial_suportado` quando
    `trial=True`).

    `trial=True` troca o commit normal por um commit TEMPORÁRIO (`commit
    trial N`/`commit confirmed N`) — a mudança fica ativa só por
    `trial_segundos` e reverte sozinha se ninguém confirmar depois (esta
    automação ainda não tem uma ação de "confirmar" separada; trial serve
    pra testar o efeito com segurança, sabendo que desfaz sozinho)."""
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
            if trial:
                comando_commit = _comando_commit_trial(vendor, trial_segundos)
                output = f'[MODO TRIAL — reverte sozinho se não for confirmado]\n' + output
                output += '\n' + conn.send_command(comando_commit, read_timeout=20)
            else:
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


# ═══════════════════════════════════════════════════════════════════════
# Validar anúncios — consulta AO VIVO no equipamento (Adj-RIB-Out/RIB local
# pós-política), diferente da simulação baseada só em config já existente
# (bgp_matcher.simular_anuncios/dados['anuncios']). Tudo aqui é leitura
# pura: nenhum comando muda config, nunca gera AcaoBgp.
# ═══════════════════════════════════════════════════════════════════════

_CIDR_RE = re.compile(r'\b(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\b')
# Cisco (IOS/IOS-XE) imprime a rota padrão como "0.0.0.0" solto, SEM "/0"
# — confirmado ao vivo (acesso 887, "show ip bgp neighbors X routes": " *
# 0.0.0.0    172.16.8.1    ..."), diferente de todo outro prefixo da mesma
# tabela, que sempre vem com máscara. _CIDR_RE sozinha não pega esse caso.
# Exige "0.0.0.0" como PRIMEIRO campo da linha (só flags de status antes)
# seguido de outro IP (o next-hop) — evita falso positivo quando "0.0.0.0"
# aparece como next-hop de rota local (ex: "45.169.6.0/24  0.0.0.0  ...",
# onde "0.0.0.0" vem DEPOIS do prefixo de verdade, não no início da linha).
_DEFAULT_ROUTE_RE = re.compile(r'^\s*[*>sdhirSmbfxatcLV]{0,4}\s+0\.0\.0\.0\s+\d', re.MULTILINE)


def _extrair_prefixos(texto):
    """Extrai só os prefixos CIDR (ex: 45.169.6.0/24) do texto bruto do
    comando — funciona igual pros 4 fabricantes porque em toda saída real
    testada (Huawei/Cisco/Juniper/Mikrotik) o prefixo é sempre o único
    token no formato IP/máscara da linha; next-hop/gateway aparecem sem
    a barra e não batem com a regex. Complementa com _DEFAULT_ROUTE_RE pro
    caso do Cisco (ver comentário acima)."""
    vistos = []
    if _DEFAULT_ROUTE_RE.search(texto or ''):
        vistos.append('0.0.0.0/0')
    for m in _CIDR_RE.finditer(texto or ''):
        p = m.group(1)
        if p not in vistos:
            vistos.append(p)
    return vistos


def _int_ou_none(m):
    return int(m.group(1)) if m else None


LIMITE_PREFIXOS_LISTAR = 500
# Acima disso não busca a lista completa, só mostra a contagem — peers
# full-table/transit reais desta base chegam a passar de 1 MILHÃO de
# prefixos recebidos (visto ao vivo: acesso 990, 1084769 rotas recebidas
# via `display bgp peer X verbose`); listar tudo travaria a conexão SSH
# (o comando de listagem demorou mais que qualquer read_timeout razoável)
# e estouraria o tamanho da resposta HTTP.


def comando_contar_recebidos(vendor, dados, sessao):
    """Comando BARATO (contadores já computados pelo próprio equipamento,
    não uma varredura da RIB inteira) que devolve só a QUANTIDADE de
    prefixos recebidos dessa sessão — usado ANTES de tentar listar tudo,
    pra decidir se é seguro (ver LIMITE_PREFIXOS_LISTAR). Não existe
    equivalente pro lado anunciado porque em todo teste ao vivo (Huawei,
    Cisco, Juniper, Mikrotik) o número de prefixos ANUNCIADOS por uma
    sessão de borda ficou sempre pequeno (1 a 14) — o risco de explosão é
    só do lado recebido (full-table/transit). Retorna (comando, parser),
    parser(texto) -> int|None."""
    peer_ip = sessao.get('peer_ip')
    nome = sessao.get('nome', peer_ip)
    if not peer_ip:
        raise AcaoBgpNaoSuportada('IP do peer não identificado nessa sessão — não dá pra consultar.')

    if vendor == 'huawei':
        return (
            f'display bgp peer {peer_ip} verbose',
            lambda t: _int_ou_none(re.search(r'Received total routes:\s*(\d+)', t)),
        )
    if vendor in ('cisco', 'datacom'):
        return (
            f'show ip bgp neighbors {peer_ip} | include Prefixes Current',
            lambda t: _int_ou_none(re.search(r'Prefixes Current:\s*\d+\s+(\d+)', t)),
        )
    if vendor == 'juniper':
        return (
            f'show bgp neighbor {peer_ip} | match "Received prefixes"',
            lambda t: _int_ou_none(re.search(r'Received prefixes:\s*(\d+)', t)),
        )
    if vendor == 'mikrotik':
        versao = dados.get('versao_routeros', 6)
        objeto = 'session' if versao == 7 else 'peer'
        return (
            f'/routing bgp {objeto} print detail where name="{nome}"',
            lambda t: _int_ou_none(re.search(r'prefix-count=(\d+)', t)),
        )
    raise AcaoBgpNaoSuportada(f'Validar anúncios não suportado para {vendor} ainda.')


def comandos_validar_anuncios(vendor, dados, sessao):
    """Comandos que LISTAM de verdade os prefixos anunciados/recebidos
    nessa sessão, consultando o equipamento ao vivo (peer_ip da sessão
    identifica o filtro em todos os fabricantes, exceto o lado anunciado
    do Mikrotik, que só filtra por nome do peer). Retorna
    {'anunciados': cmd, 'recebidos': cmd, 'recebidos_fallback': cmd|None}."""
    peer_ip = sessao.get('peer_ip')
    nome = sessao.get('nome', peer_ip)
    if not peer_ip:
        raise AcaoBgpNaoSuportada('IP do peer não identificado nessa sessão — não dá pra consultar.')

    if vendor == 'huawei':
        return {
            'anunciados': f'display bgp routing-table peer {peer_ip} advertised-routes',
            'recebidos': f'display bgp routing-table peer {peer_ip} received-routes',
            'recebidos_fallback': None,
        }
    if vendor in ('cisco', 'datacom'):
        return {
            'anunciados': f'show ip bgp neighbors {peer_ip} advertised-routes',
            'recebidos': f'show ip bgp neighbors {peer_ip} received-routes',
            # Sem "soft-reconfiguration inbound" configurado no peer,
            # "received-routes" erra com "% Inbound soft reconfiguration
            # not enabled" (confirmado ao vivo) — "routes" mostra o
            # equivalente pós-política (o que realmente entrou na RIB
            # local), sem precisar dessa config extra no equipamento.
            'recebidos_fallback': f'show ip bgp neighbors {peer_ip} routes',
        }
    if vendor == 'juniper':
        return {
            'anunciados': f'show route advertising-protocol bgp {peer_ip}',
            'recebidos': f'show route receive-protocol bgp {peer_ip}',
            'recebidos_fallback': None,
        }
    if vendor == 'mikrotik':
        versao = dados.get('versao_routeros', 6)
        if versao == 7:
            return {
                # v7 não guarda mais "recebido de qual peer" no /ip route
                # (propriedade removida — confirmado ao vivo, "received-from"
                # nem aparece entre os campos filtráveis); usa o gateway da
                # rota (= endereço remoto do peer) como proxy, já que é o
                # próprio peer_ip da sessão.
                'anunciados': f'/routing bgp advertisements print where peer="{nome}"',
                'recebidos': f'/ip route print where gateway={peer_ip} bgp=yes',
                'recebidos_fallback': None,
            }
        return {
            'anunciados': f'/routing bgp advertisements print peer="{nome}"',
            'recebidos': f'/ip route print where received-from="{nome}"',
            'recebidos_fallback': None,
        }
    raise AcaoBgpNaoSuportada(f'Validar anúncios não suportado para {vendor} ainda.')


def validar_anuncios_ao_vivo(acesso, vendor, dados, sessao):
    """Conecta de verdade e roda só comandos de LEITURA (nunca escreve
    nada, nunca gera AcaoBgp) pra ver o que essa sessão está anunciando/
    recebendo AGORA no equipamento — complementar à simulação baseada em
    config (bgp_matcher.simular_anuncios), que mostra o que a policy
    DEVERIA deixar passar, não o que está passando de fato no RIB.

    Sempre conta os recebidos primeiro (comando barato, contadores já
    computados) antes de tentar listar — acima de LIMITE_PREFIXOS_LISTAR
    não busca a lista (peer full-table/transit), só devolve a contagem.

    Retorna dict, nunca levanta exceção de conexão:
    {'status': 'sucesso', 'anunciados': [...], 'recebidos': [...]|None,
     'total_recebidos': int|None, 'recebidos_truncado': bool}
    ou {'status': 'erro', 'mensagem': str}."""
    fabricante_conexao = 'cisco' if vendor == 'datacom' else vendor
    if fabricante_conexao not in DEVICE_TYPES:
        return {'status': 'erro', 'mensagem': f'Fabricante "{vendor}" sem driver de conexão configurado.'}

    try:
        comando_contagem, parser_contagem = comando_contar_recebidos(vendor, dados, sessao)
        comandos = comandos_validar_anuncios(vendor, dados, sessao)
    except AcaoBgpNaoSuportada as e:
        return {'status': 'erro', 'mensagem': str(e)}

    conn, tunel = None, None
    try:
        conn, tunel = _conectar_script(acesso, fabricante_conexao)

        total_recebidos = None
        try:
            out_contagem = conn.send_command(comando_contagem, read_timeout=20)
            total_recebidos = parser_contagem(out_contagem)
        except Exception as e:
            logger.warning(f'validar_anuncios_ao_vivo: contagem de recebidos falhou em {acesso} (segue sem trava): {e}')

        out_anunciados = conn.send_command(comandos['anunciados'], read_timeout=25)
        anunciados = _extrair_prefixos(out_anunciados)

        recebidos = None
        recebidos_truncado = False
        if total_recebidos is not None and total_recebidos > LIMITE_PREFIXOS_LISTAR:
            recebidos_truncado = True
        else:
            out_recebidos = conn.send_command(comandos['recebidos'], read_timeout=25)
            if comandos.get('recebidos_fallback') and 'soft reconfiguration not enabled' in out_recebidos.lower():
                out_recebidos = conn.send_command(comandos['recebidos_fallback'], read_timeout=25)
            recebidos = _extrair_prefixos(out_recebidos)
            if total_recebidos is None:
                total_recebidos = len(recebidos)

        return {
            'status': 'sucesso',
            'anunciados': anunciados,
            'recebidos': recebidos,
            'total_recebidos': total_recebidos,
            'recebidos_truncado': recebidos_truncado,
        }
    except Exception as e:
        logger.error(f'❌ Erro validando anúncios ao vivo em {acesso}: {e}')
        return {'status': 'erro', 'mensagem': str(e)}
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass
        if tunel:
            _fechar_tunel(tunel)
