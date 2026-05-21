from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0059_agentconfig_openai'),
    ]

    operations = [
        migrations.AddField(
            model_name='whatsappgrupo',
            name='acesso_global',
            field=models.BooleanField(
                default=False,
                verbose_name='Acesso Global',
                help_text='Permite que este grupo acesse hosts de qualquer cliente da plataforma. Use apenas em grupos NOC internos.',
            ),
        ),
    ]
