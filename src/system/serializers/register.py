import json
import os
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from rest_framework import serializers

from system.models import TrainerCertification, VerificationCode

User = get_user_model()

TRAINER_REQUIRED_FIELDS = [
    'full_name', 'contact_no', 'bio',
    'expertise_categories', 'years_of_experience',
    'pricing_per_session', 'session_type',
]

def _save_file_locally(file_bytes, filename):
    local_dir = os.path.join(settings.MEDIA_ROOT, 'trainer_uploads')
    os.makedirs(local_dir, exist_ok=True)
    file_path = os.path.join(local_dir, filename)
    with open(file_path, 'wb') as f:
        f.write(file_bytes)
    return file_path


class UserRegisterSerializer(serializers.Serializer):
    # Common fields
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)
    username = serializers.CharField(max_length=255)
    is_trainer = serializers.BooleanField()

    # Trainer-only text fields
    full_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    contact_no = serializers.CharField(max_length=20, required=False, allow_blank=True)
    bio = serializers.CharField(required=False, allow_blank=True)
    expertise_categories = serializers.CharField(required=False, allow_blank=True)
    years_of_experience = serializers.IntegerField(required=False, min_value=0)
    pricing_per_session = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, min_value=0
    )
    session_type = serializers.ChoiceField(
        choices=['online', 'offline', 'both'], required=False
    )

    # Trainer image fields — sent as multipart files
    profile_image = serializers.ImageField(required=False)
    id_proof = serializers.ImageField(required=False)
    certifications = serializers.ListField(
        child=serializers.ImageField(),
        required=False,
        allow_empty=True,
    )

    def validate_email(self, value):
        validate_email(value)
        existing = User.objects.filter(email=value).first()
        if existing:
            # Rejected trainers are allowed to re-register with the same email
            if existing.is_trainer and existing.is_rejected:
                return value
            raise serializers.ValidationError('Email already registered.')
        return value

    def validate_password(self, value):
        if len(value) < 8:
            raise serializers.ValidationError('Password must be at least 8 characters.')
        if not re.search(r'[^A-Za-z0-9]', value):
            raise serializers.ValidationError('Password must include a special character.')
        if not re.search(r'\d', value):
            raise serializers.ValidationError('Password must include a number.')
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})

        # Multipart form data may provide list values as JSON strings.
        expertise_categories = attrs.get('expertise_categories')
        if isinstance(expertise_categories, str):
            try:
                parsed = json.loads(expertise_categories)
                if not isinstance(parsed, list):
                    raise serializers.ValidationError
                attrs['expertise_categories'] = parsed
            except (json.JSONDecodeError, serializers.ValidationError):
                attrs['expertise_categories'] = [
                    item.strip() for item in expertise_categories.split(',') if item.strip()
                ]

        request = self.context.get('request')
        if request:
            cert_files = request.FILES.getlist('certifications')
            if cert_files:
                attrs['certifications'] = cert_files

        if attrs.get('is_trainer'):
            errors = {}

            for field in TRAINER_REQUIRED_FIELDS:
                if not attrs.get(field) and attrs.get(field) != 0:
                    errors[field] = "This field is required for trainers."

            if not attrs.get('profile_image'):
                errors['profile_image'] = "This field is required for trainers."
            if not attrs.get('id_proof'):
                errors['id_proof'] = "This field is required for trainers."
            if not attrs.get('certifications'):
                errors['certifications'] = "At least one certification is required for trainers."

            if errors:
                raise serializers.ValidationError(errors)

        return attrs

    def create(self, validated_data):
        validated_data.pop('confirm_password')
        email = validated_data.pop('email')
        password = validated_data.pop('password')
        is_trainer = validated_data.get('is_trainer', False)

        # Extract image fields before passing to create_user
        profile_image_file = validated_data.pop('profile_image', None)
        id_proof_file = validated_data.pop('id_proof', None)
        certifications_files = validated_data.pop('certifications', [])

        # Convert id_proof file to bytes for BinaryField storage.
        id_proof_bytes = None
        id_proof_content_type = None
        if id_proof_file:
            id_proof_bytes = id_proof_file.read()
            id_proof_content_type = id_proof_file.content_type or 'image/jpeg'
            validated_data['id_proof'] = id_proof_bytes
            validated_data['id_proof_content_type'] = id_proof_content_type

        if is_trainer:
            validated_data['is_email_verified'] = True
            validated_data['is_admin_approved'] = False
        else:
            validated_data['is_email_verified'] = False
            validated_data['is_admin_approved'] = True

        try:
            with transaction.atomic():
                # If a rejected trainer is re-registering, delete their old record first
                User.objects.filter(email=email, is_trainer=True, is_rejected=True).delete()

                if User.objects.select_for_update().filter(email=email).exists():
                    raise serializers.ValidationError('Email already registered.')

                user = User.objects.create_user(
                    email=email,
                    password=password,
                    **validated_data
                )
                user.is_active = True
                user.save()

                # Save profile_image via Django's ImageField
                if profile_image_file:
                    user.profile_image.save(profile_image_file.name, profile_image_file, save=True)

                # Save id_proof locally as well
                if id_proof_bytes:
                    ext = (id_proof_content_type or 'image/jpeg').split('/')[-1]
                    _save_file_locally(id_proof_bytes, f"id_proof_{user.id}.{ext}")

                if is_trainer:
                    for index, cert_file in enumerate(certifications_files):
                        cert_bytes = cert_file.read()
                        cert_content_type = cert_file.content_type or 'image/jpeg'
                        ext = cert_content_type.split('/')[-1]
                        _save_file_locally(cert_bytes, f"cert_{user.id}_{index}.{ext}")
                        TrainerCertification.objects.create(
                            user=user,
                            name=cert_file.name or f"cert_{index}.{ext}",
                            image=cert_bytes,
                            content_type=cert_content_type,
                        )
                else:
                    VerificationCode.generate(email=email, otp_for='email_verification')

                return user
        except IntegrityError as e:
            raise serializers.ValidationError('Failed to register user.') from e
        except ValidationError as e:
            raise serializers.ValidationError('Failed to register user.') from e
