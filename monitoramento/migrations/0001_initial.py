from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        # FK para o app clientes
        ('clientes', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ZabbixConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url', models.CharField(help_text='URL base do Zabbix (ex: https://zabbix.empresa.com) — sem /api_jsonrpc.php', max_length=500)),
                ('usuario', models.CharField(blank=True, default='', max_length=100)),
                ('senha', models.CharField(blank=True, default='', max_length=255)),
                ('api_token', models.CharField(blank=True, help_text='Token de API (Zabbix 5.4+). Se preenchido, ignora usuário/senha.', max_length=512, null=True)),
                ('ativo', models.BooleanField(default=True)),
                ('data_criacao', models.DateTimeField(auto_now_add=True)),
                ('data_atualizacao', models.DateTimeField(auto_now=True)),
                ('cliente', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='zabbix_config', to='clientes.cliente')),
            ],
            options={
                'verbose_name': 'Configuração Zabbix',
                'verbose_name_plural': 'Configurações Zabbix',
            },
        ),
        migrations.CreateModel(
            name='MonitorTopology',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=255)),
                ('descricao', models.TextField(blank=True, default='')),
                ('data_criacao', models.DateTimeField(auto_now_add=True)),
                ('data_atualizacao', models.DateTimeField(auto_now=True)),
                ('cliente', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='monitor_topologies', to='clientes.cliente')),
            ],
            options={
                'verbose_name': 'Topologia de Monitoramento',
                'verbose_name_plural': 'Topologias de Monitoramento',
                'ordering': ['-data_atualizacao'],
            },
        ),
        migrations.CreateModel(
            name='MonitorNode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('router', 'Roteador'), ('switch', 'Switch'), ('firewall', 'Firewall'), ('server', 'Servidor'), ('ap', 'Access Point'), ('cloud', 'Nuvem / Internet'), ('endpoint', 'Host / Endpoint')], default='switch', max_length=50)),
                ('label', models.CharField(max_length=255)),
                ('zabbix_hostid', models.CharField(blank=True, max_length=100, null=True)),
                ('zabbix_hostname', models.CharField(blank=True, max_length=255, null=True)),
                ('pos_x', models.FloatField(default=200)),
                ('pos_y', models.FloatField(default=200)),
                ('topologia', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='nodes', to='monitoramento.monitortopology')),
            ],
            options={
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='MonitorLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label', models.CharField(blank=True, default='', max_length=100)),
                ('zabbix_itemid_in', models.CharField(blank=True, help_text='Item ID Zabbix — tráfego de entrada (bps)', max_length=100, null=True)),
                ('zabbix_itemid_out', models.CharField(blank=True, help_text='Item ID Zabbix — tráfego de saída (bps)', max_length=100, null=True)),
                ('zabbix_itemid_status', models.CharField(blank=True, help_text='Item ID Zabbix — status operacional da interface (0=down / 1=up)', max_length=100, null=True)),
                ('topologia', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='links', to='monitoramento.monitortopology')),
                ('node_destino', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='links_entrada', to='monitoramento.monitornode')),
                ('node_origem', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='links_saida', to='monitoramento.monitornode')),
            ],
            options={
                'ordering': ['id'],
            },
        ),
    ]
