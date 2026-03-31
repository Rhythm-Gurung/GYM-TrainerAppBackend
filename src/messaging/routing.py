from django.urls import path
from messaging.consumers import ChatConsumer, BadgeConsumer

websocket_urlpatterns = [
    path('ws/chat/<int:booking_id>/', ChatConsumer.as_asgi(), name='ws-chat'),
    path('ws/badge/', BadgeConsumer.as_asgi(), name='ws-badge'),
]
