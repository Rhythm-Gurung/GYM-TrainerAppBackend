from django.conf import settings
from django.db import models


class ChatSession(models.Model):
    """
    Represents an active chat conversation between a trainer and client.
    Tied to a confirmed booking to ensure chat only happens for valid sessions.
    """

    trainer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='trainer_chat_sessions',
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='client_chat_sessions',
    )
    booking = models.OneToOneField(
        'scheduling.Booking',
        on_delete=models.CASCADE,
        related_name='chat_session',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Chat Session'
        verbose_name_plural = 'Chat Sessions'
        db_table = 'messaging_chat_session'
        constraints = [
            models.UniqueConstraint(
                fields=['trainer', 'client', 'booking'],
                name='uniq_trainer_client_booking_chat',
            ),
        ]
        ordering = ['-updated_at']

    def __str__(self):
        return f'Chat: {self.client} ↔ {self.trainer} (Booking #{self.booking.id})'


class ChatMessage(models.Model):
    """
    Represents a single message in a chat session.
    Messages can only exist for confirmed bookings.
    """

    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_messages',
    )
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Chat Message'
        verbose_name_plural = 'Chat Messages'
        db_table = 'messaging_chat_message'
        ordering = ['timestamp']
        indexes = [
            models.Index(fields=['session', 'timestamp']),
            models.Index(fields=['sender']),
            models.Index(fields=['is_read']),
        ]

    def __str__(self):
        return f'Message from {self.sender.username} in {self.session}'
