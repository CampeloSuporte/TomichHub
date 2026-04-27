import json
import pexpect
import telnetlib
import threading
import ipaddress
import logging
import time
import os
import socket
import paramiko
from channels.generic.websocket import WebsocketConsumer
from .models import Acesso, ProxyServer

logger = logging.getLogger(__name__)


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

        for attr in ('ssh_process', 'telnet_client', 'tunnel_process', '_paramiko_client', '_tunnel_server'):
            obj = getattr(self, attr, None)
            if obj:
                try:
                    obj.close()
                except:
                    pass
            setattr(self, attr, None)  # ← garante None após fechar

        self.protocol         = None
        self.read_thread      = None
        self.is_reading       = False
        self.is_huawei        = False
        self.acessoId         = None

    def disconnect(self, close_code):
        logger.info("🔌 WebSocket desconectando...")
        self.limpar_recursos()
        logger.info("✅ Limpeza concluída")

    def receive(self, text_data):
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

            if self.is_private_ip(acesso.host):
                if protocol == 'ssh':
                    self.connect_ssh_via_proxy(acesso)
                else:
                    self.connect_telnet_via_proxy(acesso)
            else:
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
        """
        try:
            command = command.replace('\x7f', '\x08')

            if self.protocol == 'ssh':
                if self.ssh_process:
                    self.ssh_process.send(command)
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
    # SSH via proxy
    # =========================================================

    def connect_ssh_via_proxy(self, acesso):
        tempo_inicio = time.time()
        try:
            proxy = self.get_active_proxy(acesso.cliente)
            logger.info(f"🔗 SSH via proxy (paramiko): {proxy.nome}")

            self.send_json({'type': 'info', 'message': f'⚡ Conectando via proxy {proxy.nome}...'})

            local_port = self._criar_tunel_paramiko(proxy, acesso.host, int(acesso.porta))

            elapsed = time.time() - tempo_inicio
            logger.info(f"⏱️  Túnel pronto em {elapsed:.1f}s — localhost:{local_port} → {acesso.host}:{acesso.porta}")

            terminal_type = "vt100" if self.is_huawei else "xterm-256color"
            ssh_cmd       = self._build_ssh_cmd(acesso.usuario, '127.0.0.1', local_port)

            env        = os.environ.copy()
            env['TERM'] = terminal_type

            self.ssh_process = pexpect.spawn(
                ssh_cmd, timeout=15, encoding=None, maxread=262144, env=env
            )

            self._authenticate_ssh_process(self.ssh_process, acesso.senha)

            if self.is_huawei:
                self._disable_huawei_paging()

            tempo_total = time.time() - tempo_inicio
            self.send_json({
                'type':    'connected',
                'message': (
                    f'✓ SSH a {acesso.host}:{acesso.porta} via {proxy.nome} ({tempo_total:.1f}s)'
                    + (" [HUAWEI]" if self.is_huawei else "")
                )
            })

            self.is_reading  = True
            self.read_thread = threading.Thread(target=self.read_ssh_output, daemon=True)
            self.read_thread.start()

        except Exception as e:
            tempo_total = time.time() - tempo_inicio
            logger.error(f"❌ SSH via proxy falhou em {tempo_total:.1f}s: {str(e)}")
            self.send_error(f'Erro SSH via proxy: {str(e)}')
            self.limpar_recursos()

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
            f"-o PreferredAuthentications=password "
            f"-o ConnectTimeout=10 "
            f"-o ServerAliveInterval=60 "
            f"-o ServerAliveCountMax=3 "
            f"-o LogLevel=ERROR "
            f"-o NumberOfPasswordPrompts=1 "
            f"-o KexAlgorithms=+diffie-hellman-group-exchange-sha1,diffie-hellman-group14-sha1,diffie-hellman-group1-sha1 "
            f"-o HostKeyAlgorithms=+ssh-rsa,ssh-dss "
            f"-o PubkeyAcceptedAlgorithms=+ssh-rsa "
            f"-o Ciphers=+aes128-cbc,aes192-cbc,aes256-cbc,3des-cbc "
            f"-o MACs=+hmac-sha1,hmac-sha2-256,hmac-sha2-512 "
            f"-p {porta} {usuario}@{host}"
        )

    def _authenticate_ssh_process(self, process, senha):
        index = process.expect([
            b"password:",
            b"Password:",
            b"Are you sure",
            rb".*[#>$\]].*",
            pexpect.TIMEOUT,
            pexpect.EOF
        ], timeout=12)

        if index == 2:  # fingerprint
            process.sendline(b"yes")
            index = process.expect([b"password:", b"Password:", pexpect.TIMEOUT], timeout=10)

        if index == 3:
            time.sleep(0.2)
            process.send('\r')
            return

        if index in (0, 1):
            process.sendline(senha)
            result = process.expect([
                b"Permission denied",
                b"Access denied",
                b"Authentication failed",
                b"Login incorrect",
                rb".*[#>$\]].*",
                pexpect.TIMEOUT,
                pexpect.EOF
            ], timeout=12)

            if result in [0, 1, 2, 3]:
                raise Exception("Senha incorreta ou acesso negado no equipamento")
            if result == 5:
                raise Exception("Timeout ao autenticar no equipamento")
            if result == 6:
                raise Exception("Equipamento encerrou conexão")

            time.sleep(0.2)
            process.send('\r')
            return

        if index == 4:
            raise Exception("Timeout ao conectar ao equipamento")
        if index == 5:
            raise Exception("Equipamento encerrou conexão")

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

    # Limiar em bytes: abaixo disso, envia imediatamente sem esperar mais dados.
    # 512 bytes cobre qualquer eco de tecla, sequência de escape de seta,
    # e prompts curtos — garante resposta <1ms para interações do usuário.
    FLUSH_IMMEDIATE = 512

    # Após acumular dados, aguarda no máximo este tempo (segundos) por mais bytes
    # antes de fazer flush forçado. Evita que outputs grandes (show run, etc.)
    # fiquem presos no buffer. 0.02s = 20ms, imperceptível ao usuário.
    FLUSH_TIMEOUT = 0.02

    def read_ssh_output(self):
        """
        Loop de leitura SSH sem select().

        Usa apenas read_nonblocking do pexpect, que acessa o buffer interno
        corretamente e nunca causa race condition com bytes do fd raw.

        Fluxo:
        1. Tenta ler até 65536 bytes com timeout de 50ms.
        2. Se recebeu dados:
           a. Payload pequeno (<=512 bytes): envia imediatamente — eco de tecla fluido.
           b. Payload grande: acumula e re-lê com timeout bem curto (20ms)
              para capturar a rajada inteira em um único envio WebSocket.
        3. Se não recebeu nada (TIMEOUT): continua o loop — sem flush, sem CPU waste.
        4. EOF/OSError: conexão encerrada, sai do loop.
        """
        try:
            logger.info("📖 Thread SSH iniciada")
            buf = bytearray()

            while self.is_reading and self.ssh_process:
                try:
                    chunk = self.ssh_process.read_nonblocking(size=65536, timeout=0.05)

                    if not chunk:
                        continue

                    buf += chunk

                    # Payload pequeno → flush imediato (eco de tecla, prompt, seta)
                    if len(buf) <= self.FLUSH_IMMEDIATE:
                        self.send_json({
                            'type': 'output',
                            'data': buf.decode('utf-8', errors='replace')
                        })
                        buf.clear()
                        continue

                    # Payload grande → tenta acumular a rajada inteira
                    deadline = time.monotonic() + self.FLUSH_TIMEOUT
                    while time.monotonic() < deadline:
                        try:
                            more = self.ssh_process.read_nonblocking(size=65536, timeout=0.005)
                            if more:
                                buf += more
                        except pexpect.exceptions.TIMEOUT:
                            break
                        except Exception:
                            break

                    self.send_json({
                        'type': 'output',
                        'data': buf.decode('utf-8', errors='replace')
                    })
                    buf.clear()

                except pexpect.exceptions.TIMEOUT:
                    # Nenhum dado disponível no momento — loop normalmente
                    if buf:
                        self.send_json({
                            'type': 'output',
                            'data': buf.decode('utf-8', errors='replace')
                        })
                        buf.clear()
                    continue

                except (pexpect.exceptions.EOF, OSError) as e:
                    logger.info(f"🔌 SSH: conexão encerrada ({type(e).__name__})")
                    if buf:
                        self.send_json({
                            'type': 'output',
                            'data': buf.decode('utf-8', errors='replace')
                        })
                    break

                except Exception as e:
                    err = str(e)
                    if any(k in err for k in ('EIO', 'EOF', 'closed', 'fd')):
                        logger.info(f"🔌 SSH fd fechado: {err}")
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
        Loop de leitura Telnet com flush imediato para payloads pequenos.
        """
        try:
            logger.info("📖 Thread Telnet iniciada")
            buf = bytearray()

            while self.is_reading and self.telnet_client:
                try:
                    # read_very_eager é não-bloqueante; dormimos um pouco
                    # só quando não há nada para ler
                    chunk = self.telnet_client.read_very_eager()

                    if not chunk:
                        if buf:
                            self.send_json({
                                'type': 'output',
                                'data': buf.decode('utf-8', errors='ignore')
                            })
                            buf.clear()
                        time.sleep(0.005)
                        continue

                    buf += chunk

                    if len(buf) <= self.FLUSH_IMMEDIATE:
                        self.send_json({
                            'type': 'output',
                            'data': buf.decode('utf-8', errors='ignore')
                        })
                        buf.clear()
                        continue

                    # Rajada grande: acumula por até FLUSH_TIMEOUT
                    deadline = time.monotonic() + self.FLUSH_TIMEOUT
                    while time.monotonic() < deadline:
                        more = self.telnet_client.read_very_eager()
                        if more:
                            buf += more
                        else:
                            break

                    self.send_json({
                        'type': 'output',
                        'data': buf.decode('utf-8', errors='ignore')
                    })
                    buf.clear()

                except EOFError:
                    logger.info("🔌 Telnet: conexão encerrada")
                    if buf:
                        self.send_json({
                            'type': 'output',
                            'data': buf.decode('utf-8', errors='ignore')
                        })
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

    def send_json(self, data):
        try:
            self.send(text_data=json.dumps(data))
        except Exception as e:
            logger.error(f"❌ Erro send_json: {str(e)}")

    def send_error(self, message):
        self.send_json({'type': 'error', 'message': message})