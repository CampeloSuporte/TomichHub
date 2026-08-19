"""Cliente fino para a IA configurada em Configurações → Integração IA
(SystemSetting: ai_provider/ai_api_key/ai_model/ai_openai_api_key/ai_openai_model).

Só chamada síncrona de turno único (sem tools, sem streaming) — usado pelo
agente "Tomichinho" do atendimento (resumo de chamados parados, resposta a
"tomichinho" e extração de tarefa em "abrir tarefa"). Se a IA não estiver
configurada ou a chamada falhar, retorna None: quem chama decide o fallback
(as automações daqui nunca podem travar por causa da IA).
"""
import logging

logger = logging.getLogger(__name__)


def call_ai(system_prompt, user_prompt, max_tokens=600):
    from .models import SystemSetting

    provider = (SystemSetting.get('ai_provider', 'claude') or 'claude').strip()
    if provider == 'openai':
        return _call_openai(system_prompt, user_prompt, max_tokens)
    return _call_claude(system_prompt, user_prompt, max_tokens)


def _call_claude(system_prompt, user_prompt, max_tokens):
    from .models import SystemSetting

    api_key = SystemSetting.get('ai_api_key', '').strip()
    model = (SystemSetting.get('ai_model', 'claude-sonnet-4-6') or 'claude-sonnet-4-6').strip()
    if not api_key:
        return None
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
        return texto.strip() or None
    except Exception as e:
        logger.warning(f"Falha ao chamar Claude (agente IA atendimento): {e}")
        return None


def _call_openai(system_prompt, user_prompt, max_tokens):
    from .models import SystemSetting

    api_key = SystemSetting.get('ai_openai_api_key', '').strip()
    model = (SystemSetting.get('ai_openai_model', 'gpt-4o') or 'gpt-4o').strip()
    if not api_key:
        return None
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
        texto = resp.choices[0].message.content or ''
        return texto.strip() or None
    except Exception as e:
        logger.warning(f"Falha ao chamar OpenAI (agente IA atendimento): {e}")
        return None
