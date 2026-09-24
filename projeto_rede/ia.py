"""
Redação assistida por IA do AS-IS.

Usa a mesma integração do atendimento (`atendimento.ai.call_ai`, configurada
em Configurações → Integração IA — hoje o ChatGPT). A IA só escreve a PROSA
de três seções (sumário executivo, análise dos achados e dos riscos), sempre
a partir dos fatos já extraídos pela análise determinística. Tabelas,
inventário e números continuam vindo da lógica: o documento tem que ser
rastreável até a linha de configuração, e isso a IA não garante.

Se a IA não estiver configurada, estiver sem crédito ou devolver algo fora do
formato, `redigir` retorna None e a composição usa o texto lógico de sempre —
gerar o documento nunca depende da IA.
"""
import json
import logging
import re
from collections import Counter

from django.utils.html import escape

logger = logging.getLogger(__name__)

# Seções cuja prosa a IA redige. Recalcular uma delas pede texto novo à IA;
# as demais não gastam chamada.
SECOES = ('sumario', 'achados', 'riscos')

# Cabe no timeout de 120 s do gunicorn mesmo tentando os dois provedores.
_TIMEOUT = 40
_MAX_TOKENS = 1800
_MAX_PARAGRAFOS = 4
_MAX_CARACTERES = 1500

_SISTEMA = (
    'Você é um engenheiro de redes sênior de provedor de internet (ISP) redigindo um documento técnico '
    'AS-IS em português do Brasil. Receberá FATOS extraídos automaticamente dos backups de configuração. '
    'Regras: use apenas os fatos fornecidos, sem inventar equipamentos, números, fabricantes ou serviços; '
    'não repita tabelas nem liste itens um a um — escreva análise em prosa corrida, objetiva e formal; '
    'não use markdown, títulos, listas ou HTML; não cite credenciais. '
    'Responda SOMENTE com um objeto JSON com as chaves "sumario", "achados" e "riscos", cada uma com uma '
    'lista de 1 a 3 parágrafos (strings). "sumario": visão executiva do estado da rede. "achados": leitura '
    'crítica dos achados estruturais, priorizando os de maior severidade e o impacto na migração TO-BE. '
    '"riscos": como os riscos residuais se relacionam e o que tratar antes da migração.'
)


def _fatos(m):
    """Resumo compacto do modelo — só o que a prosa precisa, sem IPs de
    gerência nem nada que não esteja no documento."""
    vendors = Counter(eq.get('vendor') or 'desconhecido' for eq in m['equipamentos'])
    return {
        'empresa': m['cliente']['nome'],
        'asn': m.get('asn'),
        'equipamentos_cadastrados': m['total_acessos'],
        'equipamentos_com_backup': m['total_com_backup'],
        'sem_backup': [x if isinstance(x, str) else x.get('nome') for x in m['sem_backup']][:15],
        'fabricantes': dict(vendors),
        'backbone': {
            'equipamentos': len(m['backbone']),
            'ldp': any(b['ldp'] for b in m['backbone']),
            'rsvp_te': any(b['rsvp'] for b in m['backbone']),
            'mtus': m['mtus'][:10],
        },
        'familias_mpbgp': [f for f, v in m['familias'].items() if v],
        'route_reflectors': [{'nome': r['nome'], 'pop': r['pop']} for r in m['rrs']],
        'pes': len(m['pes']),
        'bngs_centrais': [b['nome'] for b in m['bngs_centrais']],
        'bngs_remotos': [b['nome'] for b in m['bngs_remotos']],
        'servicos_pppoe_l2vpn': len(m['pppoe']),
        'cgnats': [c['nome'] for c in m['cgnats']],
        'upstreams': sorted({f'{s["descricao"] or s["cliente"]} (AS{s["asn"]}, {s["pop"]})'
                             for s in m['upstreams']})[:20],
        'isp_downstreams': len(m['downstreams']),
        'parceiros_conteudo_ix': len(m['parceiros']),
        'vrfs': [v['nome'] for v in m['vrfs']][:20],
        'servicos_l2vpn': len(m['l2vpn']),
        'sessoes_ebgp_sem_classificacao': len(m['nao_classificados']),
        'achados': [{'id': a['id'], 'severidade': a['severidade'], 'titulo': a['titulo'],
                     'evidencia': a['evidencia'][:300], 'impacto': a['impacto'][:300]}
                    for a in m['achados']][:30],
        'riscos': m['riscos'],
    }


def _extrair_json(texto):
    """A IA às vezes embrulha o JSON em ```json ... ``` ou põe uma frase antes."""
    ini, fim = texto.find('{'), texto.rfind('}')
    if ini < 0 or fim <= ini:
        return None
    try:
        return json.loads(texto[ini:fim + 1])
    except ValueError:
        return None


def _paragrafos(valor):
    if isinstance(valor, str):
        valor = re.split(r'\n\s*\n', valor)
    if not isinstance(valor, list):
        return []
    saida = []
    for par in valor[:_MAX_PARAGRAFOS]:
        if not isinstance(par, str):
            continue
        par = re.sub(r'\s+', ' ', par.replace('**', '')).strip()
        if par:
            saida.append(par[:_MAX_CARACTERES])
    return saida


def redigir(modelo):
    """{'sumario': [...], 'achados': [...], 'riscos': [...]} ou None."""
    from atendimento.ai import call_ai

    try:
        prompt = 'FATOS DO AMBIENTE (JSON):\n' + json.dumps(_fatos(modelo), ensure_ascii=False, default=str)
    except Exception:
        logger.exception('AS-IS/IA: falha ao montar os fatos para a IA')
        return None
    resposta = call_ai(_SISTEMA, prompt, max_tokens=_MAX_TOKENS, timeout=_TIMEOUT, max_retries=0)
    if not resposta:
        return None
    dados = _extrair_json(resposta)
    if not isinstance(dados, dict):
        logger.warning('AS-IS/IA: resposta fora do formato JSON esperado; usando texto lógico')
        return None
    textos = {k: _paragrafos(dados.get(k)) for k in SECOES}
    return textos if any(textos.values()) else None


def status(textos):
    """O que a tela e o `coleta` do documento registram sobre a IA."""
    from atendimento.ai import ultimo_erro_ia
    from atendimento.models import SystemSetting

    provedor = (SystemSetting.get('ai_provider', 'claude') or 'claude').strip()
    nome = {'openai': 'ChatGPT', 'claude': 'Claude'}.get(provedor, provedor)
    if textos:
        return {'usada': True, 'provedor': nome, 'motivo': ''}
    return {'usada': False, 'provedor': nome, 'motivo': ultimo_erro_ia() or 'IA não configurada ou resposta inválida'}


def html(paragrafos):
    """Parágrafos da IA como HTML seguro (texto puro, escapado)."""
    return ''.join(f'<p>{escape(par)}</p>' for par in paragrafos)
