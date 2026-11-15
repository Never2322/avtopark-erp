// Очередь отправки: записи, сделанные без связи, лежат в телефоне (IndexedDB) и уходят сами, когда сеть вернулась.
//
// В очередь попадают рейсы, заправки (вместе с фото), отметки «приехал» и «закончил» и фото к нарядам.
// Повторная отправка безопасна: рейсы и заправки уходят со своим кодом (client_id), отметки повторять можно,
// одинаковое фото к наряду сервер второй раз не добавляет. Время отметки запоминается в момент нажатия.
// Очередь привязана к человеку: на общем телефоне чужие записи не видны и не отправляются от чужого имени.
import { useSyncExternalStore } from "react";
import { api, ApiError, get, setCsrf } from "./api";

export type OutboxKind = "trip" | "fuel" | "job_arrive" | "job_finish" | "job_photos";

export interface OutboxItem {
  id?: number;
  uid: number;
  kind: OutboxKind;
  title: string;                           // как показать человеку: «Рейс · А101АА · Склад — карьер»
  url: string;                             // адрес без «/api»
  json?: unknown;                          // тело запроса, если файлов нет
  fields?: Record<string, string>;         // поля формы, если есть файлы
  files?: { name: string; blob: Blob }[];
  job_id?: number;
  saved_at: string;
  tries: number;
  error?: string;                          // сервер отказал: запись остаётся в списке, пока человек её не уберёт
}

export type NewItem = Omit<OutboxItem, "id" | "uid" | "saved_at" | "tries" | "error">;

const DB = "erp-outbox", STORE = "items", SEND_TIMEOUT_MS = 60_000;

/* ---------------- хранилище ---------------- */

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") return reject(new Error("no-idb"));
    const rq = indexedDB.open(DB, 1);
    rq.onupgradeneeded = () => rq.result.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
    rq.onsuccess = () => resolve(rq.result);
    rq.onerror = () => reject(rq.error);
  });
}

async function run<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  try {
    return await new Promise<T>((resolve, reject) => {
      const t = db.transaction(STORE, mode);
      const rq = fn(t.objectStore(STORE));
      t.oncomplete = () => resolve(rq.result);
      t.onerror = t.onabort = () => reject(t.error);
    });
  } finally {
    db.close();
  }
}

const readAll = () => run("readonly", (s) => s.getAll() as IDBRequest<OutboxItem[]>);
const putItem = (it: OutboxItem) => run("readwrite", (s) => s.put(it));
const dropItem = (id: number) => run("readwrite", (s) => s.delete(id));

/* ---------------- состояние ---------------- */

interface Snapshot { items: OutboxItem[]; flushing: boolean }
let snap: Snapshot = { items: [], flushing: false };
let uid: number | null = null;
const listeners = new Set<() => void>();
const set = (next: Partial<Snapshot>) => { snap = { ...snap, ...next }; listeners.forEach((f) => f()); };
const subscribe = (f: () => void) => { listeners.add(f); return () => { listeners.delete(f); }; };

export const useOutbox = () => useSyncExternalStore(subscribe, () => snap);

async function reload() {
  try {
    const all = await readAll();
    set({ items: all.filter((i) => i.uid === uid).sort((a, b) => (a.id ?? 0) - (b.id ?? 0)) });
  } catch {
    set({ items: [] });
  }
}

/** Кто сейчас вошёл: очередь показывает и отправляет только его записи. */
export function setOutboxUser(id: number | null) {
  uid = id;
  void reload();
}

export const outboxAvailable = () => typeof indexedDB !== "undefined" && uid !== null;

/* ---------------- добавление и отправка ---------------- */

export async function enqueue(item: NewItem): Promise<void> {
  if (uid === null) throw new Error("no-user");
  await putItem({ ...item, uid, saved_at: new Date().toISOString(), tries: 0 });
  await reload();
  void flush();
}

export async function discard(id: number) {
  await dropItem(id);
  await reload();
}

type Outcome = "ok" | "rejected" | "retry" | "server" | "auth";
const MAX_SERVER_FAILS = 8;          // сервер отвечает ошибкой снова и снова: так очередь не должна встать навсегда

function body(it: OutboxItem): unknown {
  if (!it.fields && !it.files?.length) return it.json ?? {};
  const f = new FormData();
  for (const [k, v] of Object.entries(it.fields ?? {})) f.set(k, v);
  for (const x of it.files ?? []) f.append("files", x.blob, x.name);
  return f;
}

async function send(it: OutboxItem): Promise<Outcome> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), SEND_TIMEOUT_MS);
  try {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await api("POST", it.url, body(it), undefined, ctl.signal);
        return "ok";
      } catch (e) {
        if (!(e instanceof ApiError)) return "retry";
        if (e.status === 403 && e.code === "csrf" && attempt === 0) {         // токен защиты устарел: берём свежий и пробуем ещё раз
          try { setCsrf((await get<{ csrf_token: string }>("/auth/me")).csrf_token); continue; } catch { return "retry"; }
        }
        if (e.status === 0 || e.status === 408 || e.status === 429) return "retry";
        if (e.status >= 500) return "server";
        if (e.status === 401) return "auth";
        // наряд уже закрыт, пока телефон был без связи: отметка «приехал» больше не нужна
        if (it.kind === "job_arrive" && e.code === "already_done") return "ok";
        it.error = e.message || "Запись не принята";
        await putItem(it);
        return "rejected";
      }
    }
    return "retry";
  } finally {
    clearTimeout(timer);
  }
}

let running = false;
/** Отправляет накопленное по порядку. Останавливается на первой неудаче связи: порядок записей важен («приехал» раньше «закончил»). */
export async function flush(): Promise<number> {
  if (running || uid === null) return 0;
  running = true;
  set({ flushing: true });
  let sent = 0;
  try {
    const todo = (await readAll()).filter((i) => i.uid === uid && !i.error).sort((a, b) => (a.id ?? 0) - (b.id ?? 0));
    for (const it of todo) {
      const r = await send(it);
      if (r === "ok") { await dropItem(it.id!); sent++; }
      else if (r === "rejected") continue;
      else if (r === "server" && it.tries + 1 >= MAX_SERVER_FAILS) {
        it.tries += 1;
        it.error = "Сервер не смог принять эту запись. Покажите её диспетчеру или внесите заново.";
        await putItem(it).catch(() => {});
      } else {
        if (r === "server") it.tries += 1;
        await putItem(it).catch(() => {});
        break;
      }
    }
  } catch {
    /* хранилище недоступно: попробуем позже */
  } finally {
    running = false;
    await reload();
    set({ flushing: false });
    if (sent) window.dispatchEvent(new CustomEvent("outbox:sent", { detail: sent }));
  }
  return sent;
}

/** Без связи ли отправка: сетевая ошибка самого запроса, а не отказ сервера. */
export const isOffline = (e: unknown) => e instanceof ApiError && e.status === 0;
