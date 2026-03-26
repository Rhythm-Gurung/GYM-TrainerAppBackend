from datetime import date

from django.contrib import admin
from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.urls import reverse
from django.utils.html import escape, mark_safe
from unfold.admin import ModelAdmin

from scheduling.models import Booking, DateOverride, ScheduleOverride, TrainerScheduleScope, WeeklyScheduleDay
from system.models import UserBase

# ──────────────────────────────────────────────────────────────────────────────
# Proxy model — lets us register a separate "Trainer Schedules" section without
# touching the system.UserBase admin.
# ──────────────────────────────────────────────────────────────────────────────

class TrainerScheduleProxy(UserBase):
    class Meta:
        proxy        = True
        verbose_name = 'Trainer Schedule'
        verbose_name_plural = 'Trainer Schedules'
        app_label    = 'scheduling'


# ──────────────────────────────────────────────────────────────────────────────
# Shared constants
# ──────────────────────────────────────────────────────────────────────────────

DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
DAY_SHORT = ['Sun',    'Mon',    'Tue',     'Wed',       'Thu',      'Fri',    'Sat']

MODE_BADGE = {
    'online':  '<span style="background:#2563eb;color:#fff;padding:1px 8px;border-radius:10px;font-size:11px;">Online</span>',
    'offline': '<span style="background:#16a34a;color:#fff;padding:1px 8px;border-radius:10px;font-size:11px;">Offline</span>',
    'both':    '<span style="background:#d97706;color:#fff;padding:1px 8px;border-radius:10px;font-size:11px;">Both</span>',
}


def _scope_label(effective_from, effective_until):
    """Return a human label for the scope based on the date range."""
    if effective_until is None:
        return 'Forever'
    delta = (effective_until - effective_from).days
    if delta >= 364:
        return 'Yearly'
    if delta >= 27:
        months = round(delta / 30)
        return f'{months} month{"s" if months != 1 else ""}'
    weeks = max(1, round(delta / 7))
    return f'{weeks} week{"s" if weeks != 1 else ""}'


def _render_schedule_days(schedule_list):
    """Render a compact 7-day schedule card row from a list of dicts (JSON or model rows)."""
    # Normalise: accepts both dicts (from JSONField) and WeeklyScheduleDay instances
    day_map = {}
    for item in schedule_list:
        if isinstance(item, dict):
            day_map[item['day_of_week']] = item
        else:
            day_map[item.day_of_week] = item

    cards = ''
    for dow in range(7):
        item    = day_map.get(dow)
        enabled = item['enabled'] if isinstance(item, dict) else (item.enabled if item else False)
        mode    = item['session_mode'] if isinstance(item, dict) else (item.session_mode if item else 'both')
        slots   = item['slots'] if isinstance(item, dict) else (list(item.slots.all()) if item else [])

        if enabled:
            hdr = 'background:#7c3aed;color:#fff;'
            dot = '●'
        else:
            hdr = 'background:#f3f4f6;color:#9ca3af;'
            dot = '○'

        if enabled:
            body = MODE_BADGE.get(mode, '')
            if slots:
                for s in slots:
                    if isinstance(s, dict):
                        slot_str = f'{s["start_time"]} – {s["end_time"]}'
                    else:
                        slot_str = f'{s.start_time:%H:%M} – {s.end_time:%H:%M}'
                    body += (
                        f'<div style="margin-top:5px;padding:3px 8px;'
                        f'background:#f5f3ff;border-left:3px solid #7c3aed;'
                        f'border-radius:3px;font-size:12px;font-family:monospace;">'
                        f'{slot_str}</div>'
                    )
            else:
                body += '<div style="color:#9ca3af;font-size:12px;margin-top:5px;">No slots</div>'
        else:
            body = '<div style="color:#9ca3af;font-size:12px;margin-top:4px;">Unavailable</div>'

        cards += (
            f'<div style="flex:1;min-width:110px;max-width:150px;border:1px solid #e5e7eb;'
            f'border-radius:8px;overflow:hidden;">'
            f'<div style="padding:6px 10px;{hdr}font-weight:600;font-size:12px;">'
            f'{dot} {DAY_NAMES[dow]}</div>'
            f'<div style="padding:7px 10px;">{body}</div>'
            f'</div>'
        )

    return f'<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:4px;">{cards}</div>'


# ──────────────────────────────────────────────────────────────────────────────
# Admin
# ──────────────────────────────────────────────────────────────────────────────

@admin.register(TrainerScheduleProxy)
class TrainerScheduleAdmin(ModelAdmin):
    # ── list view ──────────────────────────────────────────────────────────────
    list_display  = ('trainer_name', 'trainer_email', 'active_days_chips', 'scope_badge', 'upcoming_blocks', 'override_count')
    search_fields = ('email', 'username', 'first_name', 'last_name')
    ordering      = ('email',)

    # ── detail view ───────────────────────────────────────────────────────────
    readonly_fields = (
        'schedule_scope_display',
        'weekly_schedule_display',
        'date_overrides_display',
        'schedule_overrides_display',
    )

    def get_fields(self, _request, _obj=None):
        return (
            'schedule_scope_display',
            'weekly_schedule_display',
            'date_overrides_display',
            'schedule_overrides_display',
        )

    def get_fieldsets(self, _request, _obj=None):
        return (
            ('Schedule Scope', {
                'fields': ('schedule_scope_display',),
            }),
            ('Weekly Schedule', {
                'fields': ('weekly_schedule_display',),
            }),
            ('Blocked Dates', {
                'fields': ('date_overrides_display',),
            }),
            ('Date-Range Schedule Overrides', {
                'fields': ('schedule_overrides_display',),
            }),
        )

    # ── permissions ───────────────────────────────────────────────────────────
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_trainer=True)

    # ── list columns ──────────────────────────────────────────────────────────

    def trainer_name(self, obj):
        name = f"{obj.first_name} {obj.last_name}".strip()
        return name or '—'
    trainer_name.short_description = 'Name'

    def trainer_email(self, obj):
        return obj.email
    trainer_email.short_description = 'Email'

    def active_days_chips(self, obj):
        enabled = WeeklyScheduleDay.objects.filter(user=obj, enabled=True).values_list('day_of_week', flat=True)
        if not enabled:
            return mark_safe('<span style="color:#9ca3af;font-size:12px;">No schedule</span>')
        chips = ''.join(
            f'<span style="display:inline-block;padding:2px 8px;margin:1px 2px;'
            f'background:#7c3aed;color:#fff;border-radius:12px;font-size:11px;">'
            f'{DAY_SHORT[d]}</span>'
            for d in sorted(enabled)
        )
        return mark_safe(chips)
    active_days_chips.short_description = 'Active Days'

    def scope_badge(self, obj):
        try:
            scope = obj.schedule_scope
        except TrainerScheduleScope.DoesNotExist:
            return mark_safe('<span style="color:#9ca3af;font-size:12px;">Not set</span>')

        today  = date.today()
        label  = _scope_label(scope.effective_from, scope.effective_until)
        f_str  = scope.effective_from.strftime('%b %d, %Y')
        u_str  = scope.effective_until.strftime('%b %d, %Y') if scope.effective_until else 'Forever'

        if scope.effective_until and scope.effective_until < today:
            color = '#9ca3af'
            tag   = '<span style="font-size:10px;color:#ef4444;"> (expired)</span>'
        elif scope.effective_from > today:
            days_until_start = (scope.effective_from - today).days
            color = '#d97706'
            tag   = f'<span style="font-size:10px;color:#d97706;"> (starts in {days_until_start}d)</span>'
        elif scope.effective_until:
            days_left = (scope.effective_until - today).days
            color = '#7c3aed'
            tag   = f'<span style="font-size:10px;color:#6b7280;"> ({days_left}d left)</span>'
        else:
            color = '#16a34a'
            tag   = ''

        return mark_safe(
            f'<span style="color:{color};font-weight:600;font-size:12px;">{label}</span>'
            f'<br><span style="color:#9ca3af;font-size:11px;">{f_str} → {u_str}</span>'
            f'{tag}'
        )
    scope_badge.short_description = 'Schedule Scope'

    def upcoming_blocks(self, obj):
        count = DateOverride.objects.filter(user=obj, date__gte=date.today()).count()
        if count == 0:
            return mark_safe('<span style="color:#9ca3af;">None</span>')
        return mark_safe(
            f'<span style="color:#e67e22;font-weight:600;">{count} blocked</span>'
        )
    upcoming_blocks.short_description = 'Upcoming Blocks'

    def override_count(self, obj):
        total  = ScheduleOverride.objects.filter(user=obj).count()
        active = ScheduleOverride.objects.filter(user=obj, start_date__lte=date.today(), end_date__gte=date.today()).count()
        if total == 0:
            return mark_safe('<span style="color:#9ca3af;">None</span>')
        badge = (
            f'<span style="color:#2563eb;font-weight:600;">{active} active</span> / '
            f'<span style="color:#9ca3af;">{total} total</span>'
        )
        return mark_safe(badge)
    override_count.short_description = 'Date-Range Overrides'

    # ── detail sections ───────────────────────────────────────────────────────

    def schedule_scope_display(self, obj):
        try:
            scope = obj.schedule_scope
        except TrainerScheduleScope.DoesNotExist:
            return mark_safe(
                '<p style="color:#9ca3af;font-style:italic;">'
                'This trainer has not saved a schedule yet.</p>'
            )

        today   = date.today()
        f_str   = scope.effective_from.strftime('%B %d, %Y')
        u_str   = scope.effective_until.strftime('%B %d, %Y') if scope.effective_until else 'Forever (no end date)'
        label   = _scope_label(scope.effective_from, scope.effective_until)

        if scope.effective_until and scope.effective_until < today:
            status_html = '<span style="background:#fee2e2;color:#dc2626;padding:2px 10px;border-radius:10px;font-size:12px;font-weight:600;">Expired</span>'
        elif scope.effective_from > today:
            days_until_start = (scope.effective_from - today).days
            status_html = (
                f'<span style="background:#fef3c7;color:#d97706;padding:2px 10px;border-radius:10px;font-size:12px;font-weight:600;">'
                f'Not yet active — starts in {days_until_start} day{"s" if days_until_start != 1 else ""}</span>'
            )
        elif scope.effective_until is None:
            status_html = '<span style="background:#dcfce7;color:#16a34a;padding:2px 10px;border-radius:10px;font-size:12px;font-weight:600;">Active — Forever</span>'
        else:
            days_left = (scope.effective_until - today).days
            status_html = (
                f'<span style="background:#ede9fe;color:#7c3aed;padding:2px 10px;border-radius:10px;font-size:12px;font-weight:600;">'
                f'Active — {days_left} day{"s" if days_left != 1 else ""} remaining</span>'
            )

        return mark_safe(
            '<div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:4px;">'

            '<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 20px;min-width:180px;">'
            '<div style="color:#6b7280;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Plan Type</div>'
            f'<div style="font-size:18px;font-weight:700;color:#111827;">{escape(label)}</div>'
            '</div>'

            '<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 20px;min-width:180px;">'
            '<div style="color:#6b7280;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Effective From</div>'
            f'<div style="font-size:15px;font-weight:600;color:#111827;">{f_str}</div>'
            '</div>'

            '<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 20px;min-width:180px;">'
            '<div style="color:#6b7280;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Effective Until</div>'
            f'<div style="font-size:15px;font-weight:600;color:#111827;">{u_str}</div>'
            '</div>'

            f'<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 20px;min-width:180px;display:flex;align-items:center;">'
            f'{status_html}'
            '</div>'

            '</div>'
        )
    schedule_scope_display.short_description = 'Schedule Scope'

    def weekly_schedule_display(self, obj):
        days = list(WeeklyScheduleDay.objects.filter(user=obj).prefetch_related('slots'))
        if not days:
            return mark_safe('<p style="color:#9ca3af;font-style:italic;">No weekly schedule set.</p>')
        return mark_safe(_render_schedule_days(days))
    weekly_schedule_display.short_description = 'Weekly Schedule'

    def date_overrides_display(self, obj):
        overrides = list(DateOverride.objects.filter(user=obj).order_by('date'))
        if not overrides:
            return mark_safe('<p style="color:#9ca3af;">No blocked dates.</p>')

        today = date.today()
        rows  = ''
        for ov in overrides:
            past        = ov.date < today
            row_style   = 'opacity:0.45;' if past else ''
            date_style  = 'color:#9ca3af;' if past else 'color:#e67e22;font-weight:600;'
            reason_html = escape(ov.reason) if ov.reason else '<em style="color:#9ca3af;">—</em>'
            status      = (
                '<span style="color:#9ca3af;font-size:11px;">past</span>'
                if past
                else '<span style="color:#e67e22;font-size:11px;">upcoming</span>'
            )
            rows += (
                f'<tr style="{row_style}">'
                f'<td style="padding:7px 14px;{date_style}">{ov.date}</td>'
                f'<td style="padding:7px 14px;">{reason_html}</td>'
                f'<td style="padding:7px 14px;">{status}</td>'
                f'</tr>'
            )

        return mark_safe(
            '<div style="overflow-x:auto;">'
            '<table style="border-collapse:collapse;font-size:13px;width:100%;min-width:400px;">'
            '<thead><tr style="border-bottom:2px solid #e5e7eb;">'
            '<th style="padding:7px 14px;text-align:left;">Date</th>'
            '<th style="padding:7px 14px;text-align:left;">Reason</th>'
            '<th style="padding:7px 14px;text-align:left;">Status</th>'
            '</tr></thead>'
            f'<tbody>{rows}</tbody>'
            '</table></div>'
        )
    date_overrides_display.short_description = 'Blocked Dates'

    def schedule_overrides_display(self, obj):
        overrides = list(ScheduleOverride.objects.filter(user=obj).order_by('start_date'))
        if not overrides:
            return mark_safe('<p style="color:#9ca3af;">No date-range schedule overrides.</p>')

        today   = date.today()
        sections = ''

        for so in overrides:
            is_past   = so.end_date < today
            is_active = so.start_date <= today <= so.end_date
            is_future = so.start_date > today

            if is_active:
                badge = '<span style="background:#dcfce7;color:#16a34a;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:600;">Active now</span>'
                border = '#16a34a'
            elif is_future:
                badge = '<span style="background:#ede9fe;color:#7c3aed;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:600;">Upcoming</span>'
                border = '#7c3aed'
            else:
                badge = '<span style="background:#f3f4f6;color:#9ca3af;padding:2px 10px;border-radius:10px;font-size:11px;">Past</span>'
                border = '#e5e7eb'

            opacity   = 'opacity:0.55;' if is_past else ''
            days_span = (so.end_date - so.start_date).days + 1

            header = (
                f'<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;'
                f'padding:10px 14px;background:#f9fafb;border-bottom:1px solid #e5e7eb;">'
                f'<span style="font-weight:600;font-size:13px;color:#111827;">'
                f'{so.start_date} → {so.end_date}</span>'
                f'<span style="color:#9ca3af;font-size:12px;">({days_span} day{"s" if days_span != 1 else ""})</span>'
                f'{badge}'
                f'</div>'
            )

            schedule_html = _render_schedule_days(so.schedule)

            sections += (
                f'<div style="{opacity}border:1px solid {border};border-radius:8px;'
                f'overflow:hidden;margin-bottom:12px;">'
                f'{header}'
                f'<div style="padding:12px 14px;">{schedule_html}</div>'
                f'</div>'
            )

        return mark_safe(f'<div style="margin-top:4px;">{sections}</div>')
    schedule_overrides_display.short_description = 'Date-Range Schedule Overrides'


# ──────────────────────────────────────────────────────────────────────────────
# Booking admin
# ──────────────────────────────────────────────────────────────────────────────

STATUS_COLORS = {
    'pending':        ('#d97706', '#fffbeb'),
    'accepted':       ('#7c3aed', '#ede9fe'),
    'confirmed':      ('#16a34a', '#f0fdf4'),
    'cancelled':      ('#dc2626', '#fef2f2'),
    'refund_pending': ('#ea580c', '#fff7ed'),
    'refunded':       ('#6b7280', '#f9fafb'),
    'completed':      ('#2563eb', '#eff6ff'),
}


def _action_mark_completed(modeladmin, request, queryset):
    """
    Admin action: mark confirmed bookings as completed.
    Releases the ON_HOLD 70% final payout → PENDING so admin can transfer it.
    """
    from django.utils import timezone
    from payment.models import TrainerPayout

    completed_count = 0
    released_count  = 0
    for booking in queryset.filter(status='confirmed'):
        booking.status = Booking.STATUS_COMPLETED
        booking.save(update_fields=['status', 'updated_at'])
        released = TrainerPayout.objects.filter(
            booking=booking,
            payout_type=TrainerPayout.TYPE_FINAL,
            status=TrainerPayout.STATUS_ON_HOLD,
        ).update(status=TrainerPayout.STATUS_PENDING)
        completed_count += 1
        released_count  += released

    modeladmin.message_user(
        request,
        f'{completed_count} booking(s) marked as completed. '
        f'{released_count} final 70% payout(s) released and now pending transfer to trainer.',
    )

_action_mark_completed.short_description = 'Mark as Completed (releases 70%% final payout to trainer)'


@admin.register(Booking)
class BookingAdmin(ModelAdmin):
    list_display   = (
        'id',
        'status_badge',
        'client_name',
        'client_email',
        'trainer_name',
        'date',
        'start_time',
        'end_time',
        'session_mode',
        'total_amount',
    )
    list_filter    = ('status', 'session_mode', 'date')
    search_fields  = (
        'client__email',
        'client__username',
        'client__first_name',
        'client__last_name',
        'client__full_name',
        'trainer__email',
        'trainer__username',
        'trainer__first_name',
        'trainer__last_name',
        'trainer__full_name',
    )
    readonly_fields = ('created_at', 'updated_at', 'cancelled_by', 'cancel_reason', 'financial_summary')
    ordering       = ('-date', '-start_time')
    list_select_related = ('client', 'trainer')
    actions        = [_action_mark_completed]

    def save_model(self, request, obj, form, change):
        """
        When admin saves a booking as completed via the detail view,
        release any on-hold 70% final payouts to pending — same as the list action.
        """
        from payment.models import TrainerPayout
        prev_status = Booking.objects.filter(pk=obj.pk).values_list('status', flat=True).first()
        super().save_model(request, obj, form, change)
        if obj.status == Booking.STATUS_COMPLETED and prev_status != Booking.STATUS_COMPLETED:
            released = TrainerPayout.objects.filter(
                booking=obj,
                payout_type=TrainerPayout.TYPE_FINAL,
                status=TrainerPayout.STATUS_ON_HOLD,
            ).update(status=TrainerPayout.STATUS_PENDING)
            if released:
                self.message_user(
                    request,
                    f'Booking marked completed — 70% final payout ({released} record) '
                    f'is now Pending Transfer. Go to Payment → Trainer Payouts to send it.',
                )

    fieldsets = (
        (None, {'fields': ('trainer', 'client', 'date', 'start_time', 'end_time', 'session_mode', 'status', 'notes', 'total_amount')}),
        ('Financial Summary', {'fields': ('financial_summary',)}),
        ('Cancellation', {'fields': ('cancelled_by', 'cancel_reason')}),
        ('Timestamps',   {'fields': ('created_at', 'updated_at')}),
    )

    def status_badge(self, obj):
        color, bg = STATUS_COLORS.get(obj.status, ('#374151', '#f9fafb'))
        label = obj.get_status_display()
        return mark_safe(
            f'<span style="background:{bg};color:{color};padding:2px 10px;'
            f'border-radius:10px;font-size:11px;font-weight:600;">{escape(label)}</span>'
        )
    status_badge.short_description = 'Status'

    def client_name(self, obj):
        client = getattr(obj, 'client', None)
        if not client:
            return '-'

        full_name = (getattr(client, 'full_name', '') or '').strip()
        if full_name:
            return full_name

        first = (getattr(client, 'first_name', '') or '').strip()
        last = (getattr(client, 'last_name', '') or '').strip()
        name = f'{first} {last}'.strip()
        if name:
            return name

        return getattr(client, 'username', '') or getattr(client, 'email', '') or '-'
    client_name.short_description = 'Client'

    def client_email(self, obj):
        client = getattr(obj, 'client', None)
        return getattr(client, 'email', '-') if client else '-'
    client_email.short_description = 'Client Email'

    def trainer_name(self, obj):
        trainer = getattr(obj, 'trainer', None)
        if not trainer:
            return '-'

        full_name = (getattr(trainer, 'full_name', '') or '').strip()
        if full_name:
            return full_name

        first = (getattr(trainer, 'first_name', '') or '').strip()
        last = (getattr(trainer, 'last_name', '') or '').strip()
        name = f'{first} {last}'.strip()
        if name:
            return name

        return getattr(trainer, 'username', '') or getattr(trainer, 'email', '') or '-'
    trainer_name.short_description = 'Trainer'

    def financial_summary(self, obj):
        from payment.models import ClientRefund, KhaltiPayment, TrainerPayout

        payment = obj.payments.filter(status=KhaltiPayment.STATUS_COMPLETED).first()
        if not payment:
            initiated = obj.payments.filter(status=KhaltiPayment.STATUS_INITIATED).exists()
            if initiated:
                return mark_safe('<p style="color:#d97706;">Payment initiated but not yet completed.</p>')
            return mark_safe('<p style="color:#9ca3af;">No payment recorded for this booking.</p>')

        total   = payment.amount
        fee     = payment.platform_fee or int(total * 0.05)
        advance = int(total * 0.25)
        final   = int(total * 0.70)

        def _rs(paisa):
            return f'Rs. {paisa / 100:,.2f}'

        def _payout_row(label, amount, payout_obj):
            if not payout_obj:
                return f'<tr><td style="padding:5px 14px 5px 0;color:#6b7280;">{label}</td><td style="color:#9ca3af;">—</td></tr>'
            status_colours = {
                'pending':     ('#d97706', '#fffbeb'),
                'on_hold':     ('#6b7280', '#f9fafb'),
                'transferred': ('#16a34a', '#f0fdf4'),
                'cancelled':   ('#dc2626', '#fef2f2'),
            }
            sc, bg = status_colours.get(payout_obj.status, ('#374151', '#f9fafb'))
            badge = (
                f'<span style="background:{bg};color:{sc};padding:1px 8px;'
                f'border-radius:8px;font-size:11px;font-weight:600;">'
                f'{payout_obj.get_status_display()}</span>'
            )
            ref = f'<br><span style="color:#9ca3af;font-size:11px;">Ref: {payout_obj.transfer_reference}</span>' if payout_obj.transfer_reference else ''
            return (
                f'<tr>'
                f'<td style="padding:5px 14px 5px 0;color:#6b7280;">{label}</td>'
                f'<td style="font-weight:700;">{_rs(amount)}</td>'
                f'<td style="padding-left:12px;">{badge}{ref}</td>'
                f'</tr>'
            )

        advance_payout = obj.trainer_payouts.filter(payout_type=TrainerPayout.TYPE_ADVANCE).first()
        final_payout   = obj.trainer_payouts.filter(payout_type=TrainerPayout.TYPE_FINAL).first()
        refund_obj     = ClientRefund.objects.filter(payment=payment).first()

        html = (
            '<table style="border-collapse:collapse;font-size:13px;width:100%;max-width:600px;">'
            '<tr style="border-bottom:1px solid #e5e7eb;">'
            '<th style="padding:5px 14px 5px 0;text-align:left;color:#374151;">Item</th>'
            '<th style="text-align:left;color:#374151;">Amount</th>'
            '<th style="padding-left:12px;text-align:left;color:#374151;">Status</th>'
            '</tr>'
            f'<tr><td style="padding:5px 14px 5px 0;color:#6b7280;">Total Paid by Client</td>'
            f'<td style="font-weight:700;color:#111827;">{_rs(total)}</td><td></td></tr>'
            f'<tr><td style="padding:5px 14px 5px 0;color:#6b7280;">5% Platform Fee (kept)</td>'
            f'<td style="font-weight:700;color:#7c3aed;">{_rs(fee)}</td><td></td></tr>'
        )
        html += _payout_row('25% Advance → Trainer', advance, advance_payout)
        html += _payout_row('70% Final → Trainer', final, final_payout)

        if refund_obj:
            refund_colours = {'pending': ('#dc2626', '#fef2f2'), 'processed': ('#16a34a', '#f0fdf4')}
            rc, rb = refund_colours.get(refund_obj.status, ('#374151', '#f9fafb'))
            refund_badge = (
                f'<span style="background:{rb};color:{rc};padding:1px 8px;'
                f'border-radius:8px;font-size:11px;font-weight:600;">'
                f'{refund_obj.get_status_display()}</span>'
            )
            ref = f'<br><span style="color:#9ca3af;font-size:11px;">Ref: {refund_obj.refund_reference}</span>' if refund_obj.refund_reference else ''
            html += (
                f'<tr style="background:#fef2f2;">'
                f'<td style="padding:5px 14px 5px 0;color:#dc2626;font-weight:600;">Refund 70% → Client</td>'
                f'<td style="font-weight:700;color:#dc2626;">{_rs(refund_obj.amount)}</td>'
                f'<td style="padding-left:12px;">{refund_badge}{ref}</td>'
                f'</tr>'
            )

        html += '</table>'
        return mark_safe(html)
    financial_summary.short_description = 'Financial Summary'


# ──────────────────────────────────────────────────────────────────────────────
# Grouped booking admin — one row per (client, trainer) pair
# ──────────────────────────────────────────────────────────────────────────────

def _display_name(user):
    if not user:
        return '-'
    full = (getattr(user, 'full_name', '') or '').strip()
    if full:
        return full
    first = (getattr(user, 'first_name', '') or '').strip()
    last  = (getattr(user, 'last_name',  '') or '').strip()
    name  = f'{first} {last}'.strip()
    return name or getattr(user, 'username', '') or getattr(user, 'email', '') or '-'


class BookingGroupProxy(Booking):
    """Proxy used solely to register a second admin view with a different label."""
    class Meta:
        proxy        = True
        verbose_name = 'Booking Request'
        verbose_name_plural = 'Booking Requests (Grouped)'
        app_label    = 'scheduling'


@admin.register(BookingGroupProxy)
class BookingGroupAdmin(ModelAdmin):
    list_display         = ('client_col', 'trainer_col', 'pending_badge', 'confirmed_badge', 'total_badge', 'latest_date_col', 'view_sessions_link')
    list_display_links   = None          # no row-level edit page — use the link column
    search_fields        = (
        'client__email', 'client__first_name', 'client__last_name',
        'trainer__email', 'trainer__first_name', 'trainer__last_name',
    )
    list_select_related  = ('client', 'trainer')
    show_full_result_count = False

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        # For each (client, trainer) pair, pick the row with the highest id so
        # Django admin has a real model instance to iterate over.  Annotate each
        # of those rows with per-pair counts via correlated subqueries.
        pair_total = (
            Booking.objects
            .filter(client=OuterRef('client'), trainer=OuterRef('trainer'))
            .values('client', 'trainer')
            .annotate(c=Count('id'))
            .values('c')
        )
        pair_pending = (
            Booking.objects
            .filter(client=OuterRef('client'), trainer=OuterRef('trainer'), status='pending')
            .values('client', 'trainer')
            .annotate(c=Count('id'))
            .values('c')
        )
        pair_confirmed = (
            Booking.objects
            .filter(client=OuterRef('client'), trainer=OuterRef('trainer'), status='confirmed')
            .values('client', 'trainer')
            .annotate(c=Count('id'))
            .values('c')
        )
        pair_latest = (
            Booking.objects
            .filter(client=OuterRef('client'), trainer=OuterRef('trainer'))
            .values('client', 'trainer')
            .annotate(d=Max('date'))
            .values('d')
        )

        # One representative id per pair (the latest booking id)
        latest_ids = (
            Booking.objects
            .values('client_id', 'trainer_id')
            .annotate(max_id=Max('id'))
            .values_list('max_id', flat=True)
        )

        return (
            Booking.objects
            .filter(id__in=latest_ids)
            .select_related('client', 'trainer')
            .annotate(
                total_count=Subquery(pair_total[:1]),
                pending_count=Subquery(pair_pending[:1]),
                confirmed_count=Subquery(pair_confirmed[:1]),
                latest_date=Subquery(pair_latest[:1]),
            )
        )

    # ── columns ───────────────────────────────────────────────────────────────

    def client_col(self, obj):
        name  = _display_name(obj.client)
        email = getattr(obj.client, 'email', '') if obj.client else ''
        return mark_safe(
            f'<span style="font-weight:600;">{escape(name)}</span>'
            + (f'<br><span style="color:#9ca3af;font-size:11px;">{escape(email)}</span>' if email else '')
        )
    client_col.short_description = 'Client'

    def trainer_col(self, obj):
        name  = _display_name(obj.trainer)
        email = getattr(obj.trainer, 'email', '') if obj.trainer else ''
        return mark_safe(
            f'<span style="font-weight:600;">{escape(name)}</span>'
            + (f'<br><span style="color:#9ca3af;font-size:11px;">{escape(email)}</span>' if email else '')
        )
    trainer_col.short_description = 'Trainer'

    def pending_badge(self, obj):
        count = obj.pending_count or 0
        if not count:
            return mark_safe('<span style="color:#9ca3af;">—</span>')
        return mark_safe(
            f'<span style="background:#fffbeb;color:#d97706;padding:2px 10px;'
            f'border-radius:10px;font-size:11px;font-weight:600;">{count} pending</span>'
        )
    pending_badge.short_description = 'Pending'
    pending_badge.admin_order_field = 'pending_count'

    def confirmed_badge(self, obj):
        count = obj.confirmed_count or 0
        if not count:
            return mark_safe('<span style="color:#9ca3af;">—</span>')
        return mark_safe(
            f'<span style="background:#f0fdf4;color:#16a34a;padding:2px 10px;'
            f'border-radius:10px;font-size:11px;font-weight:600;">{count} confirmed</span>'
        )
    confirmed_badge.short_description = 'Confirmed'
    confirmed_badge.admin_order_field = 'confirmed_count'

    def total_badge(self, obj):
        count = obj.total_count or 0
        return mark_safe(
            f'<span style="color:#6b7280;font-size:12px;">{count} total</span>'
        )
    total_badge.short_description = 'Total Sessions'
    total_badge.admin_order_field = 'total_count'

    def latest_date_col(self, obj):
        d = getattr(obj, 'latest_date', None) or obj.date
        return mark_safe(f'<span style="font-size:12px;color:#374151;">{d}</span>')
    latest_date_col.short_description = 'Latest Session'
    latest_date_col.admin_order_field = 'latest_date'

    def view_sessions_link(self, obj):
        url = (
            reverse('admin:scheduling_booking_changelist')
            + f'?client__id__exact={obj.client_id}&trainer__id__exact={obj.trainer_id}'
        )
        total = obj.total_count or 1
        return mark_safe(
            f'<a href="{url}" style="background:#7c3aed;color:#fff;padding:3px 12px;'
            f'border-radius:6px;font-size:11px;font-weight:600;text-decoration:none;">'
            f'View {total} session{"s" if total != 1 else ""} →</a>'
        )
    view_sessions_link.short_description = 'Sessions'

