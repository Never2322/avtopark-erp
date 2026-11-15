// Связь с служебным скриптом (sw.js): регистрация и очистка сохранённых данных при выходе или смене человека.
export function registerOfflineWorker() {
  if (!("serviceWorker" in navigator) || !import.meta.env.PROD) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => { /* без скрипта программа работает как обычный сайт */ });
  });
}

/** Стирает из телефона сохранённые данные прошлого человека. keepMe: оставить только «кто я» (при обновлении страницы). */
export function clearOfflineData(keepMe = false) {
  try { navigator.serviceWorker?.controller?.postMessage({ type: "clear-data", keepMe }); } catch { /* нет скрипта: нечего стирать */ }
}

const UID_KEY = "erp.uid";
/** Запоминает, чьи данные лежат в телефоне; если вошёл другой человек, прошлые стираются. */
export function rememberUser(id: number | null, fromLogin: boolean) {
  try {
    const prev = localStorage.getItem(UID_KEY);
    if (id === null) { localStorage.removeItem(UID_KEY); clearOfflineData(); return; }
    if (prev !== String(id)) { localStorage.setItem(UID_KEY, String(id)); clearOfflineData(!fromLogin); }
  } catch { /* хранилище недоступно */ }
}
