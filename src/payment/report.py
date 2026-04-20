from datetime import timedelta
from io import BytesIO

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.utils import timezone

from payment.models import ClientRefund, KhaltiPayment, TrainerPayout
from scheduling.models import Booking
from system.models import UserBase

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

PERIOD_LABELS = {
    '7d': 'Last 7 Days',
    '30d': 'Last 30 Days',
    '6m': 'Last 6 Months',
    '1y': 'Last Year',
    'overall': 'All Time (Overall)',
}

PURPLE = colors.HexColor('#7c3aed')
LIGHT_PURPLE = colors.HexColor('#f5f3ff')
GREY = colors.HexColor('#6b7280')
GRID_COLOR = colors.HexColor('#e5e7eb')
SEPARATOR_BG = colors.HexColor('#f3f4f6')


def _rs(paisa):
    return f'Rs. {(paisa or 0) / 100:,.2f}'


def _get_date_range(period):
    now = timezone.now()
    offsets = {'7d': 7, '30d': 30, '6m': 180, '1y': 365}
    if period in offsets:
        return now - timedelta(days=offsets[period]), now
    return None, now


def _gather_data(period):
    start, end = _get_date_range(period)

    def in_period(qs):
        return qs.filter(created_at__gte=start, created_at__lte=end) if start else qs

    # ── Users ────────────────────────────────────────────────────────────────
    total_users = UserBase.objects.count()
    total_trainers = UserBase.objects.filter(is_trainer=True).count()
    total_clients = UserBase.objects.filter(is_trainer=False).count()
    approved_trainers = UserBase.objects.filter(is_trainer=True, is_admin_approved=True).count()
    pending_trainers = UserBase.objects.filter(
        is_trainer=True, is_admin_approved=False, is_rejected=False
    ).count()
    new_trainers = in_period(UserBase.objects.filter(is_trainer=True)).count()
    new_clients = in_period(UserBase.objects.filter(is_trainer=False)).count()

    # ── Bookings ─────────────────────────────────────────────────────────────
    bookings_qs = in_period(Booking.objects)
    total_bookings = bookings_qs.count()
    booking_status = list(
        bookings_qs.values('status').annotate(count=Count('id')).order_by('-count')
    )

    # ── Financials (period) ───────────────────────────────────────────────────
    period_payments = in_period(
        KhaltiPayment.objects.filter(status=KhaltiPayment.STATUS_COMPLETED)
    ).aggregate(total=Sum('amount'), fee=Sum('platform_fee'))
    period_revenue = period_payments['total'] or 0
    period_fee = period_payments['fee'] or 0

    # ── Financials (all-time) ─────────────────────────────────────────────────
    all_collected = (
        KhaltiPayment.objects.filter(status=KhaltiPayment.STATUS_COMPLETED)
        .aggregate(total=Sum('amount'))['total'] or 0
    )
    payout_agg = TrainerPayout.objects.aggregate(
        transferred=Sum('amount', filter=Q(status=TrainerPayout.STATUS_TRANSFERRED)),
        pending=Sum('amount', filter=Q(status=TrainerPayout.STATUS_PENDING)),
    )
    refund_agg = ClientRefund.objects.aggregate(
        processed=Sum('amount', filter=Q(status=ClientRefund.STATUS_PROCESSED)),
        pending=Sum('amount', filter=Q(status=ClientRefund.STATUS_PENDING)),
    )
    khalti_balance = (
        all_collected
        - (payout_agg['transferred'] or 0)
        - (refund_agg['processed'] or 0)
    )

    # ── Top trainers by all-time revenue ─────────────────────────────────────
    top_trainers = list(
        Booking.objects.filter(status=Booking.STATUS_COMPLETED)
        .values('trainer__full_name', 'trainer__email')
        .annotate(revenue=Sum('total_amount'), sessions=Count('id'))
        .order_by('-revenue')[:10]
    )

    return {
        'period_label': PERIOD_LABELS.get(period, 'All Time'),
        'start': start,
        'end': end,
        'users': {
            'total': total_users,
            'trainers': total_trainers,
            'clients': total_clients,
            'approved': approved_trainers,
            'pending': pending_trainers,
            'new_trainers': new_trainers,
            'new_clients': new_clients,
        },
        'bookings': {
            'total': total_bookings,
            'by_status': booking_status,
        },
        'financials': {
            'period_revenue': period_revenue,
            'period_fee': period_fee,
            'all_collected': all_collected,
            'payout_transferred': payout_agg['transferred'] or 0,
            'payout_pending': payout_agg['pending'] or 0,
            'refund_processed': refund_agg['processed'] or 0,
            'refund_pending': refund_agg['pending'] or 0,
            'khalti_balance': khalti_balance,
        },
        'top_trainers': top_trainers,
    }


def _table_style(header_rows=1, bold_last=False):
    style = [
        ('BACKGROUND', (0, 0), (-1, header_rows - 1), PURPLE),
        ('TEXTCOLOR', (0, 0), (-1, header_rows - 1), colors.white),
        ('FONTNAME', (0, 0), (-1, header_rows - 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ROWBACKGROUNDS', (0, header_rows), (-1, -1), [colors.white, LIGHT_PURPLE]),
        ('GRID', (0, 0), (-1, -1), 0.5, GRID_COLOR),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
    ]
    if bold_last:
        style += [
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('TEXTCOLOR', (0, -1), (-1, -1), PURPLE),
        ]
    return TableStyle(style)


def _generate_pdf(data):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'ReportTitle', parent=styles['Title'],
        fontSize=26, textColor=PURPLE, spaceAfter=4, alignment=TA_CENTER,
    )
    sub_style = ParagraphStyle(
        'ReportSub', parent=styles['Normal'],
        fontSize=10, textColor=GREY, alignment=TA_CENTER, spaceAfter=3,
    )
    section_style = ParagraphStyle(
        'Section', parent=styles['Heading2'],
        fontSize=13, textColor=PURPLE, spaceBefore=18, spaceAfter=8,
        fontName='Helvetica-Bold',
    )
    footer_style = ParagraphStyle(
        'Footer', parent=styles['Normal'],
        fontSize=8, textColor=GREY, alignment=TA_CENTER, spaceBefore=8,
    )

    story = []

    # ── Title block ───────────────────────────────────────────────────────────
    story.append(Paragraph('SETu Platform Report', title_style))
    story.append(Paragraph(f"Period: <b>{data['period_label']}</b>", sub_style))
    if data['start']:
        date_range = (
            f"{data['start'].strftime('%Y-%m-%d')}  →  {data['end'].strftime('%Y-%m-%d')}"
        )
    else:
        date_range = f"From the beginning  →  {data['end'].strftime('%Y-%m-%d')}"
    story.append(Paragraph(date_range, sub_style))
    story.append(
        Paragraph(f"Generated on {timezone.now().strftime('%Y-%m-%d at %H:%M')}", sub_style)
    )
    story.append(HRFlowable(width='100%', thickness=1.5, color=PURPLE, spaceAfter=16))

    # ── User Statistics ───────────────────────────────────────────────────────
    story.append(Paragraph('User Statistics', section_style))
    u = data['users']
    pl = data['period_label']
    user_rows = [
        ['Metric', 'Count'],
        ['Total Registered Users', str(u['total'])],
        ['Total Trainers', str(u['trainers'])],
        ['  Approved Trainers', str(u['approved'])],
        ['  Pending Approval', str(u['pending'])],
        ['Total Clients', str(u['clients'])],
        [f'New Trainers Registered ({pl})', str(u['new_trainers'])],
        [f'New Clients Registered ({pl})', str(u['new_clients'])],
    ]
    t = Table(user_rows, colWidths=['72%', '28%'])
    ts = _table_style()
    ts.add('ALIGN', (1, 0), (1, -1), 'CENTER')
    t.setStyle(ts)
    story.append(t)
    story.append(Spacer(1, 4))

    # ── Booking Statistics ────────────────────────────────────────────────────
    story.append(Paragraph('Booking Statistics', section_style))
    b = data['bookings']
    status_labels = {
        'pending': 'Pending',
        'accepted': 'Accepted',
        'confirmed': 'Confirmed (Paid)',
        'completed': 'Completed',
        'cancelled': 'Cancelled',
        'refunded': 'Refunded',
        'disputed': 'Disputed',
        'no_show': 'No Show',
        'rejected': 'Rejected',
        'expired': 'Expired',
    }
    booking_rows = [
        ['Booking Status', 'Count'],
        [f'Total Bookings ({pl})', str(b['total'])],
    ]
    for row in b['by_status']:
        label = status_labels.get(row['status'], row['status'].replace('_', ' ').title())
        booking_rows.append([f'  {label}', str(row['count'])])

    t = Table(booking_rows, colWidths=['72%', '28%'])
    ts = _table_style()
    ts.add('ALIGN', (1, 0), (1, -1), 'CENTER')
    ts.add('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold')
    t.setStyle(ts)
    story.append(t)
    story.append(Spacer(1, 4))

    # ── Financial Summary ─────────────────────────────────────────────────────
    story.append(Paragraph('Financial Summary', section_style))
    f = data['financials']
    fin_rows = [
        ['Financial Metric', 'Amount'],
        [f'Revenue Collected ({pl})', _rs(f['period_revenue'])],
        [f'Platform Fee Earned ({pl})', _rs(f['period_fee'])],
        ['', ''],
        ['Total Collected (All Time)', _rs(f['all_collected'])],
        ['Total Paid Out to Trainers', _rs(f['payout_transferred'])],
        ['Pending Trainer Payouts', _rs(f['payout_pending'])],
        ['Total Refunded to Clients', _rs(f['refund_processed'])],
        ['Pending Client Refunds', _rs(f['refund_pending'])],
        ['Admin Khalti Balance', _rs(f['khalti_balance'])],
    ]
    t = Table(fin_rows, colWidths=['60%', '40%'])
    ts = _table_style(bold_last=True)
    ts.add('ALIGN', (1, 0), (1, -1), 'RIGHT')
    ts.add('FONTNAME', (0, 1), (-1, 2), 'Helvetica-Bold')
    # Separator row (empty row index 3)
    ts.add('BACKGROUND', (0, 3), (-1, 3), SEPARATOR_BG)
    ts.add('TOPPADDING', (0, 3), (-1, 3), 2)
    ts.add('BOTTOMPADDING', (0, 3), (-1, 3), 2)
    t.setStyle(ts)
    story.append(t)
    story.append(Spacer(1, 4))

    # ── Top Trainers ──────────────────────────────────────────────────────────
    if data['top_trainers']:
        story.append(Paragraph('Top Trainers by All-Time Revenue', section_style))
        top_rows = [['#', 'Trainer', 'Sessions', 'Revenue']]
        for i, tr in enumerate(data['top_trainers'], 1):
            name = tr.get('trainer__full_name') or tr.get('trainer__email') or '—'
            top_rows.append([
                str(i),
                name,
                str(tr.get('sessions', 0)),
                _rs(tr.get('revenue') or 0),
            ])
        t = Table(top_rows, colWidths=['8%', '52%', '15%', '25%'])
        ts = _table_style()
        ts.add('ALIGN', (0, 0), (0, -1), 'CENTER')
        ts.add('ALIGN', (2, 0), (3, -1), 'RIGHT')
        t.setStyle(ts)
        story.append(t)

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 24))
    story.append(HRFlowable(width='100%', thickness=0.5, color=GREY))
    story.append(
        Paragraph('SETu Platform — Confidential Administrative Report', footer_style)
    )

    doc.build(story)
    buffer.seek(0)
    return buffer


@staff_member_required
def generate_report(request):
    period = request.GET.get('period', 'overall')
    if period not in PERIOD_LABELS:
        period = 'overall'

    data = _gather_data(period)
    buffer = _generate_pdf(data)

    filename = f"setu_report_{period}_{timezone.now().strftime('%Y%m%d_%H%M')}.pdf"
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response
