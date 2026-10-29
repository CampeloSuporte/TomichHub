from django.db import migrations, models


def todos_online(apps, schema_editor):
    # O campo existia sem uso (default 'offline', ninguém nunca mudava).
    # Agora 'offline' dispara alerta no NOC — começa todo mundo online.
    apps.get_model('atendimento', 'AgentStatus').objects.update(status='online')


class Migration(migrations.Migration):

    dependencies = [
        ('atendimento', '0020_message_transcricao'),
    ]

    operations = [
        migrations.AlterField(
            model_name='agentstatus',
            name='status',
            field=models.CharField(choices=[('online', 'Online'), ('busy', 'Ocupado'), ('away', 'Ausente'), ('offline', 'Offline')], default='online', max_length=20),
        ),
        migrations.AddField(
            model_name='agentstatus',
            name='status_changed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='conversation',
            name='offline_alert_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(todos_online, migrations.RunPython.noop),
    ]
