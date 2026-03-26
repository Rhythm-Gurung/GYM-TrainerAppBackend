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
from system.apis.client import client_profile_image_view, client_profile_view
from system.apis.health import health_check
from system.apis.trainer import (
    admin_verify_trainer_view,
    certification_detail_view,
    certifications_list_view,
    gallery_detail_view,
    gallery_list_view,
    id_proof_view,
    profile_image_view,
    update_profile_view,
)

urlpatterns = [
    # --- System ---
    path('health/', health_check, name='health-check'),

    # --- Shared (Client & Trainer) ---
    # Auth
    path('auth/login/', login, name='login'),
    path('auth/logout/', logout, name='logout'),
    path('auth/register/', UserRegisterAPIView.as_view(), name='register'),
    path('auth/check-email/', check_email_exists, name='check-email-exists'),
    path('auth/refresh/', refresh_token, name='refresh-token'),
    path('auth/whoami/', whoami, name='whoami'),

    # Password
    path('auth/forgot-password/', forgot_password, name='forgot-password'),
    path('auth/verify-forgot-password/', verify_forgot_password, name='verify-forgot-password'),
    path('auth/reset-password/', reset_password, name='reset-password'),

    # Google OAuth
    path('auth/google/', google_login, name='google-login'),
    path('auth/google/link/', link_google_account, name='link-google'),
    path('auth/google/unlink/', unlink_social_account, name='unlink-social'),

    # --- Client Only ---
    # Email verification (OTP-based; trainers skip this flow)
    path('auth/verify-email/', verify_email, name='verify-email'),
    path('auth/resend-verification/', resend_verification_code, name='resend-verification'),

    # Client profile
    path('client/profile/',        client_profile_view,       name='client-profile'),
    path('client/profile-image/',  client_profile_image_view, name='client-profile-image'),

    # --- Admin / Trainer ---
    # Trainer document access (used by admin to review during approval)
    path('trainer/id-proof/', id_proof_view, name='trainer-id-proof'),
    path('trainer/certifications/', certifications_list_view, name='trainer-certifications'),
    path('trainer/certifications/<int:cert_id>/', certification_detail_view, name='trainer-certification-image'),
    path('trainer/profile-image/', profile_image_view, name='trainer-profile-image'),
    path('trainer/update-profile/', update_profile_view, name='trainer-update-profile'),
    path('trainer/gallery/', gallery_list_view, name='trainer-gallery'),
    path('trainer/gallery/<int:image_id>/', gallery_detail_view, name='trainer-gallery-image'),
    path('admin/trainer/<int:trainer_id>/verify/', admin_verify_trainer_view, name='admin-trainer-verify'),
]
