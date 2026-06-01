# Generated migration for adding privada field to Despesa

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0005_despesa_recorrencia'),
    ]

    operations = [
        migrations.AddField(
            model_name='despesa',
            name='privada',
            field=models.BooleanField(default=False, help_text='Marcar como privada para mostrar apenas ao criador'),
        ),
    ]
