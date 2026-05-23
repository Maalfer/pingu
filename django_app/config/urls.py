"""URL routing principal."""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from apps.core import views as core_views

urlpatterns = [
    # Página raíz: login (si no autenticado) o redirect a /home/.
    path("", core_views.root, name="root"),

    # Apps (páginas)
    path("", include("apps.accounts.urls")),
    path("home/", core_views.home, name="home"),
    path("notes/", include("apps.notes.urls")),
    path("music/", include("apps.music.urls")),
    path("videos/", include("apps.videos.urls")),
    path("shopping/", include("apps.shopping.urls")),
    path("todos/", include("apps.todos.urls")),
    path("gastos/", include("apps.gastos.urls")),
    path("calendar/", include("apps.calendar_app.urls")),
    path("friends/", include("apps.friends.urls")),
    path("chat/", include("apps.chat.urls")),
    path("files/", include("apps.files_app.urls")),
    # /push/ ya no expone URLs propias (las APIs viven en /api/push/).

    # APIs montadas al mismo path que el FastAPI antiguo (compat. del JS frontend).
    path("api/vault/",    include("apps.notes.api_urls")),
    path("api/files/",    include("apps.files_app.api_urls")),
    path("api/videos/",   include("apps.videos.api_urls")),
    path("api/admin/",    include("apps.accounts.admin_urls")),
    path("api/push/",     include("apps.push_notif.urls")),
    path("api/shopping/", include("apps.shopping.api_urls")),
    path("api/todos/",    include("apps.todos.api_urls")),
    path("api/calendar/", include("apps.calendar_app.api_urls")),
    path("api/chat/",     include("apps.chat.api_urls")),
    path("api/gastos/",   include("apps.gastos.api_urls")),
    path("api/friends/",  include("apps.friends.api_urls")),
    path("api/profile/",  include("apps.accounts.profile_urls")),
    # /api/stream/<id>, /api/download, /api/delete  (paths "huérfanos" del FastAPI)
    path("api/",          include("apps.music.api_urls")),
    # /fragments/library  (router SPA del music app)
    path("fragments/<str:page>", core_views.music_fragment),

    # Service worker + manifest a nivel raíz (necesario para PWA scope).
    path("sw.js", core_views.service_worker, name="sw"),
    path("manifest.json", core_views.manifest, name="manifest"),

    # Compatibilidad con la URL antigua /app → /music
    path("app/", core_views.legacy_app_redirect),

    # Admin Django (separado del panel de admins de usuarios).
    path("django-admin/", admin.site.urls),
    path("admin/", core_views.admin_panel, name="admin_panel"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
