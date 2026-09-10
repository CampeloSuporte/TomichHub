from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0113_geofeedbloco_bloco_rir'),
    ]

    operations = [
        migrations.AddField(
            model_name='openvpnconfig',
            name='usar_profile_existente',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='openvpnconfig',
            name='ppp_profile',
            field=models.CharField(blank=True, default='OPEN_VPN', max_length=100),
        ),
    ]
