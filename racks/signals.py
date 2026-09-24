"""
Salvar a topologia sincroniza os cabos do rack (`services.sincronizar_cabos`).

Por signal e não dentro de `clientes.views.topologia_salvar`: o editor de
topologia não precisa saber que racks existem. Qualquer falha aqui é só
registrada — nunca pode impedir o mapa de ser salvo.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from clientes.models import TopologiaDiagrama

logger = logging.getLogger(__name__)


@receiver(post_save, sender=TopologiaDiagrama, dispatch_uid='racks_sincronizar_ao_salvar_topologia')
def sincronizar_ao_salvar_topologia(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from .models import RackEquipamento
    from .services import sincronizar_cabos
    # Cliente sem nada montado (a maioria): nem lê o mapa.
    if not RackEquipamento.objects.filter(rack__cliente_id=instance.cliente_id).exists():
        return
    try:
        sincronizar_cabos(instance.cliente)
    except Exception:
        logger.exception('Falha ao sincronizar cabos do rack com a topologia (cliente %s)', instance.cliente_id)
