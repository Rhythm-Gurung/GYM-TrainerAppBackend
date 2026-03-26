from django.db.models import Q, Sum

from payment.models import ClientRefund, KhaltiPayment, TrainerPayout


def dashboard_callback(request, context):
    """
    Injects financial summary data into the admin dashboard context.
    Called by Unfold via UNFOLD['DASHBOARD_CALLBACK'].
    """
    def _rs(paisa):
        return f'Rs. {(paisa or 0) / 100:,.2f}'

    # ── Incoming payments ────────────────────────────────────────────────────
    payment_agg = KhaltiPayment.objects.filter(
        status=KhaltiPayment.STATUS_COMPLETED,
    ).aggregate(
        total_collected = Sum('amount'),
        total_platform_fee = Sum('platform_fee'),
    )
    total_collected    = payment_agg['total_collected']    or 0
    total_platform_fee = payment_agg['total_platform_fee'] or 0

    # ── Outgoing: trainer payouts ────────────────────────────────────────────
    payout_agg = TrainerPayout.objects.aggregate(
        transferred = Sum('amount', filter=Q(status=TrainerPayout.STATUS_TRANSFERRED)),
        pending     = Sum('amount', filter=Q(status=TrainerPayout.STATUS_PENDING)),
        on_hold     = Sum('amount', filter=Q(status=TrainerPayout.STATUS_ON_HOLD)),
    )
    transferred_to_trainers = payout_agg['transferred'] or 0
    pending_trainer_payouts = payout_agg['pending']     or 0
    on_hold_payouts         = payout_agg['on_hold']     or 0

    # ── Outgoing: client refunds ─────────────────────────────────────────────
    refund_agg = ClientRefund.objects.aggregate(
        processed = Sum('amount', filter=Q(status=ClientRefund.STATUS_PROCESSED)),
        pending   = Sum('amount', filter=Q(status=ClientRefund.STATUS_PENDING)),
    )
    processed_refunds = refund_agg['processed'] or 0
    pending_refunds   = refund_agg['pending']   or 0

    # ── Admin's Khalti balance ───────────────────────────────────────────────
    # What's sitting in admin Khalti right now:
    # Total collected − already transferred to trainers − already refunded to clients
    khalti_balance = total_collected - transferred_to_trainers - processed_refunds

    context['payment_summary'] = {
        'khalti_balance':          _rs(khalti_balance),
        'khalti_balance_raw':      khalti_balance,
        'total_collected':         _rs(total_collected),
        'platform_fee':            _rs(total_platform_fee),
        'pending_trainer_payouts': _rs(pending_trainer_payouts),
        'pending_trainer_count':   TrainerPayout.objects.filter(status=TrainerPayout.STATUS_PENDING).count(),
        'on_hold':                 _rs(on_hold_payouts),
        'pending_refunds':         _rs(pending_refunds),
        'pending_refund_count':    ClientRefund.objects.filter(status=ClientRefund.STATUS_PENDING).count(),
    }

    return context
