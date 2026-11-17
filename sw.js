/* Quietvox Sipariş Takip — Service Worker
 *
 * Strateji:
 *  - Uygulama kabuğu (index.html, manifest, ikonlar): önce önbellek (cache-first),
 *    arka planda sessizce güncellenir; böylece yeni sürüm bir sonraki açılışta gelir.
 *  - data.json: önce ağ (network-first); çevrimdışıysa son kaydedilen kopya.
 *  - Font ve QR kütüphanesi (CDN): önce önbellek, arka planda güncelle.
 *  - Diğer tüm istekler: dokunulmaz (tarayıcı kendi halleder).
 *
 * Tüm yollar görelidir; site bir alt yolda (ör. /repo/) yayınlansa da çalışır.
 */
'use strict';

// Kabukta değişiklik yapınca bu sürümü artırın: eski önbellekler silinir.
const VERSION = 'b12';
const SHELL_CACHE = 'qv-shell-' + VERSION;
const DATA_CACHE = 'qv-data-' + VERSION;
const CDN_CACHE = 'qv-cdn-' + VERSION;
const CURRENT_CACHES = [SHELL_CACHE, DATA_CACHE, CDN_CACHE];

const SHELL_FILES = [
  './',
  './index.html',
  './manifest.webmanifest',
  './icons/favicon-32.png',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable-192.png',
  './icons/icon-maskable-512.png',
  './icons/apple-touch-icon.png'
];

const CDN_HOSTS = ['fonts.googleapis.com', 'fonts.gstatic.com', 'cdnjs.cloudflare.com'];

// data.json her zaman aynı anahtarla saklanır (?t=... parametresi yok sayılır)
const DATA_KEY = new URL('./data.json', self.registration.scope).href;
const INDEX_KEY = new URL('./index.html', self.registration.scope).href;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_FILES))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names
          .filter((name) => name.startsWith('qv-') && !CURRENT_CACHES.includes(name))
          .map((name) => caches.delete(name))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('message', (event) => {
  // Uygulamadaki "Güncelle" düğmesi { type: 'SKIP_WAITING' } gönderir; eski biçimler de kabul edilir
  const data = event.data;
  if (data && (data.type === 'SKIP_WAITING' || data.action === 'skipWaiting' || data === 'skipWaiting')) {
    self.skipWaiting();
  }
});

// Yanıt önbelleğe yazılabilir mi? (başarılı ya da CDN'den gelen opak yanıt)
function isCacheable(response) {
  return Boolean(response) && (response.ok || response.type === 'opaque');
}

// Önce ağ; olmazsa önbellek (data.json için)
async function networkFirstData(request) {
  const cache = await caches.open(DATA_CACHE);
  try {
    const targetUrl = typeof request === 'string' ? request : (request.url || DATA_KEY);
    const response = await fetch(targetUrl, { cache: 'no-cache' });
    if (response && response.ok) {
      await cache.put(DATA_KEY, response.clone());
      return response;
    }
    const cached = await cache.match(DATA_KEY);
    if (cached) return cached;
    return response;
  } catch (err) {
    const cached = await cache.match(DATA_KEY);
    if (cached) return cached;
    throw err;
  }
}

// Önce ağ; olmazsa önbellekteki sayfa (uygulamanın kendisi için)
async function networkFirstShell(request) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const response = await fetch(request, { cache: 'no-store' });
    if (response.ok) {
      await cache.put(INDEX_KEY, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await cache.match(INDEX_KEY);
    if (cached) return cached;
    throw err;
  }
}

// Önce önbellek; arka planda güncelle (kabuk ve CDN için)
async function cacheFirstWithRefresh(event, cacheName, cacheKey, fallbackKey) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(cacheKey, { ignoreSearch: cacheName === SHELL_CACHE });

  const refresh = fetch(event.request)
    .then(async (response) => {
      if (isCacheable(response)) {
        await cache.put(cacheKey, response.clone());
      }
      return response;
    });

  if (cached) {
    // Arka plan güncellemesi başarısız olabilir (çevrimdışı); sorun değil.
    event.waitUntil(refresh.catch(() => undefined));
    return cached;
  }

  try {
    return await refresh;
  } catch (err) {
    const fallback = fallbackKey ? await cache.match(fallbackKey) : undefined;
    if (fallback) return fallback;
    throw err;
  }
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  let url;
  try {
    url = new URL(request.url);
  } catch (err) {
    return;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return;

  const sameOrigin = url.origin === self.location.origin;
  const inScope = sameOrigin && request.url.startsWith(self.registration.scope);

  if (inScope) {
    // version.json her zaman doğrudan ağdan alınır (önbelleğe alınmaz)
    if (url.pathname.endsWith('/version.json')) {
      return;
    }
    if (url.origin + url.pathname === DATA_KEY) {
      event.respondWith(networkFirstData(request));
      return;
    }
    if (request.mode === 'navigate') {
      // Sayfa açılışı: önce ağ (güncellemeler hemen görünsün); bağlantı yoksa önbellekteki index.html
      event.respondWith(networkFirstShell(request));
      return;
    }
    if (url.pathname === new URL(self.registration.scope).pathname + 'sw.js') return;
    event.respondWith(cacheFirstWithRefresh(event, SHELL_CACHE, url.origin + url.pathname, null));
    return;
  }

  if (CDN_HOSTS.includes(url.hostname)) {
    event.respondWith(cacheFirstWithRefresh(event, CDN_CACHE, request.url, null));
  }
  // Diğer dış istekler: karışma.
});
