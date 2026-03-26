from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class TrainerReview(models.Model):
    trainer  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='given_reviews')
    rating   = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment  = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Trainer Review'
        verbose_name_plural = 'Trainer Reviews'
        db_table            = 'trainer_listing_review'
        unique_together     = [('trainer', 'reviewer')]
        ordering            = ['-created_at']

    def __str__(self):
        return f'{self.reviewer} → {self.trainer} ({self.rating}★)'


class TrainerFavourite(models.Model):
    client  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='favourited_trainers')
    trainer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='favourited_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Trainer Favourite'
        verbose_name_plural = 'Trainer Favourites'
        db_table            = 'trainer_listing_favourite'
        unique_together     = [('client', 'trainer')]

    def __str__(self):
        return f'{self.client} ♥ {self.trainer}'
