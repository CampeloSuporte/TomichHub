# clientes/tasks.py - VERSÃO CORRIGIDA
"""
✅ VERSÃO CORRIGIDA COM AMBAS AS FUNÇÕES CORRETAS

- limpar_backups_antigos() - completa e isolada
- validar_blocos_rpki_irr_agendado() - completa e isolada
"""

from celery import shared_task
from celery.utils.log import get_task_logger
from django.contrib.auth.models import User
from .models import Acesso, BackupLog, BackupTemplate, BlocoIP, ValidacaoRPKI_IRR_Log
from .views import realizar_backup, executar_validacao_rpki_irr
from datetime import datetime
import traceback
import os
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from django.core.cache import cache

logger = get_task_logger(__name__)


@shared_task(bind=True, max_retries=2)  # ✅ Reduzido de 3 para 2
def executar_backup_agendado(self, acesso_id):
    """
    ✅ Task Celery para executar backup automaticamente
    - Com proteção contra execução duplicada
    - Com retry limitado
    - Com limpeza de cache ao final
    """
    task_key = f'backup_task_{acesso_id}'
    
    try:
        acesso = Acesso.objects.get(id=acesso_id)
        
        if not acesso.backup_habilitado or not acesso.backup_automatico:
            logger.warning(
                f"⚠️ Backup agendado foi desabilitado para {acesso.tipo}"
            )
            cache.delete(task_key)
            return {
                'status': 'ignorado',
                'motivo': 'Backup desabilitado'
            }
        
        if not acesso.backup_template:
            logger.error(f"❌ Nenhum template configurado para {acesso.tipo}")
            cache.delete(task_key)
            return {
                'status': 'erro',
                'erro': 'Template não configurado'
            }
        
        logger.info(f"🔄 Executando backup agendado: {acesso.tipo} ({acesso.host})")
        
        # ✅ Executar backup
        resultado = realizar_backup(acesso, usuario=None)
        
        if resultado['sucesso']:
            logger.info(
                f"✅ Backup bem-sucedido: {acesso.tipo} | "
                f"Tamanho: {resultado['tamanho']} bytes | "
                f"Duração: {resultado['duracao']}"
            )
            cache.delete(task_key)  # ✅ Liberar task key
            
            return {
                'status': 'sucesso',
                'acesso_id': acesso_id,
                'tamanho': resultado['tamanho'],
                'duracao': resultado['duracao']
            }
        else:
            logger.error(f"❌ Backup falhou: {resultado['erro']}")
            cache.delete(task_key)
            raise Exception(resultado['erro'])
        
    except Acesso.DoesNotExist:
        logger.error(f"❌ Acesso #{acesso_id} não encontrado no banco")
        cache.delete(task_key)
        return {
            'status': 'erro',
            'erro': f'Acesso #{acesso_id} não encontrado'
        }
        
    except Exception as exc:
        logger.error(f"❌ Erro ao executar backup: {str(exc)}")
        cache.delete(task_key)
        
        # ✅ Retry apenas se não atingiu limite
        if self.request.retries < self.max_retries:
            retry_count = self.request.retries + 1
            countdown_secs = 120  # 2 minutos
            logger.info(
                f"🔄 Agendando retry {retry_count}/{self.max_retries} "
                f"em {countdown_secs}s para acesso #{acesso_id}"
            )
            self.retry(exc=exc, countdown=countdown_secs)
        else:
            logger.error(
                f"❌ Máximo de retries ({self.max_retries}) atingido "
                f"para acesso #{acesso_id} ({acesso_id})"
            )
            return {
                'status': 'erro_final',
                'acesso_id': acesso_id,
                'erro': f'Falha após {self.max_retries} tentativas'
            }

@shared_task
def agendar_backups_pendentes():
    """
    ✅ VERSÃO SEM CELERY - Executa backups DIRETAMENTE (síncrono)
    """
    
    logger.info("\n" + "="*80)
    logger.info("🔄 AGENDADOR DE BACKUPS")
    logger.info("="*80)
    
    try:
        # ✅ BUSCAR ACESSOS COM BACKUP AUTOMÁTICO
        acessos = Acesso.objects.filter(
            backup_habilitado=True,
            backup_automatico=True,
            backup_template__isnull=False
        ).select_related('cliente', 'backup_template')
        
        total = acessos.count()
        logger.info(f"📊 Total de acessos: {total}\n")
        
        if total == 0:
            return {'status': 'ok', 'total': 0, 'agendados': 0}
        
        agendados = 0
        erros = 0
        
        # ✅ EXECUTAR BACKUPS DIRETAMENTE (SEM CELERY)
        for idx, acesso in enumerate(acessos, 1):
            try:
                logger.info(f"[{idx}/{total}] {acesso.tipo} ({acesso.host})")
                
                if not acesso.backup_template:
                    logger.error(f"    ❌ Sem template")
                    erros += 1
                    continue
                
                # ✅ EXECUTAR BACKUP DIRETO
                resultado = realizar_backup(acesso, usuario=None)
                
                if resultado['sucesso']:
                    logger.info(f"    ✅ SUCESSO")
                    agendados += 1
                else:
                    logger.error(f"    ❌ {resultado['erro']}")
                    erros += 1
            
            except Exception as e:
                logger.error(f"    ❌ ERRO: {str(e)}")
                erros += 1
        
        logger.info(f"\n✅ Executados: {agendados}, Erros: {erros}\n")
        
        return {
            'status': 'ok',
            'total': total,
            'agendados': agendados,
            'erros': erros
        }
    
    except Exception as e:
        logger.error(f"❌ ERRO CRÍTICO: {str(e)}")
        return {'status': 'erro', 'motivo': str(e)}




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


@shared_task
def validar_blocos_rpki_irr_agendado():
    """
    ✅ Task Celery para validar RPKI/IRR diariamente às 4h da manhã
    
    Processa todos os BlocoIP cadastrados e atualiza:
    - rpki_valido, rpki_status, rpki_mensagem
    - irr_valido, irr_status, irr_mensagem
    - ultima_validacao
    """
    
    logger.info("\n" + "="*80)
    logger.info("🌐 VALIDAÇÃO RPKI/IRR AGENDADA")
    logger.info("="*80)
    logger.info(f"⏰ Horário: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    
    # Buscar todos os blocos
    blocos = BlocoIP.objects.all().select_related('cliente').order_by('cliente_id', 'bloco')
    total_blocos = blocos.count()
    
    logger.info(f"📊 Total de blocos: {total_blocos}\n")
    
    sucesso = 0
    erro = 0
    rpki_validos = 0
    rpki_invalidos = 0
    irr_validos = 0
    irr_invalidos = 0
    
    # Processar cada bloco
    for idx, bloco in enumerate(blocos, 1):
        try:
            cliente_nome = bloco.cliente.nome_empresa if bloco.cliente else "SEM_CLIENTE"
            logger.info(f"[{idx}/{total_blocos}] Cliente {cliente_nome} | {bloco.bloco}")
            logger.info("-" * 60)
            
            # Executar validação
            resultado = executar_validacao_rpki_irr(bloco)
            
            # ✅ ATUALIZAR ultima_validacao
            bloco.ultima_validacao = datetime.now()
            bloco.save()
            
            logger.info("   ✅ Validação concluída")
            
            # Contar resultados RPKI
            if bloco.rpki_valido is True:
                rpki_validos += 1
                logger.info(f"   🟢 RPKI: Valid")
            else:
                rpki_invalidos += 1
                logger.info(f"   🔴 RPKI: Invalid/Unknown")
            
            # Contar resultados IRR
            if bloco.irr_valido is True:
                irr_validos += 1
                logger.info(f"   🟢 IRR: Found")
            else:
                irr_invalidos += 1
                logger.info(f"   🔴 IRR: Not Found/Error")
            
            # Log detalhado
            logger.info(f"   Status: {bloco.rpki_status} / {bloco.irr_status}")
            
            sucesso += 1
            logger.info("")
            
        except Exception as e:
            logger.error(f"   ❌ Erro na validação: {str(e)}")
            logger.error(f"   {traceback.format_exc()}")
            erro += 1
            logger.info("")
            continue
    
    # Resumo final
    logger.info("=" * 80)
    logger.info("✅ VALIDAÇÃO CONCLUÍDA")
    logger.info("=" * 80)
    logger.info(f"📊 Total: {total_blocos}")
    logger.info(f"✅ Sucesso: {sucesso}")
    logger.info(f"❌ Erro: {erro}")
    logger.info(f"🟢 RPKI Válidos: {rpki_validos}")
    logger.info(f"🔴 RPKI Inválidos: {rpki_invalidos}")
    logger.info(f"🟢 IRR Válidos: {irr_validos}")
    logger.info(f"🔴 IRR Inválidos: {irr_invalidos}")
    logger.info(f"⏰ Timestamp: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    logger.info("=" * 80 + "\n")
    
    resultado = {
        'status': 'ok',
        'total': total_blocos,
        'sucesso': sucesso,
        'erro': erro,
        'rpki_validos': rpki_validos,
        'rpki_invalidos': rpki_invalidos,
        'irr_validos': irr_validos,
        'irr_invalidos': irr_invalidos,
        'timestamp': datetime.now().isoformat()
    }
    
    return resultado


@shared_task
def agendar_backups_pendentes_SEM_CELERY():
    """
    ✅ VERSÃO SEM CELERY - Executa backups diretamente via crontab
    - Não usa .delay() (enfileiramento Celery)
    - Executa direto e síncrono
    - Perfeito para crontab
    """
    
    logger.info("\n" + "="*80)
    logger.info("🔄 AGENDADOR DE BACKUPS (SEM CELERY)")
    logger.info("="*80)
    logger.info(f"⏰ Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    
    try:
        # ✅ BUSCAR ACESSOS
        acessos_automaticos = Acesso.objects.filter(
            backup_habilitado=True,
            backup_automatico=True,
            backup_template__isnull=False
        ).select_related('cliente', 'backup_template')
        
        total = acessos_automaticos.count()
        logger.info(f"📊 Total de acessos: {total}\n")
        
        if total == 0:
            logger.warning("⚠️ Nenhum acesso com backup automático")
            return {
                'status': 'ok',
                'total': 0,
                'agendados': 0,
                'erro': 0,
                'mensagem': 'Nenhum acesso com backup automático'
            }
        
        agendados = 0
        erros = 0
        
        # ✅ EXECUTAR BACKUPS DIRETAMENTE (SEM CELERY)
        for idx, acesso in enumerate(acessos_automaticos, 1):
            try:
                cliente_nome = acesso.cliente.nome_empresa if acesso.cliente else "SEM_CLIENTE"
                
                logger.info(f"[{idx}/{total}] {acesso.tipo} ({acesso.host})", end=" ", flush=True)
                logger.info(f"[{idx}/{total}] {acesso.tipo} ({acesso.host}) - {cliente_nome}")
                
                # ✅ VALIDAÇÕES
                if not acesso.backup_habilitado:
                    logger.warning(f"    ⚠️ Desabilitado")
                    continue
                
                if not acesso.backup_template:
                    logger.error(f"    ❌ Sem template")
                    erros += 1
                    continue
                
                # ✅ EXECUTAR BACKUP DIRETAMENTE (SÍNCRONO)
                logger.info(f"    🔄 Iniciando backup...")
                
                resultado = realizar_backup(acesso, usuario=None)
                
                if resultado['sucesso']:
                    logger.info(
                        f"    ✅ SUCESSO - "
                        f"Tamanho: {resultado['tamanho']} bytes, "
                        f"Duração: {resultado['duracao']}"
                    )
                    agendados += 1
                else:
                    logger.error(f"    ❌ FALHA - {resultado['erro']}")
                    erros += 1
                
            except Exception as e:
                logger.error(f"    ❌ EXCEÇÃO: {str(e)}")
                logger.error(f"       {traceback.format_exc()}")
                erros += 1
                continue
        
        # ✅ RESUMO
        logger.info("\n" + "="*80)
        logger.info("✅ CONCLUSÃO")
        logger.info("="*80)
        logger.info(f"  📊 Total: {total}")
        logger.info(f"  ✅ Executados: {agendados}")
        logger.info(f"  ❌ Erros: {erros}")
        logger.info("="*80 + "\n")
        
        return {
            'status': 'ok',
            'total': total,
            'agendados': agendados,
            'erro': erros,
            'timestamp': datetime.now().isoformat()
        }
    
    except Exception as e:
        logger.error(f"\n❌ ERRO CRÍTICO: {str(e)}")
        logger.error(f"{traceback.format_exc()}\n")
        return {
            'status': 'erro',
            'motivo': str(e),
            'traceback': traceback.format_exc()
        }
