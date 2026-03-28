from django.contrib.auth import get_user_model
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from notification.models import Notification
from payment.models import ClientRefund, KhaltiPayment, TrainerPayout
from scheduling.models import Booking

User = get_user_model()


def _name(user):
    return (
        getattr(user, 'full_name', '')
        or f'{getattr(user, "first_name", "")} {getattr(user, "last_name", "")}'.strip()
        or getattr(user, 'username', '')
        or getattr(user, 'email', '')
        or 'User'
    )


def _notify(user, type_, title, message):
    if not user:
        return
    Notification.objects.create(
        user=user,
        type=type_,
        title=title,
        message=message,
    )


def _notify_admins(type_, title, message):
    for admin in User.objects.filter(is_staff=True, is_active=True):
        _notify(admin, type_, title, message)


@receiver(pre_save, sender=Booking)
def booking_pre_save(sender, instance: Booking, **kwargs):
    if not instance.pk:
        instance._old_status = None
        return
    old = Booking.objects.filter(pk=instance.pk).values_list('status', flat=True).first()
    instance._old_status = old


@receiver(post_save, sender=Booking)
def booking_post_save(sender, instance: Booking, created: bool, **kwargs):
    client = instance.client
    trainer = instance.trainer
    client_name = _name(client)
    trainer_name = _name(trainer)

    if created and instance.status == Booking.STATUS_PENDING:
        _notify(
            trainer,
            Notification.TYPE_BOOKING,
            'New booking request',
            f'Booking incoming from {client_name}. Please accept or cancel.',
        )
        return

    old_status = getattr(instance, '_old_status', None)
    new_status = instance.status
    if not old_status or old_status == new_status:
        return

    if old_status == Booking.STATUS_PENDING and new_status == Booking.STATUS_ACCEPTED:
        _notify(
            client,
            Notification.TYPE_BOOKING,
            'Booking accepted',
            f'{trainer_name} accepted your booking. Please complete payment to confirm.',
        )

    if new_status in {Booking.STATUS_CANCELLED, Booking.STATUS_REFUND_PENDING, Booking.STATUS_REFUNDED}:
        _notify(
            client,
            Notification.TYPE_BOOKING,
            'Booking updated',
            f'Booking #{instance.id} status changed to {new_status}.',
        )
        _notify(
            trainer,
            Notification.TYPE_BOOKING,
            'Booking updated',
            f'Booking #{instance.id} status changed to {new_status}.',
        )


@receiver(pre_save, sender=KhaltiPayment)
def payment_pre_save(sender, instance: KhaltiPayment, **kwargs):
    if not instance.pk:
        instance._old_status = None
        return
    old = KhaltiPayment.objects.filter(pk=instance.pk).values_list('status', flat=True).first()
    instance._old_status = old


@receiver(post_save, sender=KhaltiPayment)
def payment_post_save(sender, instance: KhaltiPayment, created: bool, **kwargs):
    old_status = getattr(instance, '_old_status', None)
    new_status = instance.status
    if old_status == new_status:
        return

    if new_status == KhaltiPayment.STATUS_COMPLETED:
        booking = instance.booking
        client = booking.client
        trainer = booking.trainer
        client_name = _name(client)
        trainer_name = _name(trainer)
        amount_rs = (instance.amount or 0) / 100

        _notify(
            client,
            Notification.TYPE_PAYMENT,
            'Payment successful',
            f'Payment of Rs. {amount_rs:,.2f} to {trainer_name} was successful.',
        )
        _notify(
            trainer,
            Notification.TYPE_PAYMENT,
            'Payment received',
            f'You received Rs. {amount_rs:,.2f} from {client_name}.',
        )
        _notify_admins(
            Notification.TYPE_PAYMENT,
            'Client payment received',
            f'Payment of Rs. {amount_rs:,.2f} received for Booking #{booking.id}.',
        )


@receiver(pre_save, sender=TrainerPayout)
def payout_pre_save(sender, instance: TrainerPayout, **kwargs):
    if not instance.pk:
        instance._old_status = None
        return
    old = TrainerPayout.objects.filter(pk=instance.pk).values_list('status', flat=True).first()
    instance._old_status = old


@receiver(post_save, sender=TrainerPayout)
def payout_post_save(sender, instance: TrainerPayout, created: bool, **kwargs):
    old_status = getattr(instance, '_old_status', None)
    new_status = instance.status
    if old_status == new_status:
        return

    if new_status == TrainerPayout.STATUS_TRANSFERRED:
        trainer = instance.booking.trainer
        amount_rs = (instance.amount or 0) / 100
        _notify(
            trainer,
            Notification.TYPE_PAYMENT,
            'Payout received',
            f'Admin transferred Rs. {amount_rs:,.2f} to you ({instance.get_payout_type_display()}).',
        )
        _notify_admins(
            Notification.TYPE_PAYMENT,
            'Trainer payout transferred',
            f'Payout #{instance.id} transferred for Booking #{instance.booking_id}.',
        )


@receiver(pre_save, sender=ClientRefund)
def refund_pre_save(sender, instance: ClientRefund, **kwargs):
    if not instance.pk:
        instance._old_status = None
        return
    old = ClientRefund.objects.filter(pk=instance.pk).values_list('status', flat=True).first()
    instance._old_status = old


@receiver(post_save, sender=ClientRefund)
def refund_post_save(sender, instance: ClientRefund, created: bool, **kwargs):
    old_status = getattr(instance, '_old_status', None)
    new_status = instance.status
    if old_status == new_status:
        return

    if new_status == ClientRefund.STATUS_PROCESSED:
        booking = instance.payment.booking
        client = booking.client
        amount_rs = (instance.amount or 0) / 100
        _notify(
            client,
            Notification.TYPE_PAYMENT,
            'Refund processed',
            f'Refund of Rs. {amount_rs:,.2f} has been processed for Booking #{booking.id}.',
        )
        _notify_admins(
            Notification.TYPE_PAYMENT,
            'Client refund processed',
            f'Refund #{instance.id} processed for Booking #{booking.id}.',
        )

