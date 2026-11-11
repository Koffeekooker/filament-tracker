// =====================================================================
// SERVICE WORKER
// ---------------------------------------------------------------------
// A service worker is a separate script the browser installs alongside the
// page. It sits between the app and the network like a small proxy: every
// file request of the app passes through its "fetch" handler. We use it to
// keep a copy of the app files in a local cache, so the app
//   - starts without internet, and
//   - can be installed as an app (Chrome requires a service worker for that).
//
// It has no access to the page (no DOM, no buttons) – it only handles
// requests. "self" is the worker itself; addEventListener registers our
// handlers for its life-cycle events: install -> activate -> fetch.
//
// UPDATES: when index.html changes, raise CACHE (filament-v8 -> v9).
// The browser compares sw.js byte by byte; a changed file triggers
// install/activate of the new version, which drops the old cache.
// =====================================================================
const CACHE = "filament-v14";
// Files stored for offline use ("./" = the folder itself, i.e. the start page).
const FILES = ["./", "./index.html", "./manifest.webmanifest", "./icon-192.png", "./icon-512.png"];

// INSTALL: download all FILES into a new cache named CACHE.
// e.waitUntil(promise) tells the browser "not finished until this promise
// resolves". skipWaiting() = activate immediately instead of waiting until
// every open app window is closed.
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});

// ACTIVATE: delete all caches with a different name (= older versions),
// then take control of already open app windows (clients.claim).
self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// FETCH: called for every request the app makes. Strategy "network first":
//   1. try the network (so a new version arrives as soon as you're online),
//      and store a copy of successful answers in the cache;
//   2. if the network fails (offline), answer from the cache.
// Not intercepted (returning without respondWith = browser handles it normally):
//   non-GET requests and /api/… calls to the optional Termux backup server.
// .then()/.catch() is the promise chain notation: "when done, do this;
// on error, do that" – the same as await + try/catch, written differently.
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.includes("/api/")) return;
  e.respondWith(
    fetch(e.request)
      .then(r => {
        if (r.ok && url.origin === location.origin) {
          const copy = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
        }
        return r;
      })
      .catch(() => caches.match(e.request, { ignoreSearch: true }))
  );
});
