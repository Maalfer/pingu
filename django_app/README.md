# BaluHome

App familiar (notas, música, vídeos, chat, gastos, calendario, etc.) en
Django 6 ASGI sobre MariaDB.

## Stack

- **Backend**: Django 6.0 + gunicorn (`UvicornWorker`, 3 workers).
- **BD**: MariaDB 11.8 (utf8mb4). Variables en `.env`.
- **Estáticos**: WhiteNoise + nginx (`/static/` aliasea a `static/`).
- **Reverse proxy**: nginx con TLS (Cloudflare delante).

## Apps

`apps/` contiene 13 apps: `accounts`, `core`, `notes`, `music`, `videos`,
`shopping`, `todos`, `gastos`, `calendar_app`, `friends`, `chat`,
`files_app`, `push_notif`. Cada app tiene su `urls.py` y, donde aplica,
un `api_urls.py` con endpoints JSON.

## Desarrollo

```bash
python -m venv venv && source venv/bin/activate
pip install -r ../requirements.txt
cp django_app/.env.example django_app/.env  # rellena DB_PASSWORD y SECRET_KEY
cd django_app && python manage.py migrate && python manage.py runserver
```

Para crear un usuario admin:

```bash
python manage.py createsuperuser  # accede luego en /django-admin/
```

## Despliegue

- Unit systemd: `scripts/baluhome.service`.
- nginx: `proxy_pass http://127.0.0.1:8001`, `client_max_body_size 1024M`,
  `/static/` aliaseado a `django_app/static/`.
- `manage.py collectstatic --noinput` después de cada cambio en assets.

## Variables de entorno (`.env`)

Ver `.env.example` para la lista completa. Las clave son `DJANGO_SECRET_KEY`,
`DJANGO_ALLOWED_HOSTS`, `DB_PASSWORD`, `BALUHOME_*_ROOT`.

## Estructura

```
config/             — settings, urls, wsgi, asgi
apps/               — 13 apps (ver lista arriba)
templates/          — plantillas Django (heredan de base.html)
static/             — CSS, JS, iconos, avatares, vendor (CodeMirror, KaTeX)
data/               — vault de notas por usuario (sólo ficheros, NO la DB)
scripts/baluhome.service — unidad systemd para producción
```

## Convenciones

- Custom `User` (`accounts.User`) configurado antes de la primera migración.
- `ForeignKey(settings.AUTH_USER_MODEL, …)` siempre que se referencia al user.
- `@login_required` + `@require_POST/GET` en todas las vistas.
- Variables globales de plantilla vía `apps.core.context_processors.global_context`.
- CSRF se envía vía header `X-CSRFToken` (interceptado globalmente en `base.html`).
- IDs de usuario son la clave física: el path de la bóveda, avatares y
  uploads dependen de `user.id`; preservarlos al migrar datos.
