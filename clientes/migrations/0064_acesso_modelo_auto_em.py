from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0063_acesso_contexto_backup'),
    ]

    operations = [
        migrations.AddField(
            model_name='acesso',
            name='modelo_auto_em',
            field=models.DateTimeField(
                null=True, blank=True,
                verbose_name='Modelo auto-detectado em',
                help_text='Última vez que a task tentou detectar o modelo via backup'
            ),
        ),
    ]
