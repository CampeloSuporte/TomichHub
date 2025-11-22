import json
import pexpect
import telnetlib
import threading
import ipaddress
import logging
import time
import os
from channels.generic.websocket import WebsocketConsumer
from .models import Acesso, ProxyServer

logger = logging.getLogger(__name__)


class SSHConsumer(WebsocketConsumer):
    def connect(self):
        self.accept()
        self.limpar_recursos()
        
    def limpar_recursos(self):
        """✅ LIMPEZA COMPLETA E SEGURA DE RECURSOS"""
        # ✅ Parar thread PRIMEIRO
        self.is_reading = False
        if hasattr(self, 'read_thread') and self.read_thread and self.read_thread.is_alive():
            time.sleep(0.2)
            try:
                # Dar tempo para thread terminar
                self.read_thread.join(timeout=1.0)
            except:
                pass
        
        # ✅ Fechar socket primeiro (para evitar segfault)
        if hasattr(self, 'ssh_process') and self.ssh_process:
            try:
                self.ssh_process.close()
            except:
                pass
            self.ssh_process = None
        
        # ✅ Fechar telnet
        if hasattr(self, 'telnet_client') and self.telnet_client:
            try:
                self.telnet_client.close()
            except:
                pass
            self.telnet_client = None
        
        # ✅ Fechar túnel
        if hasattr(self, 'tunnel_process') and self.tunnel_process:
            try:
                self.tunnel_process.close()
            except:
                pass
            self.tunnel_process = None
        
        # ✅ Reinicializar variáveis
        self.ssh_process = None
        self.telnet_client = None
        self.tunnel_process = None
        self.protocol = None
        self.read_thread = None
        self.is_reading = False
        self.is_huawei = False
        self.acessoId = None
        
    def disconnect(self, close_code):
        """Fecha tudo ao desconectar"""
        logger.info("🔌 WebSocket desconectando...")
        self.limpar_recursos()
        logger.info("✅ Limpeza concluída")
    
    def receive(self, text_data):
        """Receber e processar comandos do frontend"""
        try:
            data = json.loads(text_data)
            action = data.get('action')
            
            if action == 'connect':
                acesso_id = data.get('acesso_id')
                logger.info(f"📋 Conectar acesso {acesso_id}")
                
                # ✅ Limpar recursos antes de nova conexão
                self.limpar_recursos()
                time.sleep(0.1)
                
                self.conectar_acesso(acesso_id)
            
            elif action == 'command':
                command = data.get('command', '')
                self.enviar_comando(command)
        
        except json.JSONDecodeError as e:
            logger.error(f"❌ Erro ao parsear JSON: {str(e)}")
            self.send_error(f"Erro ao parsear JSON: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Erro na função receive: {str(e)}")
            self.send_error(f"Erro: {str(e)}")
    
    def conectar_acesso(self, acesso_id):
        """Conectar a um acesso específico"""
        try:
            self.acessoId = acesso_id
            acesso = Acesso.objects.get(id=acesso_id)
            protocol = self.detect_protocol(acesso.porta)
            self.protocol = protocol
            
            self.is_huawei = acesso.equipamento and 'huawei' in acesso.equipamento.lower() if hasattr(acesso, 'equipamento') else False
            
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
        """Enviar comando"""
        try:
            if self.protocol == 'ssh':
                if self.ssh_process:
                    logger.debug(f"📤 ENVIANDO: {repr(command[:50])}")
                    self.ssh_process.send(command)
            
            elif self.protocol == 'telnet':
                if self.telnet_client:
                    self.telnet_client.write(command.encode('utf-8'))
        
        except Exception as e:
            logger.error(f"❌ Erro ao enviar: {str(e)}")
            self.send_error(f'Erro ao enviar comando: {str(e)}')
    
    def is_private_ip(self, host):
        """Verifica se o host é um IP privado"""
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private
        except ValueError:
            return False
    
    def detect_protocol(self, porta):
        """Detectar protocolo baseado na porta"""
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
        """Retorna um proxy ativo do cliente"""
        try:
            proxy = ProxyServer.objects.filter(
                cliente=cliente,
                ativo=True
            ).first()
            
            if not proxy:
                raise Exception(f"Nenhum proxy SSH ativo para {cliente.nome_empresa}")
            return proxy
        except Exception as e:
            raise Exception(f"Erro ao buscar proxy: {str(e)}")
    
    def connect_ssh(self, acesso):
        """Conexão SSH direta"""
        try:
            logger.info(f"🔗 SSH: {acesso.host}:{acesso.porta}")
            
            terminal_type = "vt100" if self.is_huawei else "xterm-256color"
            
            ssh_cmd = (
                f"ssh -o StrictHostKeyChecking=no "
                f"-o UserKnownHostsFile=/dev/null "
                f"-o ConnectTimeout=10 "
                f"-o ServerAliveInterval=60 "
                f"-o LogLevel=ERROR "
                f"-o HostKeyAlgorithms=+ssh-rsa "  # ✅ ADICIONADO
                f"-o PubkeyAcceptedAlgorithms=+ssh-rsa "  # ✅ ADICIONADO
            )
            
            if self.is_huawei:
                ssh_cmd += (
                    f"-o KexAlgorithms=+diffie-hellman-group1-sha1 "
                    f"-o HostKeyAlgorithms=+ssh-rsa "
                )
            
            ssh_cmd += f"-p {acesso.porta} {acesso.usuario}@{acesso.host}"
            
            logger.info(f"🖥️ Terminal type: {terminal_type}")
            
            env = os.environ.copy()
            env['TERM'] = terminal_type
            
            self.ssh_process = pexpect.spawn(
                ssh_cmd,
                timeout=15,
                encoding=None,
                maxread=262144,
                env=env
            )
            
            index = self.ssh_process.expect([
                pexpect.TIMEOUT,
                b"password:",
                b"Password:",
                b"Are you sure",
                pexpect.EOF
            ], timeout=10)
            
            if index == 2 or index == 1:
                logger.info(f"📤 Enviando senha")
                self.ssh_process.sendline(acesso.senha)
                
                index = self.ssh_process.expect([
                    pexpect.TIMEOUT,
                    b"Permission denied",
                    rb".*[#>$\]].*",
                    pexpect.EOF
                ], timeout=10)
                
                if index == 1:
                    raise Exception("Erro de autenticação SSH")
                elif index != 2:
                    raise Exception("Timeout ao conectar")
            
            elif index == 3:
                self.ssh_process.sendline('yes')
                self.ssh_process.expect(b"password:", timeout=10)
                self.ssh_process.sendline(acesso.senha)
                self.ssh_process.expect([rb".*[#>$\]].*", pexpect.EOF], timeout=10)
            
            logger.info(f"✅ SSH: Conectado")
            
            if self.is_huawei:
                try:
                    logger.info("⚙️ Desabilitando paginação Huawei...")
                    self.ssh_process.send("screen-length 0 temporary\r")
                    time.sleep(0.5)
                    try:
                        self.ssh_process.read_nonblocking(timeout=0.5, size=32768)
                    except:
                        pass
                except Exception as e:
                    logger.warning(f"⚠️ Não foi possível desabilitar paginação: {e}")
            
            self.send_json({
                'type': 'connected',
                'message': f'✓ Conectado SSH a {acesso.host}:{acesso.porta}' + (" [HUAWEI]" if self.is_huawei else "")
            })
            
            # ✅ Iniciar thread de leitura
            self.is_reading = True
            self.read_thread = threading.Thread(target=self.read_ssh_output, daemon=True)
            self.read_thread.start()
        
        except Exception as e:
            logger.error(f"❌ SSH: {str(e)}")
            self.send_error(f'Erro SSH: {str(e)}')
    
    def connect_ssh_via_proxy(self, acesso):
        """Conexão SSH via proxy"""
        try:
            proxy = self.get_active_proxy(acesso.cliente)
            logger.info(f"🔗 SSH via proxy: {proxy.nome}")
            
            self.send_json({
                'type': 'info',
                'message': f'⚡ SSH via proxy {proxy.nome}...'
            })
            
            local_port = self.find_available_port()
            
            tunnel_cmd = (
                f"ssh -N -L {local_port}:{acesso.host}:{acesso.porta} "
                f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
                f"-o ConnectTimeout=10 -o LogLevel=ERROR "
                f"-p {proxy.porta} {proxy.usuario}@{proxy.host}"
                f"-o HostKeyAlgorithms=+ssh-rsa "  # ✅ ADICIONADO
                f"-o PubkeyAcceptedAlgorithms=+ssh-rsa "  # ✅ ADICIONADO
            )
            
            self.tunnel_process = pexpect.spawn(tunnel_cmd, timeout=15, encoding=None)
            
            index = self.tunnel_process.expect([
                b"password:",
                b"Password:",
                pexpect.TIMEOUT,
                pexpect.EOF
            ], timeout=10)
            
            if index == 0 or index == 1:
                self.tunnel_process.sendline(proxy.senha)
                time.sleep(2)
            
            logger.info(f"✅ Túnel criado")
            
            terminal_type = "vt100" if self.is_huawei else "xterm-256color"
            
            ssh_cmd = (
                f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
                f"-o ConnectTimeout=10 -o ServerAliveInterval=60 -o LogLevel=ERROR "
            )
            
            if self.is_huawei:
                ssh_cmd += (
                    f"-o KexAlgorithms=+diffie-hellman-group1-sha1 "
                    f"-o HostKeyAlgorithms=+ssh-rsa "
                )
            
            ssh_cmd += f"-p {local_port} {acesso.usuario}@127.0.0.1"
            
            env = os.environ.copy()
            env['TERM'] = terminal_type
            
            self.ssh_process = pexpect.spawn(ssh_cmd, timeout=15, encoding=None, maxread=262144, env=env)
            
            index = self.ssh_process.expect([
                pexpect.TIMEOUT,
                b"password:",
                b"Password:",
                pexpect.EOF
            ], timeout=10)
            
            if index != 1 and index != 2:
                raise Exception("Timeout ao conectar via proxy")
            
            logger.info(f"📤 Enviando senha")
            self.ssh_process.sendline(acesso.senha)
            
            index = self.ssh_process.expect([
                pexpect.TIMEOUT,
                b"Permission denied",
                rb".*[#>$\]].*",
                pexpect.EOF
            ], timeout=10)
            
            if index != 2:
                raise Exception("Erro de autenticação via proxy")
            
            logger.info(f"✅ SSH via proxy: Conectado")
            
            if self.is_huawei:
                try:
                    logger.info("⚙️ Desabilitando paginação Huawei...")
                    self.ssh_process.send("screen-length 0 temporary\r")
                    time.sleep(0.5)
                    try:
                        self.ssh_process.read_nonblocking(timeout=0.5, size=32768)
                    except:
                        pass
                except Exception as e:
                    logger.warning(f"⚠️ Não foi possível desabilitar paginação: {e}")
            
            self.send_json({
                'type': 'connected',
                'message': f'✓ SSH a {acesso.host}:{acesso.porta} via {proxy.nome}' + (" [HUAWEI]" if self.is_huawei else "")
            })
            
            # ✅ Iniciar thread de leitura
            self.is_reading = True
            self.read_thread = threading.Thread(target=self.read_ssh_output, daemon=True)
            self.read_thread.start()
        
        except Exception as e:
            logger.error(f"❌ SSH via proxy: {str(e)}")
            self.send_error(f'Erro SSH via proxy: {str(e)}')
            
            if self.tunnel_process:
                try:
                    self.tunnel_process.close()
                except:
                    pass
    
    def connect_telnet(self, acesso):
        """Conexão Telnet direta"""
        try:
            logger.info(f"🔗 Telnet: {acesso.host}:{acesso.porta}")
            
            self.telnet_client = telnetlib.Telnet(acesso.host, int(acesso.porta), timeout=10)
            
            logger.info(f"✅ Telnet: Conectado")
            
            self.send_json({
                'type': 'info',
                'message': f'Conectando Telnet a {acesso.host}:{acesso.porta}...'
            })
            
            self.authenticate_telnet(acesso.usuario, acesso.senha)
            
            self.send_json({
                'type': 'connected',
                'message': f'✓ Conectado Telnet a {acesso.host}:{acesso.porta}'
            })
            
            # ✅ Iniciar thread de leitura
            self.is_reading = True
            self.read_thread = threading.Thread(target=self.read_telnet_output, daemon=True)
            self.read_thread.start()
        
        except Exception as e:
            logger.error(f"❌ Telnet: {str(e)}")
            self.send_error(f'Erro Telnet: {str(e)}')
    
    def authenticate_telnet(self, username, password):
        """Autenticar em Telnet"""
        try:
            logger.info(f"🔐 Telnet: Autenticando")
            
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
            
            logger.info(f"📤 Enviando username")
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
            
            logger.info("📤 Enviando senha")
            self.telnet_client.write(password.encode('utf-8') + b'\n')
            time.sleep(1)
            
            output = self.telnet_client.read_very_eager()
            if output:
                self.send_json({'type': 'output', 'data': output.decode('utf-8', errors='ignore')})
            
            logger.info(f"✅ Telnet: Autenticado")
        
        except Exception as e:
            logger.error(f"❌ Telnet autenticação: {str(e)}")
            self.send_error(f'Erro na autenticação Telnet: {str(e)}')
            raise
    
    def connect_telnet_via_proxy(self, acesso):
        """Conexão Telnet via proxy SSH"""
        try:
            proxy = self.get_active_proxy(acesso.cliente)
            logger.info(f"🔗 Telnet via proxy: {proxy.nome}")
            
            self.send_json({
                'type': 'info',
                'message': f'⚡ Telnet via proxy {proxy.nome}...'
            })
            
            local_port = self.find_available_port()
            
            tunnel_cmd = (
                f"ssh -N -L {local_port}:{acesso.host}:{acesso.porta} "
                f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
                f"-o ConnectTimeout=10 -o LogLevel=ERROR "
                f"-p {proxy.porta} {proxy.usuario}@{proxy.host}"
            )
            
            self.tunnel_process = pexpect.spawn(tunnel_cmd, timeout=15, encoding=None)
            
            index = self.tunnel_process.expect([
                b"password:",
                b"Password:",
                pexpect.TIMEOUT,
                pexpect.EOF
            ], timeout=10)
            
            if index == 0 or index == 1:
                self.tunnel_process.sendline(proxy.senha)
                time.sleep(2)
            
            logger.info(f"✅ Túnel Telnet criado")
            
            self.telnet_client = telnetlib.Telnet('127.0.0.1', local_port, timeout=10)
            
            self.send_json({'type': 'info', 'message': f'Autenticando Telnet...'})
            
            self.authenticate_telnet(acesso.usuario, acesso.senha)
            
            self.send_json({
                'type': 'connected',
                'message': f'✓ Telnet a {acesso.host}:{acesso.porta} via {proxy.nome}'
            })
            
            # ✅ Iniciar thread de leitura
            self.is_reading = True
            self.read_thread = threading.Thread(target=self.read_telnet_output, daemon=True)
            self.read_thread.start()
        
        except Exception as e:
            logger.error(f"❌ Telnet via proxy: {str(e)}")
            self.send_error(f'Erro Telnet via proxy: {str(e)}')
            
            if self.tunnel_process:
                try:
                    self.tunnel_process.close()
                except:
                    pass
    
    def read_ssh_output(self):
        """✅ Leitura SSH com segurança"""
        try:
            logger.info("📖 Thread SSH iniciada")
            
            while self.is_reading and self.ssh_process:
                try:
                    output = self.ssh_process.read_nonblocking(timeout=0.1, size=262144)
                    
                    if output and len(output) > 0:
                        try:
                            texto = output.decode('utf-8', errors='replace')
                        except:
                            texto = output.decode('latin1', errors='replace')
                        
                        self.send_json({
                            'type': 'output',
                            'data': texto
                        })
                    else:
                        time.sleep(0.001)
                
                except pexpect.exceptions.TIMEOUT:
                    continue
                except pexpect.exceptions.EOF:
                    logger.info("🔌 SSH: EOF recebido")
                    break
                except Exception as e:
                    logger.error(f"❌ Erro leitura SSH: {str(e)}")
                    break
        
        except Exception as e:
            logger.error(f"❌ Erro thread SSH: {str(e)}")
        finally:
            logger.info("🛑 Thread SSH finalizada")
            self.is_reading = False
    
    def read_telnet_output(self):
        """Leitura Telnet com segurança"""
        try:
            logger.info("📖 Thread Telnet iniciada")
            
            while self.is_reading and self.telnet_client:
                try:
                    output = self.telnet_client.read_very_eager()
                    
                    if output:
                        self.send_json({
                            'type': 'output',
                            'data': output.decode('utf-8', errors='ignore')
                        })
                    else:
                        time.sleep(0.001)
                
                except EOFError:
                    logger.info("🔌 Telnet: Conexão encerrada")
                    break
        
        except Exception as e:
            logger.error(f"❌ Erro thread Telnet: {str(e)}")
        finally:
            logger.info("🛑 Thread Telnet finalizada")
            self.is_reading = False
    
    def find_available_port(self, start=9000, max_attempts=100):
        """Encontra uma porta disponível"""
        import socket
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
        """Enviar JSON para o frontend"""
        try:
            self.send(text_data=json.dumps(data))
        except Exception as e:
            logger.error(f"❌ Erro send_json: {str(e)}")
    
    def send_error(self, message):
        """Enviar erro para o frontend"""
        self.send_json({
            'type': 'error',
            'message': message
        })