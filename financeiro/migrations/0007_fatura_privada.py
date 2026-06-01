# Generated migration for adding privada field to Fatura

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0006_despesa_privada'),
    ]

    operations = [
        migrations.AddField(
            model_name='fatura',
            name='privada',
            field=models.BooleanField(default=False, help_text='Marcar como privada para mostrar apenas para staff'),
        ),
    ]
