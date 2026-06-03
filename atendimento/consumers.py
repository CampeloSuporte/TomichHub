import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


class ConversationConsumer(AsyncWebsocketConsumer):
    """
    WebSocket para tempo real na conversa.
    URL: /ws/atendimento/conversa/<conversation_id>/
    Grupo: atendimento_conv_<conversation_id>
    """

    async def connect(self):
        if not self.scope["user"].is_authenticated:
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
        if not self.scope["user"].is_authenticated:
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
