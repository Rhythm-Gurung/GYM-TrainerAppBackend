"""
Client-facing scheduling endpoints.

GET  /api/trainers/{trainer_id}/available-slots/?date=YYYY-MM-DD
GET  /api/trainers/{trainer_id}/available-dates/?year=YYYY&month=M
POST /api/trainers/{trainer_id}/book/
GET  /api/bookings/
GET  /api/bookings/{booking_id}/
POST /api/bookings/{booking_id}/cancel/

Priority when resolving availability for a date:
  1. DateOverride   — trainer is fully blocked
  2. ScheduleOverride — custom schedule for that date range
  3. WeeklyScheduleDay — default recurring weekly schedule
"""

import calendar
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from scheduling.models import Booking, DateOverride, ScheduleOverride, TrainerScheduleScope, WeeklyScheduleDay
from scheduling.serializers.schedule import BookingCancelSerializer, BookingCreateSerializer
from system.serializers.users import MessageResponseSerializer

User = get_user_model()


def _js_dow(d):
    """Convert a Python date to JavaScript day-of-week convention (0=Sunday … 6=Saturday)."""
    return d.isoweekday() % 7


def _resolve_day(trainer, target_date):
    """
    Returns (is_available, session_mode, slots) for a date applying 3-tier priority.
    slots is a list of {'start_time': 'HH:MM', 'end_time': 'HH:MM'} dicts.
    """
    # 0. Scope check — date must fall within effective_from / effective_until
    try:
        scope = trainer.schedule_scope
        if target_date < scope.effective_from:
            return False, None, []
        if scope.effective_until and target_date > scope.effective_until:
            return False, None, []
    except TrainerScheduleScope.DoesNotExist:
        pass  # No scope set — fall through to schedule checks

    # 1. DateOverride — blocked entirely
    if DateOverride.objects.filter(user=trainer, date=target_date).exists():
        return False, None, []

    dow = _js_dow(target_date)

    # 2. ScheduleOverride — date falls within a custom date-range schedule
    so = ScheduleOverride.objects.filter(
        user=trainer,
        start_date__lte=target_date,
        end_date__gte=target_date,
    ).first()
    if so:
        day_data = next((d for d in so.schedule if d['day_of_week'] == dow), None)
        if day_data and day_data['enabled']:
            return True, day_data['session_mode'], day_data['slots']
        return False, (day_data['session_mode'] if day_data else None), []

    # 3. WeeklyScheduleDay — default recurring schedule
    try:
        day = WeeklyScheduleDay.objects.prefetch_related('slots').get(user=trainer, day_of_week=dow)
    except WeeklyScheduleDay.DoesNotExist:
        return False, None, []

    if not day.enabled:
        return False, day.session_mode, []

    slots = [
        {'start_time': s.start_time.strftime('%H:%M'), 'end_time': s.end_time.strftime('%H:%M')}
        for s in day.slots.all()
    ]
    return True, day.session_mode, slots


# ---------------------------------------------------------------------------
# Available slots for a specific date
# ---------------------------------------------------------------------------

@extend_schema(
    summary="Get Available Slots for a Date",
    parameters=[
        OpenApiParameter(name='date', description='Target date (YYYY-MM-DD)', required=True, type=str),
    ],
    responses={
        200: OpenApiResponse(description="Slots for the requested date"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Missing/invalid date"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Trainer not found"),
    },
    tags=["Client – Trainer Availability"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def available_slots_view(request, trainer_id):
    try:
        trainer = User.objects.get(id=trainer_id, is_trainer=True, is_active=True)
    except User.DoesNotExist:
        return Response({'status': False, 'message': 'Trainer not found.'}, status=status.HTTP_404_NOT_FOUND)

    date_param = request.query_params.get('date')
    if not date_param:
        return Response(
            {'status': False, 'message': 'Query parameter "date" is required (YYYY-MM-DD).'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        target_date = date.fromisoformat(date_param)
    except ValueError:
        return Response(
            {'status': False, 'message': 'Invalid date format. Use YYYY-MM-DD.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    is_available, session_mode, slots = _resolve_day(trainer, target_date)

    # Fetch already-booked start times (pending/accepted/confirmed count as taken)
    booked_starts = set(
        t.strftime('%H:%M') for t in
        Booking.objects.filter(
            trainer=trainer,
            date=target_date,
            status__in=[Booking.STATUS_PENDING, Booking.STATUS_ACCEPTED, Booking.STATUS_CONFIRMED],
        ).values_list('start_time', flat=True)
    )

    slots_out = [
        {**s, 'is_booked': s['start_time'] in booked_starts}
        for s in slots
    ]

    return Response(
        {
            'status': True,
            'data': {
                'date': date_param,
                'is_available': is_available,
                'session_mode': session_mode,
                'slots': slots_out,
            },
        },
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Available dates for a full month (calendar picker)
# ---------------------------------------------------------------------------

@extend_schema(
    summary="Get Available Dates in a Month",
    parameters=[
        OpenApiParameter(name='year',  description='Year (e.g. 2026)', required=True, type=int),
        OpenApiParameter(name='month', description='Month number 1–12', required=True, type=int),
    ],
    responses={
        200: OpenApiResponse(description="List of available date strings"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Missing/invalid params"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Trainer not found"),
    },
    tags=["Client – Trainer Availability"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def available_dates_view(request, trainer_id):
    try:
        trainer = User.objects.get(id=trainer_id, is_trainer=True, is_active=True)
    except User.DoesNotExist:
        return Response({'status': False, 'message': 'Trainer not found.'}, status=status.HTTP_404_NOT_FOUND)

    year_param  = request.query_params.get('year')
    month_param = request.query_params.get('month')
    if not year_param or not month_param:
        return Response(
            {'status': False, 'message': 'Query parameters "year" and "month" are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        year  = int(year_param)
        month = int(month_param)
        if not (1 <= month <= 12):
            raise ValueError
    except ValueError:
        return Response({'status': False, 'message': 'Invalid year or month.'}, status=status.HTTP_400_BAD_REQUEST)

    _, days_in_month = calendar.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end   = date(year, month, days_in_month)

    # Clip to trainer's schedule scope — dates outside it are never available
    try:
        scope = trainer.schedule_scope
        if month_end < scope.effective_from or (scope.effective_until and month_start > scope.effective_until):
            return Response({'status': True, 'data': {'available_dates': []}}, status=status.HTTP_200_OK)
        month_start = max(month_start, scope.effective_from)
        if scope.effective_until:
            month_end = min(month_end, scope.effective_until)
    except TrainerScheduleScope.DoesNotExist:
        pass  # No scope — use full month

    # Pre-fetch for the whole month — avoids per-day DB hits
    blocked_dates = set(
        DateOverride.objects.filter(user=trainer, date__year=year, date__month=month)
        .values_list('date', flat=True)
    )
    schedule_overrides = list(
        ScheduleOverride.objects.filter(
            user=trainer,
            start_date__lte=month_end,
            end_date__gte=month_start,
        )
    )
    enabled_dows = set(
        WeeklyScheduleDay.objects.filter(user=trainer, enabled=True).values_list('day_of_week', flat=True)
    )

    # Pre-fetch dates where ALL slots are booked (pending or confirmed)
    from django.db.models import Count
    booked_counts = dict(
        Booking.objects.filter(
            trainer=trainer,
            date__year=year,
            date__month=month,
            status__in=[Booking.STATUS_PENDING, Booking.STATUS_ACCEPTED, Booking.STATUS_CONFIRMED],
        ).values('date').annotate(n=Count('id')).values_list('date', 'n')
    )
    # Slot counts per day from WeeklyScheduleDay
    slot_counts_by_dow = dict(
        WeeklyScheduleDay.objects.filter(user=trainer, enabled=True)
        .annotate(n=Count('slots'))
        .values_list('day_of_week', 'n')
    )

    def _is_fully_booked(d, slot_count):
        """True if every slot on this date is already taken."""
        if slot_count == 0:
            return True
        return booked_counts.get(d, 0) >= slot_count

    def _override_for(d):
        for so in schedule_overrides:
            if so.start_date <= d <= so.end_date:
                return so
        return None

    available = []
    for day_num in range(month_start.day, month_end.day + 1):
        d = date(year, month, day_num)
        if d in blocked_dates:
            continue
        dow = _js_dow(d)
        so = _override_for(d)
        if so:
            day_data = next((x for x in so.schedule if x['day_of_week'] == dow), None)
            if day_data and day_data['enabled']:
                slot_count = len(day_data.get('slots', []))
                if not _is_fully_booked(d, slot_count):
                    available.append(d.strftime('%Y-%m-%d'))
        elif dow in enabled_dows:
            slot_count = slot_counts_by_dow.get(dow, 0)
            if not _is_fully_booked(d, slot_count):
                available.append(d.strftime('%Y-%m-%d'))

    return Response({'status': True, 'data': {'available_dates': available}}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _booking_to_dict(b):
    return {
        'id':           b.id,
        'trainer_id':   b.trainer_id,
        'trainer_name': b.trainer.full_name or b.trainer.email,
        'client_id':    b.client_id,
        'client_name':  b.client.full_name or b.client.email,
        'date':         b.date.strftime('%Y-%m-%d'),
        'start_time':   b.start_time.strftime('%H:%M'),
        'end_time':     b.end_time.strftime('%H:%M'),
        'session_mode': b.session_mode,
        'status':       b.status,
        'notes':        b.notes,
        'total_amount': str(b.total_amount),
        'cancelled_by': b.cancelled_by,
        'cancel_reason': b.cancel_reason,
        'created_at':   b.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Book a slot — POST /api/trainers/{trainer_id}/book/
# ---------------------------------------------------------------------------

@extend_schema(
    summary="Book a Trainer Slot",
    request=BookingCreateSerializer,
    responses={
        201: OpenApiResponse(description="Booking created (pending confirmation)"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Validation error"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Trainer not found"),
        409: OpenApiResponse(response=MessageResponseSerializer, description="Slot already booked"),
    },
    tags=["Client – Bookings"],
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def book_slot_view(request, trainer_id):
    client = request.user
    if client.is_trainer:
        return Response({'status': False, 'message': 'Trainers cannot book sessions.'}, status=status.HTTP_403_FORBIDDEN)

    try:
        trainer = User.objects.get(id=trainer_id, is_trainer=True, is_active=True, is_admin_approved=True)
    except User.DoesNotExist:
        return Response({'status': False, 'message': 'Trainer not found.'}, status=status.HTTP_404_NOT_FOUND)

    serializer = BookingCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    vd = serializer.validated_data

    # Verify the slot actually exists in the trainer's schedule for that date
    is_available, session_mode, slots = _resolve_day(trainer, vd['date'])
    if not is_available:
        return Response(
            {'status': False, 'message': 'Trainer is not available on this date.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    slot_match = next(
        (s for s in slots
         if s['start_time'] == vd['start_time'].strftime('%H:%M')
         and s['end_time'] == vd['end_time'].strftime('%H:%M')),
        None,
    )
    if slot_match is None:
        return Response(
            {'status': False, 'message': 'The requested time slot does not exist in the trainer\'s schedule.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Validate session_mode against trainer's offering for that day
    if session_mode != 'both' and vd['session_mode'] != session_mode:
        return Response(
            {'status': False, 'message': f'Trainer only offers {session_mode} sessions on this date.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # If there's already an active booking for this slot, return a conflict.
    # (Cancelled bookings should not block re-booking.)
    if Booking.objects.filter(
        trainer=trainer,
        date=vd['date'],
        start_time=vd['start_time'],
    ).exclude(status__in=[Booking.STATUS_CANCELLED, Booking.STATUS_REFUNDED]).exists():
        return Response(
            {'status': False, 'message': 'This slot has already been booked.'},
            status=status.HTTP_409_CONFLICT,
        )

    start_dt = datetime.combine(date.today(), vd['start_time'])
    end_dt   = datetime.combine(date.today(), vd['end_time'])
    duration_hours = (end_dt - start_dt).total_seconds() / 3600
    pricing = trainer.pricing_per_session or 0
    total_amount = round(float(pricing) * duration_hours, 2)

    try:
        booking = Booking.objects.create(
            trainer=trainer,
            client=client,
            date=vd['date'],
            start_time=vd['start_time'],
            end_time=vd['end_time'],
            session_mode=vd['session_mode'],
            notes=vd['notes'],
            status=Booking.STATUS_PENDING,
            total_amount=total_amount,
        )
    except IntegrityError:
        # Safety net for race conditions.
        return Response(
            {'status': False, 'message': 'This slot has already been booked.'},
            status=status.HTTP_409_CONFLICT,
        )

    return Response({'status': True, 'data': _booking_to_dict(booking)}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Client's own bookings — GET /api/bookings/
# ---------------------------------------------------------------------------

@extend_schema(
    summary="List My Bookings (Client)",
    parameters=[
        OpenApiParameter(name='status', description='Filter by status: pending/confirmed/cancelled/completed', required=False, type=str),
        OpenApiParameter(name='upcoming', description='Set to "true" to show only future bookings', required=False, type=str),
    ],
    responses={200: OpenApiResponse(description="List of bookings")},
    tags=["Client – Bookings"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def client_bookings_list_view(request):
    client = request.user
    if client.is_trainer:
        return Response({'status': False, 'message': 'Use the trainer bookings endpoint.'}, status=status.HTTP_403_FORBIDDEN)

    qs = Booking.objects.filter(client=client).select_related('trainer', 'client')

    status_param = request.query_params.get('status')
    if status_param:
        qs = qs.filter(status=status_param)

    if request.query_params.get('upcoming', '').lower() == 'true':
        qs = qs.filter(date__gte=date.today())

    return Response({'status': True, 'data': [_booking_to_dict(b) for b in qs]}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Single booking detail — GET /api/bookings/{booking_id}/
# ---------------------------------------------------------------------------

@extend_schema(
    summary="Get Booking Detail (Client)",
    responses={
        200: OpenApiResponse(description="Booking detail"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not found"),
    },
    tags=["Client – Bookings"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def client_booking_detail_view(request, booking_id):
    try:
        booking = Booking.objects.select_related('trainer', 'client').get(id=booking_id, client=request.user)
    except Booking.DoesNotExist:
        return Response({'status': False, 'message': 'Booking not found.'}, status=status.HTTP_404_NOT_FOUND)
    return Response({'status': True, 'data': _booking_to_dict(booking)}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Cancel a booking — POST /api/bookings/{booking_id}/cancel/
# ---------------------------------------------------------------------------

@extend_schema(
    summary="Cancel a Booking (Client)",
    request=BookingCancelSerializer,
    responses={
        200: OpenApiResponse(description="Booking cancelled"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Cannot cancel"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not found"),
    },
    tags=["Client – Bookings"],
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def client_cancel_booking_view(request, booking_id):
    try:
        booking = Booking.objects.select_related('trainer', 'client').get(id=booking_id, client=request.user)
    except Booking.DoesNotExist:
        return Response({'status': False, 'message': 'Booking not found.'}, status=status.HTTP_404_NOT_FOUND)

    cancellable = (Booking.STATUS_PENDING, Booking.STATUS_ACCEPTED, Booking.STATUS_CONFIRMED)
    if booking.status not in cancellable:
        return Response(
            {'status': False, 'message': f'Cannot cancel a booking that is {booking.status}.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = BookingCancelSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    booking.cancelled_by  = 'client'
    booking.cancel_reason = serializer.validated_data['reason']

    if booking.status == Booking.STATUS_CONFIRMED:
        # Payment was made — trigger refund flow
        _trigger_refund_on_cancel(booking)
        booking.status = Booking.STATUS_REFUND_PENDING
    else:
        # No payment involved (pending or accepted) — simple cancel
        booking.status = Booking.STATUS_CANCELLED

    booking.save(update_fields=['status', 'cancelled_by', 'cancel_reason', 'updated_at'])
    return Response({'status': True, 'data': _booking_to_dict(booking)}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Client booking stats — GET /api/bookings/stats/
# ---------------------------------------------------------------------------

@extend_schema(
    summary="Get Booking Stats (Client)",
    responses={200: OpenApiResponse(description="Booking stats for the authenticated client")},
    tags=["Client – Bookings"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def client_booking_stats_view(request):
    client = request.user
    qs = Booking.objects.filter(client=client)
    total_count = qs.count()
    completed_count = qs.filter(status=Booking.STATUS_COMPLETED).count()
    return Response(
        {'status': True, 'data': {'total_count': total_count, 'completed_count': completed_count}},
        status=status.HTTP_200_OK,
    )


def _trigger_refund_on_cancel(booking):
    """
    Called when a client cancels a confirmed booking.
    - Cancels the final_70 TrainerPayout (trainer keeps only the 25% advance)
    - Creates a ClientRefund record for 70% of the total paid
    """
    from payment.models import ClientRefund, TrainerPayout

    # Cancel the on-hold final payout — trainer does not receive it
    TrainerPayout.objects.filter(
        booking=booking,
        payout_type=TrainerPayout.TYPE_FINAL,
        status=TrainerPayout.STATUS_ON_HOLD,
    ).update(status=TrainerPayout.STATUS_CANCELLED)

    # Find the completed payment to link the refund
    completed_payment = booking.payments.filter(status='completed').first()
    if completed_payment and not hasattr(completed_payment, 'client_refund'):
        refund_amount = int(completed_payment.amount * 0.70)
        ClientRefund.objects.create(
            payment=completed_payment,
            amount=refund_amount,
            status=ClientRefund.STATUS_PENDING,
        )
