from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0069_hotspotconfig_interface_fisica'),
    ]

    operations = [
        migrations.AddField(
            model_name='hotspotconfig',
            name='logo',
            field=models.ImageField(blank=True, null=True, upload_to='hotspot/logos/'),
        ),
    ]
