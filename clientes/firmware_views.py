import os, json, mimetypes
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, FileResponse, Http404, StreamingHttpResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

from clientes.decorators import admin_required
from clientes.models import FirmwarePasta, FirmwareArquivo, FirmwareCompartilhamento

FIRMWARE_ROOT = os.path.join(settings.MEDIA_ROOT, 'firmware')


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
@admin_required
def firmware_index(request):
    return render(request, 'firmware.html')


@login_required(login_url='login')
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
        'links': _gerar_links(base, host, token, arq.nome, ftp_user, ftp_senha),
    })


def _gerar_links(base, host, token, nome_arquivo, ftp_user='', ftp_senha=''):
    from django.urls import reverse
    path_dl   = reverse('firmware_download', kwargs={'token': token, 'nome_arquivo': nome_arquivo})
    url_http  = base + path_dl
    url_https = url_http.replace('http://', 'https://')
    if ftp_user and ftp_senha:
        url_ftp  = f'ftp://{ftp_user}:{ftp_senha}@{host}/{nome_arquivo}'
        url_sftp = f'sftp://{ftp_user}:{ftp_senha}@{host}/{nome_arquivo}'
    else:
        url_ftp  = f'ftp://{host}/{nome_arquivo}'
        url_sftp = f'sftp://{host}/{nome_arquivo}'
    url_tftp = f'tftp://{host}/{nome_arquivo}'

    return {
        'http':    url_http,
        'https':   url_https,
        'ftp':     url_ftp,
        'sftp':    url_sftp,
        'tftp':    url_tftp,
        'cisco':   f'copy ftp://{ftp_user}:{ftp_senha}@{host}/{nome_arquivo} flash:' if ftp_user else f'copy http://{host}/ferramentas/firmware/dl/{token}/{nome_arquivo} flash:',
        'mikrotik': f'/tool fetch url="{url_http}" dst-path="{nome_arquivo}"',
        'huawei':  f'tftp {host} get {nome_arquivo}',
        'linux':   f'wget "{url_http}" -O "{nome_arquivo}"',
        'curl':    f'curl -L "{url_http}" -o "{nome_arquivo}"',
    }


@login_required(login_url='login')
@admin_required
@require_POST
def firmware_revogar_link(request, comp_id):
    comp = get_object_or_404(FirmwareCompartilhamento, pk=comp_id)
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

    response = FileResponse(
        open(arq.arquivo.path, 'rb'),
        content_type=arq.mime_type or 'application/octet-stream',
        as_attachment=True,
        filename=arq.nome,
    )
    response['Content-Length'] = arq.tamanho
    response['Accept-Ranges'] = 'bytes'
    return response


# ── Info de compartilhamentos ativos de um arquivo ───────────────────────────
@login_required(login_url='login')
@admin_required
@require_POST
def firmware_upload_url(request):
    """Faz download de um arquivo via HTTP/HTTPS e salva no gerenciador."""
    import requests as req_lib
    from urllib.parse import urlparse, unquote

    data = json.loads(request.body)
    url = data.get('url', '').strip()
    pasta_id = data.get('pasta_id') or None

    if not url:
        return JsonResponse({'ok': False, 'erro': 'URL não informada'}, status=400)

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return JsonResponse({'ok': False, 'erro': 'Apenas URLs http:// ou https:// são suportadas'}, status=400)

    pasta = FirmwarePasta.objects.get(pk=int(pasta_id)) if pasta_id else None

    # Nome do arquivo a partir da URL
    path_part = parsed.path.rstrip('/')
    nome = unquote(os.path.basename(path_part)) or 'arquivo'
    if '.' not in nome:
        nome = nome + '.bin'

    # Diretório de destino
    dest_dir = os.path.join(FIRMWARE_ROOT, pasta.caminho_completo) if pasta else FIRMWARE_ROOT
    os.makedirs(dest_dir, exist_ok=True)

    # Evitar colisão de nomes
    dest_path = os.path.join(dest_dir, nome)
    base, ext = os.path.splitext(nome)
    contador = 1
    while os.path.exists(dest_path):
        nome = f'{base}({contador}){ext}'
        dest_path = os.path.join(dest_dir, nome)
        contador += 1

    try:
        resp = req_lib.get(url, stream=True, timeout=30, verify=True,
                           headers={'User-Agent': 'Mozilla/5.0 (firmware-manager)'})
        resp.raise_for_status()
        with open(dest_path, 'wb') as fp:
            for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                if chunk:
                    fp.write(chunk)
    except req_lib.exceptions.SSLError:
        return JsonResponse({'ok': False, 'erro': 'Erro de certificado SSL na URL informada'}, status=400)
    except req_lib.exceptions.ConnectionError:
        return JsonResponse({'ok': False, 'erro': 'Não foi possível conectar ao servidor remoto'}, status=400)
    except req_lib.exceptions.Timeout:
        return JsonResponse({'ok': False, 'erro': 'Tempo limite excedido ao tentar baixar o arquivo'}, status=400)
    except req_lib.exceptions.HTTPError as e:
        return JsonResponse({'ok': False, 'erro': f'Servidor remoto retornou erro: {e.response.status_code}'}, status=400)
    except Exception as e:
        # Remove arquivo parcial se houver
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except Exception:
                pass
        return JsonResponse({'ok': False, 'erro': f'Erro ao baixar arquivo: {str(e)}'}, status=500)

    tamanho = os.path.getsize(dest_path)
    mime_type = mimetypes.guess_type(nome)[0] or 'application/octet-stream'
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
@admin_required
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
            'links': _gerar_links(base, host, c.token, arq.nome, c.ftp_user, c.ftp_senha),
        })
    return JsonResponse({'ok': True, 'compartilhamentos': result})
