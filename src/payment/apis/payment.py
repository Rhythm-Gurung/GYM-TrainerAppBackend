import requests
from django.conf import settings
from django.db.models import Q, Sum
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from payment.models import KhaltiPayment, TrainerPayout
from payment.serializers.payment import InitiatePaymentSerializer, KhaltiPaymentSerializer
from scheduling.models.schedule import Booking


def _khalti_urls():
    base = settings.KHALTI_GATEWAY_URL
    return (
        f'{base}/api/v2/epayment/initiate/',
        f'{base}/api/v2/epayment/lookup/',
    )

def _khalti_headers():
    return {
        'Authorization': f'key {settings.KHALTI_SECRET_KEY}',
        'Content-Type':  'application/json',
    }


class InitiatePaymentView(APIView):
    """
    POST /api/payment/initiate/
    Body: { "booking_id": <int> }

    Client initiates Khalti payment for a trainer-accepted booking.
    Returns: { "pidx": "...", "payment_url": "..." }
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Initiate Khalti Payment',
        request=InitiatePaymentSerializer,
        responses={201: OpenApiResponse(description='Payment initiated')},
        tags=['Payment'],
    )
    def post(self, request):
        serializer = InitiatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking_id = serializer.validated_data['booking_id']

        try:
            booking = Booking.objects.select_related('client', 'trainer').get(
                id=booking_id,
                client=request.user,
            )
        except Booking.DoesNotExist:
            return Response({'detail': 'Booking not found.'}, status=status.HTTP_404_NOT_FOUND)

        if booking.status != Booking.STATUS_ACCEPTED:
            return Response(
                {'detail': f'Payment can only be initiated for accepted bookings. Current status: {booking.status}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if booking.payments.filter(status=KhaltiPayment.STATUS_COMPLETED).exists():
            return Response(
                {'detail': 'Payment already completed for this booking.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        client  = booking.client
        trainer = booking.trainer

        if not client.contact_no:
            return Response(
                {'detail': 'Your phone number is required to make a payment. Please update your profile.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount_paisa = int(booking.total_amount * 100)
        if amount_paisa <= 0:
            return Response(
                {'detail': 'Booking has no amount set. Contact the trainer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        customer_name = (
            client.full_name
            or f'{client.first_name} {client.last_name}'.strip()
            or client.username
        )
        trainer_name = (
            trainer.full_name
            or f'{trainer.first_name} {trainer.last_name}'.strip()
            or trainer.username
        )

        payload = {
            'return_url':          settings.KHALTI_RETURN_URL,
            'website_url':         settings.KHALTI_WEBSITE_URL,
            'amount':              amount_paisa,
            'purchase_order_id':   f'BOOKING-{booking.id}',
            'purchase_order_name': f'Session with {trainer_name} on {booking.date}',
            'customer_info': {
                'name':  customer_name,
                'email': client.email,
                'phone': client.contact_no,
            },
        }

        initiate_url, _ = _khalti_urls()
        try:
            resp = requests.post(initiate_url, json=payload, headers=_khalti_headers(), timeout=10)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            return Response({'detail': 'Khalti gateway timeout. Try again.'}, status=status.HTTP_504_GATEWAY_TIMEOUT)
        except requests.exceptions.RequestException as e:
            err = {}
            try:
                err = e.response.json() if e.response is not None else {}
            except Exception:
                pass
            return Response(
                {'detail': 'Failed to initiate payment with Khalti.', 'khalti_error': err},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        data        = resp.json()
        pidx        = data.get('pidx')
        payment_url = data.get('payment_url')

        payment = KhaltiPayment.objects.create(
            booking=booking,
            pidx=pidx,
            amount=amount_paisa,
            status=KhaltiPayment.STATUS_INITIATED,
            khalti_response=data,
        )

        return Response({
            'pidx':        pidx,
            'payment_url': payment_url,
            'payment_id':  payment.id,
        }, status=status.HTTP_201_CREATED)


class VerifyPaymentView(APIView):
    """
    GET /api/payment/verify/
    Khalti return_url — called after the user completes (or fails) payment.
    Query params: ?pidx=xxx&status=Completed&...

    On success:
      - booking: accepted → confirmed
      - Creates TrainerPayout(advance_25, pending) — admin transfers 25% now
      - Creates TrainerPayout(final_70, on_hold)   — admin transfers 70% after session
      - Records 5% platform_fee on KhaltiPayment
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary='Verify Khalti Payment (Return URL)',
        responses={200: OpenApiResponse(description='HTML response')},
        tags=['Payment'],
    )
    def get(self, request):
        from django.http import HttpResponse

        def _html(title, emoji, heading, body, color):
            return HttpResponse(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    background:#f9fafb;display:flex;align-items:center;justify-content:center;min-height:100vh;}}
  .card{{background:#fff;border-radius:16px;padding:40px 32px;max-width:360px;width:90%;
    text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.08);}}
  .emoji{{font-size:56px;margin-bottom:16px;}}
  h1{{margin:0 0 12px;font-size:22px;color:#111827;}}
  p{{margin:0 0 24px;font-size:15px;color:#6b7280;line-height:1.5;}}
  .badge{{display:inline-block;padding:6px 18px;border-radius:20px;
    font-size:13px;font-weight:600;background:{color}10;color:{color};}}
</style></head>
<body><div class="card">
  <div class="emoji">{emoji}</div>
  <h1>{heading}</h1>
  <p>{body}</p>
  <span class="badge">{title}</span>
</div></body></html>""")

        pidx = request.query_params.get('pidx')
        if not pidx:
            return _html('Missing pidx', '⚠️', 'Invalid Request',
                         'No payment reference was provided.', '#d97706')

        try:
            payment = KhaltiPayment.objects.select_related('booking__client', 'booking__trainer').get(pidx=pidx)
        except KhaltiPayment.DoesNotExist:
            return _html('Not Found', '❓', 'Payment Not Found',
                         'This payment record does not exist.', '#6b7280')

        if payment.status == KhaltiPayment.STATUS_COMPLETED:
            return _html('Already Verified', '✅', 'Payment Confirmed',
                         f'Booking #{payment.booking_id} is already confirmed. You can close this and return to the app.',
                         '#16a34a')

        # Call Khalti Lookup API
        _, lookup_url = _khalti_urls()
        try:
            lookup = requests.post(
                lookup_url,
                json={'pidx': pidx},
                headers=_khalti_headers(),
                timeout=10,
            )
            lookup_data = lookup.json()
        except requests.exceptions.Timeout:
            return _html('Timeout', '⏱️', 'Verification Timed Out',
                         'Could not reach Khalti. Please return to the app and try again.', '#d97706')
        except Exception:
            return _html('Error', '⚠️', 'Verification Failed',
                         'Something went wrong. Please return to the app.', '#dc2626')

        verified_status = lookup_data.get('status', '')
        payment.khalti_response = lookup_data

        if verified_status == 'Completed':
            total_paisa    = payment.amount
            platform_fee   = int(total_paisa * 0.05)
            advance_amount = int(total_paisa * 0.25)
            final_amount   = int(total_paisa * 0.70)

            payment.status         = KhaltiPayment.STATUS_COMPLETED
            payment.transaction_id = lookup_data.get('transaction_id', '')
            payment.platform_fee   = platform_fee
            payment.save()

            booking = payment.booking
            if booking.status == Booking.STATUS_ACCEPTED:
                booking.status = Booking.STATUS_CONFIRMED
                booking.save(update_fields=['status', 'updated_at'])

            TrainerPayout.objects.create(
                booking=booking,
                payout_type=TrainerPayout.TYPE_ADVANCE,
                amount=advance_amount,
                status=TrainerPayout.STATUS_PENDING,
            )
            TrainerPayout.objects.create(
                booking=booking,
                payout_type=TrainerPayout.TYPE_FINAL,
                amount=final_amount,
                status=TrainerPayout.STATUS_ON_HOLD,
            )

            client  = booking.client
            trainer = booking.trainer
            client_name  = client.full_name  or f'{client.first_name} {client.last_name}'.strip()  or client.email
            trainer_name = trainer.full_name or f'{trainer.first_name} {trainer.last_name}'.strip() or trainer.email

            return _html(
                'Payment Successful', '🎉', 'Payment Confirmed!',
                f'Rs. {total_paisa / 100:,.2f} paid by <strong>{client_name}</strong> '
                f'to trainer <strong>{trainer_name}</strong>. '
                f'Booking #{booking.id} is confirmed. '
                f'You can now close this and return to the app.',
                '#16a34a',
            )

        failure_map = {
            'Canceled':      KhaltiPayment.STATUS_CANCELLED,
            'User canceled': KhaltiPayment.STATUS_CANCELLED,
            'Expired':       KhaltiPayment.STATUS_EXPIRED,
            'Failed':        KhaltiPayment.STATUS_FAILED,
        }
        payment.status = failure_map.get(verified_status, KhaltiPayment.STATUS_FAILED)
        payment.save()

        return _html('Payment Failed', '❌', f'Payment {payment.get_status_display()}',
                     'Your payment could not be completed. Please return to the app and try again.',
                     '#dc2626')


def _attempt_auto_verify(payment):
    """
    If a KhaltiPayment is still 'initiated', call Khalti's lookup API and
    complete the verification in-process. Returns the (possibly updated) payment.
    """
    _, lookup_url = _khalti_urls()
    try:
        resp = requests.post(
            lookup_url,
            json={'pidx': payment.pidx},
            headers=_khalti_headers(),
            timeout=10,
        )
        data = resp.json()
    except Exception:
        return payment

    verified_status = data.get('status', '')
    payment.khalti_response = data

    if verified_status == 'Completed':
        total_paisa    = payment.amount
        platform_fee   = int(total_paisa * 0.05)
        advance_amount = int(total_paisa * 0.25)
        final_amount   = int(total_paisa * 0.70)

        payment.status         = KhaltiPayment.STATUS_COMPLETED
        payment.transaction_id = data.get('transaction_id', '')
        payment.platform_fee   = platform_fee
        payment.save()

        booking = payment.booking
        if booking.status == Booking.STATUS_ACCEPTED:
            booking.status = Booking.STATUS_CONFIRMED
            booking.save(update_fields=['status', 'updated_at'])

        TrainerPayout.objects.create(
            booking=booking,
            payout_type=TrainerPayout.TYPE_ADVANCE,
            amount=advance_amount,
            status=TrainerPayout.STATUS_PENDING,
        )
        TrainerPayout.objects.create(
            booking=booking,
            payout_type=TrainerPayout.TYPE_FINAL,
            amount=final_amount,
            status=TrainerPayout.STATUS_ON_HOLD,
        )
        return payment

    failure_map = {
        'Canceled':      KhaltiPayment.STATUS_CANCELLED,
        'User canceled': KhaltiPayment.STATUS_CANCELLED,
        'Expired':       KhaltiPayment.STATUS_EXPIRED,
        'Failed':        KhaltiPayment.STATUS_FAILED,
    }
    if verified_status in failure_map:
        payment.status = failure_map[verified_status]
        payment.save()

    return payment


class PaymentStatusView(APIView):
    """
    GET /api/payment/status/<booking_id>/
    Returns the latest payment record for a booking.

    If the latest payment is still 'initiated', automatically attempts to verify
    it with Khalti before returning — so the frontend just needs to call this
    endpoint after returning from the Khalti payment screen.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Get Payment Status for Booking',
        responses={200: OpenApiResponse(response=KhaltiPaymentSerializer)},
        tags=['Payment'],
    )
    def get(self, request, booking_id):
        try:
            booking = Booking.objects.get(id=booking_id, client=request.user)
        except Booking.DoesNotExist:
            return Response({'detail': 'Booking not found.'}, status=status.HTTP_404_NOT_FOUND)

        latest = booking.payments.order_by('-created_at').first()
        if not latest:
            return Response({'detail': 'No payment found for this booking.'}, status=status.HTTP_404_NOT_FOUND)

        if latest.status == KhaltiPayment.STATUS_INITIATED:
            latest = _attempt_auto_verify(latest)

        return Response(KhaltiPaymentSerializer(latest).data)


class VerifyTrainerPayoutView(APIView):
    """
    GET /api/payment/trainer-payout/verify/
    Khalti return_url after admin pays the trainer payout.

    Flow:
      1. Admin ran "Send Payout to Trainer via Khalti" action in admin panel.
      2. Khalti payment was initiated using TRAINER's merchant key.
      3. Admin paid using a dummy Khalti number.
      4. Khalti redirects here with ?pidx=...&status=Completed
      5. We look up the payout by the stored pidx, verify with Khalti,
         mark the payout as Transferred, then redirect back to admin panel.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary='Verify Trainer Payout (Return URL)',
        responses={200: OpenApiResponse(description='Redirect/HTML')},
        tags=['Payment'],
    )
    def get(self, request):
        from django.contrib import messages
        from django.shortcuts import redirect
        from django.urls import reverse

        list_url = reverse('admin:payment_trainerpayout_changelist')

        def detail_url(pk):
            return reverse('admin:payment_trainerpayout_change', args=[pk])

        pidx = request.query_params.get('pidx')
        if not pidx:
            messages.error(request, 'Khalti did not return a pidx. Payout not verified.')
            return redirect(list_url)

        payout = TrainerPayout.objects.filter(
            transfer_reference=f'pending:{pidx}'
        ).select_related('booking__trainer').first()

        if not payout:
            messages.error(request, f'No payout record found for pidx {pidx}.')
            return redirect(list_url)

        if payout.status == TrainerPayout.STATUS_TRANSFERRED:
            messages.info(request, f'Payout #{payout.id} was already marked as Transferred.')
            return redirect(detail_url(payout.id))

        lookup_url = f'{settings.KHALTI_GATEWAY_URL}/api/v2/epayment/lookup/'
        headers    = {
            'Authorization': f'key {settings.KHALTI_TRAINER_SECRET_KEY}',
            'Content-Type':  'application/json',
        }
        try:
            resp = requests.post(lookup_url, json={'pidx': pidx}, headers=headers, timeout=10)
            data = resp.json()
        except requests.exceptions.Timeout:
            messages.error(request, 'Khalti verification timed out. Try re-verifying from the payout detail page.')
            return redirect(detail_url(payout.id))
        except Exception as e:
            messages.error(request, f'Khalti lookup error: {e}')
            return redirect(detail_url(payout.id))

        if data.get('status') == 'Completed':
            trainer      = payout.booking.trainer
            trainer_name = trainer.full_name or f'{trainer.first_name} {trainer.last_name}'.strip() or trainer.email

            payout.status             = TrainerPayout.STATUS_TRANSFERRED
            payout.transferred_at     = timezone.now()
            payout.transfer_reference = data.get('transaction_id') or pidx
            payout.save(update_fields=['status', 'transferred_at', 'transfer_reference'])

            messages.success(
                request,
                f'Payout #{payout.id} confirmed — Rs. {payout.amount / 100:,.2f} transferred to {trainer_name}.',
            )
            return redirect(list_url)

        khalti_status = data.get('status', 'Unknown')
        messages.warning(
            request,
            f'Payout #{payout.id} not completed — Khalti status: "{khalti_status}". '
            f'If you already paid, use the Re-verify button on the payout detail page.',
        )
        return redirect(detail_url(payout.id))


class TrainerEarningsView(APIView):
    """
    GET /api/payment/trainer/earnings/
    Returns the logged-in trainer's payout summary and per-booking breakdown.

    Summary fields (all in Rs.):
      - total_earned_rs      : sum of all transferred payouts
      - pending_transfer_rs  : advance payouts ready — admin hasn't sent yet
      - on_hold_rs           : final 70% payouts waiting for session completion
      - total_bookings_paid  : number of bookings that have at least one transferred payout

    Breakdown: list of every payout record, newest first.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Trainer Earnings',
        responses={200: OpenApiResponse(description='Earnings summary')},
        tags=['Payment'],
    )
    def get(self, request):
        if not request.user.is_trainer:
            return Response({'detail': 'Only trainers can access earnings.'}, status=status.HTTP_403_FORBIDDEN)

        payouts = (
            TrainerPayout.objects
            .filter(booking__trainer=request.user)
            .select_related('booking__client')
            .order_by('-created_at')
        )

        def _rs(paisa):
            return round((paisa or 0) / 100, 2)

        agg = payouts.aggregate(
            total_transferred = Sum('amount', filter=Q(status=TrainerPayout.STATUS_TRANSFERRED)),
            total_pending     = Sum('amount', filter=Q(status=TrainerPayout.STATUS_PENDING)),
            total_on_hold     = Sum('amount', filter=Q(status=TrainerPayout.STATUS_ON_HOLD)),
        )

        breakdown = []
        for p in payouts:
            client = p.booking.client
            client_name = (
                client.full_name
                or f'{client.first_name} {client.last_name}'.strip()
                or client.email
            )
            breakdown.append({
                'payout_id':          p.id,
                'booking_id':         p.booking_id,
                'client_name':        client_name,
                'booking_date':       p.booking.date,
                'payout_type':        p.payout_type,
                'payout_type_label':  p.get_payout_type_display(),
                'amount_rs':          _rs(p.amount),
                'status':             p.status,
                'status_label':       p.get_status_display(),
                'transfer_reference': p.transfer_reference,
                'transferred_at':     p.transferred_at,
            })

        total_bookings_paid = (
            payouts.filter(status=TrainerPayout.STATUS_TRANSFERRED)
            .values('booking_id').distinct().count()
        )

        return Response({
            'summary': {
                'total_earned_rs':     _rs(agg['total_transferred']),
                'pending_transfer_rs': _rs(agg['total_pending']),
                'on_hold_rs':          _rs(agg['total_on_hold']),
                'total_bookings_paid': total_bookings_paid,
            },
            'payouts': breakdown,
        })
