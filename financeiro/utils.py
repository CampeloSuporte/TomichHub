from django.conf import settings
from django.core.files.base import ContentFile
from datetime import datetime, date
import os
from decimal import Decimal
import io

# Tentar importar biblioteca para gerar boletos
try:
    from pyfpdf import FPDF
except ImportError:
    FPDF = None


def gerar_boleto_pdf(boleto, config):
    """
    ✅ Gera boleto em PDF com logo
    
    Requer: pip install fpdf2
    
    Se não tiver fpdf2, instale:
    pip install fpdf2
    """
    
    if not FPDF:
        print("❌ FPDF não instalado. Execute: pip install fpdf2")
        return None
    
    try:
        print(f"\n{'='*80}")
        print(f"📄 GERANDO BOLETO PDF")
        print(f"{'='*80}")
        print(f"Boleto: {boleto.numero_boleto}")
        print(f"Cliente: {boleto.cliente.nome_empresa}")
        print(f"Valor: R$ {boleto.valor}")
        
        pdf = FPDF(orientation='P', unit='mm', format='A4')
        pdf.add_page()
        
        # ============================================
        # CABEÇALHO COM LOGO
        # ============================================
        if config.empresa_logo:
            logo_path = config.empresa_logo.path
            if os.path.exists(logo_path):
                try:
                    # Logo
                    pdf.image(logo_path, x=10, y=10, w=30, h=0)
                except Exception as e:
                    print(f"⚠️ Erro ao inserir logo: {str(e)}")
        
        # Dados da empresa
        pdf.set_font("Helvetica", 'B', 12)
        pdf.set_xy(50, 15)
        pdf.cell(0, 5, config.empresa_nome, ln=True)
        
        pdf.set_font("Helvetica", '', 9)
        pdf.set_x(50)
        pdf.cell(0, 4, f"CNPJ: {config.empresa_cnpj}", ln=True)
        
        pdf.set_x(50)
        pdf.cell(0, 4, f"Tel: {config.telefone} | {config.email}", ln=True)
        
        # ============================================
        # SEÇÃO DE BANCO
        # ============================================
        
        # Linha separadora
        pdf.set_y(45)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        
        # Código do banco
        pdf.set_font("Helvetica", 'B', 16)
        pdf.set_xy(15, 48)
        pdf.cell(20, 8, config.banco_codigo, border=1)
        
        # DV (dígito verificador - placeholder)
        pdf.set_font("Helvetica", 'B', 14)
        pdf.set_xy(155, 48)
        pdf.cell(20, 8, "0", border=1)
        
        # Nosso número
        pdf.set_font("Helvetica", 'B', 11)
        pdf.set_xy(10, 58)
        pdf.cell(0, 5, f"Nosso Número: {boleto.nosso_numero}", ln=True)
        
        # Linha separadora
        pdf.line(10, 65, 200, 65)
        
        # ============================================
        # DADOS DO BOLETO
        # ============================================
        
        y = 70
        
        # Seção 1: Cliente e Beneficiário
        pdf.set_font("Helvetica", '', 9)
        pdf.set_xy(10, y)
        pdf.cell(0, 4, "PAGADOR", ln=True)
        
        pdf.set_xy(10, y+4)
        pdf.set_font("Helvetica", 'B', 10)
        pdf.cell(0, 4, f"{boleto.cliente.nome_empresa}", ln=True)
        
        pdf.set_xy(10, y+8)
        pdf.set_font("Helvetica", '', 9)
        pdf.cell(0, 4, f"CNPJ: {boleto.cliente.cnpj}", ln=True)
        
        pdf.set_xy(10, y+12)
        pdf.cell(0, 4, f"Endereço: {boleto.cliente.endereco}, {boleto.cliente.cidade} - {boleto.cliente.estado}", ln=True)
        
        # Seção 2: Banco
        pdf.set_xy(130, y)
        pdf.set_font("Helvetica", '', 9)
        pdf.cell(0, 4, "LOCAL DE PAGAMENTO", ln=True)
        
        pdf.set_xy(130, y+4)
        pdf.set_font("Helvetica", 'B', 10)
        pdf.cell(0, 4, config.banco_nome, ln=True)
        
        # Seção 3: Valores
        y += 25
        
        pdf.set_font("Helvetica", '', 9)
        pdf.set_xy(10, y)
        pdf.cell(0, 4, "VENCIMENTO", ln=True)
        
        pdf.set_xy(10, y+4)
        pdf.set_font("Helvetica", 'B', 11)
        pdf.cell(0, 5, boleto.data_vencimento.strftime('%d/%m/%Y'), ln=True)
        
        # Valor
        pdf.set_xy(130, y)
        pdf.set_font("Helvetica", '', 9)
        pdf.cell(0, 4, "VALOR DO DOCUMENTO", ln=True)
        
        pdf.set_xy(130, y+4)
        pdf.set_font("Helvetica", 'B', 11)
        valor_formatado = f"R$ {boleto.valor:,.2f}".replace(',', '.')
        pdf.cell(0, 5, valor_formatado, ln=True)
        
        # Seção 4: Descrição
        y += 25
        
        pdf.set_font("Helvetica", '', 9)
        pdf.set_xy(10, y)
        pdf.cell(0, 4, "DESCRIÇÃO DO PAGAMENTO", ln=True)
        
        pdf.set_xy(10, y+4)
        pdf.set_font("Helvetica", '', 10)
        
        if boleto.fatura:
            descricao = f"Fatura {boleto.fatura.numero_fatura} - {boleto.fatura.observacoes or 'Serviços de Rede e Consultoria'}"
        else:
            descricao = "Serviços prestados"
        
        # Quebrar texto se necessário
        pdf.multi_cell(0, 4, descricao)
        
        # Seção 5: Instruções
        y = 150
        
        pdf.set_font("Helvetica", '', 9)
        pdf.set_xy(10, y)
        pdf.cell(0, 4, "INSTRUÇÕES", ln=True)
        
        pdf.set_xy(10, y+4)
        pdf.set_font("Helvetica", '', 8)
        instrucoes = [
            f"• Juros de mora: {config.juros_atraso_percentual}% ao dia de atraso",
            f"• Multa por atraso: R$ {config.multa_atraso:,.2f}" if config.multa_atraso > 0 else "• Sem multa por atraso",
            "• Favor usar o código de barras para pagamento",
        ]
        
        for instr in instrucoes:
            pdf.cell(0, 3, instr, ln=True)
            pdf.set_x(10)
        
        # ============================================
        # CÓDIGO DE BARRAS SIMULADO
        # ============================================
        
        y = 180
        
        pdf.set_xy(10, y)
        pdf.set_font("Helvetica", '', 9)
        pdf.cell(0, 4, "CÓDIGO DE BARRAS", ln=True)
        
        # Número do código (simulado)
        pdf.set_xy(10, y+4)
        pdf.set_font("Courier", 'B', 12)
        codigo = gerar_codigo_barras(boleto)
        pdf.cell(0, 5, codigo, ln=True)
        
        # ============================================
        # RODAPÉ
        # ============================================
        
        pdf.set_y(255)
        pdf.set_font("Helvetica", '', 7)
        pdf.set_text_color(100, 100, 100)
        
        pdf.cell(0, 3, f"Boleto gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M:%S')} - ID: {boleto.uuid}", 
                 align='C', ln=True)
        pdf.cell(0, 3, "Este é um documento digital. Não é necessário assinatura.", 
                 align='C', ln=True)
        
        # ============================================
        # SALVAR PDF
        # ============================================
        
        # Gerar nome do arquivo
        nome_arquivo = f"boleto_{boleto.numero_boleto.replace('/', '_')}.pdf"
        
        # Criar diretório se não existir
        upload_dir = os.path.join(settings.MEDIA_ROOT, 'financeiro', 'boletos', 
                                 datetime.now().strftime('%Y'), 
                                 datetime.now().strftime('%m'))
        os.makedirs(upload_dir, exist_ok=True)
        
        arquivo_path = os.path.join(upload_dir, nome_arquivo)
        
        # Salvar PDF
        pdf.output(arquivo_path, 'F')
        
        # Salvar caminho relativo no banco
        arquivo_relativo = os.path.relpath(arquivo_path, settings.MEDIA_ROOT)
        boleto.arquivo_pdf = arquivo_relativo
        boleto.status = 'ENVIADO'
        boleto.save()
        
        print(f"✅ Boleto gerado com sucesso!")
        print(f"📁 Arquivo: {arquivo_path}")
        print(f"{'='*80}\n")
        
        return arquivo_path
        
    except Exception as e:
        print(f"❌ ERRO ao gerar boleto: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def gerar_codigo_barras(boleto):
    """
    Gera número simulado de código de barras para exibição
    Formato: banco-nosso_numero-vencimento-valor (simplificado)
    """
    try:
        # Formato: BANCO AGENCIA CONTA NOSSO_NUMERO
        banco = str(boleto.fatura.cliente.id).zfill(5)
        nosso = str(boleto.nosso_numero).zfill(10)
        valor = str(int(boleto.valor * 100)).zfill(10)
        
        codigo = f"{banco} {nosso} {valor}"
        return codigo
    except:
        return "00000 0000000000 0000000000"


def atualizar_relatorio_financeiro(cliente):
    """
    Atualiza/cria relatório financeiro do cliente
    """
    try:
        from django.db.models import Sum
        from .models import RelatorioFinanceiro
        
        # Obter ou criar relatório
        relatorio, criado = RelatorioFinanceiro.objects.get_or_create(cliente=cliente)
        
        # Calcular totais de consultorias
        total_consultorias = Consultoria.objects.filter(
            cliente=cliente,
            status='ATIVO'
        ).aggregate(total=Sum('quantidade_meses') * Sum('valor_unitario'))['total'] or 0
        
        # Calcular totais de alugueis
        total_alugueis = AluguelIPv4.objects.filter(
            cliente=cliente,
            status='ATIVO'
        ).aggregate(total=Sum('valor_mensal'))['total'] or 0
        
        # Calcular totais de faturas
        from .models import Fatura, Pagamento
        total_faturas = Fatura.objects.filter(
            cliente=cliente
        ).aggregate(total=Sum('valor_total'))['total'] or 0
        
        total_pago = Pagamento.objects.filter(
            fatura__cliente=cliente
        ).aggregate(total=Sum('valor'))['total'] or 0
        
        # Atualizar relatório
        relatorio.total_consultorias = total_consultorias
        relatorio.total_alugueis = total_alugueis
        relatorio.total_faturas = total_faturas
        relatorio.total_pago = total_pago
        relatorio.saldo_aberto = total_faturas - total_pago
        
        # Contar faturas por status
        relatorio.faturas_abertas = Fatura.objects.filter(
            cliente=cliente,
            status='ABERTA'
        ).count()
        
        relatorio.faturas_vencidas = Fatura.objects.filter(
            cliente=cliente,
            status='ABERTA',
            data_vencimento__lt=date.today()
        ).count()
        
        relatorio.faturas_pagas = Fatura.objects.filter(
            cliente=cliente,
            status='PAGA'
        ).count()
        
        relatorio.save()
        
        return relatorio
        
    except Exception as e:
        print(f"❌ Erro ao atualizar relatório: {str(e)}")
        return None


def enviar_boleto_email(boleto, email_destino=None):
    """
    Envia boleto por email para o cliente
    
    Requer: pip install django-anymail (ou usar EMAIL_BACKEND padrão)
    """
    try:
        from django.core.mail import EmailMessage
        from django.template.loader import render_to_string
        
        if not email_destino:
            email_destino = boleto.cliente.email
        
        # Gerar HTML do email
        contexto = {
            'boleto': boleto,
            'cliente': boleto.cliente,
            'empresa': ConfiguracaoFinanceira.objects.first(),
        }
        
        html_message = render_to_string('financeiro/emails/boleto.html', contexto)
        
        # Criar email
        email = EmailMessage(
            subject=f'Boleto para pagamento - {boleto.numero_boleto}',
            body='Segue em anexo o boleto para pagamento.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email_destino],
        )
        
        email.attach_file(boleto.arquivo_pdf.path)
        email.attach_alternative(html_message, "text/html")
        
        # Enviar
        email.send(fail_silently=False)
        
        return True
        
    except Exception as e:
        print(f"❌ Erro ao enviar email: {str(e)}")
        return False


# Importar modelos ao final para evitar circular imports
from .models import Consultoria, AluguelIPv4, ConfiguracaoFinanceira
