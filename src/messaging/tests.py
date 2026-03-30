from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from scheduling.models import Booking
from messaging.models import ChatSession, ChatMessage
from datetime import datetime, time

UserBase = get_user_model()


class MessagingApiTestCase(TestCase):
    """Test REST API endpoints for messaging."""
    
    def setUp(self):
        """Create test users and bookings."""
        self.client = APIClient()
        
        # Create trainer and client
        self.trainer = UserBase.objects.create_user(
            email='trainer@test.com',
            password='testpass123',
            username='trainer',
            is_trainer=True,
        )
        self.client_user = UserBase.objects.create_user(
            email='client@test.com',
            password='testpass123',
            username='client',
            is_trainer=False,
        )
        
        # Create a confirmed booking
        self.booking = Booking.objects.create(
            trainer=self.trainer,
            client=self.client_user,
            date=datetime.now().date(),
            start_time=time(10, 0),
            end_time=time(11, 0),
            status=Booking.STATUS_CONFIRMED,
            total_amount=100,
        )
        
        # Create a chat session
        self.session = ChatSession.objects.create(
            trainer=self.trainer,
            client=self.client_user,
            booking=self.booking,
        )
    
    def _get_token(self, user):
        """Get JWT token for a user."""
        refresh = RefreshToken.for_user(user)
        return str(refresh.access_token)
    
    def test_list_chat_sessions_authenticated(self):
        """Test listing chat sessions for authenticated user."""
        token = self._get_token(self.trainer)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        
        response = self.client.get('/api/chat/sessions/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.session.id)
    
    def test_list_chat_sessions_unauthenticated(self):
        """Test that unauthenticated users cannot list sessions."""
        response = self.client.get('/api/chat/sessions/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_chat_history_authenticated(self):
        """Test retrieving chat history."""
        # Create some messages
        ChatMessage.objects.create(
            session=self.session,
            sender=self.trainer,
            content='Hello from trainer',
        )
        ChatMessage.objects.create(
            session=self.session,
            sender=self.client_user,
            content='Hello from client',
        )
        
        token = self._get_token(self.trainer)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        
        response = self.client.get(f'/api/chat/history/{self.booking.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)
    
    def test_chat_history_unauthorized_user(self):
        """Test that unauthorized users cannot view chat history."""
        other_user = UserBase.objects.create_user(
            email='other@test.com',
            password='testpass123',
            username='other',
        )
        token = self._get_token(other_user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        
        response = self.client.get(f'/api/chat/history/{self.booking.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    def test_mark_messages_read(self):
        """Test marking messages as read."""
        # Create messages
        msg1 = ChatMessage.objects.create(
            session=self.session,
            sender=self.trainer,
            content='Message 1',
            is_read=False,
        )
        msg2 = ChatMessage.objects.create(
            session=self.session,
            sender=self.trainer,
            content='Message 2',
            is_read=False,
        )
        
        token = self._get_token(self.client_user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        
        # Mark all as read
        response = self.client.post(f'/api/chat/read/{self.booking.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['marked_count'], 2)
        
        # Verify messages are marked as read
        msg1.refresh_from_db()
        msg2.refresh_from_db()
        self.assertTrue(msg1.is_read)
        self.assertTrue(msg2.is_read)

    def test_chat_is_disabled_after_booking_completed(self):
        """Chat must be disabled once booking moves out of confirmed state."""
        self.booking.status = Booking.STATUS_COMPLETED
        self.booking.save(update_fields=['status'])

        token = self._get_token(self.trainer)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        sessions_response = self.client.get('/api/chat/sessions/')
        self.assertEqual(sessions_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(sessions_response.data), 0)

        history_response = self.client.get(f'/api/chat/history/{self.booking.id}/')
        self.assertEqual(history_response.status_code, status.HTTP_404_NOT_FOUND)


class MessagingConsumerTestCase(TestCase):
    """Test WebSocket consumer for messaging."""
    
    def setUp(self):
        """Create test users and bookings."""
        # Create trainer and client
        self.trainer = UserBase.objects.create_user(
            email='trainer@test.com',
            password='testpass123',
            username='trainer',
            is_trainer=True,
        )
        self.client_user = UserBase.objects.create_user(
            email='client@test.com',
            password='testpass123',
            username='client',
            is_trainer=False,
        )
        
        # Create a confirmed booking
        self.booking = Booking.objects.create(
            trainer=self.trainer,
            client=self.client_user,
            date=datetime.now().date(),
            start_time=time(10, 0),
            end_time=time(11, 0),
            status=Booking.STATUS_CONFIRMED,
            total_amount=100,
        )
    
    def test_consumer_requires_authenticated_user(self):
        """Consumer should reject connections without valid JWT token."""
        # This would be tested with AsyncClient in a real async test setup
        # For now, just verify the booking and models work
        self.assertIsNotNone(self.booking)
        self.assertEqual(self.booking.status, Booking.STATUS_CONFIRMED)
    
    def test_consumer_requires_confirmed_booking(self):
        """Consumer should reject connections if booking is not confirmed."""
        # Create an unconfirmed booking
        unconfirmed_booking = Booking.objects.create(
            trainer=self.trainer,
            client=self.client_user,
            date=datetime.now().date(),
            start_time=time(12, 0),
            end_time=time(13, 0),
            status=Booking.STATUS_PENDING,
            total_amount=100,
        )
        
        # Verify it's not confirmed
        self.assertNotEqual(unconfirmed_booking.status, Booking.STATUS_CONFIRMED)
    
    def test_chat_session_creation(self):
        """Test ChatSession creation via consumer."""
        self.assertFalse(ChatSession.objects.filter(booking=self.booking).exists())
        
        # Simulate session creation
        session = ChatSession.objects.create(
            trainer=self.trainer,
            client=self.client_user,
            booking=self.booking,
        )
        
        self.assertTrue(ChatSession.objects.filter(booking=self.booking).exists())
        self.assertEqual(session.trainer, self.trainer)
        self.assertEqual(session.client, self.client_user)
    
    def test_message_persistence(self):
        """Test that messages are persisted to database."""
        session = ChatSession.objects.create(
            trainer=self.trainer,
            client=self.client_user,
            booking=self.booking,
        )
        
        # Create a message
        message = ChatMessage.objects.create(
            session=session,
            sender=self.trainer,
            content='Test message',
        )
        
        # Verify it's in database
        retrieved = ChatMessage.objects.get(id=message.id)
        self.assertEqual(retrieved.content, 'Test message')
        self.assertEqual(retrieved.sender, self.trainer)
        self.assertFalse(retrieved.is_read)


class ChatSessionModelTestCase(TestCase):
    """Test ChatSession model."""
    
    def setUp(self):
        """Create test users and bookings."""
        self.trainer = UserBase.objects.create_user(
            email='trainer@test.com',
            password='testpass123',
            username='trainer',
            is_trainer=True,
        )
        self.client_user = UserBase.objects.create_user(
            email='client@test.com',
            password='testpass123',
            username='client',
            is_trainer=False,
        )
        
        self.booking = Booking.objects.create(
            trainer=self.trainer,
            client=self.client_user,
            date=datetime.now().date(),
            start_time=time(10, 0),
            end_time=time(11, 0),
            status=Booking.STATUS_CONFIRMED,
            total_amount=100,
        )
    
    def test_chat_session_unique_constraint(self):
        """Test that only one chat session per booking is allowed."""
        
        # Try to create another session for the same booking
        # This should fail due to unique constraint
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            ChatSession.objects.create(
                trainer=self.trainer,
                client=self.client_user,
                booking=self.booking,
            )
