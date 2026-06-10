import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0011_aluguelipv4_bloco_v6'),
    ]

    operations = [
        migrations.CreateModel(
            name='ContratoAluguel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('status', models.CharField(choices=[('pendente', 'Aguardando assinatura'), ('assinado', 'Assinado'), ('expirado', 'Expirado')], default='pendente', max_length=20)),
                ('assinatura_img', models.TextField(blank=True, default='')),
                ('nome_assinante', models.CharField(blank=True, default='', max_length=255)),
                ('ip_assinante', models.GenericIPAddressField(blank=True, null=True)),
                ('assinado_em', models.DateTimeField(blank=True, null=True)),
                ('pdf_assinado', models.FileField(blank=True, null=True, upload_to='contratos/assinados/')),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('expira_em', models.DateTimeField(blank=True, null=True)),
                ('aluguel', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contratos', to='financeiro.aluguelipv4')),
            ],
            options={
                'verbose_name': 'Contrato de Aluguel',
                'verbose_name_plural': 'Contratos de Aluguel',
                'ordering': ['-criado_em'],
            },
        ),
    ]
