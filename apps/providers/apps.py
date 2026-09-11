from django.apps import AppConfig


class ProvidersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.providers'

    def ready(self):
        from apps.providers.adapters.builtin import register_builtin_adapters

        register_builtin_adapters()
