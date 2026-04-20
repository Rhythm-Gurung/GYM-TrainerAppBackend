from datetime import datetime

from django.db import transaction
from django.utils import timezone

from notification.models import Notification
from scheduling.models import Booking, SessionVerificationRequest
from scheduling.services.booking_expiry import is_booking_expired


TERMINAL_BOOKING_STATUSES = {
    Booking.STATUS_CANCELLED,
    Booking.STATUS_REFUNDED,
    Booking.STATUS_COMPLETED,
}


def _booking_end_datetime(booking: Booking):
    return timezone.make_aware(datetime.combine(booking.date, booking.end_time))


def create_booking_notification(user, title: str, message: str):
    Notification.objects.create(
        user=user,
        type=Notification.TYPE_BOOKING,
        title=title,
        message=message,
    )


@transaction.atomic
def refresh_booking_verification_state(booking: Booking, now=None):
    now = now or timezone.now()
    booking = Booking.objects.select_for_update().get(pk=booking.pk)

    if booking.status in TERMINAL_BOOKING_STATUSES:
        return booking

    pending_qs = booking.verification_requests.filter(
        status=SessionVerificationRequest.STATUS_PENDING,
        expires_at__lte=now,
    )

    from payment.services import sync_payout_on_booking_status

    expired_any = False
    for req in pending_qs:
        req.status = SessionVerificationRequest.STATUS_EXPIRED
        req.save(update_fields=['status', 'updated_at'])
        expired_any = True
        if req.request_type == SessionVerificationRequest.TYPE_START:
            if not booking.verification_requests.filter(
                request_type=SessionVerificationRequest.TYPE_START,
                status=SessionVerificationRequest.STATUS_ACCEPTED,
            ).exists():
                booking.status = Booking.STATUS_NO_SHOW_CLIENT
        elif req.request_type == SessionVerificationRequest.TYPE_END:
            booking.status = Booking.STATUS_END_NOT_CONFIRMED

        create_booking_notification(
            user=booking.trainer,
            title='Session verification request expired',
            message=f'The {req.request_type} request for booking #{booking.id} expired.',
        )
        create_booking_notification(
            user=booking.client,
            title='Session verification request expired',
            message=f'The {req.request_type} request for booking #{booking.id} expired due to no response.',
        )

    if expired_any:
        booking.save(update_fields=['status', 'updated_at'])
        sync_payout_on_booking_status(booking, booking.status)

    # Expired booking rule: pending/accepted bookings that have passed session start time
    if booking.status in [Booking.STATUS_PENDING, Booking.STATUS_ACCEPTED]:
        if is_booking_expired(booking, now):
            booking.status = Booking.STATUS_MISSED
            booking.save(update_fields=['status', 'updated_at'])
            sync_payout_on_booking_status(booking, Booking.STATUS_MISSED)
            create_booking_notification(
                user=booking.trainer,
                title='Booking expired',
                message=f'Booking #{booking.id} expired because the session start time has passed without acceptance/payment.',
            )
            create_booking_notification(
                user=booking.client,
                title='Booking expired',
                message=f'Booking #{booking.id} expired because the session start time has passed without acceptance/payment.',
            )

    # Missed-day rule: when the booked day is over and no start was accepted, mark missed.
    if booking.status not in TERMINAL_BOOKING_STATUSES and booking.status != Booking.STATUS_MISSED:
        if now.date() > booking.date:
            accepted_start_exists = booking.verification_requests.filter(
                request_type=SessionVerificationRequest.TYPE_START,
                status=SessionVerificationRequest.STATUS_ACCEPTED,
            ).exists()
            if not accepted_start_exists:
                booking.status = Booking.STATUS_MISSED
                booking.save(update_fields=['status', 'updated_at'])
                sync_payout_on_booking_status(booking, Booking.STATUS_MISSED)
                create_booking_notification(
                    user=booking.trainer,
                    title='Booking marked missed',
                    message=f'Booking #{booking.id} was marked missed because no start request was accepted on the booked day.',
                )
                create_booking_notification(
                    user=booking.client,
                    title='Booking marked missed',
                    message=f'Booking #{booking.id} was marked missed because no start request was accepted on the booked day.',
                )

    # Safety rule for same-day requests after session has ended.
    if now > _booking_end_datetime(booking) and booking.status == Booking.STATUS_CONFIRMED:
        accepted_start_exists = booking.verification_requests.filter(
            request_type=SessionVerificationRequest.TYPE_START,
            status=SessionVerificationRequest.STATUS_ACCEPTED,
        ).exists()
        if not accepted_start_exists:
            booking.status = Booking.STATUS_MISSED
            booking.save(update_fields=['status', 'updated_at'])
            sync_payout_on_booking_status(booking, Booking.STATUS_MISSED)
            create_booking_notification(
                user=booking.trainer,
                title='Booking marked missed',
                message=f'Booking #{booking.id} was marked missed because no start request was accepted on the booked day.',
            )
            create_booking_notification(
                user=booking.client,
                title='Booking marked missed',
                message=f'Booking #{booking.id} was marked missed because no start request was accepted on the booked day.',
            )

    return booking
