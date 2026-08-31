from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0112_remover_wireguard'),
    ]

    operations = [
        migrations.AddField(
            model_name='geofeedbloco',
            name='bloco_rir',
            field=models.CharField(blank=True, db_index=True, max_length=50, verbose_name='Bloco original (alocado no RIR)'),
        ),
    ]
