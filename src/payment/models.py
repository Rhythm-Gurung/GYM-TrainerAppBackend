from django.db import models
from django.contrib.auth import get_user_model
import uuid

from core.abstracts.models import DefaultModel

User = get_user_model()


def _payment_group_id():
    return f'pg_{uuid.uuid4().hex[:16]}'


class PaymentGroup(DefaultModel):
    STATUS_INITIATED = 'initiated'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED    = 'failed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_EXPIRED   = 'expired'

    STATUS_CHOICES = [
        (STATUS_INITIATED, 'Initiated'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED,    'Failed'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_EXPIRED,   'Expired'),
    ]

    payment_group_id = models.CharField(max_length=40, unique=True, default=_payment_group_id, editable=False)
    client           = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payment_groups')
    bookings         = models.ManyToManyField('scheduling.Booking', through='PaymentGroupBooking', related_name='payment_groups')
    pidx             = models.CharField(max_length=255, unique=True, blank=True, null=True)
    provider_reference = models.CharField(max_length=255, blank=True)
    total_amount     = models.PositiveBigIntegerField(help_text='Total amount in paisa across all selected bookings')
    status           = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_INITIATED)
    expires_at       = models.DateTimeField(null=True, blank=True)
    idempotency_key  = models.CharField(max_length=128, blank=True)
    khalti_response  = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name        = 'Payment Group'
        verbose_name_plural = 'Payment Groups'
        db_table            = 'payment_payment_group'
        ordering            = ['-created_at']

    def __str__(self):
        return f'{self.payment_group_id} ({self.status})'


class PaymentGroupBooking(DefaultModel):
    payment_group = models.ForeignKey(PaymentGroup, on_delete=models.CASCADE, related_name='booking_links')
    booking       = models.ForeignKey('scheduling.Booking', on_delete=models.CASCADE, related_name='payment_group_links')
    amount        = models.PositiveBigIntegerField(help_text='Per-booking amount in paisa', default=0)

    class Meta:
        verbose_name        = 'Payment Group Booking'
        verbose_name_plural = 'Payment Group Bookings'
        db_table            = 'payment_payment_group_booking'
        unique_together     = [('payment_group', 'booking')]
        ordering            = ['-created_at']

    def __str__(self):
        return f'{self.payment_group.payment_group_id} -> Booking #{self.booking_id}'


class KhaltiPayment(DefaultModel):
    STATUS_INITIATED = 'initiated'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED    = 'failed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_EXPIRED   = 'expired'
    STATUS_REFUNDED  = 'refunded'

    STATUS_CHOICES = [
        (STATUS_INITIATED, 'Initiated'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED,    'Failed'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_EXPIRED,   'Expired'),
        (STATUS_REFUNDED,  'Refunded'),
    ]

    booking          = models.ForeignKey(
        'scheduling.Booking',
        on_delete=models.CASCADE,
        related_name='payments',
    )
    pidx             = models.CharField(max_length=255, unique=True)
    transaction_id   = models.CharField(max_length=255, blank=True)
    amount           = models.PositiveBigIntegerField(help_text='Total amount in paisa')
    platform_fee     = models.PositiveBigIntegerField(default=0, help_text='5% platform fee in paisa (auto-set)')
    status           = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_INITIATED)
    khalti_response  = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name        = 'Khalti Payment'
        verbose_name_plural = 'Khalti Payments'
        db_table            = 'payment_khalti_payment'
        ordering            = ['-created_at']

    def __str__(self):
        return f'Payment #{self.id} – Booking #{self.booking_id} ({self.status})'


class TrainerPayout(DefaultModel):
    """
    Tracks each payout from the admin's Khalti account to a trainer.
    Admin transfers manually and marks done here.
    """
    TYPE_ADVANCE = 'advance_25'  # 25% — released immediately after payment confirmed
    TYPE_FINAL   = 'final_70'    # 70% — released after booking is completed

    STATUS_PENDING     = 'pending'      # ready for admin to transfer now
    STATUS_ON_HOLD     = 'on_hold'      # waiting for booking to complete
    STATUS_TRANSFERRED = 'transferred'  # admin has manually transferred
    STATUS_CANCELLED   = 'cancelled'    # booking cancelled before payout was made

    PAYOUT_TYPE_CHOICES = [
        (TYPE_ADVANCE, 'Advance 25%'),
        (TYPE_FINAL,   'Final 70%'),
    ]
    STATUS_CHOICES = [
        (STATUS_PENDING,     'Pending Transfer'),
        (STATUS_ON_HOLD,     'On Hold'),
        (STATUS_TRANSFERRED, 'Transferred'),
        (STATUS_CANCELLED,   'Cancelled'),
    ]

    booking            = models.ForeignKey(
        'scheduling.Booking',
        on_delete=models.CASCADE,
        related_name='trainer_payouts',
    )
    payout_type        = models.CharField(max_length=15, choices=PAYOUT_TYPE_CHOICES)
    amount             = models.PositiveBigIntegerField(help_text='Amount in paisa')
    status             = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_PENDING)
    transfer_reference = models.CharField(max_length=255, blank=True, help_text='Khalti P2P txn ID or bank ref')
    notes              = models.TextField(blank=True)
    transferred_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'Trainer Payout'
        verbose_name_plural = 'Trainer Payouts'
        db_table            = 'payment_trainer_payout'
        ordering            = ['-created_at']

    def __str__(self):
        return f'Payout #{self.id} – {self.get_payout_type_display()} – Booking #{self.booking_id} ({self.status})'

    @property
    def amount_rupees(self):
        return self.amount / 100


class ClientRefund(DefaultModel):
    """
    Tracks a 70% refund owed to a client when they cancel a confirmed booking.
    Admin processes manually and marks done here.
    """
    STATUS_PENDING   = 'pending'
    STATUS_PROCESSED = 'processed'

    STATUS_CHOICES = [
        (STATUS_PENDING,   'Pending Refund'),
        (STATUS_PROCESSED, 'Processed'),
    ]

    payment            = models.OneToOneField(
        KhaltiPayment,
        on_delete=models.CASCADE,
        related_name='client_refund',
    )
    amount             = models.PositiveBigIntegerField(help_text='Refund amount in paisa (70% of total)')
    status             = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_PENDING)
    refund_reference   = models.CharField(max_length=255, blank=True, help_text='Khalti refund txn ID or bank ref')
    notes              = models.TextField(blank=True)
    processed_at       = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'Client Refund'
        verbose_name_plural = 'Client Refunds'
        db_table            = 'payment_client_refund'
        ordering            = ['-created_at']

    def __str__(self):
        return f'Refund #{self.id} – Booking #{self.payment.booking_id} ({self.status})'

    @property
    def amount_rupees(self):
        return self.amount / 100
