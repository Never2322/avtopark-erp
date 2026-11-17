// Счётчик ходок: личный калькулятор водителя самосвала. Нажал «Ходка» — в списке появилась запись с датой и временем.
// Только для водителя: директор и диспетчер эти записи не видят, в отчёты они не идут. Нажатую по ошибке ходку можно убрать.
// Время берётся с часов телефона в момент нажатия вместе с его часовым поясом, а без связи нажатие ждёт в телефоне и уходит само.
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, CloudOff, ListOrdered, Plus, Trash2, Undo2 } from "lucide-react";
import { api, errorText, get } from "../api";
import { useAuth } from "../auth";
import { discard, enqueue, isOffline, outboxAvailable, useOutbox, type OutboxItem } from "../outbox";
import { fmtDate, plural } from "../format";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, PageHeader, useToast } from "../ui";
import { newId } from "./Entries";

interface HaulItem { id: number; n: number; time: string; marked_at: string }
interface HaulDay { date: string; weekday: string; count: number }
interface HaulData { date: string; weekday: string; today: string; count: number; items: HaulItem[]; month_title: string; month_count: number; days: HaulDay[]; limit: number }
interface HaulBody { at: string; tz_min: number; client_id: string }
interface Row { key: string; at: number; time: string; id?: number; outbox?: number; pending: boolean }

const pad = (n: number) => String(n).padStart(2, "0");
/** День и время по часам телефона: телефон сам меняет пояс по месту, где он находится. */
const deviceDate = (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const deviceTime = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const addDays = (iso: string, n: number) => { const [y, m, d] = iso.split("-").map(Number); return deviceDate(new Date(y, m - 1, d + n)); };
/** Дата и время нажатия по поясу, с которым оно сделано (так показывает и сервер). */
const localOf = (b: HaulBody) => { const iso = new Date(Date.parse(b.at) + b.tz_min * 60_000).toISOString(); return { date: iso.slice(0, 10), time: iso.slice(11, 16) }; };
const hauls = (n: number) => `${n} ${plural(n, "ходка", "ходки", "ходок")}`;
const longDay = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long" }).format(new Date(y, m - 1, d));
};
const bodyOf = (it: OutboxItem) => it.json as HaulBody | undefined;

export function Hauls() {
  const { me } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const enabled = !!me?.features?.hauls;
  const [today, setToday] = useState(deviceDate());
  const [day, setDay] = useState(today);
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<{ id?: number; cid: string; n?: number; time: string; queued: boolean } | null>(null);
  const [asking, setAsking] = useState<Row | null>(null);
  const { items: outbox } = useOutbox();

  // за полночь «сегодня» меняется само
  useEffect(() => {
    const t = setInterval(() => {
      const now = deviceDate();
      if (now !== today) { if (day === today) setDay(now); setToday(now); }
    }, 30_000);
    return () => clearInterval(t);
  }, [today, day]);
  // подсказка «Отменить» живёт десять секунд
  useEffect(() => {
    if (!last) return;
    const t = setTimeout(() => setLast(null), 10_000);
    return () => clearTimeout(t);
  }, [last]);

  const q = useQuery({ queryKey: ["hauls", day], queryFn: () => get<HaulData>("/hauls", { date: day }), enabled, placeholderData: (p) => p, refetchInterval: 60_000 });
  const d = q.data;

  const pending = useMemo(() => outbox.filter((i) => i.kind === "haul" && !i.error && bodyOf(i)), [outbox]);
  const rows: Row[] = useMemo(() => {
    const server: Row[] = (d?.date === day ? d.items : []).map((i) => ({ key: `s${i.id}`, at: Date.parse(i.marked_at), time: i.time, id: i.id, pending: false }));
    const waiting: Row[] = pending.flatMap((i) => {
      const b = bodyOf(i)!, l = localOf(b);
      return l.date === day ? [{ key: `o${i.id}`, at: Date.parse(b.at), time: l.time, outbox: i.id, pending: true }] : [];
    });
    return [...server, ...waiting].sort((a, b) => a.at - b.at);
  }, [d, day, pending]);
  const monthWaiting = pending.filter((i) => localOf(bodyOf(i)!).date.slice(0, 7) === day.slice(0, 7)).length;
  const isToday = day === today;
  const shown = [...rows].reverse();                    // новые сверху

  const put = (r: HaulData) => { qc.setQueryData(["hauls", r.date], r); qc.invalidateQueries({ queryKey: ["hauls"], refetchType: "none" }); };

  const press = async () => {
    if (busy) return;
    setBusy(true);
    const now = new Date();
    const body: HaulBody = { at: now.toISOString(), tz_min: -now.getTimezoneOffset(), client_id: newId() };
    try {
      const r = await api<HaulData & { id: number }>("POST", "/hauls", body);
      put(r);
      if (r.date !== day) setDay(r.date);
      setLast({ id: r.id, cid: body.client_id, n: r.items.find((i) => i.id === r.id)?.n, time: deviceTime(now), queued: false });
    } catch (e) {
      if (isOffline(e) && outboxAvailable()) {
        try {
          await enqueue({ kind: "haul", title: `Ходка · ${deviceTime(now)}`, url: "/hauls", json: body });
          setLast({ cid: body.client_id, time: deviceTime(now), queued: true });
        } catch {
          toast.error("Не удалось сохранить ходку в телефоне. Нажмите ещё раз.");
        }
      } else {
        toast.error(errorText(e));
      }
    } finally {
      setTimeout(() => setBusy(false), 900);          // случайное двойное касание второй ходкой не станет
    }
  };

  const undo = async () => {
    if (!last) return;
    const l = last;
    setLast(null);
    try {
      if (l.id) put(await api<HaulData>("DELETE", `/hauls/${l.id}`));
      else { const it = outbox.find((i) => i.kind === "haul" && bodyOf(i)?.client_id === l.cid); if (it?.id) await discard(it.id); }
    } catch (e) { toast.error(errorText(e)); }
  };

  const remove = async (row: Row) => {
    if (row.outbox) { await discard(row.outbox); return; }
    put(await api<HaulData>("DELETE", `/hauls/${row.id}`));
    toast.success("Ходка убрана");
  };

  if (!enabled) {
    return (
      <>
        <PageHeader title="Счётчик ходок" />
        <Card><Empty icon={ListOrdered} title="Счётчик ходок есть у водителей самосвалов">Он появится, когда за вами закрепят самосвал.</Empty></Card>
      </>
    );
  }
  const count = rows.length;
  const month = (d?.month_count ?? 0) + monthWaiting;
  const days = (d?.days ?? []).filter((x) => x.date !== day);
  return (
    <>
      <PageHeader title="Счётчик ходок" sub="Нажимайте после каждой ходки. Это ваш личный счётчик: директор и диспетчер его не видят" />
      <div className="stack">
        {q.isError && <Alert tone="warning">Список сейчас не загрузился, возможно, нет связи. Кнопка работает: нажатия сохранятся в телефоне и уйдут сами.</Alert>}
        <Card className="haul-card">
          <div className="haul-nav">
            <Button icon={ChevronLeft} aria-label="Предыдущий день" onClick={() => setDay(addDays(day, -1))} />
            <div className="haul-day">
              <strong>{isToday ? "Сегодня" : longDay(day)}</strong>
              {isToday && <span className="muted small">{longDay(day)}</span>}
            </div>
            <Button icon={ChevronRight} aria-label="Следующий день" disabled={isToday} onClick={() => setDay(addDays(day, 1))} />
          </div>
          <div className="haul-count" aria-live="polite">
            <span className="haul-big">{count}</span>
            <span className="haul-word">{plural(count, "ходка", "ходки", "ходок")}</span>
          </div>
          {isToday ? (
            <button type="button" className="haul-btn" disabled={busy} onClick={() => void press()}><Plus size={34} aria-hidden /> Ходка</button>
          ) : (
            <Button size="lg" onClick={() => setDay(today)}>Вернуться на сегодня</Button>
          )}
          {last && (
            <div className="haul-undo" role="status">
              <span>{last.queued ? <><CloudOff size={16} aria-hidden /> Нет связи: ходка в {last.time} сохранена в телефоне</> : <>Записана ходка{last.n ? ` №${last.n}` : ""} в {last.time}</>}</span>
              <Button size="sm" icon={Undo2} onClick={() => void undo()}>Отменить</Button>
            </div>
          )}
          <span className="muted small">За {d?.month_title ?? "месяц"}: {hauls(month)}</span>
        </Card>

        <Card title={isToday ? "Ходки сегодня" : `Ходки: ${fmtDate(day)}`} flush>
          {shown.length === 0 ? (
            <Empty icon={ListOrdered} title="Ходок пока нет">{isToday ? "Нажмите «Ходка», когда разгрузились: запись появится здесь с датой и временем." : "В этот день ходок не записано."}</Empty>
          ) : (
            <ul className="haul-list">
              {shown.map((r) => (
                <li className="haul-row" key={r.key}>
                  <span className="haul-n">№{rows.indexOf(r) + 1}</span>
                  <div className="haul-when">
                    <strong>{r.time}</strong>
                    <span className="muted small">{fmtDate(day)}</span>
                  </div>
                  {r.pending && <Badge tone="warning">ждёт отправки</Badge>}
                  <Button variant="ghost" size="sm" icon={Trash2} aria-label={`Убрать ходку №${rows.indexOf(r) + 1}`} onClick={() => setAsking(r)}>Убрать</Button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {days.length > 0 && (
          <Card title="Прошлые дни" flush>
            <ul className="haul-list">
              {days.map((x) => (
                <li key={x.date}>
                  <button type="button" className="haul-day-row" onClick={() => setDay(x.date)}>
                    <span><strong>{x.weekday}</strong> <span className="muted">{fmtDate(x.date)}</span></span>
                    <span className="haul-day-n">{hauls(x.count)}</span>
                    <ChevronRight size={16} aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
      {asking && (
        <ConfirmModal title={`Убрать ходку №${rows.indexOf(asking) + 1}?`} danger confirmLabel="Убрать"
          text={`Ходка в ${asking.time} пропадёт из счётчика. Остальные номера пересчитаются.`}
          onConfirm={() => remove(asking)} onClose={() => setAsking(null)} />
      )}
    </>
  );
}
