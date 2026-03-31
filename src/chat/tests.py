from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model

UserBase = get_user_model()


class ClientChatTestCase(TestCase):
    """Test cases for client chat API"""
    
    def setUp(self):
        self.client = APIClient()
        # Create a test user
        self.user = UserBase.objects.create_user(
            email='testclient@test.com',
            password='testpass123'
        )
    
    def test_client_chat_endpoint(self):
        """Test client chat endpoint with valid message"""
        self.client.force_authenticate(user=self.user)
        
        data = {
            "message": "What are some good chest exercises?"
        }
        
        response = self.client.post('/api/chat/client/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('response', response.data)
        self.assertTrue(response.data['status'])
    
    def test_client_chat_with_history(self):
        """Test client chat with conversation history"""
        self.client.force_authenticate(user=self.user)
        
        data = {
            "message": "What about cardio?",
            "conversation_history": [
                {"role": "user", "content": "What are some good chest exercises?"},
                {"role": "assistant", "content": "Some good chest exercises include push-ups, bench press, and dumbbell flyes."}
            ]
        }
        
        response = self.client.post('/api/chat/client/history/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('response', response.data)
        self.assertTrue(response.data['status'])
    
    def test_client_chat_unauthenticated(self):
        """Test that unauthenticated users cannot access chat"""
        data = {"message": "What exercises should I do?"}
        response = self.client.post('/api/chat/client/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class TrainerChatTestCase(TestCase):
    """Test cases for trainer chat API"""
    
    def setUp(self):
        self.client = APIClient()
        # Create a test trainer user
        self.trainer = UserBase.objects.create_user(
            email='testtrainer@test.com',
            password='testpass123',
            is_trainer=True
        )
    
    def test_trainer_chat_endpoint(self):
        """Test trainer chat endpoint with valid message"""
        self.client.force_authenticate(user=self.trainer)
        
        data = {
            "message": "How do I create a progressive overload plan?"
        }
        
        response = self.client.post('/api/chat/trainer/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('response', response.data)
        self.assertTrue(response.data['status'])
    
    def test_trainer_chat_with_history(self):
        """Test trainer chat with conversation history"""
        self.client.force_authenticate(user=self.trainer)
        
        data = {
            "message": "Any tips for periodization?",
            "conversation_history": [
                {"role": "user", "content": "How do I create a progressive overload plan?"},
                {"role": "assistant", "content": "Progressive overload involves gradually increasing weight, reps, or intensity."}
            ]
        }
        
        response = self.client.post('/api/chat/trainer/history/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('response', response.data)
        self.assertTrue(response.data['status'])
