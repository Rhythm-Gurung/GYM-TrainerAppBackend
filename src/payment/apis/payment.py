import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from payment.models import KhaltiPayment, PaymentGroup, PaymentGroupBooking, TrainerPayout
from payment.serializers.payment import (
    BulkInitiatePaymentSerializer,
    InitiatePaymentSerializer,
    KhaltiPaymentSerializer,
    PaymentGroupSerializer,
)
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


def _booking_amount_paisa(booking):
    return int(booking.total_amount * 100)


def _ensure_booking_confirmed_and_payouts(booking, total_paisa):
    if booking.status == Booking.STATUS_ACCEPTED:
        booking.status = Booking.STATUS_CONFIRMED
        booking.save(update_fields=['status', 'updated_at'])

    advance_amount = int(total_paisa * 0.25)
    final_amount   = int(total_paisa * 0.70)

    if not TrainerPayout.objects.filter(
        booking=booking,
        payout_type=TrainerPayout.TYPE_ADVANCE,
    ).exists():
        TrainerPayout.objects.create(
            booking=booking,
            payout_type=TrainerPayout.TYPE_ADVANCE,
            amount=advance_amount,
            status=TrainerPayout.STATUS_PENDING,
        )

    if not TrainerPayout.objects.filter(
        booking=booking,
        payout_type=TrainerPayout.TYPE_FINAL,
    ).exists():
        TrainerPayout.objects.create(
            booking=booking,
            payout_type=TrainerPayout.TYPE_FINAL,
            amount=final_amount,
            status=TrainerPayout.STATUS_ON_HOLD,
        )


def _finalize_single_payment(payment, lookup_data):
    with transaction.atomic():
        payment = KhaltiPayment.objects.select_for_update().select_related('booking').get(pk=payment.pk)
        if payment.status == KhaltiPayment.STATUS_COMPLETED:
            return payment

        total_paisa  = payment.amount
        platform_fee = int(total_paisa * 0.05)

        payment.status         = KhaltiPayment.STATUS_COMPLETED
        payment.transaction_id = lookup_data.get('transaction_id', '')
        payment.platform_fee   = platform_fee
        payment.khalti_response = lookup_data
        payment.save(update_fields=['status', 'transaction_id', 'platform_fee', 'khalti_response', 'updated_at'])

        booking = Booking.objects.select_for_update().get(pk=payment.booking_id)
        _ensure_booking_confirmed_and_payouts(booking, total_paisa)

        payment.booking = booking
        return payment


def _finalize_payment_group(group, lookup_data):
    with transaction.atomic():
        group = PaymentGroup.objects.select_for_update().get(pk=group.pk)
        if group.status == PaymentGroup.STATUS_COMPLETED:
            return group

        group.status             = PaymentGroup.STATUS_COMPLETED
        group.provider_reference = lookup_data.get('transaction_id', '')
        group.khalti_response    = lookup_data
        group.save(update_fields=['status', 'provider_reference', 'khalti_response', 'updated_at'])

        links = list(
            PaymentGroupBooking.objects
            .filter(payment_group=group)
            .select_related('booking')
        )
        booking_ids = [link.booking_id for link in links]
        bookings = {
            b.id: b
            for b in Booking.objects.select_for_update().filter(id__in=booking_ids)
        }

        for link in links:
            booking = bookings.get(link.booking_id)
            if booking is None:
                continue

            booking_amount = link.amount or _booking_amount_paisa(booking)
            per_booking_pidx = f'{group.payment_group_id}:{booking.id}'
            platform_fee = int(booking_amount * 0.05)

            KhaltiPayment.objects.update_or_create(
                booking=booking,
                pidx=per_booking_pidx,
                defaults={
                    'transaction_id': group.provider_reference,
                    'amount': booking_amount,
                    'platform_fee': platform_fee,
                    'status': KhaltiPayment.STATUS_COMPLETED,
                    'khalti_response': lookup_data,
                },
            )
            _ensure_booking_confirmed_and_payouts(booking, booking_amount)

        return group


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


class BulkInitiatePaymentView(APIView):
    """
    POST /api/payment/bulk/initiate/
    Body: { "booking_ids": [<int>, ...] }

    Frontend only sends booking IDs. Backend validates ownership/status and
    computes total payable amount from DB before initiating Khalti.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Initiate Khalti Bulk Payment',
        request=BulkInitiatePaymentSerializer,
        responses={201: OpenApiResponse(description='Bulk payment initiated')},
        tags=['Payment'],
    )
    def post(self, request):
        serializer = BulkInitiatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking_ids = serializer.validated_data['booking_ids']

        idempotency_key = request.headers.get('Idempotency-Key', '').strip()
        if idempotency_key:
            existing = (
                PaymentGroup.objects
                .filter(client=request.user, idempotency_key=idempotency_key)
                .order_by('-created_at')
                .first()
            )
            if existing and existing.status in [PaymentGroup.STATUS_INITIATED, PaymentGroup.STATUS_COMPLETED]:
                return Response({
                    'payment_group_id': existing.payment_group_id,
                    'payment_url': existing.khalti_response.get('payment_url', ''),
                    'total_amount': existing.total_amount,
                    'currency': 'NPR',
                    'expires_at': existing.expires_at,
                }, status=status.HTTP_200_OK)

        with transaction.atomic():
            bookings = list(
                Booking.objects.select_for_update()
                .select_related('client', 'trainer')
                .filter(id__in=booking_ids, client=request.user)
            )

            if len(bookings) != len(booking_ids):
                found_ids = {b.id for b in bookings}
                missing = [bid for bid in booking_ids if bid not in found_ids]
                return Response(
                    {'detail': 'Some bookings were not found.', 'missing_booking_ids': missing},
                    status=status.HTTP_404_NOT_FOUND,
                )

            non_accepted = [b.id for b in bookings if b.status != Booking.STATUS_ACCEPTED]
            if non_accepted:
                return Response(
                    {
                        'detail': 'All selected bookings must be in accepted status.',
                        'invalid_booking_ids': non_accepted,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            already_paid = [
                b.id
                for b in bookings
                if b.payments.filter(status=KhaltiPayment.STATUS_COMPLETED).exists()
            ]
            if already_paid:
                return Response(
                    {
                        'detail': 'Some selected bookings are already paid.',
                        'already_paid_booking_ids': already_paid,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            trainer_ids = {b.trainer_id for b in bookings}
            if len(trainer_ids) > 1:
                return Response(
                    {'detail': 'Bulk payment currently supports one trainer per checkout.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            client = bookings[0].client
            trainer = bookings[0].trainer
            if not client.contact_no:
                return Response(
                    {'detail': 'Your phone number is required to make a payment. Please update your profile.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            total_amount = sum(_booking_amount_paisa(b) for b in bookings)
            if total_amount <= 0:
                return Response(
                    {'detail': 'Selected bookings do not have payable amount.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            group = PaymentGroup.objects.create(
                client=request.user,
                total_amount=total_amount,
                status=PaymentGroup.STATUS_INITIATED,
                idempotency_key=idempotency_key,
            )
            PaymentGroupBooking.objects.bulk_create([
                PaymentGroupBooking(
                    payment_group=group,
                    booking=b,
                    amount=_booking_amount_paisa(b),
                )
                for b in bookings
            ])

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
            'amount':              total_amount,
            'purchase_order_id':   group.payment_group_id,
            'purchase_order_name': f'{len(bookings)} session(s) with {trainer_name}',
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
            group.status = PaymentGroup.STATUS_FAILED
            group.save(update_fields=['status', 'updated_at'])
            return Response({'detail': 'Khalti gateway timeout. Try again.'}, status=status.HTTP_504_GATEWAY_TIMEOUT)
        except requests.exceptions.RequestException as e:
            group.status = PaymentGroup.STATUS_FAILED
            group.save(update_fields=['status', 'updated_at'])
            err = {}
            try:
                err = e.response.json() if e.response is not None else {}
            except Exception:
                pass
            return Response(
                {'detail': 'Failed to initiate payment with Khalti.', 'khalti_error': err},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        data = resp.json()
        expires_at = parse_datetime(data.get('expires_at') or '') if data.get('expires_at') else None

        group.pidx = data.get('pidx')
        group.expires_at = expires_at
        group.khalti_response = data
        group.save(update_fields=['pidx', 'expires_at', 'khalti_response', 'updated_at'])

        return Response({
            'payment_group_id': group.payment_group_id,
            'payment_url': data.get('payment_url'),
            'total_amount': group.total_amount,
            'currency': 'NPR',
            'expires_at': group.expires_at,
        }, status=status.HTTP_201_CREATED)


class BulkPaymentStatusView(APIView):
    """
    GET /api/payment/bulk/status/<payment_group_id>/
    Returns group status and selected booking statuses.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Get Bulk Payment Status',
        responses={200: OpenApiResponse(response=PaymentGroupSerializer)},
        tags=['Payment'],
    )
    def get(self, request, payment_group_id):
        try:
            group = (
                PaymentGroup.objects
                .prefetch_related('bookings')
                .get(payment_group_id=payment_group_id, client=request.user)
            )
        except PaymentGroup.DoesNotExist:
            return Response({'detail': 'Payment group not found.'}, status=status.HTTP_404_NOT_FOUND)

        if group.status == PaymentGroup.STATUS_INITIATED and group.pidx:
            group = _attempt_auto_verify_payment_group(group)

        payload = PaymentGroupSerializer(group).data
        payload['bookings'] = [
            {'booking_id': b.id, 'status': b.status}
            for b in group.bookings.only('id', 'status').order_by('id')
        ]
        return Response(payload, status=status.HTTP_200_OK)


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

        payment = KhaltiPayment.objects.select_related('booking__client', 'booking__trainer').filter(pidx=pidx).first()
        group = PaymentGroup.objects.select_related('client').filter(pidx=pidx).first()

        if not payment and not group:
            return _html('Not Found', '❓', 'Payment Not Found',
                         'This payment record does not exist.', '#6b7280')

        if payment and payment.status == KhaltiPayment.STATUS_COMPLETED:
            return _html('Already Verified', '✅', 'Payment Confirmed',
                         f'Booking #{payment.booking_id} is already confirmed. You can close this and return to the app.',
                         '#16a34a')

        if group and group.status == PaymentGroup.STATUS_COMPLETED:
            return _html('Already Verified', '✅', 'Bulk Payment Confirmed',
                         f'Payment group {group.payment_group_id} is already confirmed. You can close this and return to the app.',
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

        if verified_status == 'Completed':
            if payment:
                payment = _finalize_single_payment(payment, lookup_data)
                total_paisa = payment.amount
                booking = payment.booking
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

            group = _finalize_payment_group(group, lookup_data)
            booking_count = group.bookings.count()
            return _html(
                'Payment Successful', '🎉', 'Bulk Payment Confirmed!',
                f'Rs. {group.total_amount / 100:,.2f} paid successfully for '
                f'<strong>{booking_count}</strong> booking(s). '
                f'Payment Group: <strong>{group.payment_group_id}</strong>. '
                f'You can now close this and return to the app.',
                '#16a34a',
            )

        failure_map = {
            'Canceled':      'cancelled',
            'User canceled': 'cancelled',
            'Expired':       'expired',
            'Failed':        'failed',
        }
        failed_status = failure_map.get(verified_status, 'failed')

        if payment:
            status_map = {
                'cancelled': KhaltiPayment.STATUS_CANCELLED,
                'expired': KhaltiPayment.STATUS_EXPIRED,
                'failed': KhaltiPayment.STATUS_FAILED,
            }
            payment.status = status_map[failed_status]
            payment.khalti_response = lookup_data
            payment.save(update_fields=['status', 'khalti_response', 'updated_at'])
        else:
            status_map = {
                'cancelled': PaymentGroup.STATUS_CANCELLED,
                'expired': PaymentGroup.STATUS_EXPIRED,
                'failed': PaymentGroup.STATUS_FAILED,
            }
            group.status = status_map[failed_status]
            group.khalti_response = lookup_data
            group.save(update_fields=['status', 'khalti_response', 'updated_at'])

        failed_label = payment.get_status_display() if payment else group.get_status_display()
        return _html('Payment Failed', '❌', f'Payment {failed_label}',
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

    if verified_status == 'Completed':
        return _finalize_single_payment(payment, data)

    failure_map = {
        'Canceled':      KhaltiPayment.STATUS_CANCELLED,
        'User canceled': KhaltiPayment.STATUS_CANCELLED,
        'Expired':       KhaltiPayment.STATUS_EXPIRED,
        'Failed':        KhaltiPayment.STATUS_FAILED,
    }
    if verified_status in failure_map:
        payment.status = failure_map[verified_status]
        payment.khalti_response = data
        payment.save(update_fields=['status', 'khalti_response', 'updated_at'])

    return payment


def _attempt_auto_verify_payment_group(group):
    _, lookup_url = _khalti_urls()
    try:
        resp = requests.post(
            lookup_url,
            json={'pidx': group.pidx},
            headers=_khalti_headers(),
            timeout=10,
        )
        data = resp.json()
    except Exception:
        return group

    verified_status = data.get('status', '')
    if verified_status == 'Completed':
        return _finalize_payment_group(group, data)

    failure_map = {
        'Canceled':      PaymentGroup.STATUS_CANCELLED,
        'User canceled': PaymentGroup.STATUS_CANCELLED,
        'Expired':       PaymentGroup.STATUS_EXPIRED,
        'Failed':        PaymentGroup.STATUS_FAILED,
    }
    if verified_status in failure_map:
        group.status = failure_map[verified_status]
        group.khalti_response = data
        group.save(update_fields=['status', 'khalti_response', 'updated_at'])

    return group


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
