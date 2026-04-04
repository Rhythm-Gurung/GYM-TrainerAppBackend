from datetime import datetime, timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class WeeklyScheduleDay(models.Model):
    """One row per day-of-week per trainer. day_of_week follows JS convention: 0=Sunday … 6=Saturday."""

    SESSION_MODE_CHOICES = [
        ('online',  'Online'),
        ('offline', 'Offline'),
        ('both',    'Both'),
    ]

    DAY_CHOICES = [(i, str(i)) for i in range(7)]

    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='schedule_days')
    day_of_week = models.IntegerField(choices=DAY_CHOICES)
    enabled     = models.BooleanField(default=False)
    session_mode = models.CharField(max_length=10, choices=SESSION_MODE_CHOICES, default='both')

    class Meta:
        verbose_name        = 'Weekly Schedule Day'
        verbose_name_plural = 'Weekly Schedule Days'
        db_table            = 'scheduling_weekly_schedule_day'
        unique_together     = [('user', 'day_of_week')]
        ordering            = ['day_of_week']

    def __str__(self):
        return f'{self.user} – day {self.day_of_week} ({"on" if self.enabled else "off"})'


class TimeSlot(models.Model):
    """A time window within a WeeklyScheduleDay."""

    day        = models.ForeignKey(WeeklyScheduleDay, on_delete=models.CASCADE, related_name='slots')
    start_time = models.TimeField()
    end_time   = models.TimeField()

    class Meta:
        verbose_name        = 'Time Slot'
        verbose_name_plural = 'Time Slots'
        db_table            = 'scheduling_time_slot'
        ordering            = ['start_time']

    def __str__(self):
        return f'{self.day} {self.start_time:%H:%M}–{self.end_time:%H:%M}'


class DateOverride(models.Model):
    """A specific calendar date on which the trainer is unavailable regardless of the weekly schedule."""

    user   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='date_overrides')
    date   = models.DateField()
    reason = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        verbose_name        = 'Date Override'
        verbose_name_plural = 'Date Overrides'
        db_table            = 'scheduling_date_override'
        unique_together     = [('user', 'date')]
        ordering            = ['date']

    def __str__(self):
        return f'{self.user} – blocked {self.date}'


class TrainerScheduleScope(models.Model):
    """
    Stores the effective date range for a trainer's recurring weekly schedule.
    One row per trainer — created/updated whenever the trainer saves their schedule.
    effective_until=None means the schedule runs indefinitely.
    """

    user           = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='schedule_scope',
    )
    effective_from  = models.DateField()
    effective_until = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = 'Trainer Schedule Scope'
        db_table     = 'scheduling_trainer_schedule_scope'

    def __str__(self):
        until = self.effective_until or 'forever'
        return f'{self.user} – {self.effective_from} → {until}'


class ScheduleOverride(models.Model):
    """
    A custom weekly schedule for a specific date range.
    Overrides the default recurring weekly schedule for any date that falls within [start_date, end_date].
    Priority: DateOverride > ScheduleOverride > WeeklyScheduleDay
    """

    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='schedule_overrides')
    start_date = models.DateField()
    end_date   = models.DateField()
    # Stores a 7-element list, same shape as the weekly schedule:
    # [{"day_of_week": 0, "enabled": false, "session_mode": "both", "slots": []}, ...]
    schedule   = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Schedule Override'
        verbose_name_plural = 'Schedule Overrides'
        db_table            = 'scheduling_schedule_override'
        ordering            = ['start_date']

    def __str__(self):
        return f'{self.user} – override {self.start_date} → {self.end_date}'


class Booking(models.Model):
    """
    A client's booking of a specific time slot on a specific date with a trainer.

    Status flow:
        pending   → confirmed  (trainer confirms)
        pending   → cancelled  (trainer or client cancels)
        confirmed → cancelled  (trainer or client cancels, must be before session)
        confirmed → completed  (auto or admin marks after session date passes)
    """

    STATUS_PENDING        = 'pending'
    STATUS_ACCEPTED       = 'accepted'        # trainer accepted — awaiting client payment
    STATUS_CONFIRMED      = 'confirmed'       # payment received — booking locked in
    STATUS_CANCELLED      = 'cancelled'
    STATUS_REFUND_PENDING = 'refund_pending'  # client cancelled after payment — refund queued
    STATUS_REFUNDED       = 'refunded'        # admin processed the refund
    STATUS_COMPLETED      = 'completed'
    STATUS_IN_PROGRESS    = 'in_progress'
    STATUS_DISPUTED       = 'disputed'
    STATUS_NO_SHOW_CLIENT = 'no_show_client'
    STATUS_END_NOT_CONFIRMED = 'session_was_taken_but_not_end_by_client'
    STATUS_MISSED         = 'missed'

    STATUS_CHOICES = [
        (STATUS_PENDING,        'Pending'),
        (STATUS_ACCEPTED,       'Accepted – Awaiting Payment'),
        (STATUS_CONFIRMED,      'Confirmed'),
        (STATUS_CANCELLED,      'Cancelled'),
        (STATUS_REFUND_PENDING, 'Refund Pending'),
        (STATUS_REFUNDED,       'Refunded'),
        (STATUS_COMPLETED,      'Completed'),
        (STATUS_IN_PROGRESS,    'In Progress'),
        (STATUS_DISPUTED,       'Disputed'),
        (STATUS_NO_SHOW_CLIENT, 'No Show (Client)'),
        (STATUS_END_NOT_CONFIRMED, 'Session Taken But End Not Confirmed By Client'),
        (STATUS_MISSED,         'Missed'),
    ]

    SESSION_MODE_CHOICES = [
        ('online',  'Online'),
        ('offline', 'Offline'),
    ]

    trainer      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='trainer_bookings',
    )
    client       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='client_bookings',
    )
    date         = models.DateField()
    start_time   = models.TimeField()
    end_time     = models.TimeField()
    session_mode = models.CharField(max_length=10, choices=SESSION_MODE_CHOICES, default='offline')
    status       = models.CharField(max_length=64, choices=STATUS_CHOICES, default=STATUS_PENDING)
    notes        = models.TextField(blank=True)
    total_amount  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cancelled_by = models.CharField(max_length=10, blank=True)  # 'trainer' | 'client'
    cancel_reason = models.TextField(blank=True)
    session_started_at = models.DateTimeField(null=True, blank=True)
    session_ended_at = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Booking'
        verbose_name_plural = 'Bookings'
        db_table            = 'scheduling_booking'
        constraints = [
            # Prevent double-booking the same slot, but allow re-booking if a prior
            # booking was cancelled.
            models.UniqueConstraint(
                fields=['trainer', 'date', 'start_time'],
                condition=~Q(status='cancelled'),
                name='uniq_active_booking_slot',
            ),
        ]
        ordering            = ['-date', '-start_time']
        indexes = [
            models.Index(fields=['-created_at'], name='bkg_created_idx'),
            models.Index(fields=['date'], name='bkg_date_idx'),
            models.Index(fields=['status'], name='bkg_status_idx'),
            models.Index(fields=['status', '-created_at'], name='bkg_status_cr_idx'),
            models.Index(fields=['trainer', '-created_at'], name='bkg_trainer_cr_idx'),
            models.Index(fields=['client', '-created_at'], name='bkg_client_cr_idx'),
        ]

    def __str__(self):
        return f'Booking #{self.id} – {self.client} → {self.trainer} on {self.date} {self.start_time:%H:%M} ({self.status})'

    @property
    def session_start_datetime(self):
        return timezone.make_aware(datetime.combine(self.date, self.start_time))

    @property
    def start_retry_deadline(self):
        # One retry is allowed only until the next day noon.
        return timezone.make_aware(datetime.combine(self.date + timedelta(days=1), datetime.min.time())).replace(hour=12)


class SessionVerificationRequest(models.Model):
    TYPE_START = 'start'
    TYPE_END = 'end'
    TYPE_CHOICES = [
        (TYPE_START, 'Start Session Request'),
        (TYPE_END, 'End Session Request'),
    ]

    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_REJECTED = 'rejected'
    STATUS_EXPIRED = 'expired'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_REJECTED, 'Rejected'),
        (STATUS_EXPIRED, 'Expired'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='verification_requests')
    request_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='session_verification_requests_sent',
    )
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='session_verification_requests_responded',
    )
    response_reason = models.TextField(blank=True)
    expires_at = models.DateTimeField()
    attempt_no = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Session Verification Request'
        verbose_name_plural = 'Session Verification Requests'
        db_table = 'scheduling_session_verification_request'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['booking', 'request_type', 'status']),
            models.Index(fields=['expires_at', 'status']),
        ]

    def __str__(self):
        return f'Booking #{self.booking_id} {self.request_type} request ({self.status})'
