"""
financeiro/tasks.py
Tasks Celery para alertas financeiros via WhatsApp.
"""
import logging
from datetime import date, timedelta
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='financeiro.tasks.enviar_alertas_whatsapp')
def enviar_alertas_whatsapp():
    """
    Roda de segunda a sexta às 8:30 (agendado no celery beat).
    Envia alertas via WhatsApp para faturas em aberto:
      - que vencem em exatamente wa_dias_antes dias (aviso)
      - que venceram hoje ou estão vencidas (cobrança)
    """
    from .models import ConfiguracaoFinanceira, Fatura
    from .whatsapp import enviar_cobranca_fatura

    # Segurança: não rodar em fim de semana mesmo se disparado manualmente
    hoje = date.today()
    if hoje.weekday() >= 5:  # 5=sábado, 6=domingo
        logger.info('financeiro.tasks: fim de semana, pulando.')
        return

    try:
        cfg = ConfiguracaoFinanceira.objects.get(pk=1)
    except ConfiguracaoFinanceira.DoesNotExist:
        logger.info('financeiro.tasks: ConfiguracaoFinanceira não existe, pulando.')
        return

    if not cfg.wa_ativo:
        logger.info('financeiro.tasks: alertas WhatsApp desativados, pulando.')
        return

    aviso_em = hoje + timedelta(days=cfg.wa_dias_antes)

    # Faturas que vencem na data de aviso
    faturas_aviso = Fatura.objects.filter(
        data_vencimento=aviso_em,
        status__in=['ABERTA', 'RASCUNHO'],
    ).select_related('cliente')

    # Faturas vencidas (qualquer dia de atraso)
    faturas_vencidas = Fatura.objects.filter(
        data_vencimento__lt=hoje,
        status__in=['ABERTA', 'RASCUNHO'],
    ).select_related('cliente')

    total_ok = total_erro = 0

    for fatura in list(faturas_aviso) + list(faturas_vencidas):
        ok, detalhe = enviar_cobranca_fatura(fatura)
        if ok:
            total_ok += 1
            logger.info('WhatsApp cobrança enviado: fatura %s → %s',
                        fatura.numero_fatura, fatura.cliente.nome_empresa)
        else:
            total_erro += 1
            logger.warning('WhatsApp cobrança falhou: fatura %s → %s | %s',
                           fatura.numero_fatura, fatura.cliente.nome_empresa, detalhe)

    logger.info('financeiro.tasks: %d enviados, %d erros.', total_ok, total_erro)
    return {'ok': total_ok, 'erro': total_erro}
