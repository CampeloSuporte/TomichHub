# clientes/tasks.py - VERSÃO FINAL FLEXÍVEL
"""
✅ VERSÃO COM VALIDAÇÃO FLEXÍVEL

Problema anterior:
  Backup do dia: 184694 bytes
  Backup antigo: 185077 bytes
  Falha porque é 383 bytes menor (0.2%)
  → NÃO DELETA os 27 backups

Solução:
  Usar tolerância de 95%
  Se backup do dia >= 95% do antigo → PODE DELETAR
  184694 >= 185077 * 0.95 (175823)?
  184694 >= 175823? SIM! ✅ DELETA
"""

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
    """
    try:
        acesso = Acesso.objects.get(id=acesso_id)
        
        if not acesso.backup_habilitado or not acesso.backup_automatico:
            logger.warning(
                f"⚠️ Backup agendado desconsiderado para {acesso.tipo}"
            )
            return
        
        if not acesso.backup_template:
            logger.error(f"❌ Sem template para {acesso.tipo}")
            return
        
        logger.info(f"🔄 Backup agendado: {acesso.tipo} ({acesso.host})")
        
        resultado = realizar_backup(acesso, usuario=None)
        
        if resultado['sucesso']:
            logger.info(
                f"✅ Backup OK: {acesso.tipo} ({resultado['tamanho']} bytes)"
            )
        else:
            logger.error(f"❌ Backup falhou: {resultado['erro']}")
            raise Exception(resultado['erro'])
        
    except Acesso.DoesNotExist:
        logger.error(f"❌ Acesso #{acesso_id} não encontrado")
    except Exception as exc:
        logger.error(f"❌ Erro: {str(exc)}")
        self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task
def agendar_backups_pendentes():
    """✅ Task periódica para agendar backups"""
    logger.info("🔍 Verificando backups agendados...")
    
    acessos_automaticos = Acesso.objects.filter(
        backup_habilitado=True,
        backup_automatico=True,
        backup_template__isnull=False
    ).select_related('cliente', 'backup_template')
    
    total = acessos_automaticos.count()
    logger.info(f"📊 Total: {total}")
    
    agendados = 0
    for acesso in acessos_automaticos:
        try:
            executar_backup_agendado.delay(acesso.id)
            agendados += 1
        except Exception as e:
            logger.error(f"❌ Erro ao agendar {acesso.tipo}: {str(e)}")
    
    logger.info(f"✅ {agendados}/{total} agendados")
    return {
        'total': total,
        'agendados': agendados,
        'timestamp': datetime.now().isoformat()
    }


@shared_task
def limpar_backups_antigos(dias=3):
    """
    ✅ VERSÃO FINAL COM VALIDAÇÃO FLEXÍVEL
    
    Estratégia:
    1. Deletar backups com mais de X dias
    2. MAS manter SEMPRE os 2 últimos
    3. Validação FLEXÍVEL de tamanho (tolerância 95%)
    
    Exemplo:
    - Backup antigo: 185077 bytes
    - Backup do dia: 184694 bytes (383 bytes menor = 0.2%)
    - Tolerância 95%: 185077 * 0.95 = 175823 bytes
    - Validação: 184694 >= 175823? SIM! ✅ DELETA
    """
    from django.utils import timezone
    from datetime import timedelta
    import os
    from django.conf import settings
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🗑️ LIMPEZA DE BACKUPS ANTIGOS")
    logger.info(f"{'='*80}")
    logger.info(f"   📅 Deletando: {dias} dias")
    logger.info(f"   ⭐ Mantendo: 2 últimos por acesso")
    logger.info(f"   🔍 Validação: Tolerância 95% de tamanho")
    logger.info(f"   ⏰ Horário: {timezone.now().strftime('%d/%m/%Y %H:%M:%S')}")
    
    data_limite = timezone.now() - timedelta(days=dias)
    logger.info(f"   Data limite: {data_limite.strftime('%d/%m/%Y %H:%M:%S')}")
    
    # ============================================================================
    # PASSO 1: Buscar backups antigos
    # ============================================================================
    backups_antigos = BackupLog.objects.filter(
        data_backup__lt=data_limite
    ).select_related('acesso', 'cliente').order_by('acesso_id', '-data_backup')
    
    total_encontrados = backups_antigos.count()
    logger.info(f"\n   🔍 Backups antigos: {total_encontrados}")
    
    if total_encontrados == 0:
        logger.info(f"   ✅ Nada a fazer!")
        logger.info(f"{'='*80}\n")
        return {
            'status': 'ok',
            'total': 0,
            'deletados': 0,
            'mantidos': 0,
            'erros': 0,
            'mensagem': 'Nenhum backup antigo'
        }
    
    # ============================================================================
    # PASSO 2: Agrupar por acesso
    # ============================================================================
    backups_por_acesso = {}
    for backup in backups_antigos:
        acesso_id = backup.acesso_id
        if acesso_id not in backups_por_acesso:
            backups_por_acesso[acesso_id] = []
        backups_por_acesso[acesso_id].append(backup)
    
    logger.info(f"   📊 Agrupados por: {len(backups_por_acesso)} acesso(s)")
    
    # ============================================================================
    # PASSO 3: Processar cada acesso
    # ============================================================================
    deletados = 0
    mantidos = 0
    erros = 0
    
    for acesso_id, backups_do_acesso in backups_por_acesso.items():
        try:
            acesso = backups_do_acesso[0].acesso
            cliente = backups_do_acesso[0].cliente
            
            logger.info(f"\n   🖥️ Acesso #{acesso_id}: {acesso.tipo} ({acesso.host})")
            logger.info(f"      📊 Antigos: {len(backups_do_acesso)}")
            
            # ================================================================
            # REGRA: Manter 2 últimos SEMPRE
            # ================================================================
            ultimos_2 = backups_do_acesso[:2]
            para_deletar = backups_do_acesso[2:]
            
            logger.info(f"      ⭐ Mantendo 2 últimos (automático)")
            
            logger.info(f"      🗑️ Para processar: {len(para_deletar)}")
            
            # ================================================================
            # PROCESSAR para deleção
            # ================================================================
            if len(para_deletar) == 0:
                logger.info(f"      ℹ️ Nenhum backup para deletar (todos nos 2 últimos)")
                mantidos += len(ultimos_2)
                continue
            
            for idx, backup in enumerate(para_deletar, 1):
                try:
                    data = backup.data_backup.strftime('%d/%m/%Y %H:%M')
                    tamanho = backup.tamanho_bytes
                    
                    logger.info(f"\n      [{idx}/{len(para_deletar)}] Backup #{backup.id}")
                    logger.info(f"         Data: {data} | Tamanho: {tamanho} bytes")
                    
                    # =========================================================
                    # NOVA VALIDAÇÃO: Verificar backup do dia com TOLERÂNCIA
                    # =========================================================
                    backup_do_dia = BackupLog.objects.filter(
                        acesso=backup.acesso,
                        cliente=backup.cliente,
                        data_backup__gte=data_limite,
                        status='SUCESSO'
                    ).order_by('-data_backup').first()
                    
                    if not backup_do_dia:
                        logger.warning(f"         ⚠️ Sem backup do dia - mantendo")
                        mantidos += 1
                        continue
                    
                    logger.info(f"         ✅ Backup do dia: #{backup_do_dia.id}")
                    logger.info(f"            Tamanho: {backup_do_dia.tamanho_bytes} bytes")
                    
                    # =========================================================
                    # TOLERÂNCIA 95%: Se backup do dia >= 95% do antigo
                    # =========================================================
                    tamanho_minimo = int(tamanho * 0.95)  # 95% do tamanho antigo
                    
                    logger.info(f"         🔍 Validação:")
                    logger.info(f"            Mínimo (95%): {tamanho_minimo} bytes")
                    logger.info(f"            Do dia: {backup_do_dia.tamanho_bytes} bytes")
                    
                    if backup_do_dia.tamanho_bytes >= tamanho_minimo:
                        logger.info(f"         ✅ Validação PASSOU ({backup_do_dia.tamanho_bytes} >= {tamanho_minimo})")
                        
                        # =====================================================
                        # DELETAR!
                        # =====================================================
                        logger.info(f"         🗑️ Deletando...")
                        
                        # Arquivo
                        if backup.arquivo_path:
                            arquivo_path = os.path.join(settings.MEDIA_ROOT, backup.arquivo_path)
                            if os.path.exists(arquivo_path):
                                try:
                                    os.remove(arquivo_path)
                                    logger.info(f"            ✅ Arquivo deletado")
                                except PermissionError:
                                    logger.error(f"            ❌ Sem permissão")
                                    raise
                        
                        # Banco
                        backup.delete()
                        logger.info(f"            ✅ Registro deletado")
                        
                        deletados += 1
                    else:
                        diferenca = backup_do_dia.tamanho_bytes - tamanho_minimo
                        logger.warning(
                            f"         ❌ Validação FALHOU ({backup_do_dia.tamanho_bytes} < {tamanho_minimo})"
                        )
                        logger.info(f"         ⚠️ Faltam {abs(diferenca)} bytes - mantendo")
                        mantidos += 1
                    
                except Exception as e:
                    logger.error(f"         ❌ Erro: {str(e)}")
                    erros += 1
                    continue
            
            # Contar mantidos dos 2 últimos
            mantidos += len(ultimos_2)
            
        except Exception as e:
            logger.error(f"   ❌ ERRO: {str(e)}", exc_info=True)
            erros += 1
            continue
    
    # ============================================================================
    # RESUMO FINAL
    # ============================================================================
    logger.info(f"\n{'='*80}")
    logger.info(f"✅ LIMPEZA CONCLUÍDA!")
    logger.info(f"   📊 Encontrados: {total_encontrados}")
    logger.info(f"   🗑️ Deletados: {deletados}")
    logger.info(f"   ⭐ Mantidos: {mantidos}")
    logger.info(f"   ❌ Erros: {erros}")
    logger.info(f"{'='*80}\n")
    
    return {
        'status': 'ok',
        'total': total_encontrados,
        'deletados': deletados,
        'mantidos': mantidos,
        'erros': erros,
        'mensagem': f'{deletados} deletados, {mantidos} mantidos'
    }