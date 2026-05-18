# Generated manually
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0052_backuplog_hash_sem_mudancas'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ScriptCRM',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=255, verbose_name='Nome')),
                ('descricao', models.TextField(blank=True, verbose_name='Descrição')),
                ('fabricante', models.CharField(
                    choices=[
                        ('zte', 'ZTE'), ('huawei', 'Huawei'), ('cisco', 'Cisco'),
                        ('mikrotik', 'MikroTik'), ('datacom', 'Datacom'),
                        ('parks', 'Parks'), ('generico', 'Genérico'),
                    ],
                    default='generico', max_length=30, verbose_name='Fabricante',
                )),
                ('modo_execucao', models.CharField(
                    choices=[
                        ('operacional', 'Operacional (show/get)'),
                        ('configuracao', 'Configuração (config)'),
                    ],
                    default='operacional', max_length=20, verbose_name='Modo',
                )),
                ('comandos', models.TextField(verbose_name='Comandos')),
                ('parametros', models.JSONField(default=list, verbose_name='Parâmetros')),
                ('ativo', models.BooleanField(default=True, verbose_name='Ativo')),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('criado_por', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='scripts_criados',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['fabricante', 'nome'], 'verbose_name': 'Script de Automação', 'verbose_name_plural': 'Scripts de Automação'},
        ),
        migrations.CreateModel(
            name='ScriptExecucaoLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('parametros_usados', models.JSONField(default=dict)),
                ('output', models.TextField(blank=True)),
                ('status', models.CharField(
                    choices=[
                        ('executando', 'Executando'), ('sucesso', 'Sucesso'),
                        ('erro', 'Erro'), ('parcial', 'Parcial'),
                    ],
                    default='executando', max_length=20,
                )),
                ('iniciado_em', models.DateTimeField(auto_now_add=True)),
                ('finalizado_em', models.DateTimeField(blank=True, null=True)),
                ('acesso', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='script_execucoes',
                    to='clientes.acesso',
                )),
                ('script', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='execucoes',
                    to='clientes.scriptcrm',
                )),
                ('usuario', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-iniciado_em'], 'verbose_name': 'Log de Execução de Script', 'verbose_name_plural': 'Logs de Execução de Scripts'},
        ),
    ]
