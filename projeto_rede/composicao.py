"""
Composição do documento AS-IS: modelo analisado → seções HTML editáveis.

O HTML gerado aqui usa só o subconjunto que `sanitizar.py` aceita e que
`exportacao.py` sabe converter para DOCX: h3/h4, p, ul/ol/li, table,
strong/em/code, `div.callout` e `span.sev-*`. Títulos de seção e subseção
não levam número — a numeração é aplicada na renderização, para continuar
certa depois que o usuário reordenar ou apagar seções.

Todo texto vindo de configuração passa por `escape`.
"""
from collections import Counter
from datetime import datetime

from django.utils.html import escape

from .analise import SEVERIDADES

# ═══════════════════════════════════════════════════════════════════════════
# Blocos de HTML
# ═══════════════════════════════════════════════════════════════════════════


def _t(v):
    if v is None or v == '' or v == []:
        return '—'
    if isinstance(v, bool):
        return 'Sim' if v else 'Não'
    if isinstance(v, (list, tuple, set)):
        return ', '.join(str(x) for x in v) or '—'
    return str(v)


def p(texto):
    return f'<p>{texto}</p>'


def h3(texto):
    return f'<h3>{escape(texto)}</h3>'


def ul(itens):
    return '<ul>' + ''.join(f'<li>{i}</li>' for i in itens) + '</ul>'


def callout(titulo, texto, tipo='info'):
    classe = {'info': 'callout', 'risco': 'callout callout-risco', 'status': 'callout callout-status'}[tipo]
    return f'<div class="{classe}"><p><strong>{escape(titulo)}</strong> {texto}</p></div>'


def tabela(cabecalhos, linhas, vazio='Nenhum item identificado nas configurações analisadas.'):
    if not linhas:
        return p(f'<em>{escape(vazio)}</em>')
    th = ''.join(f'<th>{escape(c)}</th>' for c in cabecalhos)
    corpo = ''
    for linha in linhas:
        corpo += '<tr>' + ''.join(f'<td>{c}</td>' for c in linha) + '</tr>'
    return f'<table><thead><tr>{th}</tr></thead><tbody>{corpo}</tbody></table>'


def e(v):
    """Escapa e normaliza vazio → travessão."""
    return escape(_t(v))


def sev(nivel):
    classe = {'Crítica': 'sev-critica', 'Alta': 'sev-alta', 'Média': 'sev-media',
              'Baixa': 'sev-baixa'}.get(nivel, 'sev-info')
    return f'<span class="{classe}">{escape(nivel)}</span>'


_FABRICANTES = {'mikrotik': 'MikroTik', 'huawei': 'Huawei', 'zte': 'ZTE', 'a10': 'A10',
                'cisco': 'Cisco', 'juniper': 'Juniper', 'datacom': 'Datacom', 'hillstone': 'Hillstone'}


def nome_fabricante(vendor):
    return _FABRICANTES.get((vendor or '').lower(), (vendor or '').capitalize())


def _data_br(d):
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d)
        except ValueError:
            return d
    return d.strftime('%d/%m/%Y') if d else '—'


def _periodo(m):
    ini, fim = m['periodo']
    if not ini:
        return 'sem backups disponíveis'
    if ini.date() == fim.date():
        return f'coletados em {_data_br(ini)}'
    return f'coletados entre {_data_br(ini)} e {_data_br(fim)}'


# ═══════════════════════════════════════════════════════════════════════════
# Seções
# ═══════════════════════════════════════════════════════════════════════════

def s_controle(m):
    empresa = escape(m['cliente']['nome'])
    total_topo = len(m['topologia']['mapas'])
    fontes = [
        f'Backups de configuração de <strong>{m["total_com_backup"]}</strong> equipamento(s), '
        f'{escape(_periodo(m))}.',
        f'Cadastro de <strong>{m["total_acessos"]}</strong> acesso(s) do cliente no CRM (função, modelo e endereço de gerência).',
    ]
    if total_topo:
        fontes.append(f'Topologia documentada no CRM: {total_topo} mapa(s) e '
                      f'{len(m["topologia"]["enlaces"])} enlace(s) com interfaces, endereçamento e VLAN.')
    if m['blocos_ip']:
        fontes.append(f'{len(m["blocos_ip"])} bloco(s) IP cadastrados para o cliente.')
    fontes.append('Validações operacionais fornecidas pelo responsável da rede durante a revisão deste documento.')
    fontes.append('HLD utilizado apenas para contextualizar o futuro TO-BE e a governança de migração.')
    return ''.join([
        h3('Objetivo'),
        p(f'Documentar o estado operacional conhecido da rede {empresa} antes da implantação da arquitetura '
          'TO-BE. O documento consolida backbone, MPLS, MP-BGP, acesso, BNG, CGNAT, trânsito IP, parceiros '
          'de conteúdo, serviços especiais e riscos residuais.'),
        h3('Fontes e método'),
        ul(fontes),
        p('A extração é automática e determinística: cada informação deste documento tem origem em uma linha '
          'de configuração, no cadastro ou na topologia. Classificações (papel de equipamento, tipo de sessão '
          'eBGP, finalidade de community) são inferidas pela política aplicada e estão sujeitas à revisão técnica.'),
        h3('Limitações'),
        callout('Risco residual aceito.',
                'O levantamento foi reconstruído a partir das configurações disponíveis. Serviços não documentados '
                'ou dependências fora dos equipamentos com backup poderão ser incorporados por adendo quando descobertos.',
                'risco'),
        h3('Critério de atualização'),
        p('Novas descobertas devem atualizar o inventário, registrar a evidência técnica, indicar os equipamentos '
          'envolvidos e avaliar o impacto sobre o TO-BE e as waves de migração.'),
    ])


def s_sumario(m):
    vendors = Counter()
    for eq in m['equipamentos']:
        if eq.get('vendor'):
            vendors[nome_fabricante(eq['vendor'])] += 1
    mix = ', '.join(f'{v} ({n})' for v, n in vendors.most_common()) or 'não identificado'
    fams = [f for f in ('vpnv4', 'vpnv6', 'l2vpn-ad-family') if m['familias'].get(f)]
    fams_txt = ', '.join({'vpnv4': 'VPNv4', 'vpnv6': 'VPNv6', 'l2vpn-ad-family': 'L2VPN'}[f] for f in fams)
    polos = sorted({r['pop'] for r in m['rrs']})
    tem_mpls = any(b['ldp'] for b in m['backbone'])
    tem_te = any(b['rsvp'] for b in m['backbone'])

    texto = [f'A rede opera com equipamentos {escape(mix)}']
    if tem_mpls:
        texto.append(' e backbone MPLS com OSPF, LDP' + (', MP-BGP' if fams else '')
                     + (' e uso de RSVP-TE' if tem_te else ''))
    texto.append('.')
    if polos:
        texto.append(f' Os route reflectors estão em {escape(", ".join(polos))}, com '
                     f'{len(m["pes"])} PE(s) distribuídos nos demais POPs.')
    if m['bngs_centrais'] or m['bngs_remotos']:
        texto.append(f' O acesso PPPoE é terminado em {len(m["bngs_centrais"])} BNG(s) central(is)'
                     + (f', coexistindo com {len(m["bngs_remotos"])} BNG(s) remoto(s) legado(s)' if m['bngs_remotos'] else '')
                     + '.')

    ups = len({(s['asn'], s['pop']) for s in m['upstreams']})
    downs = len(m['downstreams'])
    linhas = [
        ['Backbone', e(', '.join(filter(None, [
            'OSPF' if any(b['ospf'] != '—' for b in m['backbone']) else '',
            'MPLS/LDP' if tem_mpls else '', 'RSVP-TE' if tem_te else '']))
            + (f' em {len(m["backbone"])} equipamento(s)' if m['backbone'] else ''))],
        ['Controle', e(f'MP-BGP com RRs em {", ".join(polos)}; famílias {fams_txt}' if polos else
                       ('MP-BGP sem route reflector identificado' if fams else 'Sem MP-BGP identificado'))],
        ['Acesso', e(f'{len(m["pppoe"])} serviço(s) PPPoE transportado(s) por L2VPN até BNG central; '
                     f'{len(m["bngs_remotos"])} BNG(s) remoto(s)' if (m['pppoe'] or m['bngs_remotos'])
                     else 'Sem BNG identificado')],
        ['CGNAT', e(', '.join(f'{c["nome"]} ({c["pop"]})' for c in m['cgnats'][:6]) or 'Não identificado')],
        ['Trânsito', e(f'{ups} upstream(s) e {downs} ISP downstream(s) em diferentes POPs')],
        ['Serviços especiais', e(f'{len(m["vrfs"])} VRF(s), {len(m["l2vpn"])} serviço(s) L2VPN, '
                                 f'{len(m["parceiros"])} sessão(ões) com parceiros de conteúdo/IX')],
        ['Risco', e(f'{len(m["achados"])} achado(s) estrutural(is), dos quais '
                    f'{len([a for a in m["achados"] if a["severidade"] in ("Crítica", "Alta")])} crítico(s)/alto(s)')],
    ]
    return p(''.join(texto)) + tabela(['Domínio', 'Estado AS-IS consolidado'],
                                      [[f'<strong>{a}</strong>', b] for a, b in linhas])


def s_topologia(m):
    partes = []
    if m['sessoes_rr']:
        partes.append(h3('Polos de controle'))
        rr_por_nome = {r['nome']: r for r in m['rrs']}
        linhas = []
        for s in m['sessoes_rr']:
            r = rr_por_nome.get(s['rr'], {})
            linhas.append([e(' / '.join(['RR'] + [x for x in r.get('papeis', []) if x != 'RR'])),
                           f'<strong>{e(s["rr"])}</strong>', e(r.get('lsr_id') or s['router_id']),
                           e(r.get('funcoes'))])
        partes.append(tabela(['Papel', 'Equipamento', 'LSR/Router ID', 'Funções observadas'], linhas))

    partes.append(h3('PEs analisados'))
    partes.append(tabela(
        ['POP / Nó', 'Equipamento', 'LSR ID', 'Plataforma', 'Software'],
        [[e(x['pop']), f'<strong>{e(x["nome"])}</strong>', e(x['lsr_id']), e(x['modelo']), e(x['versao'])]
         for x in m['pes']]))

    outros = [x for x in m['demais'] if x['vendor'] or x['backup']]
    if outros:
        partes.append(h3('Demais equipamentos cadastrados'))
        partes.append(tabela(
            ['Equipamento', 'Função (cadastro)', 'Plataforma', 'Papéis inferidos', 'Backup analisado'],
            [[e(x['nome']), e(x['funcao']), e(x['modelo']), e(x['papeis']),
              e(_data_br(x['backup']['confirmado_em']) if x['backup'] and x['backup']['arquivo_disponivel'] else 'Não')]
             for x in sorted(outros, key=lambda x: (x['funcao'], x['nome']))]))

    enlaces = m['topologia']['enlaces']
    if enlaces:
        partes.append(h3('Enlaces documentados na topologia'))
        partes.append(tabela(
            ['Ponta A', 'Interface A', 'Ponta B', 'Interface B', 'Endereçamento', 'VLAN', 'Capacidade'],
            [[e(l['a']), e(l['iface_a']), e(l['b']), e(l['iface_b']),
              e(' ↔ '.join(filter(None, [l['ip_a'], l['ip_b']]))), e(l['vlan']), e((l['capacidade'] or '').upper())]
             for l in enlaces]))
    return ''.join(partes)


def s_underlay(m):
    partes = [h3('OSPF e MPLS')]
    bb = m['backbone']
    if bb:
        processos = Counter(b['ospf'] for b in bb if b['ospf'] != '—')
        itens = []
        if processos:
            proc, n = processos.most_common(1)[0]
            itens.append(f'Processo OSPF {escape(proc)} predomina no backbone ({n} de {len(bb)} equipamentos).')
        com_lsr = len([b for b in bb if b['lsr_id']])
        itens.append(f'{com_lsr} de {len(bb)} equipamentos com LSR-ID definido (loopback como identificador).')
        ldp = [b for b in bb if b['ldp']]
        itens.append(f'MPLS LDP habilitado em {len(ldp)} equipamento(s), '
                     f'{sum(b["ldp_ifs"] for b in bb)} interface(s) no total; '
                     f'{sum(b["ldp_remotos"] for b in bb)} LDP remote-peer(s) para pseudowires.')
        rsvp = [b for b in bb if b['rsvp']]
        if rsvp:
            com_tunel = [b['nome'] for b in bb if b['tuneis']]
            itens.append(f'RSVP-TE habilitado em {len(rsvp)} equipamento(s); túneis TE explícitos em: '
                         f'{escape(", ".join(com_tunel) or "nenhum")}.')
        partes.append(ul(itens))
        partes.append(tabela(
            ['Equipamento', 'LSR ID', 'OSPF', 'LDP (ifs)', 'RSVP-TE (ifs)', 'Túneis TE', 'BFD', 'MTU backbone', 'MPLS MTU'],
            [[e(b['nome']), e(b['lsr_id']), e(b['ospf']), e(f'{"Sim" if b["ldp"] else "Não"} ({b["ldp_ifs"]})'),
              e(f'{"Sim" if b["rsvp"] else "Não"} ({b["rsvp_ifs"]})'), e(b['tuneis']), e(b['bfd_detalhe']),
              e(b['mtus']), e(b['mpls_mtus'])] for b in bb]))
    else:
        partes.append(p('<em>Nenhum equipamento com OSPF/MPLS identificado nos backups.</em>'))

    partes.append(h3('MP-BGP e Route Reflectors'))
    nomes_af = {'vpnv4': 'VPNv4', 'vpnv6': 'VPNv6', 'l2vpn-ad-family': 'L2VPN (BGP AD/VPLS)',
                'unicast-v4': 'IPv4 unicast', 'unicast-v6': 'IPv6 unicast', 'evpn': 'EVPN'}
    itens = []
    if m['sessoes_rr']:
        for s in m['sessoes_rr']:
            itens.append(f'RR em <strong>{e(s["pop"])}</strong> ({e(s["rr"])}) com {s["clientes"]} cliente(s) '
                         f'refletido(s) nas famílias {e(", ".join(nomes_af.get(f, f) for f in s["familias"]))}.')
    fams = [f'{nomes_af.get(f, f)} ({n})' for f, n in m['familias'].most_common() if not f.startswith('vrf')]
    if fams:
        itens.append(f'Famílias observadas nas sessões iBGP (quantidade de sessões): {e(", ".join(fams))}.')
    if m['peers_ibgp_desconhecidos']:
        itens.append(f'{len(m["peers_ibgp_desconhecidos"])} sessão(ões) iBGP apontam para endereços sem equipamento '
                     'cadastrado (peers históricos) — ver tabela abaixo.')
    partes.append(ul(itens) if itens else p('<em>Nenhuma sessão iBGP identificada.</em>'))
    if m['peers_ibgp_desconhecidos']:
        partes.append(tabela(['Configurado em', 'Peer', 'Descrição'],
                             [[e(x['equipamento']), e(x['peer']), e(x['descricao'])]
                              for x in m['peers_ibgp_desconhecidos']]))

    partes.append(h3('BFD'))
    com = [b['nome'] for b in bb if b['bfd_detalhe'] != 'Não']
    partes.append(p(f'BFD aparece em {len(com)} de {len(bb)} equipamento(s) do backbone '
                    f'({m["ospf_ifs_bfd"]} de {m["ospf_ifs"]} interfaces OSPF com BFD), '
                    + ('de forma não uniforme entre enlaces e serviços.' if len(com) < len(bb) else 'de forma uniforme.')))
    return ''.join(partes)


def s_achados(m):
    if not m['achados']:
        return p('<em>Nenhum achado estrutural identificado automaticamente.</em>')
    cont = Counter(a['severidade'] for a in m['achados'])
    resumo = ', '.join(f'{cont[s]} {s.lower()}' for s in SEVERIDADES if cont[s])
    return p(f'Foram identificados {len(m["achados"])} achados ({escape(resumo)}). A severidade considera o impacto '
             'na migração para o TO-BE e na operação atual.') + tabela(
        ['ID', 'Severidade', 'Achado', 'Evidência / impacto'],
        [[f'<strong>{e(a["id"])}</strong>', sev(a['severidade']), e(a['titulo']),
          f'{e(a["evidencia"])} <em>{e(a["impacto"])}</em>'] for a in m['achados']])


def s_bng(m):
    partes = [h3('BNGs centrais')]
    partes.append(tabela(
        ['Local', 'Equipamento', 'Plataforma', 'Características'],
        [[e(b['pop']), e(b['nome']), e(b['modelo']), e('; '.join(filter(None, [
            'PPPoE' + (f' com {b["pppoe_l2vpn"]} serviço(s) via L2VPN' if b['pppoe_l2vpn'] else ''),
            ('VRF ' + ', '.join(b['vrfs'])) if b['vrfs'] else '',
            'pools ' + ', '.join(f'{x["nome"]} {x["rede"]}' for x in b['pools'][:4]) if b['pools'] else '',
            'IPv6 PD ' + ', '.join(f'{x["prefixo"]}' + (f' (/{x["tamanho_delegado"]})' if x['tamanho_delegado'] else '')
                                   for x in b['pools_v6'] if x['prefixo'])[:120] if b['pools_v6'] else '',
            f'RADIUS: {", ".join(b["radius"])}' if b['radius'] else '',
        ])))] for b in m['bngs_centrais']],
        vazio='Nenhum BNG central Huawei identificado.'))

    if m['pppoe']:
        partes.append(h3('PPPoE transportado para o BNG central'))
        partes.append(tabela(
            ['ID/VSI', 'Serviço', 'VLAN no BNG', 'BNG', 'Instância'],
            [[f'<strong>{e(x["id"])}</strong>', e(x['servico']), e(x['vlan']), e(x['pop_bng']), e(x['vsi'])]
             for x in m['pppoe']]))

    partes.append(h3('BNGs remotos legados'))
    if m['bngs_remotos']:
        nomes = ', '.join(b['nome'] for b in m['bngs_remotos'])
        partes.append(p('Os BNGs remotos são registrados como existência e dependência, sem inventário aprofundado, '
                        f'por estarem previstos para descontinuidade: {e(nomes)}.'))
        partes.append(tabela(
            ['Equipamento', 'Plataforma', 'Servidores PPPoE', 'RADIUS', 'NAT embarcado', 'Pools', 'Backup'],
            [[e(b['nome']), e(b['modelo']), e(b['pppoe']), e(bool(b['radius'])), e(b['nat_embarcado']),
              e(b['pools']), e('Não' if b['sem_backup'] else 'Sim')] for b in m['bngs_remotos']]))
    else:
        partes.append(p('<em>Nenhum BNG remoto identificado.</em>'))

    partes.append(h3('CGNAT'))
    for c in m['cgnats']:
        if c.get('sem_backup'):
            partes.append(p(f'<strong>{e(c["nome"])}</strong>: cadastrado como CGNAT, sem backup analisado.'))
            continue
        partes.append(tabela(['Item', f'AS-IS — {c["nome"]}'], [
            ['Equipamento', e(f'{c["modelo"]} / {c["nome"]}' + (f' (RouterOS {c["versao"]})' if c['versao'] else ''))],
            ['Integração', e(c['integracao'])],
            ['Pool privado', e(c['pools_privados'])],
            ['Pool público', e(c['publicos'])],
            ['Roteamento', e('; '.join(filter(None, [c['bgp'], f'default via {c["default"]}' if c['default'] else ''])))],
            ['NAT', e(f'{c["regras"]} regra(s) ativas; {c["netmap"]} netmap, {c["src_nat"]} src-nat'
                      + ('; faixas determinísticas de portas' if c['deterministico'] else '')
                      + (f'; origem: {c["comentarios"]}' if c['comentarios'] else ''))],
        ]))
    if not m['cgnats']:
        partes.append(p('<em>Nenhum CGNAT identificado.</em>'))
    return ''.join(partes)


def s_transito(m):
    partes = [p('Sessões eBGP classificadas pela política aplicada (prefixos aceitos, rotas anunciadas, '
                'local-preference e communities), não apenas pela descrição.')]
    partes.append(h3('Upstreams'))
    partes.append(tabela(
        ['Operadora', 'POP', 'ASN', 'Peer', 'Família', 'Recebe', 'Anuncia', 'LP', 'Estado'],
        [[e(s['descricao'] or s['cliente']), e(s['pop']), e(s['asn']), e(s['peer']), e(s['familia'].upper()),
          e(s['recebe']), e(s['anuncia']), e(s['lp']), e('Ativa' if s['ativa'] else 'Desativada')]
         for s in m['upstreams']]))

    partes.append(h3('ISP downstreams (clientes de trânsito)'))
    partes.append(tabela(
        ['Cliente', 'POP', 'ASN', 'Entrega', 'Prefixos aceitos', 'LP', 'Communities na importação'],
        [[f'<strong>{e(g["cliente"])}</strong>', e(g['pop']), e(g['asn']), e(g['entrega_txt']),
          e('; '.join(g['prefixos'])), e(g['lp']),
          e(', '.join(g['communities']))] for g in m['downstreams']]))
    multi = [g for g in m['downstreams'] if g['multi_perfil']]
    if multi:
        nomes = sorted({f'{g["cliente"]} (AS{g["asn"]})' for g in multi})
        partes.append(callout('Perfis distintos por POP.',
                              f'{e(", ".join(nomes))} possuem sessões com entrega diferente em POPs diferentes. '
                              'Validar operacionalmente antes da migração.'))
    if m['listas_nome_divergente']:
        partes.append(callout('Atenção à entrega real.',
                              'Há prefix-lists nomeadas como DEFAULT que permitem mais que a rota default '
                              '(ex.: <code>:: 0 less-equal 48</code>). A coluna “Entrega” reflete o conteúdo da lista, '
                              'não o nome.', 'risco'))

    if m['estaticas']:
        partes.append(h3('Entregas L3 por rota estática'))
        partes.append(p('Blocos públicos roteados estaticamente para clientes sem sessão BGP.'))
        partes.append(tabela(
            ['Descrição', 'POP', 'Bloco', 'Próximo salto', 'VRF'],
            [[e(r['descricao']), e(r['pop']), e(r['prefixo']), e(r['proximo_salto']), e(r['vrf'] or 'global')]
             for r in m['estaticas']]))

    if m['nao_classificados']:
        partes.append(h3('Sessões eBGP sem classificação automática'))
        partes.append(tabela(
            ['Descrição', 'POP', 'ASN', 'Peer', 'VRF', 'Recebe', 'Anuncia', 'Estado'],
            [[e(s['descricao']), e(s['pop']), e(s['asn']), e(s['peer']), e(s['vrf']), e(s['recebe']),
              e(s['anuncia']), e('Ativa' if s['ativa'] else 'Desativada')] for s in m['nao_classificados']]))
    return ''.join(partes)


def s_servicos(m):
    partes = [h3('VRFs')]
    partes.append(tabela(
        ['VRF', 'RD', 'Route targets', 'PEs', 'Sessões eBGP'],
        [[f'<strong>{e(v["nome"])}</strong>', e(v['rds']), e(v['rts'][:6]), e(v['pes']), e(v['peers_ebgp'])]
         for v in m['vrfs']]))
    legado = [v for v in m['vrfs'] if v['rd_legado'] or v['rd_divergente']]
    if legado:
        partes.append(callout('RD/RT fora do padrão.',
                              e('; '.join(f'{v["nome"]}: {", ".join(sorted(set(v["fora_padrao"])) or v["rds"])}'
                                          for v in legado)), 'risco'))

    if m['clientes_l3vpn']:
        partes.append(h3('Clientes L3VPN'))
        partes.append(tabela(
            ['VRF', 'POP', 'ASN', 'Peer', 'Descrição', 'Policy in/out', 'BFD'],
            [[e(s['vrf']), e(s['pop']), e(s['asn']), e(s['peer']), e(s['descricao']),
              e(f'{s["policy_in"] or "—"} / {s["policy_out"] or "—"}'), e(s['bfd'])]
             for s in m['clientes_l3vpn']]))

    partes.append(h3('Parceiros de conteúdo, CDN e IX'))
    partes.append(tabela(
        ['Parceiro', 'POP', 'ASN', 'Peer', 'Classificação', 'Recebe', 'Anuncia', 'Observações'],
        [[e(s['descricao'] or s['cliente']), e(s['pop']), e(s['asn']), e(s['peer']), e(s['classe']),
          e(s['recebe']), e(s['anuncia']),
          e('; '.join(filter(None, [
              f'LP {", ".join(map(str, s["lp"]))}' if s['lp'] else '',
              f'communities {", ".join(s["communities_in"][:4])}' if s['communities_in'] else '',
              'remove communities na saída' if s['remove_communities_out'] else '',
              f'prepend {s["prepend_out"]}x' if s['prepend_out'] else '',
              'descrição divergente' if s['divergente'] else '',
          ])))] for s in m['parceiros']]))

    if m['internos']:
        partes.append(h3('Sessões internas (ASN privado / CGNAT / BNG)'))
        partes.append(tabela(
            ['Equipamento', 'Peer', 'ASN', 'Descrição', 'Classificação', 'Estado'],
            [[e(s['equipamento']), e(s['peer']), e(s['asn']), e(s['descricao']), e(s['classe']),
              e('Ativa' if s['ativa'] else 'Desativada')] for s in m['internos']]))
    return ''.join(partes)


def s_l2vpn(m):
    servicos = [s for s in m['l2vpn']]
    if not servicos:
        return p('<em>Nenhum serviço L2VPN identificado nos backups.</em>')
    cont = Counter(s['tipo'].upper() for s in servicos)
    return p(f'{len(servicos)} serviço(s) L2 identificados ('
             + escape(', '.join(f'{n} {t}' for t, n in cont.most_common()))
             + '). O mesmo ID em equipamentos diferentes foi consolidado como um serviço.') + tabela(
        ['ID', 'Serviço', 'Pontas', 'VLANs', 'Tecnologia', 'MTU', 'Observações'],
        [[f'<strong>{e(s["id"])}</strong>', e(s['nomes'][:3]), e(s['pontas']), e(s['vlans'][:6]),
          e(' / '.join(filter(None, [', '.join(s['tecnologias']), ', '.join(s['sinalizacao'])]))),
          e(s['mtus']),
          e('; '.join(filter(None, [
              'PPPoE' if s['pppoe'] else '',
              'uma ponta conhecida' if s['uma_ponta'] else '',
              f'peer sem cadastro: {", ".join(s["peers_desconhecidos"])}' if s['peers_desconhecidos'] else '',
              ', '.join(s['descricoes'][:2]),
          ])))] for s in servicos])


def s_communities(m):
    c = m['communities']
    if not c['linhas']:
        return p('<em>Nenhuma community BGP identificada nas configurações.</em>')
    partes = [p('O AS-IS utiliza communities para classificar origem, importação e intenção de anúncio. O mapeamento '
                'abaixo registra o uso atual e não deve ser confundido com o padrão a ser definido para o TO-BE.')]
    partes.append(tabela(
        ['Finalidade atual', 'Community / faixa', 'Filtros', 'Onde aparece', 'Aplicações'],
        [[e(x['finalidade']), f'<strong>{e(x["valor"])}</strong>', e(x['filtros'][:4]),
          e(x['equipamentos'][:6]), e(x['aplicada'])] for x in c['linhas']]))
    if c['regra_prepend']:
        partes.append(callout('Regra de prepend vigente.',
                              'Nas famílias identificadas, o último dígito de 0 a 5 indica a quantidade de repetições '
                              'do ASN no AS-PATH.'))
    if c['asn_4byte'] and c['prefixo_difere_asn']:
        partes.append(callout('Prefixo das communities.',
                              f'As communities usam o prefixo {e(c["prefixo"])}, diferente do ASN {e(m["asn"])} '
                              '(4 bytes). Avaliar large-communities no TO-BE.', 'risco'))
    return ''.join(partes)


def s_seguranca(m):
    seg = m['seguranca']
    huawei = [s for s in seg if s['vendor'] == 'Huawei']
    telnet = [s['nome'] for s in seg if s['risco_telnet']]
    ftp = [s['nome'] for s in seg if s['risco_ftp']]
    snmp_v2 = [s['nome'] for s in huawei if s['risco_snmp']]
    bogons = len([s for s in m['ebgp'] if s['filtra_bogons']])
    ebgp_pub = len([s for s in m['ebgp'] if s['vendor'] == 'huawei' and s['vrf_internet']])
    datas = _periodo(m)
    linhas = [
        ['Acesso de gerência', e(f'{len(telnet)} equipamento(s) com telnet ativo e {len(ftp)} com FTP ativo; '
                                 f'demais restritos a SSH.' if (telnet or ftp) else 'SSH em todos os equipamentos analisados.')],
        ['Credenciais legadas', e('As configurações contêm contas locais '
                                  f'(média de {round(sum(s["usuarios"] for s in huawei) / len(huawei), 1) if huawei else 0} '
                                  'por equipamento Huawei). A rotação e a remoção devem ser tratadas em mudança controlada, '
                                  'fora deste documento.')],
        ['SNMP', e(f'SNMP v2c ativo em {len(snmp_v2)} de {len(huawei)} equipamento(s) Huawei.' if huawei else '—')],
        ['Filtros BGP', e(f'Uso de prefix-lists, route-policies e deny final; filtro de bogons explícito em '
                          f'{bogons} de {ebgp_pub} sessão(ões) eBGP na tabela de Internet.')],
        ['Padronização', e('Erros de grafia e classificações incorretas em descrições: '
                           + ', '.join(f'{g["errado"]}→{g["correto"]}' for g in m['grafias'])
                           if m['grafias'] else 'Sem erros de grafia recorrentes detectados.')],
        ['Backups', e(f'Arquivos utilizados {datas}.')],
    ]
    partes = [tabela(['Tema', 'Observação AS-IS'], [[f'<strong>{a}</strong>', b] for a, b in linhas])]
    partes.append(h3('Postura de gerência por equipamento'))
    partes.append(tabela(
        ['Equipamento', 'Telnet', 'FTP', 'SSH', 'SNMP', 'Contas locais'],
        [[e(s['nome']), e(s['telnet']), e(s['ftp']), e(s['ssh']), e(s['snmp']), e(s['usuarios'])] for s in seg]))
    return ''.join(partes)


def s_riscos(m):
    return tabela(['Risco', 'Tratamento'], [[e(r['risco']), e(r['tratamento'])] for r in m['riscos']])


def s_status(m):
    def st(cond, ok='Consolidado', parcial='Parcial', nada='Não identificado no ambiente'):
        if cond is None:
            return nada
        return ok if cond else parcial

    sem = len(m['sem_backup'])
    linhas = [
        ['Backbone OSPF/MPLS/LDP', st(bool(m['backbone']) and not sem if m['backbone'] else None)],
        ['MP-BGP e Route Reflectors', st(True if m['sessoes_rr'] else None)],
        ['BNG e PPPoE', st(True if (m['bngs_centrais'] or m['bngs_remotos']) else None,
                           ok='Consolidado no nível necessário ao projeto')],
        ['CGNAT', st(all(not c.get('sem_backup') for c in m['cgnats']) if m['cgnats'] else None)],
        ['ISP downstreams', 'Inventariados com entrega, prefixos, LP e communities quando aplicável'
         if m['downstreams'] else 'Não identificado no ambiente'],
        ['Parceiros de conteúdo', st(True if m['parceiros'] else None)],
        ['Serviços L3VPN / VRFs', st(True if m['vrfs'] else None, ok='Consolidado por presença e dependências principais')],
        ['Transportes L2VPN', 'Inventariados conforme evidência disponível' if m['l2vpn'] else 'Não identificado no ambiente'],
        ['Cobertura de backup', 'Completa' if not sem else f'Parcial — {sem} equipamento(s) sem backup analisado'],
        ['Dependências ocultas', 'Risco residual aceito'],
    ]
    return callout('STATUS:', 'O levantamento cobre a infraestrutura e os serviços conhecidos presentes nas configurações '
                   'disponibilizadas. Novas descobertas serão incorporadas como revisão ou adendo.', 'status') + \
        tabela(['Domínio', 'Status'], [[e(a), e(b)] for a, b in linhas])


def s_anexo(m):
    linhas = []
    for eq in sorted(m['equipamentos'], key=lambda x: (x['funcao'], x['nome'])):
        b = eq.get('backup')
        if b and b.get('arquivo_disponivel'):
            estado = f'{_data_br(b["confirmado_em"])}'
        elif b:
            estado = 'Registro sem arquivo'
        elif eq['protocolo'] == 'SSH':
            estado = 'Sem backup'
        else:
            estado = 'Não se aplica'
        linhas.append([e(eq['nome']), e(eq.get('hostname')), e(eq['host']), e(eq['funcao']), e(eq['modelo']),
                       e(nome_fabricante(eq.get('vendor'))), e(estado)])
    return p('Relação dos acessos cadastrados e da fonte usada para cada um. Credenciais não fazem parte deste '
             'documento.') + tabela(
        ['Cadastro', 'Hostname', 'Gerência', 'Função', 'Modelo', 'Fabricante', 'Backup analisado'], linhas)


SECOES = [
    ('controle', 'Controle e finalidade', s_controle),
    ('sumario', 'Sumário executivo', s_sumario),
    ('topologia', 'Topologia e inventário de equipamentos', s_topologia),
    ('underlay', 'Underlay, MPLS e controle', s_underlay),
    ('achados', 'Achados estruturais do AS-IS', s_achados),
    ('bng', 'BNG, PPPoE e CGNAT', s_bng),
    ('transito', 'Trânsito IP e ISP downstreams', s_transito),
    ('servicos', 'Serviços especiais, VRFs e parceiros', s_servicos),
    ('l2vpn', 'Transportes e L2VPNs', s_l2vpn),
    ('communities', 'Communities do ambiente atual', s_communities),
    ('seguranca', 'Operação, segurança e dívida técnica', s_seguranca),
    ('riscos', 'Riscos e pendências residuais', s_riscos),
    ('status', 'Status formal do AS-IS', s_status),
    ('anexo', 'Anexo A — Inventário de acessos e fontes', s_anexo),
]
_POR_CHAVE = {k: (t, f) for k, t, f in SECOES}


def gerar_secao(modelo, chave):
    titulo, func = _POR_CHAVE[chave]
    return {'chave': chave, 'titulo': titulo, 'html': func(modelo), 'auto': True}


def gerar_secoes(modelo):
    return [gerar_secao(modelo, k) for k, _, _ in SECOES]


def metadados_padrao(modelo, responsavel=''):
    empresa = modelo['cliente']['nome']
    ini, fim = modelo['periodo']
    if ini:
        data_base = (f'Backups coletados em {_data_br(ini)}' if ini.date() == fim.date()
                     else f'Backups coletados entre {_data_br(ini)} e {_data_br(fim)}')
    else:
        data_base = 'Sem backups disponíveis'
    return {
        'empresa': empresa,
        'titulo': 'AS-IS DA INFRAESTRUTURA DE REDE',
        'subtitulo': 'Estado operacional reconstruído a partir das configurações reais',
        'documento': f'AS-IS da Infraestrutura de Rede {empresa}',
        'versao': '1.0',
        'status': 'Rascunho — gerado automaticamente, pendente de revisão técnica',
        'asn': modelo['asn'],
        'data_base': data_base,
        'responsavel': responsavel,
        'classificacao': 'Uso interno',
        'principio': 'Este AS-IS registra o ambiente atual. O HLD é referência para o TO-BE e não representa '
                     'uma configuração já implementada.',
    }


CAMPOS_METADADOS = [
    ('documento', 'Documento'), ('versao', 'Versão'), ('status', 'Status'), ('asn', 'ASN'),
    ('data_base', 'Data-base'), ('responsavel', 'Responsável'), ('classificacao', 'Classificação'),
]


def resumo_coleta(modelo):
    """O que fica gravado no documento sobre a coleta (rastreabilidade)."""
    fontes = []
    for eq in modelo['equipamentos']:
        b = eq.get('backup') or {}
        fontes.append({
            'acesso_id': eq['acesso_id'], 'nome': eq['nome'], 'hostname': eq.get('hostname', ''),
            'vendor': eq.get('vendor', ''), 'backup_id': b.get('id'),
            'backup_em': b.get('confirmado_em'), 'hash': b.get('hash', ''),
            'arquivo_disponivel': bool(b.get('arquivo_disponivel')),
        })
    return {
        'gerado_em': modelo['gerado_em'], 'asn': modelo['asn'],
        'total_acessos': modelo['total_acessos'], 'total_com_backup': modelo['total_com_backup'],
        'achados': len(modelo['achados']), 'fontes': fontes,
        'mapas_topologia': len(modelo['topologia']['mapas']),
    }
