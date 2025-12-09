#!/bin/bash
cd /opt/crm

# Agendar backups - 00:00 (meia-noite)
python manage.py shell -c "from clientes.tasks import agendar_backups_pendentes; agendar_backups_pendentes()" >> /var/log/crm-tasks.log 2>&1

# Limpar backups antigos - 03:00
python manage.py shell -c "from clientes.tasks import limpar_backups_antigos; limpar_backups_antigos(dias=3)" >> /var/log/crm-tasks.log 2>&1

# Validar RPKI/IRR - 04:00
python manage.py shell -c "from clientes.tasks import validar_blocos_rpki_irr_agendado; validar_blocos_rpki_irr_agendado()" >> /var/log/crm-tasks.log 2>&1
