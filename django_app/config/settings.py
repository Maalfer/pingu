"""Pingu/BaluHome Django settings."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# .env loader minimalista (sin dependencias externas).
ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    for _line in ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ── Núcleo ────────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or "baluhome-dev-secret-CHANGE-ME"
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"

_hosts = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]
ALLOWED_HOSTS = _hosts or (["*"] if DEBUG else ["127.0.0.1", "localhost"])

CSRF_TRUSTED_ORIGINS = [
    f"https://{h}" for h in ALLOWED_HOSTS if h not in ("127.0.0.1", "localhost", "*")
] + ["http://127.0.0.1:8002", "http://localhost:8002"]

# ── Apps / middleware ─────────────────────────────────────────────────────────
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Local apps
    "apps.core",
    "apps.accounts",
    "apps.notes",
    "apps.music",
    "apps.videos",
    "apps.shopping",
    "apps.todos",
    "apps.gastos",
    "apps.calendar_app",
    "apps.friends",
    "apps.chat",
    "apps.files_app",
    "apps.push_notif",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.GlobalContextMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "builtins": ["apps.core.templatetags.balu_filters"],
        "context_processors": [
            "django.template.context_processors.debug",
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
            "apps.core.context_processors.global_context",
        ],
    },
}]

# ── BD ────────────────────────────────────────────────────────────────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "data" / "balusong.sqlite3",
        # WAL + larger timeout: mejor concurrencia para SQLite en multi-worker.
        "OPTIONS": {"timeout": 20, "init_command": "PRAGMA journal_mode=WAL;"},
    }
}

# ── Auth ──────────────────────────────────────────────────────────────────────
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/"
LOGIN_REDIRECT_URL = "/home/"
LOGOUT_REDIRECT_URL = "/"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 6}},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "apps.accounts.hashers.BCryptLegacyHasher",
]

# ── I18N ──────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = "es"
TIME_ZONE = "Europe/Madrid"
USE_I18N = True
USE_TZ = True

# ── Static / Media ────────────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", "/var/www/balusong/static_collected"))
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Uploads: tope 1 GB. nginx debe ir alineado en client_max_body_size.
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5 MB en RAM; el resto va a disco temporal
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024 * 1024 + 10 * 1024 * 1024  # 1 GB + holgura

# ── Sesiones / CSRF ───────────────────────────────────────────────────────────
SESSION_COOKIE_AGE = 60 * 60 * 24 * 365
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # Necesario para que JS lea el token y lo envíe vía header
CSRF_HEADER_NAME = "HTTP_X_CSRFTOKEN"
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# ── Seguridad HTTPS (sólo cuando no es DEBUG) ────────────────────────────────
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = False  # nginx ya redirige 80 → 443
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "SAMEORIGIN"

# ── Logging ───────────────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# ── Constantes app ────────────────────────────────────────────────────────────
ASSET_VERSION = os.environ.get("ASSET_VERSION", "v105")

BALUHOME_DATA_ROOT = Path(os.environ.get("BALUHOME_DATA_ROOT", BASE_DIR / "data"))
BALUHOME_VAULT_ROOT = Path(os.environ.get("BALUHOME_VAULT_ROOT", BALUHOME_DATA_ROOT / "vault"))
BALUHOME_UPLOADS_ROOT = Path(os.environ.get("BALUHOME_UPLOADS_ROOT", BALUHOME_DATA_ROOT / "uploads"))
BALUHOME_VIDEOS_ROOT = Path(os.environ.get("BALUHOME_VIDEOS_ROOT", BALUHOME_DATA_ROOT / "videos"))
BALUHOME_AVATARS_ROOT = Path(os.environ.get("BALUHOME_AVATARS_ROOT", BASE_DIR / "static" / "avatars"))

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:admin@baluhome.local")
