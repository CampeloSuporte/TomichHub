"""
services.py — Clientes de APIs externas de integração (disparo de WhatsApp/HSM).
"""
import logging
import re

import requests

logger = logging.getLogger(__name__)

CHATMIX_API_URL = 'https://envios.bulkv2.chatmix.com.br/api'

_PLACEHOLDER_RE = re.compile(r'\{(nome|telefone)\}')


def normalizar_numero_whatsapp(numero):
    """Normaliza um telefone BR para o formato +55DDDNÚMERO exigido pela API."""
    digitos = re.sub(r'\D', '', numero or '')
    if not digitos:
        return ''
    if not digitos.startswith('55') and len(digitos) in (10, 11):
        digitos = '55' + digitos
    return '+' + digitos


def montar_variaveis_mensagem(mensagem_modelo, lead):
    """Extrai, na ordem em que aparecem no corpo configurado, os valores do
    lead ({nome}/{telefone}) a enviar como `variables` da API de disparo HSM —
    a ordem deve corresponder exatamente à esperada pelo template no Chatmix."""
    campos = {'nome': lead.nome, 'telefone': lead.telefone}
    ordem = _PLACEHOLDER_RE.findall(mensagem_modelo or '')
    if not ordem:
        ordem = ['nome']
    return [campos.get(chave, '') for chave in ordem]


class ChatmixClient:
    """Cliente para a API de Disparos HSM do Chatmix.

    Doc: https://wiki.vmixsolucoes.com.br/chatmix-documentacao/integracoes/integracao-disparo/api-de-disparos-hsm
    """

    def __init__(self, key, token):
        self.key = key
        self.token = token
        self.session = requests.Session()

    def enviar_hsm(self, numero, variaveis, template_id, timeout=20):
        """Envia um disparo HSM. `variaveis` é uma lista de valores, na ordem
        exigida pelo template configurado no Chatmix. Retorna (ok, detalhe)."""
        mensagem = 'variables=' + '|'.join(str(v) for v in variaveis) + '||template=' + str(template_id)
        payload = {
            'key': self.key,
            'token': self.token,
            'numero': numero,
            'mensagem': mensagem,
        }
        try:
            r = self.session.post(CHATMIX_API_URL, json=payload, timeout=timeout)
            r.raise_for_status()
            return True, r.text[:500]
        except requests.HTTPError as e:
            detalhe = f'HTTP {e.response.status_code}: {e.response.text[:200]}'
            logger.error('Chatmix HSM erro: %s', detalhe)
            return False, detalhe
        except Exception as e:
            logger.error('Chatmix HSM erro: %s', e)
            return False, str(e)
