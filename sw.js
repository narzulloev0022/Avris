/* Avris AI — service worker.
 *
 * Strategy (medical data first):
 *  - /api/*            NETWORK ONLY. PHI is never written to Cache Storage.
 *  - navigations (/)   network-first, offline fallback to the cached shell —
 *                      the SPA's Demo Mode then keeps the app usable offline.
 *  - same-origin static (css/js/svg/png) stale-while-revalidate.
 *  - cross-origin (fonts, CDN) untouched — browser HTTP cache handles them.
 *
 * SW_VERSION must be bumped together with the ?v= asset version on deploy —
 * activation drops every old cache. /sw.js itself is served with
 * Cache-Control: no-cache so updates propagate within one page load.
 */
"use strict";

var SW_VERSION = "v45";
var SHELL_CACHE = "avris-shell-" + SW_VERSION;
var STATIC_CACHE = "avris-static-" + SW_VERSION;

var SHELL_URLS = [
  "/",
  "/app",
  "/styles.css?v=42",
  "/app.js?v=42",
  "/assets/favicon-hyperion.svg",
  "/assets/logo.svg",
  "/manifest.json",
  "/assets/vendor/anime.umd.min.js",
  "/assets/vendor/motion.min.js",
  "/assets/vendor/gsap/gsap.min.js",
  "/assets/vendor/gsap/CustomEase.min.js",
  "/assets/vendor/gsap/CustomBounce.min.js",
  "/assets/vendor/gsap/CustomWiggle.min.js",
  "/assets/vendor/gsap/CSSRulePlugin.min.js",
  "/assets/vendor/gsap/Draggable.min.js",
  "/assets/vendor/gsap/DrawSVGPlugin.min.js",
  "/assets/vendor/gsap/EasePack.min.js",
  "/assets/vendor/gsap/Flip.min.js",
  "/assets/vendor/gsap/InertiaPlugin.min.js",
  "/assets/vendor/gsap/MorphSVGPlugin.min.js",
  "/assets/vendor/gsap/MotionPathPlugin.min.js",
  "/assets/vendor/gsap/Observer.min.js",
  "/assets/vendor/gsap/Physics2DPlugin.min.js",
  "/assets/vendor/gsap/PhysicsPropsPlugin.min.js",
  "/assets/vendor/gsap/ScrambleTextPlugin.min.js",
  "/assets/vendor/gsap/ScrollTrigger.min.js",
  "/assets/vendor/gsap/ScrollSmoother.min.js",
  "/assets/vendor/gsap/ScrollToPlugin.min.js",
  "/assets/vendor/gsap/SplitText.min.js",
  "/assets/vendor/gsap/TextPlugin.min.js"
];

self.addEventListener("install", function (e) {
  e.waitUntil(
    caches.open(SHELL_CACHE).then(function (cache) {
      return cache.addAll(SHELL_URLS);
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        if (k !== SHELL_CACHE && k !== STATIC_CACHE) return caches.delete(k);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;

  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // fonts/CDN — untouched

  // Medical data: never cached, no offline fallback with stale PHI.
  if (url.pathname.indexOf("/api/") === 0) return;

  // App navigations: fresh HTML when online, cached shell when offline.
  // Each page is cached under its own URL — a visit to /lab or the marketing
  // root must never overwrite the app shell (that broke offline Demo Mode).
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req).then(function (resp) {
        var copy = resp.clone();
        caches.open(SHELL_CACHE).then(function (c) { c.put(url.pathname, copy); });
        return resp;
      }).catch(function () {
        return caches.match(url.pathname, { cacheName: SHELL_CACHE }).then(function (own) {
          return own ||
            caches.match("/app", { cacheName: SHELL_CACHE }).then(function (app) {
              return app || caches.match("/", { cacheName: SHELL_CACHE });
            });
        });
      })
    );
    return;
  }

  // Static assets: stale-while-revalidate.
  e.respondWith(
    caches.match(req).then(function (cached) {
      var network = fetch(req).then(function (resp) {
        if (resp && resp.status === 200) {
          var copy = resp.clone();
          caches.open(STATIC_CACHE).then(function (c) { c.put(req, copy); });
        }
        return resp;
      }).catch(function () { return cached; });
      return cached || network;
    })
  );
});
