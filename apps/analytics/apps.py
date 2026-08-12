from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.analytics'

    def ready(self):
        from apps.analytics import signals  # noqa: F401
