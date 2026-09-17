"""
Composição do Change Plan TO-BE a partir do plano de `tobe.gerar_plano`.

Cada wave vira uma seção com objetivo, texto padrão do programa (sequência,
critérios, gates) e a tabela de itens gerados. Seções chave
`tobe_wave_N`; o contexto é `{'plano', 'conv', 'refs'}`.
"""
from django.utils.html import escape

from . import convencao as cv
from .composicao import callout, e, h3, p, tabela, ul

LIMITE_ITENS = 400


def _ol(itens):
    return '<ol>' + ''.join(f'<li>{i}</li>' for i in itens) + '</ol>'


def _tabela_itens(itens, com_lote=False, com_prioridade=False, vazio='Nenhuma mudança identificada para esta wave.'):
    cab = ['ID', 'Objeto', 'Ação', 'Atual', 'Alvo']
    if com_lote:
        cab.insert(1, 'Lote')
    if com_prioridade:
        cab.append('Prioridade')
    linhas = []
    for it in itens[:LIMITE_ITENS]:
        linha = [f'<strong>{e(it["id"])}</strong>', e(it['objeto']), e(it['acao']), e(it['atual']), e(it['alvo'])]
        if com_lote:
            linha.insert(1, e(it['lote']))
        if com_prioridade:
            linha.append(e(it['prioridade']))
        linhas.append(linha)
    extra = ''
    if len(itens) > LIMITE_ITENS:
        extra = p(f'<em>{len(itens) - LIMITE_ITENS} item(ns) adicional(is) omitido(s); ver inventário.</em>')
    return tabela(cab, linhas, vazio=vazio) + extra


def _wave(plano, n):
    return plano['waves'][n]


def _gate(itens):
    return h3('Gate de saída') + ul([escape(i) for i in itens])


def s_controle(ctx):
    plano, conv, refs = ctx['plano'], ctx['conv'], ctx['refs']
    bng = ' e '.join(filter(None, [plano.get('bng01'), plano.get('bng02')])) or 'nos BNGs centrais'
    partes = [
        callout('ESCOPO.', 'Este documento organiza a transformação em waves. Os comandos por release, interfaces '
                'exatas, testes operacionais detalhados e rollback por equipamento devem ser formalizados nos LLDs '
                'e nos planos de mudança de cada wave.'),
        h3('Objetivos'),
        ul([
            'Executar a evolução do AS-IS para o TO-BE com dependências claras e gates obrigatórios.',
            'Corrigir primeiro a fundação de transporte, iniciando pela MTU de todo o backbone.',
            'Preservar a continuidade dos serviços durante a coexistência entre objetos legados e novos.',
            f'Centralizar BNG e CGNAT em {escape(bng)}, com CGNAT executado em card de serviço.',
            'Padronizar underlay, MP-BGP, VRFs, RD, RT, communities, L2VPN e L3VPN conforme o HLD aprovado.',
            'Manter inventário e evidências de pré-check, mudança, aceite e rollback.',
        ]),
        h3('Princípios de governança'),
        ul([escape(g.get('regra', '')) for g in conv.get('governanca', [])]),
        h3('Fontes deste plano'),
        ul([escape(x) for x in refs.get('fontes', [])]),
    ]
    return ''.join(partes)


def s_visao(ctx):
    plano = ctx['plano']
    linhas = [[f'<strong>{w["numero"]}</strong>', e(w['nome']), e(w['natureza']), e(w['gate']), e(w['total'])]
              for w in plano['waves']]
    return ''.join([
        tabela(['Wave', 'Escopo', 'Natureza', 'Gate de saída', 'Itens'], linhas),
        callout('GATE PRINCIPAL.', 'A Wave 1 é bloqueante. As Waves 2 a 9 somente avançam após a comprovação de MTU '
                'adequada nos caminhos principal e alternativo, incluindo o transporte MPLS e os serviços '
                'L2VPN/L3VPN.', 'risco'),
        h3('Dependência macro'),
        _ol([escape(w['nome']) for w in plano['waves']]),
    ])


def s_premissas(ctx):
    plano = ctx['plano']
    partes = []
    papeis = plano.get('papeis') or []
    partes.append(h3('Papéis-alvo'))
    partes.append(tabela(['Equipamento', 'Papel TO-BE', 'Estado'],
                         [[e(x['equipamento']), e(x['papel']), e(x['estado'])] for x in papeis],
                         vazio='Nenhum papel-alvo definido.'))
    fonte = 'cenário de topologia TO-BE' if plano.get('com_cenario') else 'AS-IS e mapeamentos (sem cenário de topologia)'
    partes.append(p(f'Papéis e enlaces derivados de: {escape(fonte)}. Enlaces de backbone considerados: '
                    f'{plano.get("enlaces", 0)}.'))
    if plano['pendencias']:
        partes.append(h3('Decisões pendentes'))
        partes.append(callout('DECISÕES PENDENTES.', 'Os itens abaixo precisam ser resolvidos no LLD ou nos '
                              'mapeamentos antes da execução da wave correspondente.', 'risco'))
        partes.append(ul([escape(x) for x in plano['pendencias']]))
    return ''.join(partes)


def s_wave0(ctx):
    w = _wave(ctx['plano'], 0)
    return ''.join([
        h3('Objetivo'), p(escape(w['objetivo'])),
        h3('Entregáveis obrigatórios'),
        ul([
            'Inventário de enlaces físicos e lógicos, com ponta A, ponta B, meio e capacidade.',
            'Matriz de MTU por porta física, Eth-Trunk, subinterface, Vlanif, interface L3, MPLS, VSI, PW e L2VC.',
            'Mapeamento de serviços por enlace (PPPoE, transportes empresariais, gerência e serviços críticos).',
            'Estado de OSPF, LDP, RSVP-TE, BFD, MP-BGP e pseudowires.',
            'Identificação e teste dos caminhos principal e alternativo.',
            'Backup das configurações e evidências de tráfego, erros e sessões.',
            'LLD, MOP, plano de rollback, janela de mudança e responsáveis da Wave 1.',
        ]),
        h3('Critérios de entrada da Wave 1'),
        tabela(['Critério', 'Evidência esperada'], [
            ['Ponta A e ponta B identificadas', 'Matriz de enlaces revisada'],
            ['Serviços dependentes inventariados', 'Lista de serviços e owners'],
            ['Caminho alternativo validado', 'Teste de convergência e reachability'],
            ['Backup disponível', 'Arquivo de configuração e checksum'],
            ['Rollback documentado', 'Sequência objetiva por equipamento'],
            ['Monitoramento preparado', 'Dashboards, alarmes e baseline'],
        ]),
        callout('REGRA.', 'Nenhuma alteração de MTU deverá ser executada sem identificação das duas pontas, '
                'validação do caminho alternativo, inventário dos serviços transportados e rollback documentado.',
                'risco'),
        h3('Pendências de preparação'),
        _tabela_itens(w['itens'], vazio='Nenhuma pendência de inventário identificada.'),
    ])


def s_wave1(ctx):
    plano, conv = ctx['plano'], ctx['conv']
    w = _wave(plano, 1)
    alvo = conv.get('mtu_alvo')
    partes = [
        callout('PRIORIDADE CRÍTICA.', 'Nenhuma wave de controle, serviços ou assinantes deverá avançar sem '
                'validação ponta a ponta da MTU.', 'risco'),
        h3('Objetivo'), p(escape(w['objetivo'])),
        h3('Valores observados no AS-IS'),
        p('Os valores abaixo são inventário técnico a validar, e não definição automática do valor-alvo.'),
        tabela(['Camada/objeto', 'Valor observado', 'Ocorrência', 'Tratamento na Wave 1'],
               [[e(x['camada']), f'<strong>{e(x["valor"])}</strong>', e(x['ocorrencia']), e(x['tratamento'])]
                for x in plano['mtu_observado']]),
    ]
    if alvo:
        partes.append(callout('VALOR-ALVO.', f'MTU do backbone definida em {e(alvo)}'
                              + (f'; MPLS MTU {e(conv.get("mpls_mtu_alvo"))}' if conv.get('mpls_mtu_alvo') else '')
                              + '.', 'status'))
    else:
        partes.append(callout('DECISÃO PENDENTE DO LLD.', 'O HLD não fixa um número único de MTU. O valor-alvo '
                              'deverá ser aprovado no LLD considerando Ethernet, VLAN/QinQ, MPLS, quantidade '
                              'máxima de labels, RSVP-TE legado, L2VPN e PPPoE.', 'risco'))
    partes += [
        h3('Escopo técnico'),
        ul(['Portas físicas e membros de agregação.', 'Eth-Trunks e interfaces roteadas.',
            'Subinterfaces e Vlanifs de backbone.', 'MTU IP para IPv4 e IPv6.', 'MPLS MTU e pilha de labels.',
            'VSI, pseudowire e L2VC.', 'PPPoE, MTU, MRU e ajuste de MSS.',
            'Caminhos de RSVP-TE mantidos como legado controlado.']),
        h3('Unidade mínima da mudança'),
        callout('ENLACE COMPLETO.', 'A unidade mínima é composta por ponta A, meio de transporte e ponta B. A '
                'alteração isolada de apenas um equipamento não caracteriza conclusão do change.'),
        h3('Sequência por enlace'),
        _ol(['Confirmar baseline, alarmes, tráfego e sessões.', 'Validar o caminho alternativo e a capacidade de desvio.',
             'Despriorizar ou retirar o enlace do encaminhamento, quando o desenho permitir.',
             'Ajustar a MTU na ponta A, no meio de transporte e na ponta B conforme o LLD.',
             'Validar física, IP, OSPF, LDP, MPLS e labels.', 'Validar MP-BGP, VPNv4, VPNv6, L2VPN e pseudowires.',
             'Validar PPPoE, BNG e serviços críticos transportados.',
             'Restituir o encaminhamento normal e observar estabilidade.',
             'Atualizar inventário, evidências e status do gate.',
             'Avançar para o próximo enlace somente após aceite.']),
        h3('Agrupamento de execução'),
        tabela(['Lote', 'Domínio', 'Enlaces', 'Objetivo'],
               [[f'<strong>{e(x["lote"])}</strong>', e(x['dominio']), e(x['enlaces']), e(x['objetivo'])]
                for x in plano['lotes_mtu']], vazio='Sem enlaces documentados para agrupar.'),
        h3('Enlaces'),
        _tabela_itens(w['itens'], com_lote=True),
        h3('Critérios de aceite por enlace'),
        ul(['Interfaces sem crescimento anormal de erros ou descartes.', 'Adjacências OSPF e sessões LDP operacionais.',
            'Labels corretos para as loopbacks esperadas.', 'Tráfego pelo caminho previsto e retorno simétrico quando aplicável.',
            'VPNv4/VPNv6, VSI, PW e L2VC sem regressão.', 'PPPoE e serviços críticos sem impacto de MTU/MRU.',
            'Caminhos principal e alternativo testados.', 'Inventário e evidências atualizados.']),
    ]
    return ''.join(partes)


def s_wave2(ctx):
    plano, conv = ctx['plano'], ctx['conv']
    w = _wave(plano, 2)
    estado = [
        ('OSPF', f'Processo {conv.get("ospf_processo")}, área {conv.get("ospf_area")}'),
        ('Router ID', conv.get('router_id')), ('P2P', f'IPv4 /{conv.get("p2p_v4")} e IPv6 /{conv.get("p2p_v6")}'),
        ('Labels', conv.get('labels')), ('LDP/IGP sync', conv.get('ldp_sync')),
        ('BFD', f'{conv.get("bfd_tx")}/{conv.get("bfd_rx")} ms, multiplicador {conv.get("bfd_mult")}'),
        ('ECMP', conv.get('ecmp')), ('RSVP-TE', conv.get('rsvp')), ('IGP', conv.get('igp_escopo')),
    ]
    return ''.join([
        p('Esta wave inicia somente após o gate de MTU.'),
        h3('Estado-alvo'),
        tabela(['Item', 'Padrão TO-BE'], [[f'<strong>{e(a)}</strong>', e(b)] for a, b in estado]),
        h3('Mudanças identificadas'),
        _tabela_itens(w['itens']),
        _gate(['Convergência validada em falha simples.', 'LDP e labels estáveis.',
               'Loopbacks alcançáveis pelos caminhos principal e alternativo.',
               'Sem redistribuição genérica não aprovada.', 'RSVP-TE remanescente inventariado e justificado.']),
    ])


def s_wave3(ctx):
    plano, conv = ctx['plano'], ctx['conv']
    w = _wave(plano, 3)
    return ''.join([
        h3('Estado-alvo'),
        ul([f'RR01: {escape(plano.get("rr01") or conv.get("rr01") or "a definir")}.',
            f'RR02: {escape(plano.get("rr02") or conv.get("rr02") or "a definir")}.',
            'Cada PE e BNG conectado aos dois RRs; sem MP-BGP direto entre PEs.',
            'iBGP normal entre RR01 e RR02, com mesmo cluster ID e router IDs individuais.',
            f'Famílias {escape(conv.get("familias_mpbgp", ""))}; EVPN {escape(str(conv.get("evpn", "")).lower())}.']),
        h3('Atividades'),
        _ol(['Criar grupos e políticas TO-BE em paralelo.', 'Validar reachability das loopbacks pelos dois caminhos.',
             'Ativar sessões com RR01 e RR02 por PE.', 'Comparar rotas recebidas e anunciadas nas famílias habilitadas.',
             'Validar communities e extended communities.',
             'Retirar sessões legadas somente após redundância comprovada.']),
        h3('Mudanças identificadas'),
        _tabela_itens(w['itens']),
        _gate(['Todos os PEs/BNGs com duas sessões RR ativas.', 'VPNv4, VPNv6 e L2VPN consistentes entre RRs.',
               'RIB e FIB comparadas antes e depois.', 'Nenhum serviço dependente de sessão PE a PE não inventariada.']),
    ])


def s_wave4(ctx):
    plano, conv = ctx['plano'], ctx['conv']
    w = _wave(plano, 4)
    mapa = plano['mapeamentos'].get('vrfs') or {}
    partes = [
        h3('Catálogo de serviços'),
        tabela(['Service ID', 'Serviço', 'RT privado'],
               [[f'<strong>{e(s.get("id"))}</strong>', e(s.get('nome')), e(cv.rt(conv, s.get('rt', '')))]
                for s in conv.get('servicos', [])]),
        p(f'Padrão de RD: <code>{e(conv.get("rd_padrao"))}</code>. A mesma VRF utiliza RD distinto por PE e RT '
          'privado comum entre os participantes.'),
        h3('De-para das VRFs legadas'),
        tabela(['VRF atual', 'Destino TO-BE'],
               [[e(k), e({'ELIMINAR': 'Eliminar (walled garden na VRF-ACCESS)',
                          'L3VPN': 'L3VPN dedicada (Wave 5)'}.get(v, v))] for k, v in mapa.items()],
               vazio='Nenhuma VRF no AS-IS.'),
        h3('De-para das communities'),
        tabela(['Community atual', 'Finalidade atual', 'Destino TO-BE'],
               [[e(x['legado']), e(x['finalidade']), e(x['alvo'])] for x in plano['communities']],
               vazio='Nenhuma community no AS-IS.'),
    ]
    if cv.formato_community(conv.get('asn')) == cv.FORMATO_LARGE:
        partes.append(callout('FORMATO DAS COMMUNITIES.', f'O ASN {e(conv.get("asn"))} tem 4 bytes: as communities '
                              'do padrão são large communities (RFC 8092). Validar o suporte em todos os '
                              'equipamentos antes de aplicar.', 'risco'))
    partes += [
        h3('Mudanças identificadas'),
        _tabela_itens(w['itens']),
        callout('DEFAULT DENY.', 'Todo route leaking deverá possuir RT, prefix-list, route-policy, caminho de retorno '
                'e registro no inventário.', 'risco'),
    ]
    return ''.join(partes)


def s_wave5(ctx):
    plano, conv = ctx['plano'], ctx['conv']
    w = _wave(plano, 5)
    return ''.join([
        h3('Padrões TO-BE'),
        tabela(['Tipo', 'Nomenclatura', 'Faixa de IDs', 'RT'],
               [[e(t.get('tipo')), f'<code>{e(t.get("nome"))}</code>', e(t.get('ids')),
                 e(cv.rt(conv, t.get('rts', '')))] for t in conv.get('transportes', [])]),
        h3('Ordem de migração'),
        tabela(['Prioridade', 'Grupo', 'Princípio'], [
            ['1', 'Gerência de OLTs e serviços de baixa criticidade', 'Validar o modelo em escopo controlado'],
            ['2', 'Transportes empresariais sem acesso em massa', 'Migrar por contrato e endpoint'],
            ['3', 'L2VPNs com dependências a confirmar', 'Preservar MTU, QoS e retorno'],
            ['4', f'Serviços críticos ({escape(plano["mapeamentos"].get("criticos") or "—")})',
             'Executar após validação completa do novo serviço'],
            ['5', 'PPPoE dos POPs', 'Migrar em conjunto com a Wave 6'],
        ]),
        h3('Serviços a migrar'),
        _tabela_itens(w['itens'], com_prioridade=True),
        h3('Validações'),
        ul(['Endpoint e identificação do contratante.', 'RD e RT corretos.', 'Sinalização BGP em novos L2VPNs.',
            'MTU ponta a ponta.', 'MAC learning ou rotas esperadas.', 'QoS e counters.', 'Caminho de retorno.',
            'Rollback para o serviço legado.']),
    ])


def s_wave6(ctx):
    plano, conv = ctx['plano'], ctx['conv']
    w = _wave(plano, 6)
    alvo = [['BNG01', e(plano.get('bng01') or conv.get('bng01') or 'a definir')],
            ['BNG02', e(plano.get('bng02') or conv.get('bng02') or 'a definir')]]
    return ''.join([
        h3('Arquitetura alvo'),
        tabela(['Função', 'Equipamento / local'], alvo),
        ul([escape(conv.get('acesso_regra', '')), 'Migração POP a POP, com coexistência controlada.',
            'Validação de PPPoE, AAA, accounting, CoA, IPv4, IPv6 PD e retorno.',
            'Remoção de BNG remoto somente após aceite do POP.']),
        h3('Mudanças identificadas'),
        _tabela_itens(w['itens']),
    ])


def s_wave7(ctx):
    plano, conv = ctx['plano'], ctx['conv']
    w = _wave(plano, 7)
    return ''.join([
        callout('TO-BE.', escape(conv.get('cgnat_modo', '')), 'status'),
        h3('Atividades'),
        _ol(['Validar hardware, licença e capacidade das cards de serviço.',
             'Criar VRF-CGNAT e RTs privados/compartilhados previstos no HLD.',
             'Preparar domínios inside e outside e respectivas políticas.',
             'Reproduzir de forma controlada os blocos, pools e requisitos operacionais do serviço atual.',
             'Executar piloto com subconjunto de assinantes.',
             'Validar tradução, retorno, logging, bypasses e observabilidade.',
             'Migrar blocos em lotes controlados.',
             'Retirar o elemento externo somente depois do aceite e estabilidade.']),
        h3('Mudanças identificadas'),
        _tabela_itens(w['itens']),
        _gate(['CGNAT ativo nas cards.', 'Inside e outside validados.', 'Retorno e logging validados.',
               'Capacidade e alarmes monitorados.', 'Elementos externos de CGNAT sem dependências remanescentes.']),
    ])


def s_wave8(ctx):
    plano, conv = ctx['plano'], ctx['conv']
    w = _wave(plano, 8)
    ident = []
    for tipo, lista in (plano.get('identidades') or {}).items():
        for asn, n in lista:
            ident.append([e(f'{tipo}{n:02d}'), e(f'AS{asn}'), e(cv.community(conv, {'UPLINK': 20000, 'CONTENT': 20100,
                                                                                    'IX': 20200}[tipo] + (n - 1) * 10))])
    return ''.join([
        ul(['Implantar identidades lógicas UPLINK01-10, CONTENT01-10 e IX01-10.',
            'Aplicar classes de Local Preference do HLD.',
            'Implantar communities internas e públicas padronizadas.',
            'Aplicar RPKI com rejeição de INVALID, fullbogons receive-only, max-prefix e RTBH.',
            'Limitar exportação a prefixos próprios, clientes diretos e RTBH autorizado.',
            'Negar trânsito lateral entre downstreams e preservar o isolamento da VRF-IX.']),
        h3('Identidades lógicas propostas'),
        tabela(['Identidade', 'Peer atual', 'Community'], ident, vazio='Nenhuma sessão de borda classificada.'),
        h3('Sessões de borda'),
        _tabela_itens(w['itens']),
    ])


def s_wave9(ctx):
    w = _wave(ctx['plano'], 9)
    return ''.join([
        h3('Objetos candidatos à retirada'),
        _tabela_itens(w['itens'], vazio='Nenhum objeto legado identificado.'),
        h3('Critérios de desativação'),
        ul(['Serviço substituto validado.', 'Caminho de ida e retorno comprovados.',
            'Período de estabilização definido no MOP concluído.', 'Sem tráfego ou sessão legítima remanescente.',
            'Rollback não depende mais do objeto.', 'Inventário e documentação atualizados.',
            'Aprovação formal do encerramento.']),
        callout('PRINCÍPIO.', 'Nenhum objeto legado deve ser removido apenas por aparente ausência de tráfego. A '
                'retirada exige evidência técnica, validação do substituto e atualização da fonte única de verdade.'),
    ])


def s_comunicacao(ctx):
    return tabela(['Momento', 'Comunicação/evidência'], [
        ['Antes da janela', 'Escopo, impacto, responsáveis, critérios de go/no-go e rollback'],
        ['Início', 'Registro do estado inicial e autorização de execução'],
        ['Durante', 'Marcos, desvios, alarmes e decisões'],
        ['Rollback', 'Motivo, ponto da execução e estado restaurado'],
        ['Aceite', 'Resultados dos testes, counters, sessões e serviços'],
        ['Encerramento', 'Inventário atualizado, pendências e próximos passos'],
    ])


def s_checklist(ctx):
    itens = ['LLD e MOP aprovados', 'Backup e baseline coletados', 'Ponta A e ponta B confirmadas',
             'Caminho alternativo testado', 'Serviços dependentes inventariados', 'Janela e responsáveis comunicados',
             'Critérios de go/no-go definidos', 'Rollback validado', 'Monitoramento e alarmes preparados',
             'Mudança aplicada conforme sequência', 'Testes técnicos concluídos', 'Testes de serviço concluídos',
             'Retorno validado', 'Evidências anexadas', 'Inventário atualizado', 'Aceite e encerramento registrados']
    return tabela(['Status', 'Item'], [['☐', e(i)] for i in itens])


def s_criterio(ctx):
    plano = ctx['plano']
    bng = ' e '.join(filter(None, [plano.get('bng01'), plano.get('bng02')])) or 'nos BNGs centrais'
    return ul(['Backbone com MTU validada ponta a ponta.', 'Underlay padronizado e estável.',
               'Dois Route Reflectors e redundância MP-BGP comprovada.',
               'VRFs, RD, RT e communities aderentes ao HLD.', 'Transportes críticos migrados e inventariados.',
               f'BNG centralizado em {escape(bng)}.', 'CGNAT executado nas cards dos BNGs centrais.',
               'Borda de Internet, Content, IX e downstreams com políticas padronizadas.',
               'Legado removido somente após aceite e estabilização.'])


def s_referencias(ctx):
    refs = ctx['refs']
    partes = [p('Documentos utilizados para estruturar o plano:'), ul([escape(x) for x in refs.get('documentos', [])])]
    if refs.get('backups'):
        partes.append(h3('Backups de configuração'))
        partes.append(ul([escape(x) for x in refs['backups']]))
    partes.append(callout('NOTA DE CONTROLE.', 'Este plano define a ordem e os gates do programa. Cada wave deve '
                          'possuir LLD e MOP próprios antes da execução em produção.'))
    return ''.join(partes)


SECOES = [
    ('tobe_controle', 'Controle, objetivos e governança', s_controle),
    ('tobe_visao', 'Visão geral das waves', s_visao),
    ('tobe_premissas', 'Papéis-alvo e decisões pendentes', s_premissas),
    ('tobe_wave_0', 'Wave 0 | Baseline, inventário e preparação', s_wave0),
    ('tobe_wave_1', 'Wave 1 | Correção de MTU do backbone', s_wave1),
    ('tobe_wave_2', 'Wave 2 | Underlay OSPF/MPLS/LDP', s_wave2),
    ('tobe_wave_3', 'Wave 3 | Route Reflectors e MP-BGP', s_wave3),
    ('tobe_wave_4', 'Wave 4 | VRFs, RD, RT e communities', s_wave4),
    ('tobe_wave_5', 'Wave 5 | L2VPN, L3VPN e serviços críticos', s_wave5),
    ('tobe_wave_6', 'Wave 6 | BNG centralizado', s_wave6),
    ('tobe_wave_7', 'Wave 7 | CGNAT integrado aos BNGs', s_wave7),
    ('tobe_wave_8', 'Wave 8 | Uplinks, Content, IX e downstreams', s_wave8),
    ('tobe_wave_9', 'Wave 9 | Desativação do legado e estabilização', s_wave9),
    ('tobe_comunicacao', 'Plano de comunicação e evidências', s_comunicacao),
    ('tobe_checklist', 'Checklist executivo por change', s_checklist),
    ('tobe_criterio', 'Critério global de conclusão do programa', s_criterio),
    ('tobe_referencias', 'Referências técnicas', s_referencias),
]

CAMPOS_METADADOS = [
    ('documento', 'Documento'), ('versao', 'Versão'), ('status', 'Status'), ('asn', 'ASN'),
    ('responsavel', 'Responsável'), ('classificacao', 'Classificação'), ('ambito', 'Âmbito'),
    ('hld', 'Documento de arquitetura relacionado'), ('data', 'Data'),
]


def metadados_padrao(conv, empresa, hld_titulo='', responsavel='', data=''):
    return {
        'empresa': empresa,
        'titulo': 'CHANGE PLAN TO-BE',
        'subtitulo': f'Transformação da Arquitetura ISP | AS{conv.get("asn", "")} — waves de mudança, gates de '
                     'qualidade e critérios de aceite',
        'documento': f'Plano de Change TO-BE {empresa}',
        'versao': '1.0',
        'status': 'Em aprovação para detalhamento em LLD e execução controlada',
        'asn': conv.get('asn', ''),
        'responsavel': responsavel,
        'classificacao': 'Uso interno',
        'ambito': 'Transformação técnica do backbone e dos serviços ISP',
        'hld': hld_titulo,
        'data': data,
        'principio_titulo': 'PRIORIDADE CRÍTICA.',
        'principio': 'A Wave 1 trata a correção e normalização de MTU em todo o backbone. Nenhuma wave de controle, '
                     'serviços ou assinantes deverá avançar sem validação ponta a ponta da MTU.',
    }
