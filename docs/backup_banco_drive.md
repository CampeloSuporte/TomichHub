# Backup do Banco no Google Drive — Documentação Técnica

**Arquivos principais:**
- `tools/backup_drive/backup_drive.sh` — script do backup
- `tools/backup_drive/crm-backup-drive.service` — unidade systemd (cópia versionada)
- `tools/backup_drive/crm-backup-drive.timer` — agendamento diário (cópia versionada)

**Atualizado em:** 2026-10-05

---

## Visão Geral

O `git push` leva só o código. Os dados do CRM ficam no PostgreSQL (`crm_db`) e, até 05/10/2026,
não tinham backup nenhum. Este backup tira um dump diário do banco, guarda uma cópia local e envia
outra, criptografada, para a pasta **`backup-banco-crm`** do Google Drive.

```
03:00 crm-backup-drive.timer
  └─ backup_drive.sh
       ├─ pg_dump -Fc crm_db  →  /var/backups/crm/crm_db_AAAA-MM-DD_HHMM.dump   (7 dias)
       ├─ pg_restore --list   (o dump precisa ser legível antes de virar "o backup de hoje")
       └─ rclone copy         →  gdrive-cripto:  =  Drive:/backup-banco-crm/    (30 dias)
```

| Item | Valor |
|------|-------|
| Horário | 03:00 + até 10 min aleatórios; `Persistent=true` roda no boot se o servidor estava desligado |
| Tamanho do dump | ~20 MB comprimido (banco de 97 MB, 05/10/2026) |
| Duração | ~1 min (3 s de dump, o resto é upload) |
| Retenção local | 7 dias em `/var/backups/crm/` (pasta 700, arquivos 600) |
| Retenção no Drive | 30 dias (só apaga `crm_db_*.dump`) |

O que **não** entra: a pasta `media/` (anexos, firmwares, backups de equipamentos — 26 GB),
que não cabe nos 15 GB gratuitos do Google.

---

## Configuração do rclone

Fica em `/root/.config/rclone/rclone.conf` (fora do git — contém o token do Google e as senhas).

| Remote | Tipo | Aponta para |
|--------|------|-------------|
| `gdrive` | drive, `scope=drive.file` | Conta Google do backup. `drive.file` só enxerga arquivos criados pelo próprio rclone — o resto do Drive fica invisível para o servidor |
| `gdrive-cripto` | crypt | `gdrive:backup-banco-crm`. Nomes de arquivo legíveis (`filename_encryption=off`, sufixo `.bin` no Drive), conteúdo criptografado |

⚠️ **As duas senhas do `gdrive-cripto` (password e salt) ficam guardadas fora do servidor**, no
gerenciador de senhas. Sem elas, os dumps do Drive não podem ser abertos — se o servidor se perder,
recrie o remote `gdrive-cripto` com as mesmas senhas antes de restaurar.

### Autorização do Google em servidor sem navegador

O `rclone config` sobe um servidor em `127.0.0.1:53682` e espera o retorno do Google. Num servidor
headless:

1. Terminal 1: `rclone config create gdrive drive scope=drive.file config_is_local=true`
2. Terminal 2: `curl -s -o /dev/null -w '%{redirect_url}\n' "http://127.0.0.1:53682/auth?state=XXXX"`
   devolve o link do Google; abrir no navegador do PC e autorizar.
3. O navegador cai num erro em `http://127.0.0.1:53682/?state=...&code=...`. Copiar essa URL e rodar
   no servidor `curl "URL"` — **com aspas**, senão o bash parte a URL nos `&`.

---

## Instalação (servidor novo)

```bash
apt-get install -y rclone
# configurar gdrive e gdrive-cripto (seção acima)
cp tools/backup_drive/crm-backup-drive.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now crm-backup-drive.timer
```

As unidades em `/etc/systemd/system/` são cópias — alterou no git, copie de novo e rode
`systemctl daemon-reload`.

Variáveis opcionais em `/etc/default/crm-backup-drive`: `BACKUP_REMOTE`, `BACKUP_LOCAL_DIR`,
`BACKUP_RETENCAO_LOCAL_DIAS`, `BACKUP_RETENCAO_DRIVE_DIAS`.

---

## Operação

```bash
systemctl start crm-backup-drive.service       # rodar agora
journalctl -u crm-backup-drive -n 20 --no-pager  # log da última execução
systemctl list-timers crm-backup-drive.timer   # próxima execução
rclone ls gdrive-cripto:                       # dumps no Drive
```

---

## Restauração

O `crm_db` é **SQL_ASCII com locale C**. Criar o banco destino com outra codificação (o padrão é
UTF-8) faz o `pg_restore` falhar por codificação, não por defeito do backup.

```bash
rclone copy gdrive-cripto:crm_db_2026-10-05_0905.dump /var/backups/crm/
sudo -u postgres createdb -E SQL_ASCII --locale=C -T template0 crm_restore_teste
sudo -u postgres pg_restore -d crm_restore_teste < /var/backups/crm/crm_db_2026-10-05_0905.dump
```

Teste feito em 05/10/2026 a partir da cópia baixada do Drive: SHA-256 idêntico ao dump local,
restauração em 5 s sem erros, 169/169 tabelas, 584/584 índices, 249 migrações; 181.682 linhas
contra 181.691 no banco vivo — a diferença eram registros criados depois do dump. Para restaurar
de verdade, o destino é `crm_db` (parar gunicorn, daphne e celery antes).
