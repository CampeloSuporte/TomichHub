# Generated migration for adding privada field to Consultoria

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0007_fatura_privada'),
    ]

    operations = [
        migrations.AddField(
            model_name='consultoria',
            name='privada',
            field=models.BooleanField(default=False, help_text='Marcar como privada para mostrar apenas para staff'),
        ),
    ]
