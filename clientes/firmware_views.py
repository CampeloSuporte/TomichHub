import os, json, mimetypes, threading, uuid, subprocess
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse, FileResponse, Http404, StreamingHttpResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

from clientes.decorators import ferramenta_instancia_required
from clientes.models import FirmwarePasta, FirmwareArquivo, FirmwareCompartilhamento


def _fw_channel_send(event_type: str, data: dict):
    """Envia mensagem ao grupo firmware_downloads via channel layer (seguro de sync)."""
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        layer = get_channel_layer()
        if layer:
            async_to_sync(layer.group_send)(
                'firmware_downloads',
                {'type': 'download_event', 'event_type': event_type, **data},
            )
    except Exception:
        pass

FIRMWARE_ROOT = os.path.join(settings.MEDIA_ROOT, 'firmware')


def _criar_symlink_tftp(dest_path: str, nome: str):
    """
    Cria (ou atualiza) um symlink RELATIVO no root do TFTP/FTP apontando para o arquivo real.
    Symlink relativo é necessário para funcionar dentro do chroot do vsftpd e tftpd-hpa.
    Ex: firmware/MA5800V100R022C11.bin -> Firmware Huawei/OLT/MA5800/22C11/MA5800V100R022C11.bin
    """
    link_path = os.path.join(FIRMWARE_ROOT, nome)
    if dest_path == link_path:
        return  # arquivo já está no root
    # Remove link/arquivo anterior conflitante
    if os.path.islink(link_path) or os.path.exists(link_path):
        try:
            os.remove(link_path)
        except Exception:
            return
    # Symlink relativo: caminho de dest_path relativo ao diretório do link (FIRMWARE_ROOT)
    try:
        rel_target = os.path.relpath(dest_path, FIRMWARE_ROOT)
        os.symlink(rel_target, link_path)
    except Exception:
        pass


# Chave Redis para progresso de download: fw_dl_progress:<task_id>
_CACHE_PREFIX  = 'fw_dl_progress:'
_CACHE_TIMEOUT = 3600  # 1 hora — limpeza automática


def _progress_set(task_id: str, **kwargs):
    key  = _CACHE_PREFIX + task_id
    data = cache.get(key) or {}
    data.update(kwargs)
    cache.set(key, data, timeout=_CACHE_TIMEOUT)


def _progress_get(task_id: str):
    return cache.get(_CACHE_PREFIX + task_id)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _server_base(request):
    """Retorna a URL base do servidor (http/https)."""
    return request.build_absolute_uri('/').rstrip('/')


def _pasta_filhas_ids(pasta):
    """Retorna IDs de uma pasta e todas suas subpastas recursivamente."""
    ids = [pasta.pk]
    for sub in pasta.subpastas.all():
        ids += _pasta_filhas_ids(sub)
    return ids


def _serialize_pasta(p):
    return {'id': p.pk, 'nome': p.nome, 'pai_id': p.pai_id, 'caminho': p.caminho_completo}


def _serialize_arquivo(a, request=None):
    links = []
    for c in a.compartilhamentos.filter(expira_em__gt=timezone.now()):
        links.append({
            'id': c.pk,
            'token': c.token,
            'expira_em': c.expira_em.strftime('%Y-%m-%d %H:%M'),
            'ftp_user': c.ftp_user,
            'ftp_senha': c.ftp_senha,
            'acessos': c.acessos,
        })
    return {
        'id': a.pk,
        'nome': a.nome,
        'tamanho': a.tamanho,
        'tamanho_str': a.tamanho_legivel(),
        'mime_type': a.mime_type,
        'pasta_id': a.pasta_id,
        'caminho': a.caminho_relativo,
        'criado_em': a.criado_em.strftime('%d/%m/%Y %H:%M'),
        'links_ativos': links,
    }


# ── Views principais ──────────────────────────────────────────────────────────
@login_required(login_url='login')
@ferramenta_instancia_required('firmware')
def firmware_index(request):
    return render(request, 'firmware.html')


@login_required(login_url='login')
@ferramenta_instancia_required('firmware')
def firmware_listar(request):
    """Retorna pastas e arquivos de uma pasta (ou raiz)."""
    pasta_id = request.GET.get('pasta_id') or None
    if pasta_id:
        pasta_id = int(pasta_id)

    pastas   = FirmwarePasta.objects.filter(pai_id=pasta_id).order_by('nome')
    arquivos = FirmwareArquivo.objects.filter(pasta_id=pasta_id).order_by('nome')

    # Breadcrumb
    breadcrumb = []
    if pasta_id:
        p = FirmwarePasta.objects.get(pk=pasta_id)
        partes = []
        cur = p
        while cur:
            partes.insert(0, {'id': cur.pk, 'nome': cur.nome})
            cur = cur.pai
        breadcrumb = partes

    return JsonResponse({
        'ok': True,
        'pasta_atual': pasta_id,
        'breadcrumb': breadcrumb,
        'pastas': [_serialize_pasta(p) for p in pastas],
        'arquivos': [_serialize_arquivo(a, request) for a in arquivos],
    })


@login_required(login_url='login')
@ferramenta_instancia_required('firmware')
@require_POST
def firmware_criar_pasta(request):
    data = json.loads(request.body)
    nome = data.get('nome', '').strip().replace('/', '_').replace('..', '_')
    if not nome:
        return JsonResponse({'ok': False, 'erro': 'Nome inválido'}, status=400)
    pai_id = data.get('pai_id') or None

    if FirmwarePasta.objects.filter(nome=nome, pai_id=pai_id).exists():
        return JsonResponse({'ok': False, 'erro': 'Pasta já existe'}, status=400)

    pasta = FirmwarePasta.objects.create(nome=nome, pai_id=pai_id)
    os.makedirs(os.path.join(FIRMWARE_ROOT, pasta.caminho_completo), exist_ok=True)
    return JsonResponse({'ok': True, 'pasta': _serialize_pasta(pasta)})


@login_required(login_url='login')
@ferramenta_instancia_required('firmware')
@require_POST
def firmware_renomear_pasta(request, pasta_id):
    """Renomeia uma pasta: move o diretório no FS e reescreve o caminho dos arquivos filhos."""
    pasta = get_object_or_404(FirmwarePasta, pk=pasta_id)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'erro': 'Requisicao invalida'}, status=400)

    nome = (data.get('nome') or '').strip().replace('/', '_').replace('..', '_')
    if not nome:
        return JsonResponse({'ok': False, 'erro': 'Nome inválido'}, status=400)

    if nome == pasta.nome:
        return JsonResponse({'ok': True, 'pasta': _serialize_pasta(pasta)})

    if FirmwarePasta.objects.filter(nome=nome, pai_id=pasta.pai_id).exclude(pk=pasta.pk).exists():
        return JsonResponse({'ok': False, 'erro': 'Já existe uma pasta com esse nome aqui'}, status=400)

    caminho_antigo = pasta.caminho_completo
    dir_antigo     = os.path.join(FIRMWARE_ROOT, caminho_antigo)

    pasta.nome = nome
    caminho_novo = pasta.caminho_completo
    dir_novo     = os.path.join(FIRMWARE_ROOT, caminho_novo)

    if os.path.exists(dir_novo):
        return JsonResponse({'ok': False, 'erro': 'Já existe um diretório com esse nome no servidor'}, status=400)

    # Move o diretório real (se ainda não existir, apenas cria o novo)
    if os.path.isdir(dir_antigo):
        try:
            os.rename(dir_antigo, dir_novo)
        except Exception as e:
            return JsonResponse({'ok': False, 'erro': f'Erro ao renomear no disco: {e}'}, status=500)
    else:
        os.makedirs(dir_novo, exist_ok=True)

    pasta.save(update_fields=['nome'])

    # Reescreve o caminho armazenado nos arquivos da pasta e de todas as subpastas
    prefixo_antigo = os.path.join('firmware', caminho_antigo) + '/'
    prefixo_novo   = os.path.join('firmware', caminho_novo) + '/'
    ids = _pasta_filhas_ids(pasta)
    for arq in FirmwareArquivo.objects.filter(pasta_id__in=ids):
        nome_atual = (arq.arquivo.name or '').replace('\\', '/')
        if not nome_atual.startswith(prefixo_antigo):
            continue
        arq.arquivo.name = prefixo_novo + nome_atual[len(prefixo_antigo):]
        arq.save(update_fields=['arquivo'])
        # Symlink no root do TFTP ficou quebrado apontando para o caminho antigo
        link_path = os.path.join(FIRMWARE_ROOT, arq.nome)
        if os.path.islink(link_path) and not os.path.exists(link_path):
            _criar_symlink_tftp(os.path.join(settings.MEDIA_ROOT, arq.arquivo.name), arq.nome)

    return JsonResponse({'ok': True, 'pasta': _serialize_pasta(pasta)})


@login_required(login_url='login')
@ferramenta_instancia_required('firmware')
@require_POST
def firmware_upload(request):
    """Upload de arquivo (suporta grandes arquivos via streaming)."""
    pasta_id = request.POST.get('pasta_id') or None
    pasta    = FirmwarePasta.objects.get(pk=int(pasta_id)) if pasta_id else None

    f = request.FILES.get('arquivo')
    if not f:
        return JsonResponse({'ok': False, 'erro': 'Nenhum arquivo enviado'}, status=400)

    nome = os.path.basename(f.name)
    # Destino no filesystem
    if pasta:
        dest_dir = os.path.join(FIRMWARE_ROOT, pasta.caminho_completo)
    else:
        dest_dir = FIRMWARE_ROOT
    os.makedirs(dest_dir, exist_ok=True)

    dest_path = os.path.join(dest_dir, nome)
    # Se já existe, adiciona sufixo
    base, ext = os.path.splitext(nome)
    contador  = 1
    while os.path.exists(dest_path):
        nome      = f'{base}({contador}){ext}'
        dest_path = os.path.join(dest_dir, nome)
        contador += 1

    # Escreve em chunks para suportar arquivos grandes
    with open(dest_path, 'wb') as fp:
        for chunk in f.chunks(chunk_size=8 * 1024 * 1024):
            fp.write(chunk)

    # Garantir leitura pelo TFTP (tftpd-hpa roda como user 'tftp')
    try:
        import grp
        tftp_gid = grp.getgrnam('tftp').gr_gid
        os.chown(dest_path, -1, tftp_gid)
        os.chmod(dest_path, 0o644)
    except Exception:
        pass

    # Symlink no root TFTP para permitir load file tftp <IP> <nome>
    _criar_symlink_tftp(dest_path, nome)

    tamanho   = os.path.getsize(dest_path)
    mime_type = mimetypes.guess_type(nome)[0] or 'application/octet-stream'

    # Caminho relativo para o FileField (relativo ao MEDIA_ROOT)
    pasta_rel = os.path.join('firmware', pasta.caminho_completo, nome) if pasta else os.path.join('firmware', nome)

    arq = FirmwareArquivo.objects.create(
        nome=nome,
        arquivo=pasta_rel,
        tamanho=tamanho,
        mime_type=mime_type,
        pasta=pasta,
        criado_por=request.user,
    )
    return JsonResponse({'ok': True, 'arquivo': _serialize_arquivo(arq, request)})


@login_required(login_url='login')
@ferramenta_instancia_required('firmware')
@require_POST
def firmware_deletar_arquivo(request, arquivo_id):
    arq = get_object_or_404(FirmwareArquivo, pk=arquivo_id)
    try:
        if arq.arquivo and os.path.exists(arq.arquivo.path):
            os.remove(arq.arquivo.path)
    except Exception:
        pass
    arq.delete()
    return JsonResponse({'ok': True})


@login_required(login_url='login')
@ferramenta_instancia_required('firmware')
@require_POST
def firmware_deletar_pasta(request, pasta_id):
    pasta = get_object_or_404(FirmwarePasta, pk=pasta_id)
    # Deleta arquivos dentro recursivamente
    ids = _pasta_filhas_ids(pasta)
    for arq in FirmwareArquivo.objects.filter(pasta_id__in=ids):
        try:
            if arq.arquivo and os.path.exists(arq.arquivo.path):
                os.remove(arq.arquivo.path)
        except Exception:
            pass
    FirmwareArquivo.objects.filter(pasta_id__in=ids).delete()
    # Remove dirs
    import shutil
    pasta_fs = os.path.join(FIRMWARE_ROOT, pasta.caminho_completo)
    if os.path.exists(pasta_fs):
        shutil.rmtree(pasta_fs, ignore_errors=True)
    pasta.delete()
    return JsonResponse({'ok': True})


@login_required(login_url='login')
@ferramenta_instancia_required('firmware')
@require_POST
def firmware_compartilhar(request, arquivo_id):
    """Gera um link de compartilhamento com tempo definido."""
    arq  = get_object_or_404(FirmwareArquivo, pk=arquivo_id)
    data = json.loads(request.body)
    horas       = int(data.get('horas', 24))
    com_senha   = bool(data.get('com_senha', False))

    token     = FirmwareCompartilhamento.gerar_token()
    expira_em = timezone.now() + timedelta(hours=horas)

    ftp_user, ftp_senha = ('', '')
    if com_senha:
        ftp_user, ftp_senha = FirmwareCompartilhamento.gerar_credenciais()
        _ftp_criar_usuario(ftp_user, ftp_senha)

    comp = FirmwareCompartilhamento.objects.create(
        arquivo=arq,
        token=token,
        expira_em=expira_em,
        ftp_user=ftp_user,
        ftp_senha=ftp_senha,
    )

    base = _server_base(request)
    host = request.get_host().split(':')[0]

    return JsonResponse({
        'ok': True,
        'comp_id': comp.pk,
        'token': token,
        'expira_em': expira_em.strftime('%d/%m/%Y %H:%M'),
        'horas': horas,
        'nome_arquivo': arq.nome,
        'tamanho': arq.tamanho_legivel(),
        'ftp_user': ftp_user,
        'ftp_senha': ftp_senha,
        'links': _gerar_links(base, host, token, arq.nome, ftp_user, ftp_senha,
                              tftp_path=arq.caminho_relativo),
    })


def _resolver_ip(host: str) -> str:
    """Resolve hostname para IP. Retorna o próprio host se já for IP ou falhar."""
    import socket
    import re
    if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', host):
        return host  # já é IPv4
    try:
        return socket.gethostbyname(host)
    except Exception:
        return host  # fallback: mantém o host original


def _gerar_links(base, host, token, nome_arquivo, ftp_user='', ftp_senha='',
                  tftp_path: str = ''):
    """
    Gera os links e comandos de download para todos os protocolos.

    tftp_path: caminho relativo do arquivo dentro do TFTP root
               (= FirmwareArquivo.caminho_relativo, ex: 'Firmware Huawei/MA5800.bin').
               Se vazio, usa apenas o nome_arquivo.
    """
    from django.urls import reverse

    path_dl   = reverse('firmware_download', kwargs={'token': token, 'nome_arquivo': nome_arquivo})
    url_http  = base + path_dl
    url_https = url_http.replace('http://', 'https://')

    # IP resolvido — OLTs não aceitam hostname
    host_ip = _resolver_ip(host)

    if ftp_user and ftp_senha:
        url_ftp  = f'ftp://{ftp_user}:{ftp_senha}@{host}/{nome_arquivo}'
        url_sftp = f'sftp://{ftp_user}:{ftp_senha}@{host}/{nome_arquivo}'
    else:
        url_ftp  = f'ftp://{host}/{nome_arquivo}'
        url_sftp = f'sftp://{host}/{nome_arquivo}'

    # Caminho para o TFTP — usa caminho_relativo se disponível
    tftp_nome = tftp_path if tftp_path else nome_arquivo
    url_tftp  = f'tftp://{host_ip}/{tftp_nome}'

    # ── Huawei MA5800/MA5600 ──
    # SFTP: load file sftp <IP> <arquivo> [username <user> password <pass>]
    if ftp_user and ftp_senha:
        huawei_sftp = (
            f'load file sftp {host_ip} {nome_arquivo} '
            f'username {ftp_user} password {ftp_senha}'
        )
    else:
        huawei_sftp = f'load file sftp {host_ip} {nome_arquivo}'

    # TFTP: load file tftp <IP> <arquivo>
    # Huawei OLT só aceita nome simples (basename), sem barras/subdiretórios
    huawei_tftp = f'load file tftp {host_ip} {nome_arquivo}'

    # FTP: load file ftp <IP> <arquivo> [username <user> password <pass>]
    if ftp_user and ftp_senha:
        huawei_ftp = (
            f'load file ftp {host_ip} {nome_arquivo} '
            f'username {ftp_user} password {ftp_senha}'
        )
    else:
        huawei_ftp = f'load file ftp {host_ip} {nome_arquivo}'

    return {
        'http':        url_http,
        'https':       url_https,
        'ftp':         url_ftp,
        'sftp':        url_sftp,
        'tftp':        url_tftp,
        'cisco':       (
            f'copy ftp://{ftp_user}:{ftp_senha}@{host}/{nome_arquivo} flash:'
            if ftp_user
            else f'copy http://{host}/ferramentas/firmware/dl/{token}/{nome_arquivo} flash:'
        ),
        'mikrotik':    f'/tool fetch url="{url_http}" dst-path="{nome_arquivo}"',
        'huawei':      huawei_sftp,
        'huawei_tftp': huawei_tftp,
        'huawei_ftp':  huawei_ftp,
        'huawei_ip':   host_ip,
        'linux':       f'wget "{url_http}" -O "{nome_arquivo}"',
        'curl':        f'curl -L "{url_http}" -o "{nome_arquivo}"',
    }


def _ftp_criar_usuario(username: str, password: str):
    """Cria usuário Linux dedicado para acesso FTP ao diretório de firmware."""
    if not username or not username.startswith('fw_'):
        return
    try:
        subprocess.run(
            ['sudo', 'useradd', '-r', '-d', FIRMWARE_ROOT, '-s', '/usr/sbin/nologin',
             '-M', '--no-user-group', username],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError:
        pass  # usuário já existe — só atualiza a senha
    try:
        subprocess.run(
            ['sudo', 'chpasswd'],
            input=f'{username}:{password}',
            text=True, check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        import logging
        logging.getLogger(__name__).error('chpasswd falhou para %s: %s', username, exc.stderr)


def _ftp_remover_usuario(username: str):
    """Remove usuário Linux de FTP se existir."""
    if not username or not username.startswith('fw_'):
        return  # segurança: só remove usuários gerados pela plataforma
    try:
        subprocess.run(['sudo', 'userdel', username], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        pass


@login_required(login_url='login')
@ferramenta_instancia_required('firmware')
@require_POST
def firmware_revogar_link(request, comp_id):
    comp = get_object_or_404(FirmwareCompartilhamento, pk=comp_id)
    _ftp_remover_usuario(comp.ftp_user)
    comp.delete()
    return JsonResponse({'ok': True})


# ── Download público (sem login, via token) ───────────────────────────────────
def firmware_download(request, token, nome_arquivo):
    comp = get_object_or_404(FirmwareCompartilhamento, token=token)
    if not comp.valido:
        raise Http404('Link expirado')

    arq = comp.arquivo
    if not arq.arquivo or not os.path.exists(arq.arquivo.path):
        raise Http404('Arquivo não encontrado')

    comp.acessos += 1
    comp.save(update_fields=['acessos'])

    dl_id   = str(uuid.uuid4())
    tamanho = arq.tamanho
    nome    = arq.nome
    ip_raw  = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '?'))
    ip      = ip_raw.split(',')[0].strip()

    # Notifica admins que um download começou
    _fw_channel_send('download_start', {
        'dl_id':       dl_id,
        'token':       token,
        'nome':        nome,
        'tamanho':     tamanho,
        'tamanho_str': arq.tamanho_legivel(),
        'ip':          ip,
    })

    def _stream():
        CHUNK            = 256 * 1024   # 256 KB por chunk
        NOTIFY_INTERVAL  = 2 * 1024 * 1024  # notifica a cada 2 MB
        bytes_sent       = 0
        last_notify      = 0

        try:
            with open(arq.arquivo.path, 'rb') as fh:
                while True:
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        break
                    yield chunk
                    bytes_sent += len(chunk)
                    diff = bytes_sent - last_notify
                    if diff >= NOTIFY_INTERVAL or bytes_sent >= tamanho:
                        pct = int(bytes_sent / tamanho * 100) if tamanho > 0 else 0
                        _fw_channel_send('download_progress', {
                            'dl_id':       dl_id,
                            'pct':         min(pct, 99),
                            'bytes_sent':  bytes_sent,
                            'bytes_total': tamanho,
                        })
                        last_notify = bytes_sent
        finally:
            # Garante notificação de conclusão mesmo em caso de erro/conexão fechada
            _fw_channel_send('download_complete', {
                'dl_id':       dl_id,
                'nome':        nome,
                'bytes_total': tamanho,
                'ip':          ip,
            })

    response = StreamingHttpResponse(
        _stream(),
        content_type=arq.mime_type or 'application/octet-stream',
    )
    response['Content-Disposition'] = f'attachment; filename="{nome}"'
    response['Content-Length']      = tamanho
    response['Accept-Ranges']       = 'bytes'
    return response


# ── Helpers de download por URL ──────────────────────────────────────────────
def _fw_normalizar_url(url: str) -> str:
    """
    Converte URLs de serviços conhecidos para links de download direto.

    OneDrive pessoal (1drv.ms / onedrive.live.com):
      https://1drv.ms/b/s!XXXXX  →  download direto via API
      https://onedrive.live.com/personal/.../download.aspx?SourceUrl=...
        → link pessoal requer autenticação; não é possível converter
          automaticamente — retorna a URL original e o worker detectará
          o HTML e vai informar o erro.

    OneDrive compartilhado (link "Qualquer pessoa com o link"):
      https://onedrive.live.com/...?resid=XXX&cid=YYY
      https://1drv.ms/...
        → adiciona &download=1

    Google Drive:
      https://drive.google.com/file/d/FILE_ID/view  →  /uc?export=download&id=FILE_ID
      https://drive.google.com/open?id=FILE_ID      →  /uc?export=download&id=FILE_ID

    Dropbox:
      https://www.dropbox.com/s/XXX/file.bin?dl=0  →  ?dl=1
    """
    from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

    parsed = urlparse(url)
    host   = parsed.netloc.lower()

    # Google Drive
    if 'drive.google.com' in host:
        import re
        m = re.search(r'/file/d/([^/]+)', parsed.path)
        if m:
            return f'https://drive.google.com/uc?export=download&confirm=t&id={m.group(1)}'
        qs = parse_qs(parsed.query)
        if 'id' in qs:
            return f'https://drive.google.com/uc?export=download&confirm=t&id={qs["id"][0]}'

    # Dropbox
    if 'dropbox.com' in host:
        return url.replace('?dl=0', '?dl=1').replace('&dl=0', '&dl=1') + (
            '&dl=1' if 'dl=' not in url else ''
        ).replace('&&', '&')

    # OneDrive compartilhado (1drv.ms ou onedrive.live.com sem /personal/)
    if '1drv.ms' in host:
        return url + ('&' if '?' in url else '?') + 'download=1'

    if 'onedrive.live.com' in host and '/personal/' not in parsed.path:
        return url + ('&' if '?' in url else '?') + 'download=1'

    return url


def _fw_inferir_nome_url(url: str) -> str:
    """
    Tenta extrair o nome real do arquivo a partir da URL.
    Handles:
      - URLs normais:  .../MA5800.bin
      - OneDrive/SharePoint: download.aspx?SourceUrl=.../MA5800.bin
      - Google Drive, Dropbox e similares com parâmetros de query
    """
    from urllib.parse import urlparse, unquote, parse_qs

    parsed    = urlparse(url)
    path_part = parsed.path.rstrip('/')
    nome      = unquote(os.path.basename(path_part)) or 'arquivo'

    # Extensões de scripts servidor — o nome real está em algum parâmetro de query
    _SCRIPT_EXTS = {'.aspx', '.ashx', '.php', '.jsp', '.do', '.axd', ''}
    _, ext_check = os.path.splitext(nome.lower())
    if ext_check in _SCRIPT_EXTS:
        qs = parse_qs(parsed.query, keep_blank_values=False)
        # Parâmetros comuns de OneDrive, SharePoint, CDNs, etc.
        for param in ('SourceUrl', 'source', 'file', 'filename', 'name', 'f', 'dl', 'path'):
            if param in qs:
                candidate = unquote(qs[param][0]).replace('\\', '/')
                candidate_name = os.path.basename(candidate.rstrip('/'))
                if candidate_name and '.' in candidate_name:
                    nome = candidate_name
                    break

    if '.' not in nome:
        nome += '.bin'
    return nome


def _fw_nome_do_content_disposition(cd: str) -> str:
    """
    Extrai o nome do arquivo do cabeçalho Content-Disposition.
    Suporta:
      - filename="foo.bin"
      - filename*=UTF-8''foo.bin  (RFC 5987 — OneDrive, Chrome downloads)
    Retorna string vazia se não encontrar.
    """
    import re
    from urllib.parse import unquote

    if not cd:
        return ''

    # RFC 5987 tem prioridade: filename*=charset''encoded_name
    m = re.search(r"filename\*\s*=\s*[A-Za-z0-9-]*''([^;\r\n]+)", cd, re.IGNORECASE)
    if m:
        return unquote(m.group(1).strip())

    # Formato clássico: filename="..." ou filename=...
    m = re.search(r'filename\s*=\s*["\']?([^"\';\r\n]+)', cd)
    if m:
        return m.group(1).strip().strip('"\'')

    return ''


@login_required(login_url='login')
@ferramenta_instancia_required('firmware')
@require_POST
def firmware_upload_url(request):
    """
    Inicia download de um arquivo via HTTP/HTTPS em background.
    Retorna task_id imediatamente; o cliente monitora via firmware_upload_url_progresso.
    """
    data     = json.loads(request.body)
    url      = data.get('url', '').strip()
    pasta_id = data.get('pasta_id') or None

    if not url:
        return JsonResponse({'ok': False, 'erro': 'URL não informada'}, status=400)

    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return JsonResponse({'ok': False, 'erro': 'Apenas URLs http:// ou https:// são suportadas'}, status=400)

    nome_hint = _fw_inferir_nome_url(url)

    task_id = str(uuid.uuid4())
    _progress_set(task_id,
                  status='conectando',
                  pct=0,
                  bytes_baixados=0,
                  bytes_total=0,
                  nome=nome_hint,
                  arquivo=None,
                  erro=None)

    threading.Thread(
        target=_fw_download_worker,
        args=(task_id, url, pasta_id, request.user.id),
        daemon=True,
    ).start()

    return JsonResponse({'ok': True, 'task_id': task_id, 'nome': nome_hint})


def _fw_download_worker(task_id: str, url: str, pasta_id, user_id: int):
    """
    Thread de download: baixa o arquivo em chunks de 256 KB e reporta o progresso
    em cada chunk via Redis.
    """
    import requests as req_lib

    dest_path = None

    def _upd(**kw):
        _progress_set(task_id, **kw)

    def _resolver_colisao(dest_dir, nome):
        dest = os.path.join(dest_dir, nome)
        base, ext = os.path.splitext(nome)
        c = 1
        while os.path.exists(dest) or os.path.exists(dest + '.tmp'):
            nome = f'{base}({c}){ext}'
            dest = os.path.join(dest_dir, nome)
            c += 1
        return dest, nome

    try:
        url_dl   = _fw_normalizar_url(url)
        nome     = _fw_inferir_nome_url(url)   # usa URL original para inferir nome
        pasta    = FirmwarePasta.objects.get(pk=int(pasta_id)) if pasta_id else None
        dest_dir = os.path.join(FIRMWARE_ROOT, pasta.caminho_completo) if pasta else FIRMWARE_ROOT
        os.makedirs(dest_dir, exist_ok=True)

        dest_path, nome = _resolver_colisao(dest_dir, nome)
        tmp_path = dest_path + '.tmp'
        _upd(nome=nome)

        resp = req_lib.get(
            url_dl, stream=True, timeout=60, verify=True,
            headers={'User-Agent': 'Mozilla/5.0 (firmware-manager)'},
        )
        resp.raise_for_status()

        # Rejeitar respostas HTML — indica página de login, erro ou redirect não seguido
        ct = resp.headers.get('Content-Type', '')
        if 'text/html' in ct:
            raise ValueError(
                'O servidor retornou uma página HTML em vez do arquivo binário. '
                'O link provavelmente requer autenticação (ex: OneDrive pessoal sem compartilhamento). '
                'Use um link de compartilhamento público: no OneDrive clique em '
                '"Compartilhar → Qualquer pessoa com o link" e cole o link gerado.'
            )

        # Tentar pegar nome real do Content-Disposition (suporta RFC 5987)
        nome_cd = os.path.basename(_fw_nome_do_content_disposition(
            resp.headers.get('Content-Disposition', '')
        ))
        if nome_cd and nome_cd != nome:
            novo_dest, nome_cd = _resolver_colisao(dest_dir, nome_cd)
            nome      = nome_cd
            dest_path = novo_dest
            tmp_path  = dest_path + '.tmp'
            _upd(nome=nome)

        total     = int(resp.headers.get('Content-Length', 0))
        baixados  = 0
        _upd(status='baixando', bytes_total=total)

        with open(tmp_path, 'wb') as fp:
            for chunk in resp.iter_content(chunk_size=256 * 1024):  # 256 KB
                if chunk:
                    fp.write(chunk)
                    baixados += len(chunk)
                    pct = int(baixados / total * 100) if total > 0 else 0
                    _upd(bytes_baixados=baixados, pct=min(pct, 99))

        # Renomear .tmp → arquivo final
        os.rename(tmp_path, dest_path)

        # Garantir leitura pelo TFTP
        try:
            import grp
            tftp_gid = grp.getgrnam('tftp').gr_gid
            os.chown(dest_path, -1, tftp_gid)
            os.chmod(dest_path, 0o644)
        except Exception:
            pass

        # Symlink no root TFTP para permitir load file tftp <IP> <nome>
        _criar_symlink_tftp(dest_path, nome)

        tamanho   = os.path.getsize(dest_path)
        mime_type = mimetypes.guess_type(nome)[0] or 'application/octet-stream'
        pasta_rel = (
            os.path.join('firmware', pasta.caminho_completo, nome)
            if pasta else os.path.join('firmware', nome)
        )

        from django.contrib.auth.models import User
        user = User.objects.filter(pk=user_id).first()

        arq = FirmwareArquivo.objects.create(
            nome=nome,
            arquivo=pasta_rel,
            tamanho=tamanho,
            mime_type=mime_type,
            pasta=pasta,
            criado_por=user,
        )

        _upd(
            status='concluido',
            pct=100,
            bytes_baixados=tamanho,
            arquivo={
                'id':          arq.pk,
                'nome':        arq.nome,
                'tamanho':     arq.tamanho,
                'tamanho_str': arq.tamanho_legivel(),
                'mime_type':   arq.mime_type,
                'pasta_id':    arq.pasta_id,
            },
        )

    except Exception as exc:
        import traceback
        msg = str(exc)
        # Mensagens amigáveis
        import requests as req_lib
        if isinstance(exc, req_lib.exceptions.SSLError):
            msg = 'Erro de certificado SSL na URL informada'
        elif isinstance(exc, req_lib.exceptions.ConnectionError):
            msg = 'Não foi possível conectar ao servidor remoto'
        elif isinstance(exc, req_lib.exceptions.Timeout):
            msg = 'Tempo limite excedido ao baixar o arquivo'
        elif isinstance(exc, req_lib.exceptions.HTTPError):
            msg = f'Servidor remoto retornou erro {exc.response.status_code}'
        _upd(status='erro', erro=msg)
        # Remover arquivo parcial
        for f in ([dest_path, dest_path + '.tmp'] if dest_path else []):
            try:
                if f and os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass


@login_required(login_url='login')
@ferramenta_instancia_required('firmware')
def firmware_upload_url_progresso(request, task_id):
    """Retorna o progresso de um download em andamento (polling)."""
    data = _progress_get(task_id)
    if data is None:
        return JsonResponse({'ok': False, 'erro': 'Tarefa não encontrada ou expirada'}, status=404)
    return JsonResponse({'ok': True, **data})


@login_required(login_url='login')
@ferramenta_instancia_required('firmware')
def firmware_links_ativos(request, arquivo_id):
    arq   = get_object_or_404(FirmwareArquivo, pk=arquivo_id)
    comps = arq.compartilhamentos.filter(expira_em__gt=timezone.now())
    base  = _server_base(request)
    host  = request.get_host().split(':')[0]
    result = []
    for c in comps:
        result.append({
            'id': c.pk,
            'token': c.token,
            'expira_em': c.expira_em.strftime('%d/%m/%Y %H:%M'),
            'acessos': c.acessos,
            'ftp_user': c.ftp_user,
            'ftp_senha': c.ftp_senha,
            'links': _gerar_links(base, host, c.token, arq.nome, c.ftp_user, c.ftp_senha,
                                  tftp_path=arq.caminho_relativo),
        })
    return JsonResponse({'ok': True, 'compartilhamentos': result})
