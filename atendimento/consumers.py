import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


@database_sync_to_async
def _pode_atendimento(user):
    """Mesma regra da porta HTTP (`atendimento.views.staff_required`): o
    módulo é exclusivo da instância principal.

    Sem isso os consumers checavam só `is_authenticated` — qualquer conta
    logada, inclusive login de portal de cliente e Consultor de revenda,
    conseguia assinar `atendimento_inbox` e receber em tempo real toda
    mensagem que passasse pelo módulo, mesmo sem conseguir abrir a tela.
    """
    from usuario.perms import pode_acessar_atendimento
    return pode_acessar_atendimento(user)


class ConversationConsumer(AsyncWebsocketConsumer):
    """
    WebSocket para tempo real na conversa.
    URL: /ws/atendimento/conversa/<conversation_id>/
    Grupo: atendimento_conv_<conversation_id>
    """

    async def connect(self):
        if not await _pode_atendimento(self.scope["user"]):
            await self.close()
            return

        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.group_name = f"atendimento_conv_{self.conversation_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # Recebe mensagem do grupo e envia ao browser
    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event["data"]))


class InboxConsumer(AsyncWebsocketConsumer):
    """
    WebSocket para a caixa de entrada.
    URL: /ws/atendimento/inbox/
    Grupo: atendimento_inbox
    Notifica quando novas conversas chegam ou mudam de status.
    """

    async def connect(self):
        if not await _pode_atendimento(self.scope["user"]):
            await self.close()
            return

        self.group_name = "atendimento_inbox"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def inbox_update(self, event):
        await self.send(text_data=json.dumps(event["data"]))

class VirtualRoomConsumer(AsyncWebsocketConsumer):
    """
    Sinalização WebRTC para a sala virtual de atendentes.
    URL: /ws/atendimento/sala/
    Grupo: atendimento_sala_virtual
    """

    _members = {}  # {channel_name: {user_id, display_name, channel_name}}
    GROUP = "atendimento_sala_virtual"

    async def connect(self):
        user = self.scope["user"]
        if not await _pode_atendimento(user):
            await self.close()
            return

        self.display_name = user.get_full_name() or user.username
        self.user_id = str(user.id)

        await self.channel_layer.group_add(self.GROUP, self.channel_name)

        # Remove conexões antigas/duplicadas do MESMO usuário (reload ou 2 abas).
        # Sem isso o atendente aparece 2x na sala e acaba ouvindo a si mesmo.
        stale = [ch for ch, m in VirtualRoomConsumer._members.items()
                 if m.get("user_id") == self.user_id]
        for ch in stale:
            VirtualRoomConsumer._members.pop(ch, None)
            # Avisa os demais para removerem a tile antiga
            await self.channel_layer.group_send(self.GROUP, {
                "type": "room_peer_left",
                "sender": self.channel_name,
                "channel_name": ch,
            })
            # Pede para a conexão antiga se fechar (se ainda estiver viva)
            try:
                await self.channel_layer.send(ch, {"type": "room_kick"})
            except Exception:
                pass

        VirtualRoomConsumer._members[self.channel_name] = {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "channel_name": self.channel_name,
            "audio": False,
            "screen": False,
        }

        await self.accept()

        # Envia lista de peers existentes para quem acabou de entrar
        # (deduplicado por usuário — no máx. 1 presença por atendente)
        existing = []
        _seen_users = {self.user_id}
        for k, v in VirtualRoomConsumer._members.items():
            if k == self.channel_name:
                continue
            uid = v.get("user_id")
            if uid in _seen_users:
                continue
            _seen_users.add(uid)
            existing.append(v)
        await self.send(text_data=json.dumps({
            "type": "room_joined",
            "my_id": self.channel_name,
            "peers": existing,
        }))

        # Notifica os outros sobre o novo participante
        await self.channel_layer.group_send(self.GROUP, {
            "type": "room_peer_joined",
            "sender": self.channel_name,
            "peer": VirtualRoomConsumer._members[self.channel_name],
        })

    async def disconnect(self, close_code):
        VirtualRoomConsumer._members.pop(self.channel_name, None)

        await self.channel_layer.group_send(self.GROUP, {
            "type": "room_peer_left",
            "sender": self.channel_name,
            "channel_name": self.channel_name,
        })
        await self.channel_layer.group_discard(self.GROUP, self.channel_name)

    async def receive(self, text_data):
        try:
            msg = json.loads(text_data)
        except Exception:
            return
        msg_type = msg.get("type")

        # Sinalização WebRTC — encaminha para o peer específico
        if msg_type in ("offer", "answer", "ice_candidate"):
            target = msg.get("target")
            if target and target in VirtualRoomConsumer._members:
                await self.channel_layer.send(target, {
                    "type": "room_signal",
                    "signal": {
                        "type": msg_type,
                        "from": self.channel_name,
                        "payload": msg.get("payload"),
                    },
                })

        elif msg_type == "chat_message":
            text = str(msg.get("text", ""))[:500].strip()
            if text:
                await self.channel_layer.group_send(self.GROUP, {
                    "type": "room_chat",
                    "message": {
                        "type": "chat_message",
                        "from": self.channel_name,
                        "display_name": self.display_name,
                        "text": text,
                    },
                })

        elif msg_type == "media_state":
            m = VirtualRoomConsumer._members.get(self.channel_name)
            if m:
                m["audio"] = bool(msg.get("audio"))
                m["screen"] = bool(msg.get("screen"))
            await self.channel_layer.group_send(self.GROUP, {
                "type": "room_media_state",
                "state": {
                    "type": "media_state",
                    "channel_name": self.channel_name,
                    "audio": bool(msg.get("audio")),
                    "screen": bool(msg.get("screen")),
                },
            })

    async def room_peer_joined(self, event):
        if event["sender"] != self.channel_name:
            await self.send(text_data=json.dumps({"type": "peer_joined", "peer": event["peer"]}))

    async def room_peer_left(self, event):
        if event["sender"] != self.channel_name:
            await self.send(text_data=json.dumps({"type": "peer_left", "channel_name": event["channel_name"]}))

    async def room_signal(self, event):
        await self.send(text_data=json.dumps(event["signal"]))

    async def room_chat(self, event):
        await self.send(text_data=json.dumps(event["message"]))

    async def room_media_state(self, event):
        await self.send(text_data=json.dumps(event["state"]))

    async def room_kick(self, event):
        """Conexão antiga do mesmo usuário — fecha para não duplicar na sala."""
        try:
            await self.send(text_data=json.dumps({"type": "kicked"}))
        except Exception:
            pass
        await self.close()
