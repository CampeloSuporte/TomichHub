from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/ssh/$', consumers.SSHConsumer.as_asgi()),
]