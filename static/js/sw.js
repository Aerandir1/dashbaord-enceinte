// Service Worker de l'enceinte.
//
// But : au lancement, peindre la coquille (page + écran de chargement)
// instantanément depuis le cache, avant même le premier aller-retour réseau.
//
// NOTE : ne s'active qu'en contexte sécurisé (HTTPS ou localhost). En HTTP sur
// le LAN, le navigateur refuse de l'enregistrer -> ce fichier reste inerte
// jusqu'à un passage en HTTPS (voir le reverse proxy nginx dans deploy/).

const CACHE = 'enceinte-shell-v1';

// La coquille : tout ce qui est nécessaire pour peindre l'écran de chargement
// puis l'ossature de l'appli. Jamais de données (/api/...) ici.
const SHELL = [
  '/',
  '/static/css/custom.css',
  '/static/js/dashboard.js',
  '/static/js/eq-editor.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  // Purge des anciens caches (changement de version).
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // Données temps réel : jamais servies depuis le cache.
  if (url.pathname.startsWith('/api/')) return;

  // Navigation : réseau d'abord (toujours frais), coquille en cache en secours.
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match('/')));
    return;
  }

  // Ressources statiques : cache immédiat + rafraîchissement en arrière-plan
  // (stale-while-revalidate) — peinture instantanée sans figer les mises à jour.
  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
