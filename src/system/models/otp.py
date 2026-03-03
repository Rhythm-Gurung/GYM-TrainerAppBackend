import random

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from system.tasks import send_emails


class VerificationCode(models.Model):
    code = models.CharField(max_length=6, null=True, blank=True, editable=False)
    email = models.EmailField()
    is_email_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expiration_time = models.DateTimeField(null=True, blank=True)
    otp_for = models.CharField(max_length=255, default='email_verification')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['code', 'email'],
                name='unique_verification_code_per_email'
            )
        ]
        indexes = [
            models.Index(fields=['email', 'otp_for']),
            models.Index(fields=['expiration_time']),
        ]

    def __str__(self):
        return f"Verification code for {self.email} ({self.otp_for})"

    def has_expired(self):
        return self.expiration_time and self.expiration_time < timezone.now()

    def is_valid(self):
        return not self.has_expired()

    def check_code(self, code):
        if self.has_expired():
            return False, 'Verification code has expired.'
        if str(self.code) == str(code):
            return True, None
        return False, 'Invalid verification code.'

    @classmethod
    def generate(cls, email, otp_for='email_verification'):
        from django.db import transaction

        with transaction.atomic():
            # Delete expired codes first
            cls.objects.filter(
                email=email,
                otp_for=otp_for,
                expiration_time__lt=timezone.now()
            ).delete()

            # Check for existing valid codes
            existing_code = cls.objects.select_for_update().filter(
                email=email,
                otp_for=otp_for,
                expiration_time__gte=timezone.now()
            ).first()

            if existing_code:
                raise ValidationError(
                    'A valid verification code already exists for this email. '
                    'Please wait for it to expire or use the existing code.'
                )

            # Limit OTP requests per hour (brute force prevention)
            one_hour_ago = timezone.now() - timezone.timedelta(hours=1)
            recent_count = cls.objects.filter(
                email=email,
                otp_for=otp_for,
                created_at__gte=one_hour_ago
            ).count()
            if recent_count >= 3:
                raise ValidationError('Too many OTP requests. Try again later.')

            # Create new verification code
            verification_code = cls(email=email, otp_for=otp_for)
            verification_code.expiration_time = timezone.now() + timezone.timedelta(minutes=10)
            verification_code.save()

            return verification_code


@receiver(pre_save, sender=VerificationCode)
def generate_verification_code(sender, instance, **kwargs):
    if not instance.code:
        instance.code = str(random.randint(100000, 999999))


@receiver(post_save, sender=VerificationCode)
def send_verification_email(sender, instance, created, **kwargs):
    if created and not instance.is_email_sent:
        if instance.otp_for == 'password_reset':
            subject = 'Password Reset Verification Code'   # CHANGE THIS
            message = 'Use the verification code below to reset your password.'
        else:
            subject = 'Email Verification Code'             # CHANGE THIS
            message = 'Use the verification code below to verify your email.'

        template = 'verification_email.html'
        send_emails(
            template=template,
            recipient_list=[instance.email],
            subject=subject,
            context={
                'verification_code': instance.code,
                'email': instance.email,
                'message': message
            }
        )

        instance.is_email_sent = True
        instance.save(update_fields=['is_email_sent'])
