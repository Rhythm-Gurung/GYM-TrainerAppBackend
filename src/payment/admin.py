import requests
from django.conf import settings
from django.contrib import admin
from django.db.models import Q, Sum
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import escape, format_html, mark_safe
from unfold.admin import ModelAdmin

from payment.models import ClientRefund, KhaltiPayment, TrainerPayout


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _badge(label, color, bg):
    return mark_safe(
        f'<span style="background:{bg};color:{color};padding:2px 10px;'
        f'border-radius:10px;font-size:11px;font-weight:600;">{escape(label)}</span>'
    )


def _rs(paisa):
    return f'Rs. {paisa / 100:,.2f}'


# ─────────────────────────────────────────────────────────────────────────────
# KhaltiPayment admin
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(KhaltiPayment)
class KhaltiPaymentAdmin(ModelAdmin):
    list_display    = ['id', 'booking_link', 'status_badge', 'amount_col', 'breakdown_col', 'transaction_id', 'created_at']
    list_filter     = ['status']
    search_fields   = ['pidx', 'transaction_id', 'booking__id']
    readonly_fields = ['pidx', 'transaction_id', 'amount', 'platform_fee', 'status',
                       'khalti_response', 'financial_breakdown', 'created_at', 'updated_at']
    ordering        = ['-created_at']

    fieldsets = (
        ('Payment Info', {'fields': ('booking', 'pidx', 'transaction_id', 'status')}),
        ('Financial Breakdown', {'fields': ('financial_breakdown',)}),
        ('Raw Response', {'fields': ('khalti_response',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )

    def booking_link(self, obj):
        return format_html(
            '<a href="/admin/scheduling/booking/{}/change/">Booking #{}</a>',
            obj.booking_id, obj.booking_id,
        )
    booking_link.short_description = 'Booking'

    def amount_col(self, obj):
        return mark_safe(f'<strong>{_rs(obj.amount)}</strong>')
    amount_col.short_description = 'Total Paid'

    def breakdown_col(self, obj):
        if obj.status != KhaltiPayment.STATUS_COMPLETED:
            return mark_safe('<span style="color:#9ca3af;">—</span>')
        adv  = int(obj.amount * 0.25)
        held = int(obj.amount * 0.70)
        fee  = obj.platform_fee or int(obj.amount * 0.05)
        return mark_safe(
            f'<span style="font-size:11px;color:#16a34a;">▲ {_rs(adv)} advance</span>'
            f'&nbsp; <span style="font-size:11px;color:#2563eb;">⏸ {_rs(held)} held</span>'
            f'&nbsp; <span style="font-size:11px;color:#7c3aed;">★ {_rs(fee)} fee</span>'
        )
    breakdown_col.short_description = 'Breakdown'

    def status_badge(self, obj):
        colours = {
            'completed': ('#16a34a', '#f0fdf4'),
            'initiated': ('#2563eb', '#eff6ff'),
            'failed':    ('#dc2626', '#fef2f2'),
            'cancelled': ('#d97706', '#fffbeb'),
            'expired':   ('#6b7280', '#f9fafb'),
        }
        color, bg = colours.get(obj.status, ('#374151', '#f9fafb'))
        return _badge(obj.get_status_display(), color, bg)
    status_badge.short_description = 'Status'

    def financial_breakdown(self, obj):
        if obj.status != KhaltiPayment.STATUS_COMPLETED:
            return mark_safe('<p style="color:#9ca3af;">Available after payment is completed.</p>')
        total   = obj.amount
        advance = int(total * 0.25)
        held    = int(total * 0.70)
        fee     = obj.platform_fee or int(total * 0.05)
        rows = [
            ('Total Collected from Client (via Khalti)', _rs(total),   '#111827'),
            ('25% Advance → Trainer',                    _rs(advance), '#16a34a'),
            ('70% Session Fee → Trainer (after session)',_rs(held),    '#2563eb'),
            ('5% Platform Fee (retained)',               _rs(fee),     '#7c3aed'),
        ]
        html = '<table style="border-collapse:collapse;font-size:13px;width:auto;">'
        for label, value, color in rows:
            html += (
                f'<tr>'
                f'<td style="padding:6px 16px 6px 0;color:#6b7280;">{label}</td>'
                f'<td style="padding:6px 0;font-weight:700;color:{color};">{value}</td>'
                f'</tr>'
            )
        html += '</table>'
        return mark_safe(html)
    financial_breakdown.short_description = 'Financial Breakdown'


# ─────────────────────────────────────────────────────────────────────────────
# TrainerPayout admin
# ─────────────────────────────────────────────────────────────────────────────

def _initiate_payout_to_trainer(modeladmin, request, queryset):
    """
    For each selected PENDING payout:
      1. Initiates a Khalti payment using the TRAINER's merchant key.
      2. Shows a clickable Khalti payment link in the admin message.
      3. Admin clicks the link, pays using a dummy Khalti number (e.g. 9800000001).
      4. Khalti redirects to /api/payment/trainer-payout/verify/ which marks the payout as Transferred.
    """
    eligible = queryset.filter(
        status=TrainerPayout.STATUS_PENDING,
    ).select_related('booking__trainer')

    if not eligible.exists():
        modeladmin.message_user(
            request,
            'No eligible payouts. Only "Pending Transfer" records can be initiated.',
            level='warning',
        )
        return

    initiate_url = f'{settings.KHALTI_GATEWAY_URL}/api/v2/epayment/initiate/'
    headers      = {
        'Authorization': f'key {settings.KHALTI_TRAINER_SECRET_KEY}',
        'Content-Type':  'application/json',
    }
    return_url   = request.build_absolute_uri('/api/payment/trainer-payout/verify/')

    for payout in eligible:
        trainer      = payout.booking.trainer
        trainer_name = trainer.full_name or f'{trainer.first_name} {trainer.last_name}'.strip() or trainer.email
        payout_label = payout.get_payout_type_display()

        payload = {
            'return_url':          return_url,
            'website_url':         settings.KHALTI_WEBSITE_URL,
            'amount':              payout.amount,
            'purchase_order_id':   f'TRPAYOUT-{payout.id}',
            'purchase_order_name': f'{payout_label} – {trainer_name} – Booking #{payout.booking_id}',
            'customer_info': {
                'name':  'SETu Admin',
                'email': request.user.email,
                'phone': '9800000000',
            },
        }

        try:
            resp = requests.post(initiate_url, json=payload, headers=headers, timeout=10)
            data = resp.json()
        except requests.exceptions.Timeout:
            modeladmin.message_user(request, f'Payout #{payout.id}: Khalti timeout. Try again.', level='error')
            continue
        except Exception as e:
            modeladmin.message_user(request, f'Payout #{payout.id}: {e}', level='error')
            continue

        if resp.status_code == 200 and data.get('payment_url'):
            pidx        = data['pidx']
            payment_url = data['payment_url']
            # Store pidx temporarily so verify endpoint can look up the payout
            payout.transfer_reference = f'pending:{pidx}'
            payout.save(update_fields=['transfer_reference'])
            modeladmin.message_user(
                request,
                mark_safe(
                    f'<strong>Payout #{payout.id}</strong> — {escape(trainer_name)} '
                    f'({_rs(payout.amount)}, {payout_label}): &nbsp;'
                    f'<a href="{payment_url}" target="_blank" '
                    f'style="background:#7c3aed;color:#fff;padding:4px 14px;border-radius:6px;'
                    f'font-weight:600;text-decoration:none;">Pay via Khalti →</a>'
                ),
            )
        else:
            err = data.get('detail') or data.get('message') or str(data)
            modeladmin.message_user(request, f'Payout #{payout.id} failed: {err}', level='error')

_initiate_payout_to_trainer.short_description = 'Send Payout to Trainer via Khalti'


@admin.register(TrainerPayout)
class TrainerPayoutAdmin(ModelAdmin):
    list_display  = ['id', 'booking_link', 'trainer_col', 'client_col', 'payout_type_badge',
                     'status_badge', 'amount_col', 'transfer_reference', 'transferred_at', 'created_at']
    list_filter   = ['status', 'payout_type']
    search_fields = ['booking__id', 'transfer_reference',
                     'booking__trainer__full_name', 'booking__trainer__email',
                     'booking__client__full_name', 'booking__client__email']
    ordering      = ['-created_at']
    actions       = [_initiate_payout_to_trainer]

    fieldsets = (
        ('Action', {'fields': ('payout_action',)}),
        ('Payout Info', {'fields': ('booking', 'payout_type', 'amount', 'status')}),
        ('Transfer Details', {'fields': ('transfer_reference', 'notes', 'transferred_at')}),
        ('Trainer Total Earnings', {'fields': ('trainer_earnings_summary',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )
    readonly_fields = ['booking', 'payout_type', 'amount', 'status', 'payout_action',
                       'transfer_reference', 'transferred_at', 'created_at', 'updated_at',
                       'trainer_earnings_summary']

    # ── Custom URL: handles the "Pay via Khalti" button click from detail page ──

    def get_urls(self):
        return [
            path(
                '<int:payout_id>/pay-via-khalti/',
                self.admin_site.admin_view(self.pay_via_khalti_view),
                name='payment-trainerpayout-pay',
            ),
            path(
                '<int:payout_id>/re-verify/',
                self.admin_site.admin_view(self.re_verify_payout_view),
                name='payment-trainerpayout-reverify',
            ),
        ] + super().get_urls()

    def re_verify_payout_view(self, request, payout_id):
        """
        Manually re-verifies a stuck payout with Khalti.
        Used when the return_url redirect failed after admin paid.
        """
        try:
            payout = TrainerPayout.objects.select_related('booking').get(pk=payout_id)
        except TrainerPayout.DoesNotExist:
            self.message_user(request, 'Payout not found.', level='error')
            return HttpResponseRedirect(reverse('admin:payment_trainerpayout_changelist'))

        ref = payout.transfer_reference or ''
        if not ref.startswith('pending:'):
            self.message_user(
                request,
                f'Payout #{payout_id} has no pending pidx to verify (reference: "{ref}").',
                level='error',
            )
            return HttpResponseRedirect(reverse('admin:payment_trainerpayout_change', args=[payout_id]))

        pidx       = ref[len('pending:'):]
        lookup_url = f'{settings.KHALTI_GATEWAY_URL}/api/v2/epayment/lookup/'
        headers    = {
            'Authorization': f'key {settings.KHALTI_TRAINER_SECRET_KEY}',
            'Content-Type':  'application/json',
        }
        try:
            resp = requests.post(lookup_url, json={'pidx': pidx}, headers=headers, timeout=10)
            data = resp.json()
        except Exception as e:
            self.message_user(request, f'Khalti lookup failed: {e}', level='error')
            return HttpResponseRedirect(reverse('admin:payment_trainerpayout_change', args=[payout_id]))

        if data.get('status') == 'Completed':
            from django.utils import timezone
            payout.status             = TrainerPayout.STATUS_TRANSFERRED
            payout.transferred_at     = timezone.now()
            payout.transfer_reference = data.get('transaction_id') or pidx
            payout.save(update_fields=['status', 'transferred_at', 'transfer_reference'])
            self.message_user(request, f'Payout #{payout_id} verified and marked as Transferred.')
        else:
            self.message_user(
                request,
                f'Khalti says status is "{data.get("status")}" — not completed yet.',
                level='warning',
            )
        return HttpResponseRedirect(reverse('admin:payment_trainerpayout_change', args=[payout_id]))

    def pay_via_khalti_view(self, request, payout_id):
        """Called when admin clicks the Pay via Khalti button on the detail page."""
        try:
            payout = TrainerPayout.objects.select_related('booking__trainer').get(pk=payout_id)
        except TrainerPayout.DoesNotExist:
            self.message_user(request, 'Payout not found.', level='error')
            return HttpResponseRedirect(reverse('admin:payment_trainerpayout_changelist'))

        if payout.status != TrainerPayout.STATUS_PENDING:
            self.message_user(
                request,
                f'Payout #{payout_id} cannot be paid — current status is "{payout.get_status_display()}".',
                level='error',
            )
            return HttpResponseRedirect(
                reverse('admin:payment_trainerpayout_change', args=[payout_id])
            )

        trainer      = payout.booking.trainer
        trainer_name = trainer.full_name or f'{trainer.first_name} {trainer.last_name}'.strip() or trainer.email
        payout_label = payout.get_payout_type_display()
        return_url   = request.build_absolute_uri('/api/payment/trainer-payout/verify/')

        payload = {
            'return_url':          return_url,
            'website_url':         settings.KHALTI_WEBSITE_URL,
            'amount':              payout.amount,
            'purchase_order_id':   f'TRPAYOUT-{payout.id}',
            'purchase_order_name': f'{payout_label} – {trainer_name} – Booking #{payout.booking_id}',
            'customer_info': {
                'name':  'SETu Admin',
                'email': request.user.email,
                'phone': '9800000000',
            },
        }
        headers = {
            'Authorization': f'key {settings.KHALTI_TRAINER_SECRET_KEY}',
            'Content-Type':  'application/json',
        }

        try:
            resp = requests.post(
                f'{settings.KHALTI_GATEWAY_URL}/api/v2/epayment/initiate/',
                json=payload, headers=headers, timeout=10,
            )
            data = resp.json()
        except requests.exceptions.Timeout:
            self.message_user(request, 'Khalti timeout. Try again.', level='error')
            return HttpResponseRedirect(
                reverse('admin:payment_trainerpayout_change', args=[payout_id])
            )
        except Exception as e:
            self.message_user(request, f'Error: {e}', level='error')
            return HttpResponseRedirect(
                reverse('admin:payment_trainerpayout_change', args=[payout_id])
            )

        if resp.status_code == 200 and data.get('payment_url'):
            payout.transfer_reference = f'pending:{data["pidx"]}'
            payout.save(update_fields=['transfer_reference'])
            return HttpResponseRedirect(data['payment_url'])

        err = data.get('detail') or data.get('message') or str(data)
        self.message_user(request, f'Khalti error: {err}', level='error')
        return HttpResponseRedirect(
            reverse('admin:payment_trainerpayout_change', args=[payout_id])
        )

    # ── Detail page fields ────────────────────────────────────────────────────

    def payout_action(self, obj):
        if obj.status == TrainerPayout.STATUS_PENDING:
            pay_url     = reverse('admin:payment-trainerpayout-pay', args=[obj.pk])
            reverify_url = reverse('admin:payment-trainerpayout-reverify', args=[obj.pk])
            trainer     = obj.booking.trainer
            phone       = trainer.contact_no or 'No phone on file'
            has_pending_pidx = (obj.transfer_reference or '').startswith('pending:')

            reverify_btn = (
                f'&nbsp;&nbsp;<a href="{reverify_url}" '
                f'style="display:inline-block;background:#16a34a;color:#fff;padding:8px 20px;'
                f'border-radius:6px;font-weight:600;font-size:14px;text-decoration:none;">'
                f'Re-verify Payment</a>'
                f'<p style="margin:6px 0 0;font-size:11px;color:#16a34a;">'
                f'Already paid? Click this to confirm with Khalti and mark as Transferred.</p>'
            ) if has_pending_pidx else ''

            return mark_safe(
                f'<div style="padding:16px;background:#f5f3ff;border:1px solid #ddd6fe;border-radius:8px;">'
                f'<p style="margin:0 0 6px;font-size:13px;color:#374151;">'
                f'<strong>Ready to send:</strong> {_rs(obj.amount)} → '
                f'<strong style="color:#16a34a;">{escape(obj.booking.trainer.full_name or phone)}</strong>'
                f'&nbsp;·&nbsp;Khalti: <code>{escape(phone)}</code></p>'
                f'<a href="{pay_url}" '
                f'style="display:inline-block;background:#7c3aed;color:#fff;padding:8px 20px;'
                f'border-radius:6px;font-weight:600;font-size:14px;text-decoration:none;">'
                f'Pay Trainer via Khalti →</a>'
                f'{reverify_btn}'
                f'<p style="margin:8px 0 0;font-size:11px;color:#6b7280;">'
                f'You will be redirected to Khalti. Use dummy number 9800000001 to complete payment.</p>'
                f'</div>'
            )
        if obj.status == TrainerPayout.STATUS_ON_HOLD:
            booking_url = reverse('admin:scheduling_booking_change', args=[obj.booking_id])
            return mark_safe(
                f'<div style="padding:16px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;">'
                f'<p style="margin:0 0 6px;font-size:13px;color:#374151;">'
                f'<strong>⏸ On Hold</strong> — This 70% final payout is locked until the session is completed.</p>'
                f'<a href="{booking_url}" '
                f'style="display:inline-block;background:#2563eb;color:#fff;padding:8px 20px;'
                f'border-radius:6px;font-weight:600;font-size:14px;text-decoration:none;">'
                f'Go to Booking #{obj.booking_id} → Mark as Completed</a>'
                f'<p style="margin:8px 0 0;font-size:11px;color:#6b7280;">'
                f'Once the booking is marked completed, this payout will become Pending Transfer.</p>'
                f'</div>'
            )
        if obj.status == TrainerPayout.STATUS_TRANSFERRED:
            return mark_safe(
                f'<div style="padding:16px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;">'
                f'<p style="margin:0;font-size:13px;color:#16a34a;font-weight:600;">'
                f'✓ Payment transferred — {_rs(obj.amount)} sent to trainer on '
                f'{obj.transferred_at.strftime("%b %d, %Y %H:%M") if obj.transferred_at else "—"}.</p>'
                f'</div>'
            )
        if obj.status == TrainerPayout.STATUS_CANCELLED:
            return mark_safe(
                '<div style="padding:12px 16px;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;">'
                '<p style="margin:0;font-size:13px;color:#dc2626;">✗ This payout was cancelled.</p>'
                '</div>'
            )
        return mark_safe('<span style="color:#9ca3af;">—</span>')
    payout_action.short_description = 'Payment Action'

    # ── List view columns ─────────────────────────────────────────────────────

    def booking_link(self, obj):
        return format_html(
            '<a href="/admin/scheduling/booking/{}/change/">Booking #{}</a>',
            obj.booking_id, obj.booking_id,
        )
    booking_link.short_description = 'Booking'

    def trainer_col(self, obj):
        t     = obj.booking.trainer
        name  = t.full_name or f'{t.first_name} {t.last_name}'.strip() or t.email
        phone = f'<br><span style="color:#6b7280;font-size:11px;">{escape(t.contact_no or "No phone")}</span>'
        return mark_safe(f'<strong style="color:#16a34a;">{escape(name)}</strong>{phone}')
    trainer_col.short_description = 'Send To (Trainer)'

    def client_col(self, obj):
        c = obj.booking.client
        return c.full_name or f'{c.first_name} {c.last_name}'.strip() or c.email
    client_col.short_description = 'Paid By (Client)'

    def amount_col(self, obj):
        return mark_safe(f'<strong>{_rs(obj.amount)}</strong>')
    amount_col.short_description = 'Amount'

    def payout_type_badge(self, obj):
        if obj.payout_type == TrainerPayout.TYPE_ADVANCE:
            return _badge('Advance 25%', '#16a34a', '#f0fdf4')
        return _badge('Final 70%', '#2563eb', '#eff6ff')
    payout_type_badge.short_description = 'Type'

    def status_badge(self, obj):
        colours = {
            TrainerPayout.STATUS_PENDING:     ('#d97706', '#fffbeb'),
            TrainerPayout.STATUS_ON_HOLD:     ('#6b7280', '#f9fafb'),
            TrainerPayout.STATUS_TRANSFERRED: ('#16a34a', '#f0fdf4'),
            TrainerPayout.STATUS_CANCELLED:   ('#dc2626', '#fef2f2'),
        }
        color, bg = colours.get(obj.status, ('#374151', '#f9fafb'))
        return _badge(obj.get_status_display(), color, bg)
    status_badge.short_description = 'Status'

    def trainer_earnings_summary(self, obj):
        trainer = obj.booking.trainer
        name    = trainer.full_name or f'{trainer.first_name} {trainer.last_name}'.strip() or trainer.email
        qs      = TrainerPayout.objects.filter(booking__trainer=trainer)
        agg     = qs.aggregate(
            earned  = Sum('amount', filter=Q(status=TrainerPayout.STATUS_TRANSFERRED)),
            pending = Sum('amount', filter=Q(status=TrainerPayout.STATUS_PENDING)),
            on_hold = Sum('amount', filter=Q(status=TrainerPayout.STATUS_ON_HOLD)),
        )
        rows = [
            ('Trainer',                      escape(name),               '#111827'),
            ('Total Transferred to Trainer', _rs(agg['earned']  or 0),  '#16a34a'),
            ('Pending Transfer',             _rs(agg['pending']  or 0), '#d97706'),
            ('On Hold (awaiting session)',   _rs(agg['on_hold']  or 0), '#2563eb'),
        ]
        html = '<table style="border-collapse:collapse;font-size:13px;width:auto;">'
        for label, value, color in rows:
            html += (
                f'<tr>'
                f'<td style="padding:6px 16px 6px 0;color:#6b7280;">{label}</td>'
                f'<td style="padding:6px 0;font-weight:700;color:{color};">{value}</td>'
                f'</tr>'
            )
        html += '</table>'
        return mark_safe(html)
    trainer_earnings_summary.short_description = 'Trainer Earnings Summary'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('booking__trainer', 'booking__client')


# ─────────────────────────────────────────────────────────────────────────────
# ClientRefund admin
# ─────────────────────────────────────────────────────────────────────────────

def _mark_refund_processed(modeladmin, request, queryset):
    updated = queryset.filter(
        status=ClientRefund.STATUS_PENDING,
    ).update(status=ClientRefund.STATUS_PROCESSED, processed_at=timezone.now())
    if updated:
        modeladmin.message_user(request, f'{updated} refund(s) marked as processed.')
    else:
        modeladmin.message_user(request, 'No pending refunds found.', level='warning')

_mark_refund_processed.short_description = 'Mark 70%% Refund as Processed to Client'


@admin.register(ClientRefund)
class ClientRefundAdmin(ModelAdmin):
    list_display  = ['id', 'booking_link', 'client_name', 'status_badge',
                     'amount_col', 'refund_reference', 'processed_at', 'created_at']
    list_filter   = ['status']
    search_fields = ['payment__booking__id', 'refund_reference']
    ordering      = ['-created_at']
    actions       = [_mark_refund_processed]

    fieldsets = (
        ('Refund Info', {'fields': ('payment', 'amount', 'status')}),
        ('Processing Details', {
            'description': 'After refunding the client manually (Khalti P2P / bank), enter the reference here.',
            'fields': ('refund_reference', 'notes', 'processed_at'),
        }),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )
    readonly_fields = ['payment', 'amount', 'status', 'processed_at', 'created_at', 'updated_at']

    def booking_link(self, obj):
        booking_id = obj.payment.booking_id
        return format_html(
            '<a href="/admin/scheduling/booking/{}/change/">Booking #{}</a>',
            booking_id, booking_id,
        )
    booking_link.short_description = 'Booking'

    def client_name(self, obj):
        client = obj.payment.booking.client
        return (
            client.full_name
            or f'{client.first_name} {client.last_name}'.strip()
            or client.email
        )
    client_name.short_description = 'Client'

    def amount_col(self, obj):
        return mark_safe(f'<strong style="color:#dc2626;">{_rs(obj.amount)}</strong>')
    amount_col.short_description = 'Refund Amount'

    def status_badge(self, obj):
        if obj.status == ClientRefund.STATUS_PENDING:
            return _badge('Pending Refund', '#dc2626', '#fef2f2')
        return _badge('Processed', '#16a34a', '#f0fdf4')
    status_badge.short_description = 'Status'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'payment__booking__client',
        )
