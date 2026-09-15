from celery import shared_task


@shared_task(name='tarefas.tasks.gerar_rotinas_mensais')
def gerar_rotinas_mensais():
    """Agendada de hora em hora em crm/celery.py. Rodar várias vezes no dia
    é seguro (ver services.gerar_ocorrencias_rotinas) e faz a tarefa do dia
    aparecer mesmo se o worker estava parado à meia-noite."""
    from .services import gerar_ocorrencias_rotinas
    return len(gerar_ocorrencias_rotinas())
