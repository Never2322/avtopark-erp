// Светлая и тёмная тема. Выбор хранится в браузере; по умолчанию тёмная, как было раньше.
// Первый кадр рисует маленький скрипт в index.html, чтобы страница не мигала тёмным перед светлой.
import { useSyncExternalStore } from "react";

export type Theme = "dark" | "light";
const KEY = "erp-theme";
const COLOR: Record<Theme, string> = { dark: "#0B1115", light: "#F2F5F6" };
const listeners = new Set<() => void>();

function stored(): Theme {
  try { return localStorage.getItem(KEY) === "light" ? "light" : "dark"; } catch { return "dark"; }
}

let current: Theme = stored();

function apply(t: Theme) {
  document.documentElement.dataset.theme = t;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", COLOR[t]);
}

export function initTheme() {
  apply(current);
  // выбор, сделанный в другой вкладке, подхватывается сразу
  window.addEventListener("storage", (e) => {
    if (e.key !== KEY) return;
    current = stored();
    apply(current);
    listeners.forEach((f) => f());
  });
}

export function setTheme(t: Theme) {
  current = t;
  try { localStorage.setItem(KEY, t); } catch { /* хранилище закрыто: тема продержится до закрытия вкладки */ }
  apply(t);
  listeners.forEach((f) => f());
}

export function useTheme(): [Theme, () => void] {
  const t = useSyncExternalStore((cb) => { listeners.add(cb); return () => { listeners.delete(cb); }; }, () => current, () => "dark" as Theme);
  return [t, () => setTheme(t === "dark" ? "light" : "dark")];
}
