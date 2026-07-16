

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
DEBUG = True

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
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
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

# ── Sessão: expira em 4 horas de inatividade ──
SESSION_COOKIE_AGE = 1 * 60 * 60        # 3600 segundos
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
