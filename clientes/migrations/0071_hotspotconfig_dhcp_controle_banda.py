from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0070_hotspotconfig_logo'),
    ]

    operations = [
        migrations.AddField(
            model_name='hotspotconfig',
            name='dhcp_controle_banda',
            field=models.BooleanField(default=False, help_text='Ativar queue simple por IP via DHCP lease script'),
        ),
        migrations.AddField(
            model_name='hotspotconfig',
            name='dhcp_banda_limit',
            field=models.CharField(default='10M/10M', help_text='Ex: 10M/10M (download/upload)', max_length=20),
        ),
    ]
