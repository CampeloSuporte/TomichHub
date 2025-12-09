#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
✅ LIMPADOR DE BACKUPS - DETECÇÃO CORRIGIDA
Encontra a configuração correta do Django
"""

import os
import sys
import argparse
import importlib.util
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# ============================================
# AUTO-DETECTAR DJANGO - VERSÃO CORRIGIDA
# ============================================

def encontrar_projeto_django():
    """Encontra o projeto Django"""
    
    possibilidades = [
        '/opt/crm',
        '/home/lucas/crm',
        '/root/crm',
        '/var/www/crm',
        os.path.expanduser('~/crm'),
        os.getcwd(),
    ]
    
    print(f"\n{Cores.AZUL}🔍 Procurando projeto Django...{Cores.NORMAL}\n")
    
    for caminho in possibilidades:
        manage_py = os.path.join(caminho, 'manage.py')
        if os.path.exists(manage_py):
            print(f"{Cores.VERDE}✅ Encontrado em: {caminho}{Cores.NORMAL}")
            return caminho
        else:
            print(f"   ⏭️ {caminho} (não encontrado)")
    
    # Pedir manualmente
    print(f"\n{Cores.VERMELHO}❌ Projeto não encontrado nos locais padrão{Cores.NORMAL}")
    print(f"\n{Cores.AMARELO}📁 Digite o caminho completo do projeto (ex: /opt/crm):{Cores.NORMAL}")
    caminho = input("> ").strip()
    
    if not os.path.exists(os.path.join(caminho, 'manage.py')):
        print(f"{Cores.VERMELHO}❌ manage.py não encontrado em: {caminho}{Cores.NORMAL}")
        sys.exit(1)
    
    return caminho


def encontrar_settings_module(projeto_path):
    """Detecta o módulo de settings - VERSÃO CORRIGIDA"""
    
    print(f"\n{Cores.AZUL}🔍 Procurando módulo de settings...{Cores.NORMAL}\n")
    
    # Listar pastas no projeto (excluir venv, media, static, etc)
    excluir = ['venv', 'env', 'media', 'static', '__pycache__', '.git', '.venv']
    
    for item in os.listdir(projeto_path):
        item_path = os.path.join(projeto_path, item)
        
        # Pular se é pasta para excluir
        if item in excluir:
            print(f"   ⏭️ {item}/ (pulado)")
            continue
        
        # Se é pasta
        if os.path.isdir(item_path):
            # Procurar wsgi.py ou settings.py
            wsgi_path = os.path.join(item_path, 'wsgi.py')
            settings_path = os.path.join(item_path, 'settings.py')
            
            if os.path.exists(wsgi_path):
                print(f"{Cores.VERDE}✅ Encontrado wsgi.py em: {item}/{Cores.NORMAL}")
                return item
            
            if os.path.exists(settings_path):
                print(f"{Cores.VERDE}✅ Encontrado settings.py em: {item}/{Cores.NORMAL}")
                return item
            
            print(f"   ⏭️ {item}/ (sem wsgi.py ou settings.py)")
    
    # Manual
    print(f"\n{Cores.AMARELO}📁 Digite o nome da pasta do projeto (ex: CRM_config):{Cores.NORMAL}")
    modulo = input("> ").strip()
    
    settings_path = os.path.join(projeto_path, modulo, 'settings.py')
    if not os.path.exists(settings_path):
        print(f"{Cores.VERMELHO}❌ settings.py não encontrado em: {projeto_path}/{modulo}/settings.py{Cores.NORMAL}")
        sys.exit(1)
    
    return modulo


# ============================================
# CORES
# ============================================

class Cores:
    VERDE = '\033[0;32m'
    VERMELHO = '\033[0;31m'
    AMARELO = '\033[1;33m'
    AZUL = '\033[0;34m'
    MAGENTA = '\033[0;35m'
    NORMAL = '\033[0m'


# ============================================
# SETUP DJANGO - VERSÃO CORRIGIDA
# ============================================

def setup_django():
    """Setup Django - versão corrigida"""
    
    projeto_path = encontrar_projeto_django()
    settings_module = encontrar_settings_module(projeto_path)
    
    print(f"\n{Cores.AZUL}{'='*60}{Cores.NORMAL}")
    print(f"{Cores.AMARELO}⚙️  CONFIGURAÇÃO:{Cores.NORMAL}")
    print(f"   📁 Projeto: {projeto_path}")
    print(f"   📦 Módulo: {settings_module}")
    print(f"   🔧 DJANGO_SETTINGS_MODULE={settings_module}.settings")
    print(f"{Cores.AZUL}{'='*60}{Cores.NORMAL}\n")
    
    # Adicionar projeto ao path
    sys.path.insert(0, projeto_path)
    os.chdir(projeto_path)
    
    # Configurar Django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', f'{settings_module}.settings')
    
    try:
        import django
        print(f"{Cores.AMARELO}🔧 Configurando Django...{Cores.NORMAL}")
        django.setup()
        print(f"{Cores.VERDE}✅ Django configurado com sucesso!\n{Cores.NORMAL}")
        return True
    except Exception as e:
        print(f"{Cores.VERMELHO}❌ Erro ao configurar Django:{Cores.NORMAL}")
        print(f"   {str(e)}\n")
        
        print(f"{Cores.AMARELO}💡 Tente executar manualmente:{Cores.NORMAL}")
        print(f"   cd {projeto_path}")
        print(f"   source venv/bin/activate")
        print(f"   python -c \"import django; django.setup()\"")
        print()
        
        sys.exit(1)


# ============================================
# MAIN - LIMPEZA
# ============================================

def listar_backups_duplicados(manter_n=2):
    """Lista backups duplicados"""
    
    from django.conf import settings
    from clientes.models import BackupLog
    
    print(f"{Cores.AZUL}{'='*70}{Cores.NORMAL}")
    print(f"{Cores.AZUL}🔍 ANALISANDO BACKUPS DUPLICADOS{Cores.NORMAL}")
    print(f"{Cores.AZUL}{'='*70}{Cores.NORMAL}\n")
    
    # Agrupar por acesso
    backups_por_acesso = defaultdict(list)
    
    backups = BackupLog.objects.filter(
        status='SUCESSO'
    ).select_related(
        'acesso', 'cliente', 'template'
    ).order_by('acesso_id', '-data_backup')
    
    for backup in backups:
        backups_por_acesso[backup.acesso_id].append(backup)
    
    print(f"{Cores.AMARELO}📊 RESUMO:{Cores.NORMAL}")
    print(f"   Total de acessos: {len(backups_por_acesso)}")
    print(f"   Manter últimos: {manter_n} por acesso")
    print(f"   Total de backups: {backups.count()}\n")
    
    total_para_deletar = 0
    espaco_a_liberar = 0
    resultado_por_acesso = {}
    
    for acesso_id in sorted(backups_por_acesso.keys()):
        backups_do_acesso = backups_por_acesso[acesso_id]
        total = len(backups_do_acesso)
        
        if total <= manter_n:
            continue
        
        para_deletar = total - manter_n
        total_para_deletar += para_deletar
        
        acesso = backups_do_acesso[0].acesso
        cliente = backups_do_acesso[0].cliente
        
        manteem = backups_do_acesso[:manter_n]
        deletam = backups_do_acesso[manter_n:]
        
        espaco_deletam = sum(b.tamanho_bytes for b in deletam)
        espaco_a_liberar += espaco_deletam
        
        resultado_por_acesso[acesso_id] = {
            'acesso': acesso,
            'cliente': cliente,
            'total': total,
            'manter': len(manteem),
            'deletar': len(deletam),
            'espaco': espaco_deletam,
            'manteem': manteem,
            'deletam': deletam
        }
        
        print(f"{Cores.AZUL}📁 Acesso #{acesso_id}: {acesso.tipo} ({acesso.host}){Cores.NORMAL}")
        print(f"   {Cores.MAGENTA}Cliente: {cliente.nome_empresa}{Cores.NORMAL}")
        print(f"   📊 Total: {total} | ⭐ Manter: {manter_n} | 🗑️ Deletar: {para_deletar}")
        print(f"   💾 Espaço a liberar: {format_bytes(espaco_deletam)}\n")
        
        print(f"   {Cores.VERDE}✅ MANTÊM (últimos {manter_n}):{Cores.NORMAL}")
        for idx, backup in enumerate(manteem, 1):
            print(
                f"      {idx}. #{backup.id:4} | {backup.data_backup.strftime('%d/%m/%Y %H:%M')} | "
                f"{format_bytes(backup.tamanho_bytes):>8}"
            )
        print()
        
        print(f"   {Cores.VERMELHO}❌ DELETAM (antigos):{Cores.NORMAL}")
        for idx, backup in enumerate(deletam, 1):
            print(
                f"      {idx}. #{backup.id:4} | {backup.data_backup.strftime('%d/%m/%Y %H:%M')} | "
                f"{format_bytes(backup.tamanho_bytes):>8}"
            )
        print()
    
    if total_para_deletar == 0:
        print(f"{Cores.VERDE}✅ Nenhum backup duplicado encontrado!{Cores.NORMAL}\n")
        return resultado_por_acesso
    
    print(f"{Cores.AZUL}{'='*70}{Cores.NORMAL}")
    print(f"{Cores.AMARELO}📊 RESUMO FINAL:{Cores.NORMAL}")
    print(f"   Total de acessos com duplicatas: {len(resultado_por_acesso)}")
    print(f"   Total de backups a deletar: {total_para_deletar}")
    print(f"   {Cores.VERDE}Espaço a liberar: {format_bytes(espaco_a_liberar)}{Cores.NORMAL}")
    print(f"{Cores.AZUL}{'='*70}{Cores.NORMAL}\n")
    
    return resultado_por_acesso


def deletar_backups_duplicados(resultado_por_acesso):
    """Deleta backups"""
    
    from django.conf import settings
    
    if not resultado_por_acesso:
        print(f"{Cores.AMARELO}Nada a deletar{Cores.NORMAL}\n")
        return
    
    total = sum(r['deletar'] for r in resultado_por_acesso.values())
    espaco = sum(r['espaco'] for r in resultado_por_acesso.values())
    
    print(f"{Cores.VERMELHO}⚠️  ATENÇÃO:{Cores.NORMAL}")
    print(f"   Vai deletar: {total} backups")
    print(f"   Espaço a liberar: {format_bytes(espaco)}")
    print()
    
    resposta = input(f"{Cores.AMARELO}Tem certeza? (s/n): {Cores.NORMAL}")
    if resposta.lower() not in ['s', 'sim', 'y', 'yes']:
        print(f"{Cores.AMARELO}Cancelado{Cores.NORMAL}\n")
        return
    
    print()
    print(f"{Cores.AZUL}{'='*70}{Cores.NORMAL}")
    print(f"{Cores.MAGENTA}🗑️  DELETANDO...{Cores.NORMAL}")
    print(f"{Cores.AZUL}{'='*70}{Cores.NORMAL}\n")
    
    deletados = 0
    erros = 0
    espaco_liberado = 0
    
    for acesso_id, dados in resultado_por_acesso.items():
        print(f"{Cores.AZUL}📁 Acesso #{acesso_id}:{Cores.NORMAL}")
        
        for idx, backup in enumerate(dados['deletam'], 1):
            try:
                if backup.arquivo_path:
                    arquivo_path = os.path.join(settings.MEDIA_ROOT, backup.arquivo_path)
                    
                    if os.path.exists(arquivo_path):
                        tamanho = os.path.getsize(arquivo_path)
                        os.remove(arquivo_path)
                        espaco_liberado += tamanho
                        print(f"   ✅ Arquivo: {os.path.basename(backup.arquivo_path)}")
                    else:
                        print(f"   ⚠️ Arquivo não encontrado: {backup.arquivo_path}")
                
                backup_id = backup.id
                backup.delete()
                print(f"   ✅ Registro #{backup_id} do BD removido")
                
                deletados += 1
                
            except Exception as e:
                print(f"   ❌ Erro: {str(e)}")
                erros += 1
        
        print()
    
    print(f"{Cores.AZUL}{'='*70}{Cores.NORMAL}")
    print(f"{Cores.VERDE}✅ DELEÇÃO CONCLUÍDA!{Cores.NORMAL}")
    print(f"   Deletados: {deletados}")
    print(f"   Erros: {erros}")
    print(f"   Espaço liberado: {format_bytes(espaco_liberado)}")
    print(f"{Cores.AZUL}{'='*70}{Cores.NORMAL}\n")


def format_bytes(bytes_value):
    """Formata bytes"""
    for unidade in ['B', 'KB', 'MB', 'GB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.1f} {unidade}"
        bytes_value /= 1024.0
    return f"{bytes_value:.1f} TB"


def main():
    parser = argparse.ArgumentParser(
        description='🗑️ Limpador de backups duplicados',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Exemplos:
  %(prog)s --apenas-listar
  %(prog)s --deletar
  %(prog)s --manter 3 --deletar
        '''
    )
    
    parser.add_argument(
        '--apenas-listar',
        action='store_true',
        help='Apenas listar'
    )
    
    parser.add_argument(
        '--deletar',
        action='store_true',
        help='Deletar após confirmação'
    )
    
    parser.add_argument(
        '--manter',
        type=int,
        default=2,
        help='Quantos últimos manter (padrão: 2)'
    )
    
    args = parser.parse_args()
    
    # Setup Django
    setup_django()
    
    # Executar
    resultado = listar_backups_duplicados(args.manter)
    
    if args.deletar and resultado:
        deletar_backups_duplicados(resultado)
    elif resultado and not args.apenas_listar and not args.deletar:
        print(f"{Cores.AMARELO}💡 Para deletar, use: --deletar{Cores.NORMAL}\n")


if __name__ == '__main__':
    main()
