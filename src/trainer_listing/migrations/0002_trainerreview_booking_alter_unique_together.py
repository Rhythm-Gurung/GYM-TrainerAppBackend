from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0009_session_verification_request'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('trainer_listing', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='trainerreview',
            name='booking',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='trainer_reviews',
                to='scheduling.booking',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='trainerreview',
            unique_together={('booking', 'reviewer')},
        ),
    ]