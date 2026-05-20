from django.apps import AppConfig


class TrainerListingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'trainer_listing'
    verbose_name = "Client's Trainer"

    def ready(self):
        import trainer_listing.signals  # noqa: F401
