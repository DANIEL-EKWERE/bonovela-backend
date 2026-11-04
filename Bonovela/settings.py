"""
Bonovela backend settings.

Secrets and environment-specific values come from `.env` (see `.env.example`);
nothing sensitive is hard-coded here.
"""

from datetime import timedelta
from pathlib import Path

import dj_database_url
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    # Third party
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'django_filters',
    'drf_spectacular',
    # Bonovela
    'apps.users',
    'apps.catalog',
    'apps.wallet',
    'apps.engagement',
    'apps.editorial',
    'apps.drama',
    'apps.marketing',
    'apps.api',
    'apps.dashboard',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.users.ip.IPTrackingMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'Bonovela.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.dashboard.context_processors.dashboard_globals',
            ],
        },
    },
]

WSGI_APPLICATION = 'Bonovela.wsgi.application'

# SQLite in development; set DATABASE_URL (Postgres) in production.
DATABASES = {
    'default': dj_database_url.parse(
        config('DATABASE_URL', default='') or f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=60,
    )
}

AUTH_USER_MODEL = 'users.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 6}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Web dashboards (Author Center, Editorial, Admin) use session login.
LOGIN_URL = 'dashboard:login'
LOGIN_REDIRECT_URL = 'dashboard:home'
LOGOUT_REDIRECT_URL = 'dashboard:login'

# ── API ──────────────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticatedOrReadOnly'],
    'DEFAULT_FILTER_BACKENDS': ['django_filters.rest_framework.DjangoFilterBackend'],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=12),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
    'ROTATE_REFRESH_TOKENS': True,
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Bonovela API',
    'DESCRIPTION': 'Backend for the Bonovela reading app.',
    'VERSION': '1.0.0',
}

CORS_ALLOW_ALL_ORIGINS = config('CORS_ALLOW_ALL', default=False, cast=bool)
CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='', cast=Csv())

# ── Business rules ───────────────────────────────────────────────────────────
BONOVELA = {
    'FREE_CHAPTERS': 10,  # novels: chapters 1–10 free
    'CHAPTER_PRICE': 15,  # coins per locked novel chapter
    'FREE_SHORT_CHAPTERS': 3,
    'SHORT_CHAPTER_PRICE': 10,
    'AUTHOR_SHARE': 0.5,  # signed authors' share of unlock revenue
    'PROMOTION_DAYS': 7,  # default length of a CE-approved promotion
    'CHECKIN_REWARDS': [15, 20, 20, 20, 20, 20, 20],
    'BONUS_EXPIRY_DAYS': 3,
    'FREE_EPISODES': 5,  # dramas: episodes 1–5 free
    'EPISODE_PRICE': 10,  # coins per locked episode (free with Plus)
    'CREATOR_SHARE': 0.5,  # drama creators' share of episode unlocks
    'PAYOUT_MINIMUM_CENTS': 1000,  # Finance pays balances of $10 or more
    # Editorial commission: share of a book's coin revenue (1 coin ≈ 1¢).
    # Set by the platform admin; editors cannot change it.
    'EDITOR_COMMISSION': {'se': 0.05, 'ce': 0.01},
    'AUTHOR_INACTIVE_DAYS': 7,  # flagged "slowing down"
    'AUTHOR_AT_RISK_DAYS': 14,  # flagged "at risk"
    'EDITOR_OVERLOAD': 1.5,  # an SE with books above 1.5× the average = overloaded
}

# AI Completion Review (optional). With no key the review runs the built-in
# manuscript checks only.
ANTHROPIC_API_KEY = config('ANTHROPIC_API_KEY', default='')
AI_REVIEW_MODEL = config('AI_REVIEW_MODEL', default='claude-opus-5-5')

# Set True only behind a reverse proxy that sets X-Forwarded-For; otherwise
# clients could fake their IP.
TRUST_X_FORWARDED_FOR = config('TRUST_X_FORWARDED_FOR', default=False, cast=bool)
