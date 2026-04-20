from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from scheduling.models import Booking, SessionVerificationRequest
from scheduling.serializers.schedule import (
    SessionVerificationRequestCreateSerializer,
    SessionVerificationRespondSerializer,
)
from scheduling.services import create_booking_notification, refresh_booking_verification_state
from system.serializers.users import MessageResponseSerializer


START_REQUEST_EXPIRE_HOURS = 1
END_REQUEST_EXPIRE_HOURS = 24


def _serialize_request(req):
    return {
        'id': req.id,
        'booking_id': req.booking_id,
        'request_type': req.request_type,
        'status': req.status,
        'attempt_no': req.attempt_no,
        'requested_by': req.requested_by_id,
        'responded_by': req.responded_by_id,
        'response_reason': req.response_reason,
        'expires_at': req.expires_at.isoformat(),
        'created_at': req.created_at.isoformat(),
        'updated_at': req.updated_at.isoformat(),
    }


def _allowed_attempt_no(booking, request_type, now):
    existing = list(
        booking.verification_requests.filter(request_type=request_type).order_by('-attempt_no', '-created_at')
    )

    if any(req.status == SessionVerificationRequest.STATUS_PENDING for req in existing):
        return None, 'There is already a pending request for this session step.'

    if not existing:
        return 1, None

    if len(existing) >= 2:
        return None, 'Resend limit reached for this session step.'

    last_req = existing[0]
    if last_req.status not in (SessionVerificationRequest.STATUS_EXPIRED, SessionVerificationRequest.STATUS_REJECTED):
        return None, 'A resend is only allowed after expiry or rejection.'

    if now > booking.start_retry_deadline:
        return None, 'Retry window has ended for this booking date.'

    return last_req.attempt_no + 1, None


def _trainer_booking_or_404(user, booking_id):
    return get_object_or_404(Booking.objects.select_related('trainer', 'client'), id=booking_id, trainer=user)


def _client_booking_or_404(user, booking_id):
    return get_object_or_404(Booking.objects.select_related('trainer', 'client'), id=booking_id, client=user)


@extend_schema(
    summary='Create Session Verification Request (Trainer)',
    request=SessionVerificationRequestCreateSerializer,
    responses={
        201: OpenApiResponse(description='Request created'),
        400: OpenApiResponse(response=MessageResponseSerializer, description='Validation error'),
        403: OpenApiResponse(response=MessageResponseSerializer, description='Forbidden'),
        404: OpenApiResponse(response=MessageResponseSerializer, description='Booking not found'),
    },
    tags=['Trainer Schedule'],
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trainer_create_session_request_view(request, booking_id):
    if not request.user.is_trainer:
        return Response({'status': False, 'message': 'Only trainers can access this.'}, status=status.HTTP_403_FORBIDDEN)

    booking = _trainer_booking_or_404(request.user, booking_id)
    booking = refresh_booking_verification_state(booking)

    serializer = SessionVerificationRequestCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    request_type = serializer.validated_data['request_type']

    now = timezone.now()
    attempt_no, attempt_error = _allowed_attempt_no(booking, request_type, now)
    if attempt_error:
        return Response({'status': False, 'message': attempt_error}, status=status.HTTP_400_BAD_REQUEST)

    if request_type == SessionVerificationRequest.TYPE_START:
        if booking.status not in (Booking.STATUS_CONFIRMED, Booking.STATUS_NO_SHOW_CLIENT):
            return Response(
                {
                    'status': False,
                    'message': f'Start request can only be created for confirmed sessions. Current status: {booking.status}.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if now > booking.start_retry_deadline:
            return Response({'status': False, 'message': 'Start request window has closed.'}, status=status.HTTP_400_BAD_REQUEST)
        expires_at = now + timedelta(hours=START_REQUEST_EXPIRE_HOURS)
    else:
        has_accepted_start = booking.verification_requests.filter(
            request_type=SessionVerificationRequest.TYPE_START,
            status=SessionVerificationRequest.STATUS_ACCEPTED,
        ).exists()
        if not has_accepted_start or booking.status != Booking.STATUS_IN_PROGRESS:
            return Response(
                {'status': False, 'message': 'End request requires an accepted start and in-progress session.'},
                status=status.HTTP_409_CONFLICT,
            )
        expires_at = now + timedelta(hours=END_REQUEST_EXPIRE_HOURS)

    session_request = SessionVerificationRequest.objects.create(
        booking=booking,
        request_type=request_type,
        status=SessionVerificationRequest.STATUS_PENDING,
        requested_by=request.user,
        expires_at=expires_at,
        attempt_no=attempt_no,
    )

    create_booking_notification(
        user=booking.client,
        title='Session verification request',
        message=(
            f'Trainer requested to {"start" if request_type == SessionVerificationRequest.TYPE_START else "end"} '
            f'the session for booking #{booking.id}. Please respond.'
        ),
    )

    return Response({'status': True, 'data': _serialize_request(session_request)}, status=status.HTTP_201_CREATED)


@extend_schema(
    summary='Respond to Session Verification Request (Client)',
    request=SessionVerificationRespondSerializer,
    responses={
        200: OpenApiResponse(description='Request handled'),
        400: OpenApiResponse(response=MessageResponseSerializer, description='Validation error'),
        403: OpenApiResponse(response=MessageResponseSerializer, description='Forbidden'),
        404: OpenApiResponse(response=MessageResponseSerializer, description='Not found'),
    },
    tags=['Client – Bookings'],
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def client_respond_session_request_view(request, booking_id, request_id):
    if request.user.is_trainer:
        return Response({'status': False, 'message': 'Only clients can access this.'}, status=status.HTTP_403_FORBIDDEN)

    booking = _client_booking_or_404(request.user, booking_id)
    booking = refresh_booking_verification_state(booking)

    session_request = get_object_or_404(
        SessionVerificationRequest.objects.select_related('booking'),
        id=request_id,
        booking=booking,
    )

    if session_request.status != SessionVerificationRequest.STATUS_PENDING:
        return Response({'status': False, 'message': 'This request is no longer pending.'}, status=status.HTTP_400_BAD_REQUEST)

    if session_request.expires_at <= timezone.now():
        refresh_booking_verification_state(booking)
        session_request.refresh_from_db()
        return Response({'status': False, 'message': 'This request has expired.'}, status=status.HTTP_400_BAD_REQUEST)

    serializer = SessionVerificationRespondSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    action = serializer.validated_data['action']
    reason = serializer.validated_data.get('reason', '')

    session_request.responded_by = request.user
    session_request.response_reason = reason

    now = timezone.now()
    if action == 'accept':
        session_request.status = SessionVerificationRequest.STATUS_ACCEPTED
        if session_request.request_type == SessionVerificationRequest.TYPE_START:
            booking.status = Booking.STATUS_IN_PROGRESS
            booking.session_started_at = now
            booking.save(update_fields=['status', 'session_started_at', 'updated_at'])
        else:
            booking.status = Booking.STATUS_COMPLETED
            booking.session_ended_at = now
            booking.save(update_fields=['status', 'session_ended_at', 'updated_at'])

            from payment.services import sync_payout_on_booking_status
            sync_payout_on_booking_status(booking, Booking.STATUS_COMPLETED)

        create_booking_notification(
            user=booking.trainer,
            title='Session verification accepted',
            message=f'Client accepted the {session_request.request_type} request for booking #{booking.id}.',
        )
    else:
        session_request.status = SessionVerificationRequest.STATUS_REJECTED
        if session_request.request_type == SessionVerificationRequest.TYPE_END:
            booking.status = Booking.STATUS_DISPUTED
            booking.save(update_fields=['status', 'updated_at'])

        create_booking_notification(
            user=booking.trainer,
            title='Session verification rejected',
            message=f'Client rejected the {session_request.request_type} request for booking #{booking.id}.',
        )
        create_booking_notification(
            user=booking.client,
            title='Session verification rejected',
            message=f'You rejected the {session_request.request_type} request for booking #{booking.id}.',
        )

    session_request.save(update_fields=['status', 'responded_by', 'response_reason', 'updated_at'])

    return Response(
        {
            'status': True,
            'data': {
                'booking_id': booking.id,
                'booking_status': booking.status,
                'verification_request': _serialize_request(session_request),
            },
        },
        status=status.HTTP_200_OK,
    )


@extend_schema(
    summary='List Session Verification Requests',
    responses={200: OpenApiResponse(description='Request list')},
    tags=['Client – Bookings'],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def booking_session_requests_view(request, booking_id):
    booking = get_object_or_404(Booking.objects.select_related('trainer', 'client'), id=booking_id)
    if request.user.id not in (booking.client_id, booking.trainer_id):
        return Response({'status': False, 'message': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

    booking = refresh_booking_verification_state(booking)
    requests_qs = booking.verification_requests.select_related('requested_by', 'responded_by').order_by('-created_at')
    return Response(
        {
            'status': True,
            'data': {
                'booking_id': booking.id,
                'booking_status': booking.status,
                'requests': [_serialize_request(r) for r in requests_qs],
            },
        },
        status=status.HTTP_200_OK,
    )
