// Общие кусочки экранов «Мои маршруты» (водитель) и «Маршруты по дням» (директор, диспетчер).
import { plural } from "../format";

/** Прибавляет дни к дате «ГГГГ-ММ-ДД» без сдвига из-за часового пояса. */
export const shiftDay = (iso: string, n: number): string => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};

/** «среда, 18 ноября». */
export const longDay = (iso: string): string =>
  new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" }).format(new Date(`${iso}T00:00:00Z`));

export const streetsWord = (n: number) => `${n} ${plural(n, "улица", "улицы", "улиц")}`;

/** Перечень улиц маршрута: нумерованный список. Свёрнут в «Улицы (N)», если open не задан. */
export function Streets({ streets, open = false }: { streets: string[]; open?: boolean }) {
  if (!streets.length) return <p className="muted small rt-nostreets">Улицы не указаны</p>;
  return (
    <details className="rt-streets" open={open}>
      <summary>{streetsWord(streets.length)}<span className="rt-peek muted small">{streets.slice(0, 2).join(" · ")}{streets.length > 2 ? " …" : ""}</span></summary>
      <ol>{streets.map((s, i) => <li key={`${i}-${s}`}>{s}</li>)}</ol>
    </details>
  );
}
