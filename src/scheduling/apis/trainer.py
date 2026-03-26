"""
Trainer-facing scheduling endpoints.

PUT  /api/trainer/schedule/                          — replace full 7-day schedule
GET  /api/trainer/schedule/                          — read weekly schedule
GET  /api/trainer/availability/overrides/            — list blocked dates (?month=YYYY-MM)
POST /api/trainer/availability/overrides/            — block a date (409 if bookings exist)
DELETE /api/trainer/availability/overrides/{id}/     — unblock a date
PATCH  /api/trainer/availability/overrides/{id}/     — update reason text
GET  /api/trainer/schedule-overrides/                — list date-range schedule overrides
POST /api/trainer/schedule-overrides/                — create date-range schedule override
GET  /api/trainer/schedule-overrides/{id}/           — get single override
PUT  /api/trainer/schedule-overrides/{id}/           — update override
DELETE /api/trainer/schedule-overrides/{id}/         — delete override
GET  /api/trainer/bookings/                          — list incoming bookings
GET  /api/trainer/bookings/{id}/                     — booking detail
POST /api/trainer/bookings/{id}/confirm/             — confirm a pending booking
POST /api/trainer/bookings/{id}/cancel/              — cancel a booking
"""

import calendar
from datetime import date

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from scheduling.models import Booking, DateOverride, ScheduleOverride, TimeSlot, TrainerScheduleScope, WeeklyScheduleDay
from scheduling.serializers.schedule import (
    BookingCancelSerializer,
    DateOverrideResponseSerializer,
    DateOverrideSerializer,
    PatchOverrideReasonSerializer,
    ScheduleOverrideInputSerializer,
    WeeklyScheduleInputSerializer,
)
from system.serializers.users import MessageResponseSerializer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_SCHEDULE = [
    {'day_of_week': i, 'enabled': False, 'session_mode': 'both', 'slots': []}
    for i in range(7)
]


def _build_schedule_response(user):
    days = WeeklyScheduleDay.objects.filter(user=user).prefetch_related('slots')
    day_map = {d.day_of_week: d for d in days}

    schedule_data = []
    for i in range(7):
        if i in day_map:
            day = day_map[i]
            schedule_data.append({
                'day_of_week': day.day_of_week,
                'enabled': day.enabled,
                'session_mode': day.session_mode,
                'slots': [
                    {'start_time': s.start_time.strftime('%H:%M'), 'end_time': s.end_time.strftime('%H:%M')}
                    for s in day.slots.all()
                ],
            })
        else:
            schedule_data.append({'day_of_week': i, 'enabled': False, 'session_mode': 'both', 'slots': []})

    try:
        scope = user.schedule_scope
        effective_from  = scope.effective_from.strftime('%Y-%m-%d')
        effective_until = scope.effective_until.strftime('%Y-%m-%d') if scope.effective_until else None
    except TrainerScheduleScope.DoesNotExist:
        effective_from  = None
        effective_until = None

    return {
        'data': schedule_data,
        'effective_from': effective_from,
        'effective_until': effective_until,
    }


# ---------------------------------------------------------------------------
# Weekly Schedule — GET / PUT
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="Get Trainer Weekly Schedule",
    responses={
        200: OpenApiResponse(description="7-day weekly schedule"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
    },
    tags=["Trainer Schedule"],
)
@extend_schema(
    methods=['PUT'],
    summary="Replace Trainer Weekly Schedule",
    request=WeeklyScheduleInputSerializer,
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description="Saved"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Validation error"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
    },
    tags=["Trainer Schedule"],
)
@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def schedule_view(request):
    user = request.user
    if not user.is_trainer:
        return Response(
            {'status': False, 'message': 'Only trainers can access this.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == 'GET':
        schedule = _build_schedule_response(user)
        return Response({'status': True, **schedule}, status=status.HTTP_200_OK)

    # PUT — replace entire schedule
    serializer = WeeklyScheduleInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    vd = serializer.validated_data

    # Delete existing days (cascades to slots)
    WeeklyScheduleDay.objects.filter(user=user).delete()

    for day_data in vd['schedule']:
        day = WeeklyScheduleDay.objects.create(
            user=user,
            day_of_week=day_data['day_of_week'],
            enabled=day_data['enabled'],
            session_mode=day_data['session_mode'],
        )
        TimeSlot.objects.bulk_create([
            TimeSlot(day=day, start_time=slot['start_time'], end_time=slot['end_time'])
            for slot in day_data['slots']
        ])

    # Upsert scope
    TrainerScheduleScope.objects.update_or_create(
        user=user,
        defaults={
            'effective_from':  vd['effective_from'],
            'effective_until': vd['effective_until'],
        },
    )

    return Response({'status': True, 'detail': 'Schedule saved successfully.'}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Date Overrides — GET list / POST
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="List Blocked Dates",
    parameters=[
        OpenApiParameter(
            name='month',
            description='Filter by month, format YYYY-MM (e.g. 2026-03)',
            required=False,
            type=str,
        ),
    ],
    responses={
        200: OpenApiResponse(description="List of date overrides"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
    },
    tags=["Trainer Schedule"],
)
@extend_schema(
    methods=['POST'],
    summary="Block a Date",
    request=DateOverrideSerializer,
    responses={
        201: OpenApiResponse(response=DateOverrideResponseSerializer, description="Created"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Duplicate date"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
        409: OpenApiResponse(response=MessageResponseSerializer, description="Confirmed bookings exist on this date"),
    },
    tags=["Trainer Schedule"],
)
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def overrides_list_view(request):
    user = request.user
    if not user.is_trainer:
        return Response(
            {'status': False, 'message': 'Only trainers can access this.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == 'GET':
        qs = DateOverride.objects.filter(user=user)

        month_param = request.query_params.get('month')
        if month_param:
            try:
                year, month = month_param.split('-')
                year, month = int(year), int(month)
                _, last_day = calendar.monthrange(year, month)
                qs = qs.filter(date__year=year, date__month=month)
            except (ValueError, AttributeError):
                return Response(
                    {'status': False, 'message': 'Invalid month format. Use YYYY-MM.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        data = list(qs.values('id', 'date', 'reason'))
        for row in data:
            row['date'] = row['date'].strftime('%Y-%m-%d')
        return Response({'status': True, 'data': data}, status=status.HTTP_200_OK)

    # POST — block a date
    serializer = DateOverrideSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    target_date = serializer.validated_data['date']
    reason = serializer.validated_data.get('reason')

    # Duplicate check
    if DateOverride.objects.filter(user=user, date=target_date).exists():
        return Response(
            {'status': False, 'detail': 'This date is already blocked.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Booking conflict check — cannot block a date with confirmed bookings
    confirmed_bookings = Booking.objects.filter(
        trainer=user, date=target_date, status=Booking.STATUS_CONFIRMED,
    )
    if confirmed_bookings.exists():
        return Response(
            {
                'status': False,
                'detail': f'You have {confirmed_bookings.count()} confirmed booking(s) on this date. Cancel them before blocking the date.',
            },
            status=status.HTTP_409_CONFLICT,
        )

    override = DateOverride.objects.create(user=user, date=target_date, reason=reason)
    return Response(
        {
            'status': True,
            'data': {
                'id': override.id,
                'date': override.date.strftime('%Y-%m-%d'),
                'reason': override.reason,
            },
        },
        status=status.HTTP_201_CREATED,
    )


# ---------------------------------------------------------------------------
# Date Override detail — DELETE / PATCH
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['DELETE'],
    summary="Unblock a Date",
    responses={
        204: OpenApiResponse(description="Deleted"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not found"),
    },
    tags=["Trainer Schedule"],
)
@extend_schema(
    methods=['PATCH'],
    summary="Update Override Reason",
    request=PatchOverrideReasonSerializer,
    responses={
        200: OpenApiResponse(response=DateOverrideResponseSerializer, description="Updated"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not found"),
    },
    tags=["Trainer Schedule"],
)
@api_view(['DELETE', 'PATCH'])
@permission_classes([IsAuthenticated])
def override_detail_view(request, override_id):
    user = request.user
    if not user.is_trainer:
        return Response(
            {'status': False, 'message': 'Only trainers can access this.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        override = DateOverride.objects.get(id=override_id, user=user)
    except DateOverride.DoesNotExist:
        return Response(
            {'status': False, 'detail': 'Override not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == 'DELETE':
        override.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # PATCH — update reason only
    serializer = PatchOverrideReasonSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    override.reason = serializer.validated_data['reason']
    override.save(update_fields=['reason'])
    return Response(
        {
            'status': True,
            'data': {
                'id': override.id,
                'date': override.date.strftime('%Y-%m-%d'),
                'reason': override.reason,
            },
        },
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Helpers — Schedule Override
# ---------------------------------------------------------------------------

def _serialize_schedule_for_storage(schedule_data):
    """Convert validated schedule data (contains datetime.time objects) to JSON-safe dicts."""
    return [
        {
            'day_of_week': day['day_of_week'],
            'enabled': day['enabled'],
            'session_mode': day['session_mode'],
            'slots': [
                {'start_time': s['start_time'].strftime('%H:%M'), 'end_time': s['end_time'].strftime('%H:%M')}
                for s in day['slots']
            ],
        }
        for day in schedule_data
    ]


def _schedule_override_to_dict(so):
    return {
        'id': so.id,
        'trainer_id': so.user_id,
        'start_date': so.start_date.strftime('%Y-%m-%d'),
        'end_date': so.end_date.strftime('%Y-%m-%d'),
        'schedule': so.schedule,
        'created_at': so.created_at.isoformat(),
        'updated_at': so.updated_at.isoformat(),
    }


def _check_overlap(user, start_date, end_date, exclude_id=None):
    """Returns the first overlapping ScheduleOverride or None."""
    qs = ScheduleOverride.objects.filter(
        user=user,
        start_date__lte=end_date,
        end_date__gte=start_date,
    )
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    return qs.first()


# ---------------------------------------------------------------------------
# Schedule Overrides — GET list / POST create
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['GET'],
    summary="List Date-Range Schedule Overrides",
    parameters=[
        OpenApiParameter(name='month',      description='Filter by month YYYY-MM',         required=False, type=str),
        OpenApiParameter(name='start_date', description='Filter: overlaps on/after (YYYY-MM-DD)', required=False, type=str),
        OpenApiParameter(name='end_date',   description='Filter: overlaps on/before (YYYY-MM-DD)', required=False, type=str),
    ],
    responses={200: OpenApiResponse(description="List of schedule overrides")},
    tags=["Trainer Schedule"],
)
@extend_schema(
    methods=['POST'],
    summary="Create Date-Range Schedule Override",
    request=ScheduleOverrideInputSerializer,
    responses={
        201: OpenApiResponse(description="Created schedule override"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Validation error"),
        409: OpenApiResponse(response=MessageResponseSerializer, description="Date range overlaps existing override"),
    },
    tags=["Trainer Schedule"],
)
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def schedule_overrides_list_view(request):
    user = request.user
    if not user.is_trainer:
        return Response({'status': False, 'message': 'Only trainers can access this.'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        qs = ScheduleOverride.objects.filter(user=user)

        month_param = request.query_params.get('month')
        start_param = request.query_params.get('start_date')
        end_param   = request.query_params.get('end_date')

        if month_param:
            try:
                yr, mo = month_param.split('-')
                yr, mo = int(yr), int(mo)
                _, last_day = calendar.monthrange(yr, mo)
                month_start = date(yr, mo, 1)
                month_end   = date(yr, mo, last_day)
                qs = qs.filter(start_date__lte=month_end, end_date__gte=month_start)
            except (ValueError, AttributeError):
                return Response(
                    {'status': False, 'message': 'Invalid month format. Use YYYY-MM.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            if start_param:
                try:
                    qs = qs.filter(end_date__gte=date.fromisoformat(start_param))
                except ValueError:
                    return Response({'status': False, 'message': 'Invalid start_date.'}, status=status.HTTP_400_BAD_REQUEST)
            if end_param:
                try:
                    qs = qs.filter(start_date__lte=date.fromisoformat(end_param))
                except ValueError:
                    return Response({'status': False, 'message': 'Invalid end_date.'}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'status': True, 'data': [_schedule_override_to_dict(so) for so in qs]}, status=status.HTTP_200_OK)

    # POST — create
    serializer = ScheduleOverrideInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    vd = serializer.validated_data
    conflict = _check_overlap(user, vd['start_date'], vd['end_date'])
    if conflict:
        return Response(
            {'status': False, 'detail': 'Date range overlaps with existing override.', 'existing_override_id': conflict.id},
            status=status.HTTP_409_CONFLICT,
        )

    so = ScheduleOverride.objects.create(
        user=user,
        start_date=vd['start_date'],
        end_date=vd['end_date'],
        schedule=_serialize_schedule_for_storage(vd['schedule']),
    )
    return Response({'status': True, 'data': _schedule_override_to_dict(so)}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Schedule Overrides — PUT update / DELETE
# ---------------------------------------------------------------------------

@extend_schema(
    methods=['PUT'],
    summary="Update Date-Range Schedule Override",
    request=ScheduleOverrideInputSerializer,
    responses={
        200: OpenApiResponse(description="Updated schedule override"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Validation error"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not found"),
        409: OpenApiResponse(response=MessageResponseSerializer, description="Overlap conflict"),
    },
    tags=["Trainer Schedule"],
)
@extend_schema(
    methods=['DELETE'],
    summary="Delete Date-Range Schedule Override",
    responses={
        204: OpenApiResponse(description="Deleted"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not found"),
    },
    tags=["Trainer Schedule"],
)
@api_view(['PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def schedule_override_detail_view(request, override_id):
    user = request.user
    if not user.is_trainer:
        return Response({'status': False, 'message': 'Only trainers can access this.'}, status=status.HTTP_403_FORBIDDEN)

    try:
        so = ScheduleOverride.objects.get(id=override_id, user=user)
    except ScheduleOverride.DoesNotExist:
        return Response({'status': False, 'detail': 'Schedule override not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        so.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # PUT — update
    if so.start_date < date.today():
        return Response(
            {'status': False, 'detail': 'Cannot update a past schedule override.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = ScheduleOverrideInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    vd = serializer.validated_data
    conflict = _check_overlap(user, vd['start_date'], vd['end_date'], exclude_id=so.id)
    if conflict:
        return Response(
            {'status': False, 'detail': 'Date range overlaps with existing override.', 'existing_override_id': conflict.id},
            status=status.HTTP_409_CONFLICT,
        )

    so.start_date = vd['start_date']
    so.end_date   = vd['end_date']
    so.schedule   = _serialize_schedule_for_storage(vd['schedule'])
    so.save(update_fields=['start_date', 'end_date', 'schedule', 'updated_at'])
    return Response({'status': True, 'data': _schedule_override_to_dict(so)}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Trainer Bookings — helpers
# ---------------------------------------------------------------------------

def _booking_to_dict(b):
    return {
        'id':            b.id,
        'trainer_id':    b.trainer_id,
        'trainer_name':  b.trainer.full_name or b.trainer.email,
        'client_id':     b.client_id,
        'client_name':   b.client.full_name or b.client.email,
        'date':          b.date.strftime('%Y-%m-%d'),
        'start_time':    b.start_time.strftime('%H:%M'),
        'end_time':      b.end_time.strftime('%H:%M'),
        'session_mode':  b.session_mode,
        'status':        b.status,
        'notes':         b.notes,
        'total_amount':  str(b.total_amount),
        'cancelled_by':  b.cancelled_by,
        'cancel_reason': b.cancel_reason,
        'created_at':    b.created_at.isoformat(),
    }


def _trainer_only(user):
    if not user.is_trainer:
        return Response({'status': False, 'message': 'Only trainers can access this.'}, status=status.HTTP_403_FORBIDDEN)
    return None


# ---------------------------------------------------------------------------
# Trainer: list bookings — GET /api/trainer/bookings/
# ---------------------------------------------------------------------------

@extend_schema(
    summary="List Trainer's Bookings",
    parameters=[
        OpenApiParameter(name='status', description='pending/confirmed/cancelled/completed', required=False, type=str),
        OpenApiParameter(name='date',   description='Filter by date YYYY-MM-DD',             required=False, type=str),
        OpenApiParameter(name='upcoming', description='"true" to show only future bookings', required=False, type=str),
    ],
    responses={200: OpenApiResponse(description="List of bookings")},
    tags=["Trainer Schedule"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trainer_bookings_list_view(request):
    err = _trainer_only(request.user)
    if err:
        return err

    qs = Booking.objects.filter(trainer=request.user).select_related('trainer', 'client')

    status_param = request.query_params.get('status')
    if status_param:
        qs = qs.filter(status=status_param)

    date_param = request.query_params.get('date')
    if date_param:
        try:
            qs = qs.filter(date=date.fromisoformat(date_param))
        except ValueError:
            return Response({'status': False, 'message': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

    if request.query_params.get('upcoming', '').lower() == 'true':
        qs = qs.filter(date__gte=date.today())

    return Response({'status': True, 'data': [_booking_to_dict(b) for b in qs]}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Trainer: booking detail — GET /api/trainer/bookings/{id}/
# ---------------------------------------------------------------------------

@extend_schema(
    summary="Get Booking Detail (Trainer)",
    responses={
        200: OpenApiResponse(description="Booking detail"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not found"),
    },
    tags=["Trainer Schedule"],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trainer_booking_detail_view(request, booking_id):
    err = _trainer_only(request.user)
    if err:
        return err

    try:
        booking = Booking.objects.select_related('trainer', 'client').get(id=booking_id, trainer=request.user)
    except Booking.DoesNotExist:
        return Response({'status': False, 'message': 'Booking not found.'}, status=status.HTTP_404_NOT_FOUND)

    return Response({'status': True, 'data': _booking_to_dict(booking)}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Trainer: confirm booking — POST /api/trainer/bookings/{id}/confirm/
# ---------------------------------------------------------------------------

@extend_schema(
    summary="Accept a Booking (Trainer)",
    responses={
        200: OpenApiResponse(description="Booking accepted — client will be prompted to pay"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Invalid state"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not found"),
    },
    tags=["Trainer Schedule"],
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trainer_confirm_booking_view(request, booking_id):
    """
    Trainer accepts a pending booking request.
    Status: pending → accepted
    The client is then expected to complete payment, after which the booking becomes confirmed.
    """
    err = _trainer_only(request.user)
    if err:
        return err

    try:
        booking = Booking.objects.select_related('trainer', 'client').get(id=booking_id, trainer=request.user)
    except Booking.DoesNotExist:
        return Response({'status': False, 'message': 'Booking not found.'}, status=status.HTTP_404_NOT_FOUND)

    if booking.status != Booking.STATUS_PENDING:
        return Response(
            {'status': False, 'message': f'Only pending bookings can be accepted. Current status: {booking.status}.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    booking.status = Booking.STATUS_ACCEPTED
    booking.save(update_fields=['status', 'updated_at'])
    return Response({'status': True, 'data': _booking_to_dict(booking)}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Trainer: cancel booking — POST /api/trainer/bookings/{id}/cancel/
# ---------------------------------------------------------------------------

@extend_schema(
    summary="Cancel a Booking (Trainer)",
    request=BookingCancelSerializer,
    responses={
        200: OpenApiResponse(description="Booking cancelled"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Cannot cancel"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not found"),
    },
    tags=["Trainer Schedule"],
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trainer_cancel_booking_view(request, booking_id):
    err = _trainer_only(request.user)
    if err:
        return err

    try:
        booking = Booking.objects.select_related('trainer', 'client').get(id=booking_id, trainer=request.user)
    except Booking.DoesNotExist:
        return Response({'status': False, 'message': 'Booking not found.'}, status=status.HTTP_404_NOT_FOUND)

    if booking.status not in (Booking.STATUS_PENDING, Booking.STATUS_CONFIRMED):
        return Response(
            {'status': False, 'message': f'Cannot cancel a booking that is already {booking.status}.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = BookingCancelSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    booking.status        = Booking.STATUS_CANCELLED
    booking.cancelled_by  = 'trainer'
    booking.cancel_reason = serializer.validated_data['reason']
    booking.save(update_fields=['status', 'cancelled_by', 'cancel_reason', 'updated_at'])
    return Response({'status': True, 'data': _booking_to_dict(booking)}, status=status.HTTP_200_OK)
