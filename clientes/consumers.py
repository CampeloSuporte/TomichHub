import json
import paramiko
import threading
import ipaddress
from channels.generic.websocket import WebsocketConsumer
from .models import Acesso, ProxyServer


class SSHConsumer(WebsocketConsumer):
    def connect(self):
        self.accept()
        self.ssh_client = None
        self.channel = None
        self.proxy_client = None  # Cliente SSH do proxy
        
    def disconnect(self, close_code):
        if self.channel:
            self.channel.close()
        if self.ssh_client:
            self.ssh_client.close()
        if self.proxy_client:
            self.proxy_client.close()
    
    def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')
        
        if action == 'connect':
            acesso_id = data.get('acesso_id')
            try:
                acesso = Acesso.objects.get(id=acesso_id)
                
                # Verificar se é IP privado
                if self.is_private_ip(acesso.host):
                    self.connect_ssh_via_proxy(
                        host=acesso.host,
                        port=int(acesso.porta),
                        username=acesso.usuario,
                        password=acesso.senha
                    )
                else:
                    self.connect_ssh(
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
            if self.channel:
                self.channel.send(command)
    
    def is_private_ip(self, host):
        """Verifica se o host é um IP privado"""
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private
        except ValueError:
            # Se não for um IP válido, assume que é público (pode ser hostname)
            return False
    
    def get_active_proxy(self):
        """Retorna um proxy ativo disponível"""
        try:
            proxy = ProxyServer.objects.filter(ativo=True).first()
            if not proxy:
                raise Exception("Nenhum servidor proxy ativo disponível")
            return proxy
        except Exception as e:
            raise Exception(f"Erro ao buscar proxy: {str(e)}")
    
    def connect_ssh(self, host, port, username, password):
        """Conexão SSH direta (IP público)"""
        try:
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            self.ssh_client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=10,
                look_for_keys=False,
                allow_agent=False
            )
            
            self.channel = self.ssh_client.invoke_shell(
                term='xterm-256color',
                width=120,
                height=40
            )
            
            self.send(text_data=json.dumps({
                'type': 'connected',
                'message': f'✓ Conectado diretamente a {host}:{port}'
            }))
            
            read_thread = threading.Thread(target=self.read_ssh_output)
            read_thread.daemon = True
            read_thread.start()
            
        except paramiko.AuthenticationException:
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Erro de autenticação. Verifique usuário e senha.'
            }))
        except Exception as e:
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Erro ao conectar: {str(e)}'
            }))
    
    def connect_ssh_via_proxy(self, host, port, username, password):
        """Conexão SSH via proxy (IP privado)"""
        try:
            # Buscar proxy ativo
            proxy = self.get_active_proxy()
            
            self.send(text_data=json.dumps({
                'type': 'info',
                'message': f'⚡ IP privado detectado. Conectando via proxy {proxy.nome}...'
            }))
            
            # 1. Conectar ao servidor proxy
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
            
            # 2. Criar um canal de transporte através do proxy
            proxy_transport = self.proxy_client.get_transport()
            dest_addr = (host, port)
            local_addr = ('127.0.0.1', 0)
            proxy_channel = proxy_transport.open_channel(
                "direct-tcpip",
                dest_addr,
                local_addr
            )
            
            # 3. Conectar ao host final através do canal do proxy
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            self.ssh_client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                sock=proxy_channel,  # Usar o canal do proxy como socket
                timeout=10,
                look_for_keys=False,
                allow_agent=False
            )
            
            # 4. Abrir shell interativo
            self.channel = self.ssh_client.invoke_shell(
                term='xterm-256color',
                width=120,
                height=40
            )
            
            self.send(text_data=json.dumps({
                'type': 'connected',
                'message': f'✓ Conectado a {host}:{port} via proxy {proxy.nome}'
            }))
            
            # 5. Iniciar thread de leitura
            read_thread = threading.Thread(target=self.read_ssh_output)
            read_thread.daemon = True
            read_thread.start()
            
        except Exception as e:
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Erro ao conectar via proxy: {str(e)}'
            }))
            
            # Limpar conexões em caso de erro
            if self.proxy_client:
                self.proxy_client.close()
            if self.ssh_client:
                self.ssh_client.close()
    
    def read_ssh_output(self):
        """Thread para ler continuamente o output do SSH"""
        try:
            while True:
                if self.channel and self.channel.recv_ready():
                    output = self.channel.recv(4096).decode('utf-8', errors='ignore')
                    self.send(text_data=json.dumps({
                        'type': 'output',
                        'data': output
                    }))
                    
                if self.channel and self.channel.exit_status_ready():
                    break
                    
        except Exception as e:
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Erro na leitura: {str(e)}'
            }))