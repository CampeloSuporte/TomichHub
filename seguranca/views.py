"""
Painel de Segurança (menu Sistema → Segurança).

Quem vê o quê:

* **Administrador** — tudo: tentativas de login de qualquer conta, bloqueios,
  banimentos de SSH (fail2ban) e eventos de injeção. É o dono do servidor.
* **Consultor** — só o que é da própria instância: tentativas e bloqueios dos
  usuários que ele já gerencia (`perms.usuarios_gerenciaveis_por`), pra
  destravar operador e cliente dele sem depender do Administrador (ver
  docs/PERMISSOES_CONSULTOR.md). Fail2ban e eventos de injeção são do
  servidor inteiro, não de uma instância — ficam fora.
* **Operador / portal do cliente** — sem acesso.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from usuario import perms

from . import fail2ban, services
from .models import AcaoSeguranca, BloqueioLogin, EventoSeguranca, TentativaLogin

LIMITE_TENTATIVAS = 300


def pode_ver_seguranca(user):
    return perms.is_admin(user) or perms.is_consultor(user)


def seguranca_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not pode_ver_seguranca(request.user):
            messages.error(request, 'Você não possui permissão para acessar o painel de segurança.')
            return redirect('login')
        return view_func(request, *args, **kwargs)
    return wrapper


def _usernames_do_escopo(user):
    """Usernames que um Consultor pode enxergar. `None` = sem limite (Admin)."""
    if perms.is_admin(user):
        return None
    return set(
        perms.usuarios_gerenciaveis_por(user).values_list('username', flat=True)
    )


def _filtrar_por_escopo(qs, usernames, campo='username'):
    if usernames is None:
        return qs
    if not usernames:
        return qs.none()
    return qs.filter(**{f'{campo}__in': usernames})


@login_required(login_url='login')
@seguranca_required
def dashboard(request):
    agora = timezone.now()
    ultimas_24h = agora - timezone.timedelta(hours=24)
    ultimos_7d = agora - timezone.timedelta(days=7)

    is_admin = perms.is_admin(request.user)
    usernames = _usernames_do_escopo(request.user)

    # ── Tentativas de login (com filtros da barra superior) ───────────────
    tentativas = TentativaLogin.objects.all()
    tentativas = _filtrar_por_escopo(tentativas, usernames)

    f_usuario = (request.GET.get('usuario') or '').strip()
    f_ip = (request.GET.get('ip') or '').strip()
    f_resultado = (request.GET.get('resultado') or '').strip()
    f_periodo = (request.GET.get('periodo') or '24h').strip()

    inicio = {'24h': ultimas_24h, '7d': ultimos_7d,
              '30d': agora - timezone.timedelta(days=30)}.get(f_periodo)
    if inicio:
        tentativas = tentativas.filter(criado_em__gte=inicio)
    if f_usuario:
        tentativas = tentativas.filter(username__icontains=f_usuario)
    if f_ip:
        tentativas = tentativas.filter(ip__icontains=f_ip)
    if f_resultado == 'falha':
        tentativas = tentativas.filter(sucesso=False)
    elif f_resultado == 'sucesso':
        tentativas = tentativas.filter(sucesso=True)

    tentativas = list(tentativas.select_related('usuario')[:LIMITE_TENTATIVAS])

    # ── Bloqueios ────────────────────────────────────────────────────────
    bloqueios_qs = BloqueioLogin.objects.select_related('usuario', 'desbloqueado_por')
    if usernames is not None:
        # Consultor só mexe em conta dele. Bloqueio por IP é decisão de
        # servidor (afeta todo mundo que sai por aquele IP) — só Admin.
        bloqueios_qs = bloqueios_qs.filter(tipo=BloqueioLogin.TIPO_CONTA)
        bloqueios_qs = _filtrar_por_escopo(bloqueios_qs, usernames, campo='chave')

    bloqueios_ativos = list(bloqueios_qs.filter(bloqueado_ate__gt=agora).order_by('-bloqueado_ate'))
    bloqueios_recentes = list(
        bloqueios_qs.filter(Q(bloqueado_ate__lte=agora) | Q(bloqueado_ate__isnull=True))
                    .filter(ultima_falha_em__gte=ultimos_7d)
                    .order_by('-ultima_falha_em')[:50]
    )

    # ── Cartões do topo ──────────────────────────────────────────────────
    base_24h = _filtrar_por_escopo(TentativaLogin.objects.filter(criado_em__gte=ultimas_24h), usernames)
    total_24h = base_24h.count()
    falhas_24h = base_24h.filter(sucesso=False).count()

    contexto = {
        'is_admin_seguranca': is_admin,
        'agora': agora,
        'tentativas': tentativas,
        'limite_tentativas': LIMITE_TENTATIVAS,
        'bloqueios_ativos': bloqueios_ativos,
        'bloqueios_recentes': bloqueios_recentes,
        'total_24h': total_24h,
        'falhas_24h': falhas_24h,
        'sucessos_24h': total_24h - falhas_24h,
        'qtd_bloqueios_ativos': len(bloqueios_ativos),
        'filtros': {'usuario': f_usuario, 'ip': f_ip, 'resultado': f_resultado, 'periodo': f_periodo},
        'config': {
            'max_tentativas': services.max_tentativas(),
            'minutos': services.minutos_bloqueio(),
            'max_tentativas_ip': services.max_tentativas_ip(),
            'minutos_ip': services.minutos_bloqueio_ip(),
            'janela': services.janela_minutos(),
        },
    }

    # ── Blocos exclusivos do Administrador ───────────────────────────────
    if is_admin:
        top_ips = list(
            TentativaLogin.objects.filter(criado_em__gte=ultimos_7d, sucesso=False)
            .exclude(ip__isnull=True)
            .values('ip').annotate(total=Count('id')).order_by('-total')[:10]
        )
        jails = fail2ban.resumo()
        banidos = fail2ban.banidos_por_ip()
        contexto.update({
            'top_ips': top_ips,
            'jails': jails,
            'fail2ban_ok': bool(jails),
            'fail2ban_erro': '' if jails else fail2ban.diagnostico(),
            'banidos': sorted(
                ({'ip': ip, 'jails': js} for ip, js in banidos.items()),
                key=lambda b: b['ip'],
            ),
            'total_banidos': len(banidos),
            'historico_fail2ban': fail2ban.historico(limite=100),
            'eventos': list(
                EventoSeguranca.objects.select_related('usuario')
                .filter(criado_em__gte=ultimos_7d)[:200]
            ),
            'eventos_24h': EventoSeguranca.objects.filter(criado_em__gte=ultimas_24h).count(),
            'acoes': list(AcaoSeguranca.objects.select_related('usuario')[:50]),
        })
    else:
        contexto.update({
            'top_ips': [], 'jails': [], 'fail2ban_ok': False, 'fail2ban_erro': '',
            'banidos': [], 'total_banidos': 0, 'historico_fail2ban': [],
            'eventos': [], 'eventos_24h': 0, 'acoes': [],
        })

    return render(request, 'seguranca/dashboard.html', contexto)


def _pode_mexer_no_bloqueio(user, bloqueio):
    if perms.is_admin(user):
        return True
    if bloqueio.tipo != BloqueioLogin.TIPO_CONTA:
        return False
    return perms.usuarios_gerenciaveis_por(user).filter(username=bloqueio.chave).exists()


@login_required(login_url='login')
@seguranca_required
def desbloquear(request):
    """Libera uma conta (ou IP) trancado pelo contador de força bruta."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'erro': 'Método não permitido.'}, status=405)

    bloqueio = BloqueioLogin.objects.filter(id=request.POST.get('id')).first()
    if not bloqueio:
        return JsonResponse({'ok': False, 'erro': 'Bloqueio não encontrado.'}, status=404)
    if not _pode_mexer_no_bloqueio(request.user, bloqueio):
        return JsonResponse({'ok': False, 'erro': 'Sem permissão para desbloquear este registro.'}, status=403)

    services.desbloquear(bloqueio, por_usuario=request.user, request=request)
    return JsonResponse({'ok': True, 'mensagem': f'{bloqueio.chave} liberado.'})


@login_required(login_url='login')
@seguranca_required
def desbloquear_todos(request):
    """Botão 'liberar todos' — usado quando um ataque em massa trancou meia
    empresa e destravar um por um não é viável."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'erro': 'Método não permitido.'}, status=405)

    qs = BloqueioLogin.objects.filter(bloqueado_ate__gt=timezone.now())
    if not perms.is_admin(request.user):
        usernames = _usernames_do_escopo(request.user)
        qs = _filtrar_por_escopo(qs.filter(tipo=BloqueioLogin.TIPO_CONTA), usernames, campo='chave')

    total = 0
    for bloqueio in qs:
        services.desbloquear(bloqueio, por_usuario=request.user, request=request)
        total += 1
    return JsonResponse({'ok': True, 'mensagem': f'{total} bloqueio(s) liberado(s).', 'total': total})


@login_required(login_url='login')
def fail2ban_desbanir(request):
    """Tira o IP da blacklist do fail2ban (libera no firewall)."""
    if not request.user.is_authenticated or not perms.is_admin(request.user):
        return JsonResponse({'ok': False, 'erro': 'Acesso restrito ao Administrador.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'erro': 'Método não permitido.'}, status=405)

    ip = (request.POST.get('ip') or '').strip()
    jail = (request.POST.get('jail') or '').strip() or None
    if not services.ip_valido(ip):
        return JsonResponse({'ok': False, 'erro': 'IP inválido.'}, status=400)

    ok, saida = fail2ban.desbanir(ip, jail)
    if not ok:
        return JsonResponse({'ok': False, 'erro': saida}, status=502)

    services.registrar_acao(
        AcaoSeguranca.ACAO_UNBAN_SSH, ip, usuario=request.user,
        detalhe=f'jail={jail or "todas"}', request=request,
    )
    return JsonResponse({'ok': True, 'mensagem': f'{ip} removido da blacklist.'})


@login_required(login_url='login')
def fail2ban_banir(request):
    """Banimento manual — o operador viu um IP hostil no painel e quer cortar
    antes de o fail2ban chegar no limite dele."""
    if not request.user.is_authenticated or not perms.is_admin(request.user):
        return JsonResponse({'ok': False, 'erro': 'Acesso restrito ao Administrador.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'erro': 'Método não permitido.'}, status=405)

    ip = (request.POST.get('ip') or '').strip()
    jail = (request.POST.get('jail') or fail2ban.JAIL_SSH).strip()
    if not services.ip_valido(ip):
        return JsonResponse({'ok': False, 'erro': 'IP inválido.'}, status=400)

    ok, saida = fail2ban.banir(ip, jail)
    if not ok:
        return JsonResponse({'ok': False, 'erro': saida}, status=502)

    services.registrar_acao(
        AcaoSeguranca.ACAO_BAN_SSH, ip, usuario=request.user,
        detalhe=f'jail={jail}', request=request,
    )
    return JsonResponse({'ok': True, 'mensagem': f'{ip} banido em {jail}.'})
