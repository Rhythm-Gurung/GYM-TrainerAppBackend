from django.urls import path

from payment.apis.payment import (
    BulkInitiatePaymentView,
    BulkPaymentStatusView,
    InitiatePaymentView,
    PaymentStatusView,
    TrainerEarningsView,
    VerifyPaymentView,
    VerifyTrainerPayoutView,
)

urlpatterns = [
    path('payment/initiate/',                    InitiatePaymentView.as_view(),      name='payment-initiate'),
    path('payment/bulk/initiate/',               BulkInitiatePaymentView.as_view(),  name='payment-bulk-initiate'),
    path('payment/verify/',                      VerifyPaymentView.as_view(),        name='payment-verify'),
    path('payment/status/<int:booking_id>/',     PaymentStatusView.as_view(),        name='payment-status'),
    path('payment/bulk/status/<str:payment_group_id>/', BulkPaymentStatusView.as_view(), name='payment-bulk-status'),
    path('payment/trainer/earnings/',            TrainerEarningsView.as_view(),      name='trainer-earnings'),
    path('payment/trainer-payout/verify/',       VerifyTrainerPayoutView.as_view(),  name='trainer-payout-verify'),
]
