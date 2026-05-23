# BaluHome Django

Migración del proyecto FastAPI a Django, en progreso.

## Estado actual

### ✓ Completado

- **Estructura idiomática Django** en `apps/`, `config/`, `templates/`, `static/`, `scripts/`.
- **13 apps**: accounts, notes, music, videos, shopping, todos, gastos, calendar_app, friends, chat, files_app, push_notif, core.
- **Modelos**: custom `User` (auth.User) + 12 modelos de entidades de la app.
- **Migraciones** aplicadas en `data/balusong.sqlite3` (DB nueva, separada de la antigua FastAPI).
- **BCrypt legacy hasher** (`apps/accounts/hashers.py`): los usuarios conservan sus contraseñas; Django re-hashea al primer login.
- **Datos migrados** desde `/var/www/balusong/data/balusong.db`:
  - 2 usuarios (Mario, fatimichiti) con contraseñas funcionales
  - 4 canciones, 5 eventos, 1 amistad, 3 gastos, 1 mensaje, 2 archivos, 3 suscripciones push
- **Vistas completamente portadas** (no son stubs):
  - `accounts`: login, register, logout, profile, settings, avatar, change-password, change-username, set-theme
  - `core`: root (login redirect), home (con contadores), sw.js, manifest.json, admin redirect
  - `notes`: vault COMPLETO (tree, file get/save, create, rename, delete, search, upload, asset, export, import)
  - `music`: library, fragment, stream, download (yt-dlp), delete
  - `shopping`, `todos`, `calendar_app`: CRUD completo
  - `friends`: request, respond, remove + lista filtrada
  - `chat`: lista de conversaciones, detalle con mensajes, send, list
  - `gastos`: lista con balances, detalle, add, delete
- **Plantillas convertidas** a sintaxis Django (Jinja `tojson`, `or '…'`, `is defined` traducidos automáticamente; `friends.html` y `calendar.html` reescritas manualmente).
- **Estáticos**: CSS, JS, iconos, avatares, vendor (CodeMirror) — 209 archivos colectados.
- **Gunicorn** funciona en puerto `:8002` (`scripts/run_gunicorn.sh`).
- **systemd unit** preparado en `scripts/baluhome_django.service`.

### ✗ Pendiente (próximas sesiones)

- **Videos**: portar descarga de torrents (background tasks), progreso, streaming.
- **Files**: portar tree, upload, download, ops de carpeta.
- **Push notifications**: subscribe/unsubscribe + envío vía pywebpush.
- **Panel admin** (`/admin/`): listado/edición de usuarios, crear invitaciones.
- **Probar manualmente cada apartado** (login con Mario, abrir notas, reproducir música, etc.) — algunos templates pueden tener pequeños detalles Jinja que no boten Django al renderizar (p. ej. `username[0]|upper` → `username|slice:":1"|upper`).
- **Switch de producción**:
  1. `sudo cp scripts/baluhome_django.service /etc/systemd/system/`
  2. `sudo systemctl daemon-reload && sudo systemctl enable --now baluhome_django.service`
  3. Editar nginx para apuntar `proxy_pass http://127.0.0.1:8002/` (era `:8001`)
  4. `sudo systemctl reload nginx`
  5. Una vez verificado, parar y deshabilitar `balusong.service`
  6. `mv /var/www/balusong /var/www/balusong.fastapi-old`
  7. `sudo mv /home/mario/balusong_django /var/www/baluhome` (con su chown a www-data correspondiente)
  8. Actualizar la systemd unit con la nueva ruta

## Cómo desarrollar

```bash
cd /home/mario/balusong_django
source venv/bin/activate

# Servidor de desarrollo
DJANGO_DEBUG=1 python manage.py runserver 127.0.0.1:8002

# Crear superusuario (admin del Django admin nativo)
python manage.py createsuperuser

# Aplicar nuevas migraciones
python manage.py makemigrations
python manage.py migrate

# Re-migrar datos (DESTRUCTIVO en algunas tablas)
python scripts/migrate_data.py

# Tests
python manage.py test
```

## Estructura

```
config/             — settings, urls, wsgi, asgi
apps/
├── accounts/       — User custom, login, registro, perfil, ajustes, avatares
├── core/           — home, sw, manifest, middleware, context_processors
├── notes/          — vault de notas markdown
├── music/          — biblioteca + reproductor + descarga YouTube
├── videos/         — videoteca con torrents (stubs)
├── shopping/       — lista de la compra
├── todos/          — tareas pendientes
├── calendar_app/   — calendario (nombre `calendar` colisiona con stdlib)
├── friends/        — relaciones de amistad
├── gastos/         — gastos compartidos
├── chat/           — mensajería 1-a-1
├── files_app/      — "mi nube" (stubs)
└── push_notif/     — web push (stubs)
templates/          — plantillas Django (heredan de base.html)
static/             — CSS, JS, iconos, avatares, vendor CodeMirror
data/               — sqlite3 + vault de notas
scripts/            — migrate_data.py, run_gunicorn.sh, systemd unit
```

## Convenciones / best practices aplicadas

- Custom `User` antes de la primera migración (recomendado por Django).
- Apps en `apps/<name>/` con `label = "<name>"` corto.
- Cada app tiene `urls.py` con `app_name` para reverse seguro.
- Modelos con `related_name` explícito, `Meta.ordering` e índices donde toca.
- `ForeignKey(settings.AUTH_USER_MODEL, …)` en vez de importar directamente.
- `@login_required` + `@require_POST` / `@require_GET` en todas las vistas que aplican.
- `context_processor` para variables globales (asset_version, username, user_id, user_role, bh_theme).
- Middleware propio (`GlobalContextMiddleware`) que normaliza el tema.
- `.env` para configuración por entorno (no committeado).
- Datos en sqlite preservando IDs de usuario (paths físicos no se rompen).
