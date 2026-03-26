from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0005_alter_booking_unique_together_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='total_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
    ]
