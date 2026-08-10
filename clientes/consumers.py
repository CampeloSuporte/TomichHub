import json
import re
import select
import pexpect
import telnetlib
import threading
import ipaddress
import logging
import time
import os
import ssl
import struct
import socket
import paramiko
from datetime import timedelta
from paramiko.message import Message
from asgiref.sync import sync_to_async
from channels.consumer import get_handler_name
from channels.generic.websocket import WebsocketConsumer
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.utils import timezone
from .models import Acesso, Cliente, ProxyServer, AcessoSessao, AcessoComando, TerminalLinkExterno
from usuario import perms as _perms

logger = logging.getLogger(__name__)

# Remove sequências ANSI (CSI, OSC, seleção de charset, etc.) do output do
# equipamento antes de gravar no transcript de auditoria — sem isso o texto
# fica ilegível (cores, posicionamento de cursor). Não é um emulador de
# terminal completo (não reconstrói telas com cursor pulando pra trás), mas
# cobre bem o caso comum de CLI de rede: eco de comando + saída linear.
_ANSI_RE = re.compile(
    r'\x1b(?:\[[0-9;?]*[a-zA-Z]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][A-Za-z0-9]|[=>NOM])'
)

# Priorizar KEX leve (group14 = 2048-bit DH) sobre group16 (4096-bit). ZTE
# OLTs têm timeout de KEX curto — group16-sha512 demora 2-5s no CPU embarcado
# e a conexão cai antes da auth (PuTTY usa group14 e funciona). group16/18
# ficam por último para compatibilidade com outros vendors. Usado em toda
# conexão paramiko.Transport direta a um equipamento (não a proxies).
_ZTE_PREFERRED_KEX = (
    'diffie-hellman-group14-sha256',
    'diffie-hellman-group14-sha1',
    'diffie-hellman-group1-sha1',
    # Só "@libssh.org" existe no _kex_info do paramiko 4.0.0 — "curve25519-sha256"
    # sem sufixo (nome moderno que switches/servidores recentes anunciam) não é
    # uma chave válida ali. Se o servidor oferece esse nome e ele fica na lista de
    # preferência, o paramiko negocia esse algoritmo (nome bate) e só quebra depois
    # tentando instanciar o Kex: KeyError('curve25519-sha256'). Removido — o
    # "@libssh.org" abaixo já cobre curve25519 nos servidores que o suportam.
    'curve25519-sha256@libssh.org',
    'ecdh-sha2-nistp256',
    'ecdh-sha2-nistp384',
    'ecdh-sha2-nistp521',
    'diffie-hellman-group-exchange-sha256',
    'diffie-hellman-group-exchange-sha1',
    'diffie-hellman-group16-sha512',
    'diffie-hellman-group18-sha512',
)


class ThreadedDispatchMixin:
    """
    Channels despacha consumers síncronos via `database_sync_to_async`, que é
    "thread sensitive": TODAS as conexões WebSocket síncronas do processo
    Daphne (config atual = 1 worker) compartilham UMA única thread global.
    SSHConsumer/WebSocketProxyConsumer fazem handshake bloqueante (paramiko,
    pexpect, socket.create_connection) em connect()/receive() — um handshake
    lento em um terminal travava teclas e conexões de TODOS os outros
    terminais abertos ao mesmo tempo, e se a fila demorasse mais que o
    application-close-timeout do Daphne (30s), a conexão morria com
    "Socket is closed".
    Aqui cada dispatch roda no thread pool padrão (não thread-sensitive),
    permitindo terminais concorrentes de verdade.
    """
    async def dispatch(self, message):
        handler = getattr(self, get_handler_name(message), None)
        if handler is None:
            raise ValueError("No handler for message type %s" % message["type"])

        def _run():
            close_old_connections()
            try:
                handler(message)
            finally:
                close_old_connections()

        await sync_to_async(_run, thread_sensitive=False)()


# ── Pool de conexões SSH com proxies ─────────────────────────────────────────
# Mantém conexões paramiko com proxies vivas para reutilizar sem re-handshake

class _ProxyPool:
    """Cache de conexões SSH ativas com servidores proxy."""
    def __init__(self):
        self._lock  = threading.Lock()
        self._conns = {}   # key → paramiko.SSHClient

    def _key(self, proxy) -> str:
        return f"{proxy.usuario}@{proxy.host}:{proxy.porta}"

    def get(self, proxy):
        """Retorna conexão ativa existente ou None.
        `is_active()` só checa uma flag interna — não detecta uma conexão
        que morreu silenciosamente (NAT/firewall derrubou por ociosidade
        sem FIN limpo). Fazemos um send_ignore() real para confirmar que o
        socket ainda responde; se não, descarta e força reconexão aqui em
        vez de deixar o open_channel() falhar depois com timeout de 10s."""
        key = self._key(proxy)
        with self._lock:
            client = self._conns.get(key)
            if client is None:
                return None
            try:
                t = client.get_transport()
                if t and t.is_active():
                    t.send_ignore()
                    return client
            except Exception:
                pass
            del self._conns[key]
            return None

    def put(self, proxy, client):
        """Armazena conexão no pool e liga keepalive SSH para que ela
        sobreviva a NAT/firewalls que derrubam conexões TCP ociosas —
        sem isso, um proxy sem uso por alguns minutos aparentava "vivo"
        (is_active()==True) mas já estava morto na próxima sessão."""
        key = self._key(proxy)
        try:
            t = client.get_transport()
            if t:
                t.set_keepalive(30)
        except Exception:
            pass
        with self._lock:
            self._conns[key] = client

    def remove(self, proxy):
        key = self._key(proxy)
        with self._lock:
            self._conns.pop(key, None)

_proxy_pool = _ProxyPool()


# ── Terminal compartilhado (opt-in) ──────────────────────────────────────────
# Um usuário conectado normalmente a um Acesso pode optar por "compartilhar"
# sua sessão; outros usuários com permissão sobre o mesmo Acesso que abrirem
# o terminal enquanto o compartilhamento estiver ativo entram na MESMA sessão
# (mesma conexão SSH/Telnet física), veem o output em tempo real e podem
# digitar — exatamente como o dono. Registro só em memória de processo (não
# Redis): igual à Sala Virtual (atendimento/consumers.py), válido porque o
# daphne.service roda com 1 único worker.
class _SharedTerminalSession:
    """`physical` é o SSHConsumer que fisicamente detém a conexão (shell
    paramiko/pexpect/telnet) — nunca muda, mesmo que o próprio WebSocket dele
    desconecte: a thread de leitura dele continua rodando e transmitindo para
    quem ainda estiver assistindo (ver `limpar_recursos`/`_sair_de_sessao_
    compartilhada`). `owner_label` é só cosmético (nome exibido como "dono
    atual" na UI) e é promovido para o próximo espectador quando quem
    compartilhou originalmente sai."""

    def __init__(self, acesso_id, physical_consumer, label):
        self.acesso_id = acesso_id
        self.physical = physical_consumer
        self.owner_label = label
        self.lock = threading.Lock()
        self.viewers = {physical_consumer: label}   # dict preserva ordem de entrada
        self.recent_output = ''
        self._RECENT_MAX = 20_000

    def add_viewer(self, consumer, label):
        with self.lock:
            self.viewers[consumer] = label

    def remove_viewer(self, consumer):
        """Remove `consumer` da sessão. Retorna True se ainda restam
        espectadores (sessão continua viva)."""
        with self.lock:
            self.viewers.pop(consumer, None)
            if consumer is self.physical and self.viewers:
                self.owner_label = next(iter(self.viewers.values()))
            return bool(self.viewers)

    def snapshot_viewers(self):
        with self.lock:
            return list(self.viewers.items())

    def append_recent(self, text):
        with self.lock:
            buf = self.recent_output + text
            if len(buf) > self._RECENT_MAX:
                buf = buf[-self._RECENT_MAX:]
            self.recent_output = buf


class _TerminalSessionRegistry:
    """acesso_id -> _SharedTerminalSession, apenas para sessões compartilhadas
    explicitamente (opt-in). Conexões normais (a maioria) nunca passam por
    aqui e continuam 1 WebSocket : 1 shell, sem nenhuma mudança de
    comportamento."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}

    def share(self, acesso_id, physical_consumer, label):
        with self._lock:
            session = _SharedTerminalSession(acesso_id, physical_consumer, label)
            self._sessions[acesso_id] = session
            return session

    def get(self, acesso_id):
        with self._lock:
            return self._sessions.get(acesso_id)

    def drop(self, acesso_id, session):
        with self._lock:
            if self._sessions.get(acesso_id) is session:
                del self._sessions[acesso_id]


_terminal_sessions = _TerminalSessionRegistry()


def _wg_peer_ativo(interface_nome: str) -> bool:
    """
    Retorna True se a interface WireGuard tem ao menos um peer com
    handshake recente (< 3 minutos). Usado para detectar se o cliente
    já migrou para a interface isolada antes de usar source-bind routing.
    """
    import subprocess, time
    try:
        r = subprocess.run(
            ['wg', 'show', interface_nome, 'latest-handshakes'],
            capture_output=True, text=True, timeout=2
        )
        if r.returncode != 0:
            return False
        agora = time.time()
        for linha in r.stdout.strip().splitlines():
            partes = linha.split()
            if len(partes) >= 2:
                ts = int(partes[1])
                if ts > 0 and (agora - ts) < 180:
                    return True
        return False
    except Exception:
        return False


def _pty_req_with_modes(shell, term='vt100', width=80, height=24):
    """
    Envia pty-req com POSIX terminal modes completos.
    Paramiko padrão envia modes vazio (bytes()) — alguns SSH servers embarcados
    (Parks, ZTE) crasham ao processar um pty-req sem modes.
    RFC 4254 §8: cada mode é opcode(1B) + value(4B), terminado com TTY_OP_END(0).
    """
    # RFC 4254 §8 opcodes
    TTY_OP_END   = 0
    ECHO         = 53
    ICRNL        = 36   # map CR → NL on input
    ONLCR        = 72   # map NL → CR+NL on output
    ISIG         = 50   # enable signals (INTR, QUIT)
    ICANON       = 51   # canonical input processing
    CS8          = 91   # 8-bit characters
    TTY_OP_ISPEED = 128
    TTY_OP_OSPEED = 129

    modes = (
        struct.pack('>BL', ECHO,          1)     +
        struct.pack('>BL', ICRNL,         1)     +
        struct.pack('>BL', ONLCR,         1)     +
        struct.pack('>BL', ISIG,          1)     +
        struct.pack('>BL', ICANON,        1)     +
        struct.pack('>BL', CS8,           1)     +
        struct.pack('>BL', TTY_OP_ISPEED, 38400) +
        struct.pack('>BL', TTY_OP_OSPEED, 38400) +
        struct.pack('B',   TTY_OP_END)
    )

    m = Message()
    m.add_byte(bytes([98]))          # MSG_CHANNEL_REQUEST = 98
    m.add_int(shell.remote_chanid)
    m.add_string('pty-req')
    m.add_boolean(True)              # want_reply
    m.add_string(term)
    m.add_int(width)
    m.add_int(height)
    m.add_int(0)                     # width_pixels
    m.add_int(0)                     # height_pixels
    m.add_string(modes)
    shell._event_pending()
    shell.transport._send_user_message(m)
    shell._wait_for_event()


class SSHConsumer(ThreadedDispatchMixin, WebsocketConsumer):
    def connect(self):
        user = self.scope.get('user')
        if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
            self.close(code=4001)
            return
        self._crm_user = user
        self.accept()
        self.limpar_recursos()

    # Limite de tamanho do transcript por sessão em memória/DB — sessões que
    # ficam horas abertas despejando "show run" repetidas vezes não podem
    # crescer sem limite. Mantém a cauda (conteúdo mais recente).
    _TRANSCRIPT_MAX = 3_000_000

    def _registrar_saida(self, texto):
        """Acumula o output do equipamento (stdout, ANSI removido) no
        transcript da sessão. Só grava no banco no fechamento da sessão
        (_encerrar_sessao_auditoria) — gravar a cada chunk seria uma UPDATE
        por tecla ecoada, inviável dado o volume de tráfego do terminal."""
        sessao = getattr(self, '_sessao_auditoria', None)
        if not sessao:
            return
        limpo = _ANSI_RE.sub('', texto)
        limpo = ''.join(ch for ch in limpo if ch in ('\t', '\n', '\r') or ch >= ' ')
        if not limpo:
            return
        buf = getattr(self, '_transcript_buf', '') + limpo
        if len(buf) > self._TRANSCRIPT_MAX + 200_000:
            buf = '[...transcript truncado, mostrando o final da sessão...]\n' + buf[-self._TRANSCRIPT_MAX:]
        self._transcript_buf = buf

    def _encerrar_sessao_auditoria(self):
        """Fecha a AcessoSessao ativa desta conexão, se houver. Chamado tanto
        ao trocar de acesso quanto em disconnect() via limpar_recursos()."""
        sessao = getattr(self, '_sessao_auditoria', None)
        if sessao and sessao.status == 'ativa':
            try:
                sessao.encerrada_em = timezone.now()
                sessao.status = 'encerrada'
                sessao.transcript = getattr(self, '_transcript_buf', '')
                sessao.save(update_fields=['encerrada_em', 'status', 'transcript'])
            except Exception as e:
                logger.error(f"❌ Erro ao encerrar sessão de auditoria: {e}")
        self._sessao_auditoria = None
        self._transcript_buf = ''

    # Teclas de seta/home/end saem como CSI (`ESC [ letra`) por padrão, mas
    # viram SS3 (`ESC O letra`) quando o equipamento coloca o terminal em
    # "application cursor keys mode" (comum em paginadores tipo more/less
    # dessas CLIs de rede) — os dois formatos precisam ser reconhecidos.
    _ESC_CSI_LETRA = {'A': 'UP', 'B': 'DOWN', 'C': 'RIGHT', 'D': 'LEFT', 'H': 'HOME', 'F': 'END'}
    _ESC_CSI_TIL   = {'1': 'HOME', '3': 'DELETE', '4': 'END', '7': 'HOME', '8': 'END'}
    _ESC_SS3_LETRA = {'A': 'UP', 'B': 'DOWN', 'C': 'RIGHT', 'D': 'LEFT', 'H': 'HOME', 'F': 'END'}

    def _parse_escape(self, raw, i):
        """Interpreta a sequência ANSI que começa em raw[i]=='\\x1b'.
        Retorna (nome_da_tecla_ou_None, índice_logo_após_a_sequência)."""
        n = len(raw)
        j = i + 1
        if j >= n:
            return None, j
        if raw[j] == '[':
            k = j + 1
            while k < n and raw[k] != '~' and not raw[k].isalpha():
                k += 1
            if k >= n:
                return None, n
            final = raw[k]
            params = raw[j + 1:k]
            k += 1
            if final == '~':
                return self._ESC_CSI_TIL.get(params.split(';')[0]), k
            return self._ESC_CSI_LETRA.get(final), k
        elif raw[j] == 'O' and j + 1 < n:
            return self._ESC_SS3_LETRA.get(raw[j + 1]), j + 2
        else:
            # ESC solto ou sequência não reconhecida — consome só o ESC
            return None, j

    def _registrar_digitacao(self, raw):
        """Reconstrói linhas de comando a partir do stream de teclas cru
        (mesmo texto que chega keystroke-a-keystroke pelo hot path binário
        de receive()), rastreando a posição do cursor para que edições no
        meio da linha (setas, home/end, delete) fiquem no lugar certo em
        vez de grudar no final — e grava cada comando completo (Enter) na
        auditoria."""
        sessao = getattr(self, '_sessao_auditoria', None)
        if not sessao:
            return
        buf = list(getattr(self, '_cmd_buffer', ''))
        cur = getattr(self, '_cmd_cursor', len(buf))
        cur = max(0, min(cur, len(buf)))

        def _flush(sufixo=''):
            nonlocal buf, cur
            cmd = ''.join(buf).strip()
            buf, cur = [], 0
            if cmd:
                try:
                    AcessoComando.objects.create(sessao=sessao, comando=f'{cmd}{sufixo}')
                except Exception as e:
                    logger.error(f"❌ Erro ao gravar comando de auditoria: {e}")

        i, n = 0, len(raw)
        while i < n:
            ch = raw[i]
            if ch == '\x1b':
                tecla, i = self._parse_escape(raw, i)
                if tecla == 'LEFT':
                    cur = max(0, cur - 1)
                elif tecla == 'RIGHT':
                    cur = min(len(buf), cur + 1)
                elif tecla == 'HOME':
                    cur = 0
                elif tecla == 'END':
                    cur = len(buf)
                elif tecla == 'DELETE':
                    if cur < len(buf):
                        del buf[cur]
                elif tecla in ('UP', 'DOWN'):
                    # Recall de histórico do equipamento — o texto recuperado
                    # vem via stdout (echo do device), não temos como
                    # reconstruí-lo a partir do stdin. Descarta o que
                    # tínhamos para não gravar um comando incompleto/errado.
                    buf, cur = [], 0
                continue
            if ch in ('\x7f', '\x08'):
                if cur > 0:
                    del buf[cur - 1]
                    cur -= 1
            elif ch in ('\r', '\n'):
                _flush()
            elif ch == '\x03':
                _flush(sufixo='  [Ctrl+C]')
            elif ch == '\t' or ch >= ' ':
                buf.insert(cur, ch)
                cur += 1
            i += 1
        self._cmd_buffer = ''.join(buf)
        self._cmd_cursor = cur

    def limpar_recursos(self):
        self._encerrar_sessao_auditoria()
        manter_vivo = self._sair_de_sessao_compartilhada()
        self._cmd_buffer = ''
        self._cmd_cursor = 0

        if manter_vivo:
            # Este consumer é quem detém fisicamente a conexão de um terminal
            # compartilhado (shell, thread de leitura, protocol, is_huawei/
            # is_parks...) e ainda há outros espectadores vendo a sessão.
            # NÃO pode zerar nenhum desses atributos aqui: outros consumers
            # continuam lendo-os via `session.physical.<attr>` para escrever
            # no shell (enviar_comando) e redimensionar o PTY (_resize_pty)
            # mesmo depois deste WebSocket específico ter desconectado.
            return

        self._fechar_recursos_fisicos()

        self.protocol         = None
        self.read_thread      = None
        self.is_reading       = False
        self.is_huawei        = False
        self.is_parks         = False
        self.acessoId         = None
        # Tamanho do terminal (cols×rows) informado pelo frontend (xterm/fit).
        # Mantém o PTY do host em sincronia com o que é exibido — sem isso,
        # apps full-screen (nano, htop, vim) desenham na largura errada e o
        # texto fica sobreposto.
        self.term_cols        = 80
        self.term_rows        = 24

    def _fechar_recursos_fisicos(self):
        """Fecha o shell/processo real e para a thread de leitura desta
        conexão. Extraído de limpar_recursos() para poder ser chamado
        também sobre uma outra instância (`session.physical`) quando o
        último espectador de uma sessão compartilhada sai DEPOIS de quem
        originalmente a compartilhou — nesse caso o dono físico já
        desconectou seu próprio WebSocket há tempos e nunca mais teria
        limpar_recursos() chamado sobre si mesmo, deixando o shell e a
        thread de leitura órfãos (conexão SSH/Telnet aberta para sempre)."""
        self.is_reading = False
        if getattr(self, 'read_thread', None) and self.read_thread.is_alive():
            time.sleep(0.2)
            try:
                self.read_thread.join(timeout=1.0)
            except Exception:
                pass

        for attr in ('ssh_process', 'telnet_client', 'tunnel_process',
                     '_paramiko_shell', '_paramiko_dest_transport',
                     '_tunnel_server'):
            obj = getattr(self, attr, None)
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
            setattr(self, attr, None)

        # _paramiko_client é o SSHClient do pool compartilhado — NÃO fechar,
        # apenas liberar a referência para não derrubar outros terminais ativos.
        self._paramiko_client = None

    def disconnect(self, close_code):
        logger.info("🔌 WebSocket desconectando...")
        self.limpar_recursos()
        logger.info("✅ Limpeza concluída")

    def receive(self, text_data=None, bytes_data=None):
        # Hot path: binary frame = raw keypress bytes (sem JSON overhead)
        if bytes_data is not None:
            try:
                self.enviar_comando(bytes_data.decode('utf-8', errors='replace'))
            except Exception as e:
                logger.error(f"❌ Erro ao enviar bytes: {e}")
            return

        try:
            data   = json.loads(text_data)
            action = data.get('action')

            if action == 'connect':
                acesso_id = data.get('acesso_id')
                logger.info(f"📋 Conectar acesso {acesso_id}")
                self.limpar_recursos()
                self._set_term_size(data.get('cols'), data.get('rows'))
                try:
                    acesso = Acesso.objects.get(id=acesso_id)
                except Acesso.DoesNotExist:
                    self.send_error('Acesso não encontrado')
                    return
                if not self._usuario_pode_acessar(acesso):
                    self.send_error('Você não tem permissão para acessar este host.')
                    return
                sessao_compartilhada = None if data.get('independente') else _terminal_sessions.get(acesso_id)
                if sessao_compartilhada:
                    self._entrar_em_sessao_compartilhada(sessao_compartilhada)
                else:
                    self.conectar_acesso(acesso_id)

            elif action == 'command':
                command = data.get('command', '')
                self.enviar_comando(command)

            elif action == 'resize':
                # Frontend redimensionou o terminal — repassa ao PTY do host.
                if self._set_term_size(data.get('cols'), data.get('rows')):
                    self._resize_pty(self.term_cols, self.term_rows)

            elif action == 'share_start':
                self._iniciar_compartilhamento()

            elif action == 'share_stop':
                self._parar_compartilhamento()

            elif action == 'criar_link_externo':
                self._criar_link_externo(data.get('minutos'))

            elif action == 'revogar_link_externo':
                self._revogar_link_externo(data.get('token'))

        except json.JSONDecodeError as e:
            self.send_error(f"Erro ao parsear JSON: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Erro na função receive: {str(e)}")
            self.send_error(f"Erro: {str(e)}")

    # =========================================================
    # Tamanho do terminal (cols×rows) — mantém o PTY do host em
    # sincronia com o xterm.js exibido no navegador.
    # =========================================================
    def _set_term_size(self, cols, rows):
        """Armazena cols/rows válidos vindos do frontend. Retorna True se mudou."""
        try:
            c, r = int(cols), int(rows)
        except (TypeError, ValueError):
            return False
        # Limites de sanidade
        c = max(20, min(c, 500))
        r = max(5, min(r, 200))
        if c == self.term_cols and r == self.term_rows:
            return False
        self.term_cols, self.term_rows = c, r
        return True

    def _pty_dims(self):
        """Dimensões a usar ao abrir o PTY. Parks/ZTE usam tamanho fixo
        conservador (firmware embarcado é sensível); demais usam o real."""
        if getattr(self, 'is_parks', False) and not getattr(self, 'is_huawei', False):
            return 80, 24
        return self.term_cols, self.term_rows

    def _resize_pty(self, cols, rows):
        """Aplica o novo tamanho ao canal/pty ativo (Paramiko ou pexpect).
        Em sessão compartilhada, redimensiona o PTY de quem detém a conexão
        física — o último espectador a redimensionar a janela "vence"."""
        session = getattr(self, '_shared_session', None)
        alvo = session.physical if session else self
        sh = getattr(alvo, '_paramiko_shell', None)
        if sh is not None:
            try:
                sh.resize_pty(width=cols, height=rows)
                return
            except Exception as e:
                logger.debug(f"resize_pty (paramiko) falhou: {e}")
        pex = getattr(alvo, 'ssh_process', None)
        if pex is not None and hasattr(pex, 'setwinsize'):
            try:
                pex.setwinsize(rows, cols)   # pexpect: (linhas, colunas)
            except Exception as e:
                logger.debug(f"setwinsize (pexpect) falhou: {e}")

    def _vpn_cobre_ip(self, cliente, host):
        """
        Verifica se existe VPN WireGuard com peer configurado que cobre o IP do host.
        Retorna o objeto VPNWireGuard (com servidor_ip_local) ou None.
        Compatível com código legado que compara com True via `if _vpn_cobre_ip(...)`.
        """
        try:
            import ipaddress as _ipa
            from .models import VPNWireGuard

            vpns = VPNWireGuard.objects.filter(cliente=cliente, ativo=True, peer_no_servidor=True)
            host_ip = _ipa.ip_address(host)

            for vpn in vpns:
                for rede_str in vpn.redes_lista():
                    try:
                        if host_ip in _ipa.ip_network(rede_str, strict=False):
                            logger.info(f"✅ VPN WG cobre {host} via {vpn.nome} (if={vpn.interface_nome} src={vpn.servidor_ip_local})")
                            return vpn   # objeto truthy — compatível com `if vpn:`
                    except ValueError:
                        pass
        except Exception as e:
            logger.warning(f"⚠️ Erro ao verificar VPN: {e}")
        return None

    def _tunel_ovpn_cobre_ip(self, cliente, host):
        """
        Verifica se existe túnel OpenVPN (aba Túneis) que cobre o IP do host.
        Ao contrário do WireGuard isolado, não precisa de source-bind — é um
        único daemon/interface compartilhado (tun-crm) com rota já correta
        no kernel via iroute por cliente, então a conexão direta já sai pelo
        caminho certo automaticamente.
        """
        try:
            import ipaddress as _ipa
            from .models import VPNOpenVPN

            tuneis = VPNOpenVPN.objects.filter(cliente=cliente, ativo=True, cert_emitido=True)
            host_ip = _ipa.ip_address(host)

            for tunel in tuneis:
                for rede_str in tunel.redes_lista():
                    try:
                        if host_ip in _ipa.ip_network(rede_str, strict=False):
                            logger.info(f"✅ Túnel OpenVPN cobre {host} via {tunel.nome}")
                            return tunel
                    except ValueError:
                        pass
        except Exception as e:
            logger.warning(f"⚠️ Erro ao verificar túnel OpenVPN: {e}")
        return None

    # =========================================================
    # Terminal compartilhado (opt-in)
    # =========================================================
    def _usuario_pode_acessar(self, acesso):
        """Mesma regra de `listar_acessos_terminal` (views.py): usa
        usuario.perms.pode_acessar_cliente, que cobre admin (vê tudo),
        Consultor/Operador (clientes da própria Instancia) e portal do
        cliente final (vínculo direto). Antes desta checagem, conectar_acesso()
        confiava cegamente no acesso_id vindo do frontend — qualquer
        autenticado podia abrir o terminal de qualquer host cadastrado, de
        qualquer cliente."""
        user = getattr(self, '_crm_user', None)
        if not user:
            return False
        return _perms.pode_acessar_cliente(user, acesso.cliente)

    def _label_usuario(self):
        user = getattr(self, '_crm_user', None)
        if not user:
            return 'Usuário'
        nome = (user.get_full_name() or '').strip()
        return nome or user.get_username()

    def _broadcast_para(self, session, payload):
        for consumer, _label in session.snapshot_viewers():
            try:
                consumer.send_json(payload)
            except Exception:
                pass

    def _iniciar_compartilhamento(self):
        """Chamado pelo usuário que já está conectado normalmente a um Acesso
        e decide compartilhar sua sessão viva com outros usuários autorizados."""
        if not getattr(self, 'acessoId', None) or not getattr(self, 'is_reading', False):
            self.send_error('Conecte-se ao terminal antes de compartilhar.')
            return
        if getattr(self, '_shared_session', None):
            self.send_error('Esta sessão já está compartilhada.')
            return
        label = self._label_usuario()
        session = _terminal_sessions.share(self.acessoId, self, label)
        self._shared_session = session
        self.send_json({'type': 'share_started', 'acesso_id': self.acessoId, 'viewers': [label]})

    def _parar_compartilhamento(self):
        """Encerra o compartilhamento: os demais espectadores são avisados e
        desconectados da sessão viva (podem reabrir o terminal, mas como uma
        conexão independente); a conexão física deste usuário continua."""
        session = getattr(self, '_shared_session', None)
        if not session or session.physical is not self:
            self.send_error('Nenhuma sessão compartilhada ativa para encerrar.')
            return
        for consumer, _label in session.snapshot_viewers():
            if consumer is self:
                continue
            try:
                consumer.send_json({'type': 'share_ended', 'message': 'O host encerrou o compartilhamento desta sessão.'})
            except Exception:
                pass
            session.remove_viewer(consumer)
            # Sem isso, o espectador expulso continuaria com `_shared_session`
            # apontando pra esta sessão — enviar_comando() ainda redirecionaria
            # a escrita para `session.physical` (o shell continua vivo), mas
            # ele não está mais em `session.viewers` então não recebe mais
            # nenhum output: ficaria "digitando às cegas" sem ver resposta.
            consumer._shared_session = None
        _terminal_sessions.drop(session.acesso_id, session)
        self._shared_session = None
        self.send_json({'type': 'share_stopped'})

    _LINK_EXTERNO_MIN_MINUTOS = 5
    _LINK_EXTERNO_MAX_MINUTOS = 240

    def _criar_link_externo(self, minutos):
        """Gera um link temporário (TerminalLinkExterno) para compartilhar
        este terminal com alguém de fora do CRM (sem login) — ex: suporte de
        fabricante durante uma chamada. Garante que a sessão esteja
        compartilhada primeiro (senão o link não teria a quem se anexar)."""
        user = getattr(self, '_crm_user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            self.send_error('Apenas usuários do CRM podem gerar links externos.')
            return

        session = getattr(self, '_shared_session', None)
        if not session:
            self._iniciar_compartilhamento()
            session = getattr(self, '_shared_session', None)
            if not session:
                return   # _iniciar_compartilhamento já enviou o erro

        try:
            mins = int(minutos)
        except (TypeError, ValueError):
            mins = 30
        mins = max(self._LINK_EXTERNO_MIN_MINUTOS, min(mins, self._LINK_EXTERNO_MAX_MINUTOS))

        link = TerminalLinkExterno.objects.create(
            acesso_id=session.acesso_id,
            criado_por=user,
            expira_em=timezone.now() + timedelta(minutes=mins),
        )
        self.send_json({
            'type': 'link_externo_criado',
            'token': str(link.id),
            'expira_em': link.expira_em.isoformat(),
            'minutos': mins,
        })

    def _revogar_link_externo(self, token):
        """Revoga um link antes do prazo — qualquer visitante já conectado
        por ele é avisado e desconectado imediatamente."""
        user = getattr(self, '_crm_user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            self.send_error('Apenas usuários do CRM podem revogar links externos.')
            return
        try:
            link = TerminalLinkExterno.objects.get(id=token)
        except (TerminalLinkExterno.DoesNotExist, ValueError, ValidationError):
            self.send_error('Link não encontrado.')
            return
        if link.criado_por_id and link.criado_por_id != user.id and not (user.is_staff or user.is_superuser):
            self.send_error('Você só pode revogar links que você mesmo criou.')
            return

        link.revogado = True
        link.save(update_fields=['revogado'])

        session = _terminal_sessions.get(link.acesso_id)
        if session:
            for consumer, _label in session.snapshot_viewers():
                if getattr(consumer, '_link_externo_id', None) == link.id:
                    try:
                        consumer.send_json({'type': 'share_ended', 'message': 'O link de acesso externo foi revogado.'})
                        consumer.close(code=4008)
                    except Exception:
                        pass
        self.send_json({'type': 'link_externo_revogado', 'token': str(link.id)})

    def _entrar_em_sessao_compartilhada(self, session, link_externo=None):
        """Anexa este WebSocket a uma sessão já compartilhada por outro
        usuário — sem abrir nenhuma conexão SSH/Telnet nova, só passa a
        receber o mesmo output e poder escrever no mesmo shell físico.
        `link_externo`, quando informado (visitante externo via token),
        fica registrado na auditoria e permite revogar/expirar este
        visitante especificamente depois."""
        try:
            acesso = Acesso.objects.get(id=session.acesso_id)
        except Acesso.DoesNotExist:
            self.send_error('Acesso não encontrado')
            return
        if not self._usuario_pode_acessar(acesso):
            self.send_error('Você não tem permissão para acessar este host.')
            return

        label = self._label_usuario()
        session.add_viewer(self, label)
        self._shared_session = session
        self.acessoId         = session.acesso_id
        self.protocol         = session.physical.protocol
        self.is_huawei        = session.physical.is_huawei
        self.is_parks         = session.physical.is_parks
        if link_externo is not None:
            self._link_externo_id = link_externo.id

        self._sessao_auditoria = AcessoSessao.objects.create(
            acesso=acesso,
            usuario=getattr(self, '_crm_user', None),
            link_externo=link_externo,
            tipo=self.protocol,
            ip_origem=(self.scope.get('client') or [None])[0],
        )
        self._cmd_buffer, self._cmd_cursor, self._transcript_buf = '', 0, ''

        self.send_json({
            'type': 'connected',
            'shared': True,
            'message': f'✓ Conectado à sessão compartilhada de {session.owner_label}',
        })
        if session.recent_output:
            self._registrar_saida(session.recent_output)
            try:
                self.send(bytes_data=session.recent_output.encode('utf-8'))
            except Exception:
                pass
        self._broadcast_para(session, {
            'type': 'share_info',
            'message': f'👀 {label} entrou no terminal compartilhado.',
            'viewers': [v for _c, v in session.snapshot_viewers()],
        })

    def _sair_de_sessao_compartilhada(self):
        """Remove este consumer de uma sessão compartilhada, se houver.
        Retorna True quando este consumer é quem detém fisicamente a conexão
        E ainda restam espectadores — nesse caso `limpar_recursos()` NÃO deve
        fechar o shell real, pois outros ainda o estão usando."""
        session = getattr(self, '_shared_session', None)
        if not session:
            return False

        label = self._label_usuario()
        ainda_ativa = session.remove_viewer(self)
        manter_vivo = ainda_ativa and session.physical is self

        if not manter_vivo:
            # Consumers comuns (e o físico quando ninguém mais resta) largam
            # a referência; o físico com espectadores restantes PRECISA manter
            # `_shared_session` para que send_output() continue encontrando a
            # lista de espectadores a partir da thread de leitura em segundo
            # plano, mesmo depois deste WebSocket ter desconectado.
            self._shared_session = None

        if not ainda_ativa:
            _terminal_sessions.drop(session.acesso_id, session)
            if session.physical is not self:
                # O dono físico já havia desconectado antes (ver comentário em
                # _fechar_recursos_fisicos) — como este era o último
                # espectador restante, é responsabilidade dele encerrar a
                # conexão real que ficou órfã.
                session.physical._fechar_recursos_fisicos()
            return False

        self._broadcast_para(session, {
            'type': 'share_info',
            'message': f'👋 {label} saiu do terminal compartilhado.',
            'viewers': [v for _c, v in session.snapshot_viewers()],
        })
        return manter_vivo

    def conectar_acesso(self, acesso_id):
        try:
            self.acessoId = acesso_id
            acesso        = Acesso.objects.get(id=acesso_id)
            if not self._usuario_pode_acessar(acesso):
                self.send_error('Você não tem permissão para acessar este host.')
                return
            protocol      = self.detect_protocol(acesso.porta)
            self.protocol = protocol

            self._sessao_auditoria = AcessoSessao.objects.create(
                acesso=acesso,
                usuario=getattr(self, '_crm_user', None),
                tipo=protocol,
                ip_origem=(self.scope.get('client') or [None])[0],
            )
            self._cmd_buffer = ''
            self._cmd_cursor = 0
            self._transcript_buf = ''

            _fab = ''
            if acesso.modelo and acesso.modelo.fabricante:
                _fab = acesso.modelo.fabricante.lower()
            elif acesso.tipo:
                _fab = acesso.tipo.lower()
            self.is_huawei = 'huawei' in _fab
            # is_parks é refinado pelo banner SSH em connect_ssh_via_proxy/_connect_ssh_paramiko_direct
            self.is_parks  = any(k in _fab for k in ('parks', 'zte'))

            logger.info(f"🔗 Protocolo: {protocol.upper()} | Huawei: {self.is_huawei}")

            is_private = self.is_private_ip(acesso.host)
            is_cgnat   = getattr(acesso, 'tipo', '') == 'CGNAT'

            if is_private:
                # IP privado: VPN WireGuard, túnel OpenVPN ou proxy
                vpn = self._vpn_cobre_ip(acesso.cliente, acesso.host)
                tunel_ovpn = None if vpn else self._tunel_ovpn_cobre_ip(acesso.cliente, acesso.host)
                if vpn:
                    # Usa source bind da interface isolada SOMENTE se ela tiver
                    # handshake ativo (cliente já migrou). Caso contrário, usa
                    # wg0 via rota default (legado) sem source bind.
                    iface = vpn.interface_nome or 'wg0'
                    isolada_ativa = (
                        iface not in ('wg0', '') and
                        vpn.servidor_ip_local and
                        _wg_peer_ativo(iface)
                    )
                    src_ip = vpn.servidor_ip_local if isolada_ativa else None
                    modo = f'{iface} isolada' if isolada_ativa else 'wg0 legado'
                    self.send_json({'type': 'info', 'message': f'🔒 Conectando via VPN WireGuard ({modo})...'})
                    if protocol == 'ssh':
                        self.connect_ssh(acesso, source_ip=src_ip)
                    else:
                        self.connect_telnet(acesso)
                elif tunel_ovpn:
                    # Servidor único compartilhado — rota já correta no kernel
                    # via iroute do cliente, sem precisar de source-bind.
                    self.send_json({'type': 'info', 'message': f'🔒 Conectando via Túnel OpenVPN ({tunel_ovpn.nome})...'})
                    if protocol == 'ssh':
                        self.connect_ssh(acesso)
                    else:
                        self.connect_telnet(acesso)
                else:
                    if protocol == 'ssh':
                        # Parks OLTs: Paramiko invoke_shell não é compatível
                        # (firmware crasha). Usar pexpect + ssh ProxyCommand.
                        if self.is_parks:
                            self.connect_ssh_parks_proxy(acesso)
                        else:
                            self.connect_ssh_via_proxy(acesso)
                    else:
                        self.connect_telnet_via_proxy(acesso)
            elif is_cgnat:
                # IP público CGNAT: tenta direto primeiro, fallback para proxy
                self.send_json({'type': 'info', 'message': '🔄 Tentando conexão direta...'})
                try:
                    if protocol == 'ssh':
                        self.connect_ssh(acesso)
                    else:
                        self.connect_telnet(acesso)
                except Exception:
                    self.send_json({'type': 'info', 'message': '↩️ Direto falhou, tentando via proxy...'})
                    if protocol == 'ssh':
                        self.connect_ssh_via_proxy(acesso)
                    else:
                        self.connect_telnet_via_proxy(acesso)
            else:
                # IP público normal: direto
                if protocol == 'ssh':
                    self.connect_ssh(acesso)
                else:
                    self.connect_telnet(acesso)

        except Acesso.DoesNotExist:
            self.send_error('Acesso não encontrado')
        except Exception as e:
            logger.error(f"❌ Erro ao conectar: {str(e)}")
            self.send_error(f'Erro ao conectar: {str(e)}')

    def enviar_comando(self, command):
        """
        BACKSPACE FIX: xterm.js envia DEL (\x7f); equipamentos esperam BS (\x08).
        Usa os.write() direto no fd do pty para SSH direto, ou channel.send() para proxy.

        Em sessão compartilhada, quem digita pode ser um mero espectador (sem
        shell próprio) — o comando é escrito no shell de quem detém a conexão
        física (`alvo`), mas a auditoria (_registrar_digitacao) fica atribuída
        a este usuário (self), não ao dono da conexão.
        """
        session = getattr(self, '_shared_session', None)
        alvo = session.physical if session else self
        try:
            if alvo.protocol in ('ssh', 'telnet'):
                self._registrar_digitacao(command)

            command = command.replace('\x7f', '\x08')

            if alvo.protocol == 'ssh':
                if getattr(alvo, '_paramiko_shell', None):
                    alvo._paramiko_shell.send(command.encode('utf-8'))
                elif alvo.ssh_process:
                    os.write(alvo.ssh_process.child_fd, command.encode('utf-8'))
            elif alvo.protocol == 'telnet':
                if alvo.telnet_client:
                    alvo.telnet_client.write(command.encode('utf-8'))
        except Exception as e:
            logger.error(f"❌ Erro ao enviar: {str(e)}")
            self.send_error(f'Erro ao enviar comando: {str(e)}')

    def is_private_ip(self, host):
        try:
            return ipaddress.ip_address(host).is_private
        except ValueError:
            return False

    def detect_protocol(self, porta):
        porta_int = int(porta)
        if porta_int == 22:
            return 'ssh'
        elif porta_int == 23:
            return 'telnet'
        elif porta_int in [2222, 8022, 10022, 9022]:
            return 'ssh'
        elif porta_int in [2323, 9023]:
            return 'telnet'
        else:
            return 'telnet' if porta_int < 1024 else 'ssh'

    def get_active_proxy(self, cliente):
        proxy = ProxyServer.objects.filter(cliente=cliente, ativo=True).first()
        if not proxy:
            raise Exception(f"Nenhum proxy SSH ativo para {cliente.nome_empresa}")
        return proxy

    # =========================================================
    # Criar túnel via Paramiko
    # =========================================================

    def _criar_tunel_paramiko(self, proxy, host_destino, porta_destino, timeout_conn=8):
        logger.info(f"⚡ [PARAMIKO] Conectando ao proxy {proxy.host}:{proxy.porta}...")

        def _nova_conexao_proxy():
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(
                hostname=proxy.host,
                port=int(proxy.porta),
                username=proxy.usuario,
                password=proxy.senha,
                timeout=timeout_conn,
                look_for_keys=False,
                allow_agent=False,
                banner_timeout=timeout_conn,
            )
            _proxy_pool.put(proxy, c)
            return c

        # Reutiliza conexão já aberta com o proxy em vez de sempre negociar
        # um handshake SSH novo (era o principal motivo do Winbox/VNC/telnet
        # via proxy demorarem para conectar).
        ssh_client = _proxy_pool.get(proxy)
        if ssh_client:
            logger.info(f"♻️ [PARAMIKO] Proxy reutilizado do pool: {proxy.host}:{proxy.porta}")
        else:
            ssh_client = _nova_conexao_proxy()
            logger.info("✅ [PARAMIKO] Proxy conectado (novo)!")
        self._paramiko_client = ssh_client

        local_port  = self.find_available_port()
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(('127.0.0.1', local_port))
        server_sock.listen(5)
        server_sock.settimeout(1)
        self._tunnel_server = server_sock

        transport = ssh_client.get_transport()

        def _forward(client_socket):
            try:
                channel = transport.open_channel(
                    'direct-tcpip',
                    (host_destino, int(porta_destino)),
                    ('127.0.0.1', local_port),
                    timeout=timeout_conn,
                )

                def _pipe(src, dst):
                    try:
                        while True:
                            data = src.recv(65536)
                            if not data:
                                break
                            dst.sendall(data)
                    except:
                        pass
                    finally:
                        try: src.close()
                        except: pass
                        try: dst.close()
                        except: pass

                t1 = threading.Thread(target=_pipe, args=(client_socket, channel), daemon=True)
                t2 = threading.Thread(target=_pipe, args=(channel, client_socket), daemon=True)
                t1.start()
                t2.start()
            except Exception as e:
                logger.error(f"❌ [TUNNEL] Forward error: {e}")
                try: client_socket.close()
                except: pass

        def _accept_loop():
            while True:
                try:
                    client_sock, _ = server_sock.accept()
                    threading.Thread(target=_forward, args=(client_sock,), daemon=True).start()
                except socket.timeout:
                    # ✅ FIX: Sai APENAS quando _tunnel_server foi explicitamente
                    # zerado pelo limpar_recursos(). Antes saía quando
                    # is_reading=False e ssh_process=None, que é exatamente
                    # o estado durante o boot do túnel — causava race condition
                    # onde o accept loop encerrava antes do pexpect conectar.
                    if self._tunnel_server is None:
                        break
                    continue
                except Exception:
                    break

        threading.Thread(target=_accept_loop, daemon=True).start()

        # Testar canal antes de retornar. Se a conexão veio do pool e estava
        # morta (ex: NAT derrubou por ociosidade), reconecta uma única vez
        # em vez de falhar a sessão inteira — `_forward` acima referencia
        # `transport` por closure, então a reatribuição abaixo é enxergada
        # por qualquer conexão de cliente aceita depois deste ponto.
        try:
            test_channel = transport.open_channel(
                'direct-tcpip',
                (host_destino, int(porta_destino)),
                ('127.0.0.1', local_port),
                timeout=timeout_conn,
            )
            test_channel.close()
            logger.info(f"✅ [PARAMIKO] Canal testado — túnel OK na porta {local_port}")
        except Exception as e:
            logger.warning(f"⚠️ [TUNNEL] Canal do pool falhou ({e}), reconectando ao proxy...")
            _proxy_pool.remove(proxy)
            try:
                ssh_client = _nova_conexao_proxy()
                self._paramiko_client = ssh_client
                transport = ssh_client.get_transport()
                test_channel = transport.open_channel(
                    'direct-tcpip',
                    (host_destino, int(porta_destino)),
                    ('127.0.0.1', local_port),
                    timeout=timeout_conn,
                )
                test_channel.close()
                logger.info(f"✅ [PARAMIKO] Canal testado após reconexão — túnel OK na porta {local_port}")
            except Exception as e2:
                raise Exception(
                    f"Proxy conectou mas não conseguiu abrir canal para "
                    f"{host_destino}:{porta_destino} — {e2}"
                )

        return local_port

    # =========================================================
    # SSH direto
    # =========================================================

    def connect_ssh(self, acesso, source_ip=None):
        try:
            logger.info(f"🔗 SSH: {acesso.host}:{acesso.porta} src={source_ip}")

            terminal_type = "xterm-256color"
            ssh_cmd       = self._build_ssh_cmd(acesso.usuario, acesso.host, acesso.porta, source_ip=source_ip)

            env        = os.environ.copy()
            env['TERM'] = terminal_type

            self.ssh_process = pexpect.spawn(
                ssh_cmd, timeout=15, encoding=None, maxread=262144, env=env
            )

            self._authenticate_ssh_process(self.ssh_process, acesso.senha)

            if self.is_huawei:
                self._disable_huawei_paging()

            self.send_json({
                'type':    'connected',
                'message': (
                    f'✓ Conectado SSH a {acesso.host}:{acesso.porta}'
                    + (" [HUAWEI]" if self.is_huawei else "")
                )
            })

            self.is_reading  = True
            self.read_thread = threading.Thread(target=self.read_ssh_output, daemon=True)
            self.read_thread.start()

        except Exception as e:
            err_msg = str(e)
            # Broken pipe / EOF after password = OpenSSH incompatível com o
            # firmware do equipamento (ex: ZTE SSH-2.0-ZTE_SSH.1.0).
            # Paramiko usa sua própria implementação do protocolo e funciona
            # mesmo quando o OpenSSH do sistema falha nesses casos.
            _fallback_triggers = (
                'encerrou conexão após envio de senha',
                'Broken pipe',
                'broken pipe',
            )
            if any(t in err_msg for t in _fallback_triggers):
                logger.warning(f"⚠️ pexpect falhou ({err_msg}). Tentando Paramiko direto...")
                # Garante que o processo pexpect (OpenSSH) está morto e o TCP fechado
                # antes de tentar nova conexão — alguns equipamentos (ex: ZTE) limitam
                # sessões concorrentes por usuário.
                try:
                    if self.ssh_process:
                        self.ssh_process.close(force=True)
                        self.ssh_process = None
                except Exception:
                    pass
                time.sleep(1.5)   # aguarda ZTE liberar a sessão anterior
                try:
                    self._connect_ssh_paramiko_direct(acesso)
                    return
                except Exception as e2:
                    logger.error(f"❌ SSH Paramiko direto também falhou: {e2}")
                    self.send_error(f'Erro SSH: {err_msg} (Paramiko fallback: {e2})')
                    return
            logger.error(f"❌ SSH: {err_msg}")
            self.send_error(f'Erro SSH: {err_msg}')

    def _connect_ssh_paramiko_direct(self, acesso):
        """
        Conexão SSH direta usando Paramiko.Transport (sem pexpect / OpenSSH binário).
        Usado como fallback quando o OpenSSH do sistema é incompatível com o
        firmware do equipamento (ex: ZTE SSH-2.0-ZTE_SSH.1.0 — Broken pipe).

        Usa Transport diretamente (como connect_ssh_via_proxy) para ter controle
        total sobre a negociação de algoritmos e autenticação.
        """
        tempo_inicio = time.time()
        logger.info(f"🔗 [PARAMIKO DIRECT] {acesso.host}:{acesso.porta}")

        # 1. Abrir socket TCP direto para o equipamento
        sock = socket.create_connection((acesso.host, int(acesso.porta)), timeout=15)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass

        # 2. Criar transporte Paramiko sobre o socket
        transport = paramiko.Transport(sock,
                                       default_window_size=2**23,
                                       default_max_packet_size=2**15)
        transport.use_compression(False)

        # Priorizar KEX leve (group14 = 2048-bit DH) sobre group16 (4096-bit).
        # ZTE OLTs têm timeout de KEX curto — group16-sha512 demora 2-5s no
        # CPU embarcado e a conexão cai antes da auth (PuTTY usa group14 e ok).
        # group16/group18 ficam por último para compatibilidade com outros vendors.
        transport._preferred_kex = _ZTE_PREFERRED_KEX

        transport.start_client(timeout=15)
        self._paramiko_dest_transport = transport

        # Detectar vendor pelo banner SSH
        remote_ver = (transport.remote_version or '').lower()
        if 'openssh' in remote_ver:
            self.is_parks = False
        elif any(k in remote_ver for k in ('parks', 'zte')):
            self.is_parks = True
        if 'huawei' in remote_ver:
            self.is_huawei = True
            self.is_parks  = False

        # 3. Autenticar com senha
        try:
            transport.auth_password(acesso.usuario, acesso.senha)
        except paramiko.AuthenticationException:
            raise Exception("Senha incorreta ou acesso negado no equipamento")
        if not transport.is_authenticated():
            raise Exception("Autenticação Paramiko falhou")

        # 4. Abrir shell interativo
        terminal_type = "vt100" if (self.is_parks and not self.is_huawei) else "xterm-256color"
        _pw, _ph = self._pty_dims()
        shell = transport.open_session()
        shell.get_pty(term=terminal_type, width=_pw, height=_ph)
        shell.invoke_shell()
        self._paramiko_shell = shell

        tempo_total = time.time() - tempo_inicio
        self.send_json({
            'type': 'connected',
            'message': (
                f'✓ Conectado SSH a {acesso.host}:{acesso.porta} ({tempo_total:.1f}s)'
                + (" [HUAWEI]" if self.is_huawei else "")
            )
        })

        self.is_reading  = True
        self.read_thread = threading.Thread(target=self._read_paramiko_shell, daemon=True)
        self.read_thread.start()

    # =========================================================
    # SSH via proxy — paramiko end-to-end (sem pexpect, sem socket local)
    # =========================================================

    def connect_ssh_via_proxy(self, acesso):
        tempo_inicio = time.time()
        try:
            proxy = self.get_active_proxy(acesso.cliente)
            logger.info(f"🔗 SSH via proxy (paramiko e2e): {proxy.nome}")
            self.send_json({'type': 'info', 'message': f'⚡ Conectando via proxy {proxy.nome}...'})

            # 1. Reutilizar conexão do pool ou criar nova
            proxy_client = _proxy_pool.get(proxy)
            if proxy_client:
                logger.info(f"♻️ Proxy reutilizado do pool: {proxy.host}:{proxy.porta}")
            else:
                proxy_client = paramiko.SSHClient()
                proxy_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                proxy_client.connect(
                    hostname=proxy.host, port=int(proxy.porta),
                    username=proxy.usuario, password=proxy.senha,
                    timeout=10, look_for_keys=False, allow_agent=False,
                    banner_timeout=10, compress=False,
                )
                # Desabilitar Nagle no TCP do proxy — reduz latência por tecla
                t = proxy_client.get_transport()
                if t and t.sock:
                    try:
                        t.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    except Exception:
                        pass
                _proxy_pool.put(proxy, proxy_client)
                logger.info(f"✅ Proxy conectado (novo): {proxy.host}:{proxy.porta}")
            self._paramiko_client = proxy_client

            # 2. Abrir canal direct-tcpip para o destino
            # Se o canal falhar (conexão do pool expirou), reconectar uma vez
            proxy_transport = proxy_client.get_transport()
            try:
                dest_sock = proxy_transport.open_channel(
                    'direct-tcpip',
                    (acesso.host, int(acesso.porta)),
                    ('127.0.0.1', 0),
                    timeout=10,
                )
            except Exception:
                # Conexão do pool estava morta — criar nova
                _proxy_pool.remove(proxy)
                proxy_client = paramiko.SSHClient()
                proxy_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                proxy_client.connect(
                    hostname=proxy.host, port=int(proxy.porta),
                    username=proxy.usuario, password=proxy.senha,
                    timeout=10, look_for_keys=False, allow_agent=False,
                    banner_timeout=10, compress=False,
                )
                t2 = proxy_client.get_transport()
                if t2 and t2.sock:
                    try:
                        t2.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    except Exception:
                        pass
                _proxy_pool.put(proxy, proxy_client)
                self._paramiko_client = proxy_client
                proxy_transport = proxy_client.get_transport()
                dest_sock = proxy_transport.open_channel(
                    'direct-tcpip',
                    (acesso.host, int(acesso.porta)),
                    ('127.0.0.1', 0),
                    timeout=10,
                )
            logger.info(f"✅ Canal aberto → {acesso.host}:{acesso.porta}")

            # 3. Criar transporte SSH sobre o canal
            dest_transport = paramiko.Transport(dest_sock,
                                                default_window_size=2**23,
                                                default_max_packet_size=2**15)
            dest_transport.use_compression(False)
            # Mesmo ajuste de _connect_ssh_paramiko_direct e do FirmwareDownloadConsumer
            # (ver _ZTE_PREFERRED_KEX acima): sem isso, o Transport tenta os grupos
            # pesados primeiro (group16/group-exchange) e em equipamentos embarcados
            # lentos a negociação passa dos 10s do start_client() abaixo — que, se
            # ainda estiver ativo (thread não morreu, só lento), retorna sem erro
            # e só explode depois no auth_password() com "No existing session"
            # (visto ao vivo num switch Huawei S5735, 10.40.40.38, banner sem client
            # version = stack SSH embarcado mínimo).
            dest_transport._preferred_kex = _ZTE_PREFERRED_KEX
            dest_transport.start_client(timeout=10)
            self._paramiko_dest_transport = dest_transport

            # Detectar vendor pelo banner SSH
            # OpenSSH (qualquer versão) suporta xterm-256color — não sobrescrever
            # Parks e ZTE usam SSH server embarcado que crasha com xterm-256color
            remote_ver = (dest_transport.remote_version or '').lower()
            if 'openssh' in remote_ver:
                self.is_parks = False   # OpenSSH funciona com qualquer terminal
            elif any(k in remote_ver for k in ('parks', 'zte')):
                self.is_parks = True
            if 'huawei' in remote_ver:
                self.is_huawei = True
                self.is_parks  = False

            # 4. Autenticar no equipamento
            try:
                dest_transport.auth_password(acesso.usuario, acesso.senha)
            except paramiko.AuthenticationException:
                raise Exception("Senha incorreta ou acesso negado no equipamento")
            if not dest_transport.is_authenticated():
                raise Exception("Autenticação falhou no equipamento")

            # 5. Abrir shell interativo
            # Parks (SSH-2.0-Parks): Paramiko envia pty-req com terminal modes vazio
            # → SSH server embarcado crasha. Enviamos modes POSIX completos via helper.
            if self.is_parks and not self.is_huawei:
                pty_term, pty_w, pty_h = "vt100", 80, 24
            else:
                pty_term = "xterm-256color"
                pty_w, pty_h = self.term_cols, self.term_rows
            logger.info(f"🖥️ PTY: term={pty_term!r} size={pty_w}x{pty_h} is_parks={self.is_parks} remote_ver={remote_ver!r}")
            shell = dest_transport.open_session()
            if self.is_parks and not self.is_huawei:
                _pty_req_with_modes(shell, term=pty_term, width=pty_w, height=pty_h)
            else:
                shell.get_pty(term=pty_term, width=pty_w, height=pty_h)
            shell.invoke_shell()
            self._paramiko_shell = shell

            tempo_total = time.time() - tempo_inicio
            self.send_json({
                'type': 'connected',
                'message': (
                    f'✓ SSH a {acesso.host}:{acesso.porta} via {proxy.nome} ({tempo_total:.1f}s)'
                    + (" [HUAWEI]" if self.is_huawei else "")
                )
            })

            self.is_reading  = True
            self.read_thread = threading.Thread(target=self._read_paramiko_shell, daemon=True)
            self.read_thread.start()

        except Exception as e:
            tempo_total = time.time() - tempo_inicio
            logger.error(f"❌ SSH via proxy falhou em {tempo_total:.1f}s: {str(e)}")
            self.send_error(f'Erro SSH via proxy: {str(e)}')
            self.limpar_recursos()

    # ─────────────────────────────────────────────────────────────────────────
    # Parks OLT: pexpect + ProxyCommand (Paramiko invoke_shell não é compatível)
    # ─────────────────────────────────────────────────────────────────────────

    def connect_ssh_parks_proxy(self, acesso):
        """
        Conecta a OLTs Parks via pexpect usando o binário ssh do sistema com ProxyCommand.
        Paramiko invoke_shell crasha o firmware Parks — o ssh real negocia corretamente.
        """
        tempo_inicio = time.time()
        try:
            proxy = self.get_active_proxy(acesso.cliente)
            logger.info(f"🔗 SSH Parks via pexpect+proxy: {proxy.nome}")
            self.send_json({'type': 'info', 'message': f'⚡ Conectando Parks via proxy {proxy.nome}...'})

            porta = int(acesso.porta) if acesso.porta else 22

            # ProxyCommand: ssh -W host:port proxy
            proxy_cmd = (
                f"ssh -o StrictHostKeyChecking=no "
                f"-o UserKnownHostsFile=/dev/null "
                f"-o IdentitiesOnly=yes "
                f"-o PubkeyAuthentication=no "
                f"-o PreferredAuthentications=password "
                f"-o LogLevel=ERROR "
                f"-p {proxy.porta} {proxy.usuario}@{proxy.host} "
                f"-W {acesso.host}:{porta}"
            )
            ssh_cmd = (
                f"ssh -o StrictHostKeyChecking=no "
                f"-o UserKnownHostsFile=/dev/null "
                f"-o IdentitiesOnly=yes "
                f"-o PubkeyAuthentication=no "
                f"-o PreferredAuthentications=password "
                f"-o ConnectTimeout=15 "
                f"-o ServerAliveInterval=15 "
                f"-o ServerAliveCountMax=3 "
                f"-o LogLevel=ERROR "
                # SEM "+": precisa vir na frente da lista, não só anexado no fim.
                # ZTE tem timeout de KEX curto — group16-sha512 (default do
                # OpenSSH atual) demora 2-5s no CPU embarcado e a conexão cai
                # antes da auth (ver _ZTE_PREFERRED_KEX acima). Com "+" o
                # group16 continua sendo tentado primeiro, e é exatamente
                # isso que fazia esse path (proxy) travar em silêncio até o
                # expect() estourar o timeout — mesmo bug já corrigido em
                # _build_ssh_cmd para a conexão direta, mas nunca replicado aqui.
                f"-o KexAlgorithms=diffie-hellman-group14-sha256,diffie-hellman-group14-sha1,curve25519-sha256,curve25519-sha256@libssh.org,ecdh-sha2-nistp256,ecdh-sha2-nistp384,ecdh-sha2-nistp521,diffie-hellman-group-exchange-sha256,diffie-hellman-group-exchange-sha1,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512,diffie-hellman-group1-sha1 "
                f"-o HostKeyAlgorithms=+ssh-rsa,ssh-dss "
                f"-o PubkeyAcceptedAlgorithms=+ssh-rsa "
                f"-o Ciphers=+aes128-cbc,aes192-cbc,aes256-cbc,3des-cbc "
                f"-o MACs=+hmac-sha1,hmac-sha2-256,hmac-sha2-512 "
                f"-o ProxyCommand='{proxy_cmd}' "
                f"-p {porta} {acesso.usuario}@{acesso.host}"
            )

            logger.info(f"🖥️ Parks pexpect cmd: ...@{acesso.host}:{porta}")
            proc = pexpect.spawn(ssh_cmd, timeout=30, encoding=None, maxread=65536)
            proc.setwinsize(50, 220)

            # Autenticar no proxy (se pedir senha do proxy)
            idx = proc.expect([
                b'password:', b'Password:',              # senha proxy ou OLT
                rb'[#>$\]]\s*$',                         # prompt direto (raro)
                b'Are you sure', b'yes/no',              # host key
                pexpect.TIMEOUT, pexpect.EOF,
            ], timeout=20)

            if idx in (3, 4):
                proc.sendline(b'yes')
                idx = proc.expect([b'password:', b'Password:', pexpect.TIMEOUT, pexpect.EOF], timeout=10)

            if idx in (5, 6):
                raise Exception('Timeout ou EOF na autenticação do proxy/OLT')

            # Pode precisar de senha do proxy E do OLT
            # Primeira senha (proxy ou OLT)
            proc.sendline(proxy.senha.encode())
            idx2 = proc.expect([
                b'password:', b'Password:',  # segunda senha (OLT)
                rb'[#>$\]]\s*$',             # prompt OLT
                pexpect.TIMEOUT, pexpect.EOF,
            ], timeout=15)

            if idx2 in (0, 1):
                # Segunda senha = OLT
                proc.sendline(acesso.senha.encode())
                proc.expect([rb'[#>$\]]\s*$', pexpect.TIMEOUT], timeout=15)
            elif idx2 in (3, 4):
                raise Exception('Timeout ao aguardar prompt do OLT Parks')

            self.ssh_process = proc

            tempo_total = time.time() - tempo_inicio
            self.send_json({
                'type': 'connected',
                'message': f'✓ SSH a {acesso.host}:{porta} via {proxy.nome} [Parks] ({tempo_total:.1f}s)',
            })

            self.is_reading  = True
            self.read_thread = threading.Thread(target=self._read_pexpect_shell, daemon=True)
            self.read_thread.start()

        except Exception as e:
            tempo_total = time.time() - tempo_inicio
            logger.error(f"❌ SSH Parks via proxy falhou em {tempo_total:.1f}s: {e}")
            self.send_error(f'Erro SSH Parks via proxy: {str(e)}')
            self.limpar_recursos()

    def _read_pexpect_shell(self):
        """Loop de leitura para sessão pexpect (Parks)."""
        proc = self.ssh_process
        logger.info("📖 Thread pexpect (Parks) iniciada")
        try:
            while self.is_reading and proc and proc.isalive():
                try:
                    chunk = proc.read_nonblocking(size=65536, timeout=0.1)
                    if chunk:
                        self.send_output(chunk.decode('utf-8', errors='replace'))
                except pexpect.TIMEOUT:
                    continue
                except (pexpect.EOF, OSError):
                    break
        except Exception as e:
            logger.error(f"❌ Erro thread pexpect Parks: {e}")
        finally:
            logger.info("🛑 Thread pexpect (Parks) finalizada")
            self.is_reading = False

    def _read_paramiko_shell(self):
        """Loop de leitura para conexão SSH via paramiko (proxy) — latência mínima via select."""
        shell = self._paramiko_shell
        _bytes_recebidos = 0
        logger.info("📖 Thread paramiko shell iniciada")
        try:
            while self.is_reading and shell and not shell.closed:
                try:
                    r, _, _ = select.select([shell], [], [], 0.1)
                except Exception:
                    break
                if not r:
                    continue

                # Lê o primeiro chunk
                try:
                    chunk = shell.recv(65536)
                except Exception:
                    chunk = b''
                if not chunk:
                    break

                buf = bytearray(chunk)

                # Drain imediato do que já está no buffer
                while shell.recv_ready():
                    try:
                        more = shell.recv(65536)
                        if more:
                            buf += more
                        else:
                            break
                    except Exception:
                        break

                # Coalescimento apenas para buffers pequenos (tab completion).
                # Respostas de tab completion são sempre pequenas (< 512 bytes).
                # Streaming de output (display current-config etc.) produz chunks
                # grandes — enviados imediatamente sem espera extra.
                if len(buf) < 512:
                    coalesce = 0.008 if self.is_huawei else 0.003
                    try:
                        r2, _, _ = select.select([shell], [], [], coalesce)
                        if r2:
                            while shell.recv_ready():
                                try:
                                    more = shell.recv(65536)
                                    if more:
                                        buf += more
                                    else:
                                        break
                                except Exception:
                                    break
                    except Exception:
                        pass

                _bytes_recebidos += len(buf)
                texto = buf.decode('utf-8', errors='replace')
                if _bytes_recebidos <= 2048:
                    logger.info(f"📥 Shell recv [{_bytes_recebidos}b]: {repr(texto[:200])}")
                self.send_output(texto)

        except Exception as e:
            logger.error(f"❌ Erro thread paramiko: {e}")
        finally:
            logger.info(f"🛑 Thread paramiko shell finalizada (total={_bytes_recebidos}b)")
            self.is_reading = False

    # =========================================================
    # Telnet via proxy
    # =========================================================

    def connect_telnet_via_proxy(self, acesso):
        tempo_inicio = time.time()
        try:
            proxy = self.get_active_proxy(acesso.cliente)
            self.send_json({'type': 'info', 'message': f'⚡ Telnet via proxy {proxy.nome}...'})

            local_port         = self._criar_tunel_paramiko(proxy, acesso.host, int(acesso.porta))
            self.telnet_client = telnetlib.Telnet('127.0.0.1', local_port, timeout=10)

            self.send_json({'type': 'info', 'message': 'Autenticando Telnet...'})
            self.authenticate_telnet(acesso.usuario, acesso.senha)

            tempo_total = time.time() - tempo_inicio
            self.send_json({
                'type':    'connected',
                'message': f'✓ Telnet a {acesso.host}:{acesso.porta} via {proxy.nome} ({tempo_total:.1f}s)'
            })

            self.is_reading  = True
            self.read_thread = threading.Thread(target=self.read_telnet_output, daemon=True)
            self.read_thread.start()

        except Exception as e:
            logger.error(f"❌ Telnet via proxy: {str(e)}")
            self.send_error(f'Erro Telnet via proxy: {str(e)}')
            self.limpar_recursos()

    # =========================================================
    # Helpers de conexão
    # =========================================================

    def _build_ssh_cmd(self, usuario, host, porta, source_ip=None):
        bind = f"-b {source_ip} " if source_ip else ""
        return (
            f"ssh {bind}"
            f"-o StrictHostKeyChecking=no "
            f"-o UserKnownHostsFile=/dev/null "
            f"-o IdentitiesOnly=yes "
            f"-o PubkeyAuthentication=no "
            f"-o PreferredAuthentications=password,keyboard-interactive "
            f"-o ConnectTimeout=10 "
            f"-o ServerAliveInterval=60 "
            f"-o ServerAliveCountMax=3 "
            f"-o LogLevel=ERROR "
            f"-o NumberOfPasswordPrompts=3 "
            f"-o KexAlgorithms=diffie-hellman-group14-sha256,diffie-hellman-group14-sha1,curve25519-sha256,curve25519-sha256@libssh.org,ecdh-sha2-nistp256,ecdh-sha2-nistp384,ecdh-sha2-nistp521,diffie-hellman-group-exchange-sha256,diffie-hellman-group-exchange-sha1,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512,diffie-hellman-group1-sha1 "
            f"-o HostKeyAlgorithms=+ssh-rsa,ssh-dss "
            f"-o PubkeyAcceptedAlgorithms=+ssh-rsa "
            f"-o Ciphers=+aes128-cbc,aes192-cbc,aes256-cbc,3des-cbc "
            f"-o MACs=+hmac-sha1,hmac-sha2-256,hmac-sha2-512 "
            f"-p {porta} {usuario}@{host}"
        )

    def _authenticate_ssh_process(self, process, senha):
        index = process.expect([
            b"password:",           # 0
            b"Password:",           # 1
            b"Are you sure",        # 2
            rb".*[#>$\]].*",        # 3 — já logado (prompt)
            b"username:",           # 4 — OLTs que pedem username via terminal
            b"Username:",           # 5
            b"login:",              # 6
            b"Login:",              # 7
            b"Permission denied",   # 8 — falha antes de enviar senha
            b"Connection refused",  # 9
            pexpect.TIMEOUT,        # 10
            pexpect.EOF             # 11
        ], timeout=15)

        # Aceitar fingerprint e re-esperar
        if index == 2:
            process.sendline(b"yes")
            index = process.expect([
                b"password:", b"Password:",
                b"username:", b"Username:", b"login:", b"Login:",
                rb".*[#>$\]].*",
                pexpect.TIMEOUT, pexpect.EOF
            ], timeout=12)
            # Re-mapeia para índices originais aproximados
            if index in (2, 3, 4, 5):   index = 4  # username prompt
            elif index == 6:             index = 3  # já no prompt
            elif index in (0, 1):        pass       # password prompt
            else:                        index = 10 # timeout/eof

        # Prompt de username inesperado — alguns OLTs exibem mesmo via SSH
        if index in (4, 5, 6, 7):
            process.sendline(process.args[0].split('@')[0].encode() if hasattr(process, 'args') else b'')
            index = process.expect([
                b"password:", b"Password:",
                rb".*[#>$\]].*",
                pexpect.TIMEOUT, pexpect.EOF
            ], timeout=12)
            # Ajuste: 0/1=senha, 2=prompt, 3=timeout, 4=eof
            if index == 2:   index = 3
            elif index == 3: index = 10
            elif index == 4: index = 11

        # Já no prompt (sem precisar de senha)
        if index == 3:
            time.sleep(0.2)
            process.send('\r')
            return

        # Erros antes mesmo de enviar senha
        if index == 8:
            raise Exception("Acesso negado — verifique usuário/chave SSH")
        if index == 9:
            raise Exception("Conexão recusada pelo equipamento")
        if index == 10:
            raise Exception("Timeout ao conectar ao equipamento (sem resposta em 15s)")
        if index == 11:
            before = (process.before or b'').decode('utf-8', errors='ignore').strip()
            detail = f" — {before[:200]}" if before else ""
            raise Exception(f"Equipamento encerrou conexão antes do prompt de senha{detail}")

        # Enviar senha (index 0 ou 1 = password:)
        if index in (0, 1):
            process.sendline(senha)
            result = process.expect([
                b"Permission denied",   # 0
                b"Access denied",       # 1
                b"Authentication failed", # 2
                b"Login incorrect",     # 3
                b"password:",           # 4 — segunda tentativa (senha errada)
                b"Password:",           # 5
                rb".*[#>$\]].*",        # 6 — sucesso
                pexpect.TIMEOUT,        # 7
                pexpect.EOF             # 8
            ], timeout=15)

            if result in (0, 1, 2, 3, 4, 5):
                raise Exception("Senha incorreta ou acesso negado no equipamento")
            if result == 7:
                raise Exception("Timeout ao autenticar no equipamento")
            if result == 8:
                raise Exception("Equipamento encerrou conexão após envio de senha")

            time.sleep(0.2)
            process.send('\r')
            return

    def _disable_huawei_paging(self):
        pass  # screen-length 0 temporary removido a pedido do operador

    # =========================================================
    # Telnet direto
    # =========================================================

    def connect_telnet(self, acesso):
        try:
            self.telnet_client = telnetlib.Telnet(acesso.host, int(acesso.porta), timeout=10)
            self.send_json({'type': 'info', 'message': f'Conectando Telnet a {acesso.host}:{acesso.porta}...'})
            self.authenticate_telnet(acesso.usuario, acesso.senha)
            self.send_json({'type': 'connected', 'message': f'✓ Conectado Telnet a {acesso.host}:{acesso.porta}'})

            self.is_reading  = True
            self.read_thread = threading.Thread(target=self.read_telnet_output, daemon=True)
            self.read_thread.start()

        except Exception as e:
            logger.error(f"❌ Telnet: {str(e)}")
            self.send_error(f'Erro Telnet: {str(e)}')

    def authenticate_telnet(self, username, password):
        try:
            self.telnet_client.write(b'\n')
            time.sleep(0.5)

            output = self.telnet_client.read_very_eager()
            if output:
                self.send_json({'type': 'output', 'data': output.decode('utf-8', errors='ignore')})

            output = b''
            for _ in range(50):
                chunk = self.telnet_client.read_very_eager()
                if chunk:
                    output += chunk
                    if any(x in output.lower() for x in [b'username', b'login', b'user']):
                        break
                time.sleep(0.1)

            if output:
                self.send_json({'type': 'output', 'data': output.decode('utf-8', errors='ignore')})

            self.telnet_client.write(username.encode('utf-8') + b'\n')
            time.sleep(0.5)

            output = self.telnet_client.read_very_eager()
            if output:
                self.send_json({'type': 'output', 'data': output.decode('utf-8', errors='ignore')})

            output = b''
            for _ in range(50):
                chunk = self.telnet_client.read_very_eager()
                if chunk:
                    output += chunk
                    if any(x in output.lower() for x in [b'password', b'passwd', b'senha']):
                        break
                time.sleep(0.1)

            if output:
                self.send_json({'type': 'output', 'data': output.decode('utf-8', errors='ignore')})

            self.telnet_client.write(password.encode('utf-8') + b'\n')
            time.sleep(1)

            output = self.telnet_client.read_very_eager()
            if output:
                self.send_json({'type': 'output', 'data': output.decode('utf-8', errors='ignore')})

        except Exception as e:
            logger.error(f"❌ Telnet autenticação: {str(e)}")
            self.send_error(f'Erro na autenticação Telnet: {str(e)}')
            raise

    # =========================================================
    # ✅ LEITURA SSH — loop direto no fd sem select()
    #
    # Problemas do código anterior:
    # 1. select() + read_nonblocking causava race condition: o select
    #    via é no fd raw do SO, mas o pexpect tem buffer interno próprio.
    #    Quando o equipamento envia sequências longas (histórico, seta cima),
    #    parte dos bytes ficava no buffer do pexpect e outra no fd — os dois
    #    caminhos chegavam fora de ordem no xterm.js, causando sobreposição.
    # 2. BATCH_MS=8ms forçava um delay mínimo de 8ms por tecla digitada.
    #    Um terminal moderno (SecureCRT, Terminus) envia o echo da tecla
    #    em <1ms — qualquer batch delay acima disso é perceptível.
    #
    # Solução:
    # - Leitura bloqueante com timeout curto (0.05s) direto no pexpect.
    # - Flush imediato para payloads pequenos (eco de tecla, prompt).
    # - Acúmulo apenas para rajadas grandes (output de comandos longos).
    # =========================================================

    def read_ssh_output(self):
        """
        Loop de leitura SSH — acesso direto ao fd do pty via os.read() + select.

        Bypassa completamente o pexpect no hot path: sem overhead Python de
        verificação de buffer interno, sem criação de objetos por iteração.
        select.select() bloqueia eficientemente no nível do SO até dados chegarem.
        """
        try:
            logger.info("📖 Thread SSH iniciada")

            fd  = self.ssh_process.child_fd
            buf = bytearray()

            # Drena o buffer interno do pexpect (bytes acumulados durante o expect()
            # de autenticação) antes de assumir o fd diretamente.
            pex_buf = getattr(self.ssh_process, 'buffer', b'')
            if pex_buf:
                buf += pex_buf
                self.ssh_process.buffer = b''

            while self.is_reading and self.ssh_process:
                try:
                    # Bloqueia no SO até dado chegar — timeout 100ms só para checar is_reading
                    r, _, _ = select.select([fd], [], [], 0.1)
                    if not r:
                        continue

                    data = os.read(fd, 65536)
                    if not data:
                        break

                    buf = bytearray(data)

                    # Drain: lê tudo que já está no kernel buffer sem esperar
                    while True:
                        r2, _, _ = select.select([fd], [], [], 0)
                        if not r2:
                            break
                        more = os.read(fd, 65536)
                        if more:
                            buf += more
                        else:
                            break

                    self.send_output(buf.decode('utf-8', errors='replace'))

                except OSError as e:
                    # EIO (errno 5) = processo filho morreu; EBADF = fd fechado
                    logger.info(f"🔌 SSH fd encerrado: {e}")
                    if buf:
                        self.send_output(buf.decode('utf-8', errors='replace'))
                    break

                except Exception as e:
                    err = str(e)
                    if any(k in err for k in ('EIO', 'EOF', 'closed', 'fd')):
                        logger.info(f"🔌 SSH encerrado: {err}")
                        break
                    logger.error(f"❌ Erro leitura SSH: {err}")
                    break

        except Exception as e:
            logger.error(f"❌ Erro thread SSH: {str(e)}")
        finally:
            logger.info("🛑 Thread SSH finalizada")
            self.is_reading = False

    # =========================================================
    # ✅ LEITURA TELNET — mesma lógica de flush imediato
    # =========================================================

    def read_telnet_output(self):
        """
        Loop de leitura Telnet otimizado — sem polling com sleep.

        Usa select.select() no socket do telnet para aguardar dados
        de forma eficiente (sem CPU waste) e sem delay fixo de 5ms.
        """
        try:
            logger.info("📖 Thread Telnet iniciada")
            buf = bytearray()

            while self.is_reading and self.telnet_client:
                try:
                    sock = self.telnet_client.sock
                    if not sock:
                        break

                    # Aguarda dados por até 1ms — latência mínima ao digitar
                    r, _, _ = select.select([sock], [], [], 0.001)
                    if not r:
                        if buf:
                            self.send_output(buf.decode('utf-8', errors='ignore'))
                            buf.clear()
                        continue

                    chunk = self.telnet_client.read_very_eager()
                    if not chunk:
                        continue

                    buf += chunk

                    # Drain: esgota buffer sem nova espera
                    while True:
                        r2, _, _ = select.select([sock], [], [], 0)
                        if not r2:
                            break
                        more = self.telnet_client.read_very_eager()
                        if more:
                            buf += more
                        else:
                            break

                    self.send_output(buf.decode('utf-8', errors='ignore'))
                    buf.clear()

                except EOFError:
                    logger.info("🔌 Telnet encerrado")
                    if buf:
                        self.send_output(buf.decode('utf-8', errors='ignore'))
                    break

                except Exception as e:
                    err = str(e)
                    if any(k in err for k in ('closed', 'bad file', 'EIO')):
                        break
                    logger.error(f"❌ Erro leitura Telnet: {err}")
                    break

        except Exception as e:
            logger.error(f"❌ Erro thread Telnet: {str(e)}")
        finally:
            logger.info("🛑 Thread Telnet finalizada")
            self.is_reading = False

    # =========================================================
    # Utilitários
    # =========================================================

    def find_available_port(self, start=9000, max_attempts=100):
        for port in range(start, start + max_attempts):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(('127.0.0.1', port))
                sock.close()
                return port
            except OSError:
                continue
        raise Exception("Nenhuma porta disponível")

    def send_output(self, text):
        """Envia output do terminal — bytes puros, sem JSON overhead.
        Se esta conexão está compartilhada, propaga para todos os
        espectadores (inclusive quem fisicamente detém o shell) em vez de só
        para `self` — é o único ponto de fan-out necessário: todos os loops
        de leitura (_read_paramiko_shell, read_ssh_output, read_telnet_output,
        _read_pexpect_shell) já chamam send_output() sem saber se a sessão
        está compartilhada."""
        session = getattr(self, '_shared_session', None)
        if session:
            session.append_recent(text)
            alvos = session.snapshot_viewers()
        else:
            alvos = [(self, None)]
        for consumer, _label in alvos:
            consumer._registrar_saida(text)
            try:
                consumer.send(bytes_data=text.encode('utf-8'))
            except Exception:
                pass

    def send_json(self, data):
        try:
            self.send(text_data=json.dumps(data))
        except Exception as e:
            logger.error(f"❌ Erro send_json: {str(e)}")

    def send_error(self, message):
        self.send_json({'type': 'error', 'message': message})


class TerminalLinkExternoConsumer(SSHConsumer):
    """WebSocket para visitantes SEM login no CRM que entraram por um link
    temporário (TerminalLinkExterno) — ex: suporte de fabricante durante uma
    chamada. Nunca abre uma conexão SSH/Telnet própria nem aceita acesso_id
    arbitrário: só pode anexar-se a uma sessão que um usuário do CRM já
    deixou compartilhada, e só enquanto o token do link for válido.
    A autorização inteira é o token — por isso `_usuario_pode_acessar` é
    sobrescrito para sempre liberar (já validado em `_conectar_via_link`
    antes de chegar lá) e `_crm_user` fica sempre None."""

    def connect(self):
        self._crm_user = None
        self.accept()
        self.limpar_recursos()

    def receive(self, text_data=None, bytes_data=None):
        if bytes_data is not None:
            try:
                self.enviar_comando(bytes_data.decode('utf-8', errors='replace'))
            except Exception as e:
                logger.error(f"❌ Erro ao enviar bytes (link externo): {e}")
            return
        try:
            data   = json.loads(text_data)
            action = data.get('action')
            if action == 'connect_link':
                self._conectar_via_link(data.get('token'))
            elif action == 'resize':
                if self._set_term_size(data.get('cols'), data.get('rows')):
                    self._resize_pty(self.term_cols, self.term_rows)
        except json.JSONDecodeError as e:
            self.send_error(f'Erro ao parsear JSON: {e}')
        except Exception as e:
            logger.error(f"❌ Erro receive (link externo): {e}")
            self.send_error(f'Erro: {e}')

    def _usuario_pode_acessar(self, acesso):
        return True

    def _label_usuario(self):
        return 'Visitante externo'

    def _conectar_via_link(self, token):
        try:
            link = TerminalLinkExterno.objects.get(id=token)
        except (TerminalLinkExterno.DoesNotExist, ValueError, ValidationError):
            self.send_error('Link inválido.')
            self.close(code=4003)
            return

        valido, motivo = link.validar()
        if not valido:
            self.send_error(motivo)
            self.close(code=4003)
            return

        session = _terminal_sessions.get(link.acesso_id)
        if not session:
            self.send_error('A sessão compartilhada não está mais ativa. Peça um novo link.')
            self.close(code=4003)
            return

        self._entrar_em_sessao_compartilhada(session, link_externo=link)

        # Encerra a conexão exatamente no momento da expiração, mesmo que
        # ninguém revogue manualmente antes disso.
        restante = (link.expira_em - timezone.now()).total_seconds()
        self._expira_timer = threading.Timer(max(restante, 0), self._expirar_por_link)
        self._expira_timer.daemon = True
        self._expira_timer.start()

    def _expirar_por_link(self):
        try:
            self.send_json({'type': 'share_ended', 'message': 'O link de acesso externo expirou.'})
            self.close(code=4008)
        except Exception:
            pass

    def disconnect(self, close_code):
        timer = getattr(self, '_expira_timer', None)
        if timer:
            timer.cancel()
        super().disconnect(close_code)


class WinboxConsumer(SSHConsumer):
    def connect(self):
        user = self.scope.get('user')
        if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
            self.close(code=4001)
            return
        self._crm_user = user
        self.accept()
        self.limpar_recursos()
        self.tcp_socket = None
        self.is_connected = False

    def limpar_recursos(self):
        super().limpar_recursos()
        if hasattr(self, 'tcp_socket') and self.tcp_socket:
            try:
                self.tcp_socket.close()
            except:
                pass
            self.tcp_socket = None
        self.is_connected = False

    def receive(self, text_data=None, bytes_data=None):
        if text_data:
            try:
                data = json.loads(text_data)
                action = data.get('action')
                if action == 'connect':
                    acesso_id = data.get('acesso_id')
                    logger.info(f"📋 Conectar Winbox acesso {acesso_id}")
                    self.conectar_winbox(acesso_id)
            except Exception as e:
                logger.error(f"❌ Erro Winbox receive texto: {str(e)}")
                self.send_error(f"Erro: {str(e)}")
        elif bytes_data:
            # Enviar dados binários para o roteador via socket TCP
            if self.is_connected and self.tcp_socket:
                try:
                    self.tcp_socket.sendall(bytes_data)
                except Exception as e:
                    logger.error(f"❌ Erro ao enviar bytes para Winbox: {e}")
                    self.limpar_recursos()
                    self.close()

    def conectar_winbox(self, acesso_id):
        try:
            self.acessoId = acesso_id
            acesso = Acesso.objects.get(id=acesso_id)
            host = acesso.host
            porta = int(acesso.winbox) if hasattr(acesso, 'winbox') and acesso.winbox else 8291

            self._sessao_auditoria = AcessoSessao.objects.create(
                acesso=acesso,
                usuario=getattr(self, '_crm_user', None),
                tipo='winbox_nativo',
                ip_origem=(self.scope.get('client') or [None])[0],
            )

            if self.is_private_ip(host):
                proxy = ProxyServer.objects.filter(cliente=acesso.cliente, ativo=True).first()
                if proxy:
                    self.send_json({'type': 'info', 'message': f'🔌 Conectando Winbox via proxy {proxy.nome}...'})
                    local_port = self._criar_tunel_paramiko(proxy, host, porta)
                    target_host = '127.0.0.1'
                    target_port = local_port
                else:
                    # Cliente só-VPN (sem ProxyServer SSH) — rota já existe no
                    # kernel via WireGuard/OpenVPN. Ver mesmo fallback em
                    # conectar_vnc() acima.
                    from .views import vpn_cobre_ip
                    if not vpn_cobre_ip(acesso.cliente, host):
                        raise Exception(f"Nenhum proxy SSH ativo nem VPN cobrindo {host} para {acesso.cliente.nome_empresa}")
                    self.send_json({'type': 'info', 'message': f'🔌 Conectando Winbox diretamente a {host}:{porta} (via VPN)...'})
                    target_host = host
                    target_port = porta
            else:
                self.send_json({'type': 'info', 'message': f'🔌 Conectando Winbox diretamente a {host}:{porta}...'})
                target_host = host
                target_port = porta

            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.settimeout(10)
            self.tcp_socket.connect((target_host, target_port))
            self.tcp_socket.settimeout(None)  # Blocking for thread
            self.is_connected = True

            self.send_json({'type': 'connected', 'message': f'✓ Conectado Winbox a {host}:{porta}'})

            self.is_reading = True
            self.read_thread = threading.Thread(target=self.read_tcp_output, daemon=True)
            self.read_thread.start()

        except Exception as e:
            logger.error(f"❌ Winbox connect error: {str(e)}")
            self.send_error(f'Erro ao conectar Winbox: {str(e)}')
            self.limpar_recursos()

    def read_tcp_output(self):
        try:
            while self.is_reading and self.tcp_socket:
                data = self.tcp_socket.recv(65536)
                if not data:
                    break
                # Enviar dados binários para o WebSocket
                self.send(bytes_data=data)
        except Exception as e:
            err = str(e)
            if not any(k in err for k in ('closed', 'bad file', 'EIO')):
                logger.error(f"❌ Erro leitura TCP Winbox: {err}")
        finally:
            self.limpar_recursos()
            self.close()

# =========================================================
# Winbox VNC Consumer (Acesso via Web Browser + noVNC)
# =========================================================
from .winbox_vnc import WinboxVNCManager
from .browser_vnc import BrowserVNCManager


class WinboxVNCConsumer(SSHConsumer):
    def connect(self):
        user = self.scope.get('user')
        if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
            self.close(code=4001)
            return
        self._crm_user = user

        # Aceitar subprotocol 'binary' ou o primeiro que o cliente enviar
        try:
            subprotocols = self.scope.get('subprotocols', [])
            if 'binary' in subprotocols:
                self.accept(subprotocol='binary')
            elif subprotocols:
                self.accept(subprotocol=subprotocols[0])
            else:
                self.accept()
        except Exception:
            self.accept()
            
        # O noVNC passa o ID pela URL (ws://.../ws/vnc/<acesso_id>/)
        # Pegar o acesso_id da URL (kwargs)
        try:
            acesso_id = self.scope['url_route']['kwargs']['acesso_id']
            logger.info(f"📋 Conectar Winbox Web VNC acesso {acesso_id}")
            self.conectar_vnc(acesso_id)
        except Exception as e:
            logger.error(f"Erro ao obter acesso_id na URL VNC: {e}")
            self.close()

    def limpar_recursos(self):
        super().limpar_recursos()
        if hasattr(self, 'tcp_socket') and self.tcp_socket:
            try:
                self.tcp_socket.close()
            except:
                pass
            self.tcp_socket = None
            
        if hasattr(self, 'vnc_manager') and self.vnc_manager:
            self.vnc_manager.stop()
            self.vnc_manager = None

    def receive(self, text_data=None, bytes_data=None):
        if text_data:
            # noVNC pode mandar alguns pacotes textuais mas primariamente binário
            pass
        if bytes_data:
            # Repassar WebSockets -> VNC (RFB protocol)
            if hasattr(self, 'tcp_socket') and self.tcp_socket:
                try:
                    self.tcp_socket.sendall(bytes_data)
                except Exception as e:
                    logger.error(f"❌ Erro ao enviar bytes para VNC: {e}")
                    self.limpar_recursos()
                    self.close()

    def conectar_vnc(self, acesso_id):
        try:
            from urllib.parse import parse_qs
            query_string = self.scope.get('query_string', b'').decode()
            params = parse_qs(query_string)
            mode = params.get('mode', ['winbox'])[0]
            vnc_w = int(params.get('w', ['1366'])[0])
            vnc_h = int(params.get('h', ['768'])[0])
            winbox_version = params.get('v', ['4'])[0]
            
            acesso = Acesso.objects.get(id=acesso_id)
            host = acesso.host

            self._sessao_auditoria = AcessoSessao.objects.create(
                acesso=acesso,
                usuario=getattr(self, '_crm_user', None),
                tipo=('webfig' if mode == 'browser' else 'winbox'),
                ip_origem=(self.scope.get('client') or [None])[0],
            )
            record_path = None
            try:
                import os as _os, time as _time
                from django.conf import settings as _settings
                rel_path = f'gravacoes_acessos/{acesso.id}/{self._sessao_auditoria.id}_{int(_time.time())}.mp4'
                record_path = _os.path.join(str(_settings.MEDIA_ROOT), rel_path)
                _os.makedirs(_os.path.dirname(record_path), exist_ok=True)
            except Exception as e:
                logger.error(f"❌ Erro ao preparar caminho de gravação: {e}")
                record_path = None

            if mode == 'browser':
                # Se o protocolo for explicitamente HTTP/HTTPS, usar a porta configurada
                if acesso.protocolo.upper() in ['HTTP', 'HTTPS']:
                    porta = int(acesso.porta)
                else:
                    # Para Winbox/MikroTik, o WebFig costuma estar na 80 ou 443
                    # Tentamos a 80 por padrão, mas o ideal seria o usuário configurar
                    porta = 80 
            else:
                porta = int(acesso.winbox) if hasattr(acesso, 'winbox') and acesso.winbox else 8291

            
            if self.is_private_ip(host):
                proxy = ProxyServer.objects.filter(cliente=acesso.cliente, ativo=True).first()
                if proxy:
                    msg_tipo = "Navegador" if mode == 'browser' else "Winbox"
                    self.send_json({'type': 'info', 'message': f'🔌 Conectando {msg_tipo} via proxy {proxy.nome}...'})
                    local_port = self._criar_tunel_paramiko(proxy, host, porta)
                    self.send_json({'type': 'info', 'message': f'✅ Túnel estabelecido na porta {local_port}'})
                    target_host = '127.0.0.1'
                    target_port = local_port
                else:
                    # Sem ProxyServer SSH ativo — clientes que só têm VPN
                    # WireGuard/OpenVPN (rota já existe no kernel via interface
                    # própria) conseguem conexão direta, sem túnel. Mesmo
                    # fallback usado por proxy_web_acesso (views.py) pro proxy
                    # HTTP; sem isso o Winbox Web falhava com "Nenhum proxy SSH
                    # ativo" pra qualquer cliente só-VPN, mesmo com o IP
                    # perfeitamente alcançável.
                    from .views import vpn_cobre_ip
                    if not vpn_cobre_ip(acesso.cliente, host):
                        raise Exception(f"Nenhum proxy SSH ativo nem VPN cobrindo {host} para {acesso.cliente.nome_empresa}")
                    target_host = host
                    target_port = porta

            else:
                target_host = host
                target_port = porta

            if mode == 'browser':
                # Inicia Xvfb + Navegador + x11vnc
                protocolo_web = 'https' if porta == 443 else 'http'
                url = f"{protocolo_web}://127.0.0.1:{target_port}/"
                
                # Teste de diagnóstico: Tentar acessar a URL via túnel antes de abrir o navegador
                self.send_json({'type': 'info', 'message': f'🔍 Testando acesso a {host}:{porta} via túnel...'})
                import requests
                try:
                    # Pequeno delay para o túnel estabilizar
                    import time
                    time.sleep(1)
                    test_res = requests.get(url, timeout=5, verify=False, allow_redirects=True)
                    self.send_json({'type': 'info', 'message': f'✅ Resposta recebida da OLT/Switch! (Status: {test_res.status_code})'})
                except Exception as e:
                    self.send_json({'type': 'info', 'message': f'❌ Falha no teste de túnel: {str(e)}'})
                    self.send_json({'type': 'info', 'message': f'💡 Verifique se o IP {host} e a porta {porta} estão corretos e acessíveis pelo Proxy.'})

                self.vnc_manager = BrowserVNCManager(url=url, record_path=record_path, width=vnc_w, height=vnc_h)




            else:
                # Inicia Xvfb + WinBox + x11vnc
                self.vnc_manager = WinboxVNCManager(
                    host=target_host,
                    port=target_port,
                    user=acesso.usuario,
                    password=acesso.senha,
                    version=winbox_version,
                    width=vnc_w,
                    height=vnc_h,
                    record_path=record_path
                )

            vnc_port = self.vnc_manager.start()

            if getattr(self.vnc_manager, 'recording', False) and self._sessao_auditoria:
                self._sessao_auditoria.arquivo_video = rel_path
                self._sessao_auditoria.save(update_fields=['arquivo_video'])

            # Conecta o socket TCP interno ao x11vnc local
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.settimeout(10)
            self.tcp_socket.connect(('127.0.0.1', vnc_port))
            self.tcp_socket.settimeout(None)
            
            self.is_reading = True
            self.read_thread = threading.Thread(target=self.read_tcp_output, daemon=True)
            self.read_thread.start()
            
            servico_nome = "WebFig (Navegador)" if mode == 'browser' else "Winbox"
            self.send_json({'type': 'connected', 'message': f'Ambiente {servico_nome} criado no servidor. Recebendo tela...'})

        except Exception as e:
            logger.error(f"❌ VNC connect error (mode={mode}): {str(e)}")
            self.send_error(str(e))
            self.limpar_recursos()


    def read_tcp_output(self):
        try:
            while self.is_reading and self.tcp_socket:
                data = self.tcp_socket.recv(65536)
                if not data:
                    break
                # Repassar VNC -> WebSockets
                self.send(bytes_data=data)
        except Exception as e:
            err = str(e)
            if not any(k in err for k in ('closed', 'bad file', 'EIO')):
                logger.error(f"❌ Erro leitura TCP VNC: {err}")
        finally:
            self.limpar_recursos()
            self.close()

# ─────────────────────────────────────────────────────────────────────────────
# WebSocket Proxy Consumer
# Aceita WS do browser e faz bridge para o equipamento via túnel SSH.
# Rota: /ws/proxy/<acesso_id>/<porta>/<scheme>/<path>
# ─────────────────────────────────────────────────────────────────────────────

class WebSocketProxyConsumer(ThreadedDispatchMixin, WebsocketConsumer):

    def connect(self):
        self.ws_sock     = None
        self.is_running  = False
        self.recv_thread = None

        subprotocols = self.scope.get('subprotocols', [])
        try:
            if subprotocols:
                self.accept(subprotocol=subprotocols[0])
            else:
                self.accept()
        except Exception:
            self.accept()

        try:
            kwargs      = self.scope['url_route']['kwargs']
            acesso_id   = kwargs['acesso_id']
            porta       = int(kwargs['porta'])
            scheme      = kwargs['scheme']
            path        = '/' + kwargs.get('path', '')
            qs          = self.scope.get('query_string', b'').decode()
            full_path   = path + ('?' + qs if qs else '')

            acesso      = Acesso.objects.get(id=acesso_id)
            target_host = acesso.host.strip()
            if '://' in target_host:
                from urllib.parse import urlparse as _up
                target_host = _up(target_host).hostname

            # Mesmo esquema de isolamento de cookies do proxy HTTP (views.py
            # proxy_web_acesso): browser guarda cookies do device como
            # "a{id}_NAME=value". Sem repassar o cookie de sessão (ex.
            # PVEAuthCookie do Proxmox), o upgrade da WebSocket é rejeitado
            # e o noVNC recebe "Falha ao conectar-se ao servidor".
            _django_cookies = {'sessionid', 'csrftoken', 'messages'}
            cookie_prefix   = f'a{acesso_id}_'
            filtered_cookie_parts = []
            for name, val in self.scope.get('cookies', {}).items():
                if name in _django_cookies:
                    continue
                if name.startswith(cookie_prefix):
                    filtered_cookie_parts.append(f'{name[len(cookie_prefix):]}={val}')
            forward_cookie = '; '.join(filtered_cookie_parts)

            try:
                is_private = ipaddress.ip_address(target_host).is_private
            except ValueError:
                is_private = False

            if is_private:
                proxy_srv = ProxyServer.objects.filter(
                    cliente=acesso.cliente, ativo=True
                ).first()
                if not proxy_srv:
                    raise Exception(f'Sem proxy SSH ativo para {acesso.cliente}')
                from .proxy_engine import TunnelPortCache, SSHConnectionPool
                local_port = TunnelPortCache.get_port(
                    proxy_srv, target_host, porta, SSHConnectionPool()
                )
                connect_host, connect_port = '127.0.0.1', local_port
            else:
                connect_host, connect_port = target_host, porta

            raw_sock = socket.create_connection((connect_host, connect_port), timeout=10)

            if scheme == 'https':
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode    = ssl.CERT_NONE
                try:
                    ctx.set_ciphers('DEFAULT@SECLEVEL=0')
                except Exception:
                    pass
                raw_sock = ctx.wrap_socket(raw_sock, server_hostname=target_host)

            raw_sock, leftover = self._ws_handshake(
                raw_sock, target_host, porta, full_path,
                subprotocols=subprotocols or ['binary'],
                cookie=forward_cookie,
            )
            self.ws_sock = raw_sock

            if leftover:
                self.send(bytes_data=leftover)

            self.is_running  = True
            self.recv_thread = threading.Thread(
                target=self._forward_from_device, daemon=True
            )
            self.recv_thread.start()
            logger.info(f"[WS_PROXY] OK acesso={acesso_id} {scheme}://{target_host}:{porta}{path}")

        except Exception as e:
            logger.error(f"[WS_PROXY] Falha ao conectar: {e}")
            self.close()

    def disconnect(self, close_code):
        self.is_running = False
        if self.ws_sock:
            try:
                self.ws_sock.close()
            except Exception:
                pass
            self.ws_sock = None

    def receive(self, text_data=None, bytes_data=None):
        if not self.ws_sock or not self.is_running:
            return
        data = bytes_data if bytes_data is not None else (text_data or '').encode()
        if not data:
            return
        try:
            self._ws_send_frame(self.ws_sock, data, is_binary=(bytes_data is not None))
        except Exception as e:
            logger.error(f"[WS_PROXY] Erro ao enviar para device: {e}")
            self.is_running = False
            self.close()

    def _forward_from_device(self):
        try:
            while self.is_running and self.ws_sock:
                ftype, data = self._ws_recv_frame(self.ws_sock)
                if ftype is None or ftype == 'close':
                    break
                if ftype == 'ping':
                    self._ws_send_pong(self.ws_sock, data)
                    continue
                if ftype in ('binary', 'text', 'cont'):
                    if ftype == 'text':
                        self.send(text_data=data.decode('utf-8', errors='replace'))
                    else:
                        self.send(bytes_data=data)
        except Exception as e:
            if self.is_running:
                logger.error(f"[WS_PROXY] Erro ao ler do device: {e}")
        finally:
            self.is_running = False
            try:
                self.close()
            except Exception:
                pass

    # ── Helpers WebSocket protocol ────────────────────────────────────────────

    @staticmethod
    def _ws_handshake(sock, host, port, path_qs, subprotocols=None, cookie=None):
        import base64, os as _os
        key = base64.b64encode(_os.urandom(16)).decode()
        lines = [
            f'GET {path_qs} HTTP/1.1',
            f'Host: {host}:{port}',
            'Upgrade: websocket',
            'Connection: Upgrade',
            f'Sec-WebSocket-Key: {key}',
            'Sec-WebSocket-Version: 13',
        ]
        if subprotocols:
            lines.append(f'Sec-WebSocket-Protocol: {", ".join(subprotocols)}')
        if cookie:
            lines.append(f'Cookie: {cookie}')
        lines += ['', '']
        sock.sendall('\r\n'.join(lines).encode())

        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError('Conexão encerrada no handshake WebSocket')
            buf += chunk

        head, _, leftover = buf.partition(b'\r\n\r\n')
        if b' 101 ' not in head:
            raise RuntimeError(
                f'Upgrade WebSocket falhou: {head[:300].decode("utf-8", errors="replace")}'
            )
        return sock, leftover

    @staticmethod
    def _ws_recv_frame(sock):
        def _recv(n):
            buf = b''
            while len(buf) < n:
                chunk = sock.recv(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            return buf

        hdr = _recv(2)
        if hdr is None:
            return None, None

        byte1, byte2 = hdr[0], hdr[1]
        opcode = byte1 & 0x0F
        masked = bool(byte2 & 0x80)
        length = byte2 & 0x7F

        if length == 126:
            d = _recv(2)
            if d is None:
                return None, None
            length = struct.unpack('!H', d)[0]
        elif length == 127:
            d = _recv(8)
            if d is None:
                return None, None
            length = struct.unpack('!Q', d)[0]

        mask_key = _recv(4) if masked else None
        payload  = _recv(length) if length > 0 else b''
        if payload is None:
            return None, None

        if masked and mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        ftype = {0x0: 'cont', 0x1: 'text', 0x2: 'binary',
                 0x8: 'close', 0x9: 'ping', 0xA: 'pong'}.get(opcode, 'binary')
        return ftype, payload

    @staticmethod
    def _ws_send_frame(sock, data, is_binary=True):
        import os as _os
        opcode   = 0x2 if is_binary else 0x1
        length   = len(data)
        mask_key = _os.urandom(4)
        masked   = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
        frame    = bytearray()
        frame.append(0x80 | opcode)
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack('!H', length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack('!Q', length))
        frame.extend(mask_key)
        frame.extend(masked)
        sock.sendall(bytes(frame))

    @staticmethod
    def _ws_send_pong(sock, data):
        frame = bytearray([0x8A])
        frame.append(0x80 | min(len(data), 125))
        frame.extend(b'\x00\x00\x00\x00')
        frame.extend(data[:125])


# ── Firmware Download Progress ─────────────────────────────────────────────
from channels.generic.websocket import AsyncWebsocketConsumer


class FirmwareDownloadConsumer(AsyncWebsocketConsumer):
    """
    Admins conectam a ws/firmware/downloads/ para receber notificações em
    tempo real sempre que alguém baixar um arquivo via link compartilhado.
    """
    GROUP = 'firmware_downloads'

    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated or not user.is_staff:
            await self.close()
            return
        await self.channel_layer.group_add(self.GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.GROUP, self.channel_name)

    # Mensagens enviadas pelo grupo → repassadas ao browser
    async def download_event(self, event):
        await self.send(text_data=json.dumps(event))
        sock.sendall(bytes(frame))


# ─────────────────────────────────────────────────────────────────────────────
# Utilitário compartilhado: executa um único comando via SSH usando a mesma
# infraestrutura da plataforma (algoritmos legados, proxy, autenticação).
# Usado pelo Agent NOC para não reimplementar a lógica de conexão.
# ─────────────────────────────────────────────────────────────────────────────

_SSH_FLAGS = (
    "-o StrictHostKeyChecking=no "
    "-o UserKnownHostsFile=/dev/null "
    "-o IdentitiesOnly=yes "
    "-o PubkeyAuthentication=no "
    "-o PreferredAuthentications=password,keyboard-interactive "
    "-o ConnectTimeout=12 "
    "-o LogLevel=ERROR "
    "-o NumberOfPasswordPrompts=3 "
    # SEM "+": precisa substituir a ordem padrão, não só anexar no fim (ver
    # o mesmo ajuste em connect_ssh_parks_proxy / _ZTE_PREFERRED_KEX acima).
    "-o KexAlgorithms=diffie-hellman-group14-sha256,diffie-hellman-group14-sha1,curve25519-sha256,curve25519-sha256@libssh.org,ecdh-sha2-nistp256,ecdh-sha2-nistp384,ecdh-sha2-nistp521,diffie-hellman-group-exchange-sha256,diffie-hellman-group-exchange-sha1,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512,diffie-hellman-group1-sha1 "
    "-o HostKeyAlgorithms=+ssh-rsa,ssh-dss "
    "-o PubkeyAcceptedAlgorithms=+ssh-rsa "
    "-o Ciphers=+aes128-cbc,aes192-cbc,aes256-cbc,3des-cbc "
    "-o MACs=+hmac-sha1,hmac-sha2-256,hmac-sha2-512"
)

_PROMPT_RE = rb'[>#\$\]] *$'


def _pexpect_exec(cmd_line: str, senha: str, comando: str,
                  is_huawei: bool, timeout: int = 25) -> str:
    """Abre conexão SSH via pexpect, executa um comando e retorna o output."""
    import re as _re
    proc = pexpect.spawn(cmd_line, timeout=timeout, encoding=None, maxread=131072)
    try:
        # Autenticação
        idx = proc.expect([
            b'password:', b'Password:', b'Are you sure', b'yes/no',
            b'username:', b'Username:', b'login:', b'Login:',
            b'Permission denied', b'Connection refused',
            pexpect.TIMEOUT, pexpect.EOF,
        ], timeout=15)
        if idx in (2, 3):
            proc.sendline(b'yes')
            idx = proc.expect([b'password:', b'Password:', pexpect.TIMEOUT, pexpect.EOF], timeout=10)
        if idx in (4, 5, 6, 7):
            raise Exception('Equipamento pediu username via prompt — não suportado neste modo')
        if idx in (8, 9):
            raise Exception('Acesso negado / conexão recusada')
        if idx in (10, 11):
            raise Exception('Timeout ou EOF durante autenticação')
        # Enviar senha
        proc.sendline(senha.encode())
        # Aguardar prompt
        proc.expect(_PROMPT_RE, timeout=15)

        if is_huawei:
            proc.sendline(b'screen-length 0 temporary')
            proc.expect(_PROMPT_RE, timeout=8)

        # Padrões de prompt esperados — inclui [Y/N] e [Y(yes)/N(no)/C(cancel)] para Huawei NE/VS
        _PROMPT_OR_CONFIRM = [
            _PROMPT_RE,
            rb'\[Y/N\]', rb'\[y/n\]',
            rb'\[Y\(yes\)/N\(no\)', rb'\[y\(yes\)/n\(no\)',  # Huawei NE commit-based
            rb'continue\?',
            pexpect.TIMEOUT, pexpect.EOF,
        ]

        # Suporte multi-linha: envia cada linha separadamente
        linhas_cmd = [l.strip() for l in comando.split('\n') if l.strip()]
        all_output = []
        for linha in linhas_cmd:
            proc.sendline(linha.encode())
            idx = proc.expect(_PROMPT_OR_CONFIRM, timeout=timeout)
            raw = proc.before or b''
            chunk = raw.decode('utf-8', errors='replace').splitlines()
            if chunk and linha in chunk[0]:
                chunk = chunk[1:]
            all_output.extend(chunk)
            # Confirmação Y/N (qualquer variante) — responde automaticamente com y
            if idx in (1, 2, 3, 4, 5):
                proc.sendline(b'y')
                proc.expect(_PROMPT_RE, timeout=10)
                extra = (proc.before or b'').decode('utf-8', errors='replace').splitlines()
                all_output.extend(extra)
            elif idx in (6, 7):
                break  # timeout/EOF — sai do loop
        return '\n'.join(all_output).strip()
    finally:
        try:
            proc.sendline(b'quit')
        except Exception:
            pass
        try:
            proc.close(force=True)
        except Exception:
            pass


def _paramiko_proxy_exec(proxy, acesso, comando: str,
                          is_huawei: bool, timeout: int = 25) -> str:
    """Executa comando via paramiko através de um proxy SSH (direct-tcpip)."""
    import re as _re

    proxy_client = _proxy_pool.get(proxy)
    _pool_hit = proxy_client is not None
    if not _pool_hit:
        proxy_client = paramiko.SSHClient()
        proxy_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        proxy_client.connect(
            hostname=proxy.host, port=int(proxy.porta),
            username=proxy.usuario, password=proxy.senha,
            timeout=12, look_for_keys=False, allow_agent=False, banner_timeout=12,
        )
        _proxy_pool.put(proxy, proxy_client)
    try:
        proxy_transport = proxy_client.get_transport()
        try:
            dest_sock = proxy_transport.open_channel(
                'direct-tcpip', (acesso.host, int(acesso.porta)), ('127.0.0.1', 0), timeout=12,
            )
        except Exception:
            # Conexão do pool expirou — reconectar
            _proxy_pool.remove(proxy)
            proxy_client = paramiko.SSHClient()
            proxy_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            proxy_client.connect(
                hostname=proxy.host, port=int(proxy.porta),
                username=proxy.usuario, password=proxy.senha,
                timeout=12, look_for_keys=False, allow_agent=False, banner_timeout=12,
            )
            _proxy_pool.put(proxy, proxy_client)
            proxy_transport = proxy_client.get_transport()
            dest_sock = proxy_transport.open_channel(
                'direct-tcpip', (acesso.host, int(acesso.porta)), ('127.0.0.1', 0), timeout=12,
            )
        dest_transport = paramiko.Transport(dest_sock)
        dest_transport._preferred_kex = _ZTE_PREFERRED_KEX
        dest_transport.start_client(timeout=12)
        dest_transport.auth_password(acesso.usuario, acesso.senha)
        if not dest_transport.is_authenticated():
            raise Exception('Autenticação falhou no equipamento destino')

        shell = dest_transport.open_session()
        shell.get_pty(term='xterm-256color', width=220, height=50)
        shell.invoke_shell()
        shell.settimeout(0.1)
        time.sleep(0.5)

        _CONFIRM_RE = _re.compile(rb'\[Y/N\]|\[y/n\]|\[Y\(yes\)|\[y\(yes\)|continue\?', _re.IGNORECASE)

        def _read_until_prompt(sh, wait=8.0) -> tuple:
            """Lê até encontrar prompt ou confirmação Y/N. Retorna (bytes, is_confirm)."""
            buf = bytearray()
            deadline = time.time() + wait
            while time.time() < deadline:
                try:
                    chunk = sh.recv(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if _re.search(_PROMPT_RE, bytes(buf)):
                        return bytes(buf), False
                    if _CONFIRM_RE.search(bytes(buf)):
                        return bytes(buf), True
                except Exception:
                    time.sleep(0.05)
            return bytes(buf), False

        _read_until_prompt(shell, 6)  # consumir banner inicial

        if is_huawei:
            shell.send(b'screen-length 0 temporary\r')
            _read_until_prompt(shell, 5)

        # Suporte multi-linha: envia cada linha separadamente
        linhas_cmd = [l.strip() for l in comando.split('\n') if l.strip()]
        all_output = []
        for linha in linhas_cmd:
            shell.send(linha.encode() + b'\r')
            raw, is_confirm = _read_until_prompt(shell, timeout)
            # Confirmação Y/N — responde automaticamente com y
            if is_confirm:
                shell.send(b'y\r')
                extra, _ = _read_until_prompt(shell, 10)
                raw = raw + extra
            chunk = raw.decode('utf-8', errors='replace').splitlines()
            if chunk and linha.lower() in chunk[0].lower():
                chunk = chunk[1:]
            while chunk and _re.search(r'[>#\$\]]\s*$', chunk[-1]):
                chunk.pop()
            all_output.extend(chunk)
        return '\n'.join(all_output).strip()
    finally:
        try:
            shell.close()
        except Exception:
            pass
        try:
            dest_transport.close()
        except Exception:
            pass
        # Não fechar proxy_client — está no pool para reutilização


def platform_ssh_exec(acesso, comando: str, timeout: int = 25) -> str:
    """
    Executa um único comando SSH em um Acesso usando a mesma infraestrutura
    da plataforma (suporte a algoritmos legados, proxy, Huawei paging).

    Retorna o output como string. Lança Exception em caso de erro.
    """
    from .models import ProxyServer
    import ipaddress as _ip

    fabricante = ''
    if acesso.modelo:
        fabricante = (getattr(acesso.modelo, 'fabricante', '') or '').lower()
    is_huawei = fabricante in ('huawei',) or 'huawei' in (acesso.tipo or '').lower()

    def _is_private(host: str) -> bool:
        try:
            return _ip.ip_address(host).is_private
        except ValueError:
            return False

    # Roteamento: IP privado → VPN isolada (source bind) ou proxy; público → direto
    if _is_private(acesso.host):
        # Verifica VPN isolada por interface primeiro
        from .models import VPNWireGuard
        vpn = None
        try:
            host_ip = _ip.ip_address(acesso.host)
            for v in VPNWireGuard.objects.filter(cliente=acesso.cliente, ativo=True, peer_no_servidor=True):
                for r in v.redes_lista():
                    try:
                        if host_ip in _ip.ip_network(r, strict=False):
                            vpn = v
                            break
                    except ValueError:
                        pass
                if vpn:
                    break
        except Exception:
            pass

        iface = vpn.interface_nome if vpn else 'wg0'
        isolada_ativa = (
            vpn and vpn.servidor_ip_local and
            iface not in ('wg0', '') and
            _wg_peer_ativo(iface)
        )
        if isolada_ativa:
            # Interface isolada com handshake ativo: source bind para routing table correto
            src = vpn.servidor_ip_local
            cmd_line = f"ssh -b {src} {_SSH_FLAGS} -p {acesso.porta} {acesso.usuario}@{acesso.host}"
            return _pexpect_exec(cmd_line, acesso.senha, comando, is_huawei, timeout)

        proxy = ProxyServer.objects.filter(cliente=acesso.cliente, ativo=True).first()
        if proxy:
            return _paramiko_proxy_exec(proxy, acesso, comando, is_huawei, timeout)
        # sem proxy/VPN, tenta direto
        cmd_line = f"ssh {_SSH_FLAGS} -p {acesso.porta} {acesso.usuario}@{acesso.host}"
        return _pexpect_exec(cmd_line, acesso.senha, comando, is_huawei, timeout)
    elif getattr(acesso, 'tipo', '') == 'CGNAT':
        # Tenta direto primeiro
        try:
            cmd_line = f"ssh {_SSH_FLAGS} -p {acesso.porta} {acesso.usuario}@{acesso.host}"
            return _pexpect_exec(cmd_line, acesso.senha, comando, is_huawei, timeout)
        except Exception:
            pass
        # Fallback via proxy
        proxy = ProxyServer.objects.filter(cliente=acesso.cliente, ativo=True).first()
        if proxy:
            return _paramiko_proxy_exec(proxy, acesso, comando, is_huawei, timeout)
        raise RuntimeError(f"CGNAT sem proxy disponível para {acesso.host}")
    else:
        cmd_line = (
            f"ssh {_SSH_FLAGS} "
            f"-p {acesso.porta} {acesso.usuario}@{acesso.host}"
        )
        return _pexpect_exec(cmd_line, acesso.senha, comando, is_huawei, timeout)
