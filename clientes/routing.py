from django.urls import re_path
from . import consumers
from home.agent_consumer import AgentNOCConsumer
from atendimento.consumers import ConversationConsumer, InboxConsumer, VirtualRoomConsumer

websocket_urlpatterns = [
    re_path(r"ws/atendimento/sala/$", VirtualRoomConsumer.as_asgi()),
    # Atendimento — tempo real
    re_path(r'ws/atendimento/conversa/(?P<conversation_id>[0-9a-f-]+)/$', ConversationConsumer.as_asgi()),
    re_path(r'ws/atendimento/inbox/$', InboxConsumer.as_asgi()),

    re_path(r'ws/ssh/$', consumers.SSHConsumer.as_asgi()),
    # Visitante externo (sem login) via link temporário de terminal compartilhado
    re_path(r'ws/ssh-link/$', consumers.TerminalLinkExternoConsumer.as_asgi()),
    re_path(r'ws/winbox/$', consumers.WinboxConsumer.as_asgi()),
    re_path(r'ws/vnc/(?P<acesso_id>\d+)/$', consumers.WinboxVNCConsumer.as_asgi()),
    # Proxy WebSocket genérico: encaminha WS do browser para o equipamento via túnel SSH
    re_path(r'ws/proxy/(?P<acesso_id>\d+)/(?P<porta>\d+)/(?P<scheme>https?)/(?P<path>.*)$',
            consumers.WebSocketProxyConsumer.as_asgi()),
    # Agent NOC — chat com IA no terminal
    re_path(r'ws/agent/(?P<acesso_id>\d+)/$', AgentNOCConsumer.as_asgi()),
    re_path(r'ws/agent/$', AgentNOCConsumer.as_asgi()),
    # Firmware — progresso de downloads via links compartilhados
    re_path(r'ws/firmware/downloads/$', consumers.FirmwareDownloadConsumer.as_asgi()),
]