"""Vistas core: root (login redirect), home dashboard, service worker, manifest, admin panel."""
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from apps.shopping.models import ShoppingItem
from apps.todos.models import Todo
from apps.videos.models import Video
from apps.chat.models import Message
from apps.friends.models import Friendship
from apps.accounts.models import User, InviteToken, ActivityLog


def root(request):
    """Login page (sin autenticar) o /home (autenticado).

    Acepta POST para que el formulario de la propia página pueda enviar
    `action=""/`. Delega al login_view real.
    """
    if request.user.is_authenticated:
        return redirect("home")
    from apps.accounts.views import login_view
    return login_view(request)


def legacy_app_redirect(request):
    return redirect("/music/", permanent=True)


@login_required
def home(request):
    """Dashboard principal."""
    user = request.user
    pending_friends = Friendship.objects.filter(addressee=user, status="pending").count()
    shopping_count = ShoppingItem.objects.filter(user=user, done=False).count()
    todos_count = Todo.objects.filter(user=user, done=False).count()
    unread_msgs = Message.objects.filter(receiver=user, read_at__isnull=True).count()
    videos_count = Video.objects.filter(user=user, status="downloading").count()
    return render(request, "core/home.html", {
        "pending_friends": pending_friends,
        "shopping_count": shopping_count,
        "todos_count": todos_count,
        "unread_msgs": unread_msgs,
        "videos_count": videos_count,
    })


def service_worker(request):
    """Sirve `sw.js` desde disco con las cabeceras correctas para Service Workers.

    El JS vive en `apps/core/static/core/sw.js` para que cualquiera pueda editarlo
    como código normal (con linter/formatter). El view sólo le añade los headers:

    - `Cache-Control: no-cache, no-store, must-revalidate` — los SW NUNCA se
      cachean; Cloudflare/proxies deben preguntar siempre al origen.
    - `Service-Worker-Allowed: /` — scope al raíz del sitio.

    Nota: no servimos directamente con nginx para asegurar estas cabeceras.
    """
    from pathlib import Path
    sw_path = Path(__file__).parent / "static" / "core" / "sw.js"
    body = sw_path.read_text(encoding="utf-8")
    resp = HttpResponse(body, content_type="application/javascript")
    resp["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    resp["Service-Worker-Allowed"] = "/"
    return resp



def manifest(request):
    v = getattr(settings, "ASSET_VERSION", "1")
    return JsonResponse({
        "name": "Pingu",
        "short_name": "Pingu",
        "start_url": "/home/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0a0a0f",
        "theme_color": "#0a0a0f",
        "icons": [
            {"src": f"/static/icons/icon-192.png?v={v}", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": f"/static/icons/icon-512.png?v={v}", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": f"/static/icons/icon-512.png?v={v}", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    })


@staff_member_required(login_url="/")
def admin_panel(request):
    users = User.objects.all().order_by("-date_joined")
    invites = InviteToken.objects.filter(is_used=False).order_by("-created_at")[:50]
    return render(request, "accounts/admin_panel.html", {"users": users, "invites": invites})


@login_required
def music_fragment(request, page):
    """Delegate /fragments/<page> to the music app (SPA fragments)."""
    from apps.music.views import fragment
    return fragment(request, page)
