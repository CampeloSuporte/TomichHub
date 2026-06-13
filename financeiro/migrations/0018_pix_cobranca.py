from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0017_whatsapp_cobranca'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracaofinanceira',
            name='wa_pix_chave',
            field=models.CharField(blank=True, default='', help_text='Chave PIX para incluir na mensagem de cobrança', max_length=150, verbose_name='Chave PIX'),
        ),
        migrations.AddField(
            model_name='configuracaofinanceira',
            name='wa_pix_tipo',
            field=models.CharField(blank=True, default='', help_text='Ex: CPF, CNPJ, E-mail, Telefone, Aleatória', max_length=20, verbose_name='Tipo da chave PIX'),
        ),
    ]
