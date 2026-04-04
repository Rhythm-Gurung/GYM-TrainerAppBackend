from datetime import timedelta
import json

from django.db.models import (
    Avg,
    Count,
    Q,
    Sum,
)
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.core.serializers.json import DjangoJSONEncoder

from payment.models import ClientRefund, KhaltiPayment, TrainerPayout
from scheduling.models import Booking
from system.models import UserBase


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

    # ── Analytics Section ────────────────────────────────────────────────────
    now = timezone.now()
    last_30_days = now - timedelta(days=30)
    last_60_days = now - timedelta(days=60)
    last_7_days = now - timedelta(days=7)
    last_14_days = now - timedelta(days=14)

    # ── Revenue Trends (Last 30 Days) ────────────────────────────────────────
    revenue_by_day = (
        KhaltiPayment.objects
        .filter(status=KhaltiPayment.STATUS_COMPLETED, created_at__gte=last_30_days)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(total=Sum('amount'))
        .order_by('day')
    )
    
    # ── Booking Trends (Last 30 Days) ────────────────────────────────────────
    bookings_by_day = (
        Booking.objects
        .filter(created_at__gte=last_30_days)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )

    # ── Booking Status Distribution ──────────────────────────────────────────
    booking_status_counts = (
        Booking.objects
        .values('status')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    # ── Session Completion Rate ──────────────────────────────────────────────
    total_sessions = Booking.objects.count()
    completed_sessions = Booking.objects.filter(status=Booking.STATUS_COMPLETED).count()
    cancelled_sessions = Booking.objects.filter(status=Booking.STATUS_CANCELLED).count()
    
    completion_rate = (completed_sessions / total_sessions * 100) if total_sessions > 0 else 0

    # ── Payment Success Rate ─────────────────────────────────────────────────
    payment_status_counts = (
        KhaltiPayment.objects
        .values('status')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    
    total_payments = KhaltiPayment.objects.count()
    completed_payments = KhaltiPayment.objects.filter(status=KhaltiPayment.STATUS_COMPLETED).count()
    payment_success_rate = (completed_payments / total_payments * 100) if total_payments > 0 else 0

    # ── Trainer Growth (Last 60 Days) ────────────────────────────────────────
    trainer_signups_by_day = (
        UserBase.objects
        .filter(is_trainer=True, created_at__gte=last_60_days)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )

    # ── Client Growth (Last 60 Days) ─────────────────────────────────────────
    client_signups_by_day = (
        UserBase.objects
        .filter(is_trainer=False, created_at__gte=last_60_days)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )

    # ── Top Trainers by Revenue (Last 30 Days) ───────────────────────────────
    top_trainers_by_revenue = (
        Booking.objects
        .filter(created_at__gte=last_30_days, status=Booking.STATUS_COMPLETED)
        .values('trainer__full_name', 'trainer__id')
        .annotate(total_revenue=Sum('total_amount'))
        .order_by('-total_revenue')[:10]
    )

    # ── Top Trainers by Booking Count (Last 30 Days) ─────────────────────────
    top_trainers_by_bookings = (
        Booking.objects
        .filter(created_at__gte=last_30_days)
        .values('trainer__full_name', 'trainer__id')
        .annotate(booking_count=Count('id'))
        .order_by('-booking_count')[:10]
    )

    # ── Advanced KPIs ─────────────────────────────────────────────────────────
    
    # Average Booking Value
    avg_booking_value = Booking.objects.filter(
        status__in=[Booking.STATUS_COMPLETED, Booking.STATUS_CONFIRMED]
    ).aggregate(avg=Avg('total_amount'))['avg'] or 0

    # Revenue Last 7 Days vs Previous 7 Days
    revenue_last_7 = KhaltiPayment.objects.filter(
        status=KhaltiPayment.STATUS_COMPLETED,
        created_at__gte=last_7_days
    ).aggregate(total=Sum('amount'))['total'] or 0

    revenue_prev_7 = KhaltiPayment.objects.filter(
        status=KhaltiPayment.STATUS_COMPLETED,
        created_at__gte=last_14_days,
        created_at__lt=last_7_days
    ).aggregate(total=Sum('amount'))['total'] or 0

    revenue_trend = ((revenue_last_7 - revenue_prev_7) / revenue_prev_7 * 100) if revenue_prev_7 > 0 else 0

    # Bookings Last 7 Days vs Previous 7 Days
    bookings_last_7 = Booking.objects.filter(created_at__gte=last_7_days).count()
    bookings_prev_7 = Booking.objects.filter(created_at__gte=last_14_days, created_at__lt=last_7_days).count()
    bookings_trend = ((bookings_last_7 - bookings_prev_7) / bookings_prev_7 * 100) if bookings_prev_7 > 0 else 0

    # Total Trainers & Active Trainers
    total_trainers = UserBase.objects.filter(is_trainer=True).count()
    approved_trainers = UserBase.objects.filter(
        is_trainer=True, 
        is_admin_approved=True,
        verification_status='verified'
    ).count()
    pending_trainers = UserBase.objects.filter(
        is_trainer=True,
        is_admin_approved=False,
        is_rejected=False
    ).count()

    # Trainer Utilization (trainers with bookings in last 30 days)
    active_trainers_30d = Booking.objects.filter(
        created_at__gte=last_30_days
    ).values('trainer').distinct().count()
    
    trainer_utilization = (active_trainers_30d / approved_trainers * 100) if approved_trainers > 0 else 0

    # Total Clients
    total_clients = UserBase.objects.filter(is_trainer=False).count()
    active_clients_30d = Booking.objects.filter(
        created_at__gte=last_30_days
    ).values('client').distinct().count()

    # Conversion Rate (registered clients who made at least 1 booking)
    clients_with_bookings = Booking.objects.values('client').distinct().count()
    conversion_rate = (clients_with_bookings / total_clients * 100) if total_clients > 0 else 0

    # Refund Rate
    total_completed_bookings = Booking.objects.filter(status=Booking.STATUS_COMPLETED).count()
    total_refunded_bookings = Booking.objects.filter(status=Booking.STATUS_REFUNDED).count()
    refund_rate = (total_refunded_bookings / (total_completed_bookings + total_refunded_bookings) * 100) if (total_completed_bookings + total_refunded_bookings) > 0 else 0

    # Convert querysets to JSON-serializable format
    def serialize_data(queryset, date_field='day'):
        """Convert queryset with date fields to JSON-serializable format"""
        result = []
        for item in queryset:
            data = dict(item)
            if date_field in data and data[date_field]:
                data[date_field] = data[date_field].isoformat()
            result.append(data)
        return result

    context['analytics'] = {
        # Revenue trends
        'revenue_by_day': serialize_data(revenue_by_day),
        'revenue_trend': round(revenue_trend, 1),
        'revenue_trend_abs': round(abs(revenue_trend), 1),
        'revenue_last_7': revenue_last_7,
        
        # Booking trends
        'bookings_by_day': serialize_data(bookings_by_day),
        'bookings_trend': round(bookings_trend, 1),
        'bookings_trend_abs': round(abs(bookings_trend), 1),
        'bookings_last_7': bookings_last_7,
        'booking_status_counts': list(booking_status_counts),
        
        # Completion rates
        'completion_rate': round(completion_rate, 1),
        'completed_sessions': completed_sessions,
        'cancelled_sessions': cancelled_sessions,
        'total_sessions': total_sessions,
        
        # Payment stats
        'payment_status_counts': list(payment_status_counts),
        'payment_success_rate': round(payment_success_rate, 1),
        'total_payments': total_payments,
        'completed_payments': completed_payments,
        
        # Trainer stats
        'trainer_signups_by_day': serialize_data(trainer_signups_by_day),
        'client_signups_by_day': serialize_data(client_signups_by_day),
        'top_trainers_by_revenue': list(top_trainers_by_revenue),
        'top_trainers_by_bookings': list(top_trainers_by_bookings),
        
        # Advanced KPIs
        'avg_booking_value': round(float(avg_booking_value), 2) if avg_booking_value else 0,
        'total_trainers': total_trainers,
        'approved_trainers': approved_trainers,
        'pending_trainers': pending_trainers,
        'trainer_utilization': round(trainer_utilization, 1),
        'active_trainers_30d': active_trainers_30d,
        'total_clients': total_clients,
        'active_clients_30d': active_clients_30d,
        'conversion_rate': round(conversion_rate, 1),
        'clients_with_bookings': clients_with_bookings,
        'refund_rate': round(refund_rate, 1),
    }
    
    # JSON encode for JavaScript
    context['analytics_json'] = json.dumps(context['analytics'], cls=DjangoJSONEncoder)

    return context
