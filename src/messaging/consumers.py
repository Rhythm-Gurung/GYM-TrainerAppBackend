import json
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from rest_framework_simplejwt.authentication import JWTAuthentication
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from scheduling.models import Booking
from messaging.models import ChatSession, ChatMessage

UserBase = get_user_model()

CHAT_ENABLED_BOOKING_STATUSES = [
    Booking.STATUS_CONFIRMED,
    Booking.STATUS_IN_PROGRESS,
]


# ---------------------------------------------------------------------------
# Module-level badge helpers (usable from consumers and REST views)
# ---------------------------------------------------------------------------

@database_sync_to_async
def _compute_badge_sessions(user_id):
    """
    Return list of {booking_id, unread_count} dicts for all sessions
    where the user has unread messages sent by the other party.
    """
    try:
        user = UserBase.objects.get(id=user_id)
    except UserBase.DoesNotExist:
        return []

    sessions = ChatSession.objects.filter(
        Q(trainer=user) | Q(client=user)
    )
    result = []
    for session in sessions:
        count = ChatMessage.objects.filter(
            session=session,
            is_read=False,
        ).exclude(sender=user).count()
        result.append({'booking_id': session.booking_id, 'unread_count': count})
    return result


async def push_badge_update(user_id):
    """
    Compute fresh unread counts for user_id and push an unread_update event
    to their badge WebSocket group.  Safe to call from sync code via
    async_to_sync(push_badge_update)(user_id).
    """
    channel_layer = get_channel_layer()
    sessions = await _compute_badge_sessions(user_id)
    total = sum(s['unread_count'] for s in sessions)
    await channel_layer.group_send(
        f'badge_user_{user_id}',
        {
            'type': 'unread_update',
            'total_unread': total,
            'sessions': sessions,
        }
    )


# ---------------------------------------------------------------------------
# Shared JWT auth mixin
# ---------------------------------------------------------------------------

class JWTAuthMixin:
    """Shared JWT authentication helpers for WebSocket consumers."""

    def _extract_jwt_token_from_scope(self):
        query_string = self.scope.get('query_string', b'').decode('utf-8')
        if 'token=' in query_string:
            return query_string.split('token=')[1].split('&')[0]

        headers = dict(self.scope.get('headers', []))
        auth_header = headers.get(b'authorization', b'').decode('utf-8')
        if auth_header.startswith('Bearer '):
            return auth_header[7:]

        return None

    async def _extract_jwt_token(self):
        return await database_sync_to_async(self._extract_jwt_token_from_scope)()

    @database_sync_to_async
    def _authenticate_jwt(self, token):
        from rest_framework.request import Request
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get('/')
        request.META['HTTP_AUTHORIZATION'] = f'Bearer {token}'
        request = Request(request)

        auth = JWTAuthentication()
        user, _ = auth.authenticate(request)
        return user


# ---------------------------------------------------------------------------
# Per-booking chat consumer
# ---------------------------------------------------------------------------

class ChatConsumer(JWTAuthMixin, AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time trainer-client messaging.
    Requires:
      - Valid JWT token in query string or headers
            - Chat-enabled booking between trainer and client
    """

    async def connect(self):
        self.booking_id = self.scope['url_route']['kwargs'].get('booking_id')

        if not self.booking_id:
            await self.close(code=4400, reason='booking_id required')
            return

        jwt_token = await self._extract_jwt_token()
        if not jwt_token:
            await self.close(code=4401, reason='Not authenticated')
            return

        try:
            user = await self._authenticate_jwt(jwt_token)
        except Exception:
            await self.close(code=4401, reason='Invalid token')
            return

        booking = await self._get_confirmed_booking(self.booking_id, user)
        if not booking:
            await self.close(code=4403, reason='Chat enabled only for confirmed or in-progress booking')
            return

        self.user = user
        self.booking = booking
        # Store FK IDs to avoid lazy-load issues in async context
        self.trainer_id = booking.trainer_id
        self.client_id = booking.client_id

        self.room_group_name = f'chat_booking_{self.booking_id}'

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        messages = await self._load_recent_messages(limit=50)
        for msg in messages:
            await self.send(text_data=json.dumps({
                'type': 'history',
                'content': msg['content'],
                'sender_id': msg['sender_id'],
                'sender_username': msg['sender_username'],
                'timestamp': msg['timestamp'],
            }))

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'user_joined',
                'user_id': self.user.id,
                'username': self.user.username,
            }
        )

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'user_left',
                    'user_id': self.user.id,
                    'username': self.user.username,
                }
            )
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({'type': 'error', 'message': 'Invalid JSON'}))
            return

        content = data.get('content', '').strip()
        if not content:
            await self.send(text_data=json.dumps({'type': 'error', 'message': 'Message cannot be empty'}))
            return

        booking = await self._get_confirmed_booking(self.booking_id, self.user)
        if not booking:
            await self.send(text_data=json.dumps({
                'type': 'chat_disabled',
                'message': 'Chat is disabled because this booking is no longer confirmed or in progress. Book a new session to continue chatting.',
            }))
            await self.close(code=4403, reason='Booking is no longer chat-enabled')
            return

        message = await self._save_message(content)
        if not message:
            await self.send(text_data=json.dumps({'type': 'error', 'message': 'Failed to save message'}))
            return

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'message_id': message['id'],
                'content': message['content'],
                'sender_id': message['sender_id'],
                'sender_username': message['sender_username'],
                'timestamp': message['timestamp'],
            }
        )

        # Push badge update to the recipient so their tab badge increments
        recipient_id = self.client_id if self.user.id == self.trainer_id else self.trainer_id
        await push_badge_update(recipient_id)

    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'message',
            'message_id': event['message_id'],
            'content': event['content'],
            'sender_id': event['sender_id'],
            'sender_username': event['sender_username'],
            'timestamp': event['timestamp'],
        }))

    async def user_joined(self, event):
        if event['user_id'] != self.user.id:
            await self.send(text_data=json.dumps({
                'type': 'user_joined',
                'user_id': event['user_id'],
                'username': event['username'],
            }))

    async def user_left(self, event):
        if event['user_id'] != self.user.id:
            await self.send(text_data=json.dumps({
                'type': 'user_left',
                'user_id': event['user_id'],
                'username': event['username'],
            }))

    @database_sync_to_async
    def _get_confirmed_booking(self, booking_id, user):
        try:
            booking = Booking.objects.get(
                id=int(booking_id),
                status__in=CHAT_ENABLED_BOOKING_STATUSES,
            )
            if booking.trainer_id == user.id or booking.client_id == user.id:
                return booking
        except (Booking.DoesNotExist, ValueError):
            pass
        return None

    @database_sync_to_async
    def _load_recent_messages(self, limit=50):
        try:
            session = ChatSession.objects.get(booking_id=int(self.booking_id))
            messages = ChatMessage.objects.filter(
                session=session
            ).select_related('sender').order_by('-timestamp')[:limit]

            return [
                {
                    'id': msg.id,
                    'content': msg.content,
                    'sender_id': msg.sender.id,
                    'sender_username': msg.sender.username,
                    'timestamp': msg.timestamp.isoformat(),
                }
                for msg in reversed(messages)
            ]
        except ChatSession.DoesNotExist:
            return []

    @database_sync_to_async
    def _save_message(self, content):
        try:
            booking_id = int(self.booking_id)

            with transaction.atomic():
                session, _ = ChatSession.objects.get_or_create(
                    booking_id=booking_id,
                    defaults={
                        'trainer_id': self.trainer_id,
                        'client_id': self.client_id,
                    }
                )

                message = ChatMessage.objects.create(
                    session=session,
                    sender=self.user,
                    content=content,
                )

                return {
                    'id': message.id,
                    'content': message.content,
                    'sender_id': message.sender.id,
                    'sender_username': message.sender.username,
                    'timestamp': message.timestamp.isoformat(),
                }
        except Exception as e:
            print(f"Error saving message: {e}")
            return None


# ---------------------------------------------------------------------------
# Global badge consumer  (ws/badge/?token=...)
# ---------------------------------------------------------------------------

class BadgeConsumer(JWTAuthMixin, AsyncWebsocketConsumer):
    """
    Global WebSocket that streams unread-message badge counts to the client.

    Connect: ws/badge/?token=<JWT>
    Server → client events only (client never needs to send anything):
        {
            "type": "unread_update",
            "total_unread": 5,
            "sessions": [
                {"booking_id": 3, "unread_count": 3},
                {"booking_id": 7, "unread_count": 2}
            ]
        }

    Events are pushed:
      - Once immediately on connect (initial state)
      - Whenever a new message is sent to the user
      - Whenever the user marks messages as read (via REST API)
    """

    async def connect(self):
        jwt_token = await self._extract_jwt_token()
        if not jwt_token:
            await self.close(code=4401, reason='Not authenticated')
            return

        try:
            user = await self._authenticate_jwt(jwt_token)
        except Exception:
            await self.close(code=4401, reason='Invalid token')
            return

        self.user = user
        self.badge_group = f'badge_user_{user.id}'

        await self.channel_layer.group_add(self.badge_group, self.channel_name)
        await self.accept()

        # Send current state immediately so the frontend has data right away
        sessions = await _compute_badge_sessions(self.user.id)
        total = sum(s['unread_count'] for s in sessions)
        await self.send(text_data=json.dumps({
            'type': 'unread_update',
            'total_unread': total,
            'sessions': sessions,
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'badge_group'):
            await self.channel_layer.group_discard(self.badge_group, self.channel_name)

    async def receive(self, text_data):
        # Read-only stream — ignore anything the client sends
        pass

    async def unread_update(self, event):
        """Forward group_send event to the WebSocket client."""
        await self.send(text_data=json.dumps({
            'type': 'unread_update',
            'total_unread': event['total_unread'],
            'sessions': event['sessions'],
        }))
