import os
import time
import socket
import logging
import subprocess

logger = logging.getLogger(__name__)

class WinboxVNCManager:
    """Gerencia o ciclo de vida do Xvfb, Openbox, x11vnc e WinBox para uma sessão via Web."""
    
    def __init__(self, host, port, user, password, winbox_path="/opt/crm/static/winbox4/WinBox"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.winbox_path = winbox_path
        
        self.display_num = None
        self.vnc_port = None
        self.processes = []
        
    def start(self):
        """Inicia os processos e retorna a porta VNC local."""
        self.display_num = self._find_free_display()
        self.vnc_port = self._find_free_port()
        
        logger.info(f"🚀 Iniciando ambiente WinBox Web no Display :{self.display_num} / VNC Port {self.vnc_port}")
        
        # 1. Start Xvfb (Widescreen HD 1366x768 para preencher melhor a tela)
        xvfb_cmd = ["Xvfb", f":{self.display_num}", "-screen", "0", "1366x768x24", "-nolisten", "tcp"]
        try:
            p_xvfb = subprocess.Popen(xvfb_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_xvfb)
        except FileNotFoundError:
            raise Exception("Xvfb não está instalado no servidor. Execute: apt-get install xvfb")
            
        time.sleep(0.5) # Aguardar Xvfb subir
        
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
            raise Exception("x11vnc não está instalado no servidor. Execute: apt-get install x11vnc")
            
        time.sleep(0.5) # Aguardar VNC subir
        
        # 4. Start WinBox
        target = f"{self.host}:{self.port}"
        winbox_cmd = [self.winbox_path, target, self.user, self.password]
        try:
            p_winbox = subprocess.Popen(winbox_cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(p_winbox)
        except FileNotFoundError:
            self.stop()
            raise Exception(f"Binário do WinBox não encontrado em {self.winbox_path}")
            
        return self.vnc_port
        
    def stop(self):
        """Mata todos os processos iniciados por esta sessão."""
        logger.info(f"🛑 Encerrando ambiente WinBox Web (Display :{self.display_num})")
        for p in reversed(self.processes):
            try:
                p.terminate()
                p.wait(timeout=2)
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
