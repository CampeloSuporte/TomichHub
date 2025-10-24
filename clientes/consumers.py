import json
import paramiko
import threading
from channels.generic.websocket import WebsocketConsumer
from .models import Acesso


class SSHConsumer(WebsocketConsumer):
    def connect(self):
        self.accept()
        self.ssh_client = None
        self.channel = None
        
    def disconnect(self, close_code):
        if self.channel:
            self.channel.close()
        if self.ssh_client:
            self.ssh_client.close()
    
    def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')
        
        if action == 'connect':
            acesso_id = data.get('acesso_id')
            try:
                acesso = Acesso.objects.get(id=acesso_id)
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
    
    def connect_ssh(self, host, port, username, password):
        try:
            # Criar cliente SSH
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Conectar
            self.ssh_client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=10,
                look_for_keys=False,
                allow_agent=False
            )
            
            # Abrir canal shell interativo
            self.channel = self.ssh_client.invoke_shell(
                term='xterm-256color',
                width=120,
                height=40
            )
            
            # Enviar mensagem de sucesso
            self.send(text_data=json.dumps({
                'type': 'connected',
                'message': f'Conectado com sucesso a {host}:{port}'
            }))
            
            # Iniciar thread para ler output do SSH
            read_thread = threading.Thread(target=self.read_ssh_output)
            read_thread.daemon = True
            read_thread.start()
            
        except paramiko.AuthenticationException:
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Erro de autenticação. Verifique usuário e senha.'
            }))
        except paramiko.SSHException as e:
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Erro SSH: {str(e)}'
            }))
        except Exception as e:
            self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Erro ao conectar: {str(e)}'
            }))
    
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