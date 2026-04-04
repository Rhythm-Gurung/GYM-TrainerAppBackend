from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from scheduling.models import Booking
from trainer_listing.models import TrainerReview

UserBase = get_user_model()


class TrainerReviewApiTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.trainer = UserBase.objects.create_user(
            email='trainer@example.com',
            password='testpass123',
            username='trainer',
            is_trainer=True,
            is_admin_approved=True,
        )
        self.client_user = UserBase.objects.create_user(
            email='client@example.com',
            password='testpass123',
            username='client',
            is_trainer=False,
        )
        self.other_client = UserBase.objects.create_user(
            email='other@example.com',
            password='testpass123',
            username='other-client',
            is_trainer=False,
        )
        self.completed_booking = Booking.objects.create(
            trainer=self.trainer,
            client=self.client_user,
            date=date.today(),
            start_time=time(10, 0),
            end_time=time(11, 0),
            status=Booking.STATUS_COMPLETED,
            total_amount=100,
        )
        self.incomplete_booking = Booking.objects.create(
            trainer=self.trainer,
            client=self.client_user,
            date=date.today(),
            start_time=time(12, 0),
            end_time=time(13, 0),
            status=Booking.STATUS_CONFIRMED,
            total_amount=100,
        )

    def _authenticate(self, user):
        token = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_client_can_review_completed_booking(self):
        self._authenticate(self.client_user)

        response = self.client.post(
            f'/api/trainers/{self.trainer.id}/reviews/',
            {'booking_id': self.completed_booking.id, 'rating': 5, 'comment': 'Great session.'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['booking_id'], self.completed_booking.id)
        self.assertEqual(TrainerReview.objects.count(), 1)
        review = TrainerReview.objects.get()
        self.assertEqual(review.booking_id, self.completed_booking.id)
        self.assertEqual(review.reviewer, self.client_user)

    def test_review_requires_completed_booking(self):
        self._authenticate(self.client_user)

        response = self.client.post(
            f'/api/trainers/{self.trainer.id}/reviews/',
            {'booking_id': self.incomplete_booking.id, 'rating': 4, 'comment': 'Too early.'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TrainerReview.objects.count(), 0)

    def test_other_client_cannot_review_booking(self):
        self._authenticate(self.other_client)

        response = self.client.post(
            f'/api/trainers/{self.trainer.id}/reviews/',
            {'booking_id': self.completed_booking.id, 'rating': 4, 'comment': 'Not my booking.'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(TrainerReview.objects.count(), 0)

    def test_duplicate_review_for_same_booking_is_blocked(self):
        TrainerReview.objects.create(
            trainer=self.trainer,
            reviewer=self.client_user,
            booking=self.completed_booking,
            rating=5,
            comment='First review.',
        )
        self._authenticate(self.client_user)

        response = self.client.post(
            f'/api/trainers/{self.trainer.id}/reviews/',
            {'booking_id': self.completed_booking.id, 'rating': 3, 'comment': 'Second review.'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(TrainerReview.objects.count(), 1)