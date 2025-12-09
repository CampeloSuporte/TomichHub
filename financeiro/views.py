
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.db.models import Q, Sum, Count, F
from django.utils import timezone
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
import json
import traceback
import calendar
import os
# ✅ ADICIONE ESTES IMPORTS (para o sistema de pagamento)
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, Image
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch, cm
from reportlab.lib import colors
from io import BytesIO
from django.core.files.base import ContentFile
import datetime as dt  # Adicionar se conflitar com datetime.date

from clientes.models import Cliente, BlocoIP
from .models import Consultoria, AluguelIPv4, Fatura, ConfiguracaoFinanceira,Pagamento
from .decorators import acesso_financeiro_restrito  
from django.db import transaction
from django.db.models.functions import Substr, Cast
# ============================================
# VIEWS RENDERIZADAS
# ============================================

@login_required
@acesso_financeiro_restrito
def dashboard_financeiro(request):
    """
    Dashboard de Financeiro
    - Sem cliente_id: Mostra visão executiva com todos os clientes
    - Com cliente_id: Mostra detalhes do cliente
    """
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
        
        # Consultorias
        consultorias = Consultoria.objects.filter(cliente=cliente)
        total_consultorias = consultorias.aggregate(total=Sum('valor_unitario'))['total'] or 0
        
        # Alugueis
        alugueis = AluguelIPv4.objects.filter(cliente=cliente)
        total_alugueis = alugueis.aggregate(total=Sum('valor_mensal'))['total'] or 0
        
        # Faturas pagas
        faturas_pagas = Fatura.objects.filter(cliente=cliente, status='PAGA')
        total_pago = faturas_pagas.aggregate(total=Sum('valor_total'))['total'] or 0
        
        # Saldo em aberto
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
# API: DASHBOARD INICIAL (CORRIGIDO)
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_dashboard_financeiro(request):
    """
    Retorna dados para o dashboard inicial (visão executiva)
    
    ✅ CORRIGIDO: 
    - total_faturamento mostra APENAS faturas que foram PAGAS
    - Mostra apenas faturas que VENCERAM (data_vencimento <= hoje)
    """
    try:
        if not request.user.is_staff:
            return JsonResponse({
                'sucesso': False,
                'erro': 'Acesso negado'
            }, status=403)
        
        hoje = date.today()
        inicio_mes = hoje.replace(day=1)
        
        # ===== SALDO EM ABERTO (APENAS VENCIDO) =====
        faturas_vencidas_abertas = Fatura.objects.filter(
            status='ABERTA',
            data_vencimento__lte=hoje  # ✅ VENCIDAS
        )
        saldo_em_aberto_vencido = faturas_vencidas_abertas.aggregate(
            total=Sum('valor_total')
        )['total'] or 0
        faturas_vencidas_count = faturas_vencidas_abertas.count()
        
        # ===== PROSPECÇÃO (CONTRATOS FUTUROS - NÃO VENCIDOS) =====
        faturas_futuras_abertas = Fatura.objects.filter(
            status='ABERTA',
            data_vencimento__gt=hoje  # ✅ FUTURAS
        )
        prospeccao_valor = faturas_futuras_abertas.aggregate(
            total=Sum('valor_total')
        )['total'] or 0
        faturas_futuras_count = faturas_futuras_abertas.count()
        
        # ===== FATURAMENTO DO MÊS (APENAS FATURAS PAGAS) =====
        # ✅ CORRIGIDO: Agora mostra o valor que REALMENTE entrou (faturas pagas)
        faturas_mes_pagas = Fatura.objects.filter(
            data_emissao__gte=inicio_mes,
            status='PAGA'  # ← APENAS FATURAS PAGAS
        )
        total_faturamento = faturas_mes_pagas.aggregate(
            total=Sum('valor_total')
        )['total'] or 0
        total_faturas_pagas = faturas_mes_pagas.count()
        
        # ===== CLIENTES COM BOLETO VENCIDO =====
        clientes_com_boleto_vencido = Cliente.objects.filter(
            faturas__status='ABERTA',
            faturas__data_vencimento__lte=hoje  # ✅ APENAS VENCIDOS
        ).distinct()
        
        clientes_abertos_list = []
        for cliente in clientes_com_boleto_vencido:
            # Contar apenas boletos vencidos
            boletos_vencidos = Fatura.objects.filter(
                cliente=cliente,
                status='ABERTA',
                data_vencimento__lte=hoje
            ).count()
            
            # Somar apenas valores vencidos
            valor_total_vencido = Fatura.objects.filter(
                cliente=cliente,
                status='ABERTA',
                data_vencimento__lte=hoje
            ).aggregate(total=Sum('valor_total'))['total'] or 0
            
            clientes_abertos_list.append({
                'id': cliente.id,
                'nome_empresa': cliente.nome_empresa,
                'cnpj': cliente.cnpj,
                'boletos_abertos': boletos_vencidos,
                'valor_total': float(valor_total_vencido),
                'dias_atraso': (hoje - Fatura.objects.filter(
                    cliente=cliente,
                    status='ABERTA',
                    data_vencimento__lte=hoje
                ).order_by('data_vencimento').first().data_vencimento if Fatura.objects.filter(
                    cliente=cliente,
                    status='ABERTA',
                    data_vencimento__lte=hoje
                ).exists() else hoje).days,
            })
        
        # Ordenar por valor em aberto (maior primeiro)
        clientes_abertos_list.sort(key=lambda x: x['valor_total'], reverse=True)
        
        return JsonResponse({
            'sucesso': True,
            # Saldo vencido (em atraso)
            'total_em_aberto': float(saldo_em_aberto_vencido),
            'faturas_vencidas': faturas_vencidas_count,
            'clientes_devendo': len(clientes_com_boleto_vencido),
            
            # Prospecção (futuro)
            'prospeccao_valor': float(prospeccao_valor),
            'faturas_futuras': faturas_futuras_count,
            
            # Faturamento do mês (AGORA APENAS PAGAS)
            'total_faturamento': float(total_faturamento),
            'total_faturas': total_faturas_pagas,  # ← Faturas pagas
            
            # Lista de clientes em atraso
            'clientes_abertos': clientes_abertos_list,
        })
    
    except Exception as e:
        print(f"Erro em api_dashboard_financeiro: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': traceback.format_exc()
        }, status=500)


# ============================================
# API: PESQUISAR CLIENTES
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_pesquisar_clientes(request):
    """
    Pesquisa clientes por nome, CNPJ ou email
    """
    try:
        if not request.user.is_staff:
            return JsonResponse({
                'sucesso': False,
                'erro': 'Acesso negado'
            }, status=403)
        
        termo = request.GET.get('termo', '').strip()
        
        if len(termo) < 2:
            return JsonResponse({
                'sucesso': True,
                'total': 0,
                'clientes': []
            })
        
        # Buscar clientes
        clientes = Cliente.objects.filter(
            Q(nome_empresa__icontains=termo) |
            Q(cnpj__icontains=termo) |
            Q(email__icontains=termo)
        )[:20]
        
        resultado = []
        hoje = date.today()
        
        for cliente in clientes:
            # Contar apenas boletos VENCIDOS
            boletos_abertos = Fatura.objects.filter(
                cliente=cliente,
                status='ABERTA',
                data_vencimento__lte=hoje
            ).count()
            
            resultado.append({
                'id': cliente.id,
                'nome_empresa': cliente.nome_empresa,
                'cnpj': cliente.cnpj,
                'email': cliente.email,
                'boletos_abertos': boletos_abertos,
            })
        
        return JsonResponse({
            'sucesso': True,
            'total': len(resultado),
            'clientes': resultado
        })
    
    except Exception as e:
        print(f"Erro em api_pesquisar_clientes: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': traceback.format_exc()
        }, status=500)


# ============================================
# API: VISUALIZAR FATURA
# ============================================

@login_required
@require_http_methods(["GET"])
def api_visualizar_fatura(request, fatura_id):
    """
    Retorna os dados completos de uma fatura em JSON
    
    URL: /financeiro/api/fatura/<id>/
    
    ✅ CORRIGIDO: Usa consultorias e alugueis_ipv4 em vez de itens
    """
    try:
        fatura = Fatura.objects.get(id=fatura_id)
        
        # Buscar pagamentos da fatura
        pagamentos = Pagamento.objects.filter(fatura=fatura)
        total_pago = pagamentos.aggregate(Sum('valor'))['valor__sum'] or 0
        
        # Montar resposta JSON
        data = {
            'sucesso': True,
            'fatura': {
                'id': fatura.id,
                'numero_fatura': fatura.numero_fatura,
                'status': fatura.status,
                'valor_total': float(fatura.valor_total),
                'data_vencimento': fatura.data_vencimento.strftime('%d/%m/%Y'),
                'data_criacao': fatura.data_emissao.strftime('%d/%m/%Y'),  # ← Correto: data_emissao
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
        
        # ✅ CORRIGIDO: Adicionar itens - CONSULTORIAS
        for consultoria in fatura.consultorias.all():
            data['fatura']['itens'].append({
                'id': consultoria.id,
                'descricao': consultoria.descricao,
                'tipo': 'CONSULTORIA',
                'valor': float(consultoria.valor_unitario),
            })
        
        # ✅ CORRIGIDO: Adicionar itens - ALUGUEIS IPv4
        for aluguel in fatura.alugueis_ipv4.all():
            data['fatura']['itens'].append({
                'id': aluguel.id,
                'descricao': aluguel.bloco_descricao,
                'tipo': 'ALUGUEL_IPV4',
                'valor': float(aluguel.valor_mensal),
            })
        
        print(f'✅ Fatura {fatura_id} carregada com sucesso')
        print(f'   Consultorias: {fatura.consultorias.count()}')
        print(f'   Alugueis: {fatura.alugueis_ipv4.count()}')
        
        return JsonResponse(data)
    
    except Fatura.DoesNotExist:
        print(f'❌ Fatura {fatura_id} não encontrada')
        return JsonResponse({
            'sucesso': False,
            'erro': f'Fatura {fatura_id} não encontrada'
        }, status=404)
    
    except Exception as e:
        print(f'❌ Erro ao carregar fatura: {str(e)}')
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': str(e)
        }, status=500)


# ============================================
# API: CRIAR CONSULTORIA
# ============================================

@login_required
@require_http_methods(["POST"])
def api_criar_consultoria(request):
    """
    Cria uma consultoria e gera faturas automaticamente
    ✅ CORRIGIDO: Melhor validação e tratamento de erros
    """
    print("\n" + "="*70)
    print("📝 api_criar_consultoria chamada")
    print("="*70)
    
    try:
        cliente_id = request.POST.get('cliente_id')
        descricao = request.POST.get('descricao')
        valor_unitario = float(request.POST.get('valor_unitario'))
        quantidade_meses = int(request.POST.get('quantidade_meses', 1))
        periodicidade = request.POST.get('periodicidade', 'MENSAL')
        data_inicio_str = request.POST.get('data_inicio')
        
        print(f"  cliente_id: {cliente_id}")
        print(f"  descricao: {descricao}")
        print(f"  valor_unitario: {valor_unitario}")
        print(f"  quantidade_meses: {quantidade_meses}")
        print(f"  periodicidade: {periodicidade}")
        print(f"  data_inicio: {data_inicio_str}")
        
        # Validações
        if not cliente_id or not descricao or not valor_unitario or not data_inicio_str:
            msg = "Preencha todos os campos obrigatórios"
            print(f"❌ {msg}")
            return JsonResponse({
                'sucesso': False,
                'erro': msg
            }, status=400)
        
        cliente = get_object_or_404(Cliente, id=cliente_id)
        data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
        
        print(f"\n✅ Cliente encontrado: {cliente.nome_empresa}")
        print(f"✅ Data de início: {data_inicio}")
        
        # Criar consultoria
        consultoria = Consultoria.objects.create(
            cliente=cliente,
            descricao=descricao,
            valor_unitario=valor_unitario,
            quantidade_meses=quantidade_meses,
            periodicidade=periodicidade,
            data_inicio=data_inicio
        )
        
        print(f"\n✅ Consultoria criada com ID: {consultoria.id}")
        
        # Gerar faturas automaticamente
        print(f"\n📋 Gerando {quantidade_meses} fatura(s)...")
        faturas_geradas = gerar_faturas_consultoria(consultoria)
        
        print(f"\n✅ {len(faturas_geradas)} fatura(s) gerada(s):")
        for f in faturas_geradas:
            print(f"   - {f['numero_fatura']}: R$ {f['valor']:.2f} (vence {f['vencimento']})")
        
        print("\n" + "="*70)
        print("✅ CONSULTORIA CRIADA COM SUCESSO!")
        print("="*70 + "\n")
        
        return JsonResponse({
            'sucesso': True,
            'mensagem': f'Consultoria criada com sucesso! {len(faturas_geradas)} fatura(s) gerada(s).',
            'consultoria': {
                'id': consultoria.id,
                'descricao': consultoria.descricao,
                'quantidade_meses': consultoria.quantidade_meses,
            },
            'faturas': faturas_geradas
        })
    
    except ValueError as e:
        print(f"❌ ValueError: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': f'Erro de validação: {str(e)}'
        }, status=400)
    except Exception as e:
        print(f"❌ Erro em api_criar_consultoria: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': traceback.format_exc()
        }, status=500)


def gerar_faturas_consultoria(consultoria):
    """
    Gera N faturas para uma consultoria de N meses
    ✅ CORRIGIDO: Melhor debug e validação
    """
    print(f"\n📋 gerar_faturas_consultoria iniciada")
    print(f"   Consultoria: {consultoria.descricao}")
    print(f"   Meses: {consultoria.quantidade_meses}")
    print(f"   Valor: R$ {consultoria.valor_unitario:.2f}")
    print(f"   Data inicial: {consultoria.data_inicio}")
    
    faturas = []
    data_inicio = consultoria.data_inicio
    
    for mes in range(consultoria.quantidade_meses):
        print(f"\n   📅 Gerando fatura {mes + 1}/{consultoria.quantidade_meses}...")
        
        # Calcular datas
        data_fatura = data_inicio + relativedelta(months=mes)
        # Vencimento no primeiro dia do próximo mês
        data_vencimento = (data_fatura + relativedelta(months=1)).replace(day=1)
        
        print(f"      Data da fatura: {data_fatura}")
        print(f"      Data de vencimento: {data_vencimento}")
        
        # Gerar número de fatura
        numero_fatura = gerar_numero_fatura_unico()
        print(f"      Número da fatura: {numero_fatura}")
        
        # Criar fatura
        fatura = Fatura.objects.create(
            cliente=consultoria.cliente,
            numero_fatura=numero_fatura,
            tipo='CONSULTORIA',
            valor_total=consultoria.valor_unitario,
            data_vencimento=data_vencimento,
            status='ABERTA'
        )
        
        print(f"      ✅ Fatura criada com ID: {fatura.id}")
        
        # Associar consultoria
        fatura.consultorias.add(consultoria)
        print(f"      ✅ Consultoria associada à fatura")
        
        faturas.append({
            'numero_fatura': fatura.numero_fatura,
            'valor': float(fatura.valor_total),
            'vencimento': fatura.data_vencimento.strftime('%d/%m/%Y'),
            'mes': mes + 1,
        })
    
    print(f"\n✅ Função concluída: {len(faturas)} fatura(s) criada(s)")
    return faturas


def gerar_numero_fatura_unico():
    """
    Gera número de fatura único e seguro com lock do banco
    
    ✅ CORRIGIDO: Usa transaction.atomic() para garantir atomicidade
    """
    try:
        with transaction.atomic():
            ano_mes = datetime.now().strftime('%Y%m')
            prefixo = f"FAT{ano_mes}"
            
            # Buscar última fatura do mês com LOCK
            ultima_fatura = (
                Fatura.objects.filter(numero_fatura__startswith=prefixo)
                .select_for_update()  # ✅ LOCK: Garante que ninguém acessa
                .order_by('-numero_fatura')
                .first()
            )
            
            if ultima_fatura:
                # Extrair número sequencial
                try:
                    ultimo_numero = int(ultima_fatura.numero_fatura[-5:])
                    novo_numero = ultimo_numero + 1
                except (ValueError, IndexError):
                    novo_numero = 1
            else:
                novo_numero = 1
            
            numero_fatura = f"{prefixo}{novo_numero:05d}"
            
            print(f"✅ Número de fatura gerado: {numero_fatura}")
            return numero_fatura
            
    except Exception as e:
        print(f"❌ Erro ao gerar número de fatura: {str(e)}")
        raise


# ============================================
# API: LISTAR CONSULTORIAS
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_listar_consultorias(request):
    """
    Lista consultorias do cliente
    """
    try:
        cliente_id = request.GET.get('cliente_id')
        
        if not cliente_id:
            return JsonResponse({
                'sucesso': False,
                'erro': 'cliente_id obrigatório'
            }, status=400)
        
        cliente = get_object_or_404(Cliente, id=cliente_id)
        consultorias = Consultoria.objects.filter(cliente=cliente)
        
        html = ''
        total_valor = 0
        
        for c in consultorias:
            total_valor += float(c.valor_unitario)
            html += f'''
            <div class="card mb-3 border-primary">
                <div class="card-body">
                    <div class="row">
                        <div class="col-md-8">
                            <h6 class="card-title">{c.descricao}</h6>
                            <small class="text-muted">
                                {c.quantidade_meses}x {c.periodicidade.title()} | 
                                Desde {c.data_inicio.strftime('%d/%m/%Y')}
                            </small>
                            <p class="card-text mt-2">
                                <strong>R$ {float(c.valor_unitario):.2f}/mês</strong>
                            </p>
                        </div>
                        <div class="col-md-4 text-end">
                            <button class="btn btn-sm btn-warning" onclick="editarConsultoria({c.id})">
                                <i class="fas fa-edit"></i> Editar
                            </button>
                            <button class="btn btn-sm btn-danger" onclick="deletarConsultoria({c.id})">
                                <i class="fas fa-trash"></i> Deletar
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
        print(f"Erro em api_listar_consultorias: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': traceback.format_exc()
        }, status=500)


# ============================================
# API: CRIAR ALUGUEL IPv4
# ============================================

@login_required
@require_http_methods(["POST"])
def api_criar_aluguel_ipv4(request):
    """
    Cria um aluguel IPv4
    """
    try:
        cliente_id = request.POST.get('cliente_id')
        bloco_descricao = request.POST.get('bloco_descricao')
        quantidade_ips = int(request.POST.get('quantidade_ips'))
        valor_mensal = float(request.POST.get('valor_mensal'))
        data_inicio_str = request.POST.get('data_inicio')
        bloco_id = request.POST.get('bloco_ip')
        
        if not cliente_id or not bloco_descricao or not quantidade_ips or not valor_mensal:
            return JsonResponse({
                'sucesso': False,
                'erro': 'Preencha todos os campos obrigatórios'
            }, status=400)
        
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
        
        return JsonResponse({
            'sucesso': True,
            'mensagem': 'Aluguel criado com sucesso!',
            'aluguel': {
                'id': aluguel.id,
                'bloco_descricao': aluguel.bloco_descricao,
            }
        })
    
    except ValueError as e:
        return JsonResponse({
            'sucesso': False,
            'erro': f'Erro de validação: {str(e)}'
        }, status=400)
    except Exception as e:
        print(f"Erro em api_criar_aluguel_ipv4: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': traceback.format_exc()
        }, status=500)


# ============================================
# API: LISTAR ALUGUEIS IPv4
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_listar_alugueis(request):
    """
    Lista alugueis IPv4 do cliente
    """
    try:
        cliente_id = request.GET.get('cliente_id')
        
        if not cliente_id:
            return JsonResponse({
                'sucesso': False,
                'erro': 'cliente_id obrigatório'
            }, status=400)
        
        cliente = get_object_or_404(Cliente, id=cliente_id)
        alugueis = AluguelIPv4.objects.filter(cliente=cliente)
        
        html = ''
        total_valor_mensal = 0
        
        for a in alugueis:
            total_valor_mensal += float(a.valor_mensal)
            html += f'''
            <div class="card mb-3 border-info">
                <div class="card-body">
                    <div class="row">
                        <div class="col-md-8">
                            <h6 class="card-title">{a.bloco_descricao}</h6>
                            <small class="text-muted">
                                {a.quantidade_ips} IPs | 
                                Desde {a.data_inicio.strftime('%d/%m/%Y')}
                            </small>
                            <p class="card-text mt-2">
                                <strong>R$ {float(a.valor_mensal):.2f}/mês</strong>
                            </p>
                        </div>
                        <div class="col-md-4 text-end">
                            <button class="btn btn-sm btn-warning" onclick="editarAluguel({a.id})">
                                <i class="fas fa-edit"></i> Editar
                            </button>
                            <button class="btn btn-sm btn-danger" onclick="deletarAluguel({a.id})">
                                <i class="fas fa-trash"></i> Deletar
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
        print(f"Erro em api_listar_alugueis: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': traceback.format_exc()
        }, status=500)


# ============================================
# API: CRIAR FATURA
# ============================================

@login_required
@require_http_methods(["POST"])
def api_criar_fatura(request):
    """
    Cria uma fatura manualmente
    """
    try:
        cliente_id = request.POST.get('cliente_id')
        tipo = request.POST.get('tipo')
        data_vencimento_str = request.POST.get('data_vencimento')
        
        if not cliente_id or not tipo or not data_vencimento_str:
            return JsonResponse({
                'sucesso': False,
                'erro': 'Preencha todos os campos obrigatórios'
            }, status=400)
        
        cliente = get_object_or_404(Cliente, id=cliente_id)
        data_vencimento = datetime.strptime(data_vencimento_str, '%Y-%m-%d').date()
        
        fatura = Fatura.objects.create(
            cliente=cliente,
            numero_fatura=gerar_numero_fatura_unico(),
            tipo=tipo,
            data_vencimento=data_vencimento,
            status='ABERTA'
        )
        
        return JsonResponse({
            'sucesso': True,
            'mensagem': 'Fatura criada com sucesso!',
            'fatura': {
                'id': fatura.id,
                'numero_fatura': fatura.numero_fatura,
            }
        })
    
    except ValueError as e:
        return JsonResponse({
            'sucesso': False,
            'erro': f'Erro de validação: {str(e)}'
        }, status=400)
    except Exception as e:
        print(f"Erro em api_criar_fatura: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': traceback.format_exc()
        }, status=500)


# ============================================
# API: LISTAR FATURAS
# ============================================

@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_listar_faturas(request):
    """
    Lista faturas do cliente
    """
    try:
        cliente_id = request.GET.get('cliente_id')
        
        if not cliente_id:
            return JsonResponse({
                'sucesso': False,
                'erro': 'cliente_id obrigatório'
            }, status=400)
        
        cliente = get_object_or_404(Cliente, id=cliente_id)
        faturas = Fatura.objects.filter(cliente=cliente).order_by('-data_criacao')
        
        html = ''
        for f in faturas:
            status_badge = 'bg-danger' if f.status == 'ABERTA' else 'bg-success'
            html += f'''
            <tr>
                <td><strong>{f.numero_fatura}</strong></td>
                <td><small class="badge bg-secondary">{f.tipo}</small></td>
                <td>R$ {float(f.valor_total):.2f}</td>
                <td>{f.data_vencimento.strftime('%d/%m/%Y')}</td>
                <td><span class="badge {status_badge}">{f.status}</span></td>
                <td>
                    <button class="btn btn-sm btn-info" onclick="visualizarFatura({f.id})">
                        <i class="fas fa-eye"></i> Visualizar
                    </button>
                </td>
            </tr>
            '''
        
        return JsonResponse({
            'sucesso': True,
            'total': faturas.count(),
            'html': html
        })
    
    except Exception as e:
        print(f"Erro em api_listar_faturas: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': traceback.format_exc()
        }, status=500)


# ============================================
# API: REGISTRAR PAGAMENTO COM COMPROVANTE
# ============================================
def gerar_comprovante_pdf(pagamento):
    """
    Gera PDF de comprovante de pagamento
    ✅ VERSÃO CORRIGIDA: Sem HRFlowable problemático
    """
    try:
        print(f"\n📄 [PDF] Iniciando geração para {pagamento.numero_recibo}...")
        
        # ===== PASSO 1: CRIAR BUFFER =====
        buffer = BytesIO()
        print(f"  ✅ Buffer criado")
        
        # ===== PASSO 2: CRIAR DOCUMENTO =====
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.5*inch,
            leftMargin=0.5*inch,
            topMargin=1*inch,
            bottomMargin=0.75*inch,
            title=f"Comprovante {pagamento.numero_recibo}"
        )
        print(f"  ✅ Documento criado")
        
        # ===== PASSO 3: DEFINIR ESTILOS =====
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
        
        print(f"  ✅ Estilos criados")
        
        # ===== PASSO 4: PREPARAR ELEMENTOS =====
        elements = []
        
        # Cabeçalho
        elements.append(Paragraph("TOMICH TECNOLOGIA", empresa_style))
        elements.append(Spacer(1, 0.2*inch))
        
        # Título
        elements.append(Paragraph("COMPROVANTE DE PAGAMENTO", title_style))
        elements.append(Spacer(1, 0.1*inch))
        
        # ❌ REMOVER: HRFlowable problemático
        # ✅ SUBSTITUIR POR: Uma linha simples usando Paragraph
        elements.append(Paragraph("_" * 80, styles['Normal']))
        elements.append(Spacer(1, 0.15*inch))
        
        # Dados do recibo
        print(f"  ✅ Preparando dados do recibo...")
        data = [
            ['Número do Recibo:', pagamento.numero_recibo],
            ['Data do Pagamento:', pagamento.data_pagamento.strftime('%d/%m/%Y')],
            ['Tipo de Pagamento:', pagamento.get_tipo_display() if hasattr(pagamento, 'get_tipo_display') else pagamento.tipo],
        ]
        
        table = Table(data, colWidths=[2*inch, 3.5*inch])
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
        elements.append(Spacer(1, 0.2*inch))
        
        # Fatura
        print(f"  ✅ Preparando dados da fatura...")
        fatura = pagamento.fatura
        fatura_data = [
            ['Número da Fatura:', fatura.numero_fatura],
            ['Cliente:', fatura.cliente.nome_empresa],
            ['CNPJ:', fatura.cliente.cnpj],
            ['Valor Original:', f'R$ {float(fatura.valor_total):.2f}'],
            ['Valor Pago:', f'R$ {float(pagamento.valor):.2f}'],
        ]
        
        fatura_table = Table(fatura_data, colWidths=[2*inch, 3.5*inch])
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
        elements.append(Spacer(1, 0.3*inch))
        
        # Footer
        elements.append(Spacer(1, 0.2*inch))
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
        
        print(f"  ✅ Elementos preparados")
        
        # ===== PASSO 5: BUILD PDF =====
        print(f"  ✅ Construindo PDF...")
        doc.build(elements)
        print(f"  ✅ PDF construído com sucesso")
        
        # ===== PASSO 6: PEGAR BYTES =====
        pdf_bytes = buffer.getvalue()
        buffer.close()
        
        tamanho_kb = len(pdf_bytes) / 1024
        print(f"  ✅ PDF gerado: {tamanho_kb:.2f} KB ({len(pdf_bytes)} bytes)")
        
        return pdf_bytes
        
    except Exception as e:
        print(f"  ❌ ERRO CRÍTICO ao gerar PDF: {str(e)}")
        import traceback
        print(f"  Traceback completo:")
        print(traceback.format_exc())
        raise

    

@login_required
@acesso_financeiro_restrito
@require_http_methods(["POST"])
def api_registrar_pagamento(request):
    """
    Registra um pagamento e salva ambos os comprovantes
    
    ✅ FINAL: 
    - Comprovante do usuário (opcional) → campo: comprovante
    - PDF gerado (automático) → campo: comprovante_pdf_gerado
    - Ambos ficam disponíveis para download!
    """
    print("\n" + "="*70)
    print("📝 api_registrar_pagamento chamada")
    print("="*70)
    
    try:
        # Verificar permissão
        if not request.user.is_staff:
            print("❌ Usuário não é staff")
            return JsonResponse({
                'sucesso': False,
                'erro': 'Acesso negado - usuário não é staff'
            }, status=403)
        
        print(f"✅ Usuário staff: {request.user.username}")
        
        # PASSO 1: Pegar dados do POST
        print("\n1️⃣ Coletando dados do POST...")
        fatura_id = request.POST.get('fatura_id', '').strip()
        data_pagamento_str = request.POST.get('data_pagamento', '').strip()
        valor_pagado_str = request.POST.get('valor_pagado', '').strip()
        observacoes = request.POST.get('observacoes', '').strip()
        comprovante_upload = request.FILES.get('comprovante')
        
        print(f"  fatura_id: {fatura_id}")
        print(f"  data_pagamento: {data_pagamento_str}")
        print(f"  valor_pagado: {valor_pagado_str}")
        print(f"  observacoes: {observacoes[:50] if observacoes else 'Vazia'}...")
        print(f"  comprovante (upload): {comprovante_upload.name if comprovante_upload else 'Nenhum arquivo'}")
        
        # PASSO 2: Validar dados obrigatórios
        print("\n2️⃣ Validando dados...")
        if not fatura_id or not data_pagamento_str or not valor_pagado_str:
            msg = f"Campos obrigatórios faltando: fatura_id={fatura_id}, data={data_pagamento_str}, valor={valor_pagado_str}"
            print(f"❌ {msg}")
            return JsonResponse({
                'sucesso': False,
                'erro': msg
            }, status=400)
        
        # PASSO 3: Converter e validar valores
        print("\n3️⃣ Convertendo valores...")
        try:
            fatura_id = int(fatura_id)
            data_pagamento = datetime.strptime(data_pagamento_str, '%Y-%m-%d').date()
            valor_pagado = float(valor_pagado_str)
            print(f"  ✅ fatura_id: {fatura_id}")
            print(f"  ✅ data_pagamento: {data_pagamento}")
            print(f"  ✅ valor_pagado: {valor_pagado}")
        except (ValueError, TypeError) as e:
            msg = f"Erro ao converter valores: {str(e)}"
            print(f"❌ {msg}")
            return JsonResponse({
                'sucesso': False,
                'erro': msg
            }, status=400)
        
        # PASSO 4: Validar valor
        print("\n4️⃣ Validando valor...")
        if valor_pagado <= 0:
            print(f"❌ Valor é zero ou negativo: {valor_pagado}")
            return JsonResponse({
                'sucesso': False,
                'erro': 'Valor deve ser maior que zero'
            }, status=400)
        
        print(f"  ✅ Valor positivo: R$ {valor_pagado:.2f}")
        
        # PASSO 5: Buscar fatura
        print("\n5️⃣ Buscando fatura...")
        try:
            fatura = Fatura.objects.get(id=fatura_id)
            print(f"  ✅ Fatura encontrada: {fatura.numero_fatura}")
            print(f"  Valor total: R$ {fatura.valor_total:.2f}")
        except Fatura.DoesNotExist:
            msg = f"Fatura {fatura_id} não encontrada"
            print(f"❌ {msg}")
            return JsonResponse({
                'sucesso': False,
                'erro': msg
            }, status=404)
        
        # PASSO 6: Validar valor com a fatura
        print("\n6️⃣ Validando valor com fatura...")
        if valor_pagado > fatura.valor_total:
            msg = f"Valor {valor_pagado} maior que total {fatura.valor_total}"
            print(f"❌ {msg}")
            return JsonResponse({
                'sucesso': False,
                'erro': f'Valor não pode ser maior que R$ {fatura.valor_total:.2f}'
            }, status=400)
        
        print(f"  ✅ Valor válido")
        
        # PASSO 7: Importar modelo Pagamento
        print("\n7️⃣ Importando modelo Pagamento...")
        from .models import Pagamento
        print(f"  ✅ Pagamento importado")
        
        # PASSO 8: Gerar número de recibo
        print("\n8️⃣ Gerando número de recibo...")
        ano_mes = datetime.now().strftime('%Y%m')
        contador = Pagamento.objects.filter(numero_recibo__startswith='REC').count() + 1
        numero_recibo = f"REC{ano_mes}{contador:05d}"
        print(f"  ✅ Número de recibo: {numero_recibo}")
        
        # PASSO 9: Criar registro de Pagamento
        print("\n9️⃣ Criando registro de Pagamento...")
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
        print(f"  ✅ Pagamento criado com ID: {pagamento.id}")
        
        # PASSO 10: Salvar comprovante do usuário (OPCIONAL)
        print("\n🔟 Processando comprovante do usuário (opcional)...")
        if comprovante_upload:
            print(f"  📁 Arquivo recebido: {comprovante_upload.name} ({comprovante_upload.size} bytes)")
            try:
                pagamento.comprovante = comprovante_upload
                pagamento.save()
                print(f"  ✅ Comprovante do usuário salvo: {comprovante_upload.name}")
                print(f"  📥 Tipo: {comprovante_upload.content_type}")
            except Exception as e:
                print(f"  ⚠️ Erro ao salvar comprovante do usuário: {e}")
        else:
            print(f"  ℹ️ Nenhum comprovante do usuário enviado (opcional)")
        
        # PASSO 11: Gerar PDF automaticamente (SEMPRE)
        print("\n1️⃣1️⃣ Gerando PDF automaticamente...")
        try:
            pdf_bytes = gerar_comprovante_pdf(pagamento)
            print(f"  ✅ PDF gerado: {len(pdf_bytes)} bytes")
            
            # Salvar em campo separado
            nome_arquivo = f"comprovante_{pagamento.numero_recibo}.pdf"
            pagamento.comprovante_pdf_gerado = ContentFile(pdf_bytes, name=nome_arquivo)
            pagamento.save()
            print(f"  ✅ PDF gerado salvo: {nome_arquivo}")
            print(f"  📄 Campo: comprovante_pdf_gerado")
        except Exception as e:
            print(f"  ⚠️ Erro ao gerar PDF: {e}")
            print(f"  Traceback: {traceback.format_exc()}")
        
        # PASSO 12: Verificar o que foi salvo
        print("\n1️⃣2️⃣ Verificando comprovantes salvos...")
        if pagamento.comprovante:
            print(f"  ✅ Comprovante do usuário: {pagamento.comprovante.url}")
        else:
            print(f"  ℹ️ Sem comprovante do usuário")
        
        if pagamento.comprovante_pdf_gerado:
            print(f"  ✅ PDF gerado: {pagamento.comprovante_pdf_gerado.url}")
        else:
            print(f"  ⚠️ PDF não foi gerado")
        
        # PASSO 13: Atualizar status da fatura
        print("\n1️⃣3️⃣ Atualizando status da fatura...")
        if valor_pagado >= fatura.valor_total:
            fatura.status = 'PAGA'
            fatura.data_pagamento = data_pagamento
            print(f"  ✅ Fatura marcada como PAGA")
        else:
            print(f"  ℹ️ Pagamento parcial - Fatura continua ABERTA")
        
        fatura.save()
        print(f"  ✅ Fatura salva")
        
        # PASSO 14: Preparar resposta
        print("\n1️⃣4️⃣ Preparando resposta...")
        resposta = {
            'sucesso': True,
            'mensagem': f'Pagamento registrado com sucesso! Recibo: {numero_recibo}',
            'pagamento': {
                'id': pagamento.id,
                'numero_recibo': pagamento.numero_recibo,
                'valor': float(pagamento.valor),
                'data_pagamento': pagamento.data_pagamento.strftime('%d/%m/%Y'),
                'fatura_status': fatura.status,
                # ✅ NOVO: URLs de ambos os comprovantes
                'comprovante_usuario_url': pagamento.comprovante.url if pagamento.comprovante else None,
                'comprovante_pdf_url': pagamento.comprovante_pdf_gerado.url if pagamento.comprovante_pdf_gerado else None,
                'tem_comprovante_usuario': bool(pagamento.comprovante),
                'tem_pdf_gerado': bool(pagamento.comprovante_pdf_gerado),
            }
        }
        
        print(f"  ✅ Resposta pronta")
        print("\n" + "="*70)
        print("✅ PAGAMENTO REGISTRADO COM SUCESSO!")
        print("="*70 + "\n")
        
        return JsonResponse(resposta, status=200)
    
    except ValueError as e:
        print(f"\n❌ ValueError: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': f'Erro de validação: {str(e)}'
        }, status=400)
    
    except Exception as e:
        print(f"\n❌ Erro GERAL em api_registrar_pagamento: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': traceback.format_exc()
        }, status=500)


# ============================================
# API: LISTAR PAGAMENTOS DE UMA FATURA
# ============================================

@login_required
@require_http_methods(["GET"])
def api_listar_pagamentos(request, fatura_id):
    """
    Retorna lista de pagamentos de uma fatura
    
    ✅ ATUALIZADO: Retorna ambos os comprovantes (usuário e PDF gerado)
    
    URL: /financeiro/api/pagamento/<fatura_id>/listar/
    """
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
            # ✅ NOVO: Preparar URLs de ambos os comprovantes
            pagamento_data = {
                'id': pag.id,
                'numero_recibo': pag.numero_recibo,
                'data_pagamento': pag.data_pagamento.strftime('%d/%m/%Y'),
                'valor': float(pag.valor),
                'tipo': pag.get_tipo_display(),
                'observacoes': pag.observacoes or '',
                # ✅ NOVO: Comprovante do usuário
                'tem_comprovante_usuario': bool(pag.comprovante),
                'comprovante_usuario_url': pag.comprovante.url if pag.comprovante else None,
                'comprovante_usuario_nome': pag.comprovante.name.split('/')[-1] if pag.comprovante else None,
                # ✅ NOVO: PDF gerado
                'tem_pdf_gerado': bool(pag.comprovante_pdf_gerado),
                'comprovante_pdf_url': pag.comprovante_pdf_gerado.url if pag.comprovante_pdf_gerado else None,
                'comprovante_pdf_nome': pag.comprovante_pdf_gerado.name.split('/')[-1] if pag.comprovante_pdf_gerado else None,
                # Resumo
                'tem_algum_comprovante': bool(pag.comprovante or pag.comprovante_pdf_gerado),
            }
            data['pagamentos'].append(pagamento_data)
        
        print(f'✅ Pagamentos da fatura {fatura_id} carregados: {len(pagamentos)} registros')
        return JsonResponse(data)
    
    except Fatura.DoesNotExist:
        return JsonResponse({
            'sucesso': False,
            'erro': 'Fatura não encontrada'
        }, status=404)
    
    except Exception as e:
        print(f'❌ Erro ao listar pagamentos: {str(e)}')
        import traceback
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e)
        }, status=500)

# ============================================
# API: DELETAR PAGAMENTO
# ============================================

@login_required
@require_http_methods(["DELETE"])
def api_deletar_pagamento(request, pagamento_id):
    """
    Deleta um registro de pagamento
    """
    try:
        if not request.user.is_staff:
            return JsonResponse({
                'sucesso': False,
                'erro': 'Acesso negado'
            }, status=403)
        
        from .models import Pagamento
        pagamento = get_object_or_404(Pagamento, id=pagamento_id)
        fatura = pagamento.fatura
        
        # Se o arquivo existe, deletar também
        if pagamento.comprovante:
            pagamento.comprovante.delete()
        
        pagamento.delete()
        
        # ✅ REABRIR FATURA SE ESTAVA PAGA E TEM MAIS PAGAMENTOS
        from .models import Pagamento as PagamentoModel
        tem_pagamentos = PagamentoModel.objects.filter(fatura=fatura).exists()
        
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
        print(f"Erro em api_deletar_pagamento: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': traceback.format_exc()
        }, status=500)
    

@login_required
@require_http_methods(["GET"])
def api_faturamento_por_mes(request):
    """
    Retorna faturamento (faturas PAGAS) por mês do ano atual
    """
    try:
        if not request.user.is_staff:
            return JsonResponse({'sucesso': False, 'erro': 'Acesso negado'}, status=403)
        
        ano_atual = datetime.now().year
        meses_nomes = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
                       'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
        
        meses = []
        valores = []
        
        for mes in range(1, 13):
            data_inicio = datetime(ano_atual, mes, 1).date()
            ultimo_dia = calendar.monthrange(ano_atual, mes)[1]
            data_fim = datetime(ano_atual, mes, ultimo_dia).date()
            
            faturamento_mes = Fatura.objects.filter(
                data_emissao__gte=data_inicio,
                data_emissao__lte=data_fim,
                status='PAGA'
            ).aggregate(total=Sum('valor_total'))['total'] or 0
            
            meses.append(meses_nomes[mes - 1])
            valores.append(float(faturamento_mes))
        
        return JsonResponse({
            'sucesso': True,
            'ano': ano_atual,
            'meses': meses,
            'valores': valores,
            'total_anual': sum(valores)
        })
    
    except Exception as e:
        return JsonResponse({'sucesso': False, 'erro': str(e)}, status=500)
    
# ============================================
# ADICIONAR ESTAS VIEWS NO financeiro/views.py
# ============================================

@login_required
@require_http_methods(["POST"])
def api_criar_venda_equipamento(request):
    """
    Cria uma venda de equipamento e gera faturas automaticamente
    ✅ Similar à consultoria, mas com nome "VendaEquipamento"
    """
    print("\n" + "="*70)
    print("📦 api_criar_venda_equipamento chamada")
    print("="*70)
    
    try:
        cliente_id = request.POST.get('cliente_id')
        descricao = request.POST.get('descricao')
        valor_total = float(request.POST.get('valor_total'))
        quantidade_parcelas = int(request.POST.get('quantidade_parcelas', 1))
        data_inicio_str = request.POST.get('data_inicio')
        
        print(f"  cliente_id: {cliente_id}")
        print(f"  descricao: {descricao}")
        print(f"  valor_total: {valor_total}")
        print(f"  quantidade_parcelas: {quantidade_parcelas}")
        print(f"  data_inicio: {data_inicio_str}")
        
        # Validações
        if not cliente_id or not descricao or not valor_total or not data_inicio_str:
            msg = "Preencha todos os campos obrigatórios"
            print(f"❌ {msg}")
            return JsonResponse({
                'sucesso': False,
                'erro': msg
            }, status=400)
        
        cliente = get_object_or_404(Cliente, id=cliente_id)
        data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
        
        print(f"\n✅ Cliente encontrado: {cliente.nome_empresa}")
        print(f"✅ Data de início: {data_inicio}")
        
        # Criar venda de equipamento
        from .models import VendaEquipamento
        venda = VendaEquipamento.objects.create(
            cliente=cliente,
            descricao=descricao,
            valor_total=valor_total,
            quantidade_parcelas=quantidade_parcelas,
            data_inicio=data_inicio
        )
        
        print(f"\n✅ Venda criada com ID: {venda.id}")
        print(f"   Valor da parcela: R$ {venda.get_valor_parcela():.2f}")
        
        # Gerar faturas automaticamente
        print(f"\n📋 Gerando {quantidade_parcelas} fatura(s)...")
        faturas_geradas = gerar_faturas_venda_equipamento(venda)
        
        print(f"\n✅ {len(faturas_geradas)} fatura(s) gerada(s):")
        for f in faturas_geradas:
            print(f"   - {f['numero_fatura']}: R$ {f['valor']:.2f} (vence {f['vencimento']})")
        
        print("\n" + "="*70)
        print("✅ VENDA DE EQUIPAMENTO CRIADA COM SUCESSO!")
        print("="*70 + "\n")
        
        return JsonResponse({
            'sucesso': True,
            'mensagem': f'Venda de equipamento criada com sucesso! {len(faturas_geradas)} fatura(s) gerada(s).',
            'venda': {
                'id': venda.id,
                'descricao': venda.descricao,
                'valor_total': float(venda.valor_total),
                'quantidade_parcelas': venda.quantidade_parcelas,
            },
            'faturas': faturas_geradas
        })
    
    except ValueError as e:
        print(f"❌ ValueError: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': f'Erro de validação: {str(e)}'
        }, status=400)
    except Exception as e:
        print(f"❌ Erro em api_criar_venda_equipamento: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': traceback.format_exc()
        }, status=500)


def gerar_faturas_venda_equipamento(venda):
    """
    Gera N faturas para uma venda de equipamento com N parcelas
    ✅ CORRIGIDO: Melhor debug e validação
    """
    print(f"\n📋 gerar_faturas_venda_equipamento iniciada")
    print(f"   Equipamento: {venda.descricao}")
    print(f"   Parcelas: {venda.quantidade_parcelas}")
    print(f"   Valor total: R$ {venda.valor_total:.2f}")
    print(f"   Valor por parcela: R$ {venda.get_valor_parcela():.2f}")
    print(f"   Data inicial: {venda.data_inicio}")
    
    faturas = []
    data_inicio = venda.data_inicio
    valor_parcela = venda.get_valor_parcela()
    
    for parcela in range(venda.quantidade_parcelas):
        print(f"\n   📅 Gerando fatura {parcela + 1}/{venda.quantidade_parcelas}...")
        
        # Calcular datas
        data_fatura = data_inicio + relativedelta(months=parcela)
        # Vencimento no primeiro dia do próximo mês
        data_vencimento = (data_fatura + relativedelta(months=1)).replace(day=1)
        
        print(f"      Data da fatura: {data_fatura}")
        print(f"      Data de vencimento: {data_vencimento}")
        
        # Gerar número de fatura
        numero_fatura = gerar_numero_fatura_unico()
        print(f"      Número da fatura: {numero_fatura}")
        
        # Criar fatura
        fatura = Fatura.objects.create(
            cliente=venda.cliente,
            numero_fatura=numero_fatura,
            tipo='VENDA_EQUIPAMENTO',
            valor_total=valor_parcela,
            data_vencimento=data_vencimento,
            status='ABERTA'
        )
        
        print(f"      ✅ Fatura criada com ID: {fatura.id}")
        
        # Associar venda de equipamento
        # ⚠️ Você precisa adicionar ManyToMany no modelo Fatura:
        # vendas_equipamentos = models.ManyToManyField(VendaEquipamento, blank=True, related_name='faturas')
        
        faturas.append({
            'numero_fatura': fatura.numero_fatura,
            'valor': float(fatura.valor_total),
            'vencimento': fatura.data_vencimento.strftime('%d/%m/%Y'),
            'parcela': parcela + 1,
        })
    
    print(f"\n✅ Função concluída: {len(faturas)} fatura(s) criada(s)")
    return faturas


@login_required
@acesso_financeiro_restrito
@require_http_methods(["GET"])
def api_listar_vendas_equipamentos(request):
    """
    Lista vendas de equipamentos do cliente
    """
    try:
        cliente_id = request.GET.get('cliente_id')
        
        if not cliente_id:
            return JsonResponse({
                'sucesso': False,
                'erro': 'cliente_id obrigatório'
            }, status=400)
        
        cliente = get_object_or_404(Cliente, id=cliente_id)
        from .models import VendaEquipamento
        vendas = VendaEquipamento.objects.filter(cliente=cliente)
        
        html = ''
        total_valor = 0
        
        for v in vendas:
            total_valor += float(v.valor_total)
            html += f'''
            <div class="card mb-3 border-success">
                <div class="card-body">
                    <div class="row">
                        <div class="col-md-8">
                            <h6 class="card-title">{v.descricao}</h6>
                            <small class="text-muted">
                                {v.quantidade_parcelas}x | 
                                Desde {v.data_inicio.strftime('%d/%m/%Y')}
                            </small>
                            <p class="card-text mt-2">
                                <strong>R$ {float(v.valor_total):.2f}</strong> (parcelas de R$ {float(v.get_valor_parcela()):.2f})
                            </p>
                        </div>
                        <div class="col-md-4 text-end">
                            <button class="btn btn-sm btn-warning" onclick="editarVendaEquipamento({v.id})">
                                <i class="fas fa-edit"></i> Editar
                            </button>
                            <button class="btn btn-sm btn-danger" onclick="deletarVendaEquipamento({v.id})">
                                <i class="fas fa-trash"></i> Deletar
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
        print(f"Erro em api_listar_vendas_equipamentos: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'sucesso': False,
            'erro': str(e),
            'trace': traceback.format_exc()
        }, status=500)