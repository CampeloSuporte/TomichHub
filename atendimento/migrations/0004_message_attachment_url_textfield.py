from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('atendimento', '0003_add_display_name_to_agentstatus'),
    ]

    operations = [
        migrations.AlterField(
            model_name='message',
            name='attachment_url',
            field=models.TextField(blank=True, null=True),
        ),
    ]
