# Generated migration for adding privada field to AluguelIPv4

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0008_consultoria_privada'),
    ]

    operations = [
        migrations.AddField(
            model_name='alugueipv4',
            name='privada',
            field=models.BooleanField(default=False, help_text='Marcar como privada para mostrar apenas para staff'),
        ),
    ]
