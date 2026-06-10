from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0010_vendaequipamento_privada'),
    ]

    operations = [
        migrations.AddField(
            model_name='aluguelipv4',
            name='bloco_v6',
            field=models.CharField(blank=True, default='', help_text='Ex: 2804:1234::/48 (opcional)', max_length=100),
        ),
    ]
