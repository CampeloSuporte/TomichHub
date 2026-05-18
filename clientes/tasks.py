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
    Limpeza de backups antigos com suporte a hash-based deduplication.

    Estratégia:
    - Registros SEM_MUDANCAS / ERRO mais antigos que X dias: deletar diretamente
      (não têm arquivo em disco, apenas ocupam espaço no banco)
    - Registros SUCESSO mais antigos que X dias: manter sempre os 2 últimos
      SUCESSO por acesso (com arquivo real); deletar os demais se existir
      um SUCESSO mais recente (prova de que a config está coberta)
    """

    logger.info(f"\n{'='*80}")
    logger.info(f"LIMPEZA DE BACKUPS ANTIGOS")
    logger.info(f"{'='*80}")
    logger.info(f"   Deletando registros com mais de: {dias} dias")
    logger.info(f"   Mantendo: 2 ultimos SUCESSO por acesso")
    logger.info(f"   Horario: {timezone.now().strftime('%d/%m/%Y %H:%M:%S')}")

    data_limite = timezone.now() - timedelta(days=dias)
    deletados = 0
    mantidos = 0
    erros = 0

    # ── Passo 1: Apagar SEM_MUDANCAS e ERRO antigos (sem arquivo em disco) ──
    sem_arquivo = BackupLog.objects.filter(
        data_backup__lt=data_limite,
        status__in=['SEM_MUDANCAS', 'ERRO'],
    )
    count_sem_arquivo = sem_arquivo.count()
    sem_arquivo.delete()
    deletados += count_sem_arquivo
    logger.info(f"   Registros SEM_MUDANCAS/ERRO antigos removidos: {count_sem_arquivo}")

    # ── Passo 2: Processar SUCESSO antigos por acesso ────────────────────────
    antigos_sucesso = (
        BackupLog.objects
        .filter(data_backup__lt=data_limite, status='SUCESSO')
        .select_related('acesso', 'cliente')
        .order_by('acesso_id', '-data_backup')
    )

    total_sucesso = antigos_sucesso.count()
    logger.info(f"   Backups SUCESSO antigos encontrados: {total_sucesso}")

    if total_sucesso == 0:
        logger.info(f"   Nada mais a fazer.")
        logger.info(f"{'='*80}\n")
        return {
            'status': 'ok',
            'total': count_sem_arquivo,
            'deletados': deletados,
            'mantidos': 0,
            'erros': 0,
            'mensagem': f'{deletados} deletados',
        }

    # Agrupar por acesso — considera apenas SUCESSO (com arquivo real)
    por_acesso = {}
    for b in antigos_sucesso:
        por_acesso.setdefault(b.acesso_id, []).append(b)

    for acesso_id, lista in por_acesso.items():
        try:
            acesso = lista[0].acesso
            logger.info(f"\n   Acesso #{acesso_id}: {acesso.tipo} ({acesso.host})")
            logger.info(f"      SUCESSO antigos: {len(lista)}")

            # Sempre proteger os 2 últimos SUCESSO com arquivo real
            ultimos_2 = [b for b in lista if b.arquivo_path][:2]
            ids_protegidos = {b.id for b in ultimos_2}
            para_avaliar = [b for b in lista if b.id not in ids_protegidos]

            logger.info(f"      Protegidos (2 ultimos): {len(ultimos_2)}")
            logger.info(f"      Para avaliar: {len(para_avaliar)}")

            mantidos += len(ultimos_2)

            if not para_avaliar:
                continue

            # Verificar se existe SUCESSO mais recente que a data_limite
            tem_recente = BackupLog.objects.filter(
                acesso_id=acesso_id,
                data_backup__gte=data_limite,
                status='SUCESSO',
            ).exists()

            if not tem_recente:
                logger.info(f"      Sem SUCESSO recente — mantendo todos")
                mantidos += len(para_avaliar)
                continue

            # Existe backup recente: pode deletar os antigos excedentes
            for b in para_avaliar:
                try:
                    if b.arquivo_path:
                        path = os.path.join(settings.MEDIA_ROOT, b.arquivo_path)
                        if os.path.exists(path):
                            os.remove(path)
                            logger.info(f"      Arquivo removido: {b.arquivo_path}")
                    b.delete()
                    deletados += 1
                except Exception as e:
                    logger.error(f"      Erro ao deletar backup #{b.id}: {e}")
                    erros += 1

        except Exception as e:
            logger.error(f"   ERRO no acesso #{acesso_id}: {e}", exc_info=True)
            erros += 1

    logger.info(f"\n{'='*80}")
    logger.info(f"LIMPEZA CONCLUIDA")
    logger.info(f"   Deletados: {deletados} | Mantidos: {mantidos} | Erros: {erros}")
    logger.info(f"{'='*80}\n")

    return {
        'status': 'ok',
        'deletados': deletados,
        'mantidos': mantidos,
        'erros': erros,
        'mensagem': f'{deletados} deletados, {mantidos} mantidos',
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
