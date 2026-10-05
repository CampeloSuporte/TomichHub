#!/usr/bin/env bash
# Backup do banco do CRM (crm_db) para o Google Drive, criptografado.
#
# pg_dump -Fc -> /var/backups/crm/ (7 dias) -> rclone copy para gdrive-cripto:
# (crypt sobre gdrive:backup-banco-crm, 30 dias). Roda pelo crm-backup-drive.timer.
# Documentação: docs/backup_banco_drive.md
set -euo pipefail

REMOTE="${BACKUP_REMOTE:-gdrive-cripto:}"
LOCAL_DIR="${BACKUP_LOCAL_DIR:-/var/backups/crm}"
RETENCAO_LOCAL_DIAS="${BACKUP_RETENCAO_LOCAL_DIAS:-7}"
RETENCAO_DRIVE_DIAS="${BACKUP_RETENCAO_DRIVE_DIAS:-30}"

log() { echo "[backup-drive] $*"; }

mkdir -p "$LOCAL_DIR"
chmod 700 "$LOCAL_DIR"

arquivo="$LOCAL_DIR/crm_db_$(date +%F_%H%M).dump"
tmp="$arquivo.parcial"

log "pg_dump crm_db -> $arquivo"
sudo -u postgres pg_dump -Fc crm_db > "$tmp"
# Dump truncado não pode virar "o backup de hoje"
pg_restore --list "$tmp" > /dev/null
mv "$tmp" "$arquivo"
chmod 600 "$arquivo"
log "dump ok: $(du -h "$arquivo" | cut -f1)"

log "enviando para $REMOTE"
rclone copy "$arquivo" "$REMOTE" --retries 3
rclone delete "$REMOTE" --min-age "${RETENCAO_DRIVE_DIAS}d" --include 'crm_db_*.dump'

find "$LOCAL_DIR" -name 'crm_db_*.dump' -mtime +"$RETENCAO_LOCAL_DIAS" -delete
find "$LOCAL_DIR" -name '*.parcial' -delete

log "concluído"
