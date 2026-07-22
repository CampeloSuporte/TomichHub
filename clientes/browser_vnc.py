import os
import time
import socket
import shutil
import logging
import subprocess
import threading

logger = logging.getLogger(__name__)

class BrowserVNCManager:
    """Gerencia o ciclo de vida do Xvfb, Openbox, x11vnc e um Navegador para uma sessão via Web."""
    
    def __init__(self, url, browser_path=None, record_path=None, width=1366, height=768):
        self.url = url
        self.browser_path = browser_path or self._find_browser()

        # mesmo tratamento do WinboxVNCManager: libx264/yuv420p exige dimensoes
        # pares, e o ffmpeg falha silenciosamente (mp4 de 0 bytes) se nao forem.
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

    def _find_browser(self):
        """Tenta encontrar um navegador instalado no sistema."""
        browsers = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/usr/bin/firefox",
            "/usr/bin/firefox-esr",
            "/usr/bin/midori",
            "/usr/bin/surf",
            "/snap/bin/chromium",
        ]
        for b in browsers:
            if os.path.exists(b):
                return b
        # Se não encontrar, tenta pelo which
        try:
            for b_name in ["google-chrome", "chromium-browser", "chromium", "firefox", "midori", "surf"]:
                path = subprocess.check_output(["which", b_name], stderr=subprocess.STDOUT).decode().strip()
                if path:
                    return path
        except:
            pass
        return None

    def start(self):
        """Inicia os processos e retorna a porta VNC local."""
        if not self.browser_path:
            raise Exception("Nenhum navegador (Chrome/Chromium/Firefox) encontrado no servidor. Por favor, instale um navegador.")

        self.display_num = self._find_free_display()
        self.vnc_port = self._find_free_port()
        
        logger.info(f"🚀 Iniciando Browser Web no Display :{self.display_num} / VNC Port {self.vnc_port} / Resolução {self.width}x{self.height} -> {self.url}")

        # 1. Start Xvfb com resolucao dinamica baseada no tamanho do painel do cliente
        xvfb_cmd = ["Xvfb", f":{self.display_num}", "-screen", "0", f"{self.width}x{self.height}x24", "-nolisten", "tcp"]
        try:
            p_xvfb = subprocess.Popen(xvfb_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_xvfb)
        except FileNotFoundError:
            raise Exception("Xvfb não está instalado no servidor.")
            
        if not self._wait_for_socket(f"/tmp/.X11-unix/X{self.display_num}"):
            logger.warning(f"⏱️ Xvfb :{self.display_num} não respondeu a tempo, seguindo mesmo assim")

        # 2. Start Openbox
        env = os.environ.copy()
        env["DISPLAY"] = f":{self.display_num}"
        # Algumas libs de GUI precisam desses caras
        env["XAUTHORITY"] = "/dev/null"
        
        try:
            p_wm = subprocess.Popen(["openbox", "--config-file", "/opt/crm/clientes/openbox_rc.xml"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_wm)
        except FileNotFoundError:
            logger.warning("Openbox não encontrado.")
            
        # 3. Start x11vnc
        vnc_cmd = [
            "x11vnc", "-display", f":{self.display_num}", 
            "-nopw", "-listen", "127.0.0.1", 
            "-xkb", "-rfbport", str(self.vnc_port),
            "-shared", "-forever", "-quiet"
        ]
        try:
            p_vnc = subprocess.Popen(vnc_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_vnc)
        except FileNotFoundError:
            self.stop()
            raise Exception("x11vnc não está instalado no servidor.")
            
        if not self._wait_for_port(self.vnc_port):
            logger.warning(f"⏱️ x11vnc na porta {self.vnc_port} não respondeu a tempo, seguindo mesmo assim")

        # 4. Start Browser
        # Comandos específicos para Chromium/Chrome para facilitar o uso em VNC
        browser_cmd = [self.browser_path]
        
        if "chrome" in self.browser_path.lower() or "chromium" in self.browser_path.lower():
            browser_cmd += [
                "--no-sandbox",
                "--disable-gpu",
                "--start-maximized",
                "--incognito",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--ignore-certificate-errors",
                "--allow-insecure-localhost",
                "--disable-web-security",
                "--proxy-server=direct://",
                "--proxy-bypass-list=*",
                "--disable-features=BlockInsecurePrivateNetworkRequests",
                f"--user-data-dir=/tmp/chrome-session-{int(time.time())}",
                self.url
            ]



        elif "firefox" in self.browser_path.lower():
            browser_cmd += ["--kiosk", self.url]
        else:
            browser_cmd.append(self.url)

        try:
            p_browser = subprocess.Popen(browser_cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_browser)
        except FileNotFoundError:
            self.stop()
            raise Exception(f"Navegador não encontrado em {self.browser_path}")

        # 5. Start ffmpeg (gravação de tela para auditoria) — disparado numa
        # thread à parte pra não segurar o retorno de start() (e a conexão
        # do cliente ao VNC) atrás do delay que dá tempo da página assentar
        # antes de começar a capturar.
        if self.record_path:
            if shutil.which("ffmpeg"):
                self.recording = True
                threading.Thread(target=self._start_recording, args=(env,), daemon=True).start()
            else:
                logger.warning("ffmpeg não encontrado — gravação de tela desabilitada para esta sessão.")

        return self.vnc_port

    def _start_recording(self, env):
        time.sleep(1.5)  # dá tempo do navegador terminar de carregar a página
        with self._stop_lock:
            if self._stopped:
                return
            ffmpeg_cmd = [
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
        """Mata todos os processos. Idempotente/thread-safe — ver comentário
        equivalente em WinboxVNCManager.stop() sobre o duplo SIGTERM."""
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
            logger.info(f"🛑 Encerrando Browser Web (Display :{self.display_num})")
            for p in reversed(self.processes):
                try:
                    p.terminate()
                    # ffmpeg precisa de mais tempo que 2s para finalizar o mux do mp4
                    p.wait(timeout=5)
                except:
                    try: p.kill()
                    except: pass
            self.processes.clear()
        
    def _find_free_display(self, start=200, max_attempts=100):
        for i in range(start, start + max_attempts):
            if not os.path.exists(f"/tmp/.X11-unix/X{i}"):
                return i
        raise Exception("Nenhum display X11 livre encontrado")
        
    def _find_free_port(self, start=6000, max_attempts=100):
        for port in range(start, start + max_attempts):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(('127.0.0.1', port))
                sock.close()
                return port
            except OSError: continue
        raise Exception("Nenhuma porta VNC disponível")
