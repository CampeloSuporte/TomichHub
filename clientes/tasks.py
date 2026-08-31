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
import re
import shutil
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from django.db.models import Q
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


@shared_task
def analisar_backups_ipam():
    """
    Analisa os backups mais recentes de todos os clientes e documenta
    automaticamente IPs, VLANs e sub-redes no IPAM nativo.
    Executado a cada 3 dias via Celery Beat.
    """
    import ipaddress
    from .models import Cliente, IPAMVlan, IPAMSubRede, IPAMEndereco
    from .ipam_views import (_detect_vendor, _parse_mikrotik, _parse_huawei,
                             _parse_generic, _descricao_e_so_nome_de_loopback)

    total_clientes   = 0
    total_processados = 0
    total_vlans      = 0
    total_subredes   = 0
    total_ips        = 0
    total_atualizados = 0

    for cliente in Cliente.objects.all():
        acessos = Acesso.objects.filter(cliente=cliente)
        for acesso in acessos:
            backup = (
                BackupLog.objects
                .filter(acesso=acesso, status='SUCESSO')
                .exclude(arquivo_path='')
                .order_by('-data_backup')
                .first()
            )
            if not backup:
                continue

            caminho = os.path.join(settings.MEDIA_ROOT, backup.arquivo_path)
            if not os.path.exists(caminho):
                continue

            try:
                with open(caminho, 'r', encoding='utf-8', errors='replace') as fh:
                    content = fh.read()
            except Exception as e:
                logger.warning(f'analisar_backups_ipam: acesso {acesso.id} erro ao ler: {e}')
                continue

            vendor = _detect_vendor(content)
            if vendor == 'mikrotik':
                parsed = _parse_mikrotik(content)
            elif vendor == 'huawei':
                parsed = _parse_huawei(content)
            else:
                parsed = _parse_generic(content)

            if not parsed['ips']:
                continue

            total_processados += 1
            host_label = f"{acesso.tipo} ({acesso.host})" if acesso.tipo else acesso.host

            # Salvar contexto no Acesso para o Agent NOC
            from .ipam_views import _build_contexto_backup
            from django.utils import timezone as _tz
            contexto = _build_contexto_backup(vendor, content)
            if contexto:
                acesso.contexto_backup = contexto
                acesso.contexto_backup_em = _tz.now()
                acesso.save(update_fields=['contexto_backup', 'contexto_backup_em'])
                # Atualiza snapshot de conhecimento do cliente imediatamente
                try:
                    _atualizar_snapshot_cliente(cliente, acesso, contexto)
                except Exception as _e:
                    logger.warning(f'analisar_backups_ipam: snapshot cliente {cliente.id}: {_e}')

            vlan_map = {}
            for v in parsed['vlans']:
                obj, created = IPAMVlan.objects.get_or_create(
                    cliente=cliente, numero=v['numero'],
                    defaults={'nome': v['nome']}
                )
                if created:
                    total_vlans += 1
                vlan_map[v['numero']] = obj

            for ip_cidr, desc, vlan_num in parsed['ips']:
                try:
                    net       = ipaddress.ip_network(ip_cidr, strict=False)
                    ip_host   = ip_cidr.split('/')[0]
                    ipaddress.ip_address(ip_host)
                    cidr_rede = str(net)
                except Exception:
                    continue

                vlan_obj = None
                if vlan_num is not None:
                    if vlan_num in vlan_map:
                        vlan_obj = vlan_map[vlan_num]
                    else:
                        obj, created = IPAMVlan.objects.get_or_create(
                            cliente=cliente, numero=vlan_num,
                            defaults={'nome': f'VLAN {vlan_num}'}
                        )
                        if created:
                            total_vlans += 1
                        vlan_map[vlan_num] = obj
                        vlan_obj = obj

                sub, sub_created = IPAMSubRede.objects.get_or_create(
                    cliente=cliente, rede=cidr_rede,
                    defaults={'vlan': vlan_obj, 'descricao': desc}
                )
                if sub_created:
                    total_subredes += 1
                elif vlan_obj and not sub.vlan:
                    sub.vlan = vlan_obj
                    sub.save(update_fields=['vlan'])

                ip_obj, ip_created = IPAMEndereco.objects.get_or_create(
                    cliente=cliente, ip=ip_host,
                    defaults={
                        'descricao': desc, 'hostname': host_label,
                        'subrede': sub, 'acesso': acesso, 'status': 'ativo',
                    }
                )
                if ip_created:
                    total_ips += 1
                else:
                    changed = False
                    if not ip_obj.hostname and host_label:
                        ip_obj.hostname = host_label; changed = True
                    # Mesma regra da view: sobrescreve descrição vazia ou o
                    # nome cru da interface de loopback deixado por rodada
                    # anterior; descrição escrita por gente fica intacta.
                    if desc and not _descricao_e_so_nome_de_loopback(desc) and (
                        not ip_obj.descricao
                        or _descricao_e_so_nome_de_loopback(ip_obj.descricao)
                    ):
                        ip_obj.descricao = desc; changed = True
                    if not ip_obj.subrede:
                        ip_obj.subrede = sub; changed = True
                    if not ip_obj.acesso:
                        ip_obj.acesso = acesso; changed = True
                    if changed:
                        ip_obj.save()
                        total_atualizados += 1

        total_clientes += 1

    logger.info(
        f'analisar_backups_ipam: {total_clientes} clientes, '
        f'{total_processados} equipamentos processados, '
        f'{total_vlans} VLANs, {total_subredes} sub-redes, '
        f'{total_ips} IPs novos, {total_atualizados} atualizados.'
    )
    return {
        'clientes': total_clientes, 'processados': total_processados,
        'vlans': total_vlans, 'subredes': total_subredes,
        'ips_novos': total_ips, 'ips_atualizados': total_atualizados,
    }


@shared_task
def ipam_scan_subredes_automaticas():
    """
    Ping em lote (via _scan_subrede_hosts) em toda sub-rede com
    scan_automatico=True. Roda a cada 30 min via Celery Beat — cada sub-rede
    é isolada em seu próprio try/except pra uma falha (proxy fora do ar, etc)
    não derrubar o scan das demais.
    """
    from .models import IPAMSubRede
    from .ipam_views import _scan_subrede_hosts

    total, ok, falhas = 0, 0, 0
    for subrede in IPAMSubRede.objects.filter(scan_automatico=True, status='ativo').select_related('cliente'):
        total += 1
        try:
            _scan_subrede_hosts(subrede)
            ok += 1
        except Exception as e:
            falhas += 1
            logger.warning(f'ipam_scan_subredes_automaticas: sub-rede {subrede.id} ({subrede.rede}) falhou: {e}')

    logger.info(f'ipam_scan_subredes_automaticas: {ok}/{total} sub-redes escaneadas, {falhas} falhas.')
    return {'total': total, 'ok': ok, 'falhas': falhas}


# ─────────────────────────────────────────────────────────────────────────────
# Habilitação automática de backup por modelo + protocolo
# ─────────────────────────────────────────────────────────────────────────────


@shared_task(bind=True)
def rotina_backup_completa(self):
    """
    Pipeline completo executado diariamente às 01h:
      1. Detecta modelo dos equipamentos via backup (janela de 3 dias)
      2. Habilita backup_habilitado + backup_automatico + template correto
         para todos os acessos SSH com modelo cadastrado
    """
    logger.info('rotina_backup_completa: iniciando pipeline')

    # Passo 1 — detecção de modelo
    r_modelo = detectar_modelos_via_backup()
    logger.info(
        f'  modelo: verificados={r_modelo["verificados"]} '
        f'atualizados={r_modelo["atualizados"]} sem_match={r_modelo["sem_match"]}'
    )

    # Passo 2 — habilitação de backup
    r_backup = habilitar_backups_automaticos()
    logger.info(
        f'  backup: habilitados={r_backup["habilitados"]} '
        f'corrigidos={r_backup["corrigidos"]} sem_template={r_backup["sem_template"]}'
    )

    return {
        'modelo_atualizados': r_modelo['atualizados'],
        'backup_habilitados': r_backup['habilitados'],
        'templates_corrigidos': r_backup['corrigidos'],
    }

def _selecionar_template(acesso, templates_cache):
    """
    Retorna o BackupTemplate adequado para o acesso com base no fabricante do modelo.

    Regras especiais:
      - ZTE, Parks           → template Cisco (IOS-like CLI)
      - Huawei OLTs (MA5xxx) → template 'backup olt huawei'
      - Huawei demais        → template 'Backup Huawei'
      - Outros               → mapeamento por fabricante, fallback Genérico
    """
    if not acesso.modelo:
        return None

    fab  = (acesso.modelo.fabricante or '').upper().strip()
    nome = (acesso.modelo.nome or '').upper().strip()

    # ── Casos especiais ───────────────────────────────────────────────────────
    # ZTE e Parks usam CLI similar ao Cisco
    if fab in ('ZTE', 'ZTE CORPORATION') or 'PARKS' in fab or 'PARKS' in nome:
        return templates_cache.get('CISCO')

    # Huawei OLT (MA5600, MA5608, MA5800 …) tem template próprio
    if fab == 'HUAWEI' and ('MA5' in nome or 'OLT' in nome):
        return templates_cache.get('HUAWEI_OLT') or templates_cache.get('HUAWEI')

    # ── Mapeamento padrão por fabricante ──────────────────────────────────────
    mapa = {
        'HUAWEI':    'HUAWEI',
        'CISCO':     'CISCO',
        'MIKROTIK':  'MIKROTIK',
        'JUNIPER':   'JUNIPER',
        'DATACOM':   'DATACOM',
        'FIBERHOME': 'FIBERHOME',
        'HILLSTONE': 'HILLSTONE',
        'A10':       'A10',
        'A10 NETWORKS': 'A10',
        'INTELBRAS': 'CISCO',   # CLI compatível
        'VSOL':      'CISCO',   # CLI compatível
        'TP-LINK':   'CISCO',   # CLI compatível
        'RAISECOM':  'CISCO',   # CLI compatível
        'DELL':      'CISCO',   # CLI compatível
        'EXTREME':   'EXTREME',
        'HP':        'HP',
    }
    chave = mapa.get(fab)
    if chave:
        return templates_cache.get(chave)

    return templates_cache.get('GENERICO')


@shared_task(bind=True)
def habilitar_backups_automaticos(self):
    """
    Para cada Acesso que:
      - usa protocolo SSH
      - tem modelo cadastrado
      - ainda não tem backup_habilitado=True

    Habilita backup_habilitado, backup_automatico e seleciona o template
    correto conforme o fabricante do modelo.

    Roda diariamente para capturar novos equipamentos.
    """
    from clientes.models import BackupTemplate

    # ── Carrega templates em memória (evita N queries) ────────────────────────
    templates_cache = {}
    for t in BackupTemplate.objects.filter(ativo=True):
        fab_key = (t.fabricante or '').upper().strip()
        nome_key = (t.nome or '').upper().strip()

        # Chaves primárias por fabricante
        if fab_key not in templates_cache:
            templates_cache[fab_key] = t

        # Chave especial para OLT Huawei
        if 'OLT' in nome_key and 'HUAWEI' in nome_key:
            templates_cache['HUAWEI_OLT'] = t

        # Chaves por nome para fabricantes que usam GENERICO no campo
        if 'FIBERHOME' in nome_key:
            templates_cache['FIBERHOME'] = t
        if 'HILLSTONE' in nome_key:
            templates_cache['HILLSTONE'] = t
        if 'A10' in nome_key:
            templates_cache['A10'] = t
        if 'MIKROTIK' in nome_key:
            templates_cache['MIKROTIK'] = t
        if 'DATACOM' in nome_key:
            templates_cache['DATACOM'] = t

    logger.info(f'Templates disponíveis: {list(templates_cache.keys())}')

    # ── Candidatos: SSH + modelo preenchido + não é VM nem hipervisor ─────────
    candidatos = Acesso.objects.filter(
        protocolo='SSH',
        modelo__isnull=False,
        backup_habilitado=False,
    ).exclude(
        funcao__descricao__icontains='vm'
    ).exclude(
        funcao__descricao__icontains='hipervisor'
    ).exclude(
        funcao__descricao__icontains='hypervisor'
    ).select_related('modelo', 'funcao')

    habilitados = 0
    sem_template = 0

    for acesso in candidatos:
        template = _selecionar_template(acesso, templates_cache)
        if not template:
            sem_template += 1
            logger.debug(
                f'[backup-auto] Sem template para {acesso.tipo} '
                f'(fabricante={acesso.modelo.fabricante})'
            )
            continue

        Acesso.objects.filter(pk=acesso.pk).update(
            backup_habilitado=True,
            backup_automatico=True,
            backup_template=template,
        )
        habilitados += 1
        logger.info(
            f'[backup-auto] {acesso.tipo} ({acesso.host}) '
            f'→ template "{template.nome}" ({acesso.modelo.fabricante})'
        )

    # ── Também atualiza template de quem já tem backup mas template errado/nulo
    sem_template_existentes = Acesso.objects.filter(
        protocolo='SSH',
        modelo__isnull=False,
        backup_habilitado=True,
        backup_template__isnull=True,
    ).exclude(
        funcao__descricao__icontains='vm'
    ).exclude(
        funcao__descricao__icontains='hipervisor'
    ).exclude(
        funcao__descricao__icontains='hypervisor'
    ).select_related('modelo', 'funcao')

    corrigidos = 0
    for acesso in sem_template_existentes:
        template = _selecionar_template(acesso, templates_cache)
        if template:
            Acesso.objects.filter(pk=acesso.pk).update(backup_template=template)
            corrigidos += 1

    # ── Remove backup de VMs e hipervisores que foram marcados incorretamente ──
    indevisos = Acesso.objects.filter(
        backup_habilitado=True,
    ).filter(
        Q(funcao__descricao__icontains='vm')
        | Q(funcao__descricao__icontains='hipervisor')
        | Q(funcao__descricao__icontains='hypervisor')
    )

    removidos = 0
    for acesso in indevisos:
        Acesso.objects.filter(pk=acesso.pk).update(
            backup_habilitado=False,
            backup_automatico=False,
            backup_template=None,
        )
        removidos += 1
        logger.info(
            f'[backup-auto] backup REMOVIDO de {acesso.tipo} ({acesso.host}) '
            f'— função: {acesso.funcao.descricao if acesso.funcao else "?"}'
        )

    logger.info(
        f'habilitar_backups_automaticos: {habilitados} habilitados, '
        f'{corrigidos} templates corrigidos, {sem_template} sem template, '
        f'{removidos} backups removidos (VM/hipervisor).'
    )
    return {
        'habilitados': habilitados,
        'corrigidos': corrigidos,
        'sem_template': sem_template,
        'removidos': removidos,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Auto-detecção de modelo via conteúdo do backup
# ─────────────────────────────────────────────────────────────────────────────

def _extrair_modelo_do_backup(conteudo):
    """
    Tenta extrair o model string do equipamento a partir do conteúdo do backup.
    Suporta RouterOS, Cisco IOS/IOS-XE/IOS-XR, Huawei VRP, ZTE, Datacom DmOS, A10.
    Retorna string com o nome do modelo ou None.
    """
    # ── MikroTik RouterOS ─────────────────────────────────────────────────────
    m = re.search(r'^#\s+model\s*=\s*(.+)$', conteudo, re.MULTILINE)
    if m:
        return m.group(1).strip()

    # ── Cisco IOS-XE: "Model Number        : ASR1001-X" ───────────────────────
    m = re.search(r'Model [Nn]umber\s*[:\-]\s*(\S+)', conteudo)
    if m:
        return m.group(1).strip()

    # ── Cisco IOS-XR: "cisco NCS5501 ()" ou "cisco ASR9006 ()" ──────────────
    m = re.search(r'^cisco\s+([A-Za-z0-9\-]+)\s+\(', conteudo, re.MULTILINE)
    if m:
        return m.group(1).strip()

    # ── Cisco IOS: "Cisco IOS Software ... cisco WS-C3750..." ─────────────────
    m = re.search(r'^cisco\s+(\S+)\s+processor', conteudo, re.MULTILINE | re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # ── Huawei VRP – linha de versão: "Huawei NE9000 ... VRP" ────────────────
    m = re.search(
        r'HUAWEI\s+(NE\w+|CE\w+|MA\d+\w*|ATN\s*\w+|CX\w+|S\d+\w*)',
        conteudo, re.IGNORECASE
    )
    if m:
        return m.group(1).strip().replace(' ', '')

    # ── Huawei – board info "NE40E-X8" ────────────────────────────────────────
    m = re.search(
        r'\b(NE\d+E?-?[A-Z0-9]+|CE\d+[A-Z0-9\-]+|MA\d+[A-Z0-9\-]+|ATN\d+[A-Z0-9\-]+)\b',
        conteudo
    )
    if m:
        return m.group(1).strip()

    # ── ZTE ZXAN – "version ZXAN V2.0" ou "Platform: ZXA10 C650" ────────────
    m = re.search(r'Platform\s*:\s*(ZXA10\s*\w+)', conteudo, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r'(C\d{3,4})\s+Version', conteudo)
    if m:
        return 'ZXA10 ' + m.group(1).strip()

    # ── Datacom DmOS ──────────────────────────────────────────────────────────
    m = re.search(r'DmOS.*?running on\s+(\S+)', conteudo, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # ── A10 Networks ──────────────────────────────────────────────────────────
    m = re.search(r'Thunder\s+([\w\-]+)\s', conteudo, re.IGNORECASE)
    if m:
        return 'A10 Thunder ' + m.group(1).strip()

    return None


def _normalizar(s):
    """Remove espaços, hífens e deixa minúsculo para comparação."""
    return re.sub(r'[\s\-_/]', '', s).lower()


def _match_modelo_equipamento(model_str):
    """
    Tenta encontrar o Modelo_equipamento correspondente a model_str.
    Estratégia:
      1. Coincidência exata (case-insensitive) no campo nome
      2. O model_str normalizado está contido no nome normalizado (ou vice-versa)
         — apenas se a string mais curta tiver >= 5 chars para evitar falso-positivo
    Retorna o objeto Modelo_equipamento ou None.
    """
    from modelo_equipamento.models import Modelo_equipamento

    if not model_str or len(model_str) < 4:
        return None

    # 1. Exato
    qs = Modelo_equipamento.objects.filter(nome__iexact=model_str)
    if qs.exists():
        return qs.first()

    # 2. Containment normalizado
    dev_norm = _normalizar(model_str)
    if len(dev_norm) < 5:
        return None

    melhor = None
    melhor_score = 0
    for obj in Modelo_equipamento.objects.all():
        nome_norm = _normalizar(obj.nome)
        # A string mais curta deve estar contida na mais longa
        if dev_norm in nome_norm:
            score = len(dev_norm)
        elif nome_norm in dev_norm:
            score = len(nome_norm)
        else:
            continue
        if score > melhor_score:
            melhor_score = score
            melhor = obj

    return melhor


@shared_task(bind=True)
def detectar_modelos_via_backup(self):
    """
    Varre os Acessos que têm backup e tenta identificar o modelo do equipamento
    lendo o conteúdo do arquivo de backup mais recente.

    Regras de skip (não processa):
      - modelo_auto_em preenchido E modelo já identificado  → nunca mais verifica
      - modelo_auto_em preenchido há menos de 3 dias       → aguarda próximo ciclo

    Roda a cada 3 dias via Celery Beat.
    """
    MEDIA_ROOT = getattr(settings, 'MEDIA_ROOT', '/opt/crm/media')
    JANELA_DIAS = 3
    agora = timezone.now()
    limite = agora - timedelta(days=JANELA_DIAS)

    total = verificados = atualizados = sem_backup = sem_match = 0

    # Candidatos: acessos com backup habilitado
    acessos = Acesso.objects.filter(backup_habilitado=True).select_related('modelo')

    for acesso in acessos:
        total += 1

        # ── Skip: modelo já encontrado anteriormente ──────────────────────────
        if acesso.modelo_auto_em and acesso.modelo_id:
            continue

        # ── Skip: tentativa recente sem resultado ─────────────────────────────
        if acesso.modelo_auto_em and acesso.modelo_auto_em >= limite:
            continue

        # ── Pega o backup mais recente com arquivo ────────────────────────────
        ultimo = (
            BackupLog.objects
            .filter(acesso=acesso, status='SUCESSO')
            .exclude(arquivo_path='')
            .exclude(arquivo_path__isnull=True)
            .order_by('-data_backup')
            .first()
        )
        if not ultimo:
            sem_backup += 1
            # Marca como tentado mesmo sem backup para não checar toda hora
            Acesso.objects.filter(pk=acesso.pk).update(modelo_auto_em=agora)
            continue

        arquivo = os.path.join(MEDIA_ROOT, ultimo.arquivo_path)
        if not os.path.exists(arquivo):
            sem_backup += 1
            Acesso.objects.filter(pk=acesso.pk).update(modelo_auto_em=agora)
            continue

        # ── Lê e extrai modelo ────────────────────────────────────────────────
        try:
            with open(arquivo, errors='replace') as f:
                conteudo = f.read(8000)   # primeiros 8 KB são suficientes
        except OSError:
            continue

        verificados += 1
        model_str = _extrair_modelo_do_backup(conteudo)
        modelo_obj = _match_modelo_equipamento(model_str) if model_str else None

        if modelo_obj:
            Acesso.objects.filter(pk=acesso.pk).update(
                modelo=modelo_obj,
                modelo_auto_em=agora,
            )
            atualizados += 1
            logger.info(
                f'[modelo-auto] {acesso.tipo} ({acesso.host}) → {modelo_obj.nome}'
            )
        else:
            # Não encontrou: marca como tentado, vai re-tentar no próximo ciclo
            Acesso.objects.filter(pk=acesso.pk).update(modelo_auto_em=agora)
            sem_match += 1
            if model_str:
                logger.debug(
                    f'[modelo-auto] {acesso.tipo}: extraiu "{model_str}" mas sem match no banco'
                )

    logger.info(
        f'detectar_modelos_via_backup: {total} acessos, '
        f'{verificados} verificados, {atualizados} atualizados, '
        f'{sem_backup} sem backup, {sem_match} sem match.'
    )
    return {
        'total': total, 'verificados': verificados,
        'atualizados': atualizados, 'sem_backup': sem_backup,
        'sem_match': sem_match,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Envio periódico de PDF com credenciais de todos os clientes
# ─────────────────────────────────────────────────────────────────────────────

DESTINATARIOS_SENHAS = [
    'campelosuporte.ti@gmail.com',
    'noc@tomich.com.br',
    'danilo@tomich.com.br',
]


def _gerar_pdf_todos_acessos():
    """
    Gera um PDF com os acessos de todos os clientes ativos.
    Retorna bytes do PDF.
    """
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle,
        Paragraph, Spacer, PageBreak,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from io import BytesIO
    from django.utils import timezone as tz
    from clientes.models import Cliente, Acesso

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )

    styles = getSampleStyleSheet()
    cor_header  = colors.HexColor('#0d1829')
    cor_accent  = colors.HexColor('#1d4ed8')
    cor_par     = colors.HexColor('#f1f5f9')

    style_titulo = ParagraphStyle('t', parent=styles['Title'],
        fontSize=14, textColor=cor_header, spaceAfter=3)
    style_sub = ParagraphStyle('s', parent=styles['Normal'],
        fontSize=8, textColor=colors.HexColor('#64748b'), spaceAfter=10)
    style_secao = ParagraphStyle('sec', parent=styles['Normal'],
        fontSize=10, textColor=cor_accent, fontName='Helvetica-Bold',
        spaceBefore=12, spaceAfter=4)
    style_cell = ParagraphStyle('c', parent=styles['Normal'], fontSize=7, leading=9)
    style_mono = ParagraphStyle('m', parent=styles['Normal'],
        fontSize=7, leading=9, fontName='Courier')

    elements = []
    gerado_em = tz.localtime(tz.now()).strftime('%d/%m/%Y %H:%M')

    elements.append(Paragraph('Relatório de Credenciais de Acesso — Todos os Clientes', style_titulo))
    elements.append(Paragraph(f'Gerado em: {gerado_em} &nbsp;|&nbsp; Documento confidencial', style_sub))
    elements.append(Spacer(1, 0.3*cm))

    clientes = Cliente.objects.prefetch_related('acessos__funcao', 'acessos__modelo').order_by('nome_empresa')

    for cliente in clientes:
        acessos = cliente.acessos.select_related('funcao', 'modelo').order_by('tipo')
        if not acessos.exists():
            continue

        elements.append(Paragraph(
            f'{cliente.nome_empresa} &nbsp;&nbsp; CNPJ: {cliente.cnpj or "—"}',
            style_secao
        ))

        header = ['Descrição', 'Host', 'Proto', 'Porta', 'Usuário', 'Senha', 'Senha Root', 'Função']
        data = [header]

        for ac in acessos:
            data.append([
                Paragraph(ac.tipo or '—', style_cell),
                Paragraph(ac.host or '—', style_mono),
                Paragraph(ac.protocolo or '—', style_cell),
                Paragraph(str(ac.porta) if ac.porta else '—', style_cell),
                Paragraph(ac.usuario or '—', style_mono),
                Paragraph(ac.senha or '—', style_mono),
                Paragraph(ac.senha_adm or '—', style_mono),
                Paragraph(ac.funcao.descricao if ac.funcao else '—', style_cell),
            ])

        col_widths = [4.0*cm, 4.0*cm, 1.5*cm, 1.3*cm, 3.2*cm, 3.5*cm, 3.5*cm, 3.0*cm]
        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, 0), cor_accent),
            ('TEXTCOLOR',     (0, 0), (-1, 0), colors.white),
            ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',      (0, 0), (-1, 0), 7),
            ('ALIGN',         (0, 0), (-1, 0), 'CENTER'),
            ('TOPPADDING',    (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING',   (0, 0), (-1, -1), 4),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID',          (0, 0), (-1, -1), 0.3, colors.HexColor('#e2e8f0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, cor_par]),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 0.4*cm))

    doc.build(elements)
    buf.seek(0)
    return buf.read()


@shared_task(bind=True)
def enviar_pdf_credenciais(self):
    """
    Gera PDF com todos os acessos e envia para os destinatários configurados.
    Agendado a cada 2 dias.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase
    from email.mime.text import MIMEText
    from email import encoders
    from django.utils import timezone as tz

    logger.info('enviar_pdf_credenciais: iniciando geração do PDF')

    try:
        pdf_bytes = _gerar_pdf_todos_acessos()
    except Exception as e:
        logger.error(f'enviar_pdf_credenciais: erro ao gerar PDF — {e}')
        raise

    # Configuração SMTP
    from clientes.models import ConfiguracaoSistema
    from django.conf import settings as djsettings

    smtp_cfg   = ConfiguracaoSistema.get()
    smtp_host  = smtp_cfg.smtp_host or getattr(djsettings, 'EMAIL_HOST', '')
    smtp_port  = int(smtp_cfg.smtp_port or getattr(djsettings, 'EMAIL_PORT', 587))
    smtp_user  = smtp_cfg.smtp_user or getattr(djsettings, 'EMAIL_HOST_USER', '')
    smtp_pass  = smtp_cfg.smtp_pass or getattr(djsettings, 'EMAIL_HOST_PASSWORD', '')
    remetente  = smtp_cfg.smtp_from or smtp_user
    use_tls    = smtp_cfg.smtp_use_tls

    if not smtp_host or not smtp_user:
        logger.error('enviar_pdf_credenciais: SMTP não configurado, abortando.')
        return {'erro': 'SMTP não configurado'}

    data_str   = tz.localtime(tz.now()).strftime('%d/%m/%Y')
    nome_arq   = f'credenciais_{tz.localtime(tz.now()).strftime("%Y%m%d")}.pdf'
    assunto    = f'[CRM] Relatório de Credenciais — {data_str}'
    corpo      = (
        f'Segue em anexo o relatório de credenciais de acesso gerado em {data_str}.\n\n'
        'Este e-mail é gerado automaticamente a cada 2 dias pelo sistema CRM.\n'
        'Documento confidencial — não repasse este arquivo.'
    )

    erros = []
    for destinatario in DESTINATARIOS_SENHAS:
        try:
            msg = MIMEMultipart()
            msg['Subject'] = assunto
            msg['From']    = remetente
            msg['To']      = destinatario

            msg.attach(MIMEText(corpo, 'plain', 'utf-8'))

            part = MIMEBase('application', 'pdf')
            part.set_payload(pdf_bytes)
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{nome_arq}"')
            msg.attach(part)

            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                server.ehlo()
                if use_tls:
                    server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(remetente, [destinatario], msg.as_string())

            logger.info(f'enviar_pdf_credenciais: enviado para {destinatario}')
        except Exception as e:
            erros.append(f'{destinatario}: {e}')
            logger.error(f'enviar_pdf_credenciais: falha ao enviar para {destinatario} — {e}')

    return {
        'enviados': len(DESTINATARIOS_SENHAS) - len(erros),
        'erros': erros,
    }


# ─────────────────────────────────────────────────────────────────────────────
# TROCAR SENHAS EM MASSA
# ─────────────────────────────────────────────────────────────────────────────

def _gerar_senha_aleatoria(tamanho=16):
    """Gera senha aleatória segura."""
    import secrets
    import string
    chars = string.ascii_letters + string.digits + '!@#$%&*'
    return ''.join(secrets.choice(chars) for _ in range(tamanho))


def _detectar_vendor(acesso):
    """
    Retorna string identificando o vendor do equipamento.
    Prioridade: função (vm/linux) > fabricante do modelo > conteúdo do backup.
    """
    funcao_desc = (acesso.funcao.descricao or '').lower() if acesso.funcao else ''

    if any(k in funcao_desc for k in ('vm', 'virtual', 'kvm', 'vps', 'linux', 'servidor')):
        return 'linux'

    fab = ''
    if acesso.modelo and acesso.modelo.fabricante:
        fab = acesso.modelo.fabricante.lower()

    if 'mikrotik' in fab:
        return 'mikrotik'
    if 'huawei' in fab:
        return 'huawei'
    if 'cisco' in fab:
        return 'cisco'
    if 'juniper' in fab:
        return 'juniper'
    if 'datacom' in fab:
        return 'datacom'
    if 'zte' in fab or 'parks' in fab:
        return 'cisco'
    if 'intelbras' in fab or 'vsol' in fab or 'tp-link' in fab or 'raisecom' in fab or 'dell' in fab:
        return 'cisco'
    if 'fiberhome' in fab or 'hillstone' in fab or 'a10' in fab:
        return 'cisco'
    if 'extreme' in fab or 'hp' in fab:
        return 'cisco'

    # Tenta ler backup para identificar
    try:
        import glob as _glob
        backup_dir = os.path.join(settings.MEDIA_ROOT, 'backups', str(acesso.cliente_id), str(acesso.id))
        arquivos = sorted(
            _glob.glob(os.path.join(backup_dir, '*.txt')) + _glob.glob(os.path.join(backup_dir, '*.rsc')),
            key=os.path.getmtime, reverse=True,
        )
        if arquivos:
            with open(arquivos[0], 'r', errors='replace') as f:
                conteudo = f.read(4000).lower()
            if 'routeros' in conteudo or 'mikrotik' in conteudo:
                return 'mikrotik'
            if 'huawei' in conteudo or 'vrp' in conteudo:
                return 'huawei'
            if 'cisco ios' in conteudo or 'ios-xe' in conteudo:
                return 'cisco'
            if 'junos' in conteudo or 'juniper' in conteudo:
                return 'juniper'
            if 'linux' in conteudo or 'ubuntu' in conteudo or 'debian' in conteudo:
                return 'linux'
    except Exception:
        pass

    return 'desconhecido'


def _ssh_ler_saida(channel, espera=3.0):
    """Lê output disponível de um canal SSH interativo."""
    import time, select
    data = ''
    deadline = time.time() + espera
    while time.time() < deadline:
        try:
            r, _, _ = select.select([channel], [], [], 0.3)
            if r:
                chunk = channel.recv(32768).decode('utf-8', errors='replace')
                if chunk:
                    data += chunk
                else:
                    break
            elif data:
                break
        except Exception:
            break
    return data


def _criar_usuario_mikrotik(client, usuario, senha):
    """MikroTik: cria usuário via exec_command (sem PTY)."""
    cmd = f'/user add name={usuario} password="{senha}" group=full'
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30, get_pty=False)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    if 'failure' in err.lower() or 'error' in err.lower():
        raise Exception(f'Erro MikroTik: {err}')
    return True


def _remover_usuario_mikrotik(client, usuario):
    """MikroTik: remove usuário via exec_command."""
    cmd = f'/user remove [find name={usuario}]'
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30, get_pty=False)
    err = stderr.read().decode('utf-8', errors='replace').strip()
    if 'failure' in err.lower() or 'error' in err.lower():
        raise Exception(f'Erro MikroTik ao remover: {err}')
    return True


def _criar_usuario_cisco(client, usuario, senha):
    """Cisco IOS/XE (e compatíveis): cria usuário via invoke_shell."""
    import time
    channel = client.invoke_shell(term='vt100', width=200, height=50)
    channel.settimeout(30)
    time.sleep(3)
    _ssh_ler_saida(channel, 3)

    cmds = [
        'configure terminal',
        f'username {usuario} privilege 15 secret {senha}',
        'end',
        'write memory',
        'exit',
    ]
    for cmd in cmds:
        channel.send(cmd + '\n')
        time.sleep(1.5)
        _ssh_ler_saida(channel, 2)

    try:
        channel.close()
    except Exception:
        pass
    return True


def _remover_usuario_cisco(client, usuario):
    """Cisco IOS/XE: remove usuário via invoke_shell."""
    import time
    channel = client.invoke_shell(term='vt100', width=200, height=50)
    channel.settimeout(30)
    time.sleep(3)
    _ssh_ler_saida(channel, 3)

    cmds = [
        'configure terminal',
        f'no username {usuario}',
        'end',
        'write memory',
        'exit',
    ]
    for cmd in cmds:
        channel.send(cmd + '\n')
        time.sleep(1.5)
        _ssh_ler_saida(channel, 2)

    try:
        channel.close()
    except Exception:
        pass
    return True


def _criar_usuario_huawei(client, usuario, senha):
    """Huawei VRP: cria usuário via invoke_shell."""
    import time
    channel = client.invoke_shell(term='vt100', width=200, height=50)
    channel.settimeout(30)
    time.sleep(3)
    _ssh_ler_saida(channel, 3)

    cmds = [
        'system-view',
        'aaa',
        f'local-user {usuario} password irreversible-cipher {senha}',
        f'local-user {usuario} privilege level 15',
        f'local-user {usuario} service-type ssh terminal',
        'quit',
        'quit',
        'save',
        'y',
    ]
    for cmd in cmds:
        channel.send(cmd + '\n')
        time.sleep(1.5)
        _ssh_ler_saida(channel, 2)

    try:
        channel.close()
    except Exception:
        pass
    return True


def _remover_usuario_huawei(client, usuario):
    """Huawei VRP: remove usuário."""
    import time
    channel = client.invoke_shell(term='vt100', width=200, height=50)
    channel.settimeout(30)
    time.sleep(3)
    _ssh_ler_saida(channel, 3)

    cmds = [
        'system-view',
        'aaa',
        f'undo local-user {usuario}',
        'y',
        'quit',
        'quit',
        'save',
        'y',
    ]
    for cmd in cmds:
        channel.send(cmd + '\n')
        time.sleep(1.5)
        _ssh_ler_saida(channel, 2)

    try:
        channel.close()
    except Exception:
        pass
    return True


def _criar_usuario_linux(client, usuario, senha):
    """
    Linux (Debian/Ubuntu): cria usuário com sudo via exec_command.
    Tenta primeiro useradd (universal), depois adduser (Debian/Ubuntu).
    """
    # Usar aspas simples na senha para evitar expansão do shell
    senha_escaped = senha.replace("'", "'\\''")
    cmd = (
        f"useradd -m -s /bin/bash {usuario} 2>&1 || true; "
        f"echo '{usuario}:{senha_escaped}' | chpasswd 2>&1; "
        f"usermod -aG sudo {usuario} 2>&1 || usermod -aG wheel {usuario} 2>&1 || true"
    )
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30, get_pty=False)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    combined = (out + ' ' + err).lower()
    # useradd retorna 9 se usuário já existe — não é erro crítico
    if 'chpasswd' in combined and 'error' in combined:
        raise Exception(f'Erro ao definir senha Linux: {out} {err}')
    return True


def _remover_usuario_linux(client, usuario):
    """Linux: remove usuário e seu diretório home."""
    cmd = f'userdel -r {usuario} 2>&1 || true'
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30, get_pty=False)
    stdout.read()
    return True


def _criar_usuario_datacom(client, usuario, senha):
    """Datacom: sintaxe similar ao Cisco."""
    import time
    channel = client.invoke_shell(term='vt100', width=200, height=50)
    channel.settimeout(30)
    time.sleep(3)
    _ssh_ler_saida(channel, 3)

    cmds = [
        'configure terminal',
        f'username {usuario} privilege 15 password {senha}',
        'end',
        'write',
        'exit',
    ]
    for cmd in cmds:
        channel.send(cmd + '\n')
        time.sleep(1.5)
        _ssh_ler_saida(channel, 2)

    try:
        channel.close()
    except Exception:
        pass
    return True


def _remover_usuario_datacom(client, usuario):
    """Datacom: remove usuário."""
    import time
    channel = client.invoke_shell(term='vt100', width=200, height=50)
    channel.settimeout(30)
    time.sleep(3)
    _ssh_ler_saida(channel, 3)

    cmds = [
        'configure terminal',
        f'no username {usuario}',
        'end',
        'write',
        'exit',
    ]
    for cmd in cmds:
        channel.send(cmd + '\n')
        time.sleep(1.5)
        _ssh_ler_saida(channel, 2)

    try:
        channel.close()
    except Exception:
        pass
    return True


def _conectar_ssh_acesso(acesso):
    """
    Abre conexão Paramiko para o acesso, criando túnel SSH se necessário.
    Retorna (client, ssh_tunnel_or_None).
    """
    import paramiko
    from .views import is_private_ip, vpn_cobre_ip, criar_ssh_tunnel
    from .models import ProxyServer

    host_conexao = acesso.host
    porta_conexao = int(acesso.porta) if acesso.porta else 22
    ssh_tunnel = None

    if is_private_ip(acesso.host):
        proxy = ProxyServer.objects.filter(cliente=acesso.cliente, ativo=True).first()
        if proxy:
            ssh_tunnel = criar_ssh_tunnel(
                {'host': proxy.host, 'porta': proxy.porta,
                 'usuario': proxy.usuario, 'senha': proxy.senha},
                acesso.host, porta_conexao
            )
            host_conexao = ssh_tunnel['local_host']
            porta_conexao = ssh_tunnel['local_port']
            import time
            time.sleep(1)
        elif not vpn_cobre_ip(acesso.cliente, acesso.host):
            raise Exception('IP privado sem proxy SSH ativo nem VPN cobrindo o host.')

    kex_algorithms = [
        'diffie-hellman-group14-sha256',
        'diffie-hellman-group14-sha1',
        'diffie-hellman-group-exchange-sha256',
        'diffie-hellman-group16-sha512',
        'ecdh-sha2-nistp256',
        'ecdh-sha2-nistp384',
        'ecdh-sha2-nistp521',
        'curve25519-sha256',
        'curve25519-sha256@libssh.org',
    ]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host_conexao,
        port=porta_conexao,
        username=acesso.usuario,
        password=acesso.senha,
        timeout=20,
        look_for_keys=False,
        allow_agent=False,
        disabled_algorithms={'kex': []},
    )
    # Reordenar KEX para evitar timeout em equipamentos lentos
    try:
        trans = client.get_transport()
        trans.get_security_options().kex = [
            k for k in kex_algorithms if k in trans.get_security_options().kex
        ] or trans.get_security_options().kex
    except Exception:
        pass

    return client, ssh_tunnel


def _fechar_ssh(client, ssh_tunnel):
    """Fecha client SSH e tunnel se houver."""
    try:
        client.close()
    except Exception:
        pass
    if ssh_tunnel:
        try:
            ssh_tunnel['ssh_client'].close()
        except Exception:
            pass
        try:
            ssh_tunnel['server_socket'].close()
        except Exception:
            pass


@shared_task(bind=True)
def executar_troca_senhas_massa(self, job_id, acesso_ids=None):
    """
    Acessa cada host SSH do cliente via Paramiko e cria um novo usuário
    com a senha definida no job. Atualiza as credenciais no banco.
    """
    import time
    from .models import TrocaSenhaJob, TrocaSenhaAcesso

    try:
        job = TrocaSenhaJob.objects.select_related('cliente').get(pk=job_id)
    except TrocaSenhaJob.DoesNotExist:
        logger.error(f'executar_troca_senhas_massa: job {job_id} não encontrado')
        return

    job.status = 'EM_ANDAMENTO'
    job.save(update_fields=['status'])

    qs = Acesso.objects.filter(protocolo='SSH').select_related('modelo', 'funcao', 'cliente')
    if acesso_ids:
        qs = qs.filter(pk__in=acesso_ids)
    else:
        qs = qs.filter(cliente=job.cliente)
    acessos = list(qs)

    job.total_acessos = len(acessos)
    job.save(update_fields=['total_acessos'])

    sucesso = 0
    erro = 0

    for acesso in acessos:
        item = TrocaSenhaAcesso.objects.create(
            job=job,
            acesso=acesso,
            usuario_antigo=acesso.usuario,
            senha_antiga=acesso.senha,
        )

        t_inicio = time.time()
        client = None
        ssh_tunnel = None

        try:
            vendor = _detectar_vendor(acesso)
            logger.info(f'troca_senhas job={job_id} acesso={acesso.id} vendor={vendor}')

            client, ssh_tunnel = _conectar_ssh_acesso(acesso)

            if vendor == 'mikrotik':
                _criar_usuario_mikrotik(client, job.novo_usuario, job.nova_senha)
            elif vendor == 'huawei':
                _criar_usuario_huawei(client, job.novo_usuario, job.nova_senha)
            elif vendor == 'datacom':
                _criar_usuario_datacom(client, job.novo_usuario, job.nova_senha)
            elif vendor == 'linux':
                _criar_usuario_linux(client, job.novo_usuario, job.nova_senha)
                # Para VMs, tenta também como ubuntu (adduser) — se useradd falhou
            else:
                # cisco (padrão) — cobre ZTE, Parks, Intelbras, etc.
                _criar_usuario_cisco(client, job.novo_usuario, job.nova_senha)

            # Atualiza credenciais no acesso
            Acesso.objects.filter(pk=acesso.pk).update(
                usuario=job.novo_usuario,
                senha=job.nova_senha,
            )

            item.status = 'SUCESSO'
            item.mensagem = f'Vendor: {vendor}. Usuário "{job.novo_usuario}" criado com sucesso.'
            sucesso += 1

        except Exception as exc:
            item.status = 'ERRO'
            item.mensagem = str(exc)
            erro += 1
            logger.error(f'troca_senhas job={job_id} acesso={acesso.id} erro: {exc}')

        finally:
            if client:
                _fechar_ssh(client, ssh_tunnel)
            item.executado_em = timezone.now()
            item.duracao_segundos = round(time.time() - t_inicio, 2)
            item.save(update_fields=['status', 'mensagem', 'executado_em', 'duracao_segundos'])

    job.status = 'CONCLUIDO'
    job.total_sucesso = sucesso
    job.total_erro = erro
    job.concluido_em = timezone.now()
    job.save(update_fields=['status', 'total_sucesso', 'total_erro', 'concluido_em'])

    logger.info(f'troca_senhas job={job_id} concluído: {sucesso} ok, {erro} erros')
    return {'job_id': job_id, 'sucesso': sucesso, 'erro': erro}


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot de conhecimento NOC — artigos automáticos por cliente
# ─────────────────────────────────────────────────────────────────────────────

def _atualizar_snapshot_cliente(cliente, acesso, contexto_backup: str):
    """
    Atualiza (ou cria) imediatamente o artigo [INFRA] do cliente na base de
    conhecimento do Agent NOC usando o contexto_backup recém-gerado.
    Chamado após cada backup bem-sucedido que atualiza o contexto_backup.
    """
    from .backup_parser import parse_backup, formatar_artigo, formatar_artigo_bgp
    from .models import AgentKnowledge
    from django.utils import timezone as _tz

    agora_str  = _tz.localtime(_tz.now()).strftime('%d/%m/%Y %H:%M')
    nome_equip = acesso.tipo or acesso.host or f'acesso-{acesso.id}'

    try:
        parsed = parse_backup(contexto_backup, nome_equip)
    except Exception:
        parsed = {'ips': [], 'bgp': [], 'vlans': [], 'modelo': '', 'as_local': '', 'vendor': 'desconhecido'}

    modelo = parsed.get('modelo', '')
    if not modelo and acesso.modelo:
        modelo = acesso.modelo.nome or ''

    hosts_info = [{
        'nome':     nome_equip,
        'host':     acesso.host or '',
        'porta':    acesso.porta or 22,
        'vendor':   parsed.get('vendor', 'desconhecido'),
        'modelo':   modelo,
        'ips':      parsed.get('ips', []),
        'bgp':      parsed.get('bgp', []),
        'vlans':    parsed.get('vlans', []),
        'ospf':     parsed.get('ospf', []),
        'vsi':      parsed.get('vsi', []),
        'l2vc':     parsed.get('l2vc', []),
        'l2vpn':    parsed.get('l2vpn', []),
        'as_local': parsed.get('as_local', ''),
    }]

    nome_empresa = cliente.nome_empresa or f'Cliente {cliente.id}'

    # Busca artigo INFRA existente para mesclar outros hosts que já estavam lá
    titulo_infra = f'[INFRA] {nome_empresa} — Snapshot Automático'
    existing = AgentKnowledge.objects.filter(cliente=cliente, titulo=titulo_infra).first()
    if existing and existing.conteudo:
        # Mesclagem simples: re-gerar com todos os acessos do cliente
        acessos_cliente = Acesso.objects.filter(cliente=cliente).select_related('modelo', 'funcao')
        todos_hosts = []
        for ac in acessos_cliente:
            if not ac.contexto_backup:
                continue
            ne = ac.tipo or ac.host or f'acesso-{ac.id}'
            try:
                p = parse_backup(ac.contexto_backup, ne)
            except Exception:
                continue
            m = p.get('modelo', '') or (ac.modelo.nome if ac.modelo else '')
            todos_hosts.append({
                'nome': ne, 'host': ac.host or '', 'porta': ac.porta or 22,
                'vendor': p.get('vendor', 'desconhecido'), 'modelo': m,
                'ips': p.get('ips', []), 'bgp': p.get('bgp', []),
                'vlans': p.get('vlans', []), 'ospf': p.get('ospf', []),
                'vsi': p.get('vsi', []), 'l2vc': p.get('l2vc', []),
                'l2vpn': p.get('l2vpn', []),
                'as_local': p.get('as_local', ''),
            })
        if todos_hosts:
            hosts_info = todos_hosts

    conteudo_md = formatar_artigo(nome_empresa, hosts_info, agora_str)
    AgentKnowledge.objects.update_or_create(
        cliente=cliente,
        titulo=titulo_infra,
        defaults={
            'conteudo':   conteudo_md,
            'categoria':  'topologia',
            'fabricante': 'generico',
            'tags':       ['snapshot', 'auto-gerado', 'infraestrutura'],
            'ativo':      True,
        },
    )

    # Artigo BGP dedicado
    bgp_md = formatar_artigo_bgp(nome_empresa, hosts_info, agora_str)
    if bgp_md:
        AgentKnowledge.objects.update_or_create(
            cliente=cliente,
            titulo=f'[BGP] {nome_empresa} — Sessões e Peers',
            defaults={
                'conteudo':   bgp_md,
                'categoria':  'topologia',
                'fabricante': 'generico',
                'tags':       ['bgp', 'peers', 'snapshot', 'auto-gerado'],
                'ativo':      True,
            },
        )


@shared_task
def gerar_snapshots_conhecimento():
    """
    Lê o backup mais recente de cada acesso SSH de cada cliente,
    extrai IPs / BGP / VLANs e grava/atualiza um artigo na base
    de conhecimento do Agent NOC por cliente.
    Gera também artigo BGP dedicado com tabela descrição→IP.
    Executado a cada 4 dias via Celery Beat.
    """
    from .backup_parser import parse_backup, formatar_artigo, formatar_artigo_bgp
    from .models import Cliente, AgentKnowledge
    from django.utils import timezone as _tz

    MEDIA_ROOT = getattr(settings, 'MEDIA_ROOT', '/opt/crm/media')
    agora_str  = _tz.localtime(_tz.now()).strftime('%d/%m/%Y %H:%M')

    total_clientes  = 0
    total_artigos   = 0
    total_equip     = 0

    for cliente in Cliente.objects.all():
        acessos = Acesso.objects.filter(cliente=cliente).select_related('modelo', 'funcao')
        hosts_info = []

        for acesso in acessos:
            backup = (
                BackupLog.objects
                .filter(acesso=acesso, status='SUCESSO')
                .exclude(arquivo_path='')
                .exclude(arquivo_path__isnull=True)
                .order_by('-data_backup')
                .first()
            )
            if not backup:
                continue

            caminho = os.path.join(MEDIA_ROOT, backup.arquivo_path)
            if not os.path.exists(caminho):
                continue

            try:
                with open(caminho, 'r', encoding='utf-8', errors='replace') as fh:
                    conteudo = fh.read()
            except OSError as e:
                logger.warning(f'gerar_snapshots_conhecimento: acesso {acesso.id} leitura: {e}')
                continue

            nome_equip = acesso.tipo or acesso.host or f'acesso-{acesso.id}'
            try:
                parsed = parse_backup(conteudo, nome_equip)
            except Exception as e:
                logger.warning(f'gerar_snapshots_conhecimento: parse acesso {acesso.id}: {e}')
                parsed = {'ips': [], 'bgp': [], 'vlans': [], 'modelo': '', 'as_local': '', 'vendor': 'desconhecido'}

            modelo = parsed.get('modelo', '')
            if not modelo and acesso.modelo:
                modelo = acesso.modelo.nome or ''

            hosts_info.append({
                'nome':     nome_equip,
                'host':     acesso.host or '',
                'porta':    acesso.porta or 22,
                'vendor':   parsed.get('vendor', 'desconhecido'),
                'modelo':   modelo,
                'ips':      parsed.get('ips', []),
                'bgp':      parsed.get('bgp', []),
                'vlans':    parsed.get('vlans', []),
                'ospf':     parsed.get('ospf', []),
                'vsi':      parsed.get('vsi', []),
                'l2vc':     parsed.get('l2vc', []),
                'l2vpn':    parsed.get('l2vpn', []),
                'as_local': parsed.get('as_local', ''),
            })
            total_equip += 1

        if not hosts_info:
            total_clientes += 1
            continue

        nome_empresa = cliente.nome_empresa or f'Cliente {cliente.id}'
        titulo       = f'[INFRA] {nome_empresa} — Snapshot Automático'
        conteudo_md  = formatar_artigo(nome_empresa, hosts_info, agora_str)

        AgentKnowledge.objects.update_or_create(
            cliente=cliente,
            titulo=titulo,
            defaults={
                'conteudo':   conteudo_md,
                'categoria':  'topologia',
                'fabricante': 'generico',
                'tags':       ['snapshot', 'auto-gerado', 'infraestrutura'],
                'ativo':      True,
            },
        )
        total_artigos += 1

        # Artigo BGP dedicado — tabela descrição→IP para o agent
        bgp_md = formatar_artigo_bgp(nome_empresa, hosts_info, agora_str)
        if bgp_md:
            titulo_bgp = f'[BGP] {nome_empresa} — Sessões e Peers'
            AgentKnowledge.objects.update_or_create(
                cliente=cliente,
                titulo=titulo_bgp,
                defaults={
                    'conteudo':   bgp_md,
                    'categoria':  'topologia',
                    'fabricante': 'generico',
                    'tags':       ['bgp', 'peers', 'snapshot', 'auto-gerado'],
                    'ativo':      True,
                },
            )

        total_clientes += 1

    logger.info(
        f'gerar_snapshots_conhecimento: {total_clientes} clientes, '
        f'{total_equip} equipamentos processados, {total_artigos} artigos atualizados.'
    )
    return {
        'clientes':   total_clientes,
        'equipamentos': total_equip,
        'artigos':    total_artigos,
    }


def _atualizar_snapshot_bgp_de_acesso(acesso):
    """
    Faz o trabalho de UM Acesso: lê o backup mais recente, extrai sessões
    BGP/prefix-lists/route-policies (`clientes.backup_parser`), simula quais
    prefixos cada sessão está anunciando (`clientes.bgp_matcher`) e grava/
    atualiza o `BgpSnapshot`. Reaproveitado tanto pela rotina noturna
    (`atualizar_snapshots_bgp`, em loop) quanto pelo botão "Atualizar agora"
    da tela de automação BGP (`clientes.bgp_views.bgp_atualizar_snapshot`,
    para um único host, sob demanda).

    Retorna (resultado: str, detalhe: str) — `resultado` é um dos:
    'sem_backup', 'sem_novidade', 'fabricante_nao_suportado', 'erro_leitura',
    'erro_parser', 'sem_bgp', 'erro_simulacao', 'ok'.
    """
    from .backup_parser import parse_backup
    from .bgp_matcher import identificar_interface, simular_anuncios
    from .models import BgpSnapshot

    MEDIA_ROOT = getattr(settings, 'MEDIA_ROOT', '/opt/crm/media')

    backup = (
        BackupLog.objects
        .filter(acesso=acesso, status='SUCESSO')
        .exclude(arquivo_path='')
        .exclude(arquivo_path__isnull=True)
        .order_by('-data_backup')
        .first()
    )
    if not backup:
        return 'sem_backup', 'Nenhum backup bem-sucedido encontrado para este host.'

    # Se o backup mais recente é o MESMO já usado pra montar o snapshot
    # atual E existe uma atualização otimista pendente (`patch_local_
    # pendente` — ver `bgp_actions.py::aplicar_efeito_localmente`), não
    # reprocessa: reescrever por cima do patch levaria o painel de volta
    # pro estado de ANTES da ação (bug real observado: operador roda
    # "parar de anunciar", o painel atualiza corretamente, mas clicar
    # "Atualizar agora" logo em seguida reverte, porque relê o mesmo
    # backup antigo que ainda não reflete a mudança feita no equipamento).
    #
    # Sem um patch pendente pra proteger, reprocessar o MESMO backup é
    # seguro e desejável — é assim que um snapshot antigo (gerado antes de
    # uma melhoria no parser/matcher, ex: o campo `interface` por sessão)
    # pega a extração/simulação atualizada sem precisar esperar um backup
    # novo do equipamento. Bug real observado: sessões BGP de alguns
    # clientes (ex: G5, Green Telecom) tinham `interface: null` e zero
    # anúncios simulados porque o snapshot deles nunca foi reprocessado
    # desde antes dessas features existirem — "Atualizar agora" sempre
    # devolvia `sem_novidade` porque o backup nunca mudou.
    snap_atual = BgpSnapshot.objects.filter(acesso=acesso).first()
    if (snap_atual and not snap_atual.erro and snap_atual.backup_log_id == backup.id
            and snap_atual.patch_local_pendente):
        return 'sem_novidade', 'Nenhum backup novo deste host desde a última atualização.'

    caminho = os.path.join(MEDIA_ROOT, backup.arquivo_path)
    if not os.path.exists(caminho):
        return 'sem_backup', f'Arquivo de backup não encontrado em disco: {backup.arquivo_path}'

    # Fabricante mais confiável (funcao → modelo → conteúdo do backup), já
    # usado por detectar_modelos_via_backup — evita re-detectar do zero e
    # herda o mesmo fallback de leitura de arquivo.
    vendor = _detectar_vendor(acesso)
    # Datacom não tem parser BGP próprio validado (nenhuma evidência de BGP
    # em backup real de Datacom até agora) — reaproveita a gramática
    # Cisco/IOS, mesmo tratamento já usado em DEVICE_TYPES pro Netmiko.
    vendor_parser = 'cisco' if vendor == 'datacom' else vendor
    if vendor_parser not in ('mikrotik', 'huawei', 'cisco', 'juniper'):
        return 'fabricante_nao_suportado', f'Fabricante "{vendor}" não suportado pela automação BGP.'

    try:
        with open(caminho, 'r', encoding='utf-8', errors='replace') as fh:
            conteudo = fh.read()
    except OSError as e:
        logger.warning(f'_atualizar_snapshot_bgp_de_acesso: acesso {acesso.id} leitura: {e}')
        return 'erro_leitura', str(e)

    nome_equip = acesso.tipo or acesso.host or f'acesso-{acesso.id}'
    try:
        dados = parse_backup(conteudo, nome_equip, vendor_hint=vendor_parser)
    except Exception as e:
        logger.warning(f'_atualizar_snapshot_bgp_de_acesso: parse acesso {acesso.id}: {e}')
        BgpSnapshot.objects.update_or_create(acesso=acesso, defaults={'erro': f'Erro no parser: {e}'})
        return 'erro_parser', str(e)

    if not dados.get('bgp'):
        return 'sem_bgp', 'Nenhuma sessão BGP encontrada neste backup.'

    dados['sessoes'] = dados.pop('bgp')
    try:
        anuncios = {}
        for sessao in dados['sessoes']:
            policy_out = sessao.get('policy_out')
            if policy_out:
                anuncios[sessao['nome']] = simular_anuncios(
                    dados.get('prefix_lists', {}), dados.get('policies', {}), policy_out
                )
            if vendor_parser in ('huawei', 'cisco', 'juniper'):
                # Interface local do peer (pra botão "ver tráfego em tempo
                # real") — huawei, cisco e juniper; peer multihop/via
                # loopback devolve None (sem interface identificável).
                sessao['interface'] = identificar_interface(dados, sessao.get('peer_ip', ''))
        dados['anuncios'] = anuncios

        BgpSnapshot.objects.update_or_create(
            acesso=acesso,
            defaults={
                'vendor': vendor_parser,
                'backup_log': backup,
                'dados': dados,
                'erro': '',
                # Reparse de verdade aconteceu — qualquer patch otimista
                # anterior já foi substituído pelos dados frescos do
                # backup/simulação, não tem mais nada pendente pra proteger.
                'patch_local_pendente': False,
            },
        )
        return 'ok', ''
    except Exception as e:
        logger.warning(f'_atualizar_snapshot_bgp_de_acesso: simulação/gravação acesso {acesso.id}: {e}')
        BgpSnapshot.objects.update_or_create(acesso=acesso, defaults={'erro': f'Erro na simulação: {e}'})
        return 'erro_simulacao', str(e)


@shared_task
def atualizar_snapshots_bgp():
    """
    Roda `_atualizar_snapshot_bgp_de_acesso` pra todo Acesso do CRM.

    Executado diariamente às 02:45, depois da rotina de backup (01h) e do
    snapshot de conhecimento do Agent NOC (02:30). Acessos sem nenhum peer
    BGP no backup são ignorados silenciosamente (não têm `BgpSnapshot`
    criado); erros de parser por Acesso ficam registrados em
    `BgpSnapshot.erro` sem derrubar a rotina nem apagar o snapshot anterior.
    """
    processados, com_bgp, com_erro = 0, 0, 0

    for acesso in Acesso.objects.select_related('modelo', 'funcao', 'cliente').all():
        resultado, _detalhe = _atualizar_snapshot_bgp_de_acesso(acesso)
        if resultado in ('sem_backup', 'fabricante_nao_suportado'):
            continue
        # 'processados' conta todo host onde deu pra ler o backup e o
        # fabricante é suportado, mesmo que não tenha achado BGP nele
        # (mesmo ponto de contagem que a versão original desta task).
        processados += 1
        if resultado == 'ok':
            com_bgp += 1
        elif resultado in ('erro_parser', 'erro_simulacao', 'erro_leitura'):
            com_erro += 1

    logger.info(
        f'atualizar_snapshots_bgp: {processados} acessos com backup, '
        f'{com_bgp} com BGP identificado, {com_erro} com erro.'
    )
    return {'processados': processados, 'com_bgp': com_bgp, 'com_erro': com_erro}


@shared_task(bind=True)
def remover_usuarios_antigos_task(self, job_id):
    """
    Para cada item SUCESSO do job, acessa o host com as NOVAS credenciais
    e remove o usuário antigo.
    """
    import time
    from .models import TrocaSenhaJob, TrocaSenhaAcesso

    try:
        job = TrocaSenhaJob.objects.select_related('cliente').get(pk=job_id)
    except TrocaSenhaJob.DoesNotExist:
        return

    itens = TrocaSenhaAcesso.objects.filter(
        job=job, status='SUCESSO', usuario_removido=False
    ).select_related('acesso__modelo', 'acesso__funcao', 'acesso__cliente')

    for item in itens:
        acesso = item.acesso
        if not acesso:
            continue

        client = None
        ssh_tunnel = None
        t_inicio = time.time()

        try:
            vendor = _detectar_vendor(acesso)

            # Conecta com as NOVAS credenciais (já atualizadas no banco)
            client, ssh_tunnel = _conectar_ssh_acesso(acesso)

            if vendor == 'mikrotik':
                _remover_usuario_mikrotik(client, item.usuario_antigo)
            elif vendor == 'huawei':
                _remover_usuario_huawei(client, item.usuario_antigo)
            elif vendor == 'datacom':
                _remover_usuario_datacom(client, item.usuario_antigo)
            elif vendor == 'linux':
                _remover_usuario_linux(client, item.usuario_antigo)
            else:
                _remover_usuario_cisco(client, item.usuario_antigo)

            item.usuario_removido = True
            item.mensagem += f' | Usuário antigo "{item.usuario_antigo}" removido.'
            item.save(update_fields=['usuario_removido', 'mensagem'])
            logger.info(f'remover_antigos job={job_id} acesso={acesso.id} ok')

        except Exception as exc:
            item.mensagem += f' | Falha ao remover "{item.usuario_antigo}": {exc}'
            item.save(update_fields=['mensagem'])
            logger.error(f'remover_antigos job={job_id} acesso={acesso.id} erro: {exc}')

        finally:
            if client:
                _fechar_ssh(client, ssh_tunnel)

    return {'job_id': job_id, 'processados': itens.count()}


# ─────────────────────────────────────────────────────────────────────────────
# Integração Disparo — envio de WhatsApp (HSM) para novos leads do Hotspot
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def enviar_disparo_hotspot_lead(self, lead_id):
    """
    Envia uma mensagem WhatsApp (HSM) via cada integração de disparo
    habilitada do cliente (Chatmix, Opa Suite) quando um novo lead é
    capturado no Hotspot. Roda em background (Celery) para não atrasar a
    resposta do portal cativo ao usuário conectando no WiFi. Se o cliente
    tiver mais de um provider habilitado ao mesmo tempo, dispara em todos.
    """
    from .models import ClienteIntegracaoDisparo, HotspotLead
    from .services import (
        ChatmixClient, OpaSuiteClient, montar_variaveis_mensagem, normalizar_numero_whatsapp,
    )

    try:
        lead = HotspotLead.objects.select_related('hotspot__cliente').get(id=lead_id)
    except HotspotLead.DoesNotExist:
        return {'status': 'ignorado', 'motivo': 'Lead não encontrado'}

    cliente = lead.hotspot.cliente
    configs = list(ClienteIntegracaoDisparo.objects.filter(cliente=cliente, habilitado=True))
    if not configs:
        return {'status': 'ignorado', 'motivo': 'Nenhuma integração de disparo habilitada'}

    numero = normalizar_numero_whatsapp(lead.telefone)
    if not numero:
        return {'status': 'ignorado', 'motivo': 'Lead sem telefone'}

    resultados = []
    tem_falha_transitoria = False

    for config in configs:
        variaveis = montar_variaveis_mensagem(config.variaveis_modelo, lead)

        if config.provider == 'chatmix':
            if not (config.api_key and config.api_token and config.template_id):
                resultados.append({'provider': 'chatmix', 'status': 'ignorado', 'motivo': 'não configurado'})
                continue
            client = ChatmixClient(config.api_key, config.api_token)
            ok, detalhe = client.enviar_hsm(numero, variaveis, config.template_id)
        elif config.provider == 'opa_suit':
            if not (config.api_dominio and config.api_token and config.canal_id and config.template_id):
                resultados.append({'provider': 'opa_suit', 'status': 'ignorado', 'motivo': 'não configurado'})
                continue
            client = OpaSuiteClient(config.api_dominio, config.api_token)
            ok, detalhe = client.enviar_template(numero, config.canal_id, config.template_id, variaveis)
        else:
            continue

        if not ok:
            logger.warning(f'⚠️ Disparo {config.provider} falhou p/ lead {lead_id}: {detalhe}')
            resultados.append({'provider': config.provider, 'status': 'erro', 'detalhe': detalhe})
            # Erro 4xx (ex: credenciais/template/variáveis incorretas) é config
            # errada, não instabilidade de rede — retry não resolve, só some
            # com as tentativas. Só reenfileira em falhas que podem ser
            # transitórias (rede, 5xx).
            if not detalhe.startswith('HTTP 4'):
                tem_falha_transitoria = True
        else:
            logger.info(f'✅ Disparo {config.provider} enviado p/ lead {lead_id} ({numero})')
            resultados.append({'provider': config.provider, 'status': 'ok', 'detalhe': detalhe})

    if tem_falha_transitoria:
        raise self.retry(exc=Exception(str(resultados)))

    return {'status': 'concluido', 'resultados': resultados}


# ─────────────────────────────────────────────────────────────────────────────
# AmpScan — varredura de portas de amplificação DDoS nos blocos de IP
# cadastrados no RPKI/IRR de cada cliente (BlocoIP). Chama o runner Rust em
# tools/ampscan_runner/ (lib https://github.com/gondimcodes/ampscan) via
# subprocess, trocando JSON por stdin/stdout — sem precisar do banco
# SQLCipher nem da autenticação de usuário do CLI original.
# ─────────────────────────────────────────────────────────────────────────────

import ipaddress
import json as _json
import subprocess

AMPSCAN_RUNNER_BIN = os.path.join(
    settings.BASE_DIR, 'tools', 'ampscan_runner', 'target', 'release', 'crm_ampscan_runner'
)

# Mesmos limites que o binário ampscan aplica (rejeitaria o prefixo se
# excedidos) — filtramos antes de chamar, pra não perder a execução inteira
# por causa de 1 bloco grande demais, e pra poder registrar quantos blocos
# foram ignorados.
_AMPSCAN_MAX_PREFIXLEN_V4 = 16   # até /16 (65536 hosts)
_AMPSCAN_MAX_PREFIXLEN_V6 = 112  # até /112 (65536 hosts)


def _ampscan_prefixos_elegiveis(cliente):
    """Monta a lista de prefixos escaneáveis a partir dos BlocoIP do cliente.

    Retorna (prefixos_para_runner, blocos_por_id, total_ignorados) — blocos
    maiores que o limite do ampscan (ex: um /48 IPv6 inteiro, impossível de
    varrer host a host) são ignorados, não travam a execução dos demais.
    """
    from .models import BlocoIP

    prefixos = []
    blocos_por_id = {}
    ignorados = 0

    for bloco in BlocoIP.objects.filter(cliente=cliente):
        try:
            net = ipaddress.ip_network(bloco.bloco, strict=False)
        except ValueError:
            ignorados += 1
            continue

        limite = _AMPSCAN_MAX_PREFIXLEN_V4 if net.version == 4 else _AMPSCAN_MAX_PREFIXLEN_V6
        if net.prefixlen < limite:
            ignorados += 1
            continue

        prefixos.append({
            'id': bloco.id,
            'prefix': str(net),
            'description': f'{cliente.nome_empresa} - {bloco.bloco}',
        })
        blocos_por_id[bloco.id] = (bloco, net)

    return prefixos, blocos_por_id, ignorados


def _ampscan_localizar_bloco(ip_str, blocos_por_id):
    """Descobre de qual BlocoIP um IP resultado do scan veio, por contenção
    de CIDR (o runner não devolve isso — o ScanReport upstream só guarda os
    prefixos como strings, não por IP)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return None
    for bloco, net in blocos_por_id.values():
        if ip in net:
            return bloco
    return None


# Portas de gestão remota — abertas não são "vulnerabilidade" no mesmo sentido
# que amplificação DDoS/proxy comprometido (muito cliente expõe de propósito
# pra administração), mas merecem visibilidade separada na aba.
_AMPSCAN_PORTAS_EXPOSICAO = {(22, 'tcp'), (3389, 'tcp')}


def _ampscan_status_para_porta(status_raw, porta, protocolo):
    """Traduz o status bruto do runner ('Open'/'OpenProtected'/outro) pro
    status persistido em AmpScanResultado, separando portas de gestão remota
    (SSH/RDP) da categoria 'vulneravel'. Retorna None para status que não
    deve ser persistido (Closed/Inconclusive/Error)."""
    if status_raw == 'Open':
        if (porta, protocolo) in _AMPSCAN_PORTAS_EXPOSICAO:
            return 'exposto'
        return 'vulneravel'
    if status_raw == 'OpenProtected':
        return 'protegido'
    return None


def _ampscan_executar_para_cliente(cliente, concurrency=200, timeout=2, retries=1, subprocess_timeout=900):
    """Roda a varredura de amplificação para um cliente e persiste o
    resultado. Isolado em try/except pelo chamador — uma falha aqui (runner
    ausente, timeout, JSON inválido) não deve derrubar a varredura dos
    demais clientes."""
    from .models import AmpScanExecucaoLog, AmpScanResultado

    execucao = AmpScanExecucaoLog.objects.create(cliente=cliente)

    prefixos, blocos_por_id, ignorados = _ampscan_prefixos_elegiveis(cliente)
    execucao.blocos_ignorados = ignorados

    if not prefixos:
        execucao.finalizado_em = timezone.now()
        execucao.save()
        return execucao

    if not os.path.isfile(AMPSCAN_RUNNER_BIN):
        execucao.sucesso = False
        execucao.erro_mensagem = f'Runner não encontrado em {AMPSCAN_RUNNER_BIN} (rode cargo build --release).'
        execucao.finalizado_em = timezone.now()
        execucao.save()
        return execucao

    entrada = _json.dumps({
        'prefixes': prefixos,
        'concurrency': concurrency,
        'timeout': timeout,
        'retries': retries,
    })

    try:
        proc = subprocess.run(
            [AMPSCAN_RUNNER_BIN],
            input=entrada,
            capture_output=True,
            text=True,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired:
        execucao.sucesso = False
        execucao.erro_mensagem = f'Runner excedeu o timeout de {subprocess_timeout}s.'
        execucao.finalizado_em = timezone.now()
        execucao.save()
        return execucao

    if proc.returncode != 0:
        execucao.sucesso = False
        execucao.erro_mensagem = (proc.stderr or 'runner retornou código != 0')[-2000:]
        execucao.finalizado_em = timezone.now()
        execucao.save()
        return execucao

    try:
        report = _json.loads(proc.stdout)
    except _json.JSONDecodeError as e:
        execucao.sucesso = False
        execucao.erro_mensagem = f'Saída do runner não é JSON válido: {e}'
        execucao.finalizado_em = timezone.now()
        execucao.save()
        return execucao

    chaves_vistas = set()
    total_vulneraveis = 0
    total_protegidos = 0
    total_expostos = 0

    for r in report.get('results', []):
        status_raw = r.get('status')
        porta = r['port']
        protocolo = r['protocol']
        status = _ampscan_status_para_porta(status_raw, porta, protocolo)
        if status is None:
            continue  # Closed / Inconclusive / Error — não interessa persistir
        if status == 'vulneravel':
            total_vulneraveis += 1
        elif status == 'protegido':
            total_protegidos += 1
        elif status == 'exposto':
            total_expostos += 1

        ip = r['ip']
        chaves_vistas.add((ip, porta, protocolo))
        bloco = _ampscan_localizar_bloco(ip, blocos_por_id)

        AmpScanResultado.objects.update_or_create(
            cliente=cliente, ip=ip, porta=porta, protocolo=protocolo,
            defaults={
                'bloco_ip': bloco,
                'servico': r.get('service_name', ''),
                'descricao_risco': r.get('description', ''),
                'status': status,
                'tempo_resposta_ms': r.get('response_time_ms'),
                'resolvido': False,
                'resolvido_em': None,
            },
        )

    # Achados anteriores nos blocos escaneados desta vez que não apareceram
    # de novo (porta fechou, host saiu do ar) — marca como resolvido em vez
    # de apagar, mantendo o histórico de quando o risco existiu.
    abertos_anteriores = AmpScanResultado.objects.filter(
        cliente=cliente, bloco_ip_id__in=blocos_por_id.keys(), resolvido=False,
    )
    for resultado in abertos_anteriores:
        if (resultado.ip, resultado.porta, resultado.protocolo) not in chaves_vistas:
            resultado.resolvido = True
            resultado.resolvido_em = timezone.now()
            resultado.save(update_fields=['resolvido', 'resolvido_em'])

    execucao.total_ips = report.get('total_ips', 0)
    execucao.total_probes = report.get('total_probes', 0)
    execucao.total_vulneraveis = total_vulneraveis
    execucao.total_protegidos = total_protegidos
    execucao.total_expostos = total_expostos
    execucao.finalizado_em = timezone.now()
    execucao.save()
    return execucao


@shared_task
def ampscan_escanear_cliente(cliente_id):
    """Varredura de amplificação sob demanda (botão 'Escanear Agora' na aba
    Vulnerabilidades) para um único cliente."""
    from .models import Cliente

    try:
        cliente = Cliente.objects.get(id=cliente_id)
    except Cliente.DoesNotExist:
        return {'status': 'ignorado', 'motivo': 'Cliente não encontrado'}

    execucao = _ampscan_executar_para_cliente(cliente)
    return {
        'status': 'ok' if execucao.sucesso else 'erro',
        'execucao_id': execucao.id,
        'total_vulneraveis': execucao.total_vulneraveis,
    }


# Quantos grupos rotativos de clientes existem. A cada execução (a cada 2
# dias, ver crm/celery.py) só 1 grupo é escaneado — cobertura completa de
# todos os clientes leva AMPSCAN_TOTAL_GRUPOS * 2 dias. O grupo do dia é
# calculado a partir da data corrente (não de um contador salvo em algum
# lugar), então é determinístico e sobrevive a reinício do Celery sem
# repetir/pular grupo por causa de drift do agendador.
AMPSCAN_TOTAL_GRUPOS = 3
_AMPSCAN_EPOCH = datetime(2026, 1, 1).date()


def _ampscan_grupo_do_dia(total_grupos=AMPSCAN_TOTAL_GRUPOS):
    dias_desde_epoch = (timezone.localdate() - _AMPSCAN_EPOCH).days
    ciclo = dias_desde_epoch // 2  # cada ciclo (1 grupo) dura 2 dias
    return ciclo % total_grupos


@shared_task
def ampscan_varrer_clientes_agendado():
    """Varredura de amplificação a cada 2 dias (Celery Beat) — só escaneia
    1/AMPSCAN_TOTAL_GRUPOS dos clientes por execução (dividido por
    `cliente.id % AMPSCAN_TOTAL_GRUPOS`), pra não disparar sondas contra
    todos os clientes no mesmo dia. Isolado em try/except por cliente (mesmo
    padrão de ipam_scan_subredes_automaticas: 1 cliente com problema não
    derruba a varredura dos demais)."""
    from .models import Cliente

    grupo_hoje = _ampscan_grupo_do_dia()
    clientes = Cliente.objects.filter(blocos_ip__isnull=False).distinct().order_by('id')
    clientes = [c for c in clientes if c.id % AMPSCAN_TOTAL_GRUPOS == grupo_hoje]
    total, ok, falhas = 0, 0, 0

    logger.info(f'ampscan_varrer_clientes_agendado: grupo {grupo_hoje}/{AMPSCAN_TOTAL_GRUPOS - 1} — {len(clientes)} cliente(s) nesta execução.')

    for cliente in clientes:
        total += 1
        try:
            execucao = _ampscan_executar_para_cliente(cliente)
            if execucao.sucesso:
                ok += 1
            else:
                falhas += 1
                logger.warning(f'ampscan_varrer_clientes_agendado: cliente {cliente.id} falhou: {execucao.erro_mensagem}')
        except Exception as e:
            falhas += 1
            logger.warning(f'ampscan_varrer_clientes_agendado: cliente {cliente.id} exceção: {e}')

    logger.info(f'ampscan_varrer_clientes_agendado: {ok}/{total} clientes escaneados, {falhas} falhas.')
    return {'total': total, 'ok': ok, 'falhas': falhas, 'grupo': grupo_hoje, 'total_grupos': AMPSCAN_TOTAL_GRUPOS}


# ─────────────────────────────────────────────────────────────────────────────
# ROTALOOP — DETECÇÃO DE LOOP DE ROTEAMENTO POR BLOCO IP
# ─────────────────────────────────────────────────────────────────────────────


def _rotaloop_ip_alvo(net):
    """IP usado como alvo do teste de loop pra um bloco: o primeiro IP útil
    (network_address + 1). Blocos minúsculos (/31, /32 IPv4; /127, /128 IPv6)
    não têm um '+1' distinto do endereço de rede, então usam o próprio
    network_address."""
    if net.num_addresses <= 2:
        return str(net.network_address)
    return str(net.network_address + 1)


def _rotaloop_detectar_loop(hops):
    """Recebe a lista de hops [{'hop': int, 'ip': str|None}, ...] (ordenada
    por hop) e detecta loop de roteamento: o mesmo IP aparecendo em 2+
    posições, em qualquer lugar do caminho (não precisa ser consecutivo).
    Hops sem resposta (ip=None) são ignorados na contagem. Retorna
    (status, ip_em_loop) — status é 'normal' ou 'loop_detectado'."""
    vistos = set()
    for h in hops:
        ip = h.get('ip')
        if ip is None:
            continue
        if ip in vistos:
            return 'loop_detectado', ip
        vistos.add(ip)
    return 'normal', None


def _rotaloop_mtr_json(host, count=3, timeout=30):
    """Roda `mtr --report --json --no-dns -c <count> <host>` e devolve a
    lista de hops [{'hop': int, 'ip': str|None}, ...] ordenada. Levanta
    RuntimeError (não deixa exceção de subprocess/parsing vazar) se mtr não
    estiver instalado, o comando expirar, ou a saída não vier no formato
    esperado — quem chama trata isso como status='inconclusivo'."""
    if not shutil.which('mtr'):
        raise RuntimeError('mtr não está instalado no servidor.')

    cmd = ['mtr', '--report', '--json', '--no-dns', '-c', str(count), host]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'mtr excedeu o timeout de {timeout}s para {host}.')

    try:
        dados = _json.loads(proc.stdout)
        hubs = dados['report']['hubs']
        if not isinstance(hubs, list):
            raise TypeError(f"'hubs' não é uma lista: {type(hubs)!r}")
        hops = []
        for hub in hubs:
            host_str = hub.get('host')
            ip = None if not host_str or host_str == '???' else host_str
            hops.append({'hop': hub.get('count'), 'ip': ip})
    except (_json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
        stderr = proc.stderr[:500] if proc.stderr else ''
        raise RuntimeError(f'Saída do mtr em formato inesperado para {host}: {e}. stderr: {stderr}')

    return hops


def _rotaloop_executar_para_bloco(bloco):
    """Roda o teste de loop pra um único BlocoIP e devolve um dict com o
    resultado (não grava no banco — quem chama decide o que persistir)."""
    try:
        net = ipaddress.ip_network(bloco.bloco, strict=False)
    except ValueError as e:
        return {'status': 'inconclusivo', 'erro': f'Bloco IP inválido: {e}', 'ip_alvo': None, 'ip_em_loop': None, 'hops': [], 'ferramenta': 'mtr'}

    ip_alvo = _rotaloop_ip_alvo(net)

    try:
        hops = _rotaloop_mtr_json(ip_alvo)
    except RuntimeError as e:
        return {'status': 'inconclusivo', 'erro': str(e), 'ip_alvo': ip_alvo, 'ip_em_loop': None, 'hops': [], 'ferramenta': 'mtr'}

    status, ip_em_loop = _rotaloop_detectar_loop(hops)
    return {'status': status, 'erro': None, 'ip_alvo': ip_alvo, 'ip_em_loop': ip_em_loop, 'hops': hops, 'ferramenta': 'mtr'}


def _rotaloop_executar_para_cliente(cliente):
    """Roda o teste de loop pra todos os BlocoIP de um cliente e persiste o
    resultado. Isolado em try/except por bloco — uma falha num bloco não
    derruba o teste dos demais, mesmo padrão do AmpScan. Blocos que não
    puderam ser testados nesta execução (inconclusivo ou exceção) NÃO
    contam como "retestados com sucesso" — um loop detectado anteriormente
    só é marcado como resolvido quando o bloco foi de fato retestado e
    voltou limpo, nunca por causa de uma falha transitória do mtr."""
    from .models import RotaLoopExecucaoLog, RotaLoopResultado

    execucao = RotaLoopExecucaoLog.objects.create(cliente=cliente)

    blocos = list(BlocoIP.objects.filter(cliente=cliente))
    total_loops = 0
    blocos_com_erro = 0
    blocos_com_loop_ids = set()
    blocos_testados_com_sucesso_ids = set()

    for bloco in blocos:
        try:
            resultado = _rotaloop_executar_para_bloco(bloco)
        except Exception:
            blocos_com_erro += 1
            logger.exception(f'rotaloop: falha inesperada no bloco {bloco.bloco} (cliente {cliente.id})')
            continue

        if resultado['status'] == 'inconclusivo':
            blocos_com_erro += 1
            continue

        blocos_testados_com_sucesso_ids.add(bloco.id)

        if resultado['status'] != 'loop_detectado':
            continue

        total_loops += 1
        blocos_com_loop_ids.add(bloco.id)
        RotaLoopResultado.objects.update_or_create(
            bloco_ip=bloco,
            defaults={
                'cliente': cliente,
                'ip_alvo': resultado['ip_alvo'],
                'status': 'loop_detectado',
                'ip_em_loop': resultado['ip_em_loop'],
                'hops': resultado['hops'],
                'ferramenta': resultado['ferramenta'],
                'resolvido': False,
                'resolvido_em': None,
            },
        )

    # Loops anteriores em blocos deste cliente que foram retestados com
    # sucesso e não apareceram de novo — marca como resolvido em vez de
    # apagar (mesmo padrão do AmpScan). Blocos que falharam o teste nesta
    # execução ficam de fora: não sabemos se o loop ainda existe.
    anteriores = RotaLoopResultado.objects.filter(
        cliente=cliente, resolvido=False, bloco_ip_id__in=blocos_testados_com_sucesso_ids,
    ).exclude(bloco_ip_id__in=blocos_com_loop_ids)
    for resultado in anteriores:
        resultado.resolvido = True
        resultado.resolvido_em = timezone.now()
        resultado.save(update_fields=['resolvido', 'resolvido_em'])

    execucao.total_blocos_testados = len(blocos)
    execucao.total_loops_detectados = total_loops
    execucao.sucesso = (blocos_com_erro == 0)
    if blocos_com_erro:
        execucao.erro_mensagem = (
            f'{blocos_com_erro} de {len(blocos)} bloco(s) não puderam ser testados '
            '(mtr indisponível/timeout/erro) — resultados anteriores desses blocos '
            'não foram reavaliados.'
        )
    execucao.finalizado_em = timezone.now()
    execucao.save()
    return execucao


@shared_task
def rotaloop_testar_cliente(cliente_id):
    """Teste de loop de roteamento sob demanda (botão 'Testar Agora' na aba
    Vulnerabilidades) para um único cliente."""
    from .models import Cliente

    try:
        cliente = Cliente.objects.get(id=cliente_id)
    except Cliente.DoesNotExist:
        return {'status': 'ignorado', 'motivo': 'Cliente não encontrado'}

    execucao = _rotaloop_executar_para_cliente(cliente)
    return {
        'status': 'ok' if execucao.sucesso else 'erro',
        'execucao_id': execucao.id,
        'total_loops_detectados': execucao.total_loops_detectados,
    }


@shared_task
def rotaloop_verificar_clientes_agendado():
    """Teste de loop de roteamento a cada 2 dias (Celery Beat) — testa TODOS
    os clientes com blocos IP a cada execução (sem revezamento de grupo,
    diferente do AmpScan: aqui é 1 mtr por bloco, não milhares de probes por
    bloco, então o custo por execução é baixo o suficiente pra não precisar
    fatiar). Isolado em try/except por cliente."""
    from .models import Cliente

    clientes = Cliente.objects.filter(blocos_ip__isnull=False).distinct().order_by('id')
    total, ok, falhas = 0, 0, 0

    logger.info(f'rotaloop_verificar_clientes_agendado: {len(clientes)} cliente(s) nesta execução.')

    for cliente in clientes:
        total += 1
        try:
            execucao = _rotaloop_executar_para_cliente(cliente)
            if execucao.sucesso:
                ok += 1
            else:
                falhas += 1
        except Exception:
            falhas += 1
            logger.exception(f'rotaloop_verificar_clientes_agendado: falha no cliente {cliente.id}')

    logger.info(f'rotaloop_verificar_clientes_agendado: concluído — {ok}/{total} OK, {falhas} falha(s).')
    return {'total': total, 'ok': ok, 'falhas': falhas}
