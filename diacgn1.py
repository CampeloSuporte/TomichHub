#!/usr/bin/env python
# Script para regenerar comprovantes PDF para pagamentos sem arquivo

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.settings')
django.setup()

from django.core.files.base import ContentFile
from financeiro.models import Pagamento
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from io import BytesIO
import datetime as dt

def gerar_comprovante_pdf(pagamento):
    """Gera PDF de comprovante"""
    try:
        buffer = BytesIO()
        
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.5*inch,
            leftMargin=0.5*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch,
            title=f"Comprovante {pagamento.numero_recibo}"
        )
        
        styles = getSampleStyleSheet()
        
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            textColor=colors.HexColor('#1a5490'),
            spaceAfter=30,
            alignment=1,
            fontName='Helvetica-Bold'
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=11,
            textColor=colors.HexColor('#333333'),
            spaceAfter=12,
            spaceBefore=12,
            fontName='Helvetica-Bold'
        )
        
        elements = []
        
        elements.append(Paragraph("COMPROVANTE DE PAGAMENTO", title_style))
        elements.append(Spacer(1, 0.2*inch))
        
        data = [
            ['Número do Recibo:', pagamento.numero_recibo],
            ['Data do Pagamento:', pagamento.data_pagamento.strftime('%d/%m/%Y')],
            ['Tipo de Pagamento:', pagamento.get_tipo_display() if hasattr(pagamento, 'get_tipo_display') else pagamento.tipo],
        ]
        
        table = Table(data, colWidths=[2*inch, 2.5*inch])
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
        
        elements.append(Paragraph("FATURA REFERENTE", heading_style))
        
        fatura = pagamento.fatura
        fatura_data = [
            ['Número da Fatura:', fatura.numero_fatura],
            ['Cliente:', fatura.cliente.nome_empresa],
            ['CNPJ:', fatura.cliente.cnpj],
            ['Valor Original:', f'R$ {float(fatura.valor_total):.2f}'],
            ['Valor Pago:', f'R$ {float(pagamento.valor):.2f}'],
        ]
        
        fatura_table = Table(fatura_data, colWidths=[2*inch, 2.5*inch])
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
        
        elements.append(Spacer(1, 0.2*inch))
        footer_style = ParagraphStyle(
            'Footer',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#666666'),
            alignment=1,
            fontName='Helvetica'
        )
        data_hora = dt.datetime.now().strftime('%d/%m/%Y às %H:%M:%S')
        elements.append(Paragraph(
            f"Comprovante gerado em {data_hora}",
            footer_style
        ))
        
        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        
        return pdf_bytes
        
    except Exception as e:
        print(f"  ❌ Erro ao gerar PDF: {str(e)}")
        raise

print("=" * 80)
print("📄 REGENERANDO COMPROVANTES PDF PARA PAGAMENTOS")
print("=" * 80)

# Encontrar pagamentos sem comprovante
pagamentos_sem_comprovante = Pagamento.objects.filter(comprovante='')
print(f"\n📊 Pagamentos sem comprovante: {pagamentos_sem_comprovante.count()}\n")

if pagamentos_sem_comprovante.count() == 0:
    print("✅ Todos os pagamentos já têm comprovante!")
    exit(0)

# Regenerar
sucesso = 0
erro = 0

for pag in pagamentos_sem_comprovante:
    try:
        print(f"📝 {pag.numero_recibo}...", end=" ")
        
        # Gerar PDF
        pdf_bytes = gerar_comprovante_pdf(pag)
        
        # Salvar
        nome_arquivo = f"comprovante_{pag.numero_recibo}.pdf"
        pag.comprovante = ContentFile(pdf_bytes, name=nome_arquivo)
        pag.save()
        
        print(f"✅ OK ({len(pdf_bytes)} bytes)")
        sucesso += 1
        
    except Exception as e:
        print(f"❌ ERRO: {str(e)}")
        erro += 1

print("\n" + "=" * 80)
print(f"📊 RESULTADO: {sucesso} regenerados, {erro} erros")
print("=" * 80)

if sucesso > 0:
    print("\n✅ Pagamentos regenerados com sucesso!")
    print("\nPróximas ações:")
    print("  1. Reiniciar Django: python manage.py runserver")
    print("  2. Testar no navegador")
    print("  3. Os comprovantes devem estar disponíveis para download")
else:
    print("\n❌ Nenhum comprovante foi regenerado")
    print("   Verifique os erros acima")
