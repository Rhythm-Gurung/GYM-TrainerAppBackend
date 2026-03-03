from django.urls import path

from system.apis.auth import (
    UserRegisterAPIView,
    check_email_exists,
    forgot_password,
    google_login,
    link_google_account,
    login,
    logout,
    refresh_token,
    resend_verification_code,
    reset_password,
    unlink_social_account,
    verify_email,
    verify_forgot_password,
    whoami,
)
from system.apis.health import health_check
from system.apis.trainer import get_certification, get_id_proof, list_certifications

urlpatterns = [
    path('health/', health_check, name='health-check'),

    # Auth
    path('auth/login/', login, name='login'),
    path('auth/logout/', logout, name='logout'),
    path('auth/register/', UserRegisterAPIView.as_view(), name='register'),
    path('auth/check-email/', check_email_exists, name='check-email-exists'),
    path('auth/refresh/', refresh_token, name='refresh-token'),
    path('auth/whoami/', whoami, name='whoami'),

    # Email verification
    path('auth/verify-email/', verify_email, name='verify-email'),
    path('auth/resend-verification/', resend_verification_code, name='resend-verification'),

    # Password
    path('auth/forgot-password/', forgot_password, name='forgot-password'),
    path('auth/verify-forgot-password/', verify_forgot_password, name='verify-forgot-password'),
    path('auth/reset-password/', reset_password, name='reset-password'),

    # Trainer documents
    path('trainer/id-proof/', get_id_proof, name='trainer-id-proof'),
    path('trainer/certifications/', list_certifications, name='trainer-certifications'),
    path('trainer/certifications/<int:cert_id>/', get_certification, name='trainer-certification-image'),

    # Google OAuth
    path('auth/google/', google_login, name='google-login'),
    path('auth/google/link/', link_google_account, name='link-google'),
    path('auth/google/unlink/', unlink_social_account, name='unlink-social'),
]
