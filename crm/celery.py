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
    'ipam-scan-subredes-automaticas': {
        'task': 'clientes.tasks.ipam_scan_subredes_automaticas',
        'schedule': timedelta(minutes=30),
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
        'schedule': crontab(hour=2, minute=30),  # diariamente às 02:30, após backups noturnos
    },
    'atualizar-snapshots-bgp': {
        'task': 'clientes.tasks.atualizar_snapshots_bgp',
        'schedule': crontab(hour=2, minute=45),  # após backups (01h) e snapshot de conhecimento (02:30)
    },
    'notificar-chamados-abertos': {
        'task': 'atendimento.tasks.notificar_chamados_abertos',
        'schedule': timedelta(minutes=10),
    },
    'escalar-chamados-sla': {
        'task': 'atendimento.tasks.escalar_chamados_sla',
        'schedule': timedelta(minutes=10),
    },
    # Roda a cada 5 min; a task verifica internamente se é o horário certo
    # e usa guard anti-duplo-envio por dia
    'alerta-diario-atendimento': {
        'task': 'atendimento.tasks.enviar_alerta_diario',
        'schedule': timedelta(minutes=5),
    },
    # Lembretes pessoais: manhã e meio-dia (horários configurados no sistema)
    'lembretes-pessoais-atendentes': {
        'task': 'atendimento.tasks.enviar_lembretes_pessoais',
        'schedule': timedelta(minutes=5),
    },
    # Alertas de cobrança via WhatsApp — seg a sex às 8:30
    'alertas-whatsapp-cobranca': {
        'task': 'financeiro.tasks.enviar_alertas_whatsapp',
        'schedule': crontab(hour=8, minute=30, day_of_week='1-5'),
    },
}

app.conf.timezone = 'America/Sao_Paulo'

app.autodiscover_tasks()
