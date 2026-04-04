import uuid

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.core.cache import cache
from django.core.validators import validate_image_file_extension
from django.db import models



class UserbaseManager(BaseUserManager):

    def create_superuser(self, email, password, **other_fields):
        other_fields.setdefault("is_superuser", True)
        other_fields.setdefault("is_active", True)
        other_fields.setdefault("is_staff", True)
        other_fields.setdefault("is_email_verified", True)
        return self.create_user(email, password, **other_fields)

    def create_user(self, email, password, **other_fields):
        other_fields.setdefault("is_superuser", False)
        if not email:
            raise ValueError("You must provide an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **other_fields)
        user.set_password(password)
        user.save()
        return user


class UserBase(AbstractBaseUser, PermissionsMixin):

    SESSION_TYPE_CHOICES = [
        ('online', 'Online'),
        ('offline', 'Offline'),
        ('both', 'Both'),
    ]

    VERIFICATION_STATUS = [
        ('pending',                    'Pending'),
        ('verified',                   'Verified'),
        ('re_verification_required',   'Re-verification Required'),
        ('reverification_rejected',    'Re-verification Rejected'),
    ]

    uuid = models.UUIDField(unique=True, default=uuid.uuid4)

    email = models.EmailField(unique=True)
    username = models.CharField(max_length=255, unique=True)
    is_email_verified = models.BooleanField(default=False)
    first_name = models.CharField(max_length=255, default='', blank=True)
    last_name = models.CharField(max_length=255, default='', blank=True)
    profile_image = models.ImageField(
        upload_to='users',
        null=True,
        blank=True,
        validators=[validate_image_file_extension]
    )
    dob = models.DateField(null=True, blank=True)

    verification_status = models.CharField(
        max_length=30,
        choices=VERIFICATION_STATUS,
        default='pending',
    )

    # Role
    is_trainer = models.BooleanField(default=False)
    is_admin_approved = models.BooleanField(default=False)  # trainers need manual admin approval
    is_rejected = models.BooleanField(default=False)        # set True when admin explicitly rejects

    # Trainer-specific fields
    full_name = models.CharField(max_length=255, default='', blank=True)
    contact_no = models.CharField(max_length=20, default='', blank=True)
    bio = models.TextField(default='', blank=True)
    expertise_categories = models.JSONField(default=list, blank=True)
    years_of_experience = models.IntegerField(null=True, blank=True)
    pricing_per_session = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    session_type = models.CharField(max_length=10, choices=SESSION_TYPE_CHOICES, null=True, blank=True)
    location = models.CharField(max_length=100, blank=True, default='')

    # Trainer documents (stored as blobs)
    id_proof = models.BinaryField(null=True, blank=True)
    id_proof_content_type = models.CharField(max_length=100, null=True, blank=True)

    agreed_to_policies = models.BooleanField(default=False)
    is_receiving_promotional_email = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    _secret = models.CharField(max_length=255, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    objects = UserbaseManager()

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        db_table = 'system_userbase'
        indexes = [
            models.Index(fields=['-created_at'], name='usr_created_idx'),
            models.Index(fields=['is_trainer'], name='usr_is_trainer_idx'),
            models.Index(fields=['is_trainer', '-created_at'], name='usr_trainer_cr_idx'),
            models.Index(fields=['is_admin_approved'], name='usr_approved_idx'),
            models.Index(fields=['verification_status'], name='usr_verif_idx'),
        ]

    def __str__(self):
        return f'{self.username}'

    def update_cache(self, access_token):
        remaining_time = cache.ttl(access_token)
        cache.set(access_token, self, remaining_time)
        refresh = cache.get(f'refresh_{access_token}')
        remaining_time = cache.ttl(f'refresh_{access_token}')
        cache.set(refresh, self, remaining_time)

    @property
    def is_social_account(self):
        return not bool(self.password)

    @property
    def has_password(self):
        return bool(self.password)


class TrainerCertification(models.Model):
    user = models.ForeignKey(UserBase, on_delete=models.CASCADE, related_name='certifications')
    name = models.CharField(max_length=255, blank=True)
    issuer = models.CharField(max_length=255, blank=True, default='')
    year   = models.PositiveSmallIntegerField(null=True, blank=True)
    image = models.BinaryField()
    content_type = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Trainer Certification'
        verbose_name_plural = 'Trainer Certifications'
        db_table = 'system_trainer_certification'

    def __str__(self):
        return f'{self.user.username} - {self.name}'


class TrainerGalleryImage(models.Model):
    """Trainer's personal gallery — experience photos, etc."""
    user = models.ForeignKey(UserBase, on_delete=models.CASCADE, related_name='gallery_images')
    image = models.BinaryField()
    content_type = models.CharField(max_length=100)
    caption = models.CharField(max_length=255, blank=True, default='')
    collection_id = models.UUIDField(db_index=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Trainer Gallery Image'
        verbose_name_plural = 'Trainer Gallery Images'
        db_table = 'system_trainer_gallery_image'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.username} – gallery #{self.pk}'


class TrainerProfileChangeLog(models.Model):
    """Records each change a trainer makes that triggers re-verification."""

    ACTION_CHOICES = [
        ('id_proof_updated',       'ID Proof Updated'),
        ('certification_added',    'Certification Added'),
        ('certification_deleted',  'Certification Deleted'),
        ('profile_fields_updated', 'Profile Fields Updated'),
    ]

    user = models.ForeignKey(UserBase, on_delete=models.CASCADE, related_name='change_logs')
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    # For profile_fields_updated: {"field": {"old": ..., "new": ...}, ...}
    # For cert changes: {"name": "cert.jpg"}
    # For id_proof: {}
    changes = models.JSONField(default=dict, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Trainer Change Log'
        verbose_name_plural = 'Trainer Change Logs'
        db_table = 'system_trainer_change_log'
        ordering = ['-changed_at']

    def __str__(self):
        return f'{self.user.username} – {self.get_action_display()} at {self.changed_at:%Y-%m-%d %H:%M}'


class UserBaseAddress(models.Model):
    user = models.ForeignKey(UserBase, on_delete=models.CASCADE, related_name='addresses')
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, null=True, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=100)
    is_default = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Address'
        verbose_name_plural = 'User Addresses'
        db_table = 'system_userbase_address'

    def __str__(self):
        return f'{self.user.username} - {self.address_line1}, {self.city}'
