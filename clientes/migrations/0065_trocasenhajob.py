from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0064_acesso_modelo_auto_em'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TrocaSenhaJob',
            fields=[
                ('id',            models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status',        models.CharField(
                    choices=[
                        ('PENDENTE',     'Pendente'),
                        ('EM_ANDAMENTO', 'Em Andamento'),
                        ('CONCLUIDO',    'Concluído'),
                        ('ERRO',         'Erro'),
                    ],
                    default='PENDENTE', max_length=20)),
                ('novo_usuario',  models.CharField(max_length=50)),
                ('nova_senha',    models.CharField(max_length=100)),
                ('criado_em',     models.DateTimeField(auto_now_add=True)),
                ('concluido_em',  models.DateTimeField(blank=True, null=True)),
                ('total_acessos', models.IntegerField(default=0)),
                ('total_sucesso', models.IntegerField(default=0)),
                ('total_erro',    models.IntegerField(default=0)),
                ('cliente',       models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='troca_senha_jobs',
                    to='clientes.cliente')),
                ('criado_por',    models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='troca_senha_jobs',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name':        'Job Troca de Senha',
                'verbose_name_plural': 'Jobs Troca de Senha',
                'ordering':            ['-criado_em'],
            },
        ),
        migrations.CreateModel(
            name='TrocaSenhaAcesso',
            fields=[
                ('id',               models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status',           models.CharField(
                    choices=[('PENDENTE', 'Pendente'), ('SUCESSO', 'Sucesso'), ('ERRO', 'Erro')],
                    default='PENDENTE', max_length=10)),
                ('mensagem',         models.TextField(blank=True)),
                ('usuario_antigo',   models.CharField(blank=True, max_length=50)),
                ('senha_antiga',     models.CharField(blank=True, max_length=100)),
                ('executado_em',     models.DateTimeField(blank=True, null=True)),
                ('duracao_segundos', models.FloatField(blank=True, null=True)),
                ('usuario_removido', models.BooleanField(default=False)),
                ('job',              models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='itens',
                    to='clientes.trocasenhajob')),
                ('acesso',           models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='troca_senha_historico',
                    to='clientes.acesso')),
            ],
            options={
                'verbose_name':        'Troca de Senha — Acesso',
                'verbose_name_plural': 'Trocas de Senha — Acessos',
                'ordering':            ['id'],
            },
        ),
    ]
