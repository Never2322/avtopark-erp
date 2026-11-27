// Три темы: чёрная (по умолчанию, как было раньше), светлая и багровая. Выбор хранится в браузере.
// Первый кадр рисует маленький скрипт в index.html, чтобы страница не мигала тёмным перед другой темой.
import { useSyncExternalStore } from "react";

export type Theme = "dark" | "light" | "crimson";
export const THEMES: { id: Theme; label: string }[] = [{ id: "dark", label: "Чёрная" }, { id: "light", label: "Светлая" }, { id: "crimson", label: "Багровая" }];
const KEY = "erp-theme";
const COLOR: Record<Theme, string> = { dark: "#040915", light: "#EDF2FA", crimson: "#0D0207" };
const listeners = new Set<() => void>();

function stored(): Theme {
  try { const v = localStorage.getItem(KEY); return v === "light" || v === "crimson" ? v : "dark"; } catch { return "dark"; }
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

export function useTheme(): [Theme, (t: Theme) => void] {
  const t = useSyncExternalStore((cb) => { listeners.add(cb); return () => { listeners.delete(cb); }; }, () => current, () => "dark" as Theme);
  return [t, setTheme];
}
