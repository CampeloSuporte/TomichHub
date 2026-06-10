from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from datetime import datetime, timedelta
from clientes.models import (
    Cliente, BlocoIP, BackupTemplate, Acesso, BackupLog,
    AgentConfig, EvolutionAPIConfig, WhatsAppGrupo, AgentKnowledge,
    AgentKnowledgeDoc, AgentSessao, AgentLog,
)
from django.contrib.auth.models import User
from clientes.decorators import admin_required, superuser_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
import json
import logging
import hashlib
import hmac
import asyncio
import re as _re
import base64
import io
import requests as _requests

logger = logging.getLogger(__name__)

@login_required(login_url='login')
@admin_required
def quadro_geral(request):
    hoje = datetime.now().date()
    inicio_dia = datetime.combine(hoje, datetime.min.time())
    fim_dia    = datetime.combine(hoje, datetime.max.time())

    # ── Stats gerais ──────────────────────────────────────────────
    total_clientes = Cliente.objects.count()
    total_acessos  = Acesso.objects.count()
    acessos_com_backup = Acesso.objects.filter(backup_habilitado=True).count()

    # ── Backups hoje ──────────────────────────────────────────────
    backups_hoje_ok    = BackupLog.objects.filter(data_backup__range=(inicio_dia, fim_dia), status='SUCESSO').count()
    backups_hoje_erro  = BackupLog.objects.filter(data_backup__range=(inicio_dia, fim_dia), status='ERRO').count()
    backups_sem_mudanca = BackupLog.objects.filter(data_backup__range=(inicio_dia, fim_dia), status='SEM_MUDANCAS').count()
    backups_total_hoje = BackupLog.objects.filter(data_backup__range=(inicio_dia, fim_dia)).count()

    # ── Últimos 10 backups ────────────────────────────────────────
    ultimos_backups = BackupLog.objects.select_related('acesso', 'cliente', 'template') \
        .order_by('-data_backup')[:10]

    # ── Top 5 clientes por nº de hosts ───────────────────────────
    top_clientes = Cliente.objects.annotate(
        total_hosts=Count('acessos')
    ).order_by('-total_hosts')[:5]

    # ── RPKI inválidos ────────────────────────────────────────────
    blocos_rpki_invalidos = BlocoIP.objects.filter(
        Q(rpki_valido=False) | Q(rpki_status__in=['Invalid', 'Unknown', 'Error', 'NotChecked'])
    ).select_related('cliente').order_by('-ultima_validacao')[:10]
    total_blocos_rpki_invalidos = BlocoIP.objects.filter(
        Q(rpki_valido=False) | Q(rpki_status__in=['Invalid', 'Unknown', 'Error', 'NotChecked'])
    ).count()

    # ── IRR inválidos ─────────────────────────────────────────────
    blocos_irr_invalidos = BlocoIP.objects.filter(
        Q(irr_valido=False) | Q(irr_status__in=['NotFound', 'ASN_Mismatch', 'Error'])
    ).select_related('cliente').order_by('-ultima_validacao')[:10]
    total_blocos_irr_invalidos = BlocoIP.objects.filter(
        Q(irr_valido=False) | Q(irr_status__in=['NotFound', 'ASN_Mismatch', 'Error'])
    ).count()

    # ── Gráfico: backups últimos 14 dias ─────────────────────────
    grafico_backups = []
    for i in range(13, -1, -1):
        d = datetime.now() - timedelta(days=i)
        d0 = d.replace(hour=0, minute=0, second=0, microsecond=0)
        d1 = d.replace(hour=23, minute=59, second=59, microsecond=999999)
        ok   = BackupLog.objects.filter(data_backup__range=(d0, d1), status='SUCESSO').count()
        erro = BackupLog.objects.filter(data_backup__range=(d0, d1), status='ERRO').count()
        grafico_backups.append({'data': d.strftime('%d/%m'), 'ok': ok, 'erro': erro})

    context = {
        'total_clientes': total_clientes,
        'total_acessos': total_acessos,
        'acessos_com_backup': acessos_com_backup,
        'backups_hoje_ok': backups_hoje_ok,
        'backups_hoje_erro': backups_hoje_erro,
        'backups_sem_mudanca': backups_sem_mudanca,
        'backups_total_hoje': backups_total_hoje,
        'ultimos_backups': ultimos_backups,
        'top_clientes': top_clientes,
        'blocos_rpki_invalidos': blocos_rpki_invalidos,
        'total_blocos_rpki_invalidos': total_blocos_rpki_invalidos,
        'blocos_irr_invalidos': blocos_irr_invalidos,
        'total_blocos_irr_invalidos': total_blocos_irr_invalidos,
        'grafico_backups': json.dumps(grafico_backups),
        'data_hoje': hoje,
    }

    return render(request, 'quadro_geral.html', context)


@login_required(login_url='login')
@admin_required
def relatorio_backups(request):
    from django.core.paginator import Paginator

    qs = BackupLog.objects.select_related('acesso', 'cliente', 'template').order_by('-data_backup')

    # Filtros
    cliente_id  = request.GET.get('cliente')
    status_f    = request.GET.get('status')
    data_ini    = request.GET.get('data_ini')
    data_fim    = request.GET.get('data_fim')

    if cliente_id:
        qs = qs.filter(cliente_id=cliente_id)
    if status_f:
        qs = qs.filter(status=status_f)
    if data_ini:
        try:
            qs = qs.filter(data_backup__date__gte=datetime.strptime(data_ini, '%Y-%m-%d').date())
        except ValueError:
            pass
    if data_fim:
        try:
            qs = qs.filter(data_backup__date__lte=datetime.strptime(data_fim, '%Y-%m-%d').date())
        except ValueError:
            pass

    paginator = Paginator(qs, 50)
    page = request.GET.get('page', 1)
    backups = paginator.get_page(page)

    clientes = Cliente.objects.order_by('nome_empresa')
    status_choices = [('SUCESSO', 'Sucesso'), ('ERRO', 'Erro'), ('PARCIAL', 'Parcial'), ('SEM_MUDANCAS', 'Sem Mudanças')]

    # Resumo
    total = qs.count()
    total_ok   = qs.filter(status='SUCESSO').count()
    total_erro = qs.filter(status='ERRO').count()
    total_sem  = qs.filter(status='SEM_MUDANCAS').count()

    context = {
        'backups': backups,
        'clientes': clientes,
        'status_choices': status_choices,
        'cliente_id': cliente_id or '',
        'status_f': status_f or '',
        'data_ini': data_ini or '',
        'data_fim': data_fim or '',
        'total': total,
        'total_ok': total_ok,
        'total_erro': total_erro,
        'total_sem': total_sem,
    }
    return render(request, 'relatorio_backups.html', context)


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
@superuser_required
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
@superuser_required
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
def lg_asn_info(request):
    """Retorna o nome/operadora de um ASN via RIPE stat API + fallback WHOIS."""
    import requests as req_lib
    import socket

    asn_raw = request.GET.get('asn', '').strip().upper().lstrip('AS')
    if not asn_raw.isdigit():
        return JsonResponse({'ok': False, 'erro': 'ASN inválido'}, status=400)

    asn = asn_raw  # apenas o número

    # 1) RIPE stat as-names (mais rápido)
    try:
        r = req_lib.get(
            f'https://stat.ripe.net/data/as-names/data.json?resource=AS{asn}',
            timeout=8,
            headers={'User-Agent': 'CRM-LG/1.0'},
        )
        data = r.json()
        nome = (data.get('data', {}).get('names') or {}).get(asn, '').strip()
        if nome:
            return JsonResponse({'ok': True, 'asn': asn, 'nome': nome})
    except Exception:
        pass

    # 2) Fallback: WHOIS raw via riswhois.ripe.net
    try:
        with socket.create_connection(('riswhois.ripe.net', 43), timeout=8) as s:
            s.sendall(f'-F AS{asn}\r\n'.encode())
            resp = b''
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
        texto = resp.decode('utf-8', errors='replace')
        for linha in texto.splitlines():
            if linha.startswith('%as'):
                # formato: %asXXXXX <nome>
                partes = linha.split(None, 1)
                if len(partes) > 1:
                    return JsonResponse({'ok': True, 'asn': asn, 'nome': partes[1].strip()})
    except Exception:
        pass

    # 3) Fallback: whois.radb.net
    try:
        with socket.create_connection(('whois.radb.net', 43), timeout=8) as s:
            s.sendall(f'AS{asn}\r\n'.encode())
            resp = b''
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
        texto = resp.decode('utf-8', errors='replace')
        for linha in texto.splitlines():
            l = linha.strip()
            if l.lower().startswith('as-name:') or l.lower().startswith('descr:'):
                valor = l.split(':', 1)[1].strip()
                if valor:
                    return JsonResponse({'ok': True, 'asn': asn, 'nome': valor})
    except Exception:
        pass

    return JsonResponse({'ok': True, 'asn': asn, 'nome': ''})


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

    # Mapeamento IX.br PTT ID → nome da cidade
    IXBR_CIDADES = {
        # Capitais / cidades principais (código de estado 2 letras)
        'SP':  'São Paulo/SP',       'RJ':  'Rio de Janeiro/RJ',
        'MG':  'Belo Horizonte/MG',  'CE':  'Fortaleza/CE',
        'DF':  'Brasília/DF',        'RS':  'Porto Alegre/RS',
        'PR':  'Curitiba/PR',        'BA':  'Salvador/BA',
        'PE':  'Recife/PE',          'GO':  'Goiânia/GO',
        'AM':  'Manaus/AM',          'PA':  'Belém/PA',
        'SC':  'Florianópolis/SC',   'ES':  'Vitória/ES',
        'MT':  'Cuiabá/MT',          'MS':  'Campo Grande/MS',
        'PB':  'João Pessoa/PB',     'RN':  'Natal/RN',
        'SE':  'Aracaju/SE',         'AL':  'Maceió/AL',
        'PI':  'Teresina/PI',        'MA':  'São Luís/MA',
        'RO':  'Porto Velho/RO',     'AC':  'Rio Branco/AC',
        'RR':  'Boa Vista/RR',       'TO':  'Palmas/TO',
        'AP':  'Macapá/AP',
        # Aliases código aeroporto / abreviatura de cidade
        'POA': 'Porto Alegre/RS',    'CWB': 'Curitiba/PR',
        'SSA': 'Salvador/BA',        'REC': 'Recife/PE',
        'GYN': 'Goiânia/GO',         'MAO': 'Manaus/AM',
        'BEL': 'Belém/PA',           'FLN': 'Florianópolis/SC',
        'VIX': 'Vitória/ES',         'CGB': 'Cuiabá/MT',
        'CGR': 'Campo Grande/MS',    'JPA': 'João Pessoa/PB',
        'NAT': 'Natal/RN',           'MCZ': 'Maceió/AL',
        'THE': 'Teresina/PI',        'STM': 'São Luís/MA',
        'PVH': 'Porto Velho/RO',     'PMW': 'Palmas/TO',
        'MCP': 'Macapá/AP',          'BVB': 'Boa Vista/RR',
        # Cidades do interior
        'CPV': 'Campina Grande/PB',  'CAS': 'Campinas/SP',
        'CAC': 'Cascavel/PR',        'CAU': 'Caruaru/PE',
        'CXJ': 'Caxias do Sul/RS',   'IMP': 'Imperatriz/MA',
        'JOI': 'Joinville/SC',       'LDB': 'Londrina/PR',
        'MGF': 'Maringá/PR',         'UBA': 'Uberlândia/MG',
        'SJP': 'S.J. dos Campos/SP', 'MAB': 'Marabá/PA',
        'PNA': 'Parnaíba/PI',        'SOB': 'Sobral/CE',
        'PAT': 'Patos/PB',           'PET': 'Pelotas/RS',
        'FCA': 'Feira de Santana/BA','ITJ': 'Itajaí/SC',
        'JUZ': 'Juazeiro do Norte/CE',
    }

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

    # ── Source 2: RIPE geoloc (geolocalização do prefixo) ────────────────────
    def query_geoloc():
        try:
            r = req_lib.get(
                f'https://stat.ripe.net/data/maxmind-geo-lite/data.json?resource={prefixo}',
                timeout=12, headers=HEADERS,
            )
            data = r.json()
            resources = data.get('data', {}).get('located_resources', [])
            for res in resources:
                locs = res.get('locations', [])
                if locs:
                    loc = max(locs, key=lambda x: x.get('covered_percentage', 0))
                    return {
                        'pais':      loc.get('country', ''),
                        'cidade':    loc.get('city', ''),
                        'latitude':  loc.get('latitude'),
                        'longitude': loc.get('longitude'),
                        'cobertura': loc.get('covered_percentage', 0),
                    }
        except Exception:
            pass
        return None

    # ── Source 3: RIPE prefix-overview (ASN info) ─────────────────────────────
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

    # ── Source 3: IX.br / NIC.br – PTTs Brasileiros ─────────────────────────
    def query_ixbr():
        """Uma chamada retorna todos os PTTs do IX.br que veem o prefixo."""
        try:
            r = req_lib.get(
                'https://lg.ix.br/api/v1/lookup/prefix',
                params={'q': prefixo},
                timeout=20,
                headers=HEADERS,
            )
            r.raise_for_status()
            data = r.json()

            routes = data.get('imported', {}).get('routes', [])
            if not routes:
                return {'fonte': 'IX.br', 'descricao': 'IX.br / NIC.br – PTTs Brasileiros',
                        'encontrado': False, 'vantage_points': []}

            # Agrupa por PTT (prefixo do RS ID: 'SP' de 'SP-rs1-v4')
            ptt_data = {}
            for route in routes:
                rs      = route.get('routeserver', {})
                rs_id   = rs.get('id', '')
                rs_name = rs.get('name', rs_id).replace(' (IPv4)', '').replace(' (IPv6)', '')
                # Extrai o prefixo do PTT (tudo antes do primeiro '-')
                ptt_key = rs_id.split('-')[0] if '-' in rs_id else rs_id
                # Só IPv4 para não duplicar
                if rs_id.endswith('-v6'):
                    continue
                path_list = route.get('bgp', {}).get('as_path', [])
                path_str  = ' '.join(str(a) for a in path_list) if path_list else ''
                if ptt_key not in ptt_data:
                    cidade = IXBR_CIDADES.get(ptt_key)
                    if not cidade:
                        # Extrai estado do hostname: rs1.fortaleza.ce.ix.br → CE
                        parts = rs_name.split('.')
                        estado = parts[2].upper() if len(parts) >= 3 else ptt_key
                        cidade = f'{ptt_key}/{estado} – IX.br'
                    ptt_data[ptt_key] = {
                        'nome':   f'PTT {cidade}',
                        'paths':  Counter(),
                        'n_peers': 0,
                    }
                if path_str:
                    ptt_data[ptt_key]['paths'][path_str] += 1
                ptt_data[ptt_key]['n_peers'] += 1

            vps = []
            for ptt_key, info in sorted(ptt_data.items()):
                mais_comum = info['paths'].most_common(1)
                vps.append({
                    'nome':     info['nome'],
                    'id':       ptt_key.lower(),
                    'as_path':  mais_comum[0][0] if mais_comum else '',
                    'n_peers':  info['n_peers'],
                    'pais':     'BR',
                    'nacional': True,
                })

            return {
                'fonte':       'IX.br',
                'descricao':   f'IX.br / NIC.br – {len(vps)} PTTs Brasileiros',
                'encontrado':  bool(vps),
                'vantage_points': vps,
            }
        except Exception as e:
            return {'fonte': 'IX.br', 'descricao': 'IX.br / NIC.br – PTTs Brasileiros',
                    'encontrado': False, 'vantage_points': [], 'erro': str(e)}

    # ── Source 4: RIS Whois (riswhois.ripe.net porta 43) ─────────────────────
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
    geo_future = None
    with ThreadPoolExecutor(max_workers=5) as ex:
        geo_future = ex.submit(query_geoloc)
        futures = {
            ex.submit(query_ripe_lg):       'ripe_lg',
            ex.submit(query_ripe_overview): 'ripe_overview',
            ex.submit(query_ixbr):          'ixbr',
            ex.submit(query_riswhois):      'riswhois',
        }
        for fut in as_completed(futures, timeout=30):
            try:
                resultados.append(fut.result())
            except Exception:
                pass
    geo_info_raw = None
    try:
        geo_info_raw = geo_future.result(timeout=3)
    except Exception:
        pass

    # ── Agrupa por AS path (RIPE RIS + IX.br têm paths completos) ────────────
    por_aspath = {}
    for res in resultados:
        if res['fonte'] not in ('RIPE RIS', 'IX.br'):
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
        'geo_info':          geo_info_raw,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GEOLOCALIZAÇÃO IP
# ─────────────────────────────────────────────────────────────────────────────

@login_required(login_url='login')
@admin_required
def geo_consulta(request):
    """Página principal da ferramenta de geolocalização de IPs/prefixos."""
    from clientes.models import ConfiguracaoSistema
    cfg = ConfiguracaoSistema.get()
    from django.urls import reverse
    geofeed_url = request.build_absolute_uri(reverse('geo_geofeed_csv'))
    return render(request, 'geo_consulta.html', {
        'prefixo_inicial': request.GET.get('prefixo', ''),
        'cfg': cfg,
        'geofeed_url': geofeed_url,
    })


@login_required(login_url='login')
@admin_required
def geo_consulta_buscar(request):
    """
    Consulta um IP/prefixo em 6 bancos de geolocalização em paralelo
    e retorna consenso + divergências como JSON.
    """
    import ipaddress
    import requests as req_lib
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from collections import Counter

    alvo = request.GET.get('prefixo', '').strip()
    if not alvo:
        return JsonResponse({'ok': False, 'erro': 'Informe um IP ou prefixo'}, status=400)

    try:
        net    = ipaddress.ip_network(alvo, strict=False)
        ip     = str(net.network_address)
        prefixo = str(net)
    except ValueError:
        return JsonResponse({'ok': False, 'erro': f'Endereço inválido: {alvo}'}, status=400)

    HEADERS = {'User-Agent': 'CRM-GEO/1.0', 'Accept': 'application/json'}
    TIMEOUT = 10

    def norm(fonte, *, pais='', pais_code='', regiao='', cidade='',
             lat=None, lon=None, isp='', org='', as_info='',
             ok=True, erro='', extra=None):
        return {
            'fonte': fonte, 'ok': ok, 'erro': erro,
            'pais': pais, 'pais_code': pais_code,
            'regiao': regiao, 'cidade': cidade,
            'lat': lat, 'lon': lon,
            'isp': isp, 'org': org, 'as_info': as_info,
            'extra': extra or {},
        }

    # ── Fonte 1: ip-api.com ──────────────────────────────────────────────────
    def query_ipapi():
        try:
            r = req_lib.get(
                f'http://ip-api.com/json/{ip}',
                params={'fields': 'status,message,country,countryCode,regionName,city,lat,lon,isp,org,as'},
                timeout=TIMEOUT, headers=HEADERS,
            )
            d = r.json()
            if d.get('status') != 'success':
                return norm('ip-api.com', ok=False, erro=d.get('message', 'não encontrado'))
            return norm('ip-api.com',
                pais=d.get('country', ''), pais_code=d.get('countryCode', ''),
                regiao=d.get('regionName', ''), cidade=d.get('city', ''),
                lat=d.get('lat'), lon=d.get('lon'),
                isp=d.get('isp', ''), org=d.get('org', ''), as_info=d.get('as', ''),
            )
        except Exception as e:
            return norm('ip-api.com', ok=False, erro=str(e))

    # ── Fonte 2: ipinfo.io ───────────────────────────────────────────────────
    def query_ipinfo():
        try:
            r = req_lib.get(f'https://ipinfo.io/{ip}/json', timeout=TIMEOUT, headers=HEADERS)
            d = r.json()
            if 'bogon' in d or 'error' in d:
                msg = d.get('error', {}).get('message', 'bogon/privado') if isinstance(d.get('error'), dict) else 'bogon/privado'
                return norm('ipinfo.io', ok=False, erro=msg)
            loc  = (d.get('loc') or ',').split(',')
            lat  = float(loc[0]) if len(loc) == 2 and loc[0] else None
            lon  = float(loc[1]) if len(loc) == 2 and loc[1] else None
            raw_org = d.get('org', '')
            asn = ''
            org = raw_org
            if raw_org.startswith('AS'):
                parts = raw_org.split(' ', 1)
                asn = parts[0]
                org = parts[1] if len(parts) > 1 else raw_org
            return norm('ipinfo.io',
                pais=d.get('country', ''), pais_code=d.get('country', ''),
                regiao=d.get('region', ''), cidade=d.get('city', ''),
                lat=lat, lon=lon, isp=raw_org, org=org, as_info=asn,
            )
        except Exception as e:
            return norm('ipinfo.io', ok=False, erro=str(e))

    # ── Fonte 3: MaxMind GeoLite2 via RIPE stat ──────────────────────────────
    def query_maxmind_ripe():
        try:
            r = req_lib.get(
                f'https://stat.ripe.net/data/maxmind-geo-lite/data.json?resource={prefixo}',
                timeout=TIMEOUT + 2, headers=HEADERS,
            )
            d = r.json()
            resources = d.get('data', {}).get('located_resources', [])
            for res in resources:
                locs = res.get('locations', [])
                if locs:
                    loc = max(locs, key=lambda x: x.get('covered_percentage', 0))
                    return norm('MaxMind/RIPE',
                        pais_code=loc.get('country', ''), pais=loc.get('country', ''),
                        cidade=loc.get('city', ''),
                        lat=loc.get('latitude'), lon=loc.get('longitude'),
                        extra={'cobertura': round(loc.get('covered_percentage', 0), 1)},
                    )
            return norm('MaxMind/RIPE', ok=False, erro='Recurso não localizado')
        except Exception as e:
            return norm('MaxMind/RIPE', ok=False, erro=str(e))

    # ── Fonte 4: DB-IP.com ───────────────────────────────────────────────────
    def query_dbip():
        try:
            r = req_lib.get(f'https://api.db-ip.com/v2/free/{ip}', timeout=TIMEOUT, headers=HEADERS)
            d = r.json()
            if 'error' in d:
                return norm('DB-IP.com', ok=False, erro=d['error'])
            return norm('DB-IP.com',
                pais=d.get('countryName', ''), pais_code=d.get('countryCode', ''),
                regiao=d.get('stateProv', ''), cidade=d.get('city', ''),
                isp=d.get('isp', ''), org=d.get('organization', ''),
            )
        except Exception as e:
            return norm('DB-IP.com', ok=False, erro=str(e))

    # ── Fonte 5: ipwhois.app ─────────────────────────────────────────────────
    def query_ipwhois():
        try:
            r = req_lib.get(f'https://ipwhois.app/json/{ip}', timeout=TIMEOUT, headers=HEADERS)
            d = r.json()
            if not d.get('success', True):
                return norm('ipwhois.app', ok=False, erro=d.get('message', 'falha'))
            return norm('ipwhois.app',
                pais=d.get('country', ''), pais_code=d.get('country_code', ''),
                regiao=d.get('region', ''), cidade=d.get('city', ''),
                lat=d.get('latitude'), lon=d.get('longitude'),
                isp=d.get('isp', ''), org=d.get('org', ''), as_info=d.get('as', ''),
            )
        except Exception as e:
            return norm('ipwhois.app', ok=False, erro=str(e))

    # ── Fonte 6: LACNIC RDAP ─────────────────────────────────────────────────
    def query_lacnic_rdap():
        try:
            rdap_headers = {**HEADERS, 'Accept': 'application/rdap+json'}
            r = req_lib.get(
                f'https://rdap.lacnic.net/rdap/ip/{ip}',
                timeout=TIMEOUT, headers=rdap_headers,
            )
            if r.status_code == 404:
                return norm('LACNIC RDAP', ok=False, erro='Prefixo não pertence ao LACNIC')
            r.raise_for_status()
            d = r.json()
            pais_code = d.get('country', '')
            nome_obj  = d.get('name', '')
            handle    = d.get('handle', '')
            # Extrai org do vcard da entidade registrant
            org_nome = ''
            for entity in d.get('entities', []):
                if 'registrant' in entity.get('roles', []):
                    vcard = entity.get('vcardArray', [])
                    if len(vcard) > 1:
                        for field in vcard[1]:
                            if isinstance(field, list) and len(field) >= 4 and field[0] == 'fn':
                                org_nome = str(field[3])
                                break
            return norm('LACNIC RDAP',
                pais_code=pais_code, pais=pais_code,
                isp=org_nome or nome_obj, org=org_nome or nome_obj,
                as_info=handle,
                extra={'handle': handle},
            )
        except Exception as e:
            return norm('LACNIC RDAP', ok=False, erro=str(e))

    # ── Executa em paralelo ──────────────────────────────────────────────────
    resultados = []
    fontes_fn  = [query_ipapi, query_ipinfo, query_maxmind_ripe,
                  query_dbip,  query_ipwhois, query_lacnic_rdap]
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(fn): fn.__name__ for fn in fontes_fn}
        for fut in as_completed(futures, timeout=18):
            try:
                resultados.append(fut.result())
            except Exception as e:
                resultados.append(norm(futures[fut], ok=False, erro=str(e)))

    # ── Consenso ─────────────────────────────────────────────────────────────
    ok_results = [r for r in resultados if r.get('ok')]

    def maioria(campo):
        vals = [r.get(campo, '').strip() for r in ok_results if r.get(campo, '').strip()]
        return Counter(vals).most_common(1)[0][0] if vals else ''

    consenso = {
        'pais_code': maioria('pais_code'),
        'pais':      maioria('pais'),
        'regiao':    maioria('regiao'),
        'cidade':    maioria('cidade'),
        'isp':       maioria('isp'),
        'org':       maioria('org'),
    }

    for r in resultados:
        if not r.get('ok'):
            r['concorda'] = None
            continue
        cp = (r.get('pais_code', '').upper() == consenso['pais_code'].upper()) if consenso['pais_code'] else True
        cc = (r.get('cidade', '').lower() == consenso['cidade'].lower())       if consenso['cidade']    else True
        r['concorda'] = cp and cc

    total_ok        = len(ok_results)
    total_concordam = sum(1 for r in ok_results if r.get('concorda'))

    return JsonResponse({
        'ok':             True,
        'alvo':           alvo,
        'ip':             ip,
        'prefixo':        prefixo,
        'resultados':     resultados,
        'consenso':       consenso,
        'total_fontes':   len(resultados),
        'total_ok':       total_ok,
        'total_concordam': total_concordam,
    })


@login_required(login_url='login')
@admin_required
def geo_atualizar(request):
    """Gera Geofeed RFC 8805, envia formulario MaxMind e e-mails para RIRs."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'erro': 'Metodo nao permitido'}, status=405)

    import json as _json, re as _re, smtplib
    from datetime import date
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    import requests as req_lib
    from clientes.models import ConfiguracaoSistema

    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'erro': 'JSON invalido'}, status=400)

    prefixo       = body.get('prefixo', '').strip()
    pais          = body.get('pais', '').strip().upper()
    regiao        = body.get('regiao', '').strip()
    cidade        = body.get('cidade', '').strip()
    org           = body.get('org', '').strip()
    lat           = body.get('lat', '').strip()
    lon           = body.get('lon', '').strip()
    email_contato = body.get('email_contato', '').strip()
    destinos_sel  = body.get('destinos', [])

    if not prefixo:
        return JsonResponse({'ok': False, 'erro': 'Prefixo nao informado'}, status=400)
    if (destinos_sel) and not email_contato:
        return JsonResponse({'ok': False, 'erro': 'Informe o e-mail de contato'}, status=400)

    hoje = date.today().isoformat()
    ip   = prefixo.split('/')[0]
    cfg  = ConfiguracaoSistema.get()

    # ── ISO 3166-2 para regiao (ex: SP -> BR-SP) ──────────────────────────────
    regiao_iso = ''
    if pais and regiao:
        r = regiao.strip()
        if len(r) <= 3 and '-' not in r:
            regiao_iso = f'{pais}-{r.upper()}'
        elif r.upper().startswith(pais + '-'):
            regiao_iso = r.upper()
        else:
            regiao_iso = r

    # ── Gera CSV Geofeed RFC 8805 (somente ASCII nos comentarios) ─────────────
    csv_content = '\n'.join([
        f'# Geofeed - gerado em {hoje}',
        '# Formato RFC 8805 - Prefix,Country,Region,City,Postal-Code',
        f'{prefixo},{pais},{regiao_iso},{cidade},',
    ])

    enviados = []
    erros    = []

    # ── Helper: extrai CSRF e faz POST em formulario MaxMind ─────────────────
    MM_UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0'
    MM_CSRF_PATS = [
        r'<input[^>]+name=["\']csrf_token["\'][^>]+value=["\']([^"\']+)',
        r'<input[^>]+value=["\']([^"\']+)["\'][^>]+name=["\']csrf_token["\']',
        r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)',
    ]

    def _mm_get_csrf(url):
        s = req_lib.Session()
        rg = s.get(url, timeout=15, headers={'User-Agent': MM_UA})
        csrf = ''
        for pat in MM_CSRF_PATS:
            m = _re.search(pat, rg.text, _re.I)
            if m:
                csrf = m.group(1)
                break
        return s, csrf

    def _mm_post(session, url, data):
        return session.post(url, data=data, timeout=35,
            headers={'Referer': url, 'User-Agent': MM_UA, 'Origin': 'https://www.maxmind.com',
                     'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'},
            allow_redirects=True)

    def _mm_resultado(r, label):
        txt = r.text.lower()
        if r.status_code in (200, 201):
            return {'ok': True,  'info': 'Aguardando confirmacao por e-mail'}
        elif 'already submitted' in txt:
            return {'ok': True,  'info': 'Ja enviado anteriormente (aguarde 2 semanas)'}
        else:
            import html as _html
            errs = _re.findall(
                r'class="[^"]*(?:invalid-feedback|field-error|alert-danger)[^"]*"[^>]*>\s*([^<]{4,200})',
                r.text, _re.I)
            det = '; '.join(e.strip() for e in errs[:2]) if errs else ''
            return {'ok': False, 'erro': f'HTTP {r.status_code}' + (f': {_html.unescape(det)}' if det else '')}

    # ── MaxMind: correcao de geolocalizacao ───────────────────────────────────
    if 'maxmind' in destinos_sel:
        try:
            mm_url = 'https://www.maxmind.com/en/geoip-location-correction'
            sess, csrf = _mm_get_csrf(mm_url)
            fd = {
                'ip_address':    prefixo,
                'country':       pais,
                'region':        '',   # select dinamico - nao enviar valor invalido
                'city':          cidade,
                'postal_code':   '',
                'email_address': email_contato,
                'other':         (f'Network: {prefixo}. '
                                  + (f'Org: {org}. ' if org else '')
                                  + (f'Coords: {lat},{lon}' if lat and lon else '')).strip(),
                'certification': '1',
            }
            if csrf:
                fd['csrf_token'] = csrf
            res = _mm_resultado(_mm_post(sess, mm_url, fd), 'MaxMind Geo')
            if res['ok']:
                enviados.append({'label': 'MaxMind Geo', 'tipo': 'web',
                                 'info': res['info'], 'email_confirmado': False})
            else:
                erros.append({'label': 'MaxMind Geo', 'erro': res['erro']})
        except Exception as e:
            erros.append({'label': 'MaxMind Geo', 'erro': str(e)})

    # ── MaxMind: correcao de ISP / Organizacao ────────────────────────────────
    if 'maxmind_org' in destinos_sel:
        try:
            org_nome  = body.get('org_nome', '').strip() or org
            corr_type = body.get('correction_type', 'Both').strip() or 'Both'
            if not org_nome:
                erros.append({'label': 'MaxMind ISP/Org', 'erro': 'Nome da organizacao nao informado'})
            else:
                mm_url2 = 'https://www.maxmind.com/en/geoip-isp-org-correction'
                sess2, csrf2 = _mm_get_csrf(mm_url2)
                fd2 = {
                    'ip_addresses':    prefixo,
                    'correction_type': corr_type,
                    'isp_org':         org_nome,
                    'email_address':   email_contato,
                    'other':           f'Network: {prefixo}.',
                    'certification':   '1',
                }
                if csrf2:
                    fd2['csrf_token'] = csrf2
                res2 = _mm_resultado(_mm_post(sess2, mm_url2, fd2), 'MaxMind ISP/Org')
                if res2['ok']:
                    enviados.append({'label': 'MaxMind ISP/Org', 'tipo': 'web',
                                     'info': res2['info'], 'email_confirmado': False,
                                     'org_nome': org_nome, 'correction_type': corr_type})
                else:
                    erros.append({'label': 'MaxMind ISP/Org', 'erro': res2['erro']})
        except Exception as e:
            erros.append({'label': 'MaxMind ISP/Org', 'erro': str(e)})

    # ── RIRs por e-mail ───────────────────────────────────────────────────────
    RIR_DESTINOS = {
        'lacnic': {'label': 'LACNIC', 'email': 'hostmaster@lacnic.net'},
        'arin':   {'label': 'ARIN',   'email': 'hostmaster@arin.net'},
    }
    rir_sel = [k for k in destinos_sel if k in RIR_DESTINOS]

    if rir_sel:
        corpo_linhas = [
            'Hello,',
            '',
            'We are requesting a geolocation correction for the following IP prefix:',
            '',
            f'  Prefix / Network:   {prefixo}',
            f'  IP Address:         {ip}',
        ]
        if pais:   corpo_linhas.append(f'  Country (correct):  {pais}')
        if regiao: corpo_linhas.append(f'  Region / State:     {regiao}')
        if cidade: corpo_linhas.append(f'  City:               {cidade}')
        if org:    corpo_linhas.append(f'  Organization / ISP: {org}')
        if lat and lon: corpo_linhas.append(f'  Coordinates:        {lat}, {lon}')
        corpo_linhas += [
            '',
            'This prefix is under our administration and the current geolocation',
            'data is incorrect. Please update it to reflect the information above.',
            '',
            'Thank you for your assistance.',
            '',
            'Best regards,',
            request.user.get_full_name() or request.user.username,
            f'Contact: {email_contato}',
        ]
        corpo = '\n'.join(corpo_linhas)

        if not cfg.smtp_host or not cfg.smtp_user:
            for k in rir_sel:
                erros.append({'label': RIR_DESTINOS[k]['label'], 'erro': 'SMTP nao configurado'})
        else:
            try:
                with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as server:
                    server.ehlo()
                    if cfg.smtp_use_tls:
                        server.starttls()
                    server.login(cfg.smtp_user, cfg.smtp_pass)
                    remetente = cfg.smtp_from or cfg.smtp_user
                    for chave in rir_sel:
                        info = RIR_DESTINOS[chave]
                        try:
                            msg = MIMEMultipart('alternative')
                            msg['Subject']  = f'Geolocation correction request: {prefixo}'
                            msg['From']     = remetente
                            msg['To']       = info['email']
                            msg['Reply-To'] = email_contato
                            msg.attach(MIMEText(corpo, 'plain', 'utf-8'))
                            server.sendmail(remetente, [info['email']], msg.as_string())
                            enviados.append({'label': info['label'], 'tipo': 'email',
                                             'email': info['email']})
                        except Exception as e:
                            erros.append({'label': info['label'], 'email': info['email'],
                                          'erro': str(e)})
            except Exception as e:
                for k in rir_sel:
                    erros.append({'label': RIR_DESTINOS[k]['label'], 'erro': f'SMTP: {e}'})

    # ── Portais para acesso direto ────────────────────────────────────────────
    portais = [
        {'nome': 'RIPE Database', 'url': 'https://apps.db.ripe.net/',
         'desc': 'Editar inetnum - adicionar atributo geofeed:', 'cor': '#00d9ff'},
        {'nome': 'LACNIC Portal', 'url': 'https://lacnic.net/lacnic/nic/portal/',
         'desc': 'Gerenciar recursos - editar objeto inetnum', 'cor': '#00c864'},
        {'nome': 'ARIN Online',   'url': 'https://account.arin.net/',
         'desc': 'Gerenciar recursos - editar objeto NET', 'cor': '#ffaa00'},
        {'nome': 'ipinfo.io',     'url': 'https://ipinfo.io/data-correction',
         'desc': 'Formulario de correcao de dados', 'cor': '#ff6eb4'},
    ]

    # ── Salva no historico (somente ASCII no JSONField) ───────────────────────
    from clientes.models import CorrecaoGeoIP
    dest_log = [{'tipo': 'geofeed', 'label': 'RFC 8805 Geofeed'}]
    for e in enviados:
        dest_log.append({'tipo': e['tipo'], 'label': e['label'],
                         'email': e.get('email', ''), 'info': e.get('info', '')})
    CorrecaoGeoIP.objects.create(
        prefixo        = prefixo,
        pais           = pais,
        regiao         = regiao,
        cidade         = cidade,
        org            = org,
        lat            = lat,
        lon            = lon,
        destinos_email = dest_log,
        solicitante    = request.user,
    )

    return JsonResponse({
        'ok':       True,
        'csv':      csv_content,
        'enviados': enviados,
        'erros':    erros,
        'portais':  portais,
        'mensagem': f'Geofeed gerado. {len(enviados)} submissao(oes) realizada(s).',
    })


# ── Geofeed público RFC 8805 ─────────────────────────────────────────────────

def geo_geofeed_csv(request):
    """Arquivo público Geofeed RFC 8805 — sem autenticação (referenciado no WHOIS)."""
    from clientes.models import CorrecaoGeoIP
    from django.http import HttpResponse
    from datetime import date

    hoje = date.today().isoformat()
    base_url = request.build_absolute_uri('/').rstrip('/')

    linhas = [
        f'# Geofeed RFC 8805',
        f'# Gerado em: {hoje}',
        f'# {base_url}',
        '# Prefix,Country,Region,City,Postal-Code',
        '',
    ]

    seen = set()
    for reg in CorrecaoGeoIP.objects.order_by('prefixo', '-data_envio'):
        if reg.prefixo in seen:
            continue
        seen.add(reg.prefixo)

        regiao_iso = ''
        if reg.pais and reg.regiao:
            r = reg.regiao.strip()
            if len(r) <= 3 and '-' not in r:
                regiao_iso = f'{reg.pais}-{r.upper()}'
            elif r.upper().startswith(reg.pais.upper() + '-'):
                regiao_iso = r.upper()
            else:
                regiao_iso = r

        linhas.append(f'{reg.prefixo},{reg.pais},{regiao_iso},{reg.cidade},')

    content = '\n'.join(linhas) + '\n'
    resp = HttpResponse(content, content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = 'inline; filename="geofeed.csv"'
    resp['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp['X-Content-Type-Options'] = 'nosniff'
    return resp


# ── Histórico de correções ────────────────────────────────────────────────────

@login_required(login_url='login')
@admin_required
def geo_historico(request):
    """Retorna o histórico de solicitações de correção como JSON."""
    from clientes.models import CorrecaoGeoIP

    qs = CorrecaoGeoIP.objects.select_related('solicitante').order_by('-data_envio')[:50]
    registros = []
    for c in qs:
        registros.append({
            'id':                  c.id,
            'prefixo':             c.prefixo,
            'pais':                c.pais,
            'regiao':              c.regiao,
            'cidade':              c.cidade,
            'org':                 c.org,
            'destinos_email':      c.destinos_email,
            'data_envio':          c.data_envio.strftime('%d/%m/%Y %H:%M'),
            'solicitante':         c.solicitante.username if c.solicitante else '—',
            'resposta_recebida':   c.resposta_recebida,
            'resposta_remetente':  c.resposta_remetente,
            'resposta_assunto':    c.resposta_assunto,
            'resposta_texto':      c.resposta_texto[:800] if c.resposta_texto else '',
            'data_resposta':       c.data_resposta.strftime('%d/%m/%Y %H:%M') if c.data_resposta else None,
            'aplicado':            c.aplicado,
            'ultima_verificacao':  c.ultima_verificacao.strftime('%d/%m/%Y %H:%M') if c.ultima_verificacao else None,
            'fontes_aplicadas':    c.fontes_aplicadas,
            'fontes_total':        c.fontes_total,
        })
    return JsonResponse({'ok': True, 'registros': registros})


# ── Verificar resposta IMAP dos RIRs ─────────────────────────────────────────

@login_required(login_url='login')
@admin_required
def geo_verificar_resposta(request, correcao_id):
    """
    Verifica via IMAP se algum RIR respondeu ao e-mail de correção enviado.
    Busca e-mails recebidos após o envio da correção, originados dos domínios
    dos RIRs (ripe.net, lacnic.net, arin.net) que contenham o prefixo no assunto
    ou no corpo.
    """
    import imaplib
    import email as email_lib
    from email.header import decode_header
    from clientes.models import CorrecaoGeoIP, ConfiguracaoSistema

    correcao = CorrecaoGeoIP.objects.filter(id=correcao_id).first()
    if not correcao:
        return JsonResponse({'ok': False, 'erro': 'Registro não encontrado'}, status=404)

    cfg = ConfiguracaoSistema.get()
    if not cfg.imap_host or not cfg.smtp_user:
        return JsonResponse(
            {'ok': False, 'erro': 'IMAP não configurado. Acesse Configurações → IMAP.'},
            status=400,
        )

    # Domínios de resposta dos RIRs
    RIR_DOMINIOS = ['ripe.net', 'lacnic.net', 'arin.net', 'apnic.net']
    prefixo_busca = correcao.prefixo.replace('/', '_')   # alguns servidores IMAP não aceitam /

    def _decode_header_str(raw):
        if not raw:
            return ''
        result = ''
        for part, enc in decode_header(raw):
            if isinstance(part, bytes):
                result += part.decode(enc or 'utf-8', errors='replace')
            else:
                result += str(part)
        return result

    def _extrair_corpo(msg):
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/plain':
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or 'utf-8', errors='replace')
        return ''

    try:
        if cfg.imap_use_ssl:
            mail = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
        else:
            mail = imaplib.IMAP4(cfg.imap_host, cfg.imap_port)

        mail.login(cfg.smtp_user, cfg.smtp_pass)
        mail.select('INBOX')

        # Data de envio como limite inferior da busca
        data_desde = correcao.data_envio.strftime('%d-%b-%Y')

        # Tenta busca por cada domínio RIR
        ids_encontrados = []
        for dominio in RIR_DOMINIOS:
            _, msgs = mail.search(None, f'(SINCE {data_desde} FROM "{dominio}")')
            if msgs[0]:
                ids_encontrados.extend(msgs[0].split())

        # Remove duplicatas mantendo ordem
        seen = set()
        ids_unicos = []
        for uid in reversed(ids_encontrados):
            if uid not in seen:
                seen.add(uid)
                ids_unicos.append(uid)

        if not ids_unicos:
            mail.logout()
            return JsonResponse({
                'ok':       True,
                'encontrou': False,
                'mensagem': 'Nenhuma resposta de RIR encontrada na caixa de entrada ainda.',
            })

        # Itera do mais recente para o mais antigo, buscando o prefixo no corpo
        msg_match = None
        for uid in ids_unicos:
            _, data = mail.fetch(uid, '(BODY.PEEK[TEXT])')
            if not data or not data[0]:
                continue
            raw_text = data[0][1] if isinstance(data[0], tuple) else b''
            texto = raw_text.decode('utf-8', errors='replace').lower()
            # Busca pelo prefixo (com e sem máscara) ou pela rede sem a parte /xx
            rede = correcao.prefixo.split('/')[0]
            if correcao.prefixo.lower() in texto or rede.lower() in texto:
                msg_match = uid
                break

        if not msg_match:
            # Aceita qualquer resposta de RIR no período mesmo sem o prefixo
            msg_match = ids_unicos[0]

        # Busca mensagem completa
        _, data_full = mail.fetch(msg_match, '(RFC822)')
        raw_full = data_full[0][1] if data_full and data_full[0] else b''
        msg = email_lib.message_from_bytes(raw_full)

        remetente = _decode_header_str(msg.get('From', ''))
        assunto   = _decode_header_str(msg.get('Subject', ''))
        corpo     = _extrair_corpo(msg)

        from datetime import datetime
        data_msg_str = msg.get('Date', '')
        try:
            from email.utils import parsedate_to_datetime
            data_msg = parsedate_to_datetime(data_msg_str)
        except Exception:
            data_msg = datetime.now()

        mail.logout()

        # Atualiza o registro
        correcao.resposta_recebida  = True
        correcao.resposta_remetente = remetente
        correcao.resposta_assunto   = assunto
        correcao.resposta_texto     = corpo[:4000]
        correcao.data_resposta      = data_msg
        correcao.save()

        return JsonResponse({
            'ok':         True,
            'encontrou':  True,
            'remetente':  remetente,
            'assunto':    assunto,
            'corpo':      corpo[:2000],
            'data':       data_msg.strftime('%d/%m/%Y %H:%M'),
        })

    except imaplib.IMAP4.error as e:
        return JsonResponse({'ok': False, 'erro': f'Falha IMAP: {e}'}, status=500)
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': f'Erro: {e}'}, status=500)


# ── Verificar se a correção foi aplicada nos bancos ──────────────────────────

@login_required(login_url='login')
@admin_required
def geo_verificar_aplicacao(request, correcao_id):
    """
    Re-consulta todos os bancos de geolocalização e compara os resultados
    com os dados submetidos na correção.  Atualiza o status de aplicação.
    """
    import ipaddress
    import requests as req_lib
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime as dt
    from clientes.models import CorrecaoGeoIP

    correcao = CorrecaoGeoIP.objects.filter(id=correcao_id).first()
    if not correcao:
        return JsonResponse({'ok': False, 'erro': 'Registro não encontrado'}, status=404)

    prefixo = correcao.prefixo
    try:
        net = ipaddress.ip_network(prefixo, strict=False)
        ip  = str(net.network_address)
    except ValueError:
        return JsonResponse({'ok': False, 'erro': 'Prefixo inválido no registro'}, status=400)

    HEADERS = {'User-Agent': 'CRM-GEO/1.0', 'Accept': 'application/json'}
    TIMEOUT = 10

    pais_esperado   = correcao.pais.upper()
    cidade_esperada = correcao.cidade.lower()

    def checa(fonte, pais_code, cidade):
        pais_ok   = (pais_code or '').upper()   == pais_esperado   if pais_esperado   else True
        cidade_ok = (cidade    or '').lower()   == cidade_esperada if cidade_esperada else True
        return pais_ok and cidade_ok

    resultados = []

    def q_ipapi():
        try:
            r = req_lib.get(f'http://ip-api.com/json/{ip}',
                params={'fields': 'status,countryCode,city'},
                timeout=TIMEOUT, headers=HEADERS)
            d = r.json()
            if d.get('status') != 'success':
                return {'fonte': 'ip-api.com', 'ok': False}
            return {'fonte': 'ip-api.com', 'ok': True,
                    'pais_code': d.get('countryCode',''), 'cidade': d.get('city',''),
                    'aplicado': checa('ip-api.com', d.get('countryCode'), d.get('city'))}
        except Exception:
            return {'fonte': 'ip-api.com', 'ok': False}

    def q_ipinfo():
        try:
            r = req_lib.get(f'https://ipinfo.io/{ip}/json', timeout=TIMEOUT, headers=HEADERS)
            d = r.json()
            if 'bogon' in d or 'error' in d:
                return {'fonte': 'ipinfo.io', 'ok': False}
            return {'fonte': 'ipinfo.io', 'ok': True,
                    'pais_code': d.get('country',''), 'cidade': d.get('city',''),
                    'aplicado': checa('ipinfo.io', d.get('country'), d.get('city'))}
        except Exception:
            return {'fonte': 'ipinfo.io', 'ok': False}

    def q_maxmind():
        try:
            r = req_lib.get(
                f'https://stat.ripe.net/data/maxmind-geo-lite/data.json?resource={prefixo}',
                timeout=TIMEOUT + 2, headers=HEADERS)
            d = r.json()
            for res in d.get('data', {}).get('located_resources', []):
                locs = res.get('locations', [])
                if locs:
                    loc = max(locs, key=lambda x: x.get('covered_percentage', 0))
                    return {'fonte': 'MaxMind/RIPE', 'ok': True,
                            'pais_code': loc.get('country',''), 'cidade': loc.get('city',''),
                            'aplicado': checa('MaxMind/RIPE', loc.get('country'), loc.get('city'))}
            return {'fonte': 'MaxMind/RIPE', 'ok': False}
        except Exception:
            return {'fonte': 'MaxMind/RIPE', 'ok': False}

    def q_dbip():
        try:
            r = req_lib.get(f'https://api.db-ip.com/v2/free/{ip}', timeout=TIMEOUT, headers=HEADERS)
            d = r.json()
            if 'error' in d:
                return {'fonte': 'DB-IP.com', 'ok': False}
            return {'fonte': 'DB-IP.com', 'ok': True,
                    'pais_code': d.get('countryCode',''), 'cidade': d.get('city',''),
                    'aplicado': checa('DB-IP.com', d.get('countryCode'), d.get('city'))}
        except Exception:
            return {'fonte': 'DB-IP.com', 'ok': False}

    def q_ipwhois():
        try:
            r = req_lib.get(f'https://ipwhois.app/json/{ip}', timeout=TIMEOUT, headers=HEADERS)
            d = r.json()
            if not d.get('success', True):
                return {'fonte': 'ipwhois.app', 'ok': False}
            return {'fonte': 'ipwhois.app', 'ok': True,
                    'pais_code': d.get('country_code',''), 'cidade': d.get('city',''),
                    'aplicado': checa('ipwhois.app', d.get('country_code'), d.get('city'))}
        except Exception:
            return {'fonte': 'ipwhois.app', 'ok': False}

    def q_lacnic():
        try:
            r = req_lib.get(f'https://rdap.lacnic.net/rdap/ip/{ip}',
                timeout=TIMEOUT, headers={**HEADERS, 'Accept': 'application/rdap+json'})
            if r.status_code == 404:
                return {'fonte': 'LACNIC RDAP', 'ok': False}
            d = r.json()
            pais = d.get('country', '')
            return {'fonte': 'LACNIC RDAP', 'ok': True,
                    'pais_code': pais, 'cidade': '',
                    'aplicado': checa('LACNIC RDAP', pais, None)}
        except Exception:
            return {'fonte': 'LACNIC RDAP', 'ok': False}

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(f) for f in [q_ipapi, q_ipinfo, q_maxmind, q_dbip, q_ipwhois, q_lacnic]]
        for fut in as_completed(futs, timeout=18):
            try:
                resultados.append(fut.result())
            except Exception:
                pass

    ok_res        = [r for r in resultados if r.get('ok')]
    aplicadas     = [r for r in ok_res if r.get('aplicado')]
    total_ok      = len(ok_res)
    total_aplicado = len(aplicadas)

    # Considerado "aplicado" se ≥ metade das fontes disponíveis confirmarem
    aplicado_geral = total_aplicado >= max(1, total_ok // 2) if total_ok > 0 else False

    # Persiste resultado
    correcao.ultima_verificacao    = dt.now()
    correcao.fontes_aplicadas      = total_aplicado
    correcao.fontes_total          = total_ok
    correcao.aplicado              = aplicado_geral
    correcao.resultado_verificacao = resultados
    correcao.save()

    return JsonResponse({
        'ok':              True,
        'aplicado':        aplicado_geral,
        'fontes_aplicadas': total_aplicado,
        'fontes_total':    total_ok,
        'resultados':      resultados,
        'mensagem': (
            f'Correção aplicada em {total_aplicado}/{total_ok} fontes.'
            if aplicado_geral else
            f'Correção ainda não propagada ({total_aplicado}/{total_ok} fontes atualizadas).'
        ),
    })


# ── Confirma e-mail MaxMind via IMAP ─────────────────────────────────────────

@login_required(login_url='login')
@admin_required
def geo_confirmar_maxmind(request, correcao_id):
    """
    Busca via IMAP o e-mail de confirmação enviado pelo MaxMind
    (noreply@maxmind.com) e acessa o link de validação automaticamente.
    """
    import imaplib
    import email as email_lib
    from email.header import decode_header
    import re as _re
    import requests as req_lib
    from clientes.models import CorrecaoGeoIP, ConfiguracaoSistema

    correcao = CorrecaoGeoIP.objects.filter(id=correcao_id).first()
    if not correcao:
        return JsonResponse({'ok': False, 'erro': 'Registro não encontrado'}, status=404)

    cfg = ConfiguracaoSistema.get()
    if not cfg.imap_host or not cfg.smtp_user:
        return JsonResponse(
            {'ok': False, 'erro': 'IMAP não configurado. Acesse Configurações → IMAP.'},
            status=400,
        )

    def _decode_str(raw):
        if not raw:
            return ''
        result = ''
        for part, enc in decode_header(raw):
            if isinstance(part, bytes):
                result += part.decode(enc or 'utf-8', errors='replace')
            else:
                result += str(part)
        return result

    def _corpo_texto(msg):
        """Extrai texto plano e HTML do e-mail."""
        partes = []
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct in ('text/plain', 'text/html'):
                    payload = part.get_payload(decode=True)
                    if payload:
                        partes.append(payload.decode(
                            part.get_content_charset() or 'utf-8', errors='replace'))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                partes.append(payload.decode(
                    msg.get_content_charset() or 'utf-8', errors='replace'))
        return '\n'.join(partes)

    try:
        if cfg.imap_use_ssl:
            mail = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
        else:
            mail = imaplib.IMAP4(cfg.imap_host, cfg.imap_port)

        mail.login(cfg.smtp_user, cfg.smtp_pass)
        mail.select('INBOX')

        data_desde = correcao.data_envio.strftime('%d-%b-%Y')

        # Busca e-mails do MaxMind após a data de envio
        _, msgs = mail.search(None, f'(SINCE {data_desde} FROM "maxmind.com")')
        ids = msgs[0].split() if msgs[0] else []

        if not ids:
            mail.logout()
            return JsonResponse({
                'ok': True,
                'confirmado': False,
                'mensagem': 'Nenhum e-mail do MaxMind encontrado na caixa de entrada ainda. Aguarde alguns minutos e tente novamente.',
            })

        # Coleta todos os links de confirmacao encontrados nos e-mails MaxMind
        links_encontrados = []
        for uid in reversed(ids):
            _, data = mail.fetch(uid, '(RFC822)')
            if not data or not data[0]:
                continue
            raw = data[0][1] if isinstance(data[0], tuple) else b''
            msg = email_lib.message_from_bytes(raw)
            if 'maxmind' not in _decode_str(msg.get('From', '')).lower():
                continue
            corpo = _corpo_texto(msg)
            for m in _re.finditer(
                r'https?://www\.maxmind\.com/en/confirm\?t=[A-Za-z0-9_\-]+', corpo
            ):
                lnk = m.group(0)
                if lnk not in links_encontrados:
                    links_encontrados.append(lnk)

        mail.logout()

        if not links_encontrados:
            return JsonResponse({
                'ok': True,
                'confirmado': False,
                'mensagem': 'Nenhum link de confirmacao MaxMind encontrado. Aguarde alguns minutos ou o e-mail ja foi confirmado anteriormente.',
            })

        # Acessa cada link e coleta resultados
        MM_UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0'
        confirmados = 0
        expirados   = 0
        for link in links_encontrados:
            try:
                r = req_lib.get(link, timeout=35, headers={'User-Agent': MM_UA},
                                allow_redirects=True)
                url_f = r.url.lower()
                txt   = r.text.lower()
                if 'confirmed' in url_f or any(kw in txt for kw in ('confirmed', 'thank', 'success')):
                    confirmados += 1
                elif 'expired' in txt or 'invalid' in txt:
                    expirados += 1
            except Exception:
                pass

        if confirmados == 0 and expirados > 0:
            return JsonResponse({
                'ok':         True,
                'confirmado': False,
                'mensagem':   'Link(s) de confirmacao expirado(s). Reenvie o formulario para receber novos links (validos por 24h).',
            })

        if confirmados > 0:
            # Marca todas as entradas MaxMind como confirmadas (sem acentos no JSON)
            dest = correcao.destinos_email or []
            for d in dest:
                if d.get('label', '').startswith('MaxMind'):
                    d['email_confirmado'] = True
            correcao.destinos_email = dest
            correcao.save()
            return JsonResponse({
                'ok':         True,
                'confirmado': True,
                'total':      confirmados,
                'mensagem':   f'{confirmados} e-mail(s) MaxMind confirmado(s) com sucesso! A correcao sera revisada em breve.',
            })

        return JsonResponse({
            'ok':         True,
            'confirmado': False,
            'mensagem':   f'Nao foi possivel confirmar automaticamente ({len(links_encontrados)} link(s) encontrado(s)). Tente novamente.',
        })

    except imaplib.IMAP4.error as e:
        return JsonResponse({'ok': False, 'erro': f'Falha IMAP: {e}'}, status=500)
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': f'Erro: {e}'}, status=500)


# ─────────────────────────────────────────────────────────────────────────────
# Configurações de Backup (admin-only)
# ─────────────────────────────────────────────────────────────────────────────

@login_required(login_url='login')
@admin_required
def backup_config(request):
    """Página de configuração de backup: templates e configuração por acesso."""
    templates = BackupTemplate.objects.all().order_by('fabricante', 'nome')
    acessos = Acesso.objects.select_related('cliente', 'funcao', 'backup_template').order_by(
        'cliente__nome_empresa', 'tipo'
    )
    fabricantes = BackupTemplate.FABRICANTES_CHOICES
    return render(request, 'backup_config.html', {
        'templates': templates,
        'acessos': acessos,
        'fabricantes': fabricantes,
    })


@login_required(login_url='login')
@admin_required
@require_http_methods(['POST'])
def backup_template_salvar(request):
    """Cria ou atualiza um BackupTemplate."""
    try:
        data = json.loads(request.body)
        template_id = data.get('id')
        if template_id:
            t = get_object_or_404(BackupTemplate, id=template_id)
        else:
            t = BackupTemplate()
        t.nome = data.get('nome', '').strip()
        t.fabricante = data.get('fabricante', 'GENERICO')
        t.comandos = data.get('comandos', '').strip()
        t.descricao = data.get('descricao', '').strip()
        t.ativo = bool(data.get('ativo', True))
        t.save()
        return JsonResponse({'ok': True, 'id': t.id, 'nome': str(t)})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required(login_url='login')
@admin_required
@require_http_methods(['POST'])
def backup_template_deletar(request, template_id):
    """Exclui um BackupTemplate (apenas se sem vínculos ativos)."""
    t = get_object_or_404(BackupTemplate, id=template_id)
    em_uso = Acesso.objects.filter(backup_template=t).count()
    if em_uso:
        return JsonResponse({'ok': False, 'erro': f'Template em uso por {em_uso} acesso(s). Desvincule antes de excluir.'}, status=400)
    t.delete()
    return JsonResponse({'ok': True})


@login_required(login_url='login')
@admin_required
@require_http_methods(['POST'])
def backup_acesso_config(request, acesso_id):
    """Atualiza backup_habilitado, backup_template e backup_automatico de um Acesso."""
    try:
        acesso = get_object_or_404(Acesso, id=acesso_id)
        data = json.loads(request.body)
        acesso.backup_habilitado = bool(data.get('backup_habilitado', False))
        acesso.backup_automatico = bool(data.get('backup_automatico', False))
        template_id = data.get('backup_template_id')
        if template_id:
            acesso.backup_template = get_object_or_404(BackupTemplate, id=template_id)
        else:
            acesso.backup_template = None
        acesso.save(update_fields=['backup_habilitado', 'backup_automatico', 'backup_template'])
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT NOC — Configuração e Gerenciamento de Grupos WhatsApp
# ═══════════════════════════════════════════════════════════════════════════════

@login_required(login_url='login')
@admin_required
def agent_config(request):
    """Painel de configuração do Agent NOC (Claude API + OpenAI + Evolution API)."""
    agent_cfg = AgentConfig.get()
    evo_cfg   = EvolutionAPIConfig.get()

    if request.method == 'POST':
        data = json.loads(request.body)
        secao = data.get('secao')

        if secao == 'claude':
            if data.get('claude_api_key'):
                agent_cfg.claude_api_key = data['claude_api_key']
            agent_cfg.claude_model        = data.get('claude_model', agent_cfg.claude_model)
            agent_cfg.claude_max_tokens   = int(data.get('claude_max_tokens', agent_cfg.claude_max_tokens))
            agent_cfg.claude_temperature  = float(data.get('claude_temperature', agent_cfg.claude_temperature))
            agent_cfg.aprovacao_padrao    = bool(data.get('aprovacao_padrao', agent_cfg.aprovacao_padrao))
            agent_cfg.timeout_sessao_wa   = int(data.get('timeout_sessao_wa', agent_cfg.timeout_sessao_wa))
            agent_cfg.prefixo_wa          = data.get('prefixo_wa', agent_cfg.prefixo_wa).strip() or '@noc'
            agent_cfg.max_comandos_sessao = int(data.get('max_comandos_sessao', agent_cfg.max_comandos_sessao))
            agent_cfg.wa_grupo_noc        = data.get('wa_grupo_noc', agent_cfg.wa_grupo_noc)
            agent_cfg.wa_noc_numero       = data.get('wa_noc_numero', agent_cfg.wa_noc_numero)
            agent_cfg.save()
            return JsonResponse({'ok': True, 'msg': 'Configuração Claude salva.'})

        if secao == 'openai':
            if data.get('openai_api_key'):
                agent_cfg.openai_api_key = data['openai_api_key']
            agent_cfg.openai_model       = data.get('openai_model', agent_cfg.openai_model)
            agent_cfg.openai_max_tokens  = int(data.get('openai_max_tokens', agent_cfg.openai_max_tokens))
            agent_cfg.openai_temperature = float(data.get('openai_temperature', agent_cfg.openai_temperature))
            agent_cfg.save()
            return JsonResponse({'ok': True, 'msg': 'Configuração OpenAI salva.'})

        if secao == 'provedor':
            provedor = data.get('provedor_ia', 'claude')
            if provedor not in ('claude', 'openai'):
                return JsonResponse({'ok': False, 'erro': 'Provedor inválido.'}, status=400)
            agent_cfg.provedor_ia = provedor
            agent_cfg.save()
            return JsonResponse({'ok': True, 'msg': f'Provedor alterado para {provedor}.'})

        if secao == 'evolution':
            if data.get('api_key'):
                evo_cfg.api_key = data['api_key']
            if data.get('webhook_secret'):
                evo_cfg.webhook_secret = data['webhook_secret']
            evo_cfg.url           = data.get('url', evo_cfg.url).rstrip('/')
            evo_cfg.instance_name = data.get('instance_name', evo_cfg.instance_name)
            evo_cfg.save()
            return JsonResponse({'ok': True, 'msg': 'Configuração Evolution API salva.'})

        return JsonResponse({'ok': False, 'erro': 'Seção inválida.'}, status=400)

    return render(request, 'agent_config.html', {
        'agent_cfg': agent_cfg,
        'evo_cfg':   evo_cfg,
    })


@login_required(login_url='login')
@admin_required
def agent_token_stats(request):
    """Retorna estatísticas de uso de tokens por provedor e período."""
    from django.utils import timezone
    periodo = request.GET.get('periodo', '7d')

    agora = timezone.now()
    if periodo == '24h':
        desde = agora - timedelta(hours=24)
    elif periodo == '30d':
        desde = agora - timedelta(days=30)
    elif periodo == 'all':
        desde = None
    else:  # 7d default
        desde = agora - timedelta(days=7)

    cfg = AgentConfig.get()
    qs = AgentLog.objects.filter(tipo='agent_msg')
    if desde:
        qs = qs.filter(criado_em__gte=desde)

    totais = qs.aggregate(
        total_input=Sum('tokens_input'),
        total_output=Sum('tokens_output'),
        total_msgs=Count('id'),
    )
    tin  = totais['total_input']  or 0
    tout = totais['total_output'] or 0
    msgs = totais['total_msgs']   or 0

    # Custo estimado (preços aproximados por 1M tokens)
    provedor = cfg.provedor_ia
    if provedor == 'claude':
        modelo = cfg.claude_model
        # Preços claude-sonnet-4-6: $3/$15 por 1M
        if 'opus' in modelo:
            custo_in, custo_out = 15.0, 75.0
        elif 'haiku' in modelo:
            custo_in, custo_out = 0.80, 4.0
        else:  # sonnet
            custo_in, custo_out = 3.0, 15.0
    else:
        modelo = cfg.openai_model
        # Preços gpt-4o: $2.50/$10 por 1M
        if 'mini' in modelo:
            custo_in, custo_out = 0.15, 0.60
        elif 'turbo' in modelo:
            custo_in, custo_out = 10.0, 30.0
        else:  # gpt-4o
            custo_in, custo_out = 2.50, 10.0

    custo_usd = (tin * custo_in + tout * custo_out) / 1_000_000

    # Cotação USD→BRL via AwesomeAPI (fallback para taxa fixa)
    taxa_brl = 5.85
    try:
        import urllib.request, json as _json
        with urllib.request.urlopen('https://economia.awesomeapi.com.br/json/last/USD-BRL', timeout=3) as resp:
            taxa_brl = float(_json.loads(resp.read())['USDBRL']['bid'])
    except Exception:
        pass

    custo_brl = custo_usd * taxa_brl

    # Histórico diário (últimos 14 dias para o gráfico)
    desde_graf = agora - timedelta(days=13)
    from django.db.models.functions import TruncDate
    por_dia = (
        AgentLog.objects
        .filter(tipo='agent_msg', criado_em__gte=desde_graf)
        .annotate(dia=TruncDate('criado_em'))
        .values('dia')
        .annotate(inp=Sum('tokens_input'), out=Sum('tokens_output'), msgs=Count('id'))
        .order_by('dia')
    )

    return JsonResponse({
        'ok': True,
        'periodo': periodo,
        'provedor': provedor,
        'modelo': modelo,
        'tokens_input':  tin,
        'tokens_output': tout,
        'total_tokens':  tin + tout,
        'total_msgs':    msgs,
        'custo_usd':     round(custo_usd, 4),
        'custo_brl':     round(custo_brl, 4),
        'taxa_brl':      round(taxa_brl, 2),
        'por_dia': [
            {'dia': str(r['dia']), 'input': r['inp'] or 0, 'output': r['out'] or 0, 'msgs': r['msgs']}
            for r in por_dia
        ],
    })


@login_required(login_url='login')
@admin_required
@require_http_methods(['POST'])
def agent_testar_claude(request):
    """Testa a conexão com a API Claude enviando uma mensagem simples."""
    try:
        import anthropic
        cfg = AgentConfig.get()
        if not cfg.claude_api_key:
            return JsonResponse({'ok': False, 'erro': 'API Key não configurada.'})
        client = anthropic.Anthropic(api_key=cfg.claude_api_key)
        msg = client.messages.create(
            model=cfg.claude_model,
            max_tokens=50,
            messages=[{'role': 'user', 'content': 'Responda apenas: OK'}],
        )
        resposta = msg.content[0].text if msg.content else '(sem resposta)'
        return JsonResponse({'ok': True, 'resposta': resposta, 'modelo': cfg.claude_model})
    except ImportError:
        return JsonResponse({'ok': False, 'erro': 'Pacote "anthropic" não instalado. Execute: pip install anthropic'})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)})


@login_required(login_url='login')
@admin_required
@require_http_methods(['POST'])
def agent_testar_openai(request):
    """Testa a conexão com a API OpenAI enviando uma mensagem simples."""
    try:
        import openai as openai_lib
        cfg = AgentConfig.get()
        if not cfg.openai_api_key:
            return JsonResponse({'ok': False, 'erro': 'API Key não configurada.'})
        client = openai_lib.OpenAI(api_key=cfg.openai_api_key)
        resp = client.chat.completions.create(
            model=cfg.openai_model or 'gpt-4o',
            max_tokens=50,
            messages=[{'role': 'user', 'content': 'Responda apenas: OK'}],
        )
        resposta = resp.choices[0].message.content if resp.choices else '(sem resposta)'
        return JsonResponse({'ok': True, 'resposta': resposta, 'modelo': cfg.openai_model})
    except ImportError:
        return JsonResponse({'ok': False, 'erro': 'Pacote "openai" não instalado. Execute: pip install openai'})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)})


@login_required(login_url='login')
@admin_required
@require_http_methods(['POST'])
def agent_testar_evolution(request):
    """Testa a conexão com a Evolution API verificando o status da instância."""
    import urllib.request, ssl
    try:
        cfg = EvolutionAPIConfig.get()
        if not cfg.url or not cfg.api_key:
            return JsonResponse({'ok': False, 'erro': 'URL e API Key são obrigatórios.'})

        url = f'{cfg.url}/instance/fetchInstances'
        req = urllib.request.Request(
            url,
            headers={'apikey': cfg.api_key, 'Content-Type': 'application/json'},
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            body = json.loads(resp.read())

        # Localiza a instância configurada
        instancias = body if isinstance(body, list) else body.get('instances', [])
        alvo = next((i for i in instancias if i.get('name') == cfg.instance_name), None)
        if not alvo:
            nomes = [i.get('name') for i in instancias]
            return JsonResponse({'ok': False, 'erro': f'Instância "{cfg.instance_name}" não encontrada. Disponíveis: {nomes}'})

        estado = alvo.get('connectionStatus') or alvo.get('state') or 'desconhecido'
        numero = (alvo.get('number') or alvo.get('ownerJid') or '').split('@')[0]

        conectado = estado.lower() in ('open', 'connected', 'online')
        if conectado:
            evo = EvolutionAPIConfig.get()
            evo.conectado  = True
            evo.numero_wa  = numero
            evo.save(update_fields=['conectado', 'numero_wa'])

        return JsonResponse({'ok': True, 'estado': estado, 'numero': numero, 'conectado': conectado})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)})


@login_required(login_url='login')
@admin_required
@require_http_methods(['POST'])
def agent_sync_grupos(request):
    """
    Consulta a Evolution API e sincroniza os grupos/contatos no banco local.
    Preserva vínculos e configurações existentes.
    """
    import urllib.request, ssl
    try:
        cfg = EvolutionAPIConfig.get()
        if not cfg.url or not cfg.api_key or not cfg.instance_name:
            return JsonResponse({'ok': False, 'erro': 'Evolution API não configurada completamente.'})

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        def evo_get(path):
            req = urllib.request.Request(
                f'{cfg.url}{path}',
                headers={'apikey': cfg.api_key},
            )
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return json.loads(r.read())

        # Busca grupos
        grupos_raw = evo_get(f'/group/fetchAllGroups/{cfg.instance_name}?getParticipants=false')
        if not isinstance(grupos_raw, list):
            grupos_raw = grupos_raw.get('data', []) if isinstance(grupos_raw, dict) else []

        novos = 0
        atualizados = 0
        for g in grupos_raw:
            jid  = g.get('id') or g.get('jid') or ''
            nome = g.get('subject') or g.get('name') or jid
            foto = g.get('pictureUrl') or g.get('picture') or ''
            if not jid:
                continue
            obj, criado = WhatsAppGrupo.objects.get_or_create(jid=jid, defaults={'nome': nome, 'tipo': 'grupo'})
            if criado:
                novos += 1
            else:
                obj.nome    = nome
                obj.tipo    = 'grupo'
                obj.foto_url = foto
                obj.save(update_fields=['nome', 'tipo', 'foto_url', 'sincronizado_em'])
                atualizados += 1

        from django.utils import timezone
        cfg.ultima_sync = timezone.now()
        cfg.save(update_fields=['ultima_sync'])

        total = WhatsAppGrupo.objects.count()
        return JsonResponse({'ok': True, 'novos': novos, 'atualizados': atualizados, 'total': total,
                             'msg': f'{novos} novo(s), {atualizados} atualizado(s). Total: {total} grupos.'})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)})


@login_required(login_url='login')
@admin_required
def agent_grupos(request):
    """Lista e gerencia grupos/contatos WhatsApp vinculados a clientes."""
    grupos   = list(WhatsAppGrupo.objects.select_related('cliente').prefetch_related('hosts_permitidos').order_by('nome'))
    clientes = Cliente.objects.all().order_by('nome_empresa')
    return render(request, 'agent_grupos.html', {
        'grupos': grupos,
        'clientes': clientes,
        'total_vinculados': sum(1 for g in grupos if g.cliente_id),
        'total_ativos':     sum(1 for g in grupos if g.ativo),
        'total_sem':        sum(1 for g in grupos if not g.cliente_id),
    })


@login_required(login_url='login')
@admin_required
@require_http_methods(['POST'])
def agent_grupo_salvar(request, grupo_id):
    """Salva o vínculo de um grupo com um cliente e suas permissões."""
    grupo = get_object_or_404(WhatsAppGrupo, id=grupo_id)
    try:
        data = json.loads(request.body)

        cliente_id    = data.get('cliente_id')
        acesso_global = bool(data.get('acesso_global', False))
        grupo.acesso_global   = acesso_global
        grupo.cliente         = Cliente.objects.get(id=cliente_id) if cliente_id else None
        grupo.nivel_permissao = data.get('nivel_permissao', 'leitura')
        grupo.ativo           = bool(data.get('ativo', True))
        grupo.save(update_fields=['cliente', 'nivel_permissao', 'ativo', 'acesso_global'])

        # Atualiza restrição de hosts (grupos globais não têm restrição de hosts)
        if acesso_global:
            grupo.hosts_permitidos.clear()
        else:
            hosts_ids = data.get('hosts_ids', [])
            if hosts_ids:
                hosts_validos = Acesso.objects.filter(id__in=hosts_ids)
                if grupo.cliente:
                    hosts_validos = hosts_validos.filter(cliente=grupo.cliente)
                grupo.hosts_permitidos.set(hosts_validos)
            else:
                grupo.hosts_permitidos.clear()

        return JsonResponse({'ok': True, 'msg': f'Grupo "{grupo.nome}" salvo com sucesso.'})
    except Cliente.DoesNotExist:
        return JsonResponse({'ok': False, 'erro': 'Cliente não encontrado.'}, status=400)
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required(login_url='login')
@admin_required
def agent_grupo_hosts(request, grupo_id):
    """Retorna os hosts do cliente vinculado ao grupo (para popular o selector no modal)."""
    grupo = get_object_or_404(WhatsAppGrupo, id=grupo_id)
    if not grupo.cliente:
        return JsonResponse({'hosts': [], 'permitidos': []})

    todos = list(Acesso.objects.filter(cliente=grupo.cliente).values('id', 'host', 'porta', 'tipo'))
    permitidos = list(grupo.hosts_permitidos.values_list('id', flat=True))
    return JsonResponse({'hosts': todos, 'permitidos': permitidos})


@login_required(login_url='login')
@admin_required
def agent_grupo_hosts_por_cliente(request):
    """Retorna hosts de um cliente (via ?cliente_id=) para o modal de vínculo."""
    cliente_id = request.GET.get('cliente_id')
    if not cliente_id:
        return JsonResponse({'hosts': []})
    hosts = list(Acesso.objects.filter(cliente_id=cliente_id).values('id', 'host', 'porta', 'tipo'))
    return JsonResponse({'hosts': hosts})


@login_required(login_url='login')
@admin_required
@require_http_methods(['POST'])
def agent_grupo_toggle(request, grupo_id):
    """Ativa/desativa um grupo rapidamente."""
    grupo = get_object_or_404(WhatsAppGrupo, id=grupo_id)
    grupo.ativo = not grupo.ativo
    grupo.save(update_fields=['ativo'])
    return JsonResponse({'ok': True, 'ativo': grupo.ativo})


# ── Base de Conhecimento ──────────────────────────────────────────────────────

@login_required(login_url='login')
@admin_required
def agent_knowledge(request):
    """Lista e gerencia a base de conhecimento do agent."""
    artigos  = AgentKnowledge.objects.select_related('cliente', 'criado_por').order_by('fabricante', 'titulo')
    docs     = AgentKnowledgeDoc.objects.select_related('cliente', 'criado_por').order_by('fabricante', 'titulo')
    clientes = Cliente.objects.all().order_by('nome_empresa')
    return render(request, 'agent_knowledge.html', {
        'artigos':     artigos,
        'docs':        docs,
        'clientes':    clientes,
        'fabricantes': AgentKnowledge.FABRICANTES,
        'categorias':  AgentKnowledge.CATEGORIAS,
    })


@login_required(login_url='login')
@admin_required
def agent_knowledge_dados(request, artigo_id):
    """Retorna JSON com os dados de um artigo para edição."""
    artigo = get_object_or_404(AgentKnowledge, id=artigo_id)
    return JsonResponse({
        'id':          artigo.id,
        'titulo':      artigo.titulo,
        'conteudo':    artigo.conteudo,
        'fabricante':  artigo.fabricante,
        'categoria':   artigo.categoria,
        'tags':        artigo.tags,
        'cliente_id':  artigo.cliente_id,
        'ativo':       artigo.ativo,
    })


@login_required(login_url='login')
@admin_required
def agent_knowledge_salvar(request, artigo_id=None):
    """Cria ou atualiza um artigo da base de conhecimento."""
    artigo = get_object_or_404(AgentKnowledge, id=artigo_id) if artigo_id else None
    try:
        data = json.loads(request.body)
        if not artigo:
            artigo = AgentKnowledge(criado_por=request.user)
        artigo.titulo     = data.get('titulo', '').strip()
        artigo.conteudo   = data.get('conteudo', '').strip()
        artigo.categoria  = data.get('categoria', 'geral')
        artigo.fabricante = data.get('fabricante', 'generico')
        artigo.tags       = data.get('tags', [])
        artigo.ativo      = bool(data.get('ativo', True))
        cliente_id = data.get('cliente_id')
        artigo.cliente = Cliente.objects.get(id=cliente_id) if cliente_id else None
        if not artigo.titulo:
            return JsonResponse({'ok': False, 'erro': 'Título obrigatório.'}, status=400)
        artigo.save()
        return JsonResponse({'ok': True, 'id': artigo.id, 'msg': 'Artigo salvo.'})
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': str(e)}, status=400)


@login_required(login_url='login')
@admin_required
@require_http_methods(['POST'])
def agent_knowledge_deletar(request, artigo_id):
    get_object_or_404(AgentKnowledge, id=artigo_id).delete()
    return JsonResponse({'ok': True})


# ── Knowledge Docs (PDF) ──────────────────────────────────────────────────────

@login_required(login_url='login')
@admin_required
@require_http_methods(['POST'])
def agent_knowledge_doc_upload(request):
    """Recebe upload de PDF, extrai texto com PyMuPDF e salva como AgentKnowledgeDoc."""
    import fitz  # PyMuPDF
    arquivo = request.FILES.get('arquivo')
    if not arquivo:
        return JsonResponse({'ok': False, 'erro': 'Nenhum arquivo enviado.'}, status=400)
    if not arquivo.name.lower().endswith('.pdf'):
        return JsonResponse({'ok': False, 'erro': 'Apenas arquivos PDF são aceitos.'}, status=400)
    if arquivo.size > 30 * 1024 * 1024:  # 30 MB
        return JsonResponse({'ok': False, 'erro': 'Arquivo muito grande (máx 30 MB).'}, status=400)

    titulo     = request.POST.get('titulo', '').strip() or arquivo.name
    fabricante = request.POST.get('fabricante', 'generico')
    categoria  = request.POST.get('categoria', 'geral')
    tags_raw   = request.POST.get('tags', '')
    cliente_id = request.POST.get('cliente_id') or None
    tags = [t.strip() for t in tags_raw.split(',') if t.strip()]

    # Extrair texto do PDF
    try:
        pdf_bytes = arquivo.read()
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
        paginas = doc.page_count
        partes = []
        for page in doc:
            text = page.get_text('text')
            if text.strip():
                partes.append(text)
        doc.close()
        conteudo_extraido = '\n\n'.join(partes)
    except Exception as e:
        return JsonResponse({'ok': False, 'erro': f'Erro ao processar PDF: {e}'}, status=400)

    # Reposicionar arquivo para salvar no storage
    arquivo.seek(0)
    cliente = Cliente.objects.get(id=cliente_id) if cliente_id else None
    obj = AgentKnowledgeDoc(
        titulo=titulo,
        arquivo=arquivo,
        nome_arquivo=arquivo.name,
        tamanho_kb=round(arquivo.size / 1024),
        paginas=paginas,
        conteudo_extraido=conteudo_extraido,
        fabricante=fabricante,
        categoria=categoria,
        tags=tags,
        cliente=cliente,
        criado_por=request.user,
    )
    obj.save()
    return JsonResponse({'ok': True, 'id': obj.id, 'paginas': paginas, 'msg': f'PDF processado: {paginas} páginas, {len(conteudo_extraido)} caracteres extraídos.'})


@login_required(login_url='login')
@admin_required
@require_http_methods(['POST'])
def agent_knowledge_doc_deletar(request, doc_id):
    doc = get_object_or_404(AgentKnowledgeDoc, id=doc_id)
    if doc.arquivo:
        try:
            doc.arquivo.delete(save=False)
        except Exception:
            pass
    doc.delete()
    return JsonResponse({'ok': True})


# ── WhatsApp Webhook ──────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['POST', 'GET'])
def agent_wa_webhook(request):
    """
    Recebe eventos da Evolution API (WhatsApp).
    GET  → validação de URL (retorna 200 OK)
    POST → processar mensagem recebida
    """
    if request.method == 'GET':
        return JsonResponse({'ok': True, 'status': 'webhook ativo'})

    # Validar assinatura HMAC (se configurada)
    try:
        evo_config = EvolutionAPIConfig.get()
        if evo_config.webhook_secret:
            sig = request.headers.get('X-Hub-Signature-256', '')
            if sig:
                esperado = 'sha256=' + hmac.new(
                    evo_config.webhook_secret.encode(),
                    request.body,
                    hashlib.sha256,
                ).hexdigest()
                if not hmac.compare_digest(sig, esperado):
                    return JsonResponse({'ok': False, 'erro': 'Assinatura inválida'}, status=401)
    except Exception:
        pass

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'erro': 'JSON inválido'}, status=400)

    # Processar em background (não bloquear o webhook)
    try:
        _processar_wa_webhook(payload)
    except Exception as exc:
        logger.warning(f"Erro ao enfileirar processamento do webhook WA: {exc}")

    return JsonResponse({'ok': True})


def _transcrever_audio_wa(evo_cfg, wa_data: dict, agent_config) -> str:
    """
    Baixa o áudio da Evolution API e transcreve via OpenAI Whisper.
    Retorna a transcrição ou '' em caso de falha.
    """
    try:
        import openai as openai_lib
        openai_key = (agent_config.openai_api_key or '').strip()
        if not openai_key:
            logger.warning("⚠️ Audio WA: openai_api_key não configurado")
            return ''

        message = wa_data.get('message') or {}
        audio_msg = (
            message.get('audioMessage')
            or message.get('pttMessage')
            or message.get('voiceMessage')
        )
        if not audio_msg:
            return ''

        mimetype = audio_msg.get('mimetype', 'audio/ogg')
        ext = 'ogg'
        if 'mp4' in mimetype or 'mp4a' in mimetype:
            ext = 'mp4'
        elif 'mpeg' in mimetype or 'mp3' in mimetype:
            ext = 'mp3'
        elif 'webm' in mimetype:
            ext = 'webm'

        url_b64 = f"{evo_cfg.url.rstrip('/')}/chat/getBase64FromMediaMessage/{evo_cfg.instance_name}"
        headers = {"apikey": evo_cfg.api_key, "Content-Type": "application/json"}
        body    = {"message": wa_data, "convertToMp4": False}
        logger.warning(f"🎙️ Transcrevendo áudio WA: {url_b64}")
        resp = _requests.post(url_b64, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        resp_data = resp.json()
        b64_data  = resp_data.get('base64') or resp_data.get('data') or ''
        if not b64_data:
            logger.warning(f"⚠️ Audio WA: sem base64 na resposta: {resp_data}")
            return ''

        audio_bytes = base64.b64decode(b64_data)
        client = openai_lib.OpenAI(api_key=openai_key)
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = f'audio.{ext}'
        # Prompt de contexto: nomes próprios e termos de rede ajudam o Whisper
        # a não fragmentar palavras como "Natividade", "Almas", "switch", etc.
        whisper_prompt = (
            "NOC, switch, roteador, interface, porta, VLAN, BGP, OSPF, uplink, "
            "Natividade, Almas, Ponte Alta, SVA, NDD, OLT, ONT, fibra, "
            "ativar, desativar, shutdown, undo shutdown, display, ping, "
            "XGigabitEthernet, GigabitEthernet, Ethernet, loopback, "
            "acesso, cliente, prefixo, IP, rota, tráfego."
        )
        transcript = client.audio.transcriptions.create(
            model='whisper-1', file=audio_file, language='pt',
            prompt=whisper_prompt,
        )
        texto = (transcript.text or '').strip()
        logger.warning(f"🎙️ Transcrição: {texto!r}")
        return texto
    except Exception as exc:
        logger.warning(f"⚠️ Erro ao transcrever áudio WA: {exc}")
        return ''


# Apelidos que o Whisper usa para "noc" / "agente noc"
_NOC_ALIASES = [
    '@noc', 'noc', 'nokia', 'noque', 'nok', 'knock', 'nóc', 'nc',
    'agente', 'agente noc', 'gente',
    'arroba noc', 'at noc', 'a noc', 'você noc', 'voce noc',
    '@tomichinho', 'tomichinho',
]


def _processar_wa_webhook(payload: dict):
    """
    Processa o payload da Evolution API.
    Suporta os eventos: MESSAGES_UPSERT / messages.upsert
    """
    import threading

    event = payload.get('event') or payload.get('type') or ''
    logger.warning(f"📨 Webhook WA: event={event!r}")
    if 'message' not in event.lower():
        return  # Só nos interessa mensagens

    data = payload.get('data') or {}
    key  = data.get('key') or {}

    # Ignorar mensagens enviadas por nós mesmos
    if key.get('fromMe'):
        return

    jid_from   = key.get('remoteJid', '')
    is_grupo   = '@g.us' in jid_from
    sender_jid = data.get('participant') or jid_from  # remetente em grupos

    # Extrair texto
    message    = data.get('message') or {}
    texto      = (
        message.get('conversation')
        or (message.get('extendedTextMessage') or {}).get('text')
        or ''
    ).strip()

    is_audio = bool(
        message.get('audioMessage')
        or message.get('pttMessage')
        or message.get('voiceMessage')
    )

    config  = AgentConfig.get()
    prefixo = (config.prefixo_wa or '@noc').lower()
    logger.warning(f"📨 WA msg: jid={jid_from!r} | texto={texto[:60]!r} | is_audio={is_audio} | prefixo={prefixo!r}")

    if is_audio:
        # Verificar grupo vinculado antes de transcrever (evita trabalho desnecessário)
        jid_lookup = jid_from if is_grupo else sender_jid
        try:
            grupo = WhatsAppGrupo.objects.select_related('cliente').get(
                jid=jid_lookup, ativo=True
            )
        except WhatsAppGrupo.DoesNotExist:
            logger.debug(f"Webhook WA (áudio): grupo {jid_lookup} não vinculado — ignorando")
            return
        if not grupo.cliente and not grupo.acesso_global:
            return

        # Transcrever
        try:
            evo_cfg = EvolutionAPIConfig.get()
        except Exception:
            evo_cfg = None
        if not evo_cfg:
            logger.warning("⚠️ Audio WA: sem EvolutionAPIConfig configurado")
            return

        transcricao = _transcrever_audio_wa(evo_cfg, data, config)
        if not transcricao:
            return

        # Checar prefixo na transcrição (suporta apelidos do Whisper)
        trans_lower = transcricao.lower().strip()
        # Remove interjeições iniciais comuns antes do prefixo
        trans_lower = _re.sub(r'^(ah|oi|ei|olá|ola|e aí)[,\.\s]+', '', trans_lower)
        mensagem_agent = None
        for alias in _NOC_ALIASES:
            if trans_lower.startswith(alias):
                mensagem_agent = transcricao[len(alias):].strip()
                # Remove pontuação inicial residual
                mensagem_agent = _re.sub(r'^[,\.\s]+', '', mensagem_agent)
                break
        if mensagem_agent is None:
            logger.warning(f"🎙️ Áudio transcrito sem prefixo NOC: {transcricao!r}")
            return
        if not mensagem_agent:
            mensagem_agent = "Olá! Como posso ajudar?"

    else:
        if not texto:
            return

        # Prefixos válidos: @noc (configurado) + @<número do bot> (menção WA) + apelidos fixos
        texto_lower = texto.lower()
        numero_bot  = (config.wa_noc_numero or '').strip().lstrip('+').replace(' ', '')
        prefixos_validos = [prefixo, '@tomichinho', 'tomichinho']
        if numero_bot:
            prefixos_validos.append(f'@{numero_bot}')

        match_prefixo = None
        for p in prefixos_validos:
            if texto_lower.startswith(p):
                match_prefixo = p
                break

        # Fallback: menção via contextInfo (WhatsApp nativo envia JID em mentionedJid)
        if match_prefixo is None and numero_bot:
            ctx = (
                (message.get('extendedTextMessage') or {}).get('contextInfo')
                or message.get('contextInfo')
                or {}
            )
            mentioned = ctx.get('mentionedJid') or []
            if f'{numero_bot}@s.whatsapp.net' in mentioned:
                # Remove a menção do início do texto se presente
                match_prefixo = next(
                    (p for p in prefixos_validos if texto_lower.startswith(p)), ''
                )

        if match_prefixo is None:
            return  # Mensagem não direcionada ao agent

        logger.warning(f"📨 WA prefixo match={match_prefixo!r}")
        mensagem_agent = texto[len(match_prefixo):].strip()
        if not mensagem_agent:
            mensagem_agent = "Olá! Como posso ajudar?"

        # Verificar grupo vinculado
        jid_lookup = jid_from if is_grupo else sender_jid
        try:
            grupo = WhatsAppGrupo.objects.select_related('cliente').get(
                jid=jid_lookup, ativo=True
            )
        except WhatsAppGrupo.DoesNotExist:
            logger.debug(f"Webhook WA: grupo {jid_lookup} não vinculado — ignorando")
            return
        if not grupo.cliente and not grupo.acesso_global:
            return

    # Executar em thread separada para não bloquear
    t = threading.Thread(
        target=_processar_wa_mensagem_sync,
        args=(grupo, sender_jid, mensagem_agent),
        daemon=True,
    )
    t.start()


def _processar_wa_mensagem_sync(grupo: 'WhatsAppGrupo', sender_jid: str, mensagem: str):
    """
    Executa o processamento da mensagem WA em uma thread síncrona com loop asyncio próprio.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_processar_wa_mensagem_async(grupo, sender_jid, mensagem))
    except Exception as exc:
        import traceback
        err = traceback.format_exc()
        logger.warning(f"Erro no processamento WA: {exc}\n{err}")
        # fallback: gravar em arquivo para não perder
        try:
            with open('/tmp/wa_agent_errors.log', 'a') as _f:
                _f.write(f"[{__import__('datetime').datetime.now()}] {err}\n")
        except Exception:
            pass
    finally:
        try:
            loop.close()
        except Exception:
            pass


async def _processar_wa_mensagem_async(grupo, sender_jid: str, mensagem: str):
    """
    Lógica assíncrona: cria/recupera sessão, processa com engine, envia resposta.
    """
    from asgiref.sync import sync_to_async
    from django.utils import timezone
    from datetime import timedelta
    from home.agent_engine import AgentNOCEngine, _evolution_send

    # Buscar ou criar sessão WhatsApp
    config = await sync_to_async(AgentConfig.get)()
    timeout_min = config.timeout_sessao_wa or 120
    desde = timezone.now() - timedelta(minutes=timeout_min)

    if grupo.acesso_global:
        # Grupo global: sessão ancorада no grupo (cliente=None)
        sessao = await sync_to_async(
            AgentSessao.objects.filter(
                canal='whatsapp',
                canal_id=grupo.jid,
                wa_grupo=grupo,
                status='ativa',
                ultima_atividade__gte=desde,
            ).order_by('-ultima_atividade').first
        )()
    else:
        sessao = await sync_to_async(
            AgentSessao.objects.filter(
                canal='whatsapp',
                canal_id=grupo.jid,
                cliente=grupo.cliente,
                status='ativa',
                ultima_atividade__gte=desde,
            ).order_by('-ultima_atividade').first
        )()

    if not sessao:
        # Encerrar sessões ativas deste JID que não sejam deste grupo/cliente
        await sync_to_async(
            AgentSessao.objects.filter(
                canal='whatsapp',
                canal_id=grupo.jid,
                status='ativa',
            ).exclude(wa_grupo=grupo).update
        )(status='encerrada')
        sessao = await sync_to_async(AgentSessao.objects.create)(
            canal='whatsapp',
            canal_id=grupo.jid,
            cliente=grupo.cliente,   # None para grupos globais (nullable)
            wa_grupo=grupo,
            status='ativa',
        )

    # Callback de aprovação: nível define o que é permitido automaticamente
    async def aprovacao_wa(acesso_id, comando):
        from home.agent_engine import _is_safe_command, _is_operational_command, BLOCKED_COMMANDS
        from clientes.models import Acesso
        import re as _re
        nivel = grupo.nivel_permissao
        # Descobrir fabricante real do host
        fabricante = 'generico'
        try:
            acesso_obj = await sync_to_async(
                Acesso.objects.select_related('modelo').get
            )(id=acesso_id)
            if acesso_obj.modelo:
                fabricante = getattr(acesso_obj.modelo, 'fabricante', 'generico') or 'generico'
        except Exception:
            pass
        cmd_lower = comando.strip().lower()
        # Sempre bloquear comandos destrutivos
        for pattern in BLOCKED_COMMANDS:
            if _re.search(pattern, cmd_lower):
                return False
        if nivel == 'leitura':
            return _is_safe_command(comando, fabricante)
        elif nivel == 'operacional':
            return _is_operational_command(comando, fabricante)
        elif nivel == 'admin':
            return True  # Tudo exceto BLOCKED_COMMANDS (verificado acima)
        return False

    respostas_wa = []

    async def notify_wa(msg):
        if msg.get('type') == 'agent_message':
            respostas_wa.append(msg.get('content', ''))

    engine = AgentNOCEngine(
        sessao_id=sessao.id,
        canal='whatsapp',
        aprovacao_cb=aprovacao_wa,
        notify_cb=notify_wa,
    )

    # Processar mensagem
    resposta = await engine.processar_mensagem(mensagem)

    # Enviar resposta via Evolution API
    try:
        evo_config = await sync_to_async(EvolutionAPIConfig.get)()
        texto_resp = resposta or (respostas_wa[-1] if respostas_wa else '')
        logger.warning(f"[WA-SEND] jid={grupo.jid!r} texto_len={len(texto_resp) if texto_resp else 0} has_config={bool(evo_config.url and evo_config.api_key)}")
        if evo_config.url and evo_config.api_key:
            if texto_resp:
                result = await _evolution_send(evo_config, grupo.jid, texto_resp)
                logger.warning(f"[WA-SEND] OK status={result.get('status','?') if isinstance(result,dict) else result}")
            else:
                logger.warning("[WA-SEND] texto_resp vazio")
    except Exception as exc:
        import traceback
        logger.warning(f"[WA-SEND] ERRO: {exc}\n{traceback.format_exc()}")
