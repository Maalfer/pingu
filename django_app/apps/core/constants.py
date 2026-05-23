"""Constantes compartidas (límites de tamaño, dominios, longitudes de campo).

Vivir aquí evita "magic numbers" sueltos por las views y permite tunearlos
en un solo punto. Si una constante debe ser configurable por entorno,
muévela a `config/settings/base.py` y léela desde aquí con
`getattr(settings, "NOMBRE", default)`.
"""
from django.conf import settings

# ── Uploads ──────────────────────────────────────────────────────────────────
MAX_AVATAR_BYTES = 4 * 1024 * 1024            # 4 MB
MAX_FILE_UPLOAD_BYTES = 1024 * 1024 * 1024    # 1 GB (sincronizado con nginx + DATA_UPLOAD_MAX_MEMORY_SIZE)
MAX_NOTE_BYTES = 5_000_000                    # 5 MB de texto Markdown

# ── Longitudes de campo (validación a nivel view) ────────────────────────────
MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 30
MAX_MESSAGE_LENGTH = 1000                     # /api/chat/send
MAX_VIDEO_TITLE_LENGTH = 300                  # video.title
MAX_GASTO_DESCRIPTION_LENGTH = 400            # Transaction.description (vista)

# ── Sistema de archivos ──────────────────────────────────────────────────────
NOTE_MAX_PATH_DEPTH = 20                      # carpetas anidadas en el vault

# ── Streaming ────────────────────────────────────────────────────────────────
STREAM_CHUNK_SIZE = 512 * 1024                # bytes por chunk al servir vídeo/audio

# ── YouTube (descarga música) ────────────────────────────────────────────────
YOUTUBE_DOMAINS = ("youtube.com", "youtu.be")

# ── Torrents (videos app, parámetros aria2c) ─────────────────────────────────
ARIA2_STOP_TIMEOUT = "300"                    # segundos sin descarga -> abort
ARIA2_MAX_PEERS = "100"

# ── Themes válidos (espejo de accounts.User.THEME_CHOICES) ───────────────────
VALID_THEMES = ("dark", "light", "dracula", "pink", "aqua")

# ── Avatars: tipos de imagen aceptados ───────────────────────────────────────
ALLOWED_AVATAR_KINDS = ("jpeg", "png", "gif", "webp")
