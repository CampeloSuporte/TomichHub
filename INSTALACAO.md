# CRM Tomich — Guia de Instalação em Ubuntu Server

> Este guia descreve passo a passo como subir uma nova instância do CRM Tomich do zero em uma VM Ubuntu 22.04 LTS ou 24.04 LTS.

---

## Sumário

1. [Requisitos de Hardware](#1-requisitos-de-hardware)
2. [Pacotes do Sistema](#2-pacotes-do-sistema)
3. [PostgreSQL](#3-postgresql)
4. [Redis](#4-redis)
5. [Clonar o Repositório](#5-clonar-o-repositório)
6. [Ambiente Python (venv)](#6-ambiente-python-venv)
7. [Configurações do Django](#7-configurações-do-django)
8. [Banco de Dados e Migrações](#8-banco-de-dados-e-migrações)
9. [Arquivos Estáticos e Mídia](#9-arquivos-estáticos-e-mídia)
10. [Serviços systemd](#10-serviços-systemd)
11. [Nginx](#11-nginx)
12. [SSL com Let's Encrypt](#12-ssl-com-lets-encrypt)
13. [Criar Superusuário](#13-criar-superusuário)
14. [Verificação Final](#14-verificação-final)
15. [Comandos Úteis do Dia a Dia](#15-comandos-úteis-do-dia-a-dia)

---

## 1. Requisitos de Hardware

| Recurso | Mínimo recomendado |
|---|---|
| CPU | 2 vCPUs |
| RAM | 4 GB |
| Disco | 40 GB |
| SO | Ubuntu 22.04 LTS ou 24.04 LTS |
| Rede | IP público (para acesso externo) |

---

## 2. Pacotes do Sistema

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    python3-pip \
    postgresql \
    postgresql-contrib \
    redis-server \
    nginx \
    git \
    curl \
    build-essential \
    libpq-dev \
    libssl-dev \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    certbot \
    python3-certbot-nginx \
    openssh-client \
    sshpass \
    expect \
    sudo
```

> **Nota:** O Python 3.12 é obrigatório. Verifique com `python3.12 --version`.

---

## 3. PostgreSQL

### 3.1 Iniciar e habilitar

```bash
sudo systemctl enable postgresql
sudo systemctl start postgresql
```

### 3.2 Criar banco e usuário

```bash
sudo -u postgres psql <<EOF
CREATE USER crm_user WITH PASSWORD '63675@ht';
CREATE DATABASE crm_db OWNER crm_user;
GRANT ALL PRIVILEGES ON DATABASE crm_db TO crm_user;
\q
EOF
```

> Substitua `'63675@ht'` pela senha desejada. Lembre de atualizar `crm/settings.py` com a mesma senha.

### 3.3 Verificar conexão

```bash
psql -U crm_user -d crm_db -h localhost -c "SELECT version();"
# Vai pedir a senha
```

---

## 4. Redis

```bash
sudo systemctl enable redis-server
sudo systemctl start redis-server

# Verificar
redis-cli ping
# Retorna: PONG
```

---

## 5. Clonar o Repositório

```bash
sudo mkdir -p /opt/crm
sudo chown $USER:$USER /opt/crm

cd /opt
git clone https://github.com/CampeloSuporte/TomichHub.git crm
cd /opt/crm
```

> Se o repositório for privado, use token pessoal:
> ```bash
> git clone https://<SEU_TOKEN>@github.com/CampeloSuporte/TomichHub.git crm
> ```

---

## 6. Ambiente Python (venv)

```bash
cd /opt/crm

python3.12 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

> Se `requirements.txt` não existir, instale manualmente (veja seção [Dependências Completas](#dependências-completas)).

---

## 7. Configurações do Django

### 7.1 Editar `crm/settings.py`

Abra o arquivo e ajuste os seguintes valores:

```python
# Chave secreta — gere uma nova para produção:
# python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
SECRET_KEY = 'sua-secret-key-aqui'

# Domínio ou IP da sua VM
ALLOWED_HOSTS = ['seu.dominio.com.br', '0.0.0.0']

# Banco de dados
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'crm_db',
        'USER': 'crm_user',
        'PASSWORD': 'SUA_SENHA_AQUI',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}

# SMTP (pode ser configurado pelo painel após instalação)
# Sistema → Configurações → SMTP
```

### 7.2 Criar diretório temporário

```bash
mkdir -p /opt/crm/tmp
```

---

## 8. Banco de Dados e Migrações

```bash
cd /opt/crm
source venv/bin/activate

python manage.py migrate
```

> Isso cria todas as tabelas necessárias (50+ migrations).

---

## 9. Arquivos Estáticos e Mídia

```bash
cd /opt/crm
source venv/bin/activate

# Coleta arquivos estáticos para /opt/crm/static/
python manage.py collectstatic --noinput

# Criar diretório de mídia com permissões corretas
mkdir -p /opt/crm/media
sudo chown -R www-data:www-data /opt/crm/media
sudo chown -R www-data:www-data /opt/crm/static
sudo chown -R www-data:www-data /opt/crm/tmp

# Criar diretório de firmware (Gerenciador de Arquivos / Firmware)
sudo mkdir -p /opt/crm/media/firmware
sudo chown www-data:www-data /opt/crm/media/firmware
```

---

## 10. Serviços systemd

### 10.1 Gunicorn (HTTP Django)

Crie `/etc/systemd/system/gunicorn.service`:

```ini
[Unit]
Description=Gunicorn daemon for Django
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/crm

Environment="PATH=/opt/crm/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="TMPDIR=/opt/crm/tmp"
Environment="ANSIBLE_LOCAL_TEMP=/opt/crm/tmp"
Environment="ANSIBLE_REMOTE_TEMP=/opt/crm/tmp"

ExecStart=/opt/crm/venv/bin/gunicorn \
    --access-logfile - \
    --workers 3 \
    --worker-class gthread \
    --threads 4 \
    --timeout 120 \
    --bind unix:/opt/crm/gunicorn.sock \
    crm.wsgi:application

ExecReload=/bin/kill -s HUP $MAINPID
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### 10.2 Daphne (WebSocket — terminal SSH)

Crie `/etc/systemd/system/daphne.service`:

```ini
[Unit]
Description=Daphne service for Django Channels
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/crm
Environment=DJANGO_SETTINGS_MODULE=crm.settings

ExecStart=/opt/crm/venv/bin/daphne \
    -b 127.0.0.1 -p 8001 \
    --ping-interval 20 \
    --ping-timeout 60 \
    crm.asgi:application

Restart=on-failure
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

### 10.3 Celery (tarefas assíncronas + agendadas)

Crie `/etc/systemd/system/celery.service`:

```ini
[Unit]
Description=Celery Service
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/crm
Environment="PATH=/opt/crm/venv/bin"

ExecStart=/opt/crm/venv/bin/celery \
    -A crm worker \
    --beat \
    -l info \
    --concurrency=1

Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

### 10.4 Habilitar e iniciar todos os serviços

```bash
sudo systemctl daemon-reload

sudo systemctl enable gunicorn daphne celery
sudo systemctl start gunicorn daphne celery

# Verificar status
sudo systemctl status gunicorn
sudo systemctl status daphne
sudo systemctl status celery
```

### 10.5 Permissões do socket Gunicorn

```bash
sudo chown www-data:www-data /opt/crm/gunicorn.sock 2>/dev/null || true
# O socket é criado automaticamente ao iniciar o gunicorn
```

---

## 11. Nginx

Crie `/etc/nginx/sites-available/crm`:

```nginx
# Redireciona HTTP → HTTPS
server {
    listen 80;
    server_name seu.dominio.com.br;
    client_max_body_size 2G;

    location / {
        return 301 https://$host$request_uri;
    }

    location /media/ {
        alias /opt/crm/media/;
    }

    location /static/ {
        alias /opt/crm/static/;
    }
}

# HTTPS
server {
    listen 443 ssl;
    server_name seu.dominio.com.br;
    client_max_body_size 2G;

    ssl_certificate     /etc/letsencrypt/live/seu.dominio.com.br/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/seu.dominio.com.br/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # Redireciona raiz para login
    location = / {
        return 301 /auth/login/;
    }

    location = /favicon.ico {
        access_log off;
        log_not_found off;
    }

    # Arquivos estáticos servidos diretamente pelo Nginx
    location /static/ {
        root /opt/crm;
    }

    location /media/ {
        root /opt/crm;
    }

    # WebSockets (terminal SSH/Telnet via Daphne)
    location /ws/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        proxy_redirect off;
        proxy_buffering off;
    }

    # Upload de firmware — sem buffering, timeout estendido para arquivos grandes (até 2 GB)
    location /clientes/firmware/upload/ {
        include proxy_params;
        proxy_pass http://unix:/opt/crm/gunicorn.sock;
        proxy_request_buffering off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    # Requisições HTTP normais (Gunicorn via socket Unix)
    location / {
        include proxy_params;
        proxy_pass http://unix:/opt/crm/gunicorn.sock;
    }
}
```

Ativar e testar:

```bash
sudo ln -s /etc/nginx/sites-available/crm /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 12. SSL com Let's Encrypt

> Substitua `seu.dominio.com.br` pelo domínio real. O DNS deve apontar para o IP da VM antes deste passo.

```bash
sudo certbot --nginx -d seu.dominio.com.br

# Renovação automática (já configurada pelo certbot, mas verifique)
sudo systemctl status certbot.timer
```

---

## 13. Criar Superusuário

```bash
cd /opt/crm
source venv/bin/activate

python manage.py createsuperuser
# Preencha: username, email, senha
```

> O superusuário terá acesso a todas as funções de admin, incluindo o menu **Sistema → Configurações** (SMTP).

---

## 14. Verificação Final

```bash
# Todos os serviços rodando?
sudo systemctl is-active gunicorn daphne celery redis postgresql nginx

# Logs em tempo real
sudo journalctl -u gunicorn -f
sudo journalctl -u daphne -f
sudo journalctl -u celery -f

# Testar resposta HTTP
curl -I http://localhost/auth/login/
# Deve retornar 301 (redirecionando para HTTPS) ou 200
```

Acesse no navegador: `https://seu.dominio.com.br`

---

## 15. Comandos Úteis do Dia a Dia

### Reiniciar serviços após atualização de código

```bash
sudo systemctl restart gunicorn daphne celery
```

### Aplicar migrações após pull

```bash
cd /opt/crm
source venv/bin/activate
python manage.py migrate
sudo systemctl restart gunicorn daphne celery
```

### Atualizar código do repositório

```bash
cd /opt/crm
git pull origin main
source venv/bin/activate
pip install -r requirements.txt     # se houver novas dependências
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart gunicorn daphne celery
```

### Ver logs de erro em tempo real

```bash
sudo journalctl -u gunicorn -n 100 -f
sudo journalctl -u daphne -n 100 -f
sudo journalctl -u celery -n 100 -f
```

### Acessar shell Django

```bash
cd /opt/crm
source venv/bin/activate
python manage.py shell
```

### Backup do banco de dados

```bash
sudo -u postgres pg_dump crm_db > /opt/backups/crm_db_$(date +%Y%m%d).sql
```

### Restaurar banco de dados

```bash
sudo -u postgres psql crm_db < /opt/backups/crm_db_20260101.sql
```

---

## Dependências Completas

Caso precise instalar manualmente (sem `requirements.txt`):

```bash
source /opt/crm/venv/bin/activate

pip install \
    Django==5.2.7 \
    gunicorn==23.0.0 \
    daphne==4.2.1 \
    channels==4.3.1 \
    channels_redis==4.3.0 \
    celery==5.5.3 \
    django-celery-beat==2.8.1 \
    django_celery_results==2.6.0 \
    redis==6.4.0 \
    django-redis==6.0.0 \
    psycopg2-binary==2.9.11 \
    djangorestframework==3.16.1 \
    django-cors-headers==4.9.0 \
    django-filter==25.2 \
    netmiko==4.3.0 \
    paramiko==4.0.0 \
    pexpect==4.9.0 \
    pillow==12.0.0 \
    Markdown==3.10.2 \
    bcrypt==5.0.0 \
    cryptography==46.0.3 \
    sshtunnel==0.4.0 \
    napalm==4.1.0 \
    requests==2.32.5 \
    reportlab==4.4.5 \
    fpdf2==2.8.5 \
    asgiref==3.10.0 \
    Twisted==25.5.0 \
    autobahn==25.10.1 \
    ansible==6.7.0 \
    pyftpdlib \
    tftpy
```

---

## Estrutura de Diretórios

```
/opt/crm/
├── crm/                  # Configurações Django (settings.py, urls.py, wsgi.py, asgi.py)
├── clientes/             # App principal (modelos, views, templates)
├── home/                 # Dashboard e administração
├── financeiro/           # Gestão financeira
├── wiki/                 # Base de conhecimento
├── monitoramento/        # Integração Zabbix
├── usuario/              # Autenticação
├── funcao_equipamento/   # Funções de equipamentos
├── modelo_equipamento/   # Modelos de equipamentos
├── templates/            # Templates globais (base.html, login.html)
├── static/               # Arquivos estáticos (coletados pelo collectstatic)
├── media/                # Uploads (backups, documentos, VPNs, faturas, firmware)
├── venv/                 # Ambiente virtual Python
├── tmp/                  # Temporários (Ansible, etc.)
├── gunicorn.sock         # Socket Unix do Gunicorn (criado em runtime)
├── manage.py
├── requirements.txt
├── SISTEMA.md            # Documentação do sistema
└── INSTALACAO.md         # Este arquivo
```

---

## Configuração Pós-Instalação

Após a instalação, acesse o sistema e configure:

1. **Sistema → Configurações** — Configure o servidor SMTP para envio de e-mails IRR
2. **Sistema → Usuários** — Crie usuários da equipe
3. **Sistema → Clientes** — Cadastre os primeiros clientes
4. **Sistema → Função de equipamento** — Cadastre tipos (Roteador, Switch, OLT, Firewall...)
5. **Sistema → Modelos de equipamento** — Cadastre modelos específicos
6. **Sistema → Ferramentas → Arquivos / Firmware** — Crie a estrutura de pastas para firmware e arquivos de configuração
7. **Sistema → Ferramentas → Pesquisa LG** — Disponível imediatamente; acessível também pelo botão "Consultar LG" na aba IRR/RPKI de cada cliente

---

## Observações de Segurança

- Altere a `SECRET_KEY` do Django para um valor único gerado aleatoriamente
- Use uma senha forte para o usuário PostgreSQL
- Nunca exponha o banco PostgreSQL ou Redis diretamente na internet
- Mantenha o SSL/TLS ativo (Let's Encrypt)
- Restrinja o `ALLOWED_HOSTS` ao domínio real (remova `'*'` em produção)
- Faça backups regulares do banco de dados
