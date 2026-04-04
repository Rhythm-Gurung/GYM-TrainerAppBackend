
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotAuthenticated
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from system.models.otp import VerificationCode
from system.serializers.register import UserRegisterSerializer
from system.serializers.users import MessageResponseSerializer, UserBaseDetailSerializer

UserBase = get_user_model()


# Swagger Serializers

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(style={"input_type": "password"})

class RefreshTokenSerializer(serializers.Serializer):
    refresh = serializers.CharField()

class TokenResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()

class LoginResponseSerializer(serializers.Serializer):
    tokens = TokenResponseSerializer()
    user = UserBaseDetailSerializer()

class WhoAmIResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    data = UserBaseDetailSerializer()

class EmailVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    verification_code = serializers.CharField(max_length=6, min_length=6)

class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()

class ChangePasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()
    new_password = serializers.CharField(style={"input_type": "password"})
    confirm_new_password = serializers.CharField(style={"input_type": "password"})

class CheckEmailSerializer(serializers.Serializer):
    email = serializers.EmailField()

class CheckEmailResponseSerializer(serializers.Serializer):
    exists = serializers.BooleanField()
    can_reapply = serializers.BooleanField()


# Token Cache Utilities

def set_token_to_cache(tokens, user):
    cache.set(tokens["access"], user, settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds())
    cache.set(tokens["refresh"], user, settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())
    cache.set(
        "refresh_" + tokens["access"],
        tokens["refresh"],
        settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds(),
    )


def remove_tokens_from_cache(access_token, user_id):
    cache.delete(access_token)
    refresh = cache.get("refresh_" + access_token)
    cache.delete(refresh)
    cache.delete("refresh_" + access_token)


def generate_token(user, request=None):
    tokens = RefreshToken.for_user(user)
    tokens = {"access": str(tokens.access_token), "refresh": str(tokens)}
    set_token_to_cache(tokens, user)
    details = UserBaseDetailSerializer(instance=user).data
    if not user.is_active:
        user.is_active = True
        user.save()
    return (tokens, details)


# Endpoints

@extend_schema(
    summary="User Login",
    request=LoginSerializer,
    responses={
        200: OpenApiResponse(response=LoginResponseSerializer, description="Login successful"),
        401: OpenApiResponse(response=MessageResponseSerializer, description="Invalid credentials"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
    },
    tags=["Authentication"]
)
@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    email = str(request.data["email"])
    password = str(request.data["password"])
    user = authenticate(email=email, password=password)
    if not user:
        raise NotAuthenticated("Email or password wrong.")
    if user.is_trainer:
        if user.is_rejected:
            return Response(
                {"detail": "Your trainer application was rejected. Please re-apply with updated information."},
                status=status.HTTP_403_FORBIDDEN
            )
        if not user.is_admin_approved:
            return Response(
                {"detail": "Your account is pending admin approval. You will be notified once approved."},
                status=status.HTTP_403_FORBIDDEN
            )
    else:
        if not user.is_email_verified:
            return Response(
                {"detail": "Please verify your email before logging in."},
                status=status.HTTP_403_FORBIDDEN
            )
    tokens, details = generate_token(user, request)
    return Response({"tokens": tokens, "user": details})


@extend_schema(
    summary="Refresh Token",
    request=RefreshTokenSerializer,
    responses={
        200: OpenApiResponse(response=TokenResponseSerializer, description="Success"),
        401: OpenApiResponse(response=MessageResponseSerializer, description="Unauthorized"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
    },
    tags=["Authentication"]
)
@api_view(["POST"])
@permission_classes([AllowAny])
def refresh_token(request):
    refresh_token_str = request.data.get("refresh")
    if not refresh_token_str:
        return Response({"detail": "Refresh token is required."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        user = cache.get(refresh_token_str)
        if not user:
            return Response({"detail": "Invalid or expired refresh token."}, status=status.HTTP_401_UNAUTHORIZED)
        refresh = RefreshToken.for_user(user)
        new_tokens = {"access": str(refresh.access_token), "refresh": str(refresh)}
        cache.delete(refresh_token_str)
        set_token_to_cache(new_tokens, user)
        return Response(new_tokens, status=status.HTTP_200_OK)
    except Exception:
        return Response({"detail": "Failed to refresh token."}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    summary="Get Current User",
    responses={
        200: OpenApiResponse(response=WhoAmIResponseSerializer, description="OK"),
        401: OpenApiResponse(response=MessageResponseSerializer, description="Unauthorized"),
    },
    tags=["Authentication"]
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def whoami(request):
    return Response({"status": True, "data": UserBaseDetailSerializer(instance=request.user, context={"request": request}).data})


@extend_schema(
    summary="User Logout",
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description="Success"),
        401: OpenApiResponse(response=MessageResponseSerializer, description="Unauthorized"),
    },
    tags=["Authentication"]
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def logout(request):
    if token := request.headers.get("Authorization", None):
        remove_tokens_from_cache(token, request.user.id)
    return Response({"status": True})


class UserRegisterAPIView(APIView):
    permission_classes = [AllowAny]
    serializer_class = UserRegisterSerializer
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary="User Registration",
        request=UserRegisterSerializer,
        responses={
            201: OpenApiResponse(response=MessageResponseSerializer, description="Created"),
            400: OpenApiResponse(description="Bad Request"),
        },
        tags=["Authentication"]
    )
    def post(self, request):
        serializer = UserRegisterSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            user = serializer.save()
            if user.is_trainer:
                detail = "Registration submitted. Your account is pending admin approval."
            else:
                detail = "Registration successful. Please check your email to verify your account."
            return Response({"detail": detail, "status": True}, status=status.HTTP_201_CREATED)
        return Response({"detail": serializer.errors, "status": False}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    summary="Email Verification",
    request=EmailVerificationSerializer,
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description="Success"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Authentication"]
)
@api_view(["POST"])
@permission_classes([AllowAny])
def verify_email(request):
    email = request.data.get("email")
    verification_code = request.data.get("verification_code")
    if not email or not verification_code:
        return Response({"detail": "Email and verification code are required."}, status=status.HTTP_400_BAD_REQUEST)
    otp_instance = VerificationCode.objects.filter(email=email, otp_for="email_verification").order_by("-created_at").first()
    if not otp_instance:
        return Response({"detail": "No verification code found for this email."}, status=status.HTTP_404_NOT_FOUND)
    is_valid, error_message = otp_instance.check_code(verification_code)
    if not is_valid:
        return Response({"detail": error_message}, status=status.HTTP_400_BAD_REQUEST)
    try:
        user = UserBase.objects.get(email=email)
    except UserBase.DoesNotExist:
        return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
    user.is_email_verified = True
    user.save()
    otp_instance.delete()
    return Response({"detail": "Email verified successfully."}, status=status.HTTP_200_OK)


@extend_schema(
    summary="Resend Verification Code",
    request=ResendVerificationSerializer,
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description="Success"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Authentication"]
)
@api_view(["POST"])
@permission_classes([AllowAny])
def resend_verification_code(request):
    email = request.data.get("email")
    if not email:
        return Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        user = UserBase.objects.get(email=email)
        if user.is_email_verified:
            return Response({"detail": "Email is already verified."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            VerificationCode.generate(email=email, otp_for="email_verification")
        except ValidationError as e:
            return Response({"detail": e.message if hasattr(e, "message") else str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": "Verification code resent successfully."}, status=status.HTTP_200_OK)
    except UserBase.DoesNotExist:
        return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)


@extend_schema(
    summary="Forgot Password",
    request=ResendVerificationSerializer,
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description="Success"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Authentication"]
)
@api_view(["POST"])
@permission_classes([AllowAny])
def forgot_password(request):
    email = request.data.get("email")
    if not email:
        return Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        user = UserBase.objects.get(email=email)
        if not user.password:
            return Response({"detail": "Password is not set."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            VerificationCode.generate(email=email, otp_for="password_reset")
        except ValidationError as e:
            return Response({"detail": e.message if hasattr(e, "message") else str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": "Password reset code sent successfully."}, status=status.HTTP_200_OK)
    except UserBase.DoesNotExist:
        return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)


@extend_schema(
    summary="Check Email Exists",
    request=CheckEmailSerializer,
    responses={
        200: OpenApiResponse(response=CheckEmailResponseSerializer, description="Success"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
    },
    tags=["Authentication"]
)
@api_view(["POST"])
@permission_classes([AllowAny])
def check_email_exists(request):
    email = request.data.get("email")
    if not email:
        return Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        user = UserBase.objects.get(email=email)
        can_reapply = user.is_trainer and user.is_rejected
        return Response({"exists": True, "can_reapply": can_reapply}, status=status.HTTP_200_OK)
    except UserBase.DoesNotExist:
        return Response({"exists": False, "can_reapply": False}, status=status.HTTP_200_OK)


@extend_schema(
    description="Verify password reset OTP before allowing the user to set a new password",
    summary="Verify Forgot Password OTP",
    request=EmailVerificationSerializer,
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description='Success'),
        400: OpenApiResponse(response=MessageResponseSerializer, description='Bad Request'),
        404: OpenApiResponse(response=MessageResponseSerializer, description='Not Found'),
    },
    tags=['Authentication']
)
@api_view(["POST"])
@permission_classes([AllowAny])
def verify_forgot_password(request):
    email = request.data.get('email')
    verification_code = request.data.get('verification_code')

    if not email or not verification_code:
        return Response({'detail': 'Email and verification code are required.'}, status=status.HTTP_400_BAD_REQUEST)

    otp_instance = VerificationCode.objects.filter(
        email=email,
        otp_for='password_reset'
    ).order_by('-created_at').first()

    if not otp_instance:
        return Response({'detail': 'No verification code found for this email.'}, status=status.HTTP_404_NOT_FOUND)

    is_valid, error_message = otp_instance.check_code(verification_code)
    if not is_valid:
        return Response({'detail': error_message}, status=status.HTTP_400_BAD_REQUEST)

    otp_instance.delete()
    # Grant a 15-minute window to complete the password reset
    cache.set(f"pwd_reset_verified_{email}", True, 60 * 15)

    return Response({'detail': 'Verification successful. You may now reset your password.'}, status=status.HTTP_200_OK)


@extend_schema(
    summary="Reset Password",
    request=ChangePasswordSerializer,
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description="Success"),
        400: OpenApiResponse(response=MessageResponseSerializer, description="Bad Request"),
        403: OpenApiResponse(response=MessageResponseSerializer, description="Forbidden"),
        404: OpenApiResponse(response=MessageResponseSerializer, description="Not Found"),
    },
    tags=["Authentication"]
)
@api_view(["POST"])
@permission_classes([AllowAny])
def reset_password(request):
    email = request.data.get("email")
    new_password = request.data.get("new_password")
    confirm_new_password = request.data.get("confirm_new_password")
    if not all([email, new_password, confirm_new_password]):
        return Response({"detail": "All fields are required."}, status=status.HTTP_400_BAD_REQUEST)
    if not cache.get(f"pwd_reset_verified_{email}"):
        return Response({"detail": "OTP verification required before resetting password."}, status=status.HTTP_403_FORBIDDEN)
    if new_password != confirm_new_password:
        return Response({"detail": "Passwords do not match."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        user = UserBase.objects.get(email=email)
        user.set_password(new_password)
        user.save()
        cache.delete(f"pwd_reset_verified_{email}")
        return Response({"detail": "Password reset successfully."}, status=status.HTTP_200_OK)
    except UserBase.DoesNotExist:
        return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
