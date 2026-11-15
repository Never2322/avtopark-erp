/* Служебный скрипт приложения: хранит саму программу в телефоне, чтобы она открывалась без связи,
   и последние данные водителя (главная, наряды, записи), чтобы их можно было посмотреть.
   Записи, сделанные без связи, сюда не попадают: их хранит и отправляет очередь внутри программы (outbox).
   Скрипт работает только по https:// и на localhost, так устроены браузеры; без него программа работает как обычный сайт. */
var BUILD = "__BUILD__";
var SHELL = "erp-shell-" + BUILD;
var DATA = "erp-data";
var FILES = __FILES__;
var DATA_PATHS = [/^\/api\/auth\/me$/, /^\/api\/driver\/home$/, /^\/api\/entries\/mine$/, /^\/api\/jobs\/mine$/];
var WAIT_MS = 5000;

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(SHELL).then(function (c) {
    return c.addAll(["/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"].concat(FILES).map(function (u) { return new Request(u, { cache: "reload" }); }));
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k.indexOf("erp-shell-") === 0 && k !== SHELL; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

/* Выход или смена человека на телефоне: чужие данные в телефоне не остаются. */
self.addEventListener("message", function (e) {
  var m = e.data || {};
  if (m.type !== "clear-data") { return; }
  e.waitUntil(m.keepMe ? caches.open(DATA).then(function (c) {
    return c.keys().then(function (reqs) {
      return Promise.all(reqs.filter(function (r) { return new URL(r.url).pathname !== "/api/auth/me"; }).map(function (r) { return c.delete(r); }));
    });
  }) : caches.delete(DATA));
});

function withTimeout(req) {
  var ctl = typeof AbortController === "function" ? new AbortController() : null;
  var timer = ctl ? setTimeout(function () { ctl.abort(); }, WAIT_MS) : null;
  return fetch(req, ctl ? { signal: ctl.signal } : undefined).then(function (res) { if (timer) { clearTimeout(timer); } return res; },
    function (err) { if (timer) { clearTimeout(timer); } throw err; });
}

/* Данные: сначала сеть; нет связи или сервер лежит — последнее сохранённое. Отказ в доступе (401, 403) подменять нельзя. */
function data(req) {
  return withTimeout(req).then(function (res) {
    if (res.ok) {
      var copy = res.clone();
      caches.open(DATA).then(function (c) { c.put(req, copy); });
      return res;
    }
    if (res.status < 500) { return res; }
    return caches.open(DATA).then(function (c) { return c.match(req); }).then(function (hit) { return hit || res; });
  }, function () {
    return caches.open(DATA).then(function (c) { return c.match(req); }).then(function (hit) { return hit || Response.error(); });
  });
}

/* Страницы: любой адрес программы открывается из сохранённой оболочки, а нужную страницу рисует сама программа. */
function page(req) {
  return withTimeout(req).then(function (res) {
    if (res.ok && !res.redirected) {
      var copy = res.clone();
      caches.open(SHELL).then(function (c) { c.put("/", copy); });
    }
    return res;
  }, function () {
    return caches.open(SHELL).then(function (c) { return c.match("/"); }).then(function (hit) {
      return hit || new Response("Нет связи, а программа ещё не сохранена в телефоне. Откройте её один раз с интернетом.", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
    });
  });
}

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") { return; }
  var url = new URL(req.url);
  if (url.origin !== location.origin) { return; }
  if (url.pathname.indexOf("/api/") === 0) {
    if (DATA_PATHS.some(function (re) { return re.test(url.pathname); })) { e.respondWith(data(req)); }
    return;
  }
  if (req.mode === "navigate") { e.respondWith(page(req)); return; }
  e.respondWith(caches.open(SHELL).then(function (c) {
    return c.match(req).then(function (hit) {
      if (hit) { return hit; }
      return fetch(req).then(function (res) {
        if (res.ok && url.pathname.indexOf("/assets/") === 0) { c.put(req, res.clone()); }
        return res;
      });
    });
  }));
});
