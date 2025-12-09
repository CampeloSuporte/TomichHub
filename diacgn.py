#!/usr/bin/env python
# Script para diagnosticar problemas com comprovantes de pagamento

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.settings')
django.setup()

from django.conf import settings
from financeiro.models import Pagamento, Fatura

print("=" * 80)
print("🔍 DIAGNÓSTICO - COMPROVANTES DE PAGAMENTO")
print("=" * 80)

# 1. Verificar configurações Django
print("\n1️⃣ VERIFICANDO CONFIGURAÇÕES DJANGO:")
print("-" * 80)

print(f"✅ MEDIA_ROOT: {settings.MEDIA_ROOT}")
print(f"   Existe: {os.path.exists(settings.MEDIA_ROOT)}")
print(f"   É gravável: {os.access(settings.MEDIA_ROOT, os.W_OK)}")

print(f"\n✅ MEDIA_URL: {settings.MEDIA_URL}")

# 2. Verificar diretórios
print("\n2️⃣ VERIFICANDO DIRETÓRIOS:")
print("-" * 80)

dirs_to_check = [
    f"{settings.MEDIA_ROOT}/financeiro/comprovantes",
    f"{settings.MEDIA_ROOT}/financeiro",
]

for directory in dirs_to_check:
    exists = os.path.exists(directory)
    writable = os.access(directory, os.W_OK) if exists else False
    print(f"✅ {directory}")
    print(f"   Existe: {exists}")
    print(f"   Gravável: {writable}")

# 3. Verificar pagamentos
print("\n3️⃣ VERIFICANDO PAGAMENTOS NO BANCO:")
print("-" * 80)

pagamentos = Pagamento.objects.all()
print(f"Total de pagamentos: {pagamentos.count()}")

if pagamentos.count() > 0:
    print("\nÚltimos 3 pagamentos:")
    for i, pag in enumerate(pagamentos[:3], 1):
        print(f"\n  {i}. {pag.numero_recibo}")
        print(f"     Fatura: {pag.fatura.numero_fatura}")
        print(f"     Valor: R$ {pag.valor:.2f}")
        print(f"     Arquivo: {pag.comprovante}")
        
        if pag.comprovante:
            arquivo_path = pag.comprovante.path if hasattr(pag.comprovante, 'path') else f"{settings.MEDIA_ROOT}/{pag.comprovante}"
            existe_arquivo = os.path.exists(arquivo_path) if hasattr(pag.comprovante, 'path') else os.path.exists(f"{settings.MEDIA_ROOT}/{pag.comprovante}")
            print(f"     ✅ Arquivo existe: {existe_arquivo}")
            print(f"     URL: {pag.comprovante.url if hasattr(pag.comprovante, 'url') else 'Não disponível'}")
        else:
            print(f"     ❌ Sem arquivo de comprovante")

else:
    print("❌ Nenhum pagamento encontrado no banco de dados")

# 4. Verificar faturas
print("\n4️⃣ VERIFICANDO FATURAS:")
print("-" * 80)

faturas = Fatura.objects.all()
print(f"Total de faturas: {faturas.count()}")

if faturas.count() > 0:
    print("\nÚltimas 3 faturas:")
    for i, fatura in enumerate(faturas[:3], 1):
        pags = fatura.pagamentos.all()
        print(f"\n  {i}. {fatura.numero_fatura}")
        print(f"     Cliente: {fatura.cliente.nome_empresa}")
        print(f"     Status: {fatura.status}")
        print(f"     Pagamentos registrados: {pags.count()}")
        
        if pags.count() > 0:
            for pag in pags:
                print(f"       - {pag.numero_recibo}: R$ {pag.valor:.2f} (com arquivo: {bool(pag.comprovante)})")

# 5. Resumo
print("\n" + "=" * 80)
print("📋 RESUMO DO DIAGNÓSTICO:")
print("=" * 80)

checks = {
    "MEDIA_ROOT configurado": os.path.exists(settings.MEDIA_ROOT),
    "MEDIA_ROOT é gravável": os.access(settings.MEDIA_ROOT, os.W_OK),
    "Diretório comprovantes existe": os.path.exists(f"{settings.MEDIA_ROOT}/financeiro/comprovantes"),
    "Há pagamentos no banco": pagamentos.count() > 0,
    "Há faturas no banco": faturas.count() > 0,
}

for check, result in checks.items():
    symbol = "✅" if result else "❌"
    print(f"{symbol} {check}")

print("\n" + "=" * 80)
