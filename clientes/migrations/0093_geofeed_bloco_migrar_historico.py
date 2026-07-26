from django.db import migrations


def migrar_historico(apps, schema_editor):
    """Popula GeofeedBloco com o prefixo mais recente de cada CorrecaoGeoIP,
    preservando o conteúdo atual do geofeed.csv público."""
    CorrecaoGeoIP = apps.get_model('clientes', 'CorrecaoGeoIP')
    GeofeedBloco = apps.get_model('clientes', 'GeofeedBloco')

    vistos = set()
    for reg in CorrecaoGeoIP.objects.order_by('prefixo', '-data_envio'):
        if reg.prefixo in vistos:
            continue
        vistos.add(reg.prefixo)
        GeofeedBloco.objects.update_or_create(
            prefixo=reg.prefixo,
            defaults={
                'pais': reg.pais,
                'regiao': reg.regiao,
                'cidade': reg.cidade,
                'ativo': True,
                'criado_por': reg.solicitante,
            },
        )


def reverter(apps, schema_editor):
    GeofeedBloco = apps.get_model('clientes', 'GeofeedBloco')
    GeofeedBloco.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0092_geofeed_bloco'),
    ]

    operations = [
        migrations.RunPython(migrar_historico, reverter),
    ]
