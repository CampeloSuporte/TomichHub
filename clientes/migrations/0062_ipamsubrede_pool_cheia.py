from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0061_ipamendereco_cascade_backup_verificacao'),
    ]

    operations = [
        migrations.AddField(
            model_name='ipamsubrede',
            name='pool_cheia',
            field=models.BooleanField(default=False),
        ),
    ]
