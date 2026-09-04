import os
import uuid
import base64
import requests
import logging
import re as _re
import unicodedata
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from .models import (
    WhatsAppConnection, ContactGroup, Conversation, Message, MessageReaction,
    ConversationActivity, ChatFlow, ChatFlowSession, Category, GroupMemberName,
)
from .scope import atendentes_do_chamado
from clientes.models import Cliente

logger = logging.getLogger(__name__)
channel_layer = get_channel_layer()


# ── WebRTC: servidor ICE (STUN + TURN com credenciais temporárias) ───────────
def build_ice_servers() -> List[Dict]:
    """
    Monta a lista de iceServers para o WebRTC (Sala Virtual e chamadas 1:1).

    Inclui STUN públicos e, se houver TURN_SECRET configurado, um servidor TURN
    com credenciais temporárias no padrão TURN REST API:
        username = "<expiry_unix>:crm"
        password = base64( HMAC_SHA1(TURN_SECRET, username) )
    Assim o segredo nunca vai para o navegador e as credenciais expiram sozinhas.
    """
    servers = [
        {'urls': 'stun:stun.l.google.com:19302'},
        {'urls': 'stun:stun1.l.google.com:19302'},
    ]
    secret = getattr(settings, 'TURN_SECRET', '')
    host   = getattr(settings, 'TURN_HOST', '')
    if secret and host:
        import hmac, hashlib, time
        ttl = int(getattr(settings, 'TURN_TTL', 12 * 3600))
        username = f"{int(time.time()) + ttl}:crm"
        digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
        credential = base64.b64encode(digest).decode()
        servers.append({
            'urls': [
                f'turn:{host}:3478?transport=udp',
                f'turn:{host}:3478?transport=tcp',
            ],
            'username': username,
            'credential': credential,
        })
    return servers

# Mapeamento: chave do payload WhatsApp → message_type interno
_MEDIA_TYPE_MAP = {
    'imageMessage':    'image',
    'stickerMessage':  'image',
    'lottieStickerMessage': 'image',   # figurinha animada
    'videoMessage':    'video',
    'audioMessage':    'audio',
    'pttMessage':      'audio',
    'documentMessage': 'document',
}

_MIME_EXT = {
    'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
    'image/webp': '.webp', 'image/sticker': '.webp',
    'audio/ogg': '.ogg', 'audio/ogg; codecs=opus': '.ogg',
    'audio/mpeg': '.mp3', 'audio/mp4': '.m4a',
    'video/mp4': '.mp4', 'video/webm': '.webm',
    'application/pdf': '.pdf',
}


def _notify_new_open_conversation(conversation, connection) -> None:
    """Envia notificação WhatsApp quando um novo chamado aberto chega.

    Avisa UMA ÚNICA VEZ por chamado (marca conversation.notif_aberto_enviada).
    Sem isso, cada nova mensagem do cliente num chamado sem atendente gerava
    uma notificação nova — virando spam no grupo.

    Chamado de contato restrito NÃO é avisado: a mensagem vai para um grupo do
    WhatsApp que a equipe inteira lê, e ela carrega o nome do contato — seria
    justamente o vazamento que a restrição quer evitar. Quem tem acesso
    continua vendo o chamado na tela do sistema."""
    try:
        from .models import SystemSetting
        if SystemSetting.get('notif_abertos_enabled', 'false') != 'true':
            return
        if conversation.group_id and conversation.group.restrito:
            return
        # Já avisado uma vez — não repete a cada mensagem do cliente
        if getattr(conversation, 'notif_aberto_enviada', False):
            return
        group_id = SystemSetting.get('notif_abertos_group_id', '').strip()
        if not group_id:
            return
        from .models import ContactGroup as _CG
        _group = _CG.objects.filter(id=group_id).select_related('connection').first()
        if not _group or not _group.connection:
            return
        notif_jid = _group.jid
        connection = _group.connection
        texto = (
            f"🔔 *Novo chamado em aberto!*\n\n"
            f"📱 Grupo: *{conversation.group.name}*\n\n"
            f"Acesse o sistema para assumir o chamado."
        )
        # send_text retorna (bool, msg_id) — checar a tupla inteira com
        # `if ok:` seria sempre verdadeiro (tupla de 2 nunca é vazia),
        # marcando o alerta como enviado mesmo se o envio falhasse de verdade.
        ok, _msg_id = EvolutionAPIClient(connection).send_text(notif_jid, texto, everyone=True)
        if ok:
            conversation.notif_aberto_enviada = True
            conversation.save(update_fields=['notif_aberto_enviada'])
    except Exception as e:
        logger.warning(f"Falha ao enviar notificação de novo chamado: {e}")


def aprender_nome_participante(connection, participant_jid: str, push_name: str) -> None:
    """Guarda o `pushName` de quem escreveu no grupo, para o autocomplete do
    "@" conseguir mostrar nome em vez de número.

    É chamada em toda mensagem de grupo recebida, então nunca pode custar
    caro: um cache em memória de 12h evita reescrever a mesma linha a cada
    mensagem da mesma pessoa — só vai ao banco quando o nome muda (ou quando
    o cache esfria). Falha aqui é irrelevante para a mensagem em si, então
    qualquer erro é engolido com log.
    """
    if not connection or not participant_jid or not push_name:
        return
    nome = push_name.strip()[:255]
    jid = participant_jid.strip()[:100]
    if not nome or not jid:
        return
    from django.core.cache import cache
    chave = f'gmn_{connection.id}_{jid}'
    if cache.get(chave) == nome:
        return
    try:
        GroupMemberName.objects.update_or_create(
            connection=connection, jid=jid, defaults={'name': nome}
        )
        cache.set(chave, nome, 43200)
    except Exception as e:
        logger.debug(f"Falha ao registrar nome de participante {jid}: {e}")


def completar_nomes_participantes(connection, participantes: List[Dict]) -> List[Dict]:
    """Preenche o `nome` de cada participante do grupo cruzando três fontes,
    da mais confiável para a menos.

    A lista que a Evolution devolve em `/group/participants` vem quase toda
    com `name: null` — é por isso que o autocomplete do "@" mostrava só
    telefone e ninguém sabia quem era quem. Aqui cada participante ainda sem
    nome é procurado em:

    1. contatos da instância (`/chat/findContacts`) — cobre quem está salvo
       na agenda do WhatsApp ou já teve o pushName visto pela Evolution;
    2. `GroupMemberName` — nomes que o próprio CRM aprendeu do `pushName` de
       quem já escreveu em algum grupo;
    3. `AttendantContact` — números da nossa equipe, que viram o nome do
       usuário do CRM.

    Quem continuar sem nome sai com `nome` vazio de propósito: a tela mostra
    o número formatado, em vez de repetir o telefone como se fosse nome.
    """
    if not participantes:
        return participantes

    faltando = [p for p in participantes if not p.get('nome')]
    if not faltando:
        return participantes

    def _chaves(p):
        """Todo jeito conhecido de identificar o mesmo participante."""
        phone = p.get('phone') or ''
        lid = p.get('lid') or ''
        # Jids completos primeiro: os números puros são ambíguos (um `lid` é
        # só dígitos, igual a um telefone) e servem só de último recurso,
        # para versões da Evolution que devolvem o id sem o sufixo.
        return [k for k in (lid, f'{phone}@s.whatsapp.net',
                            lid.split('@')[0] if lid else '', phone) if k]

    # 1. Contatos da instância — uma chamada só, em cache de 10 min.
    if connection:
        from django.core.cache import cache
        chave_cache = f'evo_contatos_{connection.id}'
        contatos = cache.get(chave_cache)
        if contatos is None:
            contatos = EvolutionAPIClient(connection).get_contacts_map()
            cache.set(chave_cache, contatos, 600 if contatos else 60)
        for p in faltando:
            for k in _chaves(p):
                if contatos.get(k):
                    p['nome'] = contatos[k]
                    break

    # 2. Nomes aprendidos das mensagens dos grupos.
    faltando = [p for p in faltando if not p.get('nome')]
    if faltando and connection:
        chaves = {k for p in faltando for k in _chaves(p)}
        aprendidos = dict(
            GroupMemberName.objects.filter(connection=connection, jid__in=chaves)
            .values_list('jid', 'name')
        )
        if aprendidos:
            for p in faltando:
                for k in _chaves(p):
                    if aprendidos.get(k):
                        p['nome'] = aprendidos[k]
                        break

    # 3. Nossa própria equipe: número do atendente vira o nome dele no CRM.
    faltando = [p for p in faltando if not p.get('nome')]
    if faltando:
        from .models import AttendantContact, normalizar_telefone_br
        equipe = {}
        for ac in AttendantContact.objects.select_related('user').all():
            chave = normalizar_telefone_br(ac.phone)
            if chave:
                equipe[chave] = ac.user.get_full_name() or ac.user.username
        if equipe:
            for p in faltando:
                nome = equipe.get(normalizar_telefone_br(p.get('phone') or ''))
                if nome:
                    p['nome'] = nome

    return participantes


def _detectar_atendente_pessoal(participant_jid: str):
    """Se `participant_jid` (remetente real de uma mensagem de grupo, vindo
    do campo `participant` do webhook) corresponder ao telefone pessoal de
    algum atendente cadastrado em Contatos Atendentes, retorna esse
    AttendantContact. Caso contrário, retorna None (mensagem é do cliente).

    Compara com normalizar_telefone_br (tolera com/sem o nono dígito) em vez
    de match exato — números BR cadastrados sem o 9 (erro comum de digitação)
    faziam essa detecção nunca bater, deixando o alerta de "atendente no
    pessoal" silenciosamente nunca disparar."""
    if not participant_jid:
        return None
    phone = participant_jid.split('@')[0].strip()
    if not phone:
        return None
    from .models import AttendantContact, normalizar_telefone_br
    alvo = normalizar_telefone_br(phone)
    if not alvo:
        return None
    for ac in AttendantContact.objects.select_related('user').all():
        if normalizar_telefone_br(ac.phone) == alvo:
            return ac
    return None


def _alertar_atendente_pessoal(conversation, group, attendant_contact, connection) -> None:
    """Avisa o grupo do NOC (mesmo grupo de 'chamados abertos') que um
    atendente está respondendo este chamado pelo WhatsApp pessoal em vez da
    plataforma. Avisa UMA ÚNICA VEZ por chamado (personal_wa_alert_sent)."""
    try:
        from .models import SystemSetting
        if getattr(conversation, 'personal_wa_alert_sent', False):
            return
        group_id = SystemSetting.get('notif_abertos_group_id', '').strip()
        if not group_id:
            return
        from .models import ContactGroup as _CG
        _alert_group = _CG.objects.filter(id=group_id).select_related('connection').first()
        if not _alert_group or not _alert_group.connection:
            return
        # Marca o atendente via mentionedJid (contextInfo do WhatsApp) — ele é
        # notificado/destacado no grupo mesmo sem o número aparecer no texto;
        # a marcação não depende de ter "@numero" escrito na mensagem.
        nome_atendente = attendant_contact.user.get_full_name() or attendant_contact.user.username
        texto = (
            f"⚠️ *Atenção!*\n\n"
            f"*{nome_atendente}* está respondendo o chamado do grupo "
            f"*{group.name}* pelo WhatsApp pessoal, não pela plataforma.\n\n"
            f"Solicitamos que responda pela plataforma, mantendo o registro "
            f"profissional do atendimento."
        )
        # send_text retorna (bool, msg_id) — checar a tupla inteira com
        # `if ok:` seria sempre verdadeiro (tupla de 2 nunca é vazia),
        # marcando o alerta como enviado mesmo se o envio falhasse de verdade.
        ok, _msg_id = EvolutionAPIClient(_alert_group.connection).send_text(
            _alert_group.jid, texto, mentions=[attendant_contact.phone]
        )
        if ok:
            conversation.personal_wa_alert_sent = True
            conversation.save(update_fields=['personal_wa_alert_sent'])
    except Exception as e:
        logger.warning(f"Falha ao alertar sobre atendente pessoal: {e}")


_TAREFA_VERBOS = ('abrir', 'abre', 'abra', 'criar', 'crie', 'cria', 'gerar', 'gere', 'gera', 'nova', 'novo')


def _normalizar_texto(texto: str) -> str:
    """Remove acentos pra comparação tolerante a erro de digitação — caso
    real: "Tomichinho, criar tarefá de configuração..." (com acento errado
    em "tarefa") não batia com o literal "tarefa" e não disparava nada.
    "tarefá"/"tarefâ"/etc. viram "tarefa" antes de qualquer checagem."""
    nfkd = unicodedata.normalize('NFKD', texto)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def _pede_abertura_de_tarefa(texto: str) -> bool:
    """"Abrir tarefa" era a única frase reconhecida — "Tomichinho, criar
    tarefa" (pedido real de um atendente) não disparava nada, porque não
    contém esse literal exato. Em vez de uma frase fixa, aceita qualquer
    mensagem com a palavra "tarefa" perto de um verbo de criação comum
    (abrir/criar/gerar/nova) — cobre a forma como as pessoas realmente
    escrevem o pedido, não só uma frase engessada."""
    if not texto:
        return False
    t = _normalizar_texto(texto.lower())
    return 'tarefa' in t and any(v in t for v in _TAREFA_VERBOS)


_FECHAR_VERBOS = ('fechar', 'feche', 'fecha', 'encerrar', 'encerre', 'encerra',
                  'finalizar', 'finalize', 'finaliza', 'concluir', 'conclua', 'conclui')
_FECHAR_ALVOS = ('chamado', 'atendimento', 'ticket', 'protocolo')
# "nao pode fechar o chamado ainda", "nao vamos encerrar o atendimento": a
# negação até 3 palavras antes do verbo inverte o pedido — fechar aqui seria
# o oposto do que foi dito.
_FECHAR_NEGADO = _re.compile(
    r'\bnao\b(?:\s+\S+){0,3}\s+(?:%s)' % '|'.join(_FECHAR_VERBOS))


def _pede_fechamento_de_chamado(texto: str) -> bool:
    """Mesma ideia de `_pede_abertura_de_tarefa`, mas para o encerramento:
    aceita qualquer mensagem com uma palavra que nomeie o chamado
    (chamado/atendimento/ticket/protocolo) perto de um verbo de fechamento
    (fechar/encerrar/finalizar/concluir).

    É de propósito mais exigente que o gatilho de tarefa — fechar o chamado
    errado é bem mais caro que abrir uma tarefa a mais. Por isso pede o alvo
    explícito (um "pode fechar" solto, falando de outra coisa, não encerra
    nada; "fechar a tarefa" também não), só aceita verbo de ação (não o
    adjetivo em "o chamado ainda não está resolvido") e ignora o pedido
    negado.
    """
    if not texto:
        return False
    t = _normalizar_texto(texto.lower())
    if not (any(a in t for a in _FECHAR_ALVOS) and any(v in t for v in _FECHAR_VERBOS)):
        return False
    return not _FECHAR_NEGADO.search(t)


def _disparar_acoes_ia(conversation, content, is_internal=False) -> None:
    """Gatilhos de AÇÃO do agente IA: abrir tarefa e fechar o chamado com a
    resolução. Rodam em background (Celery) — nunca bloqueiam o envio nem
    dependem da IA responder a tempo.

    Vale para os três caminhos em que uma mensagem entra no chamado:
    recebida do WhatsApp (`_disparar_agente_ia`), digitada na caixa normal
    do chat pela plataforma e digitada como comentário interno. Os dois
    últimos passam por `ConversationService.send_message`, que antes só
    olhava os gatilhos em nota interna — "Tomichinho fechar atendimento"
    escrito na caixa normal do chat não fazia nada.

    `is_internal` segue pra task: o que começou como comentário privado da
    equipe não pode gerar resposta no WhatsApp do cliente.
    """
    texto = _normalizar_texto((content or '').lower())
    if _pede_abertura_de_tarefa(texto):
        from .tasks import abrir_tarefa_ia
        abrir_tarefa_ia.delay(str(conversation.id), content, is_internal)
    # Chamado já encerrado não reabre nem refaz resolução — e a própria
    # "Mensagem de encerramento" das configurações ("Finalizamos seu
    # atendimento...") passa por aqui depois do fechamento, então sem esta
    # guarda ela enfileiraria uma task só pra ser descartada lá dentro.
    if _pede_fechamento_de_chamado(texto) and conversation.status not in ('resolved', 'closed'):
        from .tasks import fechar_chamado_ia
        fechar_chamado_ia.delay(str(conversation.id), content, is_internal)


def _disparar_agente_ia(conversation, content) -> None:
    """Checa gatilhos de texto do agente IA "Tomichinho" numa mensagem recém
    recebida e dispara a ação correspondente em background (Celery) — nunca
    bloqueia o webhook nem depende da IA responder a tempo.

    Qualquer remetente aciona (atendente ou cliente): "tomichinho" pede uma
    resposta da IA no próprio grupo; um pedido de tarefa (ver
    `_pede_abertura_de_tarefa`) pede a criação de uma Tarefa vinculada ao
    cliente do grupo; um pedido de fechamento (ver
    `_pede_fechamento_de_chamado`) encerra o chamado com a resolução
    redigida pela IA. Podem disparar juntos na mesma mensagem.
    """
    if 'tomichinho' in _normalizar_texto((content or '').lower()):
        from .tasks import responder_tomichinho
        responder_tomichinho.delay(str(conversation.id))
    _disparar_acoes_ia(conversation, content)


def aplicar_mencoes(texto, mentions):
    """Prepara o texto que vai pro WhatsApp a partir do que o atendente
    escreveu com "@" no chat.

    No CRM a mensagem fica legível ("@João Silva, confere aí?"), mas o
    WhatsApp só destaca a menção quando o corpo traz "@<número>" batendo com
    o `mentioned` do envio — então aqui o nome é trocado pelo número. Devolve
    (texto_para_whatsapp, [números]).

    `mentions` é uma lista de {'nome', 'phone'} vinda do autocomplete: são os
    pares que o próprio atendente escolheu, não um palpite sobre o texto.
    """
    if not mentions:
        return texto, []
    numeros = []
    # Nomes mais longos primeiro: com "João" e "João Silva" na mesma conversa,
    # trocar o curto antes deixaria "@5511... Silva" no meio da frase.
    for m in sorted(mentions, key=lambda m: len(m.get('nome') or ''), reverse=True):
        nome = (m.get('nome') or '').strip()
        phone = _re.sub(r'\D', '', str(m.get('phone') or ''))
        if not phone:
            continue
        if nome and f'@{nome}' in texto:
            texto = texto.replace(f'@{nome}', f'@{phone}')
        if phone not in numeros:
            numeros.append(phone)
    return texto, numeros


def _ia_enviar(conversation, group, connection, texto, sender_name='Tomichinho',
               sender_type='ai', enviar_whatsapp=True) -> None:
    """Salva uma mensagem do agente IA e (se `enviar_whatsapp`) manda pro
    WhatsApp do grupo — mesmo padrão de `_flow_enviar`. Usado tanto pra
    resposta a "tomichinho"/confirmação de tarefa vinda do WhatsApp
    (`enviar_whatsapp=True`, sender_type='ai') quanto pra confirmação de
    tarefa aberta a partir de uma NOTA INTERNA (`enviar_whatsapp=False`,
    sender_type='internal') — essa última não pode vazar pro cliente."""
    if not texto or not conversation:
        return
    import uuid as _uuid
    msg = Message.objects.create(
        conversation=conversation,
        sender_type=sender_type,
        sender_name=sender_name,
        message_type='text',
        content=texto,
        external_id='ia_%s' % _uuid.uuid4().hex,
        is_internal=(sender_type == 'internal'),
    )
    if enviar_whatsapp:
        conversation.last_message_at = timezone.now()
        conversation.save(update_fields=['last_message_at'])
        try:
            EvolutionAPIClient(connection).send_text(group.jid, texto)
        except Exception as e:
            logger.warning(f"Falha ao enviar resposta do agente IA: {e}")
    ConversationService._broadcast_msg(conversation, group, msg, inbox=False)


def finalizar_conversa(conversation, resolution=None, actor=None,
                       status='resolved', enviar_encerramento=True) -> None:
    """Encerra um chamado: grava status/resolução, registra a atividade,
    avisa a caixa de entrada por WebSocket, (opcionalmente) manda a mensagem
    de encerramento configurada ao cliente e deixa o marco de conclusão com o
    protocolo no histórico interno.

    Existe para que a tela (`api_update_conversation`) e o agente IA
    (`fechar_chamado_ia`) fechem chamado exatamente do mesmo jeito — antes
    isso vivia só dentro da view.

    `enviar_encerramento=False` é o caso do fechamento pedido em nota
    interna: nada pode sair pro WhatsApp do cliente.
    """
    from .models import SystemSetting

    old_status = conversation.status
    conversation.status = status
    conversation.closed_at = timezone.now()
    campos = ['status', 'closed_at']
    if resolution:
        conversation.resolution = resolution.strip()
        campos.append('resolution')
    conversation.save(update_fields=campos)

    ConversationActivity.objects.create(
        conversation=conversation, actor=actor, action='status_changed',
        old_value=old_status, new_value=status,
    )

    # Notifica a caixa de entrada em tempo real ANTES de qualquer I/O externo
    # (WhatsApp) — a conversa some das listas (bolhas "assumidas", sidebar,
    # abas) sem esperar a Evolution API responder.
    try:
        _ws_send_inbox({
            'type': 'conversation_status',
            'conversation_id': str(conversation.id),
            'status': conversation.status,
            'assigned_to_id': conversation.assigned_to_id,
        })
    except Exception as _e:
        logger.warning(f"Falha ao notificar inbox (status): {_e}")

    if enviar_encerramento:
        closing_msg = SystemSetting.get('msg_encerramento', '').strip()
        if closing_msg:
            try:
                ConversationService.send_message(conversation, closing_msg, actor)
            except Exception as _e:
                logger.warning(f"Falha ao enviar msg de encerramento: {_e}")

    # Marco de conclusão com o número do protocolo: fica SÓ no histórico
    # interno da conversa — o grupo do cliente não recebe nada. Antes isso ia
    # pro WhatsApp via EvolutionAPI (numa thread em background) e só era
    # gravado se o envio desse certo; hoje é gravação direta, sem I/O externo
    # e sem depender da API estar de pé. Quem quiser avisar o cliente no
    # fechamento usa a "Mensagem de encerramento" das configurações.
    texto_conclusao = (
        f"✅ Chamado concluído!\n"
        f"📋 Protocolo: #{conversation.conversation_id}"
    )
    try:
        msg_conclusao = Message.objects.create(
            conversation=conversation, sender_type='system',
            sender_name='Sistema', message_type='text', content=texto_conclusao,
            external_id=f'concluido_{uuid.uuid4().hex}',
        )
        # Sem WS a linha só apareceria ao recarregar a conversa.
        ConversationService._broadcast_msg(
            conversation, conversation.group, msg_conclusao, inbox=False)
    except Exception as _e:
        logger.warning(f"Falha ao registrar conclusão (conv {conversation.id}): {_e}")


# ── SLA (tempo de resposta/resolução) ────────────────────────────────────────
# Prazos padrão por prioridade, em minutos. Sobrescrevíveis via SystemSetting
# (chaves 'sla_response_<priority>' / 'sla_resolution_<priority>').
SLA_DEFAULTS_MINUTOS = {
    'urgent': {'response': 15, 'resolution': 240},    # 15 min / 4h
    'high':   {'response': 30, 'resolution': 480},    # 30 min / 8h
    'medium': {'response': 120, 'resolution': 1440},  # 2h / 24h
    'low':    {'response': 240, 'resolution': 2880},  # 4h / 48h
}


def get_sla_minutos(priority: str) -> Tuple[int, int]:
    """Retorna (minutos_resposta, minutos_resolucao) para a prioridade, com
    override via SystemSetting."""
    from .models import SystemSetting
    defaults = SLA_DEFAULTS_MINUTOS.get(priority, SLA_DEFAULTS_MINUTOS['medium'])
    try:
        resp = int(SystemSetting.get(f'sla_response_{priority}', '') or defaults['response'])
    except (ValueError, TypeError):
        resp = defaults['response']
    try:
        reso = int(SystemSetting.get(f'sla_resolution_{priority}', '') or defaults['resolution'])
    except (ValueError, TypeError):
        reso = defaults['resolution']
    return resp, reso


def aplicar_sla(conversation, from_time=None):
    """Calcula e grava os prazos de SLA (resposta/resolução) com base na
    prioridade atual da conversa. Não salva — quem chama decide quando salvar."""
    base = from_time or timezone.now()
    resp_min, reso_min = get_sla_minutos(conversation.priority)
    conversation.sla_response_due_at = base + timedelta(minutes=resp_min)
    conversation.sla_resolution_due_at = base + timedelta(minutes=reso_min)


def _save_media_file(b64_data: str, mimetype: str) -> str:
    """Decodifica base64, grava em MEDIA_ROOT e retorna a URL relativa."""
    ext = _MIME_EXT.get(mimetype.split(';')[0].strip(), '.bin')
    filename = str(uuid.uuid4()) + ext
    dir_path = os.path.join(settings.MEDIA_ROOT, 'atendimento', 'media')
    os.makedirs(dir_path, exist_ok=True)
    with open(os.path.join(dir_path, filename), 'wb') as f:
        f.write(base64.b64decode(b64_data))
    return f"{settings.MEDIA_URL}atendimento/media/{filename}"


def _remover_arquivo_de_midia(message) -> None:
    """Apaga do disco o arquivo de uma mensagem de mídia excluída.

    Sem isto a exclusão seria só de fachada: a linha some da conversa, mas o
    arquivo continua servido em `attachment_url` para quem tiver o link — e
    "mandei o documento errado" é justamente o motivo mais comum de apagar.

    Silencioso por opção: a mensagem já foi apagada no WhatsApp e marcada no
    CRM quando chegamos aqui, e falhar em remover um arquivo (já removido,
    permissão, caminho fora do MEDIA_ROOT) não pode desfazer nada disso.
    """
    url = getattr(message, 'attachment_url', '') or ''
    if not url or not url.startswith(settings.MEDIA_URL):
        return
    try:
        relativo = url[len(settings.MEDIA_URL):]
        caminho = os.path.normpath(os.path.join(settings.MEDIA_ROOT, relativo))
        # Guarda contra "../" num attachment_url adulterado: só apaga dentro
        # do MEDIA_ROOT.
        raiz = os.path.normpath(settings.MEDIA_ROOT)
        if not caminho.startswith(raiz + os.sep):
            logger.warning(f"attachment_url fora do MEDIA_ROOT, não removido: {url}")
            return
        if os.path.isfile(caminho):
            os.remove(caminho)
    except Exception as e:
        logger.warning(f"Falha ao remover mídia de mensagem apagada ({url}): {e}")


def _read_attachment_as_base64(attachment_url: str) -> str:
    """Lê de volta um arquivo salvo por _save_media_file e devolve em
    base64 — usado pra reenviar a mídia de uma mensagem agendada, que só
    guarda a URL (não o base64) enquanto espera a hora de enviar."""
    if not attachment_url.startswith(settings.MEDIA_URL):
        raise ValueError(f"attachment_url fora de MEDIA_URL: {attachment_url}")
    relative = attachment_url[len(settings.MEDIA_URL):]
    abs_path = os.path.join(settings.MEDIA_ROOT, relative)
    with open(abs_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('ascii')


def _ws_send_conversation(conversation_id: str, data: dict):
    """Envia evento para o grupo WebSocket da conversa (thread-safe)."""
    try:
        async_to_sync(channel_layer.group_send)(
            f"atendimento_conv_{conversation_id}",
            {"type": "chat.message", "data": data},
        )
    except Exception as e:
        logger.warning(f"WebSocket send falhou (conv {conversation_id}): {e}")


def _ws_send_inbox(data: dict):
    """Envia evento para o grupo da caixa de entrada."""
    try:
        async_to_sync(channel_layer.group_send)(
            "atendimento_inbox",
            {"type": "inbox.update", "data": data},
        )
    except Exception as e:
        logger.warning(f"WebSocket send falhou (inbox): {e}")


def notify_reassignment(conversation: Conversation, old_assigned_to_id):
    """Avisa a caixa de entrada (WS) que um chamado mudou de atendente —
    seja por "Assumir", auto-atribuição ao responder, transferência manual
    ou reatribuição automática por SLA. Sem isso, o novo/antigo responsável
    só via a mudança em "Assumidos" depois de recarregar a página."""
    if old_assigned_to_id == conversation.assigned_to_id:
        return
    _ws_send_inbox({
        "type": "conversation_reassigned",
        "conversation_id": str(conversation.id),
        "group_name": conversation.group.name if conversation.group else "",
        "old_assigned_to_id": old_assigned_to_id,
        "assigned_to_id": conversation.assigned_to_id,
    })


def _nomes_de_contato(msg_content: dict) -> str:
    """Nome(s) do(s) contato(s) compartilhado(s) via vCard, ou string vazia.

    Sem isto, compartilhar um contato no grupo virava um balão "[sem conteúdo]" —
    o texto útil (displayName) fica dentro de contactMessage/contactsArrayMessage,
    que a extração de texto não olhava.
    """
    unico = msg_content.get("contactMessage") or {}
    if unico.get("displayName"):
        return f"👤 {unico['displayName']}"

    varios = (msg_content.get("contactsArrayMessage") or {}).get("contacts") or []
    nomes = [c.get("displayName") for c in varios if c.get("displayName")]
    if nomes:
        return "👤 " + ", ".join(nomes)

    return ""


def _extrair_reacao(msg_content: dict):
    """Detecta reação a uma mensagem e devolve (id_da_mensagem_alvo, emoji).

    São dois formatos e a diferença importa:

    - `reactionMessage`: emoji em texto puro. É o que chega quando alguém
      reage a uma mensagem de outro participante.
    - `secretEncryptedMessage`: mesma coisa, porém criptografada. É o que
      chega quando reagem a uma mensagem que *nós* enviamos (`targetMessageKey.
      fromMe = true`). O emoji é cifrado com o messageSecret da mensagem
      original, que não guardamos, então devolvemos emoji vazio — dá pra
      mostrar que houve reação, não qual foi.

    Emoji vazio no `reactionMessage` significa reação *removida* (o WhatsApp
    manda texto vazio pra desfazer), e isso é diferente de "não sei o emoji".
    Por isso o retorno distingue os dois via `cifrada`.
    """
    reaction = msg_content.get("reactionMessage")
    if reaction:
        alvo = (reaction.get("key") or {}).get("id")
        if alvo:
            return {"alvo": alvo, "emoji": reaction.get("text") or "", "cifrada": False}

    secreta = msg_content.get("secretEncryptedMessage")
    if secreta:
        alvo = (secreta.get("targetMessageKey") or {}).get("id")
        if alvo:
            return {"alvo": alvo, "emoji": "", "cifrada": True}

    return None


def _extrair_edicao(msg_content: dict):
    """Detecta que o WhatsApp mandou a EDIÇÃO de uma mensagem já recebida e
    devolve {'alvo': id_da_original, 'texto': novo_texto}.

    Quando alguém edita no celular, o que chega não é uma mensagem nova: é um
    `protocolMessage` do tipo MESSAGE_EDIT carregando a `key` da original e o
    texto novo. Sem tratar isso, cai no extrator de conteúdo, não bate com
    nada e vira um balão "[sem conteúdo]" no meio da conversa — o mesmo
    estrago que as reações faziam antes de terem tratamento próprio.

    O formato varia entre versões (o `protocolMessage` pode vir na raiz ou
    dentro de um `editedMessage`), então tenta os dois.
    """
    candidatos = [
        msg_content.get("protocolMessage"),
        ((msg_content.get("editedMessage") or {}).get("message") or {}).get("protocolMessage"),
    ]
    for proto in candidatos:
        if not proto:
            continue
        # `type` vem como string nas versões novas e como o enum (14) nas antigas.
        tipo = proto.get("type")
        if tipo not in ("MESSAGE_EDIT", 14):
            continue
        alvo = (proto.get("key") or {}).get("id")
        editada = proto.get("editedMessage") or {}
        texto = (
            editada.get("conversation")
            or (editada.get("extendedTextMessage") or {}).get("text")
            or (editada.get("imageMessage") or {}).get("caption")
            or (editada.get("videoMessage") or {}).get("caption")
            or ""
        )
        if alvo and texto:
            return {"alvo": alvo, "texto": texto}
    return None


def auto_assign_on_reply(conversation: Conversation, agent) -> bool:
    """Atribui a conversa a quem respondeu, quando ela ainda não tem dono.

    Fica aqui (e não na view) porque TODO envio de agente passa por
    ConversationService.send_message/send_media: chat, mídia, mensagem
    agendada e mensagem de encerramento. Quando essa regra morava só na
    view de texto, responder por qualquer um dos outros caminhos deixava
    o chamado sem responsável e ele nunca aparecia em "Assumidos".

    Devolve True se acabou de atribuir (o front usa para trocar o
    cabeçalho de "Assumir" para "Transferir" sem recarregar a página).
    """
    if agent is None or not getattr(agent, "is_authenticated", True):
        return False
    if conversation.assigned_to_id:
        return False

    old_assigned_to_id = conversation.assigned_to_id
    conversation.assigned_to = agent
    conversation.save(update_fields=["assigned_to"])
    ConversationActivity.objects.create(
        conversation=conversation,
        actor=agent,
        action="assigned",
        new_value=agent.get_full_name() or agent.username,
    )
    notify_reassignment(conversation, old_assigned_to_id)
    return True


def _numero_nao_existe(response) -> bool:
    """Detecta o 400 específico da Evolution API que indica número inexistente
    no WhatsApp: {"response": {"message": [{"exists": false, ...}]}}."""
    if response is None or response.status_code != 400:
        return False
    try:
        itens = response.json().get('response', {}).get('message', [])
        return any(item.get('exists') is False for item in itens)
    except Exception:
        return False


def _alternar_nono_digito(jid: str) -> Optional[str]:
    """Alterna a presença do 9º dígito num JID celular BR (55+DDD+[9]+8 dígitos).
    Retorna None se o JID não tiver essa forma (nada a alternar)."""
    if '@' not in jid:
        return None
    numero, dominio = jid.split('@', 1)
    if not numero.startswith('55') or not numero.isdigit():
        return None
    local = numero[4:]  # após 55 + DDD (2 dígitos)
    ddd = numero[2:4]
    if len(local) == 9 and local[0] == '9':
        return f'55{ddd}{local[1:]}@{dominio}'
    if len(local) == 8:
        return f'55{ddd}9{local}@{dominio}'
    return None


class EvolutionAPIClient:
    """Cliente para Evolution API v2"""

    def __init__(self, connection: WhatsAppConnection):
        self.connection = connection
        self.base_url = connection.evolution_url.rstrip('/')
        self.api_key = connection.api_key
        self.instance = connection.instance_name
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'apikey': self.api_key,
        })

    def _get(self, path: str, params=None, timeout=15) -> requests.Response:
        return self.session.get(f"{self.base_url}{path}", params=params, timeout=timeout)

    def _post(self, path: str, payload=None, timeout=30) -> requests.Response:
        return self.session.post(f"{self.base_url}{path}", json=payload or {}, timeout=timeout)

    def _delete(self, path: str, payload=None, timeout=30) -> requests.Response:
        # A Evolution espera o corpo num DELETE (não é query string) — é o
        # formato do `deleteMessageForEveryone`.
        return self.session.delete(f"{self.base_url}{path}", json=payload or {}, timeout=timeout)

    # ── Conectividade ────────────────────────────────────────────────────────

    def test_connection(self) -> Tuple[bool, str]:
        """Testa conexão verificando o estado da instância"""
        try:
            # Evolution API v2: GET /instance/connectionState/{instance}
            r = self._get(f"/instance/connectionState/{self.instance}")
            if r.status_code == 404:
                return False, f"Instância '{self.instance}' não encontrada (404). Verifique o nome da instância."
            r.raise_for_status()
            data = r.json()
            state = data.get('instance', {}).get('state') or data.get('state', 'unknown')
            if state == 'open':
                return True, f"Conectado — instância '{self.instance}' online"
            # Instância existe mas não está com sessão WhatsApp ativa (ex:
            # 'close' = desconectada, precisa reescanear o QR code) — antes
            # isso retornava sucesso, escondendo exatamente o motivo pelo
            # qual os envios de mensagem falham com 400.
            return False, f"Instância '{self.instance}' encontrada mas desconectada (estado: {state}) — reconecte escaneando o QR code."
        except requests.HTTPError as e:
            return False, f"HTTP {e.response.status_code}: {e.response.text[:200]}"
        except Exception as e:
            return False, str(e)

    # ── Grupos ───────────────────────────────────────────────────────────────

    def fetch_all_groups(self) -> List[Dict]:
        """Busca todos os grupos da instância"""
        try:
            # Endpoint primário: GET /group/fetchAllGroups/{instance}
            r = self._get(
                f"/group/fetchAllGroups/{self.instance}",
                params={"getParticipants": "false"},
                timeout=60,
            )
            if r.ok:
                data = r.json() or []
                if isinstance(data, list) and data:
                    return data

            # Fallback: POST /chat/findChats/{instance}
            r2 = self._post(f"/chat/findChats/{self.instance}", {})
            if r2.ok:
                chats = r2.json() or []
                return [
                    {
                        "id": c.get("remoteJid") or c.get("id"),
                        "subject": c.get("pushName") or c.get("name") or c.get("remoteJid"),
                        "pictureUrl": c.get("profilePicUrl"),
                    }
                    for c in chats
                    if (c.get("remoteJid") or c.get("id", "")).endswith("@g.us")
                ]
        except Exception as e:
            logger.error(f"Erro ao buscar grupos [{self.instance}]: {e}")
        return []

    def get_group_info(self, jid: str) -> Optional[Dict]:
        """Obtém informações detalhadas de um grupo"""
        try:
            r = self._get(
                f"/group/findGroupInfos/{self.instance}",
                params={"groupJid": jid},
            )
            if r.ok:
                return r.json()
        except Exception as e:
            logger.warning(f"Erro ao obter info do grupo {jid}: {e}")
        return None

    # ── Mensagens ────────────────────────────────────────────────────────────

    def get_group_participants(self, group_jid: str) -> List[str]:
        """Retorna lista de números de telefone dos participantes do grupo."""
        try:
            r = self._get(f"/group/participants/{self.instance}", params={"groupJid": group_jid})
            if r.ok:
                parts = r.json().get("participants", [])
                return [
                    p["phoneNumber"].split("@")[0]
                    for p in parts
                    if p.get("phoneNumber")
                ]
        except Exception as e:
            logger.warning(f"Erro ao buscar participantes do grupo {group_jid}: {e}")
        return []

    def get_group_participants_info(self, group_jid: str) -> List[Dict]:
        """Participantes do grupo para o "@" do chat, com tudo que a Evolution
        souber sobre cada um: número, id interno (`@lid`), nome e foto.

        Cada item traz `phone` (número real), `lid` (identificador que o
        WhatsApp usa hoje dentro do grupo e que também vem no `participant`
        dos webhooks), `nome` (vazio quando a Evolution não sabe — quem
        completa é `completar_nomes_participantes`) e `foto`.

        A Evolution não é consistente no nome dos campos entre versões
        (`phoneNumber`/`id`/`jid`, `name`/`pushName`/`notify`), então lê todos
        e usa o primeiro que vier.
        """
        try:
            r = self._get(f"/group/participants/{self.instance}", params={"groupJid": group_jid})
            if not r.ok:
                return []
            itens = []
            for p in r.json().get("participants", []):
                bruto = p.get("phoneNumber") or p.get("id") or p.get("jid") or ""
                phone = bruto.split("@")[0].split(":")[0].strip()
                if not phone:
                    continue
                # `id` costuma vir como "<id>@lid": é a chave que casa com o
                # `participant` dos webhooks e com o `remoteJid` dos contatos.
                lid = (p.get("id") or "").strip()
                nome = (p.get("name") or p.get("pushName") or p.get("notify")
                        or p.get("verifiedName") or "").strip()
                itens.append({
                    "phone": phone,
                    "lid": lid,
                    "nome": nome,
                    "foto": (p.get("imgUrl") or p.get("pictureUrl") or "").strip(),
                    "admin": bool(p.get("admin")),
                })
            return itens
        except Exception as e:
            logger.warning(f"Erro ao buscar participantes (com nome) do grupo {group_jid}: {e}")
            return []

    def get_contacts_map(self) -> Dict[str, str]:
        """Mapa jid -> nome de todos os contatos que a instância conhece.

        `/group/participants` devolve `name` nulo para quase todo mundo, mas
        `/chat/findContacts` traz o `pushName` que a instância já viu — é o
        que transforma a lista do "@" de números crus em gente identificável.
        Uma chamada só cobre o grupo inteiro (é a lista completa da
        instância), então sai bem mais barato que consultar contato a
        contato.

        As chaves saem em dois formatos, porque o participante pode ser
        identificado por qualquer um deles: o jid como veio
        ("...@lid"/"...@s.whatsapp.net") e o número puro.
        """
        try:
            r = self._post(f"/chat/findContacts/{self.instance}", {})
            if not r.ok:
                return {}
            mapa = {}
            for c in r.json() or []:
                jid = (c.get("remoteJid") or c.get("id") or "").strip()
                nome = (c.get("pushName") or c.get("name") or "").strip()
                if not jid or not nome or jid.endswith("@g.us"):
                    continue
                mapa[jid] = nome
                mapa.setdefault(jid.split("@")[0], nome)
            return mapa
        except Exception as e:
            logger.warning(f"Erro ao buscar contatos da instância {self.instance}: {e}")
            return {}

    def edit_text(self, jid: str, message_id: str, text: str) -> Tuple[bool, str]:
        """Reescreve uma mensagem já entregue no WhatsApp. Retorna (ok, erro).

        Evolution 2.x expõe isso em `POST /chat/updateMessage/{instance}` com
        a `key` da mensagem original — o mesmo `id` que guardamos em
        `Message.external_id` depois do envio. `fromMe: True` porque o
        WhatsApp só deixa editar mensagem própria.

        Não existe `mentioned` aqui, ao contrário do `send_text`: o
        `updateMessageSchema` da Evolution só carrega `number`, `text` e
        `key`, e o controller ignora qualquer outro campo. Ou seja, **editar
        não dispara notificação de menção** — o texto pode ganhar um
        "@número", mas ninguém é avisado por causa da edição. Mandar o campo
        assim mesmo só daria a impressão de que funciona.

        O WhatsApp recusa edição fora da janela dele (15 min) e em mensagem
        que não seja de texto; nesses casos a Evolution responde 400 com
        "Message not compatible". O chamador já barra os dois casos antes de
        chegar aqui — isto aqui é a última linha de defesa, e devolve o
        motivo em vez de estourar.
        """
        body = {
            "number": jid,
            "text": text,
            "key": {"id": message_id, "remoteJid": jid, "fromMe": True},
        }
        try:
            r = self._post(f"/chat/updateMessage/{self.instance}", body)
            if r.ok:
                return True, ""
            detalhe = ""
            try:
                resposta = r.json().get("response", {}).get("message")
                detalhe = "; ".join(resposta) if isinstance(resposta, list) else str(resposta or "")
            except Exception:
                detalhe = r.text[:200]
            logger.error(f"Erro ao editar mensagem {message_id} em {jid}: {r.status_code} {detalhe}")
            return False, detalhe or f"Evolution respondeu {r.status_code}"
        except Exception as e:
            logger.error(f"Erro ao editar mensagem {message_id} em {jid}: {e}")
            return False, str(e)

    def delete_message(self, jid: str, message_id: str, participant: str = "") -> Tuple[bool, str]:
        """Apaga para todos uma mensagem já entregue. Retorna (ok, erro).

        Evolution 2.x: `DELETE /chat/deleteMessageForEveryone/{instance}`. O
        corpo aqui é **plano** (`id`, `remoteJid`, `fromMe` no topo), ao
        contrário do `updateMessage`, que aninha os mesmos campos dentro de
        `key` — schema confirmado contra a instância 2.3.7 em produção.
        Mandar no formato errado devolve 400 reclamando das três propriedades.

        `participant` só existe em grupo (quem enviou dentro do grupo); em
        conversa 1:1 vai vazio e o campo é omitido.

        Ao contrário da edição, o WhatsApp aceita apagar **mídia** também, e a
        janela de "apagar para todos" é bem mais longa que os 15 min da
        edição — quem decide é ele, e o motivo da recusa volta para a tela.
        """
        body = {"id": message_id, "remoteJid": jid, "fromMe": True}
        if participant:
            body["participant"] = participant
        try:
            r = self._delete(f"/chat/deleteMessageForEveryone/{self.instance}", body)
            if r.ok:
                return True, ""
            detalhe = ""
            try:
                resposta = r.json().get("response", {}).get("message")
                detalhe = "; ".join(map(str, resposta)) if isinstance(resposta, list) else str(resposta or "")
            except Exception:
                detalhe = r.text[:200]
            logger.error(f"Erro ao apagar mensagem {message_id} em {jid}: {r.status_code} {detalhe}")
            return False, detalhe or f"Evolution respondeu {r.status_code}"
        except Exception as e:
            logger.error(f"Erro ao apagar mensagem {message_id} em {jid}: {e}")
            return False, str(e)

    def send_text(self, jid: str, text: str, mentions: List[str] = None,
                  everyone: bool = False) -> Tuple[bool, str]:
        """Envia mensagem de texto. Retorna (sucesso, message_id_evolution).
        everyone=True: passa os números no campo 'mentioned' (todos recebem notificação)
        sem poluir o corpo da mensagem com @número.
        """
        body = {"number": jid, "text": text}
        if everyone and jid.endswith("@g.us"):
            numbers = self.get_group_participants(jid)
            if numbers:
                body["mentioned"] = numbers
            else:
                body["everyOne"] = True
        elif mentions:
            body["mentioned"] = mentions
        try:
            r = self._post(f"/message/sendText/{self.instance}", body)
            r.raise_for_status()
            msg_id = r.json().get("key", {}).get("id") or ""
            return True, msg_id
        except requests.HTTPError as e:
            # BR: alguns números existem no WhatsApp só com o nono dígito e
            # outros só sem ele (contas antigas/portadas) — quem cadastrou o
            # contato não tem como saber qual variante está registrada. A
            # Evolution API responde 400 com "exists": false quando o JID
            # exato não existe; nesse caso tenta a variante alternada do 9º
            # dígito antes de desistir, em vez de forçar o usuário a
            # descobrir manualmente.
            jid_alt = _numero_nao_existe(e.response) and _alternar_nono_digito(jid)
            if jid_alt:
                logger.warning(
                    f"Envio p/ {jid} recusado (número não existe) — tentando variante do 9º dígito: {jid_alt}"
                )
                try:
                    body["number"] = jid_alt
                    r2 = self._post(f"/message/sendText/{self.instance}", body)
                    r2.raise_for_status()
                    msg_id = r2.json().get("key", {}).get("id") or ""
                    return True, msg_id
                except Exception as e2:
                    logger.error(f"Erro ao enviar texto para {jid} (variante {jid_alt} também falhou): {e2}")
                    return False, ""
            logger.error(f"Erro ao enviar texto para {jid}: {e}")
            return False, ""
        except Exception as e:
            logger.error(f"Erro ao enviar texto para {jid}: {e}")
            return False, ""

    def send_media(self, jid: str, mediatype: str, media_b64: str,
                   filename: str = "arquivo", caption: str = "") -> bool:
        """Envia mídia (imagem, documento, vídeo)"""
        try:
            r = self._post(f"/message/sendMedia/{self.instance}", {
                "number": jid,
                "mediatype": mediatype,
                "media": media_b64,
                "fileName": filename,
                "caption": caption or "",
            })
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Erro ao enviar mídia para {jid}: {e}")
            return False

    def download_media(self, event_data: dict) -> tuple:
        """Baixa a mídia de uma mensagem recebida via webhook.
        Retorna (base64_str, mimetype) ou (None, None) em caso de falha.
        """
        try:
            r = self._post(
                f"/chat/getBase64FromMediaMessage/{self.instance}",
                {"message": event_data, "convertToMp4": False},
                timeout=15,
            )
            if r.ok:
                data = r.json()
                b64 = data.get("base64") or data.get("data")
                mime = data.get("mimetype") or data.get("mimeType") or "application/octet-stream"
                if b64:
                    return b64, mime
        except Exception as e:
            logger.warning(f"Falha ao baixar mídia: {e}")
        return None, None

    def send_audio(self, jid: str, audio_b64: str) -> bool:
        """Envia áudio PTT"""
        try:
            r = self._post(f"/message/sendWhatsAppAudio/{self.instance}", {
                "number": jid,
                "audio": audio_b64,
                "encoding": True,
            })
            r.raise_for_status()
            return True
        except Exception:
            # fallback como media
            return self.send_media(jid, "audio", audio_b64, "audio.mp3")

    # ── Webhook ──────────────────────────────────────────────────────────────

    def configure_webhook(self, webhook_url: str) -> Tuple[bool, str]:
        """Configura webhook na Evolution API v2"""
        try:
            r = self._post(f"/webhook/set/{self.instance}", {
                "webhook": {
                    "enabled": True,
                    "url": webhook_url,
                    "webhookByEvents": False,
                    "webhookBase64": False,
                    "events": [
                        "MESSAGES_UPSERT",
                        "CONNECTION_UPDATE",
                        "GROUP_UPDATE",
                    ],
                }
            })
            r.raise_for_status()
            return True, "Webhook configurado com sucesso"
        except requests.HTTPError as e:
            return False, f"HTTP {e.response.status_code}: {e.response.text[:200]}"
        except Exception as e:
            return False, str(e)


class ConversationService:
    """Lógica de negócio para conversas"""

    @staticmethod
    def sync_groups(connection: WhatsAppConnection) -> Dict:
        """Sincroniza grupos da Evolution API"""
        try:
            client = EvolutionAPIClient(connection)
            groups = client.fetch_all_groups()

            if not groups:
                return {"success": False, "message": "Nenhum grupo encontrado"}

            created = updated = 0
            for g in groups:
                jid = g.get("id") or g.get("remoteJid")
                if not jid:
                    continue

                name = g.get("subject") or g.get("name") or jid
                pic  = g.get("pictureUrl") or g.get("profilePicUrl")

                obj, is_new = ContactGroup.objects.update_or_create(
                    jid=jid, connection=connection,
                    defaults={"name": name, "is_group": True, "profile_picture": pic},
                )
                if is_new:
                    created += 1
                    ConversationService.auto_link_group(obj)
                else:
                    updated += 1

            connection.last_sync = timezone.now()
            connection.save(update_fields=["last_sync"])

            return {
                "success": True,
                "created": created,
                "updated": updated,
                "message": f"{created} novos grupos, {updated} atualizados",
            }
        except Exception as e:
            logger.error(f"Erro ao sincronizar grupos: {e}")
            return {"success": False, "message": str(e)}

    @staticmethod
    def auto_link_group(group: ContactGroup) -> bool:
        """Tenta vincular grupo automaticamente ao cliente pelo nome"""
        if group.cliente:
            return False
        try:
            cliente = Cliente.objects.filter(
                nome_empresa__icontains=group.name[:10]
            ).first()
            if cliente:
                group.cliente = cliente
                group.save(update_fields=["cliente"])
                return True
        except Exception as e:
            logger.error(f"Erro ao vincular grupo: {e}")
        return False

    @staticmethod
    def get_or_create_conversation(group: ContactGroup) -> Conversation:
        # Busca apenas conversas regulares (não-task) abertas — task_conv são tickets separados
        conv = Conversation.objects.filter(
            group=group, status__in=["new", "open", "pending"], is_task_conv=False
        ).order_by('-last_message_at').first()
        if not conv:
            conv = Conversation.objects.create(
                group=group, cliente=group.cliente, status="new"
            )
        elif conv.cliente is None and group.cliente:
            conv.cliente = group.cliente
            conv.save(update_fields=["cliente"])
        return conv

    @staticmethod
    def process_webhook(data: Dict) -> Dict:
        """
        Processa evento recebido da Evolution API v2.

        Estrutura real do payload Evolution API v2:
        {
          "event": "MESSAGES_UPSERT",
          "instance": "nome_instancia",
          "data": {
            "key": { "remoteJid": "...@g.us", "fromMe": false, "id": "MSG_ID" },
            "pushName": "Nome",
            "message": { "conversation": "texto" },
            "messageTimestamp": 1234567890
          }
        }
        """
        try:
            instance_name = data.get("instance")
            event = data.get("event") or data.get("type", "")
            event_data = data.get("data", {})

            logger.info(f"Webhook recebido: event={event} instance={instance_name}")

            # Ignora eventos que não são mensagens
            if event not in ("MESSAGES_UPSERT", "messages.upsert"):
                return {"success": True, "message": f"Evento ignorado: {event}"}

            # ── Extrai key e JID ───────────────────────────────────────────
            # Evolution API v2: data.key.remoteJid
            key = event_data.get("key", {})
            jid = (
                key.get("remoteJid")
                or event_data.get("remoteJid")
                or event_data.get("from")
            )

            if not jid:
                logger.warning(f"Webhook sem JID. event_data keys: {list(event_data.keys())}")
                return {"success": False, "message": "JID não encontrado"}

            # ── Grupo (@g.us) ou contato 1:1 cadastrado ────────────────────
            # Grupo sempre entra. Conversa privada só entra se aquele número
            # estiver CADASTRADO como contato em Grupos/Contatos — senão
            # qualquer pessoa que mandasse mensagem para o número do WhatsApp
            # abriria um chamado (spam, engano, cliente pedindo outra coisa).
            # Cadastrar o contato é o ato explícito de dizer "esse número é
            # atendimento"; até 04/09/2026 nenhuma mensagem privada passava.
            if not jid.endswith("@g.us"):
                if not ContactGroup.objects.filter(
                    jid=jid,
                    connection__instance_name=instance_name,
                    connection__is_active=True,
                ).exclude(status='deleted').exists():
                    return {"success": True, "message": "Privada de número não cadastrado ignorada"}

            from_me = key.get("fromMe", False)

            # Mensagens enviadas pelo próprio atendente via CRM já são salvas
            # localmente em send_message() — ignorar o echo do webhook evita duplicação.
            if from_me:
                return {"success": True, "message": "fromMe ignored"}

            # ── Atendente respondendo pelo WhatsApp pessoal? ────────────────
            # `participant` é o remetente real dentro do grupo (distinto do
            # `remoteJid`, que é o JID do grupo). Se bater com o telefone de
            # um atendente cadastrado em Contatos Atendentes, tratamos como
            # mensagem do agente (não do cliente) e avisamos o grupo do NOC.
            # Em grupo, `participant` é o remetente real dentro do grupo, e a
            # detecção abaixo serve para saber se quem falou foi um ATENDENTE
            # pelo WhatsApp pessoal (em vez do cliente). Numa conversa 1:1 esse
            # campo não vem e a detecção não se aplica: do outro lado está o
            # contato, sempre — é ele o cliente daquele chamado. Preencher
            # `participant` com o próprio `remoteJid` aqui faria um contato
            # cadastrado com o número de um atendente virar "mensagem de
            # agente", e o chamado ficaria sem nenhuma fala do cliente.
            participant = event_data.get("participant") or key.get("participant") or ""
            atendente_pessoal = _detectar_atendente_pessoal(participant)
            sender_type = 'agent' if atendente_pessoal else 'customer'
            if atendente_pessoal:
                push_name_override = atendente_pessoal.user.get_full_name() or atendente_pessoal.user.username
            else:
                push_name_override = None

            # ── Busca conexão ──────────────────────────────────────────────
            connection = WhatsAppConnection.objects.filter(
                instance_name=instance_name, is_active=True
            ).first()
            if not connection:
                logger.warning(f"Conexão não encontrada para instância: {instance_name}")
                return {"success": False, "message": "Conexão não encontrada"}

            # ── Snooze: "Reagendar lembrete" no grupo de notificações ──────
            _early_text = (
                event_data.get("message", {}).get("conversation")
                or event_data.get("message", {}).get("extendedTextMessage", {}).get("text")
                or ""
            )
            if "reagendar" in _early_text.lower():
                from .models import SystemSetting
                _notif_gid = SystemSetting.get('notif_abertos_group_id', '').strip()
                if _notif_gid:
                    _ng = ContactGroup.objects.filter(id=_notif_gid).first()
                    if _ng and _ng.jid == jid:
                        _amanha = (timezone.localtime(timezone.now()) + timedelta(days=1)).strftime('%Y-%m-%d')
                        SystemSetting.set('notif_abertos_snooze_until', _amanha)
                        try:
                            EvolutionAPIClient(connection).send_text(
                                jid,
                                f"✅ *Lembrete reagendado!*\n\nVoltarei a notificar chamados em aberto amanhã ({_amanha}).",
                            )
                        except Exception as _e:
                            logger.warning(f"Falha ao confirmar snooze: {_e}")
                        return {"success": True, "message": "notif_snoozed"}
            # ──────────────────────────────────────────────────────────────

            # ── Grupo e conversa ───────────────────────────────────────────
            # `is_group` vai no defaults: sem isso todo contato 1:1 nascia com
            # o default do campo (True) e se passava por grupo no admin, nos
            # filtros e na tela de Grupos/Contatos.
            group, _ = ContactGroup.objects.get_or_create(
                jid=jid, connection=connection,
                defaults={"name": jid.split("@")[0], "is_group": jid.endswith("@g.us")}
            )

            # ── Extrai conteúdo e detecta tipo de mídia ───────────────────
            msg_content = event_data.get("message", {})
            push_name = event_data.get("pushName") or ""

            # Todo mundo que fala no grupo passa a ser conhecido pelo nome no
            # autocomplete do "@" — a Evolution não devolve nome na lista de
            # participantes, mas manda o pushName em cada mensagem.
            aprender_nome_participante(connection, participant, push_name)

            # ── Reações: anexam à mensagem alvo, não viram balão ───────────
            # Sem isto caíam no fallback "[sem conteúdo]" e poluíam a conversa
            # com balões vazios — era o caso mais comum de balão sem texto.
            reacao = _extrair_reacao(msg_content)
            if reacao:
                reacao["external_id"] = key.get("id") or f"reac_{timezone.now().timestamp()}"
                return ConversationService._registrar_reacao(
                    reacao, push_name_override or push_name, participant
                )

            # ── Edição feita pelo cliente no celular ───────────────────────
            # Não é mensagem nova: é a reescrita de uma que já está na tela.
            # Sem isto viraria um balão "[sem conteúdo]" e o balão original
            # continuaria mostrando o texto antigo.
            edicao = _extrair_edicao(msg_content)
            if edicao:
                return ConversationService._registrar_edicao_recebida(edicao)

            # Eventos sem conteúdo pra mostrar. Viravam balão "[sem conteúdo]".
            #  - albumMessage: cabeçalho de álbum; as fotos chegam depois, cada
            #    uma no seu próprio evento.
            #  - pinInChatMessage: alguém fixou/desafixou mensagem no grupo.
            #  - associatedChildMessage: item filho de outro evento.
            # Testa a presença da chave, não o valor: estes eventos podem vir
            # com objeto vazio ({}), que é falsy — com .get() escapariam do
            # filtro e voltariam a virar balão "[sem conteúdo]".
            for _ignorado in ("albumMessage", "pinInChatMessage", "associatedChildMessage"):
                if _ignorado in msg_content:
                    return {"success": True, "message": f"{_ignorado} ignored"}

            # Detecta se é mensagem de mídia
            detected_type = "text"
            for wkey, mtype in _MEDIA_TYPE_MAP.items():
                if msg_content.get(wkey):
                    detected_type = mtype
                    break

            # Contato compartilhado (vCard): mostra o nome em vez de nada.
            _contatos = _nomes_de_contato(msg_content)

            # Extrai texto/legenda
            content = (
                msg_content.get("conversation")
                or msg_content.get("extendedTextMessage", {}).get("text")
                or msg_content.get("imageMessage", {}).get("caption")
                or msg_content.get("videoMessage", {}).get("caption")
                or msg_content.get("documentMessage", {}).get("title")
                or msg_content.get("documentMessage", {}).get("fileName")
                or _contatos
                or ("" if detected_type == "audio" else None)
                or ("[mídia]" if detected_type != "text" else None)
                or "[sem conteúdo]"
            )

            # ── Baixa mídia (apenas para mensagens recebidas) ──────────────
            attachment_url = None
            if detected_type != "text" and not from_me:
                # Tenta base64 direto no payload (se webhookBase64 estiver on)
                b64 = event_data.get("base64")
                mime = None
                if b64:
                    # Descobre mimetype a partir do objeto de mídia
                    for wkey in _MEDIA_TYPE_MAP:
                        mime = msg_content.get(wkey, {}).get("mimetype")
                        if mime:
                            break
                else:
                    # Baixa via API da Evolution
                    try:
                        client = EvolutionAPIClient(connection)
                        b64, mime = client.download_media(event_data)
                    except Exception as _e:
                        logger.warning(f"download_media falhou: {_e}")

                if b64 and mime:
                    try:
                        attachment_url = _save_media_file(b64, mime)
                    except Exception as _e:
                        logger.warning(f"_save_media_file falhou: {_e}")

            message_id = key.get("id") or f"local_{timezone.now().timestamp()}"

            # @noc → mensagem direcionada ao Agent NOC; o atendimento ignora
            # totalmente (não cria nem abre chamado).
            if '@noc' in (content or '').lower():
                return {"success": True, "message": "noc_ignored"}

            now = timezone.now()

            # ── 1. Auto-atendimento em andamento? ──────────────────────────
            session = ChatFlowSession.objects.filter(
                group_jid=jid, expires_at__gt=now,
            ).select_related('flow', 'conversation').first()
            if session:
                conv = session.conversation
                if conv:
                    msg, created = ConversationService._salvar_msg(
                        conv, message_id, content, detected_type,
                        push_name_override or push_name, attachment_url, sender_type=sender_type)
                    if created:
                        conv.last_message_at = now
                        conv.save(update_fields=['last_message_at'])
                        ConversationService._broadcast_msg(conv, group, msg)
                        _disparar_agente_ia(conv, content)
                    if atendente_pessoal:
                        _alertar_atendente_pessoal(conv, group, atendente_pessoal, connection)
                # Mensagem do atendente pessoal não deve avançar o fluxo do bot
                # (que espera resposta do cliente, não do agente)
                if not atendente_pessoal:
                    ConversationService._processar_passo_fluxo(session, conv, content, group, connection)
                return {"success": True, "message": "flow_step",
                        "conversation_id": str(conv.id) if conv else None}

            # ── 2. Chamado já aberto? ──────────────────────────────────────
            conv = Conversation.objects.filter(
                group=group, status__in=['new', 'open', 'pending'], is_task_conv=False,
            ).order_by('-last_message_at').first()
            if conv:
                msg, created = ConversationService._salvar_msg(
                    conv, message_id, content, detected_type,
                    push_name_override or push_name, attachment_url, sender_type=sender_type)
                if created:
                    conv.last_message_at = now
                    if conv.status == 'new':
                        conv.status = 'open'
                    conv.save(update_fields=['last_message_at', 'status'])
                    if not conv.assigned_to:
                        _notify_new_open_conversation(conv, connection)
                    ConversationService._broadcast_msg(conv, group, msg)
                    _disparar_agente_ia(conv, content)
                if atendente_pessoal:
                    _alertar_atendente_pessoal(conv, group, atendente_pessoal, connection)
                return {"success": True, "conversation_id": str(conv.id),
                        "message_id": str(msg.id), "created": created}

            # ── 2.5 Resposta de CSAT? ───────────────────────────────────────
            # Se há um chamado recém-resolvido aguardando avaliação e a
            # mensagem parece uma nota (1-5), registra e NÃO abre chamado novo.
            csat_resultado = ConversationService._tentar_registrar_csat(
                group, content, connection)
            if csat_resultado:
                return csat_resultado

            # ── 3. Nova conversa — abre e aparece já na 1ª mensagem ────────
            # Reaproveita uma conversa 'pre' antiga (de antes desta mudança)
            # se existir, em vez de deixá-la órfã e escondida para sempre.
            conv = Conversation.objects.filter(
                group=group, status='pre', is_task_conv=False,
            ).order_by('-last_message_at').first()
            if not conv:
                conv = Conversation.objects.create(
                    group=group, cliente=group.cliente, status='open')

            msg, created = ConversationService._salvar_msg(
                conv, message_id, content, detected_type,
                push_name_override or push_name, attachment_url, sender_type=sender_type)
            if not created:
                return {"success": True, "message": "dup", "conversation_id": str(conv.id)}
            conv.last_message_at = now
            conv.status = 'open'
            aplicar_sla(conv, from_time=now)
            conv.save(update_fields=['last_message_at', 'status', 'sla_response_due_at', 'sla_resolution_due_at'])

            # Mostra a mensagem na hora — antes de qualquer processamento do bot
            ConversationService._broadcast_msg(conv, group, msg, inbox=True)

            if atendente_pessoal:
                _alertar_atendente_pessoal(conv, group, atendente_pessoal, connection)

            # O auto-atendimento NÃO manda mais nada para o grupo do cliente.
            # Ele respondia com a saudação + a mensagem de conclusão a cada
            # chamado aberto, o que só poluía a conversa do grupo (o chamado
            # já é aberto na 1ª mensagem, sem depender de resposta do bot).
            # A configuração continua existindo em "Auto Atendimento" para
            # assunto/categoria; o que saiu foi o envio automático.
            if not conv.assigned_to:
                _notify_new_open_conversation(conv, connection)
            _disparar_agente_ia(conv, content)
            logger.info(f"Chamado aberto na 1ª msg: conversa #{conv.conversation_id}")

            return {"success": True, "conversation_id": str(conv.id), "opened": True}
        except Exception as e:
            logger.error(f"Erro ao processar webhook: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    # ── CSAT (pesquisa de satisfação) ──────────────────────────────────────
    @staticmethod
    def _tentar_registrar_csat(group: ContactGroup, content: str, connection: WhatsAppConnection):
        """Se houver um chamado deste grupo recém-resolvido aguardando
        avaliação (csat_requested_at preenchido, csat_rating vazio, dentro
        da janela de 48h) e a mensagem recebida parecer uma nota de 1 a 5,
        registra o CSAT e agradece — sem abrir um chamado novo.
        Retorna o dict de resultado do webhook se tratou como CSAT, ou None
        caso a mensagem deva seguir o fluxo normal (abrir/continuar chamado)."""
        import re
        janela = timezone.now() - timedelta(hours=48)
        conv = Conversation.objects.filter(
            group=group, csat_requested_at__gte=janela, csat_rating__isnull=True,
            status__in=['resolved', 'closed'],
        ).order_by('-csat_requested_at').first()
        if not conv:
            return None

        texto = (content or '').strip()
        m = re.match(r'^\s*([1-5])\b\s*(.*)$', texto)
        if not m:
            return None

        nota = int(m.group(1))
        comentario = m.group(2).strip()
        conv.csat_rating = nota
        conv.csat_comment = comentario
        conv.save(update_fields=['csat_rating', 'csat_comment'])

        try:
            EvolutionAPIClient(connection).send_text(
                group.jid, "🙏 Obrigado pela avaliação! Sua opinião nos ajuda a melhorar."
            )
        except Exception as _e:
            logger.warning(f"Falha ao agradecer CSAT: {_e}")

        return {"success": True, "message": "csat_registrado",
                "conversation_id": str(conv.id), "rating": nota}

    # ── Auto-atendimento (fluxo) sobre uma conversa já existente ───────────
    @staticmethod
    def _flow_do_grupo(group: ContactGroup):
        """Retorna o ChatFlow ativo pra este grupo.

        Um flow com group_ids vazio vale como padrão universal (todos os
        grupos, inclusive um recém-criado pela própria 1ª mensagem via
        get_or_create). Sem isso, um grupo só recebia a saudação do bot se
        alguém lembrasse de adicioná-lo manualmente na lista — 15 dos 43
        grupos cadastrados nunca foram, então o cliente mandava mensagem e
        só um humano acabava respondendo, bem mais tarde.
        Flow com group_ids preenchido continua tendo prioridade (permite um
        fluxo dedicado pra um cliente específico no futuro).

        `auto_atendimento_excluido` é um opt-out explícito por grupo — sem ele,
        não havia como tirar um grupo específico do auto atendimento quando
        existe um fluxo universal ativo (ele pegaria todo mundo de novo)."""
        if group.auto_atendimento_excluido:
            return None
        gid = str(group.id)
        flows = list(ChatFlow.objects.filter(active=True))
        for f in flows:
            if f.group_ids and gid in [str(x) for x in f.group_ids]:
                return f
        for f in flows:
            if not f.group_ids:
                return f
        return None

    @staticmethod
    def _registrar_edicao_recebida(edicao: dict) -> Dict:
        """Aplica no CRM a edição que o cliente fez no WhatsApp."""
        alvo = Message.objects.filter(external_id=edicao["alvo"]).first()
        if not alvo:
            # Mensagem anterior ao CRM (ou nunca sincronizada): não há balão
            # para atualizar, e criar um novo mostraria a conversa fora de
            # ordem. Melhor ignorar do que inventar.
            return {"success": True, "message": "edicao de mensagem desconhecida"}

        agora = timezone.now()
        alvo.content = edicao["texto"]
        alvo.edited_at = agora
        alvo.save(update_fields=["content", "edited_at"])

        _ws_send_conversation(str(alvo.conversation_id), {
            "type": "message_edited",
            "message": {
                "id": str(alvo.id),
                "content": alvo.content,
                "edited_at": timezone.localtime(agora).strftime("%H:%M"),
            },
        })
        return {"success": True, "message": "edicao aplicada"}

    @staticmethod
    def _registrar_reacao(reacao: dict, push_name: str, participant: str) -> Dict:
        """Anexa a reação à mensagem alvo e avisa a tela por WebSocket.

        Não cria mensagem nenhuma: reação não é balão, é um detalhe da
        mensagem que recebeu a reação (como no WhatsApp).
        """
        alvo = Message.objects.filter(external_id=reacao["alvo"]).first()
        if not alvo:
            # Reação a mensagem anterior ao histórico que temos. Ignorar é
            # melhor do que criar um balão vazio pendurado no fim da conversa.
            return {"success": True, "message": "reaction target not found"}

        # Reagir de novo troca a reação anterior da mesma pessoa; texto vazio
        # (e não cifrado) é o WhatsApp removendo a reação.
        anteriores = alvo.reactions.filter(sender_jid=participant) if participant else None
        if anteriores is not None:
            anteriores.delete()

        if not reacao["emoji"] and not reacao["cifrada"]:
            ConversationService._broadcast_reacoes(alvo)
            return {"success": True, "message": "reaction removed"}

        MessageReaction.objects.get_or_create(
            external_id=reacao["external_id"],
            defaults={
                "message": alvo,
                "emoji": reacao["emoji"],
                "sender_name": push_name or "",
                "sender_jid": participant or "",
            },
        )
        ConversationService._broadcast_reacoes(alvo)
        return {"success": True, "message": "reaction saved"}

    @staticmethod
    def _broadcast_reacoes(msg: Message):
        """Manda a lista atualizada de reações da mensagem para a tela."""
        _ws_send_conversation(str(msg.conversation_id), {
            "type": "reactions",
            "message_id": str(msg.id),
            "reactions": [
                {"emoji": r.emoji, "sender_name": r.sender_name}
                for r in msg.reactions.all()
            ],
        })

    @staticmethod
    def _salvar_msg(conversation, message_id, content, detected_type,
                    push_name, attachment_url, sender_type='customer'):
        """Cria a mensagem (idempotente pelo external_id)."""
        return Message.objects.get_or_create(
            external_id=message_id,
            defaults={
                "conversation": conversation,
                "sender_type": sender_type,
                "message_type": detected_type,
                "content": content,
                "sender_name": push_name,
                "attachment_url": attachment_url,
            },
        )

    @staticmethod
    def _broadcast_msg(conversation, group, msg, inbox=True):
        """Envia a mensagem por WebSocket (conversa + opcionalmente inbox)."""
        local_now = timezone.localtime(timezone.now())
        payload = {
            "type": "new_message",
            "message": {
                "id": str(msg.id),
                "content": msg.content,
                "sender_type": msg.sender_type,
                "sender_name": msg.sender_name or "",
                "created_at": local_now.strftime("%H:%M"),
                "created_at_iso": msg.created_at.isoformat(),
                "message_type": msg.message_type,
                "attachment_url": msg.attachment_url or "",
                "sender_id": msg.sender_id,
            },
            "conversation": {
                "id": str(conversation.id),
                "conversation_id": conversation.conversation_id,
                "status": conversation.status,
                "group_name": group.name,
                "last_message_at": local_now.strftime("%H:%M"),
                "assigned_to_id": conversation.assigned_to_id,
                # Atendentes que podem ver este chamado (None = todos). O
                # InboxConsumer é UM grupo de canal só ("atendimento_inbox"),
                # ou seja, todo mundo logado recebe o mesmo pacote — sem esta
                # lista, um chamado de contato restrito apareceria na caixa de
                # entrada de quem não pode vê-lo, com nome e tudo. Ver
                # consumers.InboxConsumer.inbox_update.
                "allowed_user_ids": atendentes_do_chamado(conversation),
            },
        }
        _ws_send_conversation(str(conversation.id), payload)
        if inbox:
            _ws_send_inbox(payload)

    @staticmethod
    def _flow_enviar(conversation, group, connection, texto):
        """Envia uma pergunta/saudação do auto-atendimento ao WhatsApp e a salva
        como mensagem 'system' para aparecer no chat."""
        if not texto or not conversation:
            return
        import uuid as _uuid
        msg = Message.objects.create(
            conversation=conversation,
            sender_type='system',
            sender_name='Auto Atendimento',
            message_type='text',
            content=texto,
            external_id='flow_%s' % _uuid.uuid4().hex,
        )
        conversation.last_message_at = timezone.now()
        conversation.save(update_fields=['last_message_at'])
        try:
            EvolutionAPIClient(connection).send_text(group.jid, texto)
        except Exception as _e:
            logger.warning(f"Falha ao enviar msg do fluxo: {_e}")
        ConversationService._broadcast_msg(conversation, group, msg, inbox=False)

    @staticmethod
    def _processar_passo_fluxo(session: ChatFlowSession, conv, content: str,
                               group: ContactGroup, connection: WhatsAppConnection):
        """Processa a resposta do cliente no auto-atendimento (assunto/categoria)."""
        flow = session.flow

        if session.step == 'subject':
            session.subject = content
            session.step = 'category'
            session.save(update_fields=['subject', 'step'])
            cats = flow.categories or []
            if cats:
                lines = [flow.category_question]
                for i, c in enumerate(cats, 1):
                    lines.append(f'{i} - {c}')
                ConversationService._flow_enviar(conv, group, connection, '\n'.join(lines))
            else:
                ConversationService._finalizar_fluxo(session, conv, group, connection, None)
            return

        if session.step == 'category':
            cats = flow.categories or []
            chosen = None
            stripped = (content or '').strip()
            try:
                num = int(stripped)
                if 1 <= num <= len(cats):
                    chosen = cats[num - 1]
            except ValueError:
                lower = stripped.lower()
                for cat in cats:
                    if lower in cat.lower() or cat.lower().startswith(lower):
                        chosen = cat
                        break
            if chosen is None and cats:
                ConversationService._flow_enviar(
                    conv, group, connection,
                    f'Por favor, responda com um número de 1 a {len(cats)}.')
                return
            ConversationService._finalizar_fluxo(session, conv, group, connection, chosen)
            return

    @staticmethod
    def _finalizar_fluxo(session: ChatFlowSession, conv, group: ContactGroup,
                         connection: WhatsAppConnection, chosen_category):
        """Conclui o auto-atendimento: grava assunto/categoria no chamado já
        existente e encerra a sessão do fluxo."""
        flow = session.flow
        category_obj = None
        if chosen_category:
            category_obj, _ = Category.objects.get_or_create(
                name=chosen_category, defaults={'color': '#7c3aed'})
        if conv:
            if session.subject:
                conv.subject = session.subject
            if category_obj:
                conv.category = category_obj
            if conv.status == 'pre':
                conv.status = 'open'
            conv.save()
        if flow.completion_message:
            ConversationService._flow_enviar(conv, group, connection, flow.completion_message)
        session.delete()
        if conv and not conv.assigned_to:
            _notify_new_open_conversation(conv, connection)
        if conv:
            _ws_send_inbox({
                "type": "new_message",
                "conversation": {
                    "id": str(conv.id),
                    "conversation_id": conv.conversation_id,
                    "status": conv.status,
                    "group_name": group.name,
                    "subject": conv.subject,
                    "last_message_at": timezone.localtime(timezone.now()).strftime("%H:%M"),
                    "assigned_to_id": conv.assigned_to_id,
                },
            })

    @staticmethod
    def get_agent_display_name(agent) -> str:
        """Retorna o nome de exibição configurado ou o nome completo"""
        if not agent:
            return "Atendente"
        try:
            return agent.agent_status.get_display_name()
        except Exception:
            return agent.get_full_name() or agent.username

    @staticmethod
    def send_message(conversation: Conversation, text: str,
                     agent=None, is_internal=False, mentions=None) -> Tuple[bool, str]:
        """Salva a mensagem imediatamente. Se `is_internal`, é uma nota
        interna — fica só no CRM, NUNCA sai pro WhatsApp do cliente (o
        toggle "Comentário Interno" da tela chegava a esta função sem
        nenhum efeito: a mensagem sempre ia pro WhatsApp igual a uma
        resposta normal, vazando nota privada pro cliente). Caso contrário,
        envia ao WhatsApp em background. Formato enviado: *NomeAgente*\n\nmensagem

        `mentions` são os contatos marcados com "@" no chat (lista de
        {'nome','phone'}): o CRM guarda o texto legível com o nome e o
        WhatsApp recebe o número, que é o que faz a menção destacar e
        notificar a pessoa. Nota interna ignora — não sai nada pro grupo.
        """
        import threading as _threading

        try:
            display_name = ConversationService.get_agent_display_name(agent)
            sender_type = "internal" if is_internal else "agent"
            texto_wa, numeros_mencionados = aplicar_mencoes(text, mentions)
            whatsapp_text = f"*{display_name}*\n\n{texto_wa}"

            # 0. Quem responde, assume — vale para qualquer caminho de envio
            # (inclusive nota interna: escrever sobre o chamado já é sinal
            # de que alguém está cuidando dele).
            auto_assign_on_reply(conversation, agent)

            # 1. Salva no DB imediatamente com ID temporário
            now = timezone.now()
            temp_id = f"sending_{int(now.timestamp() * 1000)}_{conversation.id}"
            msg = Message.objects.create(
                external_id=temp_id,
                conversation=conversation,
                sender_type=sender_type,
                sender=agent,
                sender_name=display_name,
                message_type="text",
                content=text,
                created_at=now,
                is_internal=is_internal,
            )

            # 2. Atualiza conversa e cria atividade. Nota interna não chega
            # ao cliente, então não conta como resposta nem como atividade
            # recente pro SLA/varredura de "chamado sem resposta" — só
            # first_response_at/last_message_at de mensagem que o cliente
            # de fato recebeu.
            update_fields = ["status"]
            if conversation.status == "new":
                conversation.status = "open"
            if not is_internal:
                conversation.last_message_at = now
                update_fields.append("last_message_at")
                if not conversation.first_response_at:
                    conversation.first_response_at = now
                    update_fields.append("first_response_at")
            conversation.save(update_fields=update_fields)
            ConversationActivity.objects.create(
                conversation=conversation,
                actor=agent,
                action="message_sent",
                description=text[:100],
            )

            # 3. Notifica via WebSocket antes de qualquer I/O externo
            local_time = timezone.localtime(now)
            _ws_send_conversation(str(conversation.id), {
                "type": "new_message",
                "message": {
                    "id": str(msg.id),
                    "content": text,
                    "sender_type": sender_type,
                    "sender_name": display_name,
                    "created_at": local_time.strftime("%H:%M"),
                    "created_at_iso": msg.created_at.isoformat(),
                    "message_type": "text",
                    # Quem escreveu: a tela usa para decidir se mostra o lápis
                    # de editar (o servidor revalida na hora de salvar).
                    "sender_id": agent.id if agent else None,
                },
            })

            # Gatilhos de ação do agente IA no que o atendente escreveu pela
            # plataforma — valem na caixa normal do chat e no comentário
            # interno ("Tomichinho fechar atendimento" nos dois lugares).
            # Só as AÇÕES: a resposta conversacional a "tomichinho" continua
            # sendo coisa do grupo do WhatsApp, senão toda menção ao agente
            # numa mensagem do atendente viraria mais uma mensagem pro cliente.
            _disparar_acoes_ia(conversation, text, is_internal)

            if is_internal:
                return True, str(msg.id)

            # 4. Envia ao WhatsApp em background — sem bloquear a resposta HTTP
            msg_id = msg.id
            group_connection = conversation.group.connection
            group_jid = conversation.group.jid

            def _send_bg():
                try:
                    client = EvolutionAPIClient(group_connection)
                    ok, remote_id = client.send_text(
                        group_jid, whatsapp_text, mentions=numeros_mencionados or None)
                    if ok and remote_id:
                        Message.objects.filter(id=msg_id).update(external_id=remote_id)
                    elif not ok:
                        logger.error(f"Envio bg falhou (msg {msg_id}): {remote_id}")
                except Exception as _e:
                    logger.error(f"Erro bg no envio (msg {msg_id}): {_e}")

            _threading.Thread(target=_send_bg, daemon=True).start()

            return True, str(msg.id)

        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {e}")
            return False, str(e)

    # O WhatsApp só aceita editar mensagem própria e dentro de 15 min do
    # envio — depois disso o próprio aplicativo esconde a opção. Manter o
    # mesmo número aqui evita prometer na tela uma edição que a Evolution
    # vai recusar com "Message not compatible".
    JANELA_EDICAO_MIN = 15

    @staticmethod
    def pode_editar(message, user, ignorar_prazo: bool = False) -> Tuple[bool, str]:
        """Diz se `user` pode editar `message` agora, e por que não quando não
        pode. Uma função só, usada pela API e pelo que a tela exibe — assim o
        botão de editar e o backend nunca discordam.

        `ignorar_prazo=True` responde "seria sua para editar, se estivesse no
        prazo". A tela usa isso para continuar mostrando o lápis depois dos 15
        minutos: some o botão e o atendente fica sem saber se o recurso existe,
        se não funciona ou se ele fez algo errado. Com o lápis lá, o clique
        explica o motivo.
        """
        from usuario import perms

        if message.sender_type not in ('agent', 'internal'):
            return False, 'Só dá para editar mensagem enviada por você — a do cliente é dele.'
        # O nome de quem escreveu vai no corpo da mensagem do WhatsApp
        # (*Fulano*), então reescrever a fala de outra pessoa sob o nome dela
        # é coisa de supervisão, não de qualquer atendente. Sem autor
        # (mensagem do agente IA ou de fluxo) ninguém edita: o texto é gerado,
        # e mudá-lo no CRM só criaria divergência com o que foi enviado.
        if not message.sender_id:
            return False, 'Mensagem automática não pode ser editada.'
        if message.sender_id != user.id and not perms.is_admin(user):
            return False, 'Essa mensagem é de outro atendente.'
        if message.message_type != 'text':
            return False, 'O WhatsApp só permite editar mensagem de texto.'
        if message.is_internal:
            # Nota interna nunca saiu do CRM: sem prazo e sem WhatsApp.
            return True, ''
        limite = message.created_at + timedelta(minutes=ConversationService.JANELA_EDICAO_MIN)
        if not ignorar_prazo and timezone.now() > limite:
            return False, (f'O WhatsApp só deixa editar até '
                           f'{ConversationService.JANELA_EDICAO_MIN} minutos depois do envio.')
        # O `external_id` só é a key do WhatsApp depois que o envio em
        # background confirma o wamid (`3EB0…`); antes disso, e nas mensagens
        # que o CRM cria por conta própria, ele é um id interno — não há o que
        # editar do outro lado.
        ids_internos = ('sending_', 'ia_', 'flow_', 'local_media_', 'concluido_', 'reac_')
        if not message.external_id or message.external_id.startswith(ids_internos):
            return False, 'Essa mensagem ainda não foi confirmada pelo WhatsApp.'
        return True, ''

    @staticmethod
    def edit_message(message, novo_texto: str, agent=None, mentions=None) -> Tuple[bool, str]:
        """Reescreve uma mensagem já enviada, no CRM e no WhatsApp.

        A edição no WhatsApp precisa sair com o MESMO cabeçalho do envio
        original (`*NomeDoAtendente*`), senão a mensagem editada apareceria
        no grupo sem a assinatura que todas as outras têm — e o nome usado é
        o de quem escreveu, não o de quem está editando.

        Ao contrário do envio, aqui o WhatsApp é chamado de forma síncrona: o
        atendente precisa saber na hora se a edição pegou de verdade lá. Uma
        edição que falha em silêncio é pior que não editar — o CRM mostraria
        um texto que o cliente nunca viu.
        """
        novo_texto = (novo_texto or '').strip()
        if not novo_texto:
            return False, 'Mensagem vazia'
        if novo_texto == message.content:
            return True, str(message.id)   # nada mudou, nada a fazer

        pode, motivo = ConversationService.pode_editar(message, agent) if agent else (True, '')
        if not pode:
            return False, motivo

        # O texto que vai pro grupo continua trocando "@Fulano" pelo número,
        # para a mensagem editada ficar igual às outras. O que NÃO acontece é
        # a notificação: `updateMessage` não aceita `mentioned` (ver
        # `EvolutionAPIClient.edit_text`), então marcar alguém novo na edição
        # não avisa essa pessoa.
        texto_wa, _numeros = aplicar_mencoes(novo_texto, mentions)

        if not message.is_internal:
            conversation = message.conversation
            group = conversation.group
            if not group or not group.connection or not group.jid:
                return False, 'Conversa sem grupo do WhatsApp configurado.'
            # O cabeçalho é o de quem ESCREVEU (o WhatsApp já mostra esse
            # nome no grupo); quem edita não se apropria da fala.
            assinatura = message.sender_name or ConversationService.get_agent_display_name(message.sender)
            ok, erro = EvolutionAPIClient(group.connection).edit_text(
                group.jid, message.external_id, f"*{assinatura}*\n\n{texto_wa}")
            if not ok:
                return False, f'O WhatsApp recusou a edição: {erro}'

        agora = timezone.now()
        message.content = novo_texto
        message.edited_at = agora
        message.save(update_fields=['content', 'edited_at'])

        ConversationActivity.objects.create(
            conversation=message.conversation,
            actor=agent,
            action='message_edited',
            description=novo_texto[:100],
        )

        # Só a conversa: a lista lateral mostra o nome do cliente, não um
        # trecho da última mensagem, então não há nada para atualizar lá.
        _ws_send_conversation(str(message.conversation_id), {
            'type': 'message_edited',
            'message': {
                'id': str(message.id),
                'content': novo_texto,
                'edited_at': timezone.localtime(agora).strftime('%H:%M'),
            },
        })

        return True, str(message.id)

    # ── Exclusão de mensagem ──────────────────────────────────────────────

    #: ids que o CRM inventa antes (ou no lugar) do wamid do WhatsApp. Mensagem
    #: com um desses nunca existiu do outro lado — não há o que apagar lá.
    IDS_INTERNOS = ('sending_', 'ia_', 'flow_', 'local_media_', 'concluido_', 'reac_')

    @staticmethod
    def pode_excluir(message, user) -> Tuple[bool, str]:
        """Diz se `user` pode apagar `message`, e por que não quando não pode.
        Uma função só, usada pela API e pelo que a tela exibe — assim o botão
        e o backend nunca discordam (mesmo desenho de `pode_editar`).

        Difere da edição em dois pontos, de propósito:

        - **Mídia pode ser apagada.** A edição é só texto porque o WhatsApp não
          reescreve mídia, mas apagar ele aceita — e mandar o arquivo errado é
          justamente o caso em que apagar mais importa.
        - **Não há prazo do lado do CRM.** A janela de "apagar para todos" é
          bem maior que os 15 min da edição e o WhatsApp já mudou esse número
          mais de uma vez; fixar um limite aqui só criaria um botão que recusa
          o que o WhatsApp aceitaria. Quem decide é ele, e o motivo da recusa
          vai para a tela.
        """
        from usuario import perms

        if message.deleted_at:
            return False, 'Essa mensagem já foi apagada.'
        if message.sender_type not in ('agent', 'internal'):
            return False, 'Só dá para apagar mensagem enviada por você — a do cliente é dele.'
        if message.sender_id and message.sender_id != user.id and not perms.is_admin(user):
            return False, 'Essa mensagem é de outro atendente.'
        # Sem autor (agente IA, fluxo automático): só administrador. Ao
        # contrário da edição — que é proibida porque reescreveria um texto
        # gerado — apagar uma resposta errada do bot é operação legítima, mas
        # é decisão de supervisão, não de quem estiver com o chat aberto.
        if not message.sender_id and not perms.is_admin(user):
            return False, 'Mensagem automática só pode ser apagada por um administrador.'
        if message.is_internal:
            # Nota interna nunca saiu do CRM: nada a pedir ao WhatsApp.
            return True, ''
        if not message.external_id or message.external_id.startswith(
                ConversationService.IDS_INTERNOS):
            return False, 'Essa mensagem ainda não foi confirmada pelo WhatsApp.'
        return True, ''

    @staticmethod
    def delete_message(message, agent=None) -> Tuple[bool, str]:
        """Apaga a mensagem para todos: primeiro no WhatsApp, depois no CRM.

        **Nessa ordem, e síncrono.** Se o WhatsApp recusar (mensagem velha
        demais, tipo incompatível), nada é marcado no CRM — senão o atendente
        veria "Mensagem apagada" na tela enquanto o cliente continua com o
        texto no celular, que é a pior das duas telas possíveis. É a mesma
        razão pela qual `edit_message` também é síncrono.

        Não há caminho de "apagar só no CRM": esconder do supervisor o que o
        cliente ainda tem na mão não é apagar, é maquiar o registro.

        O arquivo de mídia sai do disco junto. Deixá-lo servido em
        `attachment_url` faria a exclusão ser só de fachada — quem tivesse a
        URL continuaria baixando o documento enviado por engano.
        """
        if message.deleted_at:
            return True, str(message.id)      # já apagada, nada a fazer

        if not message.is_internal:
            conversation = message.conversation
            group = conversation.group
            if not group or not group.connection or not group.jid:
                return False, 'Conversa sem grupo do WhatsApp configurado.'
            ok, erro = EvolutionAPIClient(group.connection).delete_message(
                group.jid, message.external_id)
            if not ok:
                return False, f'O WhatsApp recusou apagar: {erro}'

        agora = timezone.now()
        message.deleted_at = agora
        message.deleted_by = agent
        message.save(update_fields=['deleted_at', 'deleted_by'])

        _remover_arquivo_de_midia(message)

        ConversationActivity.objects.create(
            conversation=message.conversation,
            actor=agent,
            action='message_deleted',
            description=(message.content or '')[:100],
        )

        _ws_send_conversation(str(message.conversation_id), {
            'type': 'message_deleted',
            'message': {
                'id': str(message.id),
                'deleted_at': timezone.localtime(agora).strftime('%H:%M'),
            },
        })

        return True, str(message.id)

    @staticmethod
    def send_media(conversation: Conversation, media_base64: str, media_type: str,
                   file_name: str, caption: str, agent=None) -> Tuple[bool, str]:
        """Salva a Message de mídia imediatamente e envia ao WhatsApp em
        background. Mesma mecânica de send_message, mas para
        imagem/áudio/vídeo/documento. Igual ao envio imediato, sempre salva
        um arquivo novo em disco (mesmo se o chamador já tiver um
        attachment_url de antes, como no agendador) — simples e evita um
        segundo caminho de código só pra reaproveitar o arquivo."""
        import threading as _threading
        import mimetypes as _mt
        import time as _t

        try:
            detected_mime, _ = _mt.guess_type(file_name)
            if not detected_mime:
                detected_mime = {
                    'image': 'image/jpeg', 'audio': 'audio/ogg',
                    'video': 'video/mp4', 'document': 'application/octet-stream',
                }.get(media_type, 'application/octet-stream')

            attachment_url = None
            try:
                attachment_url = _save_media_file(media_base64, detected_mime)
            except Exception as _save_err:
                logger.warning("Salvar midia falhou: %s", _save_err)

            if caption:
                content = caption
            elif media_type == 'document':
                content = file_name
            else:
                type_labels = {'image': 'Imagem', 'audio': 'Áudio', 'document': 'Documento', 'video': 'Vídeo'}
                content = type_labels.get(media_type, media_type)

            display_name = ConversationService.get_agent_display_name(agent)
            auto_assign_on_reply(conversation, agent)
            now = timezone.now()
            msg = Message.objects.create(
                conversation=conversation, sender_type='agent', sender=agent,
                sender_name=display_name, message_type=media_type, content=content,
                external_id=f"local_media_{int(_t.time()*1000)}",
                attachment_url=attachment_url, created_at=now,
            )
            conversation.last_message_at = now
            if conversation.status == 'new':
                conversation.status = 'open'
            conversation.save(update_fields=['last_message_at', 'status'])

            group_connection = conversation.group.connection
            group_jid = conversation.group.jid
            msg_id = msg.id

            def _send_bg():
                try:
                    client = EvolutionAPIClient(group_connection)
                    if media_type == 'audio':
                        client.send_audio(group_jid, media_base64)
                    else:
                        client.send_media(group_jid, mediatype=media_type, media_b64=media_base64,
                                          filename=file_name, caption=caption)
                except Exception as _e:
                    logger.error(f"Erro bg envio mídia (msg {msg_id}): {_e}")

            _threading.Thread(target=_send_bg, daemon=True).start()

            return True, str(msg.id)

        except Exception as e:
            logger.error(f"Erro ao enviar mídia: {e}")
            return False, str(e)
