/* Pingu Service Worker v5 */
const CACHE = 'pingu-v5';
const MUSIC_CACHE = 'pingu-music-v1';
const MUSIC_CACHE_MAX_BYTES = 800 * 1024 * 1024; // 800 MB hard cap

self.addEventListener('install', e => e.waitUntil(self.skipWaiting()));
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        // Borrar todas las cachés legacy excepto la nueva versión y la de música.
        keys.filter(k => k !== CACHE && k !== MUSIC_CACHE).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// ── Mensajes desde la página (limpiar caché de música) ────────────────────
self.addEventListener('message', e => {
  if (!e.data) return;
  if (e.data.type === 'clear-music-cache') {
    e.waitUntil(
      caches.delete(MUSIC_CACHE).then(() => {
        e.source && e.source.postMessage({ type: 'music-cache-cleared' });
      })
    );
  }
});

// ── Streaming de música: cachea el archivo completo, sirve Range desde caché ──
function parseRange(header, total) {
  const m = /bytes=(\d+)-(\d*)/.exec(header || '');
  if (!m) return null;
  const start = parseInt(m[1], 10);
  const end = m[2] ? Math.min(parseInt(m[2], 10), total - 1) : total - 1;
  if (isNaN(start) || start < 0 || start > end) return null;
  return { start, end };
}

async function sliceFromCache(cached, request) {
  const buffer = await cached.arrayBuffer();
  const total = buffer.byteLength;
  const contentType = cached.headers.get('Content-Type') || 'audio/mpeg';
  const range = parseRange(request.headers.get('Range'), total);
  if (!range) {
    return new Response(buffer, {
      status: 200,
      headers: {
        'Content-Type': contentType,
        'Content-Length': String(total),
        'Accept-Ranges': 'bytes',
        'X-Pingu-Cache': 'HIT',
      },
    });
  }
  const slice = buffer.slice(range.start, range.end + 1);
  return new Response(slice, {
    status: 206,
    statusText: 'Partial Content',
    headers: {
      'Content-Type': contentType,
      'Content-Length': String(slice.byteLength),
      'Content-Range': `bytes ${range.start}-${range.end}/${total}`,
      'Accept-Ranges': 'bytes',
      'X-Pingu-Cache': 'HIT',
    },
  });
}

async function trimMusicCache() {
  try {
    const cache = await caches.open(MUSIC_CACHE);
    const reqs = await cache.keys();
    if (reqs.length === 0) return;
    let total = 0;
    const sized = [];
    for (const r of reqs) {
      const resp = await cache.match(r);
      const len = parseInt(resp?.headers.get('Content-Length') || '0', 10);
      sized.push({ req: r, len });
      total += len;
    }
    if (total <= MUSIC_CACHE_MAX_BYTES) return;
    for (const { req, len } of sized) {
      await cache.delete(req);
      total -= len;
      if (total <= MUSIC_CACHE_MAX_BYTES) break;
    }
  } catch (_) { /* fail-quiet */ }
}

async function handleMusicStream(event) {
  const request = event.request;
  const url = new URL(request.url);
  url.search = '';
  const cacheKey = new Request(url.toString(), { method: 'GET' });
  const cache = await caches.open(MUSIC_CACHE);
  const cached = await cache.match(cacheKey);
  if (cached) return sliceFromCache(cached, request);

  event.waitUntil((async () => {
    try {
      const fullResp = await fetch(cacheKey, { cache: 'no-store' });
      if (fullResp.ok && fullResp.status === 200) {
        await cache.put(cacheKey, fullResp.clone());
        await trimMusicCache();
      }
    } catch (_) {}
  })());

  return fetch(request);
}

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith('/api/stream/')) {
    e.respondWith(handleMusicStream(e));
    return;
  }

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
