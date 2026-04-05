// StudyTogether Service Worker v3
// Push notifications + Network-first + Stale-while-revalidate

const CACHE_VERSION = 'v3';
const CACHE_NAME    = `studytogether-${CACHE_VERSION}`;

const STATIC_CACHE = [
  '/static/css/style.css',
];

// ── INSTALL ───────────────────────────────────────────────────────────────────
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      cache.addAll(STATIC_CACHE).catch(() => {})
    )
  );
});

// ── ACTIVATE ──────────────────────────────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    Promise.all([
      caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
      ),
      clients.claim()
    ])
  );
});

// ── FETCH ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  if (!url.protocol.startsWith('http') || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/')) { event.respondWith(fetch(request)); return; }
  if (url.pathname === '/sw.js' || url.pathname === '/manifest.json') { event.respondWith(fetch(request)); return; }
  if (request.destination === 'style' || request.destination === 'script') { event.respondWith(staleWhileRevalidate(request)); return; }
  event.respondWith(networkFirst(request));
});

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    return cached || new Response('Offline — abra o app conectado primeiro.', {
      status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' }
    });
  }
}

async function staleWhileRevalidate(request) {
  const cache  = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  const fetchPromise = fetch(request).then(response => {
    if (response.ok) cache.put(request, response.clone()).catch(() => {});
    return response;
  }).catch(() => null);
  return cached || fetchPromise;
}

// ── PUSH NOTIFICATIONS ────────────────────────────────────────────────────────
self.addEventListener('push', event => {
  if (!event.data) return;
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      tag: 'partner-status',
      renotify: true,
      data: { url: '/dashboard' }
    })
  );
});

// Ao clicar na notificação, abre o app
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow('/dashboard');
    })
  );
});

// ── MENSAGENS ─────────────────────────────────────────────────────────────────
self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});
