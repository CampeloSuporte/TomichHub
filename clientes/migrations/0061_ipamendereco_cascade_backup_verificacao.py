from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0060_whatsappgrupo_acesso_global'),
    ]

    operations = [
        # IPAMEndereco.subrede: SET_NULL → CASCADE
        migrations.AlterField(
            model_name='ipamendereco',
            name='subrede',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='ips',
                to='clientes.ipamsubrede',
            ),
        ),
        # BackupLog: novo campo ultima_verificacao
        migrations.AddField(
            model_name='backuplog',
            name='ultima_verificacao',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Última verificação sem mudanças',
            ),
        ),
    ]
