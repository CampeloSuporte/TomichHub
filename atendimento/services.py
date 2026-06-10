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
    """Envia notificação WhatsApp quando um novo chamado aberto chega."""
    try:
        from .models import SystemSetting
        if SystemSetting.get('notif_abertos_enabled', 'false') != 'true':
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
        EvolutionAPIClient(connection).send_text(notif_jid, texto, everyone=True)
    except Exception as e:
        logger.warning(f"Falha ao enviar notificação de novo chamado: {e}")


def _save_media_file(b64_data: str, mimetype: str) -> str:
    """Decodifica base64, grava em MEDIA_ROOT e retorna a URL relativa."""
    ext = _MIME_EXT.get(mimetype.split(';')[0].strip(), '.bin')
    filename = str(uuid.uuid4()) + ext
    dir_path = os.path.join(settings.MEDIA_ROOT, 'atendimento', 'media')
    os.makedirs(dir_path, exist_ok=True)
    with open(os.path.join(dir_path, filename), 'wb') as f:
        f.write(base64.b64decode(b64_data))
    return f"{settings.MEDIA_URL}atendimento/media/{filename}"


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
            return True, f"Instância encontrada (estado: {state})"
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
        try:
            body = {"number": jid, "text": text}
            if everyone and jid.endswith("@g.us"):
                numbers = self.get_group_participants(jid)
                if numbers:
                    body["mentioned"] = numbers
                else:
                    body["everyOne"] = True
            elif mentions:
                body["mentioned"] = mentions
            r = self._post(f"/message/sendText/{self.instance}", body)
            r.raise_for_status()
            msg_id = r.json().get("key", {}).get("id") or ""
            return True, msg_id
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

            # ── Busca conexão ──────────────────────────────────────────────
            connection = WhatsAppConnection.objects.filter(
                instance_name=instance_name, is_active=True
            ).first()
            if not connection:
                logger.warning(f"Conexão não encontrada para instância: {instance_name}")
                return {"success": False, "message": "Conexão não encontrada"}

            # ── Grupo e conversa ───────────────────────────────────────────
            group, _ = ContactGroup.objects.get_or_create(
                jid=jid, connection=connection,
                defaults={"name": jid.split("@")[0]}
            )

            # ── Auto-atendimento (chat flow) ───────────────────────────────
            _msg_obj = event_data.get("message", {})
            _text_content = (
                _msg_obj.get("conversation")
                or _msg_obj.get("extendedTextMessage", {}).get("text")
                or ""
            )
            _flow_result = ConversationService._handle_chat_flow(
                group=group, jid=jid, connection=connection,
                content=_text_content, from_me=False,
            )
            if _flow_result is not None:
                return _flow_result
            # ──────────────────────────────────────────────────────────────

            conversation = ConversationService.get_or_create_conversation(group)

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

            # ── Salva mensagem (idempotente pelo ID) ───────────────────────
            message_id = key.get("id") or f"local_{timezone.now().timestamp()}"
            msg, created = Message.objects.get_or_create(
                external_id=message_id,
                defaults={
                    "conversation": conversation,
                    "sender_type": "agent" if from_me else "customer",
                    "message_type": detected_type,
                    "content": content,
                    "sender_name": push_name,
                    "attachment_url": attachment_url,
                },
            )

            if created:
                now = timezone.now()
                conversation.last_message_at = now
                if conversation.status == "new" and not from_me:
                    conversation.status = "open"
                conversation.save(update_fields=["last_message_at", "status"])

                # Notificação imediata se for novo chamado sem atendente
                if not from_me and not conversation.assigned_to:
                    _notify_new_open_conversation(conversation, connection)
                logger.info(f"Mensagem criada: {message_id} na conversa #{conversation.conversation_id}")

                # ── Notifica WebSocket ─────────────────────────────────
                local_now = timezone.localtime(now)
                msg_payload = {
                    "type": "new_message",
                    "message": {
                        "id": str(msg.id),
                        "content": content,
                        "sender_type": msg.sender_type,
                        "sender_name": push_name,
                        "created_at": local_now.strftime("%H:%M"),
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
                _ws_send_conversation(str(conversation.id), msg_payload)
                _ws_send_inbox(msg_payload)

            return {
                "success": True,
                "conversation_id": str(conversation.id),
                "message_id": str(msg.id),
                "created": created,
            }
        except Exception as e:
            logger.error(f"Erro ao processar webhook: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    @staticmethod
    def _handle_chat_flow(group: ContactGroup, jid: str, connection: WhatsAppConnection,
                          content: str, from_me: bool) -> Optional[Dict]:
        """
        Verifica se uma mensagem deve ser tratada pelo auto-atendimento.
        Retorna dict se tratado, None se o processamento normal deve continuar.
        """
        now = timezone.now()

        # Sessão ativa para este JID?
        session = ChatFlowSession.objects.filter(
            group_jid=jid,
            expires_at__gt=now,
        ).select_related('flow').first()

        if session:
            # Mensagens do próprio bot durante o fluxo — ignora
            if from_me:
                return {"success": True, "message": "bot_flow_msg"}

            flow = session.flow
            client = EvolutionAPIClient(connection)

            if session.step == 'subject':
                session.subject = content
                session.step = 'category'
                session.save()

                cats = flow.categories or []
                if cats:
                    lines = [flow.category_question]
                    for i, c in enumerate(cats, 1):
                        lines.append(f'{i} - {c}')
                    client.send_text(jid, '\n'.join(lines))
                else:
                    ConversationService._complete_chat_flow(session, group, connection, None)
                    session.delete()
                return {"success": True, "message": "flow_subject"}

            if session.step == 'category':
                cats = flow.categories or []
                chosen = None
                stripped = content.strip()
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
                    client.send_text(jid, f'Por favor, responda com um número de 1 a {len(cats)}.')
                    return {"success": True, "message": "flow_invalid_cat"}

                ConversationService._complete_chat_flow(session, group, connection, chosen)
                session.delete()
                return {"success": True, "message": "flow_complete"}

            return None

        # Sem sessão ativa — verifica se deve iniciar um fluxo
        if from_me:
            return None

        # Não inicia se já existe conversa regular aberta (task_conv não conta)
        has_open = Conversation.objects.filter(
            group=group,
            status__in=['new', 'open', 'pending'],
            is_task_conv=False,
        ).exists()
        if has_open:
            return None

        # Procura fluxo ativo que inclua este grupo
        group_id_str = str(group.id)
        matched_flow = None
        for f in ChatFlow.objects.filter(active=True):
            ids = [str(gid) for gid in (f.group_ids or [])]
            if group_id_str in ids:
                matched_flow = f
                break

        if not matched_flow:
            return None

        # Cria sessão ANTES de enviar mensagens (evita race com webhook from_me)
        ChatFlowSession.objects.create(
            flow=matched_flow,
            group_jid=jid,
            step='subject',
            expires_at=now + timedelta(hours=1),
        )

        client = EvolutionAPIClient(connection)
        if matched_flow.greeting_message:
            client.send_text(jid, matched_flow.greeting_message)
        client.send_text(jid, matched_flow.subject_question)

        return {"success": True, "message": "flow_started"}

    @staticmethod
    def _complete_chat_flow(session: ChatFlowSession, group: ContactGroup,
                            connection: WhatsAppConnection, chosen_category: Optional[str]) -> None:
        """Cria a conversa/ticket ao finalizar o fluxo de auto-atendimento."""
        flow = session.flow
        client = EvolutionAPIClient(connection)

        category_obj = None
        if chosen_category:
            category_obj, _ = Category.objects.get_or_create(
                name=chosen_category,
                defaults={'color': '#7c3aed'},
            )

        conv = Conversation.objects.create(
            group=group,
            cliente=group.cliente,
            status='open',
            subject=session.subject or '',
            category=category_obj,
        )

        # Salva mensagens coletadas durante o fluxo para exibir no chamado
        flow_msgs = []
        cid = conv.conversation_id

        flow_msgs.append(Message(
            conversation=conv,
            sender_type='system',
            sender_name='Auto Atendimento',
            message_type='text',
            content=flow.subject_question,
            external_id='flow_%s_q1' % cid,
        ))
        if session.subject:
            flow_msgs.append(Message(
                conversation=conv,
                sender_type='customer',
                sender_name='',
                message_type='text',
                content=session.subject,
                external_id='flow_%s_a1' % cid,
            ))
        if flow.categories:
            flow_msgs.append(Message(
                conversation=conv,
                sender_type='system',
                sender_name='Auto Atendimento',
                message_type='text',
                content=flow.category_question,
                external_id='flow_%s_q2' % cid,
            ))
            if chosen_category:
                flow_msgs.append(Message(
                    conversation=conv,
                    sender_type='customer',
                    sender_name='',
                    message_type='text',
                    content=chosen_category,
                    external_id='flow_%s_a2' % cid,
                ))

        if flow_msgs:
            Message.objects.bulk_create(flow_msgs)
            conv.last_message_at = timezone.now()
            conv.save(update_fields=['last_message_at'])

        if flow.completion_message:
            client.send_text(group.jid, flow.completion_message)

        _notify_new_open_conversation(conv, connection)

        _ws_send_inbox({
            "type": "new_message",
            "conversation": {
                "id": str(conv.id),
                "conversation_id": conv.conversation_id,
                "status": conv.status,
                "group_name": group.name,
                "subject": conv.subject,
                "last_message_at": conv.created_at.strftime("%H:%M"),
                "assigned_to_id": None,
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
            conversation.save(update_fields=["last_message_at", "status"])
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
