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
    (ex: Huawei "parar de anunciar" quando o prefixo não vem de um
    `network` statement) — melhor recusar explicitamente do que arriscar um
    edit incorreto num equipamento de borda em produção."""


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
        return [f'bgp {asn}', linha]

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
    asn = sessao.get('as_local')
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
            return cabecalho + ['undo apply as-path']
        asns = ' '.join([str(asn)] * novo_valor)
        return cabecalho + [f'apply as-path {asns} additive']

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
        rede = _rede_correspondente(dados, prefixo)
        if not rede:
            raise AcaoBgpNaoSuportada(
                f'{prefixo} não vem de um `network` statement explícito — parar de anunciar só é '
                'suportado nesse caso para Huawei (edição manual da route-policy necessária aqui).'
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
        return [f'bgp {asn}', f'{familia} unicast', f'undo network {ip} {mask_arg}']

    if vendor in ('cisco', 'datacom'):
        termo, entrada = _termo_e_entrada_responsaveis(dados, policy_nome, prefixo)
        if not termo or not entrada:
            raise AcaoBgpNaoSuportada(
                f'A regra que permite {prefixo} não usa uma prefix-list nomeada com uma entrada '
                'específica — não dá pra inserir um `deny` acima com segurança.'
            )
        nomes_pl = termo.get('prefix_lists') or []
        pl_nome = nomes_pl[0] if nomes_pl else None
        seq_existente = entrada.get('seq')
        if not pl_nome or seq_existente is None:
            raise AcaoBgpNaoSuportada('Não encontrei o seq da entrada de prefix-list correspondente.')
        if seq_existente <= 1:
            raise AcaoBgpNaoSuportada(
                f'A entrada seq {seq_existente} da prefix-list "{pl_nome}" não tem seq livre abaixo '
                'dela — renumere a prefix-list manualmente antes de tentar esta ação.'
            )
        novo_seq = seq_existente - 1
        cmd = f'ip prefix-list {pl_nome} seq {novo_seq} deny {prefixo}'
        if ':' in prefixo:
            cmd = f'ipv6 prefix-list {pl_nome} seq {novo_seq} deny {prefixo}'
        return [cmd]

    raise AcaoBgpNaoSuportada(f'Fabricante "{vendor}" não suportado para parar de anunciar.')


# ═══════════════════════════════════════════════════════════════════════
# Execução real (conecta e envia)
# ═══════════════════════════════════════════════════════════════════════

# Fabricantes cuja conexão exige `commit()` explícito depois do
# `send_config_set` — sem isso o driver Juniper do Netmiko descarta
# (discard) qualquer mudança não commitada ao sair do modo config.
_PRECISA_COMMIT = {'juniper'}
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
            output = conn.send_config_set(comandos, exit_config_mode=False)
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
