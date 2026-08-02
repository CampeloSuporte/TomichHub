"""
Simulador de match de route-policy/prefix-list, vendor-agnóstico.

Opera sobre a representação canônica extraída pelos parsers de
`clientes/backup_parser.py` (chaves `prefix_lists` e `policies`, no mesmo
formato independente de fabricante — ver docstrings de `parse_mikrotik`/
`parse_huawei`/`parse_cisco`/`parse_juniper`), simulando o resultado de
aplicar uma route-policy/route-map/policy-statement/filter-chain contra os
prefixos conhecidos do próprio backup.

Limitação importante: não há acesso à RIB viva do equipamento — a simulação
é fiel à CONFIGURAÇÃO (o que as regras fariam com esses prefixos), não ao
estado real de rotas anunciadas agora mesmo.
"""
import ipaddress


def _prefix_bate_entrada(prefixo, entrada):
    """Confere se `prefixo` ("X.X.X.X/Y") é coberto por uma entrada de
    prefix-list (dict com acao/prefixo/len_min/len_max), respeitando a
    faixa de tamanho de máscara quando informada."""
    try:
        rede_prefixo = ipaddress.ip_network(prefixo, strict=False)
        rede_entrada = ipaddress.ip_network(entrada['prefixo'], strict=False)
    except ValueError:
        return False
    if rede_prefixo.version != rede_entrada.version:
        return False
    if not rede_prefixo.subnet_of(rede_entrada):
        return False
    tam = rede_prefixo.prefixlen
    len_min = entrada.get('len_min')
    len_max = entrada.get('len_max')
    if len_min is not None and tam < len_min:
        return False
    if len_max is not None and tam > len_max:
        return False
    return True


def entrada_que_bate(prefixo, entradas):
    """Percorre as entradas de uma prefix-list em ordem e devolve a PRIMEIRA
    que bater (não só True/False) — usado por `bgp_actions.py` quando a ação
    precisa saber qual entrada específica (seq/index) foi responsável pelo
    match, não só o resultado permit/deny."""
    for entrada in entradas:
        if _prefix_bate_entrada(prefixo, entrada):
            return entrada
    return None


def _prefix_list_bate(prefixo, entradas):
    """Percorre as entradas de uma prefix-list em ordem — a primeira que
    bater decide permit/deny (mesma semântica sequencial usada por todos os
    fabricantes suportados)."""
    entrada = entrada_que_bate(prefixo, entradas)
    return bool(entrada) and entrada['acao'] == 'permit'


def _todos_prefixos_candidatos(prefix_lists):
    """União de todos os prefixos citados em qualquer prefix-list do
    snapshot — sem acesso à RIB viva, é a melhor aproximação disponível de
    'quais prefixos este equipamento poderia anunciar'."""
    vistos = set()
    for entradas in prefix_lists.values():
        for entrada in entradas:
            vistos.add(entrada['prefixo'])

    def _chave(p):
        rede = ipaddress.ip_network(p, strict=False)
        return (rede.version, rede)

    return sorted(vistos, key=_chave)


def simular_anuncios(prefix_lists, policies, policy_nome):
    """Retorna list[{"prefixo","permitido","prepend"}] simulando o resultado
    de aplicar a policy `policy_nome` contra todos os prefixos conhecidos do
    snapshot. Devolve [] se `policy_nome` for vazio ou não existir."""
    termos = policies.get(policy_nome) if policy_nome else None
    if not termos:
        return []

    termos_ordenados = sorted(termos, key=lambda t: t.get('ordem', 0))
    candidatos = _todos_prefixos_candidatos(prefix_lists)

    resultado = []
    for prefixo in candidatos:
        decidido = False
        for termo in termos_ordenados:
            nomes_pl = termo.get('prefix_lists') or []
            # termo sem match_prefix_lists = match-all (catch-all da chain/term)
            bate = not nomes_pl or any(
                _prefix_list_bate(prefixo, prefix_lists.get(nome, []))
                for nome in nomes_pl
            )
            if bate:
                permitido = termo['acao'] == 'accept'
                resultado.append({
                    'prefixo': prefixo,
                    'permitido': permitido,
                    'prepend': termo.get('prepend', 0) if permitido else 0,
                })
                decidido = True
                break
        if not decidido:
            # nenhum termo bateu — deny implícito de final de policy
            resultado.append({'prefixo': prefixo, 'permitido': False, 'prepend': 0})
    return resultado


def listar_prefix_lists(prefix_lists, policies, policy_nome):
    """Pra "anunciar prefixo novo": lista as prefix-lists NOMEADAS conhecidas
    no snapshot do equipamento inteiro — não só as já usadas por essa sessão
    — pro usuário escolher qual anexar como um NODE/termo novo na export
    policy DESSA sessão (`bgp_actions.py::comandos_novo_anuncio`), sem
    editar a prefix-list em si (que pode estar em uso por outras sessões/
    policies — mesmo cuidado já aplicado em "parar de anunciar": editar o
    objeto compartilhado vazaria o efeito pra fora desta sessão).

    Exclui prefix-lists sintéticas — nome com "#" (Mikrotik/Juniper: um
    route-filter/regra embutido direto num term/chain, não um objeto
    nomeado que dê pra referenciar de outro lugar) e a chave interna
    `__networks__` (Huawei/Cisco: união de `network` statements pra
    simulação, não um objeto real de prefix-list — `if-match ip-prefix
    __networks__`/`match ip address prefix-list __networks__` não existe
    no equipamento). Marca quais já estão anexadas nessa sessão (via algum
    termo `accept` já existente) pra UI não oferecer redundante.

    Retorna {"candidatas": [{"nome", "amostra": [até 3 prefixos], "ja_anunciando": bool}]}."""
    ja_anunciando = set()
    for termo in policies.get(policy_nome or '', []):
        if termo.get('acao') == 'accept':
            ja_anunciando.update(termo.get('prefix_lists') or [])

    candidatas = [
        {
            'nome': nome,
            'amostra': [e['prefixo'] for e in entradas[:3]],
            'ja_anunciando': nome in ja_anunciando,
        }
        for nome, entradas in sorted(prefix_lists.items())
        if '#' not in nome and not nome.startswith('__')
    ]

    return {'candidatas': candidatas}


def identificar_interface(dados, peer_ip):
    """Acha a interface LOCAL cuja subnet contém `peer_ip`, usando
    `dados['ips']` (já extraído pelo parser — lista de
    `{"ip": "X.X.X.X/Y", "interface": "NOME"}`). Peers eBGP diretamente
    conectados (a grande maioria em ambiente de borda/upstream) ficam na
    MESMA subnet do lado local — ex: interface com `177.85.201.250/30` e
    peer `177.85.201.249` — então dá pra inferir por qual interface aquele
    peer é alcançado sem precisar consultar rota/ARP ao vivo no
    equipamento. Devolve o nome da interface, ou `None` se não achar
    (peer multihop/via loopback, ou IP fora de qualquer subnet local
    conhecida no backup — nesses casos não tem como inferir com
    segurança, melhor não adivinhar)."""
    try:
        peer = ipaddress.ip_address(peer_ip)
    except ValueError:
        return None
    for entrada in dados.get('ips', []):
        try:
            rede = ipaddress.ip_network(entrada['ip'], strict=False)
        except (ValueError, KeyError):
            continue
        if peer in rede:
            return entrada.get('interface')
    return None
