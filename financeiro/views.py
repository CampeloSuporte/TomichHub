from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
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
from .models import Consultoria, AluguelIPv4, Fatura, ConfiguracaoFinanceira, Pagamento, Despesa, ContratoAluguel, CartaLOA
from .decorators import acesso_financeiro_restrito
from django.db import transaction
from django.db.models.functions import Substr, Cast

# ============================================
# VIEWS RENDERIZADAS
# ============================================

@login_required
@acesso_financeiro_restrito
def listar_despesas_page(request):
    return render(request, 'financeiro/despesas.html')


@login_required
@acesso_financeiro_restrito
def painel_blocos_ip(request):
    return render(request, 'financeiro/blocos_ip.html')


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
        privada = request.POST.get('privada') == 'on' or request.POST.get('privada') == 'true'

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
            data_inicio=data_inicio,
            privada=privada
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

    # Mesma proteção contra duplicidade de gerar_faturas_aluguel_ipv4.
    vencimentos_existentes = set(
        consultoria.faturas.filter(status__in=['ABERTA', 'PAGA']).values_list('data_vencimento', flat=True)
    )

    for mes in range(consultoria.quantidade_meses):
        # Vencimento preserva o dia de data_inicio
        # Ex: início 15/02 → vencimentos: 15/03, 15/04, 15/05...
        data_vencimento = data_inicio + relativedelta(months=mes + 1)
        if data_vencimento in vencimentos_existentes:
            continue
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
        privada = request.POST.get('privada') == 'on' or request.POST.get('privada') == 'true'

        if not cliente_id or not bloco_descricao or not valor_mensal or not data_inicio_str:
            return JsonResponse({'sucesso': False, 'erro': 'Preencha todos os campos obrigatórios'}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()

        bloco_ip = None
        if bloco_id:
            bloco_ip = get_object_or_404(BlocoIP, id=bloco_id)

        bloco_v6 = (request.POST.get('bloco_v6') or '').strip()

        aluguel = AluguelIPv4.objects.create(
            cliente=cliente,
            bloco_ip=bloco_ip,
            bloco_descricao=bloco_descricao,
            bloco_v6=bloco_v6,
            quantidade_ips=quantidade_ips,
            valor_mensal=valor_mensal,
            data_inicio=data_inicio,
            privada=privada
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

    # Vencimentos já cobertos por fatura aberta/paga deste aluguel — evita
    # duplicar cobrança do mesmo mês se a função for chamada mais de uma vez
    # (ex: reemissão manual depois que faturas foram apagadas por engano).
    vencimentos_existentes = set(
        aluguel.faturas.filter(status__in=['ABERTA', 'PAGA']).values_list('data_vencimento', flat=True)
    )

    for mes in range(quantidade_meses):
        # Vencimento preserva o dia de data_inicio
        # Ex: início 10/02 → vencimentos: 10/03, 10/04, 10/05...
        data_vencimento = data_inicio + relativedelta(months=mes + 1)
        if data_vencimento in vencimentos_existentes:
            continue
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
        aluguel.bloco_v6        = (request.POST.get('bloco_v6') or aluguel.bloco_v6 or '').strip()
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
@require_http_methods(["POST"])
def api_gerar_fatura_aluguel(request, aluguel_id):
    """Gera fatura(s) para um aluguel IPv4 já existente — usado tanto pra
    emitir a próxima cobrança quanto pra reemitir faturas apagadas por
    engano. gerar_faturas_aluguel_ipv4 já ignora vencimentos que já têm
    fatura aberta/paga, então repetir a chamada não duplica cobrança."""
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)

        aluguel = get_object_or_404(AluguelIPv4, id=aluguel_id)
        if aluguel.status != 'ATIVO':
            return JsonResponse({'sucesso': False, 'erro': 'Este aluguel não está ativo.'}, status=400)

        quantidade_meses = int(request.POST.get('quantidade_meses', 1) or 1)
        if not 1 <= quantidade_meses <= 24:
            return JsonResponse({'sucesso': False, 'erro': 'Quantidade de meses deve ser entre 1 e 24.'}, status=400)

        faturas_geradas = gerar_faturas_aluguel_ipv4(aluguel, quantidade_meses)

        if not faturas_geradas:
            return JsonResponse({
                'sucesso': True,
                'mensagem': 'Nenhuma fatura nova — todos os vencimentos desse período já têm fatura.',
                'faturas': [],
            })

        return JsonResponse({
            'sucesso': True,
            'mensagem': f'{len(faturas_geradas)} fatura(s) gerada(s).',
            'faturas': faturas_geradas,
        })

    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


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
                v6_badge = f'<span class="badge bg-secondary ms-2">{a.bloco_v6}</span>' if a.bloco_v6 else ''
                v6_esc   = (a.bloco_v6 or '').replace("'", "\\'")

                # Verifica se existe contrato assinado para este aluguel
                contrato_assinado = ContratoAluguel.objects.filter(
                    aluguel=a, status='assinado'
                ).order_by('-assinado_em').first()
                btn_pdf_assinado = ''
                if contrato_assinado and contrato_assinado.pdf_assinado:
                    pdf_url = contrato_assinado.pdf_assinado.url
                    btn_pdf_assinado = f'''
                        <a href="{pdf_url}" target="_blank"
                           class="btn btn-sm btn-success" title="Baixar PDF Assinado pelo Cliente">
                            <i class="fas fa-file-signature me-1"></i>PDF Assinado
                        </a>'''

                html += f'''
                <div class="card mb-3 border-info">
                    <div class="card-body">
                        <div class="row align-items-center">
                            <div class="col-md-6">
                                <h6 class="card-title mb-1">
                                    <i class="fas fa-network-wired text-info me-2"></i>{a.bloco_descricao}
                                    {v6_badge}
                                </h6>
                                <small class="text-muted">
                                    {a.quantidade_ips} IPs | Início: {a.data_inicio.strftime('%d/%m/%Y')}
                                </small>
                            </div>
                            <div class="col-md-2 text-center">
                                <strong class="text-info fs-5">R$ {float(a.valor_mensal):.2f}</strong>
                                <div><small class="text-muted">/mês</small></div>
                            </div>
                            <div class="col-md-4 text-end d-flex gap-1 justify-content-end flex-wrap">
                                {btn_pdf_assinado}
                                {f'''<button class="btn btn-sm btn-outline-primary"
                                    onclick="abrirGerarFaturaAluguel({a.id}, '{a.bloco_descricao}')"
                                    title="Gerar fatura(s) para este aluguel">
                                    <i class="fas fa-file-invoice-dollar me-1"></i>Gerar Fatura
                                </button>''' if a.status == 'ATIVO' else ''}
                                <a href="/financeiro/api/aluguel/{a.id}/contrato/" target="_blank"
                                   class="btn btn-sm btn-outline-success" title="Baixar Contrato PDF (sem assinatura)">
                                    <i class="fas fa-file-pdf me-1"></i>PDF
                                </a>
                                <button class="btn btn-sm btn-outline-info"
                                    onclick="abrirContratosAluguel({a.id}, '{a.bloco_descricao}')"
                                    title="Assinatura Digital do Contrato">
                                    <i class="fas fa-signature me-1"></i>Contrato
                                </button>
                                <button class="btn btn-sm btn-outline-purple"
                                    style="border-color:#bc8cff;color:#bc8cff;"
                                    onclick="abrirLoaAluguel({a.id}, '{a.bloco_descricao}')"
                                    title="Carta de Autorização (LOA)">
                                    <i class="fas fa-file-signature me-1"></i>LOA
                                </button>
                                <button class="btn btn-sm btn-outline-warning"
                                    onclick="abrirEditarAluguel({a.id}, '{a.bloco_descricao}', '{v6_esc}', {float(a.valor_mensal)}, {a.quantidade_ips}, '{data_inicio_iso}')"
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
        privada = request.POST.get('privada') == 'on' or request.POST.get('privada') == 'true'

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
            data_inicio=data_inicio,
            privada=privada
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

        data_inicio_str = request.POST.get('data_inicio', '').strip()
        faturas_atualizadas = 0
        if data_inicio_str:
            nova_data = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
            if nova_data != venda.data_inicio:
                dia_antigo = venda.data_inicio.day
                venda.data_inicio = nova_data
                # Atualiza faturas em aberto do cliente deste tipo que tenham o dia antigo
                faturas = Fatura.objects.filter(
                    cliente=venda.cliente,
                    tipo='VENDA_EQUIPAMENTO',
                    status__in=['ABERTA', 'RASCUNHO'],
                ).order_by('data_vencimento')
                for fatura in faturas:
                    if fatura.data_vencimento.day != dia_antigo:
                        continue
                    try:
                        novo_venc = fatura.data_vencimento.replace(day=nova_data.day)
                        if novo_venc != fatura.data_vencimento:
                            fatura.data_vencimento = novo_venc
                            fatura.save(update_fields=['data_vencimento'])
                            faturas_atualizadas += 1
                    except ValueError:
                        pass

        venda.save()

        msg = 'Venda atualizada com sucesso!'
        if faturas_atualizadas:
            msg += f' {faturas_atualizadas} fatura(s) tiveram o vencimento ajustado.'
        return JsonResponse({'sucesso': True, 'mensagem': msg})

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
                                <button class="btn btn-sm btn-outline-warning me-1" onclick="abrirEditarVenda({v.id}, '{v.descricao}', '{v.data_inicio.strftime('%Y-%m-%d')}')" title="Editar">
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
        from decimal import Decimal, ROUND_HALF_UP
        valor_pagado = Decimal(valor_pagado_str).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

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


@login_required
@require_http_methods(["POST"])
def api_ajustar_vencimento(request):
    """Ajusta o dia de vencimento de todas as faturas abertas de um cliente."""
    try:
        import json as _json
        from calendar import monthrange
        data = _json.loads(request.body)
        cliente_id = data.get('cliente_id')
        dia = data.get('dia')

        if not cliente_id:
            return JsonResponse({'sucesso': False, 'erro': 'cliente_id obrigatório'}, status=400)
        try:
            dia = int(dia)
            if not (1 <= dia <= 31):
                raise ValueError()
        except (TypeError, ValueError):
            return JsonResponse({'sucesso': False, 'erro': 'Dia inválido (1-31)'}, status=400)

        from clientes.models import Cliente as _Cliente
        cliente = get_object_or_404(_Cliente, id=cliente_id)

        # Atualiza faturas abertas (não pagas)
        faturas = Fatura.objects.filter(cliente=cliente).exclude(status='PAGA')
        atualizadas = 0
        for fatura in faturas:
            _, ultimo_dia = monthrange(
                fatura.data_vencimento.year,
                fatura.data_vencimento.month
            )
            dia_efetivo = min(dia, ultimo_dia)
            fatura.data_vencimento = fatura.data_vencimento.replace(day=dia_efetivo)
            fatura.save(update_fields=['data_vencimento'])
            atualizadas += 1

        return JsonResponse({
            'sucesso': True,
            'atualizadas': atualizadas,
            'mensagem': f'{atualizadas} fatura(s) ajustada(s) para o dia {dia}.',
        })
    except Exception as e:
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
        from datetime import date
        from dateutil.relativedelta import relativedelta
        data = json.loads(request.body)
        nome = data.get('nome', '').strip()
        if not nome:
            return JsonResponse({'sucesso': False, 'erro': 'Nome é obrigatório'})
        vencimento_str = data.get('data_vencimento', '')
        if not vencimento_str:
            return JsonResponse({'sucesso': False, 'erro': 'Data de vencimento é obrigatória'})
        try:
            valor = float(str(data.get('valor', 0)).replace(',', '.'))
        except (ValueError, TypeError):
            return JsonResponse({'sucesso': False, 'erro': 'Valor inválido'})

        parcelas = int(data.get('parcelas', 1) or 1)
        parcelas = max(1, min(12, parcelas))
        recorrencia = data.get('recorrencia', 'UNICA') if parcelas == 1 else 'UNICA'
        meses_raw = data.get('meses_recorrencia', None)
        meses_recorrencia = int(meses_raw) if meses_raw and str(meses_raw).isdigit() and recorrencia != 'UNICA' and parcelas == 1 else None
        privada = data.get('privada', False)
        categoria = data.get('categoria', 'OUTROS')
        descricao = data.get('descricao', '').strip()
        observacoes = data.get('observacoes', '').strip()

        vencimento_base = date.fromisoformat(vencimento_str)

        if parcelas > 1:
            criadas = []
            for i in range(1, parcelas + 1):
                venc_i = vencimento_base + relativedelta(months=i - 1)
                nome_i = f'{nome} ({i}/{parcelas})'
                d = Despesa.objects.create(
                    nome=nome_i,
                    descricao=descricao,
                    valor=valor,
                    categoria=categoria,
                    recorrencia='UNICA',
                    meses_recorrencia=None,
                    ocorrencia_atual=i,
                    data_vencimento=venc_i,
                    observacoes=observacoes,
                    privada=privada,
                    criado_por=request.user,
                )
                criadas.append(d.id)
            return JsonResponse({'sucesso': True, 'ids': criadas, 'msg': f'{parcelas} parcelas criadas com sucesso!'})

        d = Despesa.objects.create(
            nome=nome,
            descricao=descricao,
            valor=valor,
            categoria=categoria,
            recorrencia=recorrencia,
            meses_recorrencia=meses_recorrencia,
            ocorrencia_atual=1,
            data_vencimento=vencimento_base,
            observacoes=observacoes,
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
@require_http_methods(['POST', 'DELETE'])
def api_deletar_despesa(request, despesa_id):
    try:
        d = get_object_or_404(Despesa, id=despesa_id)
        nome = d.nome
        d.delete()
        return JsonResponse({'sucesso': True, 'msg': f'Despesa "{nome}" deletada.'})
    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)


@login_required
@acesso_financeiro_restrito
@require_http_methods(['POST'])
def api_despesas_bulk(request):
    """Ação em massa sobre despesas: deletar, marcar privada/pública, marcar paga."""
    try:
        data = json.loads(request.body)
        ids  = [int(i) for i in data.get('ids', []) if str(i).isdigit()]
        acao = data.get('acao', '')
        if not ids:
            return JsonResponse({'sucesso': False, 'erro': 'Nenhuma despesa selecionada.'})
        from django.db.models import Q
        qs = Despesa.objects.filter(
            id__in=ids
        ).filter(Q(privada=False) | Q(privada=True, criado_por=request.user))
        count = qs.count()
        if acao == 'deletar':
            qs.delete()
            return JsonResponse({'sucesso': True, 'msg': f'{count} despesa(s) deletada(s).'})
        elif acao == 'privada':
            qs.update(privada=True)
            return JsonResponse({'sucesso': True, 'msg': f'{count} despesa(s) marcada(s) como privada.'})
        elif acao == 'publica':
            qs.update(privada=False)
            return JsonResponse({'sucesso': True, 'msg': f'{count} despesa(s) marcada(s) como pública.'})
        elif acao == 'pagar':
            hoje = timezone.localdate()
            qs.filter(status='PENDENTE').update(status='PAGO', data_pagamento=hoje)
            return JsonResponse({'sucesso': True, 'msg': f'{count} despesa(s) marcada(s) como paga.'})
        else:
            return JsonResponse({'sucesso': False, 'erro': 'Ação inválida.'})
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


# ============================================
# GERAR CONTRATO DE ALUGUEL DE BLOCO IP
# ============================================

@login_required
def gerar_contrato_aluguel(request, aluguel_id):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table as RLTable, TableStyle as RLTableStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from io import BytesIO
    from datetime import date

    aluguel = get_object_or_404(AluguelIPv4, id=aluguel_id)
    cliente = aluguel.cliente

    loc_nome   = cliente.nome_empresa or ''
    loc_cnpj   = cliente.cnpj or ''
    loc_end    = cliente.endereco or ''
    loc_cep    = cliente.cep or ''
    loc_cidade = cliente.cidade or ''
    loc_estado = cliente.estado or ''
    loc_end_full = loc_end
    if loc_cidade:  loc_end_full += f', {loc_cidade}'
    if loc_estado:  loc_end_full += f'/{loc_estado}'
    if loc_cep:     loc_end_full += f', CEP {loc_cep}'

    bloco_v4 = aluguel.bloco_descricao or ''
    bloco_v6 = aluguel.bloco_v6 or ''

    if bloco_v6:
        bloco_objeto = f'{bloco_v4} (IPv4) e {bloco_v6} (IPv6)'
        bloco_tipo   = 'blocos IPv4 e IPv6'
    else:
        bloco_objeto = bloco_v4
        bloco_tipo   = 'bloco IPv4'

    prefixo = bloco_v4.split('/')[-1] if '/' in bloco_v4 else 'bloco'

    valor = float(aluguel.valor_mensal)

    def _extenso(v):
        reais = int(v)
        centavos = round((v - reais) * 100)
        mapa = {
            1:'UM', 2:'DOIS', 3:'TRÊS', 4:'QUATRO', 5:'CINCO',
            6:'SEIS', 7:'SETE', 8:'OITO', 9:'NOVE', 10:'DEZ',
            11:'ONZE', 12:'DOZE', 13:'TREZE', 14:'QUATORZE', 15:'QUINZE',
            16:'DEZESSEIS', 17:'DEZESSETE', 18:'DEZOITO', 19:'DEZENOVE',
            20:'VINTE', 30:'TRINTA', 40:'QUARENTA', 50:'CINQUENTA',
            60:'SESSENTA', 70:'SETENTA', 80:'OITENTA', 90:'NOVENTA',
        }
        def _centenas(n):
            if n == 0: return ''
            if n == 100: return 'CEM'
            cent_map = {200:'DUZENTOS',300:'TREZENTOS',400:'QUATROCENTOS',500:'QUINHENTOS',
                        600:'SEISCENTOS',700:'SETECENTOS',800:'OITOCENTOS',900:'NOVECENTOS'}
            c = (n // 100) * 100; r = n % 100
            base = cent_map.get(c, '') if c else ''
            resto = _dezenas(r)
            return f'{base} E {resto}' if base and resto else base or resto
        def _dezenas(n):
            if n == 0: return ''
            if n in mapa: return mapa[n]
            d = (n // 10) * 10; u = n % 10
            return f'{mapa[d]} E {mapa[u]}' if u else mapa[d]
        def _numero(n):
            if n == 0: return 'ZERO'
            if n == 1000: return 'MIL'
            if n > 1000:
                m = n // 1000; r = n % 1000
                mil = 'UM MIL' if m == 1 else f'{_numero(m)} MIL'
                return f'{mil} E {_centenas(r)}' if r else mil
            return _centenas(n) or _dezenas(n)

        txt = _numero(reais)
        if centavos:
            txt += f' REAIS E {_dezenas(centavos)} CENTAVOS'
        else:
            txt += ' REAIS'
        return txt

    valor_extenso = _extenso(valor)
    valor_fmt = f'R$ {valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    multa = valor * 12 * 0.40
    multa_fmt = f'R$ {multa:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

    hoje = date.today()
    meses_pt = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
    data_ext = f'Senhor do Bonfim, {hoje.day} de {meses_pt[hoje.month-1]} de {hoje.year}'

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=2.5*cm, leftMargin=2.5*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title=f'Contrato Aluguel Bloco IP - {loc_nome}',
    )

    styles = getSampleStyleSheet()
    st_titulo = ParagraphStyle('Titulo', parent=styles['Normal'],
        fontSize=13, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=16)
    st_corpo  = ParagraphStyle('Corpo', parent=styles['Normal'],
        fontSize=10, fontName='Helvetica', alignment=TA_JUSTIFY, leading=16, spaceAfter=8)
    st_secao  = ParagraphStyle('Secao', parent=styles['Normal'],
        fontSize=10, fontName='Helvetica-Bold', spaceBefore=12, spaceAfter=4)
    st_assina = ParagraphStyle('Assina', parent=styles['Normal'],
        fontSize=10, fontName='Helvetica', alignment=TA_CENTER, leading=14)

    B = lambda t: f'<b>{t}</b>'
    story = []

    story.append(Paragraph('CONTRATO DE ALUGUEL DE BLOCO IP', st_titulo))
    story.append(Spacer(1, 0.3*cm))

    story.append(Paragraph(
        f'Pelo presente instrumento particular de contrato de aluguel, de um lado, '
        f'{B(loc_nome)} CNPJ sob o nº {B(loc_cnpj)}, residente e domiciliado(a) na '
        f'{B(loc_end_full)}, doravante denominado(a) {B("locatário(a)")}, '
        f'e do outro lado, {B("CAMPELO SUPORTE E CONSULTORIA ESPECIALIZADA EM REDES")}, '
        f'CNPJ sob o nº {B("45.216.351/0001-72")}, residente e domiciliado(a) na '
        f'{B("R CARLOS PEREIRA DE CARVALHO, nº 295, Centro, CEP 48.840-000")}, '
        f'doravante denominado(a) {B("locador(a)")}, têm justo e contratado o seguinte:',
        st_corpo
    ))

    clausulas = [
        ('1 - Objeto',
         f'O objeto do presente contrato é o aluguel do {bloco_tipo} com a vigência de um ano, '
         f'localizado no endereço {B("(" + bloco_objeto + ")")} pelo valor mensal de '
         f'{B(valor_fmt + " (" + valor_extenso + ")")}, com sigilo absoluto. '
         f'O locatário(a) se compromete a não emprestar ou alugar o bloco para terceiros '
         f'e a anunciá-lo somente como /{prefixo}.'),
        ('2 - Prazo',
         'O prazo de vigência do presente contrato é de 1 (um) ano, contado a partir da data de '
         'assinatura deste instrumento e terminando em 12 meses.'),
        ('3 - Condições de Pagamento',
         f'O locatário(a) se compromete a pagar o valor mensal do aluguel até o dia 17 (Dezessete) '
         f'de cada mês da seguinte forma: Pix — de forma pré-paga, deve ser pago para uso do bloco. '
         f'O não pagamento poderá ocorrer a suspensão do aluguel no prazo de 10 dias.'),
        ('4 - Sigilo e Informações',
         f'O locatário(a) se compromete a manter sigilo absoluto sobre a existência do bloco em sua '
         f'posse e não poderá informar a nenhuma entidade governamental ou terceiros sobre a sua existência.'),
        ('5 - Obrigações do Locatário',
         f'O locatário(a) se compromete a utilizar o bloco somente para o fim específico acordado '
         f'entre as partes, bem como a zelar pela sua conservação e segurança, não podendo danificá-lo '
         f'ou causar qualquer tipo de prejuízo ao locador(a).'),
        ('6 - Rescisão',
         f'Em caso de rescisão do presente contrato, fica estipulada uma multa rescisória equivalente '
         f'a 40% do valor total do contrato ({B(multa_fmt)}). Em caso de rescisão deverá pagar a '
         f'referida multa, no prazo de 30 (trinta) dias após a rescisão. A não realização do pagamento '
         f'da multa implicará na cobrança judicial do valor devido, acrescido de juros de mora e '
         f'correção monetária.'),
        ('7 - Foro',
         'Fica eleito o foro da Comarca de Senhor do Bonfim - BA, para dirimir qualquer controvérsia '
         'oriunda deste contrato.'),
    ]

    for titulo_c, texto_c in clausulas:
        story.append(Paragraph(titulo_c, st_secao))
        story.append(Paragraph(texto_c, st_corpo))

    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(
        'E, por estarem assim justas e contratadas, as partes assinam o presente contrato em duas vias '
        'de igual teor e forma, na presença das testemunhas abaixo.',
        st_corpo
    ))
    story.append(Spacer(1, 0.8*cm))
    story.append(Paragraph(f'<b>{data_ext}.</b>', st_assina))
    story.append(Spacer(1, 1.5*cm))

    # Assinatura do locador
    cfg_fin = ConfiguracaoFinanceira.objects.first()
    locador_cell = _celula_assinatura_locador(cfg_fin, st_assina, cm)

    tbl = RLTable(
        [[
            Paragraph('______________________________________________<br/>'
                      f'<b>{loc_nome}</b><br/>'
                      f'CNPJ: {loc_cnpj}<br/>Locatário(a)', st_assina),
            locador_cell,
        ]],
        colWidths=[8*cm, 8*cm],
    )
    tbl.setStyle(RLTableStyle([
        ('ALIGN',        (0,0), (-1,-1), 'CENTER'),
        ('VALIGN',       (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING',  (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(tbl)

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    nome_arquivo = f'contrato_bloco_ip_{loc_nome.replace(" ", "_")}_{bloco_v4.replace("/", "-")}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{nome_arquivo}"'
    return response


# ============================================
# ASSINATURA DIGITAL DE CONTRATO
# ============================================

def _celula_assinatura_locador(cfg_fin, st_assina, cm):
    """Retorna a célula da tabela para o locador: imagem + texto se tiver assinatura salva."""
    from reportlab.platypus import Paragraph, Image as RLImage
    from io import BytesIO
    import base64 as _b64

    assinatura = (cfg_fin.assinatura_locador if cfg_fin else '') or ''
    if assinatura:
        try:
            img_data = assinatura
            if ',' in img_data:
                img_data = img_data.split(',', 1)[1]
            img_bytes  = _b64.b64decode(img_data)
            img_buf    = BytesIO(img_bytes)
            img        = RLImage(img_buf, width=6*cm, height=2*cm)
            return [
                img,
                Paragraph('<b>CAMPELO SUPORTE E CONSULTORIA</b><br/>'
                          'CNPJ: 45.216.351/0001-72<br/>Locador(a)', st_assina),
            ]
        except Exception:
            pass
    # Fallback: linha em branco
    return Paragraph('______________________________________________<br/>'
                     '<b>CAMPELO SUPORTE E CONSULTORIA</b><br/>'
                     'CNPJ: 45.216.351/0001-72<br/>Locador(a)', st_assina)


def _build_contrato_story(aluguel, styles_extra=None):
    """Retorna (story_list, nome_arquivo) com o conteúdo do contrato sem bloco de assinatura."""
    from reportlab.platypus import Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from datetime import date

    cliente    = aluguel.cliente
    loc_nome   = cliente.nome_empresa or ''
    loc_cnpj   = cliente.cnpj or ''
    loc_end    = cliente.endereco or ''
    loc_cep    = cliente.cep or ''
    loc_cidade = cliente.cidade or ''
    loc_estado = cliente.estado or ''
    loc_end_full = loc_end
    if loc_cidade: loc_end_full += f', {loc_cidade}'
    if loc_estado: loc_end_full += f'/{loc_estado}'
    if loc_cep:    loc_end_full += f', CEP {loc_cep}'

    bloco_v4 = aluguel.bloco_descricao or ''
    bloco_v6 = aluguel.bloco_v6 or ''
    bloco_objeto = f'{bloco_v4} (IPv4) e {bloco_v6} (IPv6)' if bloco_v6 else bloco_v4
    bloco_tipo   = 'blocos IPv4 e IPv6' if bloco_v6 else 'bloco IPv4'
    prefixo      = bloco_v4.split('/')[-1] if '/' in bloco_v4 else 'bloco'

    valor = float(aluguel.valor_mensal)

    def _extenso(v):
        reais = int(v); centavos = round((v - reais) * 100)
        mapa = {1:'UM',2:'DOIS',3:'TRÊS',4:'QUATRO',5:'CINCO',6:'SEIS',7:'SETE',8:'OITO',9:'NOVE',
                10:'DEZ',11:'ONZE',12:'DOZE',13:'TREZE',14:'QUATORZE',15:'QUINZE',16:'DEZESSEIS',
                17:'DEZESSETE',18:'DEZOITO',19:'DEZENOVE',20:'VINTE',30:'TRINTA',40:'QUARENTA',
                50:'CINQUENTA',60:'SESSENTA',70:'SETENTA',80:'OITENTA',90:'NOVENTA'}
        cent_map = {200:'DUZENTOS',300:'TREZENTOS',400:'QUATROCENTOS',500:'QUINHENTOS',
                    600:'SEISCENTOS',700:'SETECENTOS',800:'OITOCENTOS',900:'NOVECENTOS'}
        def _dez(n):
            if n == 0: return ''
            if n in mapa: return mapa[n]
            d=(n//10)*10; u=n%10
            return f'{mapa[d]} E {mapa[u]}' if u else mapa[d]
        def _cent(n):
            if n == 0: return ''
            if n == 100: return 'CEM'
            c=(n//100)*100; r=n%100
            base=cent_map.get(c,'')
            resto=_dez(r)
            return f'{base} E {resto}' if base and resto else base or resto
        def _num(n):
            if n == 0: return 'ZERO'
            if n == 1000: return 'MIL'
            if n > 1000:
                m=n//1000; r=n%1000
                mil='UM MIL' if m==1 else f'{_num(m)} MIL'
                return f'{mil} E {_cent(r)}' if r else mil
            return _cent(n) or _dez(n)
        txt = _num(reais)
        txt += f' REAIS E {_dez(centavos)} CENTAVOS' if centavos else ' REAIS'
        return txt

    valor_extenso = _extenso(valor)
    valor_fmt = f'R$ {valor:,.2f}'.replace(',','X').replace('.',',').replace('X','.')
    multa     = valor * 12 * 0.40
    multa_fmt = f'R$ {multa:,.2f}'.replace(',','X').replace('.',',').replace('X','.')

    hoje      = date.today()
    meses_pt  = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                 'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
    data_ext  = f'Senhor do Bonfim, {hoje.day} de {meses_pt[hoje.month-1]} de {hoje.year}'

    styles   = getSampleStyleSheet()
    st_titulo = ParagraphStyle('Titulo', parent=styles['Normal'],
        fontSize=13, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=16)
    st_corpo  = ParagraphStyle('Corpo', parent=styles['Normal'],
        fontSize=10, fontName='Helvetica', alignment=TA_JUSTIFY, leading=16, spaceAfter=8)
    st_secao  = ParagraphStyle('Secao', parent=styles['Normal'],
        fontSize=10, fontName='Helvetica-Bold', spaceBefore=12, spaceAfter=4)
    st_assina = ParagraphStyle('Assina', parent=styles['Normal'],
        fontSize=10, fontName='Helvetica', alignment=TA_CENTER, leading=14)

    B = lambda t: f'<b>{t}</b>'
    story = []
    story.append(Paragraph('CONTRATO DE ALUGUEL DE BLOCO IP', st_titulo))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        f'Pelo presente instrumento particular de contrato de aluguel, de um lado, '
        f'{B(loc_nome)} CNPJ sob o nº {B(loc_cnpj)}, residente e domiciliado(a) na '
        f'{B(loc_end_full)}, doravante denominado(a) {B("locatário(a)")}, '
        f'e do outro lado, {B("CAMPELO SUPORTE E CONSULTORIA ESPECIALIZADA EM REDES")}, '
        f'CNPJ sob o nº {B("45.216.351/0001-72")}, residente e domiciliado(a) na '
        f'{B("R CARLOS PEREIRA DE CARVALHO, nº 295, Centro, CEP 48.840-000")}, '
        f'doravante denominado(a) {B("locador(a)")}, têm justo e contratado o seguinte:',
        st_corpo
    ))
    clausulas = [
        ('1 - Objeto',
         f'O objeto do presente contrato é o aluguel do {bloco_tipo} com a vigência de um ano, '
         f'localizado no endereço {B("(" + bloco_objeto + ")")} pelo valor mensal de '
         f'{B(valor_fmt + " (" + valor_extenso + ")")}, com sigilo absoluto. '
         f'O locatário(a) se compromete a não emprestar ou alugar o bloco para terceiros '
         f'e a anunciá-lo somente como /{prefixo}.'),
        ('2 - Prazo',
         'O prazo de vigência do presente contrato é de 1 (um) ano, contado a partir da data de '
         'assinatura deste instrumento e terminando em 12 meses.'),
        ('3 - Condições de Pagamento',
         f'O locatário(a) se compromete a pagar o valor mensal do aluguel até o dia 17 (Dezessete) '
         f'de cada mês da seguinte forma: Pix — de forma pré-paga, deve ser pago para uso do bloco. '
         f'O não pagamento poderá ocorrer a suspensão do aluguel no prazo de 10 dias.'),
        ('4 - Sigilo e Informações',
         f'O locatário(a) se compromete a manter sigilo absoluto sobre a existência do bloco em sua '
         f'posse e não poderá informar a nenhuma entidade governamental ou terceiros sobre a sua existência.'),
        ('5 - Obrigações do Locatário',
         f'O locatário(a) se compromete a utilizar o bloco somente para o fim específico acordado '
         f'entre as partes, bem como a zelar pela sua conservação e segurança, não podendo danificá-lo '
         f'ou causar qualquer tipo de prejuízo ao locador(a).'),
        ('6 - Rescisão',
         f'Em caso de rescisão do presente contrato, fica estipulada uma multa rescisória equivalente '
         f'a 40% do valor total do contrato ({B(multa_fmt)}). Em caso de rescisão deverá pagar a '
         f'referida multa, no prazo de 30 (trinta) dias após a rescisão. A não realização do pagamento '
         f'da multa implicará na cobrança judicial do valor devido, acrescido de juros de mora e '
         f'correção monetária.'),
        ('7 - Foro',
         'Fica eleito o foro da Comarca de Senhor do Bonfim - BA, para dirimir qualquer controvérsia '
         'oriunda deste contrato.'),
    ]
    for titulo_c, texto_c in clausulas:
        story.append(Paragraph(titulo_c, st_secao))
        story.append(Paragraph(texto_c, st_corpo))

    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(
        'E, por estarem assim justas e contratadas, as partes assinam o presente contrato em duas vias '
        'de igual teor e forma, na presença das testemunhas abaixo.',
        st_corpo
    ))
    story.append(Spacer(1, 0.8*cm))
    story.append(Paragraph(f'<b>{data_ext}.</b>', st_assina))

    nome_arquivo = f'contrato_{loc_nome.replace(" ","_")}_{bloco_v4.replace("/","-")}.pdf'
    return story, nome_arquivo, st_assina, loc_nome, loc_cnpj


@login_required
@require_http_methods(['POST'])
def gerar_link_assinatura(request, aluguel_id):
    """Cria (ou retorna existente) um ContratoAluguel com link único para assinatura."""
    aluguel = get_object_or_404(AluguelIPv4, id=aluguel_id)

    dias = int(request.POST.get('dias_validade', 7))
    expira = timezone.now() + timedelta(days=dias)

    # Reutiliza contrato pendente se existir
    contrato = ContratoAluguel.objects.filter(aluguel=aluguel, status='pendente').first()
    if not contrato:
        contrato = ContratoAluguel.objects.create(aluguel=aluguel, expira_em=expira)
    else:
        contrato.expira_em = expira
        contrato.save(update_fields=['expira_em'])

    scheme = 'https' if request.is_secure() else 'http'
    link   = f'{scheme}://{request.get_host()}/financeiro/contrato/{contrato.token}/assinar/'
    return JsonResponse({'ok': True, 'link': link, 'token': str(contrato.token),
                         'expira_em': contrato.expira_em.strftime('%d/%m/%Y %H:%M')})


def assinar_contrato(request, token):
    """Página pública onde o cliente lê e assina o contrato."""
    from django.views.decorators.csrf import csrf_exempt
    contrato = get_object_or_404(ContratoAluguel, token=token)

    if contrato.status == 'assinado':
        return render(request, 'financeiro/contrato_assinado.html', {'contrato': contrato})
    if contrato.esta_expirado():
        contrato.status = 'expirado'
        contrato.save(update_fields=['status'])
        return render(request, 'financeiro/contrato_expirado.html', {'contrato': contrato})

    aluguel  = contrato.aluguel
    cliente  = aluguel.cliente
    return render(request, 'financeiro/contrato_assinar.html', {
        'contrato': contrato,
        'aluguel':  aluguel,
        'cliente':  cliente,
    })


from django.views.decorators.csrf import csrf_exempt

def _enviar_contrato_email(contrato):
    """Envia PDF do contrato assinado por email para o cliente."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase
    from email.mime.text import MIMEText
    from email import encoders as _enc
    from clientes.models import ConfiguracaoSistema
    from django.conf import settings as djsettings

    cliente = contrato.aluguel.cliente
    email_destino = getattr(cliente, 'email', '') or ''
    if not email_destino:
        print(f'⚠️ Email do cliente {cliente.nome_empresa} não cadastrado — contrato não enviado.')
        return False

    smtp_cfg  = ConfiguracaoSistema.get()
    smtp_host = smtp_cfg.smtp_host or getattr(djsettings, 'EMAIL_HOST', '')
    smtp_port = int(smtp_cfg.smtp_port or getattr(djsettings, 'EMAIL_PORT', 587))
    smtp_user = smtp_cfg.smtp_user or getattr(djsettings, 'EMAIL_HOST_USER', '')
    smtp_pass = smtp_cfg.smtp_pass or getattr(djsettings, 'EMAIL_HOST_PASSWORD', '')
    remetente = smtp_cfg.smtp_from or smtp_user
    use_tls   = smtp_cfg.smtp_use_tls

    if not smtp_host or not smtp_user:
        print('⚠️ SMTP não configurado — contrato não enviado por email.')
        return False

    assinado_em = contrato.assinado_em.strftime('%d/%m/%Y às %H:%M') if contrato.assinado_em else ''
    bloco_v4    = contrato.aluguel.bloco_descricao
    bloco_v6    = contrato.aluguel.bloco_v6 or ''
    blocos_str  = f'{bloco_v4}' + (f' e {bloco_v6}' if bloco_v6 else '')

    corpo_html = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:600px;margin:0 auto;background:#f9f9f9;border-radius:8px;overflow:hidden;">
      <div style="background:#161b22;padding:28px 32px;text-align:center;">
        <h2 style="color:#e6edf3;margin:0;font-size:1.2rem;">Contrato Assinado Digitalmente</h2>
        <p style="color:#7d8590;margin:6px 0 0;font-size:.85rem;">Campelo Suporte e Consultoria Especializada em Redes</p>
      </div>
      <div style="padding:28px 32px;background:#fff;">
        <p style="color:#333;font-size:.95rem;">Olá, <b>{cliente.nome_empresa}</b>!</p>
        <p style="color:#555;font-size:.9rem;line-height:1.6;">
          Segue em anexo a cópia do contrato de aluguel de bloco IP assinado digitalmente.
        </p>
        <div style="background:#f5f5f5;border-radius:6px;padding:16px;margin:20px 0;font-size:.85rem;color:#555;">
          <p style="margin:0 0 6px;"><b>Bloco(s):</b> {blocos_str}</p>
          <p style="margin:0 0 6px;"><b>Assinado por:</b> {contrato.nome_assinante}</p>
          <p style="margin:0 0 6px;"><b>Data/hora:</b> {assinado_em}</p>
          <p style="margin:0;"><b>Token de verificação:</b> <code style="font-size:.8rem;">{contrato.token}</code></p>
        </div>
        <p style="color:#555;font-size:.85rem;line-height:1.6;">
          Este documento é a sua cópia do contrato. Guarde-o em local seguro.
        </p>
      </div>
      <div style="padding:16px 32px;background:#f0f0f0;text-align:center;">
        <p style="color:#888;font-size:.75rem;margin:0;">
          E-mail gerado automaticamente pelo sistema CRM — Campelo Suporte · CNPJ 45.216.351/0001-72
        </p>
      </div>
    </div>
    """

    msg = MIMEMultipart('mixed')
    msg['Subject'] = f'Contrato de Aluguel de Bloco IP — {blocos_str}'
    msg['From']    = remetente
    msg['To']      = email_destino

    msg.attach(MIMEText(corpo_html, 'html', 'utf-8'))

    # Anexa PDF se existir
    if contrato.pdf_assinado:
        try:
            pdf_bytes = contrato.pdf_assinado.read()
            part = MIMEBase('application', 'pdf')
            part.set_payload(pdf_bytes)
            _enc.encode_base64(part)
            part.add_header('Content-Disposition',
                            f'attachment; filename="contrato_assinado_{bloco_v4.replace("/","-")}.pdf"')
            msg.attach(part)
        except Exception as exc:
            print(f'⚠️ Não foi possível anexar PDF: {exc}')

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.ehlo()
        if use_tls:
            server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(remetente, [email_destino], msg.as_string())

    print(f'✅ Contrato enviado por email para {email_destino}')
    return True


@csrf_exempt
@require_http_methods(['POST'])
def confirmar_assinatura(request, token):
    """Recebe a assinatura do cliente, gera PDF assinado e marca como assinado."""
    import base64 as _b64
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table as RLTable, TableStyle as RLTableStyle, Image as RLImage
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm

    contrato = get_object_or_404(ContratoAluguel, token=token)
    if contrato.status == 'assinado':
        return JsonResponse({'ok': False, 'erro': 'Contrato já assinado.'}, status=400)
    if contrato.esta_expirado():
        return JsonResponse({'ok': False, 'erro': 'Link expirado.'}, status=400)

    try:
        body          = json.loads(request.body)
        assinatura_b64 = body.get('assinatura', '')
        nome_assinante = (body.get('nome') or '').strip()
    except Exception:
        return JsonResponse({'ok': False, 'erro': 'Dados inválidos.'}, status=400)

    if not assinatura_b64 or not nome_assinante:
        return JsonResponse({'ok': False, 'erro': 'Nome e assinatura são obrigatórios.'}, status=400)

    # Salva assinatura
    contrato.assinatura_img = assinatura_b64
    contrato.nome_assinante = nome_assinante
    contrato.ip_assinante   = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
    contrato.assinado_em    = timezone.now()
    contrato.status         = 'assinado'

    # Gera PDF com assinatura embutida
    try:
        story, nome_arquivo, st_assina, loc_nome, loc_cnpj = _build_contrato_story(contrato.aluguel)

        # Decodifica imagem da assinatura e compõe sobre fundo branco
        img_data = assinatura_b64
        if ',' in img_data:
            img_data = img_data.split(',', 1)[1]
        img_bytes = _b64.b64decode(img_data)

        try:
            from PIL import Image as _PILImage
            _img_pil = _PILImage.open(BytesIO(img_bytes))
            if _img_pil.mode in ('RGBA', 'LA', 'P'):
                _img_pil = _img_pil.convert('RGBA')
                _bg = _PILImage.new('RGBA', _img_pil.size, (255, 255, 255, 255))
                _bg.paste(_img_pil, mask=_img_pil.split()[3])
                _img_pil = _bg.convert('RGB')
            else:
                _img_pil = _img_pil.convert('RGB')
            img_buffer = BytesIO()
            _img_pil.save(img_buffer, format='PNG')
            img_buffer.seek(0)
        except Exception:
            img_buffer = BytesIO(img_bytes)

        # Bloco de assinaturas com imagem do cliente
        from reportlab.platypus import Paragraph
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER

        assinado_em_fmt = contrato.assinado_em.strftime('%d/%m/%Y %H:%M')

        img_assinatura = RLImage(img_buffer, width=6*cm, height=2*cm)

        cfg_fin = ConfiguracaoFinanceira.objects.first()
        locador_cell = _celula_assinatura_locador(cfg_fin, st_assina, cm)

        tbl = RLTable(
            [[
                # Coluna locatário — imagem de assinatura
                [img_assinatura,
                 Paragraph(f'<b>{loc_nome}</b><br/>CNPJ: {loc_cnpj}<br/>'
                           f'Assinado em: {assinado_em_fmt}<br/>Locatário(a)', st_assina)],
                # Coluna locador — imagem salva ou linha em branco
                locador_cell,
            ]],
            colWidths=[8*cm, 8*cm],
        )
        tbl.setStyle(RLTableStyle([
            ('ALIGN',        (0,0), (-1,-1), 'CENTER'),
            ('VALIGN',       (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING',  (0,0), (-1,-1), 8),
            ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ]))
        story.append(Spacer(1, 1.2*cm))
        story.append(tbl)

        # Rodapé com hash de verificação
        from reportlab.platypus import Paragraph as P2
        st_footer = ParagraphStyle('Footer', fontName='Helvetica', fontSize=7,
                                   alignment=TA_CENTER, textColor=(0.5,0.5,0.5))
        story.append(Spacer(1, 0.5*cm))
        story.append(P2(
            f'Documento assinado digitalmente · Token: {contrato.token} · '
            f'IP: {contrato.ip_assinante} · {assinado_em_fmt}',
            st_footer
        ))

        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                rightMargin=2.5*cm, leftMargin=2.5*cm,
                                topMargin=2*cm, bottomMargin=2*cm)
        doc.build(story)
        pdf_bytes = buf.getvalue()

        contrato.pdf_assinado.save(nome_arquivo, ContentFile(pdf_bytes), save=False)
    except Exception as exc:
        import traceback as _tb
        print(f'⚠️ Erro ao gerar PDF assinado: {exc}\n{_tb.format_exc()}')

    contrato.save()

    # Envia cópia por email ao cliente (não-bloqueante)
    try:
        _enviar_contrato_email(contrato)
    except Exception as exc:
        print(f'⚠️ Erro ao enviar email contrato: {exc}')

    return JsonResponse({'ok': True, 'mensagem': 'Contrato assinado com sucesso!'})


@login_required
def download_contrato_assinado(request, token):
    """Download do PDF assinado (apenas usuários autenticados)."""
    contrato = get_object_or_404(ContratoAluguel, token=token, status='assinado')
    if not contrato.pdf_assinado:
        return HttpResponse('PDF ainda não gerado.', status=404)
    response = HttpResponse(contrato.pdf_assinado.read(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="contrato_assinado_{contrato.token}.pdf"'
    return response


@login_required
@acesso_financeiro_restrito
def listar_contratos_aluguel(request, aluguel_id):
    """Lista contratos de um aluguel (para o painel admin)."""
    aluguel   = get_object_or_404(AluguelIPv4, id=aluguel_id)
    contratos = ContratoAluguel.objects.filter(aluguel=aluguel).order_by('-criado_em')
    data = []
    for c in contratos:
        scheme = 'https' if request.is_secure() else 'http'
        link   = f'{scheme}://{request.get_host()}/financeiro/contrato/{c.token}/assinar/'
        data.append({
            'id':           c.id,
            'token':        str(c.token),
            'status':       c.status,
            'status_label': c.get_status_display(),
            'nome_assinante': c.nome_assinante,
            'assinado_em':  c.assinado_em.strftime('%d/%m/%Y %H:%M') if c.assinado_em else None,
            'criado_em':    c.criado_em.strftime('%d/%m/%Y %H:%M'),
            'expira_em':    c.expira_em.strftime('%d/%m/%Y %H:%M') if c.expira_em else None,
            'link':         link,
            'pdf_url':      c.pdf_assinado.url if c.pdf_assinado else None,
        })
    return JsonResponse({'ok': True, 'contratos': data})


@login_required
@acesso_financeiro_restrito
def assinatura_locador(request):
    """GET: retorna assinatura salva. POST: salva nova assinatura."""
    cfg = ConfiguracaoFinanceira.objects.first()
    if request.method == 'GET':
        return JsonResponse({
            'ok': True,
            'assinatura': cfg.assinatura_locador if cfg else '',
        })
    # POST
    try:
        body = json.loads(request.body)
        assinatura = (body.get('assinatura') or '').strip()
    except Exception:
        return JsonResponse({'ok': False, 'erro': 'Dados inválidos.'}, status=400)

    if not cfg:
        return JsonResponse({'ok': False, 'erro': 'Configuração financeira não encontrada.'}, status=400)

    cfg.assinatura_locador = assinatura
    cfg.save(update_fields=['assinatura_locador'])
    return JsonResponse({'ok': True})


@login_required
@acesso_financeiro_restrito
def api_painel_blocos_ip(request):
    """Retorna todos os aluguéis de IP (todos os status) com dados de faturas por bloco."""
    status_filtro = request.GET.get('status', '')
    busca = request.GET.get('busca', '').strip()

    qs = (
        AluguelIPv4.objects
        .select_related('cliente')
        .order_by('cliente__nome_empresa', 'bloco_descricao')
    )
    if status_filtro:
        qs = qs.filter(status=status_filtro)
    if busca:
        qs = qs.filter(
            Q(cliente__nome_empresa__icontains=busca) |
            Q(bloco_descricao__icontains=busca) |
            Q(bloco_v6__icontains=busca) |
            Q(cliente__cnpj__icontains=busca)
        )

    hoje = date.today()
    aluguel_ids = list(qs.values_list('id', flat=True))

    # Faturas abertas indexadas por aluguel_id
    faturas_map = {}
    if aluguel_ids:
        faturas_abertas = (
            Fatura.objects
            .filter(alugueis_ipv4__id__in=aluguel_ids, status__in=['ABERTA', 'RASCUNHO'])
            .prefetch_related('alugueis_ipv4')
            .order_by('data_vencimento')
        )
        for f in faturas_abertas:
            for aid in f.alugueis_ipv4.values_list('id', flat=True):
                if aid not in faturas_map:
                    faturas_map[aid] = []
                faturas_map[aid].append({
                    'id':         f.id,
                    'numero':     f.numero_fatura,
                    'valor':      float(f.valor_total),
                    'vencimento': f.data_vencimento.strftime('%d/%m/%Y'),
                    'vencida':    f.data_vencimento < hoje,
                    'status':     f.status,
                })

    alugueis_list = []
    clientes_ativos = set()
    total_mensal = 0.0
    counts = {'ATIVO': 0, 'PAUSADO': 0, 'CANCELADO': 0}
    total_fat_abertas = 0
    total_fat_valor = 0.0

    for a in qs:
        faturas = faturas_map.get(a.id, [])
        total_fat_abertas += len(faturas)
        total_fat_valor += sum(f['valor'] for f in faturas)
        counts[a.status] = counts.get(a.status, 0) + 1
        if a.status == 'ATIVO':
            clientes_ativos.add(a.cliente_id)
            total_mensal += float(a.valor_mensal)

        alugueis_list.append({
            'id':             a.id,
            'cliente_id':     a.cliente_id,
            'cliente_nome':   a.cliente.nome_empresa,
            'cliente_cnpj':   a.cliente.cnpj,
            'cliente_email':  a.cliente.email,
            'bloco':          a.bloco_descricao,
            'bloco_v6':       a.bloco_v6 or '',
            'qtd_ips':        a.quantidade_ips,
            'valor_mensal':   float(a.valor_mensal),
            'data_inicio':    a.data_inicio.strftime('%d/%m/%Y'),
            'status':         a.status,
            'faturas':        faturas,
            'tem_vencida':    any(f['vencida'] for f in faturas),
        })

    return JsonResponse({
        'ok': True,
        'alugueis': alugueis_list,
        'resumo': {
            'clientes_ativos':   len(clientes_ativos),
            'blocos_ativos':     counts.get('ATIVO', 0),
            'blocos_pausados':   counts.get('PAUSADO', 0),
            'blocos_cancelados': counts.get('CANCELADO', 0),
            'receita_mensal':    total_mensal,
            'fat_abertas_qtd':   total_fat_abertas,
            'fat_abertas_valor': total_fat_valor,
        }
    })


@login_required
@acesso_financeiro_restrito
def api_clientes_aluguel_ativo(request):
    """Clientes com aluguel IPv4 ativo e suas faturas abertas."""
    alugueis = (
        AluguelIPv4.objects
        .filter(status='ATIVO')
        .select_related('cliente')
        .order_by('cliente__nome_empresa', 'bloco_descricao')
    )

    clientes_map = {}
    for a in alugueis:
        cid = a.cliente_id
        if cid not in clientes_map:
            clientes_map[cid] = {
                'id':           cid,
                'nome':         a.cliente.nome_empresa,
                'cnpj':         a.cliente.cnpj,
                'email':        a.cliente.email,
                'telefone':     a.cliente.telefone or '',
                'blocos':       [],
                'faturas':      [],
                'total_mensal': 0,
            }
        clientes_map[cid]['blocos'].append({
            'bloco':       a.bloco_descricao,
            'bloco_v6':    a.bloco_v6,
            'qtd_ips':     a.quantidade_ips,
            'valor':       float(a.valor_mensal),
            'data_inicio': a.data_inicio.strftime('%d/%m/%Y'),
        })
        clientes_map[cid]['total_mensal'] += float(a.valor_mensal)

    if clientes_map:
        faturas = (
            Fatura.objects
            .filter(cliente_id__in=clientes_map.keys(), status__in=['ABERTA', 'RASCUNHO'])
            .order_by('data_vencimento')
        )
        hoje = date.today()
        for f in faturas:
            venc = f.data_vencimento
            clientes_map[f.cliente_id]['faturas'].append({
                'numero':       f.numero_fatura,
                'valor':        float(f.valor_total),
                'vencimento':   venc.strftime('%d/%m/%Y'),
                'vencida':      venc < hoje,
                'status':       f.status,
                'status_label': dict(Fatura.STATUS_CHOICES).get(f.status, f.status),
            })

    resultado = sorted(clientes_map.values(), key=lambda x: x['nome'])
    return JsonResponse({'ok': True, 'clientes': resultado})


# ═══════════════════════════════════════════════════════════════════════════════
# CARTA LOA — Letter of Authorization para anúncio de bloco IP
# ═══════════════════════════════════════════════════════════════════════════════

def _whois_lookup(prefixo_v4: str) -> dict:
    """
    Consulta WHOIS/RDAP do prefixo e retorna dict com:
      - asn:          'AS268024'
      - responsavel:  nome do contato técnico/administrativo/registrante
    """
    try:
        from ipwhois import IPWhois
        ip_base = prefixo_v4.split('/')[0]
        result  = IPWhois(ip_base).lookup_rdap(depth=1)

        asn_raw = result.get('asn', '')
        asn     = f'AS{asn_raw}' if asn_raw and not str(asn_raw).upper().startswith('AS') else str(asn_raw)

        # Coleta nomes por papel
        names_by_role: dict[str, str] = {}
        for obj_data in result.get('objects', {}).values():
            contact = obj_data.get('contact') or {}
            name    = (contact.get('name') or '').strip()
            if not name:
                continue
            for role in (obj_data.get('roles') or []):
                if role not in names_by_role:
                    names_by_role[role] = name

        # Proprietária = dono registrado do bloco (registrant)
        proprietaria = names_by_role.get('registrant', '')

        # Responsável/representante = contato técnico ou administrativo (pessoa que assina)
        responsavel = ''
        for role in ('technical', 'administrative', 'abuse', 'noc', 'registrant'):
            if role in names_by_role:
                responsavel = names_by_role[role]
                break

        return {'asn': asn, 'proprietaria': proprietaria, 'responsavel': responsavel}
    except Exception:
        return {'asn': '', 'responsavel': ''}


def _whois_asn(prefixo_v4: str) -> str:
    """Compat: retorna apenas o ASN."""
    return _whois_lookup(prefixo_v4)['asn']


def _build_loa_story(loa):
    """Constrói o story do ReportLab para a Carta LOA (sem bloco de assinatura)."""
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT

    aluguel = loa.aluguel
    cliente = aluguel.cliente

    loc_nome = cliente.nome_empresa or ''
    loc_cnpj = cliente.cnpj or ''
    bloco_v4 = aluguel.bloco_descricao or ''
    bloco_v6 = aluguel.bloco_v6 or ''
    asn_up   = loa.asn_upstream or ''

    # Proprietária = dono real do bloco (WHOIS registrant)
    prop_nome = (loa.proprietaria_whois or '').strip()
    if not prop_nome and aluguel.bloco_descricao:
        # Fallback: busca WHOIS na hora e salva para uso futuro
        wd = _whois_lookup(aluguel.bloco_descricao)
        prop_nome = wd.get('proprietaria', '')
        if prop_nome and loa.pk:
            loa.proprietaria_whois = prop_nome
            if not loa.responsavel_whois:
                loa.responsavel_whois = wd.get('responsavel', '')
            if not loa.asn_upstream:
                loa.asn_upstream = wd.get('asn', '')
            loa.save(update_fields=['proprietaria_whois', 'responsavel_whois', 'asn_upstream'])

    styles = getSampleStyleSheet()
    st_titulo = ParagraphStyle('LoaTitulo', parent=styles['Normal'],
        fontSize=13, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=24)
    st_corpo = ParagraphStyle('LoaCorpo', parent=styles['Normal'],
        fontSize=11, fontName='Helvetica', alignment=TA_JUSTIFY, leading=18, spaceAfter=12)
    st_bloco = ParagraphStyle('LoaBloco', parent=styles['Normal'],
        fontSize=12, fontName='Helvetica-Bold', alignment=TA_LEFT,
        spaceBefore=4, spaceAfter=4, leftIndent=0)
    st_assina = ParagraphStyle('LoaAssina', parent=styles['Normal'],
        fontSize=11, fontName='Helvetica', alignment=TA_CENTER, leading=16)

    blocos_linha = bloco_v4
    if bloco_v6:
        blocos_linha += f'    prefixo {bloco_v6}'

    story = []
    story.append(Paragraph('CARTA DE AUTORIZAÇÃO', st_titulo))
    story.append(Paragraph('A quem possa interessar,', st_corpo))
    story.append(Paragraph(
        f'Esta carta serve como autorização para a <b>{loc_nome}</b> com CNPJ '
        f'<b>{loc_cnpj}</b> anunciar o seguinte bloco de endereços IP:',
        st_corpo
    ))
    story.append(Paragraph(f'[BLOCO / AS]  {blocos_linha}', st_bloco))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        'Esta carta serve como autorização para os seguintes Upstreams realizarem o '
        'trânsito BGP do bloco acima:',
        st_corpo
    ))
    story.append(Paragraph(f'<b>{asn_up}</b>', st_bloco))
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(
        f'Como representante da empresa <b>{prop_nome}</b> proprietária da sub-rede e / '
        f'ou ASN, declaro que estou autorizado por a representar e assinar esta LOA.',
        st_corpo
    ))

    return story, st_assina, prop_nome


@login_required
@require_http_methods(['POST'])
def api_gerar_carta_loa(request, aluguel_id):
    """Cria (ou retorna existente) uma CartaLOA com link único para assinatura."""
    aluguel = get_object_or_404(AluguelIPv4, id=aluguel_id)

    dias   = int(request.POST.get('dias_validade', 30))
    expira = timezone.now() + timedelta(days=dias)

    # WHOIS: ASN + nome do responsável técnico
    cfg       = ConfiguracaoFinanceira.objects.first()
    asn_up    = (cfg.asn_proprio or '').strip() if cfg else ''
    whois_data = {}
    if aluguel.bloco_descricao:
        whois_data = _whois_lookup(aluguel.bloco_descricao)
    if not asn_up:
        asn_up = whois_data.get('asn', '')
    proprietaria = whois_data.get('proprietaria', '')
    responsavel  = whois_data.get('responsavel', '')

    loa = CartaLOA.objects.filter(aluguel=aluguel, status='pendente').first()
    if not loa:
        loa = CartaLOA.objects.create(
            aluguel=aluguel,
            asn_upstream=asn_up,
            proprietaria_whois=proprietaria,
            responsavel_whois=responsavel,
            expira_em=expira,
        )
    else:
        loa.expira_em          = expira
        loa.asn_upstream       = asn_up or loa.asn_upstream
        loa.proprietaria_whois = proprietaria or loa.proprietaria_whois
        loa.responsavel_whois  = responsavel or loa.responsavel_whois
        loa.save(update_fields=['expira_em', 'asn_upstream', 'proprietaria_whois', 'responsavel_whois'])

    scheme = 'https' if request.is_secure() else 'http'
    link   = f'{scheme}://{request.get_host()}/financeiro/loa/{loa.token}/assinar/'
    return JsonResponse({
        'ok': True, 'link': link,
        'token': str(loa.token),
        'asn_upstream':  loa.asn_upstream,
        'proprietaria':  loa.proprietaria_whois,
        'responsavel':   loa.responsavel_whois,
        'expira_em':     loa.expira_em.strftime('%d/%m/%Y %H:%M'),
    })


def assinar_loa(request, token):
    """Página pública onde o representante da locadora assina a LOA."""
    loa = get_object_or_404(CartaLOA, token=token)

    if loa.status == 'assinado':
        return render(request, 'financeiro/loa_assinada.html', {'loa': loa})
    if loa.esta_expirado():
        loa.status = 'expirado'
        loa.save(update_fields=['status'])
        return render(request, 'financeiro/loa_expirada.html', {'loa': loa})

    aluguel = loa.aluguel
    cliente = aluguel.cliente
    cfg     = ConfiguracaoFinanceira.objects.first()
    return render(request, 'financeiro/loa_assinar.html', {
        'loa': loa, 'aluguel': aluguel, 'cliente': cliente, 'cfg': cfg,
    })


@csrf_exempt
@require_http_methods(['POST'])
def confirmar_assinatura_loa(request, token):
    """Recebe assinatura do responsável da locadora, gera PDF e finaliza a LOA."""
    import base64 as _b64
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table as RLTable, TableStyle as RLTableStyle, Image as RLImage
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    loa = get_object_or_404(CartaLOA, token=token)
    if loa.status == 'assinado':
        return JsonResponse({'ok': False, 'erro': 'LOA já assinada.'}, status=400)
    if loa.esta_expirado():
        return JsonResponse({'ok': False, 'erro': 'Link expirado.'}, status=400)

    try:
        body           = json.loads(request.body)
        assinatura_b64 = body.get('assinatura', '')
        nome_assinante = (body.get('nome') or '').strip()
    except Exception:
        return JsonResponse({'ok': False, 'erro': 'Dados inválidos.'}, status=400)

    if not assinatura_b64 or not nome_assinante:
        return JsonResponse({'ok': False, 'erro': 'Nome e assinatura são obrigatórios.'}, status=400)

    loa.assinatura_img = assinatura_b64
    loa.nome_assinante = nome_assinante
    loa.ip_assinante   = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
    loa.assinado_em    = timezone.now()
    loa.status         = 'assinado'

    try:
        story, st_assina, prop_nome = _build_loa_story(loa)
        prop_cnpj = ''

        img_data = assinatura_b64
        if ',' in img_data:
            img_data = img_data.split(',', 1)[1]
        img_bytes = _b64.b64decode(img_data)

        try:
            from PIL import Image as _PILImage
            _img = _PILImage.open(BytesIO(img_bytes))
            if _img.mode in ('RGBA', 'LA', 'P'):
                _img = _img.convert('RGBA')
                _bg = _PILImage.new('RGBA', _img.size, (255, 255, 255, 255))
                _bg.paste(_img, mask=_img.split()[3])
                _img = _bg.convert('RGB')
            else:
                _img = _img.convert('RGB')
            _buf = BytesIO(); _img.save(_buf, format='PNG'); _buf.seek(0)
        except Exception:
            _buf = BytesIO(img_bytes)

        from reportlab.platypus import Paragraph, Spacer
        assinado_em_fmt = loa.assinado_em.strftime('%d/%m/%Y %H:%M')
        img_ass = RLImage(_buf, width=6*cm, height=2*cm)

        from reportlab.lib.styles import ParagraphStyle as PS
        st_hr = PS('LoaHR', fontName='Helvetica', fontSize=9,
                   alignment=TA_CENTER, leading=13, textColor=(0.3, 0.3, 0.3))

        cnpj_linha = f'CNPJ: {prop_cnpj}<br/>' if prop_cnpj else ''
        tbl = RLTable([[
            [img_ass,
             Paragraph(
                f'<b>{prop_nome}</b><br/>'
                f'{cnpj_linha}'
                f'Assinado em: {assinado_em_fmt}<br/>'
                f'Locador(a) — Proprietário(a) do bloco',
                st_assina
             )],
        ]], colWidths=[14*cm])
        tbl.setStyle(RLTableStyle([
            ('ALIGN',  (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LINEABOVE', (0, 0), (-1, 0), 0.5, (0.6, 0.6, 0.6)),
            ('LINEBELOW', (0, -1), (-1, -1), 0.5, (0.6, 0.6, 0.6)),
        ]))

        story.append(Spacer(1, 1.2*cm))
        story.append(tbl)
        story.append(Spacer(1, 0.4*cm))

        from reportlab.platypus import Paragraph as P2
        st_footer = PS('LoaFooter', fontName='Helvetica', fontSize=7,
                       alignment=TA_CENTER, textColor=(0.5, 0.5, 0.5))
        story.append(P2(
            f'Documento assinado digitalmente · Token: {loa.token} · '
            f'IP: {loa.ip_assinante} · {assinado_em_fmt}',
            st_footer
        ))

        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                rightMargin=2.5*cm, leftMargin=2.5*cm,
                                topMargin=2.5*cm, bottomMargin=2*cm,
                                title=f'Carta LOA — {loa.aluguel.bloco_descricao}')
        doc.build(story)

        bloco_safe = loa.aluguel.bloco_descricao.replace('/', '-')
        loa.pdf_assinado.save(
            f'loa_{bloco_safe}_{loa.token}.pdf',
            ContentFile(buf.getvalue()), save=False
        )
    except Exception as exc:
        import traceback as _tb
        print(f'⚠️ Erro ao gerar PDF LOA: {exc}\n{_tb.format_exc()}')

    loa.save()

    try:
        _enviar_loa_email(loa)
    except Exception as exc:
        print(f'⚠️ Erro ao enviar email LOA: {exc}')

    return JsonResponse({'ok': True, 'mensagem': 'LOA assinada com sucesso!'})


def _enviar_loa_email(loa):
    """Envia PDF da LOA assinada por email ao cliente."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase
    from email.mime.text import MIMEText
    from email import encoders as _enc
    from clientes.models import ConfiguracaoSistema

    cliente = loa.aluguel.cliente
    email_destino = getattr(cliente, 'email', '') or ''
    if not email_destino:
        return False

    smtp_cfg  = ConfiguracaoSistema.get()
    smtp_host = smtp_cfg.smtp_host or ''
    smtp_port = int(smtp_cfg.smtp_port or 587)
    smtp_user = smtp_cfg.smtp_user or ''
    smtp_pass = smtp_cfg.smtp_pass or ''
    remetente = smtp_cfg.smtp_from or smtp_user
    use_tls   = smtp_cfg.smtp_use_tls

    if not smtp_host or not smtp_user:
        return False

    bloco_v4 = loa.aluguel.bloco_descricao
    bloco_v6 = loa.aluguel.bloco_v6 or ''
    blocos   = bloco_v4 + (f' / {bloco_v6}' if bloco_v6 else '')
    asn      = loa.asn_upstream
    assinado = loa.assinado_em.strftime('%d/%m/%Y às %H:%M') if loa.assinado_em else ''

    corpo_html = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;">
      <div style="background:#161b22;padding:24px;text-align:center;">
        <h2 style="color:#e6edf3;margin:0;">Carta de Autorização (LOA) Assinada</h2>
      </div>
      <div style="padding:24px;background:#fff;">
        <p>Olá, <b>{cliente.nome_empresa}</b>!</p>
        <p>Segue em anexo a Carta de Autorização (LOA) assinada para o seu bloco IP.</p>
        <div style="background:#f5f5f5;border-radius:6px;padding:16px;margin:16px 0;font-size:.9rem;">
          <p style="margin:0 0 4px;"><b>Bloco(s):</b> {blocos}</p>
          <p style="margin:0 0 4px;"><b>ASN Upstream:</b> {asn}</p>
          <p style="margin:0 0 4px;"><b>Assinada em:</b> {assinado}</p>
          <p style="margin:0;"><b>Token:</b> <code>{loa.token}</code></p>
        </div>
        <p style="font-size:.85rem;color:#555;">Apresente este documento ao seu upstream para liberar o anúncio BGP.</p>
      </div>
    </div>
    """

    msg = MIMEMultipart('mixed')
    msg['Subject'] = f'Carta LOA — {blocos}'
    msg['From']    = remetente
    msg['To']      = email_destino
    msg.attach(MIMEText(corpo_html, 'html', 'utf-8'))

    if loa.pdf_assinado:
        try:
            pdf_bytes = loa.pdf_assinado.read()
            part = MIMEBase('application', 'pdf')
            part.set_payload(pdf_bytes)
            _enc.encode_base64(part)
            part.add_header('Content-Disposition',
                            f'attachment; filename="loa_{bloco_v4.replace("/","-")}.pdf"')
            msg.attach(part)
        except Exception:
            pass

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.ehlo()
        if use_tls:
            server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(remetente, [email_destino], msg.as_string())
    return True


@login_required
def preview_carta_loa(request, aluguel_id):
    """Gera e retorna PDF de prévia da LOA (sem assinatura) para visualização."""
    from reportlab.platypus import SimpleDocTemplate, Spacer, Paragraph
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    aluguel = get_object_or_404(AluguelIPv4, id=aluguel_id)

    # Usa LOA pendente existente ou monta um objeto temporário com dados do aluguel
    loa = CartaLOA.objects.filter(aluguel=aluguel).order_by('-criado_em').first()
    if not loa:
        # Objeto temporário (não salvo) só para gerar a prévia
        cfg        = ConfiguracaoFinanceira.objects.first()
        asn_up     = (cfg.asn_proprio or '').strip() if cfg else ''
        whois_data = _whois_lookup(aluguel.bloco_descricao) if aluguel.bloco_descricao else {}
        if not asn_up:
            asn_up = whois_data.get('asn', '')
        loa = CartaLOA(aluguel=aluguel, asn_upstream=asn_up,
                       proprietaria_whois=whois_data.get('proprietaria', ''),
                       responsavel_whois=whois_data.get('responsavel', ''))

    story, st_assina, prop_nome = _build_loa_story(loa)
    prop_cnpj = ''

    # Bloco de assinatura em branco (prévia)
    from reportlab.platypus import Table as RLTable, TableStyle as RLTableStyle, HRFlowable
    st_previa = ParagraphStyle('Previa', fontName='Helvetica-Oblique', fontSize=9,
                               alignment=TA_CENTER, textColor=(0.6, 0.6, 0.6))
    tbl = RLTable([[
        [Spacer(1, 1.8*cm),
         Paragraph(f'<b>{prop_nome}</b><br/>{"CNPJ: "+prop_cnpj+"<br/>" if prop_cnpj else ""}Locador(a) — Proprietário(a) do bloco', st_assina)],
    ]], colWidths=[14*cm])
    tbl.setStyle(RLTableStyle([
        ('ALIGN',     (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN',    (0, 0), (-1, -1), 'TOP'),
        ('LINEABOVE', (0, 0), (-1, 0),  0.5, (0.6, 0.6, 0.6)),
        ('LINEBELOW', (0, -1), (-1, -1), 0.5, (0.6, 0.6, 0.6)),
    ]))
    story.append(Spacer(1, 1.2*cm))
    story.append(tbl)
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph('[ PRÉVIA — Assinatura será inserida após a confirmação digital ]', st_previa))

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            rightMargin=2.5*cm, leftMargin=2.5*cm,
                            topMargin=2.5*cm, bottomMargin=2*cm,
                            title=f'Prévia LOA — {aluguel.bloco_descricao}')
    doc.build(story)

    bloco_safe = aluguel.bloco_descricao.replace('/', '-')
    response = HttpResponse(buf.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="previa_loa_{bloco_safe}.pdf"'
    return response


@login_required
def download_loa_assinada(request, token):
    loa = get_object_or_404(CartaLOA, token=token, status='assinado')
    if not loa.pdf_assinado:
        return HttpResponse('PDF ainda não gerado.', status=404)
    response = HttpResponse(loa.pdf_assinado.read(), content_type='application/pdf')
    bloco_safe = loa.aluguel.bloco_descricao.replace('/', '-')
    response['Content-Disposition'] = f'attachment; filename="loa_{bloco_safe}.pdf"'
    return response


@login_required
def api_status_loa(request, aluguel_id):
    """Retorna lista de LOAs de um aluguel."""
    aluguel = get_object_or_404(AluguelIPv4, id=aluguel_id)
    loas = CartaLOA.objects.filter(aluguel=aluguel).order_by('-criado_em')
    data = []
    for loa in loas:
        scheme = 'https' if request.is_secure() else 'http'
        data.append({
            'id':               loa.id,
            'token':            str(loa.token),
            'status':           loa.status,
            'status_label':     dict(CartaLOA.STATUS_CHOICES).get(loa.status, loa.status),
            'asn_upstream':     loa.asn_upstream,
            'proprietaria':     loa.proprietaria_whois,
            'responsavel':      loa.responsavel_whois,
            'criado_em':        loa.criado_em.strftime('%d/%m/%Y %H:%M'),
            'assinado_em':  loa.assinado_em.strftime('%d/%m/%Y %H:%M') if loa.assinado_em else None,
            'expira_em':    loa.expira_em.strftime('%d/%m/%Y %H:%M') if loa.expira_em else None,
            'link_assinatura': f'{scheme}://{request.get_host()}/financeiro/loa/{loa.token}/assinar/',
            'link_download':   f'{scheme}://{request.get_host()}/financeiro/loa/{loa.token}/download/' if loa.status == 'assinado' else None,
        })
    return JsonResponse({'ok': True, 'loas': data})


# ============================================
# WhatsApp — Configuração e Cobranças
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(['POST'])
def api_salvar_config_whatsapp(request):
    """Salva configuração da Evolution API financeira."""
    cfg, _ = ConfiguracaoFinanceira.objects.get_or_create(pk=1)
    data = json.loads(request.body)
    cfg.wa_ativo         = bool(data.get('wa_ativo', False))
    cfg.wa_evolution_url = data.get('wa_evolution_url', '').strip()
    cfg.wa_api_key       = data.get('wa_api_key', '').strip()
    cfg.wa_instance      = data.get('wa_instance', '').strip()
    cfg.wa_dias_antes    = int(data.get('wa_dias_antes', 3) or 3)
    cfg.wa_hora_envio    = data.get('wa_hora_envio', '09:00').strip()
    cfg.wa_msg_aviso     = data.get('wa_msg_aviso', '').strip()
    cfg.wa_msg_vencido   = data.get('wa_msg_vencido', '').strip()
    cfg.wa_pix_chave     = data.get('wa_pix_chave', '').strip()
    cfg.wa_pix_tipo      = data.get('wa_pix_tipo', '').strip()
    cfg.save()
    return JsonResponse({'ok': True, 'msg': 'Configuração WhatsApp salva.'})


@login_required
@acesso_financeiro_restrito
@require_http_methods(['POST'])
def api_testar_whatsapp(request):
    """Testa a conexão com a Evolution API financeira."""
    from .whatsapp import testar_conexao
    ok, msg = testar_conexao()
    return JsonResponse({'ok': ok, 'msg': msg})


@login_required
@acesso_financeiro_restrito
@require_http_methods(['POST'])
def api_enviar_cobranca_manual(request):
    """Envia cobrança manual via WhatsApp para uma fatura específica (ignora wa_ativo)."""
    from .whatsapp import montar_texto_cobranca, enviar_mensagem, _normalizar_telefone
    data = json.loads(request.body)
    fatura_id = data.get('fatura_id')
    if not fatura_id:
        return JsonResponse({'ok': False, 'erro': 'fatura_id obrigatório.'}, status=400)
    try:
        fatura = Fatura.objects.prefetch_related(
            'consultorias', 'alugueis_ipv4'
        ).select_related('cliente').get(pk=fatura_id)
    except Fatura.DoesNotExist:
        return JsonResponse({'ok': False, 'erro': 'Fatura não encontrada.'}, status=404)

    jid = _normalizar_telefone(getattr(fatura.cliente, 'telefone', None))
    if not jid:
        return JsonResponse({'ok': False, 'erro': f'Telefone inválido ou ausente no cadastro do cliente: {fatura.cliente.telefone!r}'})

    texto = montar_texto_cobranca(fatura)
    ok, detalhe = enviar_mensagem(jid, texto)
    return JsonResponse({'ok': ok, 'msg': detalhe if ok else None, 'erro': detalhe if not ok else None})


@login_required
@acesso_financeiro_restrito
def api_config_whatsapp(request):
    """Retorna a configuração atual do WhatsApp financeiro."""
    try:
        cfg = ConfiguracaoFinanceira.objects.get(pk=1)
    except ConfiguracaoFinanceira.DoesNotExist:
        return JsonResponse({'ok': True, 'config': {}})
    return JsonResponse({'ok': True, 'config': {
        'wa_ativo':         cfg.wa_ativo,
        'wa_evolution_url': cfg.wa_evolution_url,
        'wa_api_key':       cfg.wa_api_key,
        'wa_instance':      cfg.wa_instance,
        'wa_dias_antes':    cfg.wa_dias_antes,
        'wa_hora_envio':    cfg.wa_hora_envio,
        'wa_msg_aviso':     cfg.wa_msg_aviso,
        'wa_msg_vencido':   cfg.wa_msg_vencido,
        'wa_pix_chave':     cfg.wa_pix_chave,
        'wa_pix_tipo':      cfg.wa_pix_tipo,
    }})


@login_required
@acesso_financeiro_restrito
@require_http_methods(['POST'])
def api_preview_whatsapp(request):
    """
    Retorna preview da mensagem de cobrança usando uma fatura real.
    Opcionalmente envia para um número de teste.
    """
    from .whatsapp import montar_texto_cobranca, enviar_mensagem, _normalizar_telefone
    data = json.loads(request.body)
    fatura_id  = data.get('fatura_id')
    numero_teste = data.get('numero_teste', '').strip()

    # Busca uma fatura aberta para usar como exemplo
    if fatura_id:
        try:
            fatura = Fatura.objects.prefetch_related(
                'consultorias', 'alugueis_ipv4', 'cliente'
            ).get(pk=fatura_id)
        except Fatura.DoesNotExist:
            return JsonResponse({'ok': False, 'erro': 'Fatura não encontrada.'}, status=404)
    else:
        fatura = Fatura.objects.filter(
            status__in=['ABERTA', 'RASCUNHO']
        ).prefetch_related('consultorias', 'alugueis_ipv4', 'cliente').first()
        if not fatura:
            return JsonResponse({'ok': False, 'erro': 'Nenhuma fatura aberta encontrada para preview.'})

    texto = montar_texto_cobranca(fatura)

    resultado = {'ok': True, 'preview': texto, 'fatura': {
        'id': fatura.id,
        'numero': fatura.numero_fatura,
        'cliente': fatura.cliente.nome_empresa,
        'valor': float(fatura.valor_total),
        'vencimento': fatura.data_vencimento.strftime('%d/%m/%Y'),
        'status': fatura.status,
    }}

    # Se forneceu número de teste, envia agora
    if numero_teste:
        jid = _normalizar_telefone(numero_teste)
        if not jid:
            resultado['envio_erro'] = f'Número inválido: {numero_teste}'
        else:
            ok, detalhe = enviar_mensagem(jid, texto)
            if ok:
                resultado['envio_ok'] = f'Mensagem enviada para {numero_teste}!'
            else:
                resultado['envio_erro'] = detalhe

    return JsonResponse(resultado)
