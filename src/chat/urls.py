from django.urls import path

from chat.apis.chat import (
    client_chat,
    client_chat_with_history,
    trainer_chat,
    trainer_chat_with_history,
)

urlpatterns = [
    # Client chat endpoints
    path('chat/client/', client_chat, name='client-chat'),
    path('chat/client/history/', client_chat_with_history, name='client-chat-history'),
    
    # Trainer chat endpoints
    path('chat/trainer/', trainer_chat, name='trainer-chat'),
    path('chat/trainer/history/', trainer_chat_with_history, name='trainer-chat-history'),
]
