from payment.models import TrainerPayout


def sync_payout_on_booking_status(booking, new_status):
    """
    Called whenever a booking transitions to a terminal or payout-relevant status.
    Updates TrainerPayout records to reflect the outcome of the booking.
    """
    from scheduling.models import Booking

    if new_status in (Booking.STATUS_COMPLETED, Booking.STATUS_END_NOT_CONFIRMED):
        # Session completed (or end not confirmed by client) — trainer earns the final 70%
        TrainerPayout.objects.filter(
            booking=booking,
            payout_type=TrainerPayout.TYPE_FINAL,
            status=TrainerPayout.STATUS_ON_HOLD,
        ).update(status=TrainerPayout.STATUS_PENDING)

    elif new_status == Booking.STATUS_NO_SHOW_CLIENT:
        # Client no-show — trainer keeps advance (25%), loses final payout
        TrainerPayout.objects.filter(
            booking=booking,
            payout_type=TrainerPayout.TYPE_FINAL,
            status=TrainerPayout.STATUS_ON_HOLD,
        ).update(status=TrainerPayout.STATUS_CANCELLED)

    elif new_status in (Booking.STATUS_MISSED, Booking.STATUS_CANCELLED):
        # No session took place — cancel all outstanding payouts
        TrainerPayout.objects.filter(
            booking=booking,
            status__in=[TrainerPayout.STATUS_ON_HOLD, TrainerPayout.STATUS_PENDING],
        ).update(status=TrainerPayout.STATUS_CANCELLED)
