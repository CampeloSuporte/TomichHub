from celery import shared_task
from celery.utils.log import get_task_logger
from django.contrib.auth.models import User
from .models import Acesso, BackupLog, BackupTemplate
from .views import realizar_backup
from datetime import datetime
import traceback
import os

logger = get_task_logger(__name__)


@shared_task(bind=True, max_retries=3)
def executar_backup_agendado(self, acesso_id):
    """
    ✅ Task Celery para executar backup automaticamente
    - Tenta até 3 vezes se falhar
    - Registra no log
    """
    try:
        acesso = Acesso.objects.get(id=acesso_id)
        
        # Verificar se backup está habilitado E agendamento ativo
        if not acesso.backup_habilitado or not acesso.backup_automatico:
            logger.warning(
                f"⚠️ Backup agendado desconsiderado para {acesso.tipo} - "
                f"Habilitado: {acesso.backup_habilitado}, "
                f"Automático: {acesso.backup_automatico}"
            )
            return
        
        if not acesso.backup_template:
            logger.error(
                f"❌ Sem template para {acesso.tipo} ({acesso.host})"
            )
            return
        
        logger.info(
            f"🔄 [AGENDADO] Iniciando backup de {acesso.tipo} ({acesso.host})"
        )
        
        # Executar backup
        resultado = realizar_backup(acesso, usuario=None)
        
        if resultado['sucesso']:
            logger.info(
                f"✅ [AGENDADO] Backup sucesso: {acesso.tipo} "
                f"({resultado['tamanho']} bytes em {resultado['duracao']})"
            )
        else:
            logger.error(
                f"❌ [AGENDADO] Backup falhou para {acesso.tipo}: "
                f"{resultado['erro']}"
            )
            # Retry com delay exponencial
            raise Exception(resultado['erro'])
        
    except Acesso.DoesNotExist:
        logger.error(f"❌ Acesso #{acesso_id} não encontrado")
    except Exception as exc:
        logger.error(
            f"❌ [AGENDADO] Erro ao executar backup: {str(exc)}\n"
            f"{traceback.format_exc()}"
        )
        # Retry com backoff exponencial
        self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task
def agendar_backups_pendentes():
    """
    ✅ Task periódica que verifica quais backups precisam ser agendados
    - Executada a cada X minutos (configurável)
    - Agenda todos os acessos com backup_automatico=True
    """
    logger.info("🔍 Verificando backups agendados...")
    
    # Buscar todos os acessos com backup automático habilitado
    acessos_automaticos = Acesso.objects.filter(
        backup_habilitado=True,
        backup_automatico=True,
        backup_template__isnull=False
    ).select_related('cliente', 'backup_template')
    
    total = acessos_automaticos.count()
    logger.info(f"📊 Total de acessos com backup automático: {total}")
    
    agendados = 0
    for acesso in acessos_automaticos:
        try:
            # Agendar a task
            executar_backup_agendado.delay(acesso.id)
            agendados += 1
            logger.info(
                f"✅ Backup agendado para {acesso.tipo} ({acesso.host})"
            )
        except Exception as e:
            logger.error(
                f"❌ Erro ao agendar {acesso.tipo}: {str(e)}"
            )
    
    logger.info(f"✅ {agendados}/{total} backups agendados com sucesso")
    return {
        'total': total,
        'agendados': agendados,
        'timestamp': datetime.now().isoformat()
    }


@shared_task
def limpar_backups_antigos(dias=30):
    """
    ✅ Task para limpar backups antigos
    - Mantém apenas os últimos X dias
    - Deleta arquivo físico e registro
    """
    from django.utils import timezone
    from datetime import timedelta
    import os
    from django.conf import settings
    
    logger.info(f"🗑️ Limpando backups com mais de {dias} dias...")
    
    data_limite = timezone.now() - timedelta(days=dias)
    backups_antigos = BackupLog.objects.filter(data_backup__lt=data_limite)
    
    total = backups_antigos.count()
    deletados = 0
    
    for backup in backups_antigos:
        try:
            # Deletar arquivo físico
            if backup.arquivo_path:
                arquivo_path = os.path.join(settings.MEDIA_ROOT, backup.arquivo_path)
                if os.path.exists(arquivo_path):
                    os.remove(arquivo_path)
            
            # Deletar registro
            backup.delete()
            deletados += 1
            
        except Exception as e:
            logger.error(f"❌ Erro ao deletar backup #{backup.id}: {str(e)}")
    
    logger.info(f"✅ {deletados}/{total} backups antigos removidos")
    return {
        'total': total,
        'deletados': deletados
    }