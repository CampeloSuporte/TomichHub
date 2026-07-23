"""
services.py — Clientes de APIs externas de integração (disparo de WhatsApp/HSM).
"""
import logging
import re

import requests

logger = logging.getLogger(__name__)

CHATMIX_API_URL = 'https://envios.bulkv2.chatmix.com.br/api'


def normalizar_numero_whatsapp(numero):
    """Normaliza um telefone BR para o formato +55DDDNÚMERO exigido pela API."""
    digitos = re.sub(r'\D', '', numero or '')
    if not digitos:
        return ''
    if not digitos.startswith('55') and len(digitos) in (10, 11):
        digitos = '55' + digitos
    return '+' + digitos


def montar_variaveis_mensagem(variaveis_modelo, lead):
    """Renderiza cada variável configurada (texto fixo e/ou {nome}/{telefone})
    substituindo pelos dados do lead. A quantidade e a ordem das entradas em
    `variaveis_modelo` devem corresponder exatamente ao que o template exige
    no Chatmix — o Chatmix rejeita o envio se vier variável faltando/sobrando.
    Remove `|` do resultado, já que é o delimitador do formato `variables=`."""
    campos = {'nome': lead.nome or '', 'telefone': lead.telefone or ''}
    valores = []
    for item in (variaveis_modelo or []):
        texto = str(item)
        try:
            texto = texto.format(**campos)
        except (KeyError, IndexError, ValueError):
            pass
        valores.append(texto.replace('|', ' '))
    return valores


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
