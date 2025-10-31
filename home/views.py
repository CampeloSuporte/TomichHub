from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from datetime import datetime, timedelta
from clientes.models import Chamado, Cliente
from django.contrib.auth.models import User
from clientes.decorators import admin_required 

@login_required(login_url='login')
@admin_required 
def quadro_geral(request):
    # ✅ DADOS DO DIA ATUAL
    hoje = datetime.now().date()
    inicio_dia = datetime.combine(hoje, datetime.min.time())
    fim_dia = datetime.combine(hoje, datetime.max.time())
    
    # Estatísticas do dia para alguns campos
    total_chamados_hoje = Chamado.objects.filter(data_criacao__range=(inicio_dia, fim_dia)).count()
    
    # ✅ ALTERADO: Esses três contam TODOS os chamados, não apenas do dia
    chamados_abertos = Chamado.objects.filter(status='ABERTO').count()
    chamados_em_andamento = Chamado.objects.filter(status='EM_ANDAMENTO').count()
    chamados_aguardando = Chamado.objects.filter(status='AGUARDANDO').count()
    
    # Esses continuam sendo apenas do dia
    chamados_resolvidos_hoje = Chamado.objects.filter(
        data_criacao__range=(inicio_dia, fim_dia),
        status='RESOLVIDO'
    ).count()
    chamados_fechados_hoje = Chamado.objects.filter(
        data_criacao__range=(inicio_dia, fim_dia),
        status='FECHADO'
    ).count()
    
    # Estatísticas por prioridade (do dia, excluindo fechados)
    urgentes_hoje = Chamado.objects.filter(
        data_criacao__range=(inicio_dia, fim_dia),
        prioridade='URGENTE'
    ).exclude(status='FECHADO').count()
    
    alta_prioridade_hoje = Chamado.objects.filter(
        data_criacao__range=(inicio_dia, fim_dia),
        prioridade='ALTA'
    ).exclude(status='FECHADO').count()
    
    # Chamados dos últimos 30 dias (para o gráfico)
    data_limite = datetime.now() - timedelta(days=30)
    
    # Dados para o gráfico - Últimos 30 dias
    grafico_dados = []
    for i in range(29, -1, -1):
        data = datetime.now() - timedelta(days=i)
        data_inicio = data.replace(hour=0, minute=0, second=0, microsecond=0)
        data_fim = data.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        count = Chamado.objects.filter(
            data_criacao__gte=data_inicio,
            data_criacao__lte=data_fim
        ).count()
        
        grafico_dados.append({
            'data': data.strftime('%d/%m'),
            'quantidade': count
        })
    
    # Chamados recentes (Últimos 10)
    chamados_recentes = Chamado.objects.select_related(
        'cliente', 'categoria', 'responsavel'
    ).order_by('-data_criacao')[:10]
    
    # Top 5 clientes com mais chamados
    top_clientes = Cliente.objects.annotate(
        total_chamados=Count('chamados')
    ).order_by('-total_chamados')[:5]
    
    # Distribuição por departamento (geral, não apenas do dia)
    por_departamento = Chamado.objects.values('departamento').annotate(
        total=Count('id')
    ).order_by('-total')
    
    # Total geral para cálculo de percentual
    total_chamados_geral = Chamado.objects.count()
    
    context = {
        'total_chamados': total_chamados_hoje,
        'chamados_abertos': chamados_abertos,  # ✅ ALTERADO
        'chamados_em_andamento': chamados_em_andamento,  # ✅ ALTERADO
        'chamados_aguardando': chamados_aguardando,  # ✅ ALTERADO
        'chamados_resolvidos': chamados_resolvidos_hoje,
        'chamados_fechados': chamados_fechados_hoje,
        'urgentes': urgentes_hoje,
        'alta_prioridade': alta_prioridade_hoje,
        'grafico_dados': grafico_dados,
        'chamados_recentes': chamados_recentes,
        'top_clientes': top_clientes,
        'por_departamento': por_departamento,
        'total_chamados_geral': total_chamados_geral,
        'data_hoje': hoje,
    }
    
    return render(request, 'quadro_geral.html', context)


@login_required(login_url='login')
@admin_required 
def listar_chamados_por_status(request, status):
    """Lista chamados filtrados por status"""
    chamados = Chamado.objects.filter(status=status).select_related(
        'cliente', 'categoria', 'responsavel', 'criado_por'
    ).prefetch_related('comentarios').order_by('-data_criacao')
    
    # Nome amigável do status
    status_display = dict(Chamado.StatusChoices.choices).get(status, status)
    
    context = {
        'chamados': chamados,
        'status': status,
        'status_display': status_display,
        'total': chamados.count(),
    }
    
    return render(request, 'listar_chamados_status.html', context)