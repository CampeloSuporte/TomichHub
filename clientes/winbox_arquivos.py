"""
Envio de arquivo do PC do usuário para o Files do MikroTik, pela tela do
WinBox Web.

O WinBox roda no servidor (Xvfb), então o seletor de arquivo dele só enxerga
o disco do servidor, e arrastar do PC para o noVNC não chega lá. O caminho é
outro: o navegador manda o arquivo para o CRM e o CRM grava no equipamento
por SFTP, na porta SSH do host (o RouterOS expõe o Files no SFTP; o usuário
precisa da policy `ftp`). Assim que termina, o arquivo aparece no Files do
WinBox aberto.

A conexão usa o mesmo caminho do backup (tasks._conectar_ssh_acesso): IP
privado via proxy SSH do cliente ou túnel OpenVPN, público direto.

A cópia roda numa thread: um .npk grande por um proxy lento passa fácil do
timeout do nginx/gunicorn. O andamento fica no cache (Redis, compartilhado
entre os workers do gunicorn) e a página consulta pelo id do envio.
"""
import copy
import logging
import os
import re
import tempfile
import threading
import time
import uuid

from django.core.cache import cache

logger = logging.getLogger(__name__)

TAMANHO_MAX = 512 * 1024 * 1024  # .npk/backup/rsc ficam muito abaixo disso
_TTL = 3600
PREFIXO_TMP = 'crm_winbox_envio_'
# Cópia mais longa possível (512 MB num link ruim) cabe folgada nisso; um
# temporário mais velho é de envio cuja thread morreu com o gunicorn.
SOBRA_SEGUNDOS = 6 * 3600
_NOME_RUIM = re.compile(r'[\x00-\x1f\x7f/\\]')


def _chave(envio_id):
    return f'winbox_envio:{envio_id}'


def nome_seguro(nome):
    """Só o nome do arquivo (sem pasta do PC), sem caractere de controle."""
    nome = (nome or '').replace('\\', '/').rsplit('/', 1)[-1].strip()
    nome = _NOME_RUIM.sub('_', nome)
    if nome in ('', '.', '..'):
        raise ValueError('Nome de arquivo inválido.')
    return nome[:200]


def pasta_segura(pasta):
    """Pasta de destino no Files ('' = raiz). Barras extras são ignoradas;
    '..' e caractere de controle são recusados."""
    partes = [p.strip() for p in (pasta or '').replace('\\', '/').split('/') if p.strip()]
    for p in partes:
        if p in ('.', '..') or _NOME_RUIM.search(p):
            raise ValueError('Pasta de destino inválida.')
    return '/'.join(partes)


def caminho_remoto(pasta, nome):
    return f'{pasta}/{nome}' if pasta else nome


def estado(envio_id):
    return cache.get(_chave(envio_id))


def _gravar_estado(envio_id, **dados):
    atual = cache.get(_chave(envio_id)) or {}
    atual.update(dados)
    cache.set(_chave(envio_id), atual, _TTL)
    return atual


def acesso_ssh(acesso):
    """Cópia do acesso apontando para a porta SSH (o principal é WinBox)."""
    porta = acesso.porta_ssh()
    if not porta:
        raise ValueError('Este host não tem SSH cadastrado: o envio de arquivo vai por SFTP. '
                         'Cadastre o SSH em Protocolos do acesso.')
    a = copy.copy(acesso)
    a.porta, a.protocolo = porta, 'SSH'
    return a


def mensagem_erro(exc):
    txt = str(exc) or exc.__class__.__name__
    baixo = txt.lower()
    if 'permission denied' in baixo or 'errno 13' in baixo:
        return ('O equipamento recusou a gravação (permission denied). O usuário do '
                'acesso precisa da policy "ftp" no RouterOS, e a pasta precisa existir.')
    if 'no such file' in baixo or 'errno 2' in baixo:
        return 'Pasta de destino não existe no equipamento.'
    if 'authentication' in baixo:
        return 'SSH recusou usuário/senha do acesso.'
    if 'subsystem' in baixo or 'channel closed' in baixo or 'eof' in baixo:
        return f'O equipamento não aceitou SFTP ({txt}). Confira se o serviço SSH está habilitado.'
    if 'timed out' in baixo or 'timeout' in baixo:
        return 'Tempo esgotado ao conectar no SSH do equipamento.'
    return txt


def iniciar_envio(acesso, arquivo, pasta, usuario, ip_origem=None):
    """Salva o upload num temporário e dispara a cópia em segundo plano.
    Retorna o id do envio. Erros de validação sobem como ValueError."""
    nome = nome_seguro(getattr(arquivo, 'name', ''))
    pasta = pasta_segura(pasta)
    if arquivo.size > TAMANHO_MAX:
        raise ValueError(f'Arquivo maior que {TAMANHO_MAX // (1024 * 1024)} MB.')
    acesso_s = acesso_ssh(acesso)

    limpar_sobras()
    # O arquivo NÃO fica no servidor: este temporário só existe até a cópia
    # para o equipamento acabar (ok ou erro, ver _executar) e some também se
    # algo falhar antes da thread começar.
    # Direto no /tmp, sem subpasta fixa: uma pasta criada por um shell como
    # root (0700) deixava o gunicorn (www-data) sem gravar → HTTP 500.
    fd, tmp = tempfile.mkstemp(prefix=PREFIXO_TMP)
    try:
        with os.fdopen(fd, 'wb') as f:
            for bloco in arquivo.chunks():
                f.write(bloco)

        envio_id = uuid.uuid4().hex
        destino = caminho_remoto(pasta, nome)
        _gravar_estado(envio_id, status='enviando', enviado=0, total=arquivo.size,
                       destino=destino, acesso_id=acesso.id, usuario_id=getattr(usuario, 'id', None))
        threading.Thread(
            target=_executar, args=(envio_id, acesso_s, tmp, destino, arquivo.size, usuario, ip_origem),
            daemon=True, name=f'winbox-envio-{envio_id[:8]}',
        ).start()
    except BaseException:
        _apagar(tmp)
        raise
    return envio_id


def _apagar(caminho):
    try:
        os.unlink(caminho)
    except OSError:
        pass


def limpar_sobras(idade=SOBRA_SEGUNDOS):
    """Apaga temporários de envios cuja thread morreu sem chegar ao finally
    (gunicorn reiniciado no meio da cópia). Roda a cada envio novo."""
    limite = time.time() - idade
    pasta = tempfile.gettempdir()
    try:
        nomes = os.listdir(pasta)
    except OSError:
        return
    for nome in nomes:
        if not nome.startswith(PREFIXO_TMP):
            continue
        caminho = os.path.join(pasta, nome)
        try:
            if os.path.getmtime(caminho) < limite:
                os.unlink(caminho)
        except OSError:
            pass


def _executar(envio_id, acesso, tmp, destino, total, usuario, ip_origem):
    from django.db import close_old_connections
    from .tasks import _conectar_ssh_acesso, _fechar_ssh

    client = tunel = None
    ultimo = [0]

    def progresso(enviado, _total):
        # Redis a cada ~256 KB, não a cada bloco de 32 KB do paramiko
        if enviado - ultimo[0] >= 256 * 1024 or enviado >= total:
            ultimo[0] = enviado
            _gravar_estado(envio_id, enviado=enviado)

    try:
        client, tunel = _conectar_ssh_acesso(acesso)
        sftp = client.open_sftp()
        try:
            with open(tmp, 'rb') as f:
                # confirm=False: o stat logo após o close às vezes chega antes
                # do RouterOS fechar o arquivo e devolve o tamanho parcial.
                sftp.putfo(f, destino, file_size=total, callback=progresso, confirm=False)
        finally:
            sftp.close()
        _gravar_estado(envio_id, status='ok', enviado=total)
        logger.info('📤 WinBox Web: %s enviou %s (%d bytes) para %s',
                    getattr(usuario, 'username', '?'), destino, total, acesso.host)
        _auditar(acesso, usuario, ip_origem, destino, total)
    except Exception as e:
        logger.warning('❌ WinBox Web: envio de %s para %s falhou: %s', destino, acesso.host, e)
        _gravar_estado(envio_id, status='erro', erro=mensagem_erro(e))
    finally:
        if client:
            _fechar_ssh(client, tunel)
        _apagar(tmp)
        close_old_connections()


def _auditar(acesso, usuario, ip_origem, destino, total):
    """Registra o envio como comando da sessão WinBox Web aberta pelo usuário
    (auditoria de acessos); sem sessão ativa, fica só no log."""
    try:
        from .models import AcessoSessao, AcessoComando
        sessao = (AcessoSessao.objects
                  .filter(acesso_id=acesso.id, usuario=usuario, tipo='winbox', status='ativa')
                  .order_by('-iniciada_em').first())
        if sessao:
            AcessoComando.objects.create(
                sessao=sessao, comando=f'[envio de arquivo] {destino} ({total} bytes)')
    except Exception as e:
        logger.warning('Auditoria do envio de arquivo falhou: %s', e)
