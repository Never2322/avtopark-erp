// Календарь: сроки документов, штрафов, аренды, СИЗ и события сотрудников плюс свои заметки. Месяц, неделя, день, год.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, Check, ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { get, patch } from "../api";
import { useAuth } from "../auth";
import { useEntityParam, useUrlState } from "../hooks";
import { Alert, Badge, Button, Card, PageHeader, QueryState, Tabs, useToast } from "../ui";
import { RecordModal, type Meta, type Row } from "./Registry";

interface Cell { d: string; n: number; wd: string; in_month: boolean; today: boolean; off: boolean }
export interface CalItem {
  source: string; source_label: string; auto: boolean; day: string; time: string; title: string; subject: string; url: string; color: string;
  entity_id: number | null; ref_id: number | null; done: boolean; all_day: boolean; end: string; row?: Row | null;
}
interface Cal {
  view: string; views: { id: string; label: string }[]; anchor: string; today: string; title: string; from: string; to: string; prev: string; next: string;
  grid: { weeks?: Cell[][]; days?: Cell[]; months?: { m: number; title: string; weeks: Cell[][] }[] };
  items: CalItem[]; can_create: boolean;
}

const WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
const MONTH_CHIPS = 3;

function Entry({ it, onOpen, onDone, busy }: { it: CalItem; onOpen: (it: CalItem) => void; onDone: (it: CalItem) => void; busy: boolean }) {
  const editable = !it.auto && !!it.row?.can_edit && !it.row?.archived;
  const body = (
    <>
      <span className="cal-time">{it.all_day || !it.time ? "весь день" : it.time}</span>
      <span className="cal-main">
        <strong className={it.done ? "cal-done" : ""}>{it.title}</strong>
        {it.subject && <span className="muted small">{it.subject}</span>}
      </span>
    </>
  );
  return (
    <div className={`cal-entry c-${it.color}`}>
      {editable && (
        <button type="button" className={`cal-check ${it.done ? "on" : ""}`} disabled={busy} aria-pressed={it.done}
          aria-label={it.done ? "Снять отметку «сделано»" : "Отметить сделанным"} onClick={() => onDone(it)}>
          {it.done && <Check size={14} aria-hidden />}
        </button>
      )}
      {it.auto && it.url ? <Link className="cal-link" to={it.url}>{body}</Link>
        : <button type="button" className="cal-link" onClick={() => onOpen(it)}>{body}</button>}
      <Badge tone={it.auto ? "neutral" : "mint"}>{it.source_label}</Badge>
    </div>
  );
}

function Chip({ it, onOpen }: { it: CalItem; onOpen: (it: CalItem) => void }) {
  const text = `${it.time ? it.time + " " : ""}${it.title}`;
  const tip = [it.source_label, it.title, it.subject].filter(Boolean).join(" · ");
  const cls = `cal-chip c-${it.color} ${it.done ? "done" : ""}`;
  return it.auto && it.url
    ? <Link className={cls} to={it.url} title={tip}>{text}</Link>
    : <button type="button" className={cls} title={tip} onClick={() => onOpen(it)}>{text}</button>;
}

export function Calendar() {
  const { can } = useAuth();
  const entity = useEntityParam();
  const qc = useQueryClient();
  const toast = useToast();
  const [url, setUrl] = useUrlState({ view: "month", d: "" });
  const [form, setForm] = useState<{ row: Row | null; start: string } | null>(null);

  const q = useQuery({ queryKey: ["calendar", url.view, url.d, entity], queryFn: () => get<Cal>("/calendar", { view: url.view, d: url.d, legal_entity_id: entity }), placeholderData: (p) => p });
  const cal = q.data;
  const canAdd = !!cal?.can_create && can("calendar_events.create");
  const meta = useQuery({ queryKey: ["reg-meta", "calendar_events"], queryFn: () => get<Meta>("/calendar_events/meta"), enabled: canAdd || !!form, staleTime: 60_000 });

  const byDay = useMemo(() => {
    const m = new Map<string, CalItem[]>();
    for (const it of cal?.items ?? []) m.set(it.day, [...(m.get(it.day) ?? []), it]);
    return m;
  }, [cal]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["calendar"] });
    qc.invalidateQueries({ queryKey: ["reminders"] });
    qc.invalidateQueries({ queryKey: ["reminders-summary"] });
  };
  const done = useMutation({
    mutationFn: (it: CalItem) => patch(`/calendar_events/${it.ref_id}`, { done: !it.done }),
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  });

  const goDay = (d: string) => setUrl({ view: "day", d });
  const open = (it: CalItem) => { if (it.row) setForm({ row: it.row, start: "" }); };
  const add = (d: string) => setForm({ row: null, start: `${d}T09:00` });
  const shown = (d: string) => byDay.get(d) ?? [];

  const dayNumber = (c: Cell) => (
    <button type="button" className="cal-num" onClick={() => goDay(c.d)} aria-label={`Открыть день ${c.d}`}>{c.n}</button>
  );

  const monthGrid = (weeks: Cell[][]) => (
    <div className="cal-month" role="grid">
      <div className="cal-head" role="row">{WEEKDAYS.map((w, i) => <span key={w} className={i > 4 ? "off" : ""}>{w}</span>)}</div>
      {weeks.map((w, wi) => (
        <div className="cal-week" role="row" key={wi}>
          {w.map((c) => {
            const list = shown(c.d);
            return (
              <div key={c.d} role="gridcell" className={`cal-day ${c.in_month ? "" : "other"} ${c.today ? "today" : ""} ${c.off ? "off" : ""}`}>
                <div className="cal-day-top">
                  {dayNumber(c)}
                  {canAdd && c.in_month && <button type="button" className="cal-add" aria-label={`Добавить событие на ${c.d}`} onClick={() => add(c.d)}><Plus size={14} aria-hidden /></button>}
                </div>
                <div className="cal-chips">
                  {list.slice(0, MONTH_CHIPS).map((it, i) => <Chip key={i} it={it} onOpen={open} />)}
                  {list.length > MONTH_CHIPS && <button type="button" className="cal-more" onClick={() => goDay(c.d)}>ещё {list.length - MONTH_CHIPS}</button>}
                </div>
                {list.length > 0 && (
                  <button type="button" className="cal-dots" onClick={() => goDay(c.d)} aria-label={`Событий: ${list.length}`}>
                    {list.slice(0, 4).map((it, i) => <i key={i} className={`c-${it.color}`} />)}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );

  const dayList = (c: Cell, withHeader: boolean) => {
    const list = shown(c.d);
    return (
      <div key={c.d} className={`cal-col ${withHeader && c.today ? "today" : ""} ${withHeader ? "" : "single"}`}>
        {withHeader && (
          <div className="cal-col-head">
            <button type="button" className="cal-num" onClick={() => goDay(c.d)}><span className="muted">{c.wd}</span> {c.n}</button>
            {canAdd && <button type="button" className="cal-add show" aria-label={`Добавить событие на ${c.d}`} onClick={() => add(c.d)}><Plus size={14} aria-hidden /></button>}
          </div>
        )}
        {list.length === 0 ? <p className="muted small cal-empty">{withHeader ? "—" : "На этот день ничего не запланировано."}</p>
          : list.map((it, i) => <Entry key={i} it={it} onOpen={open} onDone={(x) => done.mutate(x)} busy={done.isPending} />)}
      </div>
    );
  };

  const yearGrid = (months: NonNullable<Cal["grid"]["months"]>) => (
    <div className="cal-year">
      {months.map((m) => (
        <div className="cal-mini" key={m.m}>
          <button type="button" className="cal-mini-title" onClick={() => setUrl({ view: "month", d: m.weeks[1]?.[0]?.d ?? "" })}>{m.title}</button>
          <div className="cal-mini-head">{WEEKDAYS.map((w) => <span key={w}>{w}</span>)}</div>
          {m.weeks.map((w, wi) => (
            <div className="cal-mini-week" key={wi}>
              {w.map((c) => {
                const list = shown(c.d);
                return c.in_month
                  ? <button type="button" key={c.d} className={`cal-mini-day ${c.today ? "today" : ""} ${list.length ? "has" : ""} ${list.length ? "c-" + list[0].color : ""}`}
                      title={list.length ? `Событий: ${list.length}` : undefined} onClick={() => goDay(c.d)}>{c.n}</button>
                  : <span key={c.d} />;
              })}
            </div>
          ))}
        </div>
      ))}
    </div>
  );

  const g = cal?.grid;
  const total = cal?.items.length ?? 0;
  return (
    <>
      <PageHeader title="Календарь" sub="Сроки по документам, штрафам, аренде и СИЗ, события сотрудников и ваши заметки. Цвет показывает срочность: красный — наступило, жёлтый — скоро"
        actions={canAdd ? <Button variant="primary" icon={Plus} onClick={() => add(cal?.view === "day" || cal?.view === "week" ? cal.anchor : cal?.today ?? "")}>Добавить событие</Button> : undefined} />
      <div className="cal-bar">
        <div className="row">
          <Button icon={ChevronLeft} aria-label="Назад" onClick={() => cal && setUrl({ d: cal.prev })} />
          <Button onClick={() => setUrl({ d: "" })}>Сегодня</Button>
          <Button icon={ChevronRight} aria-label="Вперёд" onClick={() => cal && setUrl({ d: cal.next })} />
        </div>
        <h2 className="cal-title">{cal?.title ?? ""}</h2>
        <span className="spacer" />
        <Tabs tabs={(cal?.views ?? [{ id: "month", label: "Месяц" }, { id: "week", label: "Неделя" }, { id: "day", label: "День" }, { id: "year", label: "Год" }]).map((v) => ({ id: v.id, label: v.label }))}
          value={(cal?.view ?? url.view) as string} onChange={(v) => setUrl({ view: v })} />
      </div>
      <QueryState q={q}>
        {cal && g && (
          <div className="stack">
            {total === 0 && cal.view !== "year" && <Alert tone="info">За этот период записей нет. Сроки подтягиваются из личных дел, штрафов, аренды, СИЗ и событий сотрудников — по тем разделам, к которым у вас есть доступ.</Alert>}
            {cal.view === "month" && g.weeks && monthGrid(g.weeks)}
            {cal.view === "week" && g.days && <div className="cal-weekgrid">{g.days.map((c) => dayList(c, true))}</div>}
            {cal.view === "day" && g.days && <Card>{dayList(g.days[0], false)}</Card>}
            {cal.view === "year" && g.months && yearGrid(g.months)}
            <div className="cal-legend muted small" aria-label="Цвета">
              <span><i className="c-red" /> наступило</span><span><i className="c-amber" /> скоро</span><span><i className="c-violet" /> день рождения</span><span><i className="c-teal" /> ваше событие</span>
              <span className="spacer" />
              {can("calendar_events.view") && <Link to="/calendar-events">Все свои события</Link>}
              <Link to="/reminders"><CalendarDays size={14} aria-hidden /> Список напоминаний</Link>
            </div>
          </div>
        )}
      </QueryState>
      {form && meta.data && (
        <RecordModal meta={meta.data} row={form.row} defaults={form.start ? { start_at: form.start } : undefined}
          onClose={() => setForm(null)} onSaved={refresh} />
      )}
    </>
  );
}
