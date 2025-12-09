# Remove PersistentScheduler completamente
import sys

# Ler o arquivo
with open('/opt/crm/crm/settings.py', 'r') as f:
    content = f.read()

# Remover linhas que causam problema
lines_to_remove = [
    "CELERY_BEAT_SCHEDULER = 'celery.beat.EpochNowScheduler'",
    "CELERY_BEAT_SCHEDULER = 'celery.beat.PersistentScheduler'",
    "CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'",
]

for line in lines_to_remove:
    content = content.replace(line + '\n', '')
    content = content.replace(line, '')

# Adicionar a configuração correta
if 'CELERY_BEAT_SCHEDULER' not in content:
    content += "\n# Celery Beat Scheduler\nCELERY_BEAT_SCHEDULER = 'celery.beat.Scheduler'\n"

# Salvar
with open('/opt/crm/crm/settings.py', 'w') as f:
    f.write(content)

print("✅ settings.py corrigido")
