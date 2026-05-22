/* push.js — BaluHome Web Push subscription */
(function () {
  'use strict';

  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;

  async function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = atob(base64);
    return Uint8Array.from([...rawData].map(c => c.charCodeAt(0)));
  }

  async function subscribe() {
    try {
      const reg = await navigator.serviceWorker.ready;
      const existing = await reg.pushManager.getSubscription();
      if (existing) return; // ya suscrito

      const keyRes = await fetch('/api/push/vapid-key').then(r => r.json());
      if (!keyRes.key) return;

      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: await urlBase64ToUint8Array(keyRes.key),
      });

      const json = sub.toJSON();
      await fetch('/api/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          endpoint: json.endpoint,
          p256dh: json.keys.p256dh,
          auth: json.keys.auth,
        }),
      });
    } catch (e) {
      // Silently ignore — push is optional
    }
  }

  // Ask for permission and subscribe after a short delay (not to interrupt UX)
  function init() {
    if (Notification.permission === 'denied') return;
    if (Notification.permission === 'granted') {
      subscribe();
    } else {
      // Wait 10s before asking, so the user has settled into the app
      setTimeout(async () => {
        const perm = await Notification.requestPermission();
        if (perm === 'granted') subscribe();
      }, 10000);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
