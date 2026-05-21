import json
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
from channels.generic.websocket import WebsocketConsumer
from .models import Acesso, ProxyServer

logger = logging.getLogger(__name__)


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
        """Retorna conexão ativa existente ou None."""
        key = self._key(proxy)
        with self._lock:
            client = self._conns.get(key)
            if client is None:
                return None
            try:
                t = client.get_transport()
                if t and t.is_active():
                    return client
            except Exception:
                pass
            del self._conns[key]
            return None

    def put(self, proxy, client):
        """Armazena conexão no pool."""
        key = self._key(proxy)
        with self._lock:
            self._conns[key] = client

    def remove(self, proxy):
        key = self._key(proxy)
        with self._lock:
            self._conns.pop(key, None)

_proxy_pool = _ProxyPool()


class SSHConsumer(WebsocketConsumer):
    def connect(self):
        self.accept()
        self.limpar_recursos()

    def limpar_recursos(self):
        self.is_reading = False
        if hasattr(self, 'read_thread') and self.read_thread and self.read_thread.is_alive():
            time.sleep(0.2)
            try:
                self.read_thread.join(timeout=1.0)
            except:
                pass

        for attr in ('ssh_process', 'telnet_client', 'tunnel_process',
                     '_paramiko_shell', '_paramiko_dest_transport',
                     '_paramiko_client', '_tunnel_server'):
            obj = getattr(self, attr, None)
            if obj:
                try:
                    obj.close()
                except:
                    pass
            setattr(self, attr, None)

        self.protocol         = None
        self.read_thread      = None
        self.is_reading       = False
        self.is_huawei        = False
        self.acessoId         = None

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
                time.sleep(0.1)
                self.conectar_acesso(acesso_id)

            elif action == 'command':
                command = data.get('command', '')
                self.enviar_comando(command)

        except json.JSONDecodeError as e:
            self.send_error(f"Erro ao parsear JSON: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Erro na função receive: {str(e)}")
            self.send_error(f"Erro: {str(e)}")

    def _vpn_cobre_ip(self, cliente, host):
        """Verifica se existe VPN WireGuard com peer configurado que cobre o IP do host."""
        try:
            import ipaddress as _ipa
            from .models import VPNWireGuard

            vpns = VPNWireGuard.objects.filter(cliente=cliente, ativo=True, peer_no_servidor=True)
            host_ip = _ipa.ip_address(host)

            for vpn in vpns:
                for rede_str in vpn.redes_lista():
                    try:
                        if host_ip in _ipa.ip_network(rede_str, strict=False):
                            logger.info(f"✅ VPN WG cobre {host} via {vpn.nome}")
                            return True
                    except ValueError:
                        pass
        except Exception as e:
            logger.warning(f"⚠️ Erro ao verificar VPN: {e}")
        return False

    def conectar_acesso(self, acesso_id):
        try:
            self.acessoId = acesso_id
            acesso        = Acesso.objects.get(id=acesso_id)
            protocol      = self.detect_protocol(acesso.porta)
            self.protocol = protocol

            self.is_huawei = (
                acesso.equipamento and 'huawei' in acesso.equipamento.lower()
                if hasattr(acesso, 'equipamento') else False
            )

            logger.info(f"🔗 Protocolo: {protocol.upper()} | Huawei: {self.is_huawei}")

            is_private = self.is_private_ip(acesso.host)
            is_cgnat   = getattr(acesso, 'tipo', '') == 'CGNAT'

            if is_private:
                # IP privado: VPN WireGuard ou proxy
                if self._vpn_cobre_ip(acesso.cliente, acesso.host):
                    self.send_json({'type': 'info', 'message': '🔒 Conectando via VPN WireGuard...'})
                    if protocol == 'ssh':
                        self.connect_ssh(acesso)
                    else:
                        self.connect_telnet(acesso)
                else:
                    if protocol == 'ssh':
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
        """
        try:
            command = command.replace('\x7f', '\x08')

            if self.protocol == 'ssh':
                if getattr(self, '_paramiko_shell', None):
                    self._paramiko_shell.send(command.encode('utf-8'))
                elif self.ssh_process:
                    os.write(self.ssh_process.child_fd, command.encode('utf-8'))
            elif self.protocol == 'telnet':
                if self.telnet_client:
                    self.telnet_client.write(command.encode('utf-8'))
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

        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(
            hostname=proxy.host,
            port=int(proxy.porta),
            username=proxy.usuario,
            password=proxy.senha,
            timeout=timeout_conn,
            look_for_keys=False,
            allow_agent=False,
            banner_timeout=timeout_conn,
        )
        logger.info("✅ [PARAMIKO] Proxy conectado!")
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

        # Testar canal antes de retornar
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
            raise Exception(
                f"Proxy conectou mas não conseguiu abrir canal para "
                f"{host_destino}:{porta_destino} — {e}"
            )

        return local_port

    # =========================================================
    # SSH direto
    # =========================================================

    def connect_ssh(self, acesso):
        try:
            logger.info(f"🔗 SSH: {acesso.host}:{acesso.porta}")

            terminal_type = "vt100" if self.is_huawei else "xterm-256color"
            ssh_cmd       = self._build_ssh_cmd(acesso.usuario, acesso.host, acesso.porta)

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
            logger.error(f"❌ SSH: {str(e)}")
            self.send_error(f'Erro SSH: {str(e)}')

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
                    banner_timeout=10,
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
                    banner_timeout=10,
                )
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
            dest_transport.start_client(timeout=10)
            self._paramiko_dest_transport = dest_transport

            # 4. Autenticar no equipamento
            try:
                dest_transport.auth_password(acesso.usuario, acesso.senha)
            except paramiko.AuthenticationException:
                raise Exception("Senha incorreta ou acesso negado no equipamento")
            if not dest_transport.is_authenticated():
                raise Exception("Autenticação falhou no equipamento")

            # 5. Abrir shell interativo
            shell = dest_transport.open_session()
            terminal_type = "vt100" if self.is_huawei else "xterm-256color"
            shell.get_pty(term=terminal_type, width=220, height=50)
            shell.invoke_shell()
            shell.settimeout(0.005)
            self._paramiko_shell = shell

            if self.is_huawei:
                time.sleep(0.5)
                shell.send("screen-length 0 temporary\r")

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

    def _read_paramiko_shell(self):
        """Loop de leitura para conexão SSH via paramiko (proxy) — non-blocking, baixa latência."""
        shell = self._paramiko_shell
        buf   = bytearray()
        logger.info("📖 Thread paramiko shell iniciada")
        try:
            shell.setblocking(False)
            while self.is_reading and shell and not shell.closed:
                if not shell.recv_ready():
                    if buf:
                        self.send_output(buf.decode('utf-8', errors='replace'))
                        buf.clear()
                    time.sleep(0.0005)   # 0.5ms idle — CPU mínimo, latência mínima
                    continue

                # Drena tudo disponível no kernel buffer agora
                while shell.recv_ready():
                    try:
                        chunk = shell.recv(65536)
                    except Exception:
                        chunk = b''
                    if not chunk:
                        break
                    buf += chunk

                if buf:
                    self.send_output(buf.decode('utf-8', errors='replace'))
                    buf.clear()

        except Exception as e:
            logger.error(f"❌ Erro thread paramiko: {e}")
        finally:
            logger.info("🛑 Thread paramiko shell finalizada")
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

    def _build_ssh_cmd(self, usuario, host, porta):
        return (
            f"ssh -o StrictHostKeyChecking=no "
            f"-o UserKnownHostsFile=/dev/null "
            f"-o IdentitiesOnly=yes "
            f"-o PubkeyAuthentication=no "
            f"-o PreferredAuthentications=password,keyboard-interactive "
            f"-o ConnectTimeout=10 "
            f"-o ServerAliveInterval=60 "
            f"-o ServerAliveCountMax=3 "
            f"-o LogLevel=ERROR "
            f"-o NumberOfPasswordPrompts=3 "
            f"-o KexAlgorithms=+diffie-hellman-group-exchange-sha1,diffie-hellman-group14-sha1,diffie-hellman-group1-sha1 "
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
        try:
            self.ssh_process.send("screen-length 0 temporary\r")
            time.sleep(0.5)
            try:
                self.ssh_process.read_nonblocking(timeout=0.5, size=32768)
            except:
                pass
        except Exception as e:
            logger.warning(f"⚠️ Não foi possível desabilitar paginação: {e}")

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
                    # Bloqueia no SO até dado chegar — timeout 1ms evita spin
                    r, _, _ = select.select([fd], [], [], 0.001)
                    if not r:
                        if buf:
                            self.send_output(buf.decode('utf-8', errors='replace'))
                            buf.clear()
                        continue

                    data = os.read(fd, 65536)
                    if not data:
                        break
                    buf += data

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
                    buf.clear()

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
        """Envia output do terminal — bytes puros, sem JSON overhead."""
        try:
            self.send(bytes_data=text.encode('utf-8'))
        except Exception:
            pass

    def send_json(self, data):
        try:
            self.send(text_data=json.dumps(data))
        except Exception as e:
            logger.error(f"❌ Erro send_json: {str(e)}")

    def send_error(self, message):
        self.send_json({'type': 'error', 'message': message})


class WinboxConsumer(SSHConsumer):
    def connect(self):
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

            if self.is_private_ip(host):
                proxy = self.get_active_proxy(acesso.cliente)
                self.send_json({'type': 'info', 'message': f'🔌 Conectando Winbox via proxy {proxy.nome}...'})
                local_port = self._criar_tunel_paramiko(proxy, host, porta)
                target_host = '127.0.0.1'
                target_port = local_port
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
            
            acesso = Acesso.objects.get(id=acesso_id)
            host = acesso.host
            
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
                proxy = self.get_active_proxy(acesso.cliente)
                msg_tipo = "Navegador" if mode == 'browser' else "Winbox"
                self.send_json({'type': 'info', 'message': f'🔌 Conectando {msg_tipo} via proxy {proxy.nome}...'})
                local_port = self._criar_tunel_paramiko(proxy, host, porta)
                self.send_json({'type': 'info', 'message': f'✅ Túnel estabelecido na porta {local_port}'})
                target_host = '127.0.0.1'
                target_port = local_port

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

                self.vnc_manager = BrowserVNCManager(url=url)




            else:
                # Inicia Xvfb + WinBox + x11vnc
                self.vnc_manager = WinboxVNCManager(
                    host=target_host,
                    port=target_port,
                    user=acesso.usuario,
                    password=acesso.senha
                )
            
            vnc_port = self.vnc_manager.start()
            
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

class WebSocketProxyConsumer(WebsocketConsumer):

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
                subprotocols=subprotocols or ['binary']
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
    def _ws_handshake(sock, host, port, path_qs, subprotocols=None):
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
    "-o KexAlgorithms=+diffie-hellman-group-exchange-sha1,diffie-hellman-group14-sha1,diffie-hellman-group1-sha1 "
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

        # Executar comando
        proc.sendline(comando.encode())
        proc.expect(_PROMPT_RE, timeout=timeout)

        # Capturar output entre o eco do comando e o próximo prompt
        raw = proc.before or b''
        lines = raw.decode('utf-8', errors='replace').splitlines()
        # Remove a primeira linha (eco do comando) e linhas em branco no início/fim
        if lines and comando in lines[0]:
            lines = lines[1:]
        return '\n'.join(lines).strip()
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
        dest_transport.start_client(timeout=12)
        dest_transport.auth_password(acesso.usuario, acesso.senha)
        if not dest_transport.is_authenticated():
            raise Exception('Autenticação falhou no equipamento destino')

        shell = dest_transport.open_session()
        shell.get_pty(term='vt100' if is_huawei else 'xterm-256color', width=220, height=50)
        shell.invoke_shell()
        shell.settimeout(0.1)
        time.sleep(0.5)

        def _read_until_prompt(sh, wait=8.0) -> bytes:
            buf = bytearray()
            deadline = time.time() + wait
            while time.time() < deadline:
                try:
                    chunk = sh.recv(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if _re.search(_PROMPT_RE, bytes(buf)):
                        break
                except Exception:
                    time.sleep(0.05)
            return bytes(buf)

        _read_until_prompt(shell, 6)  # consumir banner inicial

        if is_huawei:
            shell.send(b'screen-length 0 temporary\r')
            _read_until_prompt(shell, 5)

        shell.send(comando.encode() + b'\r')
        raw = _read_until_prompt(shell, timeout)

        lines = raw.decode('utf-8', errors='replace').splitlines()
        if lines and comando in lines[0]:
            lines = lines[1:]
        # Remove últimas linhas que são só prompt
        while lines and _re.search(r'[>#\$\]]\s*$', lines[-1]):
            lines.pop()
        return '\n'.join(lines).strip()
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

    # Roteamento: IP privado → proxy; CGNAT → tenta direto, fallback proxy; público → direto
    if _is_private(acesso.host):
        proxy = ProxyServer.objects.filter(cliente=acesso.cliente, ativo=True).first()
        if proxy:
            return _paramiko_proxy_exec(proxy, acesso, comando, is_huawei, timeout)
        # sem proxy configurado, tenta direto mesmo sendo privado
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
