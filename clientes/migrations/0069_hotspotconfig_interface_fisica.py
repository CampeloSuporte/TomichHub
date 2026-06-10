from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0068_hotspot_lead_data_nascimento'),
    ]

    operations = [
        migrations.AddField(
            model_name='hotspotconfig',
            name='interface_fisica',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
    ]
