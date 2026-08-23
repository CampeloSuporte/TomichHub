

from pathlib import Path
import os
from celery.schedules import crontab

# Diretório base do projeto
BASE_DIR = Path(__file__).resolve().parent.parent

# Caminhos de mídia (uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-)41v!4g-#@=9-&-fa*=g%t0ex-$%2srvjg#-lzyvx+%y9ei#ja'

# SECURITY WARNING: don't run with debug turned on in production!
# Em produção fica False: com True o Django devolve a própria página de erro
# (traceback, variáveis de ambiente, lista completa de rotas). Com False ele
# renderiza templates/{400,403,403_csrf,404,500}.html e grava o traceback em
# logs/django-erros.log (ver LOGGING no fim deste arquivo).
# Para depurar pontualmente: exportar DJANGO_DEBUG=1 no serviço e reiniciar.
DEBUG = os.environ.get('DJANGO_DEBUG', '0') == '1'

ALLOWED_HOSTS = ['*']


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'clientes',
    'funcao_equipamento',
    'modelo_equipamento',
    'usuario',
    'home',
    'channels',
    'rest_framework',
    'rest_framework.authtoken',
    'financeiro',
    'wiki',
    'markdown',
    'monitoramento.apps.MonitoramentoConfig',
    'atendimento',
    'tarefas',
    'seguranca',
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Filtro de injeção (SQLi/path traversal/XSS) — antes de sessão e auth,
    # pra descartar a requisição sem custo de banco. Ver seguranca/middleware.py.
    'seguranca.middleware.ProtecaoInjecaoMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'usuario.middleware.ProtegerAdminMiddleware',
    'usuario.middleware.Forcar2FAMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'crm.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'financeiro.context_processors.financeiro_context',
                'usuario.context_processors.perfil_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'crm.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'crm_db',
        'USER': 'crm_user',
        'PASSWORD': '63675@ht',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'pt-BR'
USE_I18N = True
TIME_ZONE = 'America/Sao_Paulo'
USE_TZ = True  # ✅ MUDAR para True (timezone aware)



# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'


STATIC_ROOT = os.path.join(BASE_DIR, 'static')

# Pasta onde o Django vai procurar arquivos estáticos no modo dev
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'staticfiles'),
]

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Adicione no final do arquivo
ASGI_APPLICATION = 'crm.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [('127.0.0.1', 6379)],
            'capacity': 1500,
            # expiry: tempo (s) que uma mensagem vive no Redis antes de ser
            # descartada se ainda não consumida. 10s era agressivo demais — em
            # conversas movimentadas, mensagens do cliente eram perdidas no
            # WebSocket. 60s (padrão) dá folga sem acumular lixo.
            'expiry': 60,
        },
    }
}

# ========================================
# WebRTC — Servidor TURN (coturn) para Sala Virtual e chamadas 1:1
# ========================================
# O coturn roda neste servidor (/etc/turnserver.conf). As credenciais são
# temporárias (TURN REST API): geradas pelo Django via HMAC do segredo abaixo.
# Usa o IP público bruto (não o domínio) porque TURN é UDP/TCP e não passa
# por proxy/CDN.
import os as _os
TURN_HOST   = _os.environ.get('TURN_HOST', '45.235.72.10')
TURN_REALM  = _os.environ.get('TURN_REALM', 'crm.tomich.com.br')
TURN_SECRET = _os.environ.get(
    'TURN_SECRET',
    '6b5cdf609e30467cad14f12cadc3f754bf63149fc56afafd1918b7054ed7a3e4',
)
TURN_TTL    = int(_os.environ.get('TURN_TTL', 12 * 3600))  # validade das credenciais (s)

# ========================================
# Cloudflare Turnstile — captcha da tela de login
# ========================================
TURNSTILE_SITE_KEY   = _os.environ.get('TURNSTILE_SITE_KEY', '0x4AAAAAAEA7NkrE4rea0ARo')
TURNSTILE_SECRET_KEY = _os.environ.get('TURNSTILE_SECRET_KEY', '0x4AAAAAAEA7Nqn2Ua_muMu_sKkd5E03noI')

# Limites de upload
DATA_UPLOAD_MAX_MEMORY_SIZE = 104857600  # 100 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 104857600  # 100 MB

# ========================================
# CELERY
# ========================================
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
CELERY_TIMEZONE = 'America/Sao_Paulo'
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 min


# Adicione ao INSTALLED_APPS:
INSTALLED_APPS += [
    'django_celery_beat',
    'django_celery_results',
]


CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/1',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}

DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000

# ── Sessão: expira em 7 dias de inatividade (era 1h — deslogava no meio de
# qualquer navegação mais longa, ex. dentro do proxy web de acessos) ──
SESSION_COOKIE_AGE = 7 * 24 * 60 * 60   # 604800 segundos
SESSION_EXPIRE_AT_BROWSER_CLOSE = False  # mantém entre abas/reaberturas até o limite
SESSION_SAVE_EVERY_REQUEST = True        # renova o timer a cada request (sliding window)
# ✅ FORÇAR timezone local em todos os DateTimeFields
import os
os.environ['TZ'] = 'America/Sao_Paulo'

# ✅ Garantir que Django usa timezone local
TIME_ZONE = 'America/Sao_Paulo'
USE_TZ = True
USE_L10N = True
X_FRAME_OPTIONS = 'SAMEORIGIN'

LOGIN_URL = '/auth/login/'


# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
# Com DEBUG=False o Django deixa de mostrar o traceback na tela — o usuário vê
# templates/500.html. Sem esta configuração o traceback também não iria para
# lugar nenhum (o handler de console padrão é filtrado por require_debug_true e
# o mail_admins não tem ADMINS/e-mail configurado), ou seja, o erro sumiria.
# Aqui ele vai para logs/django-erros.log e para o journal do serviço
# (journalctl -u gunicorn / -u daphne / -u celery).
LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'completo': {
            'format': '[{asctime}] {levelname} {name} {process:d} — {message}',
            'style': '{',
        },
    },
    'handlers': {
        'arquivo_erros': {
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'django-erros.log'),
            'maxBytes': 10 * 1024 * 1024,   # 10 MB por arquivo
            'backupCount': 5,               # mantém ~50 MB de histórico
            'formatter': 'completo',
            'encoding': 'utf-8',
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'completo',
        },
    },
    'loggers': {
        # 500 e exceções não tratadas (inclui o traceback completo)
        'django.request': {
            'handlers': ['arquivo_erros', 'console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        # Apps do CRM que já usam logging.getLogger(__name__)
        'clientes': {
            'handlers': ['arquivo_erros', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
        'atendimento': {
            'handlers': ['arquivo_erros', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# Segurança — força bruta no login, fail2ban e filtro de injeção
# ──────────────────────────────────────────────────────────────────────────────
# Documentação completa: docs/SEGURANCA.md

# Bloqueio de CONTA: errou a senha N vezes, tranca por M minutos.
SEGURANCA_MAX_TENTATIVAS = int(os.environ.get('SEGURANCA_MAX_TENTATIVAS', 3))
SEGURANCA_BLOQUEIO_MINUTOS = int(os.environ.get('SEGURANCA_BLOQUEIO_MINUTOS', 5))

# Bloqueio por IP: mais folgado, porque um IP legítimo pode ser o NAT de um
# escritório inteiro. Cobre o robô que varre usernames inexistentes, caso em
# que a conta não serve de chave.
SEGURANCA_MAX_TENTATIVAS_IP = int(os.environ.get('SEGURANCA_MAX_TENTATIVAS_IP', 10))
SEGURANCA_BLOQUEIO_IP_MINUTOS = int(os.environ.get('SEGURANCA_BLOQUEIO_IP_MINUTOS', 15))

# Janela deslizante: falhas mais antigas que isso não somam para o bloqueio.
SEGURANCA_JANELA_MINUTOS = int(os.environ.get('SEGURANCA_JANELA_MINUTOS', 15))

# Retenção do log de tentativas/eventos (task Celery seguranca.limpar_registros).
SEGURANCA_RETENCAO_DIAS = int(os.environ.get('SEGURANCA_RETENCAO_DIAS', 90))

# Filtro de injeção. `BLOQUEAR=False` = modo observação (registra e deixa passar),
# útil pra checar falso positivo antes de ligar de vez numa rota nova.
SEGURANCA_INJECAO_ATIVO = os.environ.get('SEGURANCA_INJECAO_ATIVO', '1') == '1'
SEGURANCA_INJECAO_BLOQUEAR = os.environ.get('SEGURANCA_INJECAO_BLOQUEAR', '1') == '1'

# Arquivo lido pelo fail2ban (jail `crm-login`). Caminho fixo em produção pra
# casar com /etc/fail2ban/jail.d/crm.local; cai em BASE_DIR/logs quando
# /var/log/crm não existe (worktree, máquina de dev).
def _caminho_auth_log():
    preferido = os.environ.get('SEGURANCA_AUTH_LOG', '/var/log/crm/auth.log')
    pasta = os.path.dirname(preferido)
    if os.path.isdir(pasta) and os.access(pasta, os.W_OK):
        return preferido
    return str(LOG_DIR / 'auth.log')


SEGURANCA_AUTH_LOG = _caminho_auth_log()

# Binário e log do fail2ban (o painel lê o histórico direto do arquivo).
FAIL2BAN_CLIENT = os.environ.get('FAIL2BAN_CLIENT', '/usr/bin/fail2ban-client')
FAIL2BAN_LOG = os.environ.get('FAIL2BAN_LOG', '/var/log/fail2ban.log')

# Handler dedicado: uma linha por falha de login, formato consumido pelo
# filtro /etc/fail2ban/filter.d/crm-login.conf. Mudar o formato aqui exige
# mudar o regex lá.
LOGGING['formatters']['auth_fail2ban'] = {
    'format': '{asctime} {message}',
    'datefmt': '%Y-%m-%d %H:%M:%S',
    'style': '{',
}
LOGGING['handlers']['auth_fail2ban'] = {
    'level': 'WARNING',
    'class': 'logging.handlers.RotatingFileHandler',
    'filename': SEGURANCA_AUTH_LOG,
    'maxBytes': 10 * 1024 * 1024,
    'backupCount': 3,
    'formatter': 'auth_fail2ban',
    'encoding': 'utf-8',
    # delay=True: o arquivo só é aberto na primeira falha de login. Sem isso,
    # um processo sem permissão de escrita (ex.: manage.py rodado por outro
    # usuário) quebraria no import do settings.
    'delay': True,
}
LOGGING['loggers']['seguranca.auth'] = {
    'handlers': ['auth_fail2ban'],
    'level': 'WARNING',
    'propagate': False,
}
LOGGING['loggers']['seguranca'] = {
    'handlers': ['arquivo_erros', 'console'],
    'level': 'INFO',
    'propagate': False,
}

# ── Cabeçalhos e cookies ──────────────────────────────────────────────────────
# O nginx termina o TLS e repassa X-Forwarded-Proto; sem esta linha o Django
# acha que toda requisição é http e `request.is_secure()` mente (o que afeta,
# entre outras coisas, o flag `secure` do cookie de dispositivo confiável do 2FA).
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
# CSRF_COOKIE_HTTPONLY fica False de propósito: o JS do CRM lê o cookie
# csrftoken pra montar o header X-CSRFToken nos fetch().

# Cookies só por HTTPS: desligado por padrão porque o servidor ainda atende em
# http:// no IP bruto (45.235.72.10) e ligar isso derrubaria o login por lá.
# Ative com SEGURANCA_COOKIES_HTTPS=1 quando todo acesso for por domínio TLS.
if os.environ.get('SEGURANCA_COOKIES_HTTPS', '0') == '1':
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
