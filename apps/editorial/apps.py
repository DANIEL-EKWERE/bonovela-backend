from django.apps import AppConfig


class EditorialConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.editorial'

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from apps.catalog.models import Chapter

        from .similarity import refresh_index

        def _changed(sender, **kwargs):
            refresh_index()

        post_save.connect(_changed, sender=Chapter, dispatch_uid='editorial-similarity-save')
        post_delete.connect(_changed, sender=Chapter, dispatch_uid='editorial-similarity-delete')
