import os
from celery import Celery
from celery.schedules import crontab
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.settings')

app = Celery('crm')

# Carregar settings MAS remover scheduler
app.config_from_object('django.conf:settings', namespace='CELERY')

# 🚨 FORÇAR: Não usar nenhum scheduler persistente
# Remover qualquer configuração que tente usar arquivo
for key in ['beat_scheduler', 'beat_db', 'beat_schedule']:
    if key in app.conf:
        delattr(app.conf, key)

# ✅ DEFINIR TUDO AQUI (em memória, sem arquivo)
app.conf.beat_schedule = {
    'agendar-backups-diariamente': {
        'task': 'clientes.tasks.agendar_backups_pendentes',
        'schedule': crontab(hour=0, minute=0),
    },
    'limpar-backups-antigos-diariamente': {
        'task': 'clientes.tasks.limpar_backups_antigos',
        'schedule': crontab(hour=3, minute=0),
        'kwargs': {'dias': 3},
    },
    'validar-rpki-irr-agendado': {
        'task': 'clientes.tasks.validar_blocos_rpki_irr_agendado',
        'schedule': crontab(hour=4, minute=0),
    },
    'analisar-backups-ipam': {
        'task': 'clientes.tasks.analisar_backups_ipam',
        'schedule': timedelta(days=3),
    },
    'detectar-modelos-via-backup': {
        'task': 'clientes.tasks.detectar_modelos_via_backup',
        'schedule': timedelta(days=3),
    },
    'rotina-backup-completa': {
        'task': 'clientes.tasks.rotina_backup_completa',
        'schedule': crontab(hour=1, minute=0),
    },
    'enviar-pdf-credenciais': {
        'task': 'clientes.tasks.enviar_pdf_credenciais',
        'schedule': timedelta(days=2),
    },
    'gerar-snapshots-conhecimento': {
        'task': 'clientes.tasks.gerar_snapshots_conhecimento',
        'schedule': timedelta(days=4),
    },
    'notificar-chamados-abertos': {
        'task': 'atendimento.tasks.notificar_chamados_abertos',
        'schedule': timedelta(minutes=10),
    },
    'alerta-diario-atendimento': {
        'task': 'atendimento.tasks.enviar_alerta_diario',
        'schedule': crontab(minute='0,5'),  # testa a cada 5min dentro da hora configurada
    },
}

app.conf.timezone = 'America/Sao_Paulo'

app.autodiscover_tasks()
