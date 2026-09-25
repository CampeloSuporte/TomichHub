from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0117_agentconfig_janela_continuacao_wa'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='agentconfig',
            name='janela_continuacao_wa',
        ),
    ]
