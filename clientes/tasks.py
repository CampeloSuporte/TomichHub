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
def limpar_backups_antigos(dias=3):
    """
    ✅ Task para limpar backups antigos com validações:
    - Exclui backups com mais de X dias
    - MAS mantém SEMPRE os 2 últimos de cada acesso
    - Antes de excluir, valida:
      1. Existe backup do MESMO ACESSO no dia da limpeza
      2. Esse backup tem tamanho IGUAL OU MAIOR
    """
    from django.utils import timezone
    from datetime import timedelta
    import os
    from django.conf import settings
    
    logger.info(f"🗑️ Limpando backups com mais de {dias} dias...")
    logger.info(f"📋 Regras: Manter 2 últimos | Validar backup do dia com tamanho ≥")
    
    data_limite = timezone.now() - timedelta(days=dias)
    logger.info(f"📅 Data limite: {data_limite}")
    
    # Pegar todos os backups antigos
    backups_antigos = BackupLog.objects.filter(
        data_backup__lt=data_limite
    ).select_related('acesso', 'cliente').order_by('acesso_id', '-data_backup')
    
    total = backups_antigos.count()
    logger.info(f"🔍 Total de backups antigos encontrados: {total}")
    
    deletados = 0
    mantidos = 0
    recusados = 0  # Não passou na validação
    erros = 0
    
    # Agrupar backups por acesso
    backups_por_acesso = {}
    for backup in backups_antigos:
        acesso_id = backup.acesso_id
        if acesso_id not in backups_por_acesso:
            backups_por_acesso[acesso_id] = []
        backups_por_acesso[acesso_id].append(backup)
    
    logger.info(f"📊 Backups agrupados por {len(backups_por_acesso)} acessos")
    
    for acesso_id, backups_do_acesso in backups_por_acesso.items():
        # Os backups já estão ordenados por -data_backup (mais recentes primeiro)
        ultimos_2 = backups_do_acesso[:2]  # Manter sempre os 2 últimos
        backups_para_avaliar = backups_do_acesso[2:]  # Resto para avaliar
        
        logger.info(f"\n🔐 Acesso #{acesso_id}: {len(backups_do_acesso)} backups antigos")
        logger.info(f"   ⭐ Mantendo os 2 últimos automaticamente")
        
        for idx, backup in enumerate(backups_para_avaliar, 1):
            try:
                equipamento_info = f"{backup.acesso.tipo} ({backup.acesso.host})"
                logger.info(f"\n   📦 [{idx}] Avaliando backup #{backup.id} - {equipamento_info}")
                logger.info(f"      Data: {backup.data_backup} | Tamanho: {backup.tamanho_bytes} bytes")
                
                # ============================================
                # VALIDAÇÃO 1: Existe backup do mesmo acesso NO DIA?
                # ============================================
                backup_do_dia = BackupLog.objects.filter(
                    acesso=backup.acesso,
                    cliente=backup.cliente,
                    data_backup__gte=data_limite,  # No dia da limpeza ou depois
                    status='SUCESSO'
                ).order_by('-data_backup').first()
                
                if not backup_do_dia:
                    logger.warning(f"      ❌ VALIDAÇÃO FALHOU: Nenhum backup do dia para {equipamento_info}")
                    logger.info(f"      ⚠️ Backup #{backup.id} MANTIDO por segurança")
                    mantidos += 1
                    recusados += 1
                    continue
                
                logger.info(f"      ✅ Backup do dia encontrado: #{backup_do_dia.id}")
                logger.info(f"         Tamanho: {backup_do_dia.tamanho_bytes} bytes")
                
                # ============================================
                # VALIDAÇÃO 2: Backup do dia >= backup antigo?
                # ============================================
                if backup_do_dia.tamanho_bytes < backup.tamanho_bytes:
                    logger.warning(
                        f"      ❌ VALIDAÇÃO FALHOU: Backup do dia é menor!"
                        f"\n         Backup do dia: {backup_do_dia.tamanho_bytes} bytes"
                        f"\n         Backup antigo: {backup.tamanho_bytes} bytes"
                    )
                    logger.info(f"      ⚠️ Backup #{backup.id} MANTIDO por segurança")
                    mantidos += 1
                    recusados += 1
                    continue
                
                logger.info(f"      ✅ Backup do dia é maior/igual: {backup_do_dia.tamanho_bytes} >= {backup.tamanho_bytes}")
                
                # ============================================
                # TUDO OK: Deletar!
                # ============================================
                logger.info(f"      🗑️ ✅ Deletando backup #{backup.id}")
                
                # Deletar arquivo físico
                if backup.arquivo_path:
                    arquivo_path = os.path.join(settings.MEDIA_ROOT, backup.arquivo_path)
                    logger.info(f"         📄 Arquivo: {arquivo_path}")
                    if os.path.exists(arquivo_path):
                        logger.info(f"         Deletando arquivo...")
                        os.remove(arquivo_path)
                        logger.info(f"         ✅ Arquivo deletado")
                    else:
                        logger.warning(f"         ⚠️ Arquivo não encontrado")
                
                # Deletar registro
                backup.delete()
                deletados += 1
                logger.info(f"         ✅ Registro deletado do banco")
                
            except Exception as e:
                erros += 1
                logger.error(f"      ❌ ERRO ao deletar backup #{backup.id}: {str(e)}")
                logger.error(f"         {traceback.format_exc()}")
    
    resultado = {
        'total': total,
        'deletados': deletados,
        'mantidos': mantidos,
        'recusados': recusados,
        'erros': erros
    }
    
    logger.info(f"\n" + "="*60)
    logger.info(f"✅ RESUMO DA LIMPEZA:")
    logger.info(f"   📊 Total encontrado: {total}")
    logger.info(f"   🗑️  Deletados: {deletados}")
    logger.info(f"   ⭐ Mantidos (2 últimos): {mantidos - recusados}")
    logger.info(f"   ⚠️  Recusados (falha na validação): {recusados}")
    logger.info(f"   ❌ Erros: {erros}")
    logger.info(f"="*60)
    
    return resultado