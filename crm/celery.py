import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.settings')

app = Celery('crm')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'agendar-backups-diariamente': {  # Nome melhor
        'task': 'clientes.tasks.agendar_backups_pendentes',
        'schedule': crontab(hour=0, minute=0),  # 00:00 apenas
        'options': {'expires': 300}  # Task expira se não executar em 5 min
    },
    'limpar-backups-antigos-diariamente': {
        'task': 'clientes.tasks.limpar_backups_antigos',
        'schedule': crontab(hour=3, minute=0),  # 03:00 apenas
        'kwargs': {'dias': 3},
        'options': {'expires': 300}
    },
}

# Adicione timezone explícito
app.conf.timezone = 'America/Sao_Paulo'  # Ou sua timezone
