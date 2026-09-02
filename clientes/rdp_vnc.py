import os
import time
import socket
import shutil
import logging
import subprocess
import threading

logger = logging.getLogger(__name__)

# Tenta FreeRDP 3.x primeiro (pacote freerdp3-x11); alguns sistemas só têm
# o binário 2.x (freerdp2-x11), que usa o mesmo conjunto de flags aqui usado.
RDP_CLIENT_CANDIDATES = ["xfreerdp3", "xfreerdp"]


class RdpVNCManager:
    """Gerencia o ciclo de vida do Xvfb, Openbox, x11vnc e xfreerdp para uma
    sessão RDP via Web — mesmo padrão do WinboxVNCManager (clientes/winbox_vnc.py),
    só trocando o binário final executado dentro do Xvfb."""

    def __init__(self, host, port, user, password, width=1366, height=768, record_path=None):
        self.host = host
        self.port = port
        self.user = user
        self.password = password

        # libx264/yuv420p exige dimensões pares (ver WinboxVNCManager).
        self.width = max(800, int(width)) & ~1
        self.height = max(600, int(height)) & ~1
        self.display_num = None
        self.vnc_port = None
        self.processes = []
        self.record_path = record_path
        self.recording = False
        self._stop_lock = threading.Lock()
        self._stopped = False
        self._rdp_stderr = []

    @staticmethod
    def _wait_for_socket(path, timeout=3.0, interval=0.02):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(path):
                return True
            time.sleep(interval)
        return False

    @staticmethod
    def _wait_for_port(port, timeout=3.0, interval=0.02):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                    return True
            except OSError:
                time.sleep(interval)
        return False

    def start(self):
        """Inicia os processos e retorna a porta VNC local."""
        self.display_num = self._find_free_display()
        self.vnc_port = self._find_free_port()

        logger.info(f"🚀 Iniciando ambiente RDP Web no Display :{self.display_num} / VNC Port {self.vnc_port} / Resolução {self.width}x{self.height}")

        # 1. Xvfb
        xvfb_cmd = ["Xvfb", f":{self.display_num}", "-screen", "0", f"{self.width}x{self.height}x24", "-nolisten", "tcp"]
        try:
            p_xvfb = subprocess.Popen(xvfb_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_xvfb)
        except FileNotFoundError:
            raise Exception("Xvfb não está instalado no servidor. Execute: apt-get install xvfb")

        if not self._wait_for_socket(f"/tmp/.X11-unix/X{self.display_num}"):
            logger.warning(f"⏱️ Xvfb :{self.display_num} não respondeu a tempo, seguindo mesmo assim")

        # 2. Openbox
        env = os.environ.copy()
        env["DISPLAY"] = f":{self.display_num}"
        try:
            p_wm = subprocess.Popen(["openbox", "--config-file", "/opt/crm/clientes/openbox_rc.xml"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_wm)
        except FileNotFoundError:
            logger.warning("Openbox não encontrado. RDP rodará sem bordas/movimentação.")

        # 3. x11vnc — NUNCA usar -ncache aqui (ver docs/winbox_vnc.md e memória do projeto).
        vnc_cmd = [
            "x11vnc", "-display", f":{self.display_num}",
            "-nopw", "-listen", "127.0.0.1",
            "-xkb", "-rfbport", str(self.vnc_port),
            "-shared", "-forever", "-quiet",
            "-nonap", "-threads", "-wait", "10"
        ]
        try:
            p_vnc = subprocess.Popen(vnc_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_vnc)
        except FileNotFoundError:
            self.stop()
            raise Exception("x11vnc não está instalado no servidor. Execute: apt-get install x11vnc")

        if not self._wait_for_port(self.vnc_port):
            logger.warning(f"⏱️ x11vnc na porta {self.vnc_port} não respondeu a tempo, seguindo mesmo assim")

        # 4. xfreerdp — /f (fullscreen) ocupa toda a resolução do Xvfb sem
        # precisar de xdotool pra maximizar (diferente do WinBox, que abre em
        # janela e precisa ser redimensionado/movido depois).
        rdp_bin = None
        for candidate in RDP_CLIENT_CANDIDATES:
            if shutil.which(candidate):
                rdp_bin = candidate
                break
        if not rdp_bin:
            self.stop()
            raise Exception(
                "Cliente RDP não está instalado no servidor. "
                "Execute: apt-get install freerdp3-x11 (ou freerdp2-x11)."
            )

        # NÃO force "/sec:..." aqui. Windows Server com NLA obrigatório (padrão
        # desde o 2012) recusa TLS puro com HYBRID_REQUIRED_BY_SERVER, o
        # xfreerdp morre em ~100 ms e o usuário só vê a tela preta do Xvfb.
        # Sem o flag, o FreeRDP negocia sozinho (NLA → TLS → RDP legado) e
        # atende tanto servidor novo quanto servidor antigo sem NLA.
        #
        # "+clipboard" (forma aceita tanto pelo FreeRDP 2 quanto pelo 3) liga o
        # canal cliprdr. Sem ele o texto que o noVNC empurra pra seleção X11 do
        # Xvfb (via x11vnc) nunca chega ao Windows, e colar de fora dentro da
        # sessão RDP simplesmente não fazia nada. O FreeRDP 3 já liga por padrão,
        # mas o binário 2.x do fallback não — então deixamos explícito.
        rdp_cmd = [
            rdp_bin,
            f"/v:{self.host}:{self.port}",
            f"/u:{self.user}",
            f"/p:{self.password}",
            "/cert:ignore",
            "/dynamic-resolution",
            "+clipboard",
            "/f",
        ]
        try:
            p_rdp = subprocess.Popen(rdp_cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.processes.append(p_rdp)
        except FileNotFoundError:
            self.stop()
            raise Exception(f"Binário do cliente RDP ({rdp_bin}) sumiu antes de iniciar.")

        # Lê o stderr do xfreerdp num thread — sem isso o pipe enche e trava o
        # cliente, e o motivo real de uma falha se perderia (era DEVNULL antes).
        threading.Thread(target=self._drenar_stderr, args=(p_rdp,), daemon=True).start()

        # Falha de RDP (NLA, senha errada, porta fechada) mata o processo em
        # menos de 1 s. Espera curta pra transformar isso em erro na tela do
        # usuário em vez de VNC conectado mostrando preto pra sempre.
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if p_rdp.poll() is not None:
                motivo = self._motivo_falha_rdp()
                self.stop()
                raise Exception(motivo)
            time.sleep(0.05)

        # 5. Gravação opcional (auditoria) — mesmo padrão do WinboxVNCManager.
        if self.record_path:
            if shutil.which("ffmpeg"):
                self.recording = True
                threading.Thread(target=self._start_recording, args=(env,), daemon=True).start()
            else:
                logger.warning("ffmpeg não encontrado — gravação de tela desabilitada para esta sessão.")

        return self.vnc_port

    # Linhas que o FreeRDP marca como [ERROR] mas que aparecem em sessão
    # saudável — vão pro log em DEBUG pra não poluir o log do daphne com erro
    # que não é erro. Kerberos: sem realm configurado ele cai pra NTLM e
    # conecta; os dois últimos são o próprio encerramento da sessão.
    _ERROS_BENIGNOS_RDP = (
        "kerberos_AcquireCredentialsHandle",
        "krb5_parse_name",
        "fsig_term_handler",
        "ERRCONNECT_CONNECT_CANCELLED",
    )

    def _drenar_stderr(self, proc, max_linhas=40):
        """Consome o stderr do xfreerdp guardando só as últimas linhas."""
        try:
            for linha in iter(proc.stderr.readline, b""):
                texto = linha.decode("utf-8", "replace").rstrip()
                if not texto:
                    continue
                self._rdp_stderr.append(texto)
                del self._rdp_stderr[:-max_linhas]
                if "[ERROR]" not in texto:
                    continue
                benigno = (
                    self._stopped
                    or any(m in texto for m in self._ERROS_BENIGNOS_RDP)
                )
                nivel = logger.debug if benigno else logger.error
                nivel(f"[xfreerdp {self.host}] {texto}")
        except Exception:
            pass
        finally:
            try:
                proc.stderr.close()
            except Exception:
                pass

    # Trechos do stderr do FreeRDP → mensagem que faz sentido pra quem opera.
    _FALHAS_RDP = (
        ("HYBRID_REQUIRED_BY_SERVER", "O servidor exige NLA e a negociação falhou — confira usuário, senha e domínio do acesso."),
        ("ERRCONNECT_LOGON_FAILURE", "Usuário ou senha inválidos no servidor RDP."),
        ("ERRCONNECT_ACCOUNT_LOCKED_OUT", "Conta bloqueada no servidor RDP."),
        ("ERRCONNECT_ACCOUNT_EXPIRED", "Conta expirada no servidor RDP."),
        ("ERRCONNECT_PASSWORD_EXPIRED", "Senha expirada no servidor RDP — troque a senha antes de conectar."),
        ("ERRCONNECT_PASSWORD_MUST_CHANGE", "O servidor exige troca de senha neste primeiro acesso."),
        ("ERRCONNECT_CONNECT_TRANSPORT_FAILED", "Não foi possível abrir a conexão TCP até o host RDP."),
        ("ERRCONNECT_CONNECT_FAILED", "Não foi possível abrir a conexão TCP até o host RDP."),
        ("ERRCONNECT_SECURITY_NEGO_CONNECT_FAILED", "Falha na negociação de segurança com o servidor RDP."),
        ("ERRINFO_LOGOFF_BY_USER", "A sessão foi encerrada no servidor."),
    )

    def _motivo_falha_rdp(self):
        """Traduz o stderr do xfreerdp na causa provável da tela preta."""
        # Dá um instante pro thread de leitura terminar de drenar o pipe.
        time.sleep(0.2)
        stderr = "\n".join(self._rdp_stderr)
        for marcador, msg in self._FALHAS_RDP:
            if marcador in stderr:
                return f"RDP {self.host}:{self.port} — {msg}"

        ultimo_erro = next(
            (l for l in reversed(self._rdp_stderr) if "[ERROR]" in l),
            "",
        )
        detalhe = ultimo_erro.split("] - ")[-1] if ultimo_erro else "sem detalhes no log do cliente RDP"
        return f"O cliente RDP encerrou antes de exibir a tela ({self.host}:{self.port}) — {detalhe}"

    def _start_recording(self, env):
        time.sleep(1.5)
        with self._stop_lock:
            if self._stopped:
                return
            ffmpeg_cmd = [
                "nice", "-n", "15", "ionice", "-c", "3",
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "x11grab", "-video_size", f"{self.width}x{self.height}",
                "-framerate", "8", "-i", f":{self.display_num}",
                "-vcodec", "libx264", "-preset", "ultrafast", "-crf", "28",
                "-pix_fmt", "yuv420p", self.record_path,
            ]
            try:
                p_ffmpeg = subprocess.Popen(ffmpeg_cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.processes.append(p_ffmpeg)
            except FileNotFoundError:
                logger.warning("ffmpeg sumiu entre a checagem e o start — gravação desabilitada para esta sessão.")

    def stop(self):
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
            logger.info(f"🛑 Encerrando ambiente RDP Web (Display :{self.display_num})")
            for p in reversed(self.processes):
                try:
                    p.terminate()
                    p.wait(timeout=5)
                except:
                    try:
                        p.kill()
                    except:
                        pass
            self.processes.clear()

    def _find_free_display(self, start=100, max_attempts=100):
        for i in range(start, start + max_attempts):
            if not os.path.exists(f"/tmp/.X11-unix/X{i}"):
                return i
        raise Exception("Nenhum display X11 livre encontrado")

    def _find_free_port(self, start=5900, max_attempts=100):
        for port in range(start, start + max_attempts):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(('127.0.0.1', port))
                sock.close()
                return port
            except OSError:
                continue
        raise Exception("Nenhuma porta VNC disponível")
