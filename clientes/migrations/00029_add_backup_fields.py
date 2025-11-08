# ============================================
# MIGRATION PARA ADICIONAR CAMPOS DE BACKUP
# Crie este arquivo em: cliente/migrations/000X_add_backup_fields.py
# (Substitua 000X pelo próximo número sequencial)
# ============================================

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cliente', '0001_initial'),  # ⚠️ AJUSTE para sua última migration
    ]

    operations = [
        # Criar model BackupTemplate
        migrations.CreateModel(
            name='BackupTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=100)),
                ('fabricante', models.CharField(choices=[
                    ('CISCO', 'Cisco'),
                    ('HUAWEI', 'Huawei'),
                    ('MIKROTIK', 'MikroTik'),
                    ('JUNIPER', 'Juniper'),
                    ('DELL', 'Dell'),
                    ('HP', 'HP/Aruba'),
                    ('EXTREME', 'Extreme Networks'),
                    ('GENERICO', 'Genérico'),
                ], max_length=20)),
                ('comandos', models.TextField(help_text='Comandos separados por linha')),
                ('ativo', models.BooleanField(default=True)),
                ('descricao', models.TextField(blank=True, null=True)),
                ('data_criacao', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Template de Backup',
                'verbose_name_plural': 'Templates de Backup',
                'ordering': ['fabricante', 'nome'],
            },
        ),
        
        # Adicionar campos de backup ao model Acesso
        migrations.AddField(
            model_name='acesso',
            name='backup_habilitado',
            field=models.BooleanField(default=False, verbose_name='Habilitar Backup'),
        ),
        migrations.AddField(
            model_name='acesso',
            name='backup_template',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='acessos',
                to='cliente.backuptemplate',
                verbose_name='Template de Backup'
            ),
        ),
        migrations.AddField(
            model_name='acesso',
            name='backup_automatico',
            field=models.BooleanField(
                default=False,
                help_text='Executar backup automaticamente via agendamento',
                verbose_name='Backup Automático'
            ),
        ),
        
        # Criar model BackupLog
        migrations.CreateModel(
            name='BackupLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('arquivo_path', models.CharField(max_length=500)),
                ('tamanho_bytes', models.IntegerField(default=0)),
                ('status', models.CharField(
                    choices=[
                        ('SUCESSO', 'Sucesso'),
                        ('ERRO', 'Erro'),
                        ('PARCIAL', 'Parcial'),
                    ],
                    default='SUCESSO',
                    max_length=10
                )),
                ('mensagem', models.TextField(blank=True, null=True)),
                ('data_backup', models.DateTimeField(auto_now_add=True)),
                ('duracao_segundos', models.FloatField(default=0)),
                ('acesso', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='backups',
                    to='cliente.acesso'
                )),
                ('cliente', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='backups',
                    to='cliente.cliente'
                )),
                ('executado_por', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to='auth.user'
                )),
                ('template', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to='cliente.backuptemplate'
                )),
            ],
            options={
                'verbose_name': 'Log de Backup',
                'verbose_name_plural': 'Logs de Backup',
                'ordering': ['-data_backup'],
            },
        ),
    ]
