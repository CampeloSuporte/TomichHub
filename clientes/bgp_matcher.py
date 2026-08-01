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


def escanear_prefix_lists(prefix_lists, policies, policy_nome, prefixo_novo):
    """Pra "anunciar prefixo novo": reúne as prefix-lists referenciadas por
    termos `accept` de `policy_nome` (candidatas pra adicionar o prefixo
    novo — são as únicas que, se ganhassem uma entrada nova, o fariam ser
    anunciado sem precisar mexer na route-policy/term em si) e confere se
    `prefixo_novo` já bate em alguma delas (nesse caso não precisa fazer
    nada — já seria anunciado automaticamente se a rota existisse).

    Retorna {"ja_coberto": bool, "lista_cobertura": str|None,
             "candidatas": [{"nome", "amostra": [até 3 prefixos]}]}."""
    termos = sorted(policies.get(policy_nome or '', []), key=lambda t: t.get('ordem', 0))

    candidatas_nomes = []
    for termo in termos:
        if termo.get('acao') != 'accept':
            continue
        for nome in (termo.get('prefix_lists') or []):
            if nome not in candidatas_nomes:
                candidatas_nomes.append(nome)

    ja_coberto, lista_cobertura = False, None
    for nome in candidatas_nomes:
        if _prefix_list_bate(prefixo_novo, prefix_lists.get(nome, [])):
            ja_coberto, lista_cobertura = True, nome
            break

    candidatas = [
        {'nome': nome, 'amostra': [e['prefixo'] for e in prefix_lists.get(nome, [])[:3]]}
        for nome in candidatas_nomes
    ]

    return {'ja_coberto': ja_coberto, 'lista_cobertura': lista_cobertura, 'candidatas': candidatas}
