# Generated migration for adding privada field to VendaEquipamento

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0009_alugueipv4_privada'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendaequipamento',
            name='privada',
            field=models.BooleanField(default=False, help_text='Marcar como privada para mostrar apenas para staff'),
        ),
    ]
