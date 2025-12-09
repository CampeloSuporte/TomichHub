#!/usr/bin/env python3
"""
🔍 DEBUG: Encontrar o erro exato na geração do PDF
"""

import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.settings')
django.setup()

from financeiro.models import Pagamento
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
import datetime as dt
import traceback

print("=" * 80)
print("🔍 DEBUG: Testando geração de PDF")
print("=" * 80)

# Pegar último pagamento
try:
    pagamento = Pagamento.objects.latest('id')
    print(f"\n✅ Pagamento encontrado: {pagamento.numero_recibo}")
except Pagamento.DoesNotExist:
    print("❌ Nenhum pagamento encontrado")
    sys.exit(1)

# ===== TESTE 1: PDF MÍNIMO =====
print("\n" + "=" * 80)
print("TESTE 1️⃣: PDF MÍNIMO (sem elementos complexos)")
print("=" * 80)

try:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    
    story = [
        Paragraph("TESTE SIMPLES", styles['Title']),
    ]
    
    print("  ✅ Construindo PDF simples...")
    doc.build(story)
    
    pdf_bytes = buffer.getvalue()
    buffer.close()
    
    print(f"  ✅ PDF gerado: {len(pdf_bytes)} bytes")
    
    if pdf_bytes.startswith(b'%PDF'):
        print(f"  ✅ Header válido!")
    else:
        print(f"  ❌ Header inválido!")
        
except Exception as e:
    print(f"  ❌ ERRO: {e}")
    traceback.print_exc()

# ===== TESTE 2: PDF COM DADOS DO PAGAMENTO =====
print("\n" + "=" * 80)
print("TESTE 2️⃣: PDF COM DADOS DO PAGAMENTO")
print("=" * 80)

try:
    buffer = BytesIO()
    
    print("  ✅ Criando documento...")
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.5*inch,
        leftMargin=0.5*inch,
        topMargin=1*inch,
        bottomMargin=0.75*inch,
    )
    
    print("  ✅ Criando estilos...")
    styles = getSampleStyleSheet()
    
    empresa_style = ParagraphStyle(
        'EmpresaName',
        parent=styles['Heading1'],
        fontSize=16,
        textColor=colors.HexColor('#1a5490'),
        spaceAfter=10,
        alignment=0,
        fontName='Helvetica-Bold'
    )
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=14,
        textColor=colors.HexColor('#333333'),
        spaceAfter=20,
        alignment=0,
        fontName='Helvetica-Bold'
    )
    
    print("  ✅ Preparando elementos...")
    elements = []
    
    elements.append(Paragraph("TOMICH TECNOLOGIA", empresa_style))
    elements.append(Spacer(1, 0.2*inch))
    elements.append(Paragraph("COMPROVANTE DE PAGAMENTO", title_style))
    elements.append(Spacer(1, 0.1*inch))
    
    print("  ✅ Adicionando linha divisória...")
    line = HRFlowable(
        width="100%",
        thickness=2,
        lineCap='round',
        strokeColor=colors.HexColor('#1a5490'),
        spaceAfter=0.15*inch,
    )
    elements.append(line)
    
    print("  ✅ Adicionando tabela 1...")
    data = [
        ['Número do Recibo:', pagamento.numero_recibo],
        ['Data do Pagamento:', pagamento.data_pagamento.strftime('%d/%m/%Y')],
        ['Tipo de Pagamento:', pagamento.tipo],
    ]
    
    table = Table(data, colWidths=[2*inch, 3.5*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f0f0f0')),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    elements.append(table)
    elements.append(Spacer(1, 0.2*inch))
    
    print("  ✅ Adicionando tabela 2...")
    fatura = pagamento.fatura
    fatura_data = [
        ['Número da Fatura:', fatura.numero_fatura],
        ['Cliente:', fatura.cliente.nome_empresa],
        ['CNPJ:', fatura.cliente.cnpj],
        ['Valor Pago:', f'R$ {float(pagamento.valor):.2f}'],
    ]
    
    fatura_table = Table(fatura_data, colWidths=[2*inch, 3.5*inch])
    fatura_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f0f0f0')),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    elements.append(fatura_table)
    elements.append(Spacer(1, 0.3*inch))
    
    print("  ✅ Adicionando footer...")
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.HexColor('#666666'),
        alignment=1,
        fontName='Helvetica'
    )
    data_hora = dt.datetime.now().strftime('%d/%m/%Y às %H:%M:%S')
    elements.append(Paragraph(
        f"Comprovante gerado em {data_hora}",
        footer_style
    ))
    
    print(f"  ✅ Total de {len(elements)} elementos preparados")
    print("  ✅ Construindo PDF...")
    doc.build(elements)
    
    pdf_bytes = buffer.getvalue()
    buffer.close()
    
    print(f"  ✅ PDF gerado: {len(pdf_bytes)} bytes")
    
    if len(pdf_bytes) == 0:
        print("  ❌ PDF VAZIO! Mas nenhum erro foi lançado!")
        print("  Isto é muito estranho... verifique o ReportLab")
    elif pdf_bytes.startswith(b'%PDF'):
        print(f"  ✅ Header válido!")
        print(f"\n🎉 PDF GERADO COM SUCESSO!")
    else:
        print(f"  ❌ Header inválido!")
        print(f"  Primeiros bytes: {pdf_bytes[:20]}")
        
except Exception as e:
    print(f"  ❌ ERRO: {e}")
    print(f"\n  TRACEBACK COMPLETO:")
    traceback.print_exc()

print("\n" + "=" * 80)
print("✅ Debug concluído")
print("=" * 80)
