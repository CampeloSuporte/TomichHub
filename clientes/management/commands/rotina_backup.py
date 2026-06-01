"""
Management command: rotina_backup

Executa o pipeline completo de verificação e habilitação de backups:

  Passo 1 — Detecta modelo dos equipamentos via conteúdo do backup
             (apenas hosts ainda sem modelo ou com verificação vencida)

  Passo 2 — Habilita backup_habilitado + backup_automatico para todos
             os acessos SSH que já têm modelo cadastrado, selecionando
             o template correto de acordo com o fabricante

Uso:
  python manage.py rotina_backup
  python manage.py rotina_backup --forcar-modelo      # re-verifica todos os modelos
  python manage.py rotina_backup --apenas-templates   # só corrige templates
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from clientes.models import Acesso, BackupTemplate
from clientes.tasks import (
    detectar_modelos_via_backup,
    habilitar_backups_automaticos,
)


class Command(BaseCommand):
    help = 'Verifica modelos via backup e habilita backup automático nos equipamentos qualificados'

    def add_arguments(self, parser):
        parser.add_argument(
            '--forcar-modelo',
            action='store_true',
            help='Reseta modelo_auto_em e re-verifica todos os equipamentos (ignora cache de 3 dias)',
        )
        parser.add_argument(
            '--apenas-templates',
            action='store_true',
            help='Pula a detecção de modelo, executa apenas a habilitação/correção de templates',
        )

    def handle(self, *args, **options):
        inicio = timezone.now()
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n══════════════════════════════════════════'
        ))
        self.stdout.write(self.style.MIGRATE_HEADING(
            '  ROTINA DE BACKUP — VERIFICAÇÃO E HABILITAÇÃO'
        ))
        self.stdout.write(self.style.MIGRATE_HEADING(
            '══════════════════════════════════════════\n'
        ))

        # ── Passo 1: Detecção de modelo ───────────────────────────────────────
        if not options['apenas_templates']:
            self.stdout.write(self.style.HTTP_INFO('▶ Passo 1 — Detecção de modelo via backup'))

            if options['forcar_modelo']:
                n = Acesso.objects.filter(modelo_auto_em__isnull=False, modelo__isnull=True).update(
                    modelo_auto_em=None
                )
                self.stdout.write(f'  Resetados para re-verificação: {n} acessos')

            antes = Acesso.objects.filter(modelo__isnull=False).count()
            resultado_modelo = detectar_modelos_via_backup()
            depois = Acesso.objects.filter(modelo__isnull=False).count()

            self.stdout.write(self.style.SUCCESS(
                f'  ✓ Verificados: {resultado_modelo["verificados"]} | '
                f'Modelos novos: {resultado_modelo["atualizados"]} | '
                f'Sem arquivo: {resultado_modelo["sem_backup"]} | '
                f'Sem match: {resultado_modelo["sem_match"]}'
            ))
            self.stdout.write(f'  Total com modelo: {antes} → {depois}\n')
        else:
            self.stdout.write(self.style.WARNING('  Detecção de modelo ignorada (--apenas-templates)\n'))

        # ── Passo 2: Habilitação de backup ────────────────────────────────────
        self.stdout.write(self.style.HTTP_INFO('▶ Passo 2 — Habilitação e seleção de template'))

        antes_backup = Acesso.objects.filter(backup_habilitado=True).count()
        resultado_backup = habilitar_backups_automaticos()
        depois_backup = Acesso.objects.filter(backup_habilitado=True).count()

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Habilitados: {resultado_backup["habilitados"]} | '
            f'Templates corrigidos: {resultado_backup["corrigidos"]} | '
            f'Sem template: {resultado_backup["sem_template"]}'
        ))
        removidos = resultado_backup.get('removidos', 0)
        if removidos:
            self.stdout.write(self.style.WARNING(
                f'  ⚠ Backup REMOVIDO (VM/hipervisor): {removidos} equipamentos'
            ))
        self.stdout.write(f'  Total com backup ativo: {antes_backup} → {depois_backup}\n')

        # ── Resumo por template ───────────────────────────────────────────────
        from django.db.models import Count
        self.stdout.write(self.style.HTTP_INFO('▶ Distribuição por template:'))
        qs = (
            Acesso.objects
            .filter(backup_habilitado=True, backup_template__isnull=False)
            .values('backup_template__nome', 'backup_template__fabricante')
            .annotate(total=Count('id'))
            .order_by('-total')
        )
        for row in qs:
            self.stdout.write(
                f'  {row["backup_template__nome"]:<32}'
                f'({row["backup_template__fabricante"]:<10})'
                f'  {row["total"]} equipamentos'
            )

        # ── Pendências ────────────────────────────────────────────────────────
        sem_modelo = Acesso.objects.filter(
            protocolo='SSH', modelo__isnull=True, backup_habilitado=False
        ).count()
        nao_ssh = Acesso.objects.filter(
            backup_habilitado=False
        ).exclude(protocolo='SSH').count()

        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            f'  SSH sem modelo (pendente próx. ciclo): {sem_modelo}'
        ))
        self.stdout.write(self.style.WARNING(
            f'  Não-SSH (Telnet/HTTP/etc, excluídos): {nao_ssh}'
        ))

        duracao = (timezone.now() - inicio).total_seconds()
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n══════════════════════════════════════════'
        ))
        self.stdout.write(self.style.SUCCESS(
            f'  Concluído em {duracao:.1f}s'
        ))
        self.stdout.write(self.style.MIGRATE_HEADING(
            '══════════════════════════════════════════\n'
        ))
