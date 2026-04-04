import base64

from django.contrib import admin
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import escape, format_html, mark_safe
from unfold.admin import ModelAdmin

from system.models import TrainerCertification, TrainerGalleryImage, TrainerProfileChangeLog, UserBase, UserBaseAddress, VerificationCode
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
    updated = queryset.filter(is_trainer=True).update(
        is_admin_approved=True, is_rejected=False, verification_status='verified'
    )

    email_errors = []
    for trainer in pending:
        try:
            send_emails(
                template='trainer_approved.html',
                recipient_list=[trainer.email],
                subject='Your SETu trainer account has been approved!',
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


@admin.action(description="🔄 Re-verify selected trainer accounts (mark as Verified)")
def reverify_trainers(modeladmin, request, queryset):
    updated = queryset.filter(
        is_trainer=True,
        is_admin_approved=True,
        verification_status='re_verification_required',
    ).update(verification_status='verified')
    if updated:
        modeladmin.message_user(request, f"{updated} trainer(s) re-verified successfully.")
    else:
        modeladmin.message_user(
            request,
            "No eligible trainers found (must be approved and awaiting re-verification).",
            level='warning',
        )


@admin.action(description="❌ Reject re-verification for selected trainers")
def reject_reverification(modeladmin, request, queryset):
    trainers = queryset.filter(
        is_trainer=True,
        is_admin_approved=True,
        verification_status='re_verification_required',
    )

    if 'apply_reverify_reject' in request.POST:
        selected_areas = request.POST.getlist('problem_areas')
        reason = request.POST.get('reason', '').strip()

        if not selected_areas and not reason:
            modeladmin.message_user(
                request,
                "Please select at least one problem area or provide a reason.",
                level='error',
            )
            return render(request, 'admin/reject_trainer_form.html', {
                'trainers': list(trainers),
                'problem_area_labels': PROBLEM_AREA_LABELS,
                'action_flag': 'apply_reverify_reject',
                'action_title': 'Reject Re-verification',
            })

        problem_area_texts = [PROBLEM_AREA_LABELS[k] for k in selected_areas if k in PROBLEM_AREA_LABELS]
        affected = list(trainers)
        trainers.update(verification_status='reverification_rejected')

        email_errors = []
        for trainer in affected:
            try:
                send_emails(
                    template='reverification_rejected.html',
                    recipient_list=[trainer.email],
                    subject='Action required: SETu profile update rejected',
                    context={
                        'full_name': trainer.full_name or trainer.username,
                        'email': trainer.email,
                        'problem_areas': problem_area_texts,
                        'reason': reason,
                    }
                )
            except Exception as e:
                email_errors.append(f"{trainer.email}: {e}")

        msg = f"{len(affected)} trainer(s) re-verification rejected."
        if email_errors:
            modeladmin.message_user(request, f"{msg} Email failed — {'; '.join(email_errors)}", level='error')
        else:
            modeladmin.message_user(request, f"{msg} Trainers notified via email.")
        return None

    if not trainers.exists():
        modeladmin.message_user(
            request,
            "No eligible trainers selected (must be approved and awaiting re-verification).",
            level='warning',
        )
        return None

    return render(request, 'admin/reject_trainer_form.html', {
        'trainers': list(trainers),
        'problem_area_labels': PROBLEM_AREA_LABELS,
        'action_flag': 'apply_reverify_reject',
        'action_title': 'Reject Re-verification',
    })


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
                    subject='Update on your SETu trainer application',
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

class TrainerGalleryInline(admin.TabularInline):
    model = TrainerGalleryImage
    extra = 0
    can_delete = False
    readonly_fields = ('gallery_preview', 'caption', 'content_type', 'created_at')
    fields = ('gallery_preview', 'caption', 'content_type', 'created_at')

    def gallery_preview(self, obj):
        if obj.image:
            data = base64.b64encode(bytes(obj.image)).decode('utf-8')
            return format_html(
                '<img src="data:{};base64,{}" style="max-height:160px;max-width:220px;border-radius:6px;" />',
                obj.content_type, data
            )
        return "No image"
    gallery_preview.short_description = 'Image'


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
        'email', 'username', 'is_trainer', 'approval_status', 'verification_status_display',
        'is_email_verified', 'is_active', 'created_at', 'view_bookings',
    )
    list_filter = ('is_trainer', 'is_admin_approved',  'is_email_verified', 'is_active', 'is_staff', 'verification_status')
    search_fields = ('email', 'username', 'first_name', 'last_name', 'full_name')
    readonly_fields = (
        'uuid', 'created_at', 'updated_at', 'last_login',
        'profile_image_preview', 'id_proof_preview',
        'pending_changes_section', 'bookings_section',
    )
    actions = [approve_trainers, reverify_trainers, reject_reverification, reject_trainers]
    inlines = [TrainerGalleryInline, TrainerCertificationInline]

    fieldsets = (
        ('Account', {
            'fields': ('uuid', 'email', 'username', 'password', 'is_active', 'is_staff', 'is_superuser')
        }),
        ('Verification & Approval', {
            'fields': ('is_email_verified', 'is_trainer', 'is_admin_approved', 'verification_status')
        }),
        ('Pending Changes (Re-verification)', {
            'fields': ('pending_changes_section',),
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
        ('Bookings', {
            'fields': ('bookings_section',),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at', 'last_login')
        }),
    )

    def approval_status(self, obj):
        if not obj.is_trainer:
            return format_html('<span style="color:gray;">{}</span>', 'N/A (Client)')
        if obj.is_rejected:
            return format_html('<span style="color:red;font-weight:bold;">{}</span>', '❌ Rejected')
        if obj.is_admin_approved:
            return format_html('<span style="color:green;font-weight:bold;">{}</span>', '✅ Approved')
        return format_html('<span style="color:orange;font-weight:bold;">{}</span>', '⏳ Pending')
    approval_status.short_description = 'Approval'

    def verification_status_display(self, obj):
        if not obj.is_trainer:
            return format_html('<span style="color:gray;">{}</span>', '—')
        badges = {
            'verified':                 ('green',    '✅ Verified'),
            'pending':                  ('orange',   '⏳ Pending'),
            're_verification_required': ('red',      '🔄 Re-verification Required'),
            'reverification_rejected':  ('#c0392b',  '❌ Rejected (update)'),
        }
        color, label = badges.get(obj.verification_status, ('gray', obj.verification_status))
        return format_html('<span style="color:{};font-weight:bold;">{}</span>', color, label)
    verification_status_display.short_description = 'Verification'

    def view_bookings(self, obj):
        base = reverse('admin:scheduling_booking_changelist')
        param = 'trainer__id__exact' if obj.is_trainer else 'client__id__exact'
        url = f'{base}?{param}={obj.pk}'
        return mark_safe(
            f'<a href="{url}" style="background:#7c3aed;color:#fff;padding:2px 10px;'
            f'border-radius:6px;font-size:11px;font-weight:600;text-decoration:none;">'
            f'Bookings →</a>'
        )
    view_bookings.short_description = 'Bookings'

    def bookings_section(self, obj):
        base = reverse('admin:scheduling_booking_changelist')
        param = 'trainer__id__exact' if obj.is_trainer else 'client__id__exact'
        url = f'{base}?{param}={obj.pk}'
        return mark_safe(
            f'<a href="{url}" style="display:inline-block;background:#7c3aed;color:#fff;'
            f'padding:6px 16px;border-radius:6px;font-size:13px;font-weight:600;'
            f'text-decoration:none;">View all bookings for this user →</a>'
        )
    bookings_section.short_description = 'Bookings'

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

    _ACTION_LABELS = {
        'id_proof_updated':       ('🪪', 'ID Proof Updated'),
        'certification_added':    ('📄', 'Certification Added'),
        'certification_deleted':  ('🗑️', 'Certification Deleted'),
        'profile_fields_updated': ('✏️', 'Profile Fields Updated'),
    }

    _FIELD_LABELS = {
        'years_of_experience': 'Years of Experience',
        'pricing_per_session': 'Pricing per Session',
        'session_type':        'Session Type',
        'expertise_categories': 'Expertise Categories',
    }

    def pending_changes_section(self, obj):
        if not obj.is_trainer:
            return mark_safe('<p style="color:gray;">Not applicable — this is a client account.</p>')

        logs = TrainerProfileChangeLog.objects.filter(user=obj).order_by('-changed_at')[:20]

        if not logs.exists():
            if obj.verification_status == 're_verification_required':
                return mark_safe(
                    '<p style="color:orange;">Re-verification required but no change log recorded '
                    '(changes may have been made before logging was enabled).</p>'
                )
            return mark_safe('<p style="color:gray;">No pending changes recorded.</p>')

        rows = []
        for log in logs:
            icon, label = self._ACTION_LABELS.get(log.action, ('🔧', log.action))
            when = log.changed_at.strftime('%Y-%m-%d %H:%M UTC')

            detail_html = ''
            if log.action == 'profile_fields_updated' and log.changes:
                field_rows = ''
                for field, diff in log.changes.items():
                    field_label = escape(self._FIELD_LABELS.get(field, field))
                    old_val = escape(str(diff.get('old', '—')))
                    new_val = escape(str(diff.get('new', '—')))
                    field_rows += (
                        f'<tr>'
                        f'<td style="padding:2px 8px;color:#555;">{field_label}</td>'
                        f'<td style="padding:2px 8px;color:#c0392b;text-decoration:line-through;">{old_val}</td>'
                        f'<td style="padding:2px 8px;color:#27ae60;">{new_val}</td>'
                        f'</tr>'
                    )
                detail_html = (
                    '<table style="margin-top:4px;border-collapse:collapse;font-size:12px;">'
                    '<tr><th style="padding:2px 8px;text-align:left;color:#333;">Field</th>'
                    '<th style="padding:2px 8px;text-align:left;color:#333;">Old</th>'
                    '<th style="padding:2px 8px;text-align:left;color:#333;">New</th></tr>'
                    + field_rows +
                    '</table>'
                )
            elif log.action in ('certification_added', 'certification_deleted') and log.changes.get('name'):
                cert_name = escape(log.changes['name'])
                detail_html = f'<span style="font-size:12px;color:#555;">File: {cert_name}</span>'

            rows.append(
                f'<li style="margin-bottom:10px;padding:8px 12px;background:#f9f9f9;'
                f'border-left:4px solid #e67e22;border-radius:4px;">'
                f'<strong>{icon} {escape(label)}</strong> '
                f'<span style="color:gray;font-size:12px;">— {when}</span>'
                f'{detail_html}'
                f'</li>'
            )

        status_color = {
            're_verification_required': '#e67e22',
            'reverification_rejected':  '#c0392b',
            'verified':                 '#27ae60',
            'pending':                  '#7f8c8d',
        }.get(obj.verification_status, '#7f8c8d')

        header = format_html(
            '<p style="margin-bottom:8px;">Current status: '
            '<strong style="color:{};">{}</strong></p>',
            status_color,
            obj.get_verification_status_display(),
        )
        list_html = mark_safe(
            '<ul style="margin:0;padding:0;list-style:none;">' + ''.join(rows) + '</ul>'
        )
        return header + list_html

    pending_changes_section.short_description = 'Trainer Change History'


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
