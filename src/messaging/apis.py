from django.db.models import Q
from drf_spectacular.utils import extend_schema, OpenApiResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from messaging.models import ChatMessage, ChatSession
from messaging.serializers import (
    ChatSessionSerializer,
    ChatMessageSerializer,
    MarkMessagesReadSerializer,
    MarkMessagesReadResponseSerializer,
)
from scheduling.models import Booking


class ChatPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 100


@extend_schema(
    summary="List Chat Sessions",
    responses={
        200: OpenApiResponse(
            response=ChatSessionSerializer(many=True),
            description="List of active chat sessions"
        ),
    },
    tags=["Messaging"]
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_chat_sessions(request):
    """
    List all chat sessions for the authenticated user.
    Can be either trainer or client in these bookings.
    """
    user = request.user
    
    # Get all confirmed bookings where user is trainer or client
    bookings = Booking.objects.filter(
        Q(trainer=user) | Q(client=user),
        status=Booking.STATUS_CONFIRMED
    ).values_list('id', flat=True)
    
    # Get or create chat sessions for these bookings
    sessions = ChatSession.objects.filter(
        booking_id__in=bookings
    ).select_related('trainer', 'client', 'booking').prefetch_related('messages')
    
    # Create chat sessions for bookings that don't have one yet
    existing_booking_ids = sessions.values_list('booking_id', flat=True)
    for booking_id in bookings:
        if booking_id not in existing_booking_ids:
            booking = Booking.objects.get(id=booking_id)
            ChatSession.objects.get_or_create(
                booking_id=booking_id,
                defaults={
                    'trainer': booking.trainer,
                    'client': booking.client,
                }
            )
    
    # Refresh sessions query after creation
    sessions = ChatSession.objects.filter(
        booking_id__in=bookings
    ).select_related('trainer', 'client', 'booking').prefetch_related('messages').order_by('-updated_at')
    
    serializer = ChatSessionSerializer(
        sessions,
        many=True,
        context={'request': request}
    )
    return Response(serializer.data)


@extend_schema(
    summary="Get Chat History",
    responses={
        200: OpenApiResponse(
            response=ChatMessageSerializer(many=True),
            description="Paginated chat message history"
        ),
        403: OpenApiResponse(
            description="User not part of this chat session"
        ),
        404: OpenApiResponse(
            description="Chat session or booking not found"
        ),
    },
    tags=["Messaging"]
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def chat_history(request, booking_id):
    """
    Get paginated message history for a specific booking.
    User must be either trainer or client in the confirmed booking.
    """
    user = request.user
    
    # Verify booking exists and is confirmed
    try:
        booking = Booking.objects.get(id=booking_id, status=Booking.STATUS_CONFIRMED)
    except Booking.DoesNotExist:
        return Response(
            {'detail': 'Booking not found or not confirmed'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Verify user is part of this booking
    if booking.trainer != user and booking.client != user:
        return Response(
            {'detail': 'You are not part of this booking'},
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Get or create chat session
    session, _ = ChatSession.objects.get_or_create(
        booking=booking,
        defaults={
            'trainer': booking.trainer,
            'client': booking.client,
        }
    )
    
    # Get messages
    messages = ChatMessage.objects.filter(session=session).select_related('sender').order_by('-timestamp')
    
    # Paginate
    paginator = ChatPagination()
    paginated_messages = paginator.paginate_queryset(messages, request)
    
    serializer = ChatMessageSerializer(paginated_messages, many=True)
    return paginator.get_paginated_response(serializer.data)


@extend_schema(
    summary="Mark Messages as Read",
    request=MarkMessagesReadSerializer,
    responses={
        200: OpenApiResponse(
            response=MarkMessagesReadResponseSerializer,
            description="Number of messages marked as read"
        ),
        403: OpenApiResponse(
            description="User not part of this chat session"
        ),
        404: OpenApiResponse(
            description="Chat session not found"
        ),
    },
    tags=["Messaging"]
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_messages_read(request, booking_id):
    """
    Mark messages as read for a specific booking.
    If message_ids not provided, marks all unread messages in this chat.
    """
    user = request.user
    
    # Verify booking exists and is confirmed
    try:
        booking = Booking.objects.get(id=booking_id, status=Booking.STATUS_CONFIRMED)
    except Booking.DoesNotExist:
        return Response(
            {'detail': 'Booking not found or not confirmed'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Verify user is part of this booking
    if booking.trainer != user and booking.client != user:
        return Response(
            {'detail': 'You are not part of this booking'},
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Get chat session
    try:
        session = ChatSession.objects.get(booking=booking)
    except ChatSession.DoesNotExist:
        return Response(
            {'detail': 'Chat session not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Parse request data
    serializer = MarkMessagesReadSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    message_ids = serializer.validated_data.get('message_ids')
    
    # Mark messages as read
    if message_ids:
        # Mark specific messages
        updated_count = ChatMessage.objects.filter(
            session=session,
            id__in=message_ids
        ).exclude(sender=user).update(is_read=True)
    else:
        # Mark all unread messages (not sent by current user)
        updated_count = ChatMessage.objects.filter(
            session=session,
            is_read=False
        ).exclude(sender=user).update(is_read=True)
    
    return Response(
        {'marked_count': updated_count},
        status=status.HTTP_200_OK
    )
