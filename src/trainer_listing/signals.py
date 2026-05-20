from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from scheduling.models import TimeSlot, TrainerScheduleScope, WeeklyScheduleDay
from system.models import TrainerCertification, TrainerGalleryImage, UserBase
from trainer_listing.cache import invalidate_trainer_cache
from trainer_listing.models import TrainerReview


@receiver([post_save, post_delete], sender=UserBase)
def invalidate_when_trainer_changes(sender, instance, **kwargs):
    if instance.is_trainer:
        invalidate_trainer_cache()


@receiver([post_save, post_delete], sender=TrainerCertification)
@receiver([post_save, post_delete], sender=TrainerGalleryImage)
def invalidate_when_trainer_media_changes(sender, instance, **kwargs):
    if instance.user and instance.user.is_trainer:
        invalidate_trainer_cache()


@receiver([post_save, post_delete], sender=WeeklyScheduleDay)
@receiver([post_save, post_delete], sender=TrainerScheduleScope)
def invalidate_when_schedule_changes(sender, instance, **kwargs):
    if instance.user and instance.user.is_trainer:
        invalidate_trainer_cache()


@receiver([post_save, post_delete], sender=TimeSlot)
def invalidate_when_time_slot_changes(sender, instance, **kwargs):
    if instance.day.user and instance.day.user.is_trainer:
        invalidate_trainer_cache()


@receiver([post_save, post_delete], sender=TrainerReview)
def invalidate_when_review_changes(sender, instance, **kwargs):
    invalidate_trainer_cache()
