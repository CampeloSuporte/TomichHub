from celery import shared_task
from datetime import timedelta
from django.utils import timezone
from django.core.files.base import ContentFile
import os
import subprocess
import hashlib
from .models import BackupConfig, Backup, BackupTemplate

@shared_task
def executar_backup_task(config_id):
    """Executa backup para uma configuração específica"""
    try:
        config = BackupConfig.objects.get(id=config_id)
        acesso = config.acesso
        template = config.template
        
        if not template or not config.habilitado:
            return {'status': 'erro', 'mensagem': 'Configuração inválida'}
        
        # Criar registro de backup
        backup = Backup.objects.create(
            config=config,
            status=Backup.StatusChoices.EXECUTANDO
        )
        
        # Determinar qual playbook executar
        playbook_map = {
            'MIKROTIK': 'backup_mikrotik.yml',
            'CISCO': 'backup_cisco.yml',
            'HUAWEI': 'backup_huawei.yml',
            'JUNIPER': 'backup_juniper.yml',
        }
        
        playbook = playbook_map.get(template.fabricante)
        if not playbook:
            backup.status = Backup.StatusChoices.ERRO
            backup.mensagem_erro = 'Template não suportado'
            backup.save()
            return {'status': 'erro', 'mensagem': 'Template não suportado'}
        
        # Executar playbook
        playbook_path = f'/var/lib/conexa-crm/ansible/playbooks/{playbook}'
        inventory_path = '/var/lib/conexa-crm/ansible/inventory.py'
        
        cmd = [
            'ansible-playbook',
            playbook_path,
            '-i', inventory_path,
            '-l', acesso.host,
            '--extra-vars', f'ansible_host={acesso.host} ansible_user={acesso.usuario} ansible_password={acesso.senha}'
        ]
        
        try:
            resultado = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600
            )
            
            if resultado.returncode == 0:
                # Backup bem-sucedido
                backup_dir = f'/var/lib/conexa-crm/backups/{acesso.host}'
                backup_files = [f for f in os.listdir(backup_dir) if f.startswith('backup-')]
                
                if backup_files:
                    arquivo_path = os.path.join(backup_dir, backup_files[-1])
                    
                    with open(arquivo_path, 'rb') as f:
                        arquivo_conteudo = f.read()
                    
                    # Salvar arquivo no Django
                    backup.arquivo.save(
                        os.path.basename(arquivo_path),
                        ContentFile(arquivo_conteudo)
                    )
                    
                    backup.tamanho_arquivo = len(arquivo_conteudo)
                    backup.hash_md5 = hashlib.md5(arquivo_conteudo).hexdigest()
                
                backup.status = Backup.StatusChoices.SUCESSO
                backup.data_conclusao = timezone.now()
                
            else:
                backup.status = Backup.StatusChoices.ERRO
                backup.mensagem_erro = resultado.stderr[:500]
            
        except subprocess.TimeoutExpired:
            backup.status = Backup.StatusChoices.ERRO
            backup.mensagem_erro = 'Timeout ao executar playbook'
        
        # Calcular tempo de execução
        backup.tempo_execucao = backup.data_conclusao - backup.data_inicio if backup.data_conclusao else None
        
        # Agendar próximo backup
        config.ultimo_backup = backup.data_conclusao
        config.proxima_execucao = timezone.now() + timedelta(days=config.intervalo_dias)
        
        backup.save()
        config.save()
        
        return {
            'status': 'sucesso' if backup.status == Backup.StatusChoices.SUCESSO else 'erro',
            'backup_id': backup.id,
            'mensagem': 'Backup executado com sucesso' if backup.status == Backup.StatusChoices.SUCESSO else backup.mensagem_erro
        }
        
    except Exception as e:
        return {'status': 'erro', 'mensagem': str(e)}


@shared_task
def agendar_backups_pendentes():
    """Executa backups que estão com a execução agendada"""
    from django_celery_beat.models import PeriodicTask
    
    configs_pendentes = BackupConfig.objects.filter(
        habilitado=True,
        proxima_execucao__lte=timezone.now(),
        template__isnull=False
    )
    
    for config in configs_pendentes:
        executar_backup_task.delay(config.id)
    
    return f'{configs_pendentes.count()} backups agendados'
