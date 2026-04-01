from django.urls import path
from messaging.apis import (
    list_chat_sessions,
    chat_history,
    mark_messages_read,
)

urlpatterns = [
    # Chat sessions
    path('chat/sessions/', list_chat_sessions, name='chat-sessions'),
    
    # Chat history
    path('chat/history/<int:booking_id>/', chat_history, name='chat-history'),
    
    # Mark messages as read
    path('chat/read/<int:booking_id>/', mark_messages_read, name='mark-read'),
]
