"""
Django management command to clean up expired bookings.

Run this command periodically (e.g., every hour via cron) to automatically
mark expired pending and accepted bookings as MISSED.

Usage:
    python manage.py cleanup_expired_bookings
    python manage.py cleanup_expired_bookings --dry-run
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from scheduling.models import Booking
from scheduling.services.booking_expiry import is_booking_expired, _get_session_start_datetime


class Command(BaseCommand):
    help = 'Mark expired pending/accepted bookings as MISSED'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be cleaned up without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now = timezone.now()
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
        
        # Find all pending and accepted bookings that have expired
        expired_bookings = []
        
        pending_bookings = Booking.objects.filter(
            status__in=[Booking.STATUS_PENDING, Booking.STATUS_ACCEPTED]
        ).select_related('client', 'trainer')
        
        for booking in pending_bookings:
            if is_booking_expired(booking, now):
                session_start = _get_session_start_datetime(booking)
                expired_bookings.append({
                    'booking': booking,
                    'session_start': session_start,
                })
        
        if not expired_bookings:
            self.stdout.write(self.style.SUCCESS('No expired bookings found.'))
            return
        
        self.stdout.write(
            self.style.WARNING(f'Found {len(expired_bookings)} expired booking(s):')
        )
        
        for item in expired_bookings:
            booking = item['booking']
            session_start = item['session_start']
            
            self.stdout.write(
                f'  - Booking #{booking.id}: {booking.client.username} with {booking.trainer.username}, '
                f'scheduled for {session_start.strftime("%Y-%m-%d %I:%M %p")}, '
                f'status: {booking.status}'
            )
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'\nWould mark {len(expired_bookings)} booking(s) as MISSED. '
                    'Run without --dry-run to apply changes.'
                )
            )
            return
        
        # Mark all expired bookings as MISSED
        from payment.services import sync_payout_on_booking_status
        updated_count = 0
        with transaction.atomic():
            for item in expired_bookings:
                booking = item['booking']
                booking.status = Booking.STATUS_MISSED
                booking.save(update_fields=['status', 'updated_at'])
                sync_payout_on_booking_status(booking, Booking.STATUS_MISSED)
                updated_count += 1
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully marked {updated_count} expired booking(s) as MISSED.'
            )
        )
