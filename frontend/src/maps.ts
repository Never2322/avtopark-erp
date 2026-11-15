// Карта 2ГИС: ссылка без ключа и загрузка скрипта карты.
// Ссылка «Открыть в 2ГИС» работает всегда и ничего не требует. Живая карта на странице рисуется скриптом 2ГИС и нужна только разделу «Карта».

/** Поиск адреса на сайте 2ГИС (новая вкладка). */
export const twogisSearch = (text: string) => `https://2gis.ru/search/${encodeURIComponent(text.trim())}`;

/* eslint-disable @typescript-eslint/no-explicit-any */
export const MAPGL_URL = "https://mapgl.2gis.com/api/js/v1";
let loading: Promise<any> | null = null;

/** Подключает скрипт карты один раз за сеанс. При неудаче следующая попытка начинает заново. */
export function loadMapgl(): Promise<any> {
  const w = window as any;
  if (w.mapgl) return Promise.resolve(w.mapgl);
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = MAPGL_URL;
      s.async = true;
      s.onload = () => (w.mapgl ? resolve(w.mapgl) : reject(new Error("mapgl не появился")));
      s.onerror = () => { loading = null; s.remove(); reject(new Error("скрипт не загрузился")); };
      document.head.appendChild(s);
    });
  }
  return loading;
}
