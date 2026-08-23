"""Manutenção periódica do módulo de segurança (Celery beat)."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='seguranca.limpar_registros')
def limpar_registros():
    """Poda tentativas/eventos além da retenção (SEGURANCA_RETENCAO_DIAS).

    Sem isso a `seguranca_tentativalogin` só cresce: um servidor exposto na
    internet leva milhares de tentativas de robô por dia, e é justamente esse
    tráfego que não para.
    """
    from . import services
    resultado = services.limpar_registros_antigos()
    logger.info('Limpeza de segurança: %s', resultado)
    return resultado
