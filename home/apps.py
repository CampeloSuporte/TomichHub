from django.apps import AppConfig


def _force_single_session(sender, request, user, **kwargs):
    """Ao fazer login, encerra todas as outras sessões ativas do mesmo usuário."""
    from django.contrib.sessions.models import Session
    from django.utils import timezone
    current_key = request.session.session_key
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        if session.session_key == current_key:
            continue
        data = session.get_decoded()
        if str(data.get('_auth_user_id')) == str(user.pk):
            session.delete()


class HomeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'home'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in
        user_logged_in.connect(_force_single_session)
