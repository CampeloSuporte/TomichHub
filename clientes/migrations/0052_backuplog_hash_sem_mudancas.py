from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0051_correcao_geoip'),
    ]

    operations = [
        migrations.AddField(
            model_name='backuplog',
            name='hash_conteudo',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='Hash SHA-256'),
        ),
        migrations.AlterField(
            model_name='backuplog',
            name='status',
            field=models.CharField(
                choices=[
                    ('SUCESSO', 'Sucesso'),
                    ('ERRO', 'Erro'),
                    ('PARCIAL', 'Parcial'),
                    ('SEM_MUDANCAS', 'Sem mudanças'),
                ],
                default='SUCESSO',
                max_length=15,
            ),
        ),
        migrations.AlterField(
            model_name='backuplog',
            name='arquivo_path',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
