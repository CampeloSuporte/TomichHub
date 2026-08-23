from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='seguranca_dashboard'),
    path('desbloquear/', views.desbloquear, name='seguranca_desbloquear'),
    path('desbloquear-todos/', views.desbloquear_todos, name='seguranca_desbloquear_todos'),
    path('fail2ban/desbanir/', views.fail2ban_desbanir, name='seguranca_fail2ban_desbanir'),
    path('fail2ban/banir/', views.fail2ban_banir, name='seguranca_fail2ban_banir'),
]
