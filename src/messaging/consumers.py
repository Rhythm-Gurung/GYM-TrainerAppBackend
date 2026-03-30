import json
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework_simplejwt.authentication import JWTAuthentication
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from scheduling.models import Booking
from messaging.models import ChatSession, ChatMessage

UserBase = get_user_model()


class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time trainer-client messaging.
    Requires:
      - Valid JWT token in query string or headers
      - Confirmed booking between trainer and client
    """

    async def connect(self):
        """
        Validate JWT token and confirmed booking before accepting connection.
        """
        self.booking_id = self.scope['url_route']['kwargs'].get('booking_id')
        
        if not self.booking_id:
            await self.close(code=4400, reason='booking_id required')
            return
        
        # Extract JWT token from query string or headers
        jwt_token = await self._extract_jwt_token()
        if not jwt_token:
            await self.close(code=4401, reason='Not authenticated')
            return
        
        # Authenticate user from JWT token
        try:
            user = await self._authenticate_jwt(jwt_token)
        except Exception:
            await self.close(code=4401, reason='Invalid token')
            return
        
        # Validate confirmed booking exists
        booking = await self._get_confirmed_booking(self.booking_id, user)
        if not booking:
            await self.close(code=4403, reason='Chat enabled only for confirmed booking')
            return
        
        # Store user and booking info
        self.user = user
        self.booking = booking
        
        # Set room group name based on booking ID
        self.room_group_name = f'chat_booking_{self.booking_id}'
        
        # Join the group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        # Accept the WebSocket connection
        await self.accept()
        
        # Load and send recent message history
        messages = await self._load_recent_messages(limit=50)
        for msg in messages:
            await self.send(text_data=json.dumps({
                'type': 'history',
                'content': msg['content'],
                'sender_id': msg['sender_id'],
                'sender_username': msg['sender_username'],
                'timestamp': msg['timestamp'],
            }))
        
        # Notify other user that this user joined
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'user_joined',
                'user_id': self.user.id,
                'username': self.user.username,
            }
        )

    async def disconnect(self, close_code):
        """
        Leave the channel group and notify others.
        """
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'user_left',
                    'user_id': self.user.id,
                    'username': self.user.username,
                }
            )
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        """
        Receive message from client, validate, persist, and broadcast.
        """
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON',
            }))
            return
        
        content = data.get('content', '').strip()
        if not content:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Message cannot be empty',
            }))
            return
        
        # Verify booking is still confirmed
        booking = await self._get_confirmed_booking(self.booking_id, self.user)
        if not booking:
            await self.send(text_data=json.dumps({
                'type': 'chat_disabled',
                'message': 'Chat is disabled because this booking is no longer confirmed. Book a new session to continue chatting.',
            }))
            await self.close(code=4403, reason='Booking is no longer confirmed')
            return
        
        # Create and persist message
        message = await self._save_message(content)
        if not message:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Failed to save message',
            }))
            return
        
        # Broadcast message to group
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

    async def chat_message(self, event):
        """
        Handle incoming chat message from group broadcast.
        """
        await self.send(text_data=json.dumps({
            'type': 'message',
            'message_id': event['message_id'],
            'content': event['content'],
            'sender_id': event['sender_id'],
            'sender_username': event['sender_username'],
            'timestamp': event['timestamp'],
        }))

    async def user_joined(self, event):
        """
        Handle user joined notification.
        """
        if event['user_id'] != self.user.id:  # Don't notify self
            await self.send(text_data=json.dumps({
                'type': 'user_joined',
                'user_id': event['user_id'],
                'username': event['username'],
            }))

    async def user_left(self, event):
        """
        Handle user left notification.
        """
        if event['user_id'] != self.user.id:  # Don't notify self
            await self.send(text_data=json.dumps({
                'type': 'user_left',
                'user_id': event['user_id'],
                'username': event['username'],
            }))

    # Helper methods (sync_to_async)

    def _extract_jwt_token_from_scope(self):
        """
        Extract JWT token from query string or headers.
        """
        # Try query string first
        query_string = self.scope.get('query_string', b'').decode('utf-8')
        if 'token=' in query_string:
            return query_string.split('token=')[1].split('&')[0]
        
        # Try headers (Authorization: Bearer <token>)
        headers = dict(self.scope.get('headers', []))
        auth_header = headers.get(b'authorization', b'').decode('utf-8')
        if auth_header.startswith('Bearer '):
            return auth_header[7:]
        
        return None

    async def _extract_jwt_token(self):
        """Async wrapper for token extraction."""
        return await database_sync_to_async(self._extract_jwt_token_from_scope)()

    @database_sync_to_async
    def _authenticate_jwt(self, token):
        """
        Authenticate user from JWT token using DRF's JWTAuthentication.
        Raises InvalidToken if token is invalid.
        """
        auth = JWTAuthentication()
        # Create a mock request object
        from rest_framework.request import Request
        from django.test import RequestFactory
        
        factory = RequestFactory()
        request = factory.get('/')
        request.META['HTTP_AUTHORIZATION'] = f'Bearer {token}'
        request = Request(request)
        
        user, validated_token = auth.authenticate(request)
        return user

    @database_sync_to_async
    def _get_confirmed_booking(self, booking_id, user):
        """
        Get booking if it's confirmed and user is either trainer or client.
        Returns booking dict or None.
        """
        try:
            booking = Booking.objects.get(
                id=int(booking_id),
                status=Booking.STATUS_CONFIRMED,
            )
            # Check if user is trainer or client in this booking
            if booking.trainer.id == user.id or booking.client.id == user.id:
                return booking
        except (Booking.DoesNotExist, ValueError):
            pass
        return None

    @database_sync_to_async
    def _load_recent_messages(self, limit=50):
        """
        Load recent messages for this booking.
        """
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
        """
        Create/get ChatSession and save message to database.
        """
        try:
            booking_id = int(self.booking_id)
            
            with transaction.atomic():
                # Get or create chat session
                session, _ = ChatSession.objects.get_or_create(
                    booking_id=booking_id,
                    defaults={
                        'trainer': self.booking.trainer,
                        'client': self.booking.client,
                    }
                )
                
                # Create message
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
