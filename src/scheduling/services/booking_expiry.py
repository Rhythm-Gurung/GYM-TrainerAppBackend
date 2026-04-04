"""
Booking expiry validation service.
Checks if bookings have passed their session time and should no longer be actionable.
"""
from datetime import datetime

from django.utils import timezone

from scheduling.models import Booking


def _get_session_start_datetime(booking: Booking):
    """Get timezone-aware datetime when the session starts."""
    return timezone.make_aware(datetime.combine(booking.date, booking.start_time))


def _get_session_end_datetime(booking: Booking):
    """Get timezone-aware datetime when the session ends."""
    return timezone.make_aware(datetime.combine(booking.date, booking.end_time))


def is_booking_expired(booking: Booking, now=None) -> bool:
    """
    Check if a booking's session time has already passed.
    
    A booking is considered expired if the current time is past the session start time.
    This prevents accepting or paying for bookings after the session was supposed to begin.
    
    Args:
        booking: The booking to check
        now: Current time (defaults to timezone.now())
    
    Returns:
        True if the session start time has passed, False otherwise
    """
    if now is None:
        now = timezone.now()
    
    session_start = _get_session_start_datetime(booking)
    return now >= session_start


def is_booking_completely_passed(booking: Booking, now=None) -> bool:
    """
    Check if a booking's entire session time window has passed.
    
    Args:
        booking: The booking to check
        now: Current time (defaults to timezone.now())
    
    Returns:
        True if the session end time has passed, False otherwise
    """
    if now is None:
        now = timezone.now()
    
    session_end = _get_session_end_datetime(booking)
    return now >= session_end


def can_accept_booking(booking: Booking, now=None) -> tuple[bool, str | None]:
    """
    Check if a trainer can accept a pending booking.
    
    Args:
        booking: The booking to check
        now: Current time (defaults to timezone.now())
    
    Returns:
        Tuple of (can_accept: bool, error_message: str | None)
    """
    if booking.status != Booking.STATUS_PENDING:
        return False, f"Only pending bookings can be accepted. Current status: {booking.status}"
    
    if is_booking_expired(booking, now):
        session_start = _get_session_start_datetime(booking)
        return False, f"This booking has expired. The session was scheduled to start at {session_start.strftime('%Y-%m-%d %I:%M %p')}."
    
    return True, None


def can_pay_for_booking(booking: Booking, now=None) -> tuple[bool, str | None]:
    """
    Check if a client can pay for an accepted booking.
    
    Args:
        booking: The booking to check
        now: Current time (defaults to timezone.now())
    
    Returns:
        Tuple of (can_pay: bool, error_message: str | None)
    """
    if booking.status != Booking.STATUS_ACCEPTED:
        return False, f"Payment can only be made for accepted bookings. Current status: {booking.status}"
    
    if is_booking_expired(booking, now):
        session_start = _get_session_start_datetime(booking)
        return False, f"This booking has expired. The session was scheduled to start at {session_start.strftime('%Y-%m-%d %I:%M %p')}."
    
    return True, None


def get_expiry_info(booking: Booking, now=None) -> dict:
    """
    Get expiry information for a booking.
    
    Returns:
        Dictionary with expiry details:
        - is_expired: bool
        - session_start_time: datetime
        - session_end_time: datetime
        - time_until_start: timedelta (None if expired)
        - time_until_end: timedelta (None if completely passed)
    """
    if now is None:
        now = timezone.now()
    
    session_start = _get_session_start_datetime(booking)
    session_end = _get_session_end_datetime(booking)
    
    is_expired = now >= session_start
    is_completely_passed = now >= session_end
    
    return {
        'is_expired': is_expired,
        'is_completely_passed': is_completely_passed,
        'session_start_time': session_start,
        'session_end_time': session_end,
        'time_until_start': None if is_expired else (session_start - now),
        'time_until_end': None if is_completely_passed else (session_end - now),
    }
