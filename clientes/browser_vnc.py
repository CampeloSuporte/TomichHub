import os
import time
import socket
import logging
import subprocess

logger = logging.getLogger(__name__)

class BrowserVNCManager:
    """Gerencia o ciclo de vida do Xvfb, Openbox, x11vnc e um Navegador para uma sessão via Web."""
    
    def __init__(self, url, browser_path=None):
        self.url = url
        self.browser_path = browser_path or self._find_browser()
        
        self.display_num = None
        self.vnc_port = None
        self.processes = []
        
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
        
        logger.info(f"🚀 Iniciando Browser Web no Display :{self.display_num} / VNC Port {self.vnc_port} -> {self.url}")
        
        # 1. Start Xvfb
        xvfb_cmd = ["Xvfb", f":{self.display_num}", "-screen", "0", "1366x768x24", "-nolisten", "tcp"]
        try:
            p_xvfb = subprocess.Popen(xvfb_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_xvfb)
        except FileNotFoundError:
            raise Exception("Xvfb não está instalado no servidor.")
            
        time.sleep(0.5)
        
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
            
        time.sleep(0.5)
        
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
            
        return self.vnc_port
        
    def stop(self):
        """Mata todos os processos."""
        logger.info(f"🛑 Encerrando Browser Web (Display :{self.display_num})")
        for p in reversed(self.processes):
            try:
                p.terminate()
                p.wait(timeout=2)
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
