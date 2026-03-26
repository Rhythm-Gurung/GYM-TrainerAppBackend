import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TrainerReview',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rating', models.PositiveSmallIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(5)])),
                ('comment', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('trainer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reviews', to=settings.AUTH_USER_MODEL)),
                ('reviewer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='given_reviews', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Trainer Review',
                'verbose_name_plural': 'Trainer Reviews',
                'db_table': 'trainer_listing_review',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='TrainerFavourite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='favourited_trainers', to=settings.AUTH_USER_MODEL)),
                ('trainer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='favourited_by', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Trainer Favourite',
                'verbose_name_plural': 'Trainer Favourites',
                'db_table': 'trainer_listing_favourite',
            },
        ),
        migrations.AlterUniqueTogether(
            name='trainerreview',
            unique_together={('trainer', 'reviewer')},
        ),
        migrations.AlterUniqueTogether(
            name='trainerfavourite',
            unique_together={('client', 'trainer')},
        ),
    ]
