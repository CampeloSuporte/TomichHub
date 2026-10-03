import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def sincronizar_vinculos_zabbix():
    """Refaz os vínculos automáticos Acesso ↔ host Zabbix de todo cliente com
    Zabbix cadastrado (configuração de Monitoramento ou acesso "Zabbix").
    Um cliente fora do ar não impede os outros."""
    from clientes.models import Acesso

    from . import vinculo_zabbix as vz
    from .models import ZabbixConfig

    ids = set(ZabbixConfig.objects.filter(ativo=True).values_list('cliente_id', flat=True))
    ids |= set(Acesso.objects.filter(tipo__icontains='zabbix', protocolo__in=['HTTP', 'HTTPS'])
               .values_list('cliente_id', flat=True))

    ok, falhas = 0, 0
    for cliente_id in sorted(ids):
        try:
            vz.sincronizar(cliente_id)
            ok += 1
        except Exception as exc:
            falhas += 1
            logger.warning('[ZBX-VINC] sincronismo do cliente %s falhou: %s', cliente_id, exc)
    return {'clientes': len(ids), 'ok': ok, 'falhas': falhas}
