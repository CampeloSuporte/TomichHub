from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from datetime import datetime, timedelta
from clientes.models import Chamado, Cliente, BlocoIP
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
    
    # ✅ NOVO: Blocos IP com validação RPKI não bem-sucedida
    blocos_rpki_invalidos = BlocoIP.objects.filter(
        Q(rpki_valido=False) | Q(rpki_status__in=['Invalid', 'Unknown', 'Error', 'NotChecked'])
    ).select_related('cliente').order_by('-ultima_validacao')[:10]
    
    total_blocos_rpki_invalidos = BlocoIP.objects.filter(
        Q(rpki_valido=False) | Q(rpki_status__in=['Invalid', 'Unknown', 'Error', 'NotChecked'])
    ).count()
    
    # ✅ NOVO: Blocos IP com validação IRR não bem-sucedida
    blocos_irr_invalidos = BlocoIP.objects.filter(
        Q(irr_valido=False) | Q(irr_status__in=['NotFound', 'ASN_Mismatch', 'Error'])
    ).select_related('cliente').order_by('-ultima_validacao')[:10]
    
    total_blocos_irr_invalidos = BlocoIP.objects.filter(
        Q(irr_valido=False) | Q(irr_status__in=['NotFound', 'ASN_Mismatch', 'Error'])
    ).count()

    context = {
        'total_chamados': total_chamados_hoje,
        'chamados_abertos': chamados_abertos,
        'chamados_em_andamento': chamados_em_andamento,
        'chamados_aguardando': chamados_aguardando,
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
        # ✅ NOVO: Dados RPKI/IRR
        'blocos_rpki_invalidos': blocos_rpki_invalidos,
        'total_blocos_rpki_invalidos': total_blocos_rpki_invalidos,
        'blocos_irr_invalidos': blocos_irr_invalidos,
        'total_blocos_irr_invalidos': total_blocos_irr_invalidos,
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

import json
from django.http import JsonResponse

@login_required(login_url='login')
@admin_required
def smtp_testar(request):
    """Envia um e-mail de teste com as configurações SMTP salvas."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'erro': 'Método não permitido'}, status=405)
    import smtplib
    from email.mime.text import MIMEText
    from clientes.models import ConfiguracaoSistema
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'erro': 'JSON inválido'}, status=400)
    destino = body.get('destino', '').strip()
    if not destino:
        return JsonResponse({'ok': False, 'erro': 'Endereço de destino não informado'}, status=400)
    cfg = ConfiguracaoSistema.get()
    if not cfg.smtp_host or not cfg.smtp_user:
        return JsonResponse({'ok': False, 'erro': 'SMTP não configurado. Preencha Host, Usuário e Senha primeiro.'}, status=400)
    try:
        msg = MIMEText('Este é um e-mail de teste enviado pelo CRM.', 'plain', 'utf-8')
        msg['Subject'] = 'Teste SMTP - CRM'
        msg['From']    = cfg.smtp_from or cfg.smtp_user
        msg['To']      = destino
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as server:
            server.ehlo()
            if cfg.smtp_use_tls:
                server.starttls()
            server.login(cfg.smtp_user, cfg.smtp_pass)
            server.sendmail(msg['From'], [destino], msg.as_string())
        return JsonResponse({'ok': True, 'mensagem': f'E-mail de teste enviado para {destino}'})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=500)


@login_required(login_url='login')
@admin_required
def configuracoes_sistema(request):
    from clientes.models import ConfiguracaoSistema
    cfg = ConfiguracaoSistema.get()
    if request.method == 'POST':
        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse({'ok': False, 'erro': 'JSON inválido'}, status=400)
        # SMTP
        if 'smtp_host' in body:
            cfg.smtp_host    = body.get('smtp_host', '').strip()
            cfg.smtp_port    = int(body.get('smtp_port', 587))
            cfg.smtp_user    = body.get('smtp_user', '').strip()
            cfg.smtp_from    = body.get('smtp_from', '').strip()
            cfg.smtp_use_tls = bool(body.get('smtp_use_tls', True))
            if body.get('smtp_pass'):
                cfg.smtp_pass = body['smtp_pass']
        # IMAP
        if 'imap_host' in body:
            cfg.imap_host    = body.get('imap_host', '').strip()
            cfg.imap_port    = int(body.get('imap_port', 993))
            cfg.imap_use_ssl = bool(body.get('imap_use_ssl', True))
        cfg.save()
        return JsonResponse({'ok': True})
    return render(request, 'configuracoes_sistema.html', {'cfg': cfg})


@login_required(login_url='login')
def lg_pesquisa(request):
    prefixo = request.GET.get('prefixo', '')
    return render(request, 'lg_pesquisa.html', {'prefixo_inicial': prefixo})


@login_required(login_url='login')
def lg_pesquisa_buscar(request):
    import ipaddress
    import socket
    import requests as req_lib
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from collections import Counter

    prefixo = request.GET.get('prefixo', '').strip()
    if not prefixo:
        return JsonResponse({'ok': False, 'erro': 'Informe um prefixo'}, status=400)
    try:
        ipaddress.ip_network(prefixo, strict=False)
    except ValueError:
        return JsonResponse({'ok': False, 'erro': f'Prefixo inválido: {prefixo}'}, status=400)

    HEADERS = {'User-Agent': 'CRM-LG/1.0'}

    # RRCs com país para classificação nacional/internacional
    RRC_PAIS = {
        'RRC00': ('NL', 'Amsterdam, NL'),
        'RRC01': ('GB', 'London, UK'),
        'RRC03': ('NL', 'Amsterdam, NL'),
        'RRC04': ('CH', 'Geneva, CH'),
        'RRC05': ('AT', 'Vienna, AT'),
        'RRC06': ('JP', 'Tokyo, JP'),
        'RRC07': ('SE', 'Stockholm, SE'),
        'RRC10': ('IT', 'Milan, IT'),
        'RRC11': ('US', 'New York, US'),
        'RRC12': ('DE', 'Frankfurt, DE'),
        'RRC13': ('RU', 'Moscow, RU'),
        'RRC14': ('US', 'Palo Alto, US'),
        'RRC15': ('BR', 'São Paulo, BR'),
        'RRC16': ('US', 'Miami, US'),
        'RRC18': ('ZA', 'Cape Town, ZA'),
        'RRC19': ('ZA', 'Johannesburg, ZA'),
        'RRC20': ('CH', 'Zurich, CH'),
        'RRC21': ('JP', 'Tokyo, JP'),
        'RRC22': ('US', 'Ashburn, US'),
        'RRC23': ('SG', 'Singapore, SG'),
        'RRC24': ('DE', 'Frankfurt, DE'),
        'RRC25': ('AE', 'Dubai, AE'),
        'RRC26': ('AU', 'Sydney, AU'),
    }

    # ── Source 1: RIPE RIS Looking Glass (principal) ──────────────────────────
    def query_ripe_lg():
        try:
            url = f'https://stat.ripe.net/data/looking-glass/data.json?resource={prefixo}'
            r = req_lib.get(url, timeout=25, headers=HEADERS)
            data = r.json()
            if data.get('status') != 'ok':
                return {'fonte': 'RIPE RIS', 'descricao': 'RIPE NCC RIS Looking Glass', 'encontrado': False, 'vantage_points': []}

            rrcs = data.get('data', {}).get('rrcs', [])
            vps = []
            for rrc_data in rrcs:
                rrc_id   = rrc_data.get('rrc', '').upper()
                location = rrc_data.get('location', '')
                peers    = rrc_data.get('peers', [])
                paths    = [p.get('as_path', '').strip() for p in peers if p.get('as_path', '').strip()]
                if not paths:
                    continue
                mais_comum = Counter(paths).most_common(1)[0][0]
                pais_info  = RRC_PAIS.get(rrc_id, (None, location))
                pais       = pais_info[0]
                nacional   = pais == 'BR'
                vps.append({
                    'nome':     f'{rrc_id} – {location}',
                    'id':       rrc_id.lower(),
                    'as_path':  mais_comum,
                    'n_peers':  len(paths),
                    'pais':     pais,
                    'nacional': nacional,
                    'all_paths': Counter(paths).most_common(5),
                })
            encontrado = bool(vps)
            return {'fonte': 'RIPE RIS', 'descricao': f'RIPE NCC RIS Looking Glass ({len(rrcs)} coletores)', 'encontrado': encontrado, 'vantage_points': vps}
        except Exception as e:
            return {'fonte': 'RIPE RIS', 'descricao': 'RIPE NCC RIS Looking Glass', 'encontrado': False, 'vantage_points': [], 'erro': str(e)}

    # ── Source 2: RIPE prefix-overview (ASN info) ─────────────────────────────
    def query_ripe_overview():
        try:
            url = f'https://stat.ripe.net/data/prefix-overview/data.json?resource={prefixo}'
            r = req_lib.get(url, timeout=15, headers=HEADERS)
            data = r.json()
            if data.get('status') != 'ok':
                return {'fonte': 'RIPE Overview', 'descricao': 'RIPE Prefix Overview', 'encontrado': False, 'vantage_points': []}
            pdata = data.get('data', {})
            if not pdata.get('announced'):
                return {'fonte': 'RIPE Overview', 'descricao': 'RIPE Prefix Overview', 'encontrado': False, 'vantage_points': []}
            asns = pdata.get('asns', [])
            vps = []
            for a in asns:
                asn    = a.get('asn', '')
                holder = a.get('holder', '')
                vps.append({
                    'nome':    f'RIPE Overview – AS{asn}',
                    'id':      'ripe_overview',
                    'as_path': str(asn),
                    'n_peers': 1,
                    'extra':   holder,
                })
            return {'fonte': 'RIPE Overview', 'descricao': 'RIPE Prefix Overview (ASN de origem)', 'encontrado': bool(vps), 'vantage_points': vps}
        except Exception as e:
            return {'fonte': 'RIPE Overview', 'descricao': 'RIPE Prefix Overview', 'encontrado': False, 'vantage_points': [], 'erro': str(e)}

    # ── Source 3: RIS Whois (riswhois.ripe.net porta 43) ─────────────────────
    def query_riswhois():
        try:
            with socket.create_connection(('riswhois.ripe.net', 43), timeout=10) as s:
                s.sendall(f'-F {prefixo}\r\n'.encode())
                chunks = []
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
            texto = b''.join(chunks).decode('utf-8', errors='replace')
            # Formato: "AS_NUM\tPREFIXO" por linha
            vps = []
            for linha in texto.splitlines():
                l = linha.strip()
                if not l or l.startswith('%'):
                    continue
                parts = l.split()
                if parts and parts[0].isdigit():
                    asn = parts[0]
                    vps.append({
                        'nome':    f'RIS Whois – AS{asn}',
                        'id':      'riswhois',
                        'as_path': asn,
                        'n_peers': 1,
                    })
            return {'fonte': 'RIS Whois', 'descricao': 'RIPE RIS Whois (riswhois.ripe.net)', 'encontrado': bool(vps), 'vantage_points': vps}
        except Exception as e:
            return {'fonte': 'RIS Whois', 'descricao': 'RIPE RIS Whois (riswhois.ripe.net)', 'encontrado': False, 'vantage_points': [], 'erro': str(e)}

    # ── Executa em paralelo ───────────────────────────────────────────────────
    resultados = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {
            ex.submit(query_ripe_lg):       'ripe_lg',
            ex.submit(query_ripe_overview): 'ripe_overview',
            ex.submit(query_riswhois):      'riswhois',
        }
        for fut in as_completed(futures, timeout=30):
            try:
                resultados.append(fut.result())
            except Exception:
                pass

    # ── Agrupa por AS path (apenas a fonte RIPE RIS tem paths completos) ──────
    por_aspath = {}
    for res in resultados:
        if res['fonte'] != 'RIPE RIS':
            continue
        for vp in res.get('vantage_points', []):
            path = vp.get('as_path', '').strip()
            if not path:
                continue
            por_aspath.setdefault(path, []).append({
                'fonte': res['fonte'],
                'ponto': vp['nome'],
            })

    # Ordena por número de coletores que viram o mesmo path
    por_aspath_ord = dict(sorted(por_aspath.items(), key=lambda x: -len(x[1])))

    encontrado = any(r.get('encontrado') for r in resultados)

    # Info de origem para o summary
    origin_info = None
    for res in resultados:
        if res['fonte'] == 'RIPE Overview' and res.get('encontrado'):
            vps = res.get('vantage_points', [])
            if vps:
                origin_info = {'asn': vps[0]['as_path'], 'nome': vps[0].get('extra', '')}
            break

    return JsonResponse({
        'ok':                True,
        'prefixo':           prefixo,
        'encontrado':        encontrado,
        'total_fontes':      len(resultados),
        'fontes_com_prefixo': sum(1 for r in resultados if r.get('encontrado')),
        'resultados':        resultados,
        'por_aspath':        por_aspath_ord,
        'origin_info':       origin_info,
    })
