"""Cliente fino para a IA configurada em Configurações → Integração IA
(SystemSetting: ai_provider/ai_api_key/ai_model/ai_openai_api_key/ai_openai_model).

Só chamada síncrona de turno único (sem tools, sem streaming) — usado pelo
agente "Tomichinho" do atendimento (resumo de chamados parados, resposta a
"tomichinho", extração de tarefa em "abrir tarefa" e a resolução do chamado
no fechamento). Se a IA não estiver configurada ou a chamada falhar, retorna
None: quem chama decide o fallback (as automações daqui nunca podem travar
por causa da IA).

Falhar em silêncio, porém, é o que já custou caro uma vez: com a conta da
OpenAI sem crédito (`429 insufficient_quota`), todo fechamento de chamado
passou meses gravando o próprio comando ("pode finalizar o chamado") como
resolução, sem nada no sistema indicando que a IA estava fora. Por isso todo
erro fica registrado em `ai_last_error`/`ai_last_error_at` (lidos por
`ultimo_erro_ia()`, exibidos em Configurações → Integração IA e citados na
mensagem de encerramento quando a resolução sai do fallback).
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

AI_ERRO_KEY = 'ai_last_error'
AI_ERRO_AT_KEY = 'ai_last_error_at'

_NOME_PROVEDOR = {'claude': 'Claude', 'openai': 'ChatGPT'}


def ultimo_erro_ia():
    """Último motivo de falha da IA, já em texto legível ('' se a última
    chamada deu certo). Não diz *quando* — use `ultimo_erro_ia_em()`."""
    from .models import SystemSetting
    return SystemSetting.get(AI_ERRO_KEY, '')


def ultimo_erro_ia_em():
    from .models import SystemSetting
    return SystemSetting.get(AI_ERRO_AT_KEY, '')


def _registrar_erro(msg):
    from .models import SystemSetting
    try:
        SystemSetting.set(AI_ERRO_KEY, (msg or '')[:500])
        SystemSetting.set(
            AI_ERRO_AT_KEY,
            timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M'),
        )
    except Exception:
        pass


def _limpar_erro():
    """Chamada deu certo: some com o aviso — mas só se havia um, pra não
    gravar no banco a cada resposta da IA."""
    from .models import SystemSetting
    try:
        if SystemSetting.get(AI_ERRO_KEY, ''):
            SystemSetting.set(AI_ERRO_KEY, '')
            SystemSetting.set(AI_ERRO_AT_KEY, '')
    except Exception:
        pass


def _motivo_legivel(e):
    """Traduz a exceção do SDK no que a pessoa que administra o sistema
    precisa fazer. O texto vai parar na tela de configurações e na mensagem
    de encerramento do chamado — "Error code: 429 - {'error': {...}}" não
    ajuda ninguém."""
    txt = str(e)
    baixo = txt.lower()
    if 'insufficient_quota' in baixo or 'credit_balance' in baixo or 'no credits remaining' in baixo:
        return 'conta sem crédito/quota — recarregue o saldo do provedor'
    if 'authentication' in baixo or 'invalid_api_key' in baixo or 'invalid x-api-key' in baixo:
        return 'API key inválida ou revogada'
    if 'rate_limit' in baixo or 'rate limit' in baixo:
        return 'limite de requisições atingido (rate limit) — tente de novo em instantes'
    if 'not_found_error' in baixo or 'does not exist' in baixo:
        return f'modelo indisponível para esta chave ({txt[:120]})'
    if 'timeout' in baixo or 'timed out' in baixo:
        return 'a IA não respondeu dentro do tempo limite'
    return txt[:200]


def call_ai(system_prompt, user_prompt, max_tokens=600):
    """Texto da IA, ou None se ela não está configurada ou falhou.

    Tenta o provedor escolhido em Configurações e, se ele falhar, o OUTRO
    provedor — desde que tenha API key salva. Uma conta sem crédito ou uma
    chave revogada não deveria derrubar as automações quando existe um
    segundo provedor configurado ali do lado.
    """
    from .models import SystemSetting

    provider = (SystemSetting.get('ai_provider', 'claude') or 'claude').strip()
    ordem = ['openai', 'claude'] if provider == 'openai' else ['claude', 'openai']

    motivos = []
    for p in ordem:
        chamada = _call_openai if p == 'openai' else _call_claude
        texto, erro = chamada(system_prompt, user_prompt, max_tokens)
        if texto:
            if p != provider:
                logger.warning(
                    f"IA: provedor {_NOME_PROVEDOR[provider]} falhou, respondido por "
                    f"{_NOME_PROVEDOR[p]} (fallback)")
            _limpar_erro()
            return texto
        motivos.append(f"{_NOME_PROVEDOR[p]}: {erro}")

    resumo = ' | '.join(motivos)
    logger.error(f"IA indisponível — {resumo}")
    _registrar_erro(resumo)
    return None


def _call_claude(system_prompt, user_prompt, max_tokens):
    """Retorna (texto, erro). `erro` só é None quando veio texto."""
    from .models import SystemSetting

    api_key = SystemSetting.get('ai_api_key', '').strip()
    model = (SystemSetting.get('ai_model', 'claude-sonnet-4-6') or 'claude-sonnet-4-6').strip()
    if not api_key:
        return None, 'sem API key configurada'
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=30)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        texto = "".join(b.text for b in resp.content if getattr(b, 'type', '') == 'text')
        texto = texto.strip()
        return (texto, None) if texto else (None, 'resposta vazia')
    except Exception as e:
        logger.warning(f"Falha ao chamar Claude (agente IA atendimento): {e}")
        return None, _motivo_legivel(e)


def _call_openai(system_prompt, user_prompt, max_tokens):
    """Retorna (texto, erro). `erro` só é None quando veio texto."""
    from .models import SystemSetting

    api_key = SystemSetting.get('ai_openai_api_key', '').strip()
    model = (SystemSetting.get('ai_openai_model', 'gpt-4o') or 'gpt-4o').strip()
    if not api_key:
        return None, 'sem API key configurada'
    try:
        import openai
        client = openai.OpenAI(api_key=api_key, timeout=30)
        resp = client.chat.completions.create(
            model=model,
            max_completion_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        texto = (resp.choices[0].message.content or '').strip()
        return (texto, None) if texto else (None, 'resposta vazia')
    except Exception as e:
        logger.warning(f"Falha ao chamar OpenAI (agente IA atendimento): {e}")
        return None, _motivo_legivel(e)
