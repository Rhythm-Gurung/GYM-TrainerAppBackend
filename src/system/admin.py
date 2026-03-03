import base64

from django.contrib import admin
from django.shortcuts import render
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from system.models import TrainerCertification, UserBase, UserBaseAddress, VerificationCode
from system.tasks import send_emails

PROBLEM_AREA_LABELS = {
    'profile_image':  'Profile image is missing, unclear, or not a real photo',
    'id_proof':       'ID proof is invalid, expired, or unreadable',
    'certifications': 'Certifications are invalid, expired, or unverifiable',
    'experience':     'Years of experience appears inaccurate or unsupported',
    'bio':            'Bio is incomplete or insufficient',
    'contact':        'Contact information is invalid',
    'duplicate':      'Duplicate or suspicious account detected',
}


# ──────────────────────────────────────────
# Admin Actions
# ──────────────────────────────────────────

@admin.action(description="✅ Approve selected trainer accounts")
def approve_trainers(modeladmin, request, queryset):
    pending = list(queryset.filter(is_trainer=True, is_admin_approved=False))
    updated = queryset.filter(is_trainer=True).update(is_admin_approved=True, is_rejected=False)

    email_errors = []
    for trainer in pending:
        try:
            send_emails(
                template='trainer_approved.html',
                recipient_list=[trainer.email],
                subject='Your GymJam trainer account has been approved!',
                context={
                    'full_name': trainer.full_name or trainer.username,
                    'email': trainer.email,
                }
            )
        except Exception as e:
            email_errors.append(f"{trainer.email}: {e}")

    if email_errors:
        modeladmin.message_user(
            request,
            f"{updated} trainer(s) approved. Email failed — {'; '.join(email_errors)}",
            level='error'
        )
    else:
        modeladmin.message_user(request, f"{updated} trainer(s) approved and notified via email.")


@admin.action(description="❌ Reject selected trainer accounts")
def reject_trainers(modeladmin, request, queryset):
    trainers = queryset.filter(is_trainer=True)

    # Step 2: form submitted — process rejection
    if 'apply' in request.POST:
        selected_areas = request.POST.getlist('problem_areas')
        reason = request.POST.get('reason', '').strip()

        if not selected_areas and not reason:
            modeladmin.message_user(
                request,
                "Please select at least one problem area or provide a reason before rejecting.",
                level='error'
            )
            # Re-render the form
            return render(request, 'admin/reject_trainer_form.html', {
                'trainers': list(trainers),
                'problem_area_labels': PROBLEM_AREA_LABELS,
            })

        problem_area_texts = [PROBLEM_AREA_LABELS[k] for k in selected_areas if k in PROBLEM_AREA_LABELS]

        rejected = list(trainers)
        trainers.update(is_admin_approved=False, is_rejected=True)

        email_errors = []
        for trainer in rejected:
            try:
                send_emails(
                    template='trainer_rejected.html',
                    recipient_list=[trainer.email],
                    subject='Update on your GymJam trainer application',
                    context={
                        'full_name': trainer.full_name or trainer.username,
                        'email': trainer.email,
                        'problem_areas': problem_area_texts,
                        'reason': reason,
                    }
                )
            except Exception as e:
                email_errors.append(f"{trainer.email}: {e}")

        if email_errors:
            modeladmin.message_user(
                request,
                f"{len(rejected)} trainer(s) rejected. Email failed — {'; '.join(email_errors)}",
                level='error'
            )
        else:
            modeladmin.message_user(
                request,
                f"{len(rejected)} trainer(s) rejected and notified via email."
            )
        return None

    # Step 1: show the intermediate rejection form
    return render(request, 'admin/reject_trainer_form.html', {
        'trainers': list(trainers),
        'problem_area_labels': PROBLEM_AREA_LABELS,
    })


# ──────────────────────────────────────────
# Inlines
# ──────────────────────────────────────────

class TrainerCertificationInline(admin.TabularInline):
    model = TrainerCertification
    extra = 0
    can_delete = False
    readonly_fields = ('cert_preview', 'name', 'content_type', 'created_at')
    fields = ('cert_preview', 'name', 'content_type', 'created_at')

    def cert_preview(self, obj):
        if obj.image:
            data = base64.b64encode(bytes(obj.image)).decode('utf-8')
            return format_html(
                '<img src="data:{};base64,{}" style="max-height:160px;max-width:220px;border-radius:6px;" />',
                obj.content_type, data
            )
        return "No image"
    cert_preview.short_description = 'Certificate'


# ──────────────────────────────────────────
# User Admin
# ──────────────────────────────────────────

@admin.register(UserBase)
class UserBaseAdmin(ModelAdmin):
    list_display = (
        'email', 'username', 'is_trainer', 'approval_status',
        'is_email_verified', 'is_active', 'created_at'
    )
    list_filter = ('is_trainer', 'is_admin_approved', 'is_email_verified', 'is_active', 'is_staff')
    search_fields = ('email', 'username', 'first_name', 'last_name', 'full_name')
    readonly_fields = (
        'uuid', 'created_at', 'updated_at', 'last_login',
        'profile_image_preview', 'id_proof_preview',
    )
    actions = [approve_trainers, reject_trainers]
    inlines = [TrainerCertificationInline]

    fieldsets = (
        ('Account', {
            'fields': ('uuid', 'email', 'username', 'password', 'is_active', 'is_staff', 'is_superuser')
        }),
        ('Verification & Approval', {
            'fields': ('is_email_verified', 'is_trainer', 'is_admin_approved')
        }),
        ('Personal Info', {
            'classes': ('collapse',),
            'fields': ('first_name', 'last_name', 'dob', 'profile_image', 'profile_image_preview')
        }),
        ('Trainer Profile', {
            'classes': ('collapse',),
            'fields': (
                'full_name', 'contact_no', 'bio',
                'expertise_categories', 'years_of_experience',
                'pricing_per_session', 'session_type',
            )
        }),
        ('Trainer ID Proof', {
            'classes': ('collapse',),
            'fields': ('id_proof_preview', 'id_proof_content_type')
        }),
        ('Social Login', {
            'classes': ('collapse',),
            'fields': ('social_provider', 'social_provider_id')
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at', 'last_login')
        }),
    )

    def approval_status(self, obj):
        if not obj.is_trainer:
            return format_html('<span style="color:gray;">{}</span>', 'N/A (Client)')
        if obj.is_admin_approved:
            return format_html('<span style="color:green;font-weight:bold;">{}</span>', '✅ Approved')
        return format_html('<span style="color:orange;font-weight:bold;">{}</span>', '⏳ Pending')
    approval_status.short_description = 'Approval'

    def profile_image_preview(self, obj):
        if obj.profile_image:
            return format_html(
                '<img src="{}" style="max-height:200px;max-width:200px;border-radius:8px;" />',
                obj.profile_image.url
            )
        return "No profile image"
    profile_image_preview.short_description = 'Profile Image'

    def id_proof_preview(self, obj):
        if obj.id_proof:
            data = base64.b64encode(bytes(obj.id_proof)).decode('utf-8')
            content_type = obj.id_proof_content_type or 'image/jpeg'
            return format_html(
                '<img src="data:{};base64,{}" style="max-height:400px;max-width:600px;border-radius:8px;" />',
                content_type, data
            )
        return "No ID proof uploaded"
    id_proof_preview.short_description = 'ID Proof'


# ──────────────────────────────────────────
# Other Models
# ──────────────────────────────────────────

@admin.register(TrainerCertification)
class TrainerCertificationAdmin(ModelAdmin):
    list_display = ('user', 'name', 'content_type', 'created_at')
    search_fields = ('user__email', 'user__username', 'name')
    readonly_fields = ('cert_preview', 'user', 'name', 'content_type', 'created_at')

    def cert_preview(self, obj):
        if obj.image:
            data = base64.b64encode(bytes(obj.image)).decode('utf-8')
            return format_html(
                '<img src="data:{};base64,{}" style="max-height:400px;max-width:600px;border-radius:8px;" />',
                obj.content_type, data
            )
        return "No image"
    cert_preview.short_description = 'Certificate Image'


@admin.register(UserBaseAddress)
class UserBaseAddressAdmin(ModelAdmin):
    list_display = ('user', 'city', 'country', 'is_default')
    search_fields = ('user__email', 'city', 'country')


@admin.register(VerificationCode)
class VerificationCodeAdmin(ModelAdmin):
    list_display = ('email', 'code', 'otp_for', 'is_email_sent', 'expiration_time', 'created_at')
    list_filter = ('otp_for', 'is_email_sent')
    search_fields = ('email',)
    readonly_fields = ('code', 'created_at', 'updated_at')
