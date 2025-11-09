import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.settings')

app = Celery('crm')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'agendar-backups-cada-5-min': {
        'task': 'clientes.tasks.agendar_backups_pendentes',
        'schedule': crontab(hour=0, minute=0),
    },
    'limpar-backups-antigos': {
        'task': 'clientes.tasks.limpar_backups_antigos',
        'schedule': crontab(hour=3, minute=0),
        'kwargs': {'dias': 30},
    },
}
