from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0001_initial_scheduling_models'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ScheduleOverride',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('schedule', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='schedule_overrides',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Schedule Override',
                'verbose_name_plural': 'Schedule Overrides',
                'db_table': 'scheduling_schedule_override',
                'ordering': ['start_date'],
            },
        ),
        migrations.AddIndex(
            model_name='scheduleoverride',
            index=models.Index(fields=['user', 'start_date', 'end_date'], name='sched_override_user_dates_idx'),
        ),
    ]
