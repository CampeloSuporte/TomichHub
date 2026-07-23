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
    no provedor (Chatmix/Opa Suite) — ambos rejeitam o envio se vier variável
    faltando/sobrando. Remove `|` do resultado (delimitador do `variables=`
    do Chatmix — inofensivo para o Opa Suite, que usa JSON puro)."""
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
        exigida pelo template configurado no Chatmix. Retorna (ok, detalhe).

        A Chatmix pode responder HTTP 200 e ainda assim sinalizar falha no
        corpo (`"success": false`) — por exemplo template pendente de
        aprovação da Meta, número sem WhatsApp, etc. Só olhar o status HTTP
        (como antes) fazia o teste reportar "enviado" mesmo quando a Chatmix
        recusou o envio internamente.
        """
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
            detalhe = r.text[:500]
            try:
                data = r.json()
            except ValueError:
                data = None
            if isinstance(data, dict) and data.get('success') is False:
                msg = data.get('message') or detalhe
                logger.error('Chatmix HSM recusou (HTTP 200, success=false): %s', msg)
                return False, msg
            return True, detalhe
        except requests.HTTPError as e:
            detalhe = f'HTTP {e.response.status_code}: {e.response.text[:200]}'
            logger.error('Chatmix HSM erro: %s', detalhe)
            return False, detalhe
        except Exception as e:
            logger.error('Chatmix HSM erro: %s', e)
            return False, str(e)


class OpaSuiteClient:
    """Cliente para a API de envio de templates do Opa Suite.

    Doc: https://api.opasuite.com.br/ (coleção Postman pública) — endpoint
    "Templates de mensagem → Enviar template".

    Diferente do Chatmix (1 endpoint fixo global), o Opa Suite é multi-tenant
    por domínio próprio — cada conta tem seu próprio `dominio` (ex:
    `https://minhaempresa.opasuite.com.br`) e autentica com um Bearer token
    gerado no cadastro de usuários (perfil de permissões "API").
    """

    def __init__(self, dominio, token):
        self.dominio = (dominio or '').rstrip('/')
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}',
        })

    def enviar_template(self, numero, canal_id, template_id, variaveis, timeout=20):
        """Envia um template de mensagem. `variaveis` é uma lista de valores,
        na ordem exigida pelo template configurado no Opa Suite. Retorna
        (ok, detalhe).

        Resposta de sucesso documentada: `{"status": "success", "code": 200,
        "data": {"message": "...", "messageSentId": "..."}}`. Qualquer
        `status` diferente de "success" é tratado como falha mesmo com HTTP
        200 — mesma cautela aplicada ao `ChatmixClient` (ver Bug 2 na doc).
        """
        payload = {
            'contato': {'canalCliente': numero},
            'template': {'_id': template_id, 'variaveis': list(variaveis)},
            'canal': canal_id,
        }
        try:
            r = self.session.post(f'{self.dominio}/api/v1/template/send', json=payload, timeout=timeout)
            r.raise_for_status()
            detalhe = r.text[:500]
            try:
                data = r.json()
            except ValueError:
                data = None
            if isinstance(data, dict) and data.get('status') not in (None, 'success'):
                msg = (data.get('data') or {}).get('message') or detalhe
                logger.error('Opa Suite recusou (HTTP 200, status!=success): %s', msg)
                return False, msg
            return True, detalhe
        except requests.HTTPError as e:
            detalhe = f'HTTP {e.response.status_code}: {e.response.text[:200]}'
            logger.error('Opa Suite erro: %s', detalhe)
            return False, detalhe
        except Exception as e:
            logger.error('Opa Suite erro: %s', e)
            return False, str(e)
