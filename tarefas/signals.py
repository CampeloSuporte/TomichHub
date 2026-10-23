from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

# Chave de sessão que liga o modal "Suas tarefas" — ver
# templatetags/tarefas_aviso.py, que a consome no primeiro base.html.
SESSAO_AVISO_TAREFAS = 'tarefas_aviso_login'


@receiver(user_logged_in)
def marcar_aviso_tarefas(sender, request, user, **kwargs):
    """Sinal em vez de mexer na view de login: `auth_login` é chamado em três
    caminhos (sem 2FA, navegador confiável e depois do código do 2FA) e o
    sinal só dispara quando o login de fato acontece — senha certa com 2FA
    pendente ainda não conta."""
    if request is not None and hasattr(request, 'session'):
        request.session[SESSAO_AVISO_TAREFAS] = True
