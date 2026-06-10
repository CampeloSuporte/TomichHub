from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0012_contratoaluguel'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracaofinanceira',
            name='assinatura_locador',
            field=models.TextField(blank=True, default='',
                help_text='Assinatura do locador em base64 (PNG). Usada automaticamente nos contratos.'),
        ),
    ]
