from django.apps import AppConfig


class RacksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'racks'
    verbose_name = 'Racks e conexões físicas'

    def ready(self):
        from . import signals  # noqa: F401 — registra o post_save da topologia
