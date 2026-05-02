from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/ssh/$', consumers.SSHConsumer.as_asgi()),
    re_path(r'ws/winbox/$', consumers.WinboxConsumer.as_asgi()),
    re_path(r'ws/vnc/(?P<acesso_id>\d+)/$', consumers.WinboxVNCConsumer.as_asgi()),
]