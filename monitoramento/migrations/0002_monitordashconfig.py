from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0064_acesso_modelo_auto_em'),
        ('monitoramento', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MonitorDashConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('dados', models.JSONField(blank=True, default=list, help_text='Lista de charts configurados')),
                ('data_atualizacao', models.DateTimeField(auto_now=True)),
                ('cliente', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='monitor_dash_config',
                    to='clientes.cliente',
                )),
            ],
            options={
                'verbose_name': 'Config Dashboard Monitor',
                'verbose_name_plural': 'Configs Dashboard Monitor',
            },
        ),
    ]
