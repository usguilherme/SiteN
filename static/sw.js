// StudyTogether Service Worker
// Estratégia: Network-first para tudo → sempre atualizado
// Static assets: stale-while-revalidate (carrega rápido, atualiza em background)

const CACHE_VERSION = 'v2';
const CACHE_NAME    = `studytogether-${CACHE_VERSION}`;

const STATIC_CACHE = [
  '/static/css/style.css',
];

// ── INSTALL: cache assets estáticos ──────────────────────────────────────────
self.addEventListener('install', event => {
  self.skipWaiting(); // Ativa o novo SW imediatamente, sem esperar abas fecharem
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      cache.addAll(STATIC_CACHE).catch(() => {})
    )
  );
});

// ── ACTIVATE: limpa caches antigos e assume controle ─────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    Promise.all([
      // Limpar versões antigas do cache
      caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
      ),
      // Tomar controle de todas as abas abertas imediatamente
      clients.claim()
    ])
  );
});

// ── FETCH: interceptar requisições ───────────────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Ignorar requisições não-HTTP e de outros domínios (ex: Google Fonts)
  if (!url.protocol.startsWith('http') || url.origin !== location.origin) return;

  // API calls → SEMPRE da rede (nunca cachear dados dinâmicos)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(request));
    return;
  }

  // Service worker e manifest → SEMPRE da rede
  if (url.pathname === '/sw.js' || url.pathname === '/manifest.json') {
    event.respondWith(fetch(request));
    return;
  }

  // CSS/JS estáticos → Stale-while-revalidate (rápido + sempre atualiza)
  if (request.destination === 'style' || request.destination === 'script') {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  // Páginas HTML → Network-first (sempre a versão mais recente)
  // Se offline, cai para o cache
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
    return cached || new Response('Offline — abra o app conectado ao Wi-Fi primeiro.', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' }
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

// ── MENSAGENS: forçar atualização ─────────────────────────────────────────────
self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});
