"""Transcrição dos áudios do chat, no próprio servidor.

Usa o faster-whisper (Whisper da OpenAI reimplementado em CTranslate2) rodando
em CPU, offline: não depende de chave, crédito ou API externa, e o áudio do
cliente não sai do servidor.

Fluxo: a mensagem de áudio é salva → `agendar()` marca `pendente` e, depois do
commit, põe a task `transcrever_audio` na fila `transcricao` → um worker
Celery só dessa fila (celery-transcricao.service, concurrency 1) transcreve e
avisa a tela por WebSocket (`message_transcribed`).

Fila própria porque o worker principal roda com concurrency 1 e o beat
embutido: um áudio de 2 minutos nele seguraria SLA, agendadas e alertas.

Sem o pacote `faster-whisper` instalado nada é agendado e o chat segue igual,
só sem o texto embaixo do player.
"""
import importlib.util
import logging
import os
import threading

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

FILA = 'transcricao'

#: Modelo do Whisper. `small` é o equilíbrio para PT-BR em CPU; `medium`
#: acerta mais, porém fica umas 3× mais lento; `base` é mais rápido e erra
#: mais. Troca pelo settings.WHISPER_MODEL sem mexer aqui.
MODELO_PADRAO = 'small'

_modelo = None
_modelo_lock = threading.Lock()


def disponivel() -> bool:
    """O pacote está instalado neste venv?"""
    if not getattr(settings, 'TRANSCRICAO_AUDIO_ATIVA', True):
        return False
    return importlib.util.find_spec('faster_whisper') is not None


def _diretorio_modelos() -> str:
    return str(getattr(settings, 'WHISPER_MODEL_DIR',
                       os.path.join(settings.BASE_DIR, 'tools', 'whisper_models')))


def _carregar_modelo():
    """Carrega o modelo uma vez por processo (leva alguns segundos e ~500 MB
    de RAM no `small`); as chamadas seguintes reaproveitam."""
    global _modelo
    if _modelo is None:
        with _modelo_lock:
            if _modelo is None:
                from faster_whisper import WhisperModel
                _modelo = WhisperModel(
                    getattr(settings, 'WHISPER_MODEL', MODELO_PADRAO),
                    device='cpu',
                    compute_type='int8',
                    cpu_threads=int(getattr(settings, 'WHISPER_CPU_THREADS', 4)),
                    download_root=_diretorio_modelos(),
                )
    return _modelo


def caminho_da_midia(attachment_url: str) -> str:
    """Caminho no disco do arquivo de `attachment_url`, ou '' se ele não
    está dentro do MEDIA_ROOT (mesma guarda contra "../" da exclusão)."""
    url = attachment_url or ''
    if not url.startswith(settings.MEDIA_URL):
        return ''
    raiz = os.path.normpath(str(settings.MEDIA_ROOT))
    caminho = os.path.normpath(os.path.join(raiz, url[len(settings.MEDIA_URL):]))
    if not caminho.startswith(raiz + os.sep):
        return ''
    return caminho


def transcrever_arquivo(caminho: str) -> str:
    """Texto falado no arquivo (ogg/opus do WhatsApp, webm do navegador,
    mp3, m4a…). A decodificação é do PyAV, que já vem com o faster-whisper.

    `vad_filter` corta os silêncios antes do modelo: áudio de WhatsApp tem
    muito respiro e ruído no começo/fim, e é ali que o Whisper "inventa"
    frases ("Obrigado por assistir") quando não há fala.
    """
    modelo = _carregar_modelo()
    segmentos, _info = modelo.transcribe(
        caminho, language='pt', vad_filter=True, beam_size=5,
        condition_on_previous_text=False,
    )
    return ' '.join(s.text.strip() for s in segmentos if s.text.strip()).strip()


def agendar(msg) -> bool:
    """Põe um áudio na fila de transcrição. Chamado logo depois de criar a
    Message; devolve False quando não há o que fazer."""
    if msg.message_type != 'audio' or not msg.attachment_url or msg.deleted_at:
        return False
    if not disponivel():
        return False
    from .models import Message
    Message.objects.filter(id=msg.id).update(transcricao_status='pendente')
    msg.transcricao_status = 'pendente'

    def _enfileirar():
        from .tasks import transcrever_audio
        try:
            transcrever_audio.apply_async(args=[str(msg.id)], queue=FILA)
        except Exception as e:   # broker fora: o chat não pode cair por isso
            logger.warning(f"Não foi possível enfileirar transcrição ({msg.id}): {e}")
            Message.objects.filter(id=msg.id).update(transcricao_status='erro')

    transaction.on_commit(_enfileirar)
    return True
