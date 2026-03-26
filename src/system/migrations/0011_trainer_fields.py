from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('system', '0010_trainer_gallery_collection_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='userbase',
            name='location',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='trainercertification',
            name='issuer',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='trainercertification',
            name='year',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
