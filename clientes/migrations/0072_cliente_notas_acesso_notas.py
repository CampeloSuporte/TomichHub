from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0071_hotspotconfig_dhcp_controle_banda'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='notas',
            field=models.TextField(
                blank=True,
                default='',
                verbose_name='Notas do Agent NOC',
                help_text='Informações, peculiaridades e contexto do cliente para o Agent NOC.',
            ),
        ),
        migrations.AddField(
            model_name='acesso',
            name='notas',
            field=models.TextField(
                blank=True,
                default='',
                verbose_name='Notas do Agent NOC',
                help_text='Notas sobre este host para o Agent NOC.',
            ),
        ),
    ]
