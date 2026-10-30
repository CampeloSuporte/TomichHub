import os
import shutil
import logging
import subprocess
import tempfile
import threading
import time

from .winbox_vnc import WinboxVNCManager

logger = logging.getLogger(__name__)

UNM_APP        = "/opt/unm2000/app/bin/unm2000access"
OPENBOX_RC_UNM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openbox_rc_unm.xml")

# O cliente pede a pasta de trabalho na 1ª abertura (a home é nova a cada
# sessão) e só depois mostra o login. Tempo de sobra: rede lenta até o proxy.
ESPERA_TELAS = 180

# Roda dentro do namespace. A única saída é o socket UNIX: todo TCP vai para o
# redsocks, que fala SOCKS com o socat, que atravessa o socket até o `ssh -D`
# no host. A rota padrão no loopback, com endereço de origem próprio, existe só
# para o connect() chegar ao iptables: sem ela dá "unreachable" antes do
# REDIRECT e, sem o endereço, a resposta se perde (origem 0.0.0.0).
# Args: socket, conf do redsocks, comando do cliente.
REDE_SH = r"""
set -e
ip link set lo up
ip addr add 10.255.0.1/32 dev lo
ip route add default dev lo src 10.255.0.1
iptables -t nat -A OUTPUT -d 127.0.0.0/8 -j RETURN
iptables -t nat -A OUTPUT -p tcp -j REDIRECT --to-ports 12345
socat TCP-LISTEN:10800,bind=127.0.0.1,fork,reuseaddr "UNIX-CONNECT:$1" &
cat > "$2" <<CONF
base { log_debug = off; log_info = off; daemon = off; redirector = iptables; }
redsocks { local_ip = 127.0.0.1; local_port = 12345; ip = 127.0.0.1; port = 10800; type = socks5; }
CONF
redsocks -c "$2" >/dev/null 2>&1 &
shift 2
exec "$@"
"""


class UnmVNCManager(WinboxVNCManager):
    """Sessão do UNM2000 (FiberHome) via Web: mesmo Xvfb/Openbox/x11vnc do
    WinBox Web, trocando o programa e a rede. O cliente fala com o servidor em
    dezenas de portas, informadas pelo próprio servidor, e o túnel de porta
    única do CRM não serve. Com `proxy`, ele roda num namespace de rede próprio
    cujo TCP inteiro sai pelo proxy SSH do cliente, sem saber de proxy nenhum.

    O namespace é do próprio www-data (unshare -Urn, sem root nem sudo) e não
    tem interface além do loopback: não há como sair por fora do proxy nem
    alcançar a rede do servidor. O namespace de PID faz o kernel matar tudo
    junto com a sessão, inclusive se o daphne cair sem passar por stop()."""

    openbox_rc = OPENBOX_RC_UNM

    def __init__(self, host, user, password, proxy=None, width=1366, height=768,
                 screen_width=None, screen_height=None, record_path=None):
        super().__init__(host, 0, user, password, width=width, height=height,
                         screen_width=screen_width, screen_height=screen_height,
                         record_path=record_path)
        self.proxy = proxy
        self._home = None
        self._limpo = False
        self._limpo_lock = threading.Lock()

    def _abrir_proxy(self):
        """Abre o SOCKS no proxy e o expõe num socket UNIX; devolve o caminho."""
        porta = self._find_free_port(start=10800)
        # Senha por ambiente (sshpass -e), fora da lista de processos
        p_ssh = subprocess.Popen(
            ["sshpass", "-e", "ssh", "-N", "-D", f"127.0.0.1:{porta}",
             "-p", str(self.proxy.porta),
             "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             "-o", "PubkeyAuthentication=no", "-o", "ExitOnForwardFailure=yes",
             "-o", "ServerAliveInterval=20", "-o", "LogLevel=ERROR",
             f"{self.proxy.usuario}@{self.proxy.host}"],
            env=dict(os.environ, SSHPASS=self.proxy.senha),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.processes.append(p_ssh)
        limite = time.time() + 15
        while not self._wait_for_port(porta, timeout=0.3):
            if p_ssh.poll() is not None:
                raise Exception(f"Não foi possível abrir o túnel no proxy {self.proxy.nome}")
            if time.time() > limite:
                raise Exception(f"O proxy {self.proxy.nome} não respondeu a tempo")

        sock = os.path.join(self._home, "socks.sock")
        self.processes.append(subprocess.Popen(
            ["socat", f"UNIX-LISTEN:{sock},fork,mode=600", f"TCP:127.0.0.1:{porta}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))
        return sock

    def _iniciar_app(self, env):
        if not os.path.exists(UNM_APP):
            raise Exception("UNM2000 não está instalado no servidor (ver docs/winbox_vnc.md)")
        self._home = tempfile.mkdtemp(prefix="unm_")
        cmd = [UNM_APP, f"-J-Duser.home={self._home}"]
        if self.proxy:
            cmd = ["unshare", "-Urn", "--pid", "--fork", "--kill-child", "--mount-proc",
                   "sh", "-c", REDE_SH, "unm-rede",
                   self._abrir_proxy(), os.path.join(self._home, "redsocks.conf")] + cmd
        app_env = dict(env, HOME=self._home, _JAVA_AWT_WM_NONREPARENTING="1")
        self.processes.append(subprocess.Popen(cmd, env=app_env, cwd="/opt/unm2000/app",
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        threading.Thread(target=self._automatizar_telas, args=(env,), daemon=True).start()

    # ── Telas iniciais ──
    @staticmethod
    def _janela(env, titulo):
        r = subprocess.run(["xdotool", "search", "--name", titulo], env=env,
                           capture_output=True, text=True, timeout=5)
        return r.stdout.split()[0] if r.stdout.split() else None

    @staticmethod
    def _teclar(env, *args, entrada=None):
        subprocess.run(["xdotool", *args], env=env, input=entrada, text=True, timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _digitar(self, env, texto):
        # Pela entrada padrão: a senha não aparece na lista de processos
        self._teclar(env, "type", "--clearmodifiers", "--delay", "30", "--file", "-", entrada=texto)

    def _automatizar_telas(self, env):
        """Responde à pasta de trabalho e preenche o login pelos atalhos do
        próprio diálogo (Alt+letra sublinhada), que não dependem de onde a
        janela abriu. Se algo falhar, o login fica na tela para digitar."""
        pasta_ok = False
        limite = time.time() + ESPERA_TELAS
        try:
            while not self._stopped and time.time() < limite:
                login = self._janela(env, "Log into UNM2000")
                if login:
                    self._preencher_login(env, login)
                    return
                pasta = None if pasta_ok else self._janela(env, "Default Work Space Config")
                if pasta:
                    self._responder_pasta(env, pasta)
                    pasta_ok = True
                time.sleep(0.5)
            if not self._stopped:
                logger.warning(f"UNM2000 :{self.display_num} não mostrou o login em {ESPERA_TELAS}s")
        except Exception as e:
            logger.warning(f"Falha ao automatizar o login do UNM2000 :{self.display_num}: {e}")

    def _responder_pasta(self, env, janela):
        # Esse diálogo abre atrás da tela de abertura, que nunca sai da frente
        abertura = self._janela(env, "Starting UNM2000")
        if abertura:
            self._teclar(env, "windowunmap", abertura)
        self._teclar(env, "windowactivate", "--sync", janela)
        self._teclar(env, "key", "alt+w", "ctrl+a")
        pasta = os.path.join(self._home, "workspace")
        os.makedirs(pasta, exist_ok=True)
        self._digitar(env, pasta)
        self._teclar(env, "key", "alt+o")

    def _preencher_login(self, env, janela):
        self._teclar(env, "windowactivate", "--sync", janela)
        for atalho, valor in (("alt+u", self.user), ("alt+p", self.password), ("alt+s", self.host)):
            if valor:
                self._teclar(env, "key", atalho)
                self._digitar(env, valor)
        if self.user and self.password:
            self._teclar(env, "key", "alt+l")

    def stop(self):
        super().stop()
        with self._limpo_lock:
            if self._limpo:
                return
            self._limpo = True
        if self._home:
            shutil.rmtree(self._home, ignore_errors=True)
