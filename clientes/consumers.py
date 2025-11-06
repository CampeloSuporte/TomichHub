import json
import paramiko
import telnetlib
import threading
import ipaddress
import logging
import time
from channels.generic.websocket import WebsocketConsumer
from .models import Acesso, ProxyServer

logger = logging.getLogger(__name__)


class SSHConsumer(WebsocketConsumer):
    def connect(self):
        self.accept()
        self.ssh_client = None
        self.telnet_client = None
        self.channel = None
        self.proxy_client = None
        self.read_thread = None
        self.protocol = None  # 'ssh' ou 'telnet'
        
    def disconnect(self, close_code):
        if self.channel:
            try:
                self.channel.close()
            except:
                pass
        if self.ssh_client:
            try:
                self.ssh_client.close()
            except:
                pass
        if self.telnet_client:
            try:
                self.telnet_client.close()
            except:
                pass
        if self.proxy_client:
            try:
                self.proxy_client.close()
            except:
                pass
    
    def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')
        
        if action == 'connect':
            acesso_id = data.get('acesso_id')
            try:
                acesso = Acesso.objects.get(id=acesso_id)
                
                # ✅ MUDANÇA: Detectar protocolo (SSH ou Telnet)
                protocol = self.detect_protocol(acesso.porta)
                self.protocol = protocol
                
                logger.info(f"🔗 Usando protocolo: {protocol.upper()}")
                
                # Verificar se é IP privado
                if self.is_private_ip(acesso.host):
                    if protocol == 'ssh':
                        self.connect_ssh_via_proxy(
                            acesso=acesso,
                            host=acesso.host,
                            port=int(acesso.porta),
                            username=acesso.usuario,
                            password=acesso.senha
                        )
                    else:  # telnet
                        self.connect_telnet_via_proxy(
                            acesso=acesso,
                            host=acesso.host,
                            port=int(acesso.porta),
                            username=acesso.usuario,
                            password=acesso.senha
                        )
                else:
                    if protocol == 'ssh':
                        self.connect_ssh(
                            host=acesso.host,
                            port=int(acesso.porta),
                            username=acesso.usuario,
                            password=acesso.senha
                        )
                    else:  # telnet
                        self.connect_telnet(
                            host=acesso.host,
                            port=int(acesso.porta),
                            username=acesso.usuario,
                            password=acesso.senha
                        )
            except Acesso.DoesNotExist:
                self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Acesso não encontrado'
                }))
            except Exception as e:
                self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'Erro ao conectar: {str(e)}'
                }))
        
        elif action == 'command':
            command = data.get('command', '')
            
            if self.protocol == 'ssh':
                if self.channel:
                    self.channel.send(command)
            elif self.protocol == 'telnet':
                if self.telnet_client:
                    try:
                        self.telnet_client.write(command.encode('utf-8'))
                    except Exception as e:
                        logger.error(f"Erro ao enviar comando Telnet: {str(e)}")
    
    def is_private_ip(self, host):
        """Verifica se o host é um IP privado"""
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private
        except ValueError:
            return False
    
    def detect_protocol(self, porta):
        """✅ NOVO: Detectar protocolo baseado na porta"""
        porta_int = int(porta)
        
        # Portas comuns
        if porta_int == 22:
            return 'ssh'
        elif porta_int == 23:
            return 'telnet'
        elif porta_int in [2222, 8022, 10022, 9022]:
            return 'ssh'
        elif porta_int in [2323, 9023]:
            return 'telnet'
        else:
            # Default: SSH para portas > 1024, Telnet para < 1024
            return 'telnet' if porta_int < 1024 else 'ssh'
    
    def get_active_proxy(self, cliente):
        """Retorna um proxy ativo do cliente específico"""
        try:
            proxy = ProxyServer.objects.filter(
                cliente=cliente,
                ativo=True
            ).first()
            
            if not proxy:
                raise Exception(f"Nenhum servidor proxy ativo disponível para o cliente {cliente.nome_empresa}")
            return proxy
        except Exception as e:
            raise Exception(f"Erro ao buscar proxy: {str(e)}")
    
    # ============================================================
    # SSH - DIRECT
    # ============================================================
    def connect_ssh(self, host, port, username, password):
        """Conexão SSH direta (IP público)"""
        try:
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            logger.info(f"🔗 SSH: Conectando a {host}:{port}")
            
            self.ssh_client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=10,
                look_for_keys=False,
                allow_agent=False
            )
            
            logger.info(f"✅ SSH: Conectado")
            
            self.channel = self.ssh_client.invoke_shell(
                term='xterm-256color',
                width=120,
                height=40
            )
            
            self.send(text_data=json.dumps({
                'type': 'connected',
                'message': f'✓ Conectado SSH a {host}:{port}'
            }))
            
            self.read_thread = threading.Thread(target=self.read_ssh_output)
            self.read_thread.daemon = True
            self.read_thread.start()
            
        except paramiko.AuthenticationException:
            logger.error(f"❌ SSH: Erro de autenticação")
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Erro de autenticação SSH. Verifique usuário e senha.'
            }))
        except Exception as e:
            logger.error(f"❌ SSH: Erro ao conectar: {str(e)}")
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Erro SSH: {str(e)}'
            }))
    
    # ============================================================
    # TELNET - DIRECT
    # ============================================================
    def connect_telnet(self, host, port, username, password):
        """✅ NOVO: Conexão Telnet direta (IP público)"""
        try:
            logger.info(f"🔗 Telnet: Conectando a {host}:{port}")
            
            self.telnet_client = telnetlib.Telnet(host, port, timeout=10)
            
            logger.info(f"✅ Telnet: Conectado")
            
            self.send(text_data=json.dumps({
                'type': 'info',
                'message': f'Conectando Telnet a {host}:{port}...'
            }))
            
            # ✅ Autenticar Telnet
            self.authenticate_telnet(username, password)
            
            self.send(text_data=json.dumps({
                'type': 'connected',
                'message': f'✓ Conectado Telnet a {host}:{port}'
            }))
            
            # Iniciar thread de leitura
            self.read_thread = threading.Thread(target=self.read_telnet_output)
            self.read_thread.daemon = True
            self.read_thread.start()
            
        except Exception as e:
            logger.error(f"❌ Telnet: Erro ao conectar: {str(e)}")
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Erro Telnet: {str(e)}'
            }))
    
    def authenticate_telnet(self, username, password):
        """✅ CORRIGIDO: Autenticar em Telnet com suporte a diferentes prompts"""
        try:
            import time
            
            logger.info(f"🔐 Telnet: Iniciando autenticação para {username}")
            
            # ✅ PASSO 0: Enviar ENTER inicial (para "Press <RETURN>")
            logger.info("📤 Enviando ENTER inicial...")
            self.telnet_client.write(b'\n')
            time.sleep(0.5)
            
            # Ler qualquer output (banner, etc)
            output = self.telnet_client.read_very_eager()
            if output:
                self.send(text_data=json.dumps({
                    'type': 'output',
                    'data': output.decode('utf-8', errors='ignore')
                }))
                logger.info(f"📨 Banner: {output[:100]}")
            
            # ✅ PASSO 1: Aguardar prompt de login/username
            logger.info("⏳ Aguardando prompt de username/login...")
            output = b''
            timeout_count = 0
            max_timeout = 50  # 5 segundos
            
            while timeout_count < max_timeout:
                try:
                    chunk = self.telnet_client.read_very_eager()
                    if chunk:
                        output += chunk
                        logger.debug(f"📨 Recebido: {chunk[:100]}")
                        
                        # Verificar variações do prompt de login
                        lower_output = output.lower()
                        if any(x in lower_output for x in [b'username', b'login', b'user', b'Login:', b'Username:']):
                            logger.info("✅ Prompt de login encontrado")
                            break
                    else:
                        timeout_count += 1
                        time.sleep(0.1)
                except EOFError:
                    logger.error("❌ Conexão fechada ao aguardar username")
                    raise Exception("Conexão Telnet fechada")
            
            # Enviar prompt ao usuário
            if output:
                self.send(text_data=json.dumps({
                    'type': 'output',
                    'data': output.decode('utf-8', errors='ignore')
                }))
            
            # ✅ PASSO 2: Enviar username
            logger.info(f"📤 Enviando username: {username}")
            self.telnet_client.write(username.encode('utf-8') + b'\n')
            time.sleep(0.5)
            
            # Ler resposta (eco ou resposta do servidor)
            output = self.telnet_client.read_very_eager()
            if output:
                self.send(text_data=json.dumps({
                    'type': 'output',
                    'data': output.decode('utf-8', errors='ignore')
                }))
                logger.debug(f"📨 Após username: {output[:100]}")
            
            # ✅ PASSO 3: Aguardar prompt de senha
            logger.info("⏳ Aguardando prompt de senha...")
            output = b''
            timeout_count = 0
            max_timeout = 50  # 5 segundos
            
            while timeout_count < max_timeout:
                try:
                    chunk = self.telnet_client.read_very_eager()
                    if chunk:
                        output += chunk
                        logger.debug(f"📨 Recebido: {chunk[:100]}")
                        
                        # Procurar por variações do prompt de senha
                        lower_output = output.lower()
                        if any(x in lower_output for x in [b'password', b'password:', b'passwd', b'senha']):
                            logger.info("✅ Prompt de senha encontrado")
                            break
                    else:
                        timeout_count += 1
                        time.sleep(0.1)
                except EOFError:
                    logger.error("❌ Conexão fechada ao aguardar password")
                    raise Exception("Conexão Telnet fechada")
            
            # Enviar prompt de senha ao usuário
            if output:
                self.send(text_data=json.dumps({
                    'type': 'output',
                    'data': output.decode('utf-8', errors='ignore')
                }))
            
            # ✅ PASSO 4: Enviar senha
            logger.info("📤 Enviando senha")
            self.telnet_client.write(password.encode('utf-8') + b'\n')
            time.sleep(0.5)
            
            # Ler resposta
            output = self.telnet_client.read_very_eager()
            if output:
                self.send(text_data=json.dumps({
                    'type': 'output',
                    'data': output.decode('utf-8', errors='ignore')
                }))
                logger.debug(f"📨 Após senha: {output[:100]}")
            
            # ✅ PASSO 5: Aguardar prompt do shell/comando
            logger.info("⏳ Aguardando prompt do shell...")
            time.sleep(1)
            
            output = self.telnet_client.read_very_eager()
            if output:
                self.send(text_data=json.dumps({
                    'type': 'output',
                    'data': output.decode('utf-8', errors='ignore')
                }))
                logger.debug(f"📨 Prompt shell: {output[:100]}")
            
            logger.info(f"✅ Telnet: Autenticado com sucesso como {username}")
            
        except Exception as e:
            logger.error(f"❌ Telnet: Erro na autenticação: {str(e)}")
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Erro na autenticação Telnet: {str(e)}'
            }))
            raise
    
    # ============================================================
    # SSH - VIA PROXY
    # ============================================================
    def connect_ssh_via_proxy(self, acesso, host, port, username, password):
        """Conexão SSH via proxy do cliente"""
        try:
            proxy = self.get_active_proxy(acesso.cliente)
            
            logger.info(f"🔗 SSH via proxy: {proxy.nome}")
            
            self.send(text_data=json.dumps({
                'type': 'info',
                'message': f'⚡ IP privado. SSH via proxy {proxy.nome}...'
            }))
            
            # 1. Conectar ao proxy
            self.proxy_client = paramiko.SSHClient()
            self.proxy_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            self.proxy_client.connect(
                hostname=proxy.host,
                port=proxy.porta,
                username=proxy.usuario,
                password=proxy.senha,
                timeout=10,
                look_for_keys=False,
                allow_agent=False
            )
            
            # 2. Criar tunelamento
            proxy_transport = self.proxy_client.get_transport()
            dest_addr = (host, port)
            local_addr = ('127.0.0.1', 0)
            proxy_channel = proxy_transport.open_channel(
                "direct-tcpip",
                dest_addr,
                local_addr
            )
            
            # 3. Conectar ao host final
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            self.ssh_client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                sock=proxy_channel,
                timeout=10,
                look_for_keys=False,
                allow_agent=False
            )
            
            # 4. Abrir shell
            self.channel = self.ssh_client.invoke_shell(
                term='xterm-256color',
                width=120,
                height=40
            )
            
            self.send(text_data=json.dumps({
                'type': 'connected',
                'message': f'✓ SSH a {host}:{port} via {proxy.nome}'
            }))
            
            # 5. Iniciar leitura
            self.read_thread = threading.Thread(target=self.read_ssh_output)
            self.read_thread.daemon = True
            self.read_thread.start()
            
        except Exception as e:
            logger.error(f"❌ SSH via proxy: {str(e)}")
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Erro SSH via proxy: {str(e)}'
            }))
            
            if self.proxy_client:
                try:
                    self.proxy_client.close()
                except:
                    pass
            if self.ssh_client:
                try:
                    self.ssh_client.close()
                except:
                    pass
    
    # ============================================================
    # TELNET - VIA PROXY
    # ============================================================
    def connect_telnet_via_proxy(self, acesso, host, port, username, password):
        """✅ NOVO: Conexão Telnet via proxy"""
        try:
            proxy = self.get_active_proxy(acesso.cliente)
            
            logger.info(f"🔗 Telnet via proxy: {proxy.nome}")
            
            self.send(text_data=json.dumps({
                'type': 'info',
                'message': f'⚡ IP privado. Telnet via proxy {proxy.nome}...'
            }))
            
            # ✅ Usar SSH como tunel para Telnet
            # 1. Conectar ao proxy via SSH
            self.proxy_client = paramiko.SSHClient()
            self.proxy_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            self.proxy_client.connect(
                hostname=proxy.host,
                port=proxy.porta,
                username=proxy.usuario,
                password=proxy.senha,
                timeout=10,
                look_for_keys=False,
                allow_agent=False
            )
            
            # 2. Criar tunelamento Telnet via SSH
            proxy_transport = self.proxy_client.get_transport()
            dest_addr = (host, port)
            local_addr = ('127.0.0.1', 0)
            proxy_channel = proxy_transport.open_channel(
                "direct-tcpip",
                dest_addr,
                local_addr
            )
            
            # 3. Converter channel SSH para socket Telnet
            # Usar socket para conectar via proxy
            import socket
            sock = proxy_channel
            
            self.telnet_client = telnetlib.Telnet()
            self.telnet_client.sock = sock
            
            logger.info(f"✅ Telnet via proxy: Conectado")
            
            self.send(text_data=json.dumps({
                'type': 'info',
                'message': f'Autenticando Telnet...'
            }))
            
            # Autenticar
            self.authenticate_telnet(username, password)
            
            self.send(text_data=json.dumps({
                'type': 'connected',
                'message': f'✓ Telnet a {host}:{port} via {proxy.nome}'
            }))
            
            # Iniciar leitura
            self.read_thread = threading.Thread(target=self.read_telnet_output)
            self.read_thread.daemon = True
            self.read_thread.start()
            
        except Exception as e:
            logger.error(f"❌ Telnet via proxy: {str(e)}")
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Erro Telnet via proxy: {str(e)}'
            }))
            
            if self.proxy_client:
                try:
                    self.proxy_client.close()
                except:
                    pass
    
    # ============================================================
    # LEITURA - SSH
    # ============================================================
    def read_ssh_output(self):
        """Thread para ler output SSH"""
        try:
            while True:
                if self.channel and self.channel.recv_ready():
                    output = self.channel.recv(4096).decode('utf-8', errors='ignore')
                    self.send(text_data=json.dumps({
                        'type': 'output',
                        'data': output
                    }))
                    
                if self.channel and self.channel.exit_status_ready():
                    logger.info("SSH: Canal fechado")
                    break
                    
        except Exception as e:
            logger.error(f"SSH: Erro na leitura: {str(e)}")
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Erro na leitura SSH: {str(e)}'
            }))
    
    # ============================================================
    # LEITURA - TELNET
    # ============================================================
    def read_telnet_output(self):
        """✅ NOVO: Thread para ler output Telnet"""
        try:
            while True:
                try:
                    # Ler com timeout curto
                    output = self.telnet_client.read_very_eager()
                    
                    if output:
                        decoded = output.decode('utf-8', errors='ignore')
                        self.send(text_data=json.dumps({
                            'type': 'output',
                            'data': decoded
                        }))
                    
                except EOFError:
                    logger.info("Telnet: Conexão encerrada")
                    break
                    
        except Exception as e:
            logger.error(f"Telnet: Erro na leitura: {str(e)}")
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Erro na leitura Telnet: {str(e)}'
            }))