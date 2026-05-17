from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import clientes.models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0049_add_imap_to_configuracaosistema'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FirmwarePasta',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=255)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('pai', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='subpastas', to='clientes.firmwarepasta')),
            ],
            options={'ordering': ['nome']},
        ),
        migrations.CreateModel(
            name='FirmwareArquivo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=500)),
                ('arquivo', models.FileField(upload_to=clientes.models.firmware_upload_path)),
                ('tamanho', models.BigIntegerField(default=0)),
                ('mime_type', models.CharField(blank=True, max_length=200)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('criado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('pasta', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='arquivos', to='clientes.firmwarepasta')),
            ],
            options={'ordering': ['nome']},
        ),
        migrations.CreateModel(
            name='FirmwareCompartilhamento',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(max_length=64, unique=True)),
                ('expira_em', models.DateTimeField()),
                ('ftp_user', models.CharField(blank=True, max_length=50)),
                ('ftp_senha', models.CharField(blank=True, max_length=50)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('acessos', models.IntegerField(default=0)),
                ('arquivo', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='compartilhamentos', to='clientes.firmwarearquivo')),
            ],
            options={'ordering': ['-criado_em']},
        ),
    ]
