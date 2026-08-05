from django.db import migrations


def usar_show_sensitive(apps, schema_editor):
    """
    /export terse (sem show-sensitive) mascara senhas de usuário, PPP secret,
    hotspot etc no RouterOS >= 6.43 — o backup salvo não permitia recuperar
    credenciais. /export show-sensitive grava os valores reais em texto puro,
    que é justamente o propósito do backup .rsc (ao contrário do /system
    backup binário, que não expõe as senhas em texto legível).
    """
    BackupTemplate = apps.get_model('clientes', 'BackupTemplate')
    for template in BackupTemplate.objects.filter(fabricante='MIKROTIK'):
        comandos = [c.strip() for c in template.comandos.split('\n') if c.strip()]
        alterado = False
        novos = []
        for cmd in comandos:
            if cmd.startswith('/export') and 'show-sensitive' not in cmd:
                cmd = cmd.replace('/export', '/export show-sensitive', 1)
                alterado = True
            novos.append(cmd)
        if alterado:
            template.comandos = '\n'.join(novos)
            template.save(update_fields=['comandos'])


def reverter(apps, schema_editor):
    BackupTemplate = apps.get_model('clientes', 'BackupTemplate')
    for template in BackupTemplate.objects.filter(fabricante='MIKROTIK'):
        comandos = [c.strip() for c in template.comandos.split('\n') if c.strip()]
        novos = [c.replace('/export show-sensitive', '/export', 1) for c in comandos]
        template.comandos = '\n'.join(novos)
        template.save(update_fields=['comandos'])


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0099_bgpsnapshot_patch_local_pendente'),
    ]

    operations = [
        migrations.RunPython(usar_show_sensitive, reverter),
    ]
