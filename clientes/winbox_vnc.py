import os
import time
import socket
import shutil
import logging
import subprocess
import threading

logger = logging.getLogger(__name__)

WINBOX4_PATH   = "/opt/crm/static/winbox4/WinBox"
WINBOX3_PATH   = "/opt/crm/static/winbox3/winbox.exe"
WINE_PREFIX    = "/opt/crm/wine-prefix"


class WinboxVNCManager:
    """Gerencia o ciclo de vida do Xvfb, Openbox, x11vnc e WinBox para uma sessão via Web."""

    def __init__(self, host, port, user, password, version='4', width=1366, height=768, record_path=None):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        # WinBox 3.43 nunca teve build nativo Linux (só Windows) — roda via Wine
        # com um WINEPREFIX 32-bit pré-inicializado (ver docs/winbox_vnc.md).
        # WinBox 4.x é nativo (ELF), sem Wine no caminho.
        self.version = '3' if str(version) == '3' else '4'

        # libx264/yuv420p exige dimensões pares — a resolução vem do painel do
        # navegador do cliente (largura/altura ímpar é comum) e, se ímpar, o
        # ffmpeg falha ao abrir o encoder e o .mp4 fica vazio (0 bytes, sem
        # moov atom, gravação não reproduz na auditoria de acessos).
        self.width = max(800, int(width)) & ~1
        self.height = max(600, int(height)) & ~1
        self.display_num = None
        self.vnc_port = None
        self.processes = []
        self.record_path = record_path
        self.recording = False
        self._stop_lock = threading.Lock()
        self._stopped = False

    @staticmethod
    def _wait_for_socket(path, timeout=3.0, interval=0.02):
        """Poll até o socket X11 existir, em vez de um sleep fixo — Xvfb
        normalmente sobe em poucas dezenas de ms, então isso troca ~500ms
        de espera cega por ~20-50ms reais na maioria das sessões."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(path):
                return True
            time.sleep(interval)
        return False

    @staticmethod
    def _wait_for_port(port, timeout=3.0, interval=0.02):
        """Poll até a porta VNC aceitar conexão, em vez de sleep fixo."""
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
        
        logger.info(f"🚀 Iniciando ambiente WinBox {self.version} Web no Display :{self.display_num} / VNC Port {self.vnc_port} / Resolução {self.width}x{self.height}")
        
        # 1. Start Xvfb com resolucao dinamica baseada no tamanho do painel do cliente
        # 24bpp: em 16bpp o alpha-blending dos ícones do WinBox 3.43 (GDI via Wine)
        # quebra e renderiza um quadrado preto atrás de cada ícone (reproduzido e
        # confirmado comparando os dois depths no mesmo equipamento — não tem relação
        # com disputa de CPU/gravação, como um comentário antigo aqui sugeria).
        xvfb_cmd = ["Xvfb", f":{self.display_num}", "-screen", "0", f"{self.width}x{self.height}x24", "-nolisten", "tcp"]
        try:
            p_xvfb = subprocess.Popen(xvfb_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_xvfb)
        except FileNotFoundError:
            raise Exception("Xvfb não está instalado no servidor. Execute: apt-get install xvfb")
            
        # Espera ativa em vez de sleep(0.5) fixo: assim que o socket X11
        # aparece a gente já segue, sem esperar o resto do meio segundo à toa.
        if not self._wait_for_socket(f"/tmp/.X11-unix/X{self.display_num}"):
            logger.warning(f"⏱️ Xvfb :{self.display_num} não respondeu a tempo, seguindo mesmo assim")

        # 2. Start Openbox (gerenciador de janelas)
        env = os.environ.copy()
        env["DISPLAY"] = f":{self.display_num}"
        
        try:
            # openbox_rc.xml remove as bordas e maximiza as janelas
            p_wm = subprocess.Popen(["openbox", "--config-file", "/opt/crm/clientes/openbox_rc.xml"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_wm)
        except FileNotFoundError:
            logger.warning("Openbox não encontrado. WinBox rodará sem bordas/movimentação.")
            
        # 3. Start x11vnc
        # -nonap: desliga o backoff de polling do x11vnc quando ocioso — sem isso,
        #   a primeira interação após um período parado sofre um atraso perceptível
        #   até o x11vnc "acordar" e voltar a pollar a tela na taxa normal.
        # -threads: paraleliza a codificação de regiões da tela entre CPUs.
        # -wait 10: reduz o intervalo de polling de tela de 20ms (padrão) para 10ms —
        #   mais atualizações por segundo, ao custo de um pouco mais de CPU.
        # NUNCA usar -ncache aqui — quebra o dimensionamento reportado ao noVNC
        # (ver docs/winbox_vnc.md e memória do projeto).
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
            
        # Idem: espera ativa até a porta do x11vnc aceitar conexão em vez de
        # sleep(0.5) cego — some com esse atraso na maioria das sessões.
        if not self._wait_for_port(self.vnc_port):
            logger.warning(f"⏱️ x11vnc na porta {self.vnc_port} não respondeu a tempo, seguindo mesmo assim")

        # 4. Start WinBox
        target = f"{self.host}:{self.port}"
        if self.version == '3':
            # WinBox 3.43 (Windows .exe) via Wine, usando o prefixo 32-bit
            # pré-inicializado — inicializar um prefixo novo a cada sessão seria
            # lento (wineboot na primeira execução) e desnecessário.
            winbox_path = WINBOX3_PATH
            env["WINEPREFIX"] = WINE_PREFIX
            env["WINEDEBUG"] = "-all"
            winbox_cmd = ["wine", WINBOX3_PATH, target, self.user, self.password]
        else:
            winbox_path = WINBOX4_PATH
            winbox_cmd = [WINBOX4_PATH, target, self.user, self.password]
        try:
            p_winbox = subprocess.Popen(winbox_cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_winbox)
        except FileNotFoundError:
            self.stop()
            raise Exception(f"Binário do WinBox não encontrado em {winbox_path}")

        # 5. Espera a janela do WinBox aparecer, maximiza e loga o resultado
        disp = self.display_num
        w, h = self.width, self.height
        subprocess.Popen(
            ["bash", "-c",
             f"xdotool search --sync --name 'WinBox' windowsize {w} {h} windowmove 0 0 && "
             f"sleep 1 && wmctrl -lG >> /tmp/winbox_debug.log 2>&1"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        # 6. Start ffmpeg (gravação de tela para auditoria) — disparado numa
        # thread à parte pra não segurar o retorno de start() (e, com isso,
        # a conexão do cliente ao VNC) atrás do delay de 1.5s que dá tempo
        # do WinBox terminar de desenhar a UI antes de começar a capturar
        # (o ícone com fundo preto era causado pelo depth 16bpp do Xvfb, não
        # pela gravação — ver comentário no Xvfb acima). `self.recording` é
        # decidido de forma síncrona aqui (só checando se o binário existe),
        # pra o chamador já saber na hora se a auditoria vai ter vídeo.
        if self.record_path:
            if shutil.which("ffmpeg"):
                self.recording = True
                threading.Thread(target=self._start_recording, args=(env,), daemon=True).start()
            else:
                logger.warning("ffmpeg não encontrado — gravação de tela desabilitada para esta sessão.")

        return self.vnc_port

    def _start_recording(self, env):
        """Roda em thread separada: espera a UI do WinBox assentar e só
        então sobe o ffmpeg, sem bloquear a conexão do cliente ao VNC."""
        time.sleep(1.5)  # dá tempo do WinBox terminar de desenhar a UI
        with self._stop_lock:
            if self._stopped:
                return
            # nice/ionice em prioridade baixa: o ffmpeg roda a sessão inteira
            # (não só no início), e x11grab faz XGetImage contra o mesmo Xvfb
            # que o Wine usa pra desenhar — sem ceder CPU/IO, a gravação
            # compete com o GDI/Wine durante toda a interação, deixando a
            # tela mais lenta pra mexer.
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
        """Mata todos os processos iniciados por esta sessão.
        Idempotente/thread-safe: limpar_recursos() pode ser chamado
        concorrentemente pela thread de leitura do VNC e pela thread de
        disconnect() do WebSocket — sem essa trava, o ffmpeg recebia dois
        SIGTERM em sequência e, no segundo, aborta sem finalizar o mp4
        (comportamento documentado do próprio ffmpeg), gerando um arquivo
        de 0 bytes mesmo em sessões longas."""
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
            logger.info(f"🛑 Encerrando ambiente WinBox Web (Display :{self.display_num})")
            for p in reversed(self.processes):
                try:
                    p.terminate()
                    # ffmpeg trata SIGTERM como parada graciosa e precisa de tempo
                    # para escrever o trailer do mp4 — 2s (usado pelos outros
                    # processos) é curto demais e corrompe a gravação.
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
