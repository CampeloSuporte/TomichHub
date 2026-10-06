"""Transcrição dos áudios do chat (atendimento/transcricao.py + task
transcrever_audio). O Whisper em si é mockado: aqui vale o encanamento —
quem entra na fila, o que é gravado e o que vai para a tela."""
import os
import tempfile
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from atendimento import transcricao
from atendimento.models import Message
from atendimento.services import ConversationService
from atendimento.tasks import transcrever_audio, _contexto_conversa
from atendimento.tests import _criar_conversa


def _audio(conv, **extra):
    campos = dict(conversation=conv, sender_type='customer', message_type='audio',
                  content='', external_id=f'wamid_{timezone.now().timestamp()}',
                  attachment_url=f'{settings.MEDIA_URL}atendimento/media/x.ogg')
    campos.update(extra)
    return Message.objects.create(**campos)


@mock.patch('atendimento.transcricao.disponivel', return_value=True)
class AgendarTest(TestCase):
    def setUp(self):
        self.conv = _criar_conversa()

    @mock.patch('atendimento.tasks.transcrever_audio.apply_async')
    def test_audio_novo_vai_para_a_fila_propria_depois_do_commit(self, mock_async, _disp):
        with self.captureOnCommitCallbacks(execute=True):
            msg, created = ConversationService._salvar_msg(
                self.conv, 'WAMID_A1', '', 'audio', 'Fulano',
                f'{settings.MEDIA_URL}atendimento/media/a.ogg')
        self.assertTrue(created)
        msg.refresh_from_db()
        self.assertEqual(msg.transcricao_status, 'pendente')
        mock_async.assert_called_once_with(args=[str(msg.id)], queue='transcricao')

    @mock.patch('atendimento.tasks.transcrever_audio.apply_async')
    def test_mensagem_repetida_do_webhook_nao_enfileira_de_novo(self, mock_async, _disp):
        url = f'{settings.MEDIA_URL}atendimento/media/a.ogg'
        with self.captureOnCommitCallbacks(execute=True):
            ConversationService._salvar_msg(self.conv, 'WAMID_A2', '', 'audio', 'F', url)
            ConversationService._salvar_msg(self.conv, 'WAMID_A2', '', 'audio', 'F', url)
        self.assertEqual(mock_async.call_count, 1)

    @mock.patch('atendimento.tasks.transcrever_audio.apply_async')
    def test_texto_e_audio_sem_arquivo_ficam_fora(self, mock_async, _disp):
        with self.captureOnCommitCallbacks(execute=True):
            ConversationService._salvar_msg(self.conv, 'WAMID_T', 'oi', 'text', 'F', None)
            m, _ = ConversationService._salvar_msg(self.conv, 'WAMID_A3', '', 'audio', 'F', None)
        mock_async.assert_not_called()
        m.refresh_from_db()
        self.assertEqual(m.transcricao_status, '')

    @mock.patch('atendimento.tasks.transcrever_audio.apply_async', side_effect=OSError('redis fora'))
    def test_broker_fora_marca_erro_sem_derrubar_o_chat(self, _async, _disp):
        with self.captureOnCommitCallbacks(execute=True):
            msg, _ = ConversationService._salvar_msg(
                self.conv, 'WAMID_A4', '', 'audio', 'F', f'{settings.MEDIA_URL}atendimento/media/a.ogg')
        msg.refresh_from_db()
        self.assertEqual(msg.transcricao_status, 'erro')


class SemBibliotecaTest(TestCase):
    @mock.patch('atendimento.transcricao.importlib.util.find_spec', return_value=None)
    def test_sem_faster_whisper_nada_e_agendado(self, _spec):
        msg = _audio(_criar_conversa())
        self.assertFalse(transcricao.agendar(msg))
        msg.refresh_from_db()
        self.assertEqual(msg.transcricao_status, '')

    @override_settings(TRANSCRICAO_AUDIO_ATIVA=False)
    def test_desligado_no_settings(self):
        self.assertFalse(transcricao.disponivel())


class CaminhoDaMidiaTest(TestCase):
    def test_dentro_do_media_root(self):
        c = transcricao.caminho_da_midia(f'{settings.MEDIA_URL}atendimento/media/a.ogg')
        self.assertTrue(c.startswith(os.path.normpath(str(settings.MEDIA_ROOT)) + os.sep))

    def test_recusa_fora_do_media_root(self):
        self.assertEqual(transcricao.caminho_da_midia(f'{settings.MEDIA_URL}../crm/settings.py'), '')
        self.assertEqual(transcricao.caminho_da_midia('/etc/passwd'), '')
        self.assertEqual(transcricao.caminho_da_midia(''), '')


class TaskTranscreverAudioTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, 'atendimento', 'media'))
        with open(os.path.join(self.tmp.name, 'atendimento', 'media', 'x.ogg'), 'wb') as f:
            f.write(b'OggS')
        self.media = override_settings(MEDIA_ROOT=self.tmp.name)
        self.media.enable()
        self.conv = _criar_conversa()

    def tearDown(self):
        self.media.disable()
        self.tmp.cleanup()

    @mock.patch('atendimento.services._ws_send_conversation')
    @mock.patch('atendimento.transcricao.transcrever_arquivo', return_value='o link caiu de novo')
    def test_grava_texto_e_avisa_a_tela(self, _tr, mock_ws):
        msg = _audio(self.conv, transcricao_status='pendente')
        transcrever_audio(str(msg.id))
        msg.refresh_from_db()
        self.assertEqual(msg.transcricao, 'o link caiu de novo')
        self.assertEqual(msg.transcricao_status, 'ok')
        conv_id, payload = mock_ws.call_args[0]
        self.assertEqual(conv_id, str(self.conv.id))
        self.assertEqual(payload['type'], 'message_transcribed')
        self.assertEqual(payload['message']['transcricao'], 'o link caiu de novo')

    @mock.patch('atendimento.services._ws_send_conversation')
    @mock.patch('atendimento.transcricao.transcrever_arquivo', return_value='')
    def test_sem_fala_vira_vazia(self, _tr, _ws):
        msg = _audio(self.conv, transcricao_status='pendente')
        transcrever_audio(str(msg.id))
        msg.refresh_from_db()
        self.assertEqual(msg.transcricao_status, 'vazia')

    @mock.patch('atendimento.services._ws_send_conversation')
    @mock.patch('atendimento.transcricao.transcrever_arquivo', side_effect=RuntimeError('decoder'))
    def test_falha_do_modelo_vira_erro(self, _tr, _ws):
        msg = _audio(self.conv, transcricao_status='pendente')
        transcrever_audio(str(msg.id))
        msg.refresh_from_db()
        self.assertEqual(msg.transcricao_status, 'erro')

    @mock.patch('atendimento.services._ws_send_conversation')
    @mock.patch('atendimento.transcricao.transcrever_arquivo')
    def test_arquivo_sumido_vira_erro_sem_chamar_o_modelo(self, mock_tr, _ws):
        msg = _audio(self.conv, attachment_url=f'{settings.MEDIA_URL}atendimento/media/nao_existe.ogg')
        transcrever_audio(str(msg.id))
        mock_tr.assert_not_called()
        msg.refresh_from_db()
        self.assertEqual(msg.transcricao_status, 'erro')

    @mock.patch('atendimento.services._ws_send_conversation')
    @mock.patch('atendimento.transcricao.transcrever_arquivo')
    def test_mensagem_apagada_nao_e_transcrita(self, mock_tr, mock_ws):
        msg = _audio(self.conv, deleted_at=timezone.now())
        transcrever_audio(str(msg.id))
        mock_tr.assert_not_called()
        mock_ws.assert_not_called()

    @mock.patch('atendimento.services._ws_send_conversation')
    def test_apagada_durante_a_transcricao_nao_grava(self, mock_ws):
        msg = _audio(self.conv, transcricao_status='pendente')

        def _apaga_no_meio(_caminho):
            Message.objects.filter(id=msg.id).update(deleted_at=timezone.now())
            return 'segredo'

        with mock.patch('atendimento.transcricao.transcrever_arquivo', side_effect=_apaga_no_meio):
            transcrever_audio(str(msg.id))
        msg.refresh_from_db()
        self.assertEqual(msg.transcricao, '')
        mock_ws.assert_not_called()


class UsoDaTranscricaoTest(TestCase):
    def test_contexto_da_ia_le_o_audio(self):
        conv = _criar_conversa()
        _audio(conv, transcricao='preciso liberar a porta 8080', transcricao_status='ok')
        texto, _ = _contexto_conversa(conv)
        self.assertIn('[áudio] preciso liberar a porta 8080', texto)

    def test_citacao_de_audio_mostra_o_que_foi_dito(self):
        msg = _audio(_criar_conversa(), transcricao='pode reiniciar a OLT', transcricao_status='ok')
        self.assertEqual(msg.resumo_citado(), '🎤 Áudio pode reiniciar a OLT')

    def test_citacao_de_audio_sem_transcricao_continua_rotulo(self):
        self.assertEqual(_audio(_criar_conversa()).resumo_citado(), '🎤 Áudio')
