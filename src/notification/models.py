from django.conf import settings
from django.db import models

from core.abstracts.models import DefaultModel


class Notification(DefaultModel):
    TYPE_BOOKING = 'booking'
    TYPE_PAYMENT = 'payment'
    TYPE_REVIEW = 'review'
    TYPE_SYSTEM = 'system'

    TYPE_CHOICES = [
        (TYPE_BOOKING, 'Booking'),
        (TYPE_PAYMENT, 'Payment'),
        (TYPE_REVIEW, 'Review'),
        (TYPE_SYSTEM, 'System'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    title = models.CharField(max_length=255)
    message = models.TextField()
    is_read = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Notification'
        verbose_name_plural = 'Notifications'
        db_table = 'notification_notification'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read', '-created_at']),
            models.Index(fields=['user', 'type', '-created_at']),
        ]

    def __str__(self):
        return f'Notification #{self.id} – {self.user} ({self.type})'

