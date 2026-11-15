// Очередь отправки на экране: значок в шапке, список «ждут отправки» и предупреждение «нет связи».
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Camera, CalendarCheck, CloudOff, Fuel, Route as RouteIcon, Send, Trash2, UploadCloud, type LucideIcon } from "lucide-react";
import { discard, flush, setOutboxUser, useOutbox, type OutboxItem, type OutboxKind } from "../outbox";
import { useAuth } from "../auth";
import { fmtDateTime, plural } from "../format";
import { Alert, Badge, Button, Card, ConfirmModal } from "../ui";

const ICONS: Record<OutboxKind, LucideIcon> = { trip: RouteIcon, fuel: Fuel, job_arrive: CalendarCheck, job_finish: CalendarCheck, job_photos: Camera };
const SYNC_EVERY_MS = 30_000;

export function useOnline() {
  const [on, setOn] = useState(typeof navigator === "undefined" ? true : navigator.onLine);
  useEffect(() => {
    const up = () => setOn(true), down = () => setOn(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => { window.removeEventListener("online", up); window.removeEventListener("offline", down); };
  }, []);
  return on;
}

/** Подключается один раз в оболочке: привязывает очередь к вошедшему и отправляет накопленное, когда появилась связь. */
export function useOutboxSync() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const { items } = useOutbox();
  const pending = items.some((i) => !i.error);
  useEffect(() => {
    setOutboxUser(me?.id ?? null);
    if (!me) return;
    const go = () => { void flush(); };
    const visible = () => { if (document.visibilityState === "visible") go(); };
    const sent = () => qc.invalidateQueries();             // после отправки списки, наряды и счётчики показывают новое
    window.addEventListener("online", go);
    document.addEventListener("visibilitychange", visible);
    window.addEventListener("outbox:sent", sent);
    go();
    return () => {
      window.removeEventListener("online", go);
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("outbox:sent", sent);
    };
  }, [me?.id, qc]);                                        // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => { void flush(); }, SYNC_EVERY_MS);
    return () => clearInterval(t);
  }, [pending]);
}

/** Полоса сверху: нет связи или есть неотправленное. */
export function ConnectionBar() {
  const online = useOnline();
  const { items, flushing } = useOutbox();
  const waiting = items.filter((i) => !i.error).length;
  const failed = items.length - waiting;
  if (online && !items.length) return null;
  return (
    <div className={`conn-bar ${online ? "" : "off"}`} role="status">
      {online ? <UploadCloud size={16} aria-hidden /> : <CloudOff size={16} aria-hidden />}
      <span>
        {!online && "Нет связи. Записи сохранятся в телефоне и уйдут сами, когда сеть появится."}
        {online && waiting > 0 && (flushing ? "Отправляем записи…" : `Ждут отправки: ${waiting} ${plural(waiting, "запись", "записи", "записей")}.`)}
        {online && waiting === 0 && failed > 0 && `Не принято сервером: ${failed}.`}
      </span>
      {items.length > 0 && <Link to="/outbox">Открыть список</Link>}
    </div>
  );
}

function Row({ it, onAsk }: { it: OutboxItem; onAsk: (it: OutboxItem) => void }) {
  const Icon = ICONS[it.kind];
  return (
    <li className="outbox-row">
      <span className="entry-ico" aria-hidden><Icon size={18} /></span>
      <div className="outbox-main">
        <strong>{it.title}</strong>
        <span className="muted small">Сохранено в телефоне {fmtDateTime(it.saved_at)}</span>
        {it.error && <Alert tone="error">{it.error}</Alert>}
      </div>
      {it.error ? <Badge tone="error">Не принято</Badge> : <Badge tone="warning">{it.tries > 0 ? "Не ушло, повторим" : "Ждёт отправки"}</Badge>}
      <Button variant="ghost" size="sm" icon={Trash2} aria-label="Убрать из телефона" title="Убрать из телефона" onClick={() => onAsk(it)} />
    </li>
  );
}

/** Список неотправленных записей. Пустой, если отправлять нечего. */
export function OutboxList({ title = "Ждут отправки", always }: { title?: string; always?: boolean }) {
  const { items, flushing } = useOutbox();
  const [asking, setAsking] = useState<OutboxItem | null>(null);
  const waiting = items.filter((i) => !i.error).length;
  if (!items.length && !always) return null;
  return (
    <Card title={`${title}${items.length ? ` (${items.length})` : ""}`}
      actions={waiting > 0 ? <Button size="sm" icon={Send} loading={flushing} onClick={() => { void flush(); }}>Отправить сейчас</Button> : undefined}>
      {items.length === 0 ? <p className="muted">Всё отправлено, в телефоне ничего не ждёт.</p> : (
        <ul className="outbox-list">{items.map((it) => <Row key={it.id} it={it} onAsk={setAsking} />)}</ul>
      )}
      {asking && (
        <ConfirmModal title="Убрать запись из телефона" danger confirmLabel="Убрать"
          text={asking.error ? "Запись не принята сервером. Если убрать её, она пропадёт: внесите её заново." : "Запись ещё не отправлена. Если убрать её, она не дойдёт до диспетчера."}
          onConfirm={async () => { if (asking.id) await discard(asking.id); }} onClose={() => setAsking(null)} />
      )}
    </Card>
  );
}

/** Для страниц: есть ли в очереди отметки по этому наряду. */
export function useJobPending(jobId: number) {
  const { items } = useOutbox();
  const mine = items.filter((i) => i.job_id === jobId && !i.error);
  return {
    arrive: mine.some((i) => i.kind === "job_arrive"),
    finish: mine.some((i) => i.kind === "job_finish"),
    photos: (stage: string) => mine.filter((i) => i.kind === "job_photos" && i.fields?.stage === stage).reduce((n, i) => n + (i.files?.length ?? 0), 0),
    failed: items.filter((i) => i.job_id === jobId && i.error),
  };
}
