#!/bin/sh
# Instala os limites de memória por serviço e o vigia de memória do container.
# Uso: sh /opt/crm/tools/memoria/instalar.sh   (como root)
set -e
DIR=$(dirname "$(readlink -f "$0")")
for svc in daphne gunicorn celery celery-atendimento-ia; do
    mkdir -p /etc/systemd/system/$svc.service.d
    install -m 644 "$DIR/dropins/$svc.conf" /etc/systemd/system/$svc.service.d/memoria.conf
done
install -m 644 "$DIR/crm-memoria-guard.service" /etc/systemd/system/crm-memoria-guard.service
touch /var/log/crm-memoria.log && chmod 640 /var/log/crm-memoria.log
systemctl daemon-reload
systemctl enable --now crm-memoria-guard.service
# MemoryHigh/MemoryMax valem na hora (systemd escreve no cgroup), sem restart.
for svc in daphne gunicorn celery celery-atendimento-ia; do
    systemctl show $svc -p MemoryHigh -p MemoryMax -p OOMPolicy | tr '\n' ' '; echo " ($svc)"
done
