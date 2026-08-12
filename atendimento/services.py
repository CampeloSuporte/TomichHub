import os
import uuid
import base64
import requests
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from .models import (
    WhatsAppConnection, ContactGroup, Conversation, Message,
    ConversationActivity, ChatFlow, ChatFlowSession, Category,
)
from clientes.models import Cliente

logger = logging.getLogger(__name__)
channel_layer = get_channel_layer()


# ── WebRTC: servidor ICE (STUN + TURN com credenciais temporárias) ───────────
def build_ice_servers() -> List[Dict]:
    """
    Monta a lista de iceServers para o WebRTC (Sala Virtual e chamadas 1:1).

    Inclui STUN públicos e, se houver TURN_SECRET configurado, um servidor TURN
    com credenciais temporárias no padrão TURN REST API:
        username = "<expiry_unix>:crm"
        password = base64( HMAC_SHA1(TURN_SECRET, username) )
    Assim o segredo nunca vai para o navegador e as credenciais expiram sozinhas.
    """
    servers = [
        {'urls': 'stun:stun.l.google.com:19302'},
        {'urls': 'stun:stun1.l.google.com:19302'},
    ]
    secret = getattr(settings, 'TURN_SECRET', '')
    host   = getattr(settings, 'TURN_HOST', '')
    if secret and host:
        import hmac, hashlib, time
        ttl = int(getattr(settings, 'TURN_TTL', 12 * 3600))
        username = f"{int(time.time()) + ttl}:crm"
        digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
        credential = base64.b64encode(digest).decode()
        servers.append({
            'urls': [
                f'turn:{host}:3478?transport=udp',
                f'turn:{host}:3478?transport=tcp',
            ],
            'username': username,
            'credential': credential,
        })
    return servers

# Mapeamento: chave do payload WhatsApp → message_type interno
_MEDIA_TYPE_MAP = {
    'imageMessage':    'image',
    'stickerMessage':  'image',
    'videoMessage':    'video',
    'audioMessage':    'audio',
    'pttMessage':      'audio',
    'documentMessage': 'document',
}

_MIME_EXT = {
    'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
    'image/webp': '.webp', 'image/sticker': '.webp',
    'audio/ogg': '.ogg', 'audio/ogg; codecs=opus': '.ogg',
    'audio/mpeg': '.mp3', 'audio/mp4': '.m4a',
    'video/mp4': '.mp4', 'video/webm': '.webm',
    'application/pdf': '.pdf',
}


def _notify_new_open_conversation(conversation, connection) -> None:
    """Envia notificação WhatsApp quando um novo chamado aberto chega.

    Avisa UMA ÚNICA VEZ por chamado (marca conversation.notif_aberto_enviada).
    Sem isso, cada nova mensagem do cliente num chamado sem atendente gerava
    uma notificação nova — virando spam no grupo."""
    try:
        from .models import SystemSetting
        if SystemSetting.get('notif_abertos_enabled', 'false') != 'true':
            return
        # Já avisado uma vez — não repete a cada mensagem do cliente
        if getattr(conversation, 'notif_aberto_enviada', False):
            return
        group_id = SystemSetting.get('notif_abertos_group_id', '').strip()
        if not group_id:
            return
        from .models import ContactGroup as _CG
        _group = _CG.objects.filter(id=group_id).select_related('connection').first()
        if not _group or not _group.connection:
            return
        notif_jid = _group.jid
        connection = _group.connection
        texto = (
            f"🔔 *Novo chamado em aberto!*\n\n"
            f"📱 Grupo: *{conversation.group.name}*\n\n"
            f"Acesse o sistema para assumir o chamado."
        )
        # send_text retorna (bool, msg_id) — checar a tupla inteira com
        # `if ok:` seria sempre verdadeiro (tupla de 2 nunca é vazia),
        # marcando o alerta como enviado mesmo se o envio falhasse de verdade.
        ok, _msg_id = EvolutionAPIClient(connection).send_text(notif_jid, texto, everyone=True)
        if ok:
            conversation.notif_aberto_enviada = True
            conversation.save(update_fields=['notif_aberto_enviada'])
    except Exception as e:
        logger.warning(f"Falha ao enviar notificação de novo chamado: {e}")


def _detectar_atendente_pessoal(participant_jid: str):
    """Se `participant_jid` (remetente real de uma mensagem de grupo, vindo
    do campo `participant` do webhook) corresponder ao telefone pessoal de
    algum atendente cadastrado em Contatos Atendentes, retorna esse
    AttendantContact. Caso contrário, retorna None (mensagem é do cliente).

    Compara com normalizar_telefone_br (tolera com/sem o nono dígito) em vez
    de match exato — números BR cadastrados sem o 9 (erro comum de digitação)
    faziam essa detecção nunca bater, deixando o alerta de "atendente no
    pessoal" silenciosamente nunca disparar."""
    if not participant_jid:
        return None
    phone = participant_jid.split('@')[0].strip()
    if not phone:
        return None
    from .models import AttendantContact, normalizar_telefone_br
    alvo = normalizar_telefone_br(phone)
    if not alvo:
        return None
    for ac in AttendantContact.objects.select_related('user').all():
        if normalizar_telefone_br(ac.phone) == alvo:
            return ac
    return None


def _alertar_atendente_pessoal(conversation, group, attendant_contact, connection) -> None:
    """Avisa o grupo do NOC (mesmo grupo de 'chamados abertos') que um
    atendente está respondendo este chamado pelo WhatsApp pessoal em vez da
    plataforma. Avisa UMA ÚNICA VEZ por chamado (personal_wa_alert_sent)."""
    try:
        from .models import SystemSetting
        if getattr(conversation, 'personal_wa_alert_sent', False):
            return
        group_id = SystemSetting.get('notif_abertos_group_id', '').strip()
        if not group_id:
            return
        from .models import ContactGroup as _CG
        _alert_group = _CG.objects.filter(id=group_id).select_related('connection').first()
        if not _alert_group or not _alert_group.connection:
            return
        # Marca o atendente via mentionedJid (contextInfo do WhatsApp) — ele é
        # notificado/destacado no grupo mesmo sem o número aparecer no texto;
        # a marcação não depende de ter "@numero" escrito na mensagem.
        nome_atendente = attendant_contact.user.get_full_name() or attendant_contact.user.username
        texto = (
            f"⚠️ *Atenção!*\n\n"
            f"*{nome_atendente}* está respondendo o chamado do grupo "
            f"*{group.name}* pelo WhatsApp pessoal, não pela plataforma.\n\n"
            f"Solicitamos que responda pela plataforma, mantendo o registro "
            f"profissional do atendimento."
        )
        # send_text retorna (bool, msg_id) — checar a tupla inteira com
        # `if ok:` seria sempre verdadeiro (tupla de 2 nunca é vazia),
        # marcando o alerta como enviado mesmo se o envio falhasse de verdade.
        ok, _msg_id = EvolutionAPIClient(_alert_group.connection).send_text(
            _alert_group.jid, texto, mentions=[attendant_contact.phone]
        )
        if ok:
            conversation.personal_wa_alert_sent = True
            conversation.save(update_fields=['personal_wa_alert_sent'])
    except Exception as e:
        logger.warning(f"Falha ao alertar sobre atendente pessoal: {e}")


# ── SLA (tempo de resposta/resolução) ────────────────────────────────────────
# Prazos padrão por prioridade, em minutos. Sobrescrevíveis via SystemSetting
# (chaves 'sla_response_<priority>' / 'sla_resolution_<priority>').
SLA_DEFAULTS_MINUTOS = {
    'urgent': {'response': 15, 'resolution': 240},    # 15 min / 4h
    'high':   {'response': 30, 'resolution': 480},    # 30 min / 8h
    'medium': {'response': 120, 'resolution': 1440},  # 2h / 24h
    'low':    {'response': 240, 'resolution': 2880},  # 4h / 48h
}


def get_sla_minutos(priority: str) -> Tuple[int, int]:
    """Retorna (minutos_resposta, minutos_resolucao) para a prioridade, com
    override via SystemSetting."""
    from .models import SystemSetting
    defaults = SLA_DEFAULTS_MINUTOS.get(priority, SLA_DEFAULTS_MINUTOS['medium'])
    try:
        resp = int(SystemSetting.get(f'sla_response_{priority}', '') or defaults['response'])
    except (ValueError, TypeError):
        resp = defaults['response']
    try:
        reso = int(SystemSetting.get(f'sla_resolution_{priority}', '') or defaults['resolution'])
    except (ValueError, TypeError):
        reso = defaults['resolution']
    return resp, reso


def aplicar_sla(conversation, from_time=None):
    """Calcula e grava os prazos de SLA (resposta/resolução) com base na
    prioridade atual da conversa. Não salva — quem chama decide quando salvar."""
    base = from_time or timezone.now()
    resp_min, reso_min = get_sla_minutos(conversation.priority)
    conversation.sla_response_due_at = base + timedelta(minutes=resp_min)
    conversation.sla_resolution_due_at = base + timedelta(minutes=reso_min)


def _save_media_file(b64_data: str, mimetype: str) -> str:
    """Decodifica base64, grava em MEDIA_ROOT e retorna a URL relativa."""
    ext = _MIME_EXT.get(mimetype.split(';')[0].strip(), '.bin')
    filename = str(uuid.uuid4()) + ext
    dir_path = os.path.join(settings.MEDIA_ROOT, 'atendimento', 'media')
    os.makedirs(dir_path, exist_ok=True)
    with open(os.path.join(dir_path, filename), 'wb') as f:
        f.write(base64.b64decode(b64_data))
    return f"{settings.MEDIA_URL}atendimento/media/{filename}"


def _read_attachment_as_base64(attachment_url: str) -> str:
    """Lê de volta um arquivo salvo por _save_media_file e devolve em
    base64 — usado pra reenviar a mídia de uma mensagem agendada, que só
    guarda a URL (não o base64) enquanto espera a hora de enviar."""
    if not attachment_url.startswith(settings.MEDIA_URL):
        raise ValueError(f"attachment_url fora de MEDIA_URL: {attachment_url}")
    relative = attachment_url[len(settings.MEDIA_URL):]
    abs_path = os.path.join(settings.MEDIA_ROOT, relative)
    with open(abs_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('ascii')


def _ws_send_conversation(conversation_id: str, data: dict):
    """Envia evento para o grupo WebSocket da conversa (thread-safe)."""
    try:
        async_to_sync(channel_layer.group_send)(
            f"atendimento_conv_{conversation_id}",
            {"type": "chat.message", "data": data},
        )
    except Exception as e:
        logger.warning(f"WebSocket send falhou (conv {conversation_id}): {e}")


def _ws_send_inbox(data: dict):
    """Envia evento para o grupo da caixa de entrada."""
    try:
        async_to_sync(channel_layer.group_send)(
            "atendimento_inbox",
            {"type": "inbox.update", "data": data},
        )
    except Exception as e:
        logger.warning(f"WebSocket send falhou (inbox): {e}")


def notify_reassignment(conversation: Conversation, old_assigned_to_id):
    """Avisa a caixa de entrada (WS) que um chamado mudou de atendente —
    seja por "Assumir", auto-atribuição ao responder, transferência manual
    ou reatribuição automática por SLA. Sem isso, o novo/antigo responsável
    só via a mudança em "Assumidos" depois de recarregar a página."""
    if old_assigned_to_id == conversation.assigned_to_id:
        return
    _ws_send_inbox({
        "type": "conversation_reassigned",
        "conversation_id": str(conversation.id),
        "group_name": conversation.group.name if conversation.group else "",
        "old_assigned_to_id": old_assigned_to_id,
        "assigned_to_id": conversation.assigned_to_id,
    })


def auto_assign_on_reply(conversation: Conversation, agent) -> bool:
    """Atribui a conversa a quem respondeu, quando ela ainda não tem dono.

    Fica aqui (e não na view) porque TODO envio de agente passa por
    ConversationService.send_message/send_media: chat, mídia, mensagem
    agendada e mensagem de encerramento. Quando essa regra morava só na
    view de texto, responder por qualquer um dos outros caminhos deixava
    o chamado sem responsável e ele nunca aparecia em "Assumidos".

    Devolve True se acabou de atribuir (o front usa para trocar o
    cabeçalho de "Assumir" para "Transferir" sem recarregar a página).
    """
    if agent is None or not getattr(agent, "is_authenticated", True):
        return False
    if conversation.assigned_to_id:
        return False

    old_assigned_to_id = conversation.assigned_to_id
    conversation.assigned_to = agent
    conversation.save(update_fields=["assigned_to"])
    ConversationActivity.objects.create(
        conversation=conversation,
        actor=agent,
        action="assigned",
        new_value=agent.get_full_name() or agent.username,
    )
    notify_reassignment(conversation, old_assigned_to_id)
    return True


def _numero_nao_existe(response) -> bool:
    """Detecta o 400 específico da Evolution API que indica número inexistente
    no WhatsApp: {"response": {"message": [{"exists": false, ...}]}}."""
    if response is None or response.status_code != 400:
        return False
    try:
        itens = response.json().get('response', {}).get('message', [])
        return any(item.get('exists') is False for item in itens)
    except Exception:
        return False


def _alternar_nono_digito(jid: str) -> Optional[str]:
    """Alterna a presença do 9º dígito num JID celular BR (55+DDD+[9]+8 dígitos).
    Retorna None se o JID não tiver essa forma (nada a alternar)."""
    if '@' not in jid:
        return None
    numero, dominio = jid.split('@', 1)
    if not numero.startswith('55') or not numero.isdigit():
        return None
    local = numero[4:]  # após 55 + DDD (2 dígitos)
    ddd = numero[2:4]
    if len(local) == 9 and local[0] == '9':
        return f'55{ddd}{local[1:]}@{dominio}'
    if len(local) == 8:
        return f'55{ddd}9{local}@{dominio}'
    return None


class EvolutionAPIClient:
    """Cliente para Evolution API v2"""

    def __init__(self, connection: WhatsAppConnection):
        self.connection = connection
        self.base_url = connection.evolution_url.rstrip('/')
        self.api_key = connection.api_key
        self.instance = connection.instance_name
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'apikey': self.api_key,
        })

    def _get(self, path: str, params=None, timeout=15) -> requests.Response:
        return self.session.get(f"{self.base_url}{path}", params=params, timeout=timeout)

    def _post(self, path: str, payload=None, timeout=30) -> requests.Response:
        return self.session.post(f"{self.base_url}{path}", json=payload or {}, timeout=timeout)

    # ── Conectividade ────────────────────────────────────────────────────────

    def test_connection(self) -> Tuple[bool, str]:
        """Testa conexão verificando o estado da instância"""
        try:
            # Evolution API v2: GET /instance/connectionState/{instance}
            r = self._get(f"/instance/connectionState/{self.instance}")
            if r.status_code == 404:
                return False, f"Instância '{self.instance}' não encontrada (404). Verifique o nome da instância."
            r.raise_for_status()
            data = r.json()
            state = data.get('instance', {}).get('state') or data.get('state', 'unknown')
            if state == 'open':
                return True, f"Conectado — instância '{self.instance}' online"
            # Instância existe mas não está com sessão WhatsApp ativa (ex:
            # 'close' = desconectada, precisa reescanear o QR code) — antes
            # isso retornava sucesso, escondendo exatamente o motivo pelo
            # qual os envios de mensagem falham com 400.
            return False, f"Instância '{self.instance}' encontrada mas desconectada (estado: {state}) — reconecte escaneando o QR code."
        except requests.HTTPError as e:
            return False, f"HTTP {e.response.status_code}: {e.response.text[:200]}"
        except Exception as e:
            return False, str(e)

    # ── Grupos ───────────────────────────────────────────────────────────────

    def fetch_all_groups(self) -> List[Dict]:
        """Busca todos os grupos da instância"""
        try:
            # Endpoint primário: GET /group/fetchAllGroups/{instance}
            r = self._get(
                f"/group/fetchAllGroups/{self.instance}",
                params={"getParticipants": "false"},
                timeout=60,
            )
            if r.ok:
                data = r.json() or []
                if isinstance(data, list) and data:
                    return data

            # Fallback: POST /chat/findChats/{instance}
            r2 = self._post(f"/chat/findChats/{self.instance}", {})
            if r2.ok:
                chats = r2.json() or []
                return [
                    {
                        "id": c.get("remoteJid") or c.get("id"),
                        "subject": c.get("pushName") or c.get("name") or c.get("remoteJid"),
                        "pictureUrl": c.get("profilePicUrl"),
                    }
                    for c in chats
                    if (c.get("remoteJid") or c.get("id", "")).endswith("@g.us")
                ]
        except Exception as e:
            logger.error(f"Erro ao buscar grupos [{self.instance}]: {e}")
        return []

    def get_group_info(self, jid: str) -> Optional[Dict]:
        """Obtém informações detalhadas de um grupo"""
        try:
            r = self._get(
                f"/group/findGroupInfos/{self.instance}",
                params={"groupJid": jid},
            )
            if r.ok:
                return r.json()
        except Exception as e:
            logger.warning(f"Erro ao obter info do grupo {jid}: {e}")
        return None

    # ── Mensagens ────────────────────────────────────────────────────────────

    def get_group_participants(self, group_jid: str) -> List[str]:
        """Retorna lista de números de telefone dos participantes do grupo."""
        try:
            r = self._get(f"/group/participants/{self.instance}", params={"groupJid": group_jid})
            if r.ok:
                parts = r.json().get("participants", [])
                return [
                    p["phoneNumber"].split("@")[0]
                    for p in parts
                    if p.get("phoneNumber")
                ]
        except Exception as e:
            logger.warning(f"Erro ao buscar participantes do grupo {group_jid}: {e}")
        return []

    def send_text(self, jid: str, text: str, mentions: List[str] = None,
                  everyone: bool = False) -> Tuple[bool, str]:
        """Envia mensagem de texto. Retorna (sucesso, message_id_evolution).
        everyone=True: passa os números no campo 'mentioned' (todos recebem notificação)
        sem poluir o corpo da mensagem com @número.
        """
        body = {"number": jid, "text": text}
        if everyone and jid.endswith("@g.us"):
            numbers = self.get_group_participants(jid)
            if numbers:
                body["mentioned"] = numbers
            else:
                body["everyOne"] = True
        elif mentions:
            body["mentioned"] = mentions
        try:
            r = self._post(f"/message/sendText/{self.instance}", body)
            r.raise_for_status()
            msg_id = r.json().get("key", {}).get("id") or ""
            return True, msg_id
        except requests.HTTPError as e:
            # BR: alguns números existem no WhatsApp só com o nono dígito e
            # outros só sem ele (contas antigas/portadas) — quem cadastrou o
            # contato não tem como saber qual variante está registrada. A
            # Evolution API responde 400 com "exists": false quando o JID
            # exato não existe; nesse caso tenta a variante alternada do 9º
            # dígito antes de desistir, em vez de forçar o usuário a
            # descobrir manualmente.
            jid_alt = _numero_nao_existe(e.response) and _alternar_nono_digito(jid)
            if jid_alt:
                logger.warning(
                    f"Envio p/ {jid} recusado (número não existe) — tentando variante do 9º dígito: {jid_alt}"
                )
                try:
                    body["number"] = jid_alt
                    r2 = self._post(f"/message/sendText/{self.instance}", body)
                    r2.raise_for_status()
                    msg_id = r2.json().get("key", {}).get("id") or ""
                    return True, msg_id
                except Exception as e2:
                    logger.error(f"Erro ao enviar texto para {jid} (variante {jid_alt} também falhou): {e2}")
                    return False, ""
            logger.error(f"Erro ao enviar texto para {jid}: {e}")
            return False, ""
        except Exception as e:
            logger.error(f"Erro ao enviar texto para {jid}: {e}")
            return False, ""

    def send_media(self, jid: str, mediatype: str, media_b64: str,
                   filename: str = "arquivo", caption: str = "") -> bool:
        """Envia mídia (imagem, documento, vídeo)"""
        try:
            r = self._post(f"/message/sendMedia/{self.instance}", {
                "number": jid,
                "mediatype": mediatype,
                "media": media_b64,
                "fileName": filename,
                "caption": caption or "",
            })
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Erro ao enviar mídia para {jid}: {e}")
            return False

    def download_media(self, event_data: dict) -> tuple:
        """Baixa a mídia de uma mensagem recebida via webhook.
        Retorna (base64_str, mimetype) ou (None, None) em caso de falha.
        """
        try:
            r = self._post(
                f"/chat/getBase64FromMediaMessage/{self.instance}",
                {"message": event_data, "convertToMp4": False},
                timeout=15,
            )
            if r.ok:
                data = r.json()
                b64 = data.get("base64") or data.get("data")
                mime = data.get("mimetype") or data.get("mimeType") or "application/octet-stream"
                if b64:
                    return b64, mime
        except Exception as e:
            logger.warning(f"Falha ao baixar mídia: {e}")
        return None, None

    def send_audio(self, jid: str, audio_b64: str) -> bool:
        """Envia áudio PTT"""
        try:
            r = self._post(f"/message/sendWhatsAppAudio/{self.instance}", {
                "number": jid,
                "audio": audio_b64,
                "encoding": True,
            })
            r.raise_for_status()
            return True
        except Exception:
            # fallback como media
            return self.send_media(jid, "audio", audio_b64, "audio.mp3")

    # ── Webhook ──────────────────────────────────────────────────────────────

    def configure_webhook(self, webhook_url: str) -> Tuple[bool, str]:
        """Configura webhook na Evolution API v2"""
        try:
            r = self._post(f"/webhook/set/{self.instance}", {
                "webhook": {
                    "enabled": True,
                    "url": webhook_url,
                    "webhookByEvents": False,
                    "webhookBase64": False,
                    "events": [
                        "MESSAGES_UPSERT",
                        "CONNECTION_UPDATE",
                        "GROUP_UPDATE",
                    ],
                }
            })
            r.raise_for_status()
            return True, "Webhook configurado com sucesso"
        except requests.HTTPError as e:
            return False, f"HTTP {e.response.status_code}: {e.response.text[:200]}"
        except Exception as e:
            return False, str(e)


class ConversationService:
    """Lógica de negócio para conversas"""

    @staticmethod
    def sync_groups(connection: WhatsAppConnection) -> Dict:
        """Sincroniza grupos da Evolution API"""
        try:
            client = EvolutionAPIClient(connection)
            groups = client.fetch_all_groups()

            if not groups:
                return {"success": False, "message": "Nenhum grupo encontrado"}

            created = updated = 0
            for g in groups:
                jid = g.get("id") or g.get("remoteJid")
                if not jid:
                    continue

                name = g.get("subject") or g.get("name") or jid
                pic  = g.get("pictureUrl") or g.get("profilePicUrl")

                obj, is_new = ContactGroup.objects.update_or_create(
                    jid=jid, connection=connection,
                    defaults={"name": name, "is_group": True, "profile_picture": pic},
                )
                if is_new:
                    created += 1
                    ConversationService.auto_link_group(obj)
                else:
                    updated += 1

            connection.last_sync = timezone.now()
            connection.save(update_fields=["last_sync"])

            return {
                "success": True,
                "created": created,
                "updated": updated,
                "message": f"{created} novos grupos, {updated} atualizados",
            }
        except Exception as e:
            logger.error(f"Erro ao sincronizar grupos: {e}")
            return {"success": False, "message": str(e)}

    @staticmethod
    def auto_link_group(group: ContactGroup) -> bool:
        """Tenta vincular grupo automaticamente ao cliente pelo nome"""
        if group.cliente:
            return False
        try:
            cliente = Cliente.objects.filter(
                nome_empresa__icontains=group.name[:10]
            ).first()
            if cliente:
                group.cliente = cliente
                group.save(update_fields=["cliente"])
                return True
        except Exception as e:
            logger.error(f"Erro ao vincular grupo: {e}")
        return False

    @staticmethod
    def get_or_create_conversation(group: ContactGroup) -> Conversation:
        # Busca apenas conversas regulares (não-task) abertas — task_conv são tickets separados
        conv = Conversation.objects.filter(
            group=group, status__in=["new", "open", "pending"], is_task_conv=False
        ).order_by('-last_message_at').first()
        if not conv:
            conv = Conversation.objects.create(
                group=group, cliente=group.cliente, status="new"
            )
        elif conv.cliente is None and group.cliente:
            conv.cliente = group.cliente
            conv.save(update_fields=["cliente"])
        return conv

    @staticmethod
    def process_webhook(data: Dict) -> Dict:
        """
        Processa evento recebido da Evolution API v2.

        Estrutura real do payload Evolution API v2:
        {
          "event": "MESSAGES_UPSERT",
          "instance": "nome_instancia",
          "data": {
            "key": { "remoteJid": "...@g.us", "fromMe": false, "id": "MSG_ID" },
            "pushName": "Nome",
            "message": { "conversation": "texto" },
            "messageTimestamp": 1234567890
          }
        }
        """
        try:
            instance_name = data.get("instance")
            event = data.get("event") or data.get("type", "")
            event_data = data.get("data", {})

            logger.info(f"Webhook recebido: event={event} instance={instance_name}")

            # Ignora eventos que não são mensagens
            if event not in ("MESSAGES_UPSERT", "messages.upsert"):
                return {"success": True, "message": f"Evento ignorado: {event}"}

            # ── Extrai key e JID ───────────────────────────────────────────
            # Evolution API v2: data.key.remoteJid
            key = event_data.get("key", {})
            jid = (
                key.get("remoteJid")
                or event_data.get("remoteJid")
                or event_data.get("from")
            )

            if not jid:
                logger.warning(f"Webhook sem JID. event_data keys: {list(event_data.keys())}")
                return {"success": False, "message": "JID não encontrado"}

            # Só processa grupos (@g.us)
            if not jid.endswith("@g.us"):
                return {"success": True, "message": "Mensagem privada ignorada"}

            from_me = key.get("fromMe", False)

            # Mensagens enviadas pelo próprio atendente via CRM já são salvas
            # localmente em send_message() — ignorar o echo do webhook evita duplicação.
            if from_me:
                return {"success": True, "message": "fromMe ignored"}

            # ── Atendente respondendo pelo WhatsApp pessoal? ────────────────
            # `participant` é o remetente real dentro do grupo (distinto do
            # `remoteJid`, que é o JID do grupo). Se bater com o telefone de
            # um atendente cadastrado em Contatos Atendentes, tratamos como
            # mensagem do agente (não do cliente) e avisamos o grupo do NOC.
            participant = event_data.get("participant") or key.get("participant") or ""
            atendente_pessoal = _detectar_atendente_pessoal(participant)
            sender_type = 'agent' if atendente_pessoal else 'customer'
            if atendente_pessoal:
                push_name_override = atendente_pessoal.user.get_full_name() or atendente_pessoal.user.username
            else:
                push_name_override = None

            # ── Busca conexão ──────────────────────────────────────────────
            connection = WhatsAppConnection.objects.filter(
                instance_name=instance_name, is_active=True
            ).first()
            if not connection:
                logger.warning(f"Conexão não encontrada para instância: {instance_name}")
                return {"success": False, "message": "Conexão não encontrada"}

            # ── Snooze: "Reagendar lembrete" no grupo de notificações ──────
            _early_text = (
                event_data.get("message", {}).get("conversation")
                or event_data.get("message", {}).get("extendedTextMessage", {}).get("text")
                or ""
            )
            if "reagendar" in _early_text.lower():
                from .models import SystemSetting
                _notif_gid = SystemSetting.get('notif_abertos_group_id', '').strip()
                if _notif_gid:
                    _ng = ContactGroup.objects.filter(id=_notif_gid).first()
                    if _ng and _ng.jid == jid:
                        _amanha = (timezone.localtime(timezone.now()) + timedelta(days=1)).strftime('%Y-%m-%d')
                        SystemSetting.set('notif_abertos_snooze_until', _amanha)
                        try:
                            EvolutionAPIClient(connection).send_text(
                                jid,
                                f"✅ *Lembrete reagendado!*\n\nVoltarei a notificar chamados em aberto amanhã ({_amanha}).",
                            )
                        except Exception as _e:
                            logger.warning(f"Falha ao confirmar snooze: {_e}")
                        return {"success": True, "message": "notif_snoozed"}
            # ──────────────────────────────────────────────────────────────

            # ── Grupo e conversa ───────────────────────────────────────────
            group, _ = ContactGroup.objects.get_or_create(
                jid=jid, connection=connection,
                defaults={"name": jid.split("@")[0]}
            )

            # ── Extrai conteúdo e detecta tipo de mídia ───────────────────
            msg_content = event_data.get("message", {})
            push_name = event_data.get("pushName") or ""

            # Detecta se é mensagem de mídia
            detected_type = "text"
            for wkey, mtype in _MEDIA_TYPE_MAP.items():
                if msg_content.get(wkey):
                    detected_type = mtype
                    break

            # Extrai texto/legenda
            content = (
                msg_content.get("conversation")
                or msg_content.get("extendedTextMessage", {}).get("text")
                or msg_content.get("imageMessage", {}).get("caption")
                or msg_content.get("videoMessage", {}).get("caption")
                or msg_content.get("documentMessage", {}).get("title")
                or msg_content.get("documentMessage", {}).get("fileName")
                or ("" if detected_type == "audio" else None)
                or ("[mídia]" if detected_type != "text" else None)
                or "[sem conteúdo]"
            )

            # ── Baixa mídia (apenas para mensagens recebidas) ──────────────
            attachment_url = None
            if detected_type != "text" and not from_me:
                # Tenta base64 direto no payload (se webhookBase64 estiver on)
                b64 = event_data.get("base64")
                mime = None
                if b64:
                    # Descobre mimetype a partir do objeto de mídia
                    for wkey in _MEDIA_TYPE_MAP:
                        mime = msg_content.get(wkey, {}).get("mimetype")
                        if mime:
                            break
                else:
                    # Baixa via API da Evolution
                    try:
                        client = EvolutionAPIClient(connection)
                        b64, mime = client.download_media(event_data)
                    except Exception as _e:
                        logger.warning(f"download_media falhou: {_e}")

                if b64 and mime:
                    try:
                        attachment_url = _save_media_file(b64, mime)
                    except Exception as _e:
                        logger.warning(f"_save_media_file falhou: {_e}")

            message_id = key.get("id") or f"local_{timezone.now().timestamp()}"

            # @noc → mensagem direcionada ao Agent NOC; o atendimento ignora
            # totalmente (não cria nem abre chamado).
            if '@noc' in (content or '').lower():
                return {"success": True, "message": "noc_ignored"}

            now = timezone.now()

            # ── 1. Auto-atendimento em andamento? ──────────────────────────
            session = ChatFlowSession.objects.filter(
                group_jid=jid, expires_at__gt=now,
            ).select_related('flow', 'conversation').first()
            if session:
                conv = session.conversation
                if conv:
                    msg, created = ConversationService._salvar_msg(
                        conv, message_id, content, detected_type,
                        push_name_override or push_name, attachment_url, sender_type=sender_type)
                    if created:
                        conv.last_message_at = now
                        conv.save(update_fields=['last_message_at'])
                        ConversationService._broadcast_msg(conv, group, msg)
                    if atendente_pessoal:
                        _alertar_atendente_pessoal(conv, group, atendente_pessoal, connection)
                # Mensagem do atendente pessoal não deve avançar o fluxo do bot
                # (que espera resposta do cliente, não do agente)
                if not atendente_pessoal:
                    ConversationService._processar_passo_fluxo(session, conv, content, group, connection)
                return {"success": True, "message": "flow_step",
                        "conversation_id": str(conv.id) if conv else None}

            # ── 2. Chamado já aberto? ──────────────────────────────────────
            conv = Conversation.objects.filter(
                group=group, status__in=['new', 'open', 'pending'], is_task_conv=False,
            ).order_by('-last_message_at').first()
            if conv:
                msg, created = ConversationService._salvar_msg(
                    conv, message_id, content, detected_type,
                    push_name_override or push_name, attachment_url, sender_type=sender_type)
                if created:
                    conv.last_message_at = now
                    if conv.status == 'new':
                        conv.status = 'open'
                    conv.save(update_fields=['last_message_at', 'status'])
                    if not conv.assigned_to:
                        _notify_new_open_conversation(conv, connection)
                    ConversationService._broadcast_msg(conv, group, msg)
                if atendente_pessoal:
                    _alertar_atendente_pessoal(conv, group, atendente_pessoal, connection)
                return {"success": True, "conversation_id": str(conv.id),
                        "message_id": str(msg.id), "created": created}

            # ── 2.5 Resposta de CSAT? ───────────────────────────────────────
            # Se há um chamado recém-resolvido aguardando avaliação e a
            # mensagem parece uma nota (1-5), registra e NÃO abre chamado novo.
            csat_resultado = ConversationService._tentar_registrar_csat(
                group, content, connection)
            if csat_resultado:
                return csat_resultado

            # ── 3. Nova conversa — abre e aparece já na 1ª mensagem ────────
            # Reaproveita uma conversa 'pre' antiga (de antes desta mudança)
            # se existir, em vez de deixá-la órfã e escondida para sempre.
            conv = Conversation.objects.filter(
                group=group, status='pre', is_task_conv=False,
            ).order_by('-last_message_at').first()
            if not conv:
                conv = Conversation.objects.create(
                    group=group, cliente=group.cliente, status='open')

            msg, created = ConversationService._salvar_msg(
                conv, message_id, content, detected_type,
                push_name_override or push_name, attachment_url, sender_type=sender_type)
            if not created:
                return {"success": True, "message": "dup", "conversation_id": str(conv.id)}
            conv.last_message_at = now
            conv.status = 'open'
            aplicar_sla(conv, from_time=now)
            conv.save(update_fields=['last_message_at', 'status', 'sla_response_due_at', 'sla_resolution_due_at'])

            # Mostra a mensagem na hora — antes de qualquer processamento do bot
            ConversationService._broadcast_msg(conv, group, msg, inbox=True)

            if atendente_pessoal:
                _alertar_atendente_pessoal(conv, group, atendente_pessoal, connection)

            flow = ConversationService._flow_do_grupo(group)
            if flow and not atendente_pessoal:
                # Auto-atendimento simplificado: sem perguntas — só avisa que
                # o chamado foi aberto (o chamado já está 'open' desde a
                # linha acima, antes deste bloco rodar). Nada de sessão de
                # fluxo aguardando resposta do cliente.
                ConversationService._flow_enviar(conv, group, connection, flow.greeting_message)
                if flow.completion_message:
                    ConversationService._flow_enviar(conv, group, connection, flow.completion_message)
                if not conv.assigned_to:
                    _notify_new_open_conversation(conv, connection)
            elif not conv.assigned_to:
                _notify_new_open_conversation(conv, connection)
            logger.info(f"Chamado aberto na 1ª msg: conversa #{conv.conversation_id}")

            return {"success": True, "conversation_id": str(conv.id), "opened": True}
        except Exception as e:
            logger.error(f"Erro ao processar webhook: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    # ── CSAT (pesquisa de satisfação) ──────────────────────────────────────
    @staticmethod
    def _tentar_registrar_csat(group: ContactGroup, content: str, connection: WhatsAppConnection):
        """Se houver um chamado deste grupo recém-resolvido aguardando
        avaliação (csat_requested_at preenchido, csat_rating vazio, dentro
        da janela de 48h) e a mensagem recebida parecer uma nota de 1 a 5,
        registra o CSAT e agradece — sem abrir um chamado novo.
        Retorna o dict de resultado do webhook se tratou como CSAT, ou None
        caso a mensagem deva seguir o fluxo normal (abrir/continuar chamado)."""
        import re
        janela = timezone.now() - timedelta(hours=48)
        conv = Conversation.objects.filter(
            group=group, csat_requested_at__gte=janela, csat_rating__isnull=True,
            status__in=['resolved', 'closed'],
        ).order_by('-csat_requested_at').first()
        if not conv:
            return None

        texto = (content or '').strip()
        m = re.match(r'^\s*([1-5])\b\s*(.*)$', texto)
        if not m:
            return None

        nota = int(m.group(1))
        comentario = m.group(2).strip()
        conv.csat_rating = nota
        conv.csat_comment = comentario
        conv.save(update_fields=['csat_rating', 'csat_comment'])

        try:
            EvolutionAPIClient(connection).send_text(
                group.jid, "🙏 Obrigado pela avaliação! Sua opinião nos ajuda a melhorar."
            )
        except Exception as _e:
            logger.warning(f"Falha ao agradecer CSAT: {_e}")

        return {"success": True, "message": "csat_registrado",
                "conversation_id": str(conv.id), "rating": nota}

    # ── Auto-atendimento (fluxo) sobre uma conversa já existente ───────────
    @staticmethod
    def _flow_do_grupo(group: ContactGroup):
        """Retorna o ChatFlow ativo pra este grupo.

        Um flow com group_ids vazio vale como padrão universal (todos os
        grupos, inclusive um recém-criado pela própria 1ª mensagem via
        get_or_create). Sem isso, um grupo só recebia a saudação do bot se
        alguém lembrasse de adicioná-lo manualmente na lista — 15 dos 43
        grupos cadastrados nunca foram, então o cliente mandava mensagem e
        só um humano acabava respondendo, bem mais tarde.
        Flow com group_ids preenchido continua tendo prioridade (permite um
        fluxo dedicado pra um cliente específico no futuro).

        `auto_atendimento_excluido` é um opt-out explícito por grupo — sem ele,
        não havia como tirar um grupo específico do auto atendimento quando
        existe um fluxo universal ativo (ele pegaria todo mundo de novo)."""
        if group.auto_atendimento_excluido:
            return None
        gid = str(group.id)
        flows = list(ChatFlow.objects.filter(active=True))
        for f in flows:
            if f.group_ids and gid in [str(x) for x in f.group_ids]:
                return f
        for f in flows:
            if not f.group_ids:
                return f
        return None

    @staticmethod
    def _salvar_msg(conversation, message_id, content, detected_type,
                    push_name, attachment_url, sender_type='customer'):
        """Cria a mensagem (idempotente pelo external_id)."""
        return Message.objects.get_or_create(
            external_id=message_id,
            defaults={
                "conversation": conversation,
                "sender_type": sender_type,
                "message_type": detected_type,
                "content": content,
                "sender_name": push_name,
                "attachment_url": attachment_url,
            },
        )

    @staticmethod
    def _broadcast_msg(conversation, group, msg, inbox=True):
        """Envia a mensagem por WebSocket (conversa + opcionalmente inbox)."""
        local_now = timezone.localtime(timezone.now())
        payload = {
            "type": "new_message",
            "message": {
                "id": str(msg.id),
                "content": msg.content,
                "sender_type": msg.sender_type,
                "sender_name": msg.sender_name or "",
                "created_at": local_now.strftime("%H:%M"),
                "created_at_iso": msg.created_at.isoformat(),
                "message_type": msg.message_type,
                "attachment_url": msg.attachment_url or "",
            },
            "conversation": {
                "id": str(conversation.id),
                "conversation_id": conversation.conversation_id,
                "status": conversation.status,
                "group_name": group.name,
                "last_message_at": local_now.strftime("%H:%M"),
                "assigned_to_id": conversation.assigned_to_id,
            },
        }
        _ws_send_conversation(str(conversation.id), payload)
        if inbox:
            _ws_send_inbox(payload)

    @staticmethod
    def _flow_enviar(conversation, group, connection, texto):
        """Envia uma pergunta/saudação do auto-atendimento ao WhatsApp e a salva
        como mensagem 'system' para aparecer no chat."""
        if not texto or not conversation:
            return
        import uuid as _uuid
        msg = Message.objects.create(
            conversation=conversation,
            sender_type='system',
            sender_name='Auto Atendimento',
            message_type='text',
            content=texto,
            external_id='flow_%s' % _uuid.uuid4().hex,
        )
        conversation.last_message_at = timezone.now()
        conversation.save(update_fields=['last_message_at'])
        try:
            EvolutionAPIClient(connection).send_text(group.jid, texto)
        except Exception as _e:
            logger.warning(f"Falha ao enviar msg do fluxo: {_e}")
        ConversationService._broadcast_msg(conversation, group, msg, inbox=False)

    @staticmethod
    def _processar_passo_fluxo(session: ChatFlowSession, conv, content: str,
                               group: ContactGroup, connection: WhatsAppConnection):
        """Processa a resposta do cliente no auto-atendimento (assunto/categoria)."""
        flow = session.flow

        if session.step == 'subject':
            session.subject = content
            session.step = 'category'
            session.save(update_fields=['subject', 'step'])
            cats = flow.categories or []
            if cats:
                lines = [flow.category_question]
                for i, c in enumerate(cats, 1):
                    lines.append(f'{i} - {c}')
                ConversationService._flow_enviar(conv, group, connection, '\n'.join(lines))
            else:
                ConversationService._finalizar_fluxo(session, conv, group, connection, None)
            return

        if session.step == 'category':
            cats = flow.categories or []
            chosen = None
            stripped = (content or '').strip()
            try:
                num = int(stripped)
                if 1 <= num <= len(cats):
                    chosen = cats[num - 1]
            except ValueError:
                lower = stripped.lower()
                for cat in cats:
                    if lower in cat.lower() or cat.lower().startswith(lower):
                        chosen = cat
                        break
            if chosen is None and cats:
                ConversationService._flow_enviar(
                    conv, group, connection,
                    f'Por favor, responda com um número de 1 a {len(cats)}.')
                return
            ConversationService._finalizar_fluxo(session, conv, group, connection, chosen)
            return

    @staticmethod
    def _finalizar_fluxo(session: ChatFlowSession, conv, group: ContactGroup,
                         connection: WhatsAppConnection, chosen_category):
        """Conclui o auto-atendimento: grava assunto/categoria no chamado já
        existente e encerra a sessão do fluxo."""
        flow = session.flow
        category_obj = None
        if chosen_category:
            category_obj, _ = Category.objects.get_or_create(
                name=chosen_category, defaults={'color': '#7c3aed'})
        if conv:
            if session.subject:
                conv.subject = session.subject
            if category_obj:
                conv.category = category_obj
            if conv.status == 'pre':
                conv.status = 'open'
            conv.save()
        if flow.completion_message:
            ConversationService._flow_enviar(conv, group, connection, flow.completion_message)
        session.delete()
        if conv and not conv.assigned_to:
            _notify_new_open_conversation(conv, connection)
        if conv:
            _ws_send_inbox({
                "type": "new_message",
                "conversation": {
                    "id": str(conv.id),
                    "conversation_id": conv.conversation_id,
                    "status": conv.status,
                    "group_name": group.name,
                    "subject": conv.subject,
                    "last_message_at": timezone.localtime(timezone.now()).strftime("%H:%M"),
                    "assigned_to_id": conv.assigned_to_id,
                },
            })

    @staticmethod
    def get_agent_display_name(agent) -> str:
        """Retorna o nome de exibição configurado ou o nome completo"""
        if not agent:
            return "Atendente"
        try:
            return agent.agent_status.get_display_name()
        except Exception:
            return agent.get_full_name() or agent.username

    @staticmethod
    def send_message(conversation: Conversation, text: str,
                     agent=None) -> Tuple[bool, str]:
        """Salva a mensagem imediatamente e envia ao WhatsApp em background.
        Formato enviado: *NomeAgente*\n\nmensagem
        """
        import threading as _threading

        try:
            display_name = ConversationService.get_agent_display_name(agent)
            whatsapp_text = f"*{display_name}*\n\n{text}"

            # 0. Quem responde, assume — vale para qualquer caminho de envio
            auto_assign_on_reply(conversation, agent)

            # 1. Salva no DB imediatamente com ID temporário
            now = timezone.now()
            temp_id = f"sending_{int(now.timestamp() * 1000)}_{conversation.id}"
            msg = Message.objects.create(
                external_id=temp_id,
                conversation=conversation,
                sender_type="agent",
                sender=agent,
                sender_name=display_name,
                message_type="text",
                content=text,
                created_at=now,
            )

            # 2. Atualiza conversa e cria atividade
            conversation.last_message_at = now
            if conversation.status == "new":
                conversation.status = "open"
            update_fields = ["last_message_at", "status"]
            if not conversation.first_response_at:
                conversation.first_response_at = now
                update_fields.append("first_response_at")
            conversation.save(update_fields=update_fields)
            ConversationActivity.objects.create(
                conversation=conversation,
                actor=agent,
                action="message_sent",
                description=text[:100],
            )

            # 3. Notifica via WebSocket antes de qualquer I/O externo
            local_time = timezone.localtime(now)
            _ws_send_conversation(str(conversation.id), {
                "type": "new_message",
                "message": {
                    "id": str(msg.id),
                    "content": text,
                    "sender_type": "agent",
                    "sender_name": display_name,
                    "created_at": local_time.strftime("%H:%M"),
                    "created_at_iso": msg.created_at.isoformat(),
                },
            })

            # 4. Envia ao WhatsApp em background — sem bloquear a resposta HTTP
            msg_id = msg.id
            group_connection = conversation.group.connection
            group_jid = conversation.group.jid

            def _send_bg():
                try:
                    client = EvolutionAPIClient(group_connection)
                    ok, remote_id = client.send_text(group_jid, whatsapp_text)
                    if ok and remote_id:
                        Message.objects.filter(id=msg_id).update(external_id=remote_id)
                    elif not ok:
                        logger.error(f"Envio bg falhou (msg {msg_id}): {remote_id}")
                except Exception as _e:
                    logger.error(f"Erro bg no envio (msg {msg_id}): {_e}")

            _threading.Thread(target=_send_bg, daemon=True).start()

            return True, str(msg.id)

        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {e}")
            return False, str(e)

    @staticmethod
    def send_media(conversation: Conversation, media_base64: str, media_type: str,
                   file_name: str, caption: str, agent=None) -> Tuple[bool, str]:
        """Salva a Message de mídia imediatamente e envia ao WhatsApp em
        background. Mesma mecânica de send_message, mas para
        imagem/áudio/vídeo/documento. Igual ao envio imediato, sempre salva
        um arquivo novo em disco (mesmo se o chamador já tiver um
        attachment_url de antes, como no agendador) — simples e evita um
        segundo caminho de código só pra reaproveitar o arquivo."""
        import threading as _threading
        import mimetypes as _mt
        import time as _t

        try:
            detected_mime, _ = _mt.guess_type(file_name)
            if not detected_mime:
                detected_mime = {
                    'image': 'image/jpeg', 'audio': 'audio/ogg',
                    'video': 'video/mp4', 'document': 'application/octet-stream',
                }.get(media_type, 'application/octet-stream')

            attachment_url = None
            try:
                attachment_url = _save_media_file(media_base64, detected_mime)
            except Exception as _save_err:
                logger.warning("Salvar midia falhou: %s", _save_err)

            if caption:
                content = caption
            elif media_type == 'document':
                content = file_name
            else:
                type_labels = {'image': 'Imagem', 'audio': 'Áudio', 'document': 'Documento', 'video': 'Vídeo'}
                content = type_labels.get(media_type, media_type)

            display_name = ConversationService.get_agent_display_name(agent)
            auto_assign_on_reply(conversation, agent)
            now = timezone.now()
            msg = Message.objects.create(
                conversation=conversation, sender_type='agent', sender=agent,
                sender_name=display_name, message_type=media_type, content=content,
                external_id=f"local_media_{int(_t.time()*1000)}",
                attachment_url=attachment_url, created_at=now,
            )
            conversation.last_message_at = now
            if conversation.status == 'new':
                conversation.status = 'open'
            conversation.save(update_fields=['last_message_at', 'status'])

            group_connection = conversation.group.connection
            group_jid = conversation.group.jid
            msg_id = msg.id

            def _send_bg():
                try:
                    client = EvolutionAPIClient(group_connection)
                    if media_type == 'audio':
                        client.send_audio(group_jid, media_base64)
                    else:
                        client.send_media(group_jid, mediatype=media_type, media_b64=media_base64,
                                          filename=file_name, caption=caption)
                except Exception as _e:
                    logger.error(f"Erro bg envio mídia (msg {msg_id}): {_e}")

            _threading.Thread(target=_send_bg, daemon=True).start()

            return True, str(msg.id)

        except Exception as e:
            logger.error(f"Erro ao enviar mídia: {e}")
            return False, str(e)
