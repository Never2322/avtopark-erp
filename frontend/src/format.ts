// Даты показываются в часовом поясе организации; на сервере всё хранится в UTC.
let tz = "Asia/Novosibirsk";
export const setTimezone = (t: string) => {
  tz = t || tz;
};

const dateFmt = () => new Intl.DateTimeFormat("ru-RU", { timeZone: tz, day: "2-digit", month: "2-digit", year: "numeric" });
const timeFmt = () => new Intl.DateTimeFormat("ru-RU", { timeZone: tz, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });

export const fmtDate = (iso?: string | null): string => {
  if (!iso) return "—";
  // чистая дата без времени не должна сдвигаться из-за часового пояса
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso.split("-").reverse().join(".");
  return dateFmt().format(new Date(iso));
};
export const fmtDateTime = (iso?: string | null): string => (iso ? timeFmt().format(new Date(iso)).replace(",", "") : "—");

export const fmtNum = (v?: number | null, digits = 1): string =>
  v === null || v === undefined ? "—" : new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(v);

/** Деньги: 1 234,50. Валюту рисует сам экран (₽ в заголовке колонки). */
export const fmtMoney = (v?: number | null): string =>
  v === null || v === undefined ? "—" : new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);

export const fmtSize = (bytes?: number | null): string => {
  if (!bytes) return "—";
  if (bytes < 1024) return bytes + " Б";
  if (bytes < 1024 * 1024) return fmtNum(bytes / 1024, 0) + " КБ";
  return fmtNum(bytes / 1024 / 1024, 1) + " МБ";
};

export const todayIso = (): string => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

export const plural = (n: number, one: string, few: string, many: string): string => {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
};

export const readingUnit = (kind: string): string => (kind === "km" ? "км" : "м·ч");

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
/** «12 марта 2026 г.» для печатных форм. */
export const fmtDateLong = (iso?: string | null): string => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]} г.` : "—";
};
