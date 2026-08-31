import json
import requests
from urllib.parse import urlencode
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import JsonResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from  django.contrib.auth import authenticate,login as auth_login
from django.contrib.auth.decorators import login_required
from clientes.decorators import admin_required  # ← ADICIONAR ESTA LINHA
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from clientes.models import Cliente
from .models import UsuarioModulo, modulos_habilitados_dict, Instancia, PerfilUsuario, InstanciaFerramenta, ferramentas_habilitadas_dict, TOTPDevice, PortalUsuarioInstancia, UsuarioAcesso, UsuarioFuncao
from . import perms
from . import totp as totp_lib
from seguranca import services as seguranca
from seguranca.models import TentativaLogin

TURNSTILE_VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'


def _verificar_turnstile(request):
    """Valida o token do Cloudflare Turnstile enviado pelo form de login."""
    token = request.POST.get('cf-turnstile-response')
    if not token:
        return False
    try:
        resp = requests.post(TURNSTILE_VERIFY_URL, data={
            'secret': settings.TURNSTILE_SECRET_KEY,
            'response': token,
            'remoteip': request.META.get('REMOTE_ADDR'),
        }, timeout=5)
        return resp.json().get('success', False)
    except requests.RequestException:
        return False


def _mensagem_bloqueio(bloqueio):
    """Texto mostrado a quem esbarrou no bloqueio por força bruta. Fala o
    tempo que falta em vez de 'tente mais tarde': sem o número, o usuário
    legítimo fica recarregando a tela e ligando pro suporte."""
    segundos = bloqueio.segundos_restantes
    minutos = max(1, -(-segundos // 60))  # arredonda pra cima
    if bloqueio.tipo == 'ip':
        return (f'Muitas tentativas de acesso a partir do seu endereço. '
                f'Tente novamente em {minutos} minuto(s).')
    return (f'Conta temporariamente bloqueada por excesso de tentativas de senha. '
            f'Tente novamente em {minutos} minuto(s) ou peça o desbloqueio ao administrador.')


def _tela_login(request, next_url='', erro=None):
    if erro:
        messages.error(request, erro)
    return render(request, 'login.html', {
        'turnstile_site_key': settings.TURNSTILE_SITE_KEY,
        'next': next_url,
    })


def _is_staff_para_role(role):
    """Admin, Consultor e Operador são back-office (`perms.is_backoffice`) e
    precisam de `is_staff=True` — é o que os módulos internos (ex.:
    atendimento, via `staff_required`) checam pra liberar acesso. Só
    'cliente' (portal) fica de fora. Antes só Admin ganhava `is_staff`, o
    que trancava todo Consultor/Operador pra fora do atendimento (e sumia
    da lista de "atendentes" pra transferir chamado) desde a criação do
    login — bug sistêmico, achado ao investigar um operador específico que
    não aparecia como atendente."""
    return role in (PerfilUsuario.ROLE_ADMIN, PerfilUsuario.ROLE_CONSULTOR, PerfilUsuario.ROLE_OPERADOR)


def _sincronizar_modulos_usuario(request, usuario):
    """
    Grava UsuarioModulo a partir dos checkboxes 'modulos' do POST — só se o
    form realmente enviou a seção (marcador oculto 'modulos_form_present'),
    pra nunca desabilitar tudo por um form incompleto (checkbox desmarcado
    não aparece no POST, então "nada marcado" é indistinguível de "campo
    ausente" sem esse marcador).
    """
    if not request.POST.get('modulos_form_present'):
        return
    modulos_marcados = set(request.POST.getlist('modulos'))
    for chave, _ in UsuarioModulo.MODULO_CHOICES:
        UsuarioModulo.objects.update_or_create(
            usuario=usuario, modulo=chave,
            defaults={'habilitado': chave in modulos_marcados},
        )


def _hosts_do_usuario(usuario):
    """O que o modal de edição precisa pra montar a seção "Hosts liberados":
    as funções e os hosts do cliente vinculado, já marcando o que vale hoje.

    Retorna `None` quando o login ainda não está vinculado a nenhum Cliente
    (a seção vira um aviso pedindo pra fazer o vínculo primeiro) — o vínculo
    é feito na tela do Cliente, não aqui.

    `modo` resume o estado gravado, e é o que o rádio da tela pré-seleciona:

    - `'todos'`  — sem registro nenhum: vê todo host do cliente, inclusive os
      cadastrados depois. É o estado de quem nunca passou por essa tela.
    - `'funcao'` — há `UsuarioFuncao`: vê os hosts das funções marcadas, e
      acompanha host novo dessas funções.
    - `'host'`   — há só `UsuarioAcesso`: vê exatamente os hosts marcados.
    """
    cliente = Cliente.objects.filter_by_usuario_vinculado(usuario).first()
    if cliente is None:
        return None

    permitidos = set(
        UsuarioAcesso.objects.filter(usuario=usuario).values_list('acesso_id', flat=True)
    )
    funcoes_ok = set(
        UsuarioFuncao.objects.filter(usuario=usuario).values_list('funcao_id', flat=True)
    )
    if funcoes_ok:
        modo = 'funcao'
    elif permitidos:
        modo = 'host'
    else:
        modo = 'todos'

    acessos = list(cliente.acessos.select_related('funcao').order_by('tipo', 'host'))
    hosts = [
        {
            'id': a.id,
            'tipo': a.tipo,
            'host': a.host,
            'protocolo': a.protocolo or '',
            'funcao': a.funcao.descricao if a.funcao else '',
            'funcao_id': a.funcao_id,
            'marcado': (modo != 'host') or (a.id in permitidos),
        }
        for a in acessos
    ]

    # Só as funções que esse cliente realmente usa — a lista global de
    # Funcao_equipamento é da plataforma inteira e encheria a tela de opção
    # que não casa com host nenhum dele.
    funcoes = {}
    sem_funcao = 0
    for a in acessos:
        if a.funcao_id is None:
            sem_funcao += 1
            continue
        item = funcoes.setdefault(a.funcao_id, {
            'id': a.funcao_id,
            'descricao': a.funcao.descricao,
            'hosts': 0,
            'marcado': a.funcao_id in funcoes_ok,
        })
        item['hosts'] += 1

    return {
        'cliente_id': cliente.id,
        'cliente_nome': cliente.nome_empresa,
        'modo': modo,
        'hosts': hosts,
        'funcoes': sorted(funcoes.values(), key=lambda f: (f['descricao'] or '').lower()),
        'hosts_sem_funcao': sem_funcao,
    }


def _sincronizar_acessos_usuario(request, usuario):
    """Grava o recorte de hosts do login a partir do POST do modal.

    Sem o marcador 'acessos_form_present' o form não mexe em nada (um POST
    antigo, ou o modal de cadastro, não podem apagar a seleção de ninguém) —
    mesmo padrão do `_sincronizar_modulos_usuario`.

    O rádio 'acessos_modo' decide o que é gravado, e os três modos são
    exclusivos entre si na tela:

    - `todos`  → apaga `UsuarioAcesso` e `UsuarioFuncao`. "Sem restrição" é a
      ausência de registro; é o que faz host novo do cliente já nascer
      visível pra esse login.
    - `funcao` → grava `UsuarioFuncao` com as funções marcadas (e limpa a
      lista host a host). É regra, não retrato: host novo de uma função
      liberada entra sozinho.
    - `host`   → grava `UsuarioAcesso` com os hosts marcados (e limpa as
      funções). Marcar todos equivale a `todos`, e é gravado como tal.

    Em `funcao` e `host`, **nada marcado não é gravado**: "zero host" é
    indistinguível de "sem restrição" nessas tabelas, e gravar liberaria tudo
    — o oposto do que o clique quis dizer. Quem quer tirar hosts do login
    desmarca a ferramenta "Acessos" logo acima.

    Só entram hosts e funções **do cliente vinculado** ao usuário: id vindo
    de POST manipulado que não seja dele é descartado.
    """
    if not request.POST.get('acessos_form_present'):
        return
    cliente = Cliente.objects.filter_by_usuario_vinculado(usuario).first()
    if cliente is None:
        return

    modo = request.POST.get('acessos_modo') or 'todos'
    if modo not in ('todos', 'funcao', 'host'):
        modo = 'todos'

    if modo == 'todos':
        UsuarioAcesso.objects.filter(usuario=usuario).delete()
        UsuarioFuncao.objects.filter(usuario=usuario).delete()
        return

    if modo == 'funcao':
        ids_funcoes = set(
            cliente.acessos.exclude(funcao__isnull=True).values_list('funcao_id', flat=True)
        )
        marcadas = {int(v) for v in request.POST.getlist('funcoes_acesso') if str(v).isdigit()} & ids_funcoes
        if not marcadas:
            messages.warning(
                request,
                'Nenhuma função marcada — o acesso aos hosts foi mantido como estava. '
                'Para o login não ver host nenhum, desmarque a ferramenta "Acessos".'
            )
            return
        UsuarioAcesso.objects.filter(usuario=usuario).delete()
        UsuarioFuncao.objects.filter(usuario=usuario).delete()
        UsuarioFuncao.objects.bulk_create(
            [UsuarioFuncao(usuario=usuario, funcao_id=fid) for fid in marcadas]
        )
        return

    ids_cliente = set(cliente.acessos.values_list('id', flat=True))
    marcados = {int(v) for v in request.POST.getlist('acessos') if str(v).isdigit()} & ids_cliente

    if ids_cliente and not marcados:
        messages.warning(
            request,
            'Nenhum host marcado — o acesso aos hosts foi mantido como estava. '
            'Para o login não ver host nenhum, desmarque a ferramenta "Acessos".'
        )
        return

    UsuarioAcesso.objects.filter(usuario=usuario).delete()
    UsuarioFuncao.objects.filter(usuario=usuario).delete()
    if marcados and marcados != ids_cliente:
        UsuarioAcesso.objects.bulk_create(
            [UsuarioAcesso(usuario=usuario, acesso_id=aid) for aid in marcados]
        )


def _sincronizar_ferramentas_instancia(request, instancia):
    """Grava InstanciaFerramenta a partir dos checkboxes 'ferramentas' do
    POST — mesmo padrão de `_sincronizar_modulos_usuario`, mas por
    instância (Consultor) em vez de por login. Só o Administrador edita
    isso (`pode_gerenciar_ferramentas_instancia`)."""
    if not request.POST.get('ferramentas_form_present'):
        return
    if not perms.pode_gerenciar_ferramentas_instancia(request.user):
        return
    ferramentas_marcadas = set(request.POST.getlist('ferramentas'))
    for chave, _ in InstanciaFerramenta.FERRAMENTA_CHOICES:
        InstanciaFerramenta.objects.update_or_create(
            instancia=instancia, ferramenta=chave,
            defaults={'habilitado': chave in ferramentas_marcadas},
        )


_TIPO_LABELS = {
    PerfilUsuario.ROLE_ADMIN: 'Administrador',
    PerfilUsuario.ROLE_CONSULTOR: 'Consultor',
    PerfilUsuario.ROLE_OPERADOR: 'Operador',
    'cliente': 'Cliente',
}


def _validar_role_permitida(request, role):
    """Um Consultor só pode conceder role 'operador' ou 'cliente' — nunca
    'admin'/'consultor'. Mesmo que o POST seja manipulado diretamente."""
    if role in (PerfilUsuario.ROLE_ADMIN, PerfilUsuario.ROLE_CONSULTOR) and not perms.is_admin(request.user):
        return False
    if role == PerfilUsuario.ROLE_OPERADOR and not perms.pode_gerenciar_usuarios(request.user):
        return False
    return True


def _resolver_instancia_operador(request):
    """Instância de um Operador sendo criado/editado: fixa (a do próprio
    Consultor) quando quem está operando é Consultor; escolhida via POST
    quando é o Administrador."""
    if perms.is_consultor(request.user):
        return perms.get_instancia(request.user)
    instancia_id = request.POST.get('instancia') or None
    return Instancia.objects.filter(id=instancia_id).first() if instancia_id else None


@login_required(login_url='login')
@perms.pode_gerenciar_usuarios_required
def cadastrar_usuario(request):
        if request.method == 'GET':
            usuario = perms.usuarios_gerenciaveis_por(request.user).select_related('perfil', 'perfil__instancia', 'totp_device')
            for u in usuario:
                u.modulos_json = json.dumps(modulos_habilitados_dict(u))
                perfil = getattr(u, 'perfil', None)
                u.role_efetivo = perfil.role if perfil else 'cliente'
                u.instancia_id_efetivo = perfil.instancia_id if perfil else None
                ferramentas = ferramentas_habilitadas_dict(perfil.instancia) if (perfil and perfil.role == PerfilUsuario.ROLE_CONSULTOR) else {}
                u.ferramentas_json = json.dumps(ferramentas)
                u.tem_2fa = getattr(u, 'totp_device', None) is not None and u.totp_device.confirmado
            return render(request, 'cadastrar_usuario.html', {
                'usuario': usuario,
                'modulos_disponiveis': UsuarioModulo.MODULO_CHOICES,
                'ferramentas_disponiveis': InstanciaFerramenta.FERRAMENTA_CHOICES,
                'instancias': Instancia.objects.filter(ativo=True).order_by('nome') if perms.is_admin(request.user) else Instancia.objects.none(),
                'pode_criar_consultor': perms.is_admin(request.user),
            })
        else:
            username = request.POST.get('username')
            email = request.POST.get('email')
            password = request.POST.get('password')
            role = request.POST.get('role') or 'cliente'

            if not _validar_role_permitida(request, role):
                messages.error(request, 'Você não possui permissão para criar esse tipo de usuário.')
                return redirect('cadastrar_usuario')

            if User.objects.filter(username=username).exists():
                messages.error(request, "Nome de usuário já existe.")
                return redirect('cadastrar_usuario')

            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                is_staff=_is_staff_para_role(role),
            )

            if role == PerfilUsuario.ROLE_CONSULTOR:
                instancia = Instancia.objects.create(nome=request.POST.get('instancia_nome') or username, criado_por=request.user)
                PerfilUsuario.objects.create(usuario=user, role=PerfilUsuario.ROLE_CONSULTOR, instancia=instancia, criado_por=request.user)
                _sincronizar_ferramentas_instancia(request, instancia)
            elif role == PerfilUsuario.ROLE_OPERADOR:
                instancia = _resolver_instancia_operador(request)
                if not instancia:
                    user.delete()
                    messages.error(request, 'Selecione uma instância válida para o Operador.')
                    return redirect('cadastrar_usuario')
                PerfilUsuario.objects.create(usuario=user, role=PerfilUsuario.ROLE_OPERADOR, instancia=instancia, criado_por=request.user)
            elif role == PerfilUsuario.ROLE_ADMIN:
                PerfilUsuario.objects.create(usuario=user, role=PerfilUsuario.ROLE_ADMIN, criado_por=request.user)
            else:
                # role == 'cliente': sem PerfilUsuario, igual ao comportamento
                # de antes. Se quem criou é Consultor, registra a instância
                # dona pra esse login continuar visível/selecionável por ele
                # (listagem de usuários e dropdown de vínculo em Cliente)
                # enquanto ainda não estiver associado a nenhum Cliente.
                instancia_criador = perms.get_instancia(request.user)
                if instancia_criador:
                    PortalUsuarioInstancia.objects.create(usuario=user, instancia=instancia_criador, criado_por=request.user)

            _sincronizar_modulos_usuario(request, user)

            tipo_usuario = _TIPO_LABELS.get(role, 'Cliente')
            messages.success(request, f"Usuário '{username}' cadastrado com sucesso como {tipo_usuario}.")
            return redirect('cadastrar_usuario')


@login_required(login_url='login')
@perms.pode_gerenciar_usuarios_required
def hosts_usuario(request, usuario_id):
    """Hosts do cliente vinculado a um login, para a seção "Hosts liberados"
    do modal de edição.

    Sob demanda em vez de embutido na página: a lista de usuários tem dezenas
    de logins de portal e cada cliente pode ter dezenas de hosts — embutir
    tudo dobrava o tamanho do HTML pra alimentar um modal que abre um usuário
    por vez. De quebra, a lista vem sempre atual (host cadastrado depois de a
    página carregar já aparece).
    """
    if not perms.is_admin(request.user) and not perms.usuarios_gerenciaveis_por(request.user).filter(id=usuario_id).exists():
        return JsonResponse({'ok': False, 'erro': 'Sem permissão'}, status=403)
    usuario = get_object_or_404(User, id=usuario_id)
    return JsonResponse({'ok': True, 'info': _hosts_do_usuario(usuario)})


@login_required(login_url='login')
@perms.pode_gerenciar_usuarios_required
def editar_usuario(request):
    if request.method == 'POST':
        usuario_id = request.POST.get('id')

        if not perms.is_admin(request.user) and not perms.usuarios_gerenciaveis_por(request.user).filter(id=usuario_id).exists():
            messages.error(request, 'Você não possui permissão para editar este usuário.')
            return redirect('cadastrar_usuario')

        usuario = get_object_or_404(User, id=usuario_id)

        username = request.POST.get('username')
        email = request.POST.get('email')
        nova_senha = request.POST.get('password')
        role = request.POST.get('role') or 'cliente'

        perfil_atual = getattr(usuario, 'perfil', None)
        role_atual = perfil_atual.role if perfil_atual else 'cliente'
        role_mudou = role != role_atual

        # Reenviar o próprio role (sem alterá-lo) não conta como "definir
        # role" — senão todo usuário fica bloqueado de editar os próprios
        # dados (email/senha) por reenviar o role atual no form. Só valida
        # quando o role está de fato mudando; e ninguém pode alterar o
        # próprio role, nem mesmo o Administrador.
        if role_mudou:
            if str(usuario.id) == str(request.user.id):
                messages.error(request, 'Você não pode alterar seu próprio tipo de usuário.')
                return redirect('cadastrar_usuario')
            if not _validar_role_permitida(request, role):
                messages.error(request, 'Você não possui permissão para definir esse tipo de usuário.')
                return redirect('cadastrar_usuario')

        # Verifica se username já existe em outro usuário
        if User.objects.filter(username=username).exclude(id=usuario_id).exists():
            messages.error(request, 'Erro: Já existe um usuário com esse username.')
            return redirect('cadastrar_usuario')

        # Verifica se email já existe em outro usuário
        if User.objects.filter(email=email).exclude(id=usuario_id).exists():
            messages.error(request, 'Erro: Já existe um usuário com esse email.')
            return redirect('cadastrar_usuario')

        # Atualiza os dados
        usuario.username = username
        usuario.email = email
        usuario.is_staff = _is_staff_para_role(role)

        # Atualiza a senha apenas se foi fornecida
        if nova_senha and nova_senha.strip():
            usuario.set_password(nova_senha)

        usuario.save()

        perfil = getattr(usuario, 'perfil', None)

        if role == PerfilUsuario.ROLE_CONSULTOR:
            if perfil and perfil.role == PerfilUsuario.ROLE_CONSULTOR and perfil.instancia:
                instancia = perfil.instancia
                if request.POST.get('instancia_nome'):
                    instancia.nome = request.POST.get('instancia_nome')
                    instancia.save(update_fields=['nome'])
            else:
                instancia = Instancia.objects.create(nome=request.POST.get('instancia_nome') or usuario.username, criado_por=request.user)
            if perfil:
                perfil.role, perfil.instancia = PerfilUsuario.ROLE_CONSULTOR, instancia
                perfil.save()
            else:
                PerfilUsuario.objects.create(usuario=usuario, role=PerfilUsuario.ROLE_CONSULTOR, instancia=instancia, criado_por=request.user)
            _sincronizar_ferramentas_instancia(request, instancia)
        elif role == PerfilUsuario.ROLE_OPERADOR:
            instancia = _resolver_instancia_operador(request)
            if not instancia:
                messages.error(request, 'Selecione uma instância válida para o Operador.')
                return redirect('cadastrar_usuario')
            if perfil:
                perfil.role, perfil.instancia = PerfilUsuario.ROLE_OPERADOR, instancia
                perfil.save()
            else:
                PerfilUsuario.objects.create(usuario=usuario, role=PerfilUsuario.ROLE_OPERADOR, instancia=instancia, criado_por=request.user)
        elif role == PerfilUsuario.ROLE_ADMIN:
            if perfil:
                perfil.role, perfil.instancia = PerfilUsuario.ROLE_ADMIN, None
                perfil.save()
            else:
                PerfilUsuario.objects.create(usuario=usuario, role=PerfilUsuario.ROLE_ADMIN, criado_por=request.user)
        else:  # cliente
            if perfil:
                perfil.delete()

        _sincronizar_modulos_usuario(request, usuario)
        _sincronizar_acessos_usuario(request, usuario)

        tipo_usuario = _TIPO_LABELS.get(role, 'Cliente')
        messages.success(request, f'Usuário atualizado com sucesso como {tipo_usuario}!')
        return redirect('cadastrar_usuario')

    messages.error(request, 'Método não permitido.')
    return redirect('cadastrar_usuario')

def _next_seguro(request, valor=None):
    """Valida um destino de pós-login (`next`) contra host/scheme atuais —
    mesma checagem que o Django faz nativamente em LoginView, pra não abrir
    open redirect. `valor` explícito tem prioridade; senão lê de POST/GET."""
    next_url = valor if valor is not None else (request.POST.get('next') or request.GET.get('next'))
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return None


def _redirect_pos_login(request, user, next_url=None):
    """Volta pra onde o usuário estava tentando ir (ex: uma URL de proxy de
    acesso que expirou a sessão no meio da navegação) quando houver um
    `next` válido; senão cai no dashboard fixo de sempre."""
    destino = _next_seguro(request, next_url)
    if destino:
        return redirect(destino)
    return redirect_user_by_role(user, request)


def login(request):
    """
    View de login com redirecionamento automático para cliente ou admin.
    ✅ CORRIGIDO: Agora usa is_staff para determinar o tipo de usuário
    """
    if request.method == 'GET':
        # Se já está logado, redireciona para o dashboard apropriado
        if request.user.is_authenticated:
            return redirect_user_by_role(request.user, request)

        return render(request, 'login.html', {
            'turnstile_site_key': settings.TURNSTILE_SITE_KEY,
            'next': request.GET.get('next', ''),
        })

    else:
        next_url = request.POST.get('next', '')
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password')

        # Proteção contra força bruta ANTES de qualquer verificação de senha:
        # enquanto durar o bloqueio nem a senha certa entra, senão o castigo
        # só atrasaria quem já errou — não quem acertasse na 4ª tentativa de
        # um ataque de dicionário. Ver seguranca/services.py.
        bloqueio = seguranca.verificar_bloqueio(request, username)
        if bloqueio:
            seguranca.registrar_tentativa(request, username, TentativaLogin.MOTIVO_BLOQUEADO)
            return _tela_login(request, next_url, _mensagem_bloqueio(bloqueio))

        if not _verificar_turnstile(request):
            # Captcha reprovado NÃO conta pro bloqueio: o widget do Cloudflare
            # falha sozinho de vez em quando (rede, extensão do navegador), e
            # trancar a conta por isso puniria quem nem chegou a errar a senha.
            seguranca.registrar_tentativa(request, username, TentativaLogin.MOTIVO_CAPTCHA)
            return _tela_login(request, next_url, 'Verificação de segurança falhou. Tente novamente.')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            device = getattr(user, 'totp_device', None)
            if device and device.confirmado:
                # Navegador já marcado como confiável pra esse usuário
                # (checkbox "Confiar neste navegador" na tela de 2FA) pula a
                # segunda etapa — continua exigindo usuário+senha sempre.
                cookie_valor = request.COOKIES.get(totp_lib.DISPOSITIVO_CONFIAVEL_COOKIE)
                usuario_confiavel = totp_lib.verificar_dispositivo_confiavel(cookie_valor) if cookie_valor else None
                if usuario_confiavel and usuario_confiavel.id == user.id:
                    auth_login(request, user)
                    seguranca.registrar_sucesso(request, user)
                    tipo_usuario = _TIPO_LABELS.get(perms.get_role(user), 'Cliente')
                    messages.success(request, f"Login realizado com sucesso. Bem-vindo, {tipo_usuario}!")
                    return _redirect_pos_login(request, user)

                # 2FA ativo: guarda o usuário como "pendente" na sessão sem
                # autenticar ainda — só loga de fato depois do código certo
                # em verificar_2fa. O `next` não sobrevive ao redirect (essa
                # segunda etapa é sua própria página, sem querystring), então
                # fica guardado na sessão até lá.
                request.session['2fa_user_id'] = user.id
                request.session['2fa_tentativas'] = 0
                request.session['2fa_next'] = _next_seguro(request, next_url)
                return redirect('verificar_2fa')

            auth_login(request, user)
            seguranca.registrar_sucesso(request, user)

            tipo_usuario = _TIPO_LABELS.get(perms.get_role(user), 'Cliente')
            messages.success(request, f"Login realizado com sucesso. Bem-vindo, {tipo_usuario}!")

            # Redirecionar baseado no tipo de usuário (ou de volta pra onde
            # ele estava, se a sessão caducou no meio de uma navegação)
            return _redirect_pos_login(request, user, next_url)
        else:
            # authenticate() devolve None tanto pra senha errada quanto pra
            # conta inativa/inexistente — separar os três só no registro
            # interno; a mensagem na tela continua genérica de propósito, pra
            # não confirmar ao atacante que o usuário existe.
            existente = User.objects.filter(username=username).first() if username else None
            if existente is None:
                motivo = TentativaLogin.MOTIVO_USUARIO_INEXISTENTE
            elif not existente.is_active:
                motivo = TentativaLogin.MOTIVO_USUARIO_INATIVO
            else:
                motivo = TentativaLogin.MOTIVO_SENHA_INVALIDA

            bloqueio, restantes = seguranca.registrar_falha(request, username, motivo)
            if bloqueio:
                erro = _mensagem_bloqueio(bloqueio)
            elif existente is not None and 0 < restantes <= 2:
                erro = f'Usuário ou senha inválidos. Resta(m) {restantes} tentativa(s) antes do bloqueio temporário.'
            else:
                erro = 'Usuário ou senha inválidos.'

            messages.error(request, erro)
            if next_url:
                return redirect(f"{reverse('login')}?{urlencode({'next': next_url})}")
            return redirect('login')


def verificar_2fa(request):
    """Segunda etapa do login: código do Google Authenticator (ou código de
    backup) pro usuário que a view `login` deixou pendente na sessão."""
    user_id = request.session.get('2fa_user_id')
    if not user_id:
        return redirect('login')

    if request.method == 'POST':
        codigo = request.POST.get('codigo', '')
        user = User.objects.filter(id=user_id).first()
        device = getattr(user, 'totp_device', None) if user else None

        if device and device.confirmado and (totp_lib.verificar_codigo(device, codigo) or totp_lib.verificar_backup_code(device, codigo)):
            del request.session['2fa_user_id']
            request.session.pop('2fa_tentativas', None)
            next_url = request.session.pop('2fa_next', None)
            auth_login(request, user)
            seguranca.registrar_sucesso(request, user)

            tipo_usuario = _TIPO_LABELS.get(perms.get_role(user), 'Cliente')
            messages.success(request, f"Login realizado com sucesso. Bem-vindo, {tipo_usuario}!")
            response = _redirect_pos_login(request, user, next_url)

            if request.POST.get('lembrar_dispositivo') == 'on':
                valor_cookie, expira_em = totp_lib.criar_dispositivo_confiavel(
                    user, descricao=request.META.get('HTTP_USER_AGENT', '')
                )
                response.set_cookie(
                    totp_lib.DISPOSITIVO_CONFIAVEL_COOKIE, valor_cookie,
                    expires=expira_em, httponly=True, secure=request.is_secure(), samesite='Lax',
                )
            return response

        # O contador da sessão (abaixo) só derruba ESTA sessão; o contador
        # persistente é o que tranca a conta de verdade — trocar de aba não
        # zera. Código de 6 dígitos é adivinhável por força bruta se ninguém
        # estiver contando.
        seguranca.registrar_falha(
            request, user.username if user else '', TentativaLogin.MOTIVO_2FA_INVALIDO,
        )

        tentativas = request.session.get('2fa_tentativas', 0) + 1
        if tentativas >= 5:
            request.session.pop('2fa_user_id', None)
            request.session.pop('2fa_tentativas', None)
            request.session.pop('2fa_next', None)
            messages.error(request, 'Muitas tentativas inválidas. Faça login novamente.')
            return redirect('login')

        request.session['2fa_tentativas'] = tentativas
        messages.error(request, 'Código inválido.')

    return render(request, 'verificar_2fa.html')


def redirect_user_by_role(user, request=None):
    """Destino pós-login conforme o papel.

    `request` é opcional só por compatibilidade com chamadas antigas: sem ele
    não dá pra enfileirar mensagem. Antes esta função chamava
    `messages.error(None, ...)`, que levanta TypeError — ou seja, um login de
    conta sem Cliente vinculado virava erro 500 em vez da mensagem
    "sua conta não possui acesso". O `auth_logout` no caminho de erro
    também é necessário: sem ele a conta fica autenticada, e o GET de
    /auth/login/ manda de volta pra cá — laço infinito de redirect.
    """
    role = perms.get_role(user)
    if role == PerfilUsuario.ROLE_ADMIN:
        return redirect('quadro_geral')
    if role in (PerfilUsuario.ROLE_CONSULTOR, PerfilUsuario.ROLE_OPERADOR):
        return redirect('quadro_instancia')

    try:
        Cliente.objects.get_by_usuario_vinculado(user)
        return redirect('cliente_dashboard')
    except Cliente.DoesNotExist:
        if request is not None:
            auth_logout(request)
            messages.error(request, 'Sua conta não possui acesso ao sistema.')
        return redirect('login')


@login_required(login_url='login')
def quadro_geral(request):
    """
    View do quadro geral - apenas para administradores
    ✅ CORRIGIDO: Verifica is_staff em vez de is_superuser/is_staff
    """
    # Verificar se é admin
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, 'Você não possui permissão para acessar esta página.')
        return redirect('login')
    
    # Restante do código do quadro geral...
    return render(request, 'quadro_geral.html')

@login_required(login_url='login')
def logout(request):
    auth_logout(request)
    messages.success(request, "Você foi deslogado com sucesso.")
    return redirect('login')


@login_required(login_url='login')
def trocar_senha(request):
    if request.method == 'GET':
        return render(request, 'trocar_senha.html')
    else:
        senha_atual = request.POST.get('senha_atual')
        nova_senha = request.POST.get('nova_senha')
        confirmar_senha = request.POST.get('confirmar_senha')
        
        # Verifica se a senha atual está correta
        if not request.user.check_password(senha_atual):
            messages.error(request, "Senha atual incorreta.")
            return redirect('trocar_senha')
        
        # Verifica se as senhas coincidem
        if nova_senha != confirmar_senha:
            messages.error(request, "As senhas não coincidem.")
            return redirect('trocar_senha')
        
        # Verifica se a senha tem no mínimo 6 caracteres
        if len(nova_senha) < 6:
            messages.error(request, "A senha deve ter no mínimo 6 caracteres.")
            return redirect('trocar_senha')
        
        # Atualiza a senha
        request.user.set_password(nova_senha)
        request.user.save()
        
        # Mantém o usuário logado após trocar a senha
        update_session_auth_hash(request, request.user)

        messages.success(request, "Senha alterada com sucesso!")
        return redirect('cadastrar_usuario')


@login_required(login_url='login')
def configurar_2fa(request):
    """Tela de autoatendimento: cada usuário ativa/desativa o 2FA (Google
    Authenticator) só na própria conta."""
    device, _ = TOTPDevice.objects.get_or_create(usuario=request.user, defaults={'secret': totp_lib.gerar_secret()})

    if request.method == 'POST':
        acao = request.POST.get('acao')

        if acao == 'ativar' and not device.confirmado:
            codigo = request.POST.get('codigo', '')
            if totp_lib.verificar_codigo(device, codigo):
                device.confirmado = True
                device.confirmado_em = timezone.now()
                device.save(update_fields=['confirmado', 'confirmado_em'])
                backup_codes = totp_lib.gerar_backup_codes(device)
                messages.success(request, 'Autenticação em duas etapas ativada com sucesso!')
                return render(request, 'configurar_2fa.html', {
                    'device': device,
                    'backup_codes': backup_codes,
                })
            messages.error(request, 'Código inválido. Confira o horário do celular e tente novamente.')

        elif acao == 'desativar' and device.confirmado:
            senha = request.POST.get('senha', '')
            if not request.user.check_password(senha):
                messages.error(request, 'Senha incorreta.')
            else:
                device.delete()
                messages.success(request, 'Autenticação em duas etapas desativada.')
                return redirect('configurar_2fa')

        elif acao == 'regenerar_backup' and device.confirmado:
            senha = request.POST.get('senha', '')
            if not request.user.check_password(senha):
                messages.error(request, 'Senha incorreta.')
            else:
                backup_codes = totp_lib.gerar_backup_codes(device)
                messages.success(request, 'Novos códigos de backup gerados. Guarde-os em um lugar seguro.')
                return render(request, 'configurar_2fa.html', {
                    'device': device,
                    'backup_codes': backup_codes,
                })

    contexto = {'device': device}
    if not device.confirmado:
        contexto['qr_code'] = totp_lib.qr_code_data_uri(totp_lib.provisioning_uri(device, request.user))
    return render(request, 'configurar_2fa.html', contexto)


@login_required(login_url='login')
@perms.pode_gerenciar_usuarios_required
def resetar_2fa_admin(request):
    """Apaga o 2FA de outro usuário — cobre perda de celular + códigos de
    backup, quando só um Administrador/Consultor destrava a conta. Cobre
    tanto usuários internos gerenciáveis (Operadores) quanto os logins do
    portal do Cliente final vinculados à própria instância do Consultor
    (`perms.pode_resetar_2fa`), já que o Consultor tem domínio total sobre
    os clientes que ele mesmo atende."""
    destino = 'cadastrar_cliente' if request.POST.get('next') == 'cliente' else 'cadastrar_usuario'
    if request.method != 'POST':
        return redirect(destino)

    usuario_id = request.POST.get('id')
    usuario = get_object_or_404(User, id=usuario_id)
    if not perms.pode_resetar_2fa(request.user, usuario):
        messages.error(request, 'Você não possui permissão para resetar o 2FA deste usuário.')
        return redirect(destino)

    TOTPDevice.objects.filter(usuario=usuario).delete()
    messages.success(request, f"2FA de '{usuario.username}' foi resetado.")
    return redirect(destino)