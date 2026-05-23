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
    return render(request, "home.html", {
        "pending_friends": pending_friends,
        "shopping_count": shopping_count,
        "todos_count": todos_count,
        "unread_msgs": unread_msgs,
        "videos_count": videos_count,
    })


def service_worker(request):
    """Sirve sw.js dinámicamente con la versión actual."""
    # Bump del CACHE name fuerza a clients a invalidar lo que tenían (importante
    # cuando cambian iconos/manifest/CSS).
    body = """/* Pingu Service Worker v4 */
const CACHE = 'pingu-v4';

self.addEventListener('install', e => e.waitUntil(self.skipWaiting()));
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/fragments/')) return;
  const isHtml = e.request.headers.get('Accept')?.includes('text/html');
  const isCss = url.pathname.endsWith('.css');
  if (isHtml || isCss) {
    e.respondWith(
      fetch(e.request, { cache: 'no-store' })
        .then(res => {
          if (res.ok) caches.open(CACHE).then(c => c.put(e.request, res.clone()));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then(cached =>
      cached || fetch(e.request).then(res => {
        if (res.ok) caches.open(CACHE).then(c => c.put(e.request, res.clone()));
        return res;
      })
    )
  );
});

self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : {};
  const title = data.title || 'Pingu';
  const opts = {
    body: data.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    data: { url: data.url || '/home/' },
    tag: data.tag,
  };
  e.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const target = e.notification.data?.url || '/home/';
  e.waitUntil(clients.matchAll({type:'window'}).then(list => {
    for (const c of list) { if (c.url.endsWith(target) && 'focus' in c) return c.focus(); }
    if (clients.openWindow) return clients.openWindow(target);
  }));
});
"""
    return HttpResponse(body, content_type="application/javascript")


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
    return render(request, "admin_panel.html", {"users": users, "invites": invites})


@login_required
def music_fragment(request, page):
    """Delegate /fragments/<page> to the music app (SPA fragments)."""
    from apps.music.views import fragment
    return fragment(request, page)
