import json
import math
import time
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


@database_sync_to_async
def _pode_ver_conversa(user, conversation_id):
    """Mesma regra da porta HTTP (`scope.pode_ver_conversation`): instância do
    cliente + atendentes escolhidos no contato/grupo. Sem isso, bastava
    conhecer o UUID da conversa para assinar o canal dela e receber cada
    mensagem em tempo real, contornando as duas restrições."""
    from .models import Conversation
    from .scope import pode_ver_conversation
    conv = (Conversation.objects
            .select_related('group', 'cliente')
            .filter(id=conversation_id)
            .first())
    if conv is None:
        return False
    return pode_ver_conversation(user, conv)


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
        if not await _pode_ver_conversa(self.scope["user"], self.conversation_id):
            await self.close()
            return

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

        self.user_id = self.scope["user"].id
        self.group_name = "atendimento_inbox"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def inbox_update(self, event):
        """Todo mundo assina o MESMO grupo de canal ("atendimento_inbox"), então
        o pacote chega igual para todos e o filtro tem que ser aqui, na saída.

        `allowed_user_ids` vem do `_broadcast_msg` (ver
        `scope.atendentes_do_chamado`): `None`/ausente = chamado aberto, entrega
        para todos — que é o caso da esmagadora maioria e o comportamento de
        sempre. Com lista, só entrega para quem está nela — **sem exceção para
        administrador**, igual ao HTTP (ver o cabeçalho de `scope.py`).

        Um pacote antigo (enfileirado antes deste deploy) não traz a chave e é
        tratado como aberto — nunca some da tela de ninguém por causa disso.
        """
        data = event["data"]
        permitidos = (data.get("conversation") or {}).get("allowed_user_ids")
        if permitidos and self.user_id not in permitidos:
            return
        await self.send(text_data=json.dumps(data))

@database_sync_to_async
def _pode_remover_da_sala(user):
    """Quem pode tirar um colega da sala virtual: Administrador e Consultor
    (os mesmos que gerenciam usuários). Operador só participa."""
    from usuario.perms import pode_gerenciar_usuarios
    return pode_gerenciar_usuarios(user)


class VirtualRoomConsumer(AsyncWebsocketConsumer):
    """
    Sinalização WebRTC para a sala virtual de atendentes.
    URL: /ws/atendimento/sala/
    Grupo: atendimento_sala_virtual
    """

    _members = {}  # {channel_name: {user_id, display_name, channel_name, private_with}}
    # Quem foi removido não volta na hora com um F5: {user_id: (expira_em, por_quem)}
    _bloqueados = {}
    BLOQUEIO_SEGUNDOS = 120
    # Chamar a atenção toca som e treme a tela do colega: um clique a cada
    # 10 s por par basta, e o limite vale mesmo mexendo no navegador.
    _chamadas = {}  # {(user_id de quem chama, user_id do chamado): expira_em}
    CHAMADA_INTERVALO_SEGUNDOS = 10
    GROUP = "atendimento_sala_virtual"

    async def connect(self):
        user = self.scope["user"]
        if not await _pode_atendimento(user):
            await self.close()
            return

        self.display_name = user.get_full_name() or user.username
        self.user_id = str(user.id)
        self.pode_remover = await _pode_remover_da_sala(user)

        bloqueio = VirtualRoomConsumer._bloqueados.get(self.user_id)
        if bloqueio and bloqueio[0] > time.time():
            # accept antes do send: sem isso o navegador só vê a conexão
            # recusada e não sabe dizer à pessoa por que não entrou.
            await self.accept()
            await self.send(text_data=json.dumps({
                "type": "kicked",
                "reason": "removed",
                "by": bloqueio[1],
                "seconds": int(bloqueio[0] - time.time()),
            }))
            await self.close()
            return
        VirtualRoomConsumer._bloqueados.pop(self.user_id, None)

        await self.channel_layer.group_add(self.GROUP, self.channel_name)

        # Remove conexões antigas/duplicadas do MESMO usuário (reload ou 2 abas).
        # Sem isso o atendente aparece 2x na sala e acaba ouvindo a si mesmo.
        stale = [ch for ch, m in VirtualRoomConsumer._members.items()
                 if m.get("user_id") == self.user_id]
        for ch in stale:
            await self._encerrar_privado(ch)
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
            "private_with": None,
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
            "can_kick": self.pode_remover,
        }))

        # Notifica os outros sobre o novo participante
        await self.channel_layer.group_send(self.GROUP, {
            "type": "room_peer_joined",
            "sender": self.channel_name,
            "peer": VirtualRoomConsumer._members[self.channel_name],
        })

    async def disconnect(self, close_code):
        if self.channel_name not in VirtualRoomConsumer._members:
            # Bloqueado na entrada (nunca entrou no grupo) ou já removido.
            await self.channel_layer.group_discard(self.GROUP, self.channel_name)
            return
        await self._encerrar_privado(self.channel_name)
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

        elif msg_type == "private_start":
            await self._iniciar_privado(msg.get("target"))

        elif msg_type == "private_end":
            await self._encerrar_privado(self.channel_name)

        elif msg_type == "kick":
            await self._remover(msg.get("target"))

        elif msg_type == "nudge":
            await self._chamar_atencao(msg.get("target"))

    # ── Conversa privada ──────────────────────────────────────────
    # A sala só guarda e anuncia o par; quem corta o áudio é o navegador
    # de cada um (para de ENVIAR o microfone a quem está fora do par e
    # emudece quem está fora). Cortar no envio é o que garante que um
    # terceiro não ouve, mesmo mexendo no próprio navegador.
    async def _iniciar_privado(self, target):
        membros = VirtualRoomConsumer._members
        eu = membros.get(self.channel_name)
        alvo = membros.get(target) if target else None
        if not eu or not alvo or target == self.channel_name:
            await self._erro("Atendente não está mais na sala.")
            return
        if eu.get("private_with") == target:
            return
        if alvo.get("private_with"):
            await self._erro(f"{alvo['display_name']} já está em uma conversa privada.")
            return
        await self._encerrar_privado(self.channel_name)
        eu["private_with"] = target
        alvo["private_with"] = self.channel_name
        await self.channel_layer.group_send(self.GROUP, {
            "type": "room_private_state",
            "state": {
                "type": "private_state",
                "a": self.channel_name,
                "b": target,
                "active": True,
                "by": self.display_name,
            },
        })

    async def _encerrar_privado(self, ch):
        membros = VirtualRoomConsumer._members
        m = membros.get(ch)
        if not m or not m.get("private_with"):
            return
        outro = m["private_with"]
        m["private_with"] = None
        if outro in membros and membros[outro].get("private_with") == ch:
            membros[outro]["private_with"] = None
        await self.channel_layer.group_send(self.GROUP, {
            "type": "room_private_state",
            "state": {
                "type": "private_state",
                "a": ch,
                "b": outro,
                "active": False,
                "by": self.display_name,
            },
        })

    # ── Remover da sala ───────────────────────────────────────────
    async def _remover(self, target):
        if not self.pode_remover:
            await self._erro("Sem permissão para remover atendentes da sala.")
            return
        alvo = VirtualRoomConsumer._members.get(target) if target else None
        if not alvo or target == self.channel_name:
            await self._erro("Atendente não está mais na sala.")
            return
        VirtualRoomConsumer._bloqueados[alvo["user_id"]] = (
            time.time() + self.BLOQUEIO_SEGUNDOS, self.display_name)
        await self._encerrar_privado(target)
        VirtualRoomConsumer._members.pop(target, None)
        # Tira a tile de todo mundo já — não depende da conexão do removido
        # estar viva para fechar e disparar o disconnect.
        await self.channel_layer.group_send(self.GROUP, {
            "type": "room_peer_left",
            "sender": target,
            "channel_name": target,
            "kicked_by": self.display_name,
        })
        try:
            await self.channel_layer.send(target, {
                "type": "room_kick",
                "reason": "removed",
                "by": self.display_name,
                "seconds": self.BLOQUEIO_SEGUNDOS,
            })
        except Exception:
            pass

    # ── Chamar a atenção ──────────────────────────────────────────
    async def _chamar_atencao(self, target):
        alvo = VirtualRoomConsumer._members.get(target) if target else None
        if not alvo or target == self.channel_name:
            await self._erro("Atendente não está mais na sala.")
            return
        agora = time.time()
        par = (self.user_id, alvo["user_id"])
        expira = VirtualRoomConsumer._chamadas.get(par, 0)
        if expira > agora:
            await self._erro(
                f"Aguarde {math.ceil(expira - agora)} s para chamar {alvo['display_name']} de novo.")
            return
        VirtualRoomConsumer._chamadas[par] = agora + self.CHAMADA_INTERVALO_SEGUNDOS
        await self.channel_layer.send(target, {
            "type": "room_nudge",
            "from": self.channel_name,
            "display_name": self.display_name,
        })
        await self.send(text_data=json.dumps({
            "type": "nudge_sent",
            "display_name": alvo["display_name"],
        }))

    async def _erro(self, texto):
        await self.send(text_data=json.dumps({"type": "room_error", "text": texto}))

    async def room_peer_joined(self, event):
        if event["sender"] != self.channel_name:
            await self.send(text_data=json.dumps({"type": "peer_joined", "peer": event["peer"]}))

    async def room_peer_left(self, event):
        if event["sender"] != self.channel_name:
            await self.send(text_data=json.dumps({
                "type": "peer_left",
                "channel_name": event["channel_name"],
                "kicked_by": event.get("kicked_by"),
            }))

    async def room_signal(self, event):
        await self.send(text_data=json.dumps(event["signal"]))

    async def room_chat(self, event):
        await self.send(text_data=json.dumps(event["message"]))

    async def room_media_state(self, event):
        await self.send(text_data=json.dumps(event["state"]))

    async def room_private_state(self, event):
        await self.send(text_data=json.dumps(event["state"]))

    async def room_nudge(self, event):
        await self.send(text_data=json.dumps({
            "type": "nudge",
            "from": event["from"],
            "display_name": event["display_name"],
        }))

    async def room_kick(self, event):
        """Fecha esta conexão: removida por um colega (reason=removed) ou
        substituída por outra aba/reload do mesmo usuário (reason=duplicate)."""
        try:
            await self.send(text_data=json.dumps({
                "type": "kicked",
                "reason": event.get("reason", "duplicate"),
                "by": event.get("by"),
                "seconds": event.get("seconds"),
            }))
        except Exception:
            pass
        await self.close()
