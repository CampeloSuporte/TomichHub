from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.db.models import Q, Sum, Count, F, Avg, Exists, OuterRef
from django.utils import timezone
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
import json
import traceback
import calendar
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, Image
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch, cm
from reportlab.lib import colors
from io import BytesIO
from django.core.files.base import ContentFile
import datetime as dt

from clientes.models import Cliente, BlocoIP
from .models import Consultoria, AluguelIPv4, Fatura, ConfiguracaoFinanceira, Pagamento, Despesa
from .decorators import acesso_financeiro_restrito
from django.db import transaction
from django.db.models.functions import Substr, Cast

# ============================================
# VIEWS RENDERIZADAS
# ============================================

@login_required
@acesso_financeiro_restrito
def dashboard_financeiro(request):
    cliente = None
    consultorias = []
    alugueis = []
    total_consultorias = 0
    total_alugueis = 0
    total_pago = 0
    saldo_aberto = 0

    cliente_id = request.GET.get('cliente_id')

    if cliente_id:
        cliente = get_object_or_404(Cliente, id=cliente_id)
        consultorias = Consultoria.objects.filter(cliente=cliente)
        total_consultorias = consultorias.aggregate(total=Sum('valor_unitario'))['total'] or 0
        alugueis = AluguelIPv4.objects.filter(cliente=cliente)
        total_alugueis = alugueis.aggregate(total=Sum('valor_mensal'))['total'] or 0
        faturas_pagas = Fatura.objects.filter(cliente=cliente, status='PAGA')
        total_pago = faturas_pagas.aggregate(total=Sum('valor_total'))['total'] or 0
        faturas_abertas = Fatura.objects.filter(cliente=cliente, status='ABERTA')
        saldo_aberto = faturas_abertas.aggregate(total=Sum('valor_total'))['total'] or 0

    context = {
        'cliente': cliente,
        'consultorias': consultorias,
        'alugueis': alugueis,
        'total_consultorias': total_consultorias,
        'total_alugueis': total_alugueis,
        'total_pago': total_pago,
        'saldo_aberto': saldo_aberto,
        'blocos_ip': BlocoIP.objects.all(),
    }

    return render(request, 'financeiro/dashboard.html', context)


# ============================================
# API: DASHBOARD INICIAL
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_dashboard_financeiro(request):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        hoje = date.today()
        inicio_mes = hoje.replace(day=1)
        mes_passado_inicio = (inicio_mes - timedelta(days=1)).replace(day=1)

        # ===== SALDO EM ABERTO (VENCIDO) =====
        faturas_vencidas_abertas = Fatura.objects.filter(
            status='ABERTA',
            data_vencimento__lte=hoje
        )
        saldo_em_aberto_vencido = faturas_vencidas_abertas.aggregate(
            total=Sum('valor_total')
        )['total'] or 0
        faturas_vencidas_count = faturas_vencidas_abertas.count()

        # ===== PROSPECÇÃO (CONTRATOS FUTUROS) =====
        faturas_futuras_abertas = Fatura.objects.filter(
            status='ABERTA',
            data_vencimento__gt=hoje
        )
        prospeccao_valor = faturas_futuras_abertas.aggregate(
            total=Sum('valor_total')
        )['total'] or 0
        faturas_futuras_count = faturas_futuras_abertas.count()

        # ===== FATURAMENTO DO MÊS =====
        faturas_mes_pagas = Fatura.objects.filter(
            data_pagamento__gte=inicio_mes,
            data_pagamento__lte=hoje,
            status='PAGA'
        )
        total_faturamento = faturas_mes_pagas.aggregate(
            total=Sum('valor_total')
        )['total'] or 0
        total_faturas_pagas = faturas_mes_pagas.count()

        # ===== FATURAMENTO MÊS PASSADO =====
        faturas_mes_passado = Fatura.objects.filter(
            data_pagamento__gte=mes_passado_inicio,
            data_pagamento__lt=inicio_mes,
            status='PAGA'
        )
        faturamento_mes_passado = faturas_mes_passado.aggregate(
            total=Sum('valor_total')
        )['total'] or 0

        # ===== VARIAÇÃO % =====
        variacao_percentual = 0
        if faturamento_mes_passado > 0:
            variacao_percentual = ((total_faturamento - faturamento_mes_passado) / faturamento_mes_passado) * 100

        # ===== TICKET MÉDIO =====
        ticket_medio = (total_faturamento / total_faturas_pagas) if total_faturas_pagas > 0 else 0

        # ===== CLIENTES COM BOLETO VENCIDO =====
        # Usa Exists() + OuterRef() em vez de faturas__campo
        # Isso funciona independente do related_name definido no model Fatura
        subquery_vencida = Fatura.objects.filter(
            cliente=OuterRef('pk'),
            status='ABERTA',
            data_vencimento__lte=hoje
        )
        clientes_com_boleto_vencido = Cliente.objects.filter(
            Exists(subquery_vencida)
        )

        clientes_abertos_list = []
        for cliente in clientes_com_boleto_vencido:
            faturas_cliente = Fatura.objects.filter(
                cliente=cliente,
                status='ABERTA',
                data_vencimento__lte=hoje
            )
            boletos_vencidos = faturas_cliente.count()
            valor_total_vencido = faturas_cliente.aggregate(
                total=Sum('valor_total')
            )['total'] or 0

            primeira_fatura = faturas_cliente.order_by('data_vencimento').first()
            dias_atraso = (hoje - primeira_fatura.data_vencimento).days if primeira_fatura else 0

            clientes_abertos_list.append({
                'id': cliente.id,
                'nome_empresa': cliente.nome_empresa,
                'cnpj': cliente.cnpj,
                'boletos_abertos': boletos_vencidos,
                'valor_total': float(valor_total_vencido),
                'dias_atraso': dias_atraso,
            })

        clientes_abertos_list.sort(key=lambda x: x['valor_total'], reverse=True)

        return JsonResponse({
            'sucesso': True,
            'total_em_aberto': float(saldo_em_aberto_vencido),
            'faturas_vencidas': faturas_vencidas_count,
            'clientes_devendo': len(clientes_abertos_list),
            'prospeccao_valor': float(prospeccao_valor),
            'faturas_futuras': faturas_futuras_count,
            'total_faturamento': float(total_faturamento),
            'total_faturas': total_faturas_pagas,
            'faturamento_mes_passado': float(faturamento_mes_passado),
            'variacao_percentual': round(float(variacao_percentual), 1),
            'ticket_medio': float(ticket_medio),
            'clientes_abertos': clientes_abertos_list,
        })

    except Exception as e:
        print(f"❌ Erro em api_dashboard_financeiro: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: FATURAMENTO POR MÊS
# ============================================

@login_required
@require_http_methods(["GET"])
def api_faturamento_por_mes(request):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        ano_atual = datetime.now().year
        meses_nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
                       'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']

        valores_pagos = []
        valores_abertos = []

        for mes in range(1, 13):
            faturamento_pago = Fatura.objects.filter(
                data_pagamento__year=ano_atual,
                data_pagamento__month=mes,
                status='PAGA'
            ).aggregate(total=Sum('valor_total'))['total'] or 0

            data_inicio = date(ano_atual, mes, 1)
            ultimo_dia = calendar.monthrange(ano_atual, mes)[1]
            data_fim = date(ano_atual, mes, ultimo_dia)

            faturamento_aberto = Fatura.objects.filter(
                data_emissao__gte=data_inicio,
                data_emissao__lte=data_fim,
                status='ABERTA'
            ).aggregate(total=Sum('valor_total'))['total'] or 0

            valores_pagos.append(float(faturamento_pago))
            valores_abertos.append(float(faturamento_aberto))

        return JsonResponse({
            'sucesso': True,
            'ano': ano_atual,
            'meses': meses_nomes,
            'valores_pagos': valores_pagos,
            'valores_abertos': valores_abertos,
            'total_anual': sum(valores_pagos),
        })

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: RESUMO FINANCEIRO DO CLIENTE
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_resumo_cliente(request):
    try:
        cliente_id = request.GET.get('cliente_id')
        if not cliente_id:
            return JsonResponse({'sucesso': False, 'erro': 'cliente_id obrigatório'}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        hoje = date.today()

        total_consultorias = Fatura.objects.filter(
            cliente=cliente, tipo='CONSULTORIA'
        ).aggregate(total=Sum('valor_total'))['total'] or 0

        total_alugueis = Fatura.objects.filter(
            cliente=cliente, tipo='ALUGUEL_IPV4'
        ).aggregate(total=Sum('valor_total'))['total'] or 0

        total_vendas = Fatura.objects.filter(
            cliente=cliente, tipo='VENDA_EQUIPAMENTO'
        ).aggregate(total=Sum('valor_total'))['total'] or 0

        total_pago = Fatura.objects.filter(
            cliente=cliente, status='PAGA'
        ).aggregate(total=Sum('valor_total'))['total'] or 0

        total_vencido = Fatura.objects.filter(
            cliente=cliente, status='ABERTA', data_vencimento__lte=hoje
        ).aggregate(total=Sum('valor_total'))['total'] or 0

        total_a_vencer = Fatura.objects.filter(
            cliente=cliente, status='ABERTA', data_vencimento__gt=hoje
        ).aggregate(total=Sum('valor_total'))['total'] or 0

        fatura_mais_antiga = Fatura.objects.filter(
            cliente=cliente, status='ABERTA', data_vencimento__lte=hoje
        ).order_by('data_vencimento').first()

        maior_atraso_dias = (hoje - fatura_mais_antiga.data_vencimento).days if fatura_mais_antiga else 0

        return JsonResponse({
            'sucesso': True,
            'resumo': {
                'total_consultorias': float(total_consultorias),
                'total_alugueis': float(total_alugueis),
                'total_vendas': float(total_vendas),
                'total_pago': float(total_pago),
                'total_vencido': float(total_vencido),
                'total_a_vencer': float(total_a_vencer),
                'maior_atraso_dias': maior_atraso_dias,
                'count_faturas_pagas': Fatura.objects.filter(cliente=cliente, status='PAGA').count(),
                'count_faturas_abertas': Fatura.objects.filter(cliente=cliente, status='ABERTA').count(),
            }
        })

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: PESQUISAR CLIENTES
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_pesquisar_clientes(request):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        termo = request.GET.get('termo', '').strip()

        if len(termo) < 2:
            return JsonResponse({'sucesso': True, 'total': 0, 'clientes': []})

        clientes = Cliente.objects.filter(
            Q(nome_empresa__icontains=termo) |
            Q(cnpj__icontains=termo) |
            Q(email__icontains=termo)
        )[:20]

        resultado = []
        hoje = date.today()

        for cliente in clientes:
            boletos_abertos = Fatura.objects.filter(
                cliente=cliente, status='ABERTA', data_vencimento__lte=hoje
            ).count()
            valor_aberto = Fatura.objects.filter(
                cliente=cliente, status='ABERTA', data_vencimento__lte=hoje
            ).aggregate(total=Sum('valor_total'))['total'] or 0

            resultado.append({
                'id': cliente.id,
                'nome_empresa': cliente.nome_empresa,
                'cnpj': cliente.cnpj,
                'email': cliente.email,
                'boletos_abertos': boletos_abertos,
                'valor_aberto': float(valor_aberto),
            })

        return JsonResponse({'sucesso': True, 'total': len(resultado), 'clientes': resultado})

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: VISUALIZAR FATURA
# ============================================

@login_required
@require_http_methods(["GET"])
def api_visualizar_fatura(request, fatura_id):
    try:
        fatura = Fatura.objects.get(id=fatura_id)

        # Verificar privacidade: staff vê tudo, usuários veem apenas públicas
        if fatura.privada and not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        pagamentos = Pagamento.objects.filter(fatura=fatura)
        total_pago = pagamentos.aggregate(Sum('valor'))['valor__sum'] or 0
        hoje = date.today()

        data = {
            'sucesso': True,
            'fatura': {
                'id': fatura.id,
                'numero_fatura': fatura.numero_fatura,
                'status': fatura.status,
                'tipo': fatura.tipo,
                'valor_total': float(fatura.valor_total),
                'data_vencimento': fatura.data_vencimento.strftime('%d/%m/%Y'),
                'data_criacao': fatura.data_emissao.strftime('%d/%m/%Y'),
                'vencida': fatura.data_vencimento < hoje and fatura.status == 'ABERTA',
                'dias_vencido': (hoje - fatura.data_vencimento).days if fatura.data_vencimento < hoje else 0,
                'cliente': {
                    'id': fatura.cliente.id,
                    'nome': fatura.cliente.nome_empresa,
                    'cnpj': fatura.cliente.cnpj,
                    'email': fatura.cliente.email,
                    'telefone': fatura.cliente.telefone or '',
                },
                'itens': [],
                'total_pago': float(total_pago),
                'saldo': float(fatura.valor_total - total_pago),
            }
        }

        for consultoria in fatura.consultorias.all():
            data['fatura']['itens'].append({
                'id': consultoria.id,
                'descricao': consultoria.descricao,
                'tipo': 'CONSULTORIA',
                'valor': float(consultoria.valor_unitario),
            })

        for aluguel in fatura.alugueis_ipv4.all():
            data['fatura']['itens'].append({
                'id': aluguel.id,
                'descricao': aluguel.bloco_descricao,
                'tipo': 'ALUGUEL_IPV4',
                'valor': float(aluguel.valor_mensal),
            })

        if hasattr(fatura, 'vendas_equipamentos'):
            for venda in fatura.vendas_equipamentos.all():
                data['fatura']['itens'].append({
                    'id': venda.id,
                    'descricao': venda.descricao,
                    'tipo': 'VENDA_EQUIPAMENTO',
                    'valor': float(venda.get_valor_parcela()),
                })

        return JsonResponse(data)

    except Fatura.DoesNotExist:
        return JsonResponse({'sucesso': False, 'erro': f'Fatura {fatura_id} não encontrada'}, status=404)
    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# HELPERS
# ============================================
def gerar_numero_fatura_unico():
    try:
        with transaction.atomic():
            ano_mes = datetime.now().strftime('%Y%m')
            prefixo = f"FAT{ano_mes}"

            ultima_fatura = (
                Fatura.objects.filter(numero_fatura__startswith=prefixo)
                .select_for_update()
                .order_by('-numero_fatura')
                .first()
            )

            if ultima_fatura:
                try:
                    ultimo_numero = int(ultima_fatura.numero_fatura[-5:])
                    novo_numero = ultimo_numero + 1
                except (ValueError, IndexError):
                    novo_numero = 1
            else:
                novo_numero = 1

            return f"{prefixo}{novo_numero:05d}"

    except Exception as e:
        print(f"❌ Erro ao gerar número de fatura: {str(e)}")
        raise

    
def gerar_numero_recibo_unico():
    """
    Gera número de recibo único com lock (thread-safe).
    Usa select_for_update() para evitar race conditions.
    
    Retorna: 'REC00000001', 'REC00000002', etc.
    """
    try:
        with transaction.atomic():
            prefixo = 'REC'

            ultimo_recibo = (
                Pagamento.objects.filter(numero_recibo__startswith=prefixo)
                .select_for_update()  # ← LOCK na base de dados
                .order_by('-numero_recibo')
                .first()
            )

            if ultimo_recibo:
                try:
                    # Extrai os últimos 8 dígitos (REC + 8 dígitos)
                    ultimo_numero = int(ultimo_recibo.numero_recibo[-8:])
                    novo_numero = ultimo_numero + 1
                except (ValueError, IndexError):
                    novo_numero = 1
            else:
                novo_numero = 1

            return f"{prefixo}{novo_numero:08d}"

    except Exception as e:
        print(f"❌ Erro ao gerar número de recibo: {str(e)}")
        raise


# ============================================
# API: CONSULTORIAS - CRIAR
# ============================================

@login_required
@require_http_methods(["POST"])
def api_criar_consultoria(request):
    try:
        cliente_id = request.POST.get('cliente_id')
        descricao = request.POST.get('descricao')
        valor_unitario = float(request.POST.get('valor_unitario'))
        quantidade_meses = int(request.POST.get('quantidade_meses', 1))
        periodicidade = request.POST.get('periodicidade', 'MENSAL')
        data_inicio_str = request.POST.get('data_inicio')

        if not cliente_id or not descricao or not valor_unitario or not data_inicio_str:
            return JsonResponse({'sucesso': False, 'erro': 'Preencha todos os campos obrigatórios'}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()

        consultoria = Consultoria.objects.create(
            cliente=cliente,
            descricao=descricao,
            valor_unitario=valor_unitario,
            quantidade_meses=quantidade_meses,
            periodicidade=periodicidade,
            data_inicio=data_inicio
        )

        faturas_geradas = gerar_faturas_consultoria(consultoria)

        return JsonResponse({
            'sucesso': True,
            'mensagem': f'Consultoria criada! {len(faturas_geradas)} fatura(s) gerada(s).',
            'consultoria': {
                'id': consultoria.id,
                'descricao': consultoria.descricao,
                'quantidade_meses': consultoria.quantidade_meses,
            },
            'faturas': faturas_geradas
        })

    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


def gerar_faturas_consultoria(consultoria):
    faturas = []
    data_inicio = consultoria.data_inicio

    for mes in range(consultoria.quantidade_meses):
        # Vencimento preserva o dia de data_inicio
        # Ex: início 15/02 → vencimentos: 15/03, 15/04, 15/05...
        data_vencimento = data_inicio + relativedelta(months=mes + 1)
        numero_fatura = gerar_numero_fatura_unico()

        fatura = Fatura.objects.create(
            cliente=consultoria.cliente,
            numero_fatura=numero_fatura,
            tipo='CONSULTORIA',
            valor_total=consultoria.valor_unitario,
            data_vencimento=data_vencimento,
            status='ABERTA'
        )
        fatura.consultorias.add(consultoria)

        faturas.append({
            'numero_fatura': fatura.numero_fatura,
            'valor': float(fatura.valor_total),
            'vencimento': fatura.data_vencimento.strftime('%d/%m/%Y'),
            'mes': mes + 1,
        })

    return faturas


# ============================================
# API: CONSULTORIAS - EDITAR
# ============================================

@login_required
@require_http_methods(["POST"])
def api_editar_consultoria(request, consultoria_id):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        consultoria = get_object_or_404(Consultoria, id=consultoria_id)
        descricao       = request.POST.get('descricao',       consultoria.descricao)
        valor_unitario  = float(request.POST.get('valor_unitario', consultoria.valor_unitario))
        periodicidade   = request.POST.get('periodicidade',   consultoria.periodicidade)
        data_inicio_str = request.POST.get('data_inicio', '').strip()

        consultoria.descricao      = descricao
        consultoria.valor_unitario = valor_unitario
        consultoria.periodicidade  = periodicidade

        faturas_atualizadas = 0

        if data_inicio_str:
            nova_data = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
            if nova_data != consultoria.data_inicio:
                consultoria.data_inicio = nova_data

                faturas = list(
                    Fatura.objects.filter(consultorias=consultoria)
                    .order_by('data_vencimento')
                )
                for i, fatura in enumerate(faturas):
                    novo_vencimento = nova_data + relativedelta(months=i + 1)
                    if fatura.data_vencimento != novo_vencimento:
                        fatura.data_vencimento = novo_vencimento
                        fatura.save(update_fields=['data_vencimento'])
                        faturas_atualizadas += 1

        consultoria.save()

        return JsonResponse({
            'sucesso': True,
            'mensagem': 'Consultoria atualizada com sucesso!',
            'faturas_atualizadas': faturas_atualizadas,
            'consultoria': {
                'id': consultoria.id,
                'descricao': consultoria.descricao,
                'valor_unitario': float(consultoria.valor_unitario),
                'data_inicio': consultoria.data_inicio.strftime('%Y-%m-%d'),
            }
        })

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)

# ============================================
# API: CONSULTORIAS - DELETAR
# ============================================

@login_required
@require_http_methods(["DELETE"])
def api_deletar_consultoria(request, consultoria_id):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        consultoria = get_object_or_404(Consultoria, id=consultoria_id)
        consultoria.delete()

        return JsonResponse({'sucesso': True, 'mensagem': 'Consultoria deletada com sucesso!'})

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: CONSULTORIAS - LISTAR
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_listar_consultorias(request):
    try:
        cliente_id = request.GET.get('cliente_id')
        if not cliente_id:
            return JsonResponse({'sucesso': False, 'erro': 'cliente_id obrigatório'}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        consultorias = Consultoria.objects.filter(cliente=cliente).order_by('-data_inicio')

        html = ''
        total_valor = 0

        if not consultorias.exists():
            html = '<div class="alert alert-info text-center"><i class="fas fa-info-circle me-2"></i>Nenhuma consultoria cadastrada. Clique em "Nova Consultoria" para adicionar.</div>'
        else:
            for c in consultorias:
                total_valor += float(c.valor_unitario)
                data_inicio_iso = c.data_inicio.strftime('%Y-%m-%d')
                html += f'''
                <div class="card mb-3 border-primary">
                    <div class="card-body">
                        <div class="row align-items-center">
                            <div class="col-md-7">
                                <h6 class="card-title mb-1"><i class="fas fa-briefcase text-primary me-2"></i>{c.descricao}</h6>
                                <small class="text-muted">
                                    {c.quantidade_meses}x {c.periodicidade.title()} | 
                                    Início: {c.data_inicio.strftime('%d/%m/%Y')}
                                </small>
                            </div>
                            <div class="col-md-3 text-center">
                                <strong class="text-success fs-5">R$ {float(c.valor_unitario):.2f}</strong>
                                <div><small class="text-muted">por mês</small></div>
                            </div>
                            <div class="col-md-2 text-end">
                                <button class="btn btn-sm btn-outline-warning me-1"
                                    onclick="abrirEditarConsultoria({c.id}, '{c.descricao}', {float(c.valor_unitario)}, '{c.periodicidade}', '{data_inicio_iso}')"
                                    title="Editar">
                                    <i class="fas fa-edit"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-danger"
                                    onclick="confirmarDeletarConsultoria({c.id}, '{c.descricao}')"
                                    title="Deletar">
                                    <i class="fas fa-trash"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
                '''

        return JsonResponse({
            'sucesso': True,
            'total': consultorias.count(),
            'total_valor': float(total_valor),
            'html': html
        })

    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: ALUGUEIS IPv4 - CRIAR
# ============================================

@login_required
@require_http_methods(["POST"])
def api_criar_aluguel_ipv4(request):
    try:
        cliente_id = request.POST.get('cliente_id')
        bloco_descricao = request.POST.get('bloco_descricao')
        quantidade_ips = int(request.POST.get('quantidade_ips', 1))
        valor_mensal = float(request.POST.get('valor_mensal'))
        data_inicio_str = request.POST.get('data_inicio')
        bloco_id = request.POST.get('bloco_ip')
        quantidade_meses = int(request.POST.get('quantidade_meses', 1))

        if not cliente_id or not bloco_descricao or not valor_mensal or not data_inicio_str:
            return JsonResponse({'sucesso': False, 'erro': 'Preencha todos os campos obrigatórios'}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()

        bloco_ip = None
        if bloco_id:
            bloco_ip = get_object_or_404(BlocoIP, id=bloco_id)

        aluguel = AluguelIPv4.objects.create(
            cliente=cliente,
            bloco_ip=bloco_ip,
            bloco_descricao=bloco_descricao,
            quantidade_ips=quantidade_ips,
            valor_mensal=valor_mensal,
            data_inicio=data_inicio,
        )

        faturas_geradas = gerar_faturas_aluguel_ipv4(aluguel, quantidade_meses)

        return JsonResponse({
            'sucesso': True,
            'mensagem': f'Aluguel IPv4 criado! {len(faturas_geradas)} fatura(s) gerada(s).',
            'aluguel': {
                'id': aluguel.id,
                'bloco_descricao': aluguel.bloco_descricao,
                'valor_mensal': float(aluguel.valor_mensal),
            },
            'faturas': faturas_geradas
        })

    except ValueError as e:
        return JsonResponse({'sucesso': False, 'erro': f'Erro de validação: {str(e)}'}, status=400)
    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


def gerar_faturas_aluguel_ipv4(aluguel, quantidade_meses=1):
    print(f"\n📋 gerar_faturas_aluguel_ipv4: {aluguel.bloco_descricao} x{quantidade_meses}")
    faturas = []
    data_inicio = aluguel.data_inicio

    for mes in range(quantidade_meses):
        # Vencimento preserva o dia de data_inicio
        # Ex: início 10/02 → vencimentos: 10/03, 10/04, 10/05...
        data_vencimento = data_inicio + relativedelta(months=mes + 1)
        numero_fatura = gerar_numero_fatura_unico()

        fatura = Fatura.objects.create(
            cliente=aluguel.cliente,
            numero_fatura=numero_fatura,
            tipo='ALUGUEL_IPV4',
            valor_total=aluguel.valor_mensal,
            data_vencimento=data_vencimento,
            status='ABERTA'
        )
        fatura.alugueis_ipv4.add(aluguel)

        faturas.append({
            'numero_fatura': fatura.numero_fatura,
            'valor': float(fatura.valor_total),
            'vencimento': fatura.data_vencimento.strftime('%d/%m/%Y'),
            'mes': mes + 1,
        })

    return faturas


# ============================================
# API: ALUGUEIS - EDITAR
# ============================================

@login_required
@require_http_methods(["POST"])
def api_editar_aluguel(request, aluguel_id):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        aluguel = get_object_or_404(AluguelIPv4, id=aluguel_id)
        aluguel.bloco_descricao = request.POST.get('bloco_descricao', aluguel.bloco_descricao)
        aluguel.valor_mensal    = float(request.POST.get('valor_mensal',   aluguel.valor_mensal))
        aluguel.quantidade_ips  = int(request.POST.get('quantidade_ips',   aluguel.quantidade_ips))
        data_inicio_str = request.POST.get('data_inicio', '').strip()

        faturas_atualizadas = 0

        if data_inicio_str:
            nova_data = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
            if nova_data != aluguel.data_inicio:
                aluguel.data_inicio = nova_data

                faturas = list(
                    Fatura.objects.filter(alugueis_ipv4=aluguel)
                    .order_by('data_vencimento')
                )
                for i, fatura in enumerate(faturas):
                    novo_vencimento = nova_data + relativedelta(months=i + 1)
                    if fatura.data_vencimento != novo_vencimento:
                        fatura.data_vencimento = novo_vencimento
                        fatura.save(update_fields=['data_vencimento'])
                        faturas_atualizadas += 1

        aluguel.save()

        return JsonResponse({
            'sucesso': True,
            'mensagem': 'Aluguel atualizado com sucesso!',
            'faturas_atualizadas': faturas_atualizadas,
            'data_inicio': aluguel.data_inicio.strftime('%Y-%m-%d'),
        })

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)

# ============================================
# API: ALUGUEIS - DELETAR
# ============================================

@login_required
@require_http_methods(["DELETE"])
def api_deletar_aluguel(request, aluguel_id):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        aluguel = get_object_or_404(AluguelIPv4, id=aluguel_id)
        aluguel.delete()

        return JsonResponse({'sucesso': True, 'mensagem': 'Aluguel deletado com sucesso!'})

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: ALUGUEIS - LISTAR
# ============================================
@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_listar_alugueis(request):
    try:
        cliente_id = request.GET.get('cliente_id')
        if not cliente_id:
            return JsonResponse({'sucesso': False, 'erro': 'cliente_id obrigatório'}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        alugueis = AluguelIPv4.objects.filter(cliente=cliente).order_by('-data_inicio')

        html = ''
        total_valor_mensal = 0

        if not alugueis.exists():
            html = '<div class="alert alert-info text-center"><i class="fas fa-info-circle me-2"></i>Nenhum aluguel IPv4 cadastrado.</div>'
        else:
            for a in alugueis:
                total_valor_mensal += float(a.valor_mensal)
                data_inicio_iso = a.data_inicio.strftime('%Y-%m-%d')
                html += f'''
                <div class="card mb-3 border-info">
                    <div class="card-body">
                        <div class="row align-items-center">
                            <div class="col-md-7">
                                <h6 class="card-title mb-1"><i class="fas fa-network-wired text-info me-2"></i>{a.bloco_descricao}</h6>
                                <small class="text-muted">
                                    {a.quantidade_ips} IPs | Início: {a.data_inicio.strftime('%d/%m/%Y')}
                                </small>
                            </div>
                            <div class="col-md-3 text-center">
                                <strong class="text-info fs-5">R$ {float(a.valor_mensal):.2f}</strong>
                                <div><small class="text-muted">/mês</small></div>
                            </div>
                            <div class="col-md-2 text-end">
                                <button class="btn btn-sm btn-outline-warning me-1"
                                    onclick="abrirEditarAluguel({a.id}, '{a.bloco_descricao}', {float(a.valor_mensal)}, {a.quantidade_ips}, '{data_inicio_iso}')"
                                    title="Editar">
                                    <i class="fas fa-edit"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-danger"
                                    onclick="confirmarDeletarAluguel({a.id}, '{a.bloco_descricao}')"
                                    title="Deletar">
                                    <i class="fas fa-trash"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
                '''

        return JsonResponse({
            'sucesso': True,
            'total': alugueis.count(),
            'total_valor_mensal': float(total_valor_mensal),
            'html': html
        })

    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: FATURAS - CRIAR
# ============================================

@login_required
@require_http_methods(["POST"])
def api_criar_fatura(request):
    try:
        cliente_id = request.POST.get('cliente_id')
        tipo = request.POST.get('tipo')
        data_vencimento_str = request.POST.get('data_vencimento')
        privada = request.POST.get('privada') == 'on' or request.POST.get('privada') == 'true'

        if not cliente_id or not tipo or not data_vencimento_str:
            return JsonResponse({'sucesso': False, 'erro': 'Preencha todos os campos obrigatórios'}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        data_vencimento = datetime.strptime(data_vencimento_str, '%Y-%m-%d').date()

        fatura = Fatura.objects.create(
            cliente=cliente,
            numero_fatura=gerar_numero_fatura_unico(),
            tipo=tipo,
            data_vencimento=data_vencimento,
            status='ABERTA',
            privada=privada
        )

        return JsonResponse({
            'sucesso': True,
            'mensagem': 'Fatura criada com sucesso!',
            'fatura': {'id': fatura.id, 'numero_fatura': fatura.numero_fatura}
        })

    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: FATURAS - DELETAR
# ============================================

@login_required
@require_http_methods(["DELETE"])
def api_deletar_fatura(request, fatura_id):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        fatura = get_object_or_404(Fatura, id=fatura_id)

        if fatura.status == 'PAGA':
            return JsonResponse({
                'sucesso': False,
                'erro': 'Não é possível deletar uma fatura já paga. Cancele o pagamento primeiro.'
            }, status=400)

        numero = fatura.numero_fatura
        cliente_nome = fatura.cliente.nome_empresa

        pagamentos = Pagamento.objects.filter(fatura=fatura)
        for pag in pagamentos:
            if pag.comprovante:
                pag.comprovante.delete(save=False)
            if pag.comprovante_pdf_gerado:
                pag.comprovante_pdf_gerado.delete(save=False)
        pagamentos.delete()

        fatura.delete()

        return JsonResponse({
            'sucesso': True,
            'mensagem': f'Fatura {numero} deletada com sucesso!',
            'numero_fatura': numero,
            'cliente': cliente_nome,
        })

    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: FATURAS - LISTAR
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_listar_faturas(request):
    try:
        cliente_id = request.GET.get('cliente_id')
        if not cliente_id:
            return JsonResponse({'sucesso': False, 'erro': 'cliente_id obrigatório'}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)

        # Filtrar faturas: mostrar públicas ou privadas se o usuário é staff
        from django.db.models import Q
        if request.user.is_staff:
            faturas = Fatura.objects.filter(cliente=cliente).order_by('-data_emissao')
        else:
            faturas = Fatura.objects.filter(cliente=cliente, privada=False).order_by('-data_emissao')

        hoje = date.today()

        html = ''
        for f in faturas:
            vencida = f.data_vencimento < hoje and f.status == 'ABERTA'
            dias_str = ''
            if vencida:
                dias = (hoje - f.data_vencimento).days
                dias_str = f'<br><small class="text-danger"><i class="fas fa-exclamation-triangle"></i> {dias}d atraso</small>'

            if f.status == 'PAGA':
                status_badge = 'bg-success'
                status_icon = '✅'
            elif vencida:
                status_badge = 'bg-danger'
                status_icon = '⚠️'
            else:
                status_badge = 'bg-warning text-dark'
                status_icon = '⏳'

            tipo_labels = {
                'CONSULTORIA': 'Consultoria',
                'ALUGUEL_IPV4': 'Aluguel IPv4',
                'VENDA_EQUIPAMENTO': 'Equipamento',
                'MISTA': 'Mista',
            }
            tipo_label = tipo_labels.get(f.tipo, f.tipo)

            if f.status == 'PAGA':
                btn_deletar = f'''
                    <button class="btn btn-sm btn-outline-secondary" disabled title="Fatura paga não pode ser deletada">
                        <i class="fas fa-trash"></i>
                    </button>'''
            else:
                btn_deletar = f'''
                    <button class="btn btn-sm btn-outline-danger"
                        onclick="confirmarDeletarFatura({f.id}, '{f.numero_fatura}', 'R$ {float(f.valor_total):.2f}')"
                        title="Deletar fatura">
                        <i class="fas fa-trash"></i>
                    </button>'''

            privada_icon = f' <i class="fas fa-lock" style="color:#b060ff;font-size:.7rem;margin-left:.3rem;" title="Privada"></i>' if f.privada else ''

            html += f'''
            <tr id="row-fatura-{f.id}">
                <td><strong>{f.numero_fatura}{privada_icon}</strong></td>
                <td><small class="badge bg-secondary">{tipo_label}</small></td>
                <td><strong>R$ {float(f.valor_total):.2f}</strong></td>
                <td>{f.data_vencimento.strftime('%d/%m/%Y')}{dias_str}</td>
                <td><span class="badge {status_badge}">{status_icon} {f.status}</span></td>
                <td class="text-nowrap">
                    <button class="btn btn-sm btn-outline-info me-1"
                        onclick="visualizarFatura({f.id})" title="Ver detalhes">
                        <i class="fas fa-eye"></i>
                    </button>
                    {btn_deletar}
                </td>
            </tr>
            '''

        return JsonResponse({'sucesso': True, 'total': faturas.count(), 'html': html})

    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: VENDAS DE EQUIPAMENTO - CRIAR
# ============================================

@login_required
@require_http_methods(["POST"])
def api_criar_venda_equipamento(request):
    try:
        cliente_id = request.POST.get('cliente_id')
        descricao = request.POST.get('descricao')
        valor_total = float(request.POST.get('valor_total'))
        quantidade_parcelas = int(request.POST.get('quantidade_parcelas', 1))
        data_inicio_str = request.POST.get('data_inicio')

        if not cliente_id or not descricao or not valor_total or not data_inicio_str:
            return JsonResponse({'sucesso': False, 'erro': 'Preencha todos os campos obrigatórios'}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()

        from .models import VendaEquipamento
        venda = VendaEquipamento.objects.create(
            cliente=cliente,
            descricao=descricao,
            valor_total=valor_total,
            quantidade_parcelas=quantidade_parcelas,
            data_inicio=data_inicio
        )

        faturas_geradas = gerar_faturas_venda_equipamento(venda)

        return JsonResponse({
            'sucesso': True,
            'mensagem': f'Venda criada! {len(faturas_geradas)} fatura(s) gerada(s).',
            'venda': {
                'id': venda.id,
                'descricao': venda.descricao,
                'valor_total': float(venda.valor_total),
                'quantidade_parcelas': venda.quantidade_parcelas,
                'valor_parcela': float(venda.get_valor_parcela()),
            },
            'faturas': faturas_geradas
        })

    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


def gerar_faturas_venda_equipamento(venda):
    faturas = []
    data_inicio = venda.data_inicio
    valor_parcela = venda.get_valor_parcela()

    for parcela in range(venda.quantidade_parcelas):
        # Vencimento preserva o dia de data_inicio
        # Ex: início 20/02 → vencimentos: 20/03, 20/04, 20/05...
        data_vencimento = data_inicio + relativedelta(months=parcela + 1)
        numero_fatura = gerar_numero_fatura_unico()

        fatura = Fatura.objects.create(
            cliente=venda.cliente,
            numero_fatura=numero_fatura,
            tipo='VENDA_EQUIPAMENTO',
            valor_total=valor_parcela,
            data_vencimento=data_vencimento,
            status='ABERTA'
        )

        if hasattr(fatura, 'vendas_equipamentos'):
            fatura.vendas_equipamentos.add(venda)

        faturas.append({
            'numero_fatura': fatura.numero_fatura,
            'valor': float(fatura.valor_total),
            'vencimento': fatura.data_vencimento.strftime('%d/%m/%Y'),
            'parcela': f'{parcela + 1}/{venda.quantidade_parcelas}',
        })

    return faturas


# ============================================
# API: VENDAS - EDITAR
# ============================================

@login_required
@require_http_methods(["POST"])
def api_editar_venda(request, venda_id):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        from .models import VendaEquipamento
        venda = get_object_or_404(VendaEquipamento, id=venda_id)
        venda.descricao = request.POST.get('descricao', venda.descricao)
        venda.save()

        return JsonResponse({'sucesso': True, 'mensagem': 'Venda atualizada com sucesso!'})

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: VENDAS - DELETAR
# ============================================

@login_required
@require_http_methods(["DELETE"])
def api_deletar_venda(request, venda_id):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        from .models import VendaEquipamento
        venda = get_object_or_404(VendaEquipamento, id=venda_id)
        venda.delete()

        return JsonResponse({'sucesso': True, 'mensagem': 'Venda deletada com sucesso!'})

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: VENDAS - LISTAR
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_listar_vendas_equipamentos(request):
    try:
        cliente_id = request.GET.get('cliente_id')
        if not cliente_id:
            return JsonResponse({'sucesso': False, 'erro': 'cliente_id obrigatório'}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        from .models import VendaEquipamento
        vendas = VendaEquipamento.objects.filter(cliente=cliente).order_by('-data_inicio')

        html = ''
        total_valor = 0

        if not vendas.exists():
            html = '<div class="alert alert-info text-center"><i class="fas fa-info-circle me-2"></i>Nenhuma venda de equipamento cadastrada.</div>'
        else:
            for v in vendas:
                total_valor += float(v.valor_total)
                html += f'''
                <div class="card mb-3 border-success">
                    <div class="card-body">
                        <div class="row align-items-center">
                            <div class="col-md-7">
                                <h6 class="card-title mb-1"><i class="fas fa-shopping-bag text-success me-2"></i>{v.descricao}</h6>
                                <small class="text-muted">
                                    {v.quantidade_parcelas}x de R$ {float(v.get_valor_parcela()):.2f} | Início: {v.data_inicio.strftime('%d/%m/%Y')}
                                </small>
                            </div>
                            <div class="col-md-3 text-center">
                                <strong class="text-success fs-5">R$ {float(v.valor_total):.2f}</strong>
                                <div><small class="text-muted">valor total</small></div>
                            </div>
                            <div class="col-md-2 text-end">
                                <button class="btn btn-sm btn-outline-warning me-1" onclick="abrirEditarVenda({v.id}, '{v.descricao}')" title="Editar">
                                    <i class="fas fa-edit"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-danger" onclick="confirmarDeletarVenda({v.id}, '{v.descricao}')" title="Deletar">
                                    <i class="fas fa-trash"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
                '''

        return JsonResponse({
            'sucesso': True,
            'total': vendas.count(),
            'total_valor': float(total_valor),
            'html': html
        })

    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# HELPER: GERAR PDF DO COMPROVANTE
# ============================================

def gerar_comprovante_pdf(pagamento):
    try:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.5 * inch,
            leftMargin=0.5 * inch,
            topMargin=1 * inch,
            bottomMargin=0.75 * inch,
            title=f"Comprovante {pagamento.numero_recibo}"
        )

        styles = getSampleStyleSheet()
        empresa_style = ParagraphStyle('EmpresaName', parent=styles['Heading1'],
                                       fontSize=16, textColor=colors.HexColor('#1a5490'),
                                       spaceAfter=10, alignment=0, fontName='Helvetica-Bold')
        title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'],
                                     fontSize=14, textColor=colors.HexColor('#333333'),
                                     spaceAfter=20, alignment=0, fontName='Helvetica-Bold')
        footer_style = ParagraphStyle('Footer', parent=styles['Normal'], fontSize=8,
                                      textColor=colors.HexColor('#666666'), alignment=1)

        elements = [
            Paragraph("TOMICH TECNOLOGIA", empresa_style),
            Spacer(1, 0.2 * inch),
            Paragraph("COMPROVANTE DE PAGAMENTO", title_style),
            Spacer(1, 0.1 * inch),
            Paragraph("_" * 80, styles['Normal']),
            Spacer(1, 0.15 * inch),
        ]

        data = [
            ['Número do Recibo:', pagamento.numero_recibo],
            ['Data do Pagamento:', pagamento.data_pagamento.strftime('%d/%m/%Y')],
            ['Tipo de Pagamento:', pagamento.get_tipo_display() if hasattr(pagamento, 'get_tipo_display') else pagamento.tipo],
        ]
        table = Table(data, colWidths=[2 * inch, 3.5 * inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f0f0f0')),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(table)
        elements.append(Spacer(1, 0.2 * inch))

        fatura = pagamento.fatura
        fatura_data = [
            ['Número da Fatura:', fatura.numero_fatura],
            ['Cliente:', fatura.cliente.nome_empresa],
            ['CNPJ:', fatura.cliente.cnpj],
            ['Valor Original:', f'R$ {float(fatura.valor_total):.2f}'],
            ['Valor Pago:', f'R$ {float(pagamento.valor):.2f}'],
        ]
        fatura_table = Table(fatura_data, colWidths=[2 * inch, 3.5 * inch])
        fatura_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f0f0f0')),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(fatura_table)
        elements.append(Spacer(1, 0.3 * inch))

        data_hora = dt.datetime.now().strftime('%d/%m/%Y às %H:%M:%S')
        elements.append(Paragraph(f"Comprovante gerado em {data_hora}", footer_style))

        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes

    except Exception as e:
        print(f"❌ Erro ao gerar PDF: {str(e)}")
        raise


# ============================================
# API: REGISTRAR PAGAMENTO
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["POST"])
def api_registrar_pagamento(request):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        fatura_id = request.POST.get('fatura_id', '').strip()
        data_pagamento_str = request.POST.get('data_pagamento', '').strip()
        valor_pagado_str = request.POST.get('valor_pagado', '').strip()
        observacoes = request.POST.get('observacoes', '').strip()
        comprovante_upload = request.FILES.get('comprovante')

        if not fatura_id or not data_pagamento_str or not valor_pagado_str:
            return JsonResponse({'sucesso': False, 'erro': 'Campos obrigatórios faltando'}, status=400)

        fatura_id = int(fatura_id)
        data_pagamento = datetime.strptime(data_pagamento_str, '%Y-%m-%d').date()
        valor_pagado = float(valor_pagado_str)

        if valor_pagado <= 0:
            return JsonResponse({'sucesso': False, 'erro': 'Valor deve ser maior que zero'}, status=400)

        fatura = Fatura.objects.get(id=fatura_id)

        if valor_pagado > fatura.valor_total:
            return JsonResponse({'sucesso': False,
                                 'erro': f'Valor não pode ser maior que R$ {fatura.valor_total:.2f}'}, status=400)

        ano_mes = datetime.now().strftime('%Y%m')
        contador = Pagamento.objects.filter(numero_recibo__startswith='REC').count() + 1
        numero_recibo = gerar_numero_recibo_unico()

        pagamento = Pagamento.objects.create(
            fatura=fatura,
            numero_recibo=numero_recibo,
            tipo='COMPROVANTE',
            valor=valor_pagado,
            data_pagamento=data_pagamento,
            data_confirmacao=date.today(),
            criado_por=request.user,
            observacoes=observacoes
        )

        if comprovante_upload:
            pagamento.comprovante = comprovante_upload
            pagamento.save()

        try:
            pdf_bytes = gerar_comprovante_pdf(pagamento)
            nome_arquivo = f"comprovante_{pagamento.numero_recibo}.pdf"
            pagamento.comprovante_pdf_gerado = ContentFile(pdf_bytes, name=nome_arquivo)
            pagamento.save()
        except Exception as e:
            print(f"⚠️ Erro ao gerar PDF: {e}")

        if valor_pagado >= fatura.valor_total:
            fatura.status = 'PAGA'
            fatura.data_pagamento = data_pagamento
            fatura.save()

        return JsonResponse({
            'sucesso': True,
            'mensagem': f'Pagamento registrado! Recibo: {numero_recibo}',
            'pagamento': {
                'id': pagamento.id,
                'numero_recibo': pagamento.numero_recibo,
                'valor': float(pagamento.valor),
                'data_pagamento': pagamento.data_pagamento.strftime('%d/%m/%Y'),
                'fatura_status': fatura.status,
                'comprovante_usuario_url': pagamento.comprovante.url if pagamento.comprovante else None,
                'comprovante_pdf_url': pagamento.comprovante_pdf_gerado.url if pagamento.comprovante_pdf_gerado else None,
                'tem_comprovante_usuario': bool(pagamento.comprovante),
                'tem_pdf_gerado': bool(pagamento.comprovante_pdf_gerado),
            }
        }, status=200)

    except Fatura.DoesNotExist:
        return JsonResponse({'sucesso': False, 'erro': 'Fatura não encontrada'}, status=404)
    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: LISTAR PAGAMENTOS
# ============================================

@login_required
@require_http_methods(["GET"])
def api_listar_pagamentos(request, fatura_id):
    try:
        fatura = Fatura.objects.get(id=fatura_id)
        pagamentos = Pagamento.objects.filter(fatura=fatura).order_by('-data_pagamento')

        total_pago = pagamentos.aggregate(Sum('valor'))['valor__sum'] or 0
        saldo = fatura.valor_total - total_pago

        data = {
            'sucesso': True,
            'fatura_id': fatura.id,
            'total_fatura': float(fatura.valor_total),
            'total_pago': float(total_pago),
            'saldo_fatura': float(saldo),
            'pagamentos': []
        }

        for pag in pagamentos:
            data['pagamentos'].append({
                'id': pag.id,
                'numero_recibo': pag.numero_recibo,
                'data_pagamento': pag.data_pagamento.strftime('%d/%m/%Y'),
                'valor': float(pag.valor),
                'tipo': pag.get_tipo_display(),
                'observacoes': pag.observacoes or '',
                'tem_comprovante_usuario': bool(pag.comprovante),
                'comprovante_usuario_url': pag.comprovante.url if pag.comprovante else None,
                'tem_pdf_gerado': bool(pag.comprovante_pdf_gerado),
                'comprovante_pdf_url': pag.comprovante_pdf_gerado.url if pag.comprovante_pdf_gerado else None,
                'tem_algum_comprovante': bool(pag.comprovante or pag.comprovante_pdf_gerado),
            })

        return JsonResponse(data)

    except Fatura.DoesNotExist:
        return JsonResponse({'sucesso': False, 'erro': 'Fatura não encontrada'}, status=404)
    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: DELETAR PAGAMENTO
# ============================================

@login_required
@require_http_methods(["DELETE"])
def api_deletar_pagamento(request, pagamento_id):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        pagamento = get_object_or_404(Pagamento, id=pagamento_id)
        fatura = pagamento.fatura

        if pagamento.comprovante:
            pagamento.comprovante.delete()
        if pagamento.comprovante_pdf_gerado:
            pagamento.comprovante_pdf_gerado.delete()

        pagamento.delete()

        tem_pagamentos = Pagamento.objects.filter(fatura=fatura).exists()
        if not tem_pagamentos and fatura.status == 'PAGA':
            fatura.status = 'ABERTA'
            fatura.data_pagamento = None
            fatura.save()

        return JsonResponse({
            'sucesso': True,
            'mensagem': 'Pagamento deletado com sucesso!',
            'fatura_status': fatura.status
        })

    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: RELATÓRIO AGING
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_aging_report(request):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        hoje = date.today()
        faturas_abertas = Fatura.objects.filter(
            status='ABERTA',
            data_vencimento__lte=hoje
        ).select_related('cliente')

        faixas = {
            '0_30': {'label': '0-30 dias', 'faturas': [], 'total': 0},
            '31_60': {'label': '31-60 dias', 'faturas': [], 'total': 0},
            '61_90': {'label': '61-90 dias', 'faturas': [], 'total': 0},
            '90_mais': {'label': '90+ dias', 'faturas': [], 'total': 0},
        }

        for fatura in faturas_abertas:
            dias = (hoje - fatura.data_vencimento).days
            item = {
                'id': fatura.id,
                'numero': fatura.numero_fatura,
                'cliente': fatura.cliente.nome_empresa,
                'valor': float(fatura.valor_total),
                'dias': dias,
                'vencimento': fatura.data_vencimento.strftime('%d/%m/%Y'),
            }

            if dias <= 30:
                faixas['0_30']['faturas'].append(item)
                faixas['0_30']['total'] += float(fatura.valor_total)
            elif dias <= 60:
                faixas['31_60']['faturas'].append(item)
                faixas['31_60']['total'] += float(fatura.valor_total)
            elif dias <= 90:
                faixas['61_90']['faturas'].append(item)
                faixas['61_90']['total'] += float(fatura.valor_total)
            else:
                faixas['90_mais']['faturas'].append(item)
                faixas['90_mais']['total'] += float(fatura.valor_total)

        total_geral = sum(f['total'] for f in faixas.values())

        return JsonResponse({
            'sucesso': True,
            'faixas': faixas,
            'total_geral': total_geral,
        })

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: CLIENTES QUE PAGARAM BOLETOS EM ATRASO
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_clientes_pagaram_vencidos(request):
    """
    Retorna clientes que quitaram faturas que estavam vencidas.
    Uma fatura é considerada "paga em atraso" quando:
      data_pagamento > data_vencimento AND status = 'PAGA'

    Parâmetro opcional: ?periodo=30 | 60 | 90 | 365 | 0 (todos)
    Default: últimos 90 dias de pagamento
    """
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        hoje = date.today()
        periodo = int(request.GET.get('periodo', 90))

        # Base: faturas pagas após o vencimento (pagamento atrasado)
        qs = Fatura.objects.filter(
            status='PAGA',
            data_pagamento__isnull=False,
            data_vencimento__isnull=False,
        ).filter(
            data_pagamento__gt=F('data_vencimento')  # pago depois do vencimento
        ).select_related('cliente')

        # Filtro de período (baseado na data do pagamento)
        if periodo > 0:
            data_corte = hoje - timedelta(days=periodo)
            qs = qs.filter(data_pagamento__gte=data_corte)

        # Agrupar por cliente
        clientes_dict = {}
        for fatura in qs.order_by('cliente_id', '-data_pagamento'):
            cid = fatura.cliente.id
            dias_atraso = (fatura.data_pagamento - fatura.data_vencimento).days

            if cid not in clientes_dict:
                clientes_dict[cid] = {
                    'id': cid,
                    'nome_empresa': fatura.cliente.nome_empresa,
                    'cnpj': fatura.cliente.cnpj,
                    'faturas_pagas_atraso': 0,
                    'valor_total_quitado': 0.0,
                    'soma_dias_atraso': 0,
                    'ultimo_pagamento': fatura.data_pagamento,
                    'faturas': [],
                }

            clientes_dict[cid]['faturas_pagas_atraso'] += 1
            clientes_dict[cid]['valor_total_quitado'] += float(fatura.valor_total)
            clientes_dict[cid]['soma_dias_atraso'] += dias_atraso

            # Mantém o pagamento mais recente
            if fatura.data_pagamento > clientes_dict[cid]['ultimo_pagamento']:
                clientes_dict[cid]['ultimo_pagamento'] = fatura.data_pagamento

            clientes_dict[cid]['faturas'].append({
                'numero': fatura.numero_fatura,
                'valor': float(fatura.valor_total),
                'vencimento': fatura.data_vencimento.strftime('%d/%m/%Y'),
                'pago_em': fatura.data_pagamento.strftime('%d/%m/%Y'),
                'dias_atraso': dias_atraso,
            })

        # Montar lista final com média de atraso
        resultado = []
        for c in clientes_dict.values():
            c['media_dias_atraso'] = round(c['soma_dias_atraso'] / c['faturas_pagas_atraso'])
            c['ultimo_pagamento'] = c['ultimo_pagamento'].strftime('%d/%m/%Y')
            del c['soma_dias_atraso']
            resultado.append(c)

        # Ordenar por data de último pagamento (mais recente primeiro)
        resultado.sort(key=lambda x: x['ultimo_pagamento'], reverse=True)

        return JsonResponse({
            'sucesso': True,
            'total_clientes': len(resultado),
            'periodo_dias': periodo,
            'clientes': resultado,
        })

    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: TOP CLIENTES POR RECEITA
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_top_clientes(request):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        top_clientes = (
            Fatura.objects.filter(status='PAGA')
            .values('cliente__id', 'cliente__nome_empresa', 'cliente__cnpj')
            .annotate(total_pago=Sum('valor_total'), count_faturas=Count('id'))
            .order_by('-total_pago')[:10]
        )

        resultado = [
            {
                'id': c['cliente__id'],
                'nome': c['cliente__nome_empresa'],
                'cnpj': c['cliente__cnpj'],
                'total_pago': float(c['total_pago']),
                'count_faturas': c['count_faturas'],
            }
            for c in top_clientes
        ]

        return JsonResponse({'sucesso': True, 'clientes': resultado})

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


# ============================================
# API: PRÓXIMAS FATURAS A VENCER
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_proximas_vencer(request):
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        hoje = date.today()
        dias = int(request.GET.get('dias', 15))
        limite = hoje + timedelta(days=dias)

        faturas = Fatura.objects.filter(
            status='ABERTA',
            data_vencimento__gt=hoje,
            data_vencimento__lte=limite
        ).select_related('cliente').order_by('data_vencimento')[:20]

        resultado = [
            {
                'id': f.id,
                'numero': f.numero_fatura,
                'cliente': f.cliente.nome_empresa,
                'cliente_id': f.cliente.id,
                'valor': float(f.valor_total),
                'vencimento': f.data_vencimento.strftime('%d/%m/%Y'),
                'dias_restantes': (f.data_vencimento - hoje).days,
            }
            for f in faturas
        ]

        return JsonResponse({'sucesso': True, 'faturas': resultado, 'total': len(resultado)})

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)

# ─────────────────────────────────────────────────────────────────────────────
# DESPESAS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@acesso_financeiro_restrito
@require_http_methods(['POST'])
def api_criar_despesa(request):
    try:
        data = json.loads(request.body)
        nome = data.get('nome', '').strip()
        if not nome:
            return JsonResponse({'sucesso': False, 'erro': 'Nome é obrigatório'})
        vencimento = data.get('data_vencimento', '')
        if not vencimento:
            return JsonResponse({'sucesso': False, 'erro': 'Data de vencimento é obrigatória'})
        try:
            valor = float(str(data.get('valor', 0)).replace(',', '.'))
        except (ValueError, TypeError):
            return JsonResponse({'sucesso': False, 'erro': 'Valor inválido'})

        recorrencia = data.get('recorrencia', 'UNICA')
        meses_raw = data.get('meses_recorrencia', None)
        meses_recorrencia = int(meses_raw) if meses_raw and str(meses_raw).isdigit() and recorrencia != 'UNICA' else None
        privada = data.get('privada', False)

        d = Despesa.objects.create(
            nome=nome,
            descricao=data.get('descricao', '').strip(),
            valor=valor,
            categoria=data.get('categoria', 'OUTROS'),
            recorrencia=recorrencia,
            meses_recorrencia=meses_recorrencia,
            ocorrencia_atual=1,
            data_vencimento=vencimento,
            observacoes=data.get('observacoes', '').strip(),
            privada=privada,
            criado_por=request.user,
        )
        return JsonResponse({'sucesso': True, 'id': d.id, 'msg': 'Despesa cadastrada.'})
    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


@login_required
@acesso_financeiro_restrito
@require_http_methods(['GET'])
def api_listar_despesas(request):
    try:
        hoje = timezone.localdate()
        status_filtro = request.GET.get('status', '')
        mes   = request.GET.get('mes', '')
        ano   = request.GET.get('ano', '')

        # Filtrar despesas: mostrar públicas ou privadas do usuário logado
        from django.db.models import Q
        qs = Despesa.objects.filter(
            Q(privada=False) | Q(privada=True, criado_por=request.user)
        )

        if status_filtro == 'PAGO':
            qs = qs.filter(status='PAGO')
        elif status_filtro == 'PENDENTE':
            qs = qs.filter(status='PENDENTE', data_vencimento__gte=hoje)
        elif status_filtro == 'VENCIDO':
            qs = qs.filter(status='PENDENTE', data_vencimento__lt=hoje)
        if mes and ano:
            qs = qs.filter(data_vencimento__month=int(mes), data_vencimento__year=int(ano))
        elif ano:
            qs = qs.filter(data_vencimento__year=int(ano))

        def _item(d):
            dias = (d.data_vencimento - hoje).days
            se = d.status_efetivo
            return {
                'id': d.id,
                'nome': d.nome,
                'descricao': d.descricao,
                'valor': float(d.valor),
                'categoria': d.categoria,
                'categoria_label': d.get_categoria_display(),
                'recorrencia': d.recorrencia,
                'recorrencia_label': d.get_recorrencia_display(),
                'meses_recorrencia': d.meses_recorrencia,
                'ocorrencia_atual': d.ocorrencia_atual,
                'privada': d.privada,
                'recorrencia_info': (
                    f'{d.ocorrencia_atual}/{d.meses_recorrencia}' if d.meses_recorrencia else
                    (d.get_recorrencia_display() if d.recorrencia != 'UNICA' else '')
                ),
                'data_vencimento': d.data_vencimento.strftime('%Y-%m-%d'),
                'data_vencimento_fmt': d.data_vencimento.strftime('%d/%m/%Y'),
                'status': d.status,
                'status_efetivo': se,
                'dias_para_vencer': dias,
                'data_pagamento': d.data_pagamento.strftime('%d/%m/%Y') if d.data_pagamento else None,
                'observacoes': d.observacoes,
            }

        return JsonResponse({'sucesso': True, 'despesas': [_item(d) for d in qs]})
    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


@login_required
@acesso_financeiro_restrito
@require_http_methods(['POST'])
def api_editar_despesa(request, despesa_id):
    try:
        d = get_object_or_404(Despesa, id=despesa_id)
        data = json.loads(request.body)

        nome = data.get('nome', '').strip()
        if nome:
            d.nome = nome
        if 'descricao' in data:
            d.descricao = data['descricao'].strip()
        if 'valor' in data:
            d.valor = float(str(data['valor']).replace(',', '.'))
        if 'categoria' in data:
            d.categoria = data['categoria']
        if 'recorrencia' in data:
            d.recorrencia = data['recorrencia']
        if 'data_vencimento' in data:
            d.data_vencimento = data['data_vencimento']
        if 'status' in data:
            d.status = data['status']
            if data['status'] == 'PAGO' and not d.data_pagamento:
                d.data_pagamento = timezone.localdate()
            elif data['status'] == 'PENDENTE':
                d.data_pagamento = None
        if 'data_pagamento' in data and data['data_pagamento']:
            d.data_pagamento = data['data_pagamento']
        if 'meses_recorrencia' in data:
            mr = data['meses_recorrencia']
            d.meses_recorrencia = int(mr) if mr and str(mr).isdigit() else None
        if 'observacoes' in data:
            d.observacoes = data['observacoes'].strip()
        if 'privada' in data:
            d.privada = data['privada']
        d.save()

        # Recorrência: gera próxima ocorrência se pago e ainda há ciclos restantes
        proxima = None
        if d.status == 'PAGO' and d.recorrencia != 'UNICA':
            prox_num = d.ocorrencia_atual + 1
            # Verifica se ainda há ocorrências (meses_recorrencia=None = indefinido)
            if d.meses_recorrencia is None or prox_num <= d.meses_recorrencia:
                mapa = {'MENSAL':1,'BIMESTRAL':2,'TRIMESTRAL':3,'SEMESTRAL':6,'ANUAL':12}
                intervalo = mapa.get(d.recorrencia, 0)
                if intervalo:
                    prox_venc = d.data_vencimento + relativedelta(months=intervalo)
                    existe = Despesa.objects.filter(
                        nome=d.nome, data_vencimento=prox_venc, status='PENDENTE'
                    ).exists()
                    if not existe:
                        Despesa.objects.create(
                            nome=d.nome, descricao=d.descricao, valor=d.valor,
                            categoria=d.categoria, recorrencia=d.recorrencia,
                            meses_recorrencia=d.meses_recorrencia,
                            ocorrencia_atual=prox_num,
                            data_vencimento=prox_venc,
                            criado_por=d.criado_por,
                            observacoes=d.observacoes,
                        )
                        proxima = prox_venc.strftime('%d/%m/%Y')

        return JsonResponse({'sucesso': True, 'msg': 'Despesa atualizada.', 'proxima_gerada': proxima})
    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


@login_required
@acesso_financeiro_restrito
@require_http_methods(['POST'])
def api_pagar_despesa(request, despesa_id):
    """Marca despesa como paga com um clique e gera próxima recorrência se aplicável."""
    try:
        d = get_object_or_404(Despesa, id=despesa_id)
        if d.status == 'PAGO':
            return JsonResponse({'sucesso': False, 'erro': 'Despesa já está paga.'})

        d.status = 'PAGO'
        d.data_pagamento = timezone.localdate()
        d.save()

        proxima = None
        proxima_num = None
        if d.recorrencia != 'UNICA':
            prox_num = d.ocorrencia_atual + 1
            if d.meses_recorrencia is None or prox_num <= d.meses_recorrencia:
                mapa = {'MENSAL':1,'BIMESTRAL':2,'TRIMESTRAL':3,'SEMESTRAL':6,'ANUAL':12}
                intervalo = mapa.get(d.recorrencia, 0)
                if intervalo:
                    prox_venc = d.data_vencimento + relativedelta(months=intervalo)
                    existe = Despesa.objects.filter(
                        nome=d.nome, data_vencimento=prox_venc, status='PENDENTE'
                    ).exists()
                    if not existe:
                        Despesa.objects.create(
                            nome=d.nome, descricao=d.descricao, valor=d.valor,
                            categoria=d.categoria, recorrencia=d.recorrencia,
                            meses_recorrencia=d.meses_recorrencia,
                            ocorrencia_atual=prox_num,
                            data_vencimento=prox_venc,
                            criado_por=d.criado_por,
                            observacoes=d.observacoes,
                        )
                        proxima = prox_venc.strftime('%d/%m/%Y')
                        proxima_num = prox_num
            else:
                proxima = 'encerrada'

        msg = f'✓ "{d.nome}" marcada como paga!'
        if proxima and proxima != 'encerrada':
            msg += f' Próxima ({proxima_num}) em {proxima}.'
        elif proxima == 'encerrada':
            msg += ' Recorrência encerrada.'

        return JsonResponse({'sucesso': True, 'msg': msg, 'proxima_gerada': proxima, 'proxima_num': proxima_num})
    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


@login_required
@acesso_financeiro_restrito
@require_http_methods(['DELETE'])
def api_deletar_despesa(request, despesa_id):
    try:
        d = get_object_or_404(Despesa, id=despesa_id)
        nome = d.nome
        d.delete()
        return JsonResponse({'sucesso': True, 'msg': f'Despesa "{nome}" deletada.'})
    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


@login_required
@require_http_methods(['GET'])
def api_despesas_dashboard(request):
    """
    Retorna despesas relevantes para o dashboard:
    - vencidas (PENDENTE + data_vencimento < hoje)
    - vencendo hoje
    - próximas (próximos 30 dias)
    - total mensal (mês atual)
    """
    try:
        hoje = timezone.localdate()
        fim_mes = hoje.replace(day=1) + relativedelta(months=1) - timedelta(days=1)

        def _s(d):
            dias = (d.data_vencimento - hoje).days
            return {
                'id': d.id,
                'nome': d.nome,
                'valor': float(d.valor),
                'categoria': d.get_categoria_display(),
                'data_vencimento_fmt': d.data_vencimento.strftime('%d/%m/%Y'),
                'dias': dias,
                'recorrencia': d.get_recorrencia_display(),
            }

        vencidas   = list(Despesa.objects.filter(status='PENDENTE', data_vencimento__lt=hoje).order_by('data_vencimento'))
        hoje_list  = list(Despesa.objects.filter(status='PENDENTE', data_vencimento=hoje))
        proximas   = list(Despesa.objects.filter(
            status='PENDENTE',
            data_vencimento__gt=hoje,
            data_vencimento__lte=hoje + timedelta(days=30),
        ).order_by('data_vencimento'))

        total_mes_pendente = Despesa.objects.filter(
            status='PENDENTE',
            data_vencimento__year=hoje.year,
            data_vencimento__month=hoje.month,
        ).aggregate(t=Sum('valor'))['t'] or 0

        total_mes_pago = Despesa.objects.filter(
            status='PAGO',
            data_pagamento__year=hoje.year,
            data_pagamento__month=hoje.month,
        ).aggregate(t=Sum('valor'))['t'] or 0

        return JsonResponse({
            'sucesso': True,
            'vencidas':  [_s(d) for d in vencidas],
            'hoje':      [_s(d) for d in hoje_list],
            'proximas':  [_s(d) for d in proximas],
            'total_mes_pendente': float(total_mes_pendente),
            'total_mes_pago':     float(total_mes_pago),
            'count_vencidas':     len(vencidas),
            'count_hoje':         len(hoje_list),
        })
    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)
