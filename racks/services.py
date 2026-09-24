"""
Regras do rack: onde um equipamento cabe, que porta está livre e como um
enlace da topologia vira um cabo entre dois equipamentos montados.

As views só traduzem HTTP <-> estas funções; toda recusa sai como
`ErroRack` com uma mensagem pronta para mostrar na tela.
"""
import json
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from clientes.models import Acesso, TopologiaDiagrama
from clientes.topologia_tipos import tipo_topologia_do_acesso

from . import catalogo
from .models import ConexaoFisica, Rack, RackEquipamento


class ErroRack(ValueError):
    """Recusa de uma operação do rack — a mensagem vai direto para a tela."""


# ── Leitura de campos ────────────────────────────────────────────────────────

def _texto(dados, campo, maximo, obrigatorio=False, rotulo=None):
    valor = str(dados.get(campo) or '').strip()
    if obrigatorio and not valor:
        raise ErroRack(f'Informe {rotulo or campo}.')
    if len(valor) > maximo:
        raise ErroRack(f'{(rotulo or campo).capitalize()} passa de {maximo} caracteres.')
    return valor


def _inteiro(valor, rotulo, minimo, maximo):
    try:
        n = int(valor)
    except (TypeError, ValueError):
        raise ErroRack(f'{rotulo} precisa ser um número.')
    if not minimo <= n <= maximo:
        raise ErroRack(f'{rotulo} precisa ficar entre {minimo} e {maximo}.')
    return n


def _bool(valor):
    if isinstance(valor, str):
        return valor.strip().lower() in ('1', 'true', 'sim', 'on')
    return bool(valor)


def _porta_chave(porta):
    """Duas grafias da mesma porta ("Ge0/0/1 " e "ge0/0/1") são a mesma porta."""
    return (porta or '').strip().casefold()


# ── Espaço no rack ───────────────────────────────────────────────────────────

def _faces_colidem(face_a, total_a, face_b, total_b):
    """Equipamento de profundidade total ocupa as duas faces do U; um passivo
    raso (patch panel, PDU) ocupa só a sua — e deixa a outra face livre."""
    return total_a or total_b or face_a == face_b


def conflitos(rack, u_inicial, altura_u, face, profundidade_total, ignorar_id=None):
    """Equipamentos do rack que ocupam algum dos U pedidos na mesma face."""
    u_final = u_inicial + altura_u - 1
    resultado = []
    for eq in rack.equipamentos.all():
        if eq.id == ignorar_id:
            continue
        if eq.u_inicial > u_final or eq.u_final < u_inicial:
            continue
        if _faces_colidem(face, profundidade_total, eq.face, eq.profundidade_total):
            resultado.append(eq)
    return resultado


def validar_posicao(rack, u_inicial, altura_u, face, profundidade_total, ignorar_id=None):
    u_final = u_inicial + altura_u - 1
    if u_final > rack.altura_u:
        raise ErroRack(f'Não cabe: U{u_inicial} a U{u_final} passa do topo do rack ({rack.altura_u}U).')
    ocupados = conflitos(rack, u_inicial, altura_u, face, profundidade_total, ignorar_id)
    if ocupados:
        nomes = ', '.join(f'{eq.nome} (U{eq.u_inicial}' + (f'–U{eq.u_final})' if eq.altura_u > 1 else ')')
                          for eq in ocupados)
        raise ErroRack(f'U{u_inicial}' + (f'–U{u_final}' if altura_u > 1 else '') + f' já ocupado por {nomes}.')


def primeiro_u_livre(rack, altura_u, face='frente', profundidade_total=True):
    """U mais alto onde o equipamento cabe (racks são montados de cima para
    baixo na prática), ou None se não houver espaço."""
    for u in range(rack.altura_u - altura_u + 1, 0, -1):
        if not conflitos(rack, u, altura_u, face, profundidade_total):
            return u
    return None


# ── Racks ────────────────────────────────────────────────────────────────────

def criar_rack(cliente, dados, usuario=None):
    return Rack.objects.create(
        cliente=cliente,
        nome=_texto(dados, 'nome', 120, obrigatorio=True, rotulo='o nome do rack'),
        local=_texto(dados, 'local', 160),
        altura_u=_inteiro(dados.get('altura_u', catalogo.ALTURA_RACK_PADRAO), 'A altura do rack',
                          1, catalogo.ALTURA_RACK_MAX),
        observacoes=_texto(dados, 'observacoes', 4000),
        criado_por=usuario,
    )


def atualizar_rack(rack, dados):
    if 'nome' in dados:
        rack.nome = _texto(dados, 'nome', 120, obrigatorio=True, rotulo='o nome do rack')
    if 'local' in dados:
        rack.local = _texto(dados, 'local', 160)
    if 'observacoes' in dados:
        rack.observacoes = _texto(dados, 'observacoes', 4000)
    if 'altura_u' in dados:
        altura = _inteiro(dados['altura_u'], 'A altura do rack', 1, catalogo.ALTURA_RACK_MAX)
        acima = [eq for eq in rack.equipamentos.all() if eq.u_final > altura]
        if acima:
            nomes = ', '.join(f'{eq.nome} (até U{eq.u_final})' for eq in acima)
            raise ErroRack(f'Não dá para reduzir para {altura}U: {nomes} ficaria fora do rack.')
        rack.altura_u = altura
    rack.save()
    return rack


# ── Equipamentos ─────────────────────────────────────────────────────────────

def _vinculo(cliente, dados, ignorar_id=None):
    """Host do CRM e/ou node da topologia que este equipamento representa.
    Cada um só pode estar montado uma vez: é o mesmo equipamento físico."""
    acesso = None
    acesso_id = dados.get('acesso_id')
    if acesso_id not in (None, '', 0, '0'):
        acesso = Acesso.objects.filter(id=acesso_id, cliente=cliente).first()
        if acesso is None:
            raise ErroRack('Host do CRM não encontrado neste cliente.')
    node_id = _texto(dados, 'topologia_node_id', 80)

    outros = RackEquipamento.objects.filter(rack__cliente=cliente).select_related('rack')
    if ignorar_id:
        outros = outros.exclude(id=ignorar_id)
    if acesso:
        ja = outros.filter(acesso=acesso).first()
        if ja:
            raise ErroRack(f'{acesso.tipo} já está montado em {ja.rack.nome} U{ja.u_inicial}.')
    if node_id:
        ja = outros.filter(topologia_node_id=node_id).first()
        if ja:
            raise ErroRack(f'Este dispositivo da topologia já está montado em {ja.rack.nome} U{ja.u_inicial}.')
    return acesso, node_id


def montar_equipamento(rack, dados):
    tipo = dados.get('tipo') or 'outro'
    if tipo not in catalogo.TIPOS:
        raise ErroRack('Tipo de equipamento desconhecido.')
    padrao = catalogo.TIPOS[tipo]
    altura = _inteiro(dados.get('altura_u', padrao['altura']), 'A altura', 1, catalogo.ALTURA_EQUIPAMENTO_MAX)
    face = dados.get('face') or padrao['face']
    if face not in dict(catalogo.FACES):
        raise ErroRack('Face inválida.')
    total = _bool(dados['profundidade_total']) if 'profundidade_total' in dados else padrao['profundidade_total']

    with transaction.atomic():
        # Trava o rack: dois arrastes simultâneos para o mesmo U não passam
        # os dois pela checagem de conflito.
        rack = Rack.objects.select_for_update().get(id=rack.id)
        if dados.get('u_inicial') in (None, ''):
            u = primeiro_u_livre(rack, altura, face, total)
            if u is None:
                raise ErroRack(f'Não há {altura}U livres em {rack.nome}.')
        else:
            u = _inteiro(dados['u_inicial'], 'O U', 1, rack.altura_u)
        validar_posicao(rack, u, altura, face, total)
        acesso, node_id = _vinculo(rack.cliente, dados)
        try:
            return RackEquipamento.objects.create(
                rack=rack, tipo=tipo,
                nome=_texto(dados, 'nome', 160) or (acesso.tipo if acesso else padrao['label']),
                u_inicial=u, altura_u=altura, face=face, profundidade_total=total,
                acesso=acesso, topologia_node_id=node_id,
                fabricante=_texto(dados, 'fabricante', 80),
                modelo=_texto(dados, 'modelo', 120) or ((acesso.modelo.nome or '') if acesso and acesso.modelo else ''),
                num_portas=_inteiro(dados.get('num_portas', padrao['portas']), 'O número de portas', 0, 512),
                observacoes=_texto(dados, 'observacoes', 4000),
            )
        except IntegrityError:
            raise ErroRack('Este host do CRM já está montado em outro rack.')


def atualizar_equipamento(eq, dados):
    """Edita e/ou move (outro U, outra face, outro rack do mesmo cliente)."""
    with transaction.atomic():
        destino = eq.rack
        if dados.get('rack_id') not in (None, '') and int(dados['rack_id']) != eq.rack_id:
            destino = Rack.objects.filter(id=dados['rack_id'], cliente_id=eq.rack.cliente_id).first()
            if destino is None:
                raise ErroRack('Rack de destino não encontrado neste cliente.')
        destino = Rack.objects.select_for_update().get(id=destino.id)

        if 'tipo' in dados:
            if dados['tipo'] not in catalogo.TIPOS:
                raise ErroRack('Tipo de equipamento desconhecido.')
            eq.tipo = dados['tipo']
        altura = _inteiro(dados.get('altura_u', eq.altura_u), 'A altura', 1, catalogo.ALTURA_EQUIPAMENTO_MAX)
        u = _inteiro(dados.get('u_inicial', eq.u_inicial), 'O U', 1, destino.altura_u)
        face = dados.get('face', eq.face)
        if face not in dict(catalogo.FACES):
            raise ErroRack('Face inválida.')
        total = _bool(dados['profundidade_total']) if 'profundidade_total' in dados else eq.profundidade_total
        validar_posicao(destino, u, altura, face, total, ignorar_id=eq.id)

        if 'acesso_id' in dados or 'topologia_node_id' in dados:
            vinc = {'acesso_id': dados.get('acesso_id', eq.acesso_id),
                    'topologia_node_id': dados.get('topologia_node_id', eq.topologia_node_id)}
            eq.acesso, eq.topologia_node_id = _vinculo(destino.cliente, vinc, ignorar_id=eq.id)

        eq.rack, eq.u_inicial, eq.altura_u, eq.face, eq.profundidade_total = destino, u, altura, face, total
        if 'nome' in dados:
            eq.nome = _texto(dados, 'nome', 160, obrigatorio=True, rotulo='o nome')
        for campo, maximo in (('fabricante', 80), ('modelo', 120), ('observacoes', 4000)):
            if campo in dados:
                setattr(eq, campo, _texto(dados, campo, maximo))
        if 'num_portas' in dados:
            eq.num_portas = _inteiro(dados['num_portas'], 'O número de portas', 0, 512)
        try:
            eq.save()
        except IntegrityError:
            raise ErroRack('Este host do CRM já está montado em outro rack.')
    return eq


# ── Conexões físicas ─────────────────────────────────────────────────────────

def _porta_em_uso(equipamento, porta, ignorar_id=None):
    chave = _porta_chave(porta)
    if not chave:
        return None  # porta ainda não identificada: não bloqueia
    qs = ConexaoFisica.objects.filter(cliente_id=equipamento.rack.cliente_id).select_related('ponta_a', 'ponta_b')
    if ignorar_id:
        qs = qs.exclude(id=ignorar_id)
    for c in qs.filter(ponta_a=equipamento):
        if _porta_chave(c.porta_a) == chave:
            return c
    for c in qs.filter(ponta_b=equipamento):
        if _porta_chave(c.porta_b) == chave:
            return c
    return None


def _campos_conexao(cliente, dados, atual=None):
    def ponta(lado):
        chave = f'ponta_{lado}_id'
        if chave not in dados and atual is not None:
            return getattr(atual, f'ponta_{lado}')
        eq = RackEquipamento.objects.select_related('rack').filter(
            id=dados.get(chave) or 0, rack__cliente=cliente).first()
        if eq is None:
            raise ErroRack(f'Escolha o equipamento da ponta {lado.upper()}.')
        return eq

    a, b = ponta('a'), ponta('b')
    porta_a = _texto(dados, 'porta_a', 80) if 'porta_a' in dados or atual is None else atual.porta_a
    porta_b = _texto(dados, 'porta_b', 80) if 'porta_b' in dados or atual is None else atual.porta_b
    if a.id == b.id and _porta_chave(porta_a) == _porta_chave(porta_b):
        raise ErroRack('As duas pontas são a mesma porta do mesmo equipamento.')
    ignorar = atual.id if atual else None
    for eq, porta in ((a, porta_a), (b, porta_b)):
        ocupada = _porta_em_uso(eq, porta, ignorar)
        if ocupada:
            outro = ocupada.ponta_b if ocupada.ponta_a_id == eq.id and _porta_chave(ocupada.porta_a) == _porta_chave(porta) else ocupada.ponta_a
            raise ErroRack(f'A porta {porta} de {eq.nome} já tem cabo (vai para {outro.nome}).')

    campos = {'ponta_a': a, 'porta_a': porta_a, 'ponta_b': b, 'porta_b': porta_b}
    if 'meio' in dados or atual is None:
        meio = dados.get('meio') or 'utp'
        if meio not in catalogo.MEIOS:
            raise ErroRack('Tipo de cabo inválido.')
        campos['meio'] = meio
    if 'conector' in dados:
        campos['conector'] = _texto(dados, 'conector', 12)
    if 'cor' in dados:
        cor = _texto(dados, 'cor', 9)
        if cor and not (cor.startswith('#') and len(cor) in (4, 7)):
            raise ErroRack('Cor do cabo precisa ser hexadecimal (#rrggbb).')
        campos['cor'] = cor
    if 'comprimento_m' in dados:
        bruto = str(dados.get('comprimento_m') or '').strip().replace(',', '.')
        try:
            campos['comprimento_m'] = Decimal(bruto) if bruto else None
        except InvalidOperation:
            raise ErroRack('Comprimento precisa ser um número (metros).')
        if campos['comprimento_m'] is not None and not 0 <= campos['comprimento_m'] < 100000:
            raise ErroRack('Comprimento fora da faixa.')
    for campo, maximo in (('identificacao', 120), ('observacoes', 4000)):
        if campo in dados:
            campos[campo] = _texto(dados, campo, maximo)
    return campos


def criar_conexao(cliente, dados, usuario=None, topologia_link_id='', diagrama=None):
    with transaction.atomic():
        campos = _campos_conexao(cliente, dados)
        try:
            return ConexaoFisica.objects.create(cliente=cliente, criado_por=usuario,
                                                topologia_link_id=topologia_link_id, diagrama=diagrama, **campos)
        except IntegrityError:
            raise ErroRack('Este enlace da topologia já tem conexão física.')


def atualizar_conexao(conexao, dados):
    with transaction.atomic():
        for campo, valor in _campos_conexao(conexao.cliente, dados, atual=conexao).items():
            setattr(conexao, campo, valor)
        conexao.save()
    return conexao


# ── Topologia -> rack ────────────────────────────────────────────────────────

def _diagramas(cliente):
    """(diagrama, dados) de todos os mapas do cliente — mapa raiz primeiro,
    porque um host agrupado aparece também como cópia de borda no sub-mapa
    e o enlace original é o do mapa de cima."""
    resultado = []
    for d in TopologiaDiagrama.objects.filter(cliente=cliente).order_by('pai_id', 'id'):
        try:
            dados = json.loads(d.dados_json or '{}')
        except ValueError:
            continue
        if isinstance(dados, dict):
            resultado.append((d, dados))
    return sorted(resultado, key=lambda par: (par[0].pai_id is not None, par[0].id))


def _acesso_do_node(node):
    acesso_id = node.get('acesso_id')
    if not acesso_id and str(node.get('id', '')).startswith('crm_'):
        acesso_id = str(node['id'])[4:]
    try:
        return int(acesso_id) if acesso_id else None
    except (TypeError, ValueError):
        return None


def _indice_montados(cliente):
    por_acesso, por_node = {}, {}
    for eq in RackEquipamento.objects.filter(rack__cliente=cliente).select_related('rack'):
        if eq.acesso_id:
            por_acesso[eq.acesso_id] = eq
        if eq.topologia_node_id:
            por_node[eq.topologia_node_id] = eq
    return por_acesso, por_node


def _equipamento_do_node(node, por_acesso, por_node):
    acesso_id = _acesso_do_node(node)
    return (por_acesso.get(acesso_id) if acesso_id else None) or por_node.get(str(node.get('id', '')))


def _montavel(node):
    return not node.get('grupo') and catalogo.tipo_do_node(node.get('type')) is not None


def dispositivos(cliente):
    """O que dá para montar: nodes físicos da topologia (todos os mapas) e
    hosts do CRM ainda não desenhados, cada um uma vez só, com a posição no
    rack de quem já está montado."""
    por_acesso, por_node = _indice_montados(cliente)
    vistos, lista = set(), []

    def entrada(chave, node_id, acesso_id, label, ip, tipo_topo, origem):
        if chave in vistos:
            return
        vistos.add(chave)
        eq = (por_acesso.get(acesso_id) if acesso_id else None) or (por_node.get(node_id) if node_id else None)
        lista.append({
            'chave': chave, 'node_id': node_id, 'acesso_id': acesso_id, 'label': label, 'ip': ip,
            'tipo_topologia': tipo_topo, 'tipo': catalogo.tipo_do_node(tipo_topo) or 'outro', 'origem': origem,
            'montado': ({'equipamento_id': eq.id, 'rack_id': eq.rack_id, 'rack': eq.rack.nome, 'u': eq.u_inicial}
                        if eq else None),
        })

    for _d, dados in _diagramas(cliente):
        for node in dados.get('nodes') or []:
            if not isinstance(node, dict) or not _montavel(node):
                continue
            acesso_id = _acesso_do_node(node)
            chave = f'a{acesso_id}' if acesso_id else f'n{node.get("id")}'
            entrada(chave, str(node.get('id', '')), acesso_id, node.get('label') or '', node.get('ip') or '',
                    node.get('type'), 'topologia')

    for acesso in Acesso.objects.filter(cliente=cliente).select_related('funcao'):
        tipo_topo = tipo_topologia_do_acesso(acesso)
        if catalogo.tipo_do_node(tipo_topo) is None:
            continue
        entrada(f'a{acesso.id}', f'crm_{acesso.id}', acesso.id, acesso.tipo, acesso.host, tipo_topo, 'crm')
    return lista


def links_topologia(cliente, link_id=None):
    """Enlaces da topologia entre equipamentos físicos e a situação de cada
    um no rack: `criada` (já tem cabo), `pronta` (as duas pontas montadas)
    ou `pendente` (falta montar alguma ponta — `faltando` diz qual)."""
    por_acesso, por_node = _indice_montados(cliente)
    conexoes = {c.topologia_link_id: c for c in
                ConexaoFisica.objects.filter(cliente=cliente).exclude(topologia_link_id='')}
    vistos, lista = set(), []
    for diagrama, dados in _diagramas(cliente):
        nodes = {str(n.get('id')): n for n in dados.get('nodes') or [] if isinstance(n, dict)}
        for link in dados.get('links') or []:
            if not isinstance(link, dict):
                continue
            lid = str(link.get('id') or '')
            if not lid or lid in vistos or (link_id and lid != link_id):
                continue
            src, tgt = nodes.get(str(link.get('src'))), nodes.get(str(link.get('tgt')))
            if not src or not tgt or not _montavel(src) or not _montavel(tgt):
                continue  # enlace lógico (Internet, grupo, VM...) não vira cabo
            vistos.add(lid)
            ponta = {}
            for lado, node, porta in (('a', src, link.get('iface_a')), ('b', tgt, link.get('iface_b'))):
                eq = _equipamento_do_node(node, por_acesso, por_node)
                ponta[lado] = {'node_id': str(node.get('id')), 'label': node.get('label') or '',
                               'acesso_id': _acesso_do_node(node), 'tipo_topologia': node.get('type'),
                               'porta': str(porta or '').strip(),
                               'equipamento_id': eq.id if eq else None,
                               'rack': eq.rack.nome if eq else None, 'u': eq.u_inicial if eq else None}
            conexao = conexoes.get(lid)
            faltando = [ponta[l]['label'] or ponta[l]['node_id'] for l in 'ab' if not ponta[l]['equipamento_id']]
            meio, conector = catalogo.meio_da_iface(link.get('iface'))
            lista.append({
                'link_id': lid, 'diagrama_id': diagrama.id, 'diagrama': diagrama.nome,
                'iface': link.get('iface') or '', 'label': link.get('label') or '', 'vlan': link.get('vlan') or '',
                'a': ponta['a'], 'b': ponta['b'], 'meio_sugerido': meio, 'conector_sugerido': conector,
                'status': 'criada' if conexao else ('pendente' if faltando else 'pronta'),
                'conexao_id': conexao.id if conexao else None, 'faltando': faltando,
            })
    return lista


def criar_conexao_do_link(cliente, link_id, dados=None, usuario=None):
    """Cabo a partir de um enlace da topologia: pontas, portas (Interface
    Lado A/B), tipo de cabo (pela velocidade) e etiqueta (rótulo do enlace)
    vêm do enlace; `dados` sobrescreve o que a pessoa ajustou na tela."""
    dados = dict(dados or {})
    achados = links_topologia(cliente, link_id=str(link_id or ''))
    if not achados:
        raise ErroRack('Enlace não encontrado na topologia salva (ou é um enlace lógico, sem cabo). '
                       'Salve a topologia e tente de novo.')
    link = achados[0]
    if link['status'] == 'criada':
        raise ErroRack('Este enlace já tem conexão física.')
    if link['status'] == 'pendente':
        raise ErroRack('Monte no rack antes: ' + ', '.join(link['faltando']) + '.')
    base = {
        'ponta_a_id': link['a']['equipamento_id'], 'porta_a': link['a']['porta'],
        'ponta_b_id': link['b']['equipamento_id'], 'porta_b': link['b']['porta'],
        'meio': link['meio_sugerido'], 'conector': link['conector_sugerido'],
        'identificacao': link['label'][:120],
    }
    base.update({k: v for k, v in dados.items() if k in (
        'porta_a', 'porta_b', 'meio', 'conector', 'cor', 'comprimento_m', 'identificacao', 'observacoes')})
    diagrama = TopologiaDiagrama.objects.filter(id=link['diagrama_id']).first()
    return criar_conexao(cliente, base, usuario, topologia_link_id=link['link_id'], diagrama=diagrama)


# ── Serialização ─────────────────────────────────────────────────────────────

def equipamento_dict(eq):
    return {
        'id': eq.id, 'rack_id': eq.rack_id, 'tipo': eq.tipo, 'nome': eq.nome,
        'u_inicial': eq.u_inicial, 'altura_u': eq.altura_u, 'face': eq.face,
        'profundidade_total': eq.profundidade_total,
        'acesso_id': eq.acesso_id, 'acesso_host': eq.acesso.host if eq.acesso_id and eq.acesso else '',
        'topologia_node_id': eq.topologia_node_id,
        'fabricante': eq.fabricante, 'modelo': eq.modelo, 'num_portas': eq.num_portas,
        'observacoes': eq.observacoes,
    }


def conexao_dict(c):
    return {
        'id': c.id, 'ponta_a_id': c.ponta_a_id, 'porta_a': c.porta_a,
        'ponta_b_id': c.ponta_b_id, 'porta_b': c.porta_b,
        'meio': c.meio, 'conector': c.conector, 'cor': c.cor,
        'comprimento_m': str(c.comprimento_m) if c.comprimento_m is not None else '',
        'identificacao': c.identificacao, 'topologia_link_id': c.topologia_link_id,
        'diagrama_id': c.diagrama_id, 'observacoes': c.observacoes,
    }


def estado(cliente):
    """Tudo que a tela do rack precisa num só JSON; toda alteração devolve
    este estado inteiro de novo, então a tela nunca fica fora de sincronia."""
    racks = list(Rack.objects.filter(cliente=cliente).prefetch_related('equipamentos__acesso'))
    return {
        'racks': [{
            'id': r.id, 'nome': r.nome, 'local': r.local, 'altura_u': r.altura_u, 'observacoes': r.observacoes,
            'equipamentos': [equipamento_dict(eq) for eq in r.equipamentos.all()],
        } for r in racks],
        'conexoes': [conexao_dict(c) for c in ConexaoFisica.objects.filter(cliente=cliente)],
        'dispositivos': dispositivos(cliente),
        'links': links_topologia(cliente),
    }
